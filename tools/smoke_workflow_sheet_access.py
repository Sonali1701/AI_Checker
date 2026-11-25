"""Offline Chrome checks of the production workflow's Google Sheets connection.

The browser loads the actual HTML and JavaScript. GIS and every HTTP endpoint
are intercepted fixtures; this never opens Google or assigns/uploads a copy.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

from smoke_workflow_auth import Fixture, GIS_FIXTURE, VALID, ROW


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-sheet-access"
SHEET = "https://docs.google.com/spreadsheets/d/offline-access/edit?gid=711687345"
GOOGLE_ACCESS = "offline-google-read-access-do-not-persist"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"

OAUTH_FIXTURE = """
window.__offlineOauth = {
  mode: 'success', granted: true, expires: 3600, clients: [], requests: [],
  deliver() {
    const options = this.clients[this.clients.length - 1];
    if (this.mode === 'popup_closed') options.error_callback({type: 'popup_closed'});
    else if (this.mode === 'popup_failed_to_open') options.error_callback({type: 'popup_failed_to_open'});
    else if (this.mode === 'denied') options.callback({error: 'access_denied'});
    else options.callback({access_token: 'offline-google-read-access-do-not-persist',
      expires_in: this.expires, scope: 'https://www.googleapis.com/auth/spreadsheets'});
  }
};
window.google.accounts.oauth2 = {
  hasGrantedAllScopes(response, scope) {
    window.__offlineOauth.checkedScope = scope;
    return window.__offlineOauth.granted;
  },
  initTokenClient(options) {
    window.__offlineOauth.clients.push(options);
    return {requestAccessToken(request) {
      window.__offlineOauth.requests.push(request);
      if (window.__offlineOauth.mode !== 'deferred') queueMicrotask(() => window.__offlineOauth.deliver());
    }};
  }
};
"""


class SheetFixture(Fixture):
    def __init__(self, browser):
        self.requests = []
        self.hold_tabs = False
        self.held_tabs = None
        super().__init__(browser, saved=VALID)
        self.signed_in()

    def handle(self, route):
        parsed = urlparse(route.request.url)
        if parsed.hostname == "accounts.google.com" and parsed.path == "/gsi/client":
            route.fulfill(content_type="text/javascript", body=GIS_FIXTURE + OAUTH_FIXTURE)
            return
        if parsed.path.startswith("/api/workflow"):
            self.requests.append({"path": parsed.path, "headers": dict(route.request.headers),
                                  "body": route.request.post_data or ""})
            if parsed.path.endswith("/sheets/connect"):
                self.json(route, {"connected": True})
                return
            if parsed.path.endswith("/sheet-tabs") and self.hold_tabs:
                self.hold_tabs = False
                self.held_tabs = route
                return
            if parsed.path.endswith("/sheet-tabs"):
                connected = bool(route.request.headers.get("x-google-sheets-token")) and 'name="sheet_url"' in (route.request.post_data or "")
                self.json(route, {"import_id": "b" * 32, "tabs": [{"name": "Copies"}],
                                  "source_name": "Offline workbook", "requires_google_token": connected})
                return
        super().handle(route)

    def configure(self, *, mode="success", granted=True, expires=3600):
        self.page.evaluate("options => Object.assign(window.__offlineOauth, options)",
                           {"mode": mode, "granted": granted, "expires": expires})

    def connect(self):
        self.page.locator("#connectSheets").click()

    def wait_held(self):
        deadline = time.monotonic() + 5
        while self.held_tabs is None and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        assert self.held_tabs is not None, "Expected a held tab-loading response"

    def tabs_requests(self):
        return [request for request in self.requests if request["path"].endswith("/sheet-tabs")]

    def close(self):
        if self.held_tabs is not None:
            self.held_tabs.abort()
            self.held_tabs = None
        super().close()


def connected_import_does_not_persist_or_forward_google_token(browser):
    fixture = SheetFixture(browser)
    try:
        page = fixture.page
        page.locator("#sheetUrl").fill(SHEET)
        fixture.connect()
        expect(page.locator("#sheetAccessStatus")).to_contain_text("Google Sheets connected")
        expect(page.locator("#sheetTab option")).to_have_count(1)
        expect(page.locator("#sheetSource")).to_contain_text("Offline workbook")
        assert len(fixture.tabs_requests()) == 1, "Connecting did not automatically load the URL"
        client = page.evaluate("() => { const c=window.__offlineOauth.clients[0]; return {client_id:c.client_id,scope:c.scope,include_granted_scopes:c.include_granted_scopes}; }")
        assert client == {"client_id": "offline.apps.googleusercontent.com", "scope": SCOPE,
                          "include_granted_scopes": False}
        assert page.evaluate("window.__offlineOauth.requests[0].prompt") == "select_account"
        assert page.evaluate("window.__offlineOauth.checkedScope") == SCOPE
        request = fixture.tabs_requests()[0]
        assert request["headers"].get("x-google-sheets-token") == GOOGLE_ACCESS
        assert request["headers"].get("authorization") == "Bearer " + VALID
        assert SHEET in request["body"]
        page.locator("#preview").click()
        expect(page.locator("#copies tr")).to_have_count(1)
        page.locator("#admin").fill("admin@pw.live")
        page.locator("#evaluator").fill("teacher@pw.live")
        page.locator("#studentClass").fill("Class 10")
        page.locator("#subject").fill("Maths")
        for target in ("paper", "key"):
            page.locator(f"#{target}").set_input_files({"name": f"{target}.pdf", "mimeType": "application/pdf",
                                                        "buffer": b"%PDF-offline-placeholder"})
        page.locator("#start").click()
        expect(page.locator("#message")).to_contain_text("Workflow started")
        preview = next(request for request in fixture.requests if request["path"].endswith("/preview"))
        assert "import_id" in preview["body"] and "worksheet" in preview["body"]
        assert "sheet_url" not in preview["body"] and SHEET not in preview["body"]
        assert preview["headers"].get("x-google-sheets-token") == GOOGLE_ACCESS
        start = next(request for request in fixture.requests if request["path"].endswith("/start"))
        assert start["headers"].get("x-google-sheets-token") == GOOGLE_ACCESS
        assert "import_id" in start["body"] and "worksheet" in start["body"]
        connection = next(request for request in fixture.requests if request["path"].endswith("/sheets/connect"))
        assert connection["headers"].get("x-google-sheets-token") == GOOGLE_ACCESS
        assert json.loads(connection["body"]) == {"expires_in": 3570}
        for request in fixture.requests:
            if not request["path"].endswith(("/sheets/connect", "/sheet-tabs", "/preview", "/start")):
                assert "x-google-sheets-token" not in request["headers"], request["path"]
            assert GOOGLE_ACCESS not in request["body"], request["path"]
        persisted = page.evaluate("JSON.stringify({local: {...localStorage},session: {...sessionStorage}})")
        assert GOOGLE_ACCESS not in persisted
        assert page.evaluate("window.WorkflowSheets.getToken()") == GOOGLE_ACCESS
        page.screenshot(path=str(OUTPUT / "connected-sheet.png"), full_page=True)
        page.locator("#signOut").click()
        fixture.locked()
        assert page.evaluate("window.WorkflowSheets.getToken()") == ""
        expect(page.locator("#copies tr")).to_have_count(0)
        print("PASS: writable Sheets OAuth, memory-only token registration, automatic tab load, source-bound start, no persistent access token")
    finally:
        fixture.close()


def uploaded_workbook_never_sends_google_token(browser):
    fixture = SheetFixture(browser)
    try:
        fixture.connect()
        expect(fixture.page.locator("#sheetAccessStatus")).to_contain_text("Google Sheets connected")
        fixture.page.locator("#sheetFile").set_input_files({"name": "copies.csv", "mimeType": "text/csv",
                                                          "buffer": b"submission_id,student_id\ncopy,student\n"})
        fixture.page.locator("#loadTabs").click()
        expect(fixture.page.locator("#sheetSource")).to_contain_text("Offline workbook")
        assert "x-google-sheets-token" not in fixture.tabs_requests()[0]["headers"]
        fixture.page.locator("#preview").click()
        expect(fixture.page.locator("#copies tr")).to_have_count(1)
        preview = next(request for request in fixture.requests if request["path"].endswith("/preview"))
        assert "x-google-sheets-token" not in preview["headers"]
        fixture.page.locator("#sheetUrl").fill(SHEET)
        assert fixture.page.locator("#sheetFile").evaluate("node => node.files.length") == 0
        expect(fixture.page.locator("#copies tr")).to_have_count(0)
        fixture.page.locator("#loadTabs").click()
        expect(fixture.page.locator("#sheetSource")).to_contain_text("Offline workbook")
        assert fixture.tabs_requests()[-1]["headers"].get("x-google-sheets-token") == GOOGLE_ACCESS
        fixture.page.locator("#sheetFile").set_input_files({"name": "replacement.csv", "mimeType": "text/csv",
                                                          "buffer": b"submission_id,student_id\ncopy,student\n"})
        expect(fixture.page.locator("#sheetUrl")).to_have_value("")
        fixture.page.locator("#loadTabs").click()
        expect(fixture.page.locator("#sheetSource")).to_contain_text("Offline workbook")
        assert "x-google-sheets-token" not in fixture.tabs_requests()[-1]["headers"]
        print("PASS: uploaded workbook never sends Google access token; changing source imports the latest URL or file")
    finally:
        fixture.close()


def terminal_statuses_stay_unselected_on_rerun(browser):
    fixture = SheetFixture(browser)
    try:
        page = fixture.page
        examples = [{**ROW, "submission_id": f"status_{number}", "workflow_status": value}
                    for number, value in enumerate(("uploaded", "rejected", "not_downloadable_file", "error occurred", "not login", ""))]
        page.evaluate("examples => { rows = examples; const option = document.createElement('option'); option.value = testKey(rows[0]); document.getElementById('test').replaceChildren(option); renderCopies(); }", examples)
        expect(page.locator("#copies tr")).to_have_count(6)
        expect(page.locator("#copies input:disabled")).to_have_count(3)
        expect(page.locator("#copies input:checked")).to_have_count(3)
        expect(page.locator("#count")).to_contain_text("3 completed copies skipped")
        page.locator("#selectAll").uncheck()
        expect(page.locator("#copies input:checked")).to_have_count(0)
        page.locator("#selectAll").check()
        expect(page.locator("#copies input:checked")).to_have_count(3)
        expect(page.locator("#copies input:disabled:checked")).to_have_count(0)
        print("PASS: terminal status rows cannot be selected; errors, login failures and pending rows remain retryable")
    finally:
        fixture.close()


def declined_or_closed_google_access_is_visible_near_tabs(browser):
    for mode, granted, expected in (("denied", True, "declined"), ("success", False, "permission was not granted"),
                                    ("popup_closed", True, "window closed"),
                                    ("popup_failed_to_open", True, "Allow the Google sign-in popup")):
        fixture = SheetFixture(browser)
        try:
            fixture.configure(mode=mode, granted=granted)
            fixture.page.locator("#sheetUrl").fill(SHEET)
            fixture.connect()
            expect(fixture.page.locator("#sheetAccessStatus")).to_contain_text(expected)
            expect(fixture.page.locator("#sheetStatus")).to_contain_text(expected)
            assert fixture.page.evaluate("window.WorkflowSheets.getToken()") == ""
            assert fixture.page.evaluate("window.WorkflowAuth.getToken()") == VALID
            assert not fixture.tabs_requests()
        finally:
            fixture.close()
    print("PASS: declined scope, missing grant and popup errors are visible beside tab controls; PW session stays signed in")


def sign_out_discards_pending_google_grant(browser):
    fixture = SheetFixture(browser)
    try:
        page = fixture.page
        page.locator("#sheetUrl").fill(SHEET)
        fixture.configure(mode="deferred")
        fixture.connect()
        page.wait_for_function("window.__offlineOauth.requests.length === 1")
        page.locator("#signOut").click()
        fixture.locked()
        fixture.configure(mode="success")
        page.evaluate("window.__offlineOauth.deliver()")
        page.wait_for_timeout(100)
        assert page.evaluate("window.WorkflowSheets.getToken()") == ""
        assert not fixture.tabs_requests(), "Late Google grant loaded a sheet after sign-out"
        expect(page.locator("#sheetSource")).to_have_text("")
        expect(page.locator("#sheetAccessStatus")).to_contain_text("edit the student sheet")
        fixture.sign_in()
        fixture.signed_in()
        assert page.evaluate("window.WorkflowSheets.getToken()") == ""
        print("PASS: sign-out clears Sheets access; late grant cannot restore it or load a sheet")
    finally:
        fixture.close()


def expired_google_token_requires_only_sheets_reconnection(browser):
    fixture = SheetFixture(browser)
    try:
        page = fixture.page
        fixture.configure(expires=60)
        fixture.connect()
        expect(page.locator("#sheetAccessStatus")).to_contain_text("Google Sheets connected")
        page.evaluate("() => { const now = Date.now(); Date.now = () => now + 31000; }")
        assert page.evaluate("window.WorkflowSheets.getToken()") == ""
        expect(page.locator("#sheetAccessStatus")).to_contain_text("connection expired")
        assert page.evaluate("window.WorkflowAuth.getToken()") == VALID
        expect(page.locator("#signOut")).to_be_visible()
        fixture.connect()
        expect(page.locator("#sheetAccessStatus")).to_contain_text("Google Sheets connected")
        assert page.evaluate("window.WorkflowSheets.getToken()") == GOOGLE_ACCESS
        assert len(fixture.auth_requests) == 1, "Sheets expiration reauthenticated the PW session"
        print("PASS: expired Sheets access reconnects without signing out of the PW workflow")
    finally:
        fixture.close()


def changed_source_discards_inflight_tabs(browser):
    fixture = SheetFixture(browser)
    try:
        page = fixture.page
        page.locator("#sheetUrl").fill(SHEET)
        fixture.hold_tabs = True
        fixture.connect()
        fixture.wait_held()
        page.locator("#sheetUrl").fill(SHEET.replace("offline-access", "another-sheet"))
        with page.expect_response(lambda response: response.url.endswith("/api/workflow/sheet-tabs")):
            fixture.json(fixture.held_tabs, {"import_id": "stale", "tabs": [{"name": "Wrong stale tab"}],
                                             "source_name": "Old sheet"})
        fixture.held_tabs = None
        expect(page.locator("#sheetTab option")).to_have_count(1)
        assert page.locator("#sheetTab").input_value() == ""
        expect(page.locator("#sheetTab")).not_to_contain_text("Wrong stale tab")
        expect(page.locator("#sheetSource")).to_have_text("")
        expect(page.locator("#copies tr")).to_have_count(0)
        expect(page.locator("#loadTabs")).to_be_enabled()
        print("PASS: source URL changes discard a previously requested tab response")
    finally:
        fixture.close()


def changed_source_discards_inflight_error(browser):
    fixture = SheetFixture(browser)
    try:
        page = fixture.page
        page.locator("#sheetUrl").fill(SHEET)
        fixture.hold_tabs = True
        fixture.connect()
        fixture.wait_held()
        page.locator("#sheetUrl").fill(SHEET.replace("offline-access", "another-sheet"))
        with page.expect_response(lambda response: response.url.endswith("/api/workflow/sheet-tabs")):
            fixture.json(fixture.held_tabs, {"detail": "Old sheet permission failed"}, status=403)
        fixture.held_tabs = None
        page.wait_for_timeout(100)
        expect(page.locator("#sheetStatus")).to_have_text("")
        expect(page.locator("#message")).not_to_contain_text("Old sheet permission failed")
        assert page.evaluate("window.WorkflowAuth.getToken()") == VALID
        expect(page.locator("#loadTabs")).to_be_enabled()
        print("PASS: source URL changes suppress old tab-loading errors")
    finally:
        fixture.close()


def main():
    OUTPUT.mkdir(exist_ok=True)
    os.environ["TEMP"] = str(OUTPUT)
    os.environ["TMP"] = str(OUTPUT)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            for scenario in (connected_import_does_not_persist_or_forward_google_token,
                             uploaded_workbook_never_sends_google_token,
                             terminal_statuses_stay_unselected_on_rerun,
                             declined_or_closed_google_access_is_visible_near_tabs,
                             sign_out_discards_pending_google_grant,
                             expired_google_token_requires_only_sheets_reconnection,
                             changed_source_discards_inflight_tabs,
                             changed_source_discards_inflight_error):
                scenario(browser)
        finally:
            browser.close()
    print("Workflow Sheets access smoke passed. Every browser request used an offline fixture.")


if __name__ == "__main__":
    main()
