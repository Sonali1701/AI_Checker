"""Operator's request of 30 Oct 2026 (build 148): checking depends on neither assignment nor upload.

That day's autopilot checked almost nothing: one evaluator was not in the portal's list, so every
copy of six tests stopped at assignment before it was checked. The copy comes from the sheet's
link, so the check needs no portal. Assignment runs alongside it, ahead when it can, for one
reason: 27 of the 39 copies assigned that day were already evaluated on the portal, with the sheet
still saying Submitted for 24 of them, and only assignment tells. Offline doubles.
"""
from __future__ import annotations

import os
import sys
import threading

from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
from test_integrated_workflow import FakePortal, LocalClient, options, pdf, row, store, token
from test_workflow_checking_lane import wait_for
from test_workflow_copy_retries import WorkflowFixture

REFUSED = "Evaluator 'teacher@example.com' was not found among 2807 readable evaluator options for this test."


def answers(fixture, assign=None, verify=None):
    """The fixture's portal, answering assignment and its verification as a real one would."""
    base = fixture.store.portal_factory

    class Portal(base):
        def perform(self, stage, copy, chosen, *args, **kwargs):
            outcome = super().perform(stage, copy, chosen, *args, **kwargs)
            if stage == "assign" and assign:
                return assign(copy["submission_id"])
            return verify if stage == "verify_assign" else outcome
    fixture.store.portal_factory = Portal


