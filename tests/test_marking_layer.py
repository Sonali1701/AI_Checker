# -*- coding: utf-8 -*-
"""The marking layer: what the student sees, and whether it agrees with itself.

Each case here is a defect measured on a real evaluated copy:

  P3  the strip printed "1+0+1+1=3/4" while only three marks were drawn, so the
      student counted three ticks against four terms and could not find the step
      that scored nothing. Four such terms on every copy examined.
  P4  the question total is no longer written on the answer at all — it is printed
      once, in the right-hand strip. On a Class 10 Physics copy the sheet carried a
      bold "Q4 1.5/3" across the student's handwriting while the strip beside it
      already read "Q4 1+0.5=1.5/3". (The original P4 defect — a total following its
      BAND onto the next question's page, "3) b) Ans 5.5/6" on page 11 of the
      Business Laws copy — cannot recur once nothing is drawn there. Where the STRIP
      entry lands is covered by test_strip_layout.)
  P6  step marks drew at 8.6-9.1pt and whole-answer marks at 14.0-14.8pt, a 1.6x
      gap, with both appearing on one page of the Accounting copy.
  P7  two renderers, 532 lines, were unreachable and still used obsolete
      partial-credit glyph rules.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import grader
import pdf_renderer as pr
from grader import Criterion, QuestionMark
from preflight import _is_red, _rgb

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def sheet(n_pages=1):
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page(width=595, height=500)
    return doc


def lines(page, ys, x1=300.0):
    return [{"lid": f"p{page}_l{i}", "x0": 40.0, "x1": x1, "y0": y, "y1": y + 18,
             "page": page, "text": f"line {i}"} for i, y in enumerate(ys)]


def render(doc, qid, score, mx, crits, all_lines):
    pr._render_question_marks(
        doc,
        QuestionMark(qid=qid, score=score, max_score=mx, page=1, y_fraction=0.2,
                     remark="", attempted=True, mark_style="per_step",
                     criteria=crits),
        all_lines, {l["lid"]: l for l in all_lines}, {})


def reds(page):
    return [sp for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
            for sp in l["spans"]
            if sp["text"].strip() and _is_red(*_rgb(sp["color"]))]


print("== P3: a step worth zero is shown where the model anchored it ==")
doc = sheet()
ls = lines(1, [80.0, 140.0, 200.0, 260.0])
render(doc, "Q4", 3, 4, [
    Criterion(description="a", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l0"),
    Criterion(description="b", awarded=0, max=1, met=False, evidence="", step_line_id="p1_l1"),
    Criterion(description="c", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l2"),
    Criterion(description="d", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l3"),
], ls)
drawn = [sp["text"].strip() for sp in reds(doc[0])]
check("the cross does not carry a circled zero", "0" not in drawn, drawn)
check("all three earned step marks remain", drawn.count("1") == 3, drawn)
wrong_row = fitz.Rect(300, 130, 355, 175)
zero_paths = [d for d in doc[0].get_drawings() if d["rect"].intersects(wrong_row)
              and d.get("color") and d["color"][0] > .6]
check("the wrong step still has a visible cross on its own line", len(zero_paths) >= 8, len(zero_paths))
check("no circle is drawn after that cross",
      not any(len(d["items"]) == 4 and all(i[0] == "c" for i in d["items"]) for d in zero_paths))
doc.close()

print("== but a zero on a GUESSED line is still dropped ==")
# Zeros that drift away from the text they are about are worse than no mark, which
# is why they were dropped in the first place. Only an explicit anchor is trusted.
doc = sheet()
ls = lines(1, [80.0, 140.0, 200.0])
render(doc, "Q5", 2, 3, [
    Criterion(description="a", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l0"),
    Criterion(description="b", awarded=0, max=1, met=False, evidence="", step_line_id=""),
    Criterion(description="c", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l2"),
], ls)
check("no floating zero when the step was never anchored",
      "0" not in [sp["text"].strip() for sp in reds(doc[0])],
      [sp["text"].strip() for sp in reds(doc[0])])
doc.close()

# The question TOTAL is printed in the right-hand strip only. It used to be written
# on the answer as well, and on a real Class 10 Physics copy that put a bold
# "Q4 1.5/3" across the student's handwriting while the strip beside it already read
# "Q4 1+0.5=1.5/3". The sheet carries ticks, crosses and circled STEP marks; the
# strip carries the arithmetic. Both halves are checked here so neither drifts back.
def _content_edge(page):
    """Where the answer ends and the marks strip begins. The strip is
    max(0.24 x original, 175), so recover the original width from the widened one."""
    total = page.rect.width
    wide = total / 1.24
    return wide if wide >= 175 / 0.24 else total - 175


def on_sheet(page):
    edge = _content_edge(page)
    return [sp["text"].strip() for sp in reds(page) if sp["bbox"][0] < edge]


def in_strip(page):
    edge = _content_edge(page)
    return [sp["text"].strip() for sp in reds(page) if sp["bbox"][0] >= edge]


print("== P4: no question total is written on the answer ==")
doc = sheet()
ls = lines(1, [80.0, 140.0])
render(doc, "Q3.a", 2, 3, [
    Criterion(description="a", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l0"),
    Criterion(description="b", awarded=1, max=2, met=False, evidence="", step_line_id="p1_l1"),
], ls)
check("no 'n/m' total is drawn over the student's answer",
      not [t for t in on_sheet(doc[0]) if "/" in t], on_sheet(doc[0]))
check("the step marks themselves are still drawn",
      {"1"} <= set(on_sheet(doc[0])), on_sheet(doc[0]))
doc.close()

print("== P4: nothing follows the band onto the next question's page ==")
# The band bleeds onto page 2, but every mark is on page 1. The original defect was
# the TOTAL following the band across; nothing is drawn there now, on either page.
doc = sheet(2)
ls = lines(1, [80.0, 140.0]) + lines(2, [60.0])
render(doc, "Q3.a", 2, 3, [
    Criterion(description="a", awarded=1, max=1, met=True, evidence="", step_line_id="p1_l0"),
    Criterion(description="b", awarded=1, max=2, met=False, evidence="", step_line_id="p1_l1"),
], ls)
p1 = [t for t in on_sheet(doc[0]) if "/" in t]
p2 = [t for t in on_sheet(doc[1]) if "/" in t]
check("no total on the answer of the marked page", not p1, f"page1={p1}")
check("and none on the page the band bled onto", not p2, f"page2={p2}")
doc.close()

print("== P6: one hand marks the sheet ==")
ratio = pr._WHOLE_MARK_SIZE / pr._STEP_MARK_SIZE
check("whole-answer marks are no more than a shade larger than step marks",
      ratio <= pr._MAX_MARK_SIZE_RATIO, f"ratio {ratio:.2f}, cap {pr._MAX_MARK_SIZE_RATIO}")
check("but still larger, since they stand for the whole answer",
      pr._WHOLE_MARK_SIZE > pr._STEP_MARK_SIZE)
check("the old 1.6x gap is gone", ratio < 13 / 8)

print("== P6b: a tick is as big as the answer it marks ==")
# A teacher's tick is broad across a spread-out line and small down a ledger's rows.
# A single fixed size reads as a stamp, not as marking.
small = pr._tick_size_for_line(20, 9.0)     # dense tabular working
median = pr._tick_size_for_line(20, 22.0)   # ordinary handwriting
large = pr._tick_size_for_line(20, 34.0)    # large, spread-out writing
check("a tick on cramped writing is smaller", small < median, f"{small:.1f} vs {median:.1f}")
check("a tick on large writing is bigger", large > median, f"{large:.1f} vs {median:.1f}")
check("ordinary handwriting keeps the sheet's default size",
      abs(median - pr._ms(20)) < 0.5, f"{median:.1f} vs {pr._ms(20):.1f}")
# An OCR line that merged a whole block comes back hundreds of points tall; without a
# clamp the tick would be drawn across the page.
check("a merged OCR block cannot draw a giant tick",
      pr._tick_size_for_line(20, 295.0) <= pr._ms(20) * pr._TICK_SCALE_MAX + 1e-6,
      pr._tick_size_for_line(20, 295.0))
check("an unresolved line falls back to the default size",
      pr._tick_size_for_line(20, 0) == pr._ms(20))
check("a whole-answer tick still outsizes a step tick on the same line",
      pr._tick_size_for_line(32, 22.0) > pr._tick_size_for_line(20, 22.0))

print("== P6b2: a whole-answer tick also grows with how much was written ==")
# The tick that stands for a WHOLE answer is not the same mark beside a one-line
# definition and beside a page of working. Length feeds the size; it is square-rooted
# and capped, because a twelve-line answer does not earn four times the tick.
def _whole(lines, line_h=22.0):
    return pr._tick_size_for_line(32, line_h, answer_lines=lines)

check("a one-line answer gets a smaller tick than a three-line one",
      _whole(1) < _whole(3), f"{_whole(1):.1f} vs {_whole(3):.1f}")
check("and a six-line answer a bigger one still",
      _whole(6) > _whole(3), f"{_whole(6):.1f} vs {_whole(3):.1f}")
check("the size never shrinks as the answer grows",
      all(_whole(n) <= _whole(n + 1) + 1e-9 for n in range(1, 20)))
check("a very long answer saturates instead of running away",
      _whole(40) == _whole(12), f"{_whole(40):.1f} vs {_whole(12):.1f}")
check("a three-line answer is the nominal, unscaled size",
      abs(_whole(3) - pr._ms(32)) < 0.5, f"{_whole(3):.1f} vs {pr._ms(32):.1f}")

# A STEP tick marks one line, so its scope is that line however long the answer
# around it runs. Length is opt-in, and the step call site does not pass it.
check("step ticks are line-scoped, not answer-scoped",
      pr._tick_size_for_line(20, 22.0) == pr._ms(20))
check("length only applies when a caller asks for it",
      pr._tick_size_for_line(20, 22.0) != pr._tick_size_for_line(20, 22.0, answer_lines=12))

# Handwriting size and answer length compose, but a long answer in large writing must
# not compound into a tick drawn across the page.
biggest = pr._tick_size_for_line(32, 400.0, answer_lines=60)
check("the two factors together are still capped",
      biggest <= pr._ms(32) * pr._TICK_TOTAL_MAX + 1e-6, biggest)
check("a long answer in small writing still beats a short one in small writing",
      pr._tick_size_for_line(32, 12.0, answer_lines=10)
      > pr._tick_size_for_line(32, 12.0, answer_lines=1))

print("== P6c: a tick is drawn the way a hand draws one ==")
# A teacher's tick is heavy through the elbow and up the flick, and lifts off to a
# point. The width used to fall away linearly from the very first millimetre, and a
# later piecewise fix left a visible bead where the wide half butted against the thin
# one, so the shape is pinned by what actually reaches the page.
def _tick_widths(seed, size=30):
    doc = fitz.open()
    pg = doc.new_page(width=200, height=200)
    pr._draw_tick(pg, 40, 100, size=size, seed=seed)
    widths = [d["width"] for d in pg.get_drawings()]
    box = None
    for d in pg.get_drawings():
        box = d["rect"] if box is None else (box | d["rect"])
    doc.close()
    return widths, box

w, box = _tick_widths("pin")
peak = w.index(max(w))
check("the stroke is one continuous run of segments", len(w) >= 12, len(w))
check("it is heaviest around the elbow, not at the very start",
      0.25 <= peak / len(w) <= 0.65, f"peak {peak}/{len(w)}")
check("it never thickens again after the peak — no bead on the flick",
      all(w[i] <= w[i - 1] + 0.02 for i in range(peak + 1, len(w))),
      [round(x, 2) for x in w])
check("full weight is HELD up the flick, not dropped at the elbow",
      w[int(len(w) * 0.7)] >= 0.8 * max(w),
      f"{w[int(len(w) * 0.7)]:.2f} of {max(w):.2f}")
check("and it finishes on a fine tip", w[-1] <= 0.35 * max(w),
      f"{w[-1]:.2f} of {max(w):.2f}")

# Forty identical ticks down a margin read as a stamp, so each one is jittered — but
# never past the cluster width its caller reserved for it.
_, box_b = _tick_widths("a-different-answer")
check("two ticks are not the same drawing",
      abs(box.width - box_b.width) > 0.01 or abs(box.height - box_b.height) > 0.01,
      f"{box} vs {box_b}")
for seed in ("s1", "s2", "s3", "s4", "s5"):
    _, b = _tick_widths(seed)
    check(f"tick {seed} stays inside the reserved cluster width",
          b.width <= 30 * 1.5 + 1.0, b.width)

print("== P7: the unreachable renderers are gone ==")
for dead in ("_render_question_marks_LEGACY", "_annotate_student_page",
             "_draw_hand_circle", "_draw_remark_on_band", "_is_synthetic_anchor"):
    check(f"{dead} no longer exists", not hasattr(pr, dead))
from unittest.mock import patch

doc = sheet()
partial_line = lines(1, [80.0])[0]
pr._MARK_BOXES.clear()
with patch.object(pr, "_draw_tick", wraps=pr._draw_tick) as partial_tick, \
     patch.object(pr, "_draw_cross", wraps=pr._draw_cross) as partial_cross:
    placed_partial = pr._draw_sheet_verdict(doc, "Q1", partial_line, "partial")
check("partial credit is marked with one tick and no cross",
      placed_partial and partial_tick.call_count == 1 and partial_cross.call_count == 0)
doc.close()

print("== P5: languages and humanities finally have conventions ==")
for subject in ("English", "Hindi", "Sanskrit", "Social Science", "History",
                "Geography", "Political Science"):
    p = grader.build_system_prompt(subject, "Class 12", exam_type="school")
    check(f"{subject} gets marking guidance",
          "LANGUAGE & HUMANITIES CONVENTIONS" in p)
eng = grader.build_system_prompt("English", "Class 12", exam_type="school")
for phrase, why in (
        ("NEVER take content marks off for grammar", "spelling never costs content"),
        ("count the DISTINCT valid points", "point-wise answers are counted"),
        ("Where the scheme allocates format marks", "letters and notices earn format"),
        ("Word limits are guidance", "length alone is not a deduction"),
        ("not faults a student can act on", "no 'expression weak' as a reason")):
    check(f"it says: {why}", phrase in eng)

print("== and nobody else is affected ==")
for subject, absent in (("Physics", True), ("Mathematics", True), ("Accountancy", True)):
    p = grader.build_system_prompt(subject, "Class 12", exam_type="school")
    check(f"{subject} does not get the humanities block",
          ("LANGUAGE & HUMANITIES CONVENTIONS" in p) is not absent)
ca = grader.build_system_prompt("Accounting", "CA Foundation", exam_type="professional")
check("a CA paper still gets commerce and professional mode",
      "COMMERCE SUBJECT" in ca and "PROFESSIONAL EXAM MODE" in ca)
check("and not the humanities block", "LANGUAGE & HUMANITIES CONVENTIONS" not in ca)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
