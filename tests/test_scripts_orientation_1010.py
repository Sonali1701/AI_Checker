# -*- coding: utf-8 -*-
"""Arts re-run on build 97 (10 Oct 2026), found by the CA/Arts session.

- Hindi remarks were dropped from the copy: the PDF safety net kept only Latin-1, so
  a student read "· Q19 , , , ." and "Q15: .". Indian scripts and Urdu are now kept
  and drawn shaped in a font that has them.
- English 0de112e9: pages 6, 8, 9 and 11 stayed upside down. Their upright reads were
  mixed-script noise, but their turned reads (like the turned pages 7 and 10, which
  read 0.46 and 0.53) fell under the 0.45 floor meant for upright pages.
- Economics 67266cbc: page 2 of a booklet photographed sideways throughout read "(2)"
  only and was refused its neighbours' quarter turn on that floor; then a correct
  Q13 on that page lost all 4 marks as "unsupported" because its working was not in
  the (empty) OCR.
"""
import os
import sys

import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest when it imports pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import pdf_renderer as pr  # noqa: E402


def test_indian_scripts_and_urdu_are_kept_and_drawn():
    text = "Q15: कहानी का उदाहरण नहीं दिया। நன்றாக بہت اچھا Δx → ₹5"
    clean = pr._sanitize_for_pdf(text)
    assert "कहानी का उदाहरण नहीं दिया" in clean and "நன்றாக" in clean and "بہت" in clean
    assert "delta x -> Rs.5" in clean               # Latin folding is unchanged
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    pr._insert_text(page, (20, 40), clean, fontsize=11, color=pr.RED)
    pr._insert_text(page, (20, 80), "Plain English remark.", fontsize=11, color=pr.RED)
    drawn = page.get_text()
    assert "Plain English remark." in drawn
    assert "Q15:" in drawn and len(drawn) > 40          # the script line was drawn too
    # Wrapping measures script letters too, so a long Hindi remark wraps.
    lines = pr._wrap_to_width("कहानी " * 30, 120, lambda s: pr._text_width(s, 11, lambda t: 0.0))
    assert len(lines) > 3


def _page(texts, conf, number=6):
    from mathpix import LineBox, PageOCR
    return PageOCR(number, 1241, 1754, [LineBox(f"P{number}L{i}", t, (100, 100 + 60 * i, 1100, 150 + 60 * i),
                                                number, confidence=conf)
                                        for i, t in enumerate(texts, 1)])


NOISE = ["कि probocif puro turad we brown", "का करका का ग्यत 4+09 पम्लाकान", "Arrung word",
         "गैजक लाग प्रय सम व्यंज्यम्य", "womposo, sub abory fra lorms", "флалогогияно флитаю"]
TURNED = ["12. The only reason of Tiger King to kill one hundred",
          "tigers was to protect himself and prove the astrology",
          "prediction wrong of him being killed by tiger. The decision",
          "reveals his stubborn nature as well as boastful behaviour"]


def test_a_turned_read_of_ordinary_handwriting_proves_the_turn():
    import pipeline
    upright = _page(NOISE, .12)
    assert pipeline._text_says_upside_down(upright, _page(TURNED, .33))
    # Still not on a turned read that is no better than noise.
    assert not pipeline._text_says_upside_down(upright, _page(TURNED, .2))
    # Twice the upright read's confidence, not just the floor.
    assert not pipeline._text_says_upside_down(_page(NOISE, .18), _page(TURNED, .3))


def test_an_answer_on_an_unread_page_is_not_called_unsupported():
    from mathpix import LineBox, PageOCR
    from grader import Criterion, GradeReport, QuestionMark
    from grading_quality import find_unsupported_credit
    pages = [PageOCR(2, 3125, 4167, [LineBox("P2L2", "(2)", (60, 100, 200, 150), 2)]),
             PageOCR(3, 4167, 3125, [LineBox("P3L14", "the industrial sector", (60, 40, 900, 90), 3),
                                     LineBox("P3L15", "(ii) Importing capital goods", (60, 100, 900, 150), 3),
                                     LineBox("P3L16", "required for importing capital goods", (60, 160, 900, 210), 3)])]
    crit = Criterion(description="GDCF", awarded=1, max=1, met=True, step_line_id="P3L15",
                     anchor_quote="Gross Domestic Capital Formation = 300 + 50",
                     evidence="GDCF = 300 + 50 = 350 crore")
    report = GradeReport(total_score=1, max_total=4, overall_remarks="", evidence_contract=True,
                         questions=[QuestionMark(qid="Q13", score=1, max_score=4, page=3,
                                                 y_fraction=.1, anchor_line_id="P3L15",
                                                 criteria=[crit])])
    assert find_unsupported_credit(report, pages)                 # no region: still checked
    report.answer_regions = {"q13": {"qid": "Q13", "status": "inferred", "pages": [3],
                                     "label_line_ids": ["P2L2"], "line_ids": ["P3L4"]}}
    assert find_unsupported_credit(report, pages) == {}
