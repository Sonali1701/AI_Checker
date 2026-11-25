"""Connected Chrome/frontend/FastAPI smoke test with external service fixtures.

The browser uses the real local workflow API, snapshot cache and durable store.
Google identity, Google Sheets, remote PDF bytes, AI grading and the portal are
clearly labelled fixtures. No Google account, real student or portal is contacted.
"""
from __future__ import annotations

import base64
import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
import re
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-connected"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)
sys.path.insert(0, str(ROOT))

import fitz
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import requests
import uvicorn

import integrated_workflow as workflow
from openpyxl.utils import column_index_from_string


SHEET_ID = "18rfe3nLcq0FW7CoKq5yww2dJgMZlCv43utM7Vyudd5I"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?pli=1&gid=711687345#gid=711687345"
ACCESS_TOKEN = "connected-smoke-sheets-access-fixture"


def credential():
    payload = {"sub": "connected-smoke-test", "email": "connected.fixture@pw.live", "exp": int(time.time()) + 3600}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"fixture.{encoded}.not-a-real-signature"


TOKEN = credential()


def copy_row(name):
    return {"submission_id": name, "student_id": f"student_{name}", "student_name": "Connected fixture student",
            "program_name": "Fixture program", "test_series_name": "Fixture batch tests", "test_id": "fixture_test",
            "test_name": "Fixture Maths Test", "copy_url": f"https://fixture.cloudfront.net/{name}.pdf",
            "portal_status": "Submitted", "total_marks": "10"}


FIRST = copy_row("first_tab_copy")
SELECTED = copy_row("selected_tab_copy")
UNSELECTED = copy_row("unselected_tab_copy")
FILE_ROW = copy_row("file_import_copy")


def sheet_grid(title, sheet_id, rows):
    cells = [list(rows[0])] + [list(row.values()) for row in rows]
    return {"properties": {"sheetId": sheet_id, "title": title}, "data": [{"startRow": 0, "startColumn": 0,
            "rowData": [{"values": [{"formattedValue": str(value), "effectiveValue": {"stringValue": str(value)}}
                                   for value in values]} for values in cells]}]}


SHEET_METADATA = {"properties": {"title": "Large connected smoke fixture workbook"}, "sheets": [
    {"properties": {"title": "Huge irrelevant fixture tab", "sheetId": 0,
                    "gridProperties": {"rowCount": 900000, "columnCount": 10}}},
    {"properties": {"title": "Selected fixture tab", "sheetId": 711687345,
                    "gridProperties": {"rowCount": 1000000, "columnCount": len(SELECTED)}}},
]}
# Sparse fixture positions force a second bounded row batch. The unrelated tab
# has no cell fixture: any request for it fails instead of silently returning data.
SELECTED_GRID_ROWS = {0: list(SELECTED), 1: list(SELECTED.values()), 501: list(UNSELECTED.values())}


def readable_pdf(label):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), label)
        page.insert_text((40, 85), "TEST FIXTURE ONLY - no real student grading or publication")
        return document.tobytes()


SOURCE_PDF = readable_pdf("SOURCE COPY: connected browser/backend fixture")
CHECKED_PDF = readable_pdf("CHECKED COPY: SIMULATED AI OUTPUT")
PAPER_PDF = readable_pdf("QUESTION PAPER: fixture only")
KEY_PDF = readable_pdf("MARKING KEY: fixture only")
REPORT = {"questions": [{"qid": "Q1", "score": 7}], "total_score": 7,
          "overall_remarks": "SIMULATED TEST RESULT - no real student assessment"}


class RemoteResponse:
    def __init__(self, data=b"", status=200):
        self.content, self.status_code = data, status
        self.headers = {"Content-Type": "application/json" if data.startswith(b"{") else "application/pdf"}
        self.is_redirect = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"Fixture HTTP {self.status_code}")


EVENTS = []
PORTAL_WINDOWS = []
REMOTE_CALLS = []
API_CALLS = []


