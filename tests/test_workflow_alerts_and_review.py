"""Operator's rules of 26 Oct 2026: faculty-review copies uploaded with a flag (build 136; by
confidence in build 135), rejected copies posted at once, silent alerts (Windows notification and email, no sound since build
137) when a run stops, the portal's 'Unable to fetch task' and login waits waited out, and the
run's own follow-up rounds and continuation after a restart (builds 136-137)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI

import integrated_workflow as workflow
import workflow_alerts
from test_integrated_workflow import (FakePortal, LocalClient, options, pdf, rejected_grade, row, store, token,
                                      wait_idle)


def scored_report(maxima=(("Q1", 2), ("Q2", 3), ("Q3", 5), ("Q4.i", 5), ("Q4.ii", 5)), total=20):
    return {"questions": [{"qid": qid, "score": mark / 2, "max_score": mark} for qid, mark in maxima],
            "total_score": sum(mark / 2 for _, mark in maxima), "max_total": total, "overall_remarks": "Checked."}


# ------------------------------------------------------------------------- confidence

def test_confidence_is_the_share_of_marks_no_reason_doubts():
    report = scored_report()
    assert workflow.review_confidence(report, ["Q2: the remark says the student wrote 36, but the page reads 63"]) == 85
    assert workflow.review_confidence(report, ["Q4: two steps are anchored to the same line"]) == 50       # Q4 covers Q4.i and Q4.ii
    assert workflow.review_confidence(report, ["Q1 credited on an option reading that could not be confirmed",
                                               "Q4.ii is anchored to no line"]) == 65
    assert workflow.review_confidence(report, ["The largest photo is 573 px; the handwriting cannot be read reliably"]) == 0
    assert workflow.review_confidence({"questions": [{"qid": "Q1", "max_score": 1}, {"qid": "Q10", "max_score": 9}], "max_total": 10},
                                      ["Q1 is marked twice"]) == 90                                       # Q1 never names Q10
    assert workflow.review_confidence(report, []) is None and workflow.review_confidence({}, ["Q1"]) is None


def test_confidence_of_an_older_copy_from_its_saved_message_is_cautious():
    report = scored_report()
    message = ("Sent for faculty review (5 reason(s)): Q1 is anchored to no line; Q2 credited on an option reading; "
               "Q3 is anchored to P1L4; and 2 more 9 point(s) to check: Q4.i: its feedback is missing its question label")
    # Q1+Q2+Q3 = 10 named; the 2 hidden reasons are assumed to doubt the two largest others (Q4.i, Q4.ii = 10).
    assert workflow.review_confidence(report, None, message) == 0
    short = "Sent for faculty review (1 reason(s)): Q2 marked wrong on an option reading 4 point(s) to check: Q4.i: label"
    assert workflow.review_confidence(report, None, short) == 85                                        # points to check are not reasons
    assert workflow.review_confidence(report, None, "Portal preparation requires review: no marks") is None


# ------------------------------------------------------------------- held copies by confidence

def held_grade(reasons, report=None, pdf_bytes=True):
    def grade(*args):
        FakePortal.events.append("grade")
        return {"release": False, "pdf": pdf() if pdf_bytes else None, "report": report or scored_report(),
                "error": "Sent for faculty review (%d reason(s)): %s" % (len(reasons), "; ".join(reasons)),
                "review_reasons": list(reasons)}
    return grade


def test_a_faculty_review_copy_is_uploaded_with_its_flag_and_reasons(tmp_path):
    s = store(tmp_path); s.grader = held_grade(["Q2: the remark says 36, the page reads 63"])
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    copy = saved["rows"][0]
    assert copy["stage"] == "uploaded" and copy["confidence"] == 85 and copy["flagged_for_review"] is True
    assert "Faculty review flagged (confidence 85%); uploaded with its marks" in copy["note"] and copy["review_reasons"]
    assert FakePortal.events == ["assign", "download", "grade", "upload"] and saved["status"] == "complete"
    report = (s.folder(run["id"]) / "report.csv").read_text(encoding="utf-8-sig")
    header, line = report.splitlines()[:2]
    assert "flagged_for_review" in header and "review_reasons" in header and "confidence" in header
    assert ",yes,85," in line and "Q2: the remark says 36" in line


def test_a_copy_doubted_as_a_whole_is_uploaded_too_unless_the_run_sets_a_minimum(tmp_path):
    s = store(tmp_path); s.grader = held_grade(["The copy's cover does not match the student"])
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "uploaded" and saved["rows"][0]["confidence"] == 0
    assert saved["rows"][0]["flagged_for_review"] is True and saved["status"] == "complete"
    s = store(tmp_path / "strict"); s.grader = held_grade(["The copy's cover does not match the student"])
    strict = options(); strict["review_min_confidence"] = 20
    run = s.create([row()], strict, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "faculty_review" and "confidence 0% is below this run's 20%" in saved["rows"][0]["note"]
    assert saved["status"] == "needs_attention" and "upload" not in FakePortal.events


def test_a_copy_the_checker_could_not_mark_is_skipped_and_says_why(tmp_path):
    # Nothing is held for faculty review (no faculty, 29 Oct 2026).
    s = store(tmp_path); s.grader = held_grade(["Q2: check"], pdf_bytes=False)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    copy = s.read(run["id"])["rows"][0]
    assert copy["stage"] == "skipped" and "no marks to upload" in copy["error"]
    assert "upload" not in FakePortal.events and FakePortal.events.count("grade") == 1   # not three attempts
    s.work(run["id"], "token")                                    # Resume / retry checks it again
    assert FakePortal.events.count("grade") == 2 and s.read(run["id"])["rows"][0]["stage"] == "skipped"


def test_a_copy_an_older_build_held_without_marks_is_checked_again(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    saved = s.read(run["id"])
    saved["rows"][0].update(stage="faculty_review", error="FIXED_RUBRIC_MISMATCH", note=workflow.NO_MARKS_NOTE)
    s.save(saved)
    s.work(run["id"], "token")
    assert "grade" in FakePortal.events and s.read(run["id"])["rows"][0]["stage"] == "uploaded"


def test_a_test_whose_copies_keep_answering_another_paper_stops_before_posting(tmp_path):
    s = store(tmp_path)
    def grade(*args):
        FakePortal.events.append("grade")
        return {"release": False, "rejected": True, "failure_code": "WRONG_QUESTION_PAPER",
                "pdf": None, "report": None, "error": "This answer sheet appears to be for a different test."}
    s.grader = grade
    run = s.create([{**row(), "student_id": f"s{i}", "submission_id": f"c{i}"} for i in range(4)],
                   options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert "question paper given to the checker may be the wrong one" in saved["error"]
    assert "reject" not in FakePortal.events or FakePortal.events.count("reject") < 3
    assert any(r["stage"] == "downloaded" for r in saved["rows"])     # the third is not rejected


def test_the_minimum_confidence_is_a_run_option_and_build_135s_threshold_is_dropped():
    for bad in (-1, 101, "high"):
        with pytest.raises(ValueError, match="0 to 100"):
            broken = options(); broken["review_min_confidence"] = bad; workflow.validate_options(broken)
    checked = workflow.validate_options({**options(), "review_upload_threshold": 20})
    assert checked["review_min_confidence"] == 0 and "review_upload_threshold" not in checked
    assert workflow.review_threshold({"review_upload_threshold": 20}) == 0


def test_a_check_that_failed_is_tried_again_then_skipped_and_resume_checks_it_again(tmp_path):
    s = store(tmp_path)
    def grade(*args):
        FakePortal.events.append("grade")
        return {"release": False, "failed": True, "pdf": None, "report": None, "error": "The proxy closed the connection"}
    s.grader = grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    copy = saved["rows"][0]
    assert FakePortal.events == ["assign", "download", "grade", "grade", "grade"]
    assert copy["stage"] == "skipped" and copy["skipped_from"] == "downloaded"
    assert "Skipped after 3 failed attempts: The checker could not finish this copy: The proxy closed the connection" in copy["error"]
    assert saved["status"] == "complete" and s.alerts.raised[-1]["title"] == "Run finished, 1 skipped: Maths Test"
    s.grader = held_grade(["Q2: check"])
    s.work(run["id"], "token")                                    # Resume / retry checks it again
    assert s.read(run["id"])["rows"][0]["stage"] == "uploaded" and FakePortal.events[-2:] == ["grade", "upload"]


def test_resume_uploads_copies_build_135_held_below_its_threshold(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    _, checked, report_path = s.copy_paths(run, "copy123")
    checked.write_bytes(pdf()); report_path.write_text(json.dumps(scored_report()), encoding="utf-8")
    saved = s.read(run["id"])
    saved["options"]["review_upload_threshold"] = 20
    saved["options"].pop("review_min_confidence", None)
    saved["rows"][0].update(stage="faculty_review", has_checked_pdf=True, confidence=0,
                            review_reasons=["The copy's cover does not match the student"],
                            error="Sent for faculty review (1 reason(s)): The copy's cover does not match the student",
                            note="Held for faculty review: confidence 0% is below 20%")
    del saved["rows"][0]["portal_assigned"]   # saved by that build, which assigned first
    s.save(saved)
    s.work(run["id"], "token")
    copy = s.read(run["id"])["rows"][0]
    assert copy["stage"] == "uploaded" and copy["flagged_for_review"] is True and FakePortal.events == ["upload"]


def test_resume_uploads_copies_held_by_an_older_build_when_confident(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    _, checked, report_path = s.copy_paths(run, "copy123")
    checked.write_bytes(pdf()); report_path.write_text(json.dumps(scored_report()), encoding="utf-8")
    saved = s.read(run["id"])
    saved["rows"][0].update(stage="faculty_review", has_checked_pdf=True,
                            error="Sent for faculty review (1 reason(s)): Q2: the remark says 36 3 point(s) to check: Q4.i")
    del saved["rows"][0]["portal_assigned"]   # saved by that build, which assigned first
    s.save(saved)
    s.work(run["id"], "token")
    row_after = s.read(run["id"])["rows"][0]
    assert row_after["stage"] == "uploaded" and row_after["confidence"] == 85
    assert FakePortal.events == ["upload"]                       # nothing graded or assigned again


# ------------------------------------------------------------------------ rejections at once

def test_a_rejected_copy_is_posted_to_the_portal_at_once(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "rejection_submitted" and saved["status"] == "complete"
    assert FakePortal.events == ["assign", "download", "grade", "reject"]
    assert FakePortal.rejections and "NOT_AN_ANSWER_SHEET" in FakePortal.rejections[0][1]


def test_resume_posts_rejections_still_waiting_and_respects_the_option(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    manual = options(); manual["submit_rejections"] = False
    run = s.create([row()], manual, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert s.read(run["id"])["rows"][0]["stage"] == "rejected" and "reject" not in FakePortal.events
    saved = s.read(run["id"]); saved["options"].pop("submit_rejections"); s.save(saved)       # a run from an older build
    s.work(run["id"], "token")
    assert s.read(run["id"])["rows"][0]["stage"] == "rejection_submitted"


# --------------------------------------------------------------------------------- alerts

def test_a_run_that_ends_by_itself_raises_one_alert_with_counts(tmp_path):
    s = store(tmp_path); s.grader = held_grade(["The copy's cover does not match the student"])
    chosen = options(); chosen["alert_email"] = "operator@pw.live"; chosen["review_min_confidence"] = 20
    run = s.create([row()], chosen, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert len(s.alerts.raised) == 1
    alert = s.alerts.raised[0]
    assert alert["title"] == "Run finished, some copies need a decision: Maths Test"
    assert "0 of 1 uploaded" in alert["body"] and "1 held for faculty review" in alert["body"] and alert["to"] == ["operator@pw.live"]
    assert "Example" not in alert["body"]                         # no student names in alerts
    s = store(tmp_path / "flagged"); s.grader = held_grade(["Q2: check"])
    run = s.create([row()], chosen, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert s.alerts.raised[-1]["title"] == "Run complete: Maths Test"
    assert "1 of 1 uploaded (1 flagged for faculty review)" in s.alerts.raised[-1]["body"]


def test_stop_by_the_operator_raises_no_alert_and_the_owner_email_is_the_default(tmp_path):
    s = store(tmp_path)
    s.owner_emails["owner"] = "signed.in@pw.live"
    run = s.create([row()], options(), "owner", pdf(), pdf())
    original = s.downloader
    def download(*args):
        original(*args); s.stop_run(run["id"])
    s.downloader = download
    s.start(run["id"], "token"); wait_idle(s, seconds=10)
    assert s.read(run["id"])["status"] == "stopped" and s.alerts.raised == []
    s.work(run["id"], "token")
    assert s.alerts.raised[-1]["to"] == ["signed.in@pw.live"] and s.alerts.raised[-1]["title"].startswith("Run complete")


def test_a_sign_out_pause_and_a_portal_login_wait_raise_alerts(tmp_path):
    s = store(tmp_path); s.signin_poll = 0.05
    class SignedOut(Exception):
        status_code = 401
    def grade(*args):
        raise SignedOut("expired")
    s.grader = grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.start(run["id"], "token")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(a["key"].endswith(":signin") for a in s.alerts.raised):
        time.sleep(.05)
    assert any(a["title"].startswith("Run paused, sign in again") for a in s.alerts.raised)
    s.alert_login_wait("EVALUATOR", "teacher@example.com")
    assert s.alerts.raised[-1]["title"] == "Portal login needed" and "teacher@example.com" in s.alerts.raised[-1]["body"]
    s.stop_run(run["id"]); wait_idle(s, seconds=10)


def test_the_portal_reports_a_login_wait_through_the_desks_hook():
    adapter = workflow.Portal.__new__(workflow.Portal)
    adapter.admin, adapter.evaluator, calls = "", "teacher@example.com", []
    adapter.alert_hook = lambda role, account: calls.append((role, account))
    adapter.on_login_wait("evaluator", "Waiting for you to log in to the evaluation site in the Chrome window...")
    adapter.on_login_wait("evaluator", "Reading your Pending list")
    assert calls == [("EVALUATOR", "teacher@example.com")]


# ------------------------------------------------- the portal's "Unable to fetch task"

class FetchFailingPortal(FakePortal):
    """The fixture portal whose task list does not load for the next `failures` uploads."""
    failures = 0

    def perform(self, stage, row, options, *args, **kwargs):
        if stage == "upload" and FetchFailingPortal.failures:
            FetchFailingPortal.failures -= 1
            self.events.append("unable to fetch")
            raise workflow.PortalFetchError("The portal says 'Unable to fetch task'")
        return super().perform(stage, row, options, *args, **kwargs)


def test_unable_to_fetch_task_never_costs_the_copy_an_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "PORTAL_FETCH_ALERT_SECONDS", 0)
    s = store(tmp_path); s.portal_factory = FetchFailingPortal
    FetchFailingPortal.failures = 5                               # more than the three attempts a copy has
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    copy = s.read(run["id"])["rows"][0]
    assert copy["stage"] == "uploaded" and copy["failures"] == 0 and copy["error"] == ""
    assert FakePortal.events == ["assign", "download", "grade"] + ["unable to fetch"] * 5 + ["upload"]
    waits = [a for a in s.alerts.raised if a["title"] == "Portal not loading tasks: Maths Test"]
    assert len(waits) == 1 and "no copy is skipped" in waits[0]["body"] and "Example" not in waits[0]["body"]


def test_a_portal_wait_shows_on_the_copy_and_stop_ends_it(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "PORTAL_FETCH_DELAYS", (30,))
    s = store(tmp_path); s.portal_factory = FetchFailingPortal
    FetchFailingPortal.failures = 10 ** 6
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.start(run["id"], "token")
    assert wait_for(lambda: "Unable to fetch task" in (s.read(run["id"])["rows"][0].get("operation") or ""), seconds=10)
    assert "trying again in 30 s" in s.read(run["id"])["rows"][0]["operation"]
    started = time.monotonic()
    s.stop_run(run["id"]); wait_idle(s, seconds=10)
    assert time.monotonic() - started < 10
    saved = s.read(run["id"])
    assert saved["status"] == "stopped" and saved["rows"][0]["stage"] == "checked" and saved["rows"][0]["failures"] == 0
    FetchFailingPortal.failures = 0


class FakeEngine:
    SITE_LOGIN_EXPIRED = "Unable to fetch task"
    BASE_URL = "https://portal.example/"
    LOGGED_IN_URL_PART = "/tev/"

    class AbortRun(Exception):
        pass

    def __init__(self):
        self.shots, self.visits = [], []
        self.StudentRecord = lambda **fields: type("Record", (), fields)()

    def site_logged_in(self, page):
        return True

    def open_allotment_test(self, page, record):
        raise self.AbortRun("The site says 'Unable to fetch task'. The site may be having problems; try again later.")

    def save_screenshot(self, page, student_id):
        self.shots.append(student_id)
        return f"C:/shots/{student_id}.png"

    def reset_page(self, page, context):
        pass


class FakePage:
    def __init__(self, engine):
        self.engine = engine
    def is_closed(self):
        return False
    def goto(self, url, wait_until=None):
        self.engine.visits.append(url)


def test_the_adapter_turns_the_engines_fetch_abort_into_a_wait_only_before_submit():
    engine = FakeEngine()
    adapter = workflow.Portal.__new__(workflow.Portal)
    adapter.engine = engine
    adapter.session = lambda kind: {"page": FakePage(engine), "context": None}
    copy = {"program_name": "P", "test_series_name": "S", "test_name": "T", "student_id": "s1", "student_name": ""}
    opts = {"evaluator_email": "teacher@example.com", "deadline": "2026-10-27T10:00"}
    with pytest.raises(workflow.PortalFetchError, match="screenshot: unable_to_fetch_s1.png"):
        adapter._perform("assign", copy, opts)
    assert engine.visits == []                                            # defer reload until the next permitted try
    with pytest.raises(workflow.PortalFetchError) as again:
        adapter._perform("assign", copy, opts)
    assert engine.visits == ["https://portal.example/"]                     # one recovery reload (fixture delay is zero)
    assert "screenshot" not in str(again.value) and len(engine.shots) == 1  # at most one picture per ten minutes
    def clicked_then_fetch_failed(page, record, before_submit):
        before_submit()
        raise engine.AbortRun("The site says 'Unable to fetch task'.")
    engine.open_allotment_test = lambda page, record: None
    adapter.assign_copy = clicked_then_fetch_failed
    with pytest.raises(engine.AbortRun):                                     # after Submit: the lost-confirmation path
        adapter._perform("assign", copy, opts, before_submit=lambda: None)


# ------------------------------------------------- the run resolves problems by itself

class LoginWaitPortal(FakePortal):
    """The evaluator window waits its full time for a login on the next `waits` uploads."""
    waits = 0

    def perform(self, stage, row, options, *args, **kwargs):
        if stage == "upload" and LoginWaitPortal.waits:
            LoginWaitPortal.waits -= 1
            self.events.append("login wait")
            raise workflow.PortalLoginWait("Site login was not detected. Sign in there with the EVALUATOR account "
                                           "teacher@example.com; the run continues by itself.")
        return super().perform(stage, row, options, *args, **kwargs)


def test_a_portal_login_wait_never_costs_the_copy_an_attempt(tmp_path):
    s = store(tmp_path); s.portal_factory = LoginWaitPortal
    LoginWaitPortal.waits = 4                                      # more than the three attempts a copy has
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    copy = s.read(run["id"])["rows"][0]
    assert copy["stage"] == "uploaded" and copy["failures"] == 0
    assert FakePortal.events.count("login wait") == 4
    assert not any(a["title"].startswith("Portal not loading") for a in s.alerts.raised)


def test_the_adapters_login_prompt_waits_instead_of_failing_the_copy():
    engine = FakeEngine()
    adapter = workflow.Portal.__new__(workflow.Portal)
    adapter.engine, adapter.root, adapter.admin, adapter.evaluator = engine, Path("."), "", "teacher@example.com"
    adapter.windows = {"evaluator": (9338, "evaluator_profile")}
    with adapter.runtime("evaluator"):
        with pytest.raises(workflow.PortalLoginWait) as waiting:
            engine.ask_continue("Site login was not detected.\n\nFinish logging in to the evaluation site in the Chrome window.")
    # The prompt names the account to sign in with and promises that the run carries on by
    # itself. Checked as two phrases rather than one: between 19 and 23 Nov 2026 the prompt
    # briefly carried a sentence about what the window is for, which sat between them.
    prompt = str(waiting.value)
    assert "EVALUATOR account teacher@example.com" in prompt
    assert "the run continues by itself" in prompt


class FlakyUploadPortal(FakePortal):
    """Uploads fail before Submit for the next `failures` tries."""
    failures = 0

    def perform(self, stage, row, options, *args, **kwargs):
        if stage == "upload" and FlakyUploadPortal.failures:
            FlakyUploadPortal.failures -= 1
            self.events.append("upload failed")
            raise RuntimeError("The upload control is missing; nothing was submitted")
        return super().perform(stage, row, options, *args, **kwargs)


def test_a_skipped_copy_is_tried_again_by_the_run_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "AUTO_RETRY_DELAYS", (0, 0, 0))
    s = store(tmp_path); s.portal_factory = FlakyUploadPortal; s.grader = held_grade(["Q2: check"])
    FlakyUploadPortal.failures = 4                                  # the first pass's three attempts, and one more
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    copy = saved["rows"][0]
    assert copy["stage"] == "uploaded" and saved["status"] == "complete" and "notice" not in saved
    assert "Tried again by the run itself (round 1)" in copy["note"] and "Faculty review flagged" in copy["note"]
    assert FakePortal.events.count("upload failed") == 4 and FakePortal.events.count("grade") == 1


def test_the_run_stops_trying_after_its_rounds_and_a_failed_check_gets_one_round(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "AUTO_RETRY_DELAYS", (0, 0, 0))
    s = store(tmp_path); s.portal_factory = FlakyUploadPortal
    FlakyUploadPortal.failures = 10 ** 6
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "skipped" and saved["status"] == "complete"
    assert FakePortal.events.count("upload failed") == 12          # three attempts in the first pass and in each of three rounds
    FlakyUploadPortal.failures = 0
    s = store(tmp_path / "check")
    def grade(*args):
        FakePortal.events.append("grade")
        return {"release": False, "failed": True, "pdf": None, "report": None, "error": "The proxy closed the connection"}
    s.grader = grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert s.read(run["id"])["rows"][0]["stage"] == "skipped"
    assert FakePortal.events.count("grade") == 6                    # each check is paid: one automatic round only


class LateConfirmPortal(FakePortal):
    """After a lost confirmation the first read of the portal cannot tell; the next finds it done."""
    reads = 0

    def perform(self, stage, row, options, *args, **kwargs):
        if stage.startswith("verify_"):
            LateConfirmPortal.reads += 1
            self.events.append(stage)
            return {"result": "unknown" if LateConfirmPortal.reads == 1 else "done", "detail": "fixture portal"}
        return super().perform(stage, row, options, *args, **kwargs)


def test_an_unconfirmed_portal_action_is_checked_again_by_the_run_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "AUTO_RETRY_DELAYS", (0, 0, 0))
    s = store(tmp_path); s.portal_factory = LateConfirmPortal; LateConfirmPortal.reads = 0
    FakePortal.fail_after_assign_marker = True                      # Submit clicked, its confirmation lost
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    copy = s.read(run["id"])["rows"][0]
    assert copy["stage"] == "uploaded" and LateConfirmPortal.reads == 2
    assert FakePortal.events.count("assign") == 1                    # never submitted a second time


def test_a_stop_during_the_wait_before_a_round_ends_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "AUTO_RETRY_DELAYS", (30,))
    s = store(tmp_path); s.portal_factory = FlakyUploadPortal
    FlakyUploadPortal.failures = 10 ** 6
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.start(run["id"], "token")
    assert wait_for(lambda: "again by itself in 1 min" in (s.read(run["id"]).get("notice") or ""), seconds=10)
    s.stop_run(run["id"]); wait_idle(s, seconds=10)
    saved = s.read(run["id"])
    assert saved["status"] == "stopped" and "notice" not in saved and saved["rows"][0]["stage"] == "skipped"
    FlakyUploadPortal.failures = 0


def test_an_interrupted_run_continues_by_itself_at_the_next_sign_in(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    saved = s.read(run["id"]); saved["status"] = "running"; s.save(saved)       # the app closed mid-run
    old = s.create([{**row(), "submission_id": "copy456", "student_id": "student456"}], options(), "owner", pdf(), pdf())
    stale = s.read(old["id"]); stale["status"] = "running"; s.save(stale)
    two_days_ago = time.time() - 2 * 86400
    os.utime(s.folder(old["id"]) / "run.json", (two_days_ago, two_days_ago))
    restarted = workflow.WorkflowStore(tmp_path, s.grader, FakePortal, s.downloader)
    restarted.resume_interrupted("someone else", "token")
    assert not restarted.active                                     # another account's sign-in continues nothing
    restarted.resume_interrupted("owner", "token")
    wait_idle(restarted, seconds=10)
    assert restarted.read(run["id"])["status"] == "complete"
    assert FakePortal.events == ["assign", "download", "grade", "upload"]
    assert restarted.read(old["id"])["status"] == "running"        # too old: it waits for Resume / retry
    restarted.resume_interrupted("owner", "token")                  # once per owner after each start of the app
    wait_idle(restarted, seconds=10)
    assert FakePortal.events == ["assign", "download", "grade", "upload"]


def test_the_saved_runs_list_continues_interrupted_runs(tmp_path, monkeypatch):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    calls = []
    monkeypatch.setattr(s, "resume_interrupted", lambda owner, authorization: calls.append(authorization))
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    assert client.get("/api/workflow/runs", headers=auth).status_code == 200
    assert calls == [auth["Authorization"]]


# ------------------------------------------------------------------ the alert centre itself

def center(tmp_path, mailer=None, toaster=None):
    sent, toasts = [], []
    c = workflow_alerts.AlertCenter(tmp_path, toaster=toaster or (lambda t, b: toasts.append((t, b))),
                                    mailer=mailer or (lambda settings, password, to, subject, body: sent.append((settings["sender"], password, to, subject, body))),
                                    protect=lambda secret: "enc:" + secret[::-1], reveal=lambda stored: stored[4:][::-1])
    return c, sent, toasts


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not predicate():
        time.sleep(.02)
    return predicate()


def test_an_alert_notifies_and_emails_from_the_configured_sender(tmp_path):
    c, sent, toasts = center(tmp_path)
    c.save_email_settings("evaluator.b4@pw.live", "abcd efgh ijkl mnop")
    stored = json.loads((tmp_path / "alerts.json").read_text(encoding="utf-8"))
    assert "abcd" not in json.dumps(stored) and stored["sender"] == "evaluator.b4@pw.live"
    assert c.raise_alert("run:end", "Run complete: Maths", "3 of 3 uploaded.", to=["operator@pw.live"])
    assert wait_for(lambda: sent and toasts)
    sender, password, to, subject, body = sent[0]
    assert (sender, password, to) == ("evaluator.b4@pw.live", "abcdefghijklmnop", ["operator@pw.live"])
    assert subject == "AI Checker: Run complete: Maths" and "3 of 3 uploaded." in body
    status = c.status()
    assert status["active"]["title"] == "Run complete: Maths" and status["email"]["configured"] and status["email"]["ok"] is True
    assert "abcdefghijklmnop" not in json.dumps(status)
    c.acknowledge()
    assert c.status()["active"] is None


def test_alerts_make_no_sound():
    # Operator, 26 Oct 2026: the build 135 siren was too loud; no sound at all.
    source = Path(workflow_alerts.__file__).read_text(encoding="utf-8")
    assert "winsound" not in source and "PlaySound" not in source and "Volume" not in source
    assert not hasattr(workflow_alerts, "Alarm") and not hasattr(workflow_alerts, "siren_wav")
    assert '<audio silent="true"/>' in workflow_alerts.toast_xml("Run complete", "3 of 3 uploaded.")


def test_alerts_without_email_set_up_still_notify_and_say_why_no_email(tmp_path):
    c, sent, toasts = center(tmp_path)
    c.raise_alert("k", "Portal login needed", "Sign in.", to=["operator@pw.live"])
    assert wait_for(lambda: c.status()["email"]["ok"] is False) and toasts
    assert "not set up" in c.status()["email"]["detail"] and sent == []


def test_a_failing_email_or_notification_is_isolated_and_repeats_are_dropped(tmp_path):
    def broken_mail(*args):
        raise OSError("535 Username and Password not accepted")
    def broken_toast(*args):
        raise OSError("no notification centre")
    c, sent, toasts = center(tmp_path, mailer=broken_mail, toaster=broken_toast)
    c.save_email_settings("evaluator.b4@pw.live", "pw")
    assert c.raise_alert("login:EVALUATOR:x", "Portal login needed", "Sign in.", to=["o@pw.live"], once_within=900)
    assert not c.raise_alert("login:EVALUATOR:x", "Portal login needed", "Sign in.", to=["o@pw.live"], once_within=900)
    assert wait_for(lambda: c.status()["email"]["ok"] is False)
    assert "535" in c.status()["email"]["detail"] and c.status()["active"]["title"] == "Portal login needed"


def test_the_alerts_api_reports_acknowledges_saves_and_tests(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    assert client.get("/api/workflow/alerts", headers=auth).json()["active"] is None
    answer = client.post("/api/workflow/alerts/email", headers=auth, json={"sender": "evaluator.b4@pw.live", "app_password": "x"})
    assert answer.status_code == 200 and answer.json()["email"]["sender"] == "evaluator.b4@pw.live"
    tested = client.post("/api/workflow/alerts/test", headers=auth, json={"to": "operator@pw.live"}).json()
    assert tested["active"]["title"] == "Test alert"
    assert client.post("/api/workflow/alerts/ack", headers=auth).status_code == 200
    assert s.alerts.acknowledged == 1
