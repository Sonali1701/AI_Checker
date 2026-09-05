# -*- coding: utf-8 -*-
"""Regressions from the Class 11 Economics copy reviewed on 2026-09-02."""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from exam_package import (build_exam_package, _declared_objective_numbers,
                          _official_objective_answers)
from grader import (AuditItem, Criterion, GradeReport, MarksItem, MarksScheme,
                    ObjectiveChoiceObservation,
                    QuestionMark, apply_audit, audit_completion_errors,
                    build_audit_prompt, finalise_student_remarks,
                    pdf_text_layer)
from mathpix import LineBox, PageOCR
from pdf_renderer import build_evaluated_pdf
from preflight import check_copy
from selective_verification import verification_candidates
from selective_verification import visual_evidence_qids
from remark_quality import report_remark_problems
from strict_grading import (apply_official_objective_evidence,
                            reconcile_report_to_package)

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def question(qid, score, maximum, **extra):
    return QuestionMark(qid=qid, score=score, max_score=maximum, page=1,
                        y_fraction=0.2, **extra)


print("== official paper text establishes the Economics MCQ contract ==")
paper = "Questions 1 to 13 are Multiple Choice Questions (MCQs) carrying 1 mark each."
key = "\n".join(f"{n}.\n({v})" for n, v in enumerate(
    [3, 1, 1, 3, 3, 4, 3, 1, 4, 1, 2, 4, 2], start=1))
check("only Q1-Q13 are declared objective",
      _declared_objective_numbers(paper) == set(range(1, 14)))
core_instruction = (
    "In Section A, Questions no. 1 to 8 are multiple choice questions (MCQs) "
    "each 1 mark."
)
check("'Questions no.' also declares the objective range",
      _declared_objective_numbers(core_instruction) == set(range(1, 9)))
answers = _official_objective_answers(key, paper)
check("numeric official options become A-D", answers[6] == "D" and answers[13] == "B")
repeated_key = "1.\n(D)\n\nWorked solution\n1.\n(A)"
check("later solution text cannot overwrite the explicit answer key",
      _official_objective_answers(repeated_key, "Questions 1 to 1 are MCQs.")[1] == "D")
visual_report_stub = GradeReport(total_score=5, max_total=5, overall_remarks="", questions=[
    question("Q10", 2, 2), question("Q13.i", 0.5, 0.5), question("Q14", 2.5, 2.5),
])
visual_qids = visual_evidence_qids(
    "10.\nDraw the Venn diagram for A and B.\n13.\nDraw appropriate Venn diagrams.\n"
    "14.\nFind the following values.", visual_report_stub)
check("drawing questions are mandatory visual-verification rows",
      visual_qids == ["Q10", "Q13.i"])

print("== two-column official keys retain question-local answer ownership ==")
column_pdf = fitz.open()
column_page = column_pdf.new_page(width=600, height=800)
column_page.insert_textbox(
    fitz.Rect(30, 80, 280, 500),
    "Q5\nText Solution:\nCorrect Answer: (B) Partner by estoppel\nExplanation.",
    fontsize=11,
)
column_page.insert_textbox(
    fitz.Rect(320, 80, 570, 500),
    "Q6\nText Solution:\nCorrect Answer: B. Karta\nExplanation.",
    fontsize=11,
)
column_key = pdf_text_layer(column_pdf.tobytes(), "official-key.pdf")
column_answers = _official_objective_answers(
    column_key,
    "Section A contains 6 Multiple Choice Questions. "
    + " ".join(f"Q{i}" for i in range(1, 7)),
)
check("Q5 answer remains inside Q5 block", column_answers.get(5) == "B")
check("Q6 dotted option format is accepted", column_answers.get(6) == "B")
column_pdf.close()

scheme = MarksScheme(total=2, items=[MarksItem(qid="Q1", max=1),
                                    MarksItem(qid="Q2", max=1)])
