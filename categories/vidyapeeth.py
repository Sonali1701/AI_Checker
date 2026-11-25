"""PW Vidyapeeth test series (Radiant, Parishram and other batches).

Starts as the school category with its own name: the papers follow school boards,
and the differences (flatbed white scans, printed booklet templates, Questions PDF
plus a separate Hint and Solution PDF) are added one measured change at a time."""
from __future__ import annotations

from dataclasses import replace

from .base import DetectionRules, PromptPolicy, ReviewPolicy
from .school import SCHOOL

# MEASURED on Parishram Humanities Class 12, Infinity Test 10 (20 Sept 2026), against
# the faculty's own marks: the AI paid full marks for correct but plain answers that
# the faculty marked at 50 to 70 percent, asking for points explained with facts or
# examples, headings, an introduction and a conclusion. English 34 vs 29.5, Sociology
# 27.5 vs 19.5, History 40 vs 30, Political Science long answers +6, History short
# answers 3/3 where the faculty gave 0.5 to 2.5.
# RE-MEASURED 2026-10-10 (15 copies of the same test, build 97, four reviewers reading
# the faculty's per-question marks): totals were close (mean |diff| 2.3) but errors
# cancelled per question (Political Science and Sociology: 18.75 marks of per-question
# error behind 3.75 of net). Too generous on vague or named-only points in History,
# Political Science, Sociology and Hindi (Hindi +8: a 3-mark "what arguments" answer with
# none of the key's arguments got 3/3); too STRICT on correct point answers in Economics
# and Geography (-4.5: 0.25 cuts for "lacks detail" the faculty never take); map sites
# passed off their real position; remarks that never named what to add (the SMEs'
# "no points for improvement"). Rules 5 to 10 below are the faculty's own levels.
# Run 4 (these rules, no fixed steps): per-question error 56.5 (build 97: 56.25), 170 of
# 247 questions exact (163), bias +0.1, Rs 18.6 a copy (20.8). TRIED and dropped (run 5):
# a Sociology presentation step and a three-quarter floor for a Political Science
# sentence; bulleted Sociology answers still got 3.5 of 6 and per-question error rose to
# 67.5. One copy moves up to 4 marks between identical runs, so judge by many copies.
VIDYAPEETH_RULES = """\
1. Class 11 and 12 History, Political Science, Sociology and English literature
   answers are marked the way the Parishram faculty mark them. Full marks need every
   point the question asks for EXPLAINED with a fact or an example (a date, a name, a
   place, an event, a line from the text), written as points or under headings, and
   for answers of 4 marks or more a short introduction and a conclusion. A correct
   point that is only named, with no explanation or fact, earns half its share. An
   answer with only vague general statements earns about a quarter. The answer key is
   a MODEL answer: any valid, relevant point the student explains earns the same as
   the key's own point. Never take marks only because the student chose different
   valid points or examples than the key (MEASURED 10 Oct: Political Science 6 of 6
   answers marked 3 for "Radcliffe Line absent").
2. The Parishram faculty's own marks, to calibrate against:
   - 2 mark answers with a correct but unexplained point: 1 to 1.5.
   - 3 mark History answers with correct points but no hard historical facts: 2 to
     2.5. One vague point: 0.5. Three points that never explain the key term the
     question names (kinship, varna): 2.
   - 4 mark answers that list points without explaining them: 1.5 to 2.5. A correct
     but plain English literature answer: 2.5.
   - 5 and 6 mark answers: a correct but plain English answer 3.5 of 5. Political
     Science with every point explained 4.5 to 5 of 6. Sociology with no
     introduction, headings or conclusion 1.5 to 2.5 of 6.
   - An 8 mark History answer that described two movements without comparing them,
     when the question asked for the difference: 5. An 8 mark answer covering every
     required head with facts but a one-line conclusion: 7.
   Full marks are rare. They go only to a complete answer with every point explained
   and an example, and for long answers an introduction and a conclusion.
3. A second examiner raises a mark under these rules only for a point that IS on the
   page and that the first examiner called missing. Being correct is not a reason to
   raise a plain answer above the levels in rules 1 and 2. On a History or English
   paper the second examiner never raises a mark for depth, detail or completeness:
   it raises only a misread (a point written on the page that the first examiner said
   was missing, or a word or option read wrongly). MEASURED 10 Oct on 22 History and
   15 English copies: its raises put the totals 1.8 and 1.4 marks above the faculty.
4. What earns nothing, in every subject: a step whose own evidence says the point is
   missing, not explained or wrong (a bare label earns at most half); a line that
   restates the question; a "Hence ..." line repeating earlier points; a wrong fact or
   a wrong definition. Marks are whole or half marks, never quarters.
5. Political Science: a 2 mark "define and state two features" answer with a one-line
   definition and bare features gets 1; full marks need a sentence explaining each
   feature. A 4 mark answer that repeats the question and names general factors with
   none of the key's facts gets 1.5; the right content without subheadings gets 3.5.
   A 6 mark answer with an introduction and three headed reasons, each explained in two
   to four lines, gets 5 to 6 even when one reason differs from the key's: do not cut
   a good answer "for depth". Six valid one-line bullets get 4.5 of 6.
6. Sociology: every descriptive answer with no introduction, conclusion, headings or
   underlined key words is marked down. A 6 mark answer of five or six one-line
   bullets gets 1.5 to 2.5; a two-part question with one part missing (causes and
   consequences, contribution and comparison) is capped at 2.5 of 6. A 4 mark answer
   of about eight lines with no introduction or conclusion gets 1.5; covering only one
   direction of a two-way relationship gets 2. A 2 mark answer of one general sentence
   or two bare labels gets 1; four concrete examples get 2. "Name the reformer and the
   issue": a name with no issue earns nothing for that half.
7. Hindi: a 2 mark answer missing the key's core sentence gets 1.5; a 2 mark list
   (features and limitations) gets full marks for any valid items, not only the key's.
   A 3 mark "what arguments" answer with none of the key's arguments gets 0.5; a
   "difference, with example" (उदाहरण सहित) answer with wrong definitions and no
   example gets 1.5. A 4 mark analysis with three of four key points and quotes gets
   2.5; generic points with no example from the text get 1.5. A 6 mark creative piece
   on topic but far over the word limit, with crossings-out, gets 5.
8. English: a relevant, correct-direction but thin answer gets about 60 to 75
   percent: 1.5 of 2 on an extract part, 2.5 of 4, 3.5 of 5. The faculty cut for depth
   and presentation (headings, examples, a conclusion, "add more points"), not for a
   missing key word, and do not require direct quotes from the text.
9. Economics or Geography: these faculty pay correct, relevant point answers in full.
   Three headed points with one explaining line each get 3 of 3; one-line bullets that
   answer a 4 mark "features" or "reasons" question get full marks, and every valid
   reason counts, including ones not in the key. Do not take 0.25 or 0.5 for "lacks
   detail". Cut only for what is missing or wrong: a part of the question not
   answered (one of two groups only implied: 0.5 off), a numerical without the formula
   written out or a step the question asks for (correct answer with numbers only: 2 of
   4), contractionary measures for deficient demand (0). A valid instrument or example
   other than the key's earns the same (Reverse Repo or OMO for Repo).
10. Maps (History, Geography): each feature earns its mark only when it is marked at
   its real position on the printed outline map AND named. A rough position is
   accepted; the wrong region earns nothing even with the right name. A hand-drawn
   sketch map, or arrows pointing to state names, is capped at 3 of 5.
11. Every remark on a descriptive answer says what to add, the way the faculty write
   it: the key points that are missing, named ("add the 560 princely states and the
   refugee crisis"), the fact or example missing from a point, or the structure
   (introduction, conclusion, headings, underlined key words). A remark that only says
   "needs more depth" is not enough.
12. These rules do not apply to Class 9 or 10 papers, to MCQs, or to answers of 1 mark.
"""

