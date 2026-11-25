# -*- coding: utf-8 -*-
"""PW Vidyapeeth team requirements (10 Oct 2026).

1. Many students' booklets in one scanned PDF, split at each printed cover page,
   each booklet's cover read for the student's details, failures set aside.
2. A results sheet: centre, course, class, board, subject, test date, registration
   number, name, phone, score, total, percentage, review status, report link.
3/4. The answer language chosen from a list; languages not yet checked on sample
   copies go to faculty review.
5. Strict, Standard or Lenient step marking.
"""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import marking_settings  # noqa: E402
import result_export  # noqa: E402
import vidyapeeth_intake as vi  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "vidyapeeth_cover_template.png")


def _cover(doc):
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=TEMPLATE)


def _answer(doc, words="The answer to the question"):
    page = doc.new_page(width=595, height=842)
    for y in range(80, 800, 28):
        page.draw_line((40, y), (555, y), color=(0.6, 0.6, 0.8), width=0.6)
    page.insert_text((60, 120), words, fontsize=14)


def _pdf(*layout) -> bytes:
    doc = fitz.open()
    for kind in layout:
        (_cover if kind == "C" else _answer)(doc)
    return doc.tobytes()


# --- 1. one PDF, many booklets -------------------------------------------------

def test_a_stack_of_booklets_splits_at_each_cover():
    segments = vi.plan_split(_pdf("C", "A", "A", "C", "A", "C", "A", "A", "A"), "batch.pdf")
    assert [(s.first, s.last, s.problem) for s in segments] == [(0, 2, ""), (3, 4, ""), (5, 8, "")]
    # One booklet (or no cover at all) is not split.
    assert vi.plan_split(_pdf("C", "A", "A"), "one.pdf") == []
    assert vi.plan_split(_pdf("A", "A"), "photos.pdf") == []


def test_stray_pages_and_an_empty_cover_are_set_aside():
    segments = vi.plan_split(_pdf("A", "C", "A", "C", "C", "A"), "batch.pdf")
    assert [(s.first, s.last) for s in segments] == [(0, 0), (1, 2), (3, 3), (4, 5)]
    assert "before the first cover" in segments[0].problem
    assert "no answer pages" in segments[2].problem
    assert not segments[1].problem and not segments[3].problem


def test_an_upload_becomes_one_copy_per_booklet():
    sheets, aside = vi.expand_upload("Centre 4.pdf", _pdf("C", "A", "C", "C", "A", "A"))
    assert [name for name, _ in sheets] == ["Centre 4 #01.pdf", "Centre 4 #03.pdf"]
    assert [fitz.open(stream=data, filetype="pdf").page_count for _, data in sheets] == [2, 3]
    assert [name for name, _ in aside] == ["Centre 4 #02.pdf"]
    assert vi.parse_segment_name("Centre 4 #03.pdf") == ("Centre 4.pdf", 3)
    assert vi.parse_segment_name("Centre 4.pdf") is None


def test_folder_mode_plans_and_loads_each_booklet(tmp_path):
    import bulk
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "stack.pdf").write_bytes(_pdf("C", "A", "C", "A", "A", "A", "C"))
    (source / "single.pdf").write_bytes(_pdf("C", "A"))
    calls = []

    def splitter(data, rel):
        calls.append(rel)
        return vi.plan_split(data, rel)
    found = bulk.plan(source, target, 1, 1, splitter=splitter)
    assert found["todo"] == ["single.pdf", "stack #01.pdf", "stack #02.pdf", "stack #03.pdf"]
    sink = bulk.FolderSink(source, target, 1, 1, segments=found["segments"])
    pages = [fitz.open(stream=sink.load(rel), filetype="pdf").page_count
             for rel in found["todo"]]
    assert pages == [2, 2, 4, 1]
    assert "no answer pages" in sink.segment_problem("stack #03.pdf")
    assert sink.segment_problem("stack #01.pdf") == "" and sink.segment_problem("single.pdf") == ""
    # The split is remembered: a second plan reads no PDF again.
    bulk.plan(source, target, 1, 1, splitter=splitter)
    assert sorted(calls) == ["single.pdf", "stack.pdf"]


