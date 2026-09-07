# -*- coding: utf-8 -*-
"""Every question is marked on the sheet, MCQs included.

Objective items used to be rendered ONLY in the right-hand margin lane: the sheet
carried nothing beside the student's own answer. The reviewer asked for the
opposite - "make sure we tick each question and give marks like a teacher" - and
turning it on exposed two band bugs that the margin lane had been hiding. Both
measured on a real Class 10 PW Physics copy (Udaan Infinity, Subjective Test-09):

  Q1  Its answer line is "Q.1 (D) 25W", which _is_header() reads as a question
      label. q_anchor() therefore refused it and snapped forward to the first
      non-header line - Q2's answer. That became Q1's region_start, which
      _owned_y_bounds() uses as a FLOOR, so Q1's tick could not be drawn at its own
      y=170 and was pinned at 203: printed across Q2's option, on top of Q2's cross.

  Q3  Its band ran to the next question's anchor and swallowed "short answer type
      questions" and the "Q.4" heading (OCR read that as "0.4", so the question-label
      truncation never fired). Three lines instead of one, an 82pt span, and a tick
      scaled for an 82pt answer sitting beside a 28pt line.

The band heuristics are right for prose. An MCQ is the case they do not fit: its
answer is one line, and that line looks like a question label because that is how a
student writes it.
"""
import os
import sys

import fitz

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdf_renderer  # noqa: E402
from grader import GradeReport, QuestionMark  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from pdf_renderer import (_is_objective_mark,  # noqa: E402
                          _tick_capped_to_answer, build_evaluated_pdf)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- unit: the flag
print("== an objective item is recognised however the grader spelled it ==")
check("the explicit flag counts",
      _is_objective_mark(QuestionMark(qid="Q1", score=1, max_score=1, page=1,
                                      y_fraction=0.1, objective=True)))
check("a correct_option counts",
      _is_objective_mark(QuestionMark(qid="Q1", score=1, max_score=1, page=1,
                                      y_fraction=0.1, correct_option="B")))
check("a chosen_option counts",
      _is_objective_mark(QuestionMark(qid="Q1", score=1, max_score=1, page=1,
                                      y_fraction=0.1, chosen_option="B")))
check("a descriptive answer does not",
      not _is_objective_mark(QuestionMark(qid="Q4", score=1, max_score=3, page=1,
                                          y_fraction=0.5)))

# ------------------------------------------------------- unit: the size cap
print()
print("== a tick never out-reaches the answer it stands for ==")
ONE_LINE = [{"y0": 158.0, "y1": 182.0}]           # a 24pt MCQ line
SIX_LINES = [{"y0": 100.0, "y1": 124.0}, {"y0": 300.0, "y1": 324.0}]
check("a one-line answer caps the tick to its own height",
      _tick_capped_to_answer(32.0, ONE_LINE) <= 24.0 / pdf_renderer._TICK_RISE + 0.01,
      f"{_tick_capped_to_answer(32.0, ONE_LINE):.1f}")
check("...so the tick's rise fits inside the line",
      _tick_capped_to_answer(32.0, ONE_LINE) * pdf_renderer._TICK_RISE <= 24.0 + 0.01)
check("a long answer is left alone",
      _tick_capped_to_answer(32.0, SIX_LINES) == 32.0,
      f"{_tick_capped_to_answer(32.0, SIX_LINES)}")
check("a tick never shrinks below legibility",
      _tick_capped_to_answer(32.0, [{"y0": 0.0, "y1": 2.0}])
      == pdf_renderer._TICK_MIN_SIZE)
check("no anchor means no cap", _tick_capped_to_answer(32.0, []) == 32.0)

# --------------------------------------------- end to end: the real MCQ block
print()
print("== the real renderer on the MCQ block that failed ==")
# Geometry copied from the copy: three options one under another, the label line
# above them, and the section heading below that Q3's band used to swallow.
LINES = [
    ("P1L1", "Mulleple choice Questions.", 131.5, 155.0),
    ("P1L2", "Q.1 (D) 05W", 158.3, 181.8),
    ("P1L3", "(1) 2 (B) \\( V x<V y \\)", 188.1, 213.5),
    ("P1L4", "Q3 (D) (A) is false, but (R) is terme", 219.3, 247.1),
    ("P1L5", "ishout answer type questions", 254.3, 284.0),
]
ocr = [PageOCR(page=1, image_width=595, image_height=842, lines=[
    LineBox(line_id=lid, text=t, bbox=(80.0, y0, 250.0 if lid != "P1L4" else 400.0, y1),
            page=1)
    for lid, t, y0, y1 in LINES])]

