"""Offline browser smoke: the Autopilot card (build 139).

Both sheet links and tab names, Check sheets: both tabs are imported with the Sheets token and
the plan shows each test with editable inputs (one with an unknown exam type marked "Will be
skipped") and the pairs without their passwords. A corrected subject and exam type go with
Start; the card then shows each pair's test and each test's status, Show here opens a test's
run in step 3, and Stop (after confirming) stops the autopilot. A running test shows its copies so
far. The Portal windows card (build 140): the open windows, the one that needs the operator named
in red, its failure picture, Hide and Show sent per window, and every window on screen (which
disables the per-window buttons). All requests are fixtures.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect
from smoke_workflow_auth import GIS_FIXTURE, VALID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-autopilot"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)

STUDENTS = "https://docs.google.com/spreadsheets/d/students/edit?gid=1"
ACCOUNTS = "https://docs.google.com/spreadsheets/d/accounts/edit?gid=2"
TEST_A = {"key": "P|S|testA|Board Subjective Test - 10 (Biology) (Infinity Pro)", "test_name": "Board Subjective Test - 10 (Biology) (Infinity Pro)",
          "program_name": "P", "test_series_name": "S", "category": "school", "student_class": "Class 12th - Science - CBSE Board",
          "subject": "Biology", "total_marks": "70", "copies": 12, "already_in_runs": 0, "problem": "", "status": "waiting", "detail": ""}
TEST_B = {**TEST_A, "key": "P|S|testB|Weekly Test (GATE)", "test_name": "Weekly Test (GATE)", "category": "", "subject": "GATE",
          "problem": "the exam type is not one the checker knows (school, Vidyapeeth, commerce, CA)"}
PLAN = {"plan_id": "p" * 32, "tests": [TEST_A, TEST_B], "pairs": [{"admin": "admin.one@pw.live", "evaluator": "eval.one@pw.live", "has_password": True}],
        "windows": 2, "copies": 24}
FOLDER = "C:\\AI Checker\\Checked copies\\VICTORY 2027 (Class 10th) - " + TEST_A["test_name"]
RUN = {"id": "e" * 32, "created_at": "2026-10-26T16:00:00", "status": "running", "options": {"evaluator_email": "eval.one@pw.live"},
       "checked_folder": FOLDER, "checking_done": True,
       "rows": [{"submission_id": "c1", "student_id": "s1", "student_name": "Student 1", "test_name": TEST_A["test_name"], "stage": "checked", "error": "",
                 "checked_file": FOLDER + "\\STUDENT_1_s1.pdf"},
                {"submission_id": "c2", "student_id": "s2", "student_name": "Student 2", "test_name": TEST_A["test_name"], "stage": "assigned", "error": ""}]}
STATE = {"status": "none"}
calls, unexpected = {"tabs": [], "plan": [], "start": [], "stop": 0, "show": [], "show_all": [], "pictures": 0, "sign_in": [], "folder": 0}, []
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
WINDOWS = {"show_all": False, "windows": [
    {"role": "admin", "account": "admin.one@pw.live", "open": True, "shown": True, "wanted": True, "why": "problem",
     "reason": "A portal step failed here: Programs not visible", "since": "2026-10-26T16:10:00",
     "problem": "Programs not visible (screenshot: s1_20261026_161000.png)", "screenshot": "s1_20261026_161000.png", "at": "2026-10-26T16:10:00"},
    {"role": "evaluator", "account": "eval.one@pw.live", "open": True, "shown": False, "wanted": False, "why": "", "reason": "",
     "since": "", "problem": "", "screenshot": "", "at": ""}]}


def window_state(role, show):
    for window in WINDOWS["windows"]:
        if window["role"] == role:
            window.update(wanted=show, why="you" if show else "", reason="Shown by you" if show else "")
    return WINDOWS


def running_state(status="running"):
    return {"status": status, "created_at": "2026-10-26T16:00:00", "error": "",
            "pairs": [{"admin": "admin.one@pw.live", "evaluator": "eval.one@pw.live", "test": TEST_A["test_name"], "state": "working"}],
            "tests": [{**TEST_A, "subject": "Botany", "status": "running", "detail": "Running with admin admin.one@pw.live and evaluator eval.one@pw.live",
                       "run_id": RUN["id"], "pair": 0, "progress": "3 of 12 uploaded; 2 in progress now"},
                      {**TEST_B, "category": "school", "status": "waiting", "run_id": "", "pair": None}],
            "counts": {"running": 1, "waiting": 1}}


def handle(route):
    parsed = urlparse(route.request.url)
    path, request = parsed.path, route.request
    if parsed.hostname == "accounts.google.com" and path == "/gsi/client":
        route.fulfill(content_type="text/javascript", body=GIS_FIXTURE)
        return
    if parsed.hostname != "workflow.test":
        unexpected.append(request.url)
        route.abort()
        return
    if path == "/api/config":
        data = {"google_client_id": "offline.apps.googleusercontent.com"}
    elif path == "/api/auth/status":
        data = {"allowed": True}
    elif path.startswith("/api/workflow"):
        assert request.headers.get("authorization") == "Bearer " + VALID
        if path == "/api/workflow/sheet-tabs":
            assert request.headers.get("x-google-sheets-token") == "sheets-token"
            url = "students" if "students" in (request.post_data or "") else "accounts"
            calls["tabs"].append(url)
            data = {"import_id": (url[0] * 32), "tabs": [{"name": "master data"}, {"name": "Sheet1"}], "source_name": "Connected Google Sheet"}
        elif path == "/api/workflow/autopilot/plan":
            assert request.headers.get("x-google-sheets-token") == "sheets-token"
            calls["plan"].append(json.loads(request.post_data))
            data = PLAN
        elif path == "/api/workflow/autopilot/start":
            calls["start"].append(json.loads(request.post_data))
            STATE.clear(); STATE.update(running_state())
            data = STATE
        elif path == "/api/workflow/autopilot/stop":
            calls["stop"] += 1
            STATE.clear(); STATE.update(running_state("stopped"))
            data = STATE
        elif path == "/api/workflow/autopilot/paper-links":
            assert request.method == "POST"
            calls["paper_links"] = calls.get("paper_links", 0) + 1
            STATE["tests"][-1].update(paper_link="https://static.pw.live/paper.pdf",
                                      key_links=["https://static.pw.live/key-1.pdf", "https://static.pw.live/key-2.pdf"])
            data = STATE
        elif path == "/api/workflow/autopilot/marks":
            calls.setdefault("marks", []).append(json.loads(request.post_data))
            STATE["tests"][-1].update(status="waiting", detail="Will run with the marks you entered")
            data = STATE
        elif path == "/api/workflow/autopilot":
            data = STATE
        elif path == "/api/workflow/runs":
            data = {"runs": [RUN] if calls["start"] else []}
        elif path == "/api/workflow/alerts":
            data = {"active": None, "recent": [], "email": {"sender": "evaluator.b4@pw.live", "configured": False, "ok": None, "detail": "", "at": ""}}
        elif path == "/api/workflow/portal-login":
            data = {"configured": True, "accounts": 2, "recent": [], "suspended": []}
        elif path == "/api/workflow/windows/sign-in":
            calls["sign_in"].append(json.loads(request.post_data)["accounts"])
            data = {"show_all": False, "windows": [
                {"role": "admin", "account": "admin.one@pw.live", "open": True, "shown": False, "wanted": False, "why": "", "reason": "",
                 "since": "", "problem": "", "screenshot": "", "at": "", "signed_in": True},
                {"role": "evaluator", "account": "eval.one@pw.live", "open": True, "shown": True, "wanted": True, "why": "sign_in",
                 "reason": "Sign in here with the EVALUATOR account eval.one@pw.live", "since": "", "problem": "", "screenshot": "", "at": "",
                 "signed_in": False}]}
        elif path == "/api/workflow/windows/show":
            sent = json.loads(request.post_data)
            calls["show"].append(sent)
            data = window_state(sent["role"], sent["show"])
        elif path == "/api/workflow/windows":
            if request.method == "POST":
                calls["show_all"].append(json.loads(request.post_data)["show_all"])
                WINDOWS["show_all"] = calls["show_all"][-1]
                for window in WINDOWS["windows"]:   # as the server says: on screen when all are, or when one is needed
                    window["wanted"] = WINDOWS["show_all"] or bool(window["why"])
            data = WINDOWS if calls["start"] else {"show_all": False, "windows": []}
        elif path == "/api/workflow/windows/screenshot/s1_20261026_161000.png":
            calls["pictures"] += 1
            route.fulfill(content_type="image/png", body=PNG)
            return
        elif path == f"/api/workflow/{RUN['id']}/open-folder":
            assert request.method == "POST"
            calls["folder"] += 1
            data = {"folder": FOLDER}
        elif path == f"/api/workflow/{RUN['id']}":
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
    route.fulfill(content_type="application/json", body=json.dumps(data))


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 1200})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    page.route("**/*", handle)
    page.add_init_script("try { sessionStorage.setItem('pwGoogleToken', " + json.dumps(VALID) + ") } catch (error) {}")
    page.goto("http://workflow.test/workflow.html")
    expect(page.locator("#authStatus")).to_contain_text("Signed in as teacher@pw.live")
    page.evaluate("window.WorkflowSheets.getToken = () => 'sheets-token'")
    expect(page.locator("#apStart")).to_be_disabled()
    expect(page.locator("#apSignIn")).to_be_disabled()

    page.locator("#apCheck").click()
    expect(page.locator("#message")).to_contain_text("Give both sheet links and both tab names")
    page.locator("#apStudentsUrl").fill(STUDENTS)
    page.locator("#apStudentsTab").fill("master data")
    page.locator("#apAccountsUrl").fill(ACCOUNTS)
    page.locator("#apAccountsTab").fill("Sheet1")
    page.locator("#apCheck").click()
    expect(page.locator("#message")).to_contain_text("Sheets read: 2 tests and 1 admin + evaluator pairs")
    assert calls["tabs"] == ["students", "accounts"], calls
    assert calls["plan"][0] == {"students": {"import_id": "s" * 32, "worksheet": "master data"}, "accounts": {"import_id": "a" * 32, "worksheet": "Sheet1"}}
    expect(page.locator("#apStatus")).to_contain_text("Plan: 2 tests, 24 copies, 1 pairs (2 portal windows)")
    expect(page.locator("#apPairs")).to_contain_text("from the sheet")
    expect(page.locator("#apTests tr").nth(1)).to_contain_text("Will be skipped: the exam type")
    expect(page.locator("#apTests tr").nth(0).locator("[data-field=subject]")).to_have_value("Biology")
    page.locator("#apTests tr").nth(0).locator("[data-field=subject]").fill("Botany")
    page.locator("#apTests tr").nth(1).locator("[data-field=category]").select_option("school")
    page.screenshot(path=str(OUTPUT / "autopilot-plan.png"), full_page=True)

    # Sign in the windows: every ID of the plan; one already signed in, one on screen for the operator.
    page.locator("#apSignIn").click()
    expect(page.locator("#message")).to_contain_text("Opening the portal window of each of the 2 IDs")
    assert calls["sign_in"] == [[{"role": "admin", "account": "admin.one@pw.live"}, {"role": "evaluator", "account": "eval.one@pw.live"}]], calls["sign_in"]
    expect(page.locator("#windowsStatus")).to_contain_text("2 open, 1 signed in, 1 on screen; 1 needs you: Evaluator eval.one@pw.live.")
    expect(page.locator("#portalWindows tr").nth(0)).to_contain_text("hidden, signed in")
    expect(page.locator("#portalWindows tr").nth(1)).to_contain_text("on screen: Sign in here with the EVALUATOR account eval.one@pw.live")

    page.locator("#apStart").click()
    expect(page.locator("#message")).to_contain_text("Autopilot started")
    sent = calls["start"][0]
    assert sent["plan_id"] == "p" * 32 and sent["overrides"][TEST_A["key"]]["subject"] == "Botany", sent
    assert sent["overrides"][TEST_B["key"]]["category"] == "school" and sent["overrides"][TEST_A["key"]]["total_marks"] == "70"
    expect(page.locator("#apStatus")).to_contain_text("Autopilot running")
    expect(page.locator("#apPairs")).to_contain_text(TEST_A["test_name"])
    expect(page.locator("#apTests tr").nth(0)).to_contain_text("running · 3 of 12 uploaded; 2 in progress now · Running with admin admin.one@pw.live")
    expect(page.locator("#apStart")).to_be_disabled()
    expect(page.locator("#apStop")).to_be_enabled()
    page.locator("#apTests tr").nth(0).get_by_role("button", name="Show here").click()
    expect(page.locator("#currentRunTitle")).to_contain_text(TEST_A["test_name"])
    # Build 146: checked copies are saved in a batch-test folder and uploaded behind.
    expect(page.locator("#checkedFolder")).to_contain_text(f"Checked copies: 1 saved in {FOLDER} · 1 checked, waiting to upload · every copy is checked; uploads go on")
    expect(page.locator("#progress")).to_contain_text("checked, waiting to upload")
    page.locator("#openCheckedFolder").click()
    expect(page.locator("#message")).to_contain_text("Opened the checked copies folder: " + FOLDER)
    page.locator("#apTests tr").nth(0).get_by_role("button", name="Open folder").click()
    expect(page.locator("#message")).to_contain_text("Opened the checked copies folder")
    assert calls["folder"] == 2
    page.screenshot(path=str(OUTPUT / "autopilot-running.png"), full_page=True)

    # The Portal windows card: polled every 5 s.
    expect(page.locator("#windowsStatus")).to_contain_text("2 open, 0 signed in, 1 on screen; 1 needs you: Admin admin.one@pw.live.", timeout=8000)
    assert "error" in page.locator("#windowsStatus").get_attribute("class")
    admin_row, evaluator_row = page.locator("#portalWindows tr").nth(0), page.locator("#portalWindows tr").nth(1)
    expect(admin_row).to_contain_text("on screen: a copy failed here on its last attempt")
    expect(admin_row).to_contain_text("Programs not visible (screenshot: s1_20261026_161000.png) (2026-10-26 16:10)")
    expect(evaluator_row).to_contain_text("hidden, working")
    admin_row.get_by_role("button", name="Picture").click()
    expect(page.locator("#windowShot")).to_be_visible()
    expect(page.locator("#windowShotTitle")).to_contain_text("Admin admin.one@pw.live: the portal when the step failed")
    assert calls["pictures"] == 1 and page.evaluate("document.getElementById('windowShotImage').naturalWidth") == 1
    page.locator("#closeWindowShot").click()
    expect(page.locator("#windowShot")).to_be_hidden()
    admin_row.get_by_role("button", name="Hide").click()
    expect(page.locator("#message")).to_contain_text("is hidden after its current step; it keeps working")
    evaluator_row.get_by_role("button", name="Show").click()
    expect(page.locator("#message")).to_contain_text("The window of Evaluator eval.one@pw.live comes on screen after its current step.")
    assert calls["show"] == [{"role": "admin", "account": "admin.one@pw.live", "show": False},
                             {"role": "evaluator", "account": "eval.one@pw.live", "show": True}], calls["show"]
    expect(evaluator_row).to_contain_text("coming on screen")
    page.locator("#showAllWindows").check()
    expect(page.locator("#message")).to_contain_text("Every portal window comes on screen after its current step.")
    assert calls["show_all"] == [True]
    expect(evaluator_row.get_by_role("button")).to_be_disabled()
    page.screenshot(path=str(OUTPUT / "autopilot-windows.png"), full_page=True)

    page.locator("#apStop").click()
    expect(page.locator("#apStatus")).to_contain_text("Autopilot stopped")
    assert calls["stop"] == 1
    expect(page.locator("#apStop")).to_be_disabled()

    # A marks problem names its paper and Model Answer (operator, 31 Oct 2026). One saved before
    # links were kept has none until Get the paper links reads them.
    expect(page.locator("#apMarks")).to_be_hidden()
    STATE["tests"].append({**TEST_A, "test_name": "Subjective Test 02 (SST)", "key": "marks", "status": "marks_problem", "copies": 3, "tab": "27 sept", "run_id": "", "pair": 0,
                           "detail": "Not started: the paper's marks need fixing. Rows add up to 40.", "paper_link": "", "key_links": []})
    expect(page.locator("#apMarksTitle")).to_have_text("Marks problems so far: 1 (3 copies)", timeout=8000)
    row = page.locator("#apMarksRows tr").first
    expect(row).to_contain_text("Subjective Test 02 (SST) (tab: 27 sept)")
    expect(row).to_contain_text("Rows add up to 40")
    expect(row).to_contain_text("Not read yet")
    page.locator("#apMarksLinks").click()
    expect(page.locator("#message")).to_contain_text("Paper links read")
    assert calls["paper_links"] == 1
    expect(row.get_by_role("link", name="Open paper")).to_have_attribute("href", "https://static.pw.live/paper.pdf")
    expect(row.get_by_role("link", name="Model Answer 2")).to_have_attribute("href", "https://static.pw.live/key-2.pdf")
    expect(page.locator("#apMarksLinks")).to_be_hidden()
    page.screenshot(path=str(OUTPUT / "autopilot-marks-problems.png"), full_page=True)

    # Enter marks: the form opens with what was read and the test's total; the operator corrects
    # them, and the test goes back in the queue with those marks.
    STATE["tests"][-1].update(marks_items=[{"qid": "Q1", "max": 10}, {"qid": "Q2", "max": 10}, {"qid": "Q3", "max": 10}, {"qid": "Q4", "max": 10}],
                              marks_choices=[{"select": 2, "options": [["Q2"], ["Q3"], ["Q4"]]}])
    page.wait_for_timeout(5500)
    row.get_by_role("button", name="Enter marks").click()
    expect(page.locator("#apMarksEntry")).to_be_visible()
    expect(page.locator("#apMarksFor")).to_contain_text("Subjective Test 02 (SST)")
    expect(page.locator("#apMarksTotal")).to_have_value("70")
    expect(page.locator("#useOperatorMarksLine")).to_be_hidden()
    assert page.locator("#marksRows tr").count() == 4
    expect(page.locator("#marksChoices")).to_have_value("2 of Q2, Q3, Q4")
    page.locator("#apMarksTotal").fill("30")
    expect(page.locator("#marksSum")).to_contain_text("These marks add up to 30 after the choice rules; the official total is 30.")
    page.screenshot(path=str(OUTPUT / "autopilot-marks-entry.png"))   # not full_page: resizing the page here moves the button from under the click
    page.locator("#apMarksRun").click()
    expect(page.locator("#message")).to_contain_text("will run with the marks you entered")
    assert calls["marks"] == [{"key": "marks", "total_marks": "30",
                               "operator_marks": {"items": [{"qid": f"Q{n}", "max": 10} for n in range(1, 5)],
                                                  "choices": [{"select": 2, "options": [["Q2"], ["Q3"], ["Q4"]]}]}}], calls["marks"]
    expect(page.locator("#apMarks")).to_be_hidden()
    expect(page.locator("#marksPanel")).to_be_hidden()
    assert page.evaluate("document.getElementById('marksPanel').parentElement.id") == "marksHomeStart"

    # Check again sends the test back with no marks, to be read once more.
    STATE["tests"][-1].update(status="marks_problem", detail="Rows add up to 40.")
    expect(page.locator("#apMarksRows tr")).to_have_count(1, timeout=8000)
    page.locator("#apMarksRows").get_by_role("button", name="Check again").click()
    expect(page.locator("#message")).to_contain_text("its marks will be read again")
    assert calls["marks"][-1] == {"key": "marks"}

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
    assert not errors, errors
    assert not unexpected, unexpected
    browser.close()
print("Workflow autopilot smoke passed: both tabs imported with the Sheets token, editable plan with a will-be-skipped test, "
      "Sign in the windows for every ID, corrected inputs sent with Start, pair and test status with copies so far, Show here, the checked copies folder (line, Open buttons), "
      "Portal windows (needs-you line, picture, Hide, Show, all on screen), Stop, marks problems with their paper links, Enter marks and Check again. All requests were offline fixtures.")