# MEASURED against the faculty's own totals, Parishram Humanities Class 12 Infinity
# Test 10 (10 Oct 2026, 85 copies, final rules): copies compared, within 1 mark of
# the faculty total, within 2 marks, mean gap. Only the copies with no detected
# fault are released, and they were no more accurate than the flagged ones (2.38
# vs 2.37 mean gap), so this table, not the gate, decides what is safe to release.
VIDYAPEETH_CALIBRATION = {
    "Economics": (5, 4, 4, 1.30),
    "English": (17, 9, 12, 1.88),
    "Geography": (14, 8, 12, 1.18),
    "Hindi": (3, 1, 2, 2.06),
    "History": (25, 9, 11, 3.52),
    "Political Science": (18, 8, 11, 2.81),
    "Sociology": (3, 1, 1, 3.75),
}
# The release rule (12 Oct 2026): a subject's fault-free copies go to students
# without a faculty look once at least min_copies faculty-marked copies have been
# compared and `share` of them were within `tolerance` marks of the faculty total.
# Change the numbers here to change what is released; the table above is the data.
VIDYAPEETH_RELEASE_RULE = {"min_copies": 10, "tolerance": 1.0, "share": 0.8}


def calibrated_subjects(calibration=None, rule=None) -> frozenset[str]:
    """The subjects whose measured marking meets the release rule."""
    calibration = VIDYAPEETH_CALIBRATION if calibration is None else calibration
    rule = VIDYAPEETH_RELEASE_RULE if rule is None else rule
    out = set()
    for subject, (copies, within1, within2, _) in calibration.items():
        within = within1 if float(rule["tolerance"]) <= 1 else within2
        if copies >= int(rule["min_copies"]) and copies and within / copies >= float(rule["share"]):
            out.add(subject)
    return frozenset(out)


VIDYAPEETH = replace(
    SCHOOL,
    id="vidyapeeth",
    display_name="PW Vidyapeeth",
    ui_hint="Vidyapeeth test series. Upload the Questions PDF as the question paper and "
            "the Hint and Solution PDF as the answer key. A centre's whole stack can be "
            "one PDF: it is split at each cover page. Copies with no detected problem go "
            "to the student, and the rest go to faculty review with the reason.",
    # Batch names (Radiant, Parishram ...) are not in the class or subject fields, so
    # the operator picks this category.
    detection=DetectionRules(auto_detectable=False),
    prompt=replace(SCHOOL.prompt, extra_rules=VIDYAPEETH_RULES),
    # User's decision, 12 Oct 2026, replacing the 9 Oct "every copy to faculty review":
    # a whole centre stack (36 Class 8 Maths booklets) went to faculty review with no
    # fault on many of them. Copies the release gate finds no fault on go to students,
    # as for teaching papers; a copy with a detected problem (an unread page, an
    # unconfirmed MCQ, a roll number that cannot be read) still goes to faculty review.
    # The calibration table above stays as the measurement record.
    review=ReviewPolicy(
        auto_release=True,
        # Copies go straight to students, so an MCQ marked by judgement instead of
        # against a verified key letter holds the copy (preflight, 14 Oct 2026).
        # A mark beside another answer's row reads as that answer's mark.
        extra_hold_codes=frozenset({"mcq-without-key-letter", "mark-on-another-row"}),
        review_reason="Vidyapeeth copies go to faculty review: the AI marks are a first "
                      "draft until a sample matches faculty marks.",
        calibrated_subjects=calibrated_subjects()),
)
