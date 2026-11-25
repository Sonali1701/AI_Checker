# -*- coding: utf-8 -*-
"""Radiant Class 9 Maths Test 02 review (2026-09-30).

Missed answers on pages the OCR could not read, a Hindi notebook graded as Maths, a
genuine copy refused as "another test", a copy lost to a question read twice, MCQ
remarks naming another question's options, and a paper "out of 28" instead of 40.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader  # noqa: E402
import mathpix  # noqa: E402
import preflight  # noqa: E402
from paper_match import PaperMatchVerdict, PaperMismatch, wrong_paper_reason  # noqa: E402
from strict_grading import option_texts_from_paper  # noqa: E402
from test_teacher_layout import _q, _report, _sheet  # noqa: E402

PAPER = """Q1
The rational number equivalent to 0.82 is:
(A) 82/100 (B) 82/99
Q2
If y = 3 - 2 root 2, then (y - 1/y) = ____
(A) 9
(B) 81
(C) 4
(D) 32
3+
7
Q3
If (3 + root 7)/(3 - root 7) = a + b root 7, then a and b are
(A) a = 8, b = 3
(B) a = 3, b = 8
(C) a = 16, b = 6
(D) a = 8, b = 6
Q9
Rationalize the denominator of \\frac{2+\\sqrt{5}}{2-\\sqrt{5}}.
"""


class PaperOptions(unittest.TestCase):
    def test_a_number_inside_a_formula_is_not_a_question(self):
        options = option_texts_from_paper(PAPER)
        self.assertEqual(options[2]["B"], "81")
        self.assertEqual(options[3]["A"], "a = 8, b = 3")
        self.assertNotIn(7, options)


class PaperCheck(unittest.TestCase):
    def _page(self, text):
        return [mathpix.PageOCR(1, 1000, 1400, [mathpix.LineBox("P1L1", text, (0, 0, 9, 9), 1)])]

    def test_a_student_copying_this_papers_question_is_not_another_test(self):
        wrote = "\\frac{2+\\sqrt{5}}{2-\\sqrt{5}} \\times \\frac{2+\\sqrt{5}}{2+\\sqrt{5}}"
        verdict = PaperMatchVerdict(verdict="different_paper", confidence=0.95, mismatches=[
            PaperMismatch(question="Q9", student_wrote=wrote,
                          paper_asks="Rationalize the denominator of \\frac{2+\\sqrt{5}}{2-\\sqrt{5}}"),
            PaperMismatch(question="Q2", student_wrote="y - 1/y = 32 ans",
                          paper_asks="If y = 3 - 2 root 2, then (y - 1/y)")])
        self.assertEqual(wrong_paper_reason(verdict, self._page(wrote + " y - 1/y = 32 ans"), PAPER), "")

    def test_a_hindi_notebook_is_held_not_graded(self):
        source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "pipeline.py"), encoding="utf-8").read()
        self.assertIn('"NOT_VERIFIED_ANSWER_SHEET"', source)
        import server
        self.assertNotIn("NOT_VERIFIED_ANSWER_SHEET", server._REJECTED_UPLOAD_CODES,
                         "it goes to a faculty reviewer, not the rejected pile")


class UnreadPages(unittest.TestCase):
    def test_a_blank_answer_beside_an_unread_page_holds_the_copy(self):
        rows = [("P1L1", "Q13 A = P(1 + r/100)^n = 117936", 60, 400),
                ("P1L2", "Q14 CI = 20840", 60, 300)]
        _doc, ocr = _sheet(rows, pages=2)
        report = _report([_q("Q13", 2, 2, 1, "P1L1"),
                          _q("Q15.ii", 0, 3, 2, "", attempted=False, remark="Not attempted.")])
        report.unresolved_answer_qids = ["Q15.ii"]
        codes = {i.code for i in preflight.release_blocking(preflight.check_copy(report, ocr_pages=ocr))}
        self.assertIn("answer-maybe-unread", codes)
        report.unresolved_answer_qids = []
        codes = {i.code for i in preflight.release_blocking(preflight.check_copy(report, ocr_pages=ocr))}
        self.assertNotIn("answer-maybe-unread", codes)

    def test_the_second_read_maps_boxes_back_to_the_photo(self):
        img = Image.new("L", (1000, 1400), 20)
        ImageDraw.Draw(img).rectangle((200, 300, 800, 1100), fill=200)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
        _enhanced, (x0, y0), scale = mathpix.paper_crop(png)

        def fake_ocr(sent, page_num, **_kw):
            return mathpix.PageOCR(page_num, 0, 0, [mathpix.LineBox(
                "P1L1", "Q15 b2/a2", (10 * scale, 20 * scale, 110 * scale, 60 * scale), page_num)])

        with patch.object(mathpix, "ocr_page", fake_ocr):
            page = mathpix.ocr_page_enhanced(png, 1, google_token="t", task_id="x")
        self.assertEqual((page.image_width, page.image_height), (1000, 1400))
        bx = page.lines[0].bbox
        self.assertAlmostEqual(bx[0], x0 + 10, places=3)
        self.assertAlmostEqual(bx[1], y0 + 20, places=3)


class GradingRules(unittest.TestCase):
    def test_the_grader_checks_final_answers_and_values(self):
        prompt = grader.build_system_prompt("Mathematics", "Class 9")
        for rule in ("Check the final answer against the key", "MCQ written as a value",
                     "Find every answer before calling one unattempted", "crossed-out heading"):
            self.assertIn(rule, prompt)


if __name__ == "__main__":
    unittest.main()
