# -*- coding: utf-8 -*-
"""Radiant 2.0 ICSE Class 9 Subjective Test 02 review (SST 30, Economics 10), 2026-10-01.

Every case is a real defect from that batch, with the OCR lines copied from the
saved reports (out_SST/_reports/*.json.gz):

1. MCQ marks moved onto the fill-in Q6's "a) Kalibangan" ... lines (Aradhya, Agrata,
   Avika, Aryan, Isha, Digeshwar), and preflight then held the copy.
2. The key page printed Q4 (B) while its own worked solution is option (A)'s text.
3. A typed syllabus / circular / receipt with a small logo went to faculty review as
   "PHOTO_TOO_SMALL" instead of being rejected as a printed document.
4. "a)" read by OCR as "d)." under Q7, so Q7(d) was marked against Q7(a)'s answer.
5. "Any three features" marked against the key's three points only.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz  # noqa: E402

import grader  # noqa: E402
import pipeline  # noqa: E402
import preflight  # noqa: E402
import server  # noqa: E402
from answer_map import build_answer_map  # noqa: E402
from evidence_repair import repair_report_evidence  # noqa: E402
from exam_package import build_exam_package  # noqa: E402
from grader import Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402
from key_answers import correct_key_against_solutions, solution_supported_options  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402
from strict_grading import apply_official_objective_evidence, option_texts_from_paper  # noqa: E402

# The paper's own text layer: Q1-Q6, its "Answer Key" page and its "Hints &
# Solutions" page, exactly as PyMuPDF extracted them.
PAPER = """Foundation
Subjective Test 02
Q1
Which feature of the Harappan cities most clearly
indicates planned urban development?
(A) Fortified citadel and lower town
(B) Separate royal palace complex
(C) Large temple-centred settlement
(D) Circular arrangement of streets
Q2
Which Harappan site is well known for evidence
of a dockyard?
(A) Kalibangan
(B) Lothal
(C) Banawali
(D) Rakhigarhi
Q3
Which statement correctly describes the shape
of the Earth?
(A) It is flattened at the equator and bulging at
the poles.
(B) It is flattened at the poles and bulging at the
equator.
(C) Its polar and equatorial diameters are exactly
equal.
(D) Its equatorial diameter is smaller than its
polar diameter.
Q4
Which condition is mainly responsible for the
presence of water in liquid form on Earth?
(A) Suitable distance from the Sun
(B) Presence of a solid inner core
(C) Unequal length of day and night
(D) Presence of mountains and plains
Q5
Which
constitutional
provision
gives
every
eligible adult citizen the right to vote?
(A) Single Citizenship
(B) Fundamental Rights
(C) Universal Adult Franchise
(D) Directive Principles
Q6
(a) The Harappan site where evidence of a
ploughed field has been found is __________.
(b) The large public water structure discovered at
Mohenjo-daro is known as the __________.
(c) The Earth is the __________ planet from the
Sun.
(d) The Constitution of India was adopted on
__________.
(e) The Constitution of India came into force on
__________.
Foundation
Answer Key
Q1
(A)
Q2
(B)
Q3
(B)
Q4
(B)
Q5
(C)
Q6
Refer Hint and Solutions
  Master NCERT with PW Books APP
