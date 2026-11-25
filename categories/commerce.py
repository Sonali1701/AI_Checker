# -*- coding: utf-8 -*-
"""Commerce stream, Class 11 and 12 (Accountancy, Business Studies, Economics ...)."""
from __future__ import annotations

import re

from .base import (Category, ChoicePolicy, CostPolicy, DetectionRules, ExaminerPolicy,
                   PromptPolicy, RenderPolicy, ReviewPolicy)

# Subjects that make a Class 11 or 12 paper a commerce paper (the costing table's list,
# the broadest of the app's older lists).
COMMERCE_SUBJECT_HINTS = (
    "account", "audit", "banking", "business", "commerce", "corporate",
    "cost", "econom", "entrepreneur", "finance", "financial", "gst",
    "insurance", "law", "management", "marketing", "secretarial", "tax",
)

# Commerce SME review of the AI copies, 5 Oct 2026. These refine the commerce
# conventions in the grading prompt for Class 11 and 12 papers only; CA keeps its own.
# Rule 4's lists, proofs and crossed-out blocks and rule 6 come from the SME review of
# the state board copies on 29 Oct 2026 (sheet rows 14, 71 and 78).
COMMERCE_RULES = """1. Economics and Business Studies theory: a correct point earns its full mark even
   when it is written in one line. Check only the FIRST N live points in the order the
   student wrote them when the question asks for N points. Include wrong points in
   that first N; never replace one with a later correct point. Extra points earn no
   marks and are not checked. If those first N points are correct, award full marks.
   Never deduct solely because a correct point is brief. This overrides
   "too thin to earn its mark" for these two subjects. MEASURED: an Economics answer
   with three correct points for each of agriculture and industry got 2/3 for "lacks
   explanation", and the SME's ruling was 3/3. NOT on a Bihar, UP, MP or Maharashtra
   board paper: there the BOARD MARKING block given with the question paper replaces
   this rule.
2. Accountancy format: an account or statement that is not drawn in its proper format
   earns NOTHING for that account, even when some figures in it are right. Not in
   format means items on the wrong side, entries written outside the columns, or one
   account broken into separate pieces with their own totals (half on one page and
   half on another). An account continued on the next page with its totals carried
   forward IS in format. One right figure in the wrong place inside a properly drawn
   account is still rule 9 of the commerce conventions, at most half that step. Say in
   the remark what the format should be. MEASURED: a Capital Account with balances on
   the wrong side, two sets of totals and split across two pages got 2/6, and the
   SME's ruling was no marks.
3. Business Studies and Economics point order (also Organisation of Commerce and
   Management / OCM and Secretarial Practice / SP): the first N rule is WITHIN one
   answer only. It does not change the best-answer rule for OR questions. Preserve
   one criterion for each of the first N points, including wrong or missing points,
   with its official weight and its own source anchor. A later point cannot fill an
   earlier wrong point's scoring slot. Read the original page to establish order and
   ignore cancelled points. If the required count or order is uncertain, report that
   uncertainty for verification rather than choose the best points.
4. Commerce Mathematics, Core Mathematics and Applied Mathematics: read handwritten
   plus/minus signs, powers, subscripts, inequalities, inverse and matrix signs from
   the original page images. Reconstruct and recompute the exact live expression.
   The final correction must give the verified corrected expression or result, not
   just a generic calculation remark. An unreadable symbol is uncertain, not guessed.
   A correct numerical part worth at most one mark earns its full mark unless the
   question asks to show, prove or derive working; missing working or a brief answer
   alone never costs half a mark. Cancelled attempts earn nothing, whether struck
   line by line or crossed out as a block with one or two diagonal strokes; such a
   block is never ticked. Read the live rewrite and assess its surviving steps
   independently; do not invent method credit for absent working.
   A step that is a list, table or matrix of values (minors, cofactors, an adjoint,
   a table of values) is checked entry by entry against the key: it earns its mark
   only when every entry is right, and at most half with one entry wrong. Cross the
   wrong entry and give its right value in the remark ("C13 is -5 and C21 is -1.").
   A proof in parts (one-one and onto; reflexive, symmetric and transitive) gives
   each part its own share, and a part carried to the step that decides it earns
   that share without a closing sentence. A part shown NOT to hold needs a counter
   example from the given set (two pairs of the relation); those pairs count wherever
   the student wrote them in the answer, and letters that only restate the definition
   earn nothing for that part. MEASURED: MP Board Core Math Q11, cofactors with C13
   and C21 wrong, was ticked for full marks where the faculty crossed each wrong
   entry; Q8 transitive, its pairs written below the conclusion, faculty 2/2; Online
   Core Math Q15, the one part done earns its own share, 1 mark, not 0.5.
5. A word that changes the meaning of an accounting or commerce term makes the answer
   wrong; it is not a spelling slip. Liabilities are amounts the business OWES, so
   "liabilities are amounts owned to outsiders" defines assets. Debit for credit,
   payable for receivable, asset for liability, income for expense or gain for loss
   turn the answer around the same way. Such a definition or point earns nothing,
   and the remark names the right word ("Liabilities are amounts owed, not owned.").
   A misspelling that keeps the meaning ("liabilites") costs nothing. MEASURED: Bihar
   Board Accountancy Q9, liabilities defined as amounts "owned" to outsiders, was
   given full marks, and the faculty's ruling was that the answer is wrong.
6. Accountancy and Book Keeping: an account, final accounts or statement worth 6
   marks or more is never marked as one criterion per account. Each item or
   adjustment that carries weight is its own half or one mark criterion (opening
   stock, purchases less returns, sales less returns, each adjustment, each capital
   balance), ticked or crossed on its own line. Gross Profit, Net Profit and the
   Balance Sheet total are criteria of their own, earned only when they equal the
   key's. A figure copied wrongly from the question, or an adjustment left out, is a
   wrong figure, not a slip carried forward; an amount the question never gives
   (cash put in to make it balance) earns nothing. A crossed item's remark gives the
   right figure ("Opening stock is 89,500."), and the question's remark names the
   errors that cost the most. MEASURED: Maharashtra HSC Book Keeping Q11 (15 marks),
   opening stock miscopied, neither return deducted, the old RDD left in and cash
   invented, got 8/15 from three 5-mark criteria with one tick per account; the
   faculty gave 7/15 and the SME found a 7.5/15 copy still lenient, with no step
   marking and no remark naming the errors.
"""

