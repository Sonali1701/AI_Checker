"""Offline endpoint regression for Skills' hidden class and bound marks proof.

The real detection, upload validation, attestation store and evaluation queue are
used. Only authentication, the paid marks reader and job persistence are replaced;
background grading is deliberately not executed.
"""
import asyncio
import io
import json
import os
import sys

import fitz
import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server
from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationStore, canonical_marks_signature


def _pdf(*lines):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "\n".join(lines))
        return document.tobytes()


def _upload(data, name):
    return UploadFile(file=io.BytesIO(data), filename=name)


@pytest.fixture
def skills(monkeypatch):
    """The screenshot's ten compulsory questions, each worth twenty marks."""
    scheme = MarksScheme(total=200, items=[
        MarksItem(qid=f"Q{number}", max=20) for number in range(1, 11)
    ])
    clock = [1000.0]
    paper = _pdf("Python Fundamentals and Data Structures", "Maximum Marks: 200",
                 "All ten questions are compulsory.")
    sheet = _pdf("Learner's typed answers", "Q1: A valid example answer.")
    key = _pdf("Official solutions", "Q1: Expected answer.")
    calls = []

    def fake_reader(data, filename, cfg, *args, **kwargs):
        # Any accidental deterministic fallback must not turn client rows into
        # authority, and no test may call an external marks provider.
        if cfg is None:
            return None, "none"
        calls.append((data, cfg, args, kwargs))
        return scheme.model_copy(deep=True), "ai"

    monkeypatch.setattr(server, "_require_allowed", lambda _authorization: "test-token")
    monkeypatch.setattr(server.pw_access, "new_task_id", lambda prefix: f"offline-{prefix}")
    monkeypatch.setattr(server, "detect_marks_scheme", fake_reader)
    monkeypatch.setattr(server, "_persist_job", lambda _job_id: None)
    monkeypatch.setattr(server, "JOBS", {})
    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", MarksAttestationStore(
        ttl_seconds=60, clock=lambda: clock[0]))
    return dict(paper=paper, sheet=sheet, key=key, scheme=scheme,
                clock=clock, calls=calls)


def _detect(skills, *, student_class="", category="innovation", subject="Python Fundamentals"):
    return asyncio.run(server.detect_marks(
        question_paper=_upload(skills["paper"], "assignment.pdf"),
        question_page_range="", provider="claude", model="",
        student_class=student_class, subject=subject, exam_type="school",
        category=category, total_marks="200", mode="ai", authorization="Bearer offline",
    ))


def _evaluate(skills, verified, *, background=None, paper=None, **changes):
    background = background if background is not None else BackgroundTasks()
    arguments = dict(
        background_tasks=background,
        answer_sheets=[_upload(skills["sheet"], "learner.pdf")],
        question_paper=_upload(paper if paper is not None else skills["paper"], "assignment.pdf"),
        answer_key=_upload(skills["key"], "solutions.pdf"),
        question_page_range="", answer_page_range="", use_question_paper_as_key=False,
        step_rubric="", provider="claude", model="", student_class="",
        subject="Python Fundamentals", exam_type="school", total_marks="200",
        exam_level="", paper_code="", exam_attempt="", applicable_law_date="",
        key_source="upload", marks_items=json.dumps(verified["items"]),
        marks_source="ai_verified", marks_attestation=verified["marks_attestation"],
        usd_to_inr="", category="innovation", answer_language="", leniency="standard",
        centre_name="", course_name="", test_date="", board="", vertical="",
        test_name="", paper_mode="", authorization="Bearer offline",
    )
    arguments.update(changes)
    result = asyncio.run(server.evaluate(**arguments))
    return result, background


