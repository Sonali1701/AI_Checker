"""Offline browser smoke: step 3 shows only this tab's run; saved runs have their own section;
the alert banner shows an active alert and Dismiss acknowledges it.

Two saved runs. A fresh tab shows no run in step 3 and lists both in Saved runs. Show here
opens one; Refresh keeps it; a second tab opens the other; the first tab still shows its own
after a refresh. An active alert shows the banner; Dismiss posts the acknowledgement and
hides it. Choosing another test in step 1 no longer changes step 3. All requests are fixtures.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect
from smoke_workflow_auth import GIS_FIXTURE, VALID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-saved-runs"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)


def sheet_row(n, test):
    return {"submission_id": f"copy{n}", "student_id": f"student{n}", "student_name": f"Student {n}",
            "program_name": "Complete Programe", "test_series_name": "Class 10 — Batch Tests", "test_id": f"test{test}",
            "test_name": f"Subjective Test {test}", "copy_url": f"https://example.cloudfront.net/copy{n}.pdf",
            "portal_status": "Submitted", "total_marks": "40"}


ROWS = [sheet_row(1, "A"), sheet_row(2, "B")]
RUN_A = {"id": "a" * 32, "created_at": "2026-10-26T09:00:00", "status": "complete", "options": {"evaluator_email": "teacher.a@pw.live"},
         "rows": [{**sheet_row(1, "A"), "stage": "uploaded", "error": "", "awarded_marks": 31, "flagged_for_review": True,
                   "note": "Faculty review flagged (confidence 0%); uploaded with its marks. The checker asked for a review: cover mismatch"}]}
RUN_B = {"id": "b" * 32, "created_at": "2026-10-26T11:00:00", "status": "needs_attention", "options": {"evaluator_email": "teacher.b@pw.live"},
         "rows": [{**sheet_row(2, "B"), "stage": "faculty_review", "error": "Needs review", "awarded_marks": "",
                   "note": "Held for faculty review: the checker could not mark this copy, so there are no marks to upload."}]}
RUNS = {RUN_A["id"]: RUN_A, RUN_B["id"]: RUN_B}
ALERT = {"active": None, "acks": 0}
PORTAL_LOGIN = {"configured": False, "saved": []}
unexpected = []


def alert_state():
    return {"active": ALERT["active"], "recent": [],
            "email": {"sender": "evaluator.b4@pw.live", "configured": False, "ok": None, "detail": "", "at": ""}}


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
        data = {"google_client_id": "offline.apps.googleusercontent.com"}
    elif path == "/api/auth/status":
        data = {"allowed": True}
    elif path.startswith("/api/workflow"):
        assert route.request.headers.get("authorization") == "Bearer " + VALID
        if path.endswith("/sheet-tabs"):
            data = {"import_id": "c" * 32, "tabs": [{"name": "October copies"}], "source_name": "Offline workbook"}
        elif path.endswith("/preview"):
            data = {"rows": ROWS}
        elif path.endswith("/runs"):
            data = {"runs": [RUN_B, RUN_A]}   # newest first, as the server sorts them
        elif path == "/api/workflow/autopilot":
            data = {"status": "none"}
        elif path == "/api/workflow/windows":
            data = {"show_all": False, "windows": []}
        elif path == "/api/workflow/alerts":
            data = alert_state()
        elif path == "/api/workflow/portal-login":
            if route.request.method == "POST":
                password = json.loads(route.request.post_data)["password"]
                PORTAL_LOGIN["saved"].append(password)
                PORTAL_LOGIN["configured"] = bool(password)
            data = {"configured": PORTAL_LOGIN["configured"], "suspended": [],
                    "recent": [{"account": "teacher.b@pw.live", "ok": False, "detail": "the portal did not open after Login", "at": "2026-10-26T15:42:00"}]}
        elif path == "/api/workflow/alerts/ack":
            ALERT["active"], ALERT["acks"] = None, ALERT["acks"] + 1
            data = alert_state()
        elif path.rsplit("/", 1)[1] in RUNS:
            data = RUNS[path.rsplit("/", 1)[1]]
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
    route.fulfill(content_type="application/json", body=json.dumps(data))


def open_tab(context, errors):
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", handle)
    page.add_init_script("try { sessionStorage.setItem('pwGoogleToken', " + json.dumps(VALID) + ") } catch (error) {}")
    page.goto("http://workflow.test/workflow.html")
    expect(page.locator("#authStatus")).to_contain_text("Signed in as teacher@pw.live")
    return page


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    errors = []
    first = open_tab(context, errors)
    expect(first.locator("#currentRunTitle")).to_contain_text("No run in this tab yet")
    expect(first.locator("#savedRuns tr")).to_have_count(2)
    expect(first.locator("#savedRuns tr").nth(0)).to_contain_text("Subjective Test B")
    expect(first.locator("#savedRuns tr").nth(1)).to_contain_text("Subjective Test A")
    expect(first.locator("#savedRuns tr").nth(0)).to_contain_text("finished, 1 needs a decision")
    expect(first.locator("#savedRuns tr").nth(1)).to_contain_text("1 / 0")     # faculty review: 1 uploaded with its flag, none held
    assert first.locator("#progress tr").count() == 0 and first.locator("#runs").count() == 0

    first.locator("#savedRuns tr").nth(1).get_by_role("button", name="Show here").click()
    expect(first.locator("#currentRunTitle")).to_contain_text("Subjective Test A — complete")
    expect(first.locator("#progress tr")).to_have_count(1)
    expect(first.locator("#progress")).to_contain_text("uploaded, flagged for faculty review")
    expect(first.locator("#runStatus")).to_contain_text("1/1 uploaded (1 flagged for faculty review)")
    expect(first.locator("#savedRuns tr").nth(1)).to_contain_text("Shown here")
    first.locator("#refresh").click()
    expect(first.locator("#currentRunTitle")).to_contain_text("Subjective Test A")

    # Choosing another test in step 1 no longer changes step 3.
    first.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/example/edit?gid=0")
    first.locator("#loadTabs").click()
    first.locator("#sheetTab").select_option("October copies")
    first.locator("#preview").click()
    first.locator("#copies input").wait_for()
    values = first.locator("#test option").evaluate_all("options => options.map(o => o.value)")
    first.locator("#test").select_option(values[1])
    first.wait_for_timeout(300)
    expect(first.locator("#currentRunTitle")).to_contain_text("Subjective Test A")
    expect(first.locator("#alertEmail")).to_have_value("teacher@pw.live")   # the signed-in account is the default alert address

    second = open_tab(context, errors)
    expect(second.locator("#currentRunTitle")).to_contain_text("No run in this tab yet")
    second.locator("#savedRuns tr").nth(0).get_by_role("button", name="Show here").click()
    expect(second.locator("#currentRunTitle")).to_contain_text("Subjective Test B — finished, 1 needs a decision")
    expect(second.locator("#progress")).to_contain_text("held for faculty review")
    expect(second.locator("#progress")).to_contain_text("no marks to upload")
    expect(second.locator("#runStatus")).to_contain_text("1 held for faculty review")
    first.locator("#refresh").click()
    expect(first.locator("#currentRunTitle")).to_contain_text("Subjective Test A")   # each tab keeps its own run

    # An active alert shows the banner; Dismiss acknowledges it.
    ALERT["active"] = {"key": "k", "title": "Run complete: Subjective Test A", "body": "1 of 1 uploaded.", "at": "", "to": []}
    expect(first.locator("#alertBanner")).to_be_visible(timeout=10000)
    expect(first.locator("#alertText")).to_contain_text("Run complete: Subjective Test A")
    first.locator("#dismissAlert").click()
    expect(first.locator("#alertBanner")).to_be_hidden()
    assert ALERT["acks"] == 1
    expect(first.locator("#alertStatus")).to_contain_text("not set up yet")

    # Section 6: the portal password is saved (never shown again) and can be removed.
    expect(first.locator("#portalLoginStatus")).to_contain_text("Not saved")
    expect(first.locator("#portalLoginStatus")).to_contain_text("Could not sign in teacher.b@pw.live (15:42)")
    first.locator("#portalPassword").fill("fixture-password")
    first.locator("#savePortalPassword").click()
    expect(first.locator("#portalLoginStatus")).to_contain_text("Saved on this computer")
    expect(first.locator("#portalPassword")).to_have_value("")
    first.locator("#removePortalPassword").click()
    expect(first.locator("#portalLoginStatus")).to_contain_text("Not saved")
    assert PORTAL_LOGIN["saved"] == ["fixture-password", ""], PORTAL_LOGIN
    first.screenshot(path=str(OUTPUT / "saved-runs.png"), full_page=True)
    assert not errors, errors
    assert not unexpected, unexpected
    browser.close()
print("Workflow saved-runs smoke passed: step 3 shows only this tab's run, saved runs listed separately with Show here, "
      "each tab keeps its own run, step-1 test changes leave step 3 alone, alert banner and Dismiss. All requests were offline fixtures.")
