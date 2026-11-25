"""Exercise the actual portal adapter against an offline Chrome DOM fixture.

No portal URLs, logins, assignments, or uploads are contacted. Search alone is
replaced with a no-op to simulate stale results; all tested adapter methods run
unchanged against browser elements and a local PDF file chooser.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".workflow-test-portal"
OUTPUT.mkdir(exist_ok=True)
os.environ["TEMP"] = str(OUTPUT)
os.environ["TMP"] = str(OUTPUT)
sys.path.insert(0, str(ROOT))

import fitz
from playwright.sync_api import sync_playwright
from portal_vendor import data_filler as engine


STYLE = """<style>
body {font:16px Arial;padding:24px} td {padding:8px;border:1px solid #ddd}
label {display:block;padding:6px} button,input,select,textarea {margin:5px;padding:6px}
[role=dialog] {border:1px solid #999;padding:16px;width:560px}
#menu {padding:10px;border:1px solid #bbb;width:180px}
</style>"""


def record(**updates):
    values = dict(program="Program", test_series="Batch Tests", test_name="Maths Test",
                  student_id="student123", evaluator="teacher@example.com",
                  deadline=(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                  total_marks="5", comment="Check the final arithmetic.", marks={1: "2", 2: "3"})
    values.update(updates)
    return engine.StudentRecord(**values)


def table(student="student123", test="Maths Test", series="Batch Tests", actions=False):
    first = '<input type="checkbox" id="studentCheck">' if not actions else ""
    last = '<button id="actions" onclick="showMenu()">⋮</button>' if actions else "Submitted"
    return (f'<table><thead><tr><th>Student</th><th>Test</th><th>Category</th><th>Action</th></tr></thead>'
            f'<tbody><tr><td>{first}{student}</td><td>{test}</td><td>{series}</td><td>{last}</td></tr></tbody></table>')


def allotment_fixture(student="student123", test="Maths Test", series="Batch Tests"):
    return STYLE + table(student, test, series) + """
    <select id="evaluator">
      <option value="wrong">Other (xteacher@example.com)</option>
      <option value="right">Correct (Teacher@Example.com)</option>
      <option value="other">Other domain (teacher@example.com.other)</option>
    </select>
    <input type="datetime-local" id="deadline">
    <button id="assignSubmit" onclick="window.assignment={checked:studentCheck.checked,
      evaluator:evaluator.value,deadline:deadline.value};
      document.getElementById('assignmentToast').innerText='Allotted successfully'">Submit</button>
    <div id="assignmentToast" role="alert"></div>
    """


MODAL = """
<div id="evaluationModal" role="dialog" style="display:none">
  <h2 onclick="marksDropdown.hidden=true">Uploaded Pdf</h2>
  <button onclick="marksDropdown.hidden=false">Select Marks</button>
  <div id="marksDropdown" hidden>
    <label>Q.<span>10</span><input id="q10" value="untouched"></label>
    <label>Q.<span>1</span><input id="q1"></label>
    <label>Q.<span>2</span><input id="q2"></label>
  </div>
  <input id="total" placeholder="Total marks">
  <textarea id="comment"></textarea>
  <button id="evaluationSubmit" onclick="window.evaluation={q1:q1.value,q2:q2.value,
    q10:q10.value,total:total.value,comment:comment.value};
    document.getElementById('evaluationToast').innerText='Evaluated successfully'">Submit</button>
  <button aria-label="close" onclick="evaluationModal.style.display='none'">×</button>
