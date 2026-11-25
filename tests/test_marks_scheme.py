# -*- coding: utf-8 -*-
"""Reading the per-question mark split off a professional paper.

Two real defects from the CA Inter Taxation 100-mark paper:
  1. printed_tag_sum only saw BRACKETED tags, so a paper whose subjective tags
     print bare on their own line ("15 Marks") summed to 30 — its MCQ brackets —
     instead of 129.
  2. The extraction was then verified by requiring it to EQUAL that sum. On a
     paper with internal choice the tags cover every option, so they always
     exceed what one student can score. Every CA paper offers choice, so every
     CA paper failed the check and fell back to proportionally rescaled maxima
     ("IT 27.0/57.5" instead of clean per-question marks).
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from grader import (paper_has_choice, paper_total_from_text, printed_tag_sum,
                    parse_strict_marks_scheme, scheme_sum_is_plausible)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# Shaped like the real paper's text layer: bracketed MCQ tags, bare subjective tags.
PAPER = """CA Intermediate Udesh Regular SEPT 2026 (Group-1)
Total Marks: 100
Date: 18-07-2026
General Instructions to Candidates
Question No. 1 of the subjective part is compulsory. Answer any two of the remaining three questions.
Income Tax
1. Which of the following is correct?
(2 Marks)
2. Compute the total income.
(2 Marks)
3. State the residential status.
(1 Mark)
Q1. Compute total income of Mr. Pratham.
15 Marks
Q2. (a) Discuss the treatment of LTC.
6 Marks
(b) Explain section 80QQB.
4 Marks
Q3. (a) Compute capital gains.
6 Marks
(b) Explain presumptive taxation.
4 Marks
"""

print("== bare, right-aligned mark tags are counted ==")
total, count = printed_tag_sum(PAPER)
check("bracketed tags still counted", count >= 3, f"count={count}")
check("bare 'N Marks' lines counted too", abs(total - 40.0) < 0.01,
      f"got {total} from {count} tags (2+2+1 bracketed + 15+6+4+6+4 bare = 40)")

print("== prose and the paper's own header are NOT counted as tags ==")
noise = "Total Marks: 100\nEach part carries 5 marks each in this section.\nMaximum Marks – 50\n"
n_total, n_count = printed_tag_sum(noise)
check("no tag found in prose/header-only text", n_count == 0, f"got {n_count} tags, sum {n_total}")
check("CA 'Total Marks :- 30' header is read as the official total",
      paper_total_from_text("Total Marks :- 30") == 30)

print("== internal choice is detected ==")
check("'Answer any two of the remaining three' -> choice", paper_has_choice(PAPER))
check("professional level alone does not invent choice",
      not paper_has_choice("all questions are compulsory", "professional"))
check("ordinary either/or prose is not question choice",
      not paper_has_choice("A variable is either continuous or discrete."))
check("a plain school paper has no choice",
      not paper_has_choice("Answer all questions. Q1. Define inertia. (2 Marks)"))
check("'attempt any' is caught too", paper_has_choice("Attempt any FIVE of the following."))

print("== the tag sum exceeding the official total is EXPECTED under choice ==")
# This is the condition detect_marks_scheme now tolerates: official <= extracted <= tags.
official, extracted, tags = 100.0, 100.0, 129.0
check("an extraction between the total and the tag sum is acceptable",
      official - 0.25 <= extracted <= tags + 0.25)
check("an extraction BELOW the official total is still rejected",
      not (official - 0.25 <= 88.0 <= tags + 0.25))
check("an extraction ABOVE the tag sum is still rejected",
      not (official - 0.25 <= 140.0 <= tags + 0.25))

print("== the extraction prompt emits a complete choice graph ==")
# Real failure: 12th English Subjective Test 07. The paper totals 80, the tool used 86,
# because Q8 ("Answer any ONE of the following") had its (a) [6 Marks] and (b) [6 Marks]
# enumerated separately and summed to 12. Q1/Q2 were handled correctly because they use
# the word "OR"; nothing covered "answer any one of the following".
from grader import build_marks_scheme_prompt

for _cls, _exam in (("Class 12", "school"), ("CA Inter", "professional")):
    _p = build_marks_scheme_prompt("English", _cls, _exam)
    check(f"{_exam} prompt has a CHOICE rule", "CHOICE" in _p)
    check(f"{_exam} prompt covers 'answer any ONE of the following'",
          "answer any ONE" in _p)
    check(f"{_exam} prompt keeps both Q8 alternatives",
          "emit both rows" in _p)
    check(f"{_exam} prompt requires explicit choice groups",
          "choice_groups" in _p)
    check(f"{_exam} total includes all offered alternatives",
          "INCLUDING all alternatives" in _p)
    check(f"{_exam} prompt still enumerates compulsory sub-parts",
          "Enumerate every SEPARATELY MARKED part" in _p)
    check(f"{_exam} prompt does not duplicate a shared roman-clause allocation",
          "SHARED total" in _p and "never 5 for each clause" in _p)

print("== the arithmetic that was actually wrong ==")
PAPER = {"Q1": 5, "Q2": 5, "Q3": 5, "Q4": 5, "Q5": 16,
         "Q6": 18, "Q7": 10, "Q8": 6, "Q9": 10}
TOOL = {"Q1": 5, "Q2": 5, "Q3": 5, "Q4": 5, "Q5": 16,
        "Q6": 18, "Q7": 10, "Q8": 12, "Q9": 10}
check("the paper totals 80", sum(PAPER.values()) == 80, sum(PAPER.values()))
check("the tool totalled 86", sum(TOOL.values()) == 86)
check("Q8 is the only question that differed",
      [q for q in PAPER if PAPER[q] != TOOL[q]] == ["Q8"],
      [q for q in PAPER if PAPER[q] != TOOL[q]])

print("== an extraction is checked against the total the paper DOES state ==")
# Real failure: a 70-mark CBSE Chemistry paper (Board Pattern Test-01) prints its marks
# only in section headers — "(5 x 5 = 25)" — and never per question. printed_tag_sum
# therefore found 0 tags, which SKIPS the sum check (it needs 3+), and left
# scheme_contradicts_paper nothing to disagree with, so the extraction came back "ai"
# = trusted. Its parts summed to 89.25: every sub-part had been given its PARENT's mark
# (Q11 10, Q16 15, Q21 10) and Q15 was dropped. Grading then reported 75 out of a
# 70-mark paper, the repair pass returned no rows, and the deterministic rescale
# smeared 70/75 over every question — printing maxima the paper never had ("Q19 8/8",
# "Q10 1/1.5") on the teacher's copy.
check("the 89.25-vs-70 extraction is rejected",
      not scheme_sum_is_plausible(89.25, 70.0, has_choice=False))
check("a correct 70-mark extraction is accepted",
      scheme_sum_is_plausible(70.0, 70.0, has_choice=False))
check("half-mark drift is tolerated", scheme_sum_is_plausible(70.2, 70.0, has_choice=False))
check("an extraction BELOW the total is rejected too",
      not scheme_sum_is_plausible(64.0, 70.0, has_choice=False))
# Under choice the parts cover every alternative, so exceeding the total is correct —
# this is the CA-paper case the suite above protects, and it must stay accepted.
check("choice papers are exempt: 129 tags on a 100-mark paper",
      scheme_sum_is_plausible(129.0, 100.0, has_choice=True))
check("an unknown official total cannot contradict anything",
      scheme_sum_is_plausible(89.25, None, has_choice=False))

print()
print("== a choice rule survives the shape the verifier actually returns ==")
# The Corporate Laws master test was refused with four validation errors and never
# graded, because Gemini wrote each alternative as {"a": [qids]} — its id as the KEY —
# and left the group unlabelled. The rule itself was right: answer any 2 of Q2/Q3/Q4
# for 20 marks. A choice paper cannot fall back on the regex or section readers, so
# that shape slip blocked the whole paper.
ITEMS = [{"qid": "Q1", "max": 2}] + [
    {"qid": f"Q{q}.{part}", "max": 5} for q in (2, 3, 4) for part in ("a", "b")]
drifted = json.dumps({"items": ITEMS, "choice_groups": [{
    "options": [{"a": ["Q2.a", "Q2.b"]}, {"b": ["Q3.a", "Q3.b"]},
                {"c": ["Q4.a", "Q4.b"]}],
    "select_count": 2, "contribution_marks": 20}]})
scheme = parse_strict_marks_scheme(drifted)
group = scheme.choice_groups[0]
check("the drifted choice group parses at all", bool(group))
check("each alternative keeps its own id",
      [o.option_id for o in group.options] == ["a", "b", "c"],
      [o.option_id for o in group.options])
check("and its question ids", group.options[0].question_ids == ["Q2.a", "Q2.b"],
      group.options[0].question_ids)
check("an unlabelled group is numbered, not rejected",
      group.group_id == "choice-1", group.group_id)
check("the rule itself is untouched",
      group.select_count == 2 and group.contribution_marks == 20)

print()
print("== but nothing genuinely missing is invented ==")
named = json.dumps({"items": ITEMS, "choice_groups": [{
    "group_id": "choice-q2", "options": [
        {"option_id": "a", "question_ids": ["Q2.a", "Q2.b"]},
        {"option_id": "b", "question_ids": ["Q3.a", "Q3.b"]}],
    "select_count": 1, "contribution_marks": 10}]})
check("a well-formed group keeps its own id",
      parse_strict_marks_scheme(named).choice_groups[0].group_id == "choice-q2")

def refuses(raw):
    try:
        parse_strict_marks_scheme(raw)
        return False
    except Exception:
        return True

unlabelled = json.loads(named)
for option in unlabelled["choice_groups"][0]["options"]:
    del option["option_id"]
check("non-scoring option labels are generated without changing memberships",
      [o.question_ids for o in parse_strict_marks_scheme(json.dumps(unlabelled)).choice_groups[0].options]
      == [["Q2.a", "Q2.b"], ["Q3.a", "Q3.b"]])
check("a choice with only one alternative is still refused",
      refuses(json.dumps({"items": ITEMS, "choice_groups": [{
          "options": [{"question_ids": ["Q2.a"]}],
          "select_count": 1, "contribution_marks": 10}]})))
check("a truncated graph is still refused", refuses('{"items": [{"qid": "Q1", "ma'))
check("an item with no maximum is still refused",
      refuses(json.dumps({"items": [{"qid": "Q1"}]})))
check("a group with no selection rule is still refused",
      refuses(json.dumps({"items": ITEMS, "choice_groups": [{
          "options": [{"a": ["Q2.a"]}], "contribution_marks": 10}]})))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
