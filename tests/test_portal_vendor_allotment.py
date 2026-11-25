import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from portal_vendor import data_filler as engine
from portal_vendor.data_filler import _allotment_identity, _evaluator_matches, _status_pattern


class PaginatedStudents:
    """Stateful browser double: real table parsing/selection, reset-on-reload, and stale selection checks."""
    def __init__(self, pages):
        self.pages, self.index = pages, len(pages) - 1
        self.status = 'InEvaluation'
        self.page = MagicMock()
        self.selected = set()
        self.snapshot_calls = self.next_calls = self.check_calls = self.uncheck_calls = 0
        self.submissions, self.refuse = [], set()
        self.after_submit = None
        self.acknowledge = True
        self.table = MagicMock()
        self.table.count.side_effect = lambda: len(self.pages[self.index])
        self.table.evaluate_all.side_effect = self.raw_rows
        self.table.nth.side_effect = self.row
        self.header = MagicMock()
        self.header.count.return_value = 1
        self.header.check.side_effect = self.check_all
        self.page.locator.side_effect = self.locator
        self.previous_button = previous = MagicMock()
        previous.count.return_value = 1
        previous.is_visible.return_value = True
        previous.evaluate.side_effect = lambda *_: self.index == 0
        previous.click.side_effect = self.previous
        self.first_button = MagicMock()
        self.first_button.count.return_value = 1
        self.first_button.is_visible.return_value = True
        self.first_button.evaluate.return_value = False
        self.first_button.click.side_effect = self.refresh
        self.page.get_by_text.side_effect = lambda pattern: MagicMock(
            last=self.first_button if pattern.fullmatch('01') else self.previous_button)

    def locator(self, selector):
        if selector == engine.SEL['data_rows']:
            return self.table
        if selector == engine.SEL['select_all']:
            return MagicMock(first=self.header)
        return MagicMock()

    def row(self, index):
        box = MagicMock()
        def check():
            self.check_calls += 1
            self.selected.add(index)
        def uncheck():
            self.uncheck_calls += 1
            self.selected.discard(index)
        box.check.side_effect, box.uncheck.side_effect = check, uncheck
        row = MagicMock()
        row.locator.return_value.first = box
        return row

    def check_all(self):
        self.check_calls += 1
        self.selected = set(range(len(self.pages[self.index])))

    def raw_rows(self, *_):
        self.snapshot_calls += 1
        return [dict(index=i, selectable=True, checked=i in self.selected,
                     cells=['', name, name+'@example.com', evaluator, '', '', '', name, self.status])
                for i, (name, evaluator) in enumerate(self.pages[self.index])]

    def previous(self):
        self.index -= 1
        self.selected.clear()

    def next(self, *_):
        self.next_calls += 1
        if self.index + 1 == len(self.pages):
            return False
        self.index += 1
        self.selected.clear()
        return True

    def refresh(self, *_):
        self.index = 0
        self.selected.clear()

    def submit(self, *_):
        self.submissions.append(self.index)
        for i in self.selected:
            record = self.pages[self.index][i]
            if record[0] not in self.refuse:
                record[1] = 'Sonali Singh'
        self.selected.clear()
        if self.after_submit:
            self.after_submit()
        return self.acknowledge

    def patches(self):
        stack = ExitStack()
        replacements = {
            'choose_status': None, 'wait_list_settled': None, 'wait_rows_changed': True,
            'should_stop': False, 'ask_yes_no': True, 'on_status': None,
            'select_evaluator': 'Sonali Singh (evaluator.b4@pw.live)',
        }
        for name, value in replacements.items():
            stack.enter_context(patch.object(engine, name, return_value=value))
        for name, func in {'go_next_page': self.next, 'refresh_students': self.refresh,
                           'submit_bundle': self.submit}.items():
            stack.enter_context(patch.object(engine, name, side_effect=func))
        return stack


