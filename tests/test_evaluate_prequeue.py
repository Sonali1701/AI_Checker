# -*- coding: utf-8 -*-
"""Evaluation must prove its denominator before returning a job id."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import BackgroundTasks, HTTPException, UploadFile

import server
from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationStore
from pipeline import cfg_from_request


failures: list[str] = []


def check(name: str, ok: bool, detail=None) -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail is not None and not ok else ""))
    if not ok:
        failures.append(name)


SCHEME = MarksScheme(
    total=3,
    items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=2)],
)
ROWS = [{"qid": item.qid, "max": item.max, "part": ""} for item in SCHEME.items]
PAPER = b"%PDF-paper"


async def start(*, token: str = "", source: str = "ai_unverified"):
    tasks = BackgroundTasks()
    result = await server.evaluate(
        background_tasks=tasks,
        answer_sheets=[UploadFile(file=io.BytesIO(b"%PDF-sheet"), filename="sheet.pdf")],
        question_paper=UploadFile(file=io.BytesIO(PAPER), filename="paper.pdf"),
        answer_key=UploadFile(file=io.BytesIO(b"%PDF-key"), filename="key.pdf"),
        provider="gemini",
        model="",
        student_class="Class 11",
        subject="Economics",
        exam_type="school",
        total_marks="3",
        exam_level="Class 11",
        paper_code="",
        exam_attempt="",
        applicable_law_date="",
        key_source="upload",
        marks_items=json.dumps(ROWS),
        marks_source=source,
        marks_attestation=token,
        usd_to_inr="",
        authorization="Bearer test",
    )
    return result, tasks


originals = {
    "require": server._require_allowed,
    "new_task": server.pw_access.new_task_id,
    "pdf_text": server.pdf_text_layer,
    "paper_total": server.paper_total_from_text,
    "detect": server.detect_marks_scheme,
    "store": server._MARKS_ATTESTATIONS,
    "persist": server._persist_job,
    "jobs": server.JOBS,
}

try:
    server._require_allowed = lambda _authorization: "google-token"
    server.pw_access.new_task_id = lambda _prefix: "evaluate-task"
    server.pdf_text_layer = lambda _data, _name: "Maximum Marks: 3"
    server.paper_total_from_text = lambda _text: 3.0
    server._persist_job = lambda _job_id: None

    print("== no doomed background job ==")
    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    server.JOBS = {}
    server.detect_marks_scheme = lambda *args, **kwargs: (None, "none")
    rejected_tasks = BackgroundTasks()
    try:
        asyncio.run(server.evaluate(
            background_tasks=rejected_tasks,
            answer_sheets=[UploadFile(file=io.BytesIO(b"%PDF-sheet"), filename="sheet.pdf")],
            question_paper=UploadFile(file=io.BytesIO(PAPER), filename="paper.pdf"),
            answer_key=UploadFile(file=io.BytesIO(b"%PDF-key"), filename="key.pdf"),
            provider="gemini", model="", student_class="Class 11",
            subject="Economics", exam_type="school", total_marks="3",
            exam_level="Class 11", paper_code="", exam_attempt="",
            applicable_law_date="", key_source="upload",
            marks_items=json.dumps(ROWS), marks_source="ai_disagreement",
            marks_attestation="", usd_to_inr="", authorization="Bearer test",
        ))
        check("unverified AI rows are rejected before queueing", False)
    except HTTPException as exc:
        check("unverified AI rows are rejected before queueing", exc.status_code == 409,
              (exc.status_code, exc.detail))
        check("pre-queue error tells the user what to do",
              "Detect max marks" in str(exc.detail), exc.detail)
    check("rejected request creates no job", not server.JOBS, server.JOBS)
    check("rejected request schedules no worker", not rejected_tasks.tasks,
          rejected_tasks.tasks)

    print("== lost browser token recovers server proof ==")
    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    server.JOBS = {}
    cfg = cfg_from_request(
        "gemini", "", "Economics", google_token="google-token",
        task_id="evaluate-task", student_class="Class 11",
        exam_type="school", total_marks=3,
    )
    context = server._marks_extraction_context(cfg, "Class 11", "Economics")
    record = server._MARKS_ATTESTATIONS.create(
        PAPER, 3, SCHEME, SCHEME,
        extractors=("claude:sonnet", "gemini:flash"), context=context,
    )
    server.detect_marks_scheme = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("cache hit must not run deterministic extraction"))
    started, recovered_tasks = asyncio.run(start(token="", source="ai_unverified"))
    payload = recovered_tasks.tasks[0].args[0]
    check("paper-bound cache recovers a lost browser token",
          bool(started.get("job_id")) and payload["question_graph_proof"] == record.proof)
    check("evaluation and every queued provider call share one task id",
          payload["cfg"].task_id == "evaluate-task", payload["cfg"].task_id)

    print("== deterministic paper graph remains free ==")
    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    server.JOBS = {}
    server.detect_marks_scheme = lambda *args, **kwargs: (SCHEME, "regex")
    deterministic, deterministic_tasks = asyncio.run(start(token="", source="regex"))
    check("trusted deterministic rows can still queue",
          bool(deterministic.get("job_id")) and len(deterministic_tasks.tasks) == 1)
finally:
    server._require_allowed = originals["require"]
    server.pw_access.new_task_id = originals["new_task"]
    server.pdf_text_layer = originals["pdf_text"]
    server.paper_total_from_text = originals["paper_total"]
    server.detect_marks_scheme = originals["detect"]
    server._MARKS_ATTESTATIONS = originals["store"]
    server._persist_job = originals["persist"]
    server.JOBS = originals["jobs"]


if failures:
    raise SystemExit(f"{len(failures)} failure(s): {', '.join(failures)}")
print("All evaluate pre-queue checks passed.")
