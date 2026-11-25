# -*- coding: utf-8 -*-
"""An enumerated answer earns a tick per point, even with no arithmetic in it.

A teacher marking "state five significances of a mission statement" ticks each
point and writes 1+1+1+1+1=5/5. A teacher marking an essay puts one tick and the
total. The renderer knew that difference and the grader is told to declare it via
`mark_style` - but when the grader left the call to us, the only signal that
turned per-step marking on was arithmetic. A theory list has no "=" or "%" in it,
so five separate points collected a single tick between them.

Prose must stay on one tick: over-marking an essay sentence by sentence is the
fault this inference exists to avoid, so both directions are checked here.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from grader import (Criterion, GradeReport, QuestionMark,  # noqa: E402
                    pdf_or_image_to_pngs)
from mathpix import LineBox, PageOCR  # noqa: E402
from pdf_renderer import _ENUM_LINE_RE, build_evaluated_pdf, STRIP_MIN_WIDTH, STRIP_FRACTION  # noqa: E402

# A legacy-contract suite: marks stay inside the sheet and within reach of their
# evidence row, and checking plans are drawn. The teacher layout (the product
# default) has its own contract in test_teacher_layout.py.
import pdf_renderer as _pr
_pr.TEACHER_LAYOUT = False

FAILS = []
OCR_W, OCR_H = 1500, 2000
SHEET_W, SHEET_H = 600.0, 800.0
MARGIN_W = max(STRIP_MIN_WIDTH, SHEET_W * STRIP_FRACTION)
PER_POINT_CLUSTERS = 2      # a tick and a circled mark are drawn side by side


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


ENUMERATED = [
    "(a) unanimity of purpose unifies employees and top management",
    "(b) allocation of resources acts as a guide for optimum use",
    "(c) motivates managers to use the resources efficiently",
    "(d) creates an environment of positive work culture",
    "(e) serves as a focal point for the business and customers",
]
PROSE = [
    "A mission is a collective goal that the company wants to achieve",
    "and works for. Having a mission helps to boost the motivation of",
    "the personnel of the entity and also glues a purpose for operating",
    "to the company. It gives the business a reason to exist and a way",
    "to explain itself to the people who work in it and buy from it.",
]


def marked(texts, style="auto"):
    """Render one question whose points sit on consecutive lines."""
    doc = fitz.open()
    page = doc.new_page(width=SHEET_W, height=SHEET_H)
    rows = [520 + 120 * i for i in range(len(texts))]
    for row, text in zip(rows, texts):
        page.insert_text(fitz.Point(20, row * SHEET_H / OCR_H), text, fontsize=8)
    sheet = doc.tobytes()

    lines, criteria = [], []
    for i, (row, text) in enumerate(zip(rows, texts)):
        lid = f"P1L{i + 1}"
        lines.append(LineBox(line_id=lid, text=text,
                             bbox=(20.0, row - 12.0, 1200.0, row + 12.0), page=1))
        criteria.append(Criterion(description=f"point {i + 1}", awarded=1.0,
                                  max=1.0, met=True, step_line_id=lid))
    total = float(len(texts))
    report = GradeReport(
        total_score=total, max_total=total,
        overall_remarks="Each point was marked where it was written.",
        questions=[QuestionMark(qid="Q4b", score=total, max_score=total, page=1,
                                anchor_line_id="P1L1", y_fraction=0.26, remark="",
                                criteria=criteria, mark_style=style)])
    pdf = build_evaluated_pdf(
        student_pdf_bytes=sheet, student_filename="s.pdf",
        student_pngs=pdf_or_image_to_pngs(sheet, "s.pdf"), report=report,
        ocr_pages=[PageOCR(page=1, image_width=OCR_W, image_height=OCR_H,
                           lines=lines)])
    return _clusters(pdf), _strip_text(pdf)


def _strip_text(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = "".join(span["text"]
                  for i in range(doc.page_count)
                  for block in doc[i].get_text("dict")["blocks"]
                  for line in block.get("lines", [])
                  for span in line["spans"]
                  if span["bbox"][0] >= doc[i].rect.width - MARGIN_W
                  and span["bbox"][1] >= doc[i].rect.height - SHEET_H)
    doc.close()
    return out


def _clusters(pdf_bytes):
    """Red strokes grouped into the marks they draw. Marks are strokes, not text."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    points = []
    for i in range(doc.page_count):
        for drawing in doc[i].get_drawings():
            colour = drawing.get("color") or drawing.get("fill")
            if not (colour and colour[0] > 0.6
                    and colour[1] < 0.4 and colour[2] < 0.4):
                continue
            rect = drawing["rect"]
            if (rect.x0 >= doc[i].rect.width - MARGIN_W or rect.y0 < doc[i].rect.height - SHEET_H
                    or rect.width > 300 or rect.height > 300):
                continue
            points.append((i, (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2))
    doc.close()
    groups = []
    for point in points:
        near = [g for g in groups
                if any(q[0] == point[0]
                       and (q[1] - point[1]) ** 2 + (q[2] - point[2]) ** 2 < 22 ** 2
                       for q in g)]
        merged = [point]
        for g in near:
            merged += g
            groups.remove(g)
        groups.append(merged)
    return len(groups)


print("== a line that opens a point is recognised; a citation mid-sentence is not ==")
for text, expected in [("(a) unanimity of purpose", True),
                       ("(b)  allocation of resources", True),
                       ("ii. motivates the managers", True),
                       ("3) creates an environment", True),
                       ("iv) serves as a focal point", True),
                       ("- translation of purpose into goals", True),
                       ("1. Compute the total income of Mr X", True),
                       ("The mission (a) must be clear to all", False),
                       ("Having a mission helps to boost the morale", False),
                       ("a)", False)]:
    check(f"{text[:44]!r} -> {expected}",
          bool(_ENUM_LINE_RE.match(text)) is expected)

print()
print("== the grader leaving it to us: infer from the answer ==")
enum_marks, enum_strip = marked(ENUMERATED, "auto")
check("an enumerated theory list gets a mark per point",
      enum_marks == len(ENUMERATED) * PER_POINT_CLUSTERS,
      f"expected {len(ENUMERATED) * PER_POINT_CLUSTERS} clusters, got {enum_marks}")
check("and the strip shows the points adding up",
      "1+1+1+1+1=5/5" in enum_strip, enum_strip[:80])

prose_marks, prose_strip = marked(PROSE, "auto")
check("flowing prose still gets one tick, not one per sentence",
      prose_marks == 1, f"{prose_marks} marks drawn")
check("and its strip shows the total alone",
      "+" not in prose_strip and "5/5" in prose_strip, prose_strip[:80])

print()
print("== an explicit mark_style still wins over the inference ==")
check("per_step on the list marks every point",
      marked(ENUMERATED, "per_step")[0] == len(ENUMERATED) * PER_POINT_CLUSTERS)
check("single on prose keeps one mark", marked(PROSE, "single")[0] == 1)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
