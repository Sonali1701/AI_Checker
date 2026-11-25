import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from portal_vendor import data_filler as engine


EMAIL = 'evaluator.b4@pw.live'


def option(text, value='', index=1, disabled=False):
    return dict(index=index, text=text, value=value, disabled=disabled)


class Dropdown:
    def __init__(self, options=None, tag='select'):
        self.options = options or []
        self.tag, self.clock, self.opens = tag, 0, 0
        self.after_open = None
        self.page, self.control = MagicMock(), MagicMock()
        self.metadata = dict(index=1, tag=tag, label='Evaluator', visible=True, disabled=False)
        self.controls = MagicMock()
        self.controls.evaluate_all.side_effect = self.inspect
        self.controls.nth.return_value = self.control
        self.page.locator.return_value = self.controls
        self.control.click.side_effect = self.open
        self.control.select_option.side_effect = lambda **kw: setattr(self, 'selected', kw['index'])
        self.control.evaluate.side_effect = lambda *_: self.selected
        self.control.fill.side_effect = lambda email: setattr(self, 'search', email)
        self.selected = -1
        self.popup = self.page.get_by_role.return_value
        self.popup.evaluate_all.side_effect = lambda *_: self.options
        self.page.wait_for_timeout.side_effect = lambda ms: setattr(self, 'clock', self.clock + ms / 1000)

    def inspect(self, *_):
        return [dict(index=0, tag='select', label='', visible=True, disabled=False,
                     options=[option('Submitted'), option('In Evaluation')]),
                dict(self.metadata, options=self.options if self.tag == 'select' else [])]

    def open(self):
        self.opens += 1
        if self.after_open:
            self.after_open()

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(engine.time, 'monotonic', side_effect=lambda: self.clock))
        stack.enter_context(patch.object(engine, 'should_stop', return_value=False))
        # The supplied tool's timeout: the workflow adapter raises it to 45 s for live runs,
        # and that change outlives any earlier test that created an adapter.
        stack.enter_context(patch.object(engine, 'DEFAULT_TIMEOUT_MS', 15_000))
        return stack


class EvaluatorDropdownTests(unittest.TestCase):
    def test_options_load_when_native_dropdown_is_opened(self):
        dropdown = Dropdown([option('Select', index=0)])
        dropdown.after_open = lambda: setattr(dropdown, 'options', [option('Sonali Singh (' + EMAIL + ')')])
        with dropdown.patches():
            selected = engine.select_evaluator(dropdown.page, EMAIL)
        self.assertEqual(selected, 'Sonali Singh (' + EMAIL + ')')
        self.assertEqual(dropdown.opens, 1)
        self.assertEqual(dropdown.selected, 1)

    def test_already_loaded_options_do_not_add_opening_delay(self):
        dropdown = Dropdown([option('Sonali Singh (' + EMAIL + ')')])
        with dropdown.patches():
            engine.select_evaluator(dropdown.page, EMAIL)
        self.assertEqual((dropdown.opens, dropdown.clock), (0, 0))

    def test_email_can_be_in_value_when_label_only_contains_name(self):
        dropdown = Dropdown([option('Sonali Singh', EMAIL)])
        with dropdown.patches():
            self.assertEqual(engine.select_evaluator(dropdown.page, EMAIL.upper()), 'Sonali Singh')

    def test_retry_reopens_an_empty_list_once(self):
        dropdown = Dropdown()
        def fill_on_retry():
            if dropdown.opens == 2:
                dropdown.options = [option('Sonali Singh (' + EMAIL + ')')]
        dropdown.after_open = fill_on_retry
        with dropdown.patches():
            engine.select_evaluator(dropdown.page, EMAIL)
        self.assertEqual(dropdown.opens, 2)

    def test_empty_list_is_not_reported_as_missing_email(self):
        dropdown = Dropdown([option('Select', index=0)])
        with dropdown.patches(), self.assertRaisesRegex(RuntimeError, 'dropdown stayed empty'):
            engine.select_evaluator(dropdown.page, EMAIL)
        self.assertEqual(dropdown.opens, 2)
        self.assertLessEqual(dropdown.clock, 30)
        dropdown.control.select_option.assert_not_called()

    def test_populated_list_missing_email_is_distinguished_from_empty_list(self):
        dropdown = Dropdown([option('Shweta (shweta@example.com)')])
        with dropdown.patches(), self.assertRaisesRegex(LookupError, '1 readable evaluator options'):
            engine.select_evaluator(dropdown.page, EMAIL)

    def test_similar_but_different_email_does_not_match(self):
        self.assertFalse(engine._email_option_matches(EMAIL + '.other', EMAIL))
        self.assertFalse(engine._email_option_matches('x' + EMAIL, EMAIL))
        self.assertTrue(engine._email_option_matches('Sonali (' + EMAIL.upper() + ')', EMAIL))

    def test_searchable_custom_combobox_is_opened_and_searched(self):
        dropdown = Dropdown(tag='input')
        dropdown.after_open = lambda: setattr(dropdown, 'options', [option('Sonali Singh (' + EMAIL + ')')])
        with dropdown.patches():
            selected = engine.select_evaluator(dropdown.page, EMAIL)
        self.assertEqual(dropdown.search, EMAIL)
        self.assertEqual(selected, 'Sonali Singh (' + EMAIL + ')')
        dropdown.popup.nth.assert_called_once_with(1)
        dropdown.popup.nth.return_value.click.assert_called_once()

    def test_hidden_dropdown_is_ignored(self):
        dropdown = Dropdown()
        dropdown.metadata['visible'] = False
        with dropdown.patches(), self.assertRaisesRegex(RuntimeError, 'dropdown could not be detected'):
            engine.select_evaluator(dropdown.page, EMAIL)
        dropdown.control.click.assert_not_called()

    def test_ambiguous_options_are_not_selected(self):
        dropdown = Dropdown([option('Sonali (' + EMAIL + ')'), option('Another (' + EMAIL + ')', index=2)])
        with dropdown.patches(), self.assertRaisesRegex(RuntimeError, 'More than one evaluator option'):
            engine.select_evaluator(dropdown.page, EMAIL)
        dropdown.control.select_option.assert_not_called()

    def test_cancellation_stops_dropdown_waiting(self):
        dropdown = Dropdown()
        with dropdown.patches(), patch.object(engine, 'should_stop', return_value=True):
            with self.assertRaises(engine.AbortRun):
                engine.select_evaluator(dropdown.page, EMAIL)
        self.assertEqual(dropdown.clock, 0)

    def test_native_selection_must_be_retained(self):
        dropdown = Dropdown([option('Sonali (' + EMAIL + ')')])
        dropdown.control.select_option.side_effect = None
        with dropdown.patches(), self.assertRaisesRegex(RuntimeError, 'did not retain'):
            engine.select_evaluator(dropdown.page, EMAIL)


if __name__ == '__main__':
    unittest.main()
