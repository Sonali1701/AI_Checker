# -*- coding: utf-8 -*-
"""Design and drawing papers (NID, NIFT, UCEED, NATA), builds 61 and 62 (2026-09-21).

Build 62 comes from the first LIVE copy, where (1) two pages stayed sideways and the
student was told to "write the page upright", (2) both loops on the logo page sat on
blank paper because the marker answered in image pixels round a 0 to 1000 ruler and
a desk edge passed as ink, and (3) the student was told the cookie on the fridge was
missing when a jar of cookies stands on top of the fridge.

Build 63 comes from the second live copy. Loops and page turns were right, but the
student was told "Reem stands beside the chair, not climbing" of a child with a knee
up on the chair and both hands pulling on its back, and "rear of shoe left plain" of
a heel that carries a wing shaped form. Neither is a "missing" claim, so nothing
checked them. Now EVERY statement a loop makes is checked, on a close-up.

Build 64 comes from the third live copy. The marks were fair, but the logo's loop
sat on the shoe beside it and the poster's marks tag on the desk: the marker took x
from the 1568 pixel clean picture and y from the 1000 pixel locator. Both pictures of
a page now have one size. The second examiner also "disagreed" with "the drawings on
the fridge are missing" while saying it saw none, because it was asked to agree with
a sentence; it now answers facts (present or absent, true or false, in view or not),
and it checks where every box is.

The written pipeline cannot mark these: the paper prints no marks, the key is
guidance, and a drawing has no OCR lines to anchor to. design_grading.py is a
separate mode where a vision model judges and code verifies:

* printed marks stand only when their quote is really in the paper,
* entered marks must fit the paper's questions,
* awards are clamped to the rubric in half marks,
* a loop is drawn only round a PART of the right drawing that has ink in it,
* nothing a student reads carries a semicolon or a dash,
* a typed, blank or confidently not-genuine upload is rejected, and every marked
  copy goes to faculty review until the marks are calibrated.

No token, no network: the model calls are replaced by readers that return what a
model would.
"""
from __future__ import annotations

import io
import os
import sys
import unittest

import fitz
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import design_grading as dg  # noqa: E402
from design_renderer import build_design_pdf  # noqa: E402
from strict_grading import StrictGradeError  # noqa: E402
from wording import wording_problems  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = dg.DesignCriterion


def _paper(marks="15, 20", total=None):
    paper = dg.DesignPaper(title="NID MOCK", questions=[
        dg.DesignQuestion(qid="Q1", brief="Design a logo for a cookie brand.", criteria=[
            C(name="Idea and brand fit", weight=40), C(name="Form and lettering", weight=40),
            C(name="Presentation and finish", weight=20)]),
        dg.DesignQuestion(qid="Q2", brief="Draw the kitchen from the toy car's eye level.", criteria=[
            C(name="Low viewpoint", weight=30), C(name="Required elements", weight=20),
            C(name="Perspective and proportion", weight=30), C(name="Composition", weight=10),
            C(name="Presentation and finish", weight=10)]),
    ])
    return dg.resolve_maxima(dg.verify_design_paper(paper, ""), marks, total)


def _sheet_png(width=1200, height=1600, drawings=True) -> bytes:
    """A photographed page: off white paper with two pencil drawings on it."""
    image = Image.new("RGB", (width, height), (244, 242, 236))
    if drawings:
        draw = ImageDraw.Draw(image)
        # Q2 in the top half: a box scene with a figure and a tall fridge.
        draw.rectangle([80, 80, 1120, 760], outline=(40, 60, 150), width=5)
        for x in range(120, 1100, 60):
            draw.line([x, 100, 600, 420], fill=(60, 80, 170), width=2)
        draw.rectangle([760, 160, 1060, 720], outline=(30, 50, 140), width=6)      # fridge
        for y in range(200, 700, 40):
            draw.line([780, y, 1040, y + 12], fill=(40, 60, 150), width=3)
        draw.ellipse([480, 240, 560, 320], outline=(30, 50, 140), width=6)          # child
        draw.rectangle([490, 320, 550, 640], outline=(30, 50, 140), width=6)
        for y in range(330, 640, 22):
            draw.line([492, y, 548, y + 10], fill=(40, 60, 150), width=3)
        # Q1 in the bottom half: a round logo with lettering.
        draw.ellipse([300, 900, 700, 1300], outline=(160, 70, 20), width=8, fill=(205, 120, 60))
        for x in range(320, 900, 36):
            draw.line([x, 1380, x + 20, 1440], fill=(120, 40, 20), width=5)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _sheet_pdf(pages=1, drawings=True) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=_sheet_png(drawings=drawings))
    return doc.tobytes()


def _marks(**overrides) -> dg.DesignMarkSheet:
    data = {
        "answers": [
            {"qid": "Q2", "attempted": True, "page": 1, "box": [60, 40, 940, 480],
             "seen": "A kitchen with a child beside a tall fridge.",
             "criteria": [{"name": "Low viewpoint", "awarded": 3.5},
                          {"name": "Required elements", "awarded": 2.5},
                          {"name": "Perspective and proportion", "awarded": 2.25},
                          {"name": "Composition", "awarded": 1},
                          {"name": "Presentation and finish", "awarded": 1}],
             "feedback": "A good attempt - the fridge follows another vanishing point; fix one system first.",
             "strengths": ["Low eye level is clear."],
             "improvements": ["Keep one perspective system."],
             "regions": [
                 {"page": 1, "box": [630, 95, 890, 455], "note": "The fridge follows a different vanishing point."},
                 {"page": 1, "box": [400, 145, 470, 405], "note": "The child is too tall for a three year old."},
                 {"page": 1, "box": [5, 5, 995, 995], "note": "A loop round the whole page says nothing."},
                 {"page": 1, "box": [60, 700, 300, 980], "note": "This part belongs to another drawing."},
                 {"page": 1, "box": [945, 500, 995, 540], "note": "Nothing is drawn in this margin."},
             ]},
            {"qid": "Q 1", "attempted": True, "page": 1, "box": [200, 540, 800, 920],
             "seen": "A round cookie with lettering under it.",
             "criteria": [{"name": "Idea and brand fit", "awarded": 99},
                          {"name": "Form and lettering", "awarded": 3},
                          {"name": "Presentation and finish", "awarded": -2}],
             "feedback": "The symbol is a plain cookie; simplify it into one bold shape.",
             "strengths": [], "improvements": ["Reduce the cookie to a simple mark."],
             "regions": []},
        ],
        "good_at": ["You answer each brief - nothing asked is missing."],
        "to_improve": ["Keep one perspective system; fix the vanishing points first."],
        "overall": "Good understanding. Work on perspective.",
    }
    data.update(overrides)
    return dg.DesignMarkSheet.model_validate(data)


