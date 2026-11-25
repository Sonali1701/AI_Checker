# -*- coding: utf-8 -*-
"""Hard-copy budget policy and model-safe per-call accounting."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from budgeting import (BudgetDenied, BudgetLedger, BudgetPolicy, CallPlan, Stage,
                       UnpricedModel, UsageEvent)
from costs import budget_for, compute_cost, record_usage_event

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


print("== commercial caps ==")
# Department caps set by the operator on 2026-09-17: school Rs.12, CA/commerce
# Rs.18, whatever the paper's total. The size of the paper picks the grader only.
check("school 50 marks is capped at Rs.12",
      BudgetPolicy.for_copy(total_marks=50, exam_type="school",
                            subject="Chemistry").cap_inr == 12)
check("school 100 marks remains capped at Rs.12",
      BudgetPolicy.for_copy(total_marks=100, exam_type="school",
                            subject="Chemistry").cap_inr == 12)
check("CA 50 marks is capped at Rs.18",
      BudgetPolicy.for_copy(total_marks=50, exam_type="professional").cap_inr == 18)
check("CA 100 marks is capped at Rs.18",
      BudgetPolicy.for_copy(total_marks=100, exam_type="professional").cap_inr == 18)
check("commerce 100 marks is capped at Rs.18 even when classed as school",
      BudgetPolicy.for_copy(total_marks=100, exam_type="school",
                            subject="Accountancy").cap_inr == 18)
check("commerce 50 marks is capped at Rs.18 too",
      BudgetPolicy.for_copy(total_marks=50, exam_type="school",
                            subject="Business Studies").cap_inr == 18)
for marks, exam_type, subject in (
    (100, "school", "Chemistry"),
    (50, "professional", "Advanced Mathematics"),
    (100, "professional", "Advanced Mathematics"),
    (100, "school", "Business Studies"),
):
    policy_cap = BudgetPolicy.for_copy(
        total_marks=marks, exam_type=exam_type, subject=subject).cap_inr
    check(f"BudgetPolicy shares costs.budget_for for {exam_type}/{subject}/{marks}",
          policy_cap == budget_for(
              marks, exam_type=exam_type, subject=subject), policy_cap)

print("== mixed-model calls keep their own rates ==")
usage = {}
record_usage_event(usage, {"provider": "claude", "model": "claude-haiku-4-5",
                           "input_tokens": 10000, "output_tokens": 2000}, stage="primary")
record_usage_event(usage, {"provider": "claude", "model": "claude-opus-4-8",
                           "input_tokens": 2000, "output_tokens": 500}, stage="verify")
cost = compute_cost(usage)
check("mixed total prices Haiku and Opus separately", cost.total_inr == 3.995,
      cost.total_inr)
check("event audit trail is retained", len(cost.events) == 2, cost.events)
check("cost identifies mixed models", cost.model.startswith("mixed:"), cost.model)

print("== reservations preserve verification money ==")
ledger = BudgetLedger(BudgetPolicy.for_copy(total_marks=50, exam_type="school"))
try:
    ledger.reserve(CallPlan(stage=Stage.PRIMARY, provider="claude",
                            model="claude-sonnet-4-6", max_output_tokens=9100))
    check("oversized primary is denied before dispatch", False)
except BudgetDenied:
    check("oversized primary is denied before dispatch", True)
ticket = ledger.reserve(CallPlan(stage=Stage.VERIFY, provider="claude",
                                 model="claude-haiku-4-5", max_output_tokens=500,
                                 mandatory=True))
ledger.commit(ticket, UsageEvent(call_id="verify-1", stage=Stage.VERIFY,
                                 provider="claude", model="claude-haiku-4-5",
                                 output_tokens=300))
check("billed verification completes the mandatory reserve",
      ledger.mandatory_verification_complete)
check("resolved verified ledger can release", ledger.can_release()[0], ledger.summary())

try:
    UsageEvent(call_id="x", stage=Stage.PRIMARY, provider="unknown",
               model="provider-model-x", output_tokens=1).cost_inr()
    check("unpriced models fail closed", False)
except UnpricedModel:
    check("unpriced models fail closed", True)

missing_usage = compute_cost(
    {"provider": "claude", "model": "claude-haiku-4-5"}, mathpix_pages=1)
check("a known model with missing usage is not an attested budget",
      missing_usage.rates_found and not missing_usage.usage_attested,
      missing_usage)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
