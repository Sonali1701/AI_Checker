# -*- coding: utf-8 -*-
"""Teaching exams: teacher recruitment mains mocks marked with no answer key.

Samples, 10 Oct 2026: KVS PRT Tier II Mock 01 (10 questions x 4 = 40, bilingual:
English letter and report, Hindi prose forms and story based teaching, maths, EVS,
civics, traditional games) and EMRS TGT Maths Tier II Mock 01 (15 x 4 = 60). Neither
comes with a key, and the tool refused every paper without one. A category may now
let the tool write a model answer key from the paper (teaching_key.py), once per
paper, checked by code, with every copy going to faculty review.
"""
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import grader  # noqa: E402
import teaching_key as tk  # noqa: E402

ITEMS = [{"qid": "Q1", "max": 4}, {"qid": "Q2", "max": 4}, {"qid": "Q1", "max": 4},
         {"qid": "Q3", "max": 0}]


NO_FAULTS = lambda *a, **k: tk.KeyCheckSheet(checks=[])  # noqa: E731 - the checker finds nothing


def _key(q2_steps=(2, 1, 1)):
    return tk.ModelAnswerKey(questions=[
        tk.KeyQuestion(qid="Q1", max_marks=4, question_summary="Formal letter on garbage collection",
                       model_answer="Sender's address, date, The Commissioner, subject, body.",
                       steps=[tk.KeyStep(description="Format: address, date, receiver, subject", marks=1,
                                         scoring_kind="format"),
                              tk.KeyStep(description="Problem and its effects", marks=2),
                              tk.KeyStep(description="Expression", marks=1)]),
        tk.KeyQuestion(qid="Q2", max_marks=4, question_summary="Value of k for equal roots",
                       model_answer="Equal roots: k² = 24, so k = 2√6 or -2√6.",
                       steps=[tk.KeyStep(description=f"Step {i}", marks=m)
                              for i, m in enumerate(q2_steps, 1)])])


# --- the category ------------------------------------------------------------

def test_teaching_is_a_category_the_operator_picks():
    teaching = categories.get("teaching")
    # Copies go to the students (user, 10 Oct 2026); the release checks still apply.
    assert teaching.answer_key.may_generate and teaching.review.auto_release
    assert categories.get("KVS").id == categories.get("emrs").id == "teaching"
    # Never guessed from the class and subject fields.
    assert categories.resolve(student_class="PRT", subject="Mathematics").id == "school"
    assert categories.resolve(student_class="TGT", subject="Mathematics",
                              category="teaching").id == "teaching"


def test_only_teaching_marks_without_a_key():
    # PW Skills assignments without a solutions file write their key too (23 Oct 2026),
    # told their own exam (categories/innovation.py); every other category needs a key.
    # Gurukul diagnostic tests too (20 Nov 2026): a DT the teacher has not keyed yet.
    assert {c.id for c in categories.REGISTRY.values()
            if c.answer_key.may_generate} == {"teaching", "innovation", "gurukul"}
    assert categories.get("teaching").answer_key.examination == ""


def test_the_teaching_rules_reach_the_grader():
    teaching = grader.build_system_prompt("Mathematics", "TGT", "school", category="teaching")
    school = grader.build_system_prompt("Mathematics", "TGT", "school", category="school")
    assert "teacher recruitment" in teaching and "teacher recruitment" not in school
    assert "Hindi or in English are equally valid" in " ".join(teaching.split())


# --- the paper's marks ---------------------------------------------------------

# KVS PRT Mock 01 as its text layer reads (the Hindi font is garbled, its tags are not).
PRT_PAGE = """3.
हिन्दी गद्य की प्रमुख हिधाओं (किानी, हनबंध, जीिनी, आत्मकथा) का संक्षिप्त परिचय दीक्षजए।
(4 अंक)
4.
प्राथहमक स्ति पि हिन्दी भाषा क्षििण में किानी की भूहमका स्पष्ट कीक्षजए।
(4 अंक)
5.
(a) A carton holds 25 oranges. Each orange weighs 200 grams. If a box can hold 10 kg, how many oranges fit in
a box? Write an expression and find the answer.
(b) A box holds 30 mangoes, each weighing 150 grams. How many mangoes can be packed in a 15 kg crate?
Write expressions and solve.
(4 Marks)
(a) एक कार्टन में 25 संतिे आते िैं।
(b) एक हिब्बे में 30 आम आते िैं।
(4 अंक)
6.
Simplify the expression 2d-d-d-d and then check if it's equal to 2d-(d-d)-c when d=5 and c=10?
(4 Marks)
व्यंजक 2d-d-d-d को सिि कीक्षजए।  (4 अंक)
"""