package = build_exam_package(
    exam_type="school", student_class="11", subject="Economics", level="11",
    paper="", attempt="", applicable_law_date=None, total_marks=2,
    question_paper=b"paper", answer_key=b"key", key_source="upload",
    marks_scheme=scheme, scheme_source="text",
    question_paper_text="Questions 1 to 2 are Multiple Choice Questions (MCQs).",
    answer_key_text="1.\n(3)\n2.\n(1)",
)
report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
    question("Q1", 0, 1, anchor_line_id="P1L2"),
    question("Q2", 0, 1, anchor_line_id="P1L1"),
])
ocr = [PageOCR(page=1, image_width=300, image_height=400, lines=[
    LineBox("P1L1", "1) (3) cardinal utility", (30, 50, 220, 70), 1),
    LineBox("P1L2", "2) (1) assertion and reason", (30, 90, 240, 110), 1),
])]
report = reconcile_report_to_package(report, package)
apply_official_objective_evidence(report, package, ocr)
check("Q1 binds only to Q1's own answer row", report.questions[0].anchor_line_id == "P1L1")
check("Q2 binds only to Q2's own answer row", report.questions[1].anchor_line_id == "P1L2")
check("official answers score both rows deterministically",
      [q.score for q in report.questions] == [1.0, 1.0])

print("== written option value outranks a conflicting option glyph ==")
value_paper = """Questions 1 to 2 are Multiple Choice Questions (MCQs).
1.
According to the cardinal utility approach, utility is:
(A) Measured only in rankings
(B) Measured in monetary terms
(C) Measured in numerical units called utils
(D) Not measurable at all
2.
Choose the true statement.
(A) True statement
(B) False statement
"""
value_report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
    question("Q1", 0, 1, anchor_line_id="P1L1"),
    question("Q2", 0, 1, anchor_line_id="P1L2"),
])
value_ocr = [PageOCR(page=1, image_width=300, image_height=400, lines=[
    LineBox("P1L1", "1.) (A) Measured in numerical units called util.",
            (30, 50, 270, 70), 1),
    LineBox("P1L2", "2.) (A) True statement", (30, 90, 220, 110), 1),
])]
value_report = reconcile_report_to_package(value_report, package)
value_notes = apply_official_objective_evidence(
    value_report, package, value_ocr, question_paper_text=value_paper)
check("Economics Q1 wording restores the mark despite glyph A",
      value_report.questions[0].score == 1.0
      and value_report.questions[0].chosen_option == "C", value_notes)
check("a matching official glyph remains correct",
      value_report.questions[1].score == 1.0, value_notes)

print("== OCR-silent checkbox rows require two visual reads ==")
visual_report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
    question("Q1", 0, 1, chosen_option="B"),
    question("Q2", 0, 1, chosen_option="A"),
])
visual_report = reconcile_report_to_package(visual_report, package)
observations = {
    "q1": ObjectiveChoiceObservation(qid="Q1", chosen_option="B", clear=True,
                                      page=1, y_fraction=0.2),
    "q2": ObjectiveChoiceObservation(qid="Q2", chosen_option="A", clear=True,
                                      page=1, y_fraction=0.3),
}
apply_official_objective_evidence(
    visual_report, package, [PageOCR(page=1, image_width=300, image_height=400,
                                    lines=[])], visual_choices=observations)
check("matching primary and independent reads are accepted",
      [q.score for q in visual_report.questions] == [0.0, 1.0])
check("visual ownership is recorded for the release gate",
      set(visual_report.objective_visual_verified_qids) == {"Q1", "Q2"})
noisy_report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
    question("Q1", 0, 1, chosen_option="B", anchor_line_id="P1L1"),
    question("Q2", 0, 1, chosen_option="A", anchor_line_id="P1L2"),
])
noisy_report = reconcile_report_to_package(noisy_report, package)
apply_official_objective_evidence(
    noisy_report, package,
    [PageOCR(page=1, image_width=300, image_height=400, lines=[
        LineBox("P1L1", "1) (D) noisy OCR", (30, 50, 220, 70), 1),
        LineBox("P1L2", "2) (C) noisy OCR", (30, 90, 220, 110), 1),
    ])], visual_choices=observations)
check("two-reader visual consensus overrides a conflicting OCR option",
      [q.chosen_option for q in noisy_report.questions] == ["B", "A"])
visual_issues = check_copy(
    visual_report, total_marks=2,
    ocr_pages=[PageOCR(page=1, image_width=300,
                       image_height=400, lines=[])])
check("preflight accepts the two-provider visual certificate",
      not any(issue.code == "objective-anchor-mismatch" for issue in visual_issues))
