"""Offline Chrome checks for the production checker/workflow PDF page controls.

Every network request is intercepted. These verify browser request construction,
defaults and stale-input protection, without Google, grading or portal calls.
"""
from __future__ import annotations

import json
import os
import time
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from smoke_workflow_auth import GIS_FIXTURE, ROW, RUN, VALID

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-pdf-ranges-ui"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)

MARKS = {"items": [{"qid": "1", "max": 5}], "method": "ai_verified", "total": 5,
         "paper_total": 5, "marks_attestation": "offline-page-proof",
         "marks_attestation_expires_at": int(time.time()) + 3600,
         "choice_detected": False, "choice_groups": [], "verification_detail": "Verified fixture"}
DESIGN = {"title": "Offline design", "questions": [{"qid": "1", "printed_marks": 5,
          "brief": "Draw a shape", "criteria": []}], "notes": []}
FILE = lambda name: {"name": name, "mimeType": "application/pdf", "buffer": b"%PDF-offline-fixture"}


def multipart(request):
    message = BytesParser(policy=default).parsebytes(
        ("Content-Type: " + request.headers.get("content-type", "") + "\r\n\r\n").encode()
        + (request.post_data_buffer or b""))
    values = {}
    if message.is_multipart():
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            values[name] = part.get_filename() or part.get_content()
    return values