def test_hindi_tags_and_a_shared_part_tag_are_read():
    scheme = grader.marks_scheme_from_text([PRT_PAGE])
    assert [(i.qid, i.max) for i in scheme.items] == [("Q3", 4), ("Q4", 4), ("Q5", 4), ("Q6", 4)]
    # The Hindi tags never count twice in the printed tag sum.
    assert grader.printed_tag_sum(PRT_PAGE) == (8.0, 2)
    # Parts tagged one by one keep their own marks.
    each = grader.marks_scheme_from_text(["7. (a) Define X. (2 Marks)\n(b) Explain Y. (3 Marks)"])
    assert [(i.qid, i.max) for i in each.items] == [("Q7.a", 2), ("Q7.b", 3)]


# --- the answer map and the blank-answer recheck --------------------------------

def _package(n):
    from exam_package import build_exam_package
    from grader import MarksItem, MarksScheme
    return build_exam_package(
        exam_type="school", student_class="PRT", subject="KVS PRT", level="PRT", paper="",
        attempt="", applicable_law_date=None, total_marks=4 * n, question_paper=b"paper",
        answer_key=b"key", key_source="upload",
        marks_scheme=MarksScheme(total=4 * n, items=[MarksItem(qid=f"Q{i}", max=4)
                                                     for i in range(1, n + 1)]),
        scheme_source="ai_verified")


def _ocr(*pages):
    from mathpix import LineBox, PageOCR
    out = []
    for number, texts in enumerate(pages, 1):
        out.append(PageOCR(number, 1000, 1400, [
            LineBox(f"P{number}L{i}", t, (80, 60 * i, 900, 60 * i + 40), number, confidence=.9)
            for i, t in enumerate(texts, 1)]))
    return out


def test_numbered_points_under_written_headings_stay_in_their_answer():
    # KVS PRT Mock 01: the Q8 and Q9 answers number their points 1. to 6. under
    # written headings; Q1's and Q2's totals were drawn inside Q9.
    import answer_map
    pages = _ocr(["Answer-2", "Our school celebrated its annual sports day."],
                 ["Answer-1", "The Municipal Corporation, Sir,"],
                 ["उत्तर - 10", "स्थानीय तथा पारम्परिक खेल"],
                 ["उत्तर - 9", "सरकार के प्रमुख कार्य इस प्रकार हैं -",
                  "1. कानून तथा व्यवस्था बनाए रखना", "2. जन कल्याण तथा विकास",
                  "3. राष्ट्रीय सुरक्षा तथा प्रशासन"])
    located = {loc.qid: loc for loc in answer_map.build_answer_map(_package(10), pages).locations}
    assert located["Q1"].pages == (2,) and located["Q2"].pages == (1,)
    assert located["Q9"].label_line_ids == ("P4L1",)
    assert {"P4L3", "P4L4", "P4L5"} <= set(located["Q9"].line_ids)
    # A booklet numbered with bare labels keeps them as question labels.
    bare = _ocr(["1. The roots are equal", "2) x = 0, y = 0", "3. h = 60 m"])
    located = {loc.qid: loc for loc in answer_map.build_answer_map(_package(3), bare).locations}
    assert [located[q].label_line_ids for q in ("Q1", "Q2", "Q3")] == [("P1L1",), ("P1L2",), ("P1L3",)]


