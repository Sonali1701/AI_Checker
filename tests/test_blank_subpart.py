# -*- coding: utf-8 -*-
"""A blank sub-part of an answered question is a zero, not a reason to bin the copy.

A CA Corporate Laws script came back `ungradable`: "The grader omitted package
question(s): Q1c, Q3b" - the two sub-parts the student left blank. Q1a, Q1b and
Q3a were all marked, correctly, and the objective section was complete. Nobody
reviews a held copy, so the student got nothing for a paper that was fully marked.
Three other runs of that same script released, which is the tell: whether the
grader troubles to emit a zero row for a blank sub-part is not stable, and a coin
flip was deciding whether a paper came back.

`find_unmarked_answers` had already settled what a missing row means, on this very
pair of questions - a sub-part whose sibling was attempted is explained by that
sibling's writing, and leaving some sub-parts blank is ordinary. This binds the
release gate to the same rule.

What must STILL hold the copy is pinned here too: a question with no attempted
sibling anywhere. That is the Class 12 Chemistry copy the gate exists for, where
twenty answered sub-parts were recorded as unattempted, and silently zeroing those
would hand back a wrong score with nothing to show it was wrong.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exam_package import build_exam_package  # noqa: E402
from grader import GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402
from strict_grading import (StrictGradeError,  # noqa: E402
                            reconcile_report_to_package)

FAILS = []

# The shape of the real paper: two descriptive questions of three and two parts.
PARTS = [("Q1.a", 5), ("Q1.b", 5), ("Q1.c", 5), ("Q3.a", 5), ("Q3.b", 5)]
TOTAL = 25


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def package():
    scheme = MarksScheme(total=TOTAL, items=[
        MarksItem(qid=qid, max=mx, question_type="law") for qid, mx in PARTS])
    return build_exam_package(
        exam_type="professional", student_class="CA Intermediate",
        subject="Corporate and Other Laws", level="inter", paper="Paper 1",
        attempt="2026", applicable_law_date=None, total_marks=TOTAL,
        question_paper=b"paper", answer_key=b"key", key_source="upload",
        marks_scheme=scheme, scheme_source="text")


def report(graded_qids):
    """A report carrying rows for only the sub-parts the grader bothered to emit."""
    rows = [QuestionMark(qid=qid, score=4.0, max_score=float(mx), page=2,
                         y_fraction=0.3, objective=False, remark="Good",
                         attempted=True)
            for qid, mx in PARTS if qid in graded_qids]
    return GradeReport(
        total_score=sum(q.score for q in rows),
        max_total=sum(q.max_score for q in rows),
        overall_remarks="", questions=rows)


print("== the copy that was thrown away ==")
# Exactly the live failure: every part marked except the two the student left blank.
graded = reconcile_report_to_package(
    report({"Q1.a", "Q1.b", "Q3.a"}), package(), answer_map=None)
booked = {q.qid: q for q in graded.questions}
check("the copy is released instead of held", True)
check("both blank sub-parts are on the copy",
      {"Q1.c", "Q3.b"} <= set(booked), sorted(booked))
check("each is scored zero of the mark the paper prints",
      all(booked[q].score == 0.0 and booked[q].max_score == 5.0
          for q in ("Q1.c", "Q3.b")))
check("and says so, so the student can dispute it",
      all(booked[q].remark == "Not attempted" for q in ("Q1.c", "Q3.b")))
check("marked as unattempted, not as a wrong answer",
      all(booked[q].attempted is False for q in ("Q1.c", "Q3.b")))
check("the denominator is the paper's official total",
      graded.max_total == TOTAL, graded.max_total)
check("the student keeps every mark they earned",
      graded.total_score == 12.0, graded.total_score)
check("a booked blank sits on its own question's page, not page 1",
      booked["Q1.c"].page == 2, booked["Q1.c"].page)

print()
print("== a whole question nobody graded still holds the copy ==")
# No row for any part of Q3: nothing explains that, and an answer the grader never
# read must not be quietly turned into a zero.
try:
    reconcile_report_to_package(
        report({"Q1.a", "Q1.b", "Q1.c"}), package(), answer_map=None)
    check("an ungraded question is refused", False, "it was released")
except StrictGradeError as exc:
    check("an ungraded question is refused", exc.code == "QUESTION_NOT_GRADED",
          exc.code)
    check("and the message names only the unexplained question",
          "Q3.a" in str(exc) and "Q3.b" in str(exc) and "Q1" not in str(exc),
          str(exc))

print()
print("== a graded-but-blank part is left exactly as the grader marked it ==")
# The grader DID emit a zero row for Q3.b. Nothing here should touch it.
rows = report({"Q1.a", "Q1.b", "Q1.c", "Q3.a"})
rows.questions.append(QuestionMark(
    qid="Q3.b", score=0.0, max_score=5.0, page=3, y_fraction=0.5,
    objective=False, remark="Cites the wrong section", attempted=True))
graded = reconcile_report_to_package(rows, package(), answer_map=None)
kept = {q.qid: q for q in graded.questions}["Q3.b"]
check("its own remark survives", kept.remark == "Cites the wrong section",
      kept.remark)
check("it is not relabelled unattempted", kept.attempted is True)
check("no duplicate row is added for it",
      sum(1 for q in graded.questions if q.qid == "Q3.b") == 1)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
