"""Offline regression for marks proofs in day-long portal workflow runs.

The real detection endpoint, capability store and evaluation queue are exercised.
Authentication, the paid marks reader and the grading worker are replaced so no
test signs in, calls an AI provider or changes a portal submission.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import io
import json
from pathlib import Path
import sys
import threading
import time

import fitz
from fastapi import BackgroundTasks, HTTPException, UploadFile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationStore


UNAVAILABLE_PROOF = "The verified marks proof is missing or expired; detect marks again."


def _pdf(text):
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


@dataclass
class Workflow:
    folder: Path
    store: MarksAttestationStore
    clock: list
    reads: list
    graded: list
    scheme: MarksScheme

    @property
    def options(self):
        return dict(category="school", student_class="Class 11", subject="Physics",
                    total_marks="3")

    def grade(self, number=1, **options):
        source = self.folder / f"student-{number}.pdf"
        if not source.exists():
            source.write_bytes(_pdf(f"Student {number}: Q1 and Q2 answers"))
        return server._workflow_grade(self.folder, source,
                                      {**self.options, **options}, "Bearer offline")

    def discard_proofs(self):
        # Model a lost server capability cache while the workflow retains its old
        # response. A subsequent run must verify again rather than trust its rows.
        with self.store._lock:
            self.store._records.clear()
            self.store._by_paper.clear()


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    clock = [1000.0]
    store = MarksAttestationStore(clock=lambda: clock[0])
    scheme = MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1),
                                       MarksItem(qid="Q2", max=2)])
    reads, graded = [], []
    lock = threading.Lock()
    (tmp_path / "question_paper.pdf").write_bytes(
        _pdf("Physics\nMaximum Marks: 3\nAll questions are compulsory."))
    (tmp_path / "answer_key.pdf").write_bytes(_pdf("Q1: Official solution\nQ2: Official solution"))

    def read_marks(data, filename, config, *args, **kwargs):
        assert config is not None, "A workflow must retain a verified AI reading."
        with lock:
            reads.append((data, config, args, kwargs))
        return scheme.model_copy(deep=True), "ai"

    def complete(payload):
        with lock:
            graded.append(payload)
            job = server.JOBS[payload["job_id"]]
            job.update(results=[dict(index=0, release=True)], pdfs={0: b"checked-copy"},
                       reports={0: dict(report={"total_score": 3})})

    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", store)
    monkeypatch.setattr(server, "_WORKFLOW_MARKS", {})
    monkeypatch.setattr(server, "_WORKFLOW_MARKS_LOCKS", {})
    monkeypatch.setattr(server, "_WORKFLOW_MARKS_GUARD", threading.Lock())
    monkeypatch.setattr(server, "_require_allowed", lambda _authorization: "offline-user")
    monkeypatch.setattr(server, "detect_marks_scheme", read_marks)
    monkeypatch.setattr(server, "_persist_job", lambda _job_id: None)
    monkeypatch.setattr(server, "_run_job", complete)
    monkeypatch.setattr(server, "JOBS", {})
    return Workflow(tmp_path, store, clock, reads, graded, scheme)


def test_the_same_marks_reading_survives_more_than_twenty_four_hours(workflow):
    assert workflow.grade()["release"] is True
    [initial] = workflow.store._records.values()
    workflow.clock[0] += 25 * 60 * 60

    assert workflow.grade(2)["release"] is True
    assert len(workflow.reads) == 1
    assert len(workflow.graded) == 2
    assert not server.JOBS  # Workflow results are copied out, then their temporary jobs are removed.
    assert workflow.store.is_available(initial.token)
    assert workflow.graded[0]["question_graph_proof"] == workflow.graded[1]["question_graph_proof"]


def test_concurrent_copies_share_one_new_reading_after_server_cache_loss(workflow, monkeypatch):
    workflow.grade()
    workflow.discard_proofs()
    reader = server.detect_marks_scheme

    def slower_reading(*args, **kwargs):
        time.sleep(.05)  # Make the other copies contend for the same paper lock.
        return reader(*args, **kwargs)

    monkeypatch.setattr(server, "detect_marks_scheme", slower_reading)
    barrier = threading.Barrier(3)

    def grade(number):
        barrier.wait(timeout=10)
        return workflow.grade(number)

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(grade, [2, 3, 4]))

    assert all(result["release"] for result in outcomes)
    assert len(workflow.reads) == 2
    assert len(workflow.graded) == 4
    assert not server.JOBS
    assert len({payload["job_id"] for payload in workflow.graded}) == 4
    assert len({payload["sheets"][0][0] for payload in workflow.graded}) == 4


def test_bounded_store_eviction_refreshes_the_workflow_token(workflow):
    workflow.store.max_entries = 1
    workflow.grade()
    [old] = workflow.store._records.values()
    workflow.clock[0] += 1
    workflow.store.create(b"another-paper", 3, workflow.scheme,
                          extractors=("claude:offline",), context="another-exam")
    assert not workflow.store.is_available(old.token)

    assert workflow.grade(2)["release"] is True
    assert len(workflow.reads) == len(workflow.graded) == 2


def test_an_explicit_short_lifetime_is_recovered_without_failing_a_copy(workflow):
    workflow.store.ttl_seconds = 60
    workflow.grade()
    workflow.clock[0] += 61

    assert workflow.grade(2)["release"] is True
    assert len(workflow.reads) == len(workflow.graded) == 2


def test_prequeue_eviction_race_retries_once_and_grades_the_copy_once(workflow, monkeypatch):
    queue = server._queue_evaluation
    attempts = []

    async def lose_proof_before_queue(background, **arguments):
        attempts.append(arguments["marks_attestation"])
        if len(attempts) == 1:
            workflow.discard_proofs()
        return await queue(background, **arguments)

    monkeypatch.setattr(server, "_queue_evaluation", lose_proof_before_queue)
    assert workflow.grade()["release"] is True
    assert len(attempts) == 2 and attempts[0] != attempts[1]
    assert len(workflow.reads) == 2
    assert len(workflow.graded) == 1
    assert not server.JOBS


def test_concurrent_prequeue_races_preserve_the_first_refreshed_proof(workflow, monkeypatch):
    workflow.grade()
    [old] = workflow.store._records.values()
    queue = server._queue_evaluation
    reached_queue = threading.Barrier(3)
    cache_lost = threading.Barrier(3)
    attempts = []
    lock = threading.Lock()

    async def simultaneous_race(background, **arguments):
        token = arguments["marks_attestation"]
        with lock:
            attempts.append(token)
        if token == old.token:
            # All three have passed the availability check before one evicts the
            # record. Late handlers for the old token must not evict the new one.
            leader = reached_queue.wait(timeout=10)
            if leader == 0:
                workflow.discard_proofs()
            cache_lost.wait(timeout=10)
        return await queue(background, **arguments)

    monkeypatch.setattr(server, "_queue_evaluation", simultaneous_race)
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(workflow.grade, [2, 3, 4]))
    assert all(result["release"] for result in outcomes)
    assert attempts.count(old.token) == 3 and len(attempts) == 6
    assert len(set(attempts)) == len(workflow.reads) == 2
    assert len(workflow.graded) == 4
    assert not server.JOBS


def test_repeated_prequeue_eviction_has_one_bounded_retry(workflow, monkeypatch):
    queue = server._queue_evaluation
    attempts = []

    async def lose_every_proof(background, **arguments):
        attempts.append(arguments["marks_attestation"])
        workflow.discard_proofs()
        return await queue(background, **arguments)

    monkeypatch.setattr(server, "_queue_evaluation", lose_every_proof)
    with pytest.raises(HTTPException) as raised:
        workflow.grade()
    assert raised.value.status_code == 400
    assert raised.value.detail == UNAVAILABLE_PROOF
    assert len(attempts) == len(workflow.reads) == 2
    assert not workflow.graded and not server.JOBS


@pytest.mark.parametrize("status,detail", [
    (400, "The exam context changed after marks verification; detect marks again."),
    (400, "The verified marks proof belongs to a different question paper."),
    (400, "Answer key could not be read."),
    (409, UNAVAILABLE_PROOF),
])
def test_unrelated_prequeue_failures_are_not_retried(workflow, monkeypatch, status, detail):
    attempts = []

    async def reject(background, **arguments):
        attempts.append(arguments)
        raise HTTPException(status, detail)

    monkeypatch.setattr(server, "_queue_evaluation", reject)
    with pytest.raises(HTTPException) as raised:
        workflow.grade()
    assert raised.value.status_code == status and raised.value.detail == detail
    assert len(attempts) == len(workflow.reads) == 1
    assert not workflow.graded and not server.JOBS


def test_a_background_failure_cannot_enqueue_the_same_copy_again(workflow, monkeypatch):
    attempts = []

    def fail_after_queue(payload):
        attempts.append(payload)
        raise HTTPException(400, UNAVAILABLE_PROOF)

    monkeypatch.setattr(server, "_run_job", fail_after_queue)
    with pytest.raises(HTTPException) as raised:
        workflow.grade()
    assert raised.value.detail == UNAVAILABLE_PROOF
    assert len(attempts) == len(workflow.reads) == len(server.JOBS) == 1


def test_changed_exam_context_gets_its_own_proof(workflow):
    workflow.grade()
    assert workflow.grade(2, subject="Chemistry")["release"] is True
    assert workflow.grade(3)["release"] is True
    assert len(workflow.reads) == 2
    assert len(workflow.graded) == 3
    assert [payload["subject"] for payload in workflow.graded] == [
        "Physics", "Chemistry", "Physics"]


@pytest.mark.parametrize("change,detail", [
    ("paper", "different question paper"),
    ("subject", "exam context changed"),
    ("maxima", "question/maxima rows changed"),
])
def test_day_old_proof_still_rejects_paper_context_and_marks_tampering(workflow, change, detail):
    workflow.grade()
    workflow.clock[0] += 25 * 60 * 60
    [proof] = workflow.store._records.values()
    paper = (workflow.folder / "question_paper.pdf").read_bytes()
    items = [dict(qid="Q1", max=1), dict(qid="Q2", max=2)]
    subject = "Physics"
    if change == "paper":
        paper = _pdf("Different Physics paper\nMaximum Marks: 3")
    elif change == "subject":
        subject = "Chemistry"
    else:
        items[0]["max"], items[1]["max"] = 2, 1
    background = BackgroundTasks()
    answer_key = UploadFile(filename="answer_key.pdf", file=io.BytesIO(
        (workflow.folder / "answer_key.pdf").read_bytes()))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(server._queue_evaluation(
            background, google_token="offline-user", task_id="offline-tampering",
            qp_bytes=paper, qp_name="question_paper.pdf", sheets=[("copy.pdf", b"copy")],
            answer_key=answer_key, step_rubric="", provider="claude", model="",
            student_class="Class 11", subject=subject, exam_type="school", total_marks="3",
            exam_level="", paper_code="", exam_attempt="", applicable_law_date="",
            key_source="upload", marks_items=json.dumps(items), marks_source="ai_verified",
            marks_attestation=proof.token, usd_to_inr="", category="school"))
    assert raised.value.status_code == 400 and detail in raised.value.detail
    assert not background.tasks
    assert len(workflow.reads) == len(workflow.graded) == 1
    assert not server.JOBS  # Failed prequeue validation never recreates the cleaned workflow job.