def test_a_label_count_never_overrules_the_inventorys_not_written(monkeypatch):
    # EMRS TGT Maths: a circled "15" was read "(5)", so Q5 counted as written, and the
    # re-read marked Q8's working as Q5 with a cross on it.
    from types import SimpleNamespace as NS
    import pipeline
    from grader import GradeReport, QuestionMark
    report = GradeReport(total_score=4, max_total=8, overall_remarks="", questions=[
        QuestionMark(qid="Q1", score=4, max_score=4, page=1, y_fraction=.1),
        QuestionMark(qid="Q2", score=0, max_score=4, page=1, y_fraction=.5, attempted=False)])
    cfg = NS(exam_package=_package(2))
    asked = []
    monkeypatch.setattr(pipeline, "find_unmarked_answers", lambda report, pages: ["Q2"])
    monkeypatch.setattr(pipeline, "_request_question_decisions",
                        lambda report, requested, **k: asked.append([s.qid for s in requested]) or {})
    common = {"filename": "copy.pdf"}
    run = dict(ocr_pages=[], cfg=cfg, common=common, policy=None, hard_cap=None,
               usd_to_inr=90, use_mathpix=True, stage="grade")
    pipeline._recheck_written_blanks(report, inventory=NS(written_qids=[], absent_qids=["Q2"],
                                                          dropped_qids=["Q2"]), **run)
    assert asked == []
    # The inventory's own sighting of an answer is still read again.
    pipeline._recheck_written_blanks(report, inventory=NS(written_qids=["Q2"], absent_qids=[],
                                                          dropped_qids=[]), **run)
    assert asked == [["Q2"]]


def test_a_misread_label_inside_another_answer_is_not_an_unread_answer():
    # EMRS TGT Maths: the circled "15" read "(5)" held the copy for an unread Q5.
    from grader import GradeReport, QuestionMark, find_unmarked_answers
    pages = _ocr(["(14) Let age of father = x", "x = 2(y + z)", "x = 40",
                  "(5) Let two consecutive odd integers be x and x+2", "x > 4.5", "(5, 7), (7, 9)"])
    report = GradeReport(total_score=8, max_total=12, overall_remarks="", questions=[
        QuestionMark(qid="Q5", score=0, max_score=4, page=1, y_fraction=.1, attempted=False),
        QuestionMark(qid="Q14", score=4, max_score=4, page=1, y_fraction=.1),
        QuestionMark(qid="Q15", score=4, max_score=4, page=1, y_fraction=.5)])
    assert find_unmarked_answers(report, pages) == ["Q5"]
    report.answer_regions = {"q15": {"qid": "Q15", "status": "located", "label_line_ids": ["P1L4"],
                                     "line_ids": ["P1L4", "P1L5", "P1L6"]}}
    assert find_unmarked_answers(report, pages) == []


def test_a_minus_in_algebra_reaches_the_student():
    from wording import plain_sentences
    assert plain_sentences("Simplified as 2s+c instead of 2d-c") == \
        "Simplified as 2s+c instead of 2d minus c."
    assert plain_sentences("2d-d-d-d equals -d") == "2d minus d minus d minus d equals minus d."
    # Words and question ranges keep their meaning.
    assert plain_sentences("U-shaped valley, Q1-Q3 and the x-axis") == \
        "U shaped valley, Q1 Q3 and the x axis."


# --- the key -----------------------------------------------------------------

def test_the_marks_table_gives_each_question_once():
    assert tk.wanted_from_items(ITEMS) == [("Q1", 4.0), ("Q2", 4.0)]


def test_a_key_that_does_not_fit_the_paper_is_refused():
    wanted = [("Q1", 4.0), ("Q2", 4.0)]
    assert tk.check_key(_key(), wanted) == []
    faults = tk.check_key(_key(q2_steps=(2, 1)), wanted + [("Q4", 4.0)])
    assert "Q2: steps add up to 3, not 4" in faults and "Q4: missing" in faults
    assert tk.check_key(_key(q2_steps=(2, 1.1, 0.9)), wanted) == [
        "Q2: a step is not a positive multiple of 0.25"]
    assert any("not on the paper: Q2" in f for f in tk.check_key(_key(), [("Q1", 4.0)]))


