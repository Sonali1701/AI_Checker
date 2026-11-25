"""Actual vision-located answer cells replace merged headings without rescoring."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from answer_map import AnswerMap, AnswerLocation
from grader import GradeReport, QuestionMark
from mathpix import LineBox, PageOCR
from objective_locations import bind_objective_locations


def fixture():
    image = Image.new("RGB", (1000, 1000), "white")
    ImageDraw.Draw(image).rectangle((620, 200, 640, 235), fill="black")
    stream = BytesIO(); image.save(stream, format="PNG")
    report = GradeReport(total_score=0, max_total=1, overall_remarks="", evidence_contract=True,
        objective_visual_verified_qids=["Q4"], questions=[QuestionMark(qid="Q4", score=0,
            max_score=1, page=1, y_fraction=.1, anchor_line_id="P1L1", objective=True,
            attempted=True, chosen_option="D", correct_option="B")],
        answer_regions={"q4": {"line_ids": ["P1L1"], "pages": [1]}})
    pages = [PageOCR(1, 1000, 1000, [LineBox("P1L1", "Section A", (100, 100, 300, 130), 1)])]
    answer_map = AnswerMap("paper", (AnswerLocation("Q4", "unresolved", pages=(1,),
        line_ids=("P1L1",), uncertain_line_ids=("P1L1",)),))
    observation = SimpleNamespace(qid="Q4", clear=True, location_clear=True,
        chosen_option="D", written_answer="", page=1, answer_bbox=[.61, .19, .66, .24])
    return report, pages, answer_map, observation, [stream.getvalue()]


class ObjectiveLocationTests(unittest.TestCase):
    def bind(self, values):
        report, pages, answer_map, observation, pngs = values
        return bind_objective_locations(report, pages, answer_map, {"q4": observation}, student_pngs=pngs)

    def test_binds_actual_cell_and_preserves_marks_and_original_map(self):
        values = fixture(); report, pages, original, _, _ = values
        updated, notes = self.bind(values)
        self.assertEqual(report.questions[0].anchor_line_id, "P1L2")
        self.assertEqual(pages[0].lines[-1].bbox, (610, 190, 660, 240))
        self.assertEqual(updated.locations[0].line_ids, ("P1L2",))
        self.assertEqual(original.locations[0].line_ids, ("P1L1",))
        self.assertEqual((report.total_score, report.questions[0].score), (0, 0))
        self.assertEqual(report.questions[0].chosen_option, "D")
        self.assertTrue(notes)

    def test_missing_unclear_whole_table_invalid_or_blank_boxes_do_not_create_lines(self):
        variants = [None, [], [0, 0, 1, .2], [.1, .1, .15, .15],
                    [.61, .19, math.nan, .24], [.61, .24, .66, .19]]
        for box in variants:
            with self.subTest(box=box):
                values = fixture(); values[3].answer_bbox = box
                self.assertEqual(self.bind(values)[1], [])
                self.assertEqual(len(values[1][0].lines), 1)
        values = fixture(); values[3].location_clear = False
        self.assertEqual(self.bind(values)[1], [])

    def test_choice_disagreement_unconfirmed_or_wrong_page_cannot_bind(self):
        for change in ("disagree", "unconfirmed", "wrong_page", "unverified"):
            values = fixture()
            if change == "disagree": values[3].chosen_option = "A"
            if change == "unconfirmed": values[0].objective_unconfirmed_qids = ["Q4"]
            if change == "wrong_page": values[3].page = 2
            if change == "unverified": values[0].objective_visual_verified_qids = []
            self.assertEqual(self.bind(values)[1], [], change)

    def test_repeated_binding_reuses_the_same_visual_line(self):
        values = fixture(); updated, _ = self.bind(values)
        values = (values[0], values[1], updated, values[3], values[4])
        self.bind(values)
        self.assertEqual(len(values[1][0].lines), 2)

    def test_keeps_readable_owned_answer_when_model_box_points_elsewhere(self):
        values = fixture(); report, pages, _, observation, pngs = values
        pages[0].lines[0] = LineBox("P1L1", "Q4 (D) a complete written answer",
            (100, 100, 400, 130), 1, confidence=.97)
        original = AnswerMap("paper", (AnswerLocation("Q4", "located", pages=(1,),
            line_ids=("P1L1",), confidence=.95),))
        updated, notes = self.bind((report, pages, original, observation, pngs))
        self.assertEqual(report.questions[0].anchor_line_id, "P1L1")
        self.assertEqual(report.answer_regions["q4"]["regions"][0]["bbox"], [100, 100, 400, 130])
        self.assertEqual(report.answer_regions["q4"]["source"], "confirmed_owned_ocr_location")
        self.assertEqual(len(pages[0].lines), 1)
        self.assertEqual(updated.locations[0].line_ids, ("P1L1",))
        self.assertEqual(report.questions[0].score, 0)

    def test_owned_answer_body_replaces_prior_question_heading_and_updates_position(self):
        values = fixture(); report, pages, _, observation, pngs = values
        pages[0].lines[0] = LineBox("P1L1", "Answer. 4", (100, 100, 400, 130), 1, confidence=1)
        pages[0].lines.append(LineBox("P1L2", "(D) a complete written answer",
            (100, 300, 500, 340), 1, confidence=.98))
        observation.written_answer = "(D) a complete written answer"
        original = AnswerMap("paper", (AnswerLocation("Q4", "located", pages=(1,),
            line_ids=("P1L1", "P1L2"), confidence=.95),))
        before = (report.total_score, report.questions[0].score, report.questions[0].chosen_option)
        updated, notes = self.bind((report, pages, original, observation, pngs))
        self.assertEqual(report.questions[0].anchor_line_id, "P1L2")
        self.assertEqual(report.questions[0].page, 1)
        self.assertAlmostEqual(report.questions[0].y_fraction, .32)
        self.assertEqual(report.answer_regions["q4"]["regions"][0]["bbox"], [100, 300, 500, 340])
        self.assertEqual(report.answer_regions["q4"]["first_position"], [1, 2])
        self.assertEqual(updated.locations[0].line_ids, ("P1L2",))
        self.assertEqual(original.locations[0].line_ids, ("P1L1", "P1L2"))
        self.assertEqual((report.total_score, report.questions[0].score, report.questions[0].chosen_option), before)
        self.assertEqual(len(pages[0].lines), 2)
        self.assertTrue(notes)

    def test_common_option_words_do_not_keep_a_conflicting_owned_anchor(self):
        values = fixture(); report, pages, _, observation, pngs = values
        report.questions[0].chosen_option = "B"
        observation.chosen_option = "B"
        observation.written_answer = "(B) The campaign against sati"
        pages[0].lines[0] = LineBox("P1L1", "Q4 (C) The campaign against untouchability",
            (100, 100, 600, 130), 1, confidence=.99)
        pages[0].lines.append(LineBox("P1L2", "(B) The campaign against sati",
            (100, 300, 600, 330), 1, confidence=.99))
        original = AnswerMap("paper", (AnswerLocation("Q4", "located", pages=(1,),
            line_ids=("P1L1", "P1L2"), confidence=.95),))
        paper = """Q4. Identify the reform campaign.