def fixture_remote_get(url, **kwargs):
    parsed = urlparse(url)
    REMOTE_CALLS.append({"host": parsed.hostname, "path": parsed.path,
                         "params": kwargs.get("params", {})})
    assert kwargs.get("allow_redirects") is False, "Remote redirects must remain inspected"
    if parsed.hostname == "sheets.googleapis.com":
        assert kwargs.get("headers", {}).get("Authorization") == "Bearer " + ACCESS_TOKEN
        params = kwargs["params"]
        if parsed.path == f"/v4/spreadsheets/{SHEET_ID}/values:batchGet":
            if params["ranges"] == ["'Selected fixture tab'!1:1"]:
                return RemoteResponse(json.dumps({"valueRanges": [{"values": [SELECTED_GRID_ROWS[0]]}]}).encode())
            assert params["valueRenderOption"] == "UNFORMATTED_VALUE"
            assert params["majorDimension"] == "ROWS"
            assert params["fields"] == "valueRanges(values)"
            value_ranges = []
            for requested in params["ranges"]:
                match = re.fullmatch(r"'Selected fixture tab'!([A-Z]+)2:([A-Z]+)1000000", requested)
                assert match, f"Wrong tab or unintended column extent: {requested}"
                assert match[1] == match[2]
                column = column_index_from_string(match[1]) - 1
                assert column < len(SELECTED_GRID_ROWS[0])
                values = [[SELECTED_GRID_ROWS[number][column]] if number in SELECTED_GRID_ROWS and column < len(SELECTED_GRID_ROWS[number]) else []
                          for number in range(1, max(SELECTED_GRID_ROWS) + 1)]
                while values and not values[-1]:
                    values.pop()
                value_ranges.append({"values": values})
            return RemoteResponse(json.dumps({"valueRanges": value_ranges}).encode())
        assert parsed.path == f"/v4/spreadsheets/{SHEET_ID}"
        if "ranges" not in params:
            assert params.get("includeGridData") != "true", "Tab listing downloaded the whole workbook"
            assert "rowData" not in params.get("fields", ""), "Tab listing requested student cells"
            return RemoteResponse(json.dumps(SHEET_METADATA).encode())
        assert params["includeGridData"] == "true"
        grids = []
        for requested in params["ranges"]:
            match = re.fullmatch(r"'Selected fixture tab'!([A-Z]+)(\d+):([A-Z]+)(\d+)", requested)
            assert match, f"Wrong tab or unbounded range requested: {requested}"
            start_column, start_row, end_column, end_row = match.groups()
            first_row, last_row = int(start_row) - 1, int(end_row)
            first_col, last_col = column_index_from_string(start_column) - 1, column_index_from_string(end_column)
            assert last_row - first_row <= 500, "Row batch exceeded bounded import size"
            assert first_col < last_col <= len(SELECTED_GRID_ROWS[0])
            rows = []
            for row_index in range(first_row, last_row):
                source = SELECTED_GRID_ROWS.get(row_index, [""] * len(SELECTED))
                rows.append({"values": [{"formattedValue": str(value), "effectiveValue": {"stringValue": str(value)}}
                                        for value in source[first_col:last_col]]})
            grids.append({"startRow": first_row, "startColumn": first_col, "rowData": rows})
        selected_properties = {"title": "Selected fixture tab", "sheetId": 711687345}
        return RemoteResponse(json.dumps({"sheets": [{"properties": selected_properties, "data": grids}]}).encode())
    if parsed.hostname == "docs.google.com":
        # Reproduce the user's private-sheet anonymous-export failure.
        return RemoteResponse(b"<html>Fixture private sheet requires sign-in</html>", 401)
    if parsed.hostname == "fixture.cloudfront.net":
        assert parsed.path == "/selected_tab_copy.pdf", "Wrong tab or unselected copy downloaded"
        return RemoteResponse(SOURCE_PDF)
    raise AssertionError(f"Unexpected external request: {parsed.hostname}")


def fixture_remote_post(url, **kwargs):
    parsed = urlparse(url)
    assert parsed.hostname == "sheets.googleapis.com", f"Unexpected external write: {parsed.hostname}"
    assert kwargs.get("headers", {}).get("Authorization") == "Bearer " + ACCESS_TOKEN
    assert kwargs.get("allow_redirects") is False
    body = kwargs["json"]
    REMOTE_CALLS.append({"host": parsed.hostname, "path": parsed.path, "params": {}, "method": "POST", "body": body})
    if parsed.path == f"/v4/spreadsheets/{SHEET_ID}:batchUpdate":
        assert body["requests"] == [{"appendDimension": {"sheetId": 711687345, "dimension": "COLUMNS", "length": 1}}]
        SHEET_METADATA["sheets"][1]["properties"]["gridProperties"]["columnCount"] += 1
    else:
        assert parsed.path == f"/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
        assert body["valueInputOption"] == "RAW"
        for item in body["data"]:
            match = re.fullmatch(r"'Selected fixture tab'!([A-Z]+)(\d+)", item["range"])
            assert match, f"Wrong worksheet written: {item['range']}"
            column, row = column_index_from_string(match[1]) - 1, int(match[2]) - 1
            assert row in (0, 1), "Unselected student was updated"
            assert column == len(SELECTED), "Existing student data was overwritten"
            values = SELECTED_GRID_ROWS[row]
            while len(values) <= column:
                values.append("")
            values[column] = item["values"][0][0]
    return RemoteResponse(b"{}")


