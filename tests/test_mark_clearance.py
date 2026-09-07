# -*- coding: utf-8 -*-
"""A step mark must not be written on top of the student's own figures.

From a real evaluated copy (11th Commerce Accountancy): in the P&L Appropriation
ledger the circled part-marks landed inside the amount column, so "150 000" came
out as "150(0.5)00" and "320 000" as "3(0.5)00". Cause: the mark was placed a
fixed 12pt past the ANCHORED line's right edge, and in a ledger the row is
several separate OCR cells — the particulars text and the amount are different
lines at the same y. The same blind rule dropped a tick into the sheet's
rough-work column.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_renderer import (_MIN_DROP_BAND, _clear_x_for_mark, disc_ink_fraction,
                          student_ink_mask)


def clear_x(*a, **k):
    """Just the x, for the cases that must not move vertically."""
    x, dy, _shrink = _clear_x_for_mark(None, *a, **k)
    assert dy == 0.0, f"unexpected vertical drop of {dy}"
    return x

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def line(lid, x0, x1, y0=100.0, y1=118.0, page=1):
    return {"lid": lid, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "page": page,
            "text": lid}


# A ledger row: particulars on the left, an amount column further right.
PARTICULARS = line("P1L1", 40.0, 200.0)
AMOUNT = line("P1L2", 260.0, 330.0)
SECOND_AMOUNT = line("P1L3", 380.0, 440.0)
CLUSTER = 60.0
RIGHT_CAP = 520.0
Y = 109.0

print("== a mark that would land on the amount column is pushed clear ==")
idx = {pl["lid"]: pl for pl in (PARTICULARS, AMOUNT)}
x = clear_x(idx, 1, Y, 212.0, CLUSTER, RIGHT_CAP, own_lid="P1L1")
check("mark starts past the amount cell", x >= AMOUNT["x1"], f"x={x} vs amount ends {AMOUNT['x1']}")
check("mark still fits before the cap", x + CLUSTER <= RIGHT_CAP, f"x={x}")

print("== it keeps sliding when the next cell is also in the way ==")
idx3 = {pl["lid"]: pl for pl in (PARTICULARS, AMOUNT, SECOND_AMOUNT)}
x = clear_x(idx3, 1, Y, 212.0, CLUSTER, RIGHT_CAP, own_lid="P1L1")
check("mark clears BOTH amount columns", x >= SECOND_AMOUNT["x1"],
      f"x={x} vs second amount ends {SECOND_AMOUNT['x1']}")

print("== nothing on the row: the mark is left exactly where it was ==")
only = {"P1L1": PARTICULARS}
check("unchanged when the row is otherwise empty",
      clear_x(only, 1, Y, 212.0, CLUSTER, RIGHT_CAP, own_lid="P1L1") == 212.0)

print("== the mark's OWN line never pushes it ==")
check("own line is ignored",
      clear_x({"P1L1": PARTICULARS}, 1, Y, 205.0, CLUSTER, RIGHT_CAP,
                        own_lid="P1L1") == 205.0)

print("== other ROWS and other PAGES are ignored ==")
other_row = {"P1L9": line("P1L9", 260.0, 330.0, y0=300.0, y1=318.0)}
check("a cell on a different row does not move the mark",
      clear_x(other_row, 1, Y, 212.0, CLUSTER, RIGHT_CAP) == 212.0)
other_page = {"P2L1": line("P2L1", 260.0, 330.0, page=2)}
check("a cell on a different page does not move the mark",
      clear_x(other_page, 1, Y, 212.0, CLUSTER, RIGHT_CAP) == 212.0)

print("== a full amount column is cleared to its right, still on the page ==")
# Amount column runs to the cap, so sliding past it would overflow. There is still
# clear paper between the particulars text and the amount.
wide = {"P1L2": line("P1L2", 260.0, 505.0)}
x, dy, _s = _clear_x_for_mark(None, wide, 1, Y, 212.0, CLUSTER, RIGHT_CAP, own_lid="P1L1")
check("mark clears the amount cell entirely", x >= 505.0, f"x={x}")
check("and still starts within the cap", x <= RIGHT_CAP, f"x={x} cap={RIGHT_CAP}")
check("no vertical drop was needed", dy == 0.0, f"dy={dy}")

print("== a row written edge to edge sends the mark BELOW it, not through it ==")
# The real CA Foundation ledger row: particulars from the left margin, amount hard
# against the right edge, nothing clear between them. The old code returned the
# caller's x and printed the mark through the figure - 7,61,024 became "76(1)24".
packed = {"P1L1": line("P1L1", 40.0, 250.0),
          "P1L2": line("P1L2", 254.0, 515.0),
          "P1L8": line("P1L8", 40.0, 515.0, y0=150.0, y1=168.0)}
x, dy, _s = _clear_x_for_mark(None, packed, 1, Y, 212.0, CLUSTER, RIGHT_CAP, own_lid="P1L0")
check("the mark is moved down into the blank band", dy > 0, f"dy={dy}")
check("it lands clear of the row above", Y + dy > 118.0, f"y={Y + dy}")
check("and clear of the row below", Y + dy < 150.0, f"y={Y + dy}")
check("x stays within the cap", x <= RIGHT_CAP, f"x={x}")

print("== with no room below either, the mark stays put rather than vanishing ==")
crammed = {"P1L1": line("P1L1", 40.0, 250.0),
           "P1L2": line("P1L2", 254.0, 515.0),
           "P1L8": line("P1L8", 40.0, 515.0, y0=119.0, y1=137.0)}
x, dy, _s = _clear_x_for_mark(None, crammed, 1, Y, 212.0, CLUSTER, RIGHT_CAP, own_lid="P1L0")
check("no drop when the next row is immediately below", dy == 0.0, f"dy={dy}")
check("a band under _MIN_DROP_BAND is refused", _MIN_DROP_BAND > 1.0)

print("== a mark already clear of everything is untouched ==")
check("already-clear mark keeps its x",
      clear_x(idx, 1, Y, 340.0, CLUSTER, RIGHT_CAP, own_lid="P1L1") == 340.0)

print("== end to end: the real renderer on a real ledger row ==")
# The unit cases above prove the search. This proves the renderer USES it: the mark
# is measured off the finished page with the same colour-separation test the release
# gate runs, so a caller that ignored the vertical drop would fail here.
import fitz

from grader import Criterion, GradeReport, QuestionMark
from mathpix import LineBox, PageOCR
from pdf_renderer import _render_question_marks
from preflight import _MARK_INK_LIMIT, _ink_under_mark, _is_red, _rgb

doc = fitz.open()
pg = doc.new_page(width=595, height=300)
pg.insert_text((40, 114), "To Machinery A/c", fontsize=13, color=(.1, .1, .12))
pg.insert_text((300, 114), "7,61,024", fontsize=13, color=(.1, .1, .12))
pg.insert_text((40, 160), "To Bank A/c", fontsize=13, color=(.1, .1, .12))

ledger = [
    {"lid": "p1_l1", "x0": 40, "x1": 250, "y0": 100, "y1": 118, "page": 1,
     "text": "To Machinery A/c"},
    {"lid": "p1_l2", "x0": 300, "x1": 560, "y0": 100, "y1": 118, "page": 1,
     "text": "7,61,024"},
    {"lid": "p1_l3", "x0": 40, "x1": 250, "y0": 148, "y1": 166, "page": 1,
     "text": "To Bank A/c"},
]
# Two criteria and >= 3 marks, so the renderer takes its per-step path.
_render_question_marks(
    doc,
    QuestionMark(qid="Q2", score=4, max_score=4, page=1, y_fraction=0.35, remark="",
                 attempted=True, mark_style="per_step",
                 criteria=[Criterion(description="machinery brought in", awarded=2,
                                     max=2, met=True, evidence="7,61,024",
                                     step_line_id="p1_l1"),
                           Criterion(description="bank entry", awarded=2, max=2,
                                     met=True, evidence="To Bank",
                                     step_line_id="p1_l3")]),
    ledger, {l["lid"]: l for l in ledger}, {})

drawn = [sp for b in pg.get_text("dict")["blocks"] for l in b.get("lines", [])
         for sp in l["spans"]
         if sp["text"].strip() and _is_red(*_rgb(sp["color"]))]
# Two step marks and nothing else: the question total is printed in the right-hand
# strip, not on the answer, so it is no longer one of the things that has to be kept
# clear of the student's figures here.
check("both step marks were drawn", len(drawn) == 2,
      f"{len(drawn)} red spans: {[sp['text'].strip() for sp in drawn]}")
_mask, _scale = student_ink_mask(pg)
worst = max((_ink_under_mark(_mask, _scale, sp["bbox"]) for sp in drawn),
            default=1.0)
check("no mark covers the student's figures", worst < _MARK_INK_LIMIT,
      f"worst overlap {worst:.3f} of the circle is the student's ink")
# Where it ends up is the search's business — on the row in a gap the raster
# confirms is blank, or in the band below when the row really is full. What must
# hold is that it covers nothing and stays beside the step it marks: dropping it
# far down the page would leave the reader unable to tell which step it scored.
first = min(drawn, key=lambda sp: sp["bbox"][1])
fy = (first["bbox"][1] + first["bbox"][3]) / 2
check("the mark stays beside its own step", 100.0 - 12 <= fy <= 148.0,
      f"y={fy:.0f}, step row is 100-118 and the next row starts at 148")
check("and it is not sitting on the amount column",
      not (300.0 <= first["bbox"][0] <= 560.0),
      f"x={first['bbox'][0]:.0f} is inside the 300-560 amount cell")
doc.close()


# A phone photo of a ruled notebook: the printed ruling is dark, runs edge to edge,
# and used to be counted as the student's ink by everything that looked at pixels.
# One rule across a mark's circle is ~5.5% of it and the release gate blocks at 6%,
# so marks lying on BLANK paper were reported as burying the writing, and the
# clearance search could not find a free spot anywhere and left all of them on top
# of the answer. MEASURED on a real 8-page CBSE Chemistry sheet: 28 of 40 marks on
# writing, up to 90% buried.
print("== printed ruling is not the student's ink ==")
from PIL import Image as _CImage, ImageDraw as _CDraw

from pdf_renderer import disc_ink_fraction as _dif, student_ink_mask as _sim


class _FakePix:
    """Just enough of a PyMuPDF pixmap for student_ink_mask."""

    def __init__(self, img):
        self.width, self.height, self.n = img.size[0], img.size[1], 3
        self.samples = img.convert("RGB").tobytes()


class _FakePage:
    def __init__(self, img):
        self._img = img

    def get_pixmap(self, dpi=150):
        return _FakePix(self._img)


_W, _H = 600, 400
_ruled = _CImage.new("RGB", (_W, _H), (250, 250, 248))
_rd2 = _CDraw.Draw(_ruled)
for _i in range(8):
    _rd2.line([0, 40 + _i * 45, _W, 40 + _i * 45], fill=(70, 80, 150), width=2)
_blank_mask, _blank_scale = _sim(_FakePage(_ruled))
# 150 dpi over a 600px image => the page is 288pt wide; probe in PDF points.
_pt = 72.0 / 150.0
_clear = _dif(_blank_mask, _blank_scale, 300 * _pt, 200 * _pt, 11.0)
check("a circle on blank ruled paper reads as clear", _clear <= 0.035,
      f"{_clear:.3f} of the circle counted as ink")

_written = _ruled.copy()
_wd = _CDraw.Draw(_written)
for _x in range(120, 480, 11):
    _wd.line([_x, 175, _x + 7, 215], fill=(20, 20, 90), width=3)
_ink_mask, _ink_scale = _sim(_FakePage(_written))
_over = _dif(_ink_mask, _ink_scale, 300 * _pt, 200 * _pt, 11.0)
check("but writing in the same place still reads as ink", _over > 0.06,
      f"{_over:.3f} of the circle counted as ink")


# The masks are cached per build so a page is rasterised once, not once per mark.
# That cache MUST NOT survive the build: marks are placed on the widened document
# created during rendering, and CPython hands a freed id() straight back out, so
# keying the reset on a document let the next sheet be measured against the previous
# sheet's pixels — marks placed from the wrong page entirely.
print("== the ink cache does not leak from one copy to the next ==")
import pdf_renderer as _pr
from pdf_renderer import build_evaluated_pdf as _bep

def _plain_sheet():
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    pg.insert_text((60, 120), "To Machinery A/c", fontsize=13, color=(.1, .1, .12))
    return d.tobytes()


_STALE = -12345                      # an id no live document can have
_pr._MARK_INK[_STALE] = {1: (None, 1.0)}
_bep(student_pdf_bytes=_plain_sheet(), student_filename="s.pdf", student_pngs=[],
     report=GradeReport(
         total_score=2, max_total=2, overall_remarks="x",
         questions=[QuestionMark(
             qid="Q1", score=2, max_score=2, page=1, y_fraction=0.14,
             anchor_line_id="P1L0", mark_style="per_step", attempted=True,
             criteria=[Criterion(description="entry", awarded=2, max=2, met=True,
                                 evidence="", step_line_id="P1L0")])]),
     ocr_pages=[PageOCR(page=1, image_width=595, image_height=842,
                        lines=[LineBox("P1L0", "To Machinery A/c",
                                       (60.0, 106.0, 210.0, 124.0), 1)])],
     mark_scale=1.0)
check("a mask left by an earlier copy is discarded, not inherited",
      _STALE not in _pr._MARK_INK,
      "the registry was reset by document id, and a freed id() comes straight back")


# What a teacher does when the answer runs edge to edge: writes the mark SMALLER and
# tucks it in, rather than printing it across the words. The search had been probing
# a fixed 11pt disc while the renderer drew a circle of radius ~19pt, so it called
# spots clear that the real mark then covered: on the CBSE Chemistry sheet 38 of 43
# marks sat on writing either way, while the 11pt probe claimed only 14 did.
print("== a mark shrinks to fit rather than sitting on the words ==")
_GAP = 26.0          # a gap too small for a full-size mark, wide enough for a small one
_row_lines = {"P1L1": line("P1L1", 40.0, 300.0),
              "P1L2": line("P1L2", 300.0 + _GAP, 515.0)}
_x, _dy, _shrink = _clear_x_for_mark(None, _row_lines, 1, Y, 300.0, CLUSTER,
                                     RIGHT_CAP, own_lid="P1L0")
check("without a page raster the OCR reasoning still runs", _shrink == 1.0,
      f"shrink={_shrink} with no raster to measure")

_probe = 19.0
_full = _clear_x_for_mark(None, {}, 1, Y, 200.0, CLUSTER, RIGHT_CAP,
                          own_lid="P1L0", probe_r=_probe)
check("an empty row never shrinks the mark", _full[2] == 1.0, f"shrink={_full[2]}")

from pdf_renderer import _MARK_SHRINKS as _SH
check("the smallest mark offered is still legible", min(_SH) >= 0.5, f"{min(_SH)}")
check("full size is tried first", _SH[0] == 1.0, f"{_SH}")
check("the ladder only ever shrinks", list(_SH) == sorted(_SH, reverse=True), f"{_SH}")

# The real thing: a page written across its whole width bar one narrow gap. A
# full-size circle does not fit in it; a smaller one does, and that is what the
# student should get rather than a mark over their words.
_sd = fitz.open()
_sp = _sd.new_page(width=595, height=200)
# Separate pen strokes, not a bar: a continuous run across the page is what
# _drop_printed_ruling exists to erase, and it would eat a solid band as "ruling".
# Lines above and below too, so the mark cannot simply step off the row — on a full
# page of answers there is no blank band to escape into, and shrinking is all that
# is left.
for _by in (60, 96, 132):
    for _bx in range(40, 520, 10):
        if _by == 96 and 296 <= _bx <= 316:   # the one gap: blank from 294 to 320
            continue
        _sp.draw_rect(fitz.Rect(_bx, _by, _bx + 4, _by + 26), color=None,
                      fill=(.1, .1, .15))
_BIG = 19.0
_x2, _dy2, _sh2 = _clear_x_for_mark(_sd, {}, 1, 109.0, 200.0, 2 * _BIG, 520.0,
                                    own_lid="P1L0", probe_dx=_BIG, probe_r=_BIG)
check("a full-size mark does not fit, so the mark shrinks", _sh2 < 1.0,
      f"shrink={_sh2}, x={_x2:.0f}")
_m2, _s2 = student_ink_mask(_sd[0])
_landed = disc_ink_fraction(_m2, _s2, _x2 + _BIG * _sh2, 109.0 + _dy2, _BIG * _sh2)
check("and the smaller mark lands on clear paper", _landed <= 0.035,
      f"{_landed:.3f} of the shrunken circle is ink")
check("it landed in the gap, not out past the writing",
      294.0 <= _x2 + _BIG * _sh2 <= 320.0,
      f"circle centre x={_x2 + _BIG * _sh2:.0f}, gap is 294-320")


# An MCQ verdict belongs to ITS answer line. REAL COPY: CA Inter Advanced Accounting,
# five MCQs ~18pt apart. The question total of the answer ABOVE walked 26pt at a time
# off the end of its own answer, came to rest inside the MCQ block, and then sat in the
# shared "already placed" registry — where it collided with the first MCQ and pushed
# every tick and cross one answer down. The option the student got RIGHT came back
# carrying a cross, and the strip's last two entries were reserved a slot past the foot
# of the page and never drew at all.
print("== an objective verdict never leaves its own line ==")
_obj_lines = {f"P1L{i}": line(f"P1L{i}", 40.0, 500.0, y0=100.0 + i * 18, y1=118.0 + i * 18)
              for i in range(5)}
_x, _dy, _sh = _clear_x_for_mark(None, _obj_lines, 1, 109.0, 300.0, 40.0, 500.0,
                                 own_lid="P1L0", lift_max=0.0)
check("a lift_max of 0 pins the mark to its row", _dy == 0.0, f"dy={_dy}")
_x2, _dy2, _s2 = _clear_x_for_mark(None, _obj_lines, 1, 109.0, 300.0, 40.0, 500.0,
                                   own_lid="P1L0")
check("the default still allows a step mark to move off a full row",
      isinstance(_dy2, float))

print("== a question total stays inside its own answer ==")
import pdf_renderer as _pr2
from grader import Criterion as _C2, QuestionMark as _Q2

_tot_doc = fitz.open()
_tp = _tot_doc.new_page(width=595, height=842)
_tp.insert_text((60, 118), "To Machinery A/c 7,61,024", fontsize=12, color=(.1, .1, .12))
_tot_lines = [{"lid": "t1", "x0": 40, "x1": 400, "y0": 100, "y1": 124, "page": 1,
               "text": "To Machinery A/c 7,61,024"}]
_seen = []
_real_ins = fitz.Page.insert_text


def _cap(self, pt, text="", **k):
    if "/" in str(text):
        _seen.append((str(text), round(pt[1])))
    return _real_ins(self, pt, text, **k)


fitz.Page.insert_text = _cap
try:
    _pr2._render_question_marks(
        _tot_doc,
        _Q2(qid="Q7", score=2, max_score=2, page=1, y_fraction=0.14, attempted=True,
            mark_style="per_step", anchor_line_id="t1",
            criteria=[_C2(description="entry", awarded=2, max=2, met=True,
                          evidence="", step_line_id="t1")]),
        _tot_lines, {l["lid"]: l for l in _tot_lines}, {})
finally:
    fitz.Page.insert_text = _real_ins
_tot = [t for t in _seen if t[0].startswith("Q7")]
check("the question total is written", bool(_tot), f"{_seen}")
if _tot:
    # its own line is 100-124; one line-height of slack below is the most it may take
    check("and it stays within a line-height of its own answer", _tot[0][1] <= 124 + 24 + 4,
          f"total drawn at y={_tot[0][1]}, answer ends at 124")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