# Bihar Board (BSEB) faculty, Uday Class 11 Commerce Test-01 (sheet rows 26 to 41,
# 10 Oct 2026). They want points explained where the CBSE SME of rule 1 credits a
# brief point, so these rules go out only with a paper that names Bihar Board
# (board_rules), never in the shared prompt.
BIHAR_BOARD_RULES = """=== BIHAR BOARD MARKING (this question paper names Bihar Board) ===
Bihar Board faculty want answers explained, not listed. For this paper these rules
replace commerce rule 1, and both examiners apply them.
1. Economics and Business Studies theory: when a question asks ONLY for points ("write
   any three characteristics", "state the features"), each point needs a line or two
   explaining what it means or why it holds. A point that only names the
   characteristic earns HALF its share, even written as a full sentence ("Statistics
   are aggregates of facts." "They are numerically expressed." is three named points,
   1.5/3); the same point with its explanation earns the full share. A longer sentence
   is not an explanation: "Statistics are collected systematically for a definite
   purpose" still only names the characteristic and earns half, so three such points
   are 1.5/3, never 2/3. Judge every point of the list the same way. When the points
   are one part of a question that also asks for a meaning or definition ("What is
   classification? Write three objectives"), each correctly stated point earns its FULL
   share without explanation ("To simplify the data" earns its mark). A "describe" or "explain" answer made only of
   one-line points earns two thirds of its marks. "Distinguish between" earns full
   marks with at least three bases of difference; two bases earn HALF the marks
   (1.5 of 3) however well they are written. A diagram
   drawn in the right form with its values labelled earns full marks: never deduct for
   a bar a little off its scale or a label cut off at the edge of the photo.
2. Definitions, any subject: the right meaning in the student's own words earns the
   full mark; never deduct for a missing textbook phrase. A definition that gives only
   part of the idea (liabilities as only the owner's claim, leaving out outsiders')
   earns half.
3. Accountancy:
   a. An accounting equation, journal, ledger or statement is marked on its working
      for each transaction. Final totals with no transaction-wise working earn ZERO
      for the whole question: no format, presentation or final-answer step either,
      whatever the rubric's steps say. The faculty gave 0/4 to exactly that answer.
   b. Every journal entry needs its narration; an entry without one loses half its
      share. GST entries show CGST and SGST and, where the question gives it, their
      payment.
   c. "Give examples": each example names the transaction and shows its effect
      ("Started business with cash Rs 1,00,000: Assets 1,00,000 = Liabilities 0 +
      Capital 1,00,000"). A correct equation with figures but no transaction named
      earns HALF its share; an example with a wrong effect earns nothing.
4. English: literature answers are marked on content. Never deduct for spelling or
   grammar slips when the content is right (the copy lists them for the student
   separately). This is for English only: in Accountancy a word that changes the
   meaning is a wrong answer (commerce rule 5). A 2-mark answer that explains lines, a
   phrase or an event earns 1 mark for the point and 1 for the explanation. The
   explanation mark needs ALL of: the answer does not open with the pronoun "It",
   "This", "That" or "They" standing for its subject (so "This is from Animals in
   Prison by Nehru ..." and "It is from Shakespeare's Sonnet 116 ..." lose it even
   when the rest is right; the faculty write "Do not start your answer with It, That,
   They, This; always begin with a name"; any other opening word, "In this stanza"
   or "The poet", is fine); every line of a quoted extract or stanza is explained,
   not only retold or set in context; and the exact reason the question asks for is
   given. Missing any of these: 1 of 2, never 1.5. Say which in the remark.
5. Remarks speak to the student: never name these rules, the board, "two thirds" or
   any other marking rule. Say what to write instead: "Explain each point in one or
   two lines." "Add the meaning of each line of the stanza."
MEASURED, faculty against tool on this test: three correct bare characteristics of
Statistics 1.5/3 (again 1.5/3 on 21 Oct where the tool gave 2/3) and 1/3 against 3/3 ("explain these points in 1 to 2 lines"); four
one-line points of importance 2/3 against 3/3 ("elaborate them in a few lines"); an
accounting equation written as final totals only 0/4 against 3.5/4; a partly right
definition of liabilities 1/2 against 0/2; English explanations 1/2 against 2/2 ("you
didn't explain the meaning of all the lines", "do not start your answer with It or
This") and 2/2 against 1.5/2 taken for spelling.
"""