def test_the_key_is_written_once_and_reused(tmp_path):
    calls = []

    def writer(*args, faults=None, **kwargs):
        calls.append(faults)
        return _key(q2_steps=(2, 1)) if len(calls) == 1 else _key()

    first = tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                                subject="EMRS TGT Maths", writer=writer, checker=NO_FAULTS, cache_dir=tmp_path)
    # The first reply's fault is named in the second request.
    assert calls == [None, ["Q2: steps add up to 3, not 4"]]
    assert not first.cached and first.pdf.startswith(b"%PDF")
    assert "Q2.s3 (1 mark): Step 3" in first.text and "not an official key" in first.text
    again = tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                                subject="EMRS TGT Maths", writer=writer, checker=NO_FAULTS, cache_dir=tmp_path)
    assert again.cached and len(calls) == 2
    # Another paper is another key.
    tk.write_answer_key(b"other paper", [], "", ITEMS, google_token="", task_id="t",
                        subject="EMRS TGT Maths", writer=writer, checker=NO_FAULTS, cache_dir=tmp_path)
    assert len(calls) == 3


def _q2(answer, steps=(2, 1, 1)):
    return tk.KeyQuestion(qid="Q2", max_marks=4, question_summary="Solve the system",
                          model_answer=answer,
                          steps=[tk.KeyStep(description=f"Step {i}", marks=m)
                                 for i, m in enumerate(steps, 1)])


def test_a_second_examiner_corrects_a_wrong_answer(tmp_path):
    # EMRS TGT Maths Q12 (10 Oct 2026): the key gave y = -1, saw its own check fail
    # and kept it. The second examiner's corrected question replaces it.
    fixed = _q2("x = 1, y = -5, z = -5 (checks in all three equations).")
    checker = lambda *a, **k: tk.KeyCheckSheet(checks=[  # noqa: E731
        tk.KeyCheck(qid="Q1", verdict="correct"),
        tk.KeyCheck(qid="Q2", verdict="wrong", problem="y is -5, not -1", corrected=[fixed])])
    key = tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                              writer=lambda *a, **k: _key(), checker=checker, cache_dir=tmp_path)
    q2 = next(q for q in key.sheet.questions if q.qid == "Q2")
    assert "y = -5" in q2.model_answer and key.corrections == ["Q2: y is -5, not -1"]
    assert "who corrected Q2: y is -5, not -1" in key.text
    # The corrections are kept with the key, so a later batch reads the same key.
    again = tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                                writer=None, checker=None, cache_dir=tmp_path)
    assert again.cached and again.corrections == key.corrections


def test_a_correction_that_does_not_fit_is_written_again(tmp_path):
    calls = []

    def writer(*args, faults=None, **kwargs):
        calls.append(faults)
        key = _key()
        if faults:
            key.questions[1] = _q2("x = 1, y = -5, z = -5")
        return key

    unusable = _q2("x = 1, y = -5, z = -5", steps=(2, 1))          # adds up to 3, not 4
    checker = lambda *a, **k: tk.KeyCheckSheet(checks=[  # noqa: E731
        tk.KeyCheck(qid="Q2", verdict="wrong", problem="y is -5", corrected=[unusable])])
    key = tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                              writer=writer, checker=checker, cache_dir=tmp_path)
    assert calls == [None, ["Q2: y is -5"]]
    assert "y = -5" in key.sheet.questions[1].model_answer


def test_a_check_that_cannot_run_leaves_the_written_key(tmp_path):
    def broken(*a, **k):
        raise RuntimeError("proxy 502")
    key = tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                              writer=lambda *a, **k: _key(), checker=broken, cache_dir=tmp_path)
    assert key.sheet.questions[1].model_answer.startswith("Equal roots")


def test_no_key_is_used_when_none_fits(tmp_path):
    with pytest.raises(tk.TeachingKeyError, match="Q2: steps add up to 3"):
        tk.write_answer_key(b"paper", [], "", ITEMS, google_token="", task_id="t",
                            writer=lambda *a, **k: _key(q2_steps=(2, 1)), checker=NO_FAULTS,
                            cache_dir=tmp_path)
    with pytest.raises(tk.TeachingKeyError, match="marks are needed"):
        tk.write_answer_key(b"paper", [], "", [], google_token="", task_id="t",
                            writer=lambda *a, **k: _key(), checker=NO_FAULTS, cache_dir=tmp_path)


