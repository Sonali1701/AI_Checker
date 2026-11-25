# -*- coding: utf-8 -*-
"""A block priced "N mark(s) each" is not a mismatch against the bracketed tags.

The Parishram Class 12 Accountancy unit test (2026-09-17) prints "Choose the correct
option. (1 mark each)" over Q1 to Q9 and eight tags (3, 3, 3, 3, 4, 4, 6, 6) over
Q10 to Q13 and their OR alternatives. The extraction of 41 marks was rejected against
the tags' 32 and the copy could not be graded, although 41 minus one alternative of
each pair is the paper's 25.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (MarksItem, MarksScheme, each_rule_allowance,  # noqa: E402
                    extraction_reconciles_with_tags, printed_tag_sum)

PAPER = """Unit Test - 02 Date-27/06/2026
Marks - 25 Time: 45 minutes
Accountancy
Instructions:
i) The question paper consists of 13 questions.
ii) Questions 1 to 9 carry 1 mark each.
iii) Questions 10 and 11 carry 3 marks each.
iv) Question 12 carries 4 marks.
v) Question 13 carries 6 marks.
Section A
I. Choose the correct option. (1 mark each)
Q1. Persons who have entered into a partnership with one another are individually known as:
Q2. Calculate the value of goodwill on the basis of 3 years' purchase of Super Profit.
Q3. Madhu and Radha were partners in a partnership firm sharing profits and losses in the ratio of 3:2.
Q4. The question consists of two statements:
Q5. B and D were partners.
Q6. On the reconstitution of a firm, the value of furniture increased.
Q7. The profit available for appropriation among the partners will be:
Q8. The Interest on Capitals of A and B will be:
Q9. A, B and C were partners in a firm sharing profits and losses equally.
Section B
II. Attempt any three. (3 marks each)
Q10. Prepare Profit and Loss Appropriation Account of Ravi, Kavi and Avi.
(3 marks)
OR
Pass the necessary journal entries in the books of the firm to rectify the above error.
(3 marks)
Q11. State all the factors affecting goodwill and describe any two of them in detail.
(3 marks)
OR
Pass necessary entries and show your workings clearly.
(3 marks)
Q12. Prepare the Capital Account and Current Account of Ajay and Vijay.
(4 marks)
OR
Pass the necessary adjustment entry.
(4 marks)
Q13. Calculate the goodwill of a firm on the basis of two years' purchases.
(6 marks)
OR
Prepare Revaluation Account and Partners' Capital Account.
(6 marks)
"""


def _scheme(rows):
    return MarksScheme(total=sum(m for _, m in rows),
                       items=[MarksItem(qid=q, max=float(m), description="") for q, m in rows])


ACCOUNTANCY = [(f"Q{i}", 1) for i in range(1, 10)] + [
    ("Q10", 3), ("Q10_OR", 3), ("Q11", 3), ("Q11_OR", 3),
    ("Q12", 4), ("Q12_OR", 4), ("Q13", 6), ("Q13_OR", 6)]


class EachRuleTests(unittest.TestCase):
    def test_the_accountancy_paper_reads_as_32_tagged_plus_9_by_rule(self):
        self.assertEqual(printed_tag_sum(PAPER), (32.0, 8))
        self.assertEqual(each_rule_allowance(_scheme(ACCOUNTANCY).items, PAPER), 9.0)

    def test_the_correct_extraction_is_accepted(self):
        self.assertTrue(extraction_reconciles_with_tags(_scheme(ACCOUNTANCY), PAPER, 25, True))

    def test_a_dropped_tagged_part_is_still_caught(self):
        # Q13_OR missing: 35 marks against the tags' 32 plus 9 by rule, and no choice
        # band would hide it on a paper without choice.
        rows = [r for r in ACCOUNTANCY if r[0] != "Q13_OR"]
        self.assertFalse(extraction_reconciles_with_tags(_scheme(rows), PAPER, 25, False))

    def test_tags_on_only_the_case_parts_do_not_refute_the_official_total(self):
        # CBSE Class 9 SST Subjective Test-03 (2026-10-01): marks are bare margin
        # numbers except "[1 M]"/"[2 M]" on the case-based and map parts (7 of 40).
        paper = ("Max. Marks: 40\nSECTION-A [Multiple Choice Questions]\n"
                 + "".join(f"{i}.\nQuestion {i}?\n1\n" for i in range(1, 11))
                 + "11.\nDifferentiate weather and climate.\n2\n"
                 "19.\nRead the source.\n(i) What is it? [1 M]\n(ii) Explain. [2 M]\n"
                 "(iii) Name it. [1 M]\n20.\nOn the map locate:\n(i) A [1 M]\n(ii) B [1 M]\n"
                 "(iii) C [1 M]\n")
        self.assertEqual(printed_tag_sum(paper), (7.0, 6))
        rows = ([(f"Q{i}", 1) for i in range(1, 11)] + [("Q11", 2), ("Q12", 2), ("Q13", 2)]
                + [(f"Q{i}", 3) for i in range(14, 18)] + [("Q18", 5)]
                + [("Q19.i", 1), ("Q19.ii", 2), ("Q19.iii", 1)]
                + [("Q20.i", 1), ("Q20.ii", 1), ("Q20.iii", 1)])
        self.assertTrue(extraction_reconciles_with_tags(_scheme(rows), paper, 40, False))
        # A reading that misses the official total is still refused.
        self.assertFalse(extraction_reconciles_with_tags(_scheme(rows[:-1]), paper, 40, False))

    def test_a_no_choice_paper_with_a_rule_priced_block(self):
        paper = ("Maximum Marks: 20\nSection A [1 Mark each]\n1. a\n2. b\n3. c\n4. d\n5. e\n"
                 "Section B\n6. Explain. (5 Marks)\n7. Derive. (5 Marks)\n8. Prove. (5 Marks)\n")
        full = [(f"Q{i}", 1) for i in range(1, 6)] + [("Q6", 5), ("Q7", 5), ("Q8", 5)]
        self.assertTrue(extraction_reconciles_with_tags(_scheme(full), paper, 20, False))
        dropped = full[:-1]                      # one 5-mark question lost
        self.assertFalse(extraction_reconciles_with_tags(_scheme(dropped), paper, 20, False))

    def test_an_each_inside_a_product_tag_grants_no_allowance(self):
        # Auditing MTS-04: "(3 MCQs x 2 Marks each = 6 Marks)" is counted once through
        # the product's total; the three MCQ rows at 2 are that 6, not extra.
        paper = ("Part - A\nMultiple choice question (MCQ)\n"
                 "General MCQs (3 MCQs \u00d7 2 Marks each = 6 Marks)\n1. a\n2. b\n3. c\n"
                 "Part - B\n4. Explain. (4 Marks)\n5. Derive. (4 Marks)\n6. Prove. (4 Marks)\n")
        rows = [("Q1", 2), ("Q2", 2), ("Q3", 2), ("Q4", 4), ("Q5", 4), ("Q6", 4)]
        self.assertEqual(printed_tag_sum(paper), (18.0, 4))
        self.assertEqual(each_rule_allowance(_scheme(rows).items, paper), 0.0)
        self.assertTrue(extraction_reconciles_with_tags(_scheme(rows), paper, 18, False))
        self.assertFalse(extraction_reconciles_with_tags(_scheme(rows + [("Q7", 2)]), paper, 18, False))

    def test_papers_without_each_rules_keep_the_old_test(self):
        paper = "1. a (2 Marks)\n2. b (2 Marks)\n3. c (3 Marks)\n4. d (3 Marks)\n"
        self.assertTrue(extraction_reconciles_with_tags(
            _scheme([("Q1", 2), ("Q2", 2), ("Q3", 3), ("Q4", 3)]), paper, 10, False))
        self.assertFalse(extraction_reconciles_with_tags(
            _scheme([("Q1", 2), ("Q2", 2), ("Q3", 3), ("Q4", 3), ("Q5", 1)]), paper, 10, False))


if __name__ == "__main__":
    unittest.main()
