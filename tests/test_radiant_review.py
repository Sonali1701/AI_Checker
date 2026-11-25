# -*- coding: utf-8 -*-
"""Radiant Class 9 review (2026-09-30): Physics, English and Biology copies released
with marks for answers that were not there, the wrong "answer any two" answers
counted, misread values quoted back to the student, pages cut in half, and circles
that did not add up.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import fitz
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader  # noqa: E402
import orient  # noqa: E402
import pipeline  # noqa: E402
import preflight  # noqa: E402
import value_recheck  # noqa: E402
from strict_grading import effective_selection_policy  # noqa: E402
from test_teacher_layout import _build, _c, _q, _report, _sheet  # noqa: E402


class ChoiceRule(unittest.TestCase):
    def test_school_papers_keep_the_best_answers_and_ca_keeps_the_order(self):
        group = NS(selection_policy="first_attempted")
        self.assertEqual(effective_selection_policy(group, NS(department="school")), "best_score")
        self.assertEqual(effective_selection_policy(group, NS(department="commerce")), "best_score")
        self.assertEqual(effective_selection_policy(group, NS(department="ca")), "first_attempted")
        self.assertEqual(effective_selection_policy(NS(selection_policy="explicit"),
                                                    NS(department="school")), "explicit")

    def test_an_extra_answer_that_is_not_counted_says_so_on_the_page(self):
        rows = [("P1L1", "Q7 Volume = 1000 cm3 upthrust 35 N", 60, 400),
                ("P1L2", "Q9 a = F/m = 5, v = 15 m/s", 60, 400)]
        doc, ocr = _sheet(rows)
        report = _report([_q("Q7", 1, 2, 1, "P1L1", [_c(1, 2, "P1L1")])])
        report.uncounted_choice = [{"qid": "Q9", "page": 1, "anchor_line_id": "P1L2"}]
        out, manifest = _build(doc, ocr, report)
        text = out[0].get_text()
        self.assertIn("not counted", text)
        self.assertIn("Q9", text)
        self.assertTrue(any(r.get("role") == "uncounted" for r in manifest))


    def test_the_legacy_layout_names_the_uncounted_answer_on_the_cover(self):
        import pdf_renderer
        rows = [("P1L1", "Q7 Volume = 1000 cm3", 60, 400)]
        doc, ocr = _sheet(rows)
        report = _report([_q("Q7", 1, 2, 1, "P1L1", [_c(1, 2, "P1L1")])])
        report.uncounted_choice = [{"qid": "Q9", "page": 1, "anchor_line_id": ""},
                                   {"qid": "Q10", "page": 1, "anchor_line_id": ""}]
        with patch.object(pdf_renderer, "TEACHER_LAYOUT", False):
            pdf_renderer.build_evaluated_pdf(
                student_pdf_bytes=doc.tobytes(), student_filename="s.pdf",
                student_pngs=[doc[0].get_pixmap(dpi=72).tobytes("png")],
                report=report, ocr_pages=ocr)
        self.assertIn("Q9 and Q10 were not counted", report.overall_remarks)


class Circles(unittest.TestCase):
    def test_a_step_with_no_line_still_shows_in_the_circles(self):
        rows = [("P1L1", "Q10 a) The Himalayas are fold mountains", 60, 380),
                ("P1L2", "formed by the collision of plates", 60, 360)]
        doc, ocr = _sheet(rows)
        q = _q("Q10.a", 2, 2, 1, "P1L2", [_c(1, 1, "P1L1"), _c(1, 1, "P9L9")])
        _out, manifest = _build(doc, ocr, _report([q]))
        shown = sum(float(r["score_text"]) for r in manifest
                    if r.get("qid") == "Q10.a" and r.get("role") == "step" and r.get("score_text"))
        self.assertEqual(shown, 2.0)


class ReleaseGate(unittest.TestCase):
    def _report(self, questions, unconfirmed=()):
        r = _report(questions)
        r.objective_unconfirmed_qids = list(unconfirmed)
        return r

    def test_an_unconfirmed_mcq_holds_the_copy_even_when_credited(self):
        r = self._report([_q("Q1", 1, 1, 1, "P1L1", objective=True, is_correct=True)], ["Q1"])
        codes = {i.code for i in preflight.release_blocking(preflight.check_copy(r))}
        self.assertIn("objective-unconfirmed", codes)

    def test_an_mcq_credited_on_a_bare_label_holds_the_copy(self):
        rows = [("P1L1", "Q1 - option", 60, 200), ("P1L2", "Q2 (B) upon the heath", 60, 260)]
        _doc, ocr = _sheet(rows)
        r = self._report([_q("Q1", 1, 1, 1, "P1L1", objective=True, is_correct=True),
                          _q("Q2", 1, 1, 1, "P1L2", objective=True, is_correct=True)])
        issues = preflight.release_blocking(preflight.check_copy(r, ocr_pages=ocr))
        bare = [i for i in issues if i.code == "objective-bare-label"]
        self.assertEqual(len(bare), 1)
        self.assertIn("Q1", bare[0].detail)
        self.assertNotIn("Q2", bare[0].detail)


class GradingRules(unittest.TestCase):
    def test_the_grader_is_told_how_to_name_and_credit_answers(self):
        prompt = grader.build_system_prompt("Biology", "Class 9")
        for rule in ("name each\n   answer by what it answers", "Grade EVERY attempted alternative",
                     "One piece of writing belongs to ONE question", "bare label",
                     "naming two or more\n   options scores 0"):
            self.assertIn(rule, prompt)


def _landscape_pdf() -> bytes:
    img = Image.new("RGB", (1600, 1000), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    page.insert_image(page.rect, stream=buf.getvalue())
    return doc.tobytes()


class OpenNotebook(unittest.TestCase):
    """Sweety's Physics photo (2026-09-30): two facing pages in one image were cut at
    34% of the width, through the left page. Now the image is never cut; its lines
    are read left page first."""

    def _lines(self, rows):
        from mathpix import LineBox
        return [LineBox(line_id=f"P2L{i}", text=t, bbox=b, page=2)
                for i, (t, b) in enumerate(rows, start=1)]

    def test_two_facing_pages_are_read_left_page_first_without_cutting(self):
        from mathpix import order_spread_lines
        rows = [("According to gravitational force", (100, 100, 700, 150)),
                ("Q8 graph of volume", (1000, 110, 1500, 160)),
                ("f1 = f/(1/2)^2 = 4f", (120, 200, 600, 250)),
                ("minimum at 4 C", (1050, 210, 1400, 260)),
                ("Q7 N = 3.5 x 1000/10", (110, 300, 650, 350)),
                ("Q10 f = 40/60", (1020, 310, 1450, 360))]
        lines = order_spread_lines(self._lines(rows), 1755, 2)
        self.assertEqual([l.text[:3] for l in lines], ["Acc", "f1 ", "Q7 ", "Q8 ", "min", "Q10"])
        self.assertEqual([l.line_id for l in lines], [f"P2L{i}" for i in range(1, 7)])
        self.assertEqual(lines[0].bbox, (100, 100, 700, 150), "a line is never cut")

    def test_one_wide_page_keeps_its_order(self):
        from mathpix import order_spread_lines
        rows = [(f"Q7 volume of body = 1000 cm3 upthrust {i}", (100, 100 + 80 * i, 1600, 150 + 80 * i))
                for i in range(6)]
        lines = order_spread_lines(self._lines(rows), 1755, 2)
        self.assertEqual([l.line_id for l in lines], [f"P2L{i}" for i in range(1, 7)])

    def test_the_uploaded_photo_is_not_split(self):
        import server
        data = _landscape_pdf()
        with patch.object(server, "_is_digital_pdf", lambda *a: False), \
                patch.object(grader, "orientation_probe", return_value=[0]) as probe:
            out = server._make_orienter(NS(google_token="t", task_id="x"))(data, "s.pdf")
        probe.assert_called_once()
        self.assertEqual(len(fitz.open(stream=out, filetype="pdf")), 1)


class NotebookOnPortraitPage(unittest.TestCase):
    """Sonakshi's Biology copy (2026-09-30): an open notebook photographed wide and
    placed on a portrait A4 page. "3. (d)" was joined to the right page's row and
    its mark drawn there; Q4 and Q5 ran to the photo's edge."""

    def test_the_fold_is_found_from_the_writing_not_the_page(self):
        from mathpix import LineBox, looks_like_spread, spread_fold
        left = [LineBox(f"P1L{i}", "x", (60, 25 + 30 * i, 300 + 20 * (i % 3), 50 + 30 * i), 1)
                for i in range(8)]
        right = [LineBox(f"P1L{i + 20}", "y", (690, 30 + 30 * i, 1200, 55 + 30 * i), 1)
                 for i in range(8)]
        lines = left + right
        self.assertTrue(looks_like_spread(lines, 1240, 1755))
        fold = spread_fold(lines, 1240)
        self.assertTrue(360 < fold < 690, fold)

    def test_a_short_answer_is_not_joined_to_the_other_page(self):
        rows = [("P1L1", "3. (d)", 30, 90), ("P1L2", "Which plants die.", 330, 480)]
        rows += [(f"P1L{i}", "left page writing that continues", 30, 300) for i in range(3, 9)]
        rows += [(f"P1L{i}", "right page writing continues", 330, 560) for i in range(9, 15)]
        doc, ocr = _sheet(rows)
        for i, line in enumerate(ocr[0].lines):     # put each pair of rows level
            y = 120 + 22 * (i % 7)
            line.bbox = (line.bbox[0], y, line.bbox[2], y + 16)
        report = _report([_q("Q3", 1, 1, 1, "P1L1", objective=True, is_correct=True)])
        _out, manifest = _build(doc, ocr, report)
        mark = [m for m in manifest if m["qid"] == "Q3" and m["role"] == "answer"][0]
        self.assertLess(mark["bbox"][0], 330, "the mark stays on the left page")

    def test_small_writing_gets_small_circles(self):
        import teacher_layout
        rows = [(f"P1L{i}", f"{i}. (A)", 60, 140) for i in range(1, 9)]
        doc, ocr = _sheet(rows)
        for i, line in enumerate(ocr[0].lines):
            y = 100 + 11.5 * i
            line.bbox = (line.bbox[0], y, line.bbox[2], y + 9)
        layout = teacher_layout.TeacherLayout(doc, _report([]), ocr)
        self.assertLess(layout.circle_radius(1), 1.2 * layout.pitch)


