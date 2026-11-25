# -*- coding: utf-8 -*-
"""Two Class 12 Commerce maths copies marked too low (2026-09-29).

* Core Maths: the photo cut off "1) 2) 3) 4)", the answers were paired with the
  wrong questions, and three correct MCQs were marked wrong. Q13's remark read
  "should be 1, not, 1" because the dash rule ate a minus sign.
* Applied Maths: "(3) 28 m" was read as "(B) 28 m", and "7.5 minutes" lost half a
  mark for not being written as "7 minutes 30 seconds".
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader  # noqa: E402
import preflight  # noqa: E402
from wording import mcq_remark, paper_numbers_options, plain_sentences, wording_problems  # noqa: E402


def _q(qid, score, maximum=1.0, remark="", objective=True, attempted=True):
    return grader.QuestionMark(qid=qid, score=score, max_score=maximum, page=1,
                               anchor_line_id="P1L1", y_fraction=0.5, remark=remark,
                               criteria=[], objective=objective, attempted=attempted)


def _report(questions, unconfirmed=()):
    report = grader.GradeReport(questions=questions,
                                total_score=sum(q.score for q in questions),
                                max_total=sum(q.max_score for q in questions),
                                overall_remarks="")
    report.objective_unconfirmed_qids = list(unconfirmed)
    return report


class MinusSign(unittest.TestCase):
    def test_a_minus_sign_is_read_as_minus_not_as_a_dash(self):
        self.assertEqual(plain_sentences("Inverse bottom-right entry should be 1, not −1"),
                         "Inverse bottom right entry should be 1, not minus 1.")
        self.assertEqual(plain_sentences("9I − A was computed wrongly"),
                         "9I minus A was computed wrongly.")
        self.assertEqual(plain_sentences("Only two points - third missing"),
                         "Only two points, third missing.")
        for text in ("should be 1, not −1", "x = -2"):
            self.assertEqual(wording_problems(plain_sentences(text)), [])


class OptionLabels(unittest.TestCase):
    def test_the_correct_option_uses_the_paper_s_own_label(self):
        self.assertEqual(mcq_remark("C", {"C": "28 m"}, numbered=True), "Correct answer is (3) 28 m.")
        self.assertEqual(mcq_remark("C", {"C": "28 m"}), "Correct answer is (C) 28 m.")
        numbered = "\n".join(f"{n}. Question (1) a (2) b (3) c (4) d" for n in range(1, 9))
        self.assertTrue(paper_numbers_options(numbered))
        self.assertFalse(paper_numbers_options(numbered.replace("(1)", "(A)").replace("(2)", "(B)")
                                               .replace("(3)", "(C)").replace("(4)", "(D)")))


class ReleaseGate(unittest.TestCase):
    def codes(self, report):
        return {i.code for i in preflight.release_blocking(preflight.check_copy(report))}

    def test_an_unconfirmed_mcq_marked_wrong_goes_to_faculty(self):
        report = _report([_q("Q1", 0), _q("Q2", 1)], unconfirmed=["Q1", "Q2"])
        self.assertIn("objective-unconfirmed-wrong", self.codes(report))

    def test_an_unconfirmed_mcq_that_was_credited_holds_too(self):
        # Since build 72 (Radiant review, 2026-09-30): "Q1 - option" was credited.
        report = _report([_q("Q1", 1), _q("Q2", 1)], unconfirmed=["Q2"])
        self.assertNotIn("objective-unconfirmed-wrong", self.codes(report))
        self.assertIn("objective-unconfirmed", self.codes(report))

    def test_a_remark_that_corrects_a_value_to_itself_holds_the_copy(self):
        report = _report([_q("Q13", 2, 3, "Inverse bottom right entry should be 1, not, 1.",
                             objective=False)])
        self.assertIn("remark-contradicts-itself", self.codes(report))
        fine = _report([_q("Q13", 2, 3, "Should be 1, not minus 1.", objective=False)])
        self.assertNotIn("remark-contradicts-itself", self.codes(fine))


class GradingRules(unittest.TestCase):
    def test_the_grader_is_told_about_equivalent_forms_and_option_numbers(self):
        prompt = grader.build_system_prompt("Mathematics", "Class 12")
        for rule in ("7.5 minutes = 7 minutes 30 seconds", "(1) (2) (3) (4)",
                     "never by guessing the order of the lines"):
            self.assertIn(rule, prompt)


if __name__ == "__main__":
    unittest.main()
