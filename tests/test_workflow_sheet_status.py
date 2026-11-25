import threading
import time

import pytest

import integrated_workflow as workflow
from workflow_sheet_status import retryable_saved_row, status_for, terminal_status
from test_integrated_workflow import FakePortal, options, pdf, row, store


@pytest.mark.parametrize("value", ["uploaded", " UPLOADED ", "rejected", "Not Downloadable File",
                                  "not_downloadable_file", "already-evaluated", "duplicate", "superseded"])
def test_completed_sheet_status_is_recognized(value):
    assert terminal_status({"workflow_status": value})


@pytest.mark.parametrize("value", ["", "error occurred", "error occured", "not login", "processing", "awaiting confirmation"])
def test_retry_or_pending_status_is_not_a_completed_sheet_status(value):
    assert not terminal_status({"workflow_status": value})


@pytest.mark.parametrize("row_data, expected", [
    ({"stage": "uploaded", "error": "old error"}, "uploaded"),
    ({"stage": "already_evaluated"}, "uploaded"),
    ({"stage": "rejection_submitted"}, "rejected"),
    ({"stage": "skipped", "auto_retry": False}, "not downloadable file"),
    ({"stage": "skipped", "auto_retry": True, "error": "proxy unavailable"}, "error occurred"),
    ({"stage": "checked", "error": "not logged in"}, "not login"),
    ({"stage": "assigned", "operation": "waiting for a portal login: account"}, "not login"),
    ({"stage": "upload_uncertain", "error": "confirmation lost"}, "awaiting confirmation"),
    ({"stage": "rejection_uncertain"}, "awaiting confirmation"),
    ({"stage": "pending"}, "processing"),
])
def test_copy_state_maps_to_rerun_status(row_data, expected):
    assert status_for(row_data) == expected


@pytest.mark.parametrize("run_state, copy_state, expected", [
    ({"status": "complete"}, {"stage": "uploaded"}, False),
    ({"status": "complete"}, {"stage": "skipped", "auto_retry": False}, True),
    ({"status": "complete"}, {"stage": "skipped", "auto_retry": True}, True),
    ({"status": "running"}, {"stage": "skipped", "auto_retry": True}, False),
    ({"status": "ready"}, {"stage": "pending", "error": "network"}, False),
    ({"status": "stopped"}, {"stage": "checked", "error": "network"}, True),
    ({"status": "failed"}, {"stage": "pending"}, True),
    ({"status": "complete"}, {"stage": "assigned", "error": "not login"}, True),
    ({"status": "failed"}, {"stage": "upload_uncertain", "error": "lost confirmation"}, False),
    ({"status": "failed"}, {"stage": "rejection_uncertain", "error": "lost confirmation"}, False),
    ({"status": "failed"}, {"stage": "assignment_uncertain", "error": "lost confirmation"}, False),
    ({"status": "complete"}, {"stage": "faculty_review", "error": "needs teacher"}, False),
])
def test_only_safe_failed_saved_copies_can_move_to_a_new_run(run_state, copy_state, expected):
    assert retryable_saved_row(run_state, copy_state) is expected