def test_the_key_pdf_reads_hindi_and_runs_over_pages():
    import fitz
    sheet = _key()
    sheet.questions[0].model_answer = "कहानी: किसी घटना या पात्र का यथार्थ चित्रण।\n" * 60
    doc = fitz.open(stream=tk.key_pdf(sheet, [("Q1", 4.0), ("Q2", 4.0)], title="KVS PRT Mock 01"),
                    filetype="pdf")
    assert doc.page_count >= 2
    assert "Q2.s1" in "".join(page.get_text() for page in doc)


def test_the_written_steps_are_frozen_for_every_copy():
    from exam_package import build_exam_package
    from grader import MarksItem, MarksScheme
    from key_rubric import fill_step_rubric, verify_key_rubric
    package = build_exam_package(
        exam_type="school", student_class="TGT", subject="Mathematics", level="TGT", paper="",
        attempt="", applicable_law_date=None, total_marks=8, question_paper=b"paper",
        answer_key=b"key", key_source="upload",
        marks_scheme=MarksScheme(total=8, items=[MarksItem(qid="Q1", max=4), MarksItem(qid="Q2", max=4)]),
        scheme_source="ai_verified")
    accepted, refused = verify_key_rubric(tk.as_key_rubric_sheet(_key()), package)
    assert refused == []
    updated, fixed = fill_step_rubric(package, accepted)
    assert sorted(fixed) == ["Q1", "Q2"]
    q2 = next(q for q in updated.questions if q.qid == "Q2")
    assert [c.criterion_id for c in q2.criteria] == ["Q2.s1", "Q2.s2", "Q2.s3"]
    assert updated.package_id != package.package_id


# --- the server ----------------------------------------------------------------

def test_only_a_teaching_paper_with_no_key_writes_one():
    import server
    assert server._generates_key("teaching", "generate", False)
    assert server._generates_key("teaching", "upload", False)
    assert not server._generates_key("teaching", "upload", True)      # an uploaded key wins
    for category in ("school", "vidyapeeth", "ca", ""):
        assert not server._generates_key(category, "generate", False)


def test_the_job_keeps_the_written_key_for_the_faculty(tmp_path, monkeypatch):
    import server
    monkeypatch.setenv("AICHECKER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_persist_job", lambda job_id: None)
    monkeypatch.setattr(tk, "request_key", lambda *a, **k: _key())
    monkeypatch.setattr(tk, "request_check", NO_FAULTS)
    server.JOBS["teach1"] = {
        "status": "running", "total": 1, "done": 0, "current": "", "error": None,
        "results": [], "pdfs": {}, "key": "k1", "created": server.time.time()}
    payload = {"qp_bytes": b"paper", "qp_name": "Mock 01 PRT.pdf", "items": ITEMS,
               "subject": "KVS PRT", "exam_level": "PRT", "sink": None}
    cfg = type("Cfg", (), {"google_token": "", "task_id": "t"})()
    try:
        key = server._write_generated_key(payload, [], "", cfg, "teach1")
        assert payload["ak_bytes"] == key.pdf and payload["ak_name"] == tk.KEY_FILENAME
        record = server.JOBS["teach1"]["generated_key"]
        assert record["questions"] == 2 and os.path.exists(record["path"])
        json.dumps(server.JOBS["teach1"])          # the job record stays JSON
        server.JOBS["teach1"]["results"] = [{"index": 0, "student": "a.pdf", "release": False,
                                             "state": "faculty_review", "review_reasons": ["x"]}]
        server.JOBS["teach1"]["pdfs"] = {0: b"%PDF-1.4 fake"}
        names = zipfile.ZipFile(io.BytesIO(server.job_zip("teach1", "k1").body)).namelist()
        assert tk.KEY_FILENAME in names and "faculty_review/REVIEW_a.pdf" in names
    finally:
        server.JOBS.pop("teach1", None)
