# -*- coding: utf-8 -*-
"""CA faculty review of the corrected FM MT01 copies (23 Oct 2026), in code.

All five were judged correctly checked; two presentation points were raised:
"Last page is not checked" (1bc804b5: a sideways photo the OCR read nothing from) and
"Sol 2 tick marks were missing" (8bc28fde: the Kp and Kd steps, each worked in a block
of several rows with a garbled OCR quote and no certificate, were folded into the
first step's mark).
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import Criterion, GradeReport, QuestionMark  # noqa: E402

# A CA copy's certificates carry checking plans, which send it to the legacy renderer
# (teacher_layout.needs_legacy_layout); this one stands for them.
_LEGACY = {"qb.9:0": {"award_scope": "section"}}


def _write(page, texts, top, pitch):
    for k, text in enumerate(texts):
        page.insert_text((60, top + pitch * k + 16), text, fontsize=16)


class StepsWithoutAPlaceableQuote(unittest.TestCase):
    def build(self, whole_total):
        import fitz
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        _write(page, ["Solution 2", "Ke = 14% + 1.2(19-14)% = 20%"], 100, 40)
        _write(page, ["Kp = 10% + 48.71/97.46 x 4", "= 10% + 2%", "= 12%",
                      "Kd = I(1-t) + (RV-NP)/n", "= 9.8/98", "= 10.81%"], 220, 75)
        s = 2.0       # OCR pixels per point
        lines = [LineBox("P1L1", "Solution 2", (60 * s, 100 * s, 360 * s, 120 * s), 1),
                 LineBox("P1L2", "Ke = 14% + 1.2(19-14)% = 20%", (60 * s, 140 * s, 360 * s, 160 * s), 1),
                 # Kp and Kd are worked in blocks of several rows, as OCR returns them.
                 LineBox("P1L3", "Kp = 10 % + 48.71 / 97.46 x 4 = 10 % + 2 % = 12 %",
                         (60 * s, 220 * s, 360 * s, 450 * s), 1, kind="math"),
                 LineBox("P1L4", "Kd = I(1-t) + (RV-NP)/n = 9.8 / 98 = 10.81 %",
                         (60 * s, 445 * s, 360 * s, 675 * s), 1, kind="math")]
        ocr = [PageOCR(1, 1200, 1600, lines)]
        q = QuestionMark(qid="QB2", score=6.0, max_score=9, page=1, y_fraction=.15, attempted=True,
                         anchor_line_id="P1L2", mark_style="per_step", remark="Kd is wrong.",
                         criteria=[Criterion(description="Ke = 20%", awarded=1, max=1, met=True,
                                             step_line_id="P1L2", anchor_quote="14% + 1.2(19-14)% = 20%"),
                                   Criterion(description="Kp = 12%", awarded=3.5, max=4, met=False,
                                             step_line_id="P1L3", anchor_quote="10 %+17-127-(90000)"),
                                   Criterion(description="Kd = 9.39%", awarded=1.5, max=4, met=False,
                                             step_line_id="P1L4", anchor_quote="=10.817 .")])
        report = GradeReport(total_score=6, max_total=9, overall_remarks="", evidence_contract=True,
                             whole_total=whole_total, questions=[q], verified_step_boxes=dict(_LEGACY),
                             answer_regions={"qb.2": {"qid": "QB2", "status": "located", "pages": [1],
                                                      "line_ids": ["P1L1", "P1L2", "P1L3", "P1L4"]}})
        return doc, ocr, report

    def placed(self, whole_total):
        import pdf_renderer as pr
        doc, ocr, report = self.build(whole_total)
        pr.build_evaluated_pdf(doc.tobytes(), "copy.pdf", [], report, ocr_pages=ocr, mark_scale=0.6)
        return {tuple(r.get("criteria") or []): r for r in report.render_manifest
                if r.get("qid") == "QB2" and r.get("role") == "step"}

    def test_a_ca_step_is_ticked_where_its_own_cited_working_ends(self):
        placed = self.placed(True)
        self.assertEqual(placed[(1,)]["line_id"], "P1L3", list(placed))
        self.assertEqual(placed[(2,)]["line_id"], "P1L4", list(placed))
        self.assertGreater(placed[(1,)]["bbox"][1], 380, "at the end of the block, beside = 12%")

    def test_other_categories_tick_it_there_too(self):
        # Commerce SME review, 29 Oct 2026 (Online Applied Maths): a step joined to
        # another step's mark read "awarded 1 but written 2". See test_commerce_sme_1029.
        placed = self.placed(False)
        self.assertEqual(placed[(1,)]["line_id"], "P1L3", list(placed))
        self.assertEqual(placed[(2,)]["line_id"], "P1L4", list(placed))


class PagesTheOcrCouldNotRead(unittest.TestCase):
    def test_a_written_page_with_no_ocr_text_is_noted_and_named_for_the_faculty(self):
        import fitz
        import pdf_renderer as pr
        from mathpix import LineBox, PageOCR
        doc = fitz.open()
        for _ in range(3):
            doc.new_page(width=600, height=800)
        _write(doc[0], ["Q1 (a) EBIT = 22,50,000", "FL = 1.8", "Fixed cost = 4,50,000"], 100, 40)
        doc[1].draw_rect(fitz.Rect(50, 50, 550, 750), color=(0.3, 0.3, 0.3), fill=(0.3, 0.3, 0.3))
        doc[2].draw_rect(fitz.Rect(10, 10, 11, 11), color=(1, 1, 1))     # a blank scanned page
        lines = [LineBox(f"P1L{k + 1}", t, (120, (100 + 40 * k) * 2, 720, (120 + 40 * k) * 2), 1)
                 for k, t in enumerate(["Q1 (a) EBIT = 22,50,000", "FL = 1.8", "Fixed cost = 4,50,000"])]
        ocr = [PageOCR(1, 1200, 1600, lines), PageOCR(2, 1200, 1600, []),
               PageOCR(3, 1200, 1600, [], blank=True)]
        q = QuestionMark(qid="QB1", score=2, max_score=2, page=1, y_fraction=.15, attempted=True,
                         anchor_line_id="P1L1", mark_style="single", criteria=[])
        report = GradeReport(total_score=2, max_total=2, overall_remarks="", evidence_contract=True,
                             whole_total=True, questions=[q], verified_step_boxes=dict(_LEGACY))
        notes = []
        real = pr._draw_strip_entry

        def capture(page, qid, *args, **kwargs):
            if kwargs.get("note"):
                notes.append((page.number + 1, kwargs["note"]))
            return real(page, qid, *args, **kwargs)
        with patch.object(pr, "_draw_strip_entry", capture):
            pr.build_evaluated_pdf(doc.tobytes(), "copy.pdf", [], report, ocr_pages=ocr, mark_scale=0.6)
        self.assertIn((2, "Not evaluated: this page could not be read."), notes)
        self.assertNotIn(3, [p for p, _ in notes], "a blank page needs no note")
        self.assertTrue(any("Page 2 could not be read" in r for r in report.review_reasons))


if __name__ == "__main__":
    unittest.main()
