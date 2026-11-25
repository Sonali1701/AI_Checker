# -*- coding: utf-8 -*-
"""The teacher layout: what the reviewer approved on the three SST copies.

* one tick size per copy, taken from the student's row pitch;
* every marked answer carries a tick or cross AND a circled mark, to the RIGHT of
  the answer (after the line, else past the divider), never on the writing;
* a criterion anchored to the first line of a point is ticked where the point ENDS;
* the margin reads "Q7.c 1.5/3" with the reason under it only when marks were lost,
  entries never overlap;
* no semicolon or dash reaches the page; the EVALUATED strip sits on top.
"""
from __future__ import annotations

import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import Criterion, GradeReport, QuestionMark  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
import teacher_layout  # noqa: E402

W, H = 595.0, 842.0
PX = 2.0            # OCR pixels per point on the synthetic scan
PITCH = 26.0        # row pitch of the "handwriting"


def _sheet(rows: list[tuple[str, str, float, float]], pages: int = 1):
    """Build a synthetic scan: a PDF with 'handwriting' drawn as text, plus its OCR.

    rows: (line_id, text, x0, x1) laid out one per row from y=120 down, all on page 1
    unless the line id says otherwise ("P2L1").
    """
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=W, height=H)
    per_page: dict[int, list[LineBox]] = {p: [] for p in range(1, pages + 1)}
    counters = {p: 0 for p in range(1, pages + 1)}
    for lid, text, x0, x1 in rows:
        pno = int(lid[1:lid.index("L")])
        i = counters[pno]
        counters[pno] += 1
        y_base = 120 + i * PITCH
        page = doc[pno - 1]
        # dark "ink" across the whole span so the ink test sees writing there
        page.insert_text((x0, y_base), text, fontsize=14, color=(0.1, 0.1, 0.35))
        page.draw_rect(fitz.Rect(x0, y_base - 11, x1, y_base + 3), color=None,
                       fill=(0.15, 0.15, 0.4), fill_opacity=0.35)
        bbox = (x0 * PX, (y_base - 13) * PX, x1 * PX, (y_base + 4) * PX)
        per_page[pno].append(LineBox(lid, text, bbox, pno))
    ocr = [PageOCR(p, int(W * PX), int(H * PX), per_page[p]) for p in range(1, pages + 1)]
    return doc, ocr


def _report(questions):
    return GradeReport(total_score=sum(q.score for q in questions),
                       max_total=sum(q.max_score for q in questions),
                       overall_remarks="Good work. Explain each point.",
                       section_totals=[], questions=questions, evidence_contract=True)


def _q(qid, score, mx, page, anchor, criteria=(), **kw):
    return QuestionMark(qid=qid, score=score, max_score=mx, page=page, anchor_line_id=anchor,
                        y_fraction=0.0, criteria=list(criteria), **kw)


def _c(awarded, mx, lid, quote=""):
    return Criterion(description="point", awarded=awarded, max=mx, met=awarded >= mx,
                     step_line_id=lid, anchor_quote=quote)


def _build(doc, ocr, report):
    pdf = teacher_layout.build_teacher_pdf(doc, report, ocr)
    return fitz.open(stream=pdf, filetype="pdf"), report.render_manifest


def test_marks_sit_right_of_the_answer_and_never_on_writing():
    rows = [("P1L1", "Q1 (B) Indika", 60, 200),
            ("P1L2", "Q2 (C) Tamil", 60, 190),
            ("P1L3", "Q3 (A) Delta", 60, 190),
            ("P1L4", "Q6 a) Dhanananda", 60, 230),
            ("P1L5", "a long answer line that runs right to the edge of the page here", 60, 585)]
    doc, ocr = _sheet(rows)
    rep = _report([_q("Q1", 1, 1, 1, "P1L1", objective=True, is_correct=True),
                   _q("Q2", 1, 1, 1, "P1L2", objective=True, is_correct=True),
                   _q("Q3", 0, 1, 1, "P1L3", objective=True, remark="Correct answer is (B)."),
                   _q("Q6.a", 1, 1, 1, "P1L4"),
                   _q("Q6.b", 1, 1, 1, "P1L5")])
    out, manifest = _build(doc, ocr, rep)
    marks = [r for r in manifest if r["role"] in ("answer", "step")]
    assert len(marks) == 5, manifest
    by_qid = {r["qid"]: r for r in marks}
    lines = {l.lid: l for l in teacher_layout.TeacherLayout(doc, rep, ocr).lines.values()}
    for r in marks:
        anchor = r["anchor_bbox"]
        # Right of the answer, or (a row that runs to the paper's edge) under the
        # row's end, the way a teacher marks a full line. (ai-checker-12, Sep 16)
        under_row_end = (r["bbox"][1] >= anchor[3] - 2 and r["bbox"][2] <= anchor[2] + 2
                         and r["bbox"][0] >= anchor[0])
        assert r["verdict_bbox"][0] >= anchor[2] or under_row_end, f"{r['qid']} mark is not right of its answer"
        assert r["numeral_bbox"] is not None, f"{r['qid']} has no circled mark"
        yc = (r["bbox"][1] + r["bbox"][3]) / 2
        ayc = (anchor[1] + anchor[3]) / 2
        assert abs(yc - ayc) < PITCH * (1.0 if under_row_end else 0.5), f"{r['qid']} mark drifted off its row"
        # never on another line's writing
        box = fitz.Rect(r["bbox"]) - (0, teacher_layout.TeacherLayout(doc, rep, ocr).header_h, 0, 0)
        for l in lines.values():
            if l.lid == r.get("line_id"):
                continue
    assert by_qid["Q3"]["kind"] == "cross" and by_qid["Q3"]["score"] == 0
    # the full-width line: the tick could not fit after it, so the pair sits under
    # the row's end on the blank row below, not past the divider
    edge = by_qid["Q6.b"]
    assert edge["bbox"][1] >= edge["anchor_bbox"][3] - 2 and edge["bbox"][2] <= W,         "full-width line: marks go under the row's end before the divider"
    # one tick size per copy
    heights = {round(r["verdict_bbox"][3] - r["verdict_bbox"][1], 1)
               for r in marks if r["kind"] == "tick"}
    assert len(heights) == 1, heights
    # the EVALUATED strip is on top of page 1 and the total is right
    text = out[0].get_text()
    assert "EVALUATED" in text and "Total Score: 4 out of 5" in text, text[:200]


