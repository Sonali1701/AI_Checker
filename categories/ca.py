# -*- coding: utf-8 -*-
"""CA / CMA / CS professional papers (ICAI style)."""
from __future__ import annotations

from .base import (Category, ChoicePolicy, CostPolicy, DetectionRules, ExaminerPolicy,
                   MarksReadingPolicy, PromptPolicy, RenderPolicy, ReviewPolicy)

# Matched against the combined class + subject text (the same list the grader has
# always used to switch on professional exam mode).
PROFESSIONAL_EXAM_HINTS = (
    "ca inter", "ca-inter", "ca found", "ca-found", "ca final", "ca-final",
    "icai", "icmai", "icsi", "chartered account", "cost accountan",
    "cma inter", "cma found", "cma final", "cs exec", "cs prof", "cs found",
    "cfa", "acca", "cpa",
)

# Faults measured on the live CA check of 6 Oct 2026 that the general professional
# rules did not stop. Kept short: the prompt is cached and shared by the batch.
CA_RULES = """1. Case based theory (Auditing, Strategic Management, Law): a point earns its mark only
   when it is explained AND tied to the facts of the case. A bare heading, or a list
   point that would fit any case, earns at most half its mark. Where the question asks
   for a conclusion or an opinion, a missing one costs that part. MEASURED: Audit
   copies paid full marks for headings with no application lost 8 and 15 marks
   against a careful examiner, and every SM theory part of an FM and SM copy was
   paid in full.
2. Which question an answer belongs to is decided by what it answers, compared with
   the question paper, not only by the label the student wrote or how OCR read it.
   "Ans 1(b)" is read as Q2(b) by OCR often enough that a heading alone never moves
   an answer to another question. A Part B descriptive answer is never an MCQ of
   Part A, and a question the paper does not have is never marked.
3. In an MCQ list, each answer belongs to the question number written beside it. A
   row left blank stays blank for that question: never move the next row's answer up
   to fill it.
4. Point wise answers: where the key gives marks per point ("0.5 marks each point"),
   one point the student writes earns at most that share, however many lines it
   takes. A point's name and its explanation or purpose line are ONE point and get
   one mark together, never one each. A line the key gives no mark for (a closing
   purpose sentence when the marks sit on the list items) earns nothing. When the
   student gives fewer points than the key, the score is the points given times the
   share, and the remark names the missing point. SME review (9 Oct 2026): Auditing
   Q6(c) paid 0.5 + 0.25 on one standard plus its purpose line, so 3 of 4 standards
   scored 1.75 instead of 1.5.
5. Taxation (Income tax and GST): a computed amount earns its step mark only when the
   amount itself is right, that is equal to the key's amount or to what a valid
   alternative method gives on the question's data. A right rate or method applied to
   a wrong figure earns nothing for that step: 30 percent standard deduction on a
   wrong net annual value, cess at 4 percent on a wrong tax, a gross salary that
   leaves out or adds an item. This overrides error carried forward for taxation.
   Put a cross on the wrong amount and quote both figures in the evidence. SME review
   (9 Oct 2026): three such amounts were ticked and paid on checked Taxation copies.
6. Figures come from the page image, not the OCR transcript. OCR misreads handwritten
   digits (37,520 as 31,520, 1,16,000 as 106000, 12,000 as 1250) and splits an amounts
   column away from its particulars. Before you deduct for a figure, or call a ledger or
   statement wrong or incomplete, read that figure on the page image. When the student's
   own totals agree with the key, a stray OCR figure is OCR noise, not an error. Never
   pair amounts with items by their order in the OCR. MEASURED (9 Oct 2026): correct
   Accounting and CMA answers lost 1 to 4 marks each to OCR misreads.
7. A step the key gives marks to is paid only for what that step asks. A criterion
   whose own evidence says the point is missing, not explained or wrong earns nothing
   for that point; a reason the question asks for ("with reasons") is part of the step.
8. One piece of writing answers ONE question. When the student has not numbered the
   answers, decide which question each answer belongs to by what it answers. The next
   page of an answer (its ledger continued, its balance carried down, its remaining
   points) belongs to that same answer and is never marked again as another question.
   A question none of the writing clearly answers is not attempted and earns 0, even
   when some text could loosely fit it; never guess an answer for a question. SME
   review (17 Oct 2026): an unnumbered Accounting copy had its Q2 machinery account
   continued on page 3 credited a second time as Q6 (7.5 marks, "Trading Account
   correct"), and on an Auditing copy Q4, which the student never wrote, was marked.
9. What earns nothing in theory (Auditing, Law, Strategic Management): a line that only
   names the topic or restates the question ("when law and regulations prescribe" for
   "when may the auditor depart from a requirement"), a point that is wrong, and a
   point that does not answer what the question asks. Do not give half marks for
   effort. The key's points carry their stated share (often 0.5 each); one point the
   student wrote is paid once, never twice under two steps. SME review (17 Oct 2026):
   three Auditing copies were over-marked on descriptive answers, a one-line
   restatement got 0.5, and one point was paid twice in Q6(c).
10. A figure that is wrong is crossed, never ticked, and its step earns nothing. This
   covers a figure DERIVED from a wrong one: income under the head Salaries taken from
   a wrong gross salary, a balance sheet total that does not agree, a working note
   whose result differs from the key. Where error carried forward is allowed (a later
   step applying the right method to the student's own wrong figure), the method step
   may earn its marks, but its evidence must say "carried forward" and it is never
   described as correct. SME review (17 Oct 2026): an incorrect gross salary, HEC,
   standard deduction, PPE working note and balance sheet total were ticked and paid.
11. Every page the student wrote on is accounted for: an answer's later pages are part
   of that answer, an extra attempt beyond the number the paper counts is marked "not
   evaluated" in its remark, and rough work is ignored. Never leave a written answer
   unmarked. SME review (17 Oct 2026): "some pages are not checked", "second last page
   is not checked, no marks, not even zero".
12. A question the student did not answer is returned attempted=false with no marks,
   and its remark is "Not attempted". It is never marked on another answer's writing:
   no cross, no 0 and no remark for it on that answer. On an "answer any N" paper a
   question you return as attempted takes one of the N places from an answer the
   student did write. An answer that IS written but wrong earns 0 with its reason. SME
   review (18 Oct 2026): Q3(a) and Q3(b), never written, were crossed on the student's
   "4 b)" answer and Q4 was dropped; an unwritten Q4(a) was zeroed on the first lines
   of "Answer 1(a)", and the written Q3(a) and Q3(b) on pages 6 and 7 were "not counted".
13. Feedback on an answer that lost marks says first what the student got right, then
   names exactly what is missing or wrong, then that marks were deducted for it. A
   step's reason is about that step only: never write "no valid characteristic is
   stated" or "no answer" for an answer whose other steps earned marks. The faculty's
   own example: "Several valid characteristics of negotiable instruments are correctly
   mentioned. However, the definition should clearly identify the three instruments
   under Section 13: promissory note, bill of exchange and cheque. Marks deducted for
   these omissions." A missing section number is never the only reason given: it costs
   no marks, so the remark names the substantive point that did. SME review (18 Oct
   2026): Q2(a) was given 5.5 of 7 with the remark "No valid characteristic is stated";
   Q3(b) lost 2.5 and Q1(b) lost 3 with only "Section 124 is not cited" and "Section
   88(4) is not cited" ("sufficient remarks are not mentioned").
14. A conclusion that contradicts the provision the student stated, or the key's
   conclusion, earns no conclusion marks even when the provision is right. Full marks
   need a correct conclusion and a complete explanation of every sub-part. SME review
   (18 Oct 2026): Q1(b) got 7 of 7 with perpetual succession explained correctly in (i)
   and then a conclusion that contradicts it, and an incomplete explanation in (ii).
15. The same page photographed twice is marked once: grade the first photo's lines and
   never cite or pay the repeated page. SME review (18 Oct 2026): "Q1a repeated so it
   should be marked as repeated".
16. Working the student struck out is cancelled and earns nothing, however correct: a
   line through it, a cross over it, or two diagonal strokes drawn across a block. Only
   the live attempt is marked; a question whose only attempt is struck out is not
   attempted. Review of FM copies (22 Oct 2026): a Q2 cost of equity and cost of
   preference shares struck through with two diagonal strokes were paid 2 marks.
17. A computation step is marked by what is right in it. A table or working that is
   correct earns its share of the step even when the last line goes wrong: a present
   value table with the right present values and a sign slip in the interpolation earns
   the table's share, never 0 for the whole step. A valid alternative method that reaches
   the key's figures earns the whole step (the bank loan found from FL = CL / OL and the
   interest equation, without writing EBT on its own line). Review of FM copies (22 Oct
   2026): such answers were given 0 of 4 and 0.5 of 1.
18. In a case study whose parts are lettered and whose options are numbered, the letter
   on a row is the part and the number is the option: "A = 3" and "(A) (3) 336000" are
   part A, option 3. Read every MCQ from the row that carries its own letter or number,
   never from a neighbouring row. Review of FM copies (22 Oct 2026): a Q3 written "(2)"
   was credited as the key's option from another row.
19. Deduct what each gap is worth, like a fair senior examiner. When an answer states the
   right provision in substance, applies it and reaches the right conclusion, a missing
   case name or citation, a wrong or missing section number, or a missing example costs
   at most 0.5 of a 5-mark answer (1 when the question itself asks for that case, section
   or example), never the whole step it sits in. Where the answer does ALL THREE of those
   things well, naming the case costs NOTHING: the name adds nothing the answer has not
   already shown, so do not take the 0.5 either. The 0.5 is for an answer that is right
   but thin. SME review (13 Nov 2026): a Business Laws Q2(b) explaining and applying the
   Doctrine of Indoor Management correctly was given 6.5 of 7 with "Turquand Rule is not
   expressly identified"; the faculty called that deduction unjustified and set it at 7
   of 7. Naming the rule and explaining the doctrine are the same knowledge. Where the key PRINTS marks against that
   case, section or example ("(2 Marks)" beside the case law), the printed marks stand: they
   are the paper setter's own split. A wrong conclusion costs the conclusion's
   share, a required part not written costs that part's share, and a wrong or irrelevant
   answer still earns nothing (rules 1, 9 and 14 stand). An answer's deduction is the sum
   of what each gap is worth, and the same gap is never taken twice. MEASURED on CS
   Executive JIGL copies (4 Nov 2026): answers the evaluator gave 4 or 5 of 5, noting only
   "case law reference missing", were given 2 to 3.5 of 5.
"""