class PaperAndMarks(unittest.TestCase):
    def test_printed_marks_need_their_quote_in_the_paper(self):
        paper = dg.DesignPaper(questions=[
            dg.DesignQuestion(qid="Q1", max_marks=20, marks_quote="(20 Marks)",
                              criteria=[C(name="Idea", weight=50), C(name="Finish", weight=50)]),
            dg.DesignQuestion(qid="Q2", max_marks=30, marks_quote="(30 Marks)",
                              criteria=[C(name="Idea", weight=50), C(name="Finish", weight=50)]),
        ])
        checked = dg.verify_design_paper(paper, "Q1 Design a logo. (20 Marks)\nQ2 Draw a scene.")
        self.assertEqual(checked.questions[0].max_marks, 20)
        self.assertIsNone(checked.questions[1].max_marks, "30 marks is printed nowhere")

    def test_entered_marks_by_order_and_by_name(self):
        self.assertEqual([q.max_marks for q in _paper("15, 20").marked], [15, 20])
        self.assertEqual([q.max_marks for q in _paper("Q2=20, Q1 = 15").marked], [15, 20])

    def test_wrong_count_is_an_operator_error_with_the_question_list(self):
        with self.assertRaises(StrictGradeError) as caught:
            _paper("15, 20, 15")
        self.assertIn("Q1, Q2", str(caught.exception))
        with self.assertRaises(StrictGradeError):
            _paper("15, 20", total=50)          # 35 is not 50
        with self.assertRaises(StrictGradeError):
            _paper("")                          # nothing printed, nothing entered

    def test_total_alone_is_shared_equally_with_a_note(self):
        paper = _paper("", total=40)
        self.assertEqual([q.max_marks for q in paper.marked], [20, 20])
        self.assertTrue(any("shared equally" in note for note in paper.notes))

    def test_criteria_add_up_to_the_question_in_half_marks(self):
        for question in _paper("15, 17.5").marked:
            self.assertAlmostEqual(sum(c.marks for c in question.criteria), question.max_marks)
            self.assertTrue(all((c.marks * 2).is_integer() for c in question.criteria))

    def test_a_question_without_criteria_gets_the_standard_ones(self):
        paper = dg.verify_design_paper(dg.DesignPaper(questions=[dg.DesignQuestion(qid="Q1")]), "")
        self.assertEqual(len(paper.questions[0].criteria), 5)

    def test_multiple_choice_is_left_out_with_a_note(self):
        paper = dg.verify_design_paper(dg.DesignPaper(questions=[
            dg.DesignQuestion(qid="Q1", kind="objective"), dg.DesignQuestion(qid="Q2")]), "")
        self.assertEqual([q.qid for q in paper.marked], ["Q2"])
        self.assertTrue(any("Multiple choice" in note for note in paper.notes))


class Verification(unittest.TestCase):
    def setUp(self):
        self.paper = _paper()
        self.pngs = [_sheet_png()]
        self.report = dg.verify_marks(_marks(), self.paper, self.pngs, student_name="Asha")

    def test_awards_are_clamped_to_the_rubric_in_half_marks(self):
        q1, q2 = self.report.answers
        self.assertEqual((q1.qid, q2.qid), ("Q1", "Q2"))
        self.assertEqual(q1.criteria[0].awarded, q1.criteria[0].max, "99 is clamped to the criterion")
        self.assertEqual(q1.criteria[2].awarded, 0.0, "a negative award is zero")
        self.assertLessEqual(q1.score, 15)
        self.assertEqual(q2.score, 10.5)       # 3.5 + 2.5 + 2.5 (2.25 to the half) + 1 + 1
        self.assertEqual(self.report.total_score, q1.score + q2.score)
        self.assertEqual(self.report.max_total, 35)

    def test_only_loops_round_a_part_of_the_right_drawing_survive(self):
        notes = [r.note for r in self.report.answers[1].regions]
        self.assertEqual(notes, ["The fridge follows a different vanishing point.",
                                 "The child is too tall for a three year old."])
        self.assertEqual([r.number for r in self.report.answers[1].regions], [1, 2])

    def test_nothing_the_student_reads_has_a_semicolon_or_dash(self):
        texts = list(self.report.good_at) + list(self.report.to_improve) + [self.report.overall]
        for answer in self.report.answers:
            texts += [answer.feedback, *answer.strengths, *answer.improvements]
            texts += [r.note for r in answer.regions]
        for text in texts:
            self.assertEqual(wording_problems(text), [], text)

    def test_an_unmarked_question_is_zero_and_asks_for_a_review(self):
        sheet = _marks()
        sheet.answers = [a for a in sheet.answers if dg.normalize_design_qid(a.qid) != "1"]
        report = dg.verify_marks(sheet, self.paper, self.pngs)
        self.assertEqual(report.answers[0].score, 0)
        self.assertTrue(any("Q1 was not marked" in reason for reason in report.review_reasons))

    def test_not_attempted_scores_nothing_whatever_the_awards_say(self):
        sheet = _marks()
        sheet.answers[1].attempted = False
        report = dg.verify_marks(sheet, self.paper, self.pngs)
        self.assertEqual(report.answers[0].score, 0)
        self.assertEqual(report.answers[0].feedback, "Not attempted.")

    def test_a_drawing_box_on_blank_paper_is_not_trusted(self):
        sheet = _marks()
        sheet.answers[1].box = [820, 520, 990, 700]      # empty paper beside the logo
        report = dg.verify_marks(sheet, self.paper, self.pngs)
        self.assertIsNone(report.answers[0].box)

    def test_two_questions_on_one_drawing_ask_for_a_review(self):
        sheet = _marks()
        sheet.answers[1].box = [70, 50, 930, 470]
        report = dg.verify_marks(sheet, self.paper, self.pngs)
        self.assertTrue(any("same drawing" in reason for reason in report.review_reasons))

    def test_criteria_with_other_names_keep_the_total_not_the_split(self):
        sheet = _marks()
        sheet.answers[0].criteria = [dg.CriterionAward(name="Overall", awarded=11)]
        report = dg.verify_marks(sheet, self.paper, self.pngs)
        self.assertEqual(report.answers[1].score, 11)
        self.assertEqual(len(report.answers[1].criteria), 5)


