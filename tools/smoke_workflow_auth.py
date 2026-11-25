"""Offline Chrome smoke test for the workflow page's own Google sign-in.

All browser requests are intercepted. Google Identity Services and the auth /
workflow APIs are local fixtures; this never contacts Google or the portal.
Run with the app's installed Playwright and system Chrome.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-auth"
ORIGIN = "http://workflow.test"


def credential(email="teacher@pw.live", *, expired=False):
    payload = {"email": email, "sub": email, "exp": int(time.time()) + (-3600 if expired else 3600)}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"offline.{encoded}.fixture"


VALID = credential()
DENIED = credential("denied@pw.live")
EXPIRED = credential(expired=True)
ROW = {
    "submission_id": "copy123", "student_id": "student123", "student_name": "Example Student",
    "program_name": "Complete Programe", "test_series_name": "Class 10 Batch Tests",
    "test_id": "test123", "test_name": "Offline Maths Test",
    "copy_url": "https://example.cloudfront.net/copy.pdf", "portal_status": "Submitted",
    "total_marks": "40",
}
RUN = {
    "id": "a" * 32, "created_at": "2026-10-07T12:00:00", "status": "complete",
    "rows": [{**ROW, "stage": "uploaded", "error": "", "awarded_marks": 32, "has_checked_pdf": True}],
}

GIS_FIXTURE = """window.__offlineGis = {initializeCount: 0, disableCount: 0};
window.google = {accounts: {id: {
  initialize(options) {
    window.__offlineGis.options = options;
    window.__offlineGis.initializeCount++;
  },
  renderButton(container) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Sign in with Google';
    button.dataset.testid = 'offline-google-sign-in';
    button.onclick = () => window.__offlineGis.options.callback({credential: window.__offlineCredential});
    container.replaceChildren(button);
  },
  disableAutoSelect() { window.__offlineGis.disableCount++; }
}}};
"""


class Fixture:
    def __init__(self, browser, *, saved="", configured=True):
        self.configured = configured
        self.auth_requests = []
        self.workflow_requests = []
        self.unexpected_requests = []
        self.errors = []
        self.hold_next_runs = False
        self.held_runs = None
        self.context = browser.new_context(viewport={"width": 1280, "height": 1000})
        self.context.route("**/*", self.handle)
        if saved:
            self.context.add_init_script(f"sessionStorage.setItem('pwGoogleToken', {json.dumps(saved)})")
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(ORIGIN + "/workflow.html", wait_until="domcontentloaded")
        self.page.wait_for_function("window.WorkflowAuth && typeof window.WorkflowAuth.getToken === 'function'")

    def handle(self, route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.hostname == "accounts.google.com" and parsed.path == "/gsi/client":
            route.fulfill(content_type="text/javascript", body=GIS_FIXTURE)
            return
        if parsed.hostname != "workflow.test":
            self.unexpected_requests.append(parsed.hostname + parsed.path)
            route.abort()
            return
        if parsed.path == "/api/config":
            self.json(route, {"google_client_id": "offline.apps.googleusercontent.com" if self.configured else "",
                              "app_name": "AI Copy Checker", "build_id": "offline", "categories": []})
            return
        if parsed.path == "/api/auth/status":
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            self.auth_requests.append(token)
            if token == VALID:
                self.json(route, {"allowed": True, "app": "AI Copy Checker"})
            else:
                self.json(route, {"detail": "This account is not authorized."}, status=403)
            return
        if parsed.path.startswith("/api/workflow"):
            self.workflow_requests.append(parsed.path)
            if request.headers.get("authorization") != "Bearer " + VALID:
                self.json(route, {"detail": "Sign in to continue."}, status=401)
            elif parsed.path.endswith("/runs"):
                if self.hold_next_runs:
                    self.hold_next_runs = False
                    self.held_runs = route
                    return
                self.json(route, {"runs": [RUN]})
            elif parsed.path.endswith("/sheet-tabs"):
                self.json(route, {"import_id": "b" * 32, "tabs": [{"name": "Copies"}], "source_name": "Offline workbook"})
            elif parsed.path.endswith("/preview"):
                self.json(route, {"rows": [ROW]})
            else:
                self.json(route, RUN)
            return
        if parsed.path == "/api/auth/signout":   # sign-out also ends the app session (build 106)
            self.json(route, {"ok": True})
            return
        if parsed.path == "/favicon.ico":
            route.fulfill(status=204)
            return
        path = (ROOT / "static" / parsed.path.lstrip("/")).resolve()
        if not path.is_relative_to(ROOT / "static") or not path.is_file():
            self.unexpected_requests.append(parsed.path)
            route.abort()
            return
        mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(path.suffix, "application/octet-stream")
        route.fulfill(content_type=mime, body=path.read_bytes())

    @staticmethod
    def json(route, data, *, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def sign_in(self, token=VALID):
        self.page.evaluate("token => window.__offlineCredential = token", token)
        self.page.get_by_test_id("offline-google-sign-in").click()

    def signed_in(self):
        self.page.wait_for_function("token => window.WorkflowAuth.getToken() === token", arg=VALID)
        expect(self.page.locator("body")).not_to_have_class(re.compile(r"\bauth-locked\b"))
        expect(self.page.locator("#signOut")).to_be_visible()
        expect(self.page.locator("#googleSignIn")).to_be_hidden()
        expect(self.page.locator("#savedRuns tr")).to_have_count(1)
        if not self.page.evaluate("sessionStorage.getItem('workflowTabRun')"):
            # Step 3 shows this tab's run only (build 135): a fresh tab opens a saved run with Show here.
            expect(self.page.locator("#currentRunTitle")).to_contain_text("No run in this tab yet")
            self.page.locator("#savedRuns tr").first.get_by_role("button", name="Show here").click()
        expect(self.page.locator("#progress")).to_contain_text("Example Student")
        expect(self.page.locator("#runStatus")).to_contain_text("1/1 uploaded")
        assert self.page.evaluate("sessionStorage.getItem('workflowTabRun')") == RUN["id"]
        assert self.page.evaluate("sessionStorage.getItem('pwGoogleToken')") == VALID

    def locked(self):
        expect(self.page.locator("body")).to_have_class(re.compile(r"\bauth-locked\b"))
        assert self.page.evaluate("window.WorkflowAuth.getToken()") == ""

    def close(self):
        try:
            assert not self.errors, self.errors
            assert not self.unexpected_requests, self.unexpected_requests
        finally:
            self.context.close()


def fresh_sign_in_and_reauthentication(browser):
    fixture = Fixture(browser)
    try:
        page = fixture.page
        expect(page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        fixture.locked()
        assert fixture.workflow_requests == [], "Workflow APIs ran before sign-in validation"
        assert page.url == ORIGIN + "/workflow.html"
        fixture.sign_in()
        fixture.signed_in()
        assert page.evaluate("window.__offlineGis.options.auto_select") is False
        assert page.evaluate("window.__offlineGis.options.hd") == "pw.live"
        page.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/offline/edit?gid=0")
        page.locator("#preview").click()
        expect(page.locator("#copies tr")).to_have_count(1)
        page.screenshot(path=str(OUTPUT / "workflow-auth-signed-in.png"), full_page=True)
        page.locator("#signOut").click()
        fixture.locked()
        expect(page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        expect(page.locator("#progress tr")).to_have_count(0)
        expect(page.locator("#copies tr")).to_have_count(0)
        expect(page.locator("#runStatus")).to_have_text("")
        expect(page.locator("#savedRuns tr")).to_have_count(0)
        assert page.evaluate("sessionStorage.getItem('pwGoogleToken')") is None
        assert page.evaluate("window.__offlineGis.disableCount") >= 1
        request_count = len(fixture.workflow_requests)
        page.wait_for_timeout(4200)
        assert len(fixture.workflow_requests) == request_count, "Signed-out page still polled saved runs"
        fixture.sign_in()
        fixture.signed_in()
        assert fixture.auth_requests == [VALID, VALID]
        print("PASS: on-page sign-in, sign-out lock/reset, polling pause, saved-run reauthentication")
    finally:
        fixture.close()


def valid_saved_credential(browser):
    fixture = Fixture(browser, saved=VALID)
    try:
        fixture.signed_in()
        assert fixture.auth_requests == [VALID], "Saved token was not validated by the auth endpoint"
        print("PASS: authorized saved credential is validated and restores saved runs")
    finally:
        fixture.close()


def denied_saved_credential(browser):
    fixture = Fixture(browser, saved=DENIED)
    try:
        expect(fixture.page.locator("#authStatus")).to_contain_text("not authorized")
        fixture.locked()
        expect(fixture.page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        assert fixture.page.evaluate("sessionStorage.getItem('pwGoogleToken')") is None
        assert fixture.auth_requests == [DENIED]
        assert fixture.workflow_requests == []
        fixture.sign_in()
        fixture.signed_in()
        print("PASS: denied saved credential clears, locks, and permits another sign-in")
    finally:
        fixture.close()


def denied_google_credential(browser):
    fixture = Fixture(browser)
    try:
        expect(fixture.page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        fixture.sign_in(DENIED)
        expect(fixture.page.locator("#authStatus")).to_contain_text("not authorized")
        fixture.locked()
        expect(fixture.page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        assert fixture.workflow_requests == []
        fixture.sign_in()
        fixture.signed_in()
        print("PASS: denied Google credential stays locked and can be replaced")
    finally:
        fixture.close()


def expired_saved_credential(browser):
    fixture = Fixture(browser, saved=EXPIRED)
    try:
        expect(fixture.page.locator("#authStatus")).to_contain_text(re.compile("expired", re.I))
        fixture.locked()
        expect(fixture.page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        assert fixture.page.evaluate("sessionStorage.getItem('pwGoogleToken')") is None
        assert fixture.workflow_requests == []
        assert fixture.auth_requests == [], "Known expired credential was sent for authorization"
        fixture.sign_in()
        fixture.signed_in()
        print("PASS: expired saved credential clears before workflow access and can be renewed")
    finally:
        fixture.close()


def missing_client_id(browser):
    fixture = Fixture(browser, configured=False)
    try:
        expect(fixture.page.locator("#authStatus")).to_contain_text(re.compile("not configured|client ID", re.I))
        fixture.locked()
        assert fixture.auth_requests == []
        assert fixture.workflow_requests == []
        fixture.page.screenshot(path=str(OUTPUT / "workflow-auth-not-configured.png"), full_page=True)
        fixture.configured = True
        fixture.page.locator("#authRetry").click()
        expect(fixture.page.get_by_test_id("offline-google-sign-in")).to_be_visible()
        fixture.sign_in()
        fixture.signed_in()
        print("PASS: missing OAuth client ID stays locked; retry recovers after configuration")
    finally:
        fixture.close()


def sign_out_discards_inflight_runs(browser):
    fixture = Fixture(browser, saved=VALID)
    try:
        fixture.signed_in()
        fixture.hold_next_runs = True
        fixture.page.locator("#refresh").click()
        deadline = time.monotonic() + 5
        while fixture.held_runs is None and time.monotonic() < deadline:
            fixture.page.wait_for_timeout(50)
        assert fixture.held_runs is not None, "Expected to intercept an in-flight saved-runs request"
        fixture.page.locator("#signOut").click()
        fixture.locked()
        with fixture.page.expect_response(lambda response: response.url.endswith("/api/workflow/runs")):
            fixture.json(fixture.held_runs, {"runs": [RUN]})
        fixture.held_runs = None
        fixture.page.wait_for_timeout(150)
        expect(fixture.page.locator("#savedRuns tr")).to_have_count(0)
        expect(fixture.page.locator("#progress tr")).to_have_count(0)
        expect(fixture.page.locator("#runStatus")).to_have_text("")
        print("PASS: sign-out discards a previously started saved-runs response")
    finally:
        if fixture.held_runs is not None:
            fixture.held_runs.abort()
        fixture.close()


def main():
    OUTPUT.mkdir(exist_ok=True)
    os.environ["TEMP"] = str(OUTPUT)
    os.environ["TMP"] = str(OUTPUT)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            for scenario in (fresh_sign_in_and_reauthentication, valid_saved_credential,
                             denied_saved_credential, denied_google_credential,
                             expired_saved_credential, missing_client_id,
                             sign_out_discards_inflight_runs):
                scenario(browser)
        finally:
            browser.close()
    print("Workflow auth smoke passed. All requests used offline fixtures; no Google or portal calls.")


if __name__ == "__main__":
    main()
