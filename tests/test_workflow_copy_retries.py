"""A failed student must not hold the rest of an unattended run in retry rounds."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the app folder, however this file is run
from collections import Counter
from datetime import datetime, timedelta
import threading
import time

import fitz
from fastapi import FastAPI, HTTPException
import pytest

import integrated_workflow as workflow


class WorkflowFixture:
    """Offline portal/grader with failures injected at actual workflow boundaries."""

    def __init__(self, folder):
        self.calls = Counter()
        self.calls_lock = threading.Lock()
        self.hook = lambda stage, student, before_submit: None
        self.sources = {}
        with fitz.open() as document:
            document.new_page().insert_text((30, 30), "Fixture answer sheet")
            self.pdf = document.tobytes()
        fixture = self

        class Portal:
            def __init__(self, root, **kwargs):
                pass

            def perform(self, stage, row, options, *args, before_submit=None, **kwargs):
                fixture.record(stage, row["submission_id"], before_submit)
                if before_submit:
                    before_submit()

            def close(self):
                pass

        def download(url, target):
            student = url.rsplit("/", 1)[1].removesuffix(".pdf")
            self.record("download", student)
            target.write_bytes(self.pdf)

        def grade(folder, source, options, authorization):
            self.record("grade", self.sources[str(source)])
            return {"release": True, "pdf": self.pdf,
                    "report": {"questions": [{"qid": "Q1", "score": 9}],
                               "total_score": 9, "overall_remarks": "Checked"}}

        self.store = workflow.WorkflowStore(folder, grade, Portal, download)
        self.store.retry_delay = 0

    def record(self, stage, student, before_submit=None):
        with self.calls_lock:
            self.calls[stage, student] += 1
        self.hook(stage, student, before_submit)

    def create(self, count=2, concurrency=1):
        rows = [{"submission_id": f"copy{n}", "student_id": f"student{n}",
                 "student_name": f"Student {n}", "program_name": "Complete Programe",
                 "test_series_name": "Batch Tests", "test_id": "test123",
                 "test_name": "Maths Test", "portal_status": "Submitted",
                 "copy_url": f"https://example.cloudfront.net/copy{n}.pdf"}
                for n in range(count)]
        options = {"evaluator_email": "teacher@example.com",
                   "deadline": (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                   "category": "school", "student_class": "Class 10", "subject": "Maths",
                   "total_marks": "10", "copies_in_flight": concurrency}
        run = self.store.create(rows, options, "owner", self.pdf, self.pdf)
        self.sources = {str(self.store.copy_paths(run, row["submission_id"])[0]): row["submission_id"]
                        for row in run["rows"]}
        return run

    def work(self, run, mode="run"):
        self.store.work(run["id"], "fixture token", mode)
        return self.store.read(run["id"])

    def wait_for_error(self, run, text, seconds=10):
        """The saved run once its status line shows `text` (a paused run keeps running)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            saved = self.store.read(run["id"])
            if text in (saved.get("error") or ""):
                return saved
            time.sleep(0.05)
        raise AssertionError(f"the run never showed {text!r}: {self.store.read(run['id']).get('error')!r}")

    def wait_idle(self, run, seconds=10):
        deadline = time.monotonic() + seconds
        while self.store.active and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not self.store.active
        return self.store.read(run["id"])