report = GradeReport(
    total_score=2, max_total=3,
    overall_remarks="You got two of the three multiple choice questions right.",
    section_totals=[{"qid": "Q1", "score": 1, "max_score": 1, "objective": True},
                    {"qid": "Q2", "score": 0, "max_score": 1, "objective": True},
                    {"qid": "Q3", "score": 1, "max_score": 1, "objective": True}],
    questions=[
        QuestionMark(qid="Q1", score=1, max_score=1, page=1, anchor_line_id="P1L2",
                     y_fraction=0.10, remark="", objective=True, attempted=True,
                     chosen_option="D", correct_option="D"),
        QuestionMark(qid="Q2", score=0, max_score=1, page=1, anchor_line_id="P1L3",
                     y_fraction=0.12, remark="Ans: A", objective=True, attempted=True,
                     chosen_option="B", correct_option="A"),
        QuestionMark(qid="Q3", score=1, max_score=1, page=1, anchor_line_id="P1L4",
                     y_fraction=0.14, remark="", objective=True, attempted=True,
                     chosen_option="D", correct_option="D"),
    ])

student = fitz.open()
sp = student.new_page(width=595, height=842)
for lid, t, y0, y1 in LINES:          # real ink, so the ink mask has something to see
    sp.insert_text((80, y1 - 6), t[:34], fontsize=12, color=(0.12, 0.14, 0.35))
student_bytes = student.tobytes()
student.close()

drawn = []          # (kind, page_width, x, y, size)
_tick, _cross = pdf_renderer._draw_tick, pdf_renderer._draw_cross


def _rec(kind, real):
    def wrapper(page, x, y, size=26, seed="", **kw):
        drawn.append((kind, page.rect.width, x, y, size))
        return real(page, x, y, size=size, seed=seed, **kw)
    return wrapper


pdf_renderer._draw_tick = _rec("tick", _tick)
pdf_renderer._draw_cross = _rec("cross", _cross)
try:
    build_evaluated_pdf(student_pdf_bytes=student_bytes, student_filename="s.pdf",
                        student_pngs=[], report=report, ocr_pages=ocr)
finally:
    pdf_renderer._draw_tick, pdf_renderer._draw_cross = _tick, _cross

# Both lanes are drawn on the SAME widened page, so width does not separate them -
# x does: the answer occupies 0..content_w and the marks strip everything past it.
sheet = [d for d in drawn if d[2] < 595.0]
print(f"  drew {len(sheet)} marks on the sheet, {len(drawn) - len(sheet)} in the strip")
check("every MCQ is marked on the sheet, not only in the margin", len(sheet) == 3,
      f"{[(k, round(y, 1)) for k, _w, _x, y, _s in sheet]}")

centre = {lid: (y0 + y1) / 2 for lid, _t, y0, y1 in LINES}
rows = {"Q1": centre["P1L2"], "Q2": centre["P1L3"], "Q3": centre["P1L4"]}
for qid, want in rows.items():
    near = [d for d in sheet if abs(d[3] - want) <= 8.0]
    others = [c for lid, c in centre.items() if abs(c - want) > 1.0]
    check(f"{qid}'s mark sits on {qid}'s own line", len(near) == 1,
          f"marks at {[round(d[3], 1) for d in sheet]}, {qid}'s line centre is {want:.1f}")
    if near:
        closest = min(others, key=lambda c: abs(near[0][3] - c))
        check(f"{qid}'s mark is nearer its own line than any neighbour",
              abs(near[0][3] - want) < abs(near[0][3] - closest),
              f"y={near[0][3]:.1f}")

kinds = {}
for kind, _w, _x, y, _s in sheet:
    for qid, want in rows.items():
        if abs(y - want) <= 8.0:
            kinds[qid] = kind
check("a right answer gets a tick", kinds.get("Q1") == "tick" and kinds.get("Q3") == "tick",
      kinds)
check("a wrong answer gets a cross", kinds.get("Q2") == "cross", kinds)

# Q3's band used to run on into "short answer type questions", so its tick was sized
# for an 82pt span instead of the 28pt line it marks.
q3 = [d for d in sheet if abs(d[3] - rows["Q3"]) <= 8.0]
if q3:
    q3_line_h = 247.1 - 219.3
    check("Q3's tick is sized for its own line, not for the lines below it",
          q3[0][4] * pdf_renderer._TICK_RISE <= q3_line_h + 1.0,
          f"size={q3[0][4]:.1f}, rise={q3[0][4] * pdf_renderer._TICK_RISE:.1f} "
          f"vs a {q3_line_h:.1f}pt line")

