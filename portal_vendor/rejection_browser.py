"""Bounded, state-aware browser recovery for rejected copies."""
from __future__ import annotations
# Vendored from Data_Filler v0.9.10; imports rewritten for the portal_vendor package.

import time
from contextlib import contextmanager
from pathlib import Path

from . import data_filler as engine

SEARCH_TIMEOUT_S = 45
SEARCH_ATTEMPTS = 2
POLL_MS = 150
LOADING = (engine.SEL['loading'] + ', [role="progressbar"], [aria-busy="true"], '
           '[data-testid*="loader" i], [data-testid*="loading" i], .animate-spin, '
           '[aria-label*="loading" i], svg:has(animate), svg:has(animateTransform)')
STATE_JS = '''([rows, loading]) => {
    const visible = el => !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
    return {
        loading: [...document.querySelectorAll(loading)].some(visible),
        text: [...document.querySelectorAll(rows)].filter(visible).map(el => el.innerText).join('\\n')
    };
}'''


@contextmanager
def requests_in_flight(page):
    """Watch requests started by this search, not unrelated previous page requests."""
    state = {'pending': set(), 'seen': False, 'failed': False}
    def started(request):
        if request.resource_type in ('xhr', 'fetch'):
            state['pending'].add(request)
            state['seen'] = True
    def finished(request):
        state['pending'].discard(request)
    def failed(request):
        if request in state['pending']:
            state['failed'] = True
        finished(request)
    def response_received(response):
        if response.request in state['pending'] and response.status >= 400:
            state['failed'] = True
    page.on('request', started)
    page.on('requestfinished', finished)
    page.on('requestfailed', failed)
    page.on('response', response_received)
    try:
        yield state
    finally:
        page.remove_listener('request', started)
        page.remove_listener('requestfinished', finished)
        page.remove_listener('requestfailed', failed)
        page.remove_listener('response', response_received)


def wait_search_ready(page, traffic) -> None:
    start = time.monotonic()
    stable_since = start
    signature = None
    previous_busy = None
    while time.monotonic() - start < SEARCH_TIMEOUT_S:
        if engine.should_stop():
            raise engine.AbortRun('Stopped by you')
        if traffic.get('failed'):
            raise TimeoutError('A search request failed; the displayed results cannot be trusted yet.')
        state = page.evaluate(STATE_JS, [engine.SEL['data_rows'], LOADING])
        now = time.monotonic()
        busy = bool(state['loading'] or traffic['pending'])
        if busy or busy != previous_busy or state['text'] != signature:
            stable_since = now
        signature = state['text']
        previous_busy = busy
        # Allow a debounced request to start even when the old table remains visible.
        quiet = 0.9 if traffic['seen'] else 3.0
        if not state['loading'] and not traffic['pending'] and now - stable_since >= quiet:
            return
        page.wait_for_timeout(POLL_MS)
    raise TimeoutError('Search is still loading after 45 seconds; student absence was not assumed.')


def search_rows(page, student_id: str, test: str, series: str):
    with requests_in_flight(page) as traffic:
        box = engine.search_box(page)
        box.fill(student_id)
        box.press('Enter')
        wait_search_ready(page, traffic)
    if engine.search_box(page).input_value().strip().lower() != student_id.lower():
        raise ValueError('The site did not retain the full Student ID search.')
    rows = page.locator(engine.SEL['data_rows']).filter(
        has=page.locator('td', has_text=engine.exact(test)))
    if series:
        rows = rows.filter(has=page.locator('td', has_text=engine.exact(series)))
    return rows


def reopen_pending(page, program: str) -> None:
    if engine.should_stop():
        raise engine.AbortRun('Stopped by you')
    page.reload(wait_until='domcontentloaded')
    engine.wait_for_manual_login(page)
    engine.open_evaluate(page, program)


