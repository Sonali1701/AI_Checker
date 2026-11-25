"""Teacher recruitment exams (KVS, NVS, EMRS, DSSSB, state TET and CTET mains).

The papers are written subjective mocks for PRT, TGT and PGT posts: 4 mark questions
in Hindi and English, across language (letters, reports), pedagogy, maths and
subject knowledge. They come with NO answer key, so the tool writes a model answer
key from the question paper once per paper (teaching_key.py) and marks every copy
against it. Copies go to the students like any school copy (user's decision,
10 Oct 2026); the release checks still hold a copy whose marks cannot be verified.

Samples, 10 Oct 2026: KVS PRT Tier II Mock 01 (10 x 4 = 40, bilingual) and EMRS TGT
Maths Tier II Mock 01 (15 x 4 = 60)."""
from __future__ import annotations

from dataclasses import replace

from .base import AnswerKeyPolicy, DetectionRules
from .school import SCHOOL

TEACHING_RULES = """\
1. This is a teacher recruitment mains paper (KVS, NVS, EMRS, DSSSB, TET and
   similar). The candidates are graduates applying to teach. Mark the way the
   recruiting board's evaluators mark.
2. The answer key is a model answer written by an examiner, not an official key. Its
   steps fix how a question's marks are split. They do not limit what counts as
   correct: a different correct method, interpretation or point earns the step it
   fulfils: a step worded in one method's terms ("States D = 0") is earned by the
   same step of another valid method (alpha = beta with the sum and product of
   roots). In mathematics a correct final answer reached by a valid method earns
   full marks, and a slip loses only the steps it affects (error carried forward).
   An unclear or faint figure gets the benefit of the doubt when the working that
   follows is right for the correct value (x + 2 < 10 followed by x < 8). A method that
   differs from the key's is never a fault: "uses the product of roots instead of the
   discriminant" is not a reason to deduct (EMRS TGT Maths Q1, 21 Oct: a correct
   alpha = beta solution missing only k = -2 root 6 was paid 1 of 4).
3. Answers in Hindi or in English are equally valid, whatever language the question
   is printed in. Do not deduct for the choice of language, or for spelling and
   grammar, except in the expression step of a language question (letter, report,
   essay, or a question on the Hindi or English language).
4. Letters and reports: the format step is earned for the parts present (sender,
   date, receiver, subject, salutation, closing; for a report the heading, byline,
   date and place). Content steps are earned for each point the question asks about.
   A letter to the wrong person or about the wrong problem earns content marks only
   for what fits the question.
5. Pedagogy questions: credit points that show how a teacher would teach at the
   stated level, with a classroom example. A definition alone earns about half.
6. Say in each remark what was missing, so the candidate knows what to add.
7. DEPTH, on every 4 mark theory answer (this overrides the school rule that a brief
   correct answer earns full marks). The board's evaluators mark the content, not
   the structure: a point earns its full share only when it is EXPLAINED, with a
   reason, an example or the subject's own term, in a sentence or two. MEASURED
   2026-10-14 (KVS PRT Mock 01, six evaluators): the tool gave 4 of 4 to every
   theory answer; the evaluators gave "your explanation is good but very short, 3
   marks", "since the explanation is brief, 2 marks", and 2.5 of 4 to three one line
   points. So: three one line points for a 4 mark question earn 2 to 2.5; a correct
   but brief explanation earns 2 to 3; a definition with nothing added earns about
   2; full marks need every point the question asks for explained. A remark on such
   an answer says "add more points" or "explain each point", and names one point to add.
   Full marks on a theory answer are the exception, as they are for the board's
   evaluators: they need what a model candidate writes, every point explained with
   the subject's terms and an example, at the length the marks call for. A complete
   and correct answer that is plain or short earns 3 to 3.5 of 4. MEASURED 2026-10-21
   (the same copy re-marked under these rules): the evaluators gave no theory answer
   4 of 4 and 3.5 to their best ones; the tool still gave 4 of 4 to three of them.
8. Subject terms. When the key's step names the stages, terms or items the question
   asks for (ploughing, levelling and irrigation for the steps of farming; the parts
   of a letter), the step is earned in full only when those terms or their
   equivalents are on the page, written step wise where the question asks for steps.
   A general description without them earns at most half the step, and the remark
   names the missing terms.
9. Several aspects. A question that asks about more than one aspect (the physical,
   social and cultural importance of games) earns each aspect's share only when
   that aspect is explained. One aspect explained briefly with the others merely
   named is 2 to 2.5 of 4 (the evaluator's own mark), however correct the examples.
10. Mathematics. The final answer step is never earned by a wrong value. A wrong
   final answer that comes from misreading the question (a strict inequality taken
   as equality, so 41 instead of 42) also loses the step that applied the misreading:
   in a short numerical question that leaves the method marks only, 1 to 2 of 4
   (the evaluator gave 1 of 4 "only for step marking"). A clear, tidy solution is
   part of the answer: working that is hard to follow loses half a mark. At PRT level,
   where the candidate will teach the method, a correct solution is expected to explain
   it in words: a correct but brief solution earns 3 of 4 (KVS PRT Mock 01 Q5: "your
   explanation is good but very short, 3 marks").
11. Language answers (letters, reports, essays). A word limit printed in the
   question is part of the task: an answer far below it loses expression marks.
   Format faults ("Your faith fully" for "Yours faithfully", a missing byline) cost
   the format step, not the content.
12. A second examiner raises a mark under these rules only for a point that IS on
   the page and that the first examiner called missing. A brief answer stays brief:
   being correct is not a reason to raise it above the levels in rules 7 to 9.
13. Read the whole answer before calling a point missing: its continuation on the
   next page, its conclusion, and what is written in its diagrams, tables and labels
   ("3.4 Å" on a DNA diagram, a shape table under VSEPR, "Barauni pipeline" on a map).
   A remark that calls missing what is written anywhere in the answer is wrong, and so
   is the deduction. MEASURED 2026-10-22 (12 teaching copies reviewed against their
   pages): remarks called written points missing on 10 of the 12, most of them on a
   continuation page or in a diagram.
14. The key's examples are examples. A word in brackets in a step ("three stages",
   "Purusha sukta") is one way to earn it, not a requirement; a different correct point
   or quotation of the same kind earns the step. A step for something the question
   does not ask (limitations when only importance is asked, who named a law when the
   law is asked) is earned by any further relevant point the answer makes. A list
   question ("any four points") pays four explained valid points, whichever they are.
15. Facts are checked, in the text and in the diagrams: a wrong fact (a couplet given
   to the wrong poet, the heart's chambers or valves swapped in a labelled diagram, the
   series and parallel formulas interchanged, an answer about another topic) earns
   nothing for that point however fluent the answer, and the remark names the error.
16. The model key was written by an AI from the question paper and can be wrong. Where
   the answer gives a different account than the key of a named classification, law,
   model, committee or scheme, judge it against the standard textbooks of this exam's
   subject, not against the key. A standard, correct account earns its steps in full,
   and no remark calls it wrong or "an application, not the classification".
   MEASURED 2026-10-23 (UP LT Grade Home Science Q9): Mundel's five classes of change
   (body motions, work place and equipment, production sequence, finished product, raw
   material) were crossed out because the key gave classes of body motion, and the
   copy lost 3 of 8 marks.
"""

TEACHING = replace(
    SCHOOL,
    id="teaching",
    display_name="Teaching exams (KVS, NVS, EMRS, TET)",
    ui_hint="Teacher recruitment mains papers. No answer key is needed: the tool writes a "
            "model answer key from the question paper, saves it with the results and marks "
            "every copy against it.",
    # The class and subject fields ("PRT", "Mathematics") do not say teaching exam,
    # so the operator picks this category.
    detection=DetectionRules(auto_detectable=False),
    prompt=replace(SCHOOL.prompt, extra_rules=TEACHING_RULES),
    answer_key=AnswerKeyPolicy(may_generate=True),
)
