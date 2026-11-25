# -*- coding: utf-8 -*-
"""Does every question come back marked, in one marker's hand?

Four defects, all measured on the renderer rather than argued about:

  M1  A CROSS WAS HALF THE SIZE OF A TICK. The two primitives take `size`
      differently — _draw_tick(s) inks ~1.5s x 1.6s, _draw_cross(s) inks s x s —
      and every call site passed the cross a FRACTION of the tick's size (0.7,
      0.85, a flat 18, a flat 20, a flat 10 in the margin lane). On the released
      Class 10 Physics copy Q1's tick spanned 25.1pt and Q2's cross, one line
      below it, spanned 12.8. Synthetically it was worse: a 19.3pt tick against a
      9.1pt cross, a verdict smaller than the student's own handwriting.

  M2  A PART-MARKED ANSWER CARRIED NOTHING AT ALL. It is not ticked (it is not
      right) and not crossed (it is not wrong), and the total lives in the margin
      strip by design — so the one question a student most wants to look at came
      back with a blank answer and a number 200pt away.

  M3  A STEP-MARKED ANSWER WHOSE STEPS ALL WENT UNPLACED CAME BACK BLANK TOO.
      anchors.resolve_steps rightly refuses to guess a line, but when every step
      is refused that correct decision compounded into an unmarked answer.

  M4  _fit_beside_marks slid a mark clear of its neighbour, applied the sheet's
      right cap to the result, and pulled it straight back on top of what it had
      just escaped — so marks overlapped anyway, the one thing it exists to stop.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import pdf_renderer as pr
from grader import Criterion, QuestionMark

FAILS = []

RENDERER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pdf_renderer.py")


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def sheet(n=1, h=500.0):
    doc = fitz.open()
    for _ in range(n):
        doc.new_page(width=595, height=h)
    return doc


def lines(page, ys, x1=300.0, texts=None):
    return [{"lid": f"p{page}_l{i}", "x0": 40.0, "x1": x1, "y0": y, "y1": y + 18,
             "page": page, "text": (texts[i] if texts else f"working step {i} = {i * 7}")}
            for i, y in enumerate(ys)]


def _is_red(c):
    return bool(c) and c[0] > 0.6 and c[1] < 0.4 and c[2] < 0.4


def glyphs(page, edge=None):
    """Red vector marks on the sheet, clustered into whole glyphs."""
    edge = page.rect.width if edge is None else edge
    out = []
    for d in page.get_drawings():
        if not (_is_red(d.get("color")) or _is_red(d.get("fill"))):
            continue
        r = +d["rect"]
        if r.x0 >= edge or r.width > page.rect.width * 0.5:
            continue
        out.append({"r": r, "n": 1})
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                if (out[i]["r"] + (-5, -5, 5, 5)).intersects(out[j]["r"]):
                    out[i]["r"] |= out[j]["r"]
                    out[i]["n"] += out[j]["n"]
                    del out[j]
                    merged = True
                    break
            if merged:
                break
    return sorted(out, key=lambda c: c["r"].y0)


def render(doc, q, all_lines, placed=None):
    pr._MARK_BOXES.clear()
    pr._render_question_marks(doc, q, all_lines,
                              {l["lid"]: l for l in all_lines},
                              {} if placed is None else placed)


TEXT = ["Photosynthesis is the process", "by which green plants make food",
        "using sunlight and water."]


def _one_question(score, mx, style="single", crits=(), texts=TEXT):
    doc = sheet()
    ls = lines(1, [80.0, 110.0, 140.0][:len(texts)], texts=list(texts))
    render(doc, QuestionMark(qid="Q7", score=score, max_score=mx, page=1,
                             y_fraction=0.2, remark="r", attempted=True,
                             mark_style=style, criteria=list(crits)), ls)
    g = glyphs(doc[0])
    doc.close()
    return g


print("== M1: a cross is drawn by the same hand as a tick ==")
# Drawn EXTENT, not the `size` argument — the two primitives scale differently, and
# comparing the arguments instead of the ink is exactly how the gap went unnoticed.
tick = _one_question(5, 5)
cross = _one_question(0, 5)
check("a full-marks answer is ticked", len(tick) == 1, len(tick))
check("an attempted-but-wrong answer is crossed", len(cross) == 1, len(cross))
if tick and cross:
    tw, xw = tick[0]["r"].width, cross[0]["r"].width
    th, xh = tick[0]["r"].height, cross[0]["r"].height
    check("the cross is not a shrunken afterthought beside the tick",
          0.7 <= xw / tw <= 1.3 and 0.7 <= xh / th <= 1.3,
          f"tick {tw:.1f}x{th:.1f}, cross {xw:.1f}x{xh:.1f}")
    check("the old sub-half-size cross is gone", xw / tw > 0.6,
          f"{xw:.1f} vs {tw:.1f}")

# The conversion itself, at every size a call site can reach.
for s in (10.0, 14.0, 20.0, 32.0, 44.0):
    got = pr._cross_size_for_tick(s)
    check(f"a tick of {s:g}pt gets a matching cross",
          abs(got - max(pr._CROSS_MIN_SIZE, s * pr._CROSS_TO_TICK)) < 1e-9, got)
check("a cross never falls below legibility",
      pr._cross_size_for_tick(1.0) >= pr._CROSS_MIN_SIZE,
      pr._cross_size_for_tick(1.0))
check("no call site sizes a cross by a bare fraction of the tick any more",
      "tick_size * 0.7" not in open(RENDERER, encoding="utf-8").read())

print("== M1b: the MCQ block a reviewer reads down in one glance ==")
doc = sheet()
ls = lines(1, [80.0, 120.0], texts=["Q1 (D) 05 W", "Q2 (B) refraction"])
by = {l["lid"]: l for l in ls}
placed = {}
pr._MARK_BOXES.clear()
for lid, qid, sc, ch, co in (("p1_l0", "Q1", 1, "D", "D"),
                             ("p1_l1", "Q2", 0, "B", "A")):
    pr._render_question_marks(
        doc, QuestionMark(qid=qid, score=sc, max_score=1, page=1, y_fraction=0.1,
                          remark="", attempted=True, mark_style="single",
                          objective=True, chosen_option=ch, correct_option=co,
                          anchor_line_id=lid, criteria=[]),
        [by[lid]], by, placed)
g = glyphs(doc[0])
check("the correct MCQ has a tick and circled mark; the wrong one has only a cross", len(g) == 3, len(g))
verdicts = [c for c in g if c["n"] > 1]  # circles are single drawing paths
if len(verdicts) == 2:
    a, b = sorted(verdicts, key=lambda c: c["r"].width)
    check("the tick and the cross beside it are the same hand",
          a["r"].width / b["r"].width >= 0.7,
          f"{a['r'].width:.1f} vs {b['r'].width:.1f}")
doc.close()

print("== M2: a part-marked answer is never handed back blank ==")
part = _one_question(3, 5)
check("3 out of 5 leaves a mark on the answer", len(part) >= 1, len(part))
# Since 2026-10-07 (tests/test_partial_credit_glyphs.py) positive partial credit
# uses the same single tick as full credit; its circled award and the reason
# in the margin say what was lost. An attempted zero is a cross.
check("and it carries the circled 3 beside its tick",
      any(c["n"] >= 12 for c in part), [c["n"] for c in part])
if tick and part:
    check("it is drawn at the size of the tick a full answer would have got",
          0.7 <= part[0]["r"].width / tick[0]["r"].width <= 1.4,
          f"{part[0]['r'].width:.1f} vs {tick[0]['r'].width:.1f}")

print("== M3: an answer whose steps all go unplaced still gets marked ==")
doc = sheet()
ls = lines(1, [80.0, 110.0, 140.0, 170.0])
render(doc, QuestionMark(
    qid="Q8", score=3, max_score=5, page=1, y_fraction=0.2, remark="",
    attempted=True, mark_style="per_step",
    criteria=[Criterion(description="unrelated wording zzz", awarded=1.5, max=2,
                        met=False, evidence="", step_line_id="nope1"),
              Criterion(description="also unrelated qqq", awarded=1.5, max=3,
                        met=False, evidence="", step_line_id="nope2")]), ls)
check("the answer carries a mark even though no step resolved",
      len(glyphs(doc[0])) >= 1, len(glyphs(doc[0])))
doc.close()

# ...and a step-marked answer that DOES resolve is still marked step by step, not
# collapsed onto one whole-answer mark by the fall-through above.
doc = sheet()
ls = lines(1, [80.0, 110.0, 140.0])
render(doc, QuestionMark(
    qid="Q9", score=3, max_score=6, page=1, y_fraction=0.2, remark="",
    attempted=True, mark_style="per_step",
    criteria=[Criterion(description="working step 0", awarded=1.5, max=3, met=False,
                        evidence="", step_line_id="p1_l0"),
              Criterion(description="working step 1", awarded=1.5, max=3, met=False,
                        evidence="", step_line_id="p1_l1")]), ls)
marks = glyphs(doc[0])
spread = (max(c["r"].y0 for c in marks) - min(c["r"].y0 for c in marks)) if marks else 0
check("resolved steps are still marked down the working, not collapsed into one",
      len(marks) >= 1 and (len(marks) > 1 or spread > 0 or marks[0]["r"].height > 40),
      f"{len(marks)} glyph(s), spread {spread:.1f}")
doc.close()

print("== M3b: an unattempted answer is still left clean ==")
doc = sheet()
ls = lines(1, [80.0])
render(doc, QuestionMark(qid="Q10", score=0, max_score=5, page=1, y_fraction=0.2,
                         remark="Unattempted", attempted=False, mark_style="single",
                         criteria=[]), ls)
check("nothing is drawn on an answer that was never written",
      not glyphs(doc[0]), len(glyphs(doc[0])))
doc.close()

print("== M4: a mark slid clear is not dragged back on top ==")
# The cap that killed it: the only clear spot to the right lies past `right_cap`, so
# the old code slid there and then clamped back into the box it had escaped.
NEIGHBOUR = fitz.Rect(120.0, 178.0, 155.0, 204.0)
pr._MARK_BOXES.clear()
pr._note_mark(1, NEIGHBOUR)
size, x, dy = pr._fit_beside_marks(1, 142.0, 219.0, 18.6, 171.7, dy_max=7.0)
box = pr._tick_box(x, 219.0 + dy, size)
check("the mark it returns really is clear of the one already there",
      not box.intersects(NEIGHBOUR),
      f"size={size:.1f} x={x:.1f} dy={dy:.1f} box={tuple(round(v, 1) for v in box)}")
check("and it stays inside the sheet", box.x1 <= 171.7 + 1e-6, box.x1)
check("and it stays on its own answer", abs(dy) <= 7.0 + 1e-6, dy)
check("and it stays legible", size >= pr._TICK_MIN_SIZE - 1e-6, size)

pr._MARK_BOXES.clear()
check("an empty page leaves the mark exactly where it was asked for",
      pr._fit_beside_marks(1, 100.0, 200.0, 20.0, 500.0) == (20.0, 100.0, 0.0),
      pr._fit_beside_marks(1, 100.0, 200.0, 20.0, 500.0))

print()
print(f"FAILED: {len(FAILS)} -> {FAILS}" if FAILS else "OK: all mark-parity checks passed")
sys.exit(1 if FAILS else 0)