latex_report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
    question("Q1", 0, 1, anchor_line_id="P1L1"),
    question("Q2", 0, 1, anchor_line_id="P1L2"),
])
latex_ocr = [PageOCR(page=1, image_width=300, image_height=400, lines=[
    LineBox("P1L1", "1.) (3) cardinal utility", (30, 50, 220, 70), 1),
    LineBox("P1L2", r"2.) ( \( A \) ) \( A \) and \( R \) both true",
            (30, 90, 240, 110), 1),
])]
latex_report = reconcile_report_to_package(latex_report, package)
apply_official_objective_evidence(latex_report, package, latex_ocr)
check("Mathpix nested LaTeX delimiters still yield the owned option",
      latex_report.questions[1].chosen_option == "A")
anchor_codes = {i.code for i in check_copy(report, ocr_pages=ocr, total_marks=2)}
check("release gate accepts independently owned objective anchors",
      "objective-anchor-mismatch" not in anchor_codes, anchor_codes)

report.questions[0].anchor_line_id = "P1L2"
bad_anchor_codes = {i.code for i in check_copy(report, ocr_pages=ocr, total_marks=2)}
check("release gate blocks a verdict attached to its neighbour",
      "objective-anchor-mismatch" in bad_anchor_codes, bad_anchor_codes)

print("== sequence-owned OCR digit repairs remain objective-safe ==")
from answer_map import build_answer_map
sequence_scheme = MarksScheme(total=8, items=[
    MarksItem(qid=f"Q{i}", max=1) for i in range(1, 9)
])
sequence_package = build_exam_package(
    exam_type="school", student_class="11", subject="Economics", level="11",
    paper="", attempt="", applicable_law_date=None, total_marks=8,
    question_paper=b"sequence-paper", answer_key=b"sequence-key",
    key_source="upload", marks_scheme=sequence_scheme, scheme_source="text",
    question_paper_text="Questions 1 to 8 are Multiple Choice Questions (MCQs).",
    answer_key_text="\n".join(f"{i}. ({(i - 1) % 4 + 1})" for i in range(1, 9)),
)
seen = [1, 2, 3, 4, 5, 6, 1, 8]
sequence_ocr = [PageOCR(page=1, image_width=500, image_height=900, lines=[
    LineBox(f"P1L{i}", f"{label}.) ({(i - 1) % 4 + 1}) answer",
            (20, i * 45, 320, i * 45 + 25), 1)
    for i, label in enumerate(seen, start=1)
])]
sequence_map = build_answer_map(sequence_package, sequence_ocr)
sequence_report = GradeReport(total_score=0, max_total=8, overall_remarks="", questions=[
    question(f"Q{i}", 0, 1, anchor_line_id=("P1L1" if i == 7 else f"P1L{i}"))
    for i in range(1, 9)
])
sequence_report = reconcile_report_to_package(sequence_report, sequence_package)
apply_official_objective_evidence(
    sequence_report, sequence_package, sequence_ocr, answer_map=sequence_map)
check("a bracketed duplicate digit binds Q7 to its sequence-owned line",
      sequence_report.questions[6].anchor_line_id == "P1L7")
sequence_codes = {i.code for i in check_copy(
    sequence_report, ocr_pages=sequence_ocr, answer_map=sequence_map, total_marks=8)}
check("preflight accepts only the sequence map's repaired Q7 ownership",
      "objective-anchor-mismatch" not in sequence_codes, sequence_codes)

confusable_scheme = MarksScheme(total=10, items=[
    MarksItem(qid=f"Q{i}", max=1) for i in range(1, 11)
])
confusable_package = build_exam_package(
    exam_type="school", student_class="11", subject="Economics", level="11",
    paper="", attempt="", applicable_law_date=None, total_marks=10,
    question_paper=b"confusable-paper", answer_key=b"confusable-key",
    key_source="upload", marks_scheme=confusable_scheme, scheme_source="text",
    question_paper_text="Questions 1 to 10 are Multiple Choice Questions (MCQs).",
    answer_key_text="\n".join(f"{i}. (4)" for i in range(1, 11)),
)
confusable_ocr = [PageOCR(page=1, image_width=500, image_height=900, lines=[
    LineBox(f"P1L{i}", f"{i}.) ({'H' if i == 9 else '1'}) answer",
            (20, i * 40, 320, i * 40 + 22), 1)
    for i in range(1, 11)
])]
confusable_map = build_answer_map(confusable_package, confusable_ocr)
confusable_report = GradeReport(
    total_score=0, max_total=10, overall_remarks="", questions=[
        question(f"Q{i}", 0, 1, anchor_line_id=f"P1L{i}") for i in range(1, 11)
    ])