class FixturePortal:
    def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
        self.root, self.evaluator, self.admin = root, evaluator, admin
        PORTAL_WINDOWS.append({"evaluator": evaluator, "evaluator_port": evaluator_port,
                               "admin": admin, "admin_port": admin_port})

    def perform(self, stage, row, options, pdf=None, report=None, reason="", before_submit=None):
        assert row["submission_id"] == SELECTED["submission_id"]
        assert row["student_id"] == SELECTED["student_id"]
        assert options["evaluator_email"] == "fixture.evaluator@example.com"
        assert options["admin_email"] == "fixture.admin@example.com"
        assert (self.admin, self.evaluator) == (("fixture.admin@example.com", "") if stage == "assign"
                                               else ("", "fixture.evaluator@example.com"))
        if stage == "upload":
            assert pdf.read_bytes() == CHECKED_PDF
            assert report == REPORT
            assert workflow.portal_marks(report) == {1: 7}
        if before_submit is not None:
            before_submit()  # the engine clicks Submit right after this marker
        EVENTS.append(stage)

    def close(self):
        pass  # one Chrome window per account is closed at the end of a run


def fixture_grade(folder, source, options, authorization):
    assert authorization == "Bearer " + TOKEN
    assert source.read_bytes() == SOURCE_PDF
    assert (folder / "question_paper.pdf").read_bytes() == PAPER_PDF
    assert (folder / "answer_key.pdf").read_bytes() == KEY_PDF
    EVENTS.append("grade")
    return {"release": True, "pdf": CHECKED_PDF, "report": REPORT}


def validated_fixture_identity(authorization):
    if authorization != "Bearer " + TOKEN:
        raise HTTPException(401, "Connected smoke fixture sign-in required")
    return TOKEN


GIS_FIXTURE = """window.__fixtureGoogle = {};
window.google = {accounts: {id: {
  initialize(options) { window.__fixtureGoogle.identityOptions = options; },
  renderButton(container) {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = 'Sign in with Google (test fixture)';
    button.onclick = () => window.__fixtureGoogle.identityOptions.callback({credential: window.__fixtureIdentity});
    container.replaceChildren(button);
  },
  disableAutoSelect() {}
}, oauth2: {
  initTokenClient(options) {
    window.__fixtureGoogle.oauthOptions = options;
    return {requestAccessToken(request) {
      window.__fixtureGoogle.oauthRequest = request;
      window.__fixtureGoogle.oauthCount = (window.__fixtureGoogle.oauthCount || 0) + 1;
      if (window.__fixtureOAuthMode === 'held') return;
      setTimeout(() => {
        if (window.__fixtureOAuthMode === 'closed') options.error_callback({type: 'popup_closed'});
        else if (window.__fixtureOAuthMode === 'denied') options.callback({error: 'access_denied'});
        else options.callback({access_token: 'connected-smoke-sheets-access-fixture', expires_in: 3600,
          scope: window.__fixtureOAuthMode === 'scope' ? '' : 'https://www.googleapis.com/auth/spreadsheets',
          token_type: 'Bearer'});
      }, 0);
    }};
  },
  hasGrantedAllScopes(response, ...scopes) { return scopes.every(scope => response.scope.split(' ').includes(scope)); }
}}};"""


