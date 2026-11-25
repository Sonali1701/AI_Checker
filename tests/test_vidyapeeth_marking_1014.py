# -*- coding: utf-8 -*-
"""Vidyapeeth Class 8 Maths, 36 copies (13 Oct 2026): what reached students wrongly.

Of the seven copies released straight to students every one had a mistake a student
would see: 9 of the paper's 16 MCQs had no verified key letter (short key answers
such as "(B) 625" were refused), so 16 MCQ marks in 14 copies disagreed with the key;
marks in dense MCQ rows and case-study parts were drawn beside the wrong answer; a
note told a student "Q19(ii): Correct answer is (B) 7"; the sheet took each
student's own handwritten test date.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from grader import GradeReport, QuestionMark  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402

# The key's own text layer, as fitz reads it (Hint & Solutions, page 1 and 2).
KEY = ("1. \n(B) 625 \n1 \n2. \n(C) 133 \n1 \n3. \n(A) 24 \n1 \n4. \n(B) 7779 \n1 \n5. \n(C) 5 \n1 \n"
       "6. \n(B) 2.5 \n1 \n7. \n(C) 9 \n1 \n8. \n(A) (3, 4, 5) \n1 \n"
       "18.(i) (A) 48 and 50 \n1 \n(ii) \n(C) 37 \n1 \n(iii) \n(D) (15, 20, 26) \n2 \n"
       "19.(i) (B) 7 \n1 \n(ii) \n(B) Their sum is n2 \n1 \n(iii) \n(D) 100 \n2")
# The paper's own text layer: its date and its MCQ section (Q1 to Q10).
PAPER = ("DATE: 03 / 05 / 2026 \n"
         'MATHEMATICS \nMultiple Choice Questions \n1. \nWhich of the following is a perfect square? \n'
         '(A) \n1057 \n(B)  \n625 \n(C)  \n7928 \n(D) \n222 \n1 \n2. \nThe value of 672 – 662 is: \n'
         '(A) \n1 \n(B)  \n132 \n(C)  \n133 \n(D) \n4489 \n1 \n3. \nHow many no-square numbers lie between 122 and 132? \n'
         '(A) \n24 \n(B)  \n25 \n(C)  \n26 \n(D) \n13 \n1 \n4. \nThe square of which of the following would be an odd number. \n'
         '(A) \n2826 \n(B)  \n7779 \n(C)  \n8200 \n(D) \n112 \n1 \n \n5. \nWhat is the smallest number by which 180 must be multiplied so that it becomes a perfect square? \n'
         '(A) \n2 \n(B)  \n3 \n(C)  \n5 \n(D) \n6 \n1 \n6. \nThe square root of 6.25 is: \n(A) \n'
         '0.25 \n(B)  \n2.5 \n(C)  \n25 \n(D) \n0.025 \n1 \n7. \nThe unit digit of the square of a number ending in 7 is: \n'
         '(A) \n7 \n(B)  \n4 \n(C)  \n9 \n(D) \n1 \n1 \n8. \nWhich of the following is a Pythagorean triplet? \n'
         '(A) \n(3, 4, 5) \n(B)  \n(5, 12, 14) \n(C)  \n(6, 8, 11) \n(D) \n(1, 1, 1) \n1 \n'
         ' \n9. \nAssertion: The number 400 is a perfect square. \nReason: A number ending in an even number of zeroes can be a perfect square. \n'
         '(A) \nBoth Assertion and Reason are true and Reason is the correct explanation of Assertion. \n'
         '(B)  \nBoth Assertion and Reason are true but Reason is not the correct explanation of Assertion. \n'
         '(C)  \nAssertion is true but Reason is false. \n(D) \nAssertion is false but Reason is true. \n'
         '1 \n10. \nAssertion: The sum of first n odd natural numbers is n2. \nReason: 1 + 3 + 5 + 7 + 9 = 52 = 25. \n'
         '(A) \nBoth Assertion and Reason are true and Reason is the correct explanation of Assertion. \n'
         '(B)  \nBoth Assertion and Reason are true but Reason is not the correct explanation of Assertion. \n'
         '(C)  \nAssertion is true but Reason is false. \n(D) \nAssertion is false but Reason is true. \n'
         '1 \n\n')


def _package(qids):
    from exam_package import ExamPackage
    return ExamPackage.model_validate({
        "schema_version": 1, "package_id": "x" * 64, "authority": "official",
        "release_eligible": True, "department": "school", "level": "Class 8", "subject": "Maths",
        "total_marks": float(len(qids)), "question_paper_sha256": "a" * 64,
        "answer_key_sha256": "b" * 64,
        "questions": [{"qid": q, "question_type": "unknown", "max_marks": 1.0} for q in qids]})


def test_short_key_answers_give_mcqs_their_letter():
    from key_answers import KeyAnswer, KeyAnswerSheet, verify_key_answers
    try:
        package = _package(["Q1", "Q2", "Q3", "Q5", "Q6", "Q7", "Q18.i", "Q18.ii", "Q19.i", "Q19.ii", "Q19.iii", "Q4"])
    except Exception:  # noqa: BLE001 - the package schema is checked elsewhere
        package = SimpleNamespace(questions=[SimpleNamespace(qid=q) for q in
                                             ("Q1", "Q2", "Q3", "Q5", "Q6", "Q7", "Q18.i", "Q18.ii",
                                              "Q19.i", "Q19.ii", "Q19.iii", "Q4")])
    right = [("Q1", "B", "(B) 625"), ("Q2", "C", "(C) 133"), ("Q3", "A", "(A) 24"),
             ("Q5", "C", "(C) 5"), ("Q6", "B", "(B) 2.5"), ("Q7", "C", "(C) 9"),
             ("Q18.ii", "C", "(C) 37"), ("Q19.i", "B", "(B) 7"), ("Q19.iii", "D", "(D) 100")]
    wrong = [("Q19.ii", "B", "(B) 7"),       # (B) 7 is printed under 19 (i), not (ii)
             ("Q18.i", "C", "(C) 37"),       # under 18 (ii)
             ("Q4", "B", "(B) 7"),           # "(B) 7" inside "(B) 7779" is not a whole answer
             ("Q2", "B", "(C) 133")]         # the quote names another letter
    sheet = KeyAnswerSheet(items=[KeyAnswer(qid=q, objective=True, option=l, quote=t)
                                  for q, l, t in right + wrong])
    got = verify_key_answers(sheet, KEY, package)
    assert got == {q: l for q, l, _ in right}


def _report(questions, **extra):
    return GradeReport(total_score=sum(q.score for q in questions),
                       max_total=sum(q.max_score for q in questions), overall_remarks="",
                       questions=questions, **extra)


def _q(qid, score, maximum=1.0, **kw):
    return QuestionMark(qid=qid, score=score, max_score=maximum, page=1, y_fraction=.5,
                        attempted=True, **kw)


def test_an_mcq_marked_by_judgement_holds_a_vidyapeeth_copy():
    import preflight
    report = _report([_q("Q1", 1, objective=True, correct_option="B", chosen_option="B"),
                      _q("Q2", 1, chosen_option="B"),          # "132 (B)": judged, not keyed
                      _q("Q11", 2, 2.0)])
    issues = preflight._check_mcq_scored_by_judgement(report, PAPER)
    assert [i.code for i in issues] == ["mcq-without-key-letter"] and "Q2" in issues[0].detail
    assert preflight.release_blocking(issues, "vidyapeeth")
    assert not preflight.release_blocking(issues, "school")


def _line(lid, text, y0, y1, x0=40.0, x1=260.0, page=1):
    return {"lid": lid, "page": page, "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "_px": LineBox(lid, text, (x0 * 2, y0 * 2, x1 * 2, y1 * 2), page)}


def test_case_study_parts_take_their_questions_rows_in_order():
    from evidence_repair import _anchor_case_parts_in_order, _part_row
    assert _part_row("11) c) 37 .") == (None, 2)              # OCR's "11)" is (ii)
    assert _part_row("18.1) a) 48 and 50 .") == (18, 1)
    assert _part_row("III) D) 100 .") == (None, 3)
    assert _part_row("iiy (C) 37") == (None, 2)              # ")" read as "y"
    assert _part_row("11. Prime factor of 7056") is None
    by_lid = {l["lid"]: l for l in (
        _line("L41", "18.1) a) 48 and 50 .", 480, 506),
        _line("L42", "11) c) 37 .", 503, 524),
        _line("L43", "iii) D) (15,20,26)", 522, 547),
        _line("L44", "19.1) (B) 7 .", 545, 568),
        _line("L45", "11) B) Their sum is equal to n", 561, 589),
        _line("L46", "III) D) 100 .", 583, 609),
        _line("L47", "D) (15,20,26)", 635, 650))}       # the visual reader's estimate
    parts = [_q("Q18.i", 1, objective=True, anchor_line_id="L41"),
             _q("Q18.ii", 1, objective=True, anchor_line_id="L41"),   # moved onto (i)'s row
             _q("Q18.iii", 2, 2.0, objective=True, anchor_line_id="L47"),
             _q("Q19.i", 1, objective=True, anchor_line_id="L44"),
             _q("Q19.ii", 1, objective=True, anchor_line_id="L45"),
             _q("Q19.iii", 2, 2.0, objective=True, anchor_line_id="L46")]
    report = _report(parts, evidence_contract=True, answer_regions={})
    changed = {}
    _anchor_case_parts_in_order(report, by_lid, changed)
    assert {q.qid: q.anchor_line_id for q in report.questions} == {
        "Q18.i": "L41", "Q18.ii": "L42", "Q18.iii": "L43",
        "Q19.i": "L44", "Q19.ii": "L45", "Q19.iii": "L46"}
    assert set(changed) == {"Q18.ii", "Q18.iii"}


def test_an_mcq_on_its_neighbours_row_takes_the_next_option_row():
    from evidence_repair import _row_after_neighbour
    q9 = _q("Q9", 0, objective=True, anchor_line_id="L3")
    q10 = _q("Q10", 1, objective=True, anchor_line_id="L29")
    by_lid = {l["lid"]: l for l in (
        _line("L3", "g9) (a)", 156, 183, 47, 143),
        _line("L4", "g104. (a)", 194, 222, 48, 150),          # "Q10>" read as "g104."
        _line("L29", "(a)", 154, 183, 36, 209))}               # the box, on Q9's row
    numbers = {"Q9": 9, "Q10": 10}
    got = _row_after_neighbour(q10, by_lid["L29"], 10, [q9, q10], numbers, by_lid,
                               {"L29"}, set(), {"L3"}, 842.0)
    assert [row["lid"] for _, row in got] == ["L4"]


def test_a_mark_beside_another_answers_row_is_found():
    import grading_quality
    report = _report([_q("Q5", 0, objective=True, correct_option="C"),
                      _q("Q6", 1, objective=True, correct_option="B")],
                     evidence_contract=True)
    report.render_page_layouts = {"1": {"content_bbox": [0, 0, 600, 842]}}
    report.render_manifest = [
        {"qid": "Q5", "role": "answer", "page": 1, "bbox": [200, 222, 225, 244],
         "anchor_bbox": [49, 221, 147, 245]},
        {"qid": "Q5", "role": "total", "page": 1, "bbox": [150, 247, 169, 266],   # on Q6's row
         "anchor_bbox": [49, 221, 147, 245]},
        {"qid": "Q6", "role": "answer", "page": 1, "bbox": [175, 246, 224, 268],
         "anchor_bbox": [46, 244, 155, 268]},
        {"qid": "Q6", "role": "verdict", "page": 1, "bbox": [640, 100, 660, 120],  # margin lane
         "anchor_bbox": [46, 244, 155, 268]}]
    with fitz.open() as doc:
        doc.new_page(width=700, height=842)
        issues = grading_quality.objective_marks_off_their_rows(report, doc)
    assert [(i.code, i.detail.split(":")[0]) for i in issues] == [("mark-on-another-row", "Q5")]
    assert "beside Q6's answer" in issues[0].detail
    import preflight
    assert preflight.release_blocking(issues, "vidyapeeth")


def _render_dense_mcqs(pitch):
    from pdf_renderer import build_evaluated_pdf
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    lines, questions, regions = [], [], {}
    entries = [(1, "B"), (0, "D"), (0, "D"), (1, "B"), (0, "A"), (1, "B"), (0, "A"), (1, "A")]
    for i, (score, option) in enumerate(entries):
        qid, lid, y = f"Q{i + 1}", f"P1L{i + 1}", 120 + pitch * i
        text = f"({i + 1}) ({option.lower()}) 4{i}2"
        page.insert_text((45, y), text, fontsize=13)
        lines.append(LineBox(lid, text, (43, y - pitch / 2 - 1, 140, y + pitch / 2 + 1), 1))
        regions[qid.lower()] = {"line_ids": [lid], "pages": [1]}
        questions.append(QuestionMark(qid=qid, score=score, max_score=1, page=1, y_fraction=y / 842,
                                      attempted=True, objective=True, chosen_option=option,
                                      correct_option="B" if score else "C", anchor_line_id=lid))
    report = GradeReport(total_score=sum(q.score for q in questions), max_total=len(questions),
                         overall_remarks="", questions=questions, evidence_contract=True,
                         answer_regions=regions)
    source = doc.tobytes()
    doc.close()
    pdf = build_evaluated_pdf(source, "copy.pdf", [], report, [PageOCR(1, 595, 842, lines)])
    return report, pdf


def test_marks_in_a_dense_mcq_block_stay_on_their_own_rows():
    import grading_quality
    import pdf_renderer as pr
    before = pr.TEACHER_LAYOUT
    pr.TEACHER_LAYOUT = False
    try:
        report, pdf = _render_dense_mcqs(pitch=20.0)
    finally:
        pr.TEACHER_LAYOUT = before
    for q in report.questions:
        rows = [r for r in report.render_manifest
                if r["qid"] == q.qid and r["role"] in {"answer", "verdict", "total"}]
        assert rows, q.qid
        for row in rows:
            centre = (row["bbox"][1] + row["bbox"][3]) / 2
            anchor = row["anchor_bbox"]
            assert anchor[1] - 3 <= centre <= anchor[3] + 3, (q.qid, row["role"], row["bbox"], anchor)
        if q.score == 0:
            # The cross and its circled 0 are one mark; no 0 drawn anywhere else.
            assert [r["score_style"] for r in rows if r["role"] == "answer"] == ["cross_and_zero"]
            assert not [r for r in rows if r["role"] == "total"]
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert grading_quality.objective_marks_off_their_rows(report, doc) == []


def test_a_parts_note_names_only_the_letter():
    from wording import finalise_wording
    report = _report([_q("Q19.ii", 0, objective=True, correct_option="B", chosen_option="A"),
                      _q("Q6", 0, objective=True, correct_option="B", chosen_option="A")])
    finalise_wording(report, {19: {"A": "6", "B": "7"}, 6: {"A": "0.25", "B": "2.5"}})
    assert [q.remark for q in report.questions] == ["Correct answer is (B).",
                                                    "Correct answer is (B) 2.5."]


def test_the_sheet_reads_the_papers_spaced_out_date():
    import result_export
    facts = result_export.paper_facts(PAPER)
    assert result_export.normal_date(facts["test_date"]) == "03/05/2026"
