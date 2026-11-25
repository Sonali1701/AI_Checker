# -*- coding: utf-8 -*-
"""Design team feedback of 12 Oct 2026 on four more NID Mock 1 copies (Disha, Dolly,
Shreya, Diya), in code.

What they asked for: feedback on colouring technique and how materials and textures
are rendered; typography that suits the medium; feedback that follows the question's
requirements and never praises an approach the brief rules out (one point perspective
where three point was required); neatness, outlines, colouring and use of the frame
(about 80 to 20 drawing to text); overall presentation. On Disha's copy the marker
called a three point attempt "clean one point perspective" and listed it as a strength.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import design_grading as dg  # noqa: E402
from test_design_papers import _checker, _marks, _paper, _sheet_png  # noqa: E402


class TheFeedbackRules(unittest.TestCase):
    def test_the_marker_is_told_what_the_design_team_asked_for(self):
        prompt = dg.DESIGN_MARKING_SYSTEM_PROMPT
        for text in ("colouring technique and how materials and textures are shown",
                     "typography and whether it suits the medium",
                     "how the frame is used",
                     "The feedback follows the question's requirements",
                     "A strength is never an approach the brief rules out",
                     "viewpoint_seen"):
            self.assertIn(text, prompt, text)
        checks = dg.DESIGN_FACULTY_CHECKS
        for text in ("Colouring technique", "Typography, wherever there is lettering",
                     "80 percent drawing to 20 percent text", "presentation of the work on the paper"):
            self.assertIn(text, checks, text)

    def test_a_missed_viewpoint_with_a_second_serious_fault_is_the_bottom_band(self):
        self.assertIn("faculty gave 2 out of 20 to a kitchen scene drawn at standing eye level",
                      dg.DESIGN_MARKING_BANDS)

    def test_the_marker_schema_carries_the_viewpoint_statement(self):
        schema = dg._marks_schema()
        answer = schema["$defs"]["_Answer"]["properties"] if "$defs" in schema else None
        self.assertIsNotNone(answer)
        self.assertIn("viewpoint_seen", answer)


class TheStripIsShort(unittest.TestCase):
    """Design team, 14 Oct 2026: the feedback strips were long paragraphs repeating
    the points; make them shorter and easier to read."""

    LONG = ("You built a full, believable kitchen with shelves, jars, dishes, the cookie jar on the "
            "fridge, the child on the chair and the startled cat, so the storytelling reads well. The "
            "brief asks for the toy car's worm's eye view looking up, but your scene is drawn in one "
            "point perspective at standing eye level with a central vanishing point on the back wall. "
            "Practise low angle room views where vertical lines rise to a third vanishing point above. "
            "Reem's proportions read older than three, so study a toddler's large head and short limbs.")

    def test_the_summary_is_two_sentences_within_a_word_budget(self):
        import design_renderer as dr
        summary = dr.short_text(self.LONG)
        self.assertLessEqual(len(summary.split()), dr.SUMMARY_WORDS)
        self.assertTrue(summary.startswith("You built a full, believable kitchen"))
        self.assertTrue(summary.endswith("."))
        self.assertNotIn("Practise low angle", summary)
        # A single very long sentence is cut at the budget, never dropped.
        one = " ".join(["word"] * 80) + "."
        self.assertEqual(len(dr.short_text(one).split()), dr.SUMMARY_WORDS)
        self.assertEqual(dr.short_text(""), "")

    def test_a_point_is_its_first_sentence_within_twelve_words(self):
        import design_renderer as dr
        point = dr.short_point("Draw the whole scene in three point perspective from the floor so Reem "
                               "towers above us. Then add the microwave.")
        self.assertLessEqual(len(point.split()), dr.POINT_WORDS)
        self.assertNotIn("microwave", point)
        self.assertEqual(dr.short_point("Fridge and cat included."), "Fridge and cat included.")
        note = dr.short_point("The child reads older than three, with limbs and proportions too mature "
                              "for a toddler of that age", dr.NOTE_WORDS)
        self.assertTrue(note.endswith("too mature."), note)

    def test_the_panel_for_one_drawing_is_compact(self):
        from unittest.mock import patch

        import design_renderer as dr
        sheet = _marks()
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        answer = report.answers[1]
        answer.feedback = self.LONG
        answer.strengths = ["Rich kitchen detail with shelves, jars, dishes, cookie jar on the fridge and a clear cat",
                            "Confident line work and tidy construction of the furniture",
                            "Good variety of objects that bring the scene to life"]
        answer.improvements = ["Draw the whole scene in three point perspective from the floor so Reem towers above us",
                               "Give Reem true three year old proportions with a larger head and shorter limbs",
                               "Add the required microwave and lead the eye upward toward the cookie"]
        height = dr._panel(None, [answer], 0, 0, 595.0)
        with patch.object(dr, "EDIT_ROOM_LINES", 0):
            content_height = dr._panel(None, [answer], 0, 0, 595.0)
        # The old layout ran the whole paragraph and both lists down one column: about
        # a third of an A4 page for this drawing. Five criteria with bars, a two line
        # summary and two short lists fit in under a quarter.
        self.assertLessEqual(content_height, 200,
                             f"content height {content_height:.0f}pt for one drawing")
        # Editable feedback reserves one extra line below the summary and each list.
        # The lists share a row, so at most two extra lines extend the panel.
        self.assertGreater(height, content_height)
        self.assertLessEqual(height - content_height, 25,
                             f"edit room adds {height - content_height:.0f}pt")
        self.assertGreater(height, 90)

    def test_the_marker_is_asked_for_short_feedback(self):
        prompt = dg.DESIGN_MARKING_SYSTEM_PROMPT
        # 60 words since 26 Oct 2026: the faults must fit (test_design_faculty_1026).
        self.assertIn("2 to 3 short plain sentences, at most 60 words in all", prompt)
        self.assertIn("at most 10 words a point", prompt)
        self.assertIn("at most 12 words", prompt)
        self.assertIn("at most 14 words a point", prompt)


class StrengthsAgreeWithTheVerdict(unittest.TestCase):
    def test_a_strength_that_praises_the_missed_main_demand_is_dropped(self):
        kept = dg.without_praise_of(
            ["Clean one point perspective with believable depth", "Good kitchen detail including the fan",
             "Cat and climbing figure both included"], "Low-Angle Perspective Accuracy")
        self.assertEqual(kept, ["Good kitchen detail including the fan", "Cat and climbing figure both included"])
        self.assertEqual(dg.without_praise_of(["Clean one point perspective"], ""), ["Clean one point perspective"])

    def test_the_guard_runs_on_the_drawing_and_on_the_cover(self):
        sheet = _marks()
        q2 = sheet.answers[0]
        q2.main_demand_met, q2.main_demand_criterion = False, "Low viewpoint"
        q2.strengths = ["Clean one point perspective with a believable low viewpoint", "Fridge and cat included"]
        sheet.good_at = ["Confident viewpoint and depth in the kitchen", "Friendly rounded lettering"]
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        answer = next(a for a in report.answers if a.qid == q2.qid)
        self.assertEqual(answer.strengths, ["Fridge and cat included."])
        self.assertEqual(report.good_at, ["Friendly rounded lettering."])

    def test_a_missed_main_demand_with_a_second_serious_fault_is_the_bottom_band(self):
        # Dolly's scene, 14 Oct 2026: eye-level view, child walking, no depth; the marker
        # gave 9.5 of 20, the faculty 2. Two serious faults cap the question at 34 percent.
        sheet = _marks()
        q2 = sheet.answers[0]
        q2.main_demand_met, q2.main_demand_criterion = False, "Low viewpoint"
        q2.serious_faults = ["wrong perspective system for the viewpoint",
                             "figure doing the wrong action and out of proportion"]
        for award, value in zip(q2.criteria, (5, 4, 5, 2, 2)):
            award.awarded = value
        answer = dg.verify_marks(sheet, _paper(), [_sheet_png()]).answers[1]
        self.assertEqual(answer.score, 6.5, "34 percent of 20, in half marks")
        for criterion in answer.criteria:
            self.assertGreaterEqual(criterion.awarded, 0)
        # One serious fault (the missed demand itself) keeps the half-marks cap.
        q2.serious_faults = ["wrong perspective system for the viewpoint"]
        for award, value in zip(q2.criteria, (5, 4, 5, 2, 2)):
            award.awarded = value
        self.assertEqual(dg.verify_marks(sheet, _paper(), [_sheet_png()]).answers[1].score, 10.0)
        self.assertIn("serious_faults", dg._marks_schema()["$defs"]["_Answer"]["properties"])
        self.assertIn("serious_faults", dg.DESIGN_MARKING_SYSTEM_PROMPT)

    def test_a_met_main_demand_keeps_every_strength(self):
        sheet = _marks()
        sheet.answers[0].strengths = ["Convincing low viewpoint", "Fridge and cat included"]
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        answer = next(a for a in report.answers if a.qid == sheet.answers[0].qid)
        self.assertEqual(len(answer.strengths), 2)


class TheViewpointIsChecked(unittest.TestCase):
    VIEW = "one point perspective at standing eye level"
    SEES = "three point perspective looking up from the floor, the fridge and chair converge to a point above"

    def _first(self):
        sheet = _marks()
        sheet.answers[0].viewpoint_seen = self.VIEW
        return sheet

    def test_the_viewpoint_statement_is_a_claim_for_the_second_examiner(self):
        claims = dg.collect_claims(self._first(), 1)
        kinds = {(c.kind, c.text) for c in claims}
        self.assertIn(("viewpoint", self.VIEW), kinds)
        self.assertNotIn("viewpoint", {c.kind for c in dg.collect_claims(_marks(), 1)},
                         "no statement, no claim")
        self.assertNotIn("viewpoint", {c.kind for c in dg.collect_claims(self._first(), 1, kinds=("answer", "part"))})

    def test_a_wrong_viewpoint_sends_the_question_back_with_the_second_examiners_reading(self):
        calls = []
        first = self._first()
        corrected = _marks()
        corrected.answers = [corrected.answers[0]]
        corrected.answers[0].viewpoint_seen = "three point perspective looking up from floor level"

        def mark(pngs, paper, context, **kwargs):
            calls.append(kwargs)
            return (corrected if kwargs.get("only_qids") else first).model_copy(deep=True)
        sheet = first.model_copy(deep=True)
        reasons = dg.second_look(sheet, _paper(), [_sheet_png()], dg.PaperContext(text="paper"),
                                 mark=mark, checker=_checker({self.VIEW: ("false", self.SEES)}),
                                 call={"google_token": "t", "task_id": "k"})
        again = [c for c in calls if c.get("only_qids")]
        self.assertEqual(len(again), 1)
        note = " ".join(again[0]["second_look"][first.answers[0].qid])
        self.assertIn("You said the drawing's viewpoint is", note)
        self.assertIn(self.SEES, note)
        self.assertIn("do not praise a system the brief rules out", note)
        self.assertEqual(reasons, [], "the re-mark agreed with the second examiner")
        self.assertEqual(sheet.answers[0].viewpoint_seen, "three point perspective looking up from floor level")

    def test_examiners_who_still_disagree_on_the_viewpoint_go_to_the_reviewer(self):
        first = self._first()

        def mark(pngs, paper, context, **kwargs):
            return first.model_copy(deep=True) if not kwargs.get("only_qids") else (
                lambda s: (setattr(s, "answers", [s.answers[0]]), s)[1])(first.model_copy(deep=True))
        sheet = first.model_copy(deep=True)
        reasons = dg.second_look(sheet, _paper(), [_sheet_png()], dg.PaperContext(text="paper"),
                                 mark=mark, checker=_checker({self.VIEW: ("false", self.SEES)}),
                                 call={"google_token": "t", "task_id": "k"})
        self.assertEqual(len(reasons), 1)
        self.assertIn("the examiners disagree", reasons[0])

    def test_a_false_that_describes_the_same_viewpoint_is_no_dispute(self):
        # 14 Oct 2026, Dolly's scene: the second examiner answered "false" while
        # writing that it saw one-point perspective at standing eye level, the very
        # thing the marker had written, and the question was re-marked for nothing.
        calls = []

        def mark(pngs, paper, context, **kwargs):
            calls.append(kwargs)
            return self._first().model_copy(deep=True)
        same = "Roughly one-point perspective at standing eye level with the horizon near the counter height"
        reasons = dg.second_look(self._first(), _paper(), [_sheet_png()], dg.PaperContext(text="paper"),
                                 mark=mark, checker=_checker({self.VIEW: ("false", same)}),
                                 call={"google_token": "t", "task_id": "k"})
        self.assertEqual(reasons, [])
        self.assertEqual([c for c in calls if c.get("only_qids")], [])

    def test_viewpoint_terms_are_read_from_the_wording(self):
        self.assertEqual(dg.viewpoint_terms("one point perspective at standing eye level"), ("one", "level"))
        self.assertEqual(dg.viewpoint_terms("Three-point perspective looking up from the floor"), ("three", "low"))
        self.assertEqual(dg.viewpoint_terms("2-point view from above the room"), ("two", "high"))
        self.assertEqual(dg.viewpoint_terms("a three quarter view of the shoe"), ("", ""))
        self.assertTrue(dg.same_viewpoint("one point perspective at standing eye level",
                                          "Roughly one-point perspective, horizon at counter height, straight-on"))
        self.assertFalse(dg.same_viewpoint("Two point perspective at seated eye level",
                                           "One-point perspective at standing eye level"))
        self.assertFalse(dg.same_viewpoint("three point perspective looking up from the floor",
                                           "one point perspective at standing eye level"))
        self.assertFalse(dg.same_viewpoint("a three quarter view", "one point at eye level"), "nothing to compare")

    def test_a_confirmed_or_unsure_viewpoint_is_no_dispute(self):
        for verdict in ("true", "unsure"):
            calls = []

            def mark(pngs, paper, context, **kwargs):
                calls.append(kwargs)
                return self._first().model_copy(deep=True)
            reasons = dg.second_look(self._first(), _paper(), [_sheet_png()], dg.PaperContext(text="paper"),
                                     mark=mark, checker=_checker({self.VIEW: (verdict, self.SEES)}),
                                     call={"google_token": "t", "task_id": "k"})
            self.assertEqual(reasons, [])
            self.assertEqual([c for c in calls if c.get("only_qids")], [])


if __name__ == "__main__":
    unittest.main()
