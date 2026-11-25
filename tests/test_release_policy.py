# -*- coding: utf-8 -*-
"""What may hold a marked copy back, from the batch of 2026-09-16.

Six copies were graded and SIX were refused. Not one of them was refused over a
wrong mark:

  0cd64879  "Two visual readers disagree on Q3; no score was released."   One MCQ.
  2546c5cf  "Second examiner did not certify every requested decision: Q6.c:
            evidence anchor 'P1L14' is outside the active answer..."      The second
            examiner quoted the wrong OCR line id while re-checking marks that were
            already decided and evidenced.
  ad7bc5c9  "the mark '0.5' clips the student's writing (4% of the circle is their
            ink)"                                                          4%.
  d442ae51  "Q6.c remark is a fragment naming nothing: 'Wrote Piedmont. Key answer
            is Intermontane.'" (a perfectly good remark) and "page(s) [2] carry no
            marks".
  5d94afe5  "Every checking/award target needs a quote and an owned original OCR
            line ID."                                                      Ditto.
  39b55d4d  "'repairing_layout' is not a valid WorkflowState" - the pipeline
            reported a stage the enum did not have, the release gate caught the
            ValueError and reported it as a blocking DEFECT of the copy. The step
            that exists to repair a copy destroyed it.

POLICY: a copy is held only when its SCORE is wrong or unprovable. Presentation,
wording, and the second examiner's own bookkeeping travel WITH the copy as warnings
the teacher reads. A copy nobody receives teaches nobody anything.
"""
from __future__ import annotations

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from automatic_workflow import AutomaticWorkflow, WorkflowState  # noqa: E402
from preflight import Issue, advisory, release_blocking  # noqa: E402

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


print("== every stage the pipeline reports is a real workflow state ==")
source = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
reported = set(re.findall(r"stage(?:_callback)?\(\s*\"([a-z_]+)\"", source))
known = {state.value for state in WorkflowState}
check("pipeline reports only known stages", reported <= known, sorted(reported - known))
check("the layout-repair stage exists", "repairing_layout" in known)

flow = AutomaticWorkflow()
for state in (WorkflowState.PACKAGE_VALIDATING, WorkflowState.PACKAGE_VALIDATED,
              WorkflowState.NORMALIZING, WorkflowState.OCR,
              WorkflowState.ANSWER_MAPPING, WorkflowState.PRIMARY_GRADING,
              WorkflowState.VERIFYING, WorkflowState.AGGREGATING,
              WorkflowState.RENDERING, WorkflowState.RELEASE_CHECK):
    flow.transition(state)
try:
    flow.transition(WorkflowState.REPAIRING_LAYOUT, "margin-only re-render")
    flow.transition(WorkflowState.RELEASE_CHECK, "checking the repaired copy")
    flow.transition(WorkflowState.RELEASED)
    check("a copy may be re-rendered and released", flow.releasable)
except RuntimeError as exc:
    check("a copy may be re-rendered and released", False, str(exc))

print("== the progress display can never fail a copy ==")
# Round two of the same bug: the stage name was added to the enum, and stage_label -
# a second table, one layer down - still did not have it, so KeyError came through
# the progress callback and the gate reported it as a blocking defect of the copy.
from automatic_workflow import stage_label  # noqa: E402

for state in WorkflowState:
    try:
        label = stage_label(state)
        check(f"every state has a label: {state.value}", bool(label.strip()), repr(label))
    except Exception as exc:  # noqa: BLE001
        check(f"every state has a label: {state.value}", False, repr(exc))
check("an unknown state still returns a label instead of raising",
      stage_label("something_new") == "Something new", stage_label("something_new"))

server_source = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
check("the server uses the shared, un-failable reporter",
      "safe_stage_reporter(flow)" in server_source)

# Drive the REAL function the server runs, through the exact path that destroyed two
# batches: a stage announced during the release check, with a listener that throws.
from automatic_workflow import safe_stage_reporter  # noqa: E402

live = AutomaticWorkflow()
for state in (WorkflowState.PACKAGE_VALIDATING, WorkflowState.PACKAGE_VALIDATED,
              WorkflowState.NORMALIZING, WorkflowState.OCR,
              WorkflowState.ANSWER_MAPPING, WorkflowState.PRIMARY_GRADING,
              WorkflowState.VERIFYING, WorkflowState.AGGREGATING,
              WorkflowState.RENDERING, WorkflowState.RELEASE_CHECK):
    live.transition(state)