Foundation
Hints & Solutions
Q1
Text Solution:
Fortified citadel and lower town
Q2
Text Solution:
Lothal
Q3
Text Solution:
It is flattened at the poles and bulging at the
equator.
Q4
Text Solution:
Suitable distance from the Sun
Q5
Text Solution:
Universal Adult Franchise
Q6
Text Solution:
(a) Kalibangan
(b) Great Bath
(c) third
(d) 26 November 1949
(e) 26 January 1950
"""


def _package(key_text=PAPER, paper_text=PAPER):
    items = [MarksItem(qid=f"Q{n}", max=1) for n in range(1, 6)]
    items += [MarksItem(qid=f"Q6.{c}", max=1) for c in "abcde"]
    items += [MarksItem(qid=f"Q7.{c}", max=m) for c, m in zip("abcd", (2, 2, 3, 3))]
    items += [MarksItem(qid=f"Q9.{c}", max=m) for c, m in zip("abcd", (2, 2, 3, 3))]
    return build_exam_package(
        exam_type="", student_class="Class 9", subject="Social Science", level="Class 9",
        paper="Subjective Test 02", attempt="", applicable_law_date=None, total_marks=30,
        question_paper=b"sst-paper", answer_key=b"sst-paper", key_source="upload",
        marks_scheme=MarksScheme(total=30, items=items), scheme_source="text",
        question_paper_text=paper_text, answer_key_text=key_text)


def _doc(pages=1, width=600, height=797):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    return doc


def _lines(page, rows):
    return [LineBox(lid, text, tuple(float(v) for v in box), page) for lid, box, text in rows]


# Aradhya, page 1 (3200 x 4250 px): Mathpix lines P1L6-P1L22.
ARADHYA_P1 = [
    ("P1L6", (436, 782, 651, 888), "MCQ"),
    ("P1L7", (1458, 786, 1931, 944), "Section-A"),
    ("P1L8", (258, 865, 2061, 1162), "Q1) (A) Eortistied citadel and lower town"),
    ("P1L9", (249, 992, 865, 1155), "Q2) (B) Lothal"),
    ("P1L10", (224, 1105, 3003, 1456), "Q3) (B) It is flattened at the poles and beloing at the equates."),
    ("P1L11", (214, 1236, 1933, 1489), "Q 7) (A) Suitable distance from the sun"),
    ("P1L12", (210, 1352, 1891, 1599), "Q5)(C) universal Adult tronchise"),
    ("P1L13", (191, 1613, 320, 1723), "Q 6"),
    ("P1L14", (249, 1727, 904, 1922), "a) Kalibangan"),
    ("P1L15", (231, 1850, 936, 2007), "b) Great Bath"),
    ("P1L16", (245, 1978, 664, 2117), "c) third"),
    ("P1L17", (253, 2096, 1321, 2262), "a) 26 Now 1949"),
    ("P1L18", (235, 2217, 1253, 2408), "C) 26 Гапиагу 1050"),
    ("P1L19", (152, 2526, 301, 2617), "Q7"),
    ("P1L20", (210, 2617, 3116, 2787), "d). Archeological remains, swhos buildings, pottery and toos"),
    ("P1L21", (249, 2735, 2957, 2878), "- seals and other inseribed objects diseovered at"),
    ("P1L22", (365, 2884, 1373, 3032), "harruppon sites."),
]


def _aradhya_report(objective_anchors):
    """Q1-Q5 objective rows on the given lines; Q6(a)-(e) cited on their own lines."""
    chosen = {"Q1": "A", "Q2": "B", "Q3": "B", "Q4": "A", "Q5": "C"}
    questions = [QuestionMark(qid=qid, score=1, max_score=1, page=1, y_fraction=.3,
                              objective=True, correct_option=chosen[qid],
                              chosen_option=chosen[qid], anchor_line_id=objective_anchors[qid])
                 for qid in ("Q1", "Q2", "Q3", "Q4", "Q5")]
    for letter, lid, words in zip("abcde", ("P1L14", "P1L15", "P1L16", "P1L17", "P1L18"),
                                  ("Kalibangan", "Great Bath", "third", "26 Nov 1949", "26 January 1950")):
        questions.append(QuestionMark(
            qid=f"Q6.{letter}", score=1, max_score=1, page=1, y_fraction=.5, anchor_line_id=lid,
            criteria=[Criterion(description=words, awarded=1, max=1, met=True,
                                step_line_id=lid, anchor_quote=words.split()[0])]))
    regions = {f"q6.{c}": {"qid": f"Q6.{c}", "status": "located", "pages": [1],
                           "line_ids": [lid], "label_line_ids": [lid]}
               for c, lid in zip("abcde", ("P1L14", "P1L15", "P1L16", "P1L17", "P1L18"))}
    return GradeReport(total_score=10, max_total=10, overall_remarks="", questions=questions,
                       evidence_contract=True, answer_regions=regions)


class McqAnchorsStayOnTheirOwnLines(unittest.TestCase):
    """Issue 1: evidence_repair._anchor_mcq_block took Q6's part lines as the MCQ block."""

    def setUp(self):
        self.ocr = [PageOCR(1, 3200, 4250, _lines(1, ARADHYA_P1))]

    def _anchors(self, report):
        return {q.qid: q.anchor_line_id for q in report.questions if q.objective}

    def test_numbered_mcq_lines_are_not_traded_for_q6_parts(self):
        # Before the fix: Q1 -> P1L14 "a) Kalibangan", Q3 -> P1L16 "c) third",
        # Q4 -> P1L17 "a) 26 Now 1949", and preflight held the copy.
        report = _aradhya_report({"Q1": "P1L8", "Q2": "P1L9", "Q3": "P1L10",
                                  "Q4": "P1L11", "Q5": "P1L12"})
        repair_report_evidence(report, self.ocr, _doc())
        self.assertEqual(self._anchors(report), {"Q1": "P1L8", "Q2": "P1L9", "Q3": "P1L10",
                                                 "Q4": "P1L11", "Q5": "P1L12"})
        issues = preflight._check_objective_anchor_ownership(report, self.ocr, paper_text=PAPER)
        self.assertEqual([i.detail for i in issues if i.code == "objective-anchor-mismatch"], [])

    def test_an_mcq_anchored_on_a_q6_part_moves_back_to_its_own_numbered_line(self):
        report = _aradhya_report({"Q1": "P1L14", "Q2": "P1L9", "Q3": "P1L16",
                                  "Q4": "P1L11", "Q5": "P1L12"})
        repair_report_evidence(report, self.ocr, _doc())
        anchors = self._anchors(report)
        self.assertEqual(anchors["Q1"], "P1L8")
        self.assertEqual(anchors["Q3"], "P1L10")
        self.assertFalse({"P1L14", "P1L15", "P1L16", "P1L17", "P1L18"} & set(anchors.values()))
        # The Q6 parts keep their own lines.
        self.assertEqual({q.qid: q.anchor_line_id for q in report.questions if not q.objective},
                         {"Q6.a": "P1L14", "Q6.b": "P1L15", "Q6.c": "P1L16",
                          "Q6.d": "P1L17", "Q6.e": "P1L18"})

    def test_another_questions_number_never_takes_an_mcq(self):
        # Isha (1240 x 1753 px): "6. a> Kalibanga" took Q1 off "1. a) Fortified ...".
        rows = [("P1L2", (311, 530, 443, 565), "Section - A"),
                ("P1L3", (264, 580, 819, 641), "1. a) Fortified citadel and lower town"),
                ("P1L4", (263, 606, 467, 643), "2 b) Lothal"),
                ("P1L5", (270, 628, 885, 699), r"3. b) \( Y t \) is filatined at the surred or"),
                ("P1L6", (259, 662, 760, 722), "4. b) Suitable distance from sub"),
                ("P1L7", (255, 690, 767, 749), "5. c) Universal Adult Franchibe"),
                ("P1L8", (258, 718, 520, 768), "6. a> Kalibanga"),
                ("P1L9", (299, 752, 544, 790), "b) Great Bath"),
                ("P1L10", (271, 782, 459, 814), "- () third")]
        ocr = [PageOCR(1, 1240, 1753, _lines(1, rows))]
        questions = [QuestionMark(qid=f"Q{n}", score=1, max_score=1, page=1, y_fraction=.3,
                                  objective=True, chosen_option="A", anchor_line_id=lid)
                     for n, lid in ((1, "P1L3"), (2, "P1L4"), (3, "P1L5"), (4, "P1L6"), (5, "P1L7"))]
        questions.append(QuestionMark(qid="Q6.a", score=1, max_score=1, page=1, y_fraction=.4,
                                      anchor_line_id="P1L8"))
        report = GradeReport(total_score=6, max_total=6, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions={})
        repair_report_evidence(report, ocr, _doc(width=600, height=848))
        self.assertEqual({q.qid: q.anchor_line_id for q in questions if q.objective},
                         {"Q1": "P1L3", "Q2": "P1L4", "Q3": "P1L5", "Q4": "P1L6", "Q5": "P1L7"})

    def test_q_label_without_punctuation_is_still_the_questions_own_line(self):
        # Avika (1241 x 1754 px): "Q1 a) Fortified ..." has no ")" after the 1, and
        # Q1 had been put on "b) The Great Bath" (Q6.b's line).
        rows = [("P1L4", (108, 250, 860, 318), "Q1 a) Fortified citadel and doner stown"),
                ("P1L5", (97, 330, 334, 377), "Q2 b) Lothal"),
                ("P1L6", (92, 408, 1093, 473), "Q3 b) it is flattened at pales and bulging at Earth"),
                ("P1L7", (91, 490, 797, 554), "Q4 a) Suitable distance from Sun."),
                ("P1L8", (89, 575, 682, 633), "Q5 (1) Uniuersal Adult Franchise"),
                ("P1L9", (89, 655, 369, 717), "Q60) Kalibongan"),
                ("P1L10", (114, 745, 456, 799), "b) The Great Bath"),
                ("P1L11", (114, 833, 272, 884), "c) third"),
                ("P1L12", (113, 923, 533, 974), "d) 26 No 1949"),
                ("P1L13", (109, 1010, 530, 1090), "C) 26 January, 1950")]
        ocr = [PageOCR(1, 1241, 1754, _lines(1, rows))]
        anchors = {1: "P1L10", 2: "P1L5", 3: "P1L12", 4: "P1L7", 5: "P1L8"}
        questions = [QuestionMark(qid=f"Q{n}", score=1, max_score=1, page=1, y_fraction=.3,
                                  objective=True, chosen_option="A", anchor_line_id=anchors[n])
                     for n in range(1, 6)]
        for letter, lid in zip("bcde", ("P1L10", "P1L11", "P1L12", "P1L13")):
            questions.append(QuestionMark(qid=f"Q6.{letter}", score=1, max_score=1, page=1,
                                          y_fraction=.5, anchor_line_id=lid))
        report = GradeReport(total_score=9, max_total=9, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions={})
        repair_report_evidence(report, ocr, _doc(width=600, height=848))
        got = {q.qid: q.anchor_line_id for q in questions if q.objective}
        self.assertEqual(got["Q1"], "P1L4")
        self.assertEqual(got["Q3"], "P1L6")