class Spreads(unittest.TestCase):
    def test_the_vision_verdict_decides_whether_a_wide_photo_is_split(self):
        data = _landscape_pdf()
        kept, changed = orient.split_two_page_spreads(data, "x.pdf", spread_probe=lambda p: [False])
        self.assertFalse(changed)
        self.assertEqual(kept, data)
        split, changed = orient.split_two_page_spreads(data, "x.pdf", spread_probe=lambda p: [True])
        self.assertTrue(changed)
        self.assertEqual(len(fitz.open(stream=split, filetype="pdf")), 2)


class ValueRecheck(unittest.TestCase):
    def _case(self):
        rows = [("P1L1", "Q6 b) A lunar month is approximately 29.5 days", 60, 420)]
        doc, ocr = _sheet(rows)
        q = _q("Q6.b", 0.5, 1, 1, "P1L1", [_c(0.5, 1, "P1L1")],
               remark="Should be 29.5, not 20.5.")
        png = doc[0].get_pixmap(dpi=72).tobytes("png")
        ocr[0].lines[0].text = "Q6 b) A lunar month is approximately 20.5 days"
        ocr[0].image_width, ocr[0].image_height = doc[0].rect.width, doc[0].rect.height
        return _report([q]), [png], ocr

    def test_a_misread_value_sends_the_copy_to_faculty(self):
        report, pngs, ocr = self._case()
        self.assertEqual([v for _q_, v in value_recheck.quoted_values(report)], ["20.5"])
        reasons = value_recheck.recheck_quoted_values(
            report, pngs, ocr, google_token="t", task_id="x",
            call=lambda png, **k: "approximately 29.5 days")
        self.assertEqual(len(reasons), 1)
        self.assertIn("20.5", reasons[0])

    def test_a_confirmed_value_changes_nothing(self):
        report, pngs, ocr = self._case()
        reasons = value_recheck.recheck_quoted_values(
            report, pngs, ocr, google_token="t", task_id="x",
            call=lambda png, **k: "approximately 20.5 days")
        self.assertEqual(reasons, [])


class PhotoSize(unittest.TestCase):
    def test_a_tiny_photo_is_checked_and_flagged_not_held(self):
        # No faculty to mark it by hand (user, 29 Oct 2026): it is checked like any copy.
        img = Image.new("RGB", (720, 500), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.assertEqual(pipeline.largest_photo_edge(buf.getvalue(), "a.png"), 720)

        class Reached(Exception):
            pass

        def graded(**kwargs):
            raise Reached()

        with patch.object(pipeline, "grade_prepared", graded), self.assertRaises(Reached):
            pipeline.grade_sheet(qp_pngs=[], sa_bytes=buf.getvalue(), filename="a.png",
                                 ak_pngs=None, ak_text=None, marks_scheme=None, cfg=NS())


if __name__ == "__main__":
    unittest.main()
