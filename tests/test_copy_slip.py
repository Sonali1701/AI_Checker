# -*- coding: utf-8 -*-
"""A miscopied number that never entered the working is a slip, not a content error.

MEASURED on evaluated_2eaa5acf (Physics, Neev Batch Test-05 Set-B, Class 9, 13 marks).

Q6.ii asks: "By what factor does its stopping distance increase compared to 54 km/h?"
The student worked v^2 = u^2 + 2as through correctly with u = 30 m/s and a = -4 m/s^2,
reached s = 112.5 m, and concluded:

    "It has increased approx 4 times more than the stopping distance of 34 km/h."

The factor 4 is exactly right: at 54 km/h (15 m/s) the stopping distance is
15^2 / 8 = 28.125 m, and 112.5 / 28.125 = 4.0. The arithmetic used 54 throughout. The
only wrong thing on the page is one digit in a sentence restating the question.

The tool scored it 0.5 / 1 and circled the 0.5 on the page.

Two facts about that copy, both verified before this rule was written:
  * The marking scheme was RIGHT. Sections A/B/C/D are 3 + 3 + 5 + 2 = 13 and the
    tool's split (1, 1, 1, 3, 5, 2) matches the paper exactly, so this is not the
    scheme-fidelity failure in test_scheme_fidelity.py.
  * Nothing was struck through. The line that appears to cross out "34" in the
    evaluated PDF is the notebook's own ruling; the only drawing near it is the red
    circle around the 0.5.

So the defect is purely the SIZE of the deduction, and it could not be fixed by
scoring alone: _snap_half rounds every award to 0.5, so on a 1-mark part the only
reachable scores are 0, 0.5 and 1. The paper prints no fractional marks either
([1 M], [2 M], [3 M]). The rule therefore has to be "this costs nothing", not
"this costs less".
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader
from grader import _snap_half
from preflight import MARGIN_REMARK_MAX_WORDS, _BANNED_IN_REMARK

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("== why the mark could not simply be made smaller ==")
check("half-mark steps are enforced", _snap_half(0.75) in (0.5, 1.0), _snap_half(0.75))
check("so 0.75 is unreachable on a 1-mark part",
      _snap_half(0.75) != 0.75,
      "any 0.25 deduction would be rounded away, so the rule must zero the deduction")

print("\n== the rule reaches every paper ==")
PAPERS = [("Physics", "Class 9", "school"), ("Mathematics", "Class 10", "school"),
          ("English", "Class 12", "school"), ("Accounting", "CA Foundation", "professional"),
          ("Taxation (Income Tax + GST)", "CA Inter", "professional")]
for subject, klass, exam in PAPERS:
    p = grader.build_system_prompt(subject, klass, exam_type=exam)
    check(f"{subject} ({klass})", "A COPY SLIP IS NOT A CONTENT ERROR" in p)

phys = grader.build_system_prompt("Physics", "Class 9", exam_type="school")

print("\n== it says the slip costs nothing ==")
check("no marks are taken", "that costs NOTHING" in phys)
check("and full marks are given explicitly", "Give\nfull marks for the part" in phys)
check("the student is still told what slipped", "say what slipped in the remark" in phys)

print("\n== both guards are stated, so it cannot excuse a real error ==")
check("the asked-for quantity must be correct",
      "the quantity the question actually asked for is CORRECT" in phys)
check("the miscopied value must not have been used",
      "the miscopied value never entered the calculation" in phys)
check("a figure carried into the working is an ordinary error",
      "If the wrong figure was carried INTO the working" in phys)
check("the test is stated as a question about consequence",
      'did writing it wrong change the answer' in phys)

print("\n== the real copy is the worked example ==")
for token in ("112.5", "34 km/h", "54", "28.125"):
    check(f"names {token!r}", token in phys)

print("\n== the example remark would survive the tool's own gate ==")
example = "Conclusion says 34 km/h. The question says 54."
check("the prompt offers that remark", example in " ".join(phys.split()))
low = example.lower()
for needle, why in _BANNED_IN_REMARK:
    check(f"no banned wording ({why})", needle not in low, f"contains {needle!r}")
check("no semicolon clause-stacking", ";" not in example)
check("no free-standing dash", " - " not in example and " — " not in example)
check(f"within {MARGIN_REMARK_MAX_WORDS} words", len(example.split()) <= MARGIN_REMARK_MAX_WORDS,
      f"{len(example.split())} words")

print("\n== it does not collide with the commerce presentation rule ==")
# Rule 9 is a CORRECT figure sitting in the wrong row, which still costs up to half a
# step. This rule is a value miscopied from the question that never reached the
# working. Different triggers, so both belong in a commerce prompt at once.
ca = grader.build_system_prompt("Accounting", "CA Foundation", exam_type="professional")
check("both rules are present together",
      "costs at most HALF that step" in ca and "A COPY SLIP IS NOT A CONTENT ERROR" in ca)
check("rule 9 still forbids taking the whole step", "never the whole step" in ca)
check("and the cancelled-work rule is still there",
      "before you report that something is duplicated" in ca.lower())

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
