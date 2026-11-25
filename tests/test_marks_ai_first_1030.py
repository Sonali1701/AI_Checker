"""Operator's request of 30 Oct 2026 (build 148): AI reads the marks of every workflow paper.

Ten of that day's fifty tests stopped on marks: six while the proxy's storage was full, three
whose section headers gave the total exactly (a free reading never earned the proof a run
needs) and one whose split the paper leaves open. The free reading stays as the check on AI:
four AI readings of one 70-mark paper gave 83, 81, 78 and 73. No network.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader
import pipeline
import pw_access
import server
import workflow_autopilot as autopilot
from grader import MarksExtractionError, MarksItem, MarksScheme
from test_workflow_autopilot import OWNER, PAIRS, PairPortal, copy, finish, pilot

OPTIONS = {"category": "school", "student_class": "Class 11th - Science - CBSE Board", "subject": "Chemistry",
           "total_marks": "70.0"}
HEADERS = [
    "Class XI Chemistry   Maximum Marks: 70",
    "Section A  Multiple Choice Questions (1 - 5) (5 x 1 = 5)",
] + [f"{n}. Question {n}" for n in range(1, 6)] + [
    "Section B  Very Short Answer Type Questions (6 - 10) (5 x 2 = 10)",
] + [f"{n}. Question {n}" for n in range(6, 11)] + [
    "Section C  Short Answer Type Questions (11 - 21) (11 x 5 = 55)",
] + [f"{n}. Question {n}" for n in range(11, 22)]
FREE = {**{n: 1 for n in range(1, 6)}, **{n: 2 for n in range(6, 11)}, **{n: 5 for n in range(11, 22)}}


def paper(lines=HEADERS):
    document = fitz.open()
    page = document.new_page()
    for number, line in enumerate(lines):
        page.insert_text((40, 40 + number * 18), line, fontsize=11)
    return document.tobytes()


def reading(marks):
    items = [MarksItem(qid=f"Q{n}", max=mark) for n, mark in marks.items()]
    return MarksScheme(total=sum(marks.values()), items=items)


def check(reader, data=None, options=OPTIONS):
    """The workflow's paper check with the AI reader replaced; only the sign-in is stubbed."""
    server._MARKS_ATTESTATIONS._records.clear() if hasattr(server._MARKS_ATTESTATIONS, "_records") else None
    with patch.object(server, "_require_allowed", return_value="token"), \
         patch.object(pipeline, "claude_extract_marks", side_effect=reader) as called:
        result = server._workflow_check_paper(data or paper(), options, "Bearer token")
    return result, called


def test_ai_reads_a_paper_whose_section_headers_give_the_total():
    fine = dict(FREE)
    fine.pop(11)
    parts = reading(fine)
    parts.items.extend([MarksItem(qid="Q11.a", max=2), MarksItem(qid="Q11.b", max=3)])
    parts.total = 70
    result, called = check(lambda *a, **k: parts)
    assert called.call_count == 1 and result["ok"] is True and result["method"] == "ai_verified"
    assert {"Q11.a", "Q11.b"} <= {item["qid"] for item in result["items"]}


def test_the_paper_takes_over_when_the_ai_service_is_down():
    def down(*args, **kwargs):
        raise pw_access.PWAccessError("claude proxy error 500: over your space quota", status_code=500)
    result, _ = check(down)
    assert result["ok"] is True and result["method"] == "paper_verified"
    assert [item["max"] for item in result["items"]] == list(FREE.values())


def test_the_paper_takes_over_when_ai_misses_the_total():
    wrong = {**FREE, 11: 10, 12: 10, 13: 10}                  # 85, as AI alone did on such papers
    result, _ = check(lambda *a, **k: reading(wrong))
    assert result["ok"] is True and result["method"] == "paper_verified" and sum(i["max"] for i in result["items"]) == 70


def test_the_paper_wins_when_ai_adds_up_but_gives_a_question_other_marks():
    wrong = {**FREE, 11: 7, 12: 3}                            # still 70
    result, _ = check(lambda *a, **k: reading(wrong))
    assert result["ok"] is True and result["method"] == "paper_verified"
    assert {i["qid"]: i["max"] for i in result["items"]}["Q11"] == 5