def test_a_cover_is_read_and_an_unreadable_roll_number_waits_for_a_person():
    data = _pdf("C", "A")
    reading = {"is_cover_page": True, "student_name": " AYUSH  RAJ ", "roll_number": "2 3 4 5 6 7 8 9",
               "subject": "Maths", "class_name": "9", "batch": "28-NF2026",
               "centre": "PW Vidyapeeth Civil Lines", "phone": "98765 43210", "date": "02-08-26"}
    agrees = lambda *a, **k: {"roll_number": "23456789", "phone": "9876543210"}  # noqa: E731
    got = vi.cover_details(data, "a.pdf", google_token="", task_id="t",
                           reader=lambda *a, **k: reading, checker=agrees)
    assert got["problems"] == []
    assert got["info"]["student_name"] == "AYUSH RAJ" and got["info"]["roll_number"] == "23456789"
    assert got["info"]["phone"] == "9876543210"
    blurred = vi.cover_details(data, "a.pdf", google_token="", task_id="t",
                               reader=lambda *a, **k: dict(reading, roll_number="23?56789"),
                               checker=agrees)
    assert "not certain" in blurred["problems"][0]
    # Two readings that disagree on a digit leave it for a person (12 Oct 2026: a
    # single reading gave 93265117 for 23256517 and 232286609 for 23228609).
    differs = vi.cover_details(data, "a.pdf", google_token="", task_id="t",
                               reader=lambda *a, **k: dict(reading, roll_number="93265117"),
                               checker=lambda *a, **k: {"roll_number": "23256517", "phone": ""})
    assert differs["info"]["roll_number"] == "?32???17" and "not certain" in differs["problems"][0]
    doubled = vi.cover_details(data, "a.pdf", google_token="", task_id="t",
                               reader=lambda *a, **k: dict(reading, roll_number="232286609"),
                               checker=lambda *a, **k: {"roll_number": "232286609", "phone": ""})
    assert "9 digits where the cover has 8 boxes" in doubled["problems"][0]
    # A booklet without the cover has nothing to read, and nothing is called.
    assert vi.cover_details(_pdf("A", "A"), "b.pdf", google_token="", task_id="t",
                            reader=lambda *a, **k: 1 / 0) is None

    def broken(*a, **k):
        raise RuntimeError("proxy 502")
    assert vi.cover_details(data, "a.pdf", google_token="", task_id="t",
                            reader=broken)["problems"]
    # The second reading failing leaves the roll number unconfirmed, not trusted.
    alone = vi.cover_details(data, "a.pdf", google_token="", task_id="t",
                             reader=lambda *a, **k: reading, checker=broken)
    assert alone["info"]["roll_number"] == "23456789?" and alone["problems"]


# --- 2. the results sheet -------------------------------------------------------

def test_the_results_sheet_has_the_requested_columns(tmp_path):
    facts = result_export.paper_facts("MATHEMATICS Subjective Test-03 Class-IX CBSE "
                                      "Time: 90 Minutes DATE: 02/08/2026")
    assert facts == {"board": "CBSE", "test_date": "02/08/2026"}
    info = {"student_name": "AYUSH RAJ", "roll_number": "234567", "class_name": "9",
            "batch": "28-NF2026", "centre": "Civil Lines", "phone": "9876543210"}
    released = result_export.row_for(
        {"state": "released", "score": 24.5, "max": 40, "percent": 61.2, "student_info": info},
        source="stack #01.pdf", report_link=str(tmp_path / "evaluated_a.pdf"), facts=facts,
        subject="Mathematics")
    held = result_export.row_for(
        {"state": "faculty_review", "score": 3, "max": 40, "percent": 7.5,
         "student_info": dict(info, student_name="RIYA"), "review_reasons": ["Q14 check"]},
        source="stack #02.pdf", report_link="", facts=facts, subject="Mathematics")
    assert list(released) == result_export.COLUMNS
    assert (released["Center Name"], released["Course"], released["Board"],
            released["Registration Number"], released["Review Status"]) == \
        ("Civil Lines", "28-NF2026", "CBSE", "234567", "Checked, sent to student")
    assert held["Review Status"] == "Faculty review" and "Q14 check" in held["Notes"]
    for row in (released, held):
        result_export.append_csv(tmp_path, row)
    rows = result_export.latest_per_copy(result_export.read_csv(tmp_path / result_export.CSV_NAME))
    path = result_export.write_xlsx(rows, tmp_path / result_export.XLSX_NAME)
    from openpyxl import load_workbook
    sheet = load_workbook(path).active
    assert [c.value for c in sheet[1]] == result_export.COLUMNS
    col = {name: i + 1 for i, name in enumerate(result_export.COLUMNS)}
    assert sheet.cell(row=2, column=col["Subject-wise Score"]).value == 24.5
    assert sheet.cell(row=2, column=col["Report Link"]).hyperlink is not None
    # The same registration number on two copies is named on both rows.
    assert "Same registration number as stack #02.pdf" in sheet.cell(row=2, column=col["Notes"]).value


