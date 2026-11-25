"""Live run of 1 Nov 2026: the struck-line check read pixels one at a time in Python and took a
quarter of the run's working time. It now counts a row at once. What a student's page is judged to
contain must not change, so each result is compared with the pixel-by-pixel reading, kept here.
"""
from __future__ import annotations

import io
import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mathpix
from mathpix import LineBox, _row_ink_coverage, _row_ink_run, detect_struck_lines


def coverage_pixel_by_pixel(pixels, x0, y, x1, threshold):
    width = x1 - x0
    if width <= 0:
        return 0.0
    return sum(1 for x in range(x0, x1) if pixels[x, y] < threshold) / width


def run_pixel_by_pixel(pixels, x0, y, x1, threshold):
    width = x1 - x0
    if width <= 0:
        return 0.0
    best = current = gap = 0
    for x in range(x0, x1):
        if pixels[x, y] < threshold:
            current += gap + 1
            gap = 0
            if current > best:
                best = current
        else:
            gap += 1
            if gap > mathpix._STRIKE_RUN_BRIDGE:
                current = gap = 0
    return best / width


def noisy_page(seed=7, width=400, height=120):
    data = np.random.default_rng(seed).integers(0, 256, (height, width), dtype=np.uint8)
    data[30:33, 50:350] = 0            # a stroke
    data[60:62, ::2] = 0               # a broken line
    return Image.fromarray(data)


@pytest.mark.parametrize("threshold", [1, 127, 128, 129, 255])
def test_row_results_match_the_pixel_by_pixel_reading_exactly(threshold):
    image = noisy_page()
    pixels, gray = image.load(), np.asarray(image)
    for y in range(0, image.height, 3):
        for x0, x1 in ((0, 400), (50, 350), (199, 200), (10, 10), (30, 20)):
            assert _row_ink_coverage(gray, x0, y, x1, threshold) == coverage_pixel_by_pixel(pixels, x0, y, x1, threshold)
            assert _row_ink_run(gray, x0, y, x1, threshold) == run_pixel_by_pixel(pixels, x0, y, x1, threshold)


def line(name, box):
    return LineBox(line_id=name, text="writing", bbox=box, page=1)


def png(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def ruled_page():
    image = Image.new("L", (800, 300), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 196, 800, 197), fill=90)                     # the page's own ruling
    for x in range(100, 700, 16):                                   # writing under the strike
        draw.rectangle((x, 44, x + 7, 66), fill=30)
    draw.rectangle((100, 52, 700, 54), fill=0)                      # a strike through it
    for x in range(100, 700, 16):                                   # plain writing
        draw.rectangle((x, 144, x + 7, 166), fill=30)
    return image


def test_a_struck_line_is_found_and_plain_writing_is_not():
    lines = [line("struck", (100, 40, 700, 76)), line("plain", (100, 140, 700, 176))]
    assert detect_struck_lines(png(ruled_page()), lines) == {"struck"}


def test_boxes_past_the_edge_of_the_page_are_still_handled():
    lines = [line("edge", (-20, 280, 900, 340)), line("thin", (10, 10, 20, 12))]
    assert detect_struck_lines(png(ruled_page()), lines) == set()


def test_a_page_that_is_not_an_image_finds_nothing():
    assert detect_struck_lines(b"not a picture", [line("a", (0, 0, 100, 40))]) == set()