# UP Board, MP Board and Maharashtra HSC faculty, Uday / Parishram Class 11 and 12
# Commerce tests (sheet rows 42 to 101, checked 21 and 22 Oct 2026). Like Bihar's
# faculty they want points explained: the tool, on CBSE rule 1, was 2.4 marks above
# them on average in Economics and Business Studies, and its released copies were
# up to 6.5 marks over. Their English is not Bihar's, so Bihar's English rule stays
# Bihar's. Rules 10 to 13 and the 5-mark depth in rule 1 come from the subject SMEs'
# review of those copies (29 Oct 2026, rows 47, 52 to 56, 90, 92 and 101).
STATE_BOARD_RULES = """=== STATE BOARD MARKING (this question paper names {board}) ===
These faculty want answers explained, not listed. For this paper these rules replace
commerce rule 1, and both examiners apply them.
1. Economics, Business Studies, OCM and SP theory: when a question asks for points
   ("state four causes", "write three features", "describe the importance"), each
   point needs a line or two explaining what it means or why it holds. A point that
   only names its heading earns HALF its share, even written as a full sentence: five
   bare headings for a 5-mark question are 2.5/5, never 4.5/5. Points run together
   in one sentence ("helps in decisions, comparison and policy") earn one third of
   the marks. When the points are one part of a question that also asks for a meaning
   or definition, each correctly stated point earns its full share without explanation.
   In an answer of 5 marks or more a point earns its full share only when it is
   explained in two lines or more (what it is, and how or why it matters). Points
   given in one line each, or as examples only ("Natural causes: flood, earthquake,
   drought"), are not explained: such an answer loses 1 to 1.5 marks in all (four or
   five such points: 3.5/5), taken as half marks on the thinnest points.
2. A point must answer what this question asks. Features given for causes, the
   definition of another term, or the text of a different answer earn nothing here,
   however correct they are elsewhere.
3. "Distinguish between": full marks need at least three bases of difference; two
   bases earn HALF; two definitions with no basis of difference earn one third.
4. Diagrams, graphs, schedules and tables: when the question or the key's answer asks
   for one, it carries its share of the marks. Full marks need the right kind (a PPC
   is not a TU/MU graph; a histogram's bars touch), both axes drawn and named with
   what they measure, and the question's values plotted to scale. Missing axes or
   axis names: one third of that share at most. The wrong kind earns nothing for it,
   and an answer that leaves out a schedule or diagram the key gives loses that share
   ("Explain the law with a schedule and diagram" answered in words only: half).
   Never deduct for a bar slightly off scale or a label cut by the photo's edge.
5. "Explain the concept / principle" for 2 marks or more: the meaning AND an example
   or its effect. The meaning alone earns half.
6. A one-word answer, fill-in or MCQ option earns its mark only when it means what the
   key gives: "Preference order" for "ordering of preferences" is right; "Privatisation"
   for "Disinvestment" is a different idea and earns nothing.
7. Accountancy: working that matches the key's own steps earns them, however briefly
   it is written. Never deduct a step for a figure the key itself writes the same way
   ("3/9 - 4/9 = 1/9 gain"), for not showing how a correct total was added up, or for a
   method the key does not insist on when it reaches the right figure.
8. English: the right meaning in other words earns the mark; never deduct for a word
   that means the same as the key's word ("sudden" for "abrupt").
9. Remarks speak to the student: never name these rules, the board or a fraction.
   Name the fault, then say what to write instead: "Explain each point in one or two
   lines." "Name both axes and draw the bars to scale." "Give three differences."
   An instruction with no reason ("Quote the words.") is not a remark.
10. Economics, Business Studies, OCM and SP theory: subject words written wrongly
   (टेलीपोन for टेलीफोन, विस्वसनीय for विश्वसनीय) cost half a mark in an answer with
   two or three such words and one mark with four or more, never more than a third
   of the answer. Never in a one-word answer, an MCQ or an Accountancy figure; a word
   that changes the meaning is commerce rule 5, not this. The remark gives the right
   spellings. For this paper this replaces "never deduct for spelling".
11. English long answers (paragraph, article, letter, appreciation, character
   sketch): mark the key's points, the printed word limit and the conclusion. This
   replaces "word limits are guidance". An answer under two thirds of the printed
   limit loses one mark of 5 to 7 (half a mark of 3 or 4); one with no concluding
   line loses half a mark; each key point left out loses its share. Spelling and
   grammar never cost content marks and are never the reason given for losing them.
   The remark names the fault first: "Below the word limit of 80 words. Add harmony
   with nature." "The conclusion is missing." "Use the key words of the question."
12. English activities (completion, web, table, match the columns, vocabulary):
   a. A completion or one-line answer needs every idea of the key's answer. One of
      two ideas earns half: "the lift was fined because he threw a passenger out",
      with "for not saying Please" left out, and "lift" is not the lift-man.
   b. A web, chart or table: each box earns its equal share only when it says what a
      box of the key says. Read the key's web from its image. One right box of three
      on a 2-mark web is 0.5.
   c. "Match the words with the suffixes and write the new words": the matching and
      the new words are two halves, not full or nothing. Suffixes copied in the
      printed order, so no word is matched with its own suffix, lose the matching
      half even when the new words are right (1 of 2); a misspelt new word
      ("voilence") is wrong.
   d. "Match the columns" is marked on the letter written against each item: a right
      name with the wrong letter ("d) O. Henry" where the key gives a) is a wrong pair.
   e. Each activity that lost marks gets its own remark naming the wrong box, pair or
      missing idea.
13. Mathematics "Activity" questions print a partly worked solution with boxes to
   fill. The answer must write out the activity with each box filled in; box values
   written alone, without the activity's statements, earn nothing even when every
   value is right. The remark says "Write out the whole activity with each box filled."
MEASURED, faculty against tool: UP Economics "describe the importance" in one line
1/3 against 3/3, "distinguish" by two definitions 1/3 against 3/3, a bar diagram with
no axes 1/3 against 3/3; MP Business Studies "don't write only keywords" 14 against
19.5; UP Business Studies five bare factor headings 2/5 against 4.5/5; MH Economics a
law explained with no schedule or diagram 4/8 against 8/8.
MEASURED, SME review of the corrected copies (29 Oct 2026): UP Business Studies Q12
and Q13, causes given as headings with examples and factors in one line each, 5/5
where the SME wanted 3.5/5; UP Economics, four right differences with four words
misspelt, faculty and SME 2/3; UP English Q4(A), about 55 words for 80, faculty 5/7
where the tool gave 6.5/7 and blamed spelling, and remarks that said only "quote the
words"; Maharashtra HSC English, a suffix table copied in printed order 1 of 2 where
the copies gave 2, a web with one right box 0.5 where the tool gave 2, an O. Henry
pair under the wrong letter 1.5 of 2; Maharashtra HSC Maths Q7(i) and (ii), box values
alone, faculty 0/4 and 0/4 where the copy gave 4/4 and 2/4.
"""