</div>
<div id="evaluationToast" role="alert"></div>
"""


def evaluation_fixture():
    return STYLE + "<h1>Evaluate</h1>" + table(actions=True) + """
    <div id="menu" style="display:none">
      <button>Model Answer</button>
      <button onclick="document.getElementById('checkedFile').click()">Upload</button>
      <button id="viewUpload" hidden onclick="menu.style.display='none';
        evaluationModal.style.display='block'">View Upload</button>
    </div>
    <input type="file" id="checkedFile" style="display:none" onchange="
      window.uploadedFile=checkedFile.files[0].name;
      document.getElementById('uploadToast').innerText='File uploaded';
      document.getElementById('viewUpload').hidden=false">
    <div id="uploadToast" role="alert"></div>
    <script>
      function showMenu(){document.getElementById('menu').style.display='block'}
      document.addEventListener('keydown',event=>{
        if(event.key==='Escape')document.getElementById('menu').style.display='none';
      });
    </script>
    """ + MODAL


def reject(call, description):
    try:
        call()
    except LookupError:
        return
    raise AssertionError(f"Unsafe selection accepted: {description}")


def assert_evaluation(page, rec):
    assert page.evaluate("window.evaluation") == {
        "q1": "2", "q2": "3", "q10": "untouched", "total": "5", "comment": rec.comment}
    assert not page.locator("#evaluationModal").is_visible()


def main():
    tests = []
    requests = []
    original_search = engine.search
    engine.search = lambda *_: None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True,
                                         args=["--disable-background-networking", "--disable-component-update"])
            try:
                context = browser.new_context(offline=True, viewport={"width": 1100, "height": 900})
                def reject_request(route):
                    requests.append(route.request.url)
                    route.abort()
                context.route("**/*", reject_request)
                context.set_default_timeout(4000)
                page = context.new_page()
                rec = record()

                page.set_content(allotment_fixture())
                engine.select_evaluator(page, rec.evaluator)
                assert page.locator("#evaluator").input_value() == "right"
                tests.append("exact evaluator email collision")

                page.set_content(allotment_fixture(student="wrong_student"))
                reject(lambda: engine.allot_student(page, rec, False), "stale wrong student assignment")
                assert not page.locator("#studentCheck").is_checked()
                assert page.evaluate("window.assignment === undefined")
                reject(lambda: engine.find_evaluate_row(page, rec), "stale wrong student upload")
                tests.append("stale wrong student rejected before mutation")

                for field in ("test", "series"):
                    options = {field: "Maths Test 2" if field == "test" else "Batch Tests 2"}
                    page.set_content(allotment_fixture(**options))
                    reject(lambda: engine.find_evaluate_row(page, rec), f"suffixed {field}")
                    assert not page.locator("#studentCheck").is_checked()
                tests.append("suffixed test and category rejected")

                page.set_content(STYLE + table(test="Maths Test 2") + table(test=" Maths-Test "))
                assert "Maths-Test" in engine.row_named(page, "Maths Test").inner_text()
                tests.append("whole-label navigation")

                page.set_content(allotment_fixture())
                assert engine.allot_student(page, rec, False) is True
                assert page.evaluate("window.assignment") == {
                    "checked": True, "evaluator": "right", "deadline": rec.deadline}
                page.screenshot(path=str(OUTPUT / "allotment-fixture.png"), full_page=True)
                tests.append("allot_student checkbox evaluator deadline Submit toast")

                page.set_content(STYLE + MODAL)
                page.locator("#evaluationModal").evaluate("element=>element.style.display='block'")
                engine.fill_evaluation_modal(page, rec, False)
                assert_evaluation(page, rec)
                tests.append("marks modal Q.1 Q.2 total comment and success toast")

                checked_pdf = OUTPUT / "checked_student123.pdf"
                with fitz.open() as doc:
                    doc.new_page().insert_text((40, 40), "Offline portal adapter checked-copy fixture")
                    doc.save(checked_pdf)
                rec.paper_path = checked_pdf
                page.set_content(evaluation_fixture())
                engine.evaluate_student(page, context, rec, False)
                assert page.evaluate("window.uploadedFile") == checked_pdf.name
                assert_evaluation(page, rec)
                page.screenshot(path=str(OUTPUT / "evaluation-fixture.png"), full_page=True)
                tests.append("evaluate_student local file chooser menu and final marks submission")
                assert not requests, f"Offline fixture unexpectedly requested URLs: {requests}"
            finally:
                browser.close()
    finally:
        engine.search = original_search
    result = {"passed": len(tests), "tests": tests, "network_requests": requests,
              "output": str(OUTPUT)}
    (OUTPUT / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