class Pages(unittest.TestCase):
    def test_a_page_is_turned_clockwise_into_one_coordinate_system(self):
        src = fitz.open()
        page = src.new_page(width=200, height=100)
        page.draw_rect(fitz.Rect(0, 0, 30, 30), fill=(0, 0, 0))       # top left corner
        turned = dg.upright_pdf(src, [90])
        self.assertEqual((round(turned[0].rect.width), round(turned[0].rect.height)), (100, 200))
        mask = dg.ink_mask(dg.page_pngs(turned, dpi=72)[0])
        rows, cols = mask.nonzero()
        self.assertGreater(cols.mean() / mask.shape[1], 0.7, "top left goes to the top RIGHT")
        self.assertLess(rows.mean() / mask.shape[0], 0.3)

    def test_both_pictures_of_a_page_are_one_pixel_frame(self):
        for size in ((1200, 1600), (1754, 1240), (3508, 2481), (900, 600)):
            png = _sheet_png(*size)
            locator, frame = dg.locator_image(png)
            judged, media = dg.judging_image(png)
            a, b = Image.open(io.BytesIO(locator)), Image.open(io.BytesIO(judged))
            self.assertEqual(a.size, b.size, "the same pixels in both pictures")
            self.assertEqual(frame, (0, 0) + a.size)
            self.assertLessEqual(max(a.size), dg.PAGE_MAX_EDGE)
            self.assertLessEqual(a.size[0] * a.size[1], dg.PAGE_MAX_PIXELS, "no provider shrinks it")
            self.assertAlmostEqual(a.size[0] / a.size[1], size[0] / size[1], places=2)
            # A part at 25 to 50 percent across and 10 to 40 percent down the page.
            width, height = a.size
            back = dg.from_locator([0.25 * width, 0.10 * height, 0.50 * width, 0.40 * height], frame)
            for got, want in zip(back, [250, 100, 500, 400]):
                self.assertAlmostEqual(got, want, delta=1.0)
        self.assertEqual(Image.open(io.BytesIO(dg.locator_image(_sheet_png(900, 600))[0])).size, (900, 600),
                         "a small page is not enlarged")

    def test_a_box_mostly_off_the_picture_is_no_box(self):
        _, frame = dg.locator_image(_sheet_png(1754, 1240))
        _, _, width, height = frame
        self.assertIsNone(dg.from_locator([width + 10, 5, width + 300, 200], frame))
        clipped = dg.from_locator([100, height - 60, 300, height + 40], frame)
        self.assertEqual(clipped[3], 1000.0, "a box that hangs over the edge is cut to the page")

    def test_pixel_boxes_past_1000_survive_parsing(self):
        region = dg.RegionNote.model_validate({"page": 1, "box": [1100, 200, 1400, 500], "note": "x y"})
        self.assertEqual(region.box, [1100.0, 200.0, 1400.0, 500.0])

    def test_the_upright_panel_is_found_whatever_order_the_panels_are_in(self):
        upright = Image.new("RGB", (400, 600), (245, 245, 245))
        ImageDraw.Draw(upright).rectangle([0, 0, 80, 80], fill=(0, 0, 0))          # top left mark
        scanned = upright.rotate(-90, expand=True)                                 # photographed turned clockwise
        out = io.BytesIO()
        scanned.save(out, format="PNG")
        for page_index in range(4):
            order = dg._PANEL_ORDERS[page_index]
            sheet = Image.open(io.BytesIO(dg.contact_sheet(out.getvalue(), order, panel=300))).convert("L")
            letter = "ABCD"[order.index(270)]                                      # 270 clockwise undoes it
            index = "ABCD".index(letter)
            x0, y0 = 16 + (index % 2) * 316, 16 + (index // 2) * 362 + 46
            panel = sheet.crop((x0, y0, x0 + 300, y0 + 300))
            dark = [(x, y) for x in range(0, 300, 6) for y in range(0, 300, 6) if panel.getpixel((x, y)) < 60]
            self.assertTrue(dark)
            self.assertLess(sum(x for x, _ in dark) / len(dark), 150, "mark is on the left")
            self.assertLess(sum(y for _, y in dark) / len(dark), 120, "mark is at the top")
            turns = dg.turns_from_panels([{"page": page_index + 1, "upright": letter, "judged_by": "label Q1"}], 4)
            self.assertEqual(turns[page_index], 270)

    def test_a_panel_letter_without_what_it_was_judged_by_turns_nothing(self):
        rows = [{"page": 1, "upright": "B", "judged_by": ""}, {"page": 2, "upright": "X", "judged_by": "nothing"},
                {"page": 3, "upright": "Q", "judged_by": "text"}, {"page": 9, "upright": "B", "judged_by": "text"}]
        self.assertEqual(dg.turns_from_panels(rows, 3), [0, 0, 0])

    def test_the_desk_at_the_edge_of_a_photograph_is_not_ink(self):
        image = Image.new("RGB", (1200, 1600), (244, 242, 236))
        ImageDraw.Draw(image).rectangle([1170, 0, 1200, 1600], fill=(205, 110, 40))   # orange desk
        out = io.BytesIO()
        image.save(out, format="PNG")
        self.assertEqual(dg.box_ink(dg.ink_mask(out.getvalue()), [790, 540, 1000, 720]), 0.0)

    def test_names_come_from_file_names_that_are_names(self):
        self.assertEqual(dg.name_from_filename("arijeet_das_NID_mock.pdf"), "Arijeet Das")
        self.assertEqual(dg.name_from_filename("64d7247c-fce9-41fc-8dab-b70c53021d8a.pdf"), "")
        self.assertEqual(dg.name_from_filename("scan_20250621.pdf"), "")


def _looker(kind="drawing_answers", confidence=0.95, name="Asha Rao"):
    def look(pngs, paper, **_):
        return dg.SheetLook(kind=kind, confidence=confidence, student_name=name,
                            what_is_seen="Two hand drawn pencil sketches with labels.")
    return look


def _orienter(turns=None):
    return lambda pngs, **_: list(turns or [0] * len(pngs))


_CONFIRMS = {"answer": "yes", "part": "true", "missing": "absent", "viewpoint": "true"}


def _checker(by_text=None, calls=None):
    """A second examiner that confirms everything (the box shows the answer, the note
    is true, the missing thing is absent) except the claims in `by_text`, a map of
    claim text to (answer, what it sees) or (answer, what it sees, in_view)."""
    def check(pngs, claims, paper, hires=None, **kwargs):
        if calls is not None:
            calls.append({"claims": claims, "stage": kwargs.get("stage", "design_second_look")})
        out = []
        for claim in claims:
            given = (by_text or {}).get(claim.text, (_CONFIRMS[claim.kind], ""))
            answer, sees, in_view = (tuple(given) + (True,))[:3]
            out.append(dg.ClaimVerdict(id=claim.id, answer=answer, sees=sees, in_view=in_view))
        return out
    return check


def _marker(calls=None, sheet=None):
    def mark(pngs, paper, context, **kwargs):
        if calls is not None:
            calls.append(kwargs.get("only_qids"))
        return (sheet or _marks()).model_copy(deep=True)
    return mark


def _grade(sa_bytes=None, **kwargs):
    return dg.grade_design_sheet(
        paper=_paper(), context=dg.PaperContext(text="paper"), sa_bytes=sa_bytes or _sheet_pdf(),
        filename=kwargs.pop("filename", "sheet.pdf"), google_token="t", task_id="task",
        looker=kwargs.pop("looker", _looker()), marker=kwargs.pop("marker", _marker()),
        orienter=kwargs.pop("orienter", _orienter()), checker=kwargs.pop("checker", _checker()), **kwargs)


class OneCopy(unittest.TestCase):
    def test_a_marked_copy_goes_to_faculty_review_with_its_pdf(self):
        stages = []
        result = _grade(stage=lambda state, detail="": stages.append(state))
        self.assertFalse(result["release"], "design marks are not calibrated yet")
        self.assertEqual(result["review_reasons"][0], dg.UNCALIBRATED_REASON)
        self.assertEqual(result["report"].student_name, "Asha Rao")
        self.assertEqual(stages, ["normalizing", "ocr", "answer_mapping", "primary_grading",
                                  "verifying", "aggregating", "rendering", "release_check"])
        doc = fitz.open(stream=result["pdf_bytes"], filetype="pdf")
        self.assertEqual(len(doc), 2, "cover and the student's page")
        cover = doc[0].get_text()
        self.assertIn("Asha Rao", cover)
        self.assertIn(f"{result['report'].total_score:g} out of 35", cover)
        page = doc[1]
        self.assertGreater(page.rect.height, 800, "the feedback panel is added below the page")
        text = page.get_text()
        for expected in ("Q2", "10.5/20", "Low viewpoint", "The fridge follows a different vanishing point."):
            self.assertIn(expected, text)
        self.assertEqual(wording_problems(text.replace("10.5/20", "")), [])
        # The panel's headings in the PW Design & Arch faculty's words (22 Oct 2026).
        for heading in ("Overall feedback", "Good Job", "Needs improvement"):
            self.assertIn(heading, text)
        self.assertNotIn("Work on", text)
        # The feedback words are form fields faculty can edit (23 Oct 2026), filled
        # with the AI's text; since 24 Oct the marks, criteria and loop notes are too.
        fields = {w.field_name: w.field_value for p in doc for w in p.widgets()}
        self.assertTrue({"cover_plus", "cover_improve", "Q2_overall_feedback",
                         "Q2_good_job", "Q2_needs_improvement"} <= set(fields), sorted(fields))
        self.assertIn("vanishing point", fields["Q2_overall_feedback"])
        self.assertTrue(fields["Q2_good_job"].startswith("• "))
        for value in fields.values():
            self.assertEqual(wording_problems(value.replace("• ", "")), [], value)
        self.assertIn("10.5/20", text, "the marks stay printed")

    def test_every_mark_name_and_note_is_editable_and_only_headings_are_printed(self):
        # PW Design & Arch, 24 Oct 2026: "we also need to make those rating points
        # editable. only heading can be non-editable but other texts or numbers
        # should be editable".
        result = _grade()
        doc = fitz.open(stream=result["pdf_bytes"], filetype="pdf")
        fields = {w.field_name: w for p in doc for w in p.widgets()}
        values = {name: w.field_value for name, w in fields.items()}
        report = result["report"]
        q2 = next(a for a in report.answers if a.qid == "Q2")
        self.assertEqual(values["cover_name"], "Asha Rao")
        self.assertEqual(values["cover_total"], f"{report.total_score:g} out of 35")
        self.assertEqual(values["cover_breakdown"], "(" + " + ".join(
            f"{a.qid} {a.score:g}" for a in report.answers) + ")")
        self.assertEqual(values["Q2_marks"], f"{q2.score:g} / {q2.max_marks:g}")
        self.assertEqual(values["Q2_marks_tag"], f"{q2.score:g}/{q2.max_marks:g}")
        for index, criterion in enumerate(q2.criteria, 1):
            self.assertEqual(values[f"Q2_c{index}_name"], criterion.name)
            self.assertEqual(values[f"Q2_c{index}_marks"], f"{criterion.awarded:g} / {criterion.max:g}")
        self.assertEqual(values["Q2_note1"], "The fridge follows a different vanishing point.")
        # The headings are printed, never fields.
        printed = " ".join(page.get_text() for page in doc)
        for heading in ("FEEDBACK ON THIS PAGE", "Overall feedback", "Good Job", "Needs improvement"):
            self.assertIn(heading, printed)
            self.assertNotIn(heading, " ".join(values.values()))
        # The marks keep their bold red look, right-aligned in their column.
        marks = fields["Q2_c1_marks"]
        self.assertEqual(doc.xref_get_key(marks.xref, "Q"), ("int", "2"))
        self.assertIn("/HeBo", doc.xref_get_key(marks.xref, "DA")[1])
        self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), "AcroForm/DR/Font/HeBo")[0], "xref")

    def test_the_totals_follow_an_edited_mark(self):
        # The question's marks, its tag on the drawing and the cover follow a faculty
        # member's edit of a criterion (calculation scripts, run here by JScript).
        from pdf_form_calc import form_scripts, run_calculations
        pdf = _grade()["pdf_bytes"]
        stored, scripts = form_scripts(pdf)
        order = [name for name, _ in scripts]
        self.assertLess(order.index("Q2_marks"), order.index("cover_total"), "questions before the cover")
        self.assertLess(order.index("Q2_marks"), order.index("Q2_marks_tag"))
        unchanged = run_calculations(pdf, {})
        if unchanged is None:
            self.skipTest("cscript (Windows Script Host) is not available")
        keys = ("cover_total", "cover_breakdown", "Q1_marks", "Q1_marks_tag", "Q2_marks", "Q2_marks_tag")
        self.assertEqual({k: unchanged[k] for k in keys}, {k: stored[k] for k in keys},
                         "opening the copy changes nothing")
        q2 = float(stored["Q2_marks"].split("/")[0])
        old = float(stored["Q2_c1_marks"].split("/")[0])
        top = stored["Q2_c1_marks"].split("/")[1].strip()
        edited = run_calculations(pdf, {"Q2_c1_marks": f"{old + 1.5:g} / {top}"})
        new_q2 = f"{q2 + 1.5:g}"
        self.assertEqual(edited["Q2_marks"], f"{new_q2} / 20")
        self.assertEqual(edited["Q2_marks_tag"], f"{new_q2}/20")
        total = float(stored["cover_total"].split()[0]) + 1.5
        self.assertEqual(edited["cover_total"], f"{total:g} out of 35")
        self.assertIn(f"Q2 {new_q2})", edited["cover_breakdown"])
        self.assertEqual(edited["Q1_marks"], stored["Q1_marks"])
        words = run_calculations(pdf, {"Q2_c1_marks": "see me"})
        self.assertEqual(words["Q2_marks"], stored["Q2_marks"], "no number, the total is left alone")

    def test_an_edited_mark_is_saved_with_the_copy(self):
        doc = fitz.open(stream=_grade()["pdf_bytes"], filetype="pdf")
        for page in doc:
            for widget in page.widgets():
                if widget.field_name == "Q2_c1_marks":
                    widget.field_value = "5 / 6"
                    widget.update()
                if widget.field_name == "Q2_c1_name":
                    widget.field_value = "Low viewpoint, checked by faculty"
                    widget.update()
        saved = fitz.open(stream=doc.tobytes(), filetype="pdf")
        values = {w.field_name: w.field_value for p in saved for w in p.widgets()}
        self.assertEqual(values["Q2_c1_marks"], "5 / 6")
        self.assertEqual(values["Q2_c1_name"], "Low viewpoint, checked by faculty")

    def test_feedback_can_be_drawn_as_plain_text(self):
        import design_renderer
        original = design_renderer.EDITABLE_FEEDBACK
        design_renderer.EDITABLE_FEEDBACK = False
        try:
            result = _grade()
        finally:
            design_renderer.EDITABLE_FEEDBACK = original
        doc = fitz.open(stream=result["pdf_bytes"], filetype="pdf")
        self.assertEqual([w for p in doc for w in p.widgets()], [])
        self.assertIn("vanishing point", doc[1].get_text())

    def test_the_stage_names_are_workflow_states(self):
        from automatic_workflow import AutomaticWorkflow, WorkflowState, safe_stage_reporter
        flow = AutomaticWorkflow()
        flow.transition(WorkflowState.PACKAGE_VALIDATING)
        flow.transition(WorkflowState.PACKAGE_VALIDATED)
        _grade(stage=safe_stage_reporter(flow))
        self.assertEqual(flow.state, WorkflowState.RELEASE_CHECK)
        flow.faculty_review("FACULTY_REVIEW", "uncalibrated")

    def test_a_left_out_question_is_asked_for_once_more(self):
        calls = []
        short = _marks()
        short.answers = short.answers[:1]
        result = _grade(marker=_marker(calls, short))
        self.assertEqual(calls, [None, ["Q1"]])
        self.assertTrue(any("Q1 was not marked" in r for r in result["review_reasons"]))

    def test_typed_blank_and_confidently_not_genuine_uploads_are_rejected(self):
        typed = fitz.open()
        page = typed.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 800), "Design is a process of thinking. " * 60, fontsize=10)
        for upload, looker in ((typed.tobytes(), _looker()),
                               (_sheet_pdf(drawings=False), _looker()),
                               (_sheet_pdf(), _looker(kind="written_notes", confidence=0.95))):
            with self.assertRaises(StrictGradeError) as caught:
                _grade(sa_bytes=upload, looker=looker)
            self.assertEqual(caught.exception.code, "NOT_AN_ANSWER_SHEET")
            self.assertIn("No marks were given", str(caught.exception))

    def test_an_unsure_page_check_marks_the_copy_and_tells_the_reviewer(self):
        result = _grade(looker=_looker(kind="other_document", confidence=0.6))
        self.assertTrue(any("page check was unsure" in r for r in result["review_reasons"]))

    def test_a_failed_page_check_never_costs_the_copy(self):
        def broken(*_, **__):
            raise RuntimeError("proxy timeout")
        result = _grade(looker=broken, orienter=broken, checker=broken, filename="riya_sharma.pdf")
        self.assertEqual(result["report"].student_name, "Riya Sharma")
        self.assertEqual(result["turns"], [0])
        self.assertEqual(result["report"].total_score, 19.5)


