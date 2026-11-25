"""Offline browser smoke: the question paper's marks are checked on Start (build 137).

A first Start is refused because the paper's marks add up to 30 while the official total is 40:
the message says so, nothing starts, and the marks editor opens in step 2 with what was read
(four questions, "2 of Q2, Q3, Q4") ready to use. The editor's sum line follows the official
total; a second Start sends the entered marks and starts the run. Change run inputs opens the
same editor for the run with its entered marks and saves them. All requests are fixtures.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect
from smoke_workflow_auth import GIS_FIXTURE, VALID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-paper-marks"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)

ROW = {"submission_id": "copy1", "student_id": "student1", "student_name": "Student 1", "program_name": "Complete Programe",
       "test_series_name": "Class 10 — Batch Tests", "test_id": "testA", "test_name": "SST Test-02",
       "copy_url": "https://example.cloudfront.net/copy1.pdf", "portal_status": "Submitted", "total_marks": "40"}
READ = {"ok": False, "items": [{"qid": "Q1", "max": 10}, {"qid": "Q2", "max": 10}, {"qid": "Q3", "max": 10}, {"qid": "Q4", "max": 10}],
        "choice_groups": [{"group_id": "choice-1", "select_count": 2, "contribution_marks": 20,
                           "options": [{"option_id": "a", "question_ids": ["Q2"]}, {"option_id": "b", "question_ids": ["Q3"]},
                                       {"option_id": "c", "question_ids": ["Q4"]}]}],
        "paper_total": 40, "message": "Fixed questions plus the choice rules add up to 30, not the official total 40."}
RUN = {"id": "d" * 32, "created_at": "2026-10-26T15:00:00", "status": "running", "rows": [{**ROW, "stage": "pending", "error": ""}]}
starts, inputs, unexpected = [], [], []


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
    status = 200
    if path == "/api/config":
        data = {"google_client_id": "offline.apps.googleusercontent.com"}
    elif path == "/api/auth/status":
        data = {"allowed": True}
    elif path.startswith("/api/workflow"):
        assert route.request.headers.get("authorization") == "Bearer " + VALID
        if path.endswith("/sheet-tabs"):
            data = {"import_id": "c" * 32, "tabs": [{"name": "October copies"}], "source_name": "Offline workbook"}
        elif path.endswith("/preview"):
            data = {"rows": [ROW]}
        elif path.endswith("/runs"):
            data = {"runs": [RUN] if starts and len(starts) > 1 else []}
        elif path == "/api/workflow/portal-login":
            data = {"configured": True, "recent": [], "suspended": []}
        elif path == "/api/workflow/autopilot":
            data = {"status": "none"}
        elif path == "/api/workflow/windows":
            data = {"show_all": False, "windows": []}
        elif path == "/api/workflow/alerts":
            data = {"active": None, "recent": [], "email": {"sender": "evaluator.b4@pw.live", "configured": False, "ok": None, "detail": "", "at": ""}}
        elif path == "/api/workflow/start":
            body = route.request.post_data_buffer.decode("utf-8", "replace")
            options = json.loads(body.split('name="options"')[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
            starts.append(options)
            if len(starts) == 1:
                status, data = 422, {"detail": {"message": "Not started: the question paper's marks need fixing first. "
                                                           + READ["message"], "marks": READ}}
            else:
                RUN["options"] = options
                data = RUN
        elif path.endswith("/inputs"):
            inputs.append(json.loads(route.request.post_data))
            data = RUN
        elif path.rsplit("/", 1)[1] == RUN["id"]:
            data = RUN
        else:
            raise AssertionError(f"unexpected workflow request {path}")
    else:
        file = ROOT / "static" / path.lstrip("/")
        if not file.is_file():
            route.fulfill(status=404)
            return
        mime = "text/javascript" if file.suffix == ".js" else "text/css" if file.suffix == ".css" else "text/html"
        route.fulfill(content_type=mime, body=file.read_bytes())
        return
    route.fulfill(status=status, content_type="application/json", body=json.dumps(data))


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 1100})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", handle)
    page.add_init_script("try { sessionStorage.setItem('pwGoogleToken', " + json.dumps(VALID) + ") } catch (error) {}")
    page.goto("http://workflow.test/workflow.html")
    expect(page.locator("#authStatus")).to_contain_text("Signed in as teacher@pw.live")
    page.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/example/edit?gid=0")
    page.locator("#loadTabs").click()
    page.locator("#sheetTab").select_option("October copies")
    page.locator("#preview").click()
    page.locator("#copies input").first.check()
    page.locator("#admin").fill("admin@pw.live")
    page.locator("#evaluator").fill("teacher.a@pw.live")
    page.locator("#studentClass").fill("Class 10")
    page.locator("#subject").fill("SST")
    page.locator("#totalMarks").fill("40")
    for target in ("paper", "key"):
        page.locator(f"#{target}").set_input_files({"name": f"{target}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-offline"})
    expect(page.locator("#marksPanel")).to_be_hidden()

    # First Start: refused with the marks that were read; the editor opens with them.
    page.locator("#start").click()
    expect(page.locator("#message")).to_contain_text("Not started: the question paper's marks need fixing first")
    expect(page.locator("#marksPanel")).to_be_visible()
    expect(page.locator("#marksRows tr")).to_have_count(4)
    expect(page.locator("#marksChoices")).to_have_value("2 of Q2, Q3, Q4")
    expect(page.locator("#useOperatorMarks")).to_be_checked()
    expect(page.locator("#marksSum")).to_contain_text("add up to 30 after the choice rules; the official total is 40. They must agree")
    assert starts[0].get("operator_marks") is None
    page.locator("#totalMarks").fill("30")                              # the paper's real total
    expect(page.locator("#marksSum")).not_to_contain_text("must agree")
    page.locator("#addMarksRow").click()
    page.locator("#marksRows tr").nth(4).locator(".marksQid").fill("Q5")
    page.locator("#marksRows tr").nth(4).locator(".marksMax").fill("2")
    expect(page.locator("#marksSum")).to_contain_text("add up to 32")
    page.locator("#marksRows tr").nth(4).get_by_role("button", name="Remove").click()
    page.screenshot(path=str(OUTPUT / "paper-marks-editor.png"), full_page=True)

    # Second Start: the entered marks go with it and the run starts.
    page.locator("#start").click()
    expect(page.locator("#message")).to_contain_text("Workflow started")
    sent = starts[1]["operator_marks"]
    assert sent == {"items": [{"qid": "Q1", "max": 10}, {"qid": "Q2", "max": 10}, {"qid": "Q3", "max": 10}, {"qid": "Q4", "max": 10}],
                    "choices": [{"select": 2, "options": [["Q2"], ["Q3"], ["Q4"]]}]}, sent
    assert starts[1]["total_marks"] == "30"

    # Change run inputs: the run's entered marks in the same editor, saved with the inputs.
    expect(page.locator("#currentRunTitle")).to_contain_text("SST Test-02")
    page.locator("#changeInputs").click()
    expect(page.locator("#marksHomeRun #marksPanel")).to_be_visible()
    expect(page.locator("#marksRows tr")).to_have_count(4)
    expect(page.locator("#useOperatorMarks")).to_be_checked()
    page.locator("#marksRows tr").nth(0).locator(".marksMax").fill("12")
    page.locator("#saveRunInputs").click()
    expect(page.locator("#message")).to_contain_text("Run inputs saved")
    assert inputs[-1]["operator_marks"]["items"][0] == {"qid": "Q1", "max": 12}, inputs
    expect(page.locator("#marksHomeStart #marksPanel")).to_be_hidden()  # back in step 2, closed
    assert not errors, errors
    assert not unexpected, unexpected
    browser.close()
print("Workflow paper-marks smoke passed: Start refused with the marks read, editor prefilled with rows and choice rule, "
      "sum follows the official total, second Start sends the entered marks, Change run inputs edits and saves them. "
      "All requests were offline fixtures.")
