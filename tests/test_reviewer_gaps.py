# -*- coding: utf-8 -*-
"""The four gaps a PW reviewer raised on AI-marked commerce sheets.

Verbatim from "Copy Checked By AI - Improvement Required":
  1. No mark will be awarded for "Total" but for "Interest on Drawings"
  2. The solution cut by the student has also been evaluated
  3. The wrong answer has been marked with a tick, and marks awarded. Though a
     remark is given
  4. Total marks gained by a student for each question have not been given
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import pdf_renderer
from grader import (
    AUDIT_SYSTEM_PROMPT,
    Criterion,
    GradeReport,
    QuestionMark,
    _reconcile_report,
    build_system_prompt,
)
from mathpix import (LineBox, PageOCR, build_transcript, pageocr_from_dict,
                     pageocr_to_dict)
from pdf_renderer import _is_total_criterion, _is_total_row, build_evaluated_pdf

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("== gap 1: a Total row is not a working line ==")
for text in ("Total 1,20,000", "Total", "TOTAL", "Grand Total 2,40,000",
             "By Balance c/d 96,000", "To Balance b/d 45,000", "Sub-total 12,000",
             "c/d 5,000", "कुल 5,000"):
    check(f"{text!r} is a total row", _is_total_row(text))
for text in ("Interest on Drawings 1,200", "To Cash A/c 8,000", "To Salary 4,000",
             "Total Debtors A/c 30,000", "Total Creditors",
             "The total amount payable by the firm is shown below here", ""):
    check(f"{text[:34]!r} is NOT a total row", not _is_total_row(text))

print("== gap 1: a criterion about the total may still be marked there ==")
check("'correct totals and casting' is a total criterion",
      _is_total_criterion(Criterion(description="correct totals and casting",
                                    awarded=1, max=1, met=True, evidence="")))
check("'balancing the account' is a total criterion",
      _is_total_criterion(Criterion(description="balancing the account",
                                    awarded=1, max=1, met=True, evidence="")))
check("'interest on drawings' is NOT a total criterion",
      not _is_total_criterion(Criterion(description="interest on drawings charged",
                                        awarded=1, max=1, met=True, evidence="")))

print("== gap 3: a tick means fully correct, not merely 'earned something' ==")
rep = _reconcile_report(GradeReport(
    total_score=0, max_total=4, overall_remarks="x",
    questions=[QuestionMark(qid="Q1", score=1, max_score=4, page=1, y_fraction=0.2,
                            remark="Wrong method",
                            criteria=[Criterion(description="step", awarded=1, max=4,
                                                met=True, evidence="")])]), None, None)
crit = rep.questions[0].criteria[0]
check("met is DERIVED, not trusted: 1 of 4 is not met", crit.met is False,
      f"met={crit.met}")
check("the awarded value survives", crit.awarded == 1)

over = _reconcile_report(GradeReport(
    total_score=0, max_total=2, overall_remarks="x",
    questions=[QuestionMark(qid="Q1", score=0, max_score=2, page=1, y_fraction=0.2,
                            criteria=[Criterion(description="step", awarded=9, max=2,
                                                met=False, evidence="")])]), None, None)
oc = over.questions[0].criteria[0]
check("an over-award is clamped to the step's maximum", oc.awarded == 2, oc.awarded)
check("and then counts as met", oc.met is True)

full = _reconcile_report(GradeReport(
    total_score=0, max_total=2, overall_remarks="x",
    questions=[QuestionMark(qid="Q1", score=0, max_score=2, page=1, y_fraction=0.2,
                            criteria=[Criterion(description="step", awarded=2, max=2,
                                                met=False, evidence="")])]), None, None)
check("full marks are met even if the model said otherwise",
      full.questions[0].criteria[0].met is True)

print("== gap 3: the renderer only ticks a fully earned step ==")
ticks, circles = [], []
_real_tick, _real_circle = pdf_renderer._draw_tick, pdf_renderer._draw_number_in_circle
pdf_renderer._draw_tick = lambda *a, **k: ticks.append(a[1:3])
pdf_renderer._draw_number_in_circle = lambda *a, **k: circles.append(a[3])


def sheet(n=2):
    doc = fitz.open()
    for _ in range(n):
        pg = doc.new_page(width=595, height=842)
        for i in range(8):
            pg.draw_line(fitz.Point(70, 120 + i * 70), fitz.Point(520, 120 + i * 70),
                         color=(0.85, 0.85, 0.9), width=0.5)
    return doc.tobytes()


def ocr(rows):
    lines = [LineBox(line_id=f"P1L{i}", text=t,
                     bbox=(80.0, 100.0 + i * 70, 500.0, 150.0 + i * 70), page=1)
             for i, t in enumerate(rows)]
    return [PageOCR(page=1, image_width=595, image_height=842, lines=lines)]


try:
    ROWS = ["To Interest on Drawings 1,200", "To Commission 2,400",
            "To Salary 4,000", "Total 7,600"]
    partial = GradeReport(
        total_score=1.5, max_total=4, overall_remarks="x",
        questions=[QuestionMark(
            qid="Q1", score=1.5, max_score=4, page=1, y_fraction=0.1,
            anchor_line_id="P1L0", mark_style="per_step", remark="Method wrong",
            criteria=[
                Criterion(description="interest on drawings", awarded=0.5, max=2,
                          met=False, evidence="", step_line_id="P1L0"),
                Criterion(description="commission", awarded=1, max=1, met=True,
                          evidence="", step_line_id="P1L1"),
            ])])
    build_evaluated_pdf(student_pdf_bytes=sheet(), student_filename="s.pdf",
                        student_pngs=[], report=partial, ocr_pages=ocr(ROWS),
                        mark_scale=1.0)
    check("the fully earned step is ticked", len(ticks) >= 1, f"{len(ticks)} ticks")
    check("the PARTIAL step is not ticked", len(ticks) == 1,
          f"{len(ticks)} ticks for one full + one partial step")
    check("both steps still show their marks", {"0.5", "1"} <= set(circles), circles)
finally:
    pdf_renderer._draw_tick, pdf_renderer._draw_number_in_circle = _real_tick, _real_circle

print("== gap 4: the question total is written on the sheet ==")
report4 = GradeReport(
    total_score=3, max_total=4, overall_remarks="x",
    questions=[QuestionMark(
        qid="Q1", score=3, max_score=4, page=1, y_fraction=0.1,
        anchor_line_id="P1L0", mark_style="per_step",
        criteria=[Criterion(description="interest on drawings", awarded=2, max=2,
                            met=True, evidence="", step_line_id="P1L0"),
                  Criterion(description="commission", awarded=1, max=2, met=False,
                            evidence="", step_line_id="P1L1")])])
pdf = build_evaluated_pdf(student_pdf_bytes=sheet(), student_filename="s.pdf",
                          student_pngs=[], report=report4,
                          ocr_pages=ocr(["To Interest on Drawings 1,200",
                                         "To Commission 2,400", "Total 3,600"]),
                          mark_scale=1.0)
out = fitz.open(stream=pdf, filetype="pdf")
page_text = out[0].get_text()
sheet_only = " ".join(
    sp["text"] for b in out[0].get_text("dict")["blocks"] for l in b.get("lines", [])
    for sp in l["spans"] if sp["bbox"][0] < out[0].rect.width * 0.72)
check("the question total appears ON the sheet, not only in the strip",
      "3/4" in sheet_only, f"sheet text: {sheet_only!r}")
check("the strip still carries its own entry", "Q1" in page_text)

print("== every question that lost marks carries a reason ==")
# From a real English copy: "Q3: 3.0 / 5.0" appeared with NO remark on the sheet and
# no entry under "Where marks were lost". The student saw the deduction and no reason.
def _c(desc, awarded, mx, ev=""):
    return Criterion(description=desc, awarded=awarded, max=mx,
                     met=awarded >= mx, evidence=ev)


_reasons = _reconcile_report(GradeReport(
    total_score=0, max_total=0, overall_remarks="x", questions=[
        QuestionMark(qid="Q3", score=0, max_score=0, page=1, y_fraction=0.2, remark="",
                     criteria=[_c("counting to twelve", 1, 1),
                               _c("significance of silence", 0.5, 1,
                                  "only half the idea given"),
                               _c("global brotherhood", 0, 1)]),
        QuestionMark(qid="Q4", score=0, max_score=0, page=1, y_fraction=0.2,
                     remark="Q4(b) too brief", criteria=[_c("part b", 0, 1)]),
        QuestionMark(qid="Q5", score=0, max_score=0, page=1, y_fraction=0.2, remark="",
                     criteria=[_c("all points", 2, 2)]),
        QuestionMark(qid="Q6", score=0, max_score=0, page=1, y_fraction=0.2, remark="",
                     criteria=[_c("working note", 0, 2)]),
    ]), None, None)
_by = {q.qid: q for q in _reasons.questions}
check("a lost-marks question with no remark gets one from its own step judgement",
      bool(_by["Q3"].remark), repr(_by["Q3"].remark))
check("it prefers the evidence the model quoted",
      "half the idea" in _by["Q3"].remark.lower(), repr(_by["Q3"].remark))
check("an existing remark is left alone", _by["Q4"].remark == "Q4(b) too brief",
      repr(_by["Q4"].remark))
check("full marks still get no remark", _by["Q5"].remark == "", repr(_by["Q5"].remark))
check("with no evidence it falls back to the rubric label",
      "working note" in _by["Q6"].remark.lower(), repr(_by["Q6"].remark))
check("the derived reason is cleaned like any other remark",
      ";" not in _by["Q3"].remark and " - " not in _by["Q3"].remark)

print("== gap 2: struck-out working is DETECTED, not just discouraged ==")
import io as _io
from PIL import Image as _Image, ImageDraw as _ImageDraw
from mathpix import detect_struck_lines, mark_struck_lines

_W, _H = 600, 300
_img = _Image.new("L", (_W, _H), 255)
_d = _ImageDraw.Draw(_img)
_lines = []
for _i, (_blobs, _strike, _under, _rule, _dense) in enumerate([
        ([70, 50, 90, 60], False, False, False, False),   # normal writing
        ([70, 50, 90, 60], True, False, False, False),    # STRUCK THROUGH
        ([70, 50, 90, 60], False, True, False, False),    # underlined
        ([70, 50, 90, 60], False, False, True, False),    # notebook ruling below
        ([], False, False, False, True)]):                # dense block of ink
    _y = 20 + _i * 60
    if _dense:
        _d.rectangle([60, _y + 6, 520, _y + 34], fill=90)
    else:
        _x = 60
        for _w in _blobs:
            _d.rectangle([_x, _y + 8, _x + _w, _y + 30], fill=40)
            _x += _w + 12
    if _strike:
        _d.line([62, _y + 20, 515, _y + 20], fill=20, width=3)
    if _under:
        _d.line([62, _y + 34, 515, _y + 34], fill=20, width=3)
    if _rule:
        _d.line([0, _y + 38, _W, _y + 38], fill=150, width=2)
    _lines.append(LineBox(f"P1L{_i}", "t", (55.0, float(_y), 525.0, float(_y + 40)), 1))
_buf = _io.BytesIO()
_img.save(_buf, "PNG")
_struck = detect_struck_lines(_buf.getvalue(), _lines)
check("the struck line is detected", "P1L1" in _struck, sorted(_struck))
check("ordinary writing is not", "P1L0" not in _struck)
check("an UNDERLINE is not mistaken for a strike", "P1L2" not in _struck)
check("notebook ruling is not mistaken for a strike", "P1L3" not in _struck)
check("a dense block of ink is not mistaken for a strike", "P1L4" not in _struck)
check("exactly one line is flagged", _struck == {"P1L1"}, sorted(_struck))

_pg = PageOCR(page=1, image_width=_W, image_height=_H, lines=_lines)
check("mark_struck_lines flags in place", mark_struck_lines(_pg, _buf.getvalue()) == 1)
check("the flag lands on the right line",
      [l.line_id for l in _pg.lines if l.cancelled] == ["P1L1"])
check("cancelled survives the proxy round-trip",
      pageocr_from_dict(pageocr_to_dict(_pg)).lines[1].cancelled is True)
check("the transcript tells the model it was crossed out",
      "CANCELLED" in build_transcript([_pg]))

# A phone photo of a ruled notebook, which is what students actually hand in. The
# case above kept its ruling OUT of the searched band and painted it grey 150 — above
# the ink threshold — so it could not fail. On a real 150 dpi photo the rules measure
# 29-101 grey and a generous Mathpix bbox puts the next rule right through the middle
# of the band. That flagged 64 of 125 lines on an 8-page CBSE Chemistry sheet, and the
# grader reported the (live) answer to Q20.ii.c as "cancelled, no surviving attempt".
print("== gap 2b: a ruled notebook page is not read as 64 cancellations ==")
_RW, _RH, _PITCH = 900, 620, 60
_rimg = _Image.new("L", (_RW, _RH), 245)
_rd = _ImageDraw.Draw(_rimg)
_rlines = []
for _i in range(6):
    _top = 20 + _i * _PITCH
    # The writing sits ON the rule, and the bbox is taller than the writing, so the
    # baseline rule lands at 0.58 of the line height — inside the searched band.
    _rd.line([0, _top + 30, _RW, _top + 30], fill=70, width=2)
    _x = 70
    while _x < 830:                                  # cursive: many short strokes
        _wd = 10 + (_x * 7 + _i * 13) % 19
        _rd.rectangle([_x, _top + 8, _x + _wd, _top + 30], fill=45)
        _x += _wd + 14                               # gaps wider than the bridge
    _rlines.append(LineBox(f"P1L{_i}", "handwriting", (60.0, float(_top),
                                                       840.0, float(_top + 52)), 1))
_rbuf = _io.BytesIO(); _rimg.save(_rbuf, "PNG")
check("printed ruling through the band is not cancellation",
      detect_struck_lines(_rbuf.getvalue(), _rlines) == set(),
      sorted(detect_struck_lines(_rbuf.getvalue(), _rlines)))

# Same page, and now one line really is struck out: the pen crosses the middle of its
# own words, so it stops well short of the margins the ruling runs into.
_rd.line([75, 20 + 2 * _PITCH + 19, 760, 20 + 2 * _PITCH + 19], fill=25, width=4)
_rbuf2 = _io.BytesIO(); _rimg.save(_rbuf2, "PNG")
check("a real strike on that same ruled page is still caught",
      detect_struck_lines(_rbuf2.getvalue(), _rlines) == {"P1L2"},
      sorted(detect_struck_lines(_rbuf2.getvalue(), _rlines)))

print("== gap 2: a struck line is never ticked ==")
def _ruled_sheet():
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    for i in range(9):
        pg.draw_line(fitz.Point(70, 120 + i * 70), fitz.Point(520, 120 + i * 70),
                     color=(0.85, 0.85, 0.9), width=0.5)
    return doc.tobytes()


def _ocr4(cancel):
    return [PageOCR(page=1, image_width=595, image_height=842, lines=[
        LineBox(f"P1L{i}", f"To Salary {i} 4,000",
                (80.0, 100.0 + i * 70, 500.0, 150.0 + i * 70), 1,
                cancelled=(i in cancel)) for i in range(4)])]


_rep = GradeReport(total_score=4, max_total=4, overall_remarks="x", questions=[
    QuestionMark(qid="Q1", score=4, max_score=4, page=1, y_fraction=0.1,
                 anchor_line_id="P1L0", mark_style="per_step",
                 criteria=[Criterion(description=f"step{i}", awarded=1, max=1, met=True,
                                     evidence="", step_line_id=f"P1L{i}")
                           for i in range(4)])])
_counts = {}
for _label, _cancel in (("live", set()), ("struck", {1, 2})):
    _t = []
    _real = pdf_renderer._draw_tick
    pdf_renderer._draw_tick = lambda *a, **k: _t.append(1)
    try:
        build_evaluated_pdf(student_pdf_bytes=_ruled_sheet(), student_filename="s.pdf",
                            student_pngs=[], report=_rep, ocr_pages=_ocr4(_cancel),
                            mark_scale=1.0)
    finally:
        pdf_renderer._draw_tick = _real
    _counts[_label] = len(_t)
# The strip no longer adds a verdict tick of its own when the page already carries
# one, so these are exactly the inline ticks. The delta of two is the point: it is
# what proves the cancelled working lines are not ticked on the student's page.
check("every live working line is ticked", _counts["live"] == 4, _counts)
check("and the two struck lines are not", _counts["struck"] == 2, _counts)
check("striking out working removes exactly those two ticks",
      _counts["live"] - _counts["struck"] == 2, _counts)

print("== gap 2: the prompt tells the model to ignore cancelled working ==")
prompt = build_system_prompt("Accountancy", "Class 12", "school")
check("a CANCELLED / STRUCK-OUT WORK section exists",
      "CANCELLED / STRUCK-OUT WORK" in prompt)
check("it says to judge from the page image, not the transcript",
      "PAGE IMAGE" in prompt and "transcript" in prompt)
check("it forbids anchoring to a cancelled line",
      "never point `step_line_id`" in prompt or "never point" in prompt)
check("only-cancelled work counts as unattempted", "attempted: false" in prompt)
check("the moderator is warned too, or it would restore the marks",
      "struck-through" in AUDIT_SYSTEM_PROMPT and "Cancelled" in AUDIT_SYSTEM_PROMPT)


# Inline annotations are written during grading and never revisited, so they outlive
# the decisions that made them. On a CA Inter copy the moderator restored Q3 to 10/10
# after disproving "used 1,73,000 instead of 1,69,000" — and the underline the first
# examiner had drawn under "173000-4000" stayed on the page, marking a figure as wrong
# on an answer that scored full marks. Every wrong MCQ was also crossed twice: once as
# the objective verdict beside the option, once inline through the words.
print("== gap 5: an inline mark the final score contradicts is dropped ==")
from grader import Annotation, prune_contradicted_annotations

_ann_rep = GradeReport(
    total_score=12, max_total=14, overall_remarks="x",
    questions=[
        QuestionMark(qid="Q3", score=10, max_score=10, page=4, y_fraction=0.3,
                     anchor_line_id="P4L25", mark_style="per_step",
                     criteria=[Criterion(description="cost of sale", awarded=10, max=10,
                                         met=True, evidence="", step_line_id="P4L25")]),
        QuestionMark(qid="Q1.ii", score=0, max_score=2, page=7, y_fraction=0.8,
                     anchor_line_id="P7L25", objective=True, chosen_option="B",
                     correct_option="A", attempted=True),
        QuestionMark(qid="Q2", score=2, max_score=4, page=2, y_fraction=0.4,
                     anchor_line_id="P2L3", mark_style="per_step",
                     criteria=[Criterion(description="format", awarded=2, max=4,
                                         met=False, evidence="", step_line_id="P2L3")]),
    ],
    annotations=[
        Annotation(type="underline", page=4, target_line_id="P4L25",
                   target_word="173000-4000"),
        Annotation(type="cross", page=7, target_line_id="P7L25",
                   target_word="Investing activities (B)"),
        Annotation(type="strikethrough", page=2, target_line_id="P2L3",
                   target_word="wrong heading"),
    ])
_dropped = prune_contradicted_annotations(_ann_rep)
_left = {a.target_line_id for a in (_ann_rep.annotations or [])}
check("two annotations are dropped", len(_dropped) == 2, f"{_dropped}")
check("the mark on a full-marks answer goes", "P4L25" not in _left, f"{_left}")
check("the duplicate cross on an MCQ goes", "P7L25" not in _left, f"{_left}")
check("a mark on an answer that DID lose marks stays", "P2L3" in _left, f"{_left}")
check("a report with no annotations is left alone",
      prune_contradicted_annotations(GradeReport(
          total_score=1, max_total=1, overall_remarks="x", questions=[])) == [])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
