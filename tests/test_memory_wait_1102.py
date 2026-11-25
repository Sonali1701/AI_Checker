"""Live run of 2 Nov 2026: under the free-memory limit a run went one copy at a time for hours, and the page
said only "waiting for grading slot". The wait now names memory, and the operator is told once. No network.
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow


@pytest.fixture(autouse=True)
def memory_limit(monkeypatch):
    monkeypatch.setattr(workflow, "MIN_FREE_MEMORY_MB", 1200)


def store(free, used=1, limit=10):
    made = workflow.WorkflowStore.__new__(workflow.WorkflowStore)
    made.grading_slots = workflow.GradingSlots(limit, free_memory=lambda: free)
    made.grading_slots.used = used
    return made


@pytest.fixture
def alerts():
    return workflow.QuietAlerts()


def test_short_memory_is_named_and_the_operator_is_told(alerts):
    made = store(393.4)
    made.alerts = alerts
    assert made.slot_wait_reason() == "waiting for free memory (393 MB free, 1200 MB needed)"
    assert [entry["key"] for entry in alerts.raised] == ["memory"] and "393 MB" in alerts.raised[0]["body"]


def test_full_slots_are_not_blamed_on_memory(alerts):
    made = store(393.0, used=10)
    made.alerts = alerts
    assert made.slot_wait_reason() == "waiting for grading slot" and not alerts.raised


@pytest.mark.parametrize("free", [None, 1200, 8000])
def test_enough_memory_says_what_it_always_said(alerts, free):
    made = store(free)
    made.alerts = alerts
    assert made.slot_wait_reason() == "waiting for grading slot" and not alerts.raised


def test_an_alert_that_fails_does_not_stop_the_copy():
    made = store(393.0)
    made.alerts = None   # raise_alert is missing, as a broken alert would be
    assert "free memory" in made.slot_wait_reason()


def test_the_wait_reaches_the_copy_and_it_still_runs(alerts):
    made = store(393.0)
    made.alerts = alerts
    made.grader = lambda *args: "graded"
    stages = []
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=made.grade("f", "s", {}, "a", progress=stages.append)))
    worker.start()
    while not stages:
        pass
    made.grading_slots.release()   # the running copy ends: one copy may always run
    worker.join(5)
    assert stages == ["waiting for free memory (393 MB free, 1200 MB needed)", "checking"] and result["out"] == "graded"
