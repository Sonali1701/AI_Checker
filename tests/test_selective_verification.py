# -*- coding: utf-8 -*-
"""Deterministic high-risk selection for the bounded second examiner."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (AuditItem, Criterion, GradeReport, QuestionMark,
                    _focus_reference_text, _norm_qid, audit_completion_errors)
from selective_verification import select_verification_qids, verification_candidates

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def q(qid, score, maximum, *, objective=False, criteria=None, remark="", attempted=True):
    return QuestionMark(qid=qid, score=score, max_score=maximum, page=1,
                        y_fraction=0.2, objective=objective, criteria=criteria or [],
                        remark=remark, attempted=attempted)


report = GradeReport(total_score=7, max_total=15, overall_remarks="", questions=[
    q("Q1", 0, 5),
    q("Q2", 5, 5, criteria=[Criterion(description="answer", awarded=5, max=5,
                                       met=True, evidence="shown", step_line_id="P1L2")]),
    q("Q3", 1, 4, criteria=[Criterion(description="method", awarded=1, max=4,
                                       met=False, evidence="", step_line_id="")]),
    q("Q4", 1, 1, objective=True),
    q("Q5", 0, 1, attempted=False, remark="Not attempted"),
])
report.false_blank_qids = ["Q1"]
report.unresolved_answer_qids = ["Q5"]

rows = verification_candidates(report)
check("false blank is the first mandatory risk", rows[0].qid == "Q1" and rows[0].mandatory,
      rows)
check("objective item is excluded", "Q4" not in {row.qid for row in rows})
check("missing deduction evidence is mandatory",
      next(row for row in rows if row.qid == "Q3").mandatory)
check("an unresolved one-mark blank is verified rather than assumed",
      next(row for row in rows if row.qid == "Q5").mandatory)
selected, omitted = select_verification_qids(report, max_questions=1)
check("budgeted selector is deterministic", selected == ["Q1"], selected)
check("mandatory work that does not fit is surfaced", "Q3" in omitted, omitted)
selected_all, omitted_all = select_verification_qids(report, max_questions=10)
check("enough capacity leaves no mandatory omission", not omitted_all, omitted_all)
check("risk set is stable", selected_all == [row.qid for row in rows], selected_all)

key_text = "\n".join([
    "Question 1", "Expected Q1 method", "Expected Q1 conclusion",
    *(f"Q1 supporting line {index}" for index in range(1, 20)),
    "Question 10", "SECRET-Q10-ONLY", "Expected Q10 conclusion",
])
focused_key = _focus_reference_text(key_text, {"Q1"}, 20_000)
check("Q1 reference focus includes its official neighbourhood",
      "Expected Q1 method" in focused_key, focused_key)
check("Q1 reference focus does not match Q10",
      "SECRET-Q10-ONLY" not in focused_key, focused_key)

selected = {_norm_qid("Q1"), _norm_qid("Q2")}
valid_verdicts = [
    AuditItem(row=0, correct=0, qid="Q1", criterion=-1,
              reason="No answer is present for Q1"),
    AuditItem(row=1, correct=5, qid="Q2", criterion=0),
]
check("a complete exact verifier response is accepted",
      not audit_completion_errors(report, valid_verdicts, True, selected))
one_based_echoes = [
    AuditItem(row=0, correct=0, qid="Q1", criterion=-1,
              reason="No answer is present for Q1"),
    AuditItem(row=1, correct=5, qid="Q2", criterion=1),
]
check("human-style one-based criterion echoes are accepted",
      not audit_completion_errors(report, one_based_echoes, True, selected))
check("an empty verifier response is incomplete",
      bool(audit_completion_errors(report, [], True, selected)))
check("a truncated verifier response is incomplete",
      bool(audit_completion_errors(report, valid_verdicts[:1], True, selected)))
check("duplicate verifier rows are incomplete",
      bool(audit_completion_errors(report, [valid_verdicts[0], valid_verdicts[0]],
                                   True, selected)))
check("out-of-range verifier rows are incomplete",
      bool(audit_completion_errors(report, [AuditItem(row=9, correct=0)],
                                   True, selected)))
check("a mismatched echoed qid is incomplete",
      bool(audit_completion_errors(report, [
          AuditItem(row=0, correct=0, qid="Q9"), valid_verdicts[1]],
          True, selected)))
check("an unrelated criterion echo is incomplete",
      bool(audit_completion_errors(report, [
          valid_verdicts[0], AuditItem(row=1, correct=5, qid="Q2", criterion=7)],
          True, selected)))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
