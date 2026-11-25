# -*- coding: utf-8 -*-
"""UP LT Grade Home Science review (23 Oct 2026).

* The cover listed "Part A Q1, Part B Q1, Part A Q2, ...": a paper in lettered
  sections (QA1..QA10, QB1..QB10) was ordered on the number before the section.
* Q9 lost 3 of 8 marks: the AI key gave classes of body motion for Mundel's
  classification, and the grader crossed out the student's correct five classes of
  change as "applications".
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import teaching_key  # noqa: E402
from categories.teaching import TEACHING_RULES  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from pdf_renderer import _prepare_report_totals  # noqa: E402


def _order(qids, objective=()):
    questions = [QuestionMark(qid=q, score=1, max_score=1, objective=q in objective,
                              page=1, y_fraction=0.1, attempted=True) for q in qids]
    report = GradeReport(total_score=len(qids), max_total=len(qids),
                         overall_remarks="", questions=questions)
    _prepare_report_totals(report)
    return [s["qid"] for s in report.section_totals]


class LetteredSections(unittest.TestCase):
    def test_each_section_is_listed_in_full_before_the_next(self):
        graded = [f"Q{s}{n}" for n in range(1, 11) for s in "AB"]   # grader's own order
        self.assertEqual(_order(graded),
                         [f"QA{n}" for n in range(1, 11)] + [f"QB{n}" for n in range(1, 11)])

    def test_unlettered_part_a_stays_before_part_b(self):
        # CA FM MT01: Part A Q1..Q4 (objective), Part B QB1..QB3.
        qids = ["QB1.A", "Q1.A", "QB2", "Q2", "QB3.A", "Q3", "Q4"]
        self.assertEqual(_order(qids, objective={"Q1.A", "Q2", "Q3", "Q4"}),
                         ["Q1", "Q2", "Q3", "Q4", "QB1", "QB2", "QB3"])

    def test_plain_numbering_and_sub_parts_keep_their_order(self):
        self.assertEqual(_order(["Q10", "Q2", "Q1b", "Q1a", "Q1"]),
                         ["Q1", "Q1a", "Q1b", "Q2", "Q10"])


class KeyCanBeWrong(unittest.TestCase):
    def test_rule_judges_named_classifications_on_the_textbooks(self):
        self.assertIn("16. The model key was written by an AI", TEACHING_RULES)
        self.assertIn("five classes of change", TEACHING_RULES)

    def test_key_prompt_gives_the_textbook_classification(self):
        self.assertIn("five classes of change", teaching_key.TEACHING_KEY_SYSTEM_PROMPT)
        self.assertEqual(teaching_key.TEACHING_KEY_VERSION, "2026-10-23.1")


if __name__ == "__main__":
    unittest.main()
