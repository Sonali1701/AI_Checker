# -*- coding: utf-8 -*-
"""The Gurukulam School (PW Gurukul): diagnostic tests, Grades 1 to 8.

A diagnostic test (DT) is a short chapter test, 20 marks in 30 minutes, that the class
teacher sets to find what each child has and has not learnt. The teacher's Word file
holds the test and its answer key one after the other ("DT & AK"); the children write
their answers in ruled notebooks, mostly without copying the questions, and the
notebook pages are scanned one PDF per child. What is new for this category:

- answers are found by the child's own numbering, which starts again at 1 in every
  section ("Sec-B 1." is Section B question 1);
- the language tests (English and Hindi grammar) test a word form, so the form must
  be exactly right, while capitals, full stops and slips elsewhere cost nothing;
- the teacher's key is followed to its own marking scheme (the half marks of the
  Hindi key), and a question the key leaves without an answer is marked from the
  paper and named for the teacher;
- remarks are read by a child and the parents: short, simple and kind.

Samples (20 Nov 2026, Drive folder from the Gurukul team): English Grade 5 Nouns,
Number (13 copies), Hindi Grade 6 (10), Science Grade 7 Measurement of Time and
Motion (10), Social Science Grade 8 The Parliamentary System (10) and one 20 MB stack
of Grade 6 Mathematics Integers copies. Every paper is 20 marks.
"""
from __future__ import annotations

from dataclasses import replace

from .base import AnswerKeyPolicy, DetectionRules, ReviewPolicy
from .school import SCHOOL

GURUKUL_RULES = """\
1. This is a diagnostic test of The Gurukulam School (PW Gurukul), Grades 1 to 8: a
   short chapter test, usually 20 marks in 30 minutes, set to find what each child has
   learnt. The child writes the answers in a ruled notebook, usually without copying
   the questions. Mark it the way the class teacher does, against the answer key and
   its marking scheme.
2. Which question an answer belongs to: the child numbers the answers ("Ans 1",
   "Q4 ->", "1.", "प्र.3") under the section headings they copy ("Sec-B", "Section C",
   "B.", "MCQ"). The numbering usually starts again at 1 in every section, so "2."
   under "Sec-C" is the second question of Section C, never question 2 of Section A.
   When a heading is missing, or the paper prints the same section name twice, match
   the answers to the questions by their content, in the paper's order. A heading
   the child copied ("Sec-B (5 marks)", "Answer the following") is not an answer.
3. One-mark items (choose the option, fill in the blank, write the plural, one word,
   true or false, arrange in order) are right or wrong: 1 mark or 0, no working
   needed, never half.
4. Language tests (English, Hindi). When the item tests a word form (a plural, a
   gender, a tense, a भाववाचक संज्ञा, a पर्यायवाची, a जातिवाचक or व्यक्तिवाचक संज्ञा),
   that form must be exactly right: "babys", "leafs", "tomatos", "mens", "fishs" or
   "sheeps" earns nothing for its item, and so does the unchanged word. A capital
   letter, a missing full stop or a slip in a word the item does not test costs
   nothing. When the task is to rewrite a sentence correcting a word, the mark is for
   the corrected word: the sentence with the right word earns it, the sentence with
   the word still wrong earns nothing. Read that word on the page image, letter by
   letter, before taking the mark away: a child's handwriting is easily read one
   letter wrong (p as n, so "sheep" reads as "sheen"; m as rn; a as o; u as v). When
   the written word can be the key's word, it is the key's word. A wrong ending the
   child clearly wrote ("sheeps", "fishs", "babys") is a real mistake, not a
   misreading.
5. Follow the key's own marking scheme exactly: the "Note:", the "अंक-विभाजन" and
   the marks printed beside each part give each element its share, and half marks
   are used only where the scheme gives them ("प्रत्येक सही शब्द के लिए ½ अंक",
   "सही अर्थ ½ अंक"). Where the key accepts any two or any one of a list, any correct
   ones the child writes earn the share, and a correct point in the child's own words
   earns the same as the key's wording.
6. Short answers (2 and 3 marks, 30 to 60 words) are marked on the ideas, not on the
   length or polish: each point the question or the scheme asks for earns its share
   when it is correct, and a point that is wrong or missing loses its share. Every
   thing the question asks for is a point of its own: "What is meant by rest and
   motion? Give examples" asks for both meanings AND an example of each, so the
   meanings alone cannot earn full marks. A part that asks for a judgement and its
   reason ("Is this correct? Explain with calculations", "Justify") has two halves,
   the judgement and the reason or calculation. A letter or paragraph is marked on the scheme's own heads (format,
   content, language and word limit).
7. A question the key gives no answer for, or says cannot be determined: work the
   answer out from the question paper and mark against it when you are sure of it.
   When you cannot be sure (a distance to be measured off a printed picture, a
   picture the paper does not show), give the mark to any answer the child attempted.
   When such a question loses marks, end its remark with "[teacher to check: the key
   gives no answer]".
8. The key was typed by the teacher. When its option letter and option text point to
   different options, or its answer contradicts the question's own data, mark against
   the answer that is correct for the question and end the remark with "[teacher to
   check: the key says ...]". Otherwise the key decides.
9. Remarks are read by a child of Grades 1 to 8 and the parents. Write one short,
   simple and kind sentence in the language of the paper (Hindi for a Hindi paper).
   For a wrong one-mark item give the right answer in words ("The plural of leaf is
   leaves.", "The answer is (b) P and R.", never only a letter); for an answer that
   lost marks, say in plain words what to add or correct ("Give an example of each.").
   When a part of one question loses marks, the remark names every part that lost
   one. Never use marking words such as criterion, rubric or step.
10. A word the child has struck out (a line through it, or scribbled over) is not an
   answer: mark the word written to replace it, and never quote the struck-out word
   as the child's answer ("baby" struck out and "babies" written after it earns the
   mark). When nothing replaces a struck-out answer, the item is not answered.
"""