class SecondLook(unittest.TestCase):
    """What the marker SAYS about a drawing is checked on close-ups before a student
    reads it: what is called missing, and what every loop's note claims."""

    COOKIE = "cookie on top of the fridge"
    JAR = "a jar of cookies stands on top of the fridge, upper right"
    POSE = "Reem stands beside the chair, not climbing and reaching upward."
    KNEE = "one knee is raised onto the chair seat and both hands grip the chair back, she is climbing"

    def _first(self):
        sheet = _marks()
        q2 = sheet.answers[0]
        q2.missing = [self.COOKIE, "drawings stuck on the fridge"]
        q2.feedback = "You missed the cookie on the fridge. Reem only stands beside the chair."
        q2.regions[1].note = self.POSE
        return sheet

    def _remarking(self, calls, second):
        first = self._first()

        def mark(pngs, paper, context, **kwargs):
            calls.append(kwargs)
            return (second if kwargs.get("only_qids") else first).model_copy(deep=True)
        return mark

    def _corrected(self):
        second = _marks()
        second.answers = [second.answers[0]]
        second.answers[0].missing = ["drawings stuck on the fridge"]
        second.answers[0].feedback = "Reem is climbing the chair and the cookie jar is on the fridge."
        second.answers[0].regions = second.answers[0].regions[:1]
        second.answers[0].criteria[1].awarded = 3.0
        return second

    def test_every_box_every_loop_note_and_every_missing_thing_is_a_claim(self):
        claims = dg.collect_claims(self._first(), 1)
        self.assertEqual([c.id for c in claims], list(range(1, len(claims) + 1)))
        self.assertEqual([c.qid for c in claims if c.kind == "answer"], ["Q2", "Q 1"])
        self.assertEqual([c.text for c in claims if c.kind == "missing"],
                         [self.COOKIE, "drawings stuck on the fridge"])
        parts = [c for c in claims if c.kind == "part"]
        self.assertIn(self.POSE, [c.text for c in parts])
        self.assertTrue(all(c.box and c.page == 1 for c in parts))

    def test_a_misread_pose_and_a_found_missing_thing_send_the_question_back_with_close_ups(self):
        calls, asked = [], []
        disputes = {self.COOKIE: ("present", self.JAR), self.POSE: ("false", self.KNEE)}
        result = _grade(marker=self._remarking(calls, self._corrected()), checker=_checker(disputes, asked))
        self.assertEqual(len(calls), 2)
        again = calls[1]
        self.assertEqual((again["only_qids"], again["only_pages"], again["stage"]), (["Q2"], [1], "design_remark"))
        notes = " ".join(again["second_look"]["Q2"])
        self.assertIn(self.JAR, notes)
        self.assertIn(self.KNEE, notes)
        self.assertIn("The second examiner finds it", notes)
        self.assertEqual(len(again["closeups"]["Q2"]), 2, "one close-up per disputed statement")
        for shot in again["closeups"]["Q2"]:
            self.assertGreaterEqual(max(Image.open(io.BytesIO(shot)).size), dg.CLOSEUP_MIN_EDGE)
        q2 = result["report"].answers[1]
        self.assertIn("Reem is climbing the chair", q2.feedback)
        self.assertEqual(q2.score, 11.0)                   # 3.5 + 3 + 2.5 + 1 + 1
        self.assertFalse(any("disagree" in r for r in result["review_reasons"]))
        log = result["second_look"]
        self.assertTrue(any(row["claim"] == self.POSE and row["verdict"] == "false" for row in log))
        self.assertTrue(any(row["kind"] == "re-marked" for row in log))
        self.assertEqual([c["stage"] for c in asked], ["design_second_look", "design_recheck"],
                         "the re-marked question's new boxes are checked for place once")
        self.assertEqual({c.kind for c in asked[1]["claims"]}, {"answer", "part"})

    def test_examiners_who_still_disagree_go_to_the_reviewer_with_the_evidence(self):
        stubborn = self._first()
        stubborn.answers = [stubborn.answers[0]]
        result = _grade(marker=self._remarking([], stubborn),
                        checker=_checker({self.POSE: ("false", self.KNEE)}))
        reason = next(r for r in result["review_reasons"] if "disagree" in r)
        self.assertIn("stands beside the chair", reason)
        self.assertIn("one knee is raised onto the chair seat", reason)

    def test_confirming_a_missing_thing_is_never_a_dispute(self):
        """Live on build 63: the checker 'disagreed' with 'drawings on the fridge are
        missing' while writing that it saw none. A find answers present or absent."""
        calls = []
        result = _grade(marker=self._remarking(calls, _marks()), checker=_checker({
            "drawings stuck on the fridge": ("absent", "no drawings or papers are on the fridge door")}))
        self.assertEqual(len(calls), 1)
        self.assertFalse(any("disagree" in r for r in result["review_reasons"]))

    def test_no_re_mark_when_the_second_examiner_confirms_is_unsure_or_cannot_say_what_it_sees(self):
        for disputes in ({}, {self.POSE: ("unsure", "")}, {self.POSE: ("false", "no")},
                         {self.COOKIE: ("absent", "")}, {self.COOKIE: ("present", "yes")}):
            calls = []
            _grade(marker=self._remarking(calls, _marks()), checker=_checker(disputes))
            self.assertEqual(len(calls), 1, disputes)

    def test_a_box_that_shows_something_else_is_dropped(self):
        sheet = self._first()
        seen = sheet.answers[1].seen
        result = _grade(marker=_marker(sheet=sheet), checker=_checker({
            seen: ("no", "this area shows the kitchen scene, not the logo"),
            self.POSE: ("unsure", "the close up shows empty floor tiles", False)}))
        q1, q2 = result["report"].answers
        self.assertIsNone(q1.box, "no marks tag goes where the logo is not")
        self.assertEqual(q1.score, 9.0, "the marks stand, only the place is dropped")
        self.assertNotIn(self.POSE, [r.note for r in q2.regions], "a loop round the wrong thing is dropped")
        self.assertTrue(any(row["verdict"].startswith("box dropped") for row in result["second_look"]))

    def test_a_find_on_a_box_that_shows_another_answer_does_not_count(self):
        calls = []
        sheet = self._first()
        _grade(marker=self._remarking(calls, _marks()), checker=_checker({
            sheet.answers[0].seen: ("no", "this area shows the logo, not the kitchen"),
            self.COOKIE: ("present", "a jar of cookies stands on the shelf")}))
        self.assertEqual(len(calls), 1)

    def test_a_close_up_is_the_part_with_some_surround_and_never_tiny(self):
        shot = Image.open(io.BytesIO(dg.closeup(_sheet_png(), [400, 145, 470, 405])))
        self.assertEqual(shot.height, 707, "260 units of a 1600 pixel page, plus 35 percent each side")
        self.assertGreater(shot.height, shot.width, "the child is taller than wide")
        small = Image.open(io.BytesIO(dg.closeup(_sheet_png(), [400, 145, 440, 245])))
        self.assertEqual(max(small.size), dg.CLOSEUP_MIN_EDGE, "a small part is enlarged")
        whole = Image.open(io.BytesIO(dg.closeup(_sheet_png(), None)))
        self.assertEqual(max(whole.size), dg.CLOSEUP_MAX_EDGE)

    def test_a_sheet_with_nothing_to_check_costs_no_second_call(self):
        bare = _marks()
        for answer in bare.answers:
            answer.regions, answer.missing, answer.box = [], [], None
        asked = []
        _grade(marker=_marker(sheet=bare), checker=_checker({}, asked))
        self.assertEqual(asked, [])

    def test_with_only_boxes_the_second_look_only_checks_where_they_are(self):
        placed = _marks()
        for answer in placed.answers:
            answer.regions, answer.missing = [], []
        asked = []
        _grade(marker=_marker(sheet=placed), checker=_checker({}, asked))
        self.assertEqual([c.kind for c in asked[0]["claims"]], ["answer", "answer"])


