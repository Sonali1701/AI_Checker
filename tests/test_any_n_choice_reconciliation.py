# -*- coding: utf-8 -*-
"""An "Answer ANY TWO" paper whose tags price one student's marks is not a mismatch.

The UP Board Class 11 Commerce English Test-01 (2026-10-21, 25 marks) prints
"(1 x 4 = 4 Marks)", "(2 x 3 = 6 Marks)", "Answer ANY TWO ... (4 + 4 = 8 Marks)" over
three parts and "Answer ANY ONE ... (7)" over three more. The tags (18) price what one
student can score and the bare "(7)" is no tag, so the right reading (43 marks of
rows, 25 after its two choice rules) was refused as ai_mismatch on every try and none
of the four copies could be graded.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (MarksChoiceGroup, MarksChoiceOption, MarksItem,  # noqa: E402
                    MarksScheme, extraction_reconciles_with_tags, printed_tag_sum)

PAPER = """Test - 01
Marks - 25
English
Section A
(Objective Type Questions)
1. Choose the correct option to answer the following questions:
(1 × 4 = 4 Marks)
(i). I didn't see nothing suspicious in the room.
(ii). We ______ the letter already.
(iii). Which of the following sentences is correct?
(iv). If plants ________ enough sunlight, they die.
(SECTION-B)
2. Read the following extract carefully and answer the questions that follow:
(2 × 3 = 6 Marks)
(A) To what is the goldfinch's movement compared?
(B) How does the poet create a sense of movement and sound in these lines?
(C) How does the goldfinch change the tree?
3. Answer ANY TWO of the following questions in about 40 words each:
(4 + 4 = 8 Marks)
(A) How did the grandmother spend her time after the narrator went to the university?
(B) Why does John Byro choose not to accuse the boys even when he recognises his horse?
(C) What was the purpose of the CT scan on Tut's mummy?
4. Answer ANY ONE of the following in about 80 words-
(7)
(A) Describe the holistic and ecological view of the world promoted by the Green Movement.
(B) What motivated the narrator and his family to undertake a round-the-world voyage?
(C) What changes did Professor Gangadhar Pant Gaitonde observe in the alternate version of history?
"""


def _scheme(q3=4.0, q4=7.0, groups=True, c3=8.0, c4=7.0):
    rows = [MarksItem(qid=f"1.{r}", max=1) for r in ("i", "ii", "iii", "iv")]
    rows += [MarksItem(qid=f"2.{p}", max=2) for p in "ABC"]
    rows += [MarksItem(qid=f"3.{p}", max=q3) for p in "ABC"]
    rows += [MarksItem(qid=f"4.{p}", max=q4) for p in "ABC"]
    rules = []
    if groups:
        rules = [
            MarksChoiceGroup(group_id="g3", select_count=2, contribution_marks=c3, options=[
                MarksChoiceOption(option_id=p, question_ids=[f"3.{p}"]) for p in "ABC"]),
            MarksChoiceGroup(group_id="g4", select_count=1, contribution_marks=c4, options=[
                MarksChoiceOption(option_id=p, question_ids=[f"4.{p}"]) for p in "ABC"]),
        ]
    return MarksScheme(total=sum(r.max for r in rows), items=rows, choice_groups=rules)


class AnyNChoiceReconciliation(unittest.TestCase):
    def test_the_tags_price_one_students_paper(self):
        self.assertEqual(printed_tag_sum(PAPER), (18.0, 3))

    def test_the_right_reading_reconciles(self):
        self.assertTrue(extraction_reconciles_with_tags(_scheme(), PAPER, 25, True))

    def test_choice_rules_that_miss_the_total_still_fail(self):
        # Q3's parts at 3 each: 23 after the rules, not 25.
        self.assertFalse(extraction_reconciles_with_tags(_scheme(q3=3, c3=6), PAPER, 25, True))
        # Q4's parts at 10 each: 28 after the rules.
        self.assertFalse(extraction_reconciles_with_tags(_scheme(q4=10, c4=10), PAPER, 25, True))

    def test_every_alternative_counted_as_compulsory_still_fails(self):
        self.assertFalse(extraction_reconciles_with_tags(_scheme(groups=False), PAPER, 25, True))


if __name__ == "__main__":
    unittest.main()