# A paper names its board in its own header or footer ("Uday Bihar Board 11th Commerce
# 2027" on every page). The full name, or BSEB, never the state alone: a CBSE
# Geography or History paper can mention Bihar.
_BIHAR_BOARD_RE = re.compile(
    r"(?i)\bbihar\s+(?:school\s+examination\s+)?board\b|\bBSEB\b")
# "Uday UP Board 11th Commerce 2027", "Parishram Pro MP Board 12th Commerce",
# "Parishram Maharashtra HSC Commerce 2027", "UDAY PRO Maharashtra Board 11th".
_STATE_BOARD_RES = (
    ("UP Board", re.compile(r"(?i)\bU\.?\s?P\.?\s+Board\b|\bUPMSP\b")),
    ("MP Board", re.compile(r"(?i)\bM\.?\s?P\.?\s+Board\b|\bMPBSE\b")),
    ("Maharashtra HSC", re.compile(
        r"(?i)\bMaharashtra\s+(?:HSC|SSC|State\s+Board|Board)\b|\bMSBSHSE\b")),
)


def paper_board(paper_text: str) -> str:
    """The state board a question paper names ("bihar", "UP Board", "MP Board",
    "Maharashtra HSC"), or "" for any other paper."""
    text = paper_text or ""
    if _BIHAR_BOARD_RE.search(text):
        return "bihar"
    return next((name for name, pattern in _STATE_BOARD_RES if pattern.search(text)), "")


