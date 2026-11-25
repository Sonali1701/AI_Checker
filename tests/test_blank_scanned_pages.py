# -*- coding: utf-8 -*-
"""A blank scanned page is not an unread page (CBSE Class 9 Test-03, 2026-10-01).

Twelve scanned booklets: the ruled pages a student left empty after the last answer
came back from the OCR with no lines, and preflight held 10 of the 12 copies with
"page(s) 4, 5, 6 could not be read". Those pages carry only the printed ruling and
faint mirrored show-through from the back of the sheet.
"""
from __future__ import annotations

import io
import os
import random
import sys
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mathpix  # noqa: E402
import preflight  # noqa: E402
from pipeline import blank_scanned_page  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_teacher_layout import _q, _report  # noqa: E402


def _page(paper=246, ruling=True, show_through=True, writing=0, pencil=False) -> bytes:
    rng = random.Random(7)
    img = Image.new("L", (1240, 1754), paper)
    draw = ImageDraw.Draw(img)
    if ruling:
        for y in range(200, 1650, 37):
            draw.line((150, y, 1100, y), fill=60, width=2)
    if show_through:
        for row in range(6):
            y = 230 + row * 37
            for _ in range(40):
                x = rng.randint(160, 1000)
                draw.line((x, y, x + rng.randint(5, 20), y + rng.randint(-8, 8)), fill=200, width=2)
    ink = 150 if pencil else 30
    for row in range(writing):          # words of letter-sized strokes, gaps between
        y = 215 + row * 37
        x = 170
        for word in range(6):
            for _ in range(rng.randint(3, 7)):
                draw.line((x, y, x + 6, y - 22), fill=ink, width=3)
                draw.line((x + 6, y - 22, x + 12, y), fill=ink, width=3)
                x += 16
            x += 30
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class BlankScannedPage(unittest.TestCase):
    def test_ruling_and_show_through_only_is_blank(self):
        self.assertTrue(blank_scanned_page(_page()))
        self.assertTrue(blank_scanned_page(_page(ruling=False)))

    def test_a_few_written_lines_are_not_blank(self):
        self.assertFalse(blank_scanned_page(_page(writing=3)))
        self.assertFalse(blank_scanned_page(_page(writing=3, pencil=True)))

    def test_a_grey_photo_is_never_called_blank(self):
        self.assertFalse(blank_scanned_page(_page(paper=180)))

    def test_written_photo_on_large_white_canvas_is_not_ruling(self):
        image = Image.new("L", (1240, 1754), 255)
        # A sideways notebook photo occupies the middle third of the PDF.
        photo = Image.open(io.BytesIO(_page(paper=180, writing=18))).rotate(90, expand=True)
        photo.thumbnail((1180, 740))
        image.paste(photo, ((image.width - photo.width) // 2, 500))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        self.assertFalse(blank_scanned_page(buf.getvalue()))

    def test_the_flag_survives_the_wire_format(self):
        page = mathpix.PageOCR(4, 1240, 1754, [], blank=True)
        self.assertTrue(mathpix.pageocr_from_dict(mathpix.pageocr_to_dict(page)).blank)
        self.assertFalse(mathpix.pageocr_from_dict({"page": 1, "image_width": 9,
                                                    "image_height": 9}).blank)


class ShowThroughLines(unittest.TestCase):
    def test_unsure_lines_on_a_blank_page_are_dropped_and_sure_ones_kept(self):
        from unittest.mock import patch
        import pipeline

        def page(n, conf):
            return mathpix.PageOCR(n, 1240, 1754, [
                mathpix.LineBox(f"P{n}L{i}", "1. salo dowslard", (60, 100 * i, 700, 100 * i + 40),
                                n, confidence=conf) for i in range(1, 15)])
        pages = [page(1, 0.13), page(2, 0.9)]
        cfg = type("Cfg", (), {"google_token": "", "task_id": "t"})()
        with patch.object(pipeline, "lines_run_vertically", lambda p: (False, 0, 0)):
            pipeline._reread_sideways_pages([_page(), _page()], pages, cfg=cfg,
                                            filename="x.pdf", usage_out={})
        self.assertTrue(pages[0].blank)
        self.assertEqual(pages[0].lines, [])
        self.assertFalse(pages[1].blank)
        self.assertEqual(len(pages[1].lines), 14)


class UpsideDownFlipNeedsTheProbe(unittest.TestCase):
    """mat4's upright page 4 read as more gibberish lines upside down and was flipped."""

    def _run(self, probe_angle):
        from unittest.mock import patch
        import grader
        import pipeline
        upright = mathpix.PageOCR(1, 1240, 1754, [
            mathpix.LineBox(f"P1L{i}", "(16) (i) a+b", (60, 300 * i, 700, 300 * i + 40), 1,
                            confidence=0.6) for i in (1, 2)])
        noise = mathpix.PageOCR(1, 1240, 1754, [
            mathpix.LineBox(f"P1L{i}", "1=6+n", (60, 300 * i, 700, 300 * i + 40), 1,
                            confidence=0.98) for i in range(1, 5)])
        reads = iter([upright, noise])
        pages = [upright]
        cfg = type("Cfg", (), {"google_token": "t", "task_id": "k"})()
        with patch.object(pipeline, "lines_run_vertically", lambda p: (False, 0, 0)), \
                patch("mathpix.ocr_page_enhanced", lambda *a, **k: next(reads)), \
                patch.object(pipeline, "mark_struck_lines", return_value=0), \
                patch.object(grader, "orientation_probe", return_value=[probe_angle]):
            rotated = pipeline._reread_sideways_pages([_page(writing=3)], pages, cfg=cfg,
                                                      filename="x.pdf", usage_out={})
        return rotated, pages[0]

    def test_more_lines_upside_down_do_not_flip_an_upright_page(self):
        rotated, page = self._run(0)
        self.assertEqual(rotated, {})
        self.assertEqual(page.lines[0].text, "(16) (i) a+b")

    def test_the_probe_confirms_a_real_upside_down_page(self):
        rotated, page = self._run(180)
        self.assertEqual(rotated, {1: 180})
        self.assertEqual(page.lines[0].text, "1=6+n")


class UnreadPageHold(unittest.TestCase):
    def _codes(self, blank):
        line = mathpix.LineBox("P1L1", "Q13 (105)^2 - (95)^2 = 2000", (60, 400, 700, 450), 1)
        ocr = [mathpix.PageOCR(1, 1240, 1754, [line] * 3),
               mathpix.PageOCR(2, 1240, 1754, [], blank=blank)]
        report = _report([_q("Q13", 2, 2, 1, "P1L1"),
                          _q("Q14", 0, 3, 2, "", attempted=False, remark="Not attempted.")])
        report.unresolved_answer_qids = ["Q14"]
        return {i.code for i in preflight.release_blocking(preflight.check_copy(report, ocr_pages=ocr))}

    def test_a_blank_page_does_not_hold_the_copy(self):
        self.assertNotIn("answer-maybe-unread", self._codes(blank=True))

    def test_an_empty_page_with_ink_still_holds_it(self):
        self.assertIn("answer-maybe-unread", self._codes(blank=False))


if __name__ == "__main__":
    unittest.main()
