# -*- coding: utf-8 -*-
"""The inventory must see every question of a long paper, and know the ids are its own.

Found by the CA new-sample run of 2026-11-14, and confirmed the same day by a faculty
remark on an unrelated Business Laws copy ("AI wrongly marked the answer unattempted.
Student has written the definition on subsequent pages").

The 100-mark Taxation paper of row 32 is 35,653 characters. The prompt cut the paper at
30,000, so questions 6(a) through 8(b) were never shown to the model at all. The student
had answered question 6 on his first three pages, heading them "Q6]" and "6)b)". With
prompt_text ALSO empty on every official question, the model had no description of
question 6 from any source, matched those pages to nothing, and nine written pages
earned no marks at all.

Two things are checked here: a long paper keeps BOTH ends, and the prompt says the
official ids are the tool's own numbering, which is what lets "Q6" reach official Q22.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import answer_inventory  # noqa: E402
from answer_inventory import build_inventory_prompt  # noqa: E402


def _package():
    questions = [SimpleNamespace(qid=f"Q{n}", max_marks=2.0, question_type="objective",
                                 prompt_text="", scorable=True) for n in range(1, 17)]
    # The real row-32 package: every written question carries an EMPTY description.
    questions += [SimpleNamespace(qid=f"Q{n}", max_marks=10.0, question_type="unknown",
                                  prompt_text="", scorable=True) for n in range(17, 25)]
    return SimpleNamespace(questions=questions, choice_groups=[])


FIRST = "1. (a) The opening question about salary income."
LAST = "8. (b) Does cancellation of registration impose any tax obligations?"


def _long_paper(total_chars):
    filler = "\nfiller line of ordinary paper text that carries no question.\n"
    body = FIRST + filler * ((total_chars // len(filler)) + 1)
    return (body[:max(0, total_chars - len(LAST))] + LAST)


class ALongPaperKeepsItsLastQuestions(unittest.TestCase):
    def test_the_end_of_a_long_paper_still_reaches_the_model(self):
        paper = _long_paper(answer_inventory._PAPER_CHARS * 2)
        prompt = build_inventory_prompt(_package(), [], paper)
        self.assertIn(FIRST, prompt, "the start of the paper was dropped")
        self.assertIn(LAST, prompt, "the LAST question was dropped - the row 32 defect")
        self.assertIn("middle of the paper left out", prompt)

    def test_a_paper_that_fits_is_passed_whole(self):
        paper = FIRST + ("\nordinary paper text.\n" * 20) + LAST
        prompt = build_inventory_prompt(_package(), [], paper)
        self.assertIn(FIRST, prompt)
        self.assertIn(LAST, prompt)
        self.assertNotIn("middle of the paper left out", prompt)

    def test_a_real_sized_ca_paper_is_not_truncated_at_all(self):
        """Row 32's paper is 35,653 chars; the old 30,000 cap lost 3 questions."""
        self.assertGreater(answer_inventory._PAPER_CHARS, 35_653)


class TheIdAlignmentWordingIsNotShippedYet(unittest.TestCase):
    """Held back from build 170: it would change the prompt for EVERY category on every
    copy, not only for a paper past the cap, and it is not yet proven on a live copy."""

    def test_the_prompt_carries_no_id_alignment_section_yet(self):
        prompt = build_inventory_prompt(_package(), [], FIRST + "\n" + LAST)
        self.assertNotIn("HOW THE OFFICIAL IDS LINE UP", prompt)


if __name__ == "__main__":
    unittest.main()
