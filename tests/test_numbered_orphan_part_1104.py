# -*- coding: utf-8 -*-
"""Unowned writing that opens with a part number goes to that part, not the one before.

CS Executive JIGL MTS-1, copy R102 (4 Nov 2026): Q4 is a case study with parts 1-4.
Page 16 opened with an underlined "3." and answered Q4.3, but the visual inventory
called Q4.3 and Q4.4 "not written", so the page joined Q4.2 as its continuation and
Q4.3 scored 0 where the evaluator gave 2/2.
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


def _located(qid, lids):
    pages = tuple(sorted({int(l[1:l.index("L")]) for l in lids}))
    return AnswerLocation(qid=qid, status="located", pages=pages, label_line_ids=(lids[0],),
                          line_ids=tuple(lids), confidence=0.7)


def _span(qid, *lids):
    return VerifiedSpan(qid=qid, line_ids=list(lids), pages=sorted({int(l[1:l.index("L")]) for l in lids}))


def _region(result, qid):
    return next(item for item in result.answer_map.locations if item.qid == qid)


def _case(page16, *, parts=("Q4.1", "Q4.2", "Q4.3", "Q4.4")):
    pages = [_page(14, ["1. The circular is ultra vires", "the Act gives no such power"]),
             _page(15, ["2. Natural justice requires a hearing", "audi alteram partem"]),
             _page(16, page16)]
    found = {"Q4.1": ["P14L1", "P14L2"], "Q4.2": ["P15L1", "P15L2"]}
    amap = AnswerMap(package_id="p", locations=tuple(
        _located(q, found[q]) if q in found else AnswerLocation(qid=q, status="unresolved")
        for q in parts))
    spans = {q: _span(q, *lids) for q, lids in found.items()}
    absent = {q for q in parts if q not in found}
    return merge_inventory(amap, spans, absent, pages)


def test_a_page_opening_with_the_next_part_number_is_that_part():
    result = _case(["3. In the present case the STA directed", "operators to reduce fleet",
                    "the circular can be challenged"])
    q3 = _region(result, "Q4.3")
    assert q3.status == "located"
    assert q3.line_ids == ("P16L1", "P16L2", "P16L3")
    assert q3.uncertain_line_ids == q3.line_ids          # the grader judges it from the page
    assert "Q4.3" not in result.absent_qids
    assert "Q4.3" in result.disputed_qids
    assert _region(result, "Q4.2").line_ids == ("P15L1", "P15L2")
    assert "Q4.4" in result.absent_qids


def test_two_part_numbers_split_the_block():
    result = _case(["3. Judicial review lies", "on grounds of ultra vires",
                    "4. Reasoned orders show the mind applied"])
    assert _region(result, "Q4.3").line_ids == ("P16L1", "P16L2")
    assert _region(result, "Q4.4").line_ids == ("P16L3",)
    assert not {"Q4.3", "Q4.4"} & set(result.absent_qids)


def test_a_plain_continuation_still_joins_the_answer_before():
    result = _case(["and the operators were not heard", "so the order is void"])
    assert _region(result, "Q4.2").line_ids == ("P15L1", "P15L2", "P16L1", "P16L2")
    assert _region(result, "Q4.3").status == "unresolved"


def test_a_number_that_names_no_unfound_part_is_left_alone():
    # Only Q4.1 and Q4.2 exist: "3." is a point inside Q4.2's answer.
    result = _case(["3. Fair hearing also needs notice", "and reasons"], parts=("Q4.1", "Q4.2"))
    assert _region(result, "Q4.2").line_ids == ("P15L1", "P15L2", "P16L1", "P16L2")


def test_an_earlier_part_number_is_not_taken_back():
    # "1." after Q4.2 is a numbered point, not a return to Q4.1 (which is found anyway)
    # nor a claim on a later part.
    result = _case(["1. first ground is ultra vires", "2. second ground is bias"])
    assert _region(result, "Q4.2").line_ids == ("P15L1", "P15L2", "P16L1", "P16L2")
    assert _region(result, "Q4.3").status == "unresolved"
