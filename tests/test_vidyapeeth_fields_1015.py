# -*- coding: utf-8 -*-
"""The Vidyapeeth batch header (PRD sheet "The new fields we need", 15 Oct 2026).

Board, vertical, centre, course, class, subject, test name, test date and mode of
paper are typed once per upload, all required, as dropdowns from a list; the
results sheet carries them for every student instead of what each cover says.
"""
from __future__ import annotations

import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import result_export  # noqa: E402
import vidyapeeth_fields  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILLED = {"centre": "Delhi - Kalusarai Vidyapeeth", "course": "Foundation", "board": "CBSE",
          "vertical": "VP", "test_name": "Infinity Test 10", "test_date": "2026-10-03",
          "paper_mode": "Hindi", "student_class": "Class 8", "subject": "Maths"}


class TheLists(unittest.TestCase):
    def test_the_teams_lists_are_served(self):
        lists = vidyapeeth_fields.load()
        self.assertEqual(lists["boards"], ["CBSE", "ICSE", "ISC", "STATE BOARD"])
        self.assertEqual(lists["verticals"], ["VP", "PS", "TC"])
        self.assertEqual(len(lists["centres"]), 17)
        self.assertIn("Howrah Vidyapeeth", lists["centres"])
        self.assertEqual(lists["modes"], ["English", "Hindi", "Hinglish", "Urdu", "Gujrati", "Marathi", "Bengali"])
        self.assertTrue({"6", "10", "12"} <= set(lists["classes"]))
        self.assertIn("SST", lists["subjects"])

    def test_every_field_is_required(self):
        self.assertEqual(vidyapeeth_fields.missing({}), list(vidyapeeth_fields.REQUIRED.values()))
        self.assertEqual(vidyapeeth_fields.missing(FILLED), [])
        self.assertEqual(vidyapeeth_fields.missing({**FILLED, "vertical": "  ", "test_name": ""}),
                         ["Vertical", "Test name"])

    def test_the_mode_of_paper_sets_the_answer_language(self):
        self.assertEqual(vidyapeeth_fields.answer_language_for("Hindi"), "hindi")
        self.assertEqual(vidyapeeth_fields.answer_language_for("Gujrati"), "gujarati")
        self.assertEqual(vidyapeeth_fields.answer_language_for("Hinglish"), "")
        self.assertEqual(vidyapeeth_fields.answer_language_for("English"), "english")
        self.assertEqual(vidyapeeth_fields.answer_language_for(""), "")


class TheResultsSheet(unittest.TestCase):
    def test_the_sheet_has_the_header_columns_in_order(self):
        cols = result_export.COLUMNS
        self.assertEqual(cols[:9], ["Center Name", "Course", "Class", "Board", "Vertical", "Subject",
                                    "Mode of Paper", "Test Date", "Test Name"])

    def test_the_operators_header_wins_over_the_cover(self):
        cover = {"student_name": "Sakshi Ranjan", "roll_number": "23230113", "class_name": "VIIIth",
                 "batch": "12-UP1516A", "centre": "Taskcon", "phone": "8787869465", "date": "3.5.26"}
        result = {"state": "released", "score": 26, "max": 40, "percent": 65, "student_info": cover}
        batch = result_export.batch_fields(FILLED)
        row = result_export.row_for(result, source="s #09.pdf", report_link="", facts={"board": "ICSE"},
                                    student_class="Class 8", subject="Maths", batch=batch)
        self.assertEqual(row["Center Name"], "Delhi - Kalusarai Vidyapeeth")
        self.assertEqual(row["Course"], "Foundation")
        self.assertEqual(row["Board"], "CBSE", "the operator's board, not the paper's")
        self.assertEqual(row["Vertical"], "VP")
        self.assertEqual(row["Mode of Paper"], "Hindi")
        self.assertEqual(row["Test Name"], "Infinity Test 10")
        self.assertEqual(row["Test Date"], "03/10/2026")
        self.assertEqual(row["Class"], "Class 8")
        # Other categories: nothing typed, the old fallbacks stand.
        row = result_export.row_for(result, source="s.pdf", report_link="", facts={"board": "ICSE"})
        self.assertEqual((row["Board"], row["Vertical"], row["Test Name"], row["Mode of Paper"]),
                         ("ICSE", "", "", ""))


class TheRoutes(unittest.TestCase):
    def test_a_vidyapeeth_batch_without_its_header_is_refused_with_the_gaps(self):
        import server
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as caught:
            server._batch_header_or_400("vidyapeeth", centre_name="", course_name="Foundation", board="CBSE",
                                        vertical="", test_name="", test_date="2026-10-03", paper_mode="Hindi",
                                        student_class="Class 8", subject="Maths")
        self.assertEqual(caught.value.status_code, 400)
        self.assertIn("Vertical, Centre name, Test name", caught.value.detail)
        header = server._batch_header_or_400("vidyapeeth", centre_name=FILLED["centre"], course_name="Foundation",
                                             board="CBSE", vertical="VP", test_name="Infinity Test 10",
                                             test_date="2026-10-03", paper_mode="Hindi",
                                             student_class="Class 8", subject="Maths")
        self.assertEqual(header["vertical"], "VP")
        # Other categories keep working with nothing typed (School of Innovation has no class).
        header = server._batch_header_or_400("innovation", centre_name="", course_name="", board="", vertical="",
                                             test_name="", test_date="", paper_mode="", student_class="", subject="")
        self.assertEqual(header["centre"], "")

    def test_the_config_carries_the_lists_for_the_dropdowns(self):
        import server
        config = server.config()
        self.assertEqual(len(config["vidyapeeth_fields"]["centres"]), 17)


class TheForm(unittest.TestCase):
    def test_the_page_has_the_header_fields_and_the_two_step_flow(self):
        html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
        for field in ("vpBoard", "vpVertical", "vpCentre", "vpCourse", "vpClass", "vpSubject", "vpTestName",
                      "testDate", "vpMode", "vpNext"):
            self.assertIn(f'id="{field}"', html, field)
        self.assertGreaterEqual(html.count("later-step"), 5, "every section after the header opens on Next")
        js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
        for name in ("fillVidyapeethSelects", "vidyapeethGaps", "syncVidyapeethDetails", "vidyapeethNext"):
            self.assertIn(f"function {name}", js, name)
        for field in ("board", "vertical", "test_name", "paper_mode", "centre_name", "course_name", "test_date"):
            self.assertIn(f'fd.append("{field}"', js, field)
        css = open(os.path.join(ROOT, "static", "styles.css"), encoding="utf-8").read()
        self.assertIn("body.vidyapeeth-mode.vp-step1 .later-step", css)


if __name__ == "__main__":
    unittest.main()
