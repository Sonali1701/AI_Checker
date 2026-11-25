import pytest


@pytest.fixture(autouse=True)
def _no_copy_retry_delay_by_default(monkeypatch):
    """Exercise the real three-attempt policy without waiting between attempts."""
    import integrated_workflow
    monkeypatch.setattr(integrated_workflow, "COPY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(integrated_workflow, "ASSIGN_HEAD_START_SECONDS", 0.3)
    monkeypatch.setattr(integrated_workflow, "MIN_FREE_MEMORY_MB", 0)   # the tests do not depend on this computer's memory
    monkeypatch.setattr(integrated_workflow, "PORTAL_FETCH_DELAYS", (0,))
    monkeypatch.setattr(integrated_workflow, "WINDOW_CRASH_DELAY_SECONDS", 0)
    # The run's own follow-up rounds (skipped copies, unconfirmed portal actions) are tested
    # on their own; elsewhere a run finishes after its first pass, as the older tests expect.
    monkeypatch.setattr(integrated_workflow, "AUTO_RETRY_DELAYS", ())
    monkeypatch.setattr(integrated_workflow, "internet_is_down", lambda: False)   # no test touches the network


@pytest.fixture(autouse=True)
def _no_sheet_look_by_default(monkeypatch):
    """No test may call the proxy for the second look at a suspected copy
    (pipeline._look_at_sheet): unavailable unless a test patches it, so the
    verified-evidence rules decide, as for a look that cannot be had."""
    import pipeline

    def unavailable(*args, **kwargs):
        raise RuntimeError("the sheet look is not available in tests")
    monkeypatch.setattr(pipeline, "claude_look_at_sheet", unavailable)


@pytest.fixture(autouse=True)
def _quiet_workflow_alerts(monkeypatch):
    """No test may show a notification or send an email."""
    import integrated_workflow
    monkeypatch.setattr(integrated_workflow, "ALERT_CENTER_FACTORY", integrated_workflow.QuietAlerts)