def test_an_upload_zip_carries_the_results_sheet(monkeypatch):
    import server
    monkeypatch.setattr(server, "_persist_job", lambda job_id: None)
    server.JOBS["vp1"] = {
        "status": "done", "total": 1, "done": 1, "current": "", "error": None, "key": "k",
        "created": server.time.time(), "pdfs": {0: b"%PDF-1.4 fake"},
        "results": [{"index": 0, "student": "stack #01.pdf", "release": True, "state": "released",
                     "score": 30, "max": 40, "percent": 75.0,
                     "student_info": {"roll_number": "234567", "student_name": "AYUSH"}}],
        "export": {"facts": {"board": "CBSE", "test_date": "02/08/2026"},
                   "student_class": "Class 9", "subject": "Mathematics"}}
    try:
        archive = zipfile.ZipFile(io.BytesIO(server.job_zip("vp1", "k").body))
        assert result_export.XLSX_NAME in archive.namelist()
        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(archive.read(result_export.XLSX_NAME))).active
        number_col = result_export.COLUMNS.index("Registration Number") + 1
        link_col = result_export.COLUMNS.index("Report Link") + 1
        assert sheet.cell(row=2, column=number_col).value == "234567"
        assert sheet.cell(row=2, column=link_col).value == "to_students/evaluated_stack_01.pdf" \
            or "evaluated_" in str(sheet.cell(row=2, column=link_col).value)
    finally:
        server.JOBS.pop("vp1", None)


# --- 3/4/5. language and step marking -------------------------------------------

def test_the_settings_are_validated_and_reach_both_examiners():
    import pipeline
    assert marking_settings.language("Tamil") == "tamil" and marking_settings.language("auto") == ""
    assert marking_settings.leniency("") == "standard"
    with pytest.raises(marking_settings.SettingError):
        marking_settings.language("klingon")
    with pytest.raises(marking_settings.SettingError):
        marking_settings.leniency("generous")
    assert marking_settings.rules("", "standard") == ""
    cfg = pipeline.ProviderConfig(category="school", answer_language="marathi", leniency="strict")
    rules = pipeline._category_rules(cfg)
    assert "written in Marathi (Devanagari script)" in rules and "STRICT step marking" in rules
    import server
    assert "LENIENT step marking" in server._with_marking_settings(
        "Q1. Define.", pipeline.ProviderConfig(leniency="lenient"))
    assert server._with_marking_settings("Q1. Define.", pipeline.ProviderConfig()) == "Q1. Define."


def test_a_language_not_yet_checked_goes_to_faculty_review():
    import pipeline
    assert pipeline._category_review_hold(pipeline.ProviderConfig(category="school")) is None
    assert pipeline._category_review_hold(
        pipeline.ProviderConfig(category="school", answer_language="hindi")) is None
    held = pipeline._category_review_hold(
        pipeline.ProviderConfig(category="school", answer_language="urdu"))
    assert held and held.startswith("Urdu answers go to faculty review")
    choices = marking_settings.ui_choices()
    assert {c["id"] for c in choices["languages"] if c["validated"]} == {"", "english", "hindi"}


# --- 1b. a centre scan framed differently (12 Oct 2026) ------------------------

def _cover_b(doc):
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=TEMPLATE.replace(".png", "_b.png"))


