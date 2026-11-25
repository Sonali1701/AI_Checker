"""Operator's request of 30 Oct 2026 (build 149): running a test again on the same computer.

A copy already checked is not checked and paid for again, skipped copies are picked up, copies
already finished are left alone, and the page shows how many were uploaded, already evaluated
and rejected. Offline doubles.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot as autopilot
from test_integrated_workflow import row as sheet_row
from test_workflow_autopilot import OWNER, PAIRS, copy, finish, pilot
from test_workflow_copy_retries import WorkflowFixture
from workflow_sheet_status import retryable_saved_row, status_for

REFUSED = "Evaluator 'teacher@example.com' was not found among 2807 readable evaluator options for this test."
FIELDS = ("submission_id", "student_id", "student_name", "program_name", "test_series_name", "test_id", "test_name",
          "portal_status", "copy_url")


def again(fixture, run, paper=None, key=None, total=None):
    """The same test run again: the same sheet rows, as a new run."""
    saved = fixture.store.read(run["id"])
    options = dict(saved["options"])
    if total:
        options["total_marks"] = total
    rows = [{field: copy_row[field] for field in FIELDS} for copy_row in saved["rows"]]
    second = fixture.store.create(rows, options, "owner", paper or fixture.pdf, key or fixture.pdf)
    fixture.sources.update({str(fixture.store.copy_paths(second, copy_row["submission_id"])[0]): copy_row["submission_id"]
                            for copy_row in second["rows"]})
    return second


def refused_then_fixed(fixture, count=3):
    def refused(stage, student, before_submit):
        if stage == "assign":
            raise RuntimeError(REFUSED)
    fixture.hook = refused
    run = fixture.create(count=count, concurrency=2)
    saved = fixture.work(run)
    assert all(copy_row["stage"] == "checked" for copy_row in saved["rows"])
    fixture.hook = lambda stage, student, before_submit: None
    return run


def other_paper():
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "A different question paper")
        return document.tobytes()


def test_a_checked_copy_is_uploaded_by_the_next_run_without_being_checked_again(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = refused_then_fixed(fixture)
    second = again(fixture, run)
    assert all(copy_row["stage"] == "checked" and "not checked again" in copy_row["note"] for copy_row in second["rows"])
    assert all(copy_row["stage"] == "superseded" for copy_row in fixture.store.read(run["id"])["rows"])
    saved = fixture.work(second)
    assert saved["status"] == "complete" and all(copy_row["stage"] == "uploaded" for copy_row in saved["rows"])
    assert all(fixture.calls["grade", f"copy{n}"] == 1 and fixture.calls["upload", f"copy{n}"] == 1 for n in range(3))


@pytest.mark.parametrize("change", ["paper", "key", "total"])
def test_a_check_against_another_paper_key_or_total_is_not_reused(tmp_path, change):
    fixture = WorkflowFixture(tmp_path)
    run = refused_then_fixed(fixture, count=1)
    second = again(fixture, run, paper=other_paper() if change == "paper" else None,
                   key=other_paper() if change == "key" else None, total="20" if change == "total" else None)
    assert second["rows"][0]["stage"] == "pending"
    assert fixture.work(second)["rows"][0]["stage"] == "uploaded" and fixture.calls["grade", "copy0"] == 2


def test_a_merged_key_with_a_new_file_id_is_the_same_key():
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "Model answer")
        first, second = document.tobytes(), document.tobytes() + b"\n"
    assert workflow.same_document(first, second) and not workflow.same_document(first, other_paper())


def test_a_skipped_copy_that_could_not_be_downloaded_is_picked_up_and_finished_ones_are_left(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    original = fixture.store.downloader

    def unreadable_once(url, target):
        if "copy0" in url and not fixture.calls["download", "copy0"]:
            fixture.record("download", "copy0")
            raise workflow.UnreadableCopy("The copy file is no longer downloadable (HTTP 403)")
        original(url, target)
    fixture.store.downloader = unreadable_once
    run = fixture.create(count=2)
    first = fixture.work(run)
    assert [copy_row["stage"] for copy_row in first["rows"]] == ["skipped", "uploaded"]
    second = again(fixture, run)
    assert [copy_row["submission_id"] for copy_row in second["rows"]] == ["copy0"]
    assert fixture.work(second)["rows"][0]["stage"] == "uploaded"
    assert fixture.calls["grade", "copy1"] == 1


def test_a_copy_skipped_at_upload_keeps_its_check(tmp_path):
    fixture = WorkflowFixture(tmp_path)

    def no_upload(stage, student, before_submit):
        if stage == "upload":
            raise RuntimeError("Upload unavailable")
    fixture.hook = no_upload
    run = fixture.create(count=1)
    assert fixture.work(run)["rows"][0]["skipped_from"] == "checked"
    fixture.hook = lambda stage, student, before_submit: None
    second = again(fixture, run)
    assert second["rows"][0]["stage"] == "checked"
    assert fixture.work(second)["rows"][0]["stage"] == "uploaded" and fixture.calls["grade", "copy0"] == 1


def test_a_run_never_started_is_picked_up_but_a_working_or_new_one_is_not(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create(count=1)
    with pytest.raises(ValueError, match="already belong"):      # its Start is about to start it
        again(fixture, run)
    saved = fixture.store.read(run["id"])
    saved["created_at"] = (datetime.now() - timedelta(hours=2)).isoformat()
    fixture.store.save(saved)
    fixture.store.active[run["id"]] = {"stop": None, "evaluator": "", "admin": ""}
    with pytest.raises(ValueError, match="already belong"):
        again(fixture, run)
    fixture.store.active.clear()
    second = again(fixture, run)
    assert second["rows"][0]["stage"] == "pending" and fixture.store.read(run["id"])["rows"][0]["stage"] == "superseded"


def test_a_test_with_nothing_left_is_refused(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create(count=1)
    fixture.work(run)
    with pytest.raises(ValueError, match="already belong"):
        again(fixture, run)


def test_a_pdf_in_the_checked_folder_with_no_saved_marks_is_not_paid_for(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    rows = {"student_name": "Tanya Singh", "student_id": "student0", "test_series_name": "Batch Tests", "test_name": "Maths Test"}
    target = workflow.checked_copies_folder(fixture.store.checked_root, rows) / workflow.checked_file_name(rows)
    target.parent.mkdir(parents=True)
    target.write_bytes(fixture.pdf)
    original = fixture.store._create

    def named(sheet, *args, **kwargs):
        return original([{**copy_row, "student_name": "Tanya Singh"} for copy_row in sheet], *args, **kwargs)
    fixture.store._create = named
    run = fixture.create(count=2)
    saved = fixture.work(run)
    held, other = saved["rows"]
    assert held["stage"] == "skipped" and "already in the Checked copies folder" in held["note"]
    assert status_for(held) == "already checked" and fixture.calls["grade", "copy0"] == 0
    assert other["stage"] == "uploaded"
    target.unlink()
    second = again(fixture, run)
    assert second["rows"][0]["stage"] == "pending"


def test_another_submission_of_the_student_is_a_new_copy(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create(count=1)
    saved = fixture.work(run)
    assert saved["rows"][0]["checked_file"]
    resubmitted = {field: saved["rows"][0][field] for field in FIELDS}
    resubmitted.update(submission_id="copy0b", copy_url="https://example.cloudfront.net/copy0b.pdf")
    second = fixture.store.create([resubmitted], dict(saved["options"]), "owner", fixture.pdf, fixture.pdf)
    assert second["rows"][0]["stage"] == "pending"


def test_a_sheet_marked_not_downloadable_is_tried_again_only_with_a_good_link():
    marked = {**sheet_row(), "workflow_status": "not downloadable file"}
    assert workflow.nothing_left(marked) is False
    assert workflow.nothing_left({**marked, "copy_url": "not a link"}) is True
    assert workflow.nothing_left({**sheet_row(), "workflow_status": "uploaded"}) is True


def test_what_a_new_run_may_take():
    done = {"status": "complete"}
    assert retryable_saved_row(done, {"stage": "skipped", "auto_retry": False}) is True
    assert retryable_saved_row(done, {"stage": "uploaded"}) is False
    assert retryable_saved_row({"status": "ready"}, {"stage": "pending"}) is False
    assert retryable_saved_row({"status": "ready"}, {"stage": "pending"}, idle=True) is True
    assert retryable_saved_row({"status": "needs_attention"}, {"stage": "upload_uncertain"}, idle=True) is False


def test_the_autopilot_shows_the_totals_and_picks_up_only_what_is_left(tmp_path):
    def rejected_or_fine(folder, source, options, authorization):
        return {"release": True, "pdf": pdf_bytes, "report": {"questions": [{"qid": "Q1", "score": 3}],
                                                              "total_score": 3, "overall_remarks": "Checked"}}
    s, ap = pilot(tmp_path)
    from test_integrated_workflow import pdf
    pdf_bytes = pdf()
    s.grader = rejected_or_fine
    rows = [copy(1, "A"), copy(2, "A"), copy(3, "B")]
    ap.start(OWNER, ap.plan(OWNER, rows, PAIRS)["plan_id"])
    finish(ap)
    totals = ap.status(OWNER)["copy_totals"]
    assert totals["copies"] == 3 and totals["uploaded"] == 3
    assert {"already_evaluated", "rejected", "rejected_waiting", "checked_waiting", "skipped"} <= set(totals)
    plan = ap.plan(OWNER, rows, PAIRS)
    assert plan["copies"] == 0 and all(test["status"] == "skipped" for test in plan["tests"])


def test_the_page_has_the_totals():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "static", "workflow.html"), encoding="utf-8").read()
    script = open(os.path.join(root, "static", "workflow.js"), encoding="utf-8").read()
    assert 'id="apTotals"' in page and 'id="savedTotals"' in page and "<th>Already evaluated</th>" in page
    assert "already evaluated" in script and "copyTotals" in script