GURUKUL = replace(
    SCHOOL,
    id="gurukul",
    display_name="The Gurukulam School (PW Gurukul)",
    ui_hint="Gurukul diagnostic tests, Grades 1 to 8, answered in notebooks. Upload the "
            "test as the question paper and its answer key. A DT & AK file that holds "
            "both can be uploaded once with 'Question paper and answer key are in the "
            "same PDF' and the pages of each. Every copy goes to teacher review until "
            "the marks are calibrated against the teachers' own.",
    # "Grade 5" and "English" do not say Gurukul, so the operator picks this category.
    detection=DetectionRules(auto_detectable=False),
    prompt=replace(SCHOOL.prompt, extra_rules=GURUKUL_RULES),
    # A DT without its key (a paper the teacher has not keyed yet) gets a model key
    # written from the paper once, saved with the results (teaching_key.py); an
    # uploaded key always wins.
    answer_key=AnswerKeyPolicy(
        may_generate=True,
        examination=("a diagnostic chapter test of The Gurukulam School (PW Gurukul), "
                     "Grades 1 to 8, usually 20 marks in 30 minutes"),
        key_notes=(
            "For this test: each objective item (choose, fill in, one word, true or false, "
            "a word form) is one step of 1 mark with its one right answer. A 2 or 3 mark "
            "answer has one step per point the question asks for, at the level of the "
            "grade; a part that asks 'is this correct? explain' has the judgement and the "
            "reason as two steps.")),
    # No teacher marks to calibrate against yet: the AI marks are a first draft for
    # the class teacher. Flip auto_release once sample copies match the teachers'.
    review=ReviewPolicy(
        auto_release=False,
        review_reason="Gurukul copies go to teacher review: the AI marks are a first "
                      "draft until they match the teachers' marks on sample copies."),
)
