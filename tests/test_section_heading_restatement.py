# -*- coding: utf-8 -*-
"""A section heading's tag over questions that print their own is counted once.

The Maharashtra HSC Commerce English unit test (2026-10-22, 25 marks) prints each
section's marks on its own line under the heading, sometimes after a title line:
"SECTION-A / PROSE / (10 marks)" over "(2 x 5 = 10 marks)", and "SECTION II / POETRY
/ (8 marks)" over a question tagged "(8 marks)". Sections III and IV print only the
heading tag. Both readings were summed, the tags read 43, and every right 25-mark
reading would have been refused as ai_mismatch.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (MarksItem, MarksScheme, extraction_reconciles_with_tags,  # noqa: E402
                    printed_tag_sum)

PAPER = """Marks : 25
GENERAL INSTRUCTIONS TO CANDIDATES
iii. Marks for each question / activity are indicated against it.
SECTION-A
         PROSE
(10 marks)
1.
Read the extract given below and answer the questions that follow:
1. Global Understanding: Complete the following.
2. Complex Factual: Complete the web.
(2 × 5 = 10 marks)
3. Inference / Interpretation / Analysis: Give reasons.
4. Personal Response: Give your views.
5. Vocabulary: Match the words with the suffixes.
SECTION II
POETRY
(8 marks)
2.
Read the extract given below and answer the questions that follow:
(8 marks)
Afoot and light-hearted I take to the open road.
SECTION III
WRITING SKILL
(3 Marks)
3.
Develop a story with the help of the given beginning.
SECTION IV
LITERARY GENRE & GRAMMAR
(4 Marks)
4.
(1) Do as directed.
"""


def _scheme(rows):
    items = [MarksItem(qid=q, max=m) for q, m in rows]
    return MarksScheme(total=sum(m for _, m in rows), items=items)


RIGHT = [(f"1.{i}", 2) for i in range(1, 6)] + [("2", 8), ("3", 3), ("4", 4)]


class SectionHeadingRestatement(unittest.TestCase):
    def test_each_section_is_counted_once(self):
        self.assertEqual(printed_tag_sum(PAPER)[0], 25.0)

    def test_the_right_reading_reconciles(self):
        self.assertTrue(extraction_reconciles_with_tags(_scheme(RIGHT), PAPER, 25, False))

    def test_a_dropped_section_still_fails(self):
        self.assertFalse(extraction_reconciles_with_tags(_scheme(RIGHT[:-1]), PAPER, 25, False))

    def test_a_heading_its_questions_do_not_add_up_to_still_counts(self):
        # The questions under the heading print 6, not 10: the heading is not a
        # restatement of them, so both stay in the sum as before.
        paper = PAPER.replace("(2 × 5 = 10 marks)", "(2 × 3 = 6 marks)")
        self.assertEqual(printed_tag_sum(paper)[0], 31.0)

    def test_a_block_tag_over_unit_parts_is_not_a_missing_question(self):
        # Maharashtra HSC SP Unit Test-01: "Select the correct option ... (5 Marks)"
        # over five 1-mark MCQs; the reading that splits them was refused.
        from grader import scheme_contradicts_paper
        paper = ("Marks - 25\nI. Select the correct option and rewrite the complete sentences: (5 Marks)\n"
                 "1. a\n2. b\n3. c\n4. d\n5. e\n"
                 "II. Give one word or term for the following statements: (4 Marks)\n1. a\n2. b\n3. c\n4. d\n"
                 "III. Distinguish between the following (Any two) (6 marks)\n"
                 "IV. Justify the following statements (AnyOne): (4 marks)\n"
                 "V. Answer the following question in detail (Any one) (6 marks)\n")
        rows = ([(f"I.{i}", 1) for i in range(1, 6)] + [(f"II.{i}", 1) for i in range(1, 5)]
                + [("III.1", 3), ("III.2", 3), ("III.3", 3), ("IV.1", 4), ("IV.2", 4),
                   ("V.1", 6), ("V.2", 6)])
        self.assertEqual(scheme_contradicts_paper(_scheme(rows), paper, 25), "")
        # A part priced at a value the paper never prints still disagrees with it.
        wrong = [(q, 1.5 if q.startswith("I.") else m) for q, m in rows]
        self.assertIn("worth 5", scheme_contradicts_paper(_scheme(wrong), paper, 25))

    def test_a_total_printed_with_an_ascii_hyphen_is_read(self):
        # Maharashtra HSC OCM Unit Test-01: "Unit Test- 01 / Marks - 25".
        from grader import paper_total_from_text
        self.assertEqual(paper_total_from_text("Unit Test- 01\nMarks - 25\ni) The paper"), 25.0)
        self.assertEqual(paper_total_from_text("Marks – 25"), 25.0)
        self.assertEqual(paper_total_from_text("Marks : 50"), 50.0)
        self.assertIsNone(paper_total_from_text("1. Explain. (5 Marks)\n2. State - 3 points"))

    def test_a_question_tag_after_its_question_line_is_not_a_heading(self):
        paper = "SECTION A\n1. Define capital.\n(3 Marks)\n2. Define drawings.\n(3 Marks)\n"
        self.assertEqual(printed_tag_sum(paper)[0], 6.0)


if __name__ == "__main__":
    unittest.main()