def test_a_cover_framed_by_another_scanner_still_splits_the_stack():
    # The 218-page centre scan: covers scored 0.51 to 0.59 against the first template
    # and the one at 0.51 was missed, joining two students' booklets.
    doc = fitz.open()
    for make in (_cover_b, _answer, _answer, _cover_b, _answer, _cover, _answer):
        make(doc)
    segments = vi.plan_split(doc.tobytes(), "centre.pdf")
    assert [(s.first, s.last, s.problem) for s in segments] == [(0, 2, ""), (3, 4, ""), (5, 6, "")]


def test_a_page_that_may_be_a_cover_goes_to_a_person(monkeypatch):
    # A page between the two thresholds is neither split at nor graded through.
    scores = {0: 0.95, 1: 0.1, 2: 0.5, 3: 0.1, 4: 0.95, 5: 0.1}
    monkeypatch.setattr(vi, "cover_score", lambda page: scores[page.number])
    segments = vi.plan_split(_pdf("A", "A", "A", "A", "A", "A"), "centre.pdf")
    assert [(s.first, s.last) for s in segments] == [(0, 3), (4, 5)]
    assert "Page 3 looks partly like a cover" in segments[0].problem and not segments[1].problem
    # One booklet with such a page is held too, not graded as one student.
    scores.update({4: 0.1})
    segments = vi.plan_split(_pdf("A", "A", "A", "A", "A", "A"), "centre.pdf")
    assert [(s.first, s.last) for s in segments] == [(0, 5)] and "Page 3" in segments[0].problem


def test_a_vidyapeeth_stack_may_be_larger_than_one_copy():
    import server
    big = b"%PDF-1.7\n" + b"0" * (61 * 1024 * 1024)
    with pytest.raises(server.HTTPException) as refused:
        server._validate_document("stack.pdf", big, "stack.pdf")
    assert refused.value.status_code == 413 and "60 MB" in refused.value.detail
    server._validate_document("stack.pdf", big, "stack.pdf", max_bytes=server._MAX_BATCH_BYTES)


def test_the_sheet_takes_the_uploads_own_centre_course_and_date():
    # One centre's 36-booklet scan (12 Oct 2026): every student wrote the centre,
    # batch, class and date their own way.
    info = {"student_name": "Sakshi Ranjan", "roll_number": "23230113", "class_name": "VIIIth",
            "batch": "12-UP1516A", "centre": "Taskcon", "phone": "91 8787869465",
            "date": "3.5.26"}
    result = {"state": "released", "score": 26, "max": 40, "percent": 65, "student_info": info}
    typed = result_export.batch_fields({"centre": " PW Vidyapeeth  Iskcon Temple ",
                                        "course": "12-UP151EA", "test_date": "2026-05-03"})
    row = result_export.row_for(result, source="s #09.pdf", report_link="", facts={},
                                student_class="Class 8", subject="Maths", batch=typed)
    assert (row["Center Name"], row["Course"], row["Class"], row["Test Date"]) == \
        ("PW Vidyapeeth Iskcon Temple", "12-UP151EA", "Class 8", "03/05/2026")
    assert row["Student Name"] == "SAKSHI RANJAN" and row["Phone Number"] == "8787869465"
    # Nothing typed: the cover's reading, tidied; a phone that is not 10 digits is noted.
    row = result_export.row_for(dict(result, student_info=dict(info, phone="90697969970")),
                                source="s #03.pdf", report_link="", facts={})
    assert (row["Center Name"], row["Course"], row["Class"], row["Test Date"]) == \
        ("Taskcon", "12-UP1516A", "8", "03/05/2026")
    assert "not a certain 10-digit number" in row["Notes"]


# --- checked copies named by student (13 Oct 2026) ------------------------------

def test_a_checked_copy_is_named_by_the_students_name_and_roll_number():
    assert result_export.copy_stem({"student_name": " Tanya  singh ", "roll_number": "23217614"},
                                   "stack_02") == "TANYA_SINGH_23217614"
    # An uncertain digit is X; a missing roll or name leaves the other; neither, the file.
    assert result_export.copy_stem({"student_name": "Tanya Singh", "roll_number": "2321761?"},
                                   "s") == "TANYA_SINGH_2321761X"
    assert result_export.copy_stem({"student_name": "Augustus Kr. Rai"}, "s") == "AUGUSTUS_KR_RAI"
    assert result_export.copy_stem({"roll_number": "23184492"}, "s") == "23184492"
    assert result_export.copy_stem({"student_name": "अमन"}, "stack_17") == "stack_17"
    assert result_export.copy_stem(None, "stack_17") == "stack_17"
    assert result_export.unique_stems([(0, "ARNAV_23389553"), (1, "ADITI"), (2, "arnav_23389553")]) \
        == {0: "ARNAV_23389553", 1: "ADITI", 2: "arnav_23389553_2"}


