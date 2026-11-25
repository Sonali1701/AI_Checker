"""Live run of 2 Nov 2026: another computer worked the same tests. Three copies left their evaluator after
they were assigned here. Each went to the browser, whose login the direct sign-in had ended, and waited for
ever on "Unable to fetch task", holding every copy of that account. Such a copy is now recorded and passed.
A fake portal stands in; nothing reaches the network.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import portal_api
from portal_api import CopyMovedOn, UseTheBrowser
from test_portal_api_1030 import EVALUATOR, OPTIONS, REPORT, ROW, FakePortal, checked, client, wrapper


@pytest.fixture(autouse=True)
def fresh_evaluator_list(monkeypatch):
    monkeypatch.setattr(portal_api, "_evaluators", {})
    monkeypatch.setattr(portal_api, "_evaluators_at", 0.0)


def store():
    made = workflow.WorkflowStore.__new__(workflow.WorkflowStore)
    made.save = lambda run: None
    return made


@pytest.mark.parametrize("status,holder,finished", [
    ("InReview", "someone", True), ("InReview", EVALUATOR, True), ("Evaluated", "someone", True),
    ("InEvaluation", "someone", False),
])
def test_a_copy_that_left_this_evaluator_is_never_posted(tmp_path, status, holder, finished):
    fake = FakePortal(status, holder)
    with pytest.raises(CopyMovedOn) as caught:
        client(fake).submit(dict(ROW), checked(tmp_path), {1: 2}, 2.0, "Well done", lambda: pytest.fail("no submit"))
    assert caught.value.finished is finished
    assert not fake.paths("PUT") and not fake.paths("S3")


def test_a_copy_not_yet_assigned_still_goes_to_the_browser(tmp_path):
    with pytest.raises(UseTheBrowser):
        client(FakePortal("Submitted")).submit(dict(ROW), checked(tmp_path), {1: 2}, 2.0, "", lambda: None)


@pytest.mark.parametrize("stage", ["upload", "reject"])
def test_the_browser_is_left_out_of_it(tmp_path, stage):
    portal = wrapper(FakePortal("InReview", "someone"), tmp_path)
    outcome = portal.perform_direct(stage, dict(ROW), OPTIONS, checked(tmp_path), REPORT, reason="Typed pages")
    assert outcome == {"result": "moved_on", "status": "InReview", "finished": True}
    assert not portal.browser_calls and not portal.direct_off and portal.direct_refusals == 0


def test_a_finished_copy_counts_as_already_evaluated():
    row = {"stage": "rejected", "error": "Typed pages", "note": "Reassigned on the portal"}
    assert store().copy_moved_on({}, row, {"result": "moved_on", "status": "InReview", "finished": True}) is True
    assert row["stage"] == "already_evaluated" and row["error"] == "" and "Nothing was posted" in row["note"]


def test_a_copy_someone_else_still_holds_is_skipped_and_can_be_picked_up_later():
    row = {"stage": "checked", "error": ""}
    assert store().copy_moved_on({}, row, {"result": "moved_on", "status": "InEvaluation", "finished": False}) is True
    assert row["stage"] == "skipped" and row["skipped_from"] == "checked" and "another evaluator" in row["note"]
    assert not row["auto_retry"]   # no follow-up round fights the other computer for it


@pytest.mark.parametrize("outcome", [None, {}, {"result": "done"}, {"result": "browser"}])
def test_an_ordinary_answer_changes_nothing(outcome):
    row = {"stage": "checked"}
    assert store().copy_moved_on({}, row, outcome) is False
    assert row == {"stage": "checked"}


def test_a_rejection_of_a_taken_copy_is_not_counted_as_posted(tmp_path):
    made = store()
    source = checked(tmp_path)
    made.copy_paths = lambda run, stem: (source, None, None)
    made.assign_copy = lambda run, row, portal: True
    row = {"submission_id": "c" * 24, "stage": "rejected", "rejection_reason": "Typed pages", "error": "Typed pages"}
    run = {"options": {"evaluator_email": "teacher@pw.live"}}
    portal = type("Desk", (), {"perform": staticmethod(lambda *args, **kwargs: {"result": "moved_on", "status": "InReview", "finished": True})})
    made.process_rejection(run, row, portal)
    assert row["stage"] == "already_evaluated" and "awarded_marks" not in row
