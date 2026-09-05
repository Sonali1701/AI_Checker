# -*- coding: utf-8 -*-
"""Compressing a margin remark must not leave a note that names nothing.

Two faults found by re-reading the same three released copies. A live Economics copy
carried, as the entire margin note on a 2/3 answer:

    Is vague. Misses.

The examiner had written "The student is vague. Misses 'the link between price and
demand'." Compression then stripped the quoted phrase (the object) and the opener
(the subject), and the release gate passed it, because it only rejected a remark that
was generic from end to end.

The same compressor also read the apostrophe in "student's" as an opening quote and
swallowed everything up to the next real one, turning a sentence into the gibberish
"The studentthe link between price and demand'".
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import GradeReport, QuestionMark, finalise_student_remarks
from remark_quality import fragment_problem, remark_quality_problem

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def finalise(remark):
    report = GradeReport(
        total_score=2.0, max_total=3.0, overall_remarks="x",
        questions=[QuestionMark(qid="Q15", score=2.0, max_score=3.0,
                                page=1, y_fraction=0.5, remark=remark)])
    return finalise_student_remarks(report).questions[0].remark


print("== the repair signal recognises a note that names nothing ==")
check("a subject-less sentence is a fragment",
      fragment_problem("Is vague. Misses.") == "fragment",
      fragment_problem("Is vague. Misses."))
check("a one-word active verb is a fragment",
      fragment_problem("Misses.") == "fragment")
check("an active verb left at the end is a fragment",
      fragment_problem("Answer omits") == "fragment")
# Detecting a fragment must not start holding copies: nobody reviews a held copy, so
# this drives the rewrite and a pre-flight warning, never the release gate.
check("a fragment is NOT a release-blocking verdict",
      remark_quality_problem("Is vague. Misses.") == "",
      remark_quality_problem("Is vague. Misses."))

print()
print("== and does not mistake a complete short note for one ==")
for good in ("Interest omitted",
             "Formula not stated",
             "No Venn diagram drawn. Elements listed only.",
             "Did not state name is reserved for proceeding",
             "Wrote x>-2 but no interval notation",
             "Not attempted"):
    check(f"{good!r} is releasable",
          remark_quality_problem(good) == "" and fragment_problem(good) == "",
          f"{remark_quality_problem(good)}/{fragment_problem(good)}")

print()
print("== a fragment is repaired, never released and never held ==")
vague = finalise("The student is vague. Misses 'the link between price and demand'.")
check("the released note is not a fragment", fragment_problem(vague) == "", vague)
check("it still names what was missed", "link between price and demand" in vague, vague)
check("it has a subject again", vague.lower().startswith("answer"), vague)

omits = finalise("The student's answer omits 'the working capital adjustment entirely'.")
check("a dangling object is restored too",
      "working capital adjustment" in omits and fragment_problem(omits) == "",
      omits)

print()
print("== a possessive apostrophe is not an opening quote ==")
possessive = finalise(
    "The student's definition is vague. Misses 'the link between price and demand'.")
check("the sentence is not swallowed", "studentthe" not in possessive, possessive)
check("no quote is left dangling", possessive.count("'") % 2 == 0, possessive)
check("the fault still reads", "vague" in possessive.lower(), possessive)

print()
print("== remarks that were already fine are untouched ==")
for unchanged in ("No Venn diagram drawn. Elements listed only.",
                  "Did not state name is reserved for proceeding with incorporation",
                  "Interest omitted"):
    check(f"{unchanged!r} survives verbatim", finalise(unchanged) == unchanged,
          finalise(unchanged))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