def test_checking_goes_on_while_the_portal_hangs_on_an_assignment(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    fixture.store.assign_head_start = 0.05
    released = threading.Event()

    def hangs(stage, student, before_submit):
        if stage == "assign":
            assert released.wait(20), "the assignment was never released"
    fixture.hook = hangs
    run = fixture.create(count=6, concurrency=2)
    worker = threading.Thread(target=fixture.work, args=(run,), daemon=True)
    worker.start()
    # copy0 is the one in the admin window; the portal never answers, and the rest are checked.
    assert wait_for(lambda: all(fixture.calls["grade", f"copy{n}"] == 1 for n in range(1, 6)))
    assert fixture.calls["upload", "copy1"] == 0
    released.set()
    worker.join(30)
    saved = fixture.store.read(run["id"])
    assert saved["status"] == "complete" and all(copy["stage"] == "uploaded" for copy in saved["rows"])
    assert all(fixture.calls[stage, f"copy{n}"] == 1 for n in range(6) for stage in ("assign", "grade", "upload"))


def test_a_refused_evaluator_stops_the_portal_lanes_and_no_check(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def refused(stage, student, before_submit):
        if stage == "assign":
            raise RuntimeError(REFUSED)
    fixture.hook = refused
    run = fixture.create(count=5, concurrency=2)
    saved = fixture.work(run)
    assert saved["status"] == "needs_attention" and saved["checking_done"] is True
    assert saved["error"].startswith("Stopped: the evaluator email") and "Checking goes on" in saved["error"]
    assert all(copy["stage"] == "checked" for copy in saved["rows"])
    assert all(fixture.calls["grade", f"copy{n}"] == 1 for n in range(5))
    assert sum(count for (stage, _), count in fixture.calls.items() if stage == "assign") == 1
    assert sum(count for (stage, _), count in fixture.calls.items() if stage == "upload") == 0
    # Fixed: Resume assigns and uploads them, and nothing is checked or paid for again.
    fixture.hook = lambda stage, student, before_submit: None
    saved = fixture.work(run)
    assert saved["status"] == "complete" and all(copy["stage"] == "uploaded" for copy in saved["rows"])
    assert all(fixture.calls["grade", f"copy{n}"] == 1 for n in range(5))


def test_a_copy_the_portal_already_holds_is_found_before_it_is_paid_for(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    fixture.store.assign_head_start = 10
    answers(fixture, assign=lambda copy: {"result": "finished", "status": "InReview"} if copy == "copy1" else {})
    saved = fixture.work(fixture.create(count=4, concurrency=2))
    stages = {copy["submission_id"]: copy["stage"] for copy in saved["rows"]}
    assert stages == {"copy0": "uploaded", "copy1": "already_evaluated", "copy2": "uploaded", "copy3": "uploaded"}
    assert fixture.calls["grade", "copy1"] == 0 and fixture.calls["download", "copy1"] == 0
    assert "InReview" in saved["rows"][1]["note"]


def test_a_copy_found_held_after_its_check_is_not_uploaded(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    answers(fixture, assign=lambda copy: {"result": "finished", "status": "InReview"})

    def first_look_fails(stage, student, before_submit):
        if stage == "assign" and fixture.calls[stage, student] == 1:
            raise RuntimeError("Portal list unavailable")
    fixture.hook = first_look_fails
    saved = fixture.work(fixture.create(count=1))
    assert saved["rows"][0]["stage"] == "already_evaluated" and saved["status"] == "complete"
    assert fixture.calls["grade", "copy0"] == 1 and fixture.calls["upload", "copy0"] == 0


def test_a_failed_look_ahead_costs_the_copy_no_attempt(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def first_look_fails(stage, student, before_submit):
        if stage == "assign" and fixture.calls[stage, student] == 1:
            raise workflow.PortalFetchError("Unable to fetch task")
    fixture.hook = first_look_fails
    saved = fixture.work(fixture.create(count=2))
    assert saved["status"] == "complete" and all(copy["stage"] == "uploaded" for copy in saved["rows"])
    assert all(copy["failures"] == 0 and not copy["error"] for copy in saved["rows"])


def test_a_lost_assignment_confirmation_after_the_check_keeps_the_check(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    answers(fixture, verify={"result": "done", "detail": "assigned"})

    def lost_after_submit(stage, student, before_submit):
        if stage == "assign":
            if fixture.calls[stage, student] == 2:
                before_submit()
            raise RuntimeError("The confirmation did not appear")
    fixture.hook = lost_after_submit
    saved = fixture.work(fixture.create(count=1))
    copy = saved["rows"][0]
    assert copy["stage"] == "uploaded" and copy["portal_assigned"] is True
    assert fixture.calls["grade", "copy0"] == 1 and fixture.calls["assign", "copy0"] == 2


def test_a_rejected_copy_is_assigned_before_its_rejection_is_posted(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    fixture.store.grader = lambda *args: {"rejected": True, "error": "Typed pages", "failure_code": "NOT_ANSWER_SHEET"}

    def first_look_fails(stage, student, before_submit):
        if stage == "assign" and fixture.calls[stage, student] == 1:
            raise RuntimeError("Portal list unavailable")
    fixture.hook = first_look_fails
    saved = fixture.work(fixture.create(count=1))
    assert saved["rows"][0]["stage"] == "rejection_submitted" and saved["rows"][0]["portal_assigned"] is True
    assert fixture.calls["assign", "copy0"] == 2 and fixture.calls["reject", "copy0"] == 1


def test_a_run_saved_by_an_older_build_is_not_assigned_twice(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    _, checked, report_path = s.copy_paths(run, "copy123")
    checked.write_bytes(pdf())
    report_path.write_text('{"questions": [{"qid": "Q1", "score": 9}], "total_score": 9, "overall_remarks": "ok"}', encoding="utf-8")
    saved = s.read(run["id"])
    saved["rows"][0].update(stage="checked", has_checked_pdf=True)
    del saved["rows"][0]["portal_assigned"]
    s.save(saved)
    s.work(run["id"], "token")
    assert s.read(run["id"])["rows"][0]["stage"] == "uploaded" and FakePortal.events == ["upload"]


def test_changing_the_evaluator_assigns_a_checked_copy_again(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    run = s.create([row()], options(), workflow.owner_for(token("alice")), pdf(), pdf())
    saved = s.read(run["id"])
    saved["rows"][0].update(stage="checked", has_checked_pdf=True, portal_assigned=True)
    s.save(saved)
    answer = client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"evaluator_email": "new.teacher@example.com"})
    assert answer.status_code == 200, answer.text
    assert s.read(run["id"])["rows"][0]["portal_assigned"] is False
