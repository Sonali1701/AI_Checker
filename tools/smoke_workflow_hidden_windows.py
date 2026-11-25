"""Real-Chrome smoke: hidden portal windows (build 140).

The real engine and Portal on ports 9347 (never a live run's 9237/9238) against a local page in
place of the portal. A copy failing in the admin window: the window is hidden (headless Chrome,
checked on its command line) and its failure picture is real; on the copy's last attempt it is
reopened on screen at the failed page; Hide makes it hidden again. Sign in the windows (build 141):
a window that is not signed in comes on screen, is not reloaded while the operator signs in, and
hides itself once signed in. A crashed page (build 143) is replaced by a fresh window. The window's login
(localStorage, as the portal keeps it) survives both switches. "On screen" windows are placed
off the screen here so the smoke does not cover the operator's desktop.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import integrated_workflow as workflow  # noqa: E402
from portal_vendor import data_filler as engine  # noqa: E402

PORT = 9347
ADMIN = "smoke.admin@example.com"


def stop_leftover_windows():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*\\build\\hidden-*' } "
                    "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True, timeout=60)


def browser_command_line():
    """The command line of the Chrome browser process on PORT (not its helper processes)."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*--remote-debugging-port=%d*' "
              "-and $_.CommandLine -notlike '*--type=*' } | Select-Object -ExpandProperty CommandLine" % PORT)
    return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60).stdout


