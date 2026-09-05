# -*- coding: utf-8 -*-
"""An over-summing criteria split is trimmed to fit, not used to refuse a copy.

The primary examiner sometimes splits a question into steps whose maxima add up to
more than the official maximum. Proportional rescaling stays refused - it would
invent a distribution nobody awarded - but so does throwing the copy away, because
the score was never the thing at risk: it is clamped to the official maximum either
way. Only the breakdown shown to the student was wrong, so the surplus comes off the
steps the student earned least on and every earned mark survives.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exam_package import build_exam_package
from grader import Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark
from strict_grading import StrictGradeError, reconcile_report_to_package

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def package(max_marks):
    scheme = MarksScheme(total=max_marks, items=[
        MarksItem(qid="Q9.i", max=max_marks, question_type="theory")])
    return build_exam_package(
        exam_type="school", student_class="Class 11 Commerce",
        subject="Business Studies", level="11", paper="Unit Test 1",
        attempt="2026", applicable_law_date=None, total_marks=max_marks,
        question_paper=b"paper", answer_key=b"key", key_source="upload",
        marks_scheme=scheme, scheme_source="text")


def report_with(criteria, score):
    return GradeReport(
        total_score=score, max_total=2, overall_remarks="", questions=[
            QuestionMark(qid="Q9.i", score=score, max_score=3, page=1,
                         y_fraction=0.3, objective=False, criteria=criteria,
                         remark="", attempted=True)])


print("== the surplus comes off the step the student earned least on ==")
# Steps total 3 against an official 2. The student earned 1 on the first step and
# nothing on the third, so the third is where the surplus belongs.
graded = reconcile_report_to_package(
    report_with([
        Criterion(description="names the source", awarded=1, max=1, met=True,
                  evidence="stated", step_line_id="P1L1"),
        Criterion(description="explains it", awarded=0.5, max=1, met=False,
                  evidence="partial", step_line_id="P1L2"),
        Criterion(description="gives an example", awarded=0, max=1, met=False,
                  evidence="", step_line_id="P1L3"),
    ], 1.5),
    package(2))
question = graded.questions[0]
maxima = [float(c.max) for c in question.criteria]
awarded = [float(c.awarded) for c in question.criteria]
check("the steps now sum to the official maximum", round(sum(maxima), 4) == 2.0, maxima)
check("no earned mark was taken away", awarded == [1.0, 0.5, 0.0], awarded)
check("the unearned step absorbed the cut", maxima[2] == 0.0, maxima)
check("the earned steps kept their maxima", maxima[0] == 1.0 and maxima[1] == 1.0, maxima)
check("the score is the official-capped total", float(question.score) == 1.5,
      question.score)

print()
print("== a step is never trimmed below what it actually paid out ==")
graded = reconcile_report_to_package(
    report_with([
        Criterion(description="a", awarded=0.5, max=1, met=False, evidence="x",
                  step_line_id="P1L1"),
        Criterion(description="b", awarded=0.5, max=1, met=False, evidence="x",
                  step_line_id="P1L2"),
        Criterion(description="c", awarded=0.5, max=1, met=False, evidence="x",
                  step_line_id="P1L3"),
    ], 1.5),
    package(2))
question = graded.questions[0]
for criterion in question.criteria:
    check(f"{criterion.description!r} still covers its award",
          float(criterion.max) >= float(criterion.awarded) - 1e-9,
          (criterion.max, criterion.awarded))
check("and the steps still sum to the official maximum",
      round(sum(float(c.max) for c in question.criteria), 4) == 2.0,
      [float(c.max) for c in question.criteria])

print()
print("== a genuine over-award is still held ==")
# 2.5 already awarded against an official maximum of 2: there is no surplus to trim
# that does not erase a mark, so this is a real fault and must not be papered over.
held = False
try:
    reconcile_report_to_package(
        report_with([
            Criterion(description="a", awarded=1, max=1, met=True, evidence="x",
                      step_line_id="P1L1"),
            Criterion(description="b", awarded=1, max=1, met=True, evidence="x",
                      step_line_id="P1L2"),
            Criterion(description="c", awarded=0.5, max=1, met=False, evidence="x",
                      step_line_id="P1L3"),
        ], 2.5),
        package(2))
except StrictGradeError as exc:
    held = exc.code == "CRITERION_MAXIMUM_MISMATCH"
    detail = str(exc)
check("an unfittable over-award raises", held)
check("and says the awarded marks are what blocks it",
      held and "leave nothing to trim" in detail, locals().get("detail", ""))

print()
print("== a split that already fits is untouched ==")
graded = reconcile_report_to_package(
    report_with([
        Criterion(description="a", awarded=1, max=1, met=True, evidence="x",
                  step_line_id="P1L1"),
        Criterion(description="b", awarded=0.5, max=1, met=False, evidence="x",
                  step_line_id="P1L2"),
    ], 1.5),
    package(2))
question = graded.questions[0]
check("its maxima are unchanged",
      [float(c.max) for c in question.criteria] == [1.0, 1.0],
      [float(c.max) for c in question.criteria])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
