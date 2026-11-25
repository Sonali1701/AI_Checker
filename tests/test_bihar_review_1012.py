# -*- coding: utf-8 -*-
"""The Uday Bihar Board copies held for faculty review on 10 Oct 2026, and why.

- English a5f0f595 answered all twenty MCQs of "any 15"; the paper says "only the
  first 15 will be evaluated", the faculty marked Q1 to Q15, the tool counted Q20 for
  Q15 under its best-score default.
- MCQ ticks sat on the visual reader's estimated boxes: half a page wide, a row off,
  on the "Choose the correct option" heading. The question's own numbered row is used.
- The second examiner's box round a whole journal entry, or a little wider than its
  maths block, was refused, so no entry got its step mark (Q11 to Q13 of 0a6c8e91).
- A value re-check cut off mid-reply was read as the page disagreeing (Q13, "1800").
- Q12 of 3319af12 carried seven margin notes and they were written over Q13's.
- A bilingual paper's options were read from the Hindi run, whose text layer is
  garbled: "Correct answer is (B) लुका िैससयोली".
"""
import hashlib
import os
import sys
from types import SimpleNamespace

import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest when it imports pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import exam_package  # noqa: E402
import strict_grading  # noqa: E402
from exam_package import ChoiceGroup, ChoiceOption  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402

ENGLISH_RULE = ("4. In Section A, there are 20 objective-type questions, out of which any 15 "
                "questions are to be answered. If more than 15 \nquestions are answered, then "
                "only the first 15 will be evaluated. Each question carries 1 mark.")


def _group(gid, count):
    return ChoiceGroup(group_id=gid, select_count=count, selection_policy="first_attempted",
                       contribution_marks=float(count),
                       options=[ChoiceOption(option_id=f"{gid}{i}", question_ids=[f"Q{i}"])
                                for i in range(1, count + 2)])


def test_the_papers_own_first_n_rule_is_followed():
    assert exam_package.printed_first_rule_counts(ENGLISH_RULE) == {15}
    assert exam_package.printed_first_rule_counts(
        "If a student has attempted an extra question, the answer deserving more marks is "
        "retained.") is None
    assert exam_package.printed_first_rule_counts(
        "Only the first attempt will be evaluated.") == set()
    groups = exam_package.apply_printed_choice_rules([_group("A", 15), _group("B", 1)], ENGLISH_RULE)
    assert [g.printed_rule for g in groups] == ["first_attempted", ""]
    commerce = SimpleNamespace(department="commerce")
    assert strict_grading.effective_selection_policy(groups[0], commerce) == "first_attempted"
    assert strict_grading.effective_selection_policy(groups[1], commerce) == "best_score"


def test_an_unprinted_rule_leaves_the_package_hash_unchanged():
    plain = _group("A", 15)
    payload = {"choice_groups": [plain.model_dump(mode="json")]}
    assert payload["choice_groups"][0]["printed_rule"] == ""
    package = SimpleNamespace(model_dump=lambda mode="json": {
        "questions": [], "choice_groups": [plain.model_dump(mode="json")], "references": []})
    data = exam_package.ExamPackage.canonical_payload(package)
    assert "printed_rule" not in data["choice_groups"][0]


def test_a_bilingual_papers_english_options_are_used():
    paper = ("Q1.\nलेखांकन के जनक कौन हैं?\n(A) एडम स्मिथ\n(B) लुका िैससयोली\n(C) हेिरी फेयोल\n"
             "(D) जे.बी. से\nWho is the father of accounting?\n(A) Adam Smith\n(B) Luca Pacioli\n"
             "(C) Henri Fayol\n(D) J.B. Say\nQ2.\nThe basic accounting equation is?\n"
             "(A) Assets = Liabilities - Capital\n(B) Assets = Liabilities + Capital\n"
             "(C) Capital = Assets + Liabilities\n(D) Liabilities = Assets + Capital\n"
             "Q3.\nWhich is not a function?\n(A) Recording\n(B) Classifying\n(C) Manufacturing\n"
             "(D) Summarizing\n")
    options = strict_grading.option_texts_from_paper(paper)
    assert options[1]["B"] == "Luca Pacioli"
    assert options[2]["B"] == "Assets = Liabilities + Capital"


