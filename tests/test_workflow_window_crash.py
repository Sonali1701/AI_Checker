"""Live run of 28 Oct 2026: a portal window's page crashed ("Target crashed": Chrome short of
memory) and the run kept using the dead page, so every later copy of that window failed three
times and was skipped (1123 of 1239 copies of one test). Build 143: a crashed window is opened
again, the copy tries again without using an attempt (a few times per copy), and each window
loads the portal afresh every REFRESH_EVERY_STEPS steps. Offline doubles only.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
from portal_vendor import data_filler as real
from test_workflow_copy_retries import WorkflowFixture
from test_workflow_hidden_windows import ADMIN, OPTIONS, ROW, TEST, Desktop, FakePage, make_portal

ALLOTMENT = real.BASE_URL + "tev/allotment/students"


def admin_portal(tmp_path, desktop, outcomes):
    """An admin window whose Students page gives each of `outcomes` in turn (an error to raise)."""
    errors = iter(outcomes)

    def open_allotment_test(page, record):
        page.goto(ALLOTMENT)
        raise next(errors)
    return make_portal(tmp_path, desktop, {"now": True}, admin=ADMIN, evaluator="", open_allotment_test=open_allotment_test)


def test_a_crashed_window_is_opened_again_instead_of_reused(tmp_path):
    desktop = Desktop()
    portal = admin_portal(tmp_path, desktop, [RuntimeError("Locator.is_visible: Target crashed"), RuntimeError("Programs not visible")])
    with pytest.raises(workflow.PortalWindowCrashed, match="crashed"):
        portal.perform("assign", dict(ROW), OPTIONS)
    assert portal.sessions["admin"]["crashed"] and portal.board.status()["windows"][0]["why"] == ""   # not a problem to show
    with pytest.raises(RuntimeError, match="Programs not visible"):
        portal.perform("assign", dict(ROW), OPTIONS)
    assert desktop.launches == [(9601, "hidden"), (9601, "hidden")]      # a fresh window on the same profile
    assert desktop.pages[0].closed and not portal.sessions["admin"].get("crashed")


class CrashPage(FakePage):
    def on(self, event, handler):
        self.handlers = {**getattr(self, "handlers", {}), event: handler}


class CrashDesktop(Desktop):
    def start_chrome(self):
        chrome = super().start_chrome()
        self.pages[-1] = CrashPage(self)
        return chrome


def test_chromes_crash_event_also_marks_the_window(tmp_path):
    desktop = CrashDesktop()
    portal = make_portal(tmp_path, desktop, {"now": True})
    assert portal.exam_documents(TEST) == {"question_paper": [], "answer_key": [], "links": {"question_paper": None, "answer_key": []}}
    desktop.pages[0].handlers["crash"]()                                # Chrome reports the crash between steps
    portal.exam_documents(TEST)
    assert len(desktop.launches) == 2 and desktop.pages[0].closed


def test_a_crash_while_reading_the_tests_files_is_a_wait_not_a_failure(tmp_path):
    desktop = Desktop()
    portal = make_portal(tmp_path, desktop, {"now": True})
    replies = iter([RuntimeError("APIRequestContext.get: Target crashed"), {"data": {"name": "Maths Test"}}])

    def api_get(page, url):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    portal.papers.api_get = api_get
    with pytest.raises(workflow.PortalWindowCrashed):
        portal.exam_documents(TEST)
    assert portal.exam_documents(TEST) == {"question_paper": [], "answer_key": [], "links": {"question_paper": None, "answer_key": []}} and len(desktop.launches) == 2


def test_each_window_loads_the_portal_afresh_every_few_steps(tmp_path):
    desktop = Desktop()
    portal = admin_portal(tmp_path, desktop, [RuntimeError("Programs not visible")] * 2)
    portal.REFRESH_EVERY_STEPS = 2
    for _ in range(2):
        with pytest.raises(RuntimeError):
            portal.perform("assign", dict(ROW), OPTIONS)
    # The login check's portal load, the step, then on the second step a fresh load first.
    assert desktop.pages[0].visits == [real.BASE_URL, ALLOTMENT, real.BASE_URL, ALLOTMENT]


def crash_at_upload(fixture, times):
    def hook(stage, student, before_submit):
        if stage == "upload" and fixture.calls[stage, student] <= times:
            raise workflow.PortalWindowCrashed("The portal window of the EVALUATOR account x crashed (its page ran out of memory)")
    return hook


def test_a_copy_whose_window_crashed_is_uploaded_without_using_an_attempt(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    fixture.hook = crash_at_upload(fixture, 2)
    saved = fixture.work(fixture.create(count=1))
    row = saved["rows"][0]
    assert row["stage"] == "uploaded" and not row.get("failures") and fixture.calls["upload", "copy0"] == 3
    crashes = [alert for alert in fixture.store.alerts.raised if alert["title"] == "A portal window crashed"]
    # One alert key, which the alert centre sends once an hour; alerts carry no student names.
    assert crashes and {alert["key"] for alert in crashes} == {"window-crash"} and "Student" not in crashes[0]["body"]


def test_a_copy_that_keeps_crashing_its_window_is_skipped_after_its_attempts(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    fixture.hook = crash_at_upload(fixture, 100)
    saved = fixture.work(fixture.create(count=2))
    bad = saved["rows"][0]
    assert bad["stage"] == "skipped" and bad["failures"] == workflow.COPY_FAILURE_LIMIT
    assert fixture.calls["upload", "copy0"] == workflow.WINDOW_CRASH_RETRIES + workflow.COPY_FAILURE_LIMIT
    assert "crashed" in bad["error"] and "times on this copy" in bad["error"]
