# -*- coding: utf-8 -*-
"""The free release gate: catch mechanical faults before a copy reaches a student.

Every case here is taken from a copy that actually shipped. Fourteen of the eighteen
defects found while reviewing real evaluated sheets were mechanical, so this gate is
what "perfect copies go to students" mostly means — and it costs nothing to run.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from grader import Criterion, GradeReport, QuestionMark
from preflight import check_copy, summarise

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def q(qid, score, mx, remark="", crits=None, attempted=True):
    return QuestionMark(qid=qid, score=score, max_score=mx, page=1, y_fraction=0.2,
                        remark=remark, attempted=attempted, criteria=crits or [])


print("== a clean copy raises nothing ==")
clean = GradeReport(total_score=8, max_total=10, overall_remarks="Good paper.",
                    questions=[q("Q1", 5, 5),
                               q("Q2", 3, 5, "Should be Rs 2,05,460")])
check("no issues on a clean copy", check_copy(clean, total_marks=10) == [],
      [str(i) for i in check_copy(clean, total_marks=10)])
check("summarise says so", summarise([]) == "pre-flight clean")

print("== the faults that actually shipped ==")
bad = GradeReport(total_score=10, max_total=91, overall_remarks="x", questions=[
    q("Q1", 5, 5), q("Q1", 5, 5),                       # 12th English: graded twice
    q("Q2", 0, 5, "[Not graded. ]."),                   # internal marker on the copy
    q("Q3", 3, 5, ""),                                  # lost marks, no reason
    q("Q4", 1, 4, "Freight of Rs 10,000 should be excluded from cost - student appears "
                  "to have included it; working notes present and format correct, and the "
                  "closing stock figure was also carried forward without the adjustment "
                  "that the question asked for in its second paragraph",
      [Criterion(description="freight", awarded=1, max=4, met=True, evidence="")]),
])
got = check_copy(bad, total_marks=86)
for code in ("denominator", "duplicate-qid", "total-mismatch", "met-mismatch",
             "remark-wording"):
    check(f"catches {code}", code in codes(got, "block"), sorted(codes(got)))
    check("blocks no-reason", "no-reason" in codes(got, "block"), sorted(codes(got)))
    for code in ("remark-punctuation", "remark-length"):
        check(f"warns on {code}", code in codes(got, "warn"), sorted(codes(got, "warn")))
check("blocking issues come first",
      [i.severity for i in got] == sorted(i.severity for i in got))

print("== each mark-arithmetic fault on its own ==")
check("a wrong denominator blocks",
      "denominator" in codes(check_copy(
          GradeReport(total_score=5, max_total=91, overall_remarks="x",
                      questions=[q("Q1", 5, 91)]), total_marks=86), "block"))
check("a question scored above its max blocks",
      "over-award" in codes(check_copy(
          GradeReport(total_score=7, max_total=5, overall_remarks="x",
                      questions=[q("Q1", 7, 5)]), total_marks=5), "block"))
check("steps that do not add to the question score block",
      "criteria-mismatch" in codes(check_copy(
          GradeReport(total_score=4, max_total=5, overall_remarks="x",
                      questions=[q("Q1", 4, 5, "x", [
                          Criterion(description="a", awarded=1, max=2, met=False,
                                    evidence="")])]), total_marks=5), "block"))
check("a step scored above its own max blocks",
      "step-over-award" in codes(check_copy(
          GradeReport(total_score=3, max_total=2, overall_remarks="x",
                      questions=[q("Q1", 3, 2, "x", [
                          Criterion(description="a", awarded=3, max=2, met=True,
                                    evidence="")])]), total_marks=2), "block"))

print("== a tick that would contradict the marks blocks ==")
# met drives whether a tick is drawn, so met=True on a part-scored step means the
# sheet shows a tick beside marks that say the step was NOT fully earned.
check("met=True on a partly-earned step blocks",
      "met-mismatch" in codes(check_copy(
          GradeReport(total_score=1, max_total=4, overall_remarks="x",
                      questions=[q("Q1", 1, 4, "x", [
                          Criterion(description="a", awarded=1, max=4, met=True,
                                    evidence="")])]), total_marks=4), "block"))
check("met=False on a fully-earned step also blocks",
      "met-mismatch" in codes(check_copy(
          GradeReport(total_score=4, max_total=4, overall_remarks="x",
                      questions=[q("Q1", 4, 4, "", [
                          Criterion(description="a", awarded=4, max=4, met=False,
                                    evidence="")])]), total_marks=4), "block"))

print("== wording that must never reach a student ==")
for text, why in (("[Not graded. ].", "internal marker"),
                  ("interest but OCR shows Rs 400", "tool internals"),
                  ("M1 opening balance needs verification", "hedging"),
                  ("The student failed to show working", "third person")):
    rep = GradeReport(total_score=1, max_total=2, overall_remarks="x",
                      questions=[q("Q1", 1, 2, text)])
    check(f"blocks {why}", "remark-wording" in codes(check_copy(rep, total_marks=2),
                                                     "block"), text)

print("== an unattempted question is not nagged for a reason ==")
un = GradeReport(total_score=0, max_total=5, overall_remarks="x",
                 questions=[q("Q9", 0, 5, "Not attempted", attempted=False)])
check("no 'no-reason' warning when nothing was attempted",
      "no-reason" not in codes(check_copy(un, total_marks=5)),
      [str(i) for i in check_copy(un, total_marks=5)])

print("== faults visible only in the finished PDF ==")


def render(pages, mark_pages, overflow=False):
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=400, height=300)
        pg.draw_line(fitz.Point(300, 0), fitz.Point(300, 300), color=(1, 0, 0))
        if i in mark_pages:
            pg.insert_text((310, 40), "Q1 2/2", fontsize=12, color=(1, 0, 0))
        if overflow and i == 0:
            pg.insert_text((360, 120), "runs past the edge here", fontsize=12,
                           color=(1, 0, 0))
    return doc.tobytes()


rep = GradeReport(total_score=2, max_total=2, overall_remarks="x",
                  questions=[q("Q1", 2, 2)])
gap = check_copy(rep, pdf_bytes=render(4, {0, 3}), total_marks=2)
check("an unmarked page inside the marked range is flagged",
      "unmarked-page" in codes(gap, "warn"), [str(i) for i in gap])
none_gap = check_copy(rep, pdf_bytes=render(3, {0, 1, 2}), total_marks=2)
check("consecutive marked pages are fine",
      "unmarked-page" not in codes(none_gap), [str(i) for i in none_gap])
over = check_copy(rep, pdf_bytes=render(2, {0, 1}, overflow=True), total_marks=2)
check("text running off the page blocks", "text-overflow" in codes(over, "block"),
      [str(i) for i in over])
check("a damaged PDF warns rather than crashing",
      "unreadable-pdf" in codes(check_copy(rep, pdf_bytes=b"not a pdf", total_marks=2)))

print("== does the review agree with the marking? ==")
# Fourteen checks looked at marks, or at remarks, but never at the two together —
# and that is the contradiction a reviewer spots first. All four cases below are
# taken verbatim from the Business Laws copy that shipped.


def agree(qid, score, mx, remark, crits=None):
    return check_copy(
        GradeReport(total_score=score, max_total=mx, overall_remarks="x",
                    questions=[q(qid, score, mx, remark, crits)]), total_marks=mx)


TICKED = [Criterion(description="scenario 1 contribution", awarded=1.5, max=1.5,
                    met=True, evidence="he can claim 2000 from A and B each scenario"),
          Criterion(description="provision", awarded=1, max=1.5, met=False, evidence="")]

check("full marks with a fault named in the margin blocks",
      "remark-on-full-marks" in codes(
          agree("Q9", 5, 5, "Working note for depreciation is missing."), "block"))
check("a citation-only deduction blocks",
      "citation-only-deduction" in codes(
          agree("Q2.a.ii", 2.5, 3, "Section 16(2) not cited by name"), "block"))
check("even when the section reference carries extra words",
      "citation-only-deduction" in codes(
          agree("Q1.b.ii", 2.5, 3,
                "Section 14(a) implied condition as to title not explicitly cited"),
          "block"))
# Q3.a's remark had NO fault word in it — "C's claim ... stated as Rs 2000 each" is
# a flat description printed under "where marks were lost", on a line carrying a
# tick and its full 1.5. Requiring a fault word is how it reached a student.
check("a remark pointing at a TICKED step is flagged, fault word or not",
      "remark-faults-earned-step" in codes(
          agree("Q3.a", 5.5, 6,
                "C's claim from A and B in scenario (1) stated as Rs 2000 each",
                TICKED)))

print("== and does not cry wolf ==")
for qid, s, m, remark, crits, why in (
        ("Q1.b.i", 3, 4,
         "Section 20 not explicitly cited. Constructive delivery discussion not directly relevant.",
         None, "a second, real ground is given"),
        ("Q1.c", 4, 6,
         "Section 12 sufficient-evidence point missing. Penalty for false statement not covered.",
         None, "complains about content, not about a citation"),
        ("Q10", 5, 5, "Full marks. Provision, application and conclusion are all correct.",
         None, "full marks with a praise remark"),
        ("Q12", 4, 4, "Well argued. Next time quote the section as well.",
         None, "full marks with coaching, not a fault"),
        ("Q11", 3, 4, "Property in specific goods passes at the contract, not on delivery.",
         [Criterion(description="passing of property", awarded=1, max=2, met=False,
                    evidence="constructive delivery")],
         "the step it faults did NOT earn its marks"),
        ("Q3.a", 5.5, 6, "Both answers correct. State the contribution rule fully in your provision.",
         TICKED, "the repaired remark no longer points at the ticked step")):
    got = agree(qid, s, m, remark, crits)
    bad = [i for i in got if i.code in ("remark-on-full-marks", "citation-only-deduction",
                                        "remark-faults-earned-step")]
    check(f"{qid}: {why}", not bad, [str(i) for i in bad])

print("== a mark drawn over the student's own figures ==")
# On a real CA Foundation Accounting copy the circled part-marks landed in the
# amounts column: one turned 7,61,024 into "76(1)24". The writing is a dark scan and
# every mark is red, so the overlap is measurable straight off the finished PDF.


def sheet(mark_on_the_figure):
    """A page with one handwritten-looking figure and one red circled mark."""
    doc = fitz.open()
    pg = doc.new_page(width=400, height=200)
    pg.insert_text((60, 100), "7,61,024", fontsize=20, color=(0.1, 0.1, 0.12))
    cx = 92 if mark_on_the_figure else 260      # on the digits, or in clear paper
    pg.draw_circle(fitz.Point(cx, 94), 10, color=(0.8, 0.08, 0.1), width=1.8)
    pg.insert_text((cx - 3, 98), "1", fontsize=9, fontname="hebo",
                   color=(0.8, 0.08, 0.1))
    return doc.tobytes()


rep1 = GradeReport(total_score=1, max_total=1, overall_remarks="x",
                   questions=[q("Q1", 1, 1)])
over = check_copy(rep1, pdf_bytes=sheet(True), total_marks=1)
clear = check_copy(rep1, pdf_bytes=sheet(False), total_marks=1)
check("a mark on the figures blocks release",
      "mark-overlaps-writing" in codes(over, "block"), [str(i) for i in over])
check("a mark in clear paper raises nothing",
      not [i for i in clear if "writing" in i.code], [str(i) for i in clear])
check("the reason names the mark and how buried it is",
      any("'1'" in i.detail and "%" in i.detail for i in over),
      [i.detail for i in over])

print("== the two tiers are ordered and calibrated ==")
from preflight import _MARK_INK_BLOCK, _MARK_INK_LIMIT, _is_red, _rgb
check("a warn tier sits below the blocking tier", _MARK_INK_LIMIT < _MARK_INK_BLOCK)
check("both sit above the confirmed-clear ceiling of 0.024", _MARK_INK_LIMIT > 0.024)
check("blocking stays at or below the confirmed unreadable case of 0.063",
      _MARK_INK_BLOCK <= 0.063)
check("the marker's own red is recognised", _is_red(*_rgb(0xCC1419)))
check("the student's dark ink is not", not _is_red(*_rgb(0x16191F)))
check("a black-and-white scan is not mistaken for red", not _is_red(*_rgb(0x808080)))

print("== the gate is wired into grading ==")
import inspect

import pipeline
src = inspect.getsource(pipeline.grade_sheet)
check("grade_sheet runs the pre-flight", "check_copy" in src)
check("and returns the issues to the caller", '"issues"' in src)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