confusable_report = reconcile_report_to_package(confusable_report, confusable_package)
apply_official_objective_evidence(
    confusable_report, confusable_package, confusable_ocr,
    answer_map=confusable_map)
check("owned numeric neighbours resolve Mathpix H as handwritten option 4",
      confusable_report.questions[8].chosen_option == "D")

print("== provisional rubric points are resolved, not shown to students ==")
provisional = GradeReport(total_score=5, max_total=6,
                          overall_remarks="Review the equilibrium condition.", questions=[
    question("Q16", 5, 6, remark="Friend's answer is wrong", criteria=[
        Criterion(description="Equilibrium combination", awarded=2, max=2,
                  met=True, evidence="", step_line_id="P1L1"),
        Criterion(description="Income verification", awarded=1.5, max=2,
                  met=False, evidence="calculation is incomplete", step_line_id="P1L2"),
        Criterion(description="Unallocated official rubric point", awarded=1.5, max=2,
                  met=False, evidence="Primary breakdown omitted this official point",
                  step_line_id="P1L2"),
    ])
])
candidates = verification_candidates(provisional)
check("a provisional point is mandatory verification",
      candidates and candidates[0].mandatory,
      [(c.qid, c.reasons, c.mandatory) for c in candidates])
prompt = build_audit_prompt(
    provisional, answer_key_text="Both conditions of equilibrium must hold.",
    ocr_transcript="3 coffee and 2 snacks; Rs 140 spent.",
    question_paper_text="Is her friend correct? Give reason.")
check("moderator sees the actual question", "=== QUESTION PAPER" in prompt)
check("moderator is told solution examples may be alternatives",
      "listed in a model answer are alternatives" in prompt)
resolved, _ = apply_audit(provisional, [
    AuditItem(row=0, correct=2, reason="The equilibrium combination is correct"),
    AuditItem(row=1, correct=2, reason="The full Rs 140 expenditure is shown"),
    AuditItem(row=2, correct=2,
              reason="The friend is rejected because income remains unspent"),
], include_awards=True, selected_qids={"16"})
check("provisional label is replaced even when a ruling keeps its marks",
      all(c.description != "Unallocated official rubric point"
          for c in resolved.questions[0].criteria))
check("full verified answer has no contradictory remark",
      resolved.questions[0].score == 6 and resolved.questions[0].remark == "")

print("== generic feedback triggers re-marking and can never be synthesised ==")
bad_feedback = GradeReport(total_score=3.5, max_total=4, overall_remarks="", questions=[
    question("Q8", 3.5, 4,
             remark="ROC checks and approves if not identical needs correction",
             criteria=[Criterion(
                 description="Explain reservation and incorporation linkage",
                 awarded=3.5, max=4, met=False,
                 evidence="Reservation period after approval is not stated",
                 step_line_id="P1L1")]),
])
feedback_candidates = verification_candidates(bad_feedback)
check("template feedback is a mandatory independent re-mark",
      feedback_candidates and feedback_candidates[0].mandatory
      and "deduction-remark-generic" in feedback_candidates[0].reasons,
      [(c.qid, c.reasons, c.mandatory) for c in feedback_candidates])
generic_verdict = [AuditItem(row=0, correct=3.5, qid="Q8", criterion=0,
                             reason="Explanation needs correction")]
specific_verdict = [AuditItem(row=0, correct=3.5, qid="Q8", criterion=0,
                              reason="State the reservation period after name approval")]
check("a verifier cannot return another generic reason",
      bool(audit_completion_errors(
          bad_feedback, generic_verdict, include_awards=False,
          selected_qids={"8"})))
check("a concise concrete correction completes verification",
      not audit_completion_errors(
          bad_feedback, specific_verdict, include_awards=False,
          selected_qids={"8"}))