ys = sorted(d[3] for d in sheet)
check("no two MCQ marks share a row",
      all(b - a > 12.0 for a, b in zip(ys, ys[1:])), [round(v, 1) for v in ys])

# ------------------------------------------- marks must not collide with marks
print()
print("== a mark is never drawn through another mark ==")
# The MCQ block that failed on BOTH new copies: three options of much the same
# length, one under another. Every mark is therefore placed at much the same x,
# and a tick runs 1.6*size tall against a ~28pt row pitch - so the tick for one
# answer was drawn through the mark for the answer above it. MEASURED: on one copy
# Q1's and Q2's ticks merged into a single 43x44pt blob, on the other Q1's cross
# and Q2's tick merged into 45x55pt. A tick may sit on the student's WRITING - a
# teacher's does - but two marks on top of each other say nothing at all.
STACK = [
    ("P1L1", "Ans 1. (D) 25 W", 150.0, 176.0),
    ("P1L2", "Ans 2 (A) Vx > Vy", 178.0, 204.0),
    ("P1L3", "Ans 3 (D) R is true", 206.0, 232.0),
]
ocr2 = [PageOCR(page=1, image_width=595, image_height=842, lines=[
    LineBox(line_id=lid, text=t, bbox=(80.0, y0, 250.0, y1), page=1)
    for lid, t, y0, y1 in STACK])]
report2 = GradeReport(
    total_score=2, max_total=3, overall_remarks="Two of three correct.",
    section_totals=[{"qid": f"Q{i}", "score": s, "max_score": 1, "objective": True}
                    for i, s in ((1, 1), (2, 0), (3, 1))],
    questions=[
        QuestionMark(qid="Q1", score=1, max_score=1, page=1, anchor_line_id="P1L1",
                     y_fraction=0.18, remark="", objective=True, attempted=True,
                     chosen_option="D", correct_option="D"),
        QuestionMark(qid="Q2", score=0, max_score=1, page=1, anchor_line_id="P1L2",
                     y_fraction=0.22, remark="Ans: A", objective=True, attempted=True,
                     chosen_option="B", correct_option="A"),
        QuestionMark(qid="Q3", score=1, max_score=1, page=1, anchor_line_id="P1L3",
                     y_fraction=0.26, remark="", objective=True, attempted=True,
                     chosen_option="D", correct_option="D"),
    ])

st2 = fitz.open()
sp2 = st2.new_page(width=595, height=842)
for lid, t, y0, y1 in STACK:
    sp2.insert_text((80, y1 - 6), t, fontsize=12, color=(0.12, 0.14, 0.35))
bytes2 = st2.tobytes()
st2.close()

drawn2 = []
_tick2, _cross2 = pdf_renderer._draw_tick, pdf_renderer._draw_cross


def _rec2(kind, real):
    def wrapper(page, x, y, size=26, seed="", **kw):
        if x < 595.0:                      # the answer lane, not the margin strip
            box = (pdf_renderer._cross_box(x, y, size) if kind == "cross"
                   else pdf_renderer._tick_box(x, y, size))
            drawn2.append((kind, box))
        return real(page, x, y, size=size, seed=seed, **kw)
    return wrapper


pdf_renderer._draw_tick = _rec2("tick", _tick2)
pdf_renderer._draw_cross = _rec2("cross", _cross2)
try:
    build_evaluated_pdf(student_pdf_bytes=bytes2, student_filename="s.pdf",
                        student_pngs=[], report=report2, ocr_pages=ocr2)
finally:
    pdf_renderer._draw_tick, pdf_renderer._draw_cross = _tick2, _cross2

check("all three answers are still marked", len(drawn2) == 3,
      f"{[k for k, _b in drawn2]}")
clashes = [(a[0], b[0]) for i, a in enumerate(drawn2) for b in drawn2[i + 1:]
           if a[1].intersects(b[1])]
check("no mark overlaps another mark", not clashes,
      f"{clashes}; boxes={[(k, tuple(round(v, 1) for v in b)) for k, b in drawn2]}")
check("and every mark is still legible", 
      all(b.height >= pdf_renderer._TICK_MIN_SIZE for _k, b in drawn2),
      f"{[round(b.height, 1) for _k, b in drawn2]}")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
