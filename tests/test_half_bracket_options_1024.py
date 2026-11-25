# -*- coding: utf-8 -*-
"""An MCQ written with only a closing bracket is still the student's option.

Maharashtra HSC Book-keeping (22 Oct 2026): the student wrote "Q1 -> B] Capital
introduced in Kind", "Q5 -> C] Income & Expenditure A/c", and every such line read
as no answer at all. A small "a)" after a label stays a sub-part ("1) a) 3").
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strict_grading import _objective_choice_from_line  # noqa: E402


class HalfBracketOptions(unittest.TestCase):
    def test_a_letter_with_only_its_closing_bracket_is_the_option(self):
        for text, number, letter in (("Q1 -> B] Capital introduced in Kind", 1, "B"),
                                     ("Q2 -> c] Interest on Drawings", 2, "C"),
                                     ("Q5 -> C) Income & Expenditure A/c", 5, "C"),
                                     ("Q3 -> B) 4000", 3, "B"),
                                     ("Q1 A) Partners", 1, "A"),
                                     ("Q7 D] 71000", 7, "D")):
            self.assertEqual(_objective_choice_from_line(text, number), letter, text)

    def test_a_label_for_another_question_or_a_sub_part_is_not_an_option(self):
        self.assertEqual(_objective_choice_from_line("Q7 D] 71000", 8), "")
        self.assertEqual(_objective_choice_from_line("a) Partners", None), "")
        self.assertEqual(_objective_choice_from_line("Q6 -> False, becoz every adjustment", 6), "")
        self.assertEqual(_objective_choice_from_line("Q4 -> B]Capital expenditure", 4), "")

    def test_the_bracketed_forms_are_unchanged(self):
        self.assertEqual(_objective_choice_from_line("Ans:- (D) Interest on Capital", 1), "D")
        self.assertEqual(_objective_choice_from_line("2) (A) A and R are true", 2), "A")


if __name__ == "__main__":
    unittest.main()
