"""Student-sheet progress, with an on-disk outbox and memory-only Google tokens."""
from __future__ import annotations

import re
import threading
import time

import requests


STATUS_ALIASES = ("workflow_status", "automation_status", "ai_checker_status", "processing_status", "status")
TERMINAL_STATUSES = {"uploaded", "rejected", "not downloadable file", "already evaluated", "duplicate", "superseded"}
UNCERTAIN_STAGES = {"assignment_uncertain", "upload_uncertain", "rejection_uncertain"}


def normalized_status(value):
    return " ".join(re.sub(r"[_-]+", " ", str(value or "").casefold()).split())


def terminal_status(row):
    return normalized_status(row.get("workflow_status")) in TERMINAL_STATUSES


def status_for(row, run=None):
    stage = row.get("stage", "")
    if stage in ("uploaded", "already_evaluated"):
        return "uploaded"
    if stage == "rejection_submitted":
        # A copy whose file could not be read keeps its own word, although the portal now holds
        # a notice page for it: the sheet still says why no one evaluated it (20 Nov 2026).
        return "not downloadable file" if row.get("rejection_code") == "FILE_NOT_READABLE" else "rejected"
    if stage in ("duplicate", "superseded"):
        return stage
    if stage == "skipped" and not row.get("auto_retry"):
        return "already checked" if row.get("checked_before") else "not downloadable file"
    if stage in UNCERTAIN_STAGES:
        return "awaiting confirmation"
    if stage == "faculty_review":
        return "faculty review"
    if stage == "rejected" and row.get("error") == row.get("rejection_reason"):
        return "rejection pending"
    operation = str(row.get("operation") or "").casefold()
    error = str(row.get("error") or "")
    run_error = str((run or {}).get("error") or "").casefold()
    if "waiting for a portal login" in operation or "waiting for sign" in operation or "waiting for you to sign in" in run_error:
        return "not login"
    if "waiting for the portal" in operation:
        return "error occurred"
    if error or (run and run.get("status") == "failed"):
        return "not login" if any(word in error.casefold() for word in ("sign in", "not logged", "login")) else "error occurred"
    if stage == "rejected":
        return "rejection pending"
    if stage == "faculty_review":
        return "faculty review"
    if stage == "checked":
        return "checked, waiting to upload"   # build 146: checking goes on while uploads follow
    return "processing" if stage else row.get("workflow_status", "")


def retryable_saved_row(run, row, idle=False):
    """A new run of the test may take a safe failure, and any skipped copy (operator, 30 Oct 2026:
    running a test again picks up its skipped copies); an uncertain Submit belongs to its old run.
    `idle`: this app is not working on that run, so one never started or cut off may be taken
    too. A finished check goes with the copy (WorkflowStore._create), so nothing is paid twice."""
    if row.get("stage") in UNCERTAIN_STAGES or (run.get("status") in ("running", "ready") and not idle):
        return False
    if row.get("stage") == "skipped":
        return True
    return row.get("stage") in ("pending", "assigned", "downloaded", "checked", "rejected") and bool(
        idle or row.get("error") or run.get("error") or run.get("status") in ("failed", "stopped"))