overlong_feedback = GradeReport(total_score=1.5, max_total=2, overall_remarks="", questions=[
    question("Q12", 1.5, 2,
             remark="Use interval notation because the complete real solution set must include every value strictly greater than minus two",
             criteria=[Criterion(description="Real number solutions", awarded=1.5,
                                 max=2, met=False, evidence="Use (-2, infinity)",
                                 step_line_id="P1L1")]),
])
finalise_student_remarks(overlong_feedback)
check("finalisation never invents a 'needs correction' fallback",
      "needs correction" not in overlong_feedback.questions[0].remark.lower(),
      overlong_feedback.questions[0].remark)
check("unresolved overlong feedback remains release-blocking",
      bool(report_remark_problems(overlong_feedback)),
      report_remark_problems(overlong_feedback))

print("== rendered scores stay out of the student's answer area ==")
doc = fitz.open()
page = doc.new_page(width=300, height=400)
for y in (80, 120, 160):
    page.draw_line(fitz.Point(20, y), fitz.Point(280, y), color=(0.75, 0.75, 0.8))
student_pdf = doc.tobytes()
doc.close()
render_report = GradeReport(total_score=3, max_total=4,
                            overall_remarks="Revise the distinction between the variables.", questions=[
    question("Q1", 1, 1, objective=True, is_correct=True, chosen_option="C",
             correct_option="C", anchor_line_id="P1L1"),
    question("Q2", 0, 1, objective=True, is_correct=False, chosen_option="B",
             correct_option="A", anchor_line_id="P1L2", remark="Ans: A"),
    # Real Business Studies Q5: the visual reader saw an attempted wrong option,
    # but Mathpix supplied no dependable line anchor.  It must still receive a
    # cross and QID-labelled 0/1 entry in the white marks strip.
    question("Q5", 0, 1, objective=True, attempted=True, is_correct=False,
             chosen_option="C", correct_option="B", anchor_line_id="",
             remark="Ans: B"),
    question("Q15", 2, 2, anchor_line_id="P1L3", mark_style="single", criteria=[
        Criterion(description="Variable distinction", awarded=2, max=2, met=True,
                  evidence="", step_line_id="P1L3")]),
])
render_ocr = [PageOCR(page=1, image_width=300, image_height=400, lines=[
    LineBox("P1L1", "1) (3)", (30, 65, 100, 85), 1),
    LineBox("P1L2", "2) (2)", (30, 105, 100, 125), 1),
    LineBox("P1L3", "30-40 is continuous", (30, 145, 210, 165), 1),
])]
rendered = build_evaluated_pdf(student_pdf, "student.pdf", [], render_report, render_ocr,
                               safe_layout=True)
out = fitz.open(stream=rendered, filetype="pdf")
sheet_top = out[0].rect.height - 400
inline_red_numbers = []
margin_qids = []
for block in out[0].get_text("dict")["blocks"]:
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "").strip()
            x0, y0, x1, y1 = span["bbox"]
            color = int(span.get("color", 0))
            red = ((color >> 16) & 255) > ((color >> 8) & 255) + 55
            if red and y0 >= sheet_top and x1 < 300 and re.fullmatch(r"\d+(?:\.\d+)?", text):
                inline_red_numbers.append((text, span["bbox"]))
            if y0 >= sheet_top and x0 > 300 and text.startswith(("Q1", "Q2", "Q5", "Q15")):
                margin_qids.append(text)
content_red_drawings = []
for drawing in out[0].get_drawings():
    rect = drawing.get("rect")
    color = drawing.get("color") or drawing.get("fill")
    if not rect or not color or rect.y0 <= sheet_top + 1.0:
        continue
    red = color[0] > color[1] + 0.2 and color[0] > color[2] + 0.2
    # x=300 is the strip divider and is allowed; no generated red path may enter
    # the original 300-point-wide student page.
    if red and rect.x0 < 299.5:
        content_red_drawings.append(tuple(round(v, 2) for v in rect))
out.close()
check("no circled score obscures an answer", not inline_red_numbers, inline_red_numbers)
check("every visible verdict remains QID-labelled in the margin",
      any(x.startswith("Q1") for x in margin_qids)
      and any(x.startswith("Q2") for x in margin_qids)
      and any(x.startswith("Q5") for x in margin_qids)
      and any(x.startswith("Q15") for x in margin_qids), margin_qids)
