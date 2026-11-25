# -*- coding: utf-8 -*-
"""PW Design & Arch faculty review of one weekly assignment 07 copy (26 Oct 2026), in code.

Voice note, in English: "In the first question the perspective is wrong and the lines are
very shaky, which is not mentioned; it only focused on the human figure. In the second
question the bean bag is proportionately off, about half the size of the room, about
five feet. In the third question no user interaction is shown, how to use it, why it is
useful, and aesthetically no element was added that would make the user buy it. In the
museum question there are no details, the floor is not drawn, only lines. All these
things must be mentioned: the feedback has to be there in detail."
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import design_grading as dg  # noqa: E402
import design_renderer as dr  # noqa: E402

_Q4 = [("Perspective and Spatial Layout", 6), ("Central Sculpture and Pedestal", 5),
       ("Pyramid Skylight", 4), ("Pillars, Reflections, and Atmosphere", 3),
       ("Presentation and Finish", 2)]


def _criteria(awards):
    return [dg.CriterionResult(name=n, max=m, awarded=a) for (n, m), a in zip(_Q4, awards)]


class TheHalfMarksCapKeepsTheMarkersJudgement(unittest.TestCase):
    """Q4 (gallery): the marker praised the perspective and missed the pyramid skylight.
    The cap used to take every excess mark from the highest award, so the copy showed
    "Perspective 1 / 6" beside "the gallery is well constructed in one point perspective"."""

    def test_the_excess_is_shared_in_proportion(self):
        criteria = _criteria([5, 4, 3, 2, 1.5])
        dg._cap_main_demand(criteria, "Pyramid Skylight", 20)
        awards = [c.awarded for c in criteria]
        self.assertEqual(sum(awards), 10.0, "still at most half of 20")
        self.assertEqual(awards[2], 1.5, "the skylight keeps at most 40 percent of 4")
        self.assertGreater(awards[0], 1.0, "the praised perspective is not emptied")
        self.assertEqual(awards[0], max(awards), "the strongest criterion stays the strongest")
        for awarded in awards:
            self.assertEqual(awarded * 2, int(awarded * 2), "half marks")

    def test_awards_are_never_raised_and_a_sheet_under_the_cap_is_untouched(self):
        low = _criteria([2, 1, 1, 0.5, 0.5])
        dg._cap_main_demand(low, "Pyramid Skylight", 20)
        self.assertEqual([c.awarded for c in low], [2, 1, 1, 0.5, 0.5])
        full = _criteria([6, 5, 4, 3, 2])
        dg._cap_main_demand(full, "Pyramid Skylight", 20)
        self.assertEqual(sum(c.awarded for c in full), 10.0)
        for criterion, before in zip(full, [6, 5, 4, 3, 2]):
            self.assertLessEqual(criterion.awarded, before)

    def test_the_second_fault_cap_is_shared_too(self):
        criteria = _criteria([5, 4, 3, 2, 1.5])
        dg._cap_main_demand(criteria, "Pyramid Skylight", 20)
        dg._cap_second_fault(criteria, 20)
        self.assertEqual(sum(c.awarded for c in criteria), 6.5, "34 percent of 20, in half marks")
        self.assertGreater(criteria[0].awarded, 0)


class TheMarkerChecksWhatTheFacultyCheck(unittest.TestCase):
    def setUp(self):
        self.prompt = " ".join(dg.DESIGN_MARKING_SYSTEM_PROMPT.split())

    def test_perspective_is_checked_line_by_line_and_shaky_lines_are_named(self):
        self.assertIn("Perspective is CHECKED, never assumed", self.prompt)
        self.assertIn("Never praise a perspective you have not checked line by line", self.prompt)
        self.assertIn("Shaky, hairy, broken, scratchy or overworked lines", self.prompt)

    def test_objects_are_in_proportion_and_called_what_they_are(self):
        self.assertIn("Scale and proportion of OBJECTS, not only figures", self.prompt)
        self.assertIn("An object that fills half the room", self.prompt)
        self.assertIn("a bean bag is not an armchair", self.prompt)

    def test_surfaces_are_drawn_not_left_as_construction_lines(self):
        self.assertIn("Construction or grid lines alone are not a finished floor", self.prompt)
        self.assertIn('never earn praise as "receding tiles"', self.prompt)

    def test_a_product_shows_its_user_and_its_appeal(self):
        self.assertIn("USER INTERACTION, how a person uses the product", self.prompt)
        self.assertIn("why it is useful", self.prompt)
        self.assertIn("AESTHETIC APPEAL, styling that would make a user want to buy it", self.prompt)
        paper = " ".join(dg.DESIGN_PAPER_SYSTEM_PROMPT.split())
        self.assertIn("keep one criterion that names the user interaction and the aesthetic appeal", paper)
        self.assertIn("Any scene is marked on accurate perspective", paper)

    def test_every_fault_that_cost_marks_is_named_and_comes_early(self):
        self.assertIn("EVERY FAULT THAT COST MARKS IS NAMED", self.prompt)
        self.assertIn("Never write only about one aspect (the figures)", self.prompt)
        self.assertIn("The second sentence names the faults that cost the most marks", self.prompt)
        self.assertIn("no praise for a perspective you marked down", self.prompt)


class ThePanelKeepsTheFaults(unittest.TestCase):
    """Q4's overall feedback on the copy was its first sentence only: the praise."""

    def test_the_sentence_naming_the_faults_is_shown(self):
        feedback = ("The gallery space is well constructed in one point perspective with receding tiles, "
                    "framed art on both walls and a strong central sculpture on its pedestal. "
                    "The required pyramid skylight is missing and the floor is only construction lines.")
        shown = dr.short_text(feedback)
        self.assertIn("pyramid skylight is missing", shown)
        self.assertLessEqual(len(shown.split()), dr.SUMMARY_WORDS)


if __name__ == "__main__":
    unittest.main()
