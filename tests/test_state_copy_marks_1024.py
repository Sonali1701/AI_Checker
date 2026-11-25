"""Marks of the 21-22 Oct state commerce copies that landed on the wrong line.

Corrected by hand on 24 Oct (UP, MP and Maharashtra, sheet rows 42 to 101):
- MP Core Math: students head each answer "Question no 04" / "Solution no 01". Those
  lines were not headings, so a single-mark answer's circled 1 went on the next
  question's heading line (five marks on one copy).
- MCQs whose own row had no room carried their mark one row down onto a "Section B"
  or "Section 2" heading (UP Business Studies, MP Economics).
- MH Economics: an assertion-reason answer written as the option's words, "Both (A)
  and (R) are true, and (R) is the correct explanation", was read as option A and
  crossed; the key is (D).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdf_renderer as pr  # noqa: E402
from strict_grading import _objective_choice_from_line  # noqa: E402


def _line(lid, y0, text, x0=60.0, page=1):
    return {"lid": lid, "page": page, "x0": x0, "x1": x0 + 200, "y0": y0, "y1": y0 + 16,
            "text": text}


class WordedQuestionLabelsAreHeadings(unittest.TestCase):
    def test_worded_labels(self):
        for text in ("Question no 04", "Question no. 7 :", "Solution no 01", "Question 5",
                     "Ques. 3", "Q.no 4", "Qn 6", "Answer no 2"):
            self.assertTrue(pr._is_header(text), text)

    def test_answers_are_not_headings(self):
        for text in ("Sol: x = 4", "Question of demand", "Ans: (B) 1500", "Q1 = 2Q2"):
            self.assertFalse(pr._is_header(text), text)


class McqMarkNeverMovesOntoAHeading(unittest.TestCase):
    def _next_row(self, text):
        anchor = _line("L1", 100, "(C) Disinvestment")
        below = _line("L2", 120, text)
        return pr._answer_continuation_row(anchor, {"L1": anchor, "L2": below})

    def test_section_and_question_headings_are_refused(self):
        for text in ("Section B", "SECTION - 2", "Part B", "Question no 04",
                     "Solution no 01", "Question no. 7) Explain"):
            self.assertIsNone(self._next_row(text), text)

    def test_the_answers_own_next_line_is_still_used(self):
        self.assertIsNotNone(self._next_row("correct."))
        self.assertIsNotNone(self._next_row("part of the working capital"))


class AssertionReasonOptionWords(unittest.TestCase):
    def test_option_words_name_no_letter(self):
        # The option's words, with no letter: option_named_by_words reads them.
        for line, number in (
                ("Both (A) and (R) are true, and (R) is the correct explanation", None),
                ("4) Both (A) and (R) are true, and (R) is the correct explanation of (A)", 4),
                ("4) (A) is true but (R) is false", 4),          # option (C)'s words
                ("Both A and R are true and R is the correct explanation of A", None)):
            self.assertEqual(_objective_choice_from_line(line, number), "", line)

    def test_a_written_letter_still_wins(self):
        self.assertEqual(_objective_choice_from_line(
            "(D) Both (A) and (R) are true, and (R) is the correct explanation"), "D")
        self.assertEqual(_objective_choice_from_line("(A) Both (A) and (R) are true"), "A")
        self.assertEqual(_objective_choice_from_line("Q3 (D) (A) is false, but (R) is true"), "D")
        self.assertEqual(_objective_choice_from_line(
            "Ans: (d) Both A and R are true and R is the correct explanation of A"), "D")
        self.assertEqual(_objective_choice_from_line("(B) Business Entity"), "B")
        self.assertEqual(_objective_choice_from_line("4) (A)", 4), "A")


if __name__ == "__main__":
    unittest.main()