class Standard(unittest.TestCase):
    """The marking standard's own rules, applied as arithmetic."""

    def test_main_demand_not_met_caps_its_criterion_and_the_question(self):
        sheet = _marks()
        q2 = sheet.answers[0]
        q2.main_demand_met, q2.main_demand_criterion = False, "Low viewpoint"
        for award, value in zip(q2.criteria, (5, 4, 5, 2, 2)):       # 18 of 20 claimed
            award.awarded = value
        answer = dg.verify_marks(sheet, _paper(), [_sheet_png()]).answers[1]
        self.assertEqual(answer.criteria[0].awarded, 2.0, "40 percent of 6, in half marks")
        self.assertEqual(answer.score, 10.0, "at most half of 20")
        for criterion in answer.criteria:
            self.assertGreaterEqual(criterion.awarded, 0)
            self.assertLessEqual(criterion.awarded, criterion.max)

    def test_main_demand_met_changes_nothing(self):
        self.assertEqual(dg.verify_marks(_marks(), _paper(), [_sheet_png()]).answers[1].score, 10.5)

    def test_a_fault_of_the_whole_viewpoint_is_never_looped(self):
        sheet = _marks()
        sheet.answers[0].regions[0].note = "Child and chair drawn at standing eye level, not towering as seen from the floor."
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        self.assertEqual([r.note for r in report.answers[1].regions],
                         ["The child is too tall for a three year old."])

    def test_what_is_absent_is_never_looped(self):
        sheet = _marks()
        sheet.answers[0].regions[0].note = "Rear of shoe left plain where wings or rockets were required."
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        self.assertEqual([r.note for r in report.answers[1].regions],
                         ["The child is too tall for a three year old."])

    def test_not_attempted_is_said_once_under_the_last_page(self):
        sheet = _marks()
        sheet.answers[1].attempted, sheet.answers[1].page = False, 1      # the marker filed it under page 1
        report = dg.verify_marks(sheet, _paper(), [_sheet_png(), _sheet_png()])
        self.assertEqual((report.answers[0].page, report.answers[0].score), (0, 0))
        pdf = build_design_pdf(fitz.open(stream=_sheet_pdf(pages=2), filetype="pdf"), report,
                               [_sheet_png(), _sheet_png()])
        doc = fitz.open(stream=pdf, filetype="pdf")
        self.assertEqual(len(doc), 3, "cover and the two pages, no page of its own")
        self.assertNotIn("Not attempted", doc[1].get_text(), "not beside the Q2 drawing")
        last = doc[2].get_text()
        self.assertIn("Q1", last)
        self.assertIn("Not attempted", last)