class SheetStatusSync:
    """Save changes first; coalesce writes on a background worker, never replay portal work."""
    def __init__(self, store):
        self.store = store
        self.tokens = {}
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.thread = None
        self.pending = set()

    def connect(self, owner, token, expires_in=3500):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", token):
            raise ValueError("Connect Google Sheets again to save student statuses.")
        try:
            lifetime = max(1, min(float(expires_in), 3600))
        except (ValueError, TypeError):
            lifetime = 3500
        with self.lock:
            self.tokens[owner] = (token, time.monotonic() + lifetime)
        for path in self.store.root.glob("*/run.json"):
            try:
                run = self.store.read(path.parent.name)
                if run.get("owner") == owner and run.get("source_sheet"):
                    self.queue(run)
            except (OSError, ValueError):
                continue

    def queue(self, run):
        if not run.get("source_sheet"):
            return
        if all(row.get("stage") == "superseded" or row.get("workflow_status") == row.get("sheet_synced_status") for row in run["rows"]):
            return
        with self.lock:
            self.pending.add(run["id"])
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.work, name="workflow-sheet-status", daemon=True)
                self.thread.start()
        self.wake.set()

    def work(self):
        failures = {}
        while True:
            self.wake.wait(30)
            self.wake.clear()
            # Collect the copies completed by parallel workers into one write.
            time.sleep(2)
            with self.lock:
                ids = list(self.pending)
                self.pending.clear()
            for run_id in ids:
                try:
                    ok = self.flush(run_id)
                except Exception:
                    # The durable rows remain pending, and the next connection retries them.
                    ok = False
                if ok is False:
                    failures[run_id] = failures.get(run_id, 0) + 1
                    if failures[run_id] < 3:
                        with self.lock:
                            self.pending.add(run_id)
                # Eight account pairs share the same Google quota. Pace reads/writes
                # between batches while allowing many changed rows in each batch.
                if len(ids) > 1:
                    time.sleep(4)
            with self.lock:
                if not self.pending:
                    self.thread = None
                    return

    def flush(self, run_id):
        run = self.store.read(run_id)
        source = run.get("source_sheet")
        if not source:
            return True
        rows = [row for row in run["rows"] if row.get("stage") != "superseded" and row.get("workflow_status") != row.get("sheet_synced_status")]
        if not rows:
            return True
        with self.lock:
            token, expiry = self.tokens.get(run["owner"], ("", 0))
        if not token or time.monotonic() >= expiry:
            self.record(run_id, {}, "Connect Google Sheets to save pending student statuses.")
            return True  # reconnect, rather than an expired token, wakes these rows
        statuses = {row["submission_id"]: row["workflow_status"] for row in rows}
        try:
            write_statuses(source, rows, token)
        except Exception as exc:
            # Exceptions must never include request/response bodies containing credentials.
            self.record(run_id, {}, str(exc))
            return False
        self.record(run_id, statuses, "")
        return True

    def record(self, run_id, statuses, error):
        # Work on a fresh snapshot: a network call must not overwrite newer worker progress.
        with self.store.save_lock(run_id):
            run = self.store.read(run_id)
            for row in run["rows"]:
                if row["submission_id"] in statuses:
                    row["sheet_synced_status"] = statuses[row["submission_id"]]
                row["sheet_sync_error"] = error
            self.store.write_run(run, merge_sync=False)
        # Workers hold a separate in-memory run. write_run merges these sync fields.


def _post(document_id, resource, token, body):
    from integrated_workflow import GoogleSheetError
    endpoint = f"https://sheets.googleapis.com/v4/spreadsheets/{document_id}{resource}"
    for attempt in range(3):
        try:
            with requests.post(endpoint, headers={"Authorization": "Bearer " + token}, json=body,
                               timeout=(10, 60), allow_redirects=False) as response:
                code = response.status_code
                if code == 200 and not response.is_redirect:
                    return
                if code in (401, 403):
                    raise GoogleSheetError("Google Sheets could not save statuses. Reconnect with edit access to the student sheet.")
                if code == 429 or 500 <= code < 600:
                    if attempt < 2:
                        time.sleep((2, 5)[attempt])
                        continue
                raise GoogleSheetError("Google Sheets could not save statuses. Updates are pending and will retry after reconnecting.")
        except requests.RequestException:
            if attempt == 2:
                raise GoogleSheetError("Could not connect to Google Sheets. Status updates remain pending.") from None
            time.sleep((1, 3)[attempt])


