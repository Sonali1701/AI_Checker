# -*- coding: utf-8 -*-
"""A page the OCR read sideways is righted and read again before grading.

The Accountancy copy c0f259bb (2026-09-17): the orientation probe turned two
upright pages by 90 degrees, the OCR of those pages came back as tall narrow boxes
of gibberish, and the released copy showed them on their side.
"""
from __future__ import annotations

import os
import sys
import unittest
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402
import orient  # noqa: E402
import pipeline  # noqa: E402
from mathpix import LineBox, PageOCR, lines_run_vertically  # noqa: E402


def _page(boxes, page=1):
    return PageOCR(page, 1000, 1400, [LineBox(f"P{page}L{i}", t, b, page, confidence=.99)
                                    for i, (t, b) in enumerate(boxes, 1)])


def _upright_png(w=600, h=800):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    for y in range(60, h - 60, 40):
        d.line((40, y, w - 120, y), fill="black", width=6)      # left-aligned text lines
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ShapeTests(unittest.TestCase):
    def test_tall_narrow_lines_mean_a_sideways_page(self):
        sideways = _page([("By Ballyd 60000", (348, 44, 596, 1172)), ("BY WCK 2000", (161, 44, 358, 797)),
                          ("By Revish", (2079, 54, 2386, 2479)), ("x y z", (3415, 154, 3742, 1420))])
        self.assertEqual(lines_run_vertically(sideways)[0], True)
        upright = _page([("(1) A) Partners", (43, 217, 358, 283)), ("(2) Capital", (23, 303, 504, 381)),
                         ("NRR = 15%", (171, 358, 442, 410)), ("[", (900, 100, 910, 700))])
        self.assertEqual(lines_run_vertically(upright)[0], False)
        self.assertEqual(lines_run_vertically(_page([("ab", (0, 0, 10, 100))]))[0], False)


class TurnTests(unittest.TestCase):
    def test_the_quarter_turn_that_reads_left_to_right_is_chosen(self):
        img = Image.open(BytesIO(_upright_png()))
        # rotate the upright page 90 CCW: righting it needs 270 CCW; and the reverse
        self.assertEqual(orient.sideways_angle(img.rotate(90, expand=True)), 270)
        self.assertEqual(orient.sideways_angle(img.rotate(270, expand=True)), 90)


