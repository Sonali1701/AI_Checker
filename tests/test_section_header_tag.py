# -*- coding: utf-8 -*-
"""A SECTION heading's mark tag is the section's total, not another question's.

A Class 10 PW Physics paper (Udaan Infinity, Subjective Test-09) prints its marks
TWICE: once in each section heading - "SECTION-B [3 Marks]" - and again on the
question inside it - "[3 M]". printed_tag_sum counted both, so a paper that prints
"Maximum Marks - 13" read as 23. A correct extraction of 13 then "disagreed" with
the paper by 10 marks, came back ai_mismatch on all four attempts, and /api/evaluate
refused the copy: an ordinary school paper could not be graded at all.

SECTION-A escaped only because "[1 Mark each]" does not match the tag pattern. That
near-miss is the tell - the three that did match were never per-question tags either.

The check this protects is a real one and must keep working: the tag VALUES are the
deterministic figure a paid extraction is reconciled against, so a paper whose tags
genuinely disagree with the extraction must still be caught.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import printed_tag_sum  # noqa: E402

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


# The real paper's layout, trimmed to the lines that carry marks.
PHYSICS = """Foundation
Subjective Test-09
Q1
SECTION-A [1 Mark each]
Multiple Choice Questions
An electric bulb is rated 220 V and 100 W.
[1 M]
Q2
which relation is correct?
[1 M]
Q3
Assertion (A): A convex mirror is used as a rear-view mirror.
[1 M]
Q4
SECTION-B [3 Marks]
Short Answer Type Questions
A hypermetropic eye has a near point of 1 m(100 cm).
[3 M]
Q5
SECTION-C [5 Marks]
Long Answer Type Question
(iii) Potential difference across the lamp and conductor.
[5 M]
Maximum Marks - 13
SECTION-D [2 Marks]
Case Based Question
(i) Find the position of the candle flame (u) in front of the lens.
[1 M]
(ii) Determine the focal length (f) of the lens.
[1 M]
"""

print("== the paper that could not be graded ==")
total, count = printed_tag_sum(PHYSICS)
check("the seven question tags are counted", count == 7, count)
check("and they sum to the 13 the paper prints as its own maximum",
      total == 13.0, total)
check("a correct extraction of 13 no longer disagrees with the paper",
      abs(13.0 - total) <= 0.25, total)

print()
print("== the section headings themselves ==")
only_headings = "SECTION-A [1 Mark each]\nSECTION-B [3 Marks]\nSECTION-C [5 Marks]\n"
h_total, h_count = printed_tag_sum(only_headings)
check("a heading tag is never counted as a question", h_count == 0, h_count)
check("so a heading-only paper reports no tags at all", h_total == 0, h_total)

for heading in ("SECTION-B [3 Marks]", "Section B [3 Marks]",
                "SECTION - C [5 Marks]", "SECTION-D [2 Marks]"):
    t, c = printed_tag_sum(heading + "\n")
    check(f"{heading!r} contributes nothing", (t, c) == (0, 0), f"{t}/{c}")

print()
print("== a question's own tag still counts, wherever it sits ==")
check("a bare question tag is counted",
      printed_tag_sum("Q4\nA hypermetropic eye.\n[3 M]\n") == (3.0, 1))
check("a tag on a line that merely mentions a section is still counted",
      printed_tag_sum("Answer all of Section B below. [3 M]\n")[1] == 1)

print()
print("== the disagreement check it protects still fires ==")
# Four per-question tags of 5, against an extraction that thinks the paper is 13:
# that is a real disagreement and must survive this change.
real = "Q1\n[5 M]\nQ2\n[5 M]\nQ3\n[5 M]\nQ4\n[5 M]\n"
r_total, r_count = printed_tag_sum(real)
check("four 5-mark tags still read as 20", (r_total, r_count) == (20.0, 4),
      f"{r_total}/{r_count}")
check("and an extraction claiming 13 is still caught",
      r_count >= 3 and abs(13.0 - r_total) > 0.25)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
