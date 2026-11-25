"""Live run of 1 Nov 2026: eight Oct papers, 1,980 copies, stopped as marks problems though the reader's
rows gave the official total exactly, because the free text scan read 92 for 80 on SST, 222 for 80 on
English and 51 for 30 on Biology. In the workflow a reading that lands on the official total is kept.
The Checker page, where a teacher is present, keeps its check. No network.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from types import SimpleNamespace

import fitz
from fastapi import UploadFile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader
import pipeline
import server
from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationStore


def scheme(*marks):
    return MarksScheme(total=float(sum(marks)), items=[MarksItem(qid=f"Q{number}", max=float(mark), description="")
                                                       for number, mark in enumerate(marks, 1)])


def read(monkeypatch, reading, total, ai_first=True):
    """The text scan disagrees with every reading here, as it did on the eight papers."""
    monkeypatch.setattr(pipeline, "pdf_text_layer", lambda data, name: "")
    monkeypatch.setattr(pipeline, "pdf_or_image_to_pngs", lambda data, name: [b"page"])
    monkeypatch.setattr(pipeline, "_extract_marks", lambda *args, **kwargs: reading)
    monkeypatch.setattr(pipeline, "extraction_reconciles_with_tags", lambda *args: False)
    cfg = SimpleNamespace(exam_type="school", provider="claude", model="test", category="")
    return pipeline.detect_marks_scheme(b"%PDF", "paper.pdf", cfg, "Class 10", "SST", total_marks=total,
                                        require_complete=True, ai_first=ai_first)


LANDS_ON_TOTAL = (4, 4, 4, 5, 5, 6, 6, 6)        # 40


def test_the_workflow_keeps_a_reading_on_the_official_total(monkeypatch):
    _, method = read(monkeypatch, scheme(*LANDS_ON_TOTAL), 40)
    assert method == "ai"


def test_the_checker_page_keeps_its_check(monkeypatch):
    _, method = read(monkeypatch, scheme(*LANDS_ON_TOTAL), 40, ai_first=False)
    assert method == "ai_mismatch"


def test_a_reading_off_the_total_still_stops_the_test(monkeypatch):
    _, method = read(monkeypatch, scheme(4, 4, 4, 5, 5, 6, 6), 40)
    assert method == "ai_mismatch"


def test_the_reading_gets_past_the_later_gates_and_earns_its_proof(monkeypatch):
    """A later gate could stop the same test again, so this goes through the whole route."""
    document = fitz.open()
    document.new_page().insert_text((72, 72), "Q1. Define demand.")
    monkeypatch.setattr(pipeline, "_extract_marks", lambda *args, **kwargs: scheme(*LANDS_ON_TOTAL))
    monkeypatch.setattr(pipeline, "extraction_reconciles_with_tags", lambda *args: False)
    monkeypatch.setattr(server, "_require_allowed", lambda auth: "fixture")
    monkeypatch.setattr(server, "cfg_from_request", lambda *args, **kwargs: SimpleNamespace(
        provider="claude", model="fixture", exam_type="school", category=""))
    monkeypatch.setattr(server, "_marks_reader_config", lambda cfg: cfg)
    monkeypatch.setattr(server, "_marks_extraction_context", lambda *args: "fixture")
    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", MarksAttestationStore())
    response = asyncio.run(server.detect_marks(
        question_paper=UploadFile(filename="paper.pdf", file=io.BytesIO(document.tobytes())),
        question_page_range="", provider="claude", model="", student_class="10", subject="SST",
        exam_type="school", category="school", total_marks="40", mode="ai_first", authorization="fixture"))
    assert response["method"] == "ai_verified"
    assert response["marks_attestation"]


def test_marks_per_case_study_are_shared_by_its_questions():
    prompt = grader.marks_examiner_prompt(80)
    assert "per case study" in prompt and "never give each question the full amount" in prompt
    assert "exactly 80" in prompt
