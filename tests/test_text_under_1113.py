"""The clear-paper test for a circled mark must stay cheap on a page full of typed text.

13 Nov 2026, PW Skills Data Analytics: a learner's Google Sheet became 12 typed pages of
sales figures. Every mark found no clear paper, so each tried several thousand spots,
and page.get_textbox() read the whole page's text again for every spot (51 ms a probe).
The copy sat in mark placement for over 25 minutes after its AI marking was done.
pdf_renderer._text_under reads the page's character boxes once per page version.
"""
import os
import sys
import time

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdf_renderer  # noqa: E402


def sheet_page():
    """A page laid out like a converted spreadsheet: a full grid of figures."""
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    for row in range(60):
        for column in range(9):
            page.insert_text((30 + column * 62, 40 + row * 13), f"{(row * 37 + column * 911) % 99991:,}", fontsize=8)
    page.insert_text((40, 830), "end of sheet", fontsize=9)
    return document, page


@pytest.fixture(autouse=True)
def fresh_cache():
    pdf_renderer._PAGE_TEXT.clear()
    yield
    pdf_renderer._PAGE_TEXT.clear()


def probes():
    return [fitz.Rect(x, y, x + w, y + w) for w in (7, 15, 22) for y in range(10, 835, 11) for x in range(5, 580, 17)]


def test_it_answers_what_get_textbox_answered():
    _document, page = sheet_page()
    rects = probes()[::37]      # get_textbox itself is the slow call this replaces
    expected = [bool(page.get_textbox(r).strip()) for r in rects]
    assert [pdf_renderer._text_under(page, r) for r in rects] == expected
    assert any(expected) and not all(expected), "the probes cover both text and clear paper"


def test_text_written_on_the_page_later_still_counts():
    _document, page = sheet_page()
    spot = fitz.Rect(300, 836, 340, 841)
    assert not pdf_renderer._text_under(page, spot)
    page.insert_text((305, 840), "17", fontsize=6)
    assert page.get_textbox(spot).strip()
    assert pdf_renderer._text_under(page, spot), "a page whose content changed is read again"


def test_a_page_full_of_figures_is_probed_quickly():
    _document, page = sheet_page()
    rects = probes()
    started = time.perf_counter()
    printed = pdf_renderer._page_text_boxes(page)     # once per mark, as _draw_sheet_score does
    for r in rects:
        pdf_renderer._boxes_touch(printed, r)
    elapsed = time.perf_counter() - started
    # The old probe cost about 50 ms each, so these would have taken minutes.
    assert elapsed < 5, f"{len(rects)} probes took {elapsed:.1f}s"


def test_a_blank_page_has_no_text_anywhere():
    document = fitz.open()
    page = document.new_page()
    assert not pdf_renderer._text_under(page, page.rect)


def test_each_build_starts_with_no_stored_text():
    source = open(pdf_renderer.__file__, encoding="utf-8").read()
    assert "_PAGE_TEXT.clear()" in source.split("def _build_evaluated_pdf", 1)[1].split("widened = fitz.open()", 1)[0]
    assert "page.get_textbox(" not in source.split("def _draw_sheet_score", 1)[1].split("def _paint_sheet_score", 1)[0]
