# -*- coding: utf-8 -*-
"""Two located answers whose regions overlap: the inventory's verified lines decide.

KVS PGT Biology mock, 21 Oct 2026. The student headed answers with bare numbers
("1. Ans:-", "3. PGA", "6. Q") and numbered the points inside answers too: "(1)
Prophase-1" in the meiosis answer (Q4) and "(1) Atrial systole" in the cardiac cycle
answer (Q5). The label map read both as Q1 headings, so Q1's region ran from page 1
over Q2's answer and into Q5's, and the overlap check zeroed Q2 and Q5 as "writing
that belongs to Q1". The inventory had placed Q2 and Q5 on those very lines.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from answer_inventory import VerifiedSpan, merge_inventory  # noqa: E402
from answer_map import AnswerLocation, AnswerMap  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402


def _page(number, texts):
    return PageOCR(number, 1000, 1400, [
        LineBox(f"P{number}L{i}", text, (50.0, 40.0 * i, 900.0, 40.0 * i + 30), number)
        for i, text in enumerate(texts, start=1)])


PAGES = [
    _page(1, ["1. Ans:- five kingdom classification was proposed", "by R.H. Whittaker in 1969"]),
    _page(2, ["M", "Mitochondria are double membrane bound", "cristae increase the surface"]),
    _page(3, ["5Q", "CARDIAC CYCLE", "(1) ATRIAL SYSTOLE the blood comes into", "the left and right atrium"]),
]


def _located(qid, lids, labels):
    pages = tuple(sorted({int(l[1]) for l in lids}))
    return AnswerLocation(qid=qid, status="located", pages=pages, label_line_ids=tuple(labels),
                          line_ids=tuple(lids), confidence=0.5)


def _span(qid, *lids):
    return VerifiedSpan(qid=qid, line_ids=list(lids), pages=sorted({int(l[1]) for l in lids}))


def _map():
    q1 = ["P1L1", "P1L2", "P2L1", "P2L2", "P2L3", "P3L3", "P3L4"]   # ran on over Q2 and into Q5
    return AnswerMap(package_id="p", locations=(
        _located("Q1", q1, ["P1L1", "P3L3"]),
        _located("Q2", ["P2L1", "P2L2", "P2L3"], ["P2L1"]),
        _located("Q5", ["P3L1", "P3L2", "P3L3", "P3L4"], ["P3L1"]),
    ))


SPANS = {"Q1": _span("Q1", "P1L1", "P1L2"), "Q2": _span("Q2", "P2L1", "P2L2", "P2L3"),
         "Q5": _span("Q5", "P3L1", "P3L2", "P3L3", "P3L4")}


def _region(result, qid):
    return next(item for item in result.answer_map.locations if item.qid == qid)


def test_lines_verified_as_another_answers_leave_the_region():
    result = merge_inventory(_map(), SPANS, set(), PAGES)
    q1 = _region(result, "Q1")
    assert q1.line_ids == ("P1L1", "P1L2")
    assert q1.label_line_ids == ("P1L1",)
    assert q1.pages == (1,)
    assert _region(result, "Q2").line_ids == ("P2L1", "P2L2", "P2L3")
    assert _region(result, "Q5").line_ids == ("P3L1", "P3L2", "P3L3", "P3L4")


def test_without_an_inventory_nothing_changes():
    amap = _map()
    result = merge_inventory(amap, {}, set(), PAGES)
    assert _region(result, "Q1") == amap.locations[0]


def test_a_continuation_page_owned_by_no_answer_joins_the_answer_before_it():
    # KVS PGT Computer Science, 21 Oct 2026: Q9 names "(1) Interactive mode (2) Script
    # mode" at the foot of page 9 and explains both on page 10, which no question
    # held; the page read "Not evaluated" although its lines earned Q9's marks.
    pages = [_page(1, ["9. Explain the two modes of Python", "(1) Interactive mode"]),
             _page(2, ["Interactive mode: code is entered line by line", "Script mode: a .py file"])]
    amap = AnswerMap(package_id="p", locations=(
        _located("Q9", ["P1L1", "P1L2"], ["P1L1"]),
        AnswerLocation(qid="Q10", status="unresolved")))
    q9 = _region(merge_inventory(amap, {}, set(), pages), "Q9")
    assert q9.line_ids == ("P1L1", "P1L2", "P2L1", "P2L2")
    assert q9.pages == (1, 2)


def test_writing_before_the_first_answer_stays_unowned():
    pages = [_page(1, ["Name: Anupama", "1. Answer to the first question"])]
    amap = AnswerMap(package_id="p", locations=(_located("Q1", ["P1L2"], ["P1L2"]),))
    assert _region(merge_inventory(amap, {}, set(), pages), "Q1").line_ids == ("P1L2",)


def test_a_line_also_in_the_regions_own_span_stays():
    spans = dict(SPANS, Q1=_span("Q1", "P1L1", "P1L2", "P2L3"))
    result = merge_inventory(_map(), spans, set(), PAGES)
    assert "P2L3" in _region(result, "Q1").line_ids
