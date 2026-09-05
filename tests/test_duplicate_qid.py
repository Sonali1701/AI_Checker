# -*- coding: utf-8 -*-
"""A repeated qid must not cost the student the answer they wrote.

From a real English copy (12th Parishram Subjective Test 07): the paper's Q1 and
Q2 are both letters, each with an OR. The student answered one option of each.
The grader labelled BOTH answers "Q1", and because both matched the scheme's Q1
slot, the report came back as:

    Q1: 10.0 / 10.0        <- one 5-mark question reported out of 10
    Q2:  0.0 /  5.0        <- "[Not graded. ]" although the letter was written

The second letter was graded 5/5 and then filed under a label already taken, so
its real question was reported as never attempted and Q1's maximum was counted
twice. The phantom's remark also reached the student as the gibberish
"[Not graded. ].", because the remark cleaner rewrote its dash and dropped its
"verify".
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (
    GradeReport,
    MarksItem,
    MarksScheme,
    QuestionMark,
    _reconcile_report,
    humanise_remark,
)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def scheme(*pairs):
    return MarksScheme(total=sum(m for _q, m in pairs),
                       items=[MarksItem(qid=q, max=m) for q, m in pairs])


def q(qid, score, mx, page=1):
    return QuestionMark(qid=qid, score=score, max_score=mx, page=page, y_fraction=0.2)


def by_qid(rep):
    return {x.qid: x for x in rep.questions}


print("== the real case: two letters both labelled Q1 ==")
rep = _reconcile_report(
    GradeReport(total_score=10, max_total=10, overall_remarks="x",
                questions=[q("Q1", 5, 5, 1), q("Q1", 5, 5, 3)]),
    scheme(("Q1", 5), ("Q2", 5), ("Q3", 5), ("Q4", 5)), 20)
got = by_qid(rep)
check("the duplicate is re-homed to Q2, the next free slot", "Q2" in got, sorted(got))
check("Q2 keeps the marks it earned", got["Q2"].score == 5 if "Q2" in got else False)
check("Q1 is no longer double-counted", got["Q1"].max_score == 5, got["Q1"].max_score)
check("no question is reported out of a doubled maximum",
      all(x.max_score == 5 for x in rep.questions),
      [(x.qid, x.max_score) for x in rep.questions])
check("the student keeps every mark", rep.total_score == 10, rep.total_score)
check("the denominator matches the paper", rep.max_total == 20, rep.max_total)

print("== a genuinely unanswered question reads plainly ==")
for qid in ("Q3", "Q4"):
    check(f"{qid} says 'Not attempted'", got[qid].remark == "Not attempted", got[qid].remark)
    check(f"{qid} is flagged unattempted", got[qid].attempted is False)
check("the phantom remark survives the remark cleaner intact",
      humanise_remark("Not attempted", margin=True) == "Not attempted",
      humanise_remark("Not attempted", margin=True))
check("no internal wording reaches the student",
      not any("not graded" in (x.remark or "").lower() for x in rep.questions),
      [x.remark for x in rep.questions])
check("the operator gets a count of what was never graded", rep.ungraded_count == 2,
      rep.ungraded_count)

print("== a duplicate is re-homed IN THE PAPER'S ORDER, not to any free slot ==")
# The old loop kept the LAST matching free slot, so a second Q1 landed on Q4.
rep2 = _reconcile_report(
    GradeReport(total_score=6, max_total=6, overall_remarks="x",
                questions=[q("Q1", 3, 3), q("Q1", 3, 3)]),
    scheme(("Q1", 3), ("Q2", 3), ("Q3", 3), ("Q4", 3)), 12)
check("the second answer goes to Q2, not Q4", "Q2" in by_qid(rep2), sorted(by_qid(rep2)))

print("== three copies of the same label all find a home ==")
rep3 = _reconcile_report(
    GradeReport(total_score=6, max_total=6, overall_remarks="x",
                questions=[q("Q1", 2, 2), q("Q1", 2, 2), q("Q1", 2, 2)]),
    scheme(("Q1", 2), ("Q2", 2), ("Q3", 2)), 6)
check("all three are placed", sorted(by_qid(rep3)) == ["Q1", "Q2", "Q3"], sorted(by_qid(rep3)))
check("nothing was dropped", rep3.total_score == 6, rep3.total_score)

print("== a graded answer is never DELETED when no same-size slot is free ==")
rep4 = _reconcile_report(
    GradeReport(total_score=9, max_total=9, overall_remarks="x",
                questions=[q("Q1", 4, 4), q("Q1", 5, 5)]),
    scheme(("Q1", 4), ("Q2", 7)), 11)
check("the odd-sized duplicate is kept, not discarded", len(rep4.questions) == 2,
      [(x.qid, x.score, x.max_score) for x in rep4.questions])
check("its score is capped at the slot it was given",
      all(x.score <= x.max_score + 1e-9 for x in rep4.questions),
      [(x.qid, x.score, x.max_score) for x in rep4.questions])

print("== ordinary reports are untouched ==")
rep5 = _reconcile_report(
    GradeReport(total_score=7, max_total=10, overall_remarks="x",
                questions=[q("Q1", 3, 5), q("Q2", 4, 5)]),
    scheme(("Q1", 5), ("Q2", 5)), 10)
check("distinct qids keep their own slots",
      [(x.qid, x.score) for x in rep5.questions] == [("Q1", 3.0), ("Q2", 4.0)],
      [(x.qid, x.score) for x in rep5.questions])
check("nothing is flagged ungraded", rep5.ungraded_count == 0)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