def test_a_value_recheck_cut_off_mid_reply_reads_nothing(monkeypatch):
    import grader
    import value_recheck
    monkeypatch.setattr(grader, "_claude_call", lambda *a, **k: {})
    monkeypatch.setattr(grader, "_claude_usage", lambda *a, **k: {})
    monkeypatch.setattr(grader, "record_usage_event", lambda *a, **k: None)
    monkeypatch.setattr(grader, "_image_block", lambda png: {})
    monkeypatch.setattr(grader, "_claude_text",
                        lambda r: '```json\n{\n  "text": "Date Particulars Dr. Debit z Credit z\\n 2,00,000')
    assert value_recheck._read_crop(b"png", google_token="t", task_id="x") == ""
    monkeypatch.setattr(grader, "_claude_text", lambda r: '{"text": "13,800"}')
    assert value_recheck._read_crop(b"png", google_token="t", task_id="x") == "13,800"


def test_a_verifier_box_round_a_whole_entry_names_its_row():
    from presentation_layout import row_box_fits
    first_line = [0.252, 0.191, 0.565, 0.214]                    # "Ramesh A/c Dr"
    assert row_box_fits(first_line, [0.16, 0.20, 0.94, 0.31])    # the entry with narration
    maths_block = [0.142, 0.138, 0.727, 0.257]                   # a worked example
    assert row_box_fits(maths_block, [0.145, 0.18, 0.755, 0.21])  # 0.028 wider than OCR's box
    assert not row_box_fits(first_line, [0.16, 0.40, 0.94, 0.50])    # another entry
    assert not row_box_fits(first_line, [0.16, 0.20, 0.94, 0.40])    # two entries at once


def _q(qid, chosen, anchor):
    return SimpleNamespace(qid=qid, objective=True, attempted=True, score=1.0, max_score=1.0,
                           chosen_option=chosen, correct_option=chosen, anchor_line_id=anchor,
                           page=1, criteria=[], is_correct=True)


def test_an_mcq_on_an_estimated_box_moves_to_its_own_numbered_row():
    import evidence_repair
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    rows = ["1 are playign (o)", "2. (B) He has been working here", "3. (A) Becomes"]
    lines = [LineBox(f"P1L{i + 1}", t, (100, 200 + 60 * i, 500, 240 + 60 * i), 1, confidence=0.4)
             for i, t in enumerate(rows)]
    lines.append(LineBox("P1L9", "(c) are playing", (110, 190, 1100, 215), 1, confidence=1.0))
    page = PageOCR(1, 1200, 1600, lines)
    questions = [_q("Q1", "C", "P1L9"), _q("Q2", "B", "P1L2"), _q("Q3", "A", "P1L3")]
    report = SimpleNamespace(
        questions=questions, objective_visual_verified_qids=["Q1", "Q2", "Q3"],
        objective_unconfirmed_qids=[],
        answer_regions={"q1": {"qid": "Q1", "source": "independent_visual_location",
                               "line_ids": ["P1L9"]}})
    by_lid = evidence_repair._lines_in_points(report, [page], doc)
    changed = {}
    evidence_repair._numbered_rows_over_visual_boxes(report, by_lid, {1: page}, doc, changed)
    assert questions[0].anchor_line_id == "P1L1", changed
    assert report.answer_regions["q1"]["source"] == "confirmed_owned_ocr_location"


def test_a_row_of_a_merged_block_is_found_by_its_number():
    import pdf_renderer as pr
    assert pr._row_number("Ques: -2-(D)") == 2
    assert pr._row_number("- No -1:-(A)") == 1
    assert pr._row_number("Note 3 the rules") is None


def test_the_margin_says_no_room_instead_of_writing_over_a_note():
    import pdf_renderer as pr
    doc = fitz.open()
    doc.new_page(width=600, height=200)
    first = pr._reserve_strip_slot(doc, 1, 40, 150, 200)
    assert first is not None
    assert pr._reserve_strip_slot(doc, 1, 40, 150, 200, allow_overlap=False) is None


def test_overlapping_margin_notes_hold_the_copy():
    from grading_quality import check_render_manifest
    doc = fitz.open()
    doc.new_page(width=770, height=842)
    pdf = doc.tobytes()
    report = SimpleNamespace(
        evidence_contract=True, render_manifest_pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        render_page_layouts={}, questions=[], render_requirements=[],
        render_manifest=[
            {"qid": "Q12", "role": "feedback", "page": 1, "bbox": [607, 366, 762, 430]},
            {"qid": "Q13", "role": "feedback", "page": 1, "bbox": [607, 375, 762, 446]},
            {"qid": "Q11", "role": "feedback", "page": 1, "bbox": [607, 180, 762, 267]}])
    codes = [i.code for i in check_render_manifest(report, pdf)]
    assert codes.count("feedback-overlap") == 1
    import categories
    assert "feedback-overlap" in categories.get("commerce").review.extra_hold_codes
