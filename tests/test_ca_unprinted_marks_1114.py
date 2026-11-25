# -*- coding: utf-8 -*-
"""A CA paper that prints no marks at all can be graded.

Found by the CS run of 2026-11-14. The Capital Market and Securities Laws paper is 8
bare questions with an official total of 40 and no marks tag anywhere on it. Over four
attempts the marks reader worked out the split and then refused it every time with
"Unresolved printed allocation: No individual marks tags are printed on the paper for
any question", so the copy failed before any marking and all 4 copies on that paper were
lost. Grading itself handles the paper: asked to read it during evaluation it returned 8
rows of 5 for a total of 40, method ai_verified.

The framework already had MarksReadingPolicy.unprinted_convention for this, added for
the School of Innovation in build 127 after the same thing happened to Data Analytics
papers. CA simply never set it.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from categories.ca import CA  # noqa: E402
from categories.innovation import INNOVATION  # noqa: E402


class CAHasAnUnprintedMarksConvention(unittest.TestCase):
    def test_the_convention_is_set(self):
        self.assertTrue(CA.marks.unprinted_convention.strip(),
                        "a CA paper printing no marks cannot be graded without this")

    def test_it_says_equal_shares_of_the_official_total(self):
        text = CA.marks.unprinted_convention.lower()
        self.assertIn("equal share", text)
        self.assertIn("official total", text)

    def test_printed_marks_still_win(self):
        """The convention must never override a paper that DOES print its marks."""
        text = CA.marks.unprinted_convention.lower()
        self.assertIn("always win", text)

    def test_it_accounts_for_choice_papers(self):
        """CA papers routinely say 'answer any four', so shares are of what counts."""
        self.assertIn("choice", CA.marks.unprinted_convention.lower())

    def test_the_printed_readers_stay_off_for_ca(self):
        """Unchanged behaviour: professional papers print marks in prose."""
        self.assertFalse(CA.marks.try_regex)
        self.assertFalse(CA.marks.try_section_headers)

    def test_innovation_is_untouched(self):
        """This change must not disturb the category the policy was built for."""
        self.assertIn("PW Skills", INNOVATION.marks.unprinted_convention)
        self.assertNotEqual(CA.marks.unprinted_convention,
                            INNOVATION.marks.unprinted_convention)


if __name__ == "__main__":
    unittest.main()