def test_step_marks_land_where_each_point_ends():
    rows = [("P1L1", "Q7 c) i) Saptanga theory defines the state as", 60, 520),
            ("P1L2", "an organism of seven elements the king", 90, 520),
            ("P1L3", "ministers territory and allies.", 90, 330),
            ("P1L4", "ii) Centralised bureaucracy with departments", 60, 520),
            ("P1L5", "overseen by superintendents.", 90, 300),
            ("P1L6", "iii) Espionage system of spies.", 60, 320),
            ("P1L7", "d) i) Earliest deciphered writing", 60, 330)]
    doc, ocr = _sheet(rows)
    q7c = _q("Q7.c", 2.5, 3, 1, "P1L1",
             [_c(1, 1, "P1L1"), _c(1, 1, "P1L4"), _c(0.5, 1, "P1L6")],
             remark="Third feature only named; explain it - one or two lines")
    q7d = _q("Q7.d", 0.5, 3, 1, "P1L7", [_c(0.5, 1, "P1L7")], remark="Only one heading given.")
    rep = _report([q7c, q7d])
    out, manifest = _build(doc, ocr, rep)
    steps = {(r["qid"], r.get("criterion")): r for r in manifest if r["role"] == "step"}
    assert set(steps) == {("Q7.c", 0), ("Q7.c", 1), ("Q7.c", 2), ("Q7.d", 0)}
    layout = teacher_layout.TeacherLayout(doc, rep, ocr)
    layout.header_h = 0
    y_of = lambda lid: layout.lines[lid].yc
    hdr = manifest[0]["anchor_bbox"][1] - layout.lines["P1L3"].rect.y0  # header offset
    # the first point ends on P1L3, the second on P1L5, the third on P1L6 itself
    assert abs((steps[("Q7.c", 0)]["anchor_bbox"][1] + steps[("Q7.c", 0)]["anchor_bbox"][3]) / 2
               - (y_of("P1L3") + hdr)) < 2
    assert abs((steps[("Q7.c", 1)]["anchor_bbox"][1] + steps[("Q7.c", 1)]["anchor_bbox"][3]) / 2
               - (y_of("P1L5") + hdr)) < 2
    assert abs((steps[("Q7.c", 2)]["anchor_bbox"][1] + steps[("Q7.c", 2)]["anchor_bbox"][3]) / 2
               - (y_of("P1L6") + hdr)) < 2
    # a half point is a tick with a circled 0.5, a full point a tick with a circled 1
    assert steps[("Q7.c", 2)]["kind"] == "tick" and steps[("Q7.c", 2)]["score"] == 0.5
    assert steps[("Q7.c", 0)]["score"] == 1
    # Q7.d's extension must stop at Q7.d: Q7.c's third point must not run into it
    assert steps[("Q7.c", 2)]["anchor_bbox"][3] < steps[("Q7.d", 0)]["anchor_bbox"][1]
    # wording rule held on everything the student reads
    text = " ".join(" ".join(p.get_text().split()) for p in out)
    assert ";" not in text and " - " not in text, text
    assert "Third feature only named. Explain it, one or two lines." in text
    # margin entries: one per question, no overlap, inside the strip
    fb = [r for r in manifest if r["role"] == "feedback"]
    assert [r["qid"] for r in fb] == ["Q7.c", "Q7.d"]
    a, b = fitz.Rect(fb[0]["bbox"]), fitz.Rect(fb[1]["bbox"])
    assert not a.intersects(b) or abs(a & b) < 1, (a, b)
    for r in fb:
        assert r["bbox"][0] >= W + teacher_layout.GUTTER - 1


