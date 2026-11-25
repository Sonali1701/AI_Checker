# -*- coding: utf-8 -*-
"""A stand-in for grading_engine.grade_copy that a worker process can import.

tests/test_grading_engine.py runs the real folder mode with real worker processes;
only the paid grading is replaced, by this function, in the worker.
"""
from __future__ import annotations

import os

import grader
from costs import compute_cost
from strict_grading import StrictGradeError


def package_check(batch: dict, name: str, sa_bytes: bytes, stage_callback=None) -> dict:
    """What the worker makes of the batch's exam package: the check grade_sheet runs
    before it grades anything (pipeline: INVALID_EXAM_PACKAGE)."""
    return {"report": [issue.code for issue in batch["cfg"].exam_package.blockers()]}


def fake_grade(batch: dict, name: str, sa_bytes: bytes, stage_callback=None) -> dict:
    for stage in ("normalizing", "ocr", "answer_mapping", "primary_grading", "verifying",
                  "aggregating", "rendering", "release_check"):
        if stage_callback is not None:
            stage_callback(stage)
    if name.startswith("junk"):
        raise StrictGradeError("NOT_AN_ANSWER_SHEET", "Typed pages, not handwriting.")
    if name.startswith("broken"):
        raise RuntimeError("claude proxy error 400: Schema is too complex.")
    report = grader.GradeReport(questions=[
        grader.QuestionMark(qid="Q1", score=2, max_score=2, page=1, anchor_line_id="",
                            y_fraction=0.2, remark="", criteria=[]),
        grader.QuestionMark(qid="Q2", score=1, max_score=2, page=1, anchor_line_id="",
                            y_fraction=0.2, remark="Define it fully", criteria=[])],
        total_score=3, max_total=4,
        overall_remarks=f"Marked in process {os.getpid()} for {batch['subject']}.")
    cost = compute_cost({"provider": "claude", "model": "claude-haiku-4-5",
                         "input_tokens": 100000, "output_tokens": 2000})
    return {"report": report, "cost": cost, "pdf_bytes": b"%PDF-marked " + sa_bytes[:8],
            "blocking": [], "issues": [], "release": not name.startswith("r2"),
            "budget": {}, "package_id": "pkg", "ocr_pages": []}