def serve_portal_double():
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"<title>Portal double</title><h1>{self.path}</h1>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def main():
    stop_leftover_windows()
    (ROOT / "build").mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hidden-", dir=ROOT / "build"))
    server, base = serve_portal_double()
    original = {name: getattr(engine, name) for name in ("BASE_URL", "wait_for_manual_login", "site_logged_in", "open_allotment_test")}
    popen = engine.subprocess.Popen

    def off_screen(args, *rest, **kwargs):
        # An "on screen" window, kept off the operator's screen for this smoke (Chrome launches only:
        # this is the process-wide Popen).
        if not any(str(a).startswith("--remote-debugging-port=") for a in args):
            return popen(args, *rest, **kwargs)
        args = [a for a in args if a != "--start-maximized"]
        if "--headless=new" not in args:
            args[-1:-1] = ["--window-position=-2400,-2400", "--window-size=1280,900"]
        return popen(args, *rest, **kwargs)

    engine.subprocess.Popen = off_screen
    engine.BASE_URL = base
    engine.wait_for_manual_login = lambda page: page.goto(base)
    signed = {"now": True}
    engine.site_logged_in = lambda page: signed["now"]

    def open_allotment_test(page, record):
        page.goto(base + "tev/allotment/students")
        raise RuntimeError("Programs not visible")
    engine.open_allotment_test = open_allotment_test

    portal = workflow.Portal(root, admin=ADMIN, admin_port=PORT)
    portal.board = workflow.PortalWindows(root)
    row = {"program_name": "Program", "test_series_name": "Series", "test_name": "Test", "student_id": "student123", "student_name": "Example"}
    options = {"evaluator_email": "smoke.eval@example.com", "deadline": "2027-01-01T12:00"}
    result = {}
    try:
        try:
            portal.perform("assign", dict(row, failures=0), options)
        except RuntimeError as exc:
            assert "Programs not visible (screenshot: " in str(exc), exc
        line = browser_command_line()
        assert "--headless=new" in line and "--user-agent=" in line and "HeadlessChrome" not in line, line
        page = portal.sessions["admin"]["page"]
        assert "HeadlessChrome" not in page.evaluate("navigator.userAgent")
        page.evaluate("localStorage.setItem('access_token', 'smoke-token')")
        entry = portal.board.status()["windows"][0]
        shot = root / "screenshots" / entry["screenshot"]
        assert not entry["shown"] and entry["why"] == "" and shot.read_bytes()[:4] == b"\x89PNG", entry
        result["hidden"] = {"screenshot_bytes": shot.stat().st_size}

        started = time.monotonic()
        try:
            portal.perform("assign", dict(row, failures=workflow.COPY_FAILURE_LIMIT - 1), options)
        except RuntimeError:
            pass
        result["shown_after_s"] = round(time.monotonic() - started, 1)
        line = browser_command_line()
        assert line and "--headless" not in line, line
        page = portal.sessions["admin"]["page"]
        assert page.url == base + "tev/allotment/students", page.url
        page.goto(base)
        assert page.evaluate("localStorage.getItem('access_token')") == "smoke-token", "the login was lost"
        entry = portal.board.status()["windows"][0]
        assert entry["shown"] and entry["why"] == "problem", entry

        started = time.monotonic()
        portal.board.hide("admin", ADMIN)
        portal.arrange_windows()
        result["hidden_again_after_s"] = round(time.monotonic() - started, 1)
        assert "--headless=new" in browser_command_line()
        page = portal.sessions["admin"]["page"]
        assert page.evaluate("localStorage.getItem('access_token')") == "smoke-token", "the login was lost"
        assert not portal.board.status()["windows"][0]["shown"]

        # Sign in the windows: this window is not signed in, so it comes on screen and waits for the
        # operator without reloading; once signed in, it hides itself.
        signed["now"] = False
        portal.SIGN_IN_CHECK_SECONDS = 1
        visits = []
        def operator_signs_in():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not (portal.board.status()["windows"][0]["shown"] and "--headless" not in browser_command_line()):
                time.sleep(0.5)
            visits.append(portal.board.status()["windows"][0]["why"])
            time.sleep(2)
            signed["now"] = True
        helper = threading.Thread(target=operator_signs_in)
        helper.start()
        started = time.monotonic()
        assert portal.sign_in("admin") == "signed_in"
        helper.join()
        result["signed_in_after_s"] = round(time.monotonic() - started, 1)
        assert visits == ["sign_in"], visits
        entry = portal.board.status()["windows"][0]
        assert entry["signed_in"] and not entry["shown"] and "--headless=new" in browser_command_line(), entry

        # Build 143: the page crashes, as when Windows ends Chrome's page processes for lack of
        # memory (CDP's Page.crash never answers, so the renderers are ended instead). The next
        # step must not reuse the dead page: it waits once (no attempt used) or opens a fresh window.
        old = portal.sessions["admin"]["page"]
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like "
                        f"'*--type=renderer*' -and $_.CommandLine -like '*{root.name}*' }} | ForEach-Object {{ Stop-Process -Id "
                        "$_.ProcessId -Force -ErrorAction SilentlyContinue }"], capture_output=True, timeout=60)
        time.sleep(1)
        outcome = []
        for _ in range(2):
            try:
                portal.perform("assign", dict(row, failures=0), options)
            except workflow.PortalWindowCrashed:
                outcome.append("waited")
                continue
            except RuntimeError as exc:
                assert "Programs not visible" in str(exc), exc
                outcome.append("fresh window")
                break
        page = portal.sessions["admin"]["page"]
        assert outcome[-1] == "fresh window" and page is not old and page.url == base + "tev/allotment/students", (outcome, page.url)
        result["after_crash"] = outcome
    finally:
        portal.close()
        engine.subprocess.Popen = popen
        for name, value in original.items():
            setattr(engine, name, value)
        server.shutdown()
    time.sleep(1)
    assert not browser_command_line().strip(), "Chrome is still running on the smoke port"
    result["folder"] = str(root)
    print(json.dumps(result, indent=2))
    print("PASS: hidden (headless) window with a real failure picture, on screen at the failed page on the last attempt, "
          "hidden again on Hide, login kept through both switches, Sign in the windows (on screen until signed in, then hidden), "
          "a crashed page replaced by a fresh window, closed at the end.")


if __name__ == "__main__":
    main()
