# -*- coding: utf-8 -*-
"""The marks strip must never write one note on top of another.

From a real evaluated copy (11th Commerce Accountancy, 21/25): Q13's score
calculation wrapped to two lines —
    0.5+0+0.5+0.5+0+
    0.5=2/3
— and the remark "sales entry amount wrong; fire/insurance entry incorrect" was
printed straight through the second line. Cause: the code that DRAWS a strip
entry knew how to break the long "0.5+0.5+..." token, but the code that RESERVES
its height still split on spaces only, so it counted the calculation as one line.
Both now go through _wrap_to_width.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from grader import GradeReport, QuestionMark
from mathpix import LineBox, PageOCR
from pdf_renderer import build_evaluated_pdf
from pdf_renderer import (
    _reserve_strip_slot,
    _reset_strip_state,
    _strip_wrap_lines,
    _wrap_to_width,
)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


STRIP_W = 155.0          # the real marks-strip column width
CALCS = [
    "0.5+0.5+0.5+0.5+0.5+0.5=3/3",
    "0.5+0+0.5+0.5+0+0.5=2/3",
    "0.5+1+0.5+0.5+0=2.5/3",
    "0.5+0.5+0.5+0+0.5+0.5+0.5+0.5=3.5/5",
]


def measure(txt, size=15.0):
    return fitz.get_text_length(txt, fontname="helv", fontsize=size)


print("== the score calculation really does wrap at strip width ==")
for calc in CALCS:
    lines = _wrap_to_width(calc, STRIP_W, lambda t: measure(t))
    wide = [ln for ln in lines if measure(ln) > STRIP_W + 0.5]
    check(f"{calc!r} fits the column", not wide, f"overflowing: {wide}")

print("== the height estimate agrees with what is drawn (the actual bug) ==")
# _strip_wrap_lines measures one point wider on purpose, so it may over-reserve
# by a line, but it must NEVER under-reserve.
for calc in CALCS:
    drawn = len(_wrap_to_width(calc, STRIP_W, lambda t: measure(t)))
    reserved = _strip_wrap_lines(calc, STRIP_W, 15.0)
    check(f"reserved >= drawn for {calc!r}", reserved >= drawn,
          f"reserved {reserved} < drawn {drawn}")
    check(f"the calculation is not counted as a single line: {calc!r}", reserved > 1,
          f"reserved {reserved} line(s) for a calc that draws {drawn}")

print("== ordinary prose still wraps on spaces, unchanged ==")
prose = "sales entry amount wrong; fire/insurance entry incorrect"
lines = _wrap_to_width(prose, STRIP_W, lambda t: measure(t, 14.5))
check("prose wraps to more than one line", len(lines) > 1, f"{lines}")
check("no prose line overflows",
      all(measure(ln, 14.5) <= STRIP_W + 0.5 for ln in lines), f"{lines}")
check("wrapping preserves every word",
      " ".join(lines).split() == prose.split(), f"{lines}")

print("== a token with no '+' still breaks rather than overflowing ==")
runon = "A" * 200
lines = _wrap_to_width(runon, STRIP_W, lambda t: measure(t))
check("an unbreakable run of characters is split", len(lines) > 1, f"{len(lines)} lines")
check("and none of its lines overflow",
      all(measure(ln) <= STRIP_W + 0.5 for ln in lines))
check("no characters are lost", "".join(lines) == runon)

print("== short text is untouched ==")
check("a short calc stays on one line",
      _wrap_to_width("1/1", STRIP_W, lambda t: measure(t)) == ["1/1"])
check("empty text yields no lines", _wrap_to_width("", STRIP_W, lambda t: measure(t)) == [])
check("_strip_wrap_lines never returns 0", _strip_wrap_lines("", STRIP_W, 14.5) == 1)

print("== out-of-order anchors stay beside their own answers ==")
_slots_doc = fitz.open()
_slots_doc.new_page(width=595, height=800)
_reset_strip_state(_slots_doc)
_late_y = _reserve_strip_slot(_slots_doc, 1, 500, 50, 800)
_early_y = _reserve_strip_slot(_slots_doc, 1, 100, 50, 800)
check("the later answer stays near its requested row", _late_y == 500, str(_late_y))
check("an earlier answer can reserve free space above it", _early_y == 100,
      f"was pushed to y={_early_y}")
check("the out-of-order slots do not overlap", _early_y + 50 + 6 <= _late_y)

print("== whole numbers are written the way a teacher writes them ==")
from pdf_renderer import _fmt_mark
for v, want in ((16.0, "16"), (50.0, "50"), (41.5, "41.5"), (2.5, "2.5"), (0.5, "0.5")):
    check(f"{v} prints as {want!r}", _fmt_mark(v) == want, _fmt_mark(v))
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pdf_renderer.py"), encoding="utf-8").read()
check("the cover total is formatted, not interpolated raw",
      "f\"Total Score: {report.total_score}" not in src)
check("and so are the question-wise totals",
      "{sec.get('score', 0)} /" not in src)



# The strip stacks entries top-down and reserves a slot even when the page has none
# left, so an entry that ran past the foot was drawn outside the page and vanished in
# silence. REAL COPY: a five-item MCQ block at the bottom of a CA sheet needed seven
# lines ("Q1.ii 0/2" and its "Ans: A" on separate lines) where five fit — the last two
# answers carried no visible mark, and nothing in the run said so.
print("== every scored question is named on the copy ==")
from grader import GradeReport as _GR, QuestionMark as _QM
from preflight import _check_rendered as _cr

_doc = fitz.open()
_pg = _doc.new_page(width=595, height=842)
_pg.insert_text((60, 100), "Q1.i 2/2", fontsize=11, color=(0.8, 0.08, 0.1))
_pg.insert_text((60, 130), "Q1.ii 0/2", fontsize=11, color=(0.8, 0.08, 0.1))
_shown = _doc.tobytes()


def _rep(qids):
    return _GR(total_score=2, max_total=4, overall_remarks="x",
               questions=[_QM(qid=q, score=1, max_score=2, page=1, y_fraction=0.2)
                          for q in qids])


check("a copy naming every question passes",
      not [i for i in _cr(_shown, _rep(["Q1.i", "Q1.ii"]))
           if i.code == "question-not-shown"])
_missing = [i for i in _cr(_shown, _rep(["Q1.i", "Q1.ii", "Q1.iv"]))
            if i.code == "question-not-shown"]
check("a question that never drew is caught", len(_missing) == 1, f"{_missing}")
check("and it is blocking", _missing and _missing[0].severity == "block")
check("the message names the question", _missing and "Q1.iv" in _missing[0].detail)
check("no report means no such check", not [i for i in _cr(_shown)
                                            if i.code == "question-not-shown"])

print()

print("== MCQ verdicts read down the strip in question order ==")
# A released Corporate Laws page showed "Q8, Q6, Q4, Q2, Q5, Q7" with Q1 and Q3 pushed
# onto the next sheet. Part I writes Ans 1-4 down the left of the page and Ans 5-8 down
# the right, so the pairs share a row: each verdict asked for the space beside its own
# anchor, the slot search answered with the nearest free gap in either direction, and
# the column came out shuffled. They are a list now, laid out in order.
_W, _H = 595.0, 842.0


def _two_column_mcq_page():
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=_W, height=_H)
        page.draw_line(fitz.Point(70, 130), fitz.Point(520, 130),
                       color=(0.8, 0.8, 0.85), width=0.5)
    return doc.tobytes()


_mcq_lines = []
for _i in range(4):
    _y = 100 + _i * 40
    _mcq_lines.append(LineBox(line_id=f"P1L{_i}", text=f"Ans {_i + 1}) B",
                              bbox=(80.0, _y, 240.0, _y + 30), page=1))
    _mcq_lines.append(LineBox(line_id=f"P1R{_i}", text=f"Ans {_i + 5}) C",
                              bbox=(300.0, _y, 460.0, _y + 30), page=1))

_anchors = ["P1L0", "P1L1", "P1L2", "P1L3", "P1R0", "P1R1", "P1R2", "P1R3"]
_mcqs = [QuestionMark(qid=f"Q{i + 1}", score=2.0, max_score=2.0, page=1,
                      y_fraction=0.2, remark="", objective=True,
                      correct_option="B", chosen_option="B", anchor_line_id=a)
         for i, a in enumerate(_anchors)]
_mcq_pdf = build_evaluated_pdf(
    student_pdf_bytes=_two_column_mcq_page(), student_filename="s.pdf",
    student_pngs=[],
    report=GradeReport(total_score=16.0, max_total=16.0, overall_remarks="x",
                       questions=_mcqs),
    ocr_pages=[PageOCR(page=1, image_width=int(_W), image_height=int(_H),
                       lines=_mcq_lines)],
    mark_scale=1.0)
_mcq_doc = fitz.open(stream=_mcq_pdf, filetype="pdf")
_seen = []
for _pno in range(_mcq_doc.page_count):
    _page = _mcq_doc[_pno]
    _edge = _page.rect.width - 175
    for _b in _page.get_text("dict")["blocks"]:
        for _l in _b.get("lines", []):
            for _sp in _l["spans"]:
                _t = _sp["text"].strip()
                if _sp["bbox"][0] >= _edge - 2 and _t.startswith("Q"):
                    _seen.append((_pno, round(_sp["bbox"][1]), _t.split()[0]))
_order = [q for _p, _y, q in sorted(_seen)]
check("every MCQ verdict is on the page holding its answers",
      len({p for p, _y, _q in _seen}) == 1, sorted({p for p, _y, _q in _seen}))
check("and they read down the strip in question order",
      _order == [f"Q{i}" for i in range(1, 9)], _order)
_mcq_doc.close()


if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