def test_pending_list_lookup_failure_retries_three_times_then_next_student(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def fail_lookup(stage, student, before_submit):
        if stage == "upload" and student == "copy0":
            raise RuntimeError("[find student] Not in your Pending list with this Test_Name and Test_Series")

    fixture.hook = fail_lookup
    saved = fixture.work(fixture.create())
    bad, good = saved["rows"]
    assert saved["status"] == "complete"
    assert bad["stage"] == "skipped" and bad["skipped_from"] == "checked"
    assert bad["failures"] == 3
    assert bad["error"].startswith("Skipped after 3 failed attempts: [find student]")
    assert good["stage"] == "uploaded"
    assert fixture.calls["upload", "copy0"] == 3
    assert all(fixture.calls[stage, "copy0"] == 1 for stage in ("assign", "download", "grade"))


@pytest.mark.parametrize("failing_stage", ["assign", "download", "grade", "upload"])
def test_third_attempt_can_succeed_without_repeating_completed_steps(tmp_path, failing_stage):
    fixture = WorkflowFixture(tmp_path)

    def fail_twice(stage, student, before_submit):
        if stage == failing_stage and fixture.calls[stage, student] < 3:
            raise RuntimeError("Temporary portal or service failure")

    fixture.hook = fail_twice
    saved = fixture.work(fixture.create(count=1))
    assert saved["status"] == "complete" and saved["rows"][0]["stage"] == "uploaded"
    assert fixture.calls[failing_stage, "copy0"] == 3
    assert all(fixture.calls[stage, "copy0"] == 1
               for stage in ("assign", "download", "grade", "upload") if stage != failing_stage)


def test_concurrent_failures_do_not_trigger_a_global_three_failure_halt(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def unavailable(stage, student, before_submit):
        if stage == "assign":
            raise RuntimeError("Portal list unavailable")

    fixture.hook = unavailable
    saved = fixture.work(fixture.create(count=8, concurrency=5))
    assert saved["status"] == "complete"
    # Every copy is checked; each then uses its three attempts in the portal lane.
    assert all(row["stage"] == "skipped" and row["skipped_from"] == "checked" and row["failures"] == 3 for row in saved["rows"])
    assert all(fixture.calls["grade", f"copy{n}"] == 1 for n in range(8))
    assert all(3 <= fixture.calls["assign", f"copy{n}"] <= 4 for n in range(8))
    assert not saved.get("error")


def test_manual_resume_gives_skipped_copy_three_new_attempts_from_saved_stage(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def unavailable(stage, student, before_submit):
        if stage == "upload":
            raise RuntimeError("Pending row missing")

    fixture.hook = unavailable
    run = fixture.create(count=1)
    assert fixture.work(run)["rows"][0]["stage"] == "skipped"
    saved = fixture.work(run)
    assert saved["status"] == "complete" and saved["rows"][0]["failures"] == 3
    assert fixture.calls["upload", "copy0"] == 6
    assert all(fixture.calls[stage, "copy0"] == 1 for stage in ("assign", "download", "grade"))
    fixture.hook = lambda *args: None
    saved = fixture.work(run)
    assert saved["rows"][0]["stage"] == "uploaded"
    assert fixture.calls["upload", "copy0"] == 7


def test_stop_during_copy_retry_wait_does_not_charge_unstarted_attempts(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    fixture.store.retry_delay = 30

    def unavailable(stage, student, before_submit):
        if stage == "assign":
            raise RuntimeError("Portal list unavailable")

    fixture.hook = unavailable
    run = fixture.create()
    fixture.store.start(run["id"], "fixture token")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if fixture.store.read(run["id"])["rows"][0].get("failures") == 1:
            break
        time.sleep(.01)
    else:
        pytest.fail("The first failure was not saved")
    assert fixture.store.stop_run(run["id"])
    while fixture.store.slot(run["id"]) is not None and time.monotonic() < deadline:
        time.sleep(.01)
    saved = fixture.store.read(run["id"])
    assert fixture.store.slot(run["id"]) is None
    assert saved["status"] == "stopped"
    assert saved["rows"][0]["stage"] == "checked" and saved["rows"][0]["failures"] == 1
    assert fixture.calls["grade", "copy0"] == 1


def test_stop_after_successful_third_assignment_preserves_prior_failure_budget(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create(count=1)

    def fail_twice_then_stop(stage, student, before_submit):
        if stage == "assign":
            if fixture.calls[stage, student] < 3:
                raise RuntimeError("Assignment list unavailable")
            assert fixture.store.stop_run(run["id"])

    fixture.hook = fail_twice_then_stop
    saved = fixture.work(run)
    assert saved["status"] == "stopped"
    # The first failed call was the assigning lane's and cost nothing; the second the portal lane's.
    assert saved["rows"][0]["stage"] == "checked" and saved["rows"][0]["portal_assigned"] is True
    assert saved["rows"][0]["failures"] == 1
    assert fixture.calls["assign", "copy0"] == 3 and fixture.calls["upload", "copy0"] == 0

    def fail_upload(stage, student, before_submit):
        if stage == "upload":
            raise RuntimeError("Upload unavailable")

    fixture.hook = fail_upload
    saved = fixture.work(run)
    assert saved["status"] == "complete"
    assert saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["skipped_from"] == "checked" and saved["rows"][0]["failures"] == 3
    assert fixture.calls["assign", "copy0"] == 3 and fixture.calls["upload", "copy0"] == 2


@pytest.mark.parametrize("status_code", [401, 403])
def test_sign_out_pauses_run_without_retrying_or_skipping_students(tmp_path, status_code):
    """A sign-out pauses the run until the owner signs in again (build 122); nothing is
    retried or skipped meanwhile, and Stop ends the wait."""
    fixture = WorkflowFixture(tmp_path)

    def signed_out(stage, student, before_submit):
        raise HTTPException(status_code, "Sign in")

    fixture.hook = signed_out
    run = fixture.create()
    store = fixture.store
    store.signin_poll = 0.05
    store.start(run["id"], "fixture token")
    saved = fixture.wait_for_error(run, "Waiting for you to sign in")
    assert saved["status"] == "running"
    assert all(row["stage"] == "pending" for row in saved["rows"])
    assert all(row["failures"] == 0 for row in saved["rows"])
    assert sum(fixture.calls.values()) == 1
    assert store.stop_run(run["id"])
    saved = fixture.wait_idle(run)
    assert saved["status"] == "stopped" and sum(fixture.calls.values()) == 1


def test_sign_in_resume_retains_copy_failures_and_only_remaining_attempt(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def fail_then_sign_out(stage, student, before_submit):
        if stage == "assign" and student == "copy0":
            if fixture.calls[stage, student] == 3:
                raise HTTPException(401, "Sign in")
            raise RuntimeError("Pending row missing")

    fixture.hook = fail_then_sign_out
    run = fixture.create()
    store = fixture.store
    store.signin_poll = 0.05
    store.start(run["id"], "fixture token")
    saved = fixture.wait_for_error(run, "Waiting for you to sign in")   # paused, not halted
    # The first failed call was the assigning lane's and cost nothing; the second the portal lane's.
    assert saved["rows"][0]["stage"] == "checked"
    assert saved["rows"][0]["failures"] == 1
    assert fixture.calls["assign", "copy0"] == 3
    store.latest_authorization["owner"] = "fresh token"   # the owner signed in again on some tab
    saved = fixture.wait_idle(run)
    assert saved["status"] == "complete"
    assert saved["rows"][0]["stage"] == "skipped" and saved["rows"][0]["failures"] == 3
    assert fixture.calls["assign", "copy0"] == 5
    assert saved["rows"][1]["stage"] == "uploaded"


@pytest.mark.parametrize("uncertain_stage", ["assign", "upload"])
def test_lost_submit_confirmation_is_held_once_while_other_students_continue(tmp_path, uncertain_stage):
    fixture = WorkflowFixture(tmp_path)

    def lose_confirmation(stage, student, before_submit):
        if stage == uncertain_stage and student == "copy0":
            before_submit()
            raise RuntimeError("Submit confirmation lost; check portal first")

    fixture.hook = lose_confirmation
    run = fixture.create()
    saved = fixture.work(run)
    expected = "assignment_uncertain" if uncertain_stage == "assign" else "upload_uncertain"
    assert saved["status"] == "needs_attention"
    assert saved["rows"][0]["stage"] == expected
    assert saved["rows"][0]["error"] == "Submit confirmation lost; check portal first"
    assert saved["rows"][1]["stage"] == "uploaded"
    saved = fixture.work(run)
    assert saved["rows"][0]["error"] == "Submit confirmation lost; check portal first"
    assert fixture.calls[uncertain_stage, "copy0"] == 1


def test_verified_not_done_after_third_uncertain_attempt_starts_fresh_budget(tmp_path):
    from test_integrated_workflow import LocalClient, token

    fixture = WorkflowFixture(tmp_path)
    app = FastAPI()
    auth_token = token("alice")
    store = workflow.install(app, lambda auth: auth_token, fixture.store.grader, tmp_path)
    store.portal_factory = fixture.store.portal_factory
    store.downloader = fixture.store.downloader
    store.retry_delay = 0
    fixture.store = store

    def fail_upload(stage, student, before_submit):
        if stage == "upload":
            if fixture.calls[stage, student] == 3:
                before_submit()
                raise RuntimeError("Upload confirmation lost")
            raise RuntimeError("Pending row unavailable")

    fixture.hook = fail_upload
    run = fixture.create(count=1)
    run["owner"] = workflow.owner_for(auth_token)
    store.save(run)
    saved = fixture.work(run)
    assert saved["rows"][0]["stage"] == "upload_uncertain" and saved["rows"][0]["failures"] == 3
    response = LocalClient(app).post(
        f"/api/workflow/{run['id']}/reconcile", headers={"Authorization": f"Bearer {auth_token}"},
        json={"submission_id": "copy0", "outcome": "not_done"})
    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["stage"] == "checked" and row["failures"] == 0 and row["error"] == ""
    saved = fixture.work(run)
    assert saved["status"] == "complete" and saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["failures"] == 3
    assert fixture.calls["upload", "copy0"] == 6
    assert all(fixture.calls[stage, "copy0"] == 1 for stage in ("assign", "download", "grade"))


def test_unreadable_copy_leaves_a_notice_immediately_and_next_student_runs(tmp_path):
    """From 20 Nov 2026 the copy is not skipped: a notice page for the student is posted in its
    place with 0 marks, so it does not stay pending on the portal. tests/test_unreadable_notice_1120
    covers the page and the copies that are left for another try."""
    fixture = WorkflowFixture(tmp_path)

    def unreadable(stage, student, before_submit):
        if stage == "download" and student == "copy0":
            raise workflow.UnreadableCopy("Downloaded file is not a readable PDF: the PDF is password protected")

    fixture.hook = unreadable
    saved = fixture.work(fixture.create())
    assert saved["status"] == "complete"
    assert saved["rows"][0]["stage"] == "rejection_submitted"
    assert "password protected" in saved["rows"][0]["note"]
    assert saved["rows"][1]["stage"] == "uploaded"
    assert fixture.calls["download", "copy0"] == 1 and fixture.calls["grade", "copy0"] == 0


NO_NAME = "HTTPSConnectionPool(host='d2bps9p1kiy4ka.cloudfront.net', port=443): Max retries exceeded (Caused by NameResolutionError)"


def failing_downloads(fixture, times):
    failed = []

    def hook(stage, student, before_submit):
        if stage == "download" and student == "copy0" and len(failed) < times:
            failed.append(student)
            raise workflow.requests.ConnectionError(NO_NAME)

    fixture.hook = hook


def test_an_internet_drop_at_the_download_costs_the_copy_nothing(tmp_path, monkeypatch):
    fixture = WorkflowFixture(tmp_path)
    failing_downloads(fixture, 8)
    monkeypatch.setattr(workflow, "internet_is_down", lambda: True)
    saved = fixture.work(fixture.create())
    assert saved["rows"][0]["stage"] == "uploaded" and saved["rows"][0]["failures"] == 0
    assert fixture.calls["download", "copy0"] == 9


def test_a_failed_download_with_the_internet_up_still_uses_the_copys_attempts(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    failing_downloads(fixture, 99)
    saved = fixture.work(fixture.create())
    assert saved["rows"][0]["stage"] == "skipped" and saved["rows"][0]["failures"] == 3
    assert fixture.calls["download", "copy0"] == 3 and saved["rows"][1]["stage"] == "uploaded"


def grader_failing_with(fixture, error, times):
    real, failed = fixture.store.grader, []

    def grade(folder, source, options, authorization):
        if len(failed) < times:
            failed.append(error)
            fixture.calls["grade failed"] += 1
            return {"failed": True, "release": False, "error": error}
        return real(folder, source, options, authorization)

    fixture.store.grader = grade


def test_an_internet_drop_during_a_check_costs_the_copy_nothing(tmp_path, monkeypatch):
    fixture = WorkflowFixture(tmp_path)
    grader_failing_with(fixture, NO_NAME, 5)
    monkeypatch.setattr(workflow, "internet_is_down", lambda: True)
    saved = fixture.work(fixture.create(count=1))
    assert saved["rows"][0]["stage"] == "uploaded" and saved["rows"][0]["failures"] == 0 and fixture.calls["grade failed"] == 5


def test_a_network_failure_with_the_internet_up_is_the_copys_own(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    grader_failing_with(fixture, NO_NAME, 99)
    saved = fixture.work(fixture.create(count=1))
    assert saved["rows"][0]["stage"] == "skipped" and fixture.calls["grade failed"] == 3


def test_a_server_error_is_not_mistaken_for_a_lost_connection(tmp_path, monkeypatch):
    fixture = WorkflowFixture(tmp_path)
    grader_failing_with(fixture, "mathpix proxy error 500: Unexpected token", 99)
    monkeypatch.setattr(workflow, "internet_is_down", lambda: True)
    saved = fixture.work(fixture.create(count=1))
    assert saved["rows"][0]["stage"] == "skipped" and fixture.calls["grade failed"] == 3


def test_a_long_outage_alerts_once_and_names_the_internet(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create(count=1)
    row, lock, stop = run["rows"][0], threading.Lock(), threading.Event()
    fetch = {"since": time.monotonic() - workflow.PORTAL_FETCH_ALERT_SECONDS - 1}
    for _ in range(2):
        assert fixture.store.portal_wait(run, row, lock, stop, fetch, workflow.InternetWait("no internet"))
    assert [alert["title"] for alert in fixture.store.alerts.raised] == ["No internet on this computer"]
    assert "Nothing is skipped" in fixture.store.alerts.raised[0]["body"] and "waiting for the internet" in row["operation"]


def test_the_check_for_the_internet_needs_only_one_host(monkeypatch):
    reached = []

    def connect(address, timeout):
        reached.append(address[0])
        if len(reached) == 1:
            raise OSError("no route")
        return open(os.devnull)

    monkeypatch.undo()
    monkeypatch.setattr(workflow.socket, "create_connection", connect)
    assert workflow.internet_is_down() is False and len(reached) == 2


def test_no_host_in_reach_is_no_internet(monkeypatch):
    def connect(address, timeout):
        raise OSError("no route")

    monkeypatch.undo()
    monkeypatch.setattr(workflow.socket, "create_connection", connect)
    assert workflow.internet_is_down() is True


def test_a_copy_too_long_for_an_answer_sheet_is_skipped_unread(tmp_path, monkeypatch):
    fixture = WorkflowFixture(tmp_path)
    monkeypatch.setattr(workflow, "MAX_COPY_PAGES", 0)
    saved = fixture.work(fixture.create())
    assert [row["stage"] for row in saved["rows"]] == ["skipped"] * len(saved["rows"])
    assert "1 pages" in saved["rows"][0]["note"] and "not read" in saved["rows"][0]["note"]
    assert fixture.calls["grade", "copy0"] == 0 and fixture.calls["upload", "copy0"] == 0


def test_a_long_copy_downloaded_before_a_restart_is_skipped_too(tmp_path, monkeypatch):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create()
    for row in run["rows"]:
        row["stage"] = "downloaded"
        fixture.store.copy_paths(run, row["submission_id"])[0].write_bytes(fixture.pdf)
    fixture.store.save(run)
    monkeypatch.setattr(workflow, "MAX_COPY_PAGES", 0)
    saved = fixture.work(run)
    assert saved["rows"][0]["stage"] == "skipped"
    assert fixture.calls["download", "copy0"] == 0 and fixture.calls["grade", "copy0"] == 0


def test_a_copy_of_an_ordinary_length_is_checked(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    saved = fixture.work(fixture.create())
    assert saved["rows"][0]["stage"] == "uploaded" and fixture.calls["grade", "copy0"] == 1


def rejected_run(fixture):
    run = fixture.create()
    for row in run["rows"]:
        row.update(stage="rejected", error="Wrong paper", rejection_reason="Wrong paper")
        fixture.store.copy_paths(run, row["submission_id"])[0].write_bytes(fixture.pdf)
    fixture.store.save(run)
    return run


def test_rejection_lookup_failure_retries_three_times_then_posts_next_rejection(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def missing_row(stage, student, before_submit):
        if stage == "reject" and student == "copy0":
            raise RuntimeError("[find student] No matching Pending row")

    fixture.hook = missing_row
    saved = fixture.work(rejected_run(fixture), "rejections")
    bad, good = saved["rows"]
    assert saved["status"] == "complete"
    assert bad["stage"] == "skipped" and bad["skipped_from"] == "rejected"
    assert bad["error"].startswith("Skipped after 3 failed attempts:")
    assert good["stage"] == "rejection_submitted"
    assert fixture.calls["reject", "copy0"] == 3 and fixture.calls["reject", "copy1"] == 1


def test_rejection_submit_with_lost_confirmation_is_not_posted_again(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def lose_confirmation(stage, student, before_submit):
        if stage == "reject" and student == "copy0":
            before_submit()
            raise RuntimeError("Rejection confirmation lost")

    fixture.hook = lose_confirmation
    run = rejected_run(fixture)
    saved = fixture.work(run, "rejections")
    assert saved["status"] == "needs_attention"
    assert saved["rows"][0]["stage"] == "rejection_uncertain"
    assert saved["rows"][0]["error"] == "Rejection confirmation lost"
    assert saved["rows"][1]["stage"] == "rejection_submitted"
    saved = fixture.work(run, "rejections")
    assert saved["rows"][0]["error"] == "Rejection confirmation lost"
    assert fixture.calls["reject", "copy0"] == 1