report_stage = safe_stage_reporter(live)
try:
    report_stage("repairing_layout", "margin-only re-render")
    check("the real reporter survives the layout-repair stage",
          live.state == WorkflowState.REPAIRING_LAYOUT, live.state)
    report_stage("not_a_stage", "junk from a future pipeline")
    check("...and an unknown stage", True)
    live.on_change = lambda snapshot: (_ for _ in ()).throw(KeyError("label"))
    report_stage("release_check", "back to the gate")
    check("...and a listener that raises (the KeyError that refused build39)", True)
except Exception as exc:  # noqa: BLE001
    check("the real reporter never raises", False, repr(exc))

# A crash inside the checker says nothing about the marks; it must not refuse a batch.
check("a gate crash warns instead of holding the copy",
      release_blocking([Issue("block", "preflight-error", "the gate itself failed")]) == [])

print("== quality gates after marking no longer refuse a copy ==")
pipeline_source = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
for code in ("REMARK_QUALITY_FAILED", "POST_VERIFICATION_MUTATION",
             "OBJECTIVE_VISUAL_VERIFICATION_INCOMPLETE"):
    check(f"{code} is no longer raised", f'"{code}"' not in pipeline_source)
for code in ("not-certified", "weak-feedback", "objective-unconfirmed"):
    check(f"...it is reported as {code} instead",
          f'"{code}"' in pipeline_source or f'"{code}"' in open(
              os.path.join(ROOT, "preflight.py"), encoding="utf-8").read())

print("== only a wrong or unprovable SCORE holds a copy ==")
live = [
    Issue("warn", "mark-touches-writing",
          "page 1: the mark '0.5' clips the student's writing (4% of the circle is their ink)"),
    Issue("warn", "remark-fragment",
          "Q6.c remark is a fragment naming nothing: 'Wrote Piedmont. Key answer is Intermontane.'"),
    Issue("warn", "unmarked-page",
          "page(s) [2] carry no marks although they sit inside the marked range"),
    Issue("block", "mark-overlaps-writing", "a mark sits on the student's writing"),
    Issue("block", "text-overflow", "a remark runs past the page edge"),
    Issue("block", "remark-wording", "the remark hedges"),
    Issue("warn", "not-certified", "the second examiner did not rule on Q7.c"),
]
check("none of the 2026-09-16 refusals holds a copy any more",
      release_blocking(live) == [], [i.code for i in release_blocking(live)])
check("...and every one of them still reaches the teacher",
      len(advisory(live)) == len(live))
for code in ("denominator", "total-mismatch", "over-award", "criteria-mismatch",
             "step-over-award", "duplicate-qid", "maxima-rescaled",
             "answer-not-read", "scheme-contradicts-paper", "met-mismatch",
             "objective-anchor-mismatch", "unattempted-answer-has-credit"):
    held = release_blocking([Issue("block", code, "detail")])
    check(f"a wrong score still holds: {code}", [i.code for i in held] == [code])
check("a score-integrity issue is not also reported as advisory",
      advisory([Issue("block", "denominator", "d")]) == [])

print("== one unreadable MCQ costs a note, not the copy ==")
import fitz  # noqa: E402
from exam_package import build_exam_package  # noqa: E402
from grader import GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402
from grader import ObjectiveChoiceObservation  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from strict_grading import (apply_official_objective_evidence,  # noqa: E402
                            reconcile_report_to_package)

PAPER = """Q1
Which ancient account written by Megasthenes describes the Mauryan Empire?
(A) Arthashastra
(B) Indika
(C) Mudrarakshasa
(D) Deepavamsa
Q2
The Sangam literature was composed in which language?
(A) Sanskrit
(B) Prakrit
(C) Tamil
(D) Pali
"""
scheme = MarksScheme(total=2, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=1)])
package = build_exam_package(
    exam_type="school", student_class="9", subject="SST", level="9", paper="",
    attempt="", applicable_law_date=None, total_marks=2, question_paper=b"p",
    answer_key=b"k", key_source="upload", marks_scheme=scheme, scheme_source="text",
    question_paper_text=PAPER, answer_key_text="Answer Key\nQ1\n(B)\nQ2\n(C)\n")


def sheet(primary, lines):
    report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
        QuestionMark(qid=f"Q{n}", score=0, max_score=1, page=1, y_fraction=0.2 * n,
                     anchor_line_id=f"P1L{n}", chosen_option=choice)
        for n, choice in zip((1, 2), primary)])
    report.evidence_contract = True
    pages = [PageOCR(page=1, image_width=800, image_height=1000, lines=[
        LineBox(f"P1L{i + 1}", text, (60, 40 + 40 * i, 700, 70 + 40 * i), 1)
        for i, text in enumerate(lines)])]
    return reconcile_report_to_package(report, package), pages


