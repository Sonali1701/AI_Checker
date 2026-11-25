# -*- coding: utf-8 -*-
"""An MCQ answer list gets its orientation asked about, not assumed.

Bihar Board Class 11 Economics, row 164, 18 Nov 2026. Page 1 is a photograph of a
ruled notebook page holding fifty answers written as "(47) A", taken sideways. Two
independent safeguards passed it through upright:

  * the free heuristic scored it 0.805 for "leave alone" against -0.619 for a quarter
    turn, because the notebook's printed rules run across the image and look exactly
    like lines of text, and fifty short answers are too sparse to outvote them;
  * lines_run_vertically wants three or more boxes over 1.6x taller than wide making
    up 60% of the page. "(47) A" is roughly square whichever way the page is turned.

So the page was graded sideways. Its letters were misread, question 47 was credited
against a key of B where the student wrote A, question 50 was credited although the
student wrote nothing, and the copy came out 21 marks above the evaluator while the
other seven copies on that paper landed within 5.

short_token_page names that page shape. The shape test is not evidence of uprightness
there, it is the absence of evidence, so the orientation probe is asked - and because
nothing claims the page IS sideways, a probe answer of "upright" is believed rather
than overridden with a forced quarter turn.
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mathpix import lines_run_vertically, short_token_page  # noqa: E402


def _line(text, x0, y0, x1, y1, kind="text"):
    return types.SimpleNamespace(text=text, bbox=(x0, y0, x1, y1), kind=kind,
                                 line_id=f"L{y0}")


def _page(lines):
    return types.SimpleNamespace(lines=lines)


def _answer_list(n=50):
    """Fifty rows of "(nn) X" - wide-ish, short, and nothing like a paragraph."""
    return _page([_line(f"({i}) B", 100, 20 * i, 180, 20 * i + 14) for i in range(1, n + 1)])


def _prose(n=12):
    return _page([_line("the demand curve slopes downward because", 60, 30 * i,
                        900, 30 * i + 22) for i in range(1, n + 1)])


def _sideways_prose(n=12):
    """What prose looks like when the page went in sideways: tall, narrow boxes."""
    return _page([_line("the demand curve slopes downward", 40 * i, 60,
                        40 * i + 18, 900) for i in range(1, n + 1)])


class TheAnswerListIsRecognised(unittest.TestCase):
    def test_an_answer_list_is_a_short_token_page(self):
        self.assertTrue(short_token_page(_answer_list()))

    def test_the_shape_test_cannot_judge_it(self):
        """The precondition for asking the probe: no sideways signal either way."""
        sideways, _, _ = lines_run_vertically(_answer_list())
        self.assertFalse(sideways)

    def test_prose_is_not_a_short_token_page(self):
        """Ordinary writing must not start paying for orientation probes."""
        self.assertFalse(short_token_page(_prose()))

    def test_sideways_prose_still_goes_down_the_old_path(self):
        sideways, _, _ = lines_run_vertically(_sideways_prose())
        self.assertTrue(sideways)
        self.assertFalse(short_token_page(_sideways_prose()))

    def test_a_handful_of_short_lines_is_not_an_answer_list(self):
        """A heading and a couple of labels must not trigger a probe."""
        self.assertFalse(short_token_page(_answer_list(n=4)))

    def test_a_mixed_page_is_judged_by_its_bulk(self):
        """Mostly prose with a few labels stays prose."""
        lines = _prose(10).lines + _answer_list(3).lines
        self.assertFalse(short_token_page(_page(lines)))

    def test_non_text_lines_are_ignored(self):
        lines = _answer_list().lines + [_line("", 0, 0, 10, 10, kind="diagram")]
        self.assertTrue(short_token_page(_page(lines)))

    def test_an_empty_page_is_not_one(self):
        self.assertFalse(short_token_page(_page([])))


if __name__ == "__main__":
    unittest.main()
