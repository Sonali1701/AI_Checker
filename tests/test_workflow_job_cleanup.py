import copy
import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from job_store import JobRepository


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("AICHECKER_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("AI_CHECKER_WORKFLOW_DIR", str(tmp_path / "workflow-data"))
    import server

    for name in ("question_paper.pdf", "answer_key.pdf", "student.pdf"):
        (tmp_path / name).write_bytes(b"fixture PDF bytes")
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    standalone = {"status": "done", "pdfs": {0: b"other student's checked PDF"}}
    repository.save("standalone", standalone)
    monkeypatch.setattr(server, "JOBS", {"standalone": standalone})
    monkeypatch.setattr(server, "_JOB_REPOSITORY", repository)
    monkeypatch.setattr(server, "_require_allowed", lambda authorization: "offline")
    monkeypatch.setattr(server, "_category_or_400", lambda category, exam_type: ("school", "fixture"))
    monkeypatch.setattr(server, "_workflow_marks_key", lambda folder: "paper")
    monkeypatch.setattr(server, "_workflow_marks_lock", lambda key: nullcontext())
    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", SimpleNamespace(is_available=lambda token: True))
    proof = {"marks_attestation": "proof", "items": [{"qid": "Q1", "max": 10}], "method": "ai_verified"}
    monkeypatch.setattr(server, "_WORKFLOW_MARKS", {
        ("paper", "school", "fixture", "class 10", "maths", 10.0, "{}"): proof,
    })
    state = {"job": {"status": "done", "key": "download-key", "results": [{"index": 0, "release": True}],
                     "pdfs": {0: b"workflow checked PDF"},
                     "reports": {0: {"report": {"total_score": 9, "questions": [{"qid": "Q1", "score": 9}]}}}}}

    async def queue(background, **kwargs):
        job = copy.deepcopy(state["job"])
        state["queued_job"] = job
        server.JOBS["workflow-internal"] = job
        repository.save("workflow-internal", job)
        if state.get("background_failure"):
            def fail():
                raise RuntimeError("worker stopped before returning its result")
            background.add_task(fail)
        return {"job_id": "workflow-internal", "download_key": "download-key"}

    monkeypatch.setattr(server, "_queue_evaluation", queue)
    chosen = {"category": "school", "student_class": "Class 10", "subject": "Maths", "total_marks": "10"}
    return server, tmp_path, repository, state, chosen


def test_workflow_job_is_removed_from_memory_and_database_after_result_is_copied(bridge):
    server, folder, repository, state, chosen = bridge
    outcome = server._workflow_grade(folder, folder / "student.pdf", chosen, "Bearer offline")
    assert outcome["release"] is True and outcome["pdf"] == b"workflow checked PDF"
    assert outcome["report"]["total_score"] == 9
    state["queued_job"]["reports"][0]["report"]["total_score"] = 0
    assert outcome["report"]["total_score"] == 9
    assert set(server.JOBS) == {"standalone"}
    assert set(repository.load()) == {"standalone"}
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT job_id FROM artifacts").fetchall() == [("standalone",)]
    assert (folder / "student.pdf").read_bytes() == b"fixture PDF bytes"


def test_workflow_job_cleanup_failure_preserves_result_and_normal_eviction_candidate(bridge, monkeypatch):
    server, folder, repository, state, chosen = bridge
    def locked(job_id):
        raise PermissionError("database temporarily locked")
    monkeypatch.setattr(repository, "delete", locked)
    outcome = server._workflow_grade(folder, folder / "student.pdf", chosen, "Bearer offline")
    assert outcome["release"] is True and outcome["pdf"] == b"workflow checked PDF"
    assert set(server.JOBS) == {"standalone", "workflow-internal"}
    assert set(repository.load()) == {"standalone", "workflow-internal"}


def test_workflow_job_is_retained_if_background_did_not_return_a_result(bridge):
    server, folder, repository, state, chosen = bridge
    state["background_failure"] = True
    with pytest.raises(RuntimeError, match="worker stopped"):
        server._workflow_grade(folder, folder / "student.pdf", chosen, "Bearer offline")
    assert "workflow-internal" in server.JOBS
    assert "workflow-internal" in repository.load()