class Fixture:
    def __init__(self, browser):
        self.posts = []
        self.errors = []
        self.unexpected = []
        self.started = False
        self.hold_path = ""
        self.held = []
        self.context = browser.new_context(viewport={"width": 1280, "height": 1000})
        self.context.route("**/*", self.handle)
        self.context.add_init_script("sessionStorage.setItem('pwGoogleToken', " + json.dumps(VALID) + ")")
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def handle(self, route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if parsed.hostname == "accounts.google.com" and path == "/gsi/client":
            route.fulfill(content_type="text/javascript", body=GIS_FIXTURE)
            return
        if parsed.hostname != "checker.test":
            self.unexpected.append(request.url)
            route.abort()
            return
        if path.startswith("/api/"):
            fields = multipart(request) if request.method == "POST" else {}
            if request.method == "POST":
                self.posts.append((path, fields))
            if path == self.hold_path:
                self.hold_path = ""
                self.held.append(route)
                return
            if path == "/api/config":
                data = {"google_client_id": "offline.apps.googleusercontent.com", "categories": []}
            elif path == "/api/auth/status":
                assert request.headers.get("authorization") == "Bearer " + VALID
                data = {"allowed": True}
            else:
                assert request.headers.get("authorization") == "Bearer " + VALID
                if path == "/api/detect-marks":
                    data = MARKS
                elif path == "/api/generate-rubric":
                    data = {"rubric": "Offline draft rubric"}
                elif path == "/api/design/read-paper":
                    data = DESIGN
                elif path == "/api/bulk/preview":
                    data = {"found": 1, "to_grade": 1, "already_done": 0, "workers": 1,
                            "parts": 1, "target": "C:/offline/evaluated", "estimate": {}}
                elif path in ("/api/evaluate", "/api/evaluate-design", "/api/bulk/start"):
                    data = {"job_id": "offlinejob", "download_key": "fixture", "to_grade": 1, "already_done": 0}
                elif path.startswith("/api/jobs/"):
                    data = {"status": "done", "done": 1, "total": 1, "results": []}
                elif path == "/api/workflow/sheet-tabs":
                    data = {"import_id": "b" * 32, "tabs": [{"name": "Copies"}], "source_name": "Offline workbook"}
                elif path == "/api/workflow/preview":
                    data = {"rows": [ROW]}
                elif path == "/api/workflow/start":
                    self.started = True
                    data = RUN
                elif path == "/api/workflow/runs":
                    data = {"runs": [RUN]} if self.started else {"runs": []}
                elif path.startswith("/api/workflow/"):
                    data = RUN
                else:
                    raise AssertionError("Unexpected API: " + path)
            route.fulfill(content_type="application/json", body=json.dumps(data))
            return
        source = ROOT / "static" / ("index.html" if path == "/" else path.lstrip("/"))
        if not source.is_file():
            route.fulfill(status=404)
            return
        mime = "text/javascript" if source.suffix == ".js" else "text/css" if source.suffix == ".css" else "text/html"
        route.fulfill(content_type=mime, body=source.read_bytes())

    def open(self, path="/"):
        self.page.goto("http://checker.test" + path)
        expect(self.page.locator("#authStatus")).to_contain_text("Signed in")

    def last(self, path):
        found = [fields for url, fields in self.posts if url == path]
        assert found, path + " was never submitted"
        return found[-1]

    def wait_post(self, path, count=1):
        for _ in range(100):
            self.page.wait_for_timeout(30)
            if sum(url == path for url, _ in self.posts) >= count:
                return self.last(path)
        raise AssertionError(path + " was not submitted")

    def checker_files(self, *, combined=False):
        p = self.page
        p.locator("#studentClass").fill("Class 10")
        p.locator("#subject").fill("Biology")
        p.locator("#totalMarks").fill("5")
        if combined:
            p.locator("#questionPageRange").fill("1")
            p.locator("#answerPageRange").fill("2-3")
            p.locator("#useQuestionPaperAsKey").check()
        p.locator("#qpFile").set_input_files(FILE("combined.pdf" if combined else "questions.pdf"))
        p.locator("#saFiles").set_input_files(FILE("student.pdf"))
        if not combined:
            p.locator("#akFile").set_input_files(FILE("answers.pdf"))
        expect(p.locator("#marksMethod")).to_contain_text("Extracted")


def assert_selection(fields, question="1", answer="2-3", combined="true"):
    assert fields["question_page_range"] == question, fields
    assert fields["answer_page_range"] == answer, fields
    assert fields["use_question_paper_as_key"] == combined, fields
    if combined == "true":
        assert "answer_key" not in fields, fields


checks = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    fixture = Fixture(browser)
    p = fixture.page

    fixture.open()
    fixture.checker_files()
    p.locator("#evaluate").click()
    fields = fixture.wait_post("/api/evaluate")
    assert_selection(fields, "", "", "false")
    assert fields["question_paper"] == "questions.pdf" and fields["answer_key"] == "answers.pdf"
    assert fields["key_source"] == "upload"
    checks.append("Separate file defaults preserve whole files and upload key source")

    fixture.open()
    fixture.checker_files(combined=True)
    expect(p.locator("#separateKeyFile")).to_be_hidden()
    expect(p.locator("#akFile")).to_be_disabled()
    assert_selection(fixture.last("/api/detect-marks"))
    assert p.evaluate("state.marksAttestation") == "offline-page-proof"
    p.locator("#questionPageRange").fill("1-2")
    assert p.evaluate("state.marksAttestation") == ""
    assert p.evaluate("state.marksItems.length") == 0
    p.locator("#questionPageRange").fill("1")
    p.locator("#detectMarks").click()
    expect(p.locator("#marksMethod")).to_contain_text("Extracted")
    p.locator("#answerPageRange").fill("3")
    assert p.evaluate("state.marksAttestation") == ""
    p.locator("#answerPageRange").fill("2-3")
    p.locator("#detectMarks").click()
    expect(p.locator("#marksMethod")).to_contain_text("Extracted")
    p.locator("#useQuestionPaperAsKey").uncheck()
    assert p.evaluate("state.marksAttestation") == ""
    expect(p.locator("#evaluate")).to_be_disabled()
    p.locator("#useQuestionPaperAsKey").check()
    p.locator("#detectMarks").click()
    expect(p.locator("#marksMethod")).to_contain_text("Extracted")
    p.locator("#evaluate").click()
    assert_selection(fixture.wait_post("/api/evaluate", 2))
    assert fixture.last("/api/evaluate")["key_source"] == "upload"
    expect(p.locator("#evaluate")).to_be_enabled()
    checks.append("Combined PDF sends one source; both ranges and toggle invalidate marks proof")

    fixture.open()
    fixture.checker_files(combined=True)
    fixture.hold_path = "/api/detect-marks"
    p.locator("#detectMarks").click()
    p.wait_for_timeout(80)
    assert len(fixture.held) == 1
    p.locator("#questionPageRange").fill("2")
    fixture.held.pop().fulfill(content_type="application/json", body=json.dumps(MARKS))
    expect(p.locator("#detectMarks")).to_be_enabled()
    assert p.evaluate("state.marksAttestation") == "" and p.evaluate("state.marksItems.length") == 0
    checks.append("In-flight marks response cannot restore proof after range changes")

    fixture.open()
    fixture.checker_files(combined=True)
    p.locator('input[name="sheetSource"][value="folder"]').check()
    p.locator("#bulkInput").fill("C:/offline/copies")
    p.locator("#bulkCheck").click()
    expect(p.locator("#bulkPreview")).to_contain_text("1 to grade")
    p.locator("#evaluate").click()
    assert_selection(fixture.wait_post("/api/bulk/start"))
    checks.append("Folder grading carries selected pages and combined source")

    fixture.open()
    fixture.checker_files(combined=True)
    p.locator('input[name="keySource"][value="rubric"]').check()
    p.locator("#genRubric").click()
    expect(p.locator("#rubricText")).to_have_value("Offline draft rubric")
    assert fixture.last("/api/generate-rubric")["question_page_range"] == "1"
    assert fixture.last("/api/generate-rubric")["use_question_paper_as_key"] == "false"
    checks.append("Draft rubric reads the selected question pages")

    fixture.open()
    p.locator("#paperType").select_option("design")
    p.locator("#questionPageRange").fill("1")
    p.locator("#answerPageRange").fill("2-3")
    p.locator("#useQuestionPaperAsKey").check()
    p.locator("#qpFile").set_input_files(FILE("combined.pdf"))
    p.locator("#saFiles").set_input_files(FILE("student.pdf"))
    p.locator("#readDesignPaper").click()
    expect(p.locator("#designTable tbody tr")).to_have_count(1)
    assert_selection(fixture.last("/api/design/read-paper"))
    p.locator("#answerPageRange").fill("3")
    expect(p.locator("#designTable tbody tr")).to_have_count(0)
    p.locator("#answerPageRange").fill("2-3")
    fixture.hold_path = "/api/design/read-paper"
    p.locator("#readDesignPaper").click()
    p.wait_for_timeout(80)
    assert len(fixture.held) == 1
    p.locator("#questionPageRange").fill("2")
    fixture.held.pop().fulfill(content_type="application/json", body=json.dumps(DESIGN))
    expect(p.locator("#readDesignPaper")).to_be_enabled()
    expect(p.locator("#designTable tbody tr")).to_have_count(0)
    p.locator("#questionPageRange").fill("1")
    p.locator("#readDesignPaper").click()
    expect(p.locator("#designTable tbody tr")).to_have_count(1)
    p.locator("#evaluate").click()
    assert_selection(fixture.wait_post("/api/evaluate-design"))
    checks.append("Design reads and grades selected pages; stale design analysis is cleared")

    for combined in (False, True):
        fixture.open("/workflow.html")
        p.locator("#sheetUrl").fill("https://docs.google.com/spreadsheets/d/offline/edit")
        p.locator("#loadTabs").click()
        expect(p.locator("#sheetTab")).to_have_value("Copies")
        p.locator("#preview").click()
        expect(p.locator("#copies tr")).to_have_count(1)
        p.locator("#evaluator").fill("teacher@example.com")
        p.locator("#studentClass").fill("Class 10")
        p.locator("#subject").fill("Biology")
        p.locator("#paper").set_input_files(FILE("combined.pdf" if combined else "questions.pdf"))
        if combined:
            p.locator("#questionPageRange").fill("1")
            p.locator("#answerPageRange").fill("2-3")
            p.locator("#useQuestionPaperAsKey").check()
            expect(p.locator("#key")).to_be_disabled()
            assert p.locator("#key").evaluate("el => el.required") is False
        else:
            p.locator("#key").set_input_files(FILE("answers.pdf"))
        p.locator("#start").click()
        expect(p.locator("#message")).to_contain_text("Workflow started")
        fields = fixture.last("/api/workflow/start")
        assert_selection(fields, "1" if combined else "", "2-3" if combined else "", str(combined).lower())
        if not combined:
            assert fields["answer_key"] == "answers.pdf"
    checks.append("Integrated workflow starts with separate defaults or one combined PDF")

    p.screenshot(path=str(OUTPUT / "combined-workflow-desktop.png"), full_page=True)
    p.set_viewport_size({"width": 390, "height": 844})
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Workflow mobile overflow"
    p.screenshot(path=str(OUTPUT / "combined-workflow-mobile.png"), full_page=True)
    fixture.open()
    fixture.checker_files(combined=True)
    p.screenshot(path=str(OUTPUT / "combined-checker-mobile.png"), full_page=True)
    overflow = p.evaluate("[...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > window.innerWidth + 1).map(el => [el.tagName, el.id, Math.round(el.getBoundingClientRect().right)])")
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Checker mobile overflow: " + repr(overflow)
    p.set_viewport_size({"width": 1280, "height": 1000})
    p.screenshot(path=str(OUTPUT / "combined-checker-desktop.png"), full_page=True)
    assert not fixture.errors, fixture.errors
    assert not fixture.unexpected, fixture.unexpected
    fixture.context.close()
    browser.close()

report = {"passed": True, "checks": checks, "live_external_calls": 0}
(OUTPUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
