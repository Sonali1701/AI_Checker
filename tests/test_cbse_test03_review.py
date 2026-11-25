# -*- coding: utf-8 -*-
"""CBSE Class 9 Maths/SST Subjective Test-03 review (2026-10-01).

MCQ option texts filed under the wrong question (numbered General Instructions,
printed marks and formula pieces on their own lines, "Assertion (A):"), and map
answers credited without anyone knowing where the places were drawn.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preflight  # noqa: E402
from strict_grading import option_texts_from_paper  # noqa: E402
from test_teacher_layout import _q, _report  # noqa: E402

SST = """General Instructions:
1.
There are 20 questions in the Question paper. All questions are compulsory.
2.
The question paper has Six Sections.
3.
Multiple Choice Type Questions (MCQ), carry 1 mark each.
4.
Very Short Answer Type Questions (VSA), carry 2 marks each.
5.
Short Answer Type Questions (SA), carry 3 marks each.
6.
Long answer type questions (LA), carry 5 marks.
7.
There are case based questions (CBQ).
8.
Map-Based Questions, carry 3 marks.

SECTION-A [Multiple Choice Questions]
1.
Which layer of the atmosphere contains the ozone layer?
(A)
Troposphere
(B)
Stratosphere
(C)
Mesosphere
(D)
Thermosphere
1
2.
Which of the following is an early writing system associated with the Sumerians?
(A)
Sindhu lipi
(B)
Hieroglyphic
(C)
Cuneiform
(D)
Brahmi
1
8.
Assertion-Reasoning:
Assertion (A): The shift to farming during the Neolithic period is called a revolution.
Reason (R): It changed the way people lived.
(A) Both A and R are true and R is the correct explanation of A.
(B) Both A and R are true but R is not the correct explanation of A.
(C) A is true but R is false.
(D) A is false but R is true.
1
SECTION-F [Map/Diagram Based Questions]
20. On an outline map of the Indian subcontinent, locate and label the following major, well-known
Harappan sites
(i)
Kalibangan
"""

MATH = """1.
If x = 2 + root 3, then the numerical value of (x - 1/x)^2 is equal to:
(A)
12
(B)
16
(C)
 4
(D)
8 root 3
1
3.
If a^2 + b^2 + c^2 = 29 and ab + bc + ca = 10, the positive value of a + b + c is
(A)
49
(B)
7
(C)
root 39
(D)
9
1
4.
Which rational number lies between root 2 and root 3?
(A)
1.25
(B)
1.5
(C)
1.85
(D)
2.1
1
6.
If (9x + 5/2)(9x - 5/2) = 81x^2 - k, then k is
(A)
25
4
(B)
-
25
4
(C)
5
2
(D)
0
1
9.
Assertion (A): root 7 lies between 2 and 3.
Reason (R): If a < b < c then root a < root b < root c.
(A) Both (A) and (R) are true, and (R) is the correct explanation of (A).
(B) Both (A) and (R) are true, but (R) is NOT the correct explanation of (A).
(C) (A) is true, but (R) is false.
(D) (A) is false, but (R) is true.
1
"""


class OptionTexts(unittest.TestCase):
    def test_numbered_instructions_do_not_swallow_the_questions(self):
        options = option_texts_from_paper(SST)
        self.assertEqual(options[1]["A"], "Troposphere")
        self.assertEqual(options[1]["D"], "Thermosphere")
        self.assertEqual(options[2]["C"], "Cuneiform")

    def test_assertion_labels_are_not_options(self):
        options = option_texts_from_paper(SST)
        self.assertTrue(options[8]["A"].startswith("Both A and R are true and R"))
        maths = option_texts_from_paper(MATH)
        self.assertTrue(maths[9]["A"].startswith("Both (A) and (R) are true, and"))
        self.assertTrue(maths[9]["D"].startswith("(A) is false"))

    def test_formula_pieces_and_printed_marks_are_not_questions(self):
        options = option_texts_from_paper(MATH)
        self.assertEqual(set(options), {1, 3, 4, 6, 9})
        self.assertEqual(options[1]["A"], "12")
        self.assertEqual(options[6]["A"].split(), ["25", "4"])
        self.assertEqual(options[6]["D"], "0")


class MapAnswers(unittest.TestCase):
    def _codes(self, attempted):
        report = _report([_q("Q19.i", 1, 1, 1, "P1L1"),
                          _q("Q20.i", 1 if attempted else 0, 1, 1, "P1L1" if attempted else "",
                             attempted=attempted)])
        return {i.code for i in preflight.release_blocking(
            preflight.check_copy(report, paper_text=SST))}

    def test_the_map_question_is_found(self):
        self.assertEqual(preflight.map_question_numbers(SST), {20})
        self.assertEqual(preflight.map_question_numbers(MATH), set())

    def test_an_attempted_map_goes_to_a_teacher(self):
        self.assertIn("map-needs-teacher", self._codes(True))

    def test_an_unattempted_map_does_not_hold(self):
        self.assertNotIn("map-needs-teacher", self._codes(False))


if __name__ == "__main__":
    unittest.main()
