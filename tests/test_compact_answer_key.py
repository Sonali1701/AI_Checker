# -*- coding: utf-8 -*-
"""The publisher key that writes "Q1" and the option letter underneath.

A Class 10 PW Physics paper (Udaan Infinity, Subjective Test-09) prints its answer
key as a two-line row per question - "Q1" with a nbsp-padded arrow, then "D" on the
next line - and "Check Solution" for the descriptive questions. Neither existing
pattern matched it: one wants "1." or "1)" (no Q, and a dot), the other wants an
explicit "Answer:" label. So the paper's own answers never reached the deterministic
objective scorer, all three MCQs were left to model judgement, and the model marked a
correct (D) WRONG and a wrong (B) RIGHT - reproducibly, over two paid runs.

The guard that stays: a bare DIGIT is only an option number inside a paper-declared
objective range. "Q14 3" in a worked solution is arithmetic far more often than it is
option C, and that is exactly what the declared range exists to stop. An explicit A-D
letter needs no such permission - a key writing "D" is stating an option outright.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exam_package import (_declared_objective_numbers,  # noqa: E402
                          _official_objective_answers)

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


# The real key, nbsp padding and all. Q4-Q6 are descriptive and say so.
PHYSICS_KEY = (
    "Foundation\nAnswer Key\n"
    "Q1 \xa0\xa0\nD\n"
    "Q2 \xa0\xa0\nA\n"
    "Q3 \xa0\xa0\nD\n"
    "Q4 \xa0\xa0\nCheck Solution\n"
    "Q5 \xa0\xa0\nCheck Solution\n"
    "Q6 \xa0\xa0\nCheck Solution\n"
)
# This paper never prints "Questions 1 to 3 are MCQs" anywhere.
PHYSICS_PAPER = "Foundation\nSubjective Test-09\nSECTION-A [1 Mark each]\n" \
                "Multiple Choice Questions\n"

print("== the key that was never read ==")
check("the paper declares no objective range at all",
      _declared_objective_numbers(PHYSICS_PAPER) == set())
answers = _official_objective_answers(PHYSICS_KEY, PHYSICS_PAPER)
check("the three MCQ answers are recovered anyway",
      answers == {1: "D", 2: "A", 3: "D"}, answers)
check("a descriptive 'Check Solution' row yields no answer",
      not ({4, 5, 6} & set(answers)), sorted(answers))

print()
print("== the digit guard is untouched ==")
# "3" as a whole answer, with no declared MCQ range, must NOT become option C.
check("a bare digit is ignored without a declared range",
      _official_objective_answers("Q14 \xa0\xa0\n3\n", "no declaration here") == {})
check("but a declared range still admits numeric options",
      _official_objective_answers(
          "1.\n(3)\n2.\n(1)",
          "Questions 1 to 2 are Multiple Choice Questions (MCQs).")
      == {1: "C", 2: "A"})

print()
print("== the existing key styles still work ==")
check("numbered key with bracketed letter",
      _official_objective_answers(
          "1.\n(D)", "Questions 1 to 1 are MCQs.") == {1: "D"})
check("an explicit 'Correct Answer:' still wins over a later restatement",
      _official_objective_answers(
          "Q5\nText Solution:\nCorrect Answer: (B) Partner by estoppel\n",
          "Section A contains 6 Multiple Choice Questions. "
          + " ".join(f"Q{i}" for i in range(1, 7))).get(5) == "B")

print()
print("== the compact form is anchored, not greedy ==")
check("a letter mid-sentence is not an answer",
      _official_objective_answers("Q2 is worth D marks in total\n", "") == {})
check("a two-word value is not a letter",
      _official_objective_answers("Q3 \xa0\xa0\nD is correct\n", "") == {})
check("same-line form is accepted",
      _official_objective_answers("Q7 A\n", "") == {7: "A"})
check("a letter beyond A-D is not an option",
      _official_objective_answers("Q8 \xa0\xa0\nZ\n", "") == {})

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