check("attempted wrong anchorless MCQ keeps its strip cross and score",
      any(x.startswith("Q5") and "0/1" in x for x in margin_qids), margin_qids)
check("released-page red marks never enter the handwriting area",
      not content_red_drawings, content_red_drawings)

print("== a student release marks the answer, and repairs only if that collides ==")
from types import SimpleNamespace
import pipeline
import preflight

repair_report = GradeReport(total_score=1, max_total=1, overall_remarks="", questions=[
    question("Q1", 1, 1),
])
originals = (pipeline.pdf_or_image_to_pngs, pipeline.grade_prepared,
             pipeline.build_evaluated_pdf, preflight.check_copy)
render_modes = []
try:
    pipeline.pdf_or_image_to_pngs = lambda *args, **kwargs: [b"png"]
    pipeline.grade_prepared = lambda **kwargs: {
        "report": repair_report, "ocr_pages": [], "cost": {},
        "budget": {"cap_inr": 15}, "verification_complete": True,
        "verification_qids": [], "answer_map": None,
    }

    def fake_render(**kwargs):
        safe = bool(kwargs.get("safe_layout"))
        render_modes.append(safe)
        return b"safe" if safe else b"normal"

    pipeline.build_evaluated_pdf = fake_render
    preflight.check_copy = lambda report, **kwargs: (
        [] if kwargs.get("pdf_bytes") == b"safe" else
        [preflight.Issue("block", "mark-overlaps-writing", "synthetic collision")]
    )
    result = pipeline.grade_sheet(
        sa_bytes=b"student", filename="student.pdf", qp_pngs=[], ak_pngs=None,
        ak_text=None, marks_scheme=None,
        cfg=SimpleNamespace(is_strict=False, exam_type="school", exam_package=None),
        total_marks=1,
    )
finally:
    (pipeline.pdf_or_image_to_pngs, pipeline.grade_prepared,
     pipeline.build_evaluated_pdf, preflight.check_copy) = originals
# A teacher's tick belongs on the line that earned it, so the first render puts the
# marks on the sheet. Margin-only is the REPAIR, not the starting point: reaching for
# it first left thirteen ticks stacked down the margin of a released Economics copy
# and none on the student's writing.
check("the first render marks the answer", render_modes[:1] == [False], render_modes)
check("a real collision falls back to the conservative layout",
      render_modes == [False, True], render_modes)
check("and the copy still releases, repaired rather than refused",
      result["layout_repaired"] and result["release"]
      and result["pdf_bytes"] == b"safe", result)

# The ordinary case: nothing collides, so what the student gets back is the copy with
# the marks on their own answers - no repair pass, no margin-only fallback.
clean_modes = []
try:
    pipeline.pdf_or_image_to_pngs = lambda *args, **kwargs: [b"png"]
    pipeline.grade_prepared = lambda **kwargs: {
        "report": repair_report, "ocr_pages": [], "cost": {},
        "budget": {"cap_inr": 15}, "verification_complete": True,
        "verification_qids": [], "answer_map": None,
    }

    def clean_render(**kwargs):
        safe = bool(kwargs.get("safe_layout"))
        clean_modes.append(safe)
        return b"safe" if safe else b"normal"

    pipeline.build_evaluated_pdf = clean_render
    preflight.check_copy = lambda report, **kwargs: []
    clean = pipeline.grade_sheet(
        sa_bytes=b"student", filename="student.pdf", qp_pngs=[], ak_pngs=None,
        ak_text=None, marks_scheme=None,
        cfg=SimpleNamespace(is_strict=False, exam_type="school", exam_package=None),
        total_marks=1,
    )
finally:
    (pipeline.pdf_or_image_to_pngs, pipeline.grade_prepared,
     pipeline.build_evaluated_pdf, preflight.check_copy) = originals
check("a clean copy is rendered once, with the marks on the answer",
      clean_modes == [False], clean_modes)
check("and is released without a margin-only rewrite",
      not clean["layout_repaired"] and clean["release"]
      and clean["pdf_bytes"] == b"normal", clean)

if FAILS:
    print(f"\nFAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("\nALL CHECKS PASSED")
