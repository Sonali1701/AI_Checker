"""Operator's request of 29 Oct 2026 (build 146): checking never waits for uploads.

Checking gave no errors; the portal's upload step did ("Unable to fetch task"), and while the
copies in flight waited to upload no other copy was checked. Now checking workers take each copy
through download and the check, and one portal worker uploads checked copies behind them
(test_workflow_check_first_1030.py: nor does checking depend on assignment, from build 148).
Checked PDFs are kept, named after the student, in "Checked copies/<batch> - <test>/", and
an autopilot pair takes its next test once every copy of its test is checked. Offline doubles.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot
from test_integrated_workflow import LocalClient, token
from test_workflow_copy_retries import WorkflowFixture


def wait_for(condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_copies_are_checked_while_the_portal_holds_up_the_uploads(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    released, uploading = threading.Event(), []

    def slow_portal(stage, student, before_submit):
        if stage == "upload":
            uploading.append(student)
            assert released.wait(20), "the upload was never released"
    fixture.hook = slow_portal
    run = fixture.create(count=6, concurrency=2)
    worker = threading.Thread(target=fixture.work, args=(run,), daemon=True)
    worker.start()
    # The first upload is stuck on the portal, yet every copy gets checked.
    assert wait_for(lambda: all(fixture.calls["grade", f"copy{n}"] == 1 for n in range(6)))
    assert wait_for(lambda: fixture.store.read(run["id"]).get("checking_done") is True)
    saved = fixture.store.read(run["id"])
    assert len(uploading) == 1
    assert sum(row["stage"] == "checked" for row in saved["rows"]) >= 5
    released.set()
    worker.join(30)
    saved = fixture.store.read(run["id"])
    assert saved["status"] == "complete" and all(row["stage"] == "uploaded" for row in saved["rows"])
    assert all(fixture.calls["upload", f"copy{n}"] == 1 for n in range(6))
    # Each copy was assigned once.
    assert all(fixture.calls["assign", f"copy{n}"] == 1 for n in range(6))


def test_checked_copies_are_kept_in_a_batch_test_folder_named_after_the_students(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    grade = fixture.store.grader

    def flag_one(folder, source, options, authorization):
        outcome = grade(folder, source, options, authorization)
        if fixture.sources[str(source)] == "copy1":
            outcome = {**outcome, "release": False, "error": "Q1 needs a teacher", "review_reasons": ["Q1 handwriting unclear"]}
        return outcome
    fixture.store.grader = flag_one
    run = fixture.create(count=2)
    for row in run["rows"]:
        row["test_series_name"] = "VICTORY 2027 (Class 10th) - Batch Tests"
        row["test_name"] = "Board Subjective Test 10 (Maths) || By Shivangi Ma'am"
    fixture.store.save(run)
    saved = fixture.work(run)
    folder = tmp_path / "Checked copies" / "VICTORY 2027 (Class 10th) - Board Subjective Test 10 (Maths) By Shivangi Ma'am"
    assert saved["checked_folder"] == str(folder)
    assert (folder / "STUDENT_0_student0.pdf").read_bytes() == fixture.pdf
    assert (folder / "Faculty review" / "STUDENT_1_student1.pdf").read_bytes() == fixture.pdf   # flagged, uploaded too
    assert [row["stage"] for row in saved["rows"]] == ["uploaded", "uploaded"]
    assert saved["rows"][1]["checked_file"].endswith(str(Path("Faculty review", "STUDENT_1_student1.pdf")))


def test_the_student_sheet_shows_a_checked_copy_waiting_to_upload():
    from workflow_sheet_status import status_for, terminal_status
    row = {"stage": "checked"}
    row["workflow_status"] = status_for(row)
    assert row["workflow_status"] == "checked, waiting to upload" and not terminal_status(row)
    assert status_for({"stage": "checked", "error": "not logged in"}) == "not login"            # errors still show first
    assert status_for({"stage": "checked", "operation": "waiting for the portal: HTTP 429"}) == "error occurred"


def test_the_checked_copies_folder_sits_next_to_the_app(tmp_path):
    store = workflow.WorkflowStore(tmp_path / "workflow_data", lambda *a: {})
    assert store.checked_root == tmp_path / "Checked copies"
    assert workflow.batch_name("Udaan 2.0 2027 (Class 10th) - Batch Tests") == "Udaan 2.0 2027 (Class 10th)"
    assert workflow.batch_name("Board Series") == "Board Series"
    assert workflow.checked_file_name({"student_name": "Tanya Singh", "student_id": "23217614"}) == "TANYA_SINGH_23217614.pdf"
    assert workflow.checked_file_name({"student_name": "", "submission_id": "abc"}) == "abc.pdf"


def test_the_page_opens_a_runs_checked_copies_folder(tmp_path, monkeypatch):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    alice = token("alice")
    fixture = WorkflowFixture(tmp_path / "other")
    template = fixture.create(count=1)
    rows = [{key: template["rows"][0][key] for key in ("submission_id", "student_id", "student_name", "program_name", "test_series_name",
                                                       "test_id", "test_name", "portal_status", "copy_url")}]
    run = s.create(rows, template["options"], workflow.owner_for(alice), fixture.pdf, fixture.pdf)
    opened = []
    monkeypatch.setattr(workflow.os, "startfile", opened.append, raising=False)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {alice}"}
    assert client.post(f"/api/workflow/{run['id']}/open-folder", headers=auth).status_code == 404   # nothing saved yet
    folder = tmp_path / "Checked copies" / "Batch Tests - Maths Test"
    folder.mkdir(parents=True)
    run = s.read(run["id"])
    run["checked_folder"] = str(folder)
    s.save(run)
    reply = client.post(f"/api/workflow/{run['id']}/open-folder", headers=auth)
    assert reply.status_code == 200 and opened == [str(folder)]
    assert client.post(f"/api/workflow/{run['id']}/open-folder", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404


# ---------------------------------------------------------------------------- the autopilot

class Store:
    """What the autopilot reads of the store: active runs and their saved state."""
    run_summary = staticmethod(workflow.WorkflowStore.run_summary)

    def __init__(self):
        self.active, self.runs, self.started = {}, {}, []
        self.latest_authorization, self.owner_emails = {"owner": "Bearer x"}, {}
        self.alerts = workflow.QuietAlerts()

    def read(self, run_id):
        return self.runs[run_id]

    def start(self, run_id, authorization):
        self.started.append(run_id)


def pilot_with(tmp_path, *tests):
    store = Store()
    pilot = workflow_autopilot.Autopilot(store, tmp_path)
    pilot.run_poll = 0.01
    pilot.state = {"id": "a" * 32, "owner": "owner", "status": "running", "created_at": "", "pairs": [],
                   "tests": [dict({"status": "running", "detail": "", "pair": 0}, **test) for test in tests]}
    return pilot, store


def test_a_pair_takes_its_next_test_once_every_copy_is_checked(tmp_path):
    pilot, store = pilot_with(tmp_path, {"run_id": "r1", "test_name": "Maths"})
    test = pilot.state["tests"][0]
    store.active["r1"] = {}
    store.runs["r1"] = {"status": "running", "checking_done": True, "rows": [{"stage": "checked", "test_name": "Maths"}]}
    pilot.follow(test)                                       # returns: the uploads go on behind
    assert test["status"] == "uploading" and "uploads go on" in test["detail"]
    assert pilot.settle_uploads() is True                  # still uploading
    store.active.clear()
    store.runs["r1"] = {"status": "complete", "rows": [{"stage": "uploaded", "test_name": "Maths"}]}
    assert pilot.settle_uploads() is False
    assert test["status"] == "done" and "1 of 1 uploaded" in test["detail"]


def test_uploads_left_after_a_restart_are_started_again_and_the_end_waits_for_them(tmp_path):
    pilot, store = pilot_with(tmp_path, {"run_id": "r1", "test_name": "Maths", "status": "uploading"})
    store.runs["r1"] = {"status": "running", "rows": [{"stage": "checked", "test_name": "Maths"}]}
    assert pilot.settle_uploads() is True and store.started == ["r1"]
    finished = threading.Thread(target=pilot.watch, daemon=True)
    finished.start()
    time.sleep(0.1)
    assert finished.is_alive()                              # the autopilot has not finished yet
    store.runs["r1"] = {"status": "complete", "rows": [{"stage": "uploaded", "test_name": "Maths"}]}
    finished.join(5)
    assert not finished.is_alive() and pilot.state["status"] == "finished"
    assert pilot.state["tests"][0]["status"] == "done"
