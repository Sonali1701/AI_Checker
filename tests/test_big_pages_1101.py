"""Live run of 1 Nov 2026: one copy of 54 pictures, 2880 x 1620 points each, froze every other copy
while its pages were turned into 20-megapixel PNGs to judge their direction. The probe shrinks each
page to 900 pixels anyway, so it now sees at most 2000 pixels, and a page that does need turning
is still rebuilt from the full render, as before.
"""
from __future__ import annotations

import os
import sys
from io import BytesIO

import fitz
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orient


def pdf(sizes):
    doc = fitz.open()
    for number, (width, height) in enumerate(sizes):
        page = doc.new_page(width=width, height=height)
        page.draw_rect(fitz.Rect(40, 40, width - 40, height - 40), color=(0, 0, 0), fill=(.8, .9, 1))
        page.insert_text((60, 90), f"Page {number + 1}", fontsize=30)
    return doc.tobytes()


def seen_by_the_probe(data, answer=None):
    seen = []

    def probe(pages):
        seen.extend(Image.open(BytesIO(page)) for page in pages)
        return answer or [0] * len(pages)

    return seen, orient.normalize_orientation(data, "copy.pdf", probe=probe)


def test_a_huge_page_is_looked_at_small():
    seen, _ = seen_by_the_probe(pdf([(2880, 1620), (1620, 2880)]))
    assert [image.size for image in seen] == [(2000, 1125), (1125, 2000)]


def test_a_copy_with_nothing_to_turn_comes_back_unchanged():
    data = pdf([(2880, 1620)] * 3)
    _, (out, rotations, changed) = seen_by_the_probe(data)
    assert out == data and rotations == [0, 0, 0] and changed is False


def test_a_normal_page_is_looked_at_with_the_same_pixels_as_before():
    data = pdf([(595, 842)])
    seen, _ = seen_by_the_probe(data)
    before = fitz.open(stream=data, filetype="pdf")[0].get_pixmap(dpi=150)
    assert seen[0].size == (before.width, before.height)
    assert seen[0].convert("RGB").tobytes() == before.samples


def test_a_big_page_that_needs_turning_is_rebuilt_from_the_full_render():
    data = pdf([(2880, 1620), (595, 842)])
    source = fitz.open(stream=data, filetype="pdf")
    expected = orient._jpeg_upright(source[0].get_pixmap(dpi=150).tobytes("png"), 90)
    _, (out, rotations, changed) = seen_by_the_probe(data, [90, 0])
    assert rotations == [90, 0] and changed is True
    result = fitz.open(stream=out, filetype="pdf")
    assert result.page_count == 2
    assert result[0].get_images(full=True)[0][2:4] == Image.open(BytesIO(expected)).size
    assert max(Image.open(BytesIO(expected)).size) == orient._REBUILD_MAX_EDGE
    assert round(result[1].rect.width) == 595 and round(result[1].rect.height) == 842


def test_a_page_too_big_for_the_cap_still_gets_a_picture():
    seen, _ = seen_by_the_probe(pdf([(14400, 14400)]))
    assert seen and max(seen[0].size) <= orient._LOOK_MAX_EDGE


def test_the_free_check_works_on_a_huge_page_too():
    data = pdf([(2880, 1620)])
    out, rotations, _ = orient.normalize_orientation(data, "copy.pdf")
    assert len(rotations) == 1 and out


def test_a_single_image_is_handled_as_before():
    buf = BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    out, rotations, changed = orient.normalize_orientation(buf.getvalue(), "copy.png", rotations=[0])
    assert out == buf.getvalue() and rotations == [0] and changed is False
