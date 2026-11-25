# -*- coding: utf-8 -*-
"""One written answer counts under one OR alternative, and a one-mark option
question is settled by the grader's own evidence (c0f259bb, 2026-09-17)."""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
from grader import Criterion, GradeReport, QuestionMark  # noqa: E402


def _q(qid, score, mx, page, y, anchor, crits=(), **kw):
    return QuestionMark(qid=qid, score=score, max_score=mx, page=page, y_fraction=y,
                        anchor_line_id=anchor, attempted=True,
                        criteria=[Criterion(description=d, awarded=a, max=m, met=a >= m,
                                            evidence=e, anchor_quote=e, step_line_id=anchor)
                                  for d, a, m, e in crits], **kw)


def _package():
    group = lambda gid, a, b: NS(group_id=gid, select_count=1, selection_policy="first_attempted",
                                 contribution_marks=4.0,
                                 options=[NS(option_id="1", question_ids=[a]), NS(option_id="2", question_ids=[b])])
    return NS(questions=[], choice_groups=[group("choice-3", "Q12.alt1", "Q12.alt2")])


class CollapseTests(unittest.TestCase):
    def test_the_alternative_the_answer_scored_on_stays(self):
        report = GradeReport(total_score=3, max_total=8, overall_remarks="", questions=[
            _q("Q12.alt1", 0, 4, 4, 0.5, "P4D2", [("Capital account", 0, 4, "closing capital")]),
            _q("Q12.alt2", 3, 4, 4, 0.5, "P4D2", [("Adjustment entry", 3, 4, "As cap Dr 4240")])])
        pipeline._collapse_shared_alternative_attempts(report, _package())
        alt1, alt2 = report.questions
        self.assertFalse(alt1.attempted)
        self.assertEqual(alt1.score, 0)
        self.assertTrue(alt2.attempted)
        self.assertEqual(alt2.score, 3)

    def test_two_different_answers_are_both_kept(self):
        report = GradeReport(total_score=5, max_total=8, overall_remarks="", questions=[
            _q("Q12.alt1", 2, 4, 4, 0.2, "P4D2"),
            _q("Q12.alt2", 3, 4, 5, 0.6, "P5D9")])
        pipeline._collapse_shared_alternative_attempts(report, _package())
        self.assertTrue(all(q.attempted for q in report.questions))

    def test_a_tie_keeps_the_papers_order_and_says_so(self):
        report = GradeReport(total_score=0, max_total=8, overall_remarks="", questions=[
            _q("Q12.alt1", 0, 4, 4, 0.5, "P4D2"),
            _q("Q12.alt2", 0, 4, 4, 0.5, "P4D2")])
        notes = pipeline._collapse_shared_alternative_attempts(report, _package())
        self.assertTrue(report.questions[0].attempted)
        self.assertFalse(report.questions[1].attempted)
        self.assertEqual(len(notes), 1)


class OptionTests(unittest.TestCase):
    def test_the_students_letter_against_the_keys_option_decides(self):
        report = GradeReport(total_score=1.5, max_total=2, overall_remarks="", questions=[
            _q("Q4", 1, 1, 1, 0.5, "P1L15",
               [("Option B: Both A and R correct, R not explanation of A", 1, 1,
                 "A) Bath A and R are toue and R is the comect explanation")], chosen_option="A"),
            _q("Q9", 0.5, 1, 2, 0.5, "P2L45",
               [("Option B: Sacrifice 1/30", 0.5, 1, "B) Saerific = 4/30")],
               remark="B's sacrifice is 1/30, not 4/30.")])
        notes = pipeline._settle_option_questions(report)
        q4, q9 = report.questions
        self.assertEqual((q4.score, q4.criteria[0].awarded, q4.is_correct), (0.0, 0.0, False))
        self.assertEqual((q9.score, q9.criteria[0].awarded, q9.remark), (1.0, 1.0, ""))
        self.assertEqual(len(notes), 2)
        self.assertTrue(q4.objective and q9.objective)
        self.assertEqual((q4.chosen_option, q9.chosen_option), ("A", "B"))

    def test_descriptive_and_multi_step_rows_are_left_alone(self):
        report = GradeReport(total_score=1, max_total=4, overall_remarks="", questions=[
            _q("Q7", 0.5, 1, 2, 0.5, "P2L34", [("Profit available Rs.40,000", 0.5, 1, "D) 71000")]),
            _q("Q10", 1, 3, 3, 0.5, "P3D3", [("Option B: x", 1, 1, "A) y"), ("Salary", 0, 2, "z")])])
        self.assertEqual(pipeline._settle_option_questions(report), [])
        self.assertEqual([q.score for q in report.questions], [0.5, 1])


if __name__ == "__main__":
    unittest.main()