class ProbeParsingTests(unittest.TestCase):
    def test_large_white_frame_is_removed_only_for_probe(self):
        canvas = Image.new("RGB", (1200, 1800), "white")
        photo = Image.new("RGB", (1140, 650), "gray")
        ImageDraw.Draw(photo).text((70, 80), "Q12 Agricultural causes", fill="black")
        canvas.paste(photo, (30, 550))
        buf = BytesIO()
        canvas.save(buf, format="PNG")
        original = buf.getvalue()
        probe = orient.writing_probe_image(original)
        self.assertLess(Image.open(BytesIO(probe)).height, canvas.height // 2)
        self.assertEqual(Image.open(BytesIO(original)).size, (1200, 1800))
        self.assertEqual(orient.writing_probe_image(b"unreadable"), b"unreadable")

    def _run(self, text, n):
        response = {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": 10, "output_tokens": 5}}
        with patch.object(grader, "_claude_call", return_value=response) as call:
            result = grader.orientation_probe([b"x"] * n, google_token="t", task_id="k")
        self.assertEqual(call.call_args.kwargs["model"], grader.ORIENTATION_MODEL)
        return result

    def test_the_last_array_is_the_answer(self):
        self.assertEqual(self._run("Image 1 [the first] is upright. Final: [0, 90, 0]", 3), [0, 90, 0])

    def test_a_reply_that_does_not_name_every_page_is_ignored(self):
        self.assertEqual(self._run("[90, 90]", 7), [0] * 7)


class RereadTests(unittest.TestCase):
    def test_readable_quarter_turn_survives_failed_opposite_read(self):
        pngs = [_upright_png()]
        empty = _page([])
        pages = [empty]
        readable = _page([("Q12 agricultural causes", (40, 60, 480, 80)),
                          ("land revenue systems", (40, 100, 480, 120)),
                          ("lack of irrigation investment", (40, 140, 480, 160)),
                          ("commercialisation of agriculture", (40, 180, 480, 200))])
        with patch("mathpix.ocr_page_enhanced",
                   side_effect=[empty, readable, RuntimeError("opposite read unavailable")]), \
                patch.object(grader, "orientation_probe", return_value=[90]), \
                patch.object(pipeline, "mark_struck_lines", return_value=0):
            rotated = pipeline._reread_sideways_pages(pngs, pages,
                cfg=NS(google_token="t", task_id="k"), filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {1: 90})
        self.assertIs(pages[0], readable)
        self.assertFalse(pages[0].blank)

    def test_successful_enhancement_survives_failed_optional_180_read(self):
        pngs = [_upright_png()]
        empty = _page([])
        pages = [empty]
        readable = _page([("Q16 savings function", (40, 60, 480, 80)),
                          ("S = -a + (1-b)Y", (40, 100, 480, 120))])
        with patch("mathpix.ocr_page_enhanced",
                   side_effect=[readable, RuntimeError("upside-down read unavailable")]), \
                patch.object(grader, "orientation_probe", return_value=[0]), \
                patch.object(pipeline, "mark_struck_lines", return_value=0):
            rotated = pipeline._reread_sideways_pages(pngs, pages,
                cfg=NS(google_token="t", task_id="k"), filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {})
        self.assertIs(pages[0], readable)
        self.assertFalse(pages[0].blank)

    def test_empty_sideways_photo_uses_probe_and_readable_quarter_turn(self):
        pngs = [_upright_png()]
        pages = [_page([])]
        readable = _page([("Q12 agricultural causes", (40, 60, 480, 80)),
                          ("land revenue systems", (40, 100, 480, 120)),
                          ("lack of irrigation investment", (40, 140, 480, 160)),
                          ("Q13 domestic product", (40, 180, 480, 200))])
        poor = _page([("x", (40, 60, 50, 80))])
        with patch("mathpix.ocr_page_enhanced", side_effect=[pages[0], readable, poor]), \
                patch.object(grader, "orientation_probe", return_value=[90]), \
                patch.object(pipeline, "mark_struck_lines", return_value=0):
            rotated = pipeline._reread_sideways_pages(pngs, pages,
                cfg=NS(google_token="t", task_id="k"), filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {1: 90})
        self.assertIs(pages[0], readable)
        self.assertFalse(pages[0].blank)

    def test_sparse_upright_probe_does_not_try_quarter_turns(self):
        page = _page([("(16) (i) a+b", (40, 60, 480, 80))])
        pngs, pages = [_upright_png()], [page]
        with patch("mathpix.ocr_page_enhanced", return_value=page) as enhanced, \
                patch.object(grader, "orientation_probe", return_value=[0]), \
                patch.object(pipeline, "mark_struck_lines", return_value=0):
            rotated = pipeline._reread_sideways_pages(pngs, pages,
                cfg=NS(google_token="t", task_id="k"), filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {})
        self.assertEqual(enhanced.call_count, 2)  # upright enhancement and existing 180 check

    def test_a_sideways_page_is_rotated_reread_and_reported(self):
        upright = _upright_png()
        sideways_png = BytesIO()
        Image.open(BytesIO(upright)).rotate(90, expand=True).save(sideways_png, format="PNG")
        pngs = [upright, sideways_png.getvalue()]
        pages = [_page([("line one here", (40, 60, 480, 80)), ("line two here", (40, 100, 480, 120)),
                        ("line three here", (40, 140, 480, 160))], 1),
                 _page([("gibberish one", (60, 40, 80, 480)), ("gibberish two", (100, 40, 120, 480)),
                        ("gibberish three", (140, 40, 160, 480))], 2)]
        reread = _page([("capital account", (40, 60, 480, 80)), ("by bal b/d", (40, 100, 480, 120)),
                        ("to bal c/d", (40, 140, 480, 160))], 2)
        cfg = NS(google_token="t", task_id="k")
        seen = {}

        def fake_ocr(png, page_num, **kw):
            seen["png"] = png
            seen["page"] = page_num
            return reread
        with patch.object(pipeline, "ocr_page", side_effect=fake_ocr), \
                patch("mathpix.ocr_page_enhanced") as enhanced, \
                patch.object(pipeline, "mark_struck_lines", return_value=0), \
                patch.object(grader, "orientation_probe", return_value=[270]):
            rotated = pipeline._reread_sideways_pages(pngs, pages, cfg=cfg, filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {2: 270})
        enhanced.assert_not_called()
        self.assertIs(pages[1], reread)
        self.assertEqual(seen["page"], 2)
        w, h = Image.open(BytesIO(pngs[1])).size
        self.assertGreater(h, w)                      # the page image is upright again
        self.assertEqual(pages[0].lines[0].text, "line one here")

    def test_an_upright_booklet_is_left_alone(self):
        pngs = [_upright_png()]
        pages = [_page([("line one here", (40, 60, 480, 80)), ("line two", (40, 100, 480, 120)),
                        ("line three", (40, 140, 480, 160))], 1)]
        with patch.object(pipeline, "ocr_page") as ocr, \
                patch("mathpix.ocr_page_enhanced") as enhanced:
            rotated = pipeline._reread_sideways_pages(pngs, pages, cfg=NS(google_token="t", task_id="k"),
                                                      filename="x.pdf", usage_out={})
        self.assertEqual(rotated, {})
        ocr.assert_not_called()
        enhanced.assert_not_called()


if __name__ == "__main__":
    unittest.main()