def find_pending_row(page, program: str, series: str, test: str, student_id: str):
    for attempt in range(SEARCH_ATTEMPTS):
        if attempt:
            engine.log.info('Refreshing Pending and retrying search for %s (%d/%d)',
                            student_id, attempt + 1, SEARCH_ATTEMPTS)
            reopen_pending(page, program)
        try:
            rows = search_rows(page, student_id, test, series)
        except (TimeoutError, engine.PWTimeout):
            if attempt + 1 == SEARCH_ATTEMPTS:
                raise
            continue
        count = rows.count()
        if count == 1:
            return rows.first
        if count > 1:
            raise LookupError(f'Duplicate website records: {count} Pending rows match this Student ID and exact '
                              'test/category. No row chosen; resolve the duplicate task or specify its category.')
    # A completed search twice returned no target. Read the other statuses; do not change them.
    found = []
    for tab in ('In Review', 'Evaluated', 'Re Assigned'):
        control = page.get_by_text(tab, exact=True).first
        if not control.is_visible():
            continue
        with requests_in_flight(page) as traffic:
            control.click()
            wait_search_ready(page, traffic)
        rows = search_rows(page, student_id, test, series)
        if rows.count():
            found.append(tab)
    if found:
        raise LookupError('Not Pending: matching test is in ' + ', '.join(found)
                          + '. No upload or resubmission attempted.')
    raise LookupError('Matching test was not found in the available evaluation statuses after a fresh-page retry. '
                      'Check assignment to this account and the exact test/category. No upload attempted.')


def has_view_upload(page, row) -> bool:
    engine.close_row_menu(page)
    engine.open_row(row)
    page.get_by_text('Model Answer', exact=True).first.wait_for(state='visible', timeout=5000)
    present = page.get_by_text('View Upload', exact=True).first.is_visible()
    engine.close_row_menu(page)
    return present


def upload_paper(page, row, path: Path, restore_row):
    """Retry pre-file-selection failures once; verify uncertain uploads without resending."""
    if not path.is_file():
        raise FileNotFoundError(f'Paper not found: {path}')
    for attempt in range(2):
        selected = False
        previous_upload = True  # Unknown baseline must never count as a new-upload transition.
        try:
            previous_upload = has_view_upload(page, row)
            page.locator(engine.SEL['upload_success']).first.wait_for(
                state='hidden', timeout=engine.TOAST_CLEAR_TIMEOUT_MS)
            if engine.should_stop():
                raise engine.AbortRun('Stopped before uploading')
            try:
                with page.expect_file_chooser(timeout=5000) as chooser:
                    engine.click_menu_item(page, row, 'Upload')
            except engine.PWTimeout:
                inputs = page.locator(engine.SEL['file_input'])
                if inputs.count() != 1:
                    raise LookupError('The upload control did not open a file chooser or one file input.')
                selected = True
                inputs.set_input_files(str(path.resolve()))
            else:
                selected = True
                chooser.value.set_files(str(path.resolve()))
            page.locator(engine.SEL['upload_success']).first.wait_for(
                state='visible', timeout=engine.UPLOAD_TIMEOUT_MS)
            engine.close_row_menu(page)
            return row
        except (engine.PWTimeout, LookupError) as exc:
            if engine.should_stop():
                raise engine.AbortRun('Stopped by you') from exc
            if not selected:
                if attempt:
                    raise RuntimeError('Upload controls failed twice before file selection: ' + engine.first_line(exc)) from exc
                engine.log.warning('Upload controls not ready for %s; reopening this student once.', path.name)
                row = restore_row()
                continue
            # File selection may already have sent the PDF. Never resend it automatically.
            engine.log.warning('Upload confirmation timed out for %s; checking the refreshed student menu.', path.name)
            row = restore_row()
            if not previous_upload and has_view_upload(page, row):
                engine.log.info('Upload verified by new View Upload action for %s; PDF was not resent.', path.name)
                return row
            raise RuntimeError('PDF was selected but upload success could not be verified. '
                               'No automatic re-upload or evaluation submission was attempted. '
                               + engine.first_line(exc)) from exc
