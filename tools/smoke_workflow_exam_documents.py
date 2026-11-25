"""Offline browser smoke test of "Fetch from portal" on the workflow page.

The portal fetch endpoints are fixtures: queued, then waiting for the evaluator
login, then ready with a question paper and a merged Model Answer. Checks that
the page shows the progress, drops the file requirements for fetched documents,
starts the run with the fetch id and no file parts, lets the operator fall back
to uploads, and cancels an unfinished fetch when the test changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect
from smoke_workflow_auth import GIS_FIXTURE, VALID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-exam-documents"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)

ROW = {"submission_id": "copy123", "student_id": "student123", "student_name": "Example Student",
       "program_name": "Complete Programe", "test_series_name": "Class 10 — Batch Tests", "test_id": "test123",
       "test_name": "Subjective Maths Test", "copy_url": "https://example.cloudfront.net/copy.pdf", "portal_status": "Submitted", "total_marks": "40"}
RUN = {"id": "a" * 32, "created_at": "2026-10-06T12:00:00", "status": "complete",
       "options": {"admin_email": "admin@example.com", "evaluator_email": "teacher@example.com"},
       "rows": [{**ROW, "stage": "uploaded", "error": "", "awarded_marks": 32}]}
DOCUMENTS = {"question_paper": {"filename": "QP_Maths.pdf", "pages": 3, "files": 1, "sources": ["QP_Maths.pdf"]},
             "answer_key": {"filename": "model-answer-merged.pdf", "pages": 5, "files": 2, "sources": ["one.pdf", "two.pdf"]}}
state = {"fetches": [], "polls": 0, "cancels": [], "started": [], "stuck": False, "unexpected": []}


def entry(fetch_id, status, detail="", documents=None):
    return {"fetch_id": fetch_id, "status": status, "detail": detail, "error": "", "test": {"test_id": "test123"},
            "documents": documents or {}}


def handle(route):
    parsed = urlparse(route.request.url)
    path = parsed.path
    if parsed.hostname == "accounts.google.com" and path == "/gsi/client":
        route.fulfill(content_type="text/javascript", body=GIS_FIXTURE)
        return
    if parsed.scheme in ("chrome-extension", "chrome", "blob"):
        route.continue_()  # Chrome's own PDF viewer loading inside the preview frame; not network
        return
    if parsed.hostname != "workflow.test":
        state["unexpected"].append(route.request.url)
        route.abort()
        return
    if path == "/api/config":
        data = {"google_client_id": "offline.apps.googleusercontent.com"}
    elif path == "/api/auth/status":
        data = {"allowed": True}
    elif path.startswith("/api/workflow"):
        assert route.request.headers.get("authorization") == "Bearer " + VALID
        if path.endswith("/sheet-tabs"):
            data = {"import_id": "b" * 32, "tabs": [{"name": "October copies"}, {"name": "Summary"}], "source_name": "Offline workbook"}
        elif path.endswith("/preview"):
            data = {"rows": [ROW]}
        elif path == "/api/workflow/exam-documents":
            body = json.loads(route.request.post_data)
            assert body["test_id"] == "test123" and body["test_name"] == ROW["test_name"], body
            fetch_id = f"{len(state['fetches']) + 1:032d}"
            state["fetches"].append(fetch_id); state["polls"] = 0
            data = entry(fetch_id, "queued")
        elif path.endswith("/cancel"):
            state["cancels"].append(path.split("/")[-2])
            data = {"status": "stopping"}
        elif path.endswith("/question_paper") or path.endswith("/answer_key"):
            state.setdefault("documents", []).append(path)
            route.fulfill(content_type="application/pdf", body=b"%PDF-1.4 fixture document")
            return
        elif path.startswith("/api/workflow/exam-documents/"):
            state["polls"] += 1
            fetch_id = path.rsplit("/", 1)[1]
            if state["stuck"] or state["polls"] == 1:
                data = entry(fetch_id, "waiting_login", "Log in to the evaluator account in the Chrome window that opened.")
            else:
                data = entry(fetch_id, "ready", "Fetched from the portal", DOCUMENTS)
        elif path.endswith("/start"):
            state["started"].append(route.request.post_data)
            data = RUN
        elif path.endswith("/runs"):
            data = {"runs": [RUN]} if state["started"] else {"runs": []}
        else:
            data = RUN
    else:
        file = ROOT / "static" / path.lstrip("/")
        if not file.is_file():
            route.fulfill(status=404)
            return
        mime = "text/javascript" if file.suffix == ".js" else "text/css" if file.suffix == ".css" else "text/html"
        route.fulfill(content_type=mime, body=file.read_bytes())
        return
    route.fulfill(content_type="application/json", body=json.dumps(data))


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", handle)
    # The init script also runs inside Chrome's PDF-viewer frame, where sessionStorage is null.
    page.add_init_script("try { sessionStorage.setItem('pwGoogleToken', " + json.dumps(VALID) + ") } catch (error) {}")
    page.goto("http://workflow.test/workflow.html")
    expect(page.locator("#authStatus")).to_contain_text("Signed in as teacher@pw.live")
    page.locator("#fetchExamDocuments").click()
    expect(page.locator("#message")).to_contain_text("select a test first")
    assert state["fetches"] == []
    page.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/example/edit?gid=0")
    page.locator("#loadTabs").click()
    page.locator("#sheetTab").select_option("October copies")
    page.locator("#preview").click()
    page.locator("#copies input").wait_for()
    page.locator("#evaluator").fill("teacher@example.com")   # the fetch names the evaluator whose window reads the portal
    assert page.locator("#paper").evaluate("node => node.required") and page.locator("#key").evaluate("node => node.required")

    page.locator("#fetchExamDocuments").click()
    expect(page.locator("#examDocumentsStatus")).to_contain_text("Log in to the evaluator account")
    expect(page.locator("#fetchExamDocuments")).to_be_disabled()
    expect(page.locator("#examDocumentsStatus")).to_contain_text("Fetched from the portal: question paper QP_Maths.pdf (3 pages) · Model Answer 2 files merged, 5 pages", timeout=15000)
    expect(page.locator("#clearExamDocuments")).to_be_visible()
    expect(page.locator("#fetchExamDocuments")).to_be_enabled()
    assert not page.locator("#paper").evaluate("node => node.required") and not page.locator("#key").evaluate("node => node.required")
    assert state["fetches"] == ["1".zfill(32)] and state["cancels"] == []
    page.get_by_role("button", name="View question paper").click()
    expect(page.locator("#documentPreview")).to_be_visible()
    expect(page.locator("#documentPreviewTitle")).to_contain_text("QP_Maths.pdf", timeout=15000)
    assert page.locator("#documentFrame").get_attribute("src").startswith("blob:")
    page.get_by_role("button", name="View Model Answer").click()
    expect(page.locator("#documentPreviewTitle")).to_contain_text("one.pdf, two.pdf", timeout=15000)
    page.locator("#closeDocumentPreview").click()
    expect(page.locator("#documentPreview")).to_be_hidden()
    assert state["documents"] == ["/api/workflow/exam-documents/" + "1".zfill(32) + "/question_paper",
                                  "/api/workflow/exam-documents/" + "1".zfill(32) + "/answer_key"]

    page.locator("#admin").fill("admin@example.com")
    page.locator("#evaluator").fill("teacher@example.com")
    page.locator("#studentClass").fill("Class 10")
    page.locator("#subject").fill("Maths")
    page.locator("#start").click()
    page.locator("#progress button").wait_for()
    assert len(state["started"]) == 1
    body = state["started"][0]
    assert '"admin_email":"admin@example.com"' in body, body
    expect(page.locator("#runAccounts")).to_contain_text("Admin login ID: admin@example.com")
    expect(page.locator("#runAccounts")).to_contain_text("Evaluator login ID: teacher@example.com")
    assert 'name="exam_documents_id"' in body and "1".zfill(32) in body, body
    assert 'name="question_paper"' not in body and 'name="answer_key"' not in body, body

    expect(page.locator("#viewRunKey")).to_be_enabled()
    page.locator("#viewRunKey").click()
    expect(page.locator("#documentPreviewTitle")).to_contain_text("Answer key used by the selected run", timeout=15000)
    assert state["documents"][-1] == "/api/workflow/" + "a" * 32 + "/document/answer_key"
    page.locator("#closeDocumentPreview").click()
    page.locator("#clearExamDocuments").click()
    expect(page.locator("#examDocumentsStatus")).to_contain_text("Using uploaded files")
    expect(page.get_by_role("button", name="View question paper")).to_have_count(0)
    expect(page.locator("#clearExamDocuments")).to_be_hidden()
    assert page.locator("#paper").evaluate("node => node.required") and page.locator("#key").evaluate("node => node.required")
    page.locator("#start").click()
    expect(page.locator("#message")).to_contain_text("Upload the official question paper")
    assert len(state["started"]) == 1 and state["cancels"] == []

    # An unfinished fetch is cancelled when the operator falls back to uploads.
    state["stuck"] = True
    page.locator("#fetchExamDocuments").click()
    expect(page.locator("#examDocumentsStatus")).to_contain_text("Log in to the evaluator account")
    page.locator("#clearExamDocuments").wait_for(state="hidden")
    page.locator("#sheetTab").select_option("Summary")
    expect(page.locator("#examDocumentsStatus")).to_have_text("")
    expect(page.locator("#fetchExamDocuments")).to_be_enabled()
    page.wait_for_function("() => true")
    deadline = 50
    while state["cancels"] != ["2".zfill(32)] and deadline:
        page.wait_for_timeout(100); deadline -= 1
    assert state["cancels"] == ["2".zfill(32)], state["cancels"]
    assert not errors, errors
    assert not state["unexpected"], state["unexpected"]
    page.screenshot(path=str(OUTPUT / "exam-documents.png"), full_page=True)
    browser.close()
print("Workflow exam-documents smoke passed: fetch progress, fetched documents replace the file requirements, PDF previews of fetched and run documents, start sends the fetch id without file parts, fallback to uploads, cancel on test change. All requests were offline fixtures.")
