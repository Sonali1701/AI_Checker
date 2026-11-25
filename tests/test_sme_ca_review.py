# -*- coding: utf-8 -*-
"""SME review of the 2026-09-19 CA batch (sheets of 2026-09-23), in code.

* Labels read as the paper prints them: "Q2(B)", not "QB2.B" (Auditing SME: "addition
  B before every question number is creating confusion").
* A CA paper's total is shown in whole marks (five SMEs: "ICAI does not award total
  marks in decimals").
* A missing section number costs nothing (Business Laws SME: "in CA marks are not
  deducted for a missing section number or article number").
* A page of answer with no mark on it gets a plain tick (Costing and FM SMEs: "Page
  no. 6 and 15 are not checked").
"""
from __future__ import annotations

import os
import sys
import unittest

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402
import pdf_renderer as pr  # noqa: E402
from test_teacher_layout import _build, _c, _q, _report, _sheet  # noqa: E402
from wording import question_label, question_labels, whole_mark_total  # noqa: E402


class Labels(unittest.TestCase):
    def test_internal_ids_read_as_the_paper_prints_them(self):
        cases = {"QB2.B": "Q2(B)", "FM.PB.Q1.A": "FM Q1(A)", "GST-OBJ-Q3": "GST MCQ 3",
                 "IT-SUB-Q2a": "IT Q2(a)", "Q1.a.i": "Q1(a)(i)", "Q8.C.1": "Q8(C)(1)",
                 "Q12": "Q12", "FM": "FM"}
        for qid, label in cases.items():
            self.assertEqual(question_label(qid), label, qid)
        self.assertEqual(question_label("Q1.5", objective=True), "MCQ 5")

    def test_a_part_letter_returns_only_to_tell_two_questions_apart(self):
        report = _report([_q("Q1", 2, 2, 1, "P1L1", objective=True), _q("QB1.A", 1, 2, 1, "P1L2"),
                          _q("QB2.A", 1, 2, 1, "P1L3")])
        report.section_totals = [{"qid": "Q1", "objective": True, "score": 2, "max_score": 2},
                                 {"qid": "QB1", "score": 1, "max_score": 2},
                                 {"qid": "QB2", "score": 1, "max_score": 2}]
        labels = question_labels(report)
        self.assertEqual(labels["QB1"], "Part B Q1")
        self.assertEqual(labels["QB2"], "Part B Q2", "the whole part names itself, not one question")
        self.assertEqual(labels["QB1.A"], "Part B Q1(A)")
        self.assertEqual(labels["Q1"], "Q1")
        plain = _report([_q("QB2.A", 1, 2, 1, "P1L1"), _q("QB3.B", 1, 2, 1, "P1L2")])
        self.assertEqual(question_labels(plain), {"QB2.A": "Q2(A)", "QB3.B": "Q3(B)"})


class WholeTotals(unittest.TestCase):
    def test_half_up(self):
        for exact, shown in ((77.5, 78), (20.25, 20), (70.5, 71), (19.5, 20), (31, 31), (40.75, 41)):
            self.assertEqual(whole_mark_total(exact), shown)

    def test_the_strip_shows_the_whole_total_only_on_a_ca_paper(self):
        rows = [("P1L1", "Ans 2 (b) The auditor should", 60, 400),
                ("P1L2", "obtain written representation", 60, 420)]
        for whole, expected in ((True, "Total Score: 2 out of 4"), (False, "Total Score: 1.5 out of 4")):
            doc, ocr = _sheet(rows)
            report = _report([_q("QB2.B", 1.5, 4, 1, "P1L2", [_c(1.5, 4, "P1L2")],
                                 remark="Written representations not explained.")])
            report.whole_total = whole
            out, _ = _build(doc, ocr, report)
            text = out[0].get_text()
            self.assertIn(expected, text)
            self.assertIn("Q2(B)", text)
            self.assertNotIn("QB2", text)


class SectionNumbers(unittest.TestCase):
    def test_the_rules_no_longer_ask_for_section_numbers(self):
        rules = grader.COMMERCE_CONVENTIONS_BLOCK + grader.PROFESSIONAL_EXAM_BLOCK
        self.assertNotIn("quote the section number", rules)
        self.assertIn("NEVER costs a mark", rules)
        self.assertIn("never a section of an Act", rules)
        for ruling in ("A remark must be true to the page", "Say the right answer, plainly",
                       "Mark wrong content where it is written", "not totalled",
                       "wrong regime", "Only what the page shows earns marks"):
            self.assertIn(ruling, rules)

    def test_code_does_not_zero_a_provision_for_a_missing_number(self):
        from mathpix import LineBox, PageOCR
        ocr = [PageOCR(1, 1000, 1400, [
            LineBox("P1L1", "A drawer of a dishonoured cheque is punishable", (0, 0, 9, 9), 1),
            LineBox("P1L2", "with imprisonment up to two years", (0, 0, 9, 9), 1),
            LineBox("P1L3", "so Mr A is liable", (0, 0, 9, 9), 1)])]
        q = _q("Q1.a", 3, 3, 1, "P1L3", [
            grader.Criterion(description="Provision cited: dishonour of cheque", awarded=1, max=1,
                             met=True, step_line_id="P1L1"),
            grader.Criterion(description="Application to facts", awarded=1, max=1, met=True,
                             step_line_id="P1L2"),
            grader.Criterion(description="Conclusion", awarded=1, max=1, met=True, step_line_id="P1L3")])
        report = _report([q])
        grader.enforce_evidence_against_awards(report, ocr)
        self.assertEqual([c.awarded for c in report.questions[0].criteria], [1, 1, 1])


class SeenPages(unittest.TestCase):
    def test_a_continuation_page_with_no_mark_gets_a_plain_tick(self):
        rows = [("P1L1", "Ans 4 (a) Statement of cost", 60, 400),
                ("P1L2", "Direct material 342000", 60, 400),
                ("P2L1", "WN 1 maximum lead time", 60, 400),
                ("P2L2", "normal usage x lead time", 60, 400),
                ("P2L3", "= 9 days", 60, 300)]
        doc, ocr = _sheet(rows, pages=2)
        q = _q("Q4.a", 7, 7, 1, "P1L2", [_c(7, 7, "P1L2")])
        report = _report([q])
        report.answer_regions = {"q4.a": {"line_ids": ["P1L1", "P1L2", "P2L1", "P2L2", "P2L3"]}}
        pdf = __import__("teacher_layout").TeacherLayout(doc, report, ocr)
        out = fitz.open(stream=pdf.build(), filetype="pdf")
        self.assertEqual(len(out), 2)
        scored = {row["page"] for row in report.render_manifest if row.get("role") != "feedback"}
        self.assertEqual(len(pdf.seen_pages), 1, "one page of the answer had no mark")
        self.assertEqual(scored | set(pdf.seen_pages), {1, 2}, "every page of the answer carries a mark")
        self.assertFalse(scored & set(pdf.seen_pages), "the courtesy tick carries no score")

    def test_a_page_already_marked_gets_nothing_more(self):
        rows = [("P1L1", "Ans 1 The company can sue", 60, 400), ("P1L2", "in its own name", 60, 400)]
        doc, ocr = _sheet(rows)
        report = _report([_q("Q1", 2, 2, 1, "P1L2", [_c(2, 2, "P1L2")])])
        report.answer_regions = {"q1": {"line_ids": ["P1L1", "P1L2"]}}
        layout = __import__("teacher_layout").TeacherLayout(doc, report, ocr)
        layout.build()
        self.assertEqual(layout.seen_pages, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