CA = Category(
    id="ca",
    display_name="CA / CMA / CS",
    ui_hint="Professional papers. ICAI strictness, only the first answers written to an "
            "OR question count, totals shown as whole marks. Every copy goes to faculty "
            "review until the marking matches faculty marks.",
    pipeline="written",
    department="ca",
    legacy_exam_type="professional",
    detection=DetectionRules(class_hints=PROFESSIONAL_EXAM_HINTS, class_tokens=("ca", "cma")),
    prompt=PromptPolicy(exam_family="professional", force_commerce_conventions=True,
                        extra_rules=CA_RULES),
    # Professional papers print marks in prose ("(5 Marks)") and in answer-any
    # instructions, which the free readers misread; the AI reading is used instead.
    # A professional paper that prints no marks at all could not be graded: the reader
    # works the split out and then refuses it as "Unresolved printed allocation", so the
    # copy fails before any marking. MEASURED 14 Nov 2026: the CS Capital Market and
    # Securities Laws paper is 8 bare questions with an official total of 40 and no marks
    # tag anywhere; it was refused four times running and all 4 copies on it were lost.
    # Grading itself handles the paper, reading 8 rows of 5 for 40, so the convention
    # only has to say out loud what the paper already implies.
    marks=MarksReadingPolicy(try_regex=False, try_section_headers=False, unprinted_convention=(
        "CA, CMA and CS papers. When the paper prints no marks anywhere for its questions "
        "(no marks tag, no 'each question carries N marks', no section marks, no total "
        "beside a part), every top-level question carries an equal share of the official "
        "total: give each the same maximum, in whole or half marks as near equal as the "
        "total allows, adding up to the official total exactly. That IS this exam's "
        "allocation, so do not report it in allocation_issues. Where the paper sets a "
        "choice ('answer any four'), the shares are of the questions that COUNT, so the "
        "selected count times the share is the official total. Marks the paper prints "
        "anywhere always win over this rule, and so does a split the answer key prints.")),
    choice=ChoicePolicy(selection_policy="first_attempted"),
    examiner=ExaminerPolicy(always_moderate=True),
    # Live check of 6 Oct 2026: 15 copies across 8 subjects against a careful examiner,
    # only 5 of 14 within 3 marks, and five released copies 4.5 to 12 marks out. Until
    # a re-run matches faculty marks, the AI's marks are a first draft for faculty.
    # A missing section number never costs marks (SME ruling, 2026-09-23), so credit
    # for stating a provision without its number is not a reason to hold a copy.
    review=ReviewPolicy(
        auto_release=False,
        review_reason="CA copies go to faculty review: the AI marks are a first draft "
                      "until they match faculty marks.",
        warn_only_codes=frozenset({"citation-credited-not-given"}),
        # An MCQ's tick on another MCQ's row (Corporate Law, sheet rows F40 and F41:
        # "MCQs tick marking is misplaced"), named for the faculty as on commerce.
        extra_hold_codes=frozenset({"mark-on-another-row", "objective-answers-share-a-row"})),
    render=RenderPolicy(mark_scale=0.6, whole_total=True),
    cost=CostPolicy(budget_inr=18.0),
)
