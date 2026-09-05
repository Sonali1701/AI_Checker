# -*- coding: utf-8 -*-
"""Marks must land on the page the criterion actually points at.

A 100-mark CA answer runs over three or four pages. The renderer used to clip a
question's band at the NEXT question's anchor, so when that next question was
never attempted (and still carried a nominal anchor on the first page), every
criterion anchored further down was rejected as out-of-band and re-spread over
the first page: 23 ticks piled onto page 2 and pages 3-4 came out blank.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from grader import Criterion, GradeReport, QuestionMark
from mathpix import LineBox, PageOCR
import pdf_renderer as pr
from pdf_renderer import build_evaluated_pdf

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


N_PAGES = 4
LINES_PER_PAGE = 8
W, H = 595.0, 842.0


def blank_pages() -> bytes:
    """A scanned booklet: pages carrying no text layer of their own, so any text
    on the rendered output is necessarily an annotation we drew."""
    doc = fitz.open()
    for _ in range(N_PAGES):
        page = doc.new_page(width=W, height=H)
        # Ruled lines stand in for the scan's ink: a truly empty page cannot be
        # stamped onto the widened sheet at all.
        for i in range(LINES_PER_PAGE):
            page.draw_line(fitz.Point(70, 130 + i * 70), fitz.Point(520, 130 + i * 70),
                           color=(0.8, 0.8, 0.85), width=0.5)
    return doc.tobytes()


def fake_ocr() -> list[PageOCR]:
    pages = []
    for p in range(1, N_PAGES + 1):
        lines = [
            LineBox(line_id=f"P{p}L{i}", text=f"working step {i} on page {p}",
                    bbox=(80.0, 80.0 + i * 70, 500.0, 130.0 + i * 70), page=p)
            for i in range(LINES_PER_PAGE)
        ]
        pages.append(PageOCR(page=p, image_width=int(W), image_height=int(H), lines=lines))
    return pages


def report() -> GradeReport:
    # Q1 is a long answer the student wrote across pages 1-4.
    spread = [f"P{p}L{i}" for p in range(1, N_PAGES + 1) for i in (2, 4, 6)]
    q1 = QuestionMark(
        qid="Q1", score=6.0, max_score=12.0, page=1, anchor_line_id="P1L1",
        y_fraction=0.1, remark="partly correct", mark_style="per_step",
        criteria=[Criterion(description=f"step {k}", awarded=0.5, max=1.0,
                            met=True, evidence="", step_line_id=lid)
                  for k, lid in enumerate(spread)],
    )
    # Q2 was never attempted: no criteria, no anchors, but the model still gave
    # it a nominal position just below Q1's start on page 1.
    q2 = QuestionMark(qid="Q2", score=0.0, max_score=8.0, page=1,
                      anchor_line_id="P1L3", y_fraction=0.3,
                      remark="not attempted", criteria=[])
    return GradeReport(total_score=6.0, max_total=20.0,
                       overall_remarks="x", questions=[q1, q2])


print("== a multi-page answer gets marked on every page it spans ==")
pdf = build_evaluated_pdf(
    student_pdf_bytes=blank_pages(), student_filename="s.pdf",
    student_pngs=[], report=report(), ocr_pages=fake_ocr(), mark_scale=1.0,
)
out = fitz.open(stream=pdf, filetype="pdf")
# Output page 0 carries the report strip plus student page 1; after that it is
# one output page per student page.
annotated = []
for i in range(len(out)):
    words = out[i].get_text().strip()
    if words:
        annotated.append(i)
print(f"  rendered {len(out)} pages; pages carrying annotation text: {annotated}")

for student_page in range(2, N_PAGES + 1):
    idx = student_page - 1
    check(f"student page {student_page} is annotated",
          idx in annotated, f"output page {idx} has no annotation text")

print("== every anchored criterion lands on its own page ==")
# Count the per-step scores drawn on each page. Each page owns 3 criteria.
for student_page in range(1, N_PAGES + 1):
    idx = student_page - 1
    txt = out[idx].get_text()
    check(f"page {student_page} shows step marks",
          "0.5" in txt, f"no step score on output page {idx}")

print("== a ticked answer is not ticked again in the margin ==")
# A released Core Maths page carried eight ticks down the working and eight more
# down the strip beside them — one pair per question. The strip's tick is a
# fallback for answers the sheet cannot mark, never a second opinion.
def red_marks(page, edge):
    """Red ink on this page, clustered into marks, split by the strip boundary."""
    rects = []
    for drawing in page.get_drawings():
        colour = drawing.get("color") or drawing.get("fill")
        if not (colour and colour[0] > 0.6 and colour[1] < 0.4 and colour[2] < 0.4):
            continue
        rect = drawing["rect"]
        if rect.width > page.rect.width * 0.4 or rect.height > page.rect.height * 0.4:
            continue  # the strip's own border rule
        rects.append(rect)
    groups = []
    for rect in rects:
        for group in groups:
            if (rect.x0 < group.x1 + 6 and group.x0 < rect.x1 + 6
                    and rect.y0 < group.y1 + 6 and group.y0 < rect.y1 + 6):
                groups[groups.index(group)] = group | rect
                break
        else:
            groups.append(fitz.Rect(rect))
    return ([g for g in groups if g.x1 <= edge + 2],
            [g for g in groups if g.x0 >= edge - 2])


full = QuestionMark(
    qid="Q1", score=3.0, max_score=3.0, page=1, anchor_line_id="P1L1",
    y_fraction=0.1, remark="", mark_style="per_step",
    criteria=[Criterion(description=f"step {k}", awarded=1.0, max=1.0, met=True,
                        evidence="", step_line_id=f"P1L{i}")
              for k, i in enumerate((2, 4, 6))],
)
one = build_evaluated_pdf(
    student_pdf_bytes=blank_pages(), student_filename="s.pdf", student_pngs=[],
    report=GradeReport(total_score=3.0, max_total=3.0, overall_remarks="x",
                       questions=[full]),
    ocr_pages=fake_ocr(), mark_scale=1.0)
doc_one = fitz.open(stream=one, filetype="pdf")
on_answer, in_strip = red_marks(doc_one[0], W)
check("the answer itself is ticked", len(on_answer) >= 1, len(on_answer))
check("and the strip does not tick it again", not in_strip,
      f"{len(in_strip)} tick(s) still drawn in the margin")
doc_one.close()

print("== a step tick follows the row, not a short label ==")
# "(c)" sits in the OCR as its own line ending at x=208 while the working it labels
# runs on to x=500. Placing the tick just past the anchored line stranded it in the
# left margin of a released Core Maths page.
LABEL_ROW_Y = (80.0, 130.0)
labelled = [
    LineBox(line_id="P1L0", text="(c)", bbox=(80.0, LABEL_ROW_Y[0], 208.0, LABEL_ROW_Y[1]), page=1),
    LineBox(line_id="P1L1", text="11 + 2 = 13", bbox=(220.0, LABEL_ROW_Y[0], 500.0, LABEL_ROW_Y[1]), page=1),
]
short_anchor = QuestionMark(
    qid="Q1", score=3.0, max_score=3.0, page=1, anchor_line_id="P1L0",
    y_fraction=0.1, remark="", mark_style="per_step",
    criteria=[Criterion(description="step", awarded=1.0, max=1.0, met=True,
                        evidence="", step_line_id="P1L0"),
              Criterion(description="step2", awarded=2.0, max=2.0, met=True,
                        evidence="", step_line_id="P1L1")],
)
stranded = build_evaluated_pdf(
    student_pdf_bytes=blank_pages(), student_filename="s.pdf", student_pngs=[],
    report=GradeReport(total_score=3.0, max_total=3.0, overall_remarks="x",
                       questions=[short_anchor]),
    # Only this page's OCR: with a full booklet of candidate lines the resolver
    # re-homes these synthetic steps elsewhere, and the row under test is the point.
    ocr_pages=[PageOCR(page=1, image_width=int(W), image_height=int(H), lines=labelled)],
    mark_scale=1.0)
doc_two = fitz.open(stream=stranded, filetype="pdf")
marks, _ = red_marks(doc_two[0], W)
check("the row is marked", bool(marks), "no red mark on the answer at all")
# The rescue is deliberately narrow: a ledger's particulars cell also ends in the
# left third of the page, and dragging its tick to the far edge of the table would
# be wrong on the papers that are mostly tables.
CW = 1500.0
ledger = {"c": {"page": 1, "x0": 100, "x1": 320, "y0": 500, "y1": 530},
          "d": {"page": 1, "x0": 340, "x1": 1400, "y0": 500, "y1": 530}}
stub = {"a": {"page": 1, "x0": 130, "x1": 208, "y0": 1700, "y1": 1730},
        "b": {"page": 1, "x0": 220, "x1": 1199, "y0": 1702, "y1": 1732}}
check("a narrow far-left label hands the tick to the row's working",
      pr._row_writing_end(stub, 1, 1700, 1730, 130, 208, CW) == 1199)
check("a ledger cell keeps its tick beside its own amount",
      pr._row_writing_end(ledger, 1, 500, 530, 100, 320, CW) == 320)
check("an anchor out in the page body is never moved",
      pr._row_writing_end(stub, 1, 1700, 1730, 400, 700, CW) == 700)
if marks:
    # The student page is stamped below the cover, so compare on x only: every mark
    # for this row must sit past the working, never beside the "(c)" that labels it.
    check("no tick is stranded left of the working",
          min(m.x0 for m in marks) > 208,
          f"leftmost mark at x={min(m.x0 for m in marks):.0f}, working ends at 500")
doc_two.close()

print("== an unattempted question still reports its zero ==")
check("Q2 verdict is drawn", "Q2" in out[0].get_text(), out[0].get_text()[:200])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