class ScanTalk(unittest.TestCase):
    """How a page was photographed is never the student's fault, or the student's news."""

    def test_sentences_about_the_scan_are_cut_and_the_rest_stays(self):
        sheet = _marks()
        sheet.answers[0].feedback = ("Your cabinets are well constructed. Write the page upright so the notes "
                                     "read clearly. The fridge stands upright but follows another vanishing point.")
        sheet.answers[0].improvements = ["Present the notes upright and organised instead of sideways.",
                                         "Add shadows under the chair so it sits on the floor."]
        sheet.to_improve = ["Plan composition and orient the page so notes read upright.",
                            "Place the slogan boldly and horizontally for readability.",
                            "Keep one perspective system."]
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        q2 = report.answers[1]
        self.assertEqual(q2.feedback, "Your cabinets are well constructed. The fridge stands upright "
                                      "but follows another vanishing point.")
        self.assertEqual(q2.improvements, ["Add shadows under the chair so it sits on the floor."])
        self.assertEqual(report.to_improve, ["Keep one perspective system."])

    def test_a_page_the_marker_still_saw_sideways_asks_for_a_review(self):
        sheet = _marks()
        sheet.sideways_pages = [1, 7]
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        self.assertTrue(any("page 1 sideways" in r for r in report.review_reasons))

    def test_the_prompt_forbids_it_and_asks_for_missing_claims_apart(self):
        prompt = dg.DESIGN_MARKING_SYSTEM_PROMPT
        for needle in ("THE SCAN IS NOT THE STUDENT'S WORK", "LOOK BEFORE YOU CALL ANYTHING MISSING",
                       "LOOK CLOSELY BEFORE YOU DESCRIBE", "Something that is absent has no region",
                       "THE MAIN DEMAND", "YOUR MARKS MUST AGREE WITH YOUR WORDS", "LOCATOR"):
            self.assertIn(needle, prompt)
        answer = dg._marks_schema()["$defs"]["_Answer"]["properties"]
        self.assertIn("missing", answer)
        self.assertIn("main_demand_met", answer)
        self.assertNotIn("why", dg._marks_schema()["$defs"]["_Award"]["properties"],
                         "a reason per criterion was paid for and shown nowhere")

    def test_boxes_come_back_from_the_locator_as_page_boxes(self):
        _, frame = dg.locator_image(_sheet_png())
        left, top, width, height = frame
        sheet = dg.DesignMarkSheet.model_validate({"answers": [{
            "qid": "Q2", "page": 1, "box": [left, top, left + width, top + height / 2],
            "regions": [{"page": 1, "box": [left + width / 2, top, left + width, top + height / 4], "note": "x y"},
                        {"page": 1, "box": [width + 20, 2, width + 90, 60], "note": "off the page"}]}]})
        dg._to_page_boxes(sheet, {1: frame})
        self.assertEqual(sheet.answers[0].box, [0.0, 0.0, 1000.0, 500.0])
        self.assertEqual(sheet.answers[0].regions[0].box, [500.0, 0.0, 1000.0, 250.0])
        self.assertIsNone(sheet.answers[0].regions[1].box)

    def test_sideways_pages_are_marked_and_drawn_upright(self):
        doc = fitz.open()
        page = doc.new_page(width=800, height=600)
        sideways = Image.open(io.BytesIO(_sheet_png())).rotate(90, expand=True)   # counter clockwise
        out = io.BytesIO()
        sideways.save(out, format="PNG")
        page.insert_image(page.rect, stream=out.getvalue())
        seen = {}

        def mark(pngs, paper, context, **_):
            seen["size"] = Image.open(io.BytesIO(pngs[0])).size
            return _marks()

        result = _grade(sa_bytes=doc.tobytes(), orienter=_orienter([90]), marker=mark)
        self.assertGreater(seen["size"][1], seen["size"][0], "the marker is shown a portrait page")
        evaluated = fitz.open(stream=result["pdf_bytes"], filetype="pdf")
        self.assertLess(evaluated[1].rect.width, 700)
        self.assertEqual(len(result["report"].answers[1].regions), 2, "boxes still land on ink")