class AllotmentTests(unittest.TestCase):
    def test_identity_uses_name_email_and_phone(self):
        row = MagicMock()
        cells = row.locator.return_value
        cells.count.return_value = 9
        cells.nth.side_effect = [
            MagicMock(inner_text=MagicMock(return_value="Yashi")),
            MagicMock(inner_text=MagicMock(return_value=" Student@example.com ")),
            MagicMock(inner_text=MagicMock(return_value=" 1234567890 ")),
        ]

        self.assertEqual(
            _allotment_identity(row),
            ("yashi", "student@example.com", "1234567890"),
        )

    def test_identity_rejects_unexpected_table_layout(self):
        row = MagicMock()
        row.locator.return_value.count.return_value = 7

        with self.assertRaisesRegex(ValueError, "unexpected layout"):
            _allotment_identity(row)

    def test_evaluator_matches_display_name_from_selected_option(self):
        row = MagicMock()
        row.locator.return_value.nth.return_value.inner_text.return_value = "Sonali Singh"

        self.assertTrue(_evaluator_matches(row, "Sonali Singh (evaluator.b4@pw.live)"))
        self.assertFalse(_evaluator_matches(row, "Shweta Ruhil (shweta@example.com)"))

    def test_status_filter_matches_spaced_and_compact_site_labels(self):
        pattern = _status_pattern("In Evaluation")

        self.assertRegex("In Evaluation", pattern)
        self.assertRegex("InEvaluation", pattern)
        self.assertNotRegex("InReview", pattern)

    def test_reassignment_survives_reload_to_first_page_and_continues(self):
        site = PaginatedStudents([
            [['Komal', 'Sonali Singh']], [['Arnav', 'Shweta Ruhil']], [['Sakshi', 'Shweta Ruhil']],
        ])
        results = []
        with site.patches():
            engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(site.submissions, [1, 2])
        self.assertEqual(len(results), 3)  # Revisiting pages must not duplicate report entries.
        self.assertEqual(sum(r['Result'] == engine.DONE for r in results), 2)
        self.assertEqual(sum(r['Result'] == engine.DONE_EARLIER for r in results), 1)

    def test_verification_finds_students_on_different_pages(self):
        site = PaginatedStudents([
            [['Arnav', 'Sonali Singh']], [['Other', 'Shweta Ruhil']], [['Sakshi', 'Sonali Singh']],
        ])
        targets = {(name.lower(), name.lower() + '@example.com', name.lower()): name
                   for name in ['Arnav', 'Sakshi']}
        with site.patches():
            found = engine._scan_reassigned_students(site.page, targets, 'Sonali Singh (email)')
        self.assertEqual(found, dict.fromkeys(targets, True))

    def test_verification_rejects_duplicate_identity_across_pages(self):
        site = PaginatedStudents([[['Arnav', 'Sonali Singh']], [['Arnav', 'Sonali Singh'], ['Other', 'Sonali Singh']]])
        with site.patches(), self.assertRaisesRegex(RuntimeError, 'uniquely verify'):
            engine._scan_reassigned_students(site.page, {('arnav', 'arnav@example.com', 'arnav'): 'Arnav'},
                                             'Sonali Singh (email)')

    def test_failed_assignment_preserves_confirmed_results_without_resubmitting(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil'], ['Sakshi', 'Shweta Ruhil']]])
        site.refuse.add('Arnav')
        results = []
        with site.patches(), self.assertRaisesRegex(RuntimeError, 'still shows a different evaluator'):
            engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(site.submissions, [0])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(r['Result'] == engine.DONE for r in results), 1)
        self.assertEqual(sum(r['Result'] == engine.UNVERIFIED for r in results), 1)

    def test_missing_student_is_not_reported_as_assigned(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        results = []
        with site.patches(), patch.object(engine, 'refresh_students', side_effect=lambda *_: site.pages[0].clear()):
            with self.assertRaisesRegex(RuntimeError, 'not found across the In Evaluation pages'):
                engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['Result'], engine.UNVERIFIED)

    def test_pagination_reset_stops_if_previous_button_does_not_work(self):
        site = PaginatedStudents([[['First', 'Sonali Singh']], [['Last', 'Sonali Singh']]])
        site.first_button.count.return_value = 0
        with site.patches(), patch.object(engine, 'wait_rows_changed', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'first Students page'):
                engine._first_allotment_page(site.page)

    def test_first_page_noop_is_success_even_when_previous_arrow_looks_enabled(self):
        site = PaginatedStudents([[['First', 'Sonali Singh']]])
        site.previous_button.evaluate.side_effect = None
        site.previous_button.evaluate.return_value = False
        with site.patches(), patch.object(engine, 'wait_rows_changed', return_value=False):
            engine._first_allotment_page(site.page)
        self.assertEqual(site.index, 0)
        site.first_button.click.assert_called_once()
        site.previous_button.click.assert_not_called()

    def test_numbered_first_page_jumps_directly_from_page_three(self):
        site = PaginatedStudents([[['First', 'Sonali Singh']], [], [['Last', 'Sonali Singh']]])
        with site.patches():
            engine._first_allotment_page(site.page)
        self.assertEqual(site.index, 0)
        site.first_button.click.assert_called_once()
        site.previous_button.click.assert_not_called()

    def test_disabled_first_page_link_does_not_get_clicked(self):
        site = PaginatedStudents([[['First', 'Sonali Singh']]])
        site.first_button.evaluate.return_value = True
        with site.patches():
            engine._first_allotment_page(site.page)
        site.first_button.click.assert_not_called()
        site.previous_button.click.assert_not_called()

    def test_large_runs_use_two_linear_passes_and_one_bulk_checkbox_per_page(self):
        for page_count in (14, 100, 250):
            with self.subTest(pages=page_count):
                site = PaginatedStudents([[[f'Student-{p}-{s}', 'Shweta Ruhil'] for s in range(50)]
                                          for p in range(page_count)])
                results = []
                with site.patches():
                    engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
                self.assertEqual(len(site.submissions), page_count)
                self.assertEqual(site.next_calls, 2 * page_count)
                self.assertEqual(site.snapshot_calls, 4 * page_count + 3)  # Also verify selection after choosing evaluator.
                self.assertEqual(site.check_calls, page_count + 1)  # One bootstrap checkbox.
                self.assertEqual(site.uncheck_calls, 1)
                self.assertEqual(len(results), page_count * 50)
                self.assertTrue(all(r['Result'] == engine.DONE for r in results))

    def test_already_assigned_pages_are_read_once_without_checkbox_churn(self):
        site = PaginatedStudents([[[f'Student-{p}-{s}', 'Sonali Singh'] for s in range(50)] for p in range(30)])
        results = []
        with site.patches():
            engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(site.submissions, [])
        self.assertEqual(site.next_calls, 30)
        self.assertEqual(site.snapshot_calls, 33)  # Includes bootstrap selection verification.
        self.assertEqual((site.check_calls, site.uncheck_calls), (1, 1))
        self.assertEqual(len(results), 1500)

    def test_mixed_page_selects_only_students_with_different_evaluator(self):
        site = PaginatedStudents([[['Komal', 'Sonali Singh'], ['Arnav', 'Shweta Ruhil'], ['Sakshi', 'Sonali Singh']]])
        results = []
        with site.patches():
            engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual((site.check_calls, site.uncheck_calls), (2, 1))
        self.assertEqual(sum(r['Result'] == engine.DONE for r in results), 1)
        self.assertEqual(sum(r['Result'] == engine.DONE_EARLIER for r in results), 2)

    def test_unacknowledged_submit_is_verified_before_more_submissions(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']], [['Sakshi', 'Shweta Ruhil']]])
        site.acknowledge = False
        results = []
        with site.patches():
            engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(site.submissions, [0, 1])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['Result'] == engine.DONE for r in results))

    def test_cancellation_keeps_attempted_assignments_unverified_in_report(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']], [['Sakshi', 'Shweta Ruhil']]])
        results = []
        with site.patches(), patch.object(engine, 'should_stop', side_effect=lambda: bool(site.submissions)):
            with self.assertRaises(engine.AbortRun):
                engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(site.submissions, [0])
        self.assertEqual(engine.summarize(results)['counts'][engine.UNVERIFIED], 1)
        self.assertEqual(engine.summarize(results)['counts'][engine.DONE], 0)

    def test_selection_drift_prevents_submission(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil'], ['Sakshi', 'Sonali Singh']]])
        site.index = 0
        records = engine._allotment_snapshot(site.page)
        # Simulate the website selecting a different student after our click.
        real_read = site.raw_rows
        def changed(*args):
            site.selected = {1}
            return real_read(*args)
        site.table.evaluate_all.side_effect = changed
        with site.patches(), self.assertRaisesRegex(RuntimeError, 'selected Students rows changed'):
            engine._select_reassignment_rows(site.page, records, records[:1])
        self.assertEqual(site.submissions, [])

    def test_changed_table_layout_is_rejected_by_bulk_reader(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']]])
        site.table.evaluate_all.return_value = [dict(index=0, selectable=True, checked=False, cells=[''] * 7)]
        site.table.evaluate_all.side_effect = None
        with self.assertRaisesRegex(ValueError, 'unexpected layout'):
            engine._allotment_snapshot(site.page)

    def test_server_rejection_is_not_treated_as_acknowledged_submission(self):
        page, deadline = MagicMock(), MagicMock()
        deadline.is_visible.return_value = False  # The form hides even though the server refused it.
        page.locator.return_value.first.count.return_value = 0
        response = MagicMock(status=403, url='https://example.test/allot')
        response.request.method = 'POST'
        page.on.side_effect = lambda event, callback: callback(response)
        self.assertFalse(engine.submit_bundle(page, deadline))

    def test_students_moved_between_pages_are_verified_in_single_final_pass(self):
        site = PaginatedStudents([[['Arnav', 'Shweta Ruhil']], [['Sakshi', 'Shweta Ruhil']]])
        def reorder():
            if len(site.submissions) == 2:
                site.pages.reverse()
        site.after_submit = reorder
        results = []
        with site.patches():
            engine.allot_in_evaluation(site.page, 'Test', 'evaluator.b4@pw.live', 'deadline', results, None)
        self.assertEqual(site.next_calls, 4)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['Result'] == engine.DONE for r in results))

    def test_same_student_names_remain_distinct_by_contact_details(self):
        site = PaginatedStudents([[['Arnav', 'Sonali Singh'], ['Arnav', 'Sonali Singh']]])
        raw = site.raw_rows()
        raw[1]['cells'][2] = 'different@example.com'
        raw[1]['cells'][7] = 'another-phone'
        site.table.evaluate_all.side_effect = None
        site.table.evaluate_all.return_value = raw
        records = engine._allotment_snapshot(site.page)
        self.assertNotEqual(records[0]['identity'], records[1]['identity'])


if __name__ == "__main__":
    unittest.main()