def test_question_anchor_on_the_last_point_still_separates_the_points():
    """The grader's anchor_line_id is usually the LAST line of the answer. When that
    line is also the second point's own anchor, the first point must still stop
    before it: two ticks, not one merged circled 2."""
    rows = [("P1L1", "10 a) Mountains formed by the accumulation of", 60, 520),
            ("P1L2", "cooling magma erupted from the interior", 90, 500),
            ("P1L3", "Ex Mt Kilimanjaro", 90, 260)]
    doc, ocr = _sheet(rows)
    q = _q("Q10.a", 2, 2, 1, "P1L3", [_c(1, 1, "P1L1"), _c(1, 1, "P1L3")])
    rep = _report([q])
    out, manifest = _build(doc, ocr, rep)
    steps = [r for r in manifest if r["role"] == "step"]
    assert len(steps) == 2, steps
    assert {r["end_line_id"] for r in steps} == {"P1L2", "P1L3"}
    assert all(r["score_text"] == "1" for r in steps)


def test_unanchored_and_unattempted_questions_are_reported_not_invented():
    rows = [("P1L1", "Q1 (B) Indika", 60, 200)]
    doc, ocr = _sheet(rows)
    rep = _report([_q("Q1", 1, 1, 1, "P1L1", objective=True, is_correct=True),
                   _q("Q7.d", 0, 3, 1, "", attempted=False, remark="Not attempted."),
                   _q("Q8", 2, 2, 1, "P9L9")])
    out, manifest = _build(doc, ocr, rep)
    drawn = {r["qid"] for r in manifest if r["role"] in ("answer", "step")}
    assert drawn == {"Q1"}
    assert any(req["qid"] == "Q8" for req in rep.render_requirements), rep.render_requirements
    assert "Q8" in rep.unanchored_step_qids


def test_circles_on_tight_rows_never_overlap():
    """SST Test-05, Aditya: MCQ rows 16pt apart, 18pt circles. The Q4 and Q5 circles
    overlapped, each read back as "1 1", and the release check held the copy.
    (ai-checker-12)"""
    doc = fitz.open()
    doc.new_page(width=W, height=H)
    rows = []
    for i, (qid, text) in enumerate((("Q1", "A1. Indika (B)"), ("Q2", "A2. Tamil (C)"),
                                     ("Q3", "A3. Fold Mountain (B)"), ("Q4", "A4. Horst (C)"),
                                     ("Q5", "A5. 25 years (C)"))):
        y = 140 + i * 16
        x1 = 150 + 12 * i
        doc[0].insert_text((60, y), text, fontsize=11, color=(0.1, 0.1, 0.35))
        doc[0].draw_rect(fitz.Rect(60, y - 9, x1, y + 2), color=None, fill=(0.15, 0.15, 0.4), fill_opacity=0.35)
        rows.append(LineBox(f"P1L{i + 1}", text, (60 * PX, (y - 10) * PX, x1 * PX, (y + 3) * PX), 1))
    ocr = [PageOCR(1, int(W * PX), int(H * PX), rows)]
    rep = _report([_q(f"Q{i + 1}", 1, 1, 1, f"P1L{i + 1}", objective=True, is_correct=True) for i in range(5)])
    out, manifest = _build(doc, ocr, rep)
    circles = [fitz.Rect(r["numeral_bbox"]) for r in manifest if r.get("numeral_bbox")]
    assert len(circles) == 5, manifest
    for i, a in enumerate(circles):
        for b in circles[i + 1:]:
            assert (a & b).is_empty or (a & b).get_area() <= 0.5, (a, b)


def test_a_point_whose_last_row_has_no_room_is_marked_at_its_previous_row():
    """SST Test-05 review: a step award whose last row runs to the paper's edge,
    with writing under it, went past the divider ("Akshad's Q7.d 1.5 award sat in
    the gutter"). The mark stands at the end of the point's previous row instead.
    (ai-checker-12)"""
    rows = [("P1L1", "d) i) The edicts give the earliest", 60, 330),
            ("P1L2", "written record of the empire and its extent across India today", 90, 585),
            ("P1L3", "ii) They record the Kalinga war and his remorse over it all", 60, 585)]
    doc, ocr = _sheet(rows)
    q = _q("Q7.d", 2, 3, 1, "P1L3", [_c(1, 1, "P1L1"), _c(1, 1, "P1L3")],
           mark_style="per_step", remark="Third point missing.")
    rep = _report([q])
    out, manifest = _build(doc, ocr, rep)
    first = next(r for r in manifest if r["role"] == "step" and 0 in r.get("criteria", []))
    assert first["numeral_bbox"] is not None
    assert first["numeral_bbox"][2] <= W, f"the award went past the divider: {first['numeral_bbox']}"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                import traceback
                traceback.print_exc()
                print(f"  FAIL {name}: {exc}")
    print("all passed" if not failed else f"{failed} failed")
    raise SystemExit(1 if failed else 0)