# 849bbe95 exactly: the page says "1) Indica (b)", the grading pass says B, and one
# key-blind reader answered A for every row.
report, pages = sheet(["B", "C"], ["1) Indica (b)", "2) Tamil (c)"])
readers = {"q1": ObjectiveChoiceObservation(qid="Q1", chosen_option="A", clear=True),
           "q2": ObjectiveChoiceObservation(qid="Q2", chosen_option="A", clear=True)}
notes = apply_official_objective_evidence(
    report, package, pages, visual_choices=readers, question_paper_text=PAPER,
    allow_unconfirmed=True)
check("the student's own answer line settles a reader disagreement",
      [q.chosen_option for q in report.questions] == ["B", "C"],
      [(q.qid, q.chosen_option) for q in report.questions])
check("...so the copy keeps its marks", report.total_score == 2.0, report.total_score)
check("...and both rows are named for the teacher",
      report.objective_unconfirmed_qids == ["Q1", "Q2"],
      report.objective_unconfirmed_qids)
check("...with a note saying what was used",
      any("please check this one" in note for note in notes), notes)

# Nothing readable on the page at all: the examiners' reading is used, never a
# silent zero, and the row is flagged.
# (a page line of "Section A" would be read as option A by the trailing-letter
# rule, so this fixture uses a line that names no option at all)
report, pages = sheet(["B", ""], ["Answers"])
notes = apply_official_objective_evidence(
    report, package, pages, visual_choices={
        "q2": ObjectiveChoiceObservation(qid="Q2", chosen_option="C", clear=True)},
    question_paper_text=PAPER, allow_unconfirmed=True)
check("an unreadable page falls back to the examiners' reading",
      [q.chosen_option for q in report.questions] == ["B", "C"],
      [(q.qid, q.chosen_option) for q in report.questions])
check("...and both rows are flagged as unconfirmed",
      report.objective_unconfirmed_qids == ["Q1", "Q2"],
      report.objective_unconfirmed_qids)

# Strictness that remains: with no official answer there is nothing to score against.
from strict_grading import StrictGradeError  # noqa: E402

no_key = build_exam_package(
    exam_type="school", student_class="9", subject="SST", level="9", paper="",
    attempt="", applicable_law_date=None, total_marks=2, question_paper=b"p",
    answer_key=b"k", key_source="upload", marks_scheme=scheme, scheme_source="text",
    question_paper_text=PAPER, answer_key_text="")
try:
    report, pages = sheet(["B", "C"], ["1) Indica (b)", "2) Tamil (c)"])
    for question in report.questions:
        question.objective = True
    apply_official_objective_evidence(report, no_key, pages,
                                      question_paper_text=PAPER, allow_unconfirmed=True)
    check("a missing official answer still stops the copy",
          not any(item.question_type == "objective" for item in no_key.questions),
          "package has objective rows but nothing was raised")
except StrictGradeError as exc:
    check("a missing official answer still stops the copy",
          exc.code == "OBJECTIVE_ANSWER_UNRESOLVED", exc.code)

print("== an OCR-garbled label does not lose a correctly scored MCQ ==")
# LIVE 2026-09-16: Mathpix read Q5's own answer line as "Qs - (25) 25 years" and Q3's
# as "Qs) Brodd Momtain". The ownership check wanted a line starting with that
# question's number, so two copies with correct MCQ marks were refused.
from preflight import check_copy  # noqa: E402

garbled, pages = sheet(["B", "C"], ["Qs) Indica (b)", "Qs - (c) Tamil"])
for question in garbled.questions:
    question.objective = True
codes = {issue.code for issue in check_copy(garbled, ocr_pages=pages, paper_text=PAPER)}
check("a line naming this question's own option owns it, whatever the label",
      "objective-anchor-mismatch" not in codes, sorted(codes))
elsewhere, other_pages = sheet(["B", "C"], ["Q9) something else entirely", "Q8) more prose"])
for question in elsewhere.questions:
    question.objective = True
codes = {issue.code for issue in check_copy(elsewhere, ocr_pages=other_pages, paper_text=PAPER)}
check("...but a line belonging to another question is still refused",
      "objective-anchor-mismatch" in codes, sorted(codes))

print("== a released copy carries its warnings to the results table ==")
check("the results row lists what to check",
      "point(s) to check" in server_source)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all checks passed")
