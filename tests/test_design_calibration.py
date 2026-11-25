# -*- coding: utf-8 -*-
"""Design marking calibrated on faculty marks (NID Mock 1, 11 sheets, 2026-10-05).

The Design team checked the AI's copies and wrote: hands distorted on a figure were
not mentioned, reviews should be constructive, posters are judged on space,
typography, alignment and execution, products need several views, labels and a
larger drawing, and "marks will be deducted more". Their own marks ran from 16 to
60 out of 70 while builds 61 to 64 kept every sheet between 28 and 45.5: the
prompt told the marker that MOST sheets earn 40 to 54 percent and capped any
criterion with a reported fault at 60 percent.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import design_grading as dg  # noqa: E402
from tools import design_calibrate as cal  # noqa: E402


class CalibrationSet(unittest.TestCase):
    def test_faculty_marks_add_up(self):
        data = cal.load()
        maxima = {q: v["max"] for q, v in data["questions"].items()}
        sheets = cal.scored_sheets(data)
        self.assertEqual(len(sheets), 11)
        for sheet in sheets:
            fac = sheet["faculty"]
            self.assertAlmostEqual(sum(fac.values()), sheet["faculty_total"], places=2, msg=sheet["student"])
            for q, mark in fac.items():
                self.assertLessEqual(mark, maxima[q], (sheet["student"], q))

    def test_the_faculty_use_the_whole_range(self):
        totals = [s["faculty_total"] for s in cal.scored_sheets(cal.load())]
        self.assertLessEqual(min(totals), 16)
        self.assertGreaterEqual(max(totals), 60)

    def test_compare_arithmetic(self):
        data = {"questions": {"Q1": {"max": 10}, "Q2": {"max": 20}},
                "sheets": [{"student": "a", "faculty": {"Q1": 5, "Q2": 10}},
                           {"student": "b", "faculty": {"Q1": 9, "Q2": 18}},
                           {"student": "c", "faculty": {"Q1": 2, "Q2": 4}}]}
        result = cal.compare({"a": {"Q1": 6, "Q2": 10}, "b": {"Q1": 7, "Q2": 14},
                              "c": {"Q1": 4, "Q2": 8}}, data)
        self.assertEqual([r["delta"] for r in result["sheets"]], [1, -6, 6])
        self.assertEqual(result["total_mae"], 4.33)
        self.assertEqual(result["questions"]["Q1"]["mae_pct"], round((10 + 20 + 20) / 3, 1))
        self.assertEqual(result["rank_correlation"], 1.0)

    def test_the_old_builds_squeezed_the_marks(self):
        result = cal.compare(cal.ai_before(cal.load()))
        self.assertLess(result["total_ai_sd"], 0.6 * result["total_faculty_sd"])


class MarkingStandard(unittest.TestCase):
    prompt = dg.DESIGN_MARKING_SYSTEM_PROMPT

    def test_no_band_pulls_every_sheet_to_the_middle(self):
        self.assertNotIn("MOST sheets", self.prompt)
        self.assertIn("using the WHOLE range", self.prompt)
        self.assertIn("Never pull marks towards the middle", self.prompt)

    def test_faculty_anchors_across_the_range(self):
        for anchor in ("18 out of 20", "14 out of 15", "8 out of 20", "4 out of 20", "2 out of 20"):
            self.assertIn(anchor, dg.DESIGN_MARKING_BANDS)

    def test_a_minor_fault_does_not_halve_a_strong_drawing(self):
        self.assertIn("A minor fault you report costs that criterion a mark or so", self.prompt)
        self.assertIn("A serious fault you report", self.prompt)

    def test_written_sub_tasks_cost_little(self):
        self.assertIn("A written sub task (a price, a materials list, a short description)", self.prompt)
        self.assertIn("together carry at most 20 percent", dg.DESIGN_PAPER_SYSTEM_PROMPT)

    def test_what_the_faculty_check(self):
        checks = dg.DESIGN_FACULTY_CHECKS
        self.assertIn(checks, self.prompt)
        for phrase in ("HANDS", "one system", "three point perspective", "a three year old",
                       "presentation slide", "negative space", "about half the sheet",
                       "more than one view", "labels", "simple, minimal, memorable"):
            self.assertIn(phrase, checks)

    def test_feedback_is_constructive(self):
        self.assertIn("constructive and specific", self.prompt)
        self.assertIn("on what genuinely works", self.prompt)
        self.assertIn("the design principle and what to do or practise", self.prompt)
        self.assertIn("not a list of missing items", self.prompt)

    def test_the_main_demand_rule_stays(self):
        self.assertIn("THE MAIN DEMAND", self.prompt)
        self.assertEqual((dg.MAIN_DEMAND_CRITERION_SHARE, dg.MAIN_DEMAND_QUESTION_SHARE), (0.40, 0.50))


class RubricIsReadAgain(unittest.TestCase):
    def test_a_new_rubric_prompt_changes_the_paper_cache_key(self):
        import server
        key = server._design_paper_key(b"paper", b"key")
        saved = dg.DESIGN_PAPER_SYSTEM_PROMPT
        try:
            dg.DESIGN_PAPER_SYSTEM_PROMPT = saved + " changed"
            self.assertNotEqual(server._design_paper_key(b"paper", b"key"), key)
        finally:
            dg.DESIGN_PAPER_SYSTEM_PROMPT = saved
        self.assertEqual(server._design_paper_key(b"paper", b"key"), key)


if __name__ == "__main__":
    unittest.main()