def test_uploaded_copy_cleans_large_files_and_preserves_report_and_marks(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    completed = saved["rows"][0]
    source, checked, report = s.copy_paths(saved, completed["submission_id"])
    assert completed["stage"] == completed["workflow_status"] == "uploaded"
    assert completed["files_cleaned"] is True and completed["has_checked_pdf"] is False
    assert not source.exists() and not checked.exists()
    assert report.exists() and completed["awarded_marks"] == 9
    assert (s.folder(run["id"]) / "question_paper.pdf").exists()
    assert (s.folder(run["id"]) / "answer_key.pdf").exists()
    before = list(FakePortal.events)
    s.work(run["id"], "token")
    assert FakePortal.events == before


def test_permanently_undownloadable_copy_is_not_retried_on_resume(tmp_path):
    s = store(tmp_path)
    def unavailable(url, target):
        FakePortal.events.append("download")
        raise workflow.UnreadableCopy("Downloaded file is not a readable PDF")
    s.downloader = unavailable
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["workflow_status"] == "not downloadable file"
    s.downloader = lambda url, target: pytest.fail("A terminal file should not download again")
    s.work(run["id"], "token")
    # 20 Nov 2026: a file that cannot be read gets a notice page posted in its place, so the
    # copy leaves the portal's pending list. It is still never downloaded a second time.
    assert FakePortal.events == ["assign", "download", "reject"]
    assert s.read(run["id"])["rows"][0]["stage"] == "rejection_submitted"


def test_sheet_rerun_skips_completed_rows_and_can_retry_safe_previous_errors(tmp_path):
    s = store(tmp_path)
    first = s.create([row()], options(), "owner", pdf(), pdf())
    first["status"] = "complete"
    first["rows"][0].update(stage="skipped", skipped_from="downloaded", auto_retry=True, error="proxy unavailable")
    s.save(first)
    second_row = row()
    second_row["workflow_status"] = "error occurred"
    finished_row = row()
    finished_row.update(submission_id="completed-copy", student_id="completed-student", workflow_status="uploaded")
    second = s.create([second_row, finished_row], options(), "owner", pdf(), pdf())
    assert [r["submission_id"] for r in second["rows"]] == ["copy123"]
    old = s.read(first["id"])["rows"][0]
    assert old["stage"] == "superseded" and old["replaced_by"] == second["id"]
    s.work(second["id"], "token")
    assert s.read(second["id"])["rows"][0]["stage"] == "uploaded"


def test_sheet_rerun_cannot_repeat_an_unknown_portal_submit(tmp_path):
    s = store(tmp_path)
    first = s.create([row()], options(), "owner", pdf(), pdf())
    first["status"] = "needs_attention"
    first["rows"][0].update(stage="upload_uncertain", error="confirmation lost")
    s.save(first)
    retry = row()
    retry["workflow_status"] = "error occurred"
    with pytest.raises(ValueError, match="saved workflow"):
        s.create([retry], options(), "owner", pdf(), pdf())


def test_old_retry_files_are_removed_only_after_replacement_is_saved(tmp_path):
    from workflow_cleanup import cleanup_copy_artifacts

    s = store(tmp_path)
    first = s.create([row()], options(), "owner", pdf(), pdf())
    source, checked, report = s.copy_paths(first, "copy123")
    source.write_bytes(b"downloaded old copy")
    checked.write_bytes(b"checked old copy")
    first["status"] = "complete"
    first["rows"][0].update(stage="checked", error="upload temporarily failed")
    s.save(first)
    assert source.exists() and checked.exists()
    unfinished_migration = {**first["rows"][0], "stage": "superseded", "replaced_by": "a" * 32}
    assert not cleanup_copy_artifacts(s, first, unfinished_migration)["eligible"]
    assert source.exists() and checked.exists()
    second = s.create([row()], options(), "owner", pdf(), pdf())
    assert (s.folder(second["id"]) / "run.json").exists()
    assert s.read(first["id"])["rows"][0]["files_cleaned"] is True
    assert not source.exists() and not checked.exists()


def test_safe_failure_cannot_move_while_its_prior_run_is_still_active(tmp_path):
    s = store(tmp_path)
    first = s.create([row()], options(), "owner", pdf(), pdf())
    first["status"] = "complete"
    first["rows"][0].update(stage="skipped", auto_retry=True, error="network failure")
    s.save(first)
    s.active[first["id"]] = {"stop": threading.Event(), "evaluator": "teacher@example.com"}
    with pytest.raises(ValueError, match="saved workflow"):
        s.create([row()], options(), "owner", pdf(), pdf())
    assert len(list(tmp_path.glob("*/run.json"))) == 1
    assert s.read(first["id"])["rows"][0]["stage"] == "skipped"


def test_repeated_error_reruns_follow_latest_run_past_superseded_history(tmp_path):
    s = store(tmp_path)
    first = s.create([row()], options(), "owner", pdf(), pdf())
    first["status"] = "complete"
    first["rows"][0].update(stage="skipped", auto_retry=True, error="first network failure")
    s.save(first)
    second = s.create([row()], options(), "owner", pdf(), pdf())
    second["status"] = "complete"
    second["rows"][0].update(stage="skipped", auto_retry=True, error="second network failure")
    s.save(second)
    third = s.create([row()], options(), "owner", pdf(), pdf())
    assert s.read(first["id"])["rows"][0]["replaced_by"] == second["id"]
    assert s.read(second["id"])["rows"][0]["replaced_by"] == third["id"]
    assert third["rows"][0]["stage"] == "pending"


def test_retry_subset_preserves_old_source_and_syncs_unselected_completed_students(tmp_path, monkeypatch):
    import workflow_sheet_status

    s = store(tmp_path)
    monkeypatch.setattr(s.sheet_status, "queue", lambda run: None)
    source_sheet = {"source_url": "https://docs.google.com/spreadsheets/d/student-sheet/edit",
                    "worksheet": "Students", "gid": 0}
    other = row()
    other.update(submission_id="other-copy", student_id="other-student")
    first = s.create([row(), other], options(), "owner", pdf(), pdf(), source_sheet)
    first["status"] = "complete"
    first["rows"][0].update(stage="skipped", auto_retry=True, error="network failure")
    first["rows"][1].update(stage="uploaded", sheet_synced_status="", error="")
    s.save(first)
    second = s.create([row()], options(), "owner", pdf(), pdf(), source_sheet)
    previous = s.read(first["id"])
    assert previous["source_sheet"] == source_sheet
    assert previous["rows"][0]["replaced_by"] == second["id"]
    writes = []
    monkeypatch.setattr(workflow_sheet_status, "write_statuses", lambda source, rows, token: writes.extend(rows))
    s.sheet_status.tokens["owner"] = ("offline-token", time.monotonic() + 100)
    s.sheet_status.flush(first["id"])
    assert [(r["submission_id"], r["workflow_status"]) for r in writes] == [("other-copy", "uploaded")]
    assert s.read(first["id"])["rows"][1]["sheet_synced_status"] == "uploaded"


def test_resume_recovers_saved_retry_claim_after_old_retirement_save_failed(tmp_path, monkeypatch):
    s = store(tmp_path)
    first = s.create([row()], options(), "owner", pdf(), pdf())
    source, checked, report = s.copy_paths(first, "copy123")
    source.write_bytes(b"downloaded retry copy")
    checked.write_bytes(b"checked retry copy")
    first["status"] = "complete"
    first["rows"][0].update(stage="skipped", skipped_from="checked", auto_retry=True, error="upload timeout")
    s.save(first)
    save = s.save

    def fail_old_retirement(run):
        if run["id"] == first["id"] and run["rows"][0]["stage"] == "superseded":
            raise OSError("disk failed between replacement save and retirement save")
        return save(run)

    monkeypatch.setattr(s, "save", fail_old_retirement)
    with pytest.raises(OSError, match="between replacement"):
        s.create([row()], options(), "owner", pdf(), pdf())
    [replacement_path] = [path for path in tmp_path.glob("*/run.json") if path.parent.name != first["id"]]
    replacement = s.read(replacement_path.parent.name)
    assert replacement["retry_from"] == {first["id"]: ["copy123"]}
    assert s.read(first["id"])["rows"][0]["stage"] == "skipped"
    assert source.exists() and checked.exists()
    monkeypatch.setattr(s, "save", save)
    started = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.args = args

        def start(self):
            started.append(s.read(self.args[0])["rows"][0].copy())

    monkeypatch.setattr(workflow.threading, "Thread", DeferredThread)
    s.start(first["id"], "offline-token")
    assert len(started) == 1 and started[0]["stage"] == "superseded"
    assert started[0]["replaced_by"] == replacement["id"]
    assert started[0]["error"] == "" and started[0]["files_cleaned"] is True
    assert not source.exists() and not checked.exists()
    assert FakePortal.events == []  # Neither assignment, grading nor upload was launched.


@pytest.mark.parametrize("status", [404, 410])
def test_permanently_missing_cloudfront_copy_is_terminal_without_retries(tmp_path, monkeypatch, status):
    import requests

    s = store(tmp_path)
    attempts = []

    class MissingResponse:
        status_code = status
        is_redirect = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            raise requests.HTTPError(f"HTTP {status}")

    def get(url, **kwargs):
        attempts.append(url)
        return MissingResponse()

    monkeypatch.setattr(workflow.requests, "get", get)
    s.downloader = workflow.download_pdf
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "offline-token")
    saved = s.read(run["id"])["rows"][0]
    assert saved["workflow_status"] == "not downloadable file" and saved["auto_retry"] is False
    assert len(attempts) == 1 and saved["error"] == ""
    assert str(status) in saved["note"]
    assert FakePortal.events == ["assign"]
    s.work(run["id"], "offline-token")
    assert len(attempts) == 1


def test_cloudfront_timeout_remains_retryable_and_keeps_error_status(tmp_path, monkeypatch):
    import requests

    s = store(tmp_path)
    attempts = []

    def timeout(url, **kwargs):
        attempts.append(url)
        raise requests.Timeout("CloudFront timed out")

    monkeypatch.setattr(workflow.requests, "get", timeout)
    s.downloader = workflow.download_pdf
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "offline-token")
    saved = s.read(run["id"])["rows"][0]
    assert saved["workflow_status"] == "error occurred" and saved["auto_retry"] is True
    assert saved["failures"] == len(attempts) == 3
    assert "timed out" in saved["error"]
    assert FakePortal.events == ["assign"]
