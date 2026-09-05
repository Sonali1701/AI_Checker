# -*- coding: utf-8 -*-
"""Does the marking scheme agree with the marks the paper itself prints?

Every other check in preflight reads the report alone, so all of them are satisfied
by a self-consistent report built on an invented scheme. That turned out to be the
largest measured error in the tool.

MEASURED, 2026-08-27, on the real CA material:

  Nine archived runs of ONE CA Inter Taxation copy (human 42/100), every run with
  the same question paper and answer key attached — verified identical, 14 pages /
  34,723 chars and 21 pages / 39,482 chars in all nine. They scored the copy:

      10.0  37.5  39.0  48.5  51.0  52.5  59.0  65.0  67.5

  a 57.5-mark spread. The cause is not judgement. The paper prints "(2 Marks)"
  against each of its 14 objective questions and "(1 Mark)" against 2 more, and
  SEVEN of the nine runs built schemes those tags contradict — objective questions
  worth 1.5, a value the paper never prints anywhere. Every run still summed to
  exactly 100, because when the repair pass fails (it raised
  "repair maxima sum to N, not the official 100" in ALL NINE runs) force_official_total
  rescales what is left. The denominator looked right; the decomposition under it
  was invented, differently each time.

  The 10.0 run is the one that matters: 32 questions, all marked attempted, scores
  summing exactly to 10/100, 13 annotations placed, per-question remarks written. A
  complete, internally consistent, confident report, wrong by 32 marks, passing
  every arithmetic gate in the pipeline.

  On the CA Final Auditing paper (human 29/100), which prints 2, 4 and 5-mark
  questions, a run built its scheme out of 3, 3.5 and 6.5 — not one printed value —
  and scored the copy 85.

Two tests, because they are sound on different papers: counts where the bracketed
tags fit inside the total, disjointness where they do not.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (MarksItem, MarksScheme, printed_tag_counts,
                    scheme_contradicts_paper, scheme_shares_no_printed_value,
                    scheme_tag_shortfall)
from preflight import check_copy

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def scheme(*maxima):
    return MarksScheme(total=round(sum(maxima), 2),
                       items=[MarksItem(qid=f"Q{i}", max=m) for i, m in enumerate(maxima)])


# The taxation paper's objective section as it actually reads: one bracketed tag per
# question, on its own line, seven 2s and a 1, twice over (Income Tax, then GST).
TAX = "\n".join(
    ["Total Marks: 100"]
    + [f"{i}. Objective question text here?\n(2 Marks)" for i in range(1, 8)]
    + ["8. Last objective question.\n(1 Mark)"] * 1
    + [f"{i}. GST objective question text?\n(2 Marks)" for i in range(1, 8)]
    + ["8. Last GST objective question.\n(1 Mark)"]
)

print("== the fixture matches the real paper ==")
counts = printed_tag_counts(TAX)
check("14 questions printed at 2 marks", counts[2.0] == 14, counts)
check("2 questions printed at 1 mark", counts[1.0] == 2, counts)

print("\n== the schemes the nine real runs produced ==")
# Objective maxima taken verbatim from each archived report.json, then padded with
# subjective parts so the scheme sums to 100 the way force_official_total leaves it.
SUBJ = (14.0, 5.5, 3.5, 5.5, 4.5, 6.0)
RUNS = {
    "haiku_bidir     67.5": ((2, 2, 2, 2, 2, 2, 2, 1) * 2, False),
    "sonnet_fixed    65.0": ((2, 2, 2, 2, 2, 2, 2, 1) * 2, False),
    "haiku_noaudit   48.5": ((2, 2, 2, 2, 2, 2, 2, 1)
                             + (2, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1), True),
    "tax_sonnet      51.0": ((2, 2, 2, 2, 2, 2, 2, 1)
                             + (2, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1), True),
    "haiku_upright2  59.0": ((2, 2, 2, 2, 2, 1.5, 1.5, 1) * 2, True),
    "opusaudit2      52.5": ((2, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1) * 2, True),
    "opusaudit       39.0": ((1.5,) * 7 + (1,) + (1.5,) * 7 + (1,), True),
    "tax_haiku       37.5": ((1.5,) * 8 * 2, True),
    "haiku_upright   10.0": ((1.5,) * 7 + (1,) + (1.5,) * 7 + (1,), True),
}
for label, (objective, should_hold) in RUNS.items():
    why = scheme_contradicts_paper(scheme(*objective, *SUBJ), TAX, 100.0)
    check(f"{label} -> {'held' if should_hold else 'passes'}",
          bool(why) == should_hold, why or "passed")

print("\n== the guard reads the paper, not the total ==")
good = scheme(*((2, 2, 2, 2, 2, 2, 2, 1) * 2), *SUBJ)
bad = scheme(*((1.5,) * 16), *SUBJ)
check("the two schemes differ only in the objective section",
      len(good.items) == len(bad.items), f"{len(good.items)} vs {len(bad.items)}")
check("and the arithmetic gate cannot tell them apart",
      not scheme_contradicts_paper(good, "", 100.0)
      and not scheme_contradicts_paper(bad, "", 100.0),
      "with no paper text there is nothing to check against, which is honest")
check("but with the paper's text the invented one is caught",
      not scheme_contradicts_paper(good, TAX, 100.0)
      and bool(scheme_contradicts_paper(bad, TAX, 100.0)))

print("\n== a paper that prints nothing is not punished for it ==")
check("no tags, no conflict", scheme_contradicts_paper(bad, "a scanned paper", 100.0) == "")
check("nor on a paper with only one or two tags",
      scheme_contradicts_paper(bad, "Q1. Something (5 Marks)\nQ2. More (7 Marks)", 100.0) == "",
      "one stray line reading as a tag must not condemn a whole scheme")
check("three is enough to believe them",
      bool(scheme_contradicts_paper(
          bad, "Q1. (5 Marks)\nQ2. (7 Marks)\nQ3. (8 Marks)", 100.0)))

print("\n== choice between TAGGED questions is not a shortfall ==")
# The counting test is only sound when the bracketed tags fit inside the paper's
# total. Where they exceed it the student chose, so a missing tag proves nothing.
CHOICE = "Total Marks: 20\n" + "\n".join(f"Q{i}. Attempt any two. (10 Marks)" for i in range(1, 5))
check("40 marks of tags on a 20-mark paper",
      sum(v * n for v, n in printed_tag_counts(CHOICE).items()) == 40)
check("answering two of the four is not flagged",
      scheme_tag_shortfall(scheme(10, 10), CHOICE, 20.0) == {},
      scheme_tag_shortfall(scheme(10, 10), CHOICE, 20.0))
check("and a paper printing ONE value never trips disjointness either",
      not scheme_shares_no_printed_value(scheme(6.5, 3.5), CHOICE),
      "splitting a 10-mark question into 6.5 + 3.5 is marking, not fabrication — with "
      "only one printed value there is no way to tell those apart, so stay quiet")

print("\n== the CA Final Auditing run that scored 29/100 as 85 ==")
AUD = "\n".join(["Q. text (2 Marks)"] * 15 + ["Q. text (4 Marks)"] * 7
                + ["Q. text (5 Marks)"] * 12)
check("its tags outrun a 100-mark paper, so counting is not sound",
      scheme_tag_shortfall(scheme(3, 3.5, 6.5), AUD, 100.0) == {})
why = scheme_contradicts_paper(scheme(*((3.5,) * 13), *((6.5,) * 7), 3, 3, 3), AUD, 100.0)
check("but disjointness catches it", bool(why), "not caught")
check("and the message names what the paper prints",
      "2, 4, 5" in why, why)

print("\n== sub-parts are allowed maxima the paper never prints ==")
# A 10-mark question split 5.5 + 4.5 is normal marking, not a fabrication, so ANY
# overlap with the printed values passes. Only total disjointness fails.
check("a split part does not trip the guard",
      not scheme_shares_no_printed_value(scheme(2, 2, 5.5, 4.5, 3.5), TAX))

print("\n== preflight blocks on it ==")


class Q:
    def __init__(self, qid, score, mx):
        self.qid, self.score, self.max_score = qid, score, mx
        self.remark, self.attempted, self.criteria = "", True, []


class R:
    def __init__(self, maxima):
        self.questions = [Q(f"Q{i}", m, m) for i, m in enumerate(maxima)]
        self.total_score = round(sum(maxima), 2)
        self.max_total = self.total_score
        self.overall_remarks = ""


faithful = R(((2,) * 7 + (1,)) * 2 + SUBJ)
invented = R((1.5,) * 16 + SUBJ)
codes = lambda r, t: [i.code for i in check_copy(r, total_marks=r.max_total, paper_text=t)]
check("a faithful scheme passes the gate",
      "scheme-contradicts-paper" not in codes(faithful, TAX), codes(faithful, TAX))
check("an invented one is blocked",
      "scheme-contradicts-paper" in codes(invented, TAX), codes(invented, TAX))
check("the issue is blocking, not a warning",
      all(i.severity == "block" for i in check_copy(invented, total_marks=invented.max_total,
                                                    paper_text=TAX)
          if i.code == "scheme-contradicts-paper"))
check("and the gate stays silent when no paper text is supplied",
      "scheme-contradicts-paper" not in codes(invented, ""),
      "without the paper there is no source of truth, and inventing one would be worse")

print("== the proportional smear is no longer silent ==")
# force_official_total's own docstring concedes the split it leaves is approximate,
# and layer 2 failed on ALL NINE archived 100-mark runs ("repair maxima sum to 96 /
# 110 / 111 / 120, not the official 100"), so on large papers the smear IS the path.
smeared = R(((2,) * 7 + (1,)) * 2 + SUBJ)
smeared.maxima_rescaled = True
found = [i for i in check_copy(smeared, total_marks=smeared.max_total, paper_text=TAX)
         if i.code == "maxima-rescaled"]
check("a rescaled report says so", len(found) == 1, [i.code for i in check_copy(smeared, total_marks=smeared.max_total, paper_text=TAX)])
check("as a BLOCK — a fabricated denominator must not reach a student", found and found[0].severity == "block")
check("an unrescaled report stays quiet",
      not [i for i in check_copy(faithful, total_marks=faithful.max_total, paper_text=TAX)
           if i.code == "maxima-rescaled"])
check("the field defaults to False on a fresh report",
      __import__("grader").GradeReport.model_fields["maxima_rescaled"].default is False)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
