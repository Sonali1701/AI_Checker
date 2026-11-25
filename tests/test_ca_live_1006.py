# -*- coding: utf-8 -*-
"""Faults found by the live CA check of 2026-10-06 (15 copies, 8 subjects, build 84).

A CA Inter Advanced Accounting copy reached faculty with no marks and no PDF: after
two readings Q2 still had a step outside 0 to its maximum, the repair only lowered
awards ABOVE the maximum, and the final compiler refused the whole copy.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grader import Criterion, QuestionMark  # noqa: E402
from pipeline import _clamp_to_valid_marks  # noqa: E402
from strict_grading import validate_question_marks  # noqa: E402


def _question(score, steps):
    return QuestionMark(qid="Q2", score=score, max_score=10, page=1, y_fraction=0.2,
                        mark_style="per_step",
                        criteria=[Criterion(description=f"step {n}", awarded=a, max=m,
                                            met=False, evidence="")
                                  for n, (a, m) in enumerate(steps)])


SPEC = SimpleNamespace(qid="Q2", max_marks=10.0, question_type="descriptive", criteria=[])


class HeldQuestionKeepsTheCopy(unittest.TestCase):
    def test_a_step_below_zero_no_longer_loses_the_copy(self):
        question = _question(6, [(4, 4), (-1, 3), (3, 3)])
        with self.assertRaises(Exception):
            validate_question_marks(question, SPEC)
        _clamp_to_valid_marks(question, 10)
        validate_question_marks(question, SPEC)
        self.assertEqual([c.awarded for c in question.criteria], [4, 0, 3])

    def test_awards_are_only_ever_lowered(self):
        question = _question(12, [(5, 4), (float("nan"), 3), (2, 3)])
        _clamp_to_valid_marks(question, 10)
        self.assertEqual(question.score, 10)
        self.assertEqual([c.awarded for c in question.criteria], [4, 0, 2])

    def test_a_step_with_no_usable_maximum_carries_no_mark(self):
        question = _question(-2, [(1, -1), (2, 4)])
        _clamp_to_valid_marks(question, 10)
        self.assertEqual(question.score, 0)
        # The rest of the 10 marks is booked as one zero step (2026-10-10), so the
        # final check accepts the breakdown.
        self.assertEqual([(c.awarded, c.max) for c in question.criteria], [(2, 4), (0, 6)])
        self.assertTrue(all(math.isfinite(c.awarded) for c in question.criteria))
        validate_question_marks(question, SPEC)


class ReaderNotesDoNotDiscardARightGraph(unittest.TestCase):
    """CA Inter Taxation MTS 01: the recheck read "Q1 compulsory, any two of Q2 to Q4,
    15 + 15 + 20 = 50" and wrote "This is consistent." in allocation_issues; the note
    threw the right reading away and the paper could not be graded."""

    RAW = ('{"items": [{"qid": "Q1", "max": 30}, {"qid": "Q2", "max": 10}, '
           '{"qid": "Q3", "max": 10}, {"qid": "Q4", "max": 10}], '
           '"choice_groups": [], "allocation_issues": ["This is consistent."]}')

    def test_a_note_still_fails_an_ordinary_reading(self):
        from grader import MarksExtractionError, parse_strict_marks_scheme
        with self.assertRaises(MarksExtractionError):
            parse_strict_marks_scheme(self.RAW)

    def test_the_total_checked_recheck_reads_past_the_note(self):
        from grader import parse_strict_marks_scheme
        scheme = parse_strict_marks_scheme(self.RAW, tolerate_notes=True)
        self.assertEqual([i.qid for i in scheme.items], ["Q1", "Q2", "Q3", "Q4"])

    def test_only_the_total_gap_recheck_tolerates_notes(self):
        from unittest.mock import patch
        import pipeline
        from test_choice_total_recheck import _scheme
        calls = []

        def extract(*args, **kwargs):
            calls.append(kwargs)
            return [_scheme(False), _scheme(True)][len(calls) - 1].model_copy(deep=True)

        cfg = type("Cfg", (), {"provider": "claude", "exam_type": "school"})()
        with patch.multiple(pipeline, _extract_marks=extract, pdf_text_layer=lambda *a: "Q19 OR",
                            paper_has_choice=lambda *a: True,
                            pdf_or_image_to_pngs=lambda *a: [b"png"]):
            pipeline.detect_marks_scheme(b"%PDF", "paper.pdf", cfg, total_marks=70,
                                         require_complete=True)
        self.assertNotIn("tolerate_allocation_notes", calls[0])
        self.assertTrue(calls[1]["tolerate_allocation_notes"])


if __name__ == "__main__":
    unittest.main()
