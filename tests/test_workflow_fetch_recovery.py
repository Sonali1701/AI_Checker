"""A broken portal list must not trigger a burst of retries from queued students."""
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import integrated_workflow as workflow
from portal_recovery import FetchRecovery, ReadFailures, retry_seconds
from test_workflow_alerts_and_review import FakeEngine, FakePage


ROW = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "student1"}
OPTIONS = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T10:00"}


def adapter(monkeypatch):
    monkeypatch.setattr(workflow, "PORTAL_FETCH_DELAYS", (20, 40, 60, 120))
    monkeypatch.setattr("portal_recovery.random.uniform", lambda *a: 0)
    clock = [100.0]
    engine = FakeEngine()
    engine.reset_page = Mock()
    engine.open_allotment_test = Mock(side_effect=engine.open_allotment_test)
    portal = workflow.Portal.__new__(workflow.Portal)
    portal.engine = engine
    page = FakePage(engine)
    live = {"page": page, "context": None}
    portal.session = Mock(return_value=live)
    portal.fetch_recovery = {"admin": FetchRecovery(lambda: clock[0])}
    return portal, engine, page, clock


def test_queued_students_share_one_account_cooldown_and_one_recovery_probe(monkeypatch):
    portal, engine, page, clock = adapter(monkeypatch)
    with pytest.raises(workflow.PortalFetchError) as first:
        portal._perform("assign", ROW, OPTIONS)
    assert first.value.retry_after == 20
    assert engine.visits == []  # no immediate reload and no reset-triggered request
    engine.reset_page.assert_not_called()
    for number in range(1, 9):
        clock[0] += 1
        with pytest.raises(workflow.PortalFetchError) as queued:
            portal._perform("assign", {**ROW, "student_id": f"student{number}"}, OPTIONS)
        assert queued.value.retry_after == 120 - clock[0]
    assert engine.open_allotment_test.call_count == portal.session.call_count == 1
    assert portal.recovery_for("admin").failures == 1
    assert portal.recovery_for("evaluator").remaining() == 0
    clock[0] = 120
    with pytest.raises(workflow.PortalFetchError) as probe:
        portal._perform("assign", ROW, OPTIONS)
    assert engine.visits == [engine.BASE_URL]
    assert probe.value.retry_after == 40
    assert engine.open_allotment_test.call_count == 2
    clock[0] = 160
    engine.open_allotment_test.side_effect = None
    portal.assign_copy = lambda *a: {"result": "assigned"}
    assert portal._perform("assign", ROW, OPTIONS) == {"result": "assigned"}
    assert portal.recovery_for("admin").failures == portal.recovery_for("admin").remaining() == 0
    assert len(engine.visits) == 2
    engine.reset_page.assert_called_once()


def test_list_timeout_with_fetch_toast_becomes_account_wait_not_student_failure(monkeypatch):
    portal, engine, page, clock = adapter(monkeypatch)
    engine.open_allotment_test.side_effect = RuntimeError("Search field never appeared")
    portal.fetch_toast_visible = lambda page: True
    with pytest.raises(workflow.PortalFetchError):
        portal._perform("assign", ROW, OPTIONS)
    assert portal.recovery_for("admin").failures == 1
    engine.reset_page.assert_not_called()


def test_after_submit_a_fetch_error_is_never_reclassified_as_safe_to_repeat(monkeypatch):
    portal, engine, page, clock = adapter(monkeypatch)
    engine.open_allotment_test.side_effect = None
    intent = Mock()
    def clicked(page, record, before_submit):
        before_submit()
        raise engine.AbortRun("Unable to fetch task")
    portal.assign_copy = clicked
    portal.window_problem = Mock()
    with pytest.raises(engine.AbortRun):
        portal._perform("assign", ROW, OPTIONS, before_submit=intent)
    intent.assert_called_once()
    assert portal.recovery_for("admin").failures == 0


def request(url="https://backend.example/workflows/tasks?student=private", method="GET"):
    return Mock(url=url, method=method, resource_type="xhr", failure=None)


@pytest.mark.parametrize("code,phrase", [(401, "denied"), (403, "permissions"), (429, "limiting"),
                                         (500, "server failed"), (503, "server failed"), (400, "rejected")])
def test_browser_diagnostics_record_only_status_not_student_urls_or_credentials(code, phrase):
    reads = ReadFailures("https://backend.example/workflows/organizations")
    req = request()
    reads.started(req)
    reads.response(SimpleNamespace(request=req, status=code, headers={"retry-after": "90"}))
    assert f"HTTP {code}" in reads.detail and phrase in reads.detail
    assert "private" not in reads.detail and "https://" not in reads.detail.lower()
    assert reads.retry_after == 90
    assert not reads.requests


def test_diagnostics_ignore_old_responses_other_hosts_and_mutations():
    reads = ReadFailures("https://backend.example/")
    for req in (request(), request("https://backend.example.evil/tasks"), request(method="POST")):
        if req.method != "GET" or "evil" in req.url:
            reads.started(req)
        reads.response(SimpleNamespace(request=req, status=503, headers={}))
    assert not reads.detail
    assert not reads.requests


def test_failed_reads_ignore_navigation_aborts_and_listener_cleanup():
    reads = ReadFailures("https://backend.example/")
    page = Mock()
    with reads.watch(page):
        req = request()
        reads.started(req)
        req.failure = "net::ERR_ABORTED"
        reads.failed(req)
        assert not reads.detail
        req = request()
        reads.started(req)
        req.failure = "net::ERR_TIMED_OUT"
        reads.failed(req)
        assert "network failure" in reads.detail
    assert page.on.call_count == page.remove_listener.call_count == 3
    assert not reads.requests


def test_retry_after_honored_and_bounded():
    recovery = FetchRecovery(lambda: 100)
    recovery.failed((20, 40), "HTTP 429", retry_after=180)
    assert 180 <= recovery.remaining() <= 190
    assert retry_seconds("9999999") == 900
    assert retry_seconds("bad") == retry_seconds("-2") == 0
    assert 118 <= retry_seconds(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120))) <= 120


def test_waiting_for_fetch_is_a_retryable_sheet_status():
    from workflow_sheet_status import status_for, terminal_status
    row = {"stage": "assigned", "operation": "waiting for the portal: HTTP 429"}
    row["workflow_status"] = status_for(row)
    assert row["workflow_status"] == "error occurred" and not terminal_status(row)


def test_account_cooldown_is_interruptible_by_stop(tmp_path):
    import threading
    from test_integrated_workflow import store, row, options, pdf
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    stop = threading.Event()
    stop.set()
    assert not s.portal_wait(run, run["rows"][0], s.save_lock(run["id"]), stop, {},
                             workflow.PortalFetchError("Unable to fetch task: HTTP 429", retry_after=300))
    assert run["rows"][0].get("failures", 0) == 0
    assert "HTTP 429" in run["rows"][0]["operation"]