def build_app(run_root):
    app = FastAPI()

    @app.middleware("http")
    async def record_request(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            if request.url.path.startswith("/api/workflow"):
                API_CALLS.append({"path": request.url.path, "method": request.method, "status": 500,
                                  "sheets_token_present": bool(request.headers.get("X-Google-Sheets-Token"))})
            raise
        if request.url.path.startswith("/api/workflow"):
            API_CALLS.append({"path": request.url.path, "method": request.method, "status": response.status_code,
                              "sheets_token_present": bool(request.headers.get("X-Google-Sheets-Token"))})
        return response

    @app.get("/api/config")
    def config():
        return {"google_client_id": "connected-smoke-fixture.apps.googleusercontent.com", "build_id": "connected-smoke-fixture"}

    @app.get("/api/auth/status")
    def auth_status(authorization: str | None = Header(None)):
        validated_fixture_identity(authorization)
        return {"allowed": True}

    workflow.ALERT_CENTER_FACTORY = workflow.QuietAlerts   # never sound the real alarm from a smoke
    store = workflow.install(app, validated_fixture_identity, fixture_grade, run_root)
    store.portal_factory = FixturePortal
    production_downloader = workflow.download_pdf

    def download(url, target):
        EVENTS.append("download")
        production_downloader(url, target)

    store.downloader = download
    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
    return app, store


def csv_bytes(row):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    return output.getvalue().encode()


def browser_run(origin, store):
    browser_errors, unexpected_browser_requests = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1050}, accept_downloads=True)

        def route_browser(route):
            parsed = urlparse(route.request.url)
            if parsed.hostname == "accounts.google.com" and parsed.path == "/gsi/client":
                route.fulfill(content_type="text/javascript", body=GIS_FIXTURE)
            elif parsed.hostname == "127.0.0.1":
                route.continue_()  # Real local frontend and workflow APIs.
            else:
                unexpected_browser_requests.append(parsed.hostname)
                route.abort()

        context.route("**/*", route_browser)
        context.add_init_script("sessionStorage.setItem('pwGoogleToken', " + json.dumps(TOKEN) + ");")
        page = context.new_page()
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.goto(origin + "/workflow.html", wait_until="domcontentloaded")
        expect(page.locator("#authStatus")).to_contain_text("Signed in as connected.fixture@pw.live")
        page.locator("#sheetUrl").fill(SHEET_URL)

        # Private-sheet access failure must be visible in the workflow itself.
        page.locator("#loadTabs").click()
        page.wait_for_function("() => /connect|sign.in|permission|access/i.test(document.getElementById('sheetStatus')?.textContent || '') || /connect|sign.in|permission|access/i.test(document.getElementById('message').textContent)")
        expect(page.locator("#copies tr")).to_have_count(0)

        # Cancellation and refused scope must remain visible without stale data.
        for mode, explanation in (("closed", "window closed"), ("denied", "access was declined"), ("scope", "permission was not granted")):
            page.evaluate("mode => window.__fixtureOAuthMode = mode", mode)
            page.locator("#connectSheets").click()
            expect(page.locator("#sheetAccessStatus")).to_contain_text(explanation)
            assert page.evaluate("window.WorkflowSheets.getToken()") == ""
            expect(page.locator("#copies tr")).to_have_count(0)

        # The Google chooser is a fixture; the resulting header goes to the actual backend.
        page.evaluate("window.__fixtureOAuthMode = 'success'")
        page.locator("#connectSheets").click()
        expect(page.locator("#sheetTab option")).to_have_count(3)
        expect(page.locator("#sheetTab")).to_contain_text("Huge irrelevant fixture tab")
        tab_discovery_calls = [call for call in REMOTE_CALLS if call["host"] == "sheets.googleapis.com"]
        assert tab_discovery_calls and all("ranges" not in call["params"] for call in tab_discovery_calls)
        assert page.evaluate("window.__fixtureGoogle.oauthCount") == 4
        assert page.evaluate("window.__fixtureGoogle.oauthOptions.scope") == "https://www.googleapis.com/auth/spreadsheets"
        page.locator("#sheetTab").select_option("Selected fixture tab")
        page.locator("#preview").click()
        expect(page.locator("#copies tr")).to_have_count(2)
        preview_calls = [call for call in API_CALLS if call["path"] == "/api/workflow/preview"]
        assert preview_calls[-1]["sheets_token_present"], "Selected Google tab needs read token"
        selected_ranges = [requested for call in REMOTE_CALLS for requested in call["params"].get("ranges", [])]
        assert any(re.search(r"!\w+502:\w+502$", requested) for requested in selected_ranges), "Second batch not exercised"
        grid_reads = [call for call in REMOTE_CALLS if call["params"].get("includeGridData") == "true"]
        assert len(grid_reads) == 3, "Allocated blank rows triggered excess grid reads"
        assert sum(call["path"].endswith("/values:batchGet") for call in REMOTE_CALLS) == 1
        expect(page.locator("#copies")).to_contain_text(SELECTED["submission_id"])
        assert FIRST["submission_id"] not in page.locator("#copies").inner_text()
        page.locator(f"#copies input[data-id='{UNSELECTED['submission_id']}']").uncheck()
        page.locator("#admin").fill("fixture.admin@example.com")
        page.locator("#evaluator").fill("fixture.evaluator@example.com")
        page.locator("#studentClass").fill("Class 10 fixture")
        page.locator("#subject").fill("Maths")
        page.locator("#deadline").fill((datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"))
        page.locator("#paper").set_input_files({"name": "fixture-paper.pdf", "mimeType": "application/pdf", "buffer": PAPER_PDF})
        page.locator("#key").set_input_files({"name": "fixture-key.pdf", "mimeType": "application/pdf", "buffer": KEY_PDF})
        page.locator("#start").click()
        expect(page.locator("#runStatus")).to_contain_text("complete", timeout=20000)
        expect(page.locator("#runStatus")).to_contain_text("1/1 uploaded")
        assert EVENTS == ["assign", "download", "grade", "upload"], EVENTS
        run_id = page.evaluate("sessionStorage.getItem('workflowTabRun')")
        expect(page.locator("#currentRunTitle")).to_contain_text("complete")
        expect(page.locator("#savedRuns tr")).to_have_count(1)
        expect(page.locator("#savedRuns")).to_contain_text("Shown here")
        assert any(alert["title"].startswith("Run complete") for alert in store.alerts.raised), store.alerts.raised
        run = store.read(run_id)
        assert run["status"] == "complete"
        assert run["options"]["admin_email"] == "fixture.admin@example.com"
        assert run["options"]["evaluator_email"] == "fixture.evaluator@example.com"
        expect(page.locator("#runAccounts")).to_contain_text("Admin login ID: fixture.admin@example.com")
        expect(page.locator("#runAccounts")).to_contain_text("Evaluator login ID: fixture.evaluator@example.com")
        assert len(PORTAL_WINDOWS) == 2, PORTAL_WINDOWS
        ports = [window["admin_port"] or window["evaluator_port"] for window in PORTAL_WINDOWS]
        assert len(set(ports)) == 2 and all(port > 9237 for port in ports), PORTAL_WINDOWS
        assert len(run["rows"]) == 1 and run["rows"][0]["submission_id"] == SELECTED["submission_id"]
        source, checked, report_path = store.copy_paths(run, SELECTED["submission_id"])
        assert run["rows"][0]["files_cleaned"] is True
        assert not source.exists() and not checked.exists()
        assert json.loads(report_path.read_text()) == REPORT
        assert ACCESS_TOKEN not in (store.folder(run_id) / "run.json").read_text()
        expect(page.get_by_role("button", name="Download checked PDF")).to_have_count(0)
        expect(page.locator("#progress")).to_contain_text("Local PDFs removed")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            synced = store.read(run_id)["rows"][0]
            if synced.get("sheet_synced_status") == "uploaded":
                break
            page.wait_for_timeout(100)
        assert synced.get("sheet_synced_status") == "uploaded", synced.get("sheet_sync_error")
        assert SELECTED_GRID_ROWS[0][-1] == "workflow_status"
        assert SELECTED_GRID_ROWS[1][-1] == "uploaded"
        assert len(SELECTED_GRID_ROWS[501]) == len(UNSELECTED), "Unselected student's status was written"
        page.locator("#admin").fill("new.admin@example.com")
        page.locator("#evaluator").fill("new.evaluator@example.com")
        page.locator("#resume").click()
        expect(page.locator("#message")).to_contain_text("Resuming with the saved portal accounts")
        expect(page.locator("#message")).to_contain_text("fixture.admin@example.com")
        expect(page.locator("#message")).to_contain_text("fixture.evaluator@example.com")
        page.locator("#refresh").click()
        expect(page.locator("#runStatus")).to_contain_text("complete")
        assert EVENTS == ["assign", "download", "grade", "upload"], "Resume repeated completed operations"
        page.screenshot(path=str(OUTPUT / "connected-workflow-complete.png"), full_page=True)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#runStatus")).to_contain_text("1/1 uploaded")
        expect(page.locator("#runAccounts")).to_contain_text("Admin login ID: fixture.admin@example.com")
        assert page.evaluate("sessionStorage.getItem('workflowTabRun')") == run_id   # this tab keeps its run after a reload

        # A fresh page has no Sheets access token; file imports still use the real API.
        page.locator("#sheetFile").set_input_files({"name": "fixture-copies.csv", "mimeType": "text/csv", "buffer": csv_bytes(FILE_ROW)})
        page.locator("#loadTabs").click()
        expect(page.locator("#sheetTab option")).to_have_count(1)
        expect(page.locator("#sheetTab")).to_have_value("CSV data")
        page.locator("#preview").click()
        expect(page.locator("#copies tr")).to_have_count(1)
        expect(page.locator("#copies")).to_contain_text(FILE_ROW["submission_id"])
        last_tabs_request = next(call for call in reversed(API_CALLS) if call["path"] == "/api/workflow/sheet-tabs")
        assert not last_tabs_request["sheets_token_present"]

        # A delayed Google callback after checker sign-out cannot restore sheet access.
        page.evaluate("window.__fixtureOAuthMode = 'held'")
        page.locator("#connectSheets").click()
        page.wait_for_function("() => window.__fixtureGoogle.oauthCount === 1")
        page.locator("#signOut").click()
        page.evaluate("window.__fixtureGoogle.oauthOptions.callback({access_token: 'connected-smoke-sheets-access-fixture', expires_in: 3600, scope: 'https://www.googleapis.com/auth/spreadsheets'})")
        assert page.evaluate("window.WorkflowSheets.getToken()") == ""
        assert page.evaluate("window.WorkflowAuth.getToken()") == ""
        expect(page.locator("#copies tr")).to_have_count(0)
        expect(page.locator("#progress tr")).to_have_count(0)
        assert not unexpected_browser_requests, unexpected_browser_requests
        assert not browser_errors, browser_errors
        assert all(call["status"] < 500 for call in API_CALLS), "Workflow API had an internal error even if a later poll recovered"
        context.close()
        browser.close()
    return run_id


def main():
    run_root = OUTPUT / ("runs-" + str(time.time_ns()))
    app, store = build_app(run_root)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2048)
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started, "Isolated fixture server did not start"
        with urlopen(origin + "/api/config", timeout=5) as response:
            assert response.status == 200
        with patch.object(workflow.requests, "get", fixture_remote_get), patch.object(workflow.requests, "post", fixture_remote_post):
            run_id = browser_run(origin, store)
        report = {"result": "passed", "test_scope": "Actual frontend, actual workflow API/cache/store; all external services are fixtures",
                  "real_components": ["Chrome frontend", "FastAPI workflow endpoints", "owner-bound SheetImportCache", "WorkflowStore",
                                      "PDF downloader validation", "persistent files", "selected-copy identity", "checked-copy API", "resume"],
                  "fixture_services": ["Google identity and Sheets OAuth chooser", "Google Sheets metadata and selected-tab bounded grid data", "remote source PDF bytes",
                                       "AI grader", "portal assignment and upload"],
                  "large_sheet_fixture": {"unrelated_tab_cells": 9000000, "selected_allocated_rows": 1000000,
                                          "selected_nonempty_copy_rows": [2, 502],
                                          "grid_requests": sum(call["params"].get("includeGridData") == "true" for call in REMOTE_CALLS),
                                          "extent_requests": sum(call["path"].endswith("/values:batchGet") for call in REMOTE_CALLS)},
                  "pipeline_events": EVENTS, "portal_windows": PORTAL_WINDOWS,
                  "source_pdf_sha256": hashlib.sha256(SOURCE_PDF).hexdigest(),
                  "run_id": run_id, "api_calls": API_CALLS, "remote_fixture_calls": REMOTE_CALLS,
                  "live_portal_or_google_requests": 0}
        (OUTPUT / "connected-workflow-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Connected workflow smoke passed: private-sheet access error, cancelled/denied/missing-scope OAuth, huge-workbook metadata-only tab list, million allocated rows with only occupied selected-tab windows fetched, selected-copy assign/download/grade/upload stage order, durable uploaded status in student sheet, confirmed-copy PDF cleanup, no-repeat resume, reload, CSV import, stale OAuth callback after sign-out. All external services were fixtures; no real Google or portal requests occurred.")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


if __name__ == "__main__":
    main()