def test_the_upload_zip_and_its_sheet_use_the_students_names(monkeypatch):
    import server
    monkeypatch.setattr(server, "_persist_job", lambda job_id: None)
    tanya = {"student_name": "TANYA SINGH", "roll_number": "23217614"}
    server.JOBS["vp2"] = {
        "status": "done", "total": 3, "done": 3, "current": "", "error": None, "key": "k",
        "created": server.time.time(), "pdfs": {0: b"%PDF-1.4 a", 1: b"%PDF-1.4 b", 2: b"%PDF-1.4 c"},
        "results": [
            {"index": 0, "student": "stack #01.pdf", "release": True, "state": "released",
             "score": 30, "max": 40, "percent": 75.0, "student_info": tanya},
            {"index": 1, "student": "stack #02.pdf", "release": False, "state": "faculty_review",
             "score": 20, "max": 40, "percent": 50.0, "review_reasons": ["Q8 check"],
             "student_info": tanya},
            {"index": 2, "student": "stack #03.pdf", "release": True, "state": "released",
             "score": 10, "max": 40, "percent": 25.0, "student_info": {}}],
        "export": {"facts": {}, "student_class": "Class 8", "subject": "Maths"}}
    try:
        archive = zipfile.ZipFile(io.BytesIO(server.job_zip("vp2", "k").body))
        names = set(archive.namelist())
        assert {"to_students/evaluated_TANYA_SINGH_23217614.pdf",
                "faculty_review/REVIEW_TANYA_SINGH_23217614_2.pdf",
                "faculty_review/REVIEW_TANYA_SINGH_23217614_2_reasons.txt",
                "to_students/evaluated_stack_03.pdf"} <= names
        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(archive.read(result_export.XLSX_NAME))).active
        link_col = result_export.COLUMNS.index("Report Link") + 1
        links = [sheet.cell(row=r, column=link_col).value for r in (2, 3, 4)]
        assert links == ["to_students/evaluated_TANYA_SINGH_23217614.pdf",
                         "faculty_review/REVIEW_TANYA_SINGH_23217614_2.pdf",
                         "to_students/evaluated_stack_03.pdf"]
        single = server.job_pdf("vp2", 0, "k")
        assert 'filename="evaluated_TANYA_SINGH_23217614.pdf"' in single.headers["content-disposition"]
    finally:
        server.JOBS.pop("vp2", None)


def test_folder_mode_names_copies_by_student_and_keeps_names_on_resume(tmp_path):
    import bulk
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    aman = {"student_name": "AMAN", "roll_number": "23299766"}
    released = {"release": True, "state": "released", "score": 30, "max": 40,
                "student_info": aman}
    sink = bulk.FolderSink(source, target, 1, 1)
    first = sink.file("stack #01.pdf", released, b"%PDF-1.4 a", {"r": 1})
    second = sink.file("stack #02.pdf", dict(released, student_info=dict(aman)), b"%PDF-1.4 b", None)
    plain = sink.file("stack #03.pdf", dict(released, student_info={}), b"%PDF-1.4 c", None)
    assert (first, second, plain) == ("to_students/evaluated_AMAN_23299766.pdf",
                                      "to_students/evaluated_AMAN_23299766_2.pdf",
                                      "to_students/evaluated_stack #03.pdf")
    assert (target / "_reports" / "stack #01.json.gz").exists()
    # A new run on the same output folder keeps each copy's name: #02 regraded is
    # still _2, and a new booklet with the same cover takes the next free name.
    again = bulk.FolderSink(source, target, 1, 1)
    assert again.copy_name("stack #02.pdf", aman) == "AMAN_23299766_2"
    assert again.copy_name("stack #04.pdf", aman) == "AMAN_23299766_3"
    assert again.copy_name("stack #01.pdf", aman) == "AMAN_23299766"
