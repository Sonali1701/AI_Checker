"""Student results are written by identity, with a durable outbox and no saved tokens."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import integrated_workflow as workflow
import workflow_sheet_status as status


TOKEN = "ya29.fixture_write_token"
SOURCE = {"source_url": "https://docs.google.com/spreadsheets/d/fixture/edit#gid=7",
          "gid": 7, "worksheet": "Students / John's"}
QUOTED = "'Students / John''s'"


def row(submission="copy1", student="student1", test="test1", value="uploaded"):
    return {"submission_id": submission, "student_id": student, "test_id": test,
            "workflow_status": value, "stage": "uploaded", "sheet_synced_status": ""}


def google(monkeypatch, *, headers=None, identities=None, columns=8, tabs=None):
    headers = headers if headers is not None else ["_id", "studentid", "testid", "testactivitystatus"]
    identities = identities if identities is not None else [("copy1", "student1", "test1")]
    descriptor = {"document_id": "fixture", "tabs": tabs if tabs is not None else [
        {"gid": 7, "name": SOURCE["worksheet"], "row_count": 30, "column_count": columns}]}
    calls, posts = [], []

    def metadata(url, token):
        assert url == SOURCE["source_url"] and token == TOKEN
        return copy.deepcopy(descriptor)

    def api(document_id, token, params, byte_limit, resource=""):
        assert document_id == "fixture" and token == TOKEN
        assert resource == "/values:batchGet"
        calls.append(copy.deepcopy(params))
        if len(calls) == 1:
            assert params["ranges"] == [QUOTED + "!1:1"]
            return {"valueRanges": [{"values": [headers]}]}
        identity_columns = [index for index, name in enumerate(headers)
                            if name in workflow.ALIASES["submission_id"] + workflow.ALIASES["student_id"]
                            + workflow.ALIASES["test_id"]]
        assert params["ranges"] == [f"{QUOTED}!{workflow._google_column_name(index)}2:"
                                    f"{workflow._google_column_name(index)}30" for index in identity_columns]
        return {"valueRanges": [{"values": [[identity[index]] if identity[index] else []
                                                for identity in identities]} for index in identity_columns]}

    monkeypatch.setattr(workflow, "fetch_connected_google_metadata", metadata)
    monkeypatch.setattr(workflow, "_google_api_json", api)
    monkeypatch.setattr(status, "_post", lambda *args: posts.append(copy.deepcopy(args)))
    return calls, posts


def test_sorted_and_inserted_rows_are_resolved_again_by_all_three_ids(monkeypatch):
    calls, posts = google(monkeypatch, identities=[("other", "s0", "t0"), ("", "", ""),
                                                   ("copy2", "student2", "test1"),
                                                   ("other2", "s2", "t2"), ("copy1", "student1", "test1")])
    status.write_statuses(SOURCE, [row(), row("copy2", "student2", value="rejected")], TOKEN)
    assert len(calls) == 2
    assert posts == [("fixture", "/values:batchUpdate", TOKEN, {"valueInputOption": "RAW", "data": [
        {"range": QUOTED + "!E1", "values": [["workflow_status"]]},
        {"range": QUOTED + "!E6", "values": [["uploaded"]]},
        {"range": QUOTED + "!E4", "values": [["rejected"]]},
    ]})]


@pytest.mark.parametrize("identities", [[("missing", "student1", "test1")],
                                         [("copy1", "someone_else", "test1")],
                                         [("copy1", "student1", "different_test")]])
def test_missing_or_changed_identity_never_writes_any_cells(monkeypatch, identities):
    _, posts = google(monkeypatch, identities=identities, columns=4)
    with pytest.raises(ValueError, match="no longer in the sheet"):
        status.write_statuses(SOURCE, [row()], TOKEN)
    assert posts == []


def test_one_missing_student_prevents_partial_status_writes(monkeypatch):
    _, posts = google(monkeypatch)
    with pytest.raises(ValueError, match="no longer in the sheet"):
        status.write_statuses(SOURCE, [row(), row("gone", "student2")], TOKEN)
    assert posts == []


@pytest.mark.parametrize("tabs", [[], [{"gid": 8, "name": SOURCE["worksheet"]}],
                                  [{"gid": 7, "name": "Renamed worksheet"}]])
def test_worksheet_identity_and_name_must_still_match(monkeypatch, tabs):
    calls, posts = google(monkeypatch, tabs=tabs)
    with pytest.raises(ValueError, match="changed or was renamed"):
        status.write_statuses(SOURCE, [row()], TOKEN)
    assert calls == [] and posts == []


def test_existing_status_heading_is_reused(monkeypatch):
    _, posts = google(monkeypatch, headers=["_id", "studentid", "testid", "automation_status"])
    status.write_statuses(SOURCE, [row(value="not downloadable file")], TOKEN)
    assert posts[0][3]["data"] == [{"range": QUOTED + "!D2", "values": [["not downloadable file"]]}]


def test_new_status_column_extends_a_full_grid_before_writing(monkeypatch):
    _, posts = google(monkeypatch, columns=4)
    status.write_statuses(SOURCE, [row()], TOKEN)
    assert posts[0] == ("fixture", ":batchUpdate", TOKEN, {"requests": [{"appendDimension": {
        "sheetId": 7, "dimension": "COLUMNS", "length": 1}}]})
    assert posts[1][1] == "/values:batchUpdate"
    assert posts[1][3]["data"][0] == {"range": QUOTED + "!E1", "values": [["workflow_status"]]}


def test_multiple_status_headings_fail_without_overwriting(monkeypatch):
    _, posts = google(monkeypatch, headers=["_id", "studentid", "testid", "workflow_status", "status"])
    with pytest.raises(ValueError, match="multiple workflow status columns"):
        status.write_statuses(SOURCE, [row()], TOKEN)
    assert posts == []


@pytest.mark.parametrize("identity", [("", "student1", "test1", "copy1", ""),
                                      ("copy1", "student1", "test1", "COPY1", "STUDENT1")])
def test_repeated_identity_headings_merge_consistent_nonempty_values(monkeypatch, identity):
    _, posts = google(monkeypatch, headers=["_id", "studentid", "testid", "submission_id", "student_id"],
                      identities=[identity])
    status.write_statuses(SOURCE, [row()], TOKEN)
    assert posts[0][3]["data"][-1] == {"range": QUOTED + "!F2", "values": [["uploaded"]]}


def test_conflicting_repeated_identity_values_never_write_statuses(monkeypatch):
    _, posts = google(monkeypatch, headers=["_id", "studentid", "testid", "submission_id"],
                      identities=[("copy1", "student1", "test1", "copy2")])
    with pytest.raises(ValueError, match="conflicting repeated identity"):
        status.write_statuses(SOURCE, [row()], TOKEN)
    assert posts == []


def test_creating_status_column_cannot_exceed_the_import_column_limit(monkeypatch):
    headers = ["_id", "studentid", "testid"] + ["unused"] * (workflow.MAX_GOOGLE_COLUMNS - 3)
    _, posts = google(monkeypatch, headers=headers, columns=workflow.MAX_GOOGLE_COLUMNS)
    with pytest.raises(ValueError, match="no room for a workflow_status column"):
        status.write_statuses(SOURCE, [row()], TOKEN)
    assert posts == []


class Response:
    def __init__(self, code):
        self.status_code, self.is_redirect = code, 300 <= code < 400

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_temporary_google_failures_retry_the_same_idempotent_values(monkeypatch):
    responses, requests_seen, delays = iter([429, 503, 200]), [], []

    def post(url, **kwargs):
        requests_seen.append((url, copy.deepcopy(kwargs)))
        return Response(next(responses))

    monkeypatch.setattr(status.requests, "post", post)
    monkeypatch.setattr(status.time, "sleep", delays.append)
    body = {"valueInputOption": "RAW", "data": [{"range": "Students!E2", "values": [["uploaded"]]}]}
    status._post("fixture", "/values:batchUpdate", TOKEN, body)
    assert len(requests_seen) == 3 and all(call == requests_seen[0] for call in requests_seen)
    assert delays == [2, 5]
    assert requests_seen[0][1]["headers"] == {"Authorization": "Bearer " + TOKEN}
    assert requests_seen[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("code", [401, 403, 302])
def test_access_denial_or_redirect_never_retries_or_forwards_the_token(monkeypatch, code):
    calls = []
    monkeypatch.setattr(status.requests, "post", lambda *args, **kwargs: calls.append(args) or Response(code))
    with pytest.raises(workflow.GoogleSheetError) as error:
        status._post("fixture", "/values:batchUpdate", TOKEN, {})
    assert len(calls) == 1 and TOKEN not in str(error.value)


def test_network_exception_text_never_exposes_google_token(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(args)
        raise requests.ConnectionError("sensitive request: " + TOKEN)

    monkeypatch.setattr(status.requests, "post", post)
    monkeypatch.setattr(status.time, "sleep", lambda seconds: None)
    with pytest.raises(workflow.GoogleSheetError) as error:
        status._post("fixture", "/values:batchUpdate", TOKEN, {})
    assert len(calls) == 3 and TOKEN not in str(error.value)


class DeferredThread:
    """Queue deterministically without running network activity on a background thread."""
    def __init__(self, target, **kwargs):
        self.target, self.started = target, False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(status.threading, "Thread", DeferredThread)
    store = workflow.WorkflowStore(tmp_path, lambda *args: {})
    saved = {"id": "a" * 32, "owner": "alice", "status": "completed", "source_sheet": copy.deepcopy(SOURCE),
             "rows": [row(), row("copy2", "student2", value="rejected"),
                      row("copy3", "student3", value="not downloadable file")]}
    store.write_run(saved)
    return store, saved, store.sheet_status


def test_connection_queues_pending_terminal_results_and_keeps_token_out_of_saved_runs(outbox):
    store, saved, sync = outbox
    sync.connect("alice", TOKEN)
    assert sync.pending == {saved["id"]} and sync.thread.started
    assert sync.tokens["alice"][0] == TOKEN
    assert TOKEN not in (store.folder(saved["id"]) / "run.json").read_text(encoding="utf-8")


def test_successful_flush_marks_results_synced_without_reprocessing_students(outbox, monkeypatch):
    store, saved, sync = outbox
    calls = []
    monkeypatch.setattr(status, "write_statuses", lambda source, rows, token: calls.append((source, rows, token)))
    sync.connect("alice", TOKEN)
    sync.flush(saved["id"])
    result = store.read(saved["id"])
    assert [r["sheet_synced_status"] for r in result["rows"]] == ["uploaded", "rejected", "not downloadable file"]
    assert [r["stage"] for r in result["rows"]] == ["uploaded"] * 3
    assert all(r["sheet_sync_error"] == "" for r in result["rows"])
    sync.flush(saved["id"])
    assert len(calls) == 1 and calls[0][2] == TOKEN


def test_failed_flush_remains_pending_and_reconnect_retries_saved_results(outbox, monkeypatch):
    store, saved, sync = outbox

    def fail(*args):
        raise ValueError("Google Sheets is busy; updates remain pending")

    monkeypatch.setattr(status, "write_statuses", fail)
    sync.connect("alice", TOKEN)
    sync.flush(saved["id"])
    result = store.read(saved["id"])
    assert all(r["sheet_synced_status"] == "" and "remain pending" in r["sheet_sync_error"] for r in result["rows"])
    sync.pending.clear()
    sync.connect("alice", TOKEN)
    assert saved["id"] in sync.pending
    monkeypatch.setattr(status, "write_statuses", lambda *args: None)
    sync.flush(saved["id"])
    assert all(r["sheet_synced_status"] == r["workflow_status"] and not r["sheet_sync_error"]
               for r in store.read(saved["id"])["rows"])


@pytest.mark.parametrize("owner, expired", [("bob", False), ("alice", True)])
def test_owner_or_expiry_mismatch_never_writes_with_another_owners_token(outbox, monkeypatch, owner, expired):
    store, saved, sync = outbox
    monkeypatch.setattr(status, "write_statuses", lambda *args: pytest.fail("unauthorized write"))
    sync.connect(owner, TOKEN)
    if expired:
        sync.tokens[owner] = (TOKEN, 0)
    sync.flush(saved["id"])
    assert all(r["sheet_synced_status"] == "" and "Connect Google Sheets" in r["sheet_sync_error"]
               for r in store.read(saved["id"])["rows"])


def test_sync_response_preserves_newer_student_progress(outbox, monkeypatch):
    store, saved, sync = outbox

    def progress_during_write(*args):
        newer = store.read(saved["id"])
        newer["rows"][0]["workflow_status"] = "rejected"
        newer["rows"][0]["stage"] = "rejection_submitted"
        store.write_run(newer)

    monkeypatch.setattr(status, "write_statuses", progress_during_write)
    sync.connect("alice", TOKEN)
    sync.flush(saved["id"])
    newer = store.read(saved["id"])["rows"][0]
    assert newer["stage"] == "rejection_submitted" and newer["workflow_status"] == "rejected"
    assert newer["sheet_synced_status"] == "uploaded"
    sync.queue(store.read(saved["id"]))
    assert saved["id"] in sync.pending


def test_worker_snapshot_does_not_erase_the_latest_sync_confirmation(outbox, monkeypatch):
    store, saved, sync = outbox
    stale_worker_snapshot = copy.deepcopy(saved)
    monkeypatch.setattr(status, "write_statuses", lambda *args: None)
    sync.connect("alice", TOKEN)
    sync.flush(saved["id"])
    stale_worker_snapshot["rows"][0]["note"] = "another worker saved progress"
    store.write_run(stale_worker_snapshot)
    current = store.read(saved["id"])["rows"][0]
    assert current["sheet_synced_status"] == "uploaded" and current["note"] == "another worker saved progress"


def test_superseded_failed_row_cannot_overwrite_new_run_while_old_run_saves_other_students(outbox, monkeypatch):
    store, saved, sync = outbox
    saved["rows"][0].update(stage="superseded", workflow_status="superseded", sheet_synced_status="error occurred")
    store.write_run(saved, merge_sync=False)
    replacement = {"id": "b" * 32, "owner": "alice", "status": "completed", "source_sheet": copy.deepcopy(SOURCE),
                   "rows": [row()]}
    store.write_run(replacement)
    calls = []
    monkeypatch.setattr(status, "write_statuses", lambda source, rows, token: calls.append(copy.deepcopy(rows)))
    sync.connect("alice", TOKEN)
    assert sync.pending == {saved["id"], replacement["id"]}
    sync.flush(replacement["id"])
    sync.flush(saved["id"])
    assert [[(r["submission_id"], r["workflow_status"]) for r in rows] for rows in calls] == [
        [("copy1", "uploaded")], [("copy2", "rejected"), ("copy3", "not downloadable file")]]
    previous = store.read(saved["id"])
    assert previous["rows"][0]["sheet_synced_status"] == "error occurred"
    assert [r["sheet_synced_status"] for r in previous["rows"][1:]] == ["rejected", "not downloadable file"]
    sync.pending.clear()
    sync.connect("alice", TOKEN)
    assert sync.pending == set()


def test_run_containing_only_superseded_rows_never_queues_or_flushes_statuses(outbox, monkeypatch):
    store, saved, sync = outbox
    for entry in saved["rows"]:
        entry.update(stage="superseded", workflow_status="superseded")
    store.write_run(saved)
    monkeypatch.setattr(status, "write_statuses", lambda *args: pytest.fail("superseded status must not be written"))
    sync.connect("alice", TOKEN)
    sync.queue(saved)
    sync.flush(saved["id"])
    assert sync.pending == set() and sync.thread is None