def board_rules(paper_text: str) -> str:
    """The board's own marking rules to send with this paper, or ""."""
    board = paper_board(paper_text)
    if board == "bihar":
        return BIHAR_BOARD_RULES
    return STATE_BOARD_RULES.format(board=board) if board else ""


COMMERCE = Category(
    id="commerce",
    display_name="Commerce (Class 11 and 12)",
    ui_hint="Accountancy, Business Studies, Economics and other commerce papers. A "
            "second examiner reviews every copy.",
    pipeline="written",
    department="commerce",
    legacy_exam_type="school",
    detection=DetectionRules(subject_hints=COMMERCE_SUBJECT_HINTS,
                             levels=("11", "12", "xi", "xii")),
    prompt=PromptPolicy(extra_rules=COMMERCE_RULES),
    choice=ChoicePolicy(selection_policy="best_score"),
    examiner=ExaminerPolicy(always_moderate=True),
    review=ReviewPolicy(extra_hold_codes=frozenset({
        "printed-award-mismatch", "step-award-mismatch", "award-circle-missing",
        "margin-score-not-marked", "step-awards-do-not-reconcile",
        "mark-away-from-evidence", "mark-outside-answer", "mark-outside-sheet",
        "question-total-not-marked", "duplicate-sheet-total", "required-mark-missing",
        "answer-not-marked", "invisible-mark", "mark-collision",
        "verdict-not-visible", "question-not-shown",
        "mark-overlaps-writing", "feedback-outside-right-margin",
        "render-certificate-mismatch", "commerce-point-order-unverified",
        "no-reason", "remark-not-actionable", "feedback-overlap",
        # An MCQ's mark on another answer's line (UP and MP Board copies released on
        # 17 and 21 Oct 2026 with a cross on the student's right answer).
        "mark-on-another-row", "objective-answers-share-a-row",
    })),
    render=RenderPolicy(mark_scale=0.8),
    cost=CostPolicy(budget_inr=18.0),
)
