# -*- coding: utf-8 -*-
"""An MCQ that carries a fixed rubric step no longer throws the whole copy away.

Found by the CA new-sample run of 2026-11-12. Both Corporate and Other Laws papers
number their MCQs Q1 to Q8 and their descriptive parts Q1.a to Q4.b, and their Q1 to
Q7 each carry one fixed step. The grader answers an MCQ with a chosen option and no
criteria breakdown, so the returned step set was empty, never matched the one fixed
step, and validate_question_marks raised FIXED_RUBRIC_MISMATCH. Every Corporate Law
copy ended with only a status.json, no report, no PDF and no score, and the first was
still charged Rs.22.84. The Taxation papers passed only because their MCQs carry no
steps.

An objective row is scored from the key's own option and is rewritten by the caller
straight after this check, so its step list is not what decides its mark.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grader import Criterion, QuestionMark  # noqa: E402
from strict_grading import validate_question_marks  # noqa: E402


def _step(criterion_id, max_marks=2.0):
    return SimpleNamespace(criterion_id=criterion_id, max_marks=max_marks,
                           description="correct option")


def _mcq_without_a_breakdown():
    """What the grader really returns for an MCQ: an option, no criteria."""
    return QuestionMark(qid="Q1", score=2.0, max_score=2.0, page=1, y_fraction=0.2,
                        mark_style="single", criteria=[])


class ObjectiveRowIsExemptFromTheFixedStepList(unittest.TestCase):
    def test_mcq_with_a_fixed_step_is_accepted_without_a_breakdown(self):
        spec = SimpleNamespace(qid="Q1", max_marks=2.0, question_type="objective",
                               criteria=[_step("Q1.s1")])
        validate_question_marks(_mcq_without_a_breakdown(), spec)

    def test_a_descriptive_row_still_has_to_match_its_fixed_steps(self):
        spec = SimpleNamespace(qid="Q1.a", max_marks=5.0, question_type="unknown",
                               criteria=[_step("Q1.a.s1", 5.0)])
        question = QuestionMark(qid="Q1.a", score=5.0, max_score=5.0, page=1,
                                y_fraction=0.2, mark_style="per_step", criteria=[])
        with self.assertRaises(Exception):
            validate_question_marks(question, spec)

    def test_a_stray_step_id_on_an_objective_row_does_not_raise(self):
        """reconcile looks each returned id up in the fixed set; a miss must be skipped."""
        spec = SimpleNamespace(qid="Q1", max_marks=2.0, question_type="objective",
                               criteria=[_step("Q1.s1")])
        question = QuestionMark(
            qid="Q1", score=2.0, max_score=2.0, page=1, y_fraction=0.2,
            mark_style="single",
            criteria=[Criterion(description="option", awarded=2.0, max=2.0, met=True,
                                evidence="", rubric_criterion_id="Q1.s_not_in_the_key")])
        validate_question_marks(question, spec)


if __name__ == "__main__":
    unittest.main()
