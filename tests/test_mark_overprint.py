# -*- coding: utf-8 -*-
"""Every step keeps its own mark, and no two marks are printed on top of each other.

A student reading a marked copy has to be able to tell which step each circled
number belongs to. Two marks in one place is worse than one mark out of place: the
values are unreadable and neither can be attributed, and the step that lost its
mark looks unmarked.

Marks are drawn as strokes, not text, so counting them means clustering the red
paths - a text search finds nothing and would pass no matter what was drawn. The
sheet here is written edge to edge on every row on purpose: that is what makes the
renderer move a mark off the writing, which is when marks come close together.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from grader import (Criterion, GradeReport, QuestionMark,  # noqa: E402
                    pdf_or_image_to_pngs)
from mathpix import LineBox, PageOCR  # noqa: E402
from pdf_renderer import build_evaluated_pdf, STRIP_MIN_WIDTH, STRIP_FRACTION  # noqa: E402

FAILS = []
SHEET_W, SHEET_H = 600.0, 800.0
OCR_W, OCR_H = 1500, 2000
STRIP_X = max(STRIP_MIN_WIDTH, SHEET_W * STRIP_FRACTION)  # student content starts after the left strip


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def marked_copy(rows):
    """One question with a step on each row of a page written edge to edge."""
    doc = fitz.open()
    page = doc.new_page(width=SHEET_W, height=SHEET_H)
    for row in rows:
        page.insert_text(fitz.Point(8, row * SHEET_H / OCR_H),
                         "working " * 40, fontsize=7)
    sheet = doc.tobytes()

    lines, criteria = [], []
    for i, row in enumerate(rows):
        lid = f"P1L{i + 1}"
        lines.append(LineBox(line_id=lid, text=f"step {i + 1}",
                             bbox=(20.0, row - 12.0, 1460.0, row + 12.0), page=1))
        criteria.append(Criterion(description=f"step {i + 1}", awarded=0.5,
                                  max=0.5, met=True, step_line_id=lid))
    total = 0.5 * len(rows)
    report = GradeReport(
        total_score=total, max_total=total,
        overall_remarks="Every step is marked where it was written.",
        questions=[QuestionMark(qid="Q1", score=total, max_score=total, page=1,
                                anchor_line_id="P1L1", y_fraction=0.26, remark="",
                                criteria=criteria, mark_style="per_step")])
    return build_evaluated_pdf(
        student_pdf_bytes=sheet, student_filename="s.pdf",
        student_pngs=pdf_or_image_to_pngs(sheet, "s.pdf"),
        report=report, ocr_pages=[PageOCR(page=1, image_width=OCR_W,
                                          image_height=OCR_H, lines=lines)])


def mark_clusters(pdf_bytes):
    """Red strokes grouped into the marks they draw, as (x, y) centres."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    points = []
    for i in range(doc.page_count):
        for drawing in doc[i].get_drawings():
            colour = drawing.get("color") or drawing.get("fill")
            if not (colour and colour[0] > 0.6
                    and colour[1] < 0.4 and colour[2] < 0.4):
                continue
            rect = drawing["rect"]
            if (rect.x0 < STRIP_X or rect.y0 < doc[i].rect.height - SHEET_H
                    or rect.width > 300 or rect.height > 300):
                continue
            points.append((i, (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2))
    doc.close()

    # How close two STROKES have to be to be one mark. A tick is a run of ~16
    # sub-segments about 1.5pt apart, so anything well above that groups a tick
    # correctly; the ceiling is how far apart two different marks get.
    #
    # This was 18, which was fine only while the renderer forced a flat 30pt between
    # marks - a gap wider than the line pitch of ordinary handwriting, and the reason
    # step marks used to walk down off the answer instead of sitting on their lines.
    # Now that a step mark sits on its own line, ticks one 24pt row apart are ~22pt
    # apart and an 18pt radius chained four of them into one "mark". The instrument
    # was measuring the defect, not the marks.
    CLUSTER_R = 8.0
    clusters = []
    for point in points:
        near = [c for c in clusters
                if any(q[0] == point[0]
                       and (q[1] - point[1]) ** 2 + (q[2] - point[2]) ** 2 < CLUSTER_R ** 2
                       for q in c)]
        merged = [point]
        for c in near:
            merged += c
            clusters.remove(c)
        clusters.append(merged)
    return [(c[0][0],
             sum(q[1] for q in c) / len(c),
             sum(q[2] for q in c) / len(c)) for c in clusters]


def closest_pair(clusters):
    best = 1e9
    for a in range(len(clusters)):
        for b in range(a + 1, len(clusters)):
            if clusters[a][0] != clusters[b][0]:
                continue
            dy = clusters[a][2] - clusters[b][2]
            dx = clusters[a][1] - clusters[b][1]
            best = min(best, (dx * dx + dy * dy) ** 0.5)
    return best


# A tick and a circled number are drawn side by side for each step, so a step that
# is marked contributes two stroke clusters.
PER_STEP_CLUSTERS = 2

print("== every step on a densely written page keeps its own mark ==")
for spacing in (60, 80, 100, 120):
    rows = [520 + spacing * i for i in range(6)]
    clusters = mark_clusters(marked_copy(rows))
    check(f"6 steps {spacing}px apart all get marked",
          len(clusters) == 6 * PER_STEP_CLUSTERS,
          f"expected {6 * PER_STEP_CLUSTERS} clusters, found {len(clusters)}")
    gap = closest_pair(clusters)
    check(f"no two marks are printed on top of each other ({spacing}px rows)",
          gap >= 14.0, f"closest pair {gap:.1f}pt apart")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