@pytest.mark.parametrize("detected_class,evaluated_class,category", [
    ("", "", "innovation"),
    ("   ", "   ", "PW Skills"),
    ("School of Innovation", "", "innovation"),
    ("", "School of Innovation", "innovation"),
    ("FSD", "FSD", "innovation"),
])
def test_skills_verified_200_mark_assignment_queues(skills, detected_class, evaluated_class, category):
    verified = _detect(skills, student_class=detected_class, category=category)
    assert verified["method"] == "ai_verified", verified
    assert verified["marks_attestation"]
    assert [(item["qid"], item["max"]) for item in verified["items"]] == [
        (f"Q{number}", 20) for number in range(1, 11)]

    result, background = _evaluate(skills, verified, student_class=evaluated_class,
                                   category=category)
    assert result["job_id"] in server.JOBS
    assert len(background.tasks) == 1
    payload = background.tasks[0].args[0]
    assert payload["cfg"].category == "innovation"
    assert payload["student_class"] == (evaluated_class.strip() or "School of Innovation")
    assert payload["exam_level"] == payload["student_class"]
    assert payload["total_marks"] == 200
    assert payload["question_graph_proof"]
    assert canonical_marks_signature(MarksScheme.model_validate(payload["verified_marks_scheme"])) == (
        canonical_marks_signature(skills["scheme"]))
    assert len(skills["calls"]) == 1


def test_skills_repeat_verification_reuses_exact_paper_proof(skills):
    verified = _detect(skills)
    repeated = _detect(skills, student_class="School of Innovation")
    assert repeated["method"] == "ai_verified"
    assert repeated["marks_attestation"] == verified["marks_attestation"]
    assert len(skills["calls"]) == 1
    result, background = _evaluate(skills, repeated)
    assert result["job_id"] in server.JOBS and len(background.tasks) == 1


def test_skills_lost_browser_token_recovers_only_matching_server_proof(skills):
    verified = _detect(skills)
    result, background = _evaluate(skills, verified, marks_attestation="")
    assert result["job_id"] in server.JOBS
    assert background.tasks[0].args[0]["question_graph_proof"]
    assert len(skills["calls"]) == 1


@pytest.mark.parametrize("changes", [
    {"subject": "HTML and CSS"},
    {"category": "school", "student_class": "School of Innovation"},
    {"student_class": "FSD"},
    {"exam_level": "FSD"},
])
def test_changed_exam_context_still_rejected_before_queue(skills, changes):
    verified = _detect(skills)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        _evaluate(skills, verified, background=background, **changes)
    assert raised.value.status_code == 400
    assert "exam context changed" in raised.value.detail
    assert not background.tasks and not server.JOBS


@pytest.mark.parametrize("mutation,detail", [
    ("paper", "different question paper"),
    ("maxima", "question/maxima rows changed"),
    ("question", "question/maxima rows changed"),
    ("expired", "missing or expired"),
    ("forged", "missing or expired"),
])
def test_paper_rows_and_proof_checks_still_reject_before_queue(skills, mutation, detail):
    verified = _detect(skills)
    changes = {}
    if mutation == "paper":
        changes["paper"] = _pdf("Different assignment", "Maximum Marks: 200")
    elif mutation in {"maxima", "question"}:
        items = [dict(item) for item in verified["items"]]
        if mutation == "maxima":
            # Keep the total 200: checking only the sum would miss this change.
            items[0]["max"], items[1]["max"] = 21, 19
        else:
            items[0]["qid"] = "Q11"
        changes["marks_items"] = json.dumps(items)
    elif mutation == "expired":
        skills["clock"][0] += 61
    else:
        changes["marks_attestation"] = "forged-browser-token"
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        _evaluate(skills, verified, background=background, **changes)
    assert raised.value.status_code == 400
    assert detail in raised.value.detail
    assert not background.tasks and not server.JOBS


def test_changed_official_total_still_rejected_when_header_is_unreadable(skills, monkeypatch):
    # A scanned/headerless paper relies on the entered total, which remains bound
    # by the store even without an independently readable printed header.
    monkeypatch.setattr(server, "paper_total_from_text", lambda _text: None)
    verified = _detect(skills)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        _evaluate(skills, verified, background=background, total_marks="201")
    assert raised.value.status_code == 400
    assert "official total changed" in raised.value.detail
    assert not background.tasks and not server.JOBS


def test_printed_total_conflict_still_rejected_before_queue(skills):
    verified = _detect(skills)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        _evaluate(skills, verified, background=background, total_marks="201")
    assert raised.value.status_code == 400
    assert "conflicts with the official paper header" in raised.value.detail
    assert not background.tasks and not server.JOBS


def test_other_categories_still_require_an_explicit_level(skills):
    verified = _detect(skills)
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        _evaluate(skills, verified, background=background, category="school")
    assert raised.value.status_code == 400
    assert "Class or CA level is required" in raised.value.detail
    assert not background.tasks and not server.JOBS