def write_statuses(source, rows, token):
    """Resolve current IDs again, so a sorted/inserted row cannot get another student's result."""
    from integrated_workflow import (_google_api_json, _google_column_name, fetch_connected_google_metadata,
                                     ALIASES, MAX_BYTES, MAX_GOOGLE_COLUMNS, MAX_GOOGLE_PAGE_BYTES)
    descriptor = fetch_connected_google_metadata(source["source_url"], token)
    tab = next((tab for tab in descriptor["tabs"] if tab["gid"] == source["gid"]), None)
    if tab is None or tab["name"] != source["worksheet"]:
        raise ValueError("Student worksheet changed or was renamed. Load tabs again before saving statuses.")
    quoted = "'" + tab["name"].replace("'", "''") + "'"
    document_id = descriptor["document_id"]
    result = _google_api_json(document_id, token, {"ranges": [f"{quoted}!1:1"], "fields": "valueRanges(values)"},
                              MAX_GOOGLE_PAGE_BYTES, "/values:batchGet")
    headers = (result.get("valueRanges", [{}])[0].get("values") or [[]])[0]
    names = [str(header).strip().casefold() for header in headers]
    if len(names) > MAX_GOOGLE_COLUMNS:
        raise ValueError("Student worksheet has too many columns to save statuses safely.")
    identity_groups = []
    for key in ("submission_id", "student_id", "test_id"):
        columns = [i for i, name in enumerate(names) if name in ALIASES[key]]
        if not columns:
            raise ValueError("Student worksheet identity headings changed. Load tabs again.")
        identity_groups.append(columns)
    identity_columns = sorted({column for group in identity_groups for column in group})
    status_columns = [i for i, name in enumerate(names) if name in STATUS_ALIASES]
    if len(status_columns) > 1:
        raise ValueError("Student worksheet has multiple workflow status columns. Keep one workflow_status column.")
    status_column = status_columns[0] if status_columns else len(headers)
    if status_column >= MAX_GOOGLE_COLUMNS:
        raise ValueError("Student worksheet has no room for a workflow_status column. Remove unused columns and load tabs again.")
    extent = max(2, tab["row_count"])
    ranges = [f"{quoted}!{_google_column_name(column)}2:{_google_column_name(column)}{extent}" for column in identity_columns]
    result = _google_api_json(document_id, token, {"ranges": ranges, "fields": "valueRanges(values)",
                             "valueRenderOption": "UNFORMATTED_VALUE", "majorDimension": "ROWS"}, MAX_BYTES, "/values:batchGet")
    columns = [entry.get("values", []) for entry in result.get("valueRanges", [])]
    if len(columns) != len(identity_columns):
        raise ValueError("Google Sheets returned incomplete student identities. Statuses remain pending.")
    by_column = dict(zip(identity_columns, columns))
    targets = {}
    for index in range(max(map(len, columns), default=0)):
        identity = []
        for group in identity_groups:
            values = {str(by_column[column][index][0]).strip().casefold()
                      for column in group if index < len(by_column[column]) and by_column[column][index]
                      and str(by_column[column][index][0]).strip()}
            if len(values) > 1:
                raise ValueError("Student worksheet has conflicting repeated identity columns. Statuses remain pending.")
            identity.append(next(iter(values), ""))
        targets.setdefault(tuple(identity), []).append(index + 2)
    data = []
    for row in rows:
        identity = tuple(row[key].strip().casefold() for key in ("submission_id", "student_id", "test_id"))
        numbers = targets.get(identity)
        if not numbers:
            raise ValueError("A selected student submission is no longer in the sheet. Load tabs again; statuses remain pending.")
        for number in numbers:
            data.append({"range": f"{quoted}!{_google_column_name(status_column)}{number}", "values": [[row["workflow_status"]]]})
    if not status_columns:
        if status_column >= tab["column_count"]:
            _post(document_id, ":batchUpdate", token, {"requests": [{"appendDimension": {
                "sheetId": tab["gid"], "dimension": "COLUMNS", "length": status_column - tab["column_count"] + 1}}]})
        data.insert(0, {"range": f"{quoted}!{_google_column_name(status_column)}1", "values": [["workflow_status"]]})
    for first in range(0, len(data), 500):
        _post(document_id, "/values:batchUpdate", token, {"valueInputOption": "RAW", "data": data[first:first + 500]})