class Rendering(unittest.TestCase):
    def test_the_plain_cover_is_used_when_the_template_is_missing(self):
        import design_renderer
        report = dg.verify_marks(_marks(), _paper(), [_sheet_png()], student_name="Asha")
        saved = design_renderer.COVER_TEMPLATE_CANDIDATES
        design_renderer.COVER_TEMPLATE_CANDIDATES = ()
        try:
            pdf = build_design_pdf(fitz.open(stream=_sheet_pdf(), filetype="pdf"), report, [_sheet_png()])
        finally:
            design_renderer.COVER_TEMPLATE_CANDIDATES = saved
        cover = fitz.open(stream=pdf, filetype="pdf")[0].get_text()
        for expected in ("OVERALL", "FEEDBACK", "plus", "improve", "Asha"):
            self.assertIn(expected, cover)

    def test_an_unlocated_answer_still_gets_its_feedback_on_a_page(self):
        sheet = _marks()
        sheet.answers[1].page = 0
        report = dg.verify_marks(sheet, _paper(), [_sheet_png()])
        self.assertTrue(any("not located" in r for r in report.review_reasons))
        pdf = build_design_pdf(fitz.open(stream=_sheet_pdf(), filetype="pdf"), report, [_sheet_png()])
        doc = fitz.open(stream=pdf, filetype="pdf")
        self.assertEqual(len(doc), 3)
        self.assertIn("simplify it into one bold shape", doc[2].get_text().replace("\n", " ").lower())

    def test_a_marks_tag_never_covers_the_students_writing(self):
        dense = Image.new("RGB", (1200, 1600), (244, 242, 236))
        draw = ImageDraw.Draw(dense)
        for y in range(0, 1600, 9):                       # writing everywhere on the page
            draw.line([0, y, 1200, y + 4], fill=(40, 40, 40), width=4)
        out = io.BytesIO()
        dense.save(out, format="PNG")
        doc = fitz.open()
        doc.new_page(width=600, height=800).insert_image(fitz.Rect(0, 0, 600, 800), stream=out.getvalue())
        report = dg.verify_marks(_marks(), _paper(), [out.getvalue()])
        report.answers[1].box = [100, 100, 700, 500]      # located, but no free corner anywhere
        pdf = build_design_pdf(fitz.open(stream=doc.tobytes(), filetype="pdf"), report, [out.getvalue()])
        page = fitz.open(stream=pdf, filetype="pdf")[1]
        fields = {w.field_name: w.field_value for w in page.widgets()}
        self.assertNotIn("10.5/20", page.get_text(), "no tag over the writing")
        self.assertNotIn("Q2_marks_tag", fields, "no tag over the writing")
        self.assertEqual(fields.get("Q2_marks"), "10.5 / 20", "the marks are still in the panel")

    def test_the_template_is_bundled_into_the_exe(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "assets", "design_feedback_cover.pdf")))
        with open(os.path.join(ROOT, "AICheckerClient.spec"), encoding="utf-8") as handle:
            spec = handle.read()
        self.assertIn("('assets', 'assets')", spec)
        for module in ("design_grading", "design_renderer"):
            self.assertIn(f"'{module}'", spec)


