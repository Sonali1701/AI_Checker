"""Both portal windows from one worker thread, with the real engine and real Playwright.

    python tools/smoke_workflow_sessions.py

Starts two headless Chrome windows exactly as the engine does (separate profiles
under a temporary workflow folder, on ports 9337 and 9338 so a live run's own
windows on 9237/9238 are never touched), then
drives the integrated workflow's Portal adapter in ONE thread: admin session,
evaluator session, a grader call that uses asyncio.run() in between, then close.
Login and navigation are stubbed; no portal address is opened and no network is
used. This is the shape of the two live failures of 12 October:
"asyncio.run() cannot be called from a running event loop" and
"It looks like you are using Playwright Sync API inside the asyncio loop".
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import integrated_workflow as workflow  # noqa: E402
from portal_vendor import data_filler as engine  # noqa: E402


def stop_leftover_windows():
    """Headless windows a failed earlier check left behind would be reused by start_chrome."""
    import subprocess
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*\build\sessions-*' } "
                    "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True, timeout=60)


def main():
    stop_leftover_windows()
    root = Path(tempfile.mkdtemp(prefix="sessions-", dir=ROOT / "build"))
    os.environ["TEMP"] = os.environ["TMP"] = str(root)
    original = {name: getattr(engine, name) for name in ("wait_for_manual_login", "site_logged_in", "open_allotment_test",
                                                          "search", "allot_student", "open_evaluate", "evaluate_student")}
    launches = []
    real_start = engine.start_chrome

    def headless_start():
        # The engine's own launcher, with the window kept off screen.
        popen = engine.subprocess.Popen
        def quiet(args, *rest, **kwargs):
            # The desk applies its port through the engine's runtime context, so read it from the
            # launch arguments rather than from the module default.
            flag = next((a for a in args if str(a).startswith("--remote-debugging-port=")), "")
            launches.append(int(flag.split("=", 1)[1]) if flag else engine.CDP_PORT)
            return popen([a for a in args if a != "--start-maximized"] + ["--headless=new"], *rest, **kwargs)
        engine.subprocess.Popen = quiet
        try:
            return real_start()
        finally:
            engine.subprocess.Popen = popen

    events = []
    engine.start_chrome = headless_start
    engine.wait_for_manual_login = lambda page: events.append(("login", launches[-1]))   # the port this window was launched on
    engine.site_logged_in = lambda page: True
    engine.open_allotment_test = lambda page, rec: page.goto(
        "data:text/html,<h1>Students</h1><table><tbody><tr><td><input type=checkbox>" + rec.student_id
        + "</td><td>Submitted</td></tr></tbody></table>")
    engine.search = lambda page, value: None
    engine.allot_student = lambda page, rec, confirm, before_submit=lambda: None: (before_submit(), events.append(("assign", rec.student_id)), True)[-1]
    engine.open_evaluate = lambda page, program: page.goto("data:text/html,<h1>Evaluate</h1>")
    engine.evaluate_student = lambda page, context, rec, confirm, before_submit=lambda: None: (before_submit(), events.append(("upload", rec.student_id)))
    portal = workflow.Portal(root)
    portal.windows = {"admin": (9337, "admin_profile"), "evaluator": (9338, "evaluator_profile")}   # never a live run's 9237/9238
    try:
        row = {"program_name": "Program", "test_series_name": "Series", "test_name": "Test", "student_id": "student123", "student_name": "Example"}
        options = {"evaluator_email": "teacher@example.com", "deadline": "2027-01-01T12:00"}
        portal.perform("assign", row, options, before_submit=lambda: events.append("marker-assign"))
        assert threading.current_thread() is threading.main_thread()
        # What the workflow does between portal stages: grade on another thread.
        store = workflow.WorkflowStore(root, lambda *a: asyncio.run(asyncio.sleep(0)) or {"ok": True}, lambda _: portal)
        assert store.grade(root, root / "x.pdf", options, "token") == {"ok": True}
        checked = root / "checked.pdf"
        checked.write_bytes(b"%PDF-1.4 fixture")
        portal.perform("upload", row, options, checked, {"questions": [{"qid": "Q1", "score": 1}], "total_score": 1},
                       before_submit=lambda: events.append("marker-upload"))
        portal.perform("assign", dict(row, student_id="student456"), options)
        assert sorted(launches) == [9337, 9338], launches
        assert len(portal.sessions) == 2 and portal.playwright is not None
        titles = {kind: live["page"].title() for kind, live in portal.sessions.items()}
    finally:
        portal.close()
        for name, value in original.items():
            setattr(engine, name, value)
        engine.start_chrome = real_start
    assert portal.sessions == {} and portal.playwright is None
    result = {"launches": launches, "events": events, "titles": titles, "folder": str(root)}
    assert events == [("login", 9337), "marker-assign", ("assign", "student123"), ("login", 9338), "marker-upload",
                      ("upload", "student123"), ("assign", "student456")], events
    print(json.dumps(result, indent=2))
    print("PASS: admin and evaluator windows from one thread, one Playwright instance, grading in between, both closed.")


if __name__ == "__main__":
    main()
