# -*- coding: utf-8 -*-
"""Where a step mark lands, and what happens when nothing says where.

The defect this replaces: a step whose `step_line_id` named a line that is not on
the copy, or that a sibling step had already claimed, fell through to a stage that
spread the leftovers evenly down the answer — `(k + 0.5) * lines / n`. MEASURED
over 278 anchors on five archived copies (ca_final, ca_v3, bl_v2, ca100_tax,
chem_t07): 23 dead and 33 shared, so about a fifth of every step mark on a CA copy
was positioned by arithmetic and still read to the student as "this line is right".

Two rules are pinned here:
  1. A step is placed from its own wording when the label fails — numbers first,
     because they survive Mathpix mangling and words do not.
  2. A step that nothing places is drawn NOWHERE and reported for re-marking.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import pdf_renderer
from anchors import (MIN_CONFIDENT, AnchorResolution, best_line, match_score,
                     resolve_steps, step_text, validate_report_anchors)
from grader import Criterion, GradeReport, QuestionMark
from mathpix import LineBox, PageOCR

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def crit(desc, awarded=1.0, mx=1.0, lid="", quote="", evidence=""):
    return Criterion(description=desc, awarded=awarded, max=mx,
                     met=awarded >= mx, evidence=evidence,
                     anchor_quote=quote, step_line_id=lid)


def line(lid, text, y=100.0):
    return {"lid": lid, "text": text, "page": 1,
            "x0": 80.0, "y0": y, "x1": 500.0, "y1": y + 20}


print("== a figure is found through the OCR's mangling of it ==")
# Real pairs from ca_final: the step names 1,12,000 and Mathpix returned '=112000'.
check("'1,12,000' matches '=112000'",
      match_score("Sale proceeds 8000 x 14 = 1,12,000", "=112000") >= MIN_CONFIDENT)
check("'84,500' matches inside a mangled '845001'",
      match_score("Closing balance 8000 shares at 84,500", "ie．（ ） 845001 －") >= MIN_CONFIDENT)
# The honest limit of matching on a rubric LABEL. This real ca_final pair shares
# only "acquisition" once Mathpix has turned "Pre-acquisition Dividend" into
# "fre－acquisition Divisent", and one word is not evidence — which is exactly why
# `anchor_quote` exists and is required of the model.
_MANGLED = "fre－acquisition Divisent \\( =80,000 \\times 20 \\%=16000 \\)"
_label_only = match_score("Pre-acquisition dividend adjusted to investment", _MANGLED)
check("a rubric label alone is not enough on mangled OCR",
      0 < _label_only < MIN_CONFIDENT, _label_only)
check("but the student's own figure, quoted, does place it",
      match_score("80,000 x 20% = 16000", _MANGLED) >= MIN_CONFIDENT)
check("an unrelated ledger line does not match",
      match_score("Sale proceeds 8000 x 14 = 1,12,000", "To Salary A/c 4,000") < MIN_CONFIDENT,
      match_score("Sale proceeds 8000 x 14 = 1,12,000", "To Salary A/c 4,000"))

print("== rubric words alone are not evidence ==")
# "correct", "note", "entry", "total" are what a rubric label is MADE of. If they
# counted, every step would match every line and the search would be worse than
# the arithmetic it replaces.
check("two stopword-only 'matches' score nothing",
      match_score("Correct entry shown", "Total marks entry correct") == 0.0,
      match_score("Correct entry shown", "Total marks entry correct"))
check("three real content words do reach the bar",
      match_score("depreciation machinery written down",
                  "depreciation on machinery written down value") >= MIN_CONFIDENT)

print("== a short digit run does not match by containment ==")
check("'12' does not match inside '7,61,024'",
      match_score("step 12", "7,61,024") == 0.0,
      match_score("step 12", "7,61,024"))

print("== the model's anchor is kept when the renderer accepts it ==")
band = [line("P1L1", "To Interest on Drawings 1,200", 100),
        line("P1L2", "To Commission 2,400", 130),
        line("P1L3", "Sale proceeds =112000", 160)]
res = resolve_steps([crit("interest on drawings", lid="P1L2")], band,
                    model_anchor_ok=lambda lid, c: True)
check("kept, and marked as the model's own", res[0].source == "model" and res[0].line_id == "P1L2",
      f"{res[0]}")

print("== a dead anchor is re-homed from the step's own words ==")
res = resolve_steps([crit("Sale proceeds 8000 x 14 = 1,12,000", lid="P9L99")], band,
                    model_anchor_ok=lambda lid, c: lid in {l["lid"] for l in band})
check("re-homed rather than guessed", res[0].source == "resolved", f"{res[0]}")
check("and onto the line that carries the figure", res[0].line_id == "P1L3", res[0].line_id)

print("== a quote beats a description ==")
res = resolve_steps([crit("format and presentation", lid="", quote="To Commission 2,400")],
                    band, model_anchor_ok=lambda lid, c: False)
check("the student's own words locate the step",
      res[0].source == "resolved" and res[0].line_id == "P1L2", f"{res[0]}")

print("== nothing matches -> nothing is drawn ==")
res = resolve_steps([crit("presentation of the statement", lid="")], band,
                    model_anchor_ok=lambda lid, c: False)
check("left unresolved instead of placed hopefully", res[0].source == "unresolved", f"{res[0]}")
check("and .placed is false so the renderer skips it", res[0].placed is False)

print("== two steps may share a line; they are never scattered ==")
# The old Stage 3 pushed the second one to "the next free real line", which is how
# 22 duplicated anchors on one CA copy became 22 marks on lines nobody chose.
# Sharing is correct: the grouping stage merges them into ONE tick carrying both
# steps' marks, which is what a teacher draws.
res = resolve_steps([crit("first point", lid="P1L1"), crit("second point", lid="P1L1")],
                    band, model_anchor_ok=lambda lid, c: True)
check("both keep the line the model named",
      [r.line_id for r in res] == ["P1L1", "P1L1"], [r.line_id for r in res])

print("== the release gate can see a dead or shared anchor ==")
ocr = [PageOCR(page=1, image_width=595, image_height=842, lines=[
    LineBox(line_id="P1L1", text="To Interest on Drawings 1,200",
            bbox=(80.0, 100.0, 500.0, 120.0), page=1),
    LineBox(line_id="P1L2", text="To Commission 2,400",
            bbox=(80.0, 130.0, 500.0, 150.0), page=1),
])]
rep = GradeReport(total_score=2, max_total=4, overall_remarks="x", questions=[
    QuestionMark(qid="Q1", score=2, max_score=4, page=1, y_fraction=0.2, remark="short",
                 criteria=[crit("a", lid="P1L1"), crit("b", lid="P1L1"),
                           crit("c", 0.0, 1.0, lid="P7L42")])])
codes = {i.code for i in validate_report_anchors(rep, ocr)}
check("a line_id absent from the copy is reported", "anchor-not-on-page" in codes, codes)
check("two steps on one line are reported", "anchor-shared-in-question" in codes, codes)

print("== an unplaced step never turns the whole answer into a cross ==")
# `drew_positive` used to stand for "every step scored zero", because every
# criterion was placed somewhere no matter what. Steps can now go unplaced, so an
# answer that scored well but placed nothing must NOT be crossed out.
crosses = []
_real_cross, _real_tick = pdf_renderer._draw_cross, pdf_renderer._draw_tick
pdf_renderer._draw_cross = lambda *a, **k: crosses.append(a[1:3])
pdf_renderer._draw_tick = lambda *a, **k: None
try:
    doc = fitz.open()
    _pg = doc.new_page(width=595, height=842)
    # show_pdf_page refuses a genuinely empty source page, so give it ruling —
    # which is what a real answer sheet has anyway.
    for _i in range(8):
        _pg.draw_line(fitz.Point(70, 120 + _i * 70), fitz.Point(520, 120 + _i * 70),
                      color=(0.85, 0.85, 0.9), width=0.5)
    sheet = doc.tobytes()
    doc.close()
    good = GradeReport(
        total_score=8, max_total=10, overall_remarks="Solid work on the ledger.",
        questions=[QuestionMark(
            qid="Q1", score=8, max_score=10, page=1, y_fraction=0.05,
            anchor_line_id="P1L1",
            remark="Closing stock omitted", mark_style="per_step",
            criteria=[crit("nothing on this page matches", 4.0, 5.0, lid="P9L9"),
                      crit("nor does this one", 4.0, 5.0, lid="P9L8")])])
    pdf_renderer.build_evaluated_pdf(
        student_pdf_bytes=sheet, student_filename="s.pdf", student_pngs=[],
        report=good, ocr_pages=ocr)
    check("an 8/10 answer with no placeable step is not crossed out",
          not crosses, f"{len(crosses)} cross(es) drawn")
    check("and the question is reported for re-marking",
          good.unanchored_step_qids == ["Q1"], good.unanchored_step_qids)
finally:
    pdf_renderer._draw_cross, pdf_renderer._draw_tick = _real_cross, _real_tick

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("all anchor-resolution checks passed")
