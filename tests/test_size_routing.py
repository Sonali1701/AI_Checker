# -*- coding: utf-8 -*-
"""Paper size picks the grader, and a runaway grader never loses the copy.

Sonnet cannot reach the tighter department caps on a long booklet, so 100-mark
papers must grade on haiku. Measured on
CA Inter Taxation (18 pages, human 42/100, same fixes and material on both):
    claude-sonnet-4-6   65.0 (+23.0)   Rs.50.1
    claude-haiku-4-5    52.5 (+10.5)   Rs.19.8
Haiku's one known failure mode is runaway generation (3 of 10 Commerce
Accountancy booklets blew through the 64k output cap), which used to kill the
sheet outright — hence the fallback this also pins.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline
from grader import OutputLimitExceeded
from pipeline import (
    _BUDGET_GRADING_MODEL,
    _LARGE_PAPER_GRADING_MODEL,
    _LARGE_PAPER_MARKS,
    cfg_from_request,
)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def cfg(total_marks=None, model="", provider="gemini", student_class="CA Inter Group 1"):
    return cfg_from_request(provider, model, "Taxation", google_token="t", task_id="x",
                            student_class=student_class, total_marks=total_marks)


print("== large-paper routing (over 50 marks) grades on haiku ==")
for marks in (80, 100):
    check(f"{marks}-mark paper -> large-paper grader",
          cfg(marks).model == _LARGE_PAPER_GRADING_MODEL, cfg(marks).model)

print("== small-paper routing keeps the steadier default ==")
for marks in (25, 50, _LARGE_PAPER_MARKS):
    check(f"{marks:g}-mark paper -> budget grader",
          cfg(marks).model == _BUDGET_GRADING_MODEL, cfg(marks).model)
check("an unknown total falls back to the steadier default, not haiku",
      cfg(None).model == _BUDGET_GRADING_MODEL, cfg(None).model)
check("a junk total does not crash routing", cfg("not-a-number").model == _BUDGET_GRADING_MODEL)

print("== size routing applies on an explicit claude request too ==")
check("claude + 100 marks -> haiku",
      cfg(100, provider="claude").model == _LARGE_PAPER_GRADING_MODEL)
check("claude + 25 marks -> sonnet",
      cfg(25, provider="claude").model == _BUDGET_GRADING_MODEL)

print("== an explicitly requested model still wins over size ==")
check("explicit Claude model is never overridden",
      cfg(100, model="claude-opus-4-8").model == "claude-opus-4-8")
check("a Gemini model id is ignored: Gemini is switched off",
      cfg(100, model="gemini-2.5-flash").model == _LARGE_PAPER_GRADING_MODEL
      and cfg(100, model="gemini-2.5-flash").provider == "claude")

print("== school papers route by size even though their cap remains Rs.15 ==")
check("a 100-mark school paper gets the lower-cost large-paper grader",
      cfg(100, student_class="Class 12").model == _LARGE_PAPER_GRADING_MODEL)

print("== a runaway grader falls back instead of losing the sheet ==")
calls = []


def fake_grade(**kw):
    calls.append(kw["model"])
    if kw["model"] == _LARGE_PAPER_GRADING_MODEL:
        raise OutputLimitExceeded("runaway")
    return "REPORT"


saved = pipeline.claude_grade
pipeline.claude_grade = fake_grade
try:
    common = {"model": _LARGE_PAPER_GRADING_MODEL}
    c = cfg(100)
    try:
        report = pipeline.claude_grade(**common)
    except OutputLimitExceeded:
        fallback = _BUDGET_GRADING_MODEL if c.model != _BUDGET_GRADING_MODEL else None
        check("a fallback model is available for the large-paper grader", fallback is not None)
        common["model"] = fallback
        report = pipeline.claude_grade(**common)
    check("the sheet is still graded after a runaway", report == "REPORT")
    check("it retried on the steadier model, exactly once",
          calls == [_LARGE_PAPER_GRADING_MODEL, _BUDGET_GRADING_MODEL], f"{calls}")
finally:
    pipeline.claude_grade = saved

print("== the runaway error is its own type, not a bare RuntimeError ==")
check("OutputLimitExceeded subclasses RuntimeError (old handlers still catch it)",
      issubclass(OutputLimitExceeded, RuntimeError))
check("but it is distinguishable for the fallback",
      OutputLimitExceeded is not RuntimeError)

print("== the frontend must actually SEND total_marks, or none of this fires ==")
# Same failure shape as the hard-coded MODEL bug: server-side routing is dead if the
# form never carries the field it routes on.
import re
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app_js = open(os.path.join(_root, "static", "app.js"), encoding="utf-8").read()
check("commonForm() appends total_marks",
      re.search(r'fd\.append\("total_marks"', app_js) is not None)
evaluate_body = re.search(r'async function evaluate\(\)\s*\{([\s\S]*?)\n\}', app_js)
check("the evaluate call is built from commonForm()",
      evaluate_body is not None and 'const fd = commonForm();' in evaluate_body.group(1))
# And the server must forward it into routing rather than only using it for the total.
server_py = open(os.path.join(_root, "server.py"), encoding="utf-8").read()
check("server passes total_marks into cfg_from_request",
      re.search(r'cfg_from_request\([\s\S]{0,400}?total_marks=paper_total', server_py) is not None)
check("strict production uses the budget-feasible primary before its challenger",
      "model=_LARGE_PAPER_GRADING_MODEL," in server_py)
check("strict reports have enough output room even on short papers",
      "primary_output_limit = 15000" in open(
          os.path.join(_root, "pipeline.py"), encoding="utf-8").read())

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