def test_a_split_the_paper_leaves_open_is_decided_by_the_reader():
    lines = ["Class XI Chemistry   Maximum Marks: 70"] + [f"{n}. Question {n}" for n in range(1, 8)]
    calls = []

    def reader(*args, **kwargs):
        calls.append(kwargs)
        if not kwargs.get("examiner_decides"):
            raise MarksExtractionError("Unresolved printed allocation: no marks printed for Q1 to Q7")
        return reading({n: 10 for n in range(1, 8)})
    result, _ = check(reader, paper(lines))
    assert len(calls) == 3 and calls[1]["visual_recheck"] and calls[2]["tolerate_allocation_notes"]
    assert result["ok"] is True and "Claude decided" in result["note"]


def test_a_decided_reading_must_still_give_the_official_total():
    lines = ["Class XI Chemistry   Maximum Marks: 70"] + [f"{n}. Question {n}" for n in range(1, 8)]

    def reader(*args, **kwargs):
        if not kwargs.get("examiner_decides"):
            raise MarksExtractionError("Unresolved printed allocation: no marks printed for Q1 to Q7")
        return reading({n: 5 for n in range(1, 8)})
    result, _ = check(reader, paper(lines))
    assert result["ok"] is False and not result["retry"]


def test_the_prompt_lifts_the_rule_against_forcing_a_total_only_for_the_last_reading():
    text = grader.marks_examiner_prompt(80)
    assert "exactly 80" in text and "FINAL READING" in text and "allocation_issues" in text


def test_an_ai_outage_on_a_paper_with_no_free_reading_is_reported_as_passing_with_its_reason():
    lines = ["Class XI Chemistry   Maximum Marks: 70"] + [f"{n}. Question {n}" for n in range(1, 8)]

    def down(*args, **kwargs):
        raise pw_access.PWAccessError("claude proxy error 500: you are over your space quota", status_code=500)

    async def no_wait(seconds):
        return None
    with patch("asyncio.sleep", no_wait):
        result, called = check(down, paper(lines))
    assert result["ok"] is False and result["retry"] is True and called.call_count == 3
    assert "space quota" in result["message"]


def test_a_paper_problem_is_not_read_again_and_again():
    lines = ["Class XI Chemistry   Maximum Marks: 70"] + [f"{n}. Question {n}" for n in range(1, 8)]
    result, called = check(lambda *a, **k: reading({n: 5 for n in range(1, 8)}), paper(lines))
    assert result["ok"] is False and not result["retry"] and called.call_count <= 2


def test_the_ordinary_checker_still_reads_section_headers_for_free():
    scheme, method = pipeline.detect_marks_scheme(paper(), "paper.pdf", None, total_marks=70.0, exam_type="school")
    assert method == "sections" and scheme.total == 70


def test_autopilot_waits_out_an_ai_outage_and_starts_the_test(tmp_path):
    answers = iter([{"ok": False, "retry": True, "message": "the AI service did not answer"}] * 2 + [{"ok": True}])
    s, ap = pilot(tmp_path, lambda paper_bytes, options, authorization: next(answers, {"ok": True}))
    ap.start(OWNER, ap.plan(OWNER, [copy(1, "A")], PAIRS[:1])["plan_id"])
    finish(ap)
    assert ap.status(OWNER)["tests"][0]["status"] == "done"


def test_autopilot_gives_up_on_an_outage_after_its_wait(tmp_path):
    s, ap = pilot(tmp_path, lambda *a: {"ok": False, "retry": True, "message": "the AI service did not answer"})
    ap.marks_wait = 0
    ap.start(OWNER, ap.plan(OWNER, [copy(1, "A")], PAIRS[:1])["plan_id"])
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "marks_problem" and "did not answer" in test["detail"]


def test_autopilot_shows_that_ai_decided_the_marks(tmp_path):
    s, ap = pilot(tmp_path, lambda *a: {"ok": True, "note": "Claude decided those."})
    ap.start(OWNER, ap.plan(OWNER, [copy(1, "A")], PAIRS[:1])["plan_id"])
    finish(ap)
    assert "Claude decided those." in ap.status(OWNER)["tests"][0]["detail"]
