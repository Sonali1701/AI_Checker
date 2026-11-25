"""Offline Chrome: failed lists, shared cooldown, response diagnostics, stale toast recovery."""
from pathlib import Path
import json
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import integrated_workflow as workflow
from portal_vendor import data_filler as engine
from portal_recovery import FetchRecovery

HTML = """<!doctype html><script>history.replaceState(null,'','/tev/dashboard');</script>
<button>Task Management</button><button onclick="load()">Allotment</button><h1>Programs</h1>
<input placeholder="Search" oninput="load()" onkeydown="if(event.key==='Enter')load()">
<div id="toast">Unable to fetch task. Please try again later</div><table><tbody id="rows"></tbody></table>
<script>
async function load(){
 const response=await fetch('https://special-ops-content-backend.penpencil.co/workflows/tasks');
 document.querySelector('#rows').innerHTML=response.ok?'<tr><td>Program</td><td><button onclick="window.opened=true">Open</button></td></tr>':'';
}
</script>"""


def main():
    requests = []
    status = [503]
    clock = [100.0]
    with tempfile.TemporaryDirectory(prefix="fetch-recovery-") as temp, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(engine.find_chrome()), headless=True)
        context = browser.new_context()
        def route(request):
            url = request.request.url
            if url.startswith("https://special-ops-content-backend.penpencil.co/"):
                requests.append(url)
                request.fulfill(status=status[0], content_type="application/json", body='{}',
                                headers={"Access-Control-Allow-Origin": "*", "Retry-After": "60"})
            elif url.startswith(engine.BASE_URL):
                request.fulfill(status=200, content_type="text/html", body=HTML)
            else:
                request.abort()
        context.route("**/*", route)
        page = context.new_page()
        page.goto(engine.BASE_URL)
        portal = workflow.Portal.__new__(workflow.Portal)
        portal.engine, portal.admin, portal.evaluator, portal.board = engine, "fixture@example.com", "", None
        portal.session = lambda kind: {"page": page, "context": context}
        recovery = FetchRecovery(lambda: clock[0])
        portal.fetch_recovery = {"admin": recovery}
        portal.assign_copy = lambda *args: {"result": "assigned"}
        row = {"program_name": "Program", "test_series_name": "Series", "test_name": "Test", "student_id": "fixture"}
        options = {"evaluator_email": "eval@example.com", "deadline": "2027-01-01T10:00"}
        with engine.runtime_context(SCREENSHOT_DIR=Path(temp)), \
                patch.object(engine, "open_allotment_test", lambda page, record: engine.open_program(page, "Allotment", record.program)), \
                patch("portal_recovery.random.uniform", return_value=0):
            try:
                portal._perform("assign", row, options)
                raise AssertionError("failed task list was accepted")
            except workflow.PortalFetchError as exc:
                assert "HTTP 503" in str(exc) and exc.retry_after == 60, str(exc)
            count = len(requests)
            for number in range(8):
                try:
                    portal._perform("assign", {**row, "student_id": str(number)}, options)
                    raise AssertionError("cooldown was bypassed")
                except workflow.PortalFetchError:
                    pass
            assert len(requests) == count
            status[0], clock[0] = 200, 160
            assert portal._perform("assign", row, options) == {"result": "assigned"}
            assert page.evaluate("window.opened") is True
            assert page.locator("#toast").is_visible()  # an old toast does not block the recovered row
            assert recovery.remaining() == recovery.failures == 0
        browser.close()
    print(json.dumps({"offline_chrome": "PASS", "queued_copies_during_cooldown": 8,
                      "extra_requests_during_cooldown": 0, "observed_status": 503,
                      "retry_after_honored": True, "stale_toast_recovery": True}))


if __name__ == "__main__":
    main()
