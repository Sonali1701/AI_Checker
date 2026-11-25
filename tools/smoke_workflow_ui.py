"""Offline browser smoke test of workflow selection, start and progress rendering."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect
from smoke_workflow_auth import GIS_FIXTURE, VALID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-ui"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)

ROW = {"submission_id": "copy123", "student_id": "student123", "student_name": "Example Student",
       "program_name": "Complete Programe", "test_series_name": "Class 10 — Batch Tests", "test_id": "test123",
       "test_name": "Subjective Maths Test", "copy_url": "https://example.cloudfront.net/copy.pdf", "portal_status": "Submitted", "total_marks": "40"}
RUN = {"id": "a" * 32, "created_at": "2026-10-06T12:00:00", "status": "complete",
       "options": {"admin_email": "admin@example.com", "evaluator_email": "teacher@example.com"},
       "rows": [{**ROW, "stage": "uploaded", "error": "", "awarded_marks": 32}]}
started = []
previewed = []
unexpected = []


def handle(route):
    parsed = urlparse(route.request.url)
    path = parsed.path
    if parsed.hostname == "accounts.google.com" and path == "/gsi/client":
        route.fulfill(content_type="text/javascript", body=GIS_FIXTURE)
        return
    if parsed.hostname != "workflow.test":
        unexpected.append(route.request.url)
        route.abort()
        return
    if path == "/api/config":
        route.fulfill(content_type="application/json", body=json.dumps({"google_client_id": "offline.apps.googleusercontent.com"}))
    elif path == "/api/auth/status":
        assert route.request.headers.get("authorization") == "Bearer " + VALID
        route.fulfill(content_type="application/json", body=json.dumps({"allowed": True}))
    elif path.startswith("/api/workflow"):
        assert route.request.headers.get("authorization") == "Bearer " + VALID
        if path.endswith("/sheet-tabs"):
            data = {"import_id": "b" * 32, "tabs": [{"name": "Summary"}, {"name": "October copies"}], "source_name": "Offline workbook"}
        elif path.endswith("/preview"):
            body = route.request.post_data
            assert "b" * 32 in body and "October copies" in body
            previewed.append(body)
            data = {"rows": [ROW]}
        elif path.endswith("/start"):
            started.append(route.request.post_data)
            data = RUN
        elif path.endswith("/runs"):
            data = {"runs": [RUN]} if started else {"runs": []}
        else:
            data = RUN
        route.fulfill(content_type="application/json", body=json.dumps(data))
    else:
        file = ROOT / "static" / path.lstrip("/")
        if not file.is_file():
            route.fulfill(status=404)
            return
        mime = "text/javascript" if file.suffix == ".js" else "text/css" if file.suffix == ".css" else "text/html"
        route.fulfill(content_type=mime, body=file.read_bytes())


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", handle)
    page.add_init_script("sessionStorage.setItem('pwGoogleToken', " + json.dumps(VALID) + ")")
    page.goto("http://workflow.test/workflow.html")
    expect(page.locator("#authStatus")).to_contain_text("Signed in as teacher@pw.live")
    page.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/example/edit?gid=0")
    page.locator("#loadTabs").click()
    expect(page.locator("#sheetTab option")).to_have_count(3)
    page.locator("#preview").click()
    expect(page.locator("#message")).to_contain_text("Select the tab")
    assert not previewed
    page.locator("#sheetTab").select_option("October copies")
    page.locator("#preview").click()
    page.locator("#copies input").wait_for()
    assert page.locator("#copies tr").count() == 1
    assert page.locator("#totalMarks").input_value() == "40"
    assert len(previewed) == 1
    page.locator("#sheetTab").select_option("Summary")
    expect(page.locator("#copies tr")).to_have_count(0)
    assert page.locator("#totalMarks").input_value() == ""
    page.locator("#sheetTab").select_option("October copies")
    page.locator("#preview").click()
    expect(page.locator("#copies tr")).to_have_count(1)
    page.locator("#selectAll").uncheck()
    page.locator("#start").click()
    expect(page.locator("#message")).to_contain_text("Select at least one copy")
    assert started == []
    page.locator("#selectAll").check()
    page.locator("#start").click()
    expect(page.locator("#message")).to_contain_text("Upload the official question paper")
    assert started == []
    page.locator("#evaluator").fill("teacher@example.com")
    page.locator("#studentClass").fill("Class 10")
    page.locator("#subject").fill("Maths")
    for target in ("paper", "key"):
        page.locator(f"#{target}").set_input_files({"name": f"{target}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-offline-placeholder"})
    page.locator("#start").click()
    expect(page.locator("#message")).to_contain_text("admin login email")
    assert started == []
    page.locator("#admin").fill("Admin@Example.com")
    expect(page.locator("#accountPairGuidance")).to_contain_text("Admin login ID: admin@example.com")
    expect(page.locator("#accountPairGuidance")).to_contain_text("Evaluator login ID: teacher@example.com")
    page.locator("#start").click()
    page.locator("#progress button").wait_for()
    assert len(started) == 1
    assert "copy123" in started[0] and "teacher@example.com" in started[0]
    assert '"admin_email":"admin@example.com"' in started[0], started[0]
    expect(page.locator("#runAccounts")).to_contain_text("Admin login ID: admin@example.com")
    expect(page.locator("#runAccounts")).to_contain_text("Evaluator login ID: teacher@example.com")
    # Editing a new run's pair cannot change the saved run used by Resume.
    page.locator("#admin").fill("other.admin@example.com")
    page.locator("#evaluator").fill("other.teacher@example.com")
    page.locator("#resume").click()
    expect(page.locator("#message")).to_contain_text("Admin login ID: admin@example.com")
    expect(page.locator("#message")).to_contain_text("Evaluator login ID: teacher@example.com")
    assert "other.admin@example.com" not in page.locator("#runAccounts").inner_text()
    assert "1/1 uploaded" in page.locator("#runStatus").inner_text()
    assert "32" in page.locator("#progress").inner_text()
    assert not errors, errors
    page.screenshot(path=str(OUTPUT / "workflow-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUTPUT / "workflow-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile layout overflows"
    RUN["rows"][0].update(stage="skipped", skipped_from="checked", has_checked_pdf=True,
                           error="Skipped after 3 failed attempts: Pending row missing")
    page.locator("#refresh").click()
    expect(page.locator("#runStatus")).to_contain_text("1 skipped")
    expect(page.locator("#progress")).to_contain_text("Skipped after 3 failed attempts")
    expect(page.get_by_role("button", name="Download checked PDF")).to_be_visible()
    RUN["status"] = "needs_attention"
    RUN["rows"][0].update(stage="upload_uncertain", error="Lost confirmation", has_checked_pdf=True)
    page.locator("#refresh").click()
    expect(page.locator("#progress")).to_contain_text("Confirmed in portal")
    RUN["status"] = "running"
    page.locator("#refresh").click()
    expect(page.get_by_role("button", name="Confirmed in portal")).to_be_disabled()
    RUN["status"] = "needs_attention"
    RUN["rows"][0].update(stage="faculty_review", has_checked_pdf=False)
    page.locator("#refresh").click()
    expect(page.locator("#progress")).to_contain_text("faculty review")
    expect(page.locator("#progress button")).to_have_count(0)
    signed_out = browser.new_page()
    signed_out.route("**/*", handle)
    signed_out.goto("http://workflow.test/workflow.html")
    expect(signed_out.locator("#authStatus")).to_contain_text("Sign in here to continue")
    expect(signed_out.get_by_test_id("offline-google-sign-in")).to_be_visible()
    assert signed_out.locator("#sheetUrl").evaluate("node => node.closest('.card').inert")
    page.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/changed/edit")
    expect(page.locator("#copies tr")).to_have_count(0)
    expect(page.locator("#sheetTab option")).to_have_count(1)
    assert not errors, errors
    assert not unexpected, unexpected
    browser.close()
print("Workflow UI smoke passed: on-page sign-in gate, selected worksheet, required files and admin ID, saved admin/evaluator login guidance, resume keeps the saved pair, progress, uncertain actions, review holds, desktop/mobile layout. All requests were offline fixtures.")