(A) The tax protests
(B) The campaign against sati
(C) The campaign against untouchability
(D) The freedom campaign
"""
        updated, _ = bind_objective_locations(report, pages, original, {"Q4": observation},
            student_pngs=pngs, question_paper_text=paper)
        self.assertEqual(report.questions[0].anchor_line_id, "P1L2")
        self.assertEqual(updated.locations[0].line_ids, ("P1L2",))
        self.assertEqual(report.questions[0].score, 0)

    def test_preflight_distinguishes_verified_option_cell_from_empty_label(self):
        from preflight import _check_bare_objective
        values = fixture(); report, pages, _, _, _ = values
        self.bind(values)
        report.questions[0].score = 1
        self.assertEqual(_check_bare_objective(report, pages), [])
        report.objective_unconfirmed_qids = ["Q4"]
        self.assertTrue(_check_bare_objective(report, pages))
        report.objective_unconfirmed_qids = []
        pages[0].lines[-1].text = "4)"
        self.assertTrue(_check_bare_objective(report, pages))

    def test_later_legacy_block_repair_keeps_the_verified_cell(self):
        import fitz
        from evidence_repair import repair_report_evidence
        values = fixture(); report, pages, _, observation, _ = values
        observation.written_answer = "An actual answer value with several written words"
        self.bind(values)
        actual = report.questions[0].anchor_line_id
        doc = fitz.open(); page = doc.new_page(width=1000, height=1000)
        for index, letter in ((4, "D"), (5, "C"), (6, "B")):
            lid, y = f"P1L{index + 6}", 150 + 50 * index
            page.insert_text((100, y), f"{index}) {letter}")
            pages[0].lines.append(LineBox(lid, f"{index}) {letter}", (100, y-20, 250, y+10), 1))
            if index != 4:
                report.questions.append(QuestionMark(qid=f"Q{index}", score=0, max_score=1,
                    page=1, y_fraction=y/1000, anchor_line_id=lid, objective=True,
                    attempted=True, chosen_option=letter, correct_option="A"))
                report.answer_regions[f"q{index}"] = {"line_ids": [lid], "pages": [1]}
        try:
            repair_report_evidence(report, pages, doc)
            self.assertEqual(report.questions[0].anchor_line_id, actual)
            self.assertEqual(report.answer_regions["q4"]["source"], "independent_visual_location")
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