class ServerRoute(unittest.TestCase):
    def test_the_route_and_the_ui_are_wired(self):
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as handle:
            server = handle.read()
        for needle in ('"/api/evaluate-design"', '"/api/design/read-paper"', "def _run_design_job"):
            self.assertIn(needle, server)
        with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as handle:
            script = handle.read()
        for needle in ("/api/evaluate-design", "/api/design/read-paper", "refreshDesignChecklist"):
            self.assertIn(needle, script)
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as handle:
            page = handle.read()
        for needle in ('id="paperType"', 'id="readDesignPaper"', 'id="designTable"'):
            self.assertIn(needle, page)

    def test_a_design_job_ends_in_faculty_review_and_downloads(self):
        import server
        paper = dg.verify_design_paper(dg.DesignPaper(title="NID MOCK", questions=[
            dg.DesignQuestion(qid="Q1", criteria=[C(name="Idea and brand fit", weight=40),
                                                   C(name="Form and lettering", weight=40),
                                                   C(name="Presentation and finish", weight=20)]),
            dg.DesignQuestion(qid="Q2", criteria=[C(name="Low viewpoint", weight=30),
                                                   C(name="Required elements", weight=20),
                                                   C(name="Perspective and proportion", weight=30),
                                                   C(name="Composition", weight=10),
                                                   C(name="Presentation and finish", weight=10)]),
        ]), "")
        saved = (server._read_design_paper_cached, dg.look_at_sheet, dg.mark_drawings,
                 dg.find_upright, dg.check_claims)
        server._read_design_paper_cached = lambda *a, **k: (paper.model_copy(deep=True),
                                                           dg.PaperContext(text="paper"))
        dg.look_at_sheet = _looker()
        dg.mark_drawings = _marker()
        dg.find_upright = _orienter()
        dg.check_claims = _checker()
        job_id = "designjobtest"
        typed = fitz.open()
        typed.new_page().insert_textbox(fitz.Rect(40, 40, 560, 800), "Typed notes on design. " * 80, fontsize=10)
        try:
            server.JOBS[job_id] = {
                "status": "running", "mode": "design", "total": 2, "done": 0, "current": "",
                "error": None, "results": [], "pdfs": {}, "reports": {}, "key": "k",
                "created": 0.0, "state": "received",
                "workflow": server.AutomaticWorkflow().snapshot(), "workflows": {}, "package_id": None}
            server._run_design_job(dict(
                job_id=job_id, qp_bytes=b"%PDF", qp_name="paper.pdf", ak_bytes=None, ak_name=None,
                sheets=[("asha.pdf", _sheet_pdf()), ("notes.pdf", typed.tobytes())],
                question_marks="Q1=15, Q2=20", total_marks=35.0, rate=94.0,
                google_token="t", task_id="task"))
            job = server.JOBS[job_id]
            self.assertEqual(job["status"], "done")
            self.assertEqual(job["state"], "faculty_review")
            by_name = {r["student"]: r for r in job["results"]}
            marked = by_name["asha.pdf"]
            self.assertEqual((marked["state"], marked["release"], marked["max"]), ("faculty_review", False, 35))
            self.assertEqual(marked["review_reasons"][0], dg.UNCALIBRATED_REASON)
            self.assertIn("second_look", job["reports"][marked["index"]])
            self.assertEqual([q["q"] for q in marked["questions"]], ["Q1", "Q2"])
            self.assertEqual(by_name["notes.pdf"]["state"], "rejected")
            response = server.job_pdf(job_id, marked["index"], k="k")
            self.assertIn("FACULTY_REVIEW_asha.pdf", response.headers["content-disposition"])
            self.assertTrue(response.body.startswith(b"%PDF"))
        finally:
            (server._read_design_paper_cached, dg.look_at_sheet, dg.mark_drawings,
             dg.find_upright, dg.check_claims) = saved
            server.JOBS.pop(job_id, None)
            try:
                server._JOB_REPOSITORY.delete(job_id)
            except Exception:
                pass

    def test_marks_that_do_not_fit_the_paper_stop_the_batch_for_the_operator(self):
        import server
        paper = dg.verify_design_paper(dg.DesignPaper(questions=[dg.DesignQuestion(qid="Q1"),
                                                                 dg.DesignQuestion(qid="Q2")]), "")
        saved = server._read_design_paper_cached
        server._read_design_paper_cached = lambda *a, **k: (paper.model_copy(deep=True), dg.PaperContext())
        job_id = "designjobbad"
        try:
            server.JOBS[job_id] = {
                "status": "running", "total": 1, "done": 0, "current": "", "error": None,
                "results": [], "pdfs": {}, "reports": {}, "key": "k", "created": 0.0,
                "state": "received", "workflow": server.AutomaticWorkflow().snapshot(),
                "workflows": {}, "package_id": None}
            server._run_design_job(dict(
                job_id=job_id, qp_bytes=b"%PDF", qp_name="paper.pdf", ak_bytes=None, ak_name=None,
                sheets=[("asha.pdf", _sheet_pdf())], question_marks="15, 20, 15",
                total_marks=None, rate=94.0, google_token="t", task_id="task"))
            job = server.JOBS[job_id]
            self.assertEqual((job["status"], job["state"]), ("error", "failed"))
            self.assertIn("Q1, Q2", job["error"])
            self.assertEqual(job["results"], [])
        finally:
            server._read_design_paper_cached = saved
            server.JOBS.pop(job_id, None)
            try:
                server._JOB_REPOSITORY.delete(job_id)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=1)