class KeyAgainstItsOwnSolution(unittest.TestCase):
    """Issue 2: Q4's key letter (B) contradicts its worked solution (option A's text)."""

    def test_the_paper_really_says_both(self):
        package = _package()
        letters = {q.qid: q.objective_answer for q in package.questions if q.question_type == "objective"}
        self.assertEqual(letters, {"Q1": "A", "Q2": "B", "Q3": "B", "Q4": "B", "Q5": "C"})
        supported = solution_supported_options(PAPER, option_texts_from_paper(PAPER))
        self.assertEqual(supported[4][0], "A")
        self.assertNotIn(6, supported)          # Q6's parts are not one option's text

    def test_the_worked_solution_wins_and_the_teacher_is_told(self):
        package = _package()
        fixed, warnings = correct_key_against_solutions(package, PAPER, PAPER)
        letters = {q.qid: q.objective_answer for q in fixed.questions if q.question_type == "objective"}
        self.assertEqual(letters, {"Q1": "A", "Q2": "B", "Q3": "B", "Q4": "A", "Q5": "C"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("Q4", warnings[0])
        self.assertIn("Suitable distance from the Sun", warnings[0])
        self.assertIn("(A)", warnings[0])
        self.assertIn("(B)", warnings[0])
        self.assertNotEqual(fixed.package_id, package.package_id)
        self.assertEqual(fixed.package_id, fixed.computed_id())
        q4 = next(q for q in fixed.questions if q.qid == "Q4")
        self.assertIn("worked solution", q4.prompt_text)

    def test_a_correct_student_now_earns_the_mark(self):
        fixed, _ = correct_key_against_solutions(_package(), PAPER, PAPER)
        rows = [("P1L3", (100, 100, 900, 140), "4. (A) Suitable distance from the sun"),
                ("P1L4", (100, 160, 900, 200), "5. (C) Universal Adult Franchise")]
        ocr = [PageOCR(1, 1240, 1753, _lines(1, rows))]
        report = GradeReport(total_score=0, max_total=2, overall_remarks="", questions=[
            QuestionMark(qid="Q4", score=0, max_score=1, page=1, y_fraction=.1,
                         objective=True, chosen_option="A", anchor_line_id="P1L3"),
            QuestionMark(qid="Q5", score=0, max_score=1, page=1, y_fraction=.1,
                         objective=True, chosen_option="C", anchor_line_id="P1L4")])
        apply_official_objective_evidence(report, fixed, ocr, question_paper_text=PAPER,
                                          allow_unconfirmed=True)
        self.assertEqual({q.qid: q.score for q in report.questions}, {"Q4": 1.0, "Q5": 1.0})

    def test_a_paraphrase_or_an_agreeing_solution_changes_nothing(self):
        paraphrase = PAPER.replace(
            "Text Solution:\nSuitable distance from the Sun",
            "Text Solution:\nBecause the Earth is at a suitable distance from the Sun, its water stays liquid.")
        package = _package(key_text=paraphrase, paper_text=paraphrase)
        same, warnings = correct_key_against_solutions(package, paraphrase, paraphrase)
        self.assertEqual(warnings, [])
        self.assertIs(same, package)
        agreeing = re.sub(r"Q4[ ]*\n\(B\)", "Q4\n(A)", PAPER)
        self.assertNotEqual(agreeing, PAPER)
        package = _package(key_text=agreeing, paper_text=agreeing)
        self.assertEqual(next(q for q in package.questions if q.qid == "Q4").objective_answer, "A")
        same, warnings = correct_key_against_solutions(package, agreeing, agreeing)
        self.assertEqual(warnings, [])

    def test_the_batch_wires_it_in_and_shows_it(self):
        source = inspect.getsource(server._run_job)
        self.assertIn("correct_key_against_solutions", source)
        self.assertIn("Answer key corrected", source)
        self.assertIn("key_warnings", inspect.getsource(server.job_status))


def _text_pdf(chars_per_page=900, logo_px=176, pages=2):
    """A typed school circular: real text layer, one small logo."""
    from PIL import Image
    import io
    doc = fitz.open()
    buf = io.BytesIO()
    Image.new("RGB", (logo_px, logo_px), "navy").save(buf, format="PNG")
    sentence = "ANNUAL SYLLABUS 2025-26 CLASS IX ENGLISH LITERATURE Julius Caesar Unit 1. "
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        if index == 0:
            page.insert_image(fitz.Rect(40, 30, 100, 90), stream=buf.getvalue())
        text = (sentence * (chars_per_page // len(sentence) + 1))[:chars_per_page]
        page.insert_textbox(fitz.Rect(40, 100, 555, 800), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def _photo_pdf(px=640, with_ocr_layer=False):
    """A small phone photo of a page, stretched over A4 (optionally with scanner OCR)."""
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (int(px * 0.75), px), "white").save(buf, format="JPEG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    if with_ocr_layer:
        page.insert_textbox(fitz.Rect(40, 40, 555, 800), "Q1 answer the earth is round " * 20,
                            fontsize=9, render_mode=3)
    data = doc.tobytes()
    doc.close()
    return data


class TypedDocumentIsNotASmallPhoto(unittest.TestCase):
    """Issue 3: logos on typed pages tripped PHOTO_TOO_SMALL before the typed check."""

    def test_a_logo_on_a_typed_page_is_not_a_photo(self):
        self.assertIsNone(pipeline.largest_photo_edge(_text_pdf(logo_px=176), "Krishna.pdf"))
        self.assertIsNone(pipeline.largest_photo_edge(_text_pdf(logo_px=640, pages=1), "Samriddhi.pdf"))

    def test_printed_notes_with_one_picture_page_are_still_a_document(self):
        # Sweep of 12,035 stored uploads: printed notes whose only non-text page is a
        # 616 px diagram ("[4]") must not turn into PHOTO_TOO_SMALL.
        notes = fitz.open(stream=_text_pdf(logo_px=176, pages=3), filetype="pdf")
        picture = fitz.open(stream=_photo_pdf(616), filetype="pdf")
        notes.insert_pdf(picture)
        self.assertIsNone(pipeline.largest_photo_edge(notes.tobytes(), "Madiha.pdf"))
        # One typed cover page beside one small photo of the answers still fires.
        cover = fitz.open(stream=_text_pdf(logo_px=176, pages=1), filetype="pdf")
        cover.insert_pdf(fitz.open(stream=_photo_pdf(640), filetype="pdf"))
        self.assertEqual(pipeline.largest_photo_edge(cover.tobytes(), "cover.pdf"), 640)

    def test_a_small_photo_is_still_measured(self):
        self.assertEqual(pipeline.largest_photo_edge(_photo_pdf(640), "photo.pdf"), 640)
        # A scanner app's OCR layer over the photo does not make it a typed page.
        self.assertEqual(pipeline.largest_photo_edge(_photo_pdf(640, True), "scan.pdf"), 640)

    def test_the_typed_upload_reaches_the_document_checks(self):
        class Reached(Exception):
            pass

        def stop(**kwargs):
            raise Reached()

        with patch.object(pipeline, "grade_prepared", stop):
            with self.assertRaises(Reached):
                pipeline.grade_sheet(qp_pngs=[], sa_bytes=_text_pdf(), filename="Krishna.pdf",
                                     ak_pngs=None, ak_text=None, marks_scheme=None, cfg=None)
            # A small photo is checked and flagged too, not held (user, 29 Oct 2026).
            with self.assertRaises(Reached):
                pipeline.grade_sheet(qp_pngs=[], sa_bytes=_photo_pdf(640), filename="photo.pdf",
                                     ak_pngs=None, ak_text=None, marks_scheme=None, cfg=None)


# Aradhya's Q7 and the start of Section B (pages 1-2).
ARADHYA_Q7 = [PageOCR(1, 3200, 4250, _lines(1, [
    ("P1L19", (152, 2526, 301, 2617), "Q7"),
    ("P1L20", (210, 2617, 3116, 2787), "d). Archeological remains, swhos buildings, pottery and toos"),
    ("P1L21", (249, 2735, 2957, 2878), "- seals and other inseribed objects diseovered at"),
    ("P1L22", (365, 2884, 1373, 3032), "harruppon sites."),
    ("P1L23", (144, 2963, 2741, 3152), "b) - Houses were generally made of balced"),
    ("P1L24", (355, 3133, 757, 3257), "briets."),
    ("P1L27", (129, 3415, 3160, 3662), "c). The cities had a well-planned and interconnected desinge"),
    ("P1L29", (233, 3639, 3139, 3921), "- Projns weregenerally made of bricks and were often covered.")])),
    PageOCR(2, 3200, 4250, _lines(2, [
        ("P2L6", (378, 1106, 2959, 1226), "- Page No.............."),
        ("P2L7", (297, 1236, 2920, 1539), "d) - Harappars used to make"),
        ("P2L8", (469, 1379, 2192, 1651), "ornament, portryes, status, ete."),
        ("P2L11", (367, 1736, 2936, 2011), "- Harappan's used to make seals tole trade."),
        ("P2L12", (1294, 2026, 1858, 2173), "Section - B"),
        ("P2L13", (214, 2109, 3011, 2349), "Q9a The earth is said to be oblete spheroid because"),
        ("P2L14", (417, 2238, 3146, 2501), "Barth is bulged at equator due to sentritugel")]))]


class SubPartLabelReadOutOfOrder(unittest.TestCase):
    """Issue 4: Q7(d) was graded against Q7(a)'s answer (0/3)."""

    def test_a_misread_first_label_is_restored_by_the_sequence(self):
        located = {loc.qid: loc for loc in build_answer_map(_package(), ARADHYA_Q7).locations}
        self.assertEqual(located["Q7.a"].label_line_ids, ("P1L20",))
        self.assertEqual(located["Q7.a"].line_ids, ("P1L20", "P1L21", "P1L22"))
        self.assertEqual(located["Q7.d"].label_line_ids, ("P2L7",))
        self.assertIn("P2L11", located["Q7.d"].line_ids)
        self.assertNotIn("P2L7", located["Q7.c"].line_ids)
        # "Section - B" ends Q7(d): Section B is not part of its answer.
        self.assertNotIn("P2L13", located["Q7.d"].line_ids)

    def test_without_the_later_proof_nothing_is_relabelled(self):
        # d) b) c) with no later d): the student may really have started with (d).
        pages = [PageOCR(1, 3200, 4250, [line for line in ARADHYA_Q7[0].lines])]
        located = {loc.qid: loc for loc in build_answer_map(_package(), pages).locations}
        self.assertEqual(located["Q7.d"].label_line_ids, ("P1L20",))
        self.assertEqual(located["Q7.a"].status, "unresolved")

    def test_the_grader_is_told_about_misread_part_letters(self):
        self.assertIn("is part (a)", grader.SCHOOL_MARKING_POLICY_BLOCK)


class AnyNPointsAreExamples(unittest.TestCase):
    """Issue 5: valid drainage / trade points docked for not being the key's three."""

    def test_school_policy_treats_the_key_list_as_examples(self):
        prompt = grader.build_system_prompt("Social Science", "Class 9", "")
        self.assertIn("2a. **The key's list is examples, not a checklist.**", prompt)
        for words in ("manholes", "Mesopotamia", "never after one key point",
                      "Never write \"not a keyed feature\""):
            self.assertIn(words, prompt)

    def test_the_second_examiner_raises_a_valid_unlisted_point(self):
        q = QuestionMark(qid="Q7.c", score=2, max_score=3, page=1, y_fraction=.5,
                         anchor_line_id="P1L1", remark="Proper slope is missing.",
                         criteria=[Criterion(description="Drainage feature 3", awarded=0, max=1,
                                             met=False, step_line_id="P1L1",
                                             evidence="Sumps inspection is not a keyed drainage feature")])
        report = GradeReport(total_score=2, max_total=3, overall_remarks="", questions=[q])
        prompt = grader.build_audit_prompt(report, "Three features of the drainage system", "")
        self.assertIn("a different correct point fills the row", prompt)


if __name__ == "__main__":
    unittest.main()
