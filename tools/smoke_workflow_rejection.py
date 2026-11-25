"""Local Chrome integration check: no network, login, or real student data."""
import sys
import tempfile
import time
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from portal_vendor import data_filler as engine
from portal_vendor import rejection_browser as adaptive
from portal_vendor.rejection_comments import RejectionComment, submit_rejection

HTML = '''<!doctype html><html><body>
<h1>Evaluate</h1><input placeholder="Search">
<table><tbody><tr><td>Student Name</td><td>Batch</td><td>Test</td><td>
<button onclick="menu.hidden=false">Actions</button></td></tr></tbody></table>
<div id="menu" hidden><span>Model Answer</span>
<button onclick="document.getElementById('file').click()">Upload</button>
<button onclick="menu.hidden=true;document.getElementById('form').hidden=false">View Upload</button></div>
<input id="file" type="file" hidden onchange="window.uploads++;toast.hidden=false;setTimeout(()=>toast.hidden=true,150)">
<div id="toast" hidden>File uploaded</div>
<div id="form" role="dialog" hidden><h2>Uploaded Pdf</h2>
<form onsubmit="event.preventDefault();window.submits++;document.getElementById('success').hidden=false">
<button type="button">Select Marks</button><input id="marks" required placeholder="Total marks" value="">
<button type="button">Rate Student</button><input id="rating" value="">
<textarea></textarea><button type="submit">Submit</button></form>
</div><div id="success" hidden>Evaluated successfully</div>
<script>window.uploads=0;window.submits=0;window.edits=[];
document.addEventListener('input',e=>window.edits.push(e.target.tagName+':'+e.target.id));
document.addEventListener('keydown',e=>{if(e.key==='Escape')menu.hidden=true});</script>
</body></html>'''


def main():
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(engine.find_chrome()), headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_default_timeout(5000)
            for initial_marks, initial_rating in [('', ''), ('7', '3')]:
                page.set_content(HTML)
                page.eval_on_selector('#marks', '(el,v)=>el.value=v', initial_marks)
                page.eval_on_selector('#rating', '(el,v)=>el.value=v', initial_rating)
                sid = 'a' * 24
                pdf = Path(tmp, sid + '.pdf')
                pdf.write_bytes(b'%PDF-1.4\n%%EOF')
                record = RejectionComment(sid, 'Student Name', 'Wrong subject\nPlease resubmit.', 'reason.txt', pdf)
                markers = []
                submit_rejection(page, record, 'Program', 'Batch', 'Test', before_submit=lambda: markers.append('attempt'))
                assert page.locator('#marks').input_value() == '0'
                assert page.locator('#rating').input_value() == initial_rating
                assert page.locator('textarea').input_value() == record.comment
                assert page.evaluate('window.uploads') == 1
                assert page.evaluate('window.submits') == 1
                assert not any(x.endswith(':rating') for x in page.evaluate('window.edits'))
                assert markers == ['attempt']
            page.set_content(HTML)
            page.locator('form').evaluate("form => { const x=document.createElement('input'); x.required=true; x.placeholder='Extra required field'; form.append(x); }")
            markers = []
            try:
                submit_rejection(page, record, 'Program', 'Batch', 'Test', before_submit=lambda: markers.append('attempt'))
            except RuntimeError as exc:
                assert 'Website validation blocked Submit' in str(exc)
            else:
                raise AssertionError('Missing required field was submitted')
            assert markers == []
            assert page.evaluate('window.submits') == 0
            page.set_content(HTML)
            page.evaluate("""() => {
                const spinner = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                spinner.innerHTML = '<animate attributeName="opacity" values="0;1;0" dur="1s" repeatCount="indefinite"/>';
                spinner.style.display = 'none';
                document.body.append(spinner);
                const body = document.querySelector('tbody');
                const rows = body.innerHTML;
                document.querySelector('input[placeholder="Search"]').oninput = () => {
                    body.innerHTML = ''; spinner.style.display = 'block';
                    setTimeout(() => {body.innerHTML=rows;spinner.style.display='none'}, 6500);
                };
            }""")
            started = time.monotonic()
            rows = adaptive.search_rows(page, sid, 'Test', 'Batch')
            assert rows.count() == 1
            assert time.monotonic() - started >= 6.5
            # A missed success toast is recovered from a newly available View Upload action.
            page.set_content(HTML)
            view = page.get_by_text('View Upload', exact=True)
            view.evaluate("el => {el.id='view-upload';el.hidden=true}")
            page.locator('#file').evaluate("el => el.onchange=()=>{window.uploads++;document.getElementById('view-upload').hidden=false}")
            row = page.locator('tbody tr')
            with patch.object(engine, 'UPLOAD_TIMEOUT_MS', 300):
                adaptive.upload_paper(page, row, pdf, lambda: row)
            assert page.evaluate('window.uploads') == 1
            assert page.evaluate('window.submits') == 0
            print('PASS: required-field validation, zero total, delayed SVG spinner search, and missed-toast upload recovery; no duplicate upload.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
