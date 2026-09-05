# -*- coding: utf-8 -*-
"""The second examiner's missing sentence is repaired, never used to refuse a copy.

Nobody reviews a held copy, so a certificate that carries a sound mark but no
justification has to be completed automatically. These checks pin the three pieces
that make that possible: the gap is reported as repairable rather than as a truncated
reply, the repair question names the offending rows, and merging a repair can only
add certainty.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (AuditItem, AuditSheet, Criterion, GradeReport, QuestionMark,
                    _norm_qid, audit_completion_errors, audit_completion_gaps,
                    audit_response_schema, build_audit_prompt)
from pipeline import _merge_audit_verdicts, _verify_output_budget

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def q(qid, score, maximum, *, criteria=None):
    return QuestionMark(qid=qid, score=score, max_score=maximum, page=1,
                        y_fraction=0.2, objective=False, criteria=criteria or [],
                        remark="", attempted=True)


report = GradeReport(total_score=3, max_total=10, overall_remarks="", questions=[
    q("Q1", 1, 5, criteria=[Criterion(description="method", awarded=1, max=5,
                                      met=False, evidence="", step_line_id="")]),
    q("Q2", 2, 5, criteria=[Criterion(description="working", awarded=2, max=5,
                                      met=False, evidence="", step_line_id="")]),
])
selected = {_norm_qid("Q1"), _norm_qid("Q2")}

print("== a ruled row with no reason is a repairable gap, not a missing verdict ==")
no_reason = [
    AuditItem(row=0, correct=1, qid="Q1", criterion=0, reason=""),
    AuditItem(row=1, correct=2, qid="Q2", criterion=0, reason="Interest omitted"),
]
errors, gaps = audit_completion_gaps(report, no_reason, True, selected)
check("the reason-less row is reported", any(e.startswith("row 0 (") for e in errors), errors)
check("it is offered for repair", gaps == {0: "missing"}, gaps)
check("it is NOT also reported as a missing verdict",
      not any("missing verdict" in e for e in errors), errors)
check("the row that did explain itself is not flagged",
      not any(e.startswith("row 1 (") for e in errors), errors)

print()
print("== a genuinely absent row is still a missing verdict, and not repairable ==")
errors, gaps = audit_completion_gaps(report, no_reason[:1], True, selected)
check("the absent row is reported missing",
      any("missing verdict row(s): 1" in e for e in errors), errors)
check("an absent row is not offered for repair", 1 not in gaps, gaps)

print()
print("== a generic reason is repairable too ==")
generic = [
    AuditItem(row=0, correct=1, qid="Q1", criterion=0, reason="incorrect answer"),
    no_reason[1],
]
errors, gaps = audit_completion_gaps(report, generic, True, selected)
check("a template verdict is caught", gaps == {0: "generic"}, gaps)

print()
print("== a complete certificate leaves nothing to repair ==")
complete = [
    AuditItem(row=0, correct=1, qid="Q1", criterion=0, reason="Formula never applied"),
    no_reason[1],
]
errors, gaps = audit_completion_gaps(report, complete, True, selected)
check("no errors", not errors, errors)
check("no gaps", not gaps, gaps)
check("the string wrapper still agrees",
      not audit_completion_errors(report, complete, True, selected))

print()
print("== the repair question names the rows that came back unusable ==")
plain = build_audit_prompt(report, "", "", include_awards=True, selected_qids=selected)
repair = build_audit_prompt(report, "", "", include_awards=True, selected_qids=selected,
                            repair_rows={0: "missing"})
check("an ordinary pass carries no repair block", "REPAIR REQUIRED" not in plain)
check("a repair pass does", "REPAIR REQUIRED" in repair)
check("and names the row", "#0" in repair.split("REPAIR REQUIRED")[1], repair[-600:])
check("while still listing every row so the numbering is unchanged",
      repair.count("  #0 ") == 1 and repair.count("  #1 ") == 1)

print()
print("== merging a repair can only add certainty ==")
first = [
    AuditItem(row=0, correct=1, qid="Q1", criterion=0, reason=""),
    AuditItem(row=1, correct=2, qid="Q2", criterion=0, reason="Interest omitted"),
]
repaired = [AuditItem(row=0, correct=1, qid="Q1", criterion=0,
                      reason="Formula never applied")]
merged = {item.row: item for item in _merge_audit_verdicts(first, repaired)}
check("the repaired reason is taken", merged[0].reason == "Formula never applied")
check("a row the repair dropped is kept from the first pass",
      merged[1].reason == "Interest omitted")
check("nothing is lost", len(merged) == 2, merged)

thinner = [AuditItem(row=1, correct=2, qid="Q2", criterion=0, reason="")]
merged = {item.row: item for item in _merge_audit_verdicts(first, thinner)}
check("a repair that loses a good reason does not overwrite it",
      merged[1].reason == "Interest omitted")

moved = [AuditItem(row=1, correct=4, qid="Q2", criterion=0, reason="Interest allowed")]
merged = {item.row: item for item in _merge_audit_verdicts(first, moved)}
check("but a repair that improves a row does replace it",
      merged[1].correct == 4 and merged[1].reason == "Interest allowed")

print()
print("== the verification budget leaves room for the whole certificate ==")
check("a small paper still gets real room", _verify_output_budget(4) >= 4000,
      _verify_output_budget(4))
check("the repair pass gets more than the first",
      _verify_output_budget(8, repair=True) > _verify_output_budget(8))
check("a large paper stays inside the provider ceiling",
      _verify_output_budget(500, repair=True) == 32000)
check("a zero-question edge case is still valid", _verify_output_budget(0) > 0)

print()
print("== the moderator cannot silently drop a justification ==")
wire = audit_response_schema()["$defs"]["AuditItem"]["required"]
check("the wire schema requires a reason", "reason" in wire, wire)
check("row and correct stay required", {"row", "correct"} <= set(wire), wire)
loose = AuditSheet.model_json_schema()["$defs"]["AuditItem"]["required"]
check("the parsing model stays permissive for tolerant recovery",
      "reason" not in loose, loose)
check("a held row names why a reason was owed",
      any("of 5)" in e for e in
          audit_completion_gaps(report, no_reason, True, selected)[0]),
      audit_completion_gaps(report, no_reason, True, selected)[0])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
