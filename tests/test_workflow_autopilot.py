"""Operator's request of 26 Oct 2026 (build 139): the autopilot. A student sheet and an accounts
sheet (admin + evaluator pairs with passwords) in; every test checked by the pairs, whole tests
in sheet order, without a person. Fixture portal, grader and checker; no network."""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import threading
import time

import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot as autopilot
from test_integrated_workflow import FakePortal, LocalClient, pdf, row, store, token

OWNER = "owner"
PAIRS = [{"admin": "admin.one@pw.live", "evaluator": "eval.one@pw.live", "password": "pw-one"},
         {"admin": "admin.two@pw.live", "evaluator": "eval.two@pw.live", "password": "pw-two"}]


def copy(n, test, **extra):
    return {**row(), "submission_id": f"copy{n}", "student_id": f"student{n}", "test_id": f"test{test}",
            "test_name": f"Board Subjective Test - {test} (Biology) (Infinity Pro)", "total_marks": "70",
            "board_or_exam": "Class 12th - Science - CBSE Board", "exam_kind": "SCHOOL PREPARATION", **extra}


# ---------------------------------------------------------------------------- inputs

def test_inputs_are_worked_out_from_the_portal_export():
    assert autopilot.subject_from_test_name("Board Subjective Test - 10 (Biology) (Infinity Pro)") == "Biology"
    assert autopilot.subject_from_test_name("Complete Subjective Test 01 (SST by Aditya Sir) (Infinity Pro)") == "SST"
    assert autopilot.subject_from_test_name("Subjective Test 12 (Hindi A) (Infinity Pro)") == "Hindi A"
    assert autopilot.subject_from_test_name("Weekly Test Physics") == "Weekly Test Physics"       # no bracket: the name itself
    assert autopilot.category_for("SCHOOL PREPARATION", "Foundation - Class 9 CBSE") == "school"
    assert autopilot.category_for("", "PW Vidyapeeth Class 11") == "vidyapeeth"
    assert autopilot.category_for("COMMERCE", "") == "commerce"
    assert autopilot.category_for("CA INTER", "") == "ca"
    assert autopilot.category_for("GATE", "Engineering") == ""
    rows = [copy(1, "A"), copy(2, "A", total_marks="70"), copy(3, "A", total_marks="")]
    assert autopilot.class_for(rows) == "Class 12th - Science - CBSE Board" and autopilot.total_for(rows) == "70"


def test_the_accounts_tab_becomes_admin_evaluator_pairs():
    values = [["Admin ID", "Evaluator Email", "Password"], ["a1@pw.live", "e1@pw.live", "x"], ["", "", ""],
              ["a1@pw.live", "e2@pw.live", ""], ["a1@pw.live", "e1@pw.live", "x"]]
    # "reviewer" is empty until the accounts tab has that column (12 Nov 2026).
    assert autopilot.account_pairs(values) == [{"admin": "a1@pw.live", "evaluator": "e1@pw.live", "password": "x", "reviewer": ""},
                                               {"admin": "a1@pw.live", "evaluator": "e2@pw.live", "password": "", "reviewer": ""}]
    with pytest.raises(ValueError, match="admin column and an evaluator column"):
        autopilot.account_pairs([["name", "email"], ["x", "y@pw.live"]])
    with pytest.raises(ValueError, match="row 2: the evaluator ID 'nobody' is not an email"):
        autopilot.account_pairs([["admin", "evaluator", "password"], ["a@pw.live", "nobody", "p"]])
    with pytest.raises(ValueError, match="cannot be an admin in one pair and an evaluator in another: b@pw.live"):
        autopilot.account_pairs([["admin", "evaluator"], ["a@pw.live", "b@pw.live"], ["b@pw.live", "c@pw.live"]])


def test_the_plan_keeps_sheet_order_and_leaves_saved_copies_to_their_runs():
    rows = [copy(1, "B"), copy(2, "A"), copy(3, "B"), copy(4, "C", exam_kind="GATE", board_or_exam="Engineering")]
    tests, by_test = autopilot.build_plan(rows, PAIRS, saved_submissions={"copy3"})
    assert [test["test_id"] for test in tests] == ["testB", "testA", "testC"]
    assert tests[0]["copies"] == 1 and tests[0]["already_in_runs"] == 1 and [r["submission_id"] for r in by_test[tests[0]["key"]]] == ["copy1"]
    assert tests[0]["subject"] == "Biology" and tests[0]["category"] == "school" and tests[0]["total_marks"] == "70"
    assert "exam type" in tests[2]["problem"]


# ---------------------------------------------------------------------- the pilot

class PairPortal(FakePortal):
    """The fixture portal for any admin or evaluator window; counts tests checked at once per evaluator."""
    busy, most, lock, slow, order = {}, {}, threading.Lock(), 0.0, []
    no_key = set()

    def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
        super().__init__(root, evaluator, evaluator_port)

    def exam_documents(self, test, progress=lambda text: None):
        self.events.append(("documents", test["test_id"], test["evaluator_email"]))
        if test["test_id"] in PairPortal.no_key:
            return {"question_paper": [("paper.pdf", pdf())]}
        return {"question_paper": [("paper.pdf", pdf())], "answer_key": [("key.pdf", pdf())]}

    def perform(self, stage, row, options, *args, **kwargs):
        with PairPortal.lock:
            PairPortal.order.append((options["evaluator_email"], row["test_id"]))
        if stage == "upload":
            with PairPortal.lock:
                PairPortal.busy[options["evaluator_email"]] = PairPortal.busy.get(options["evaluator_email"], 0) + 1
                PairPortal.most[options["evaluator_email"]] = max(PairPortal.most.get(options["evaluator_email"], 0), PairPortal.busy[options["evaluator_email"]])
            time.sleep(PairPortal.slow)
            with PairPortal.lock:
                PairPortal.busy[options["evaluator_email"]] -= 1
        return super().perform(stage, row, options, *args, **kwargs)


def pilot(tmp_path, checker=None):
    s = store(tmp_path); s.portal_factory = PairPortal
    PairPortal.busy, PairPortal.most, PairPortal.no_key, PairPortal.slow, PairPortal.order = {}, {}, set(), 0.0, []
    s.latest_authorization[OWNER] = "token"; s.owner_emails[OWNER] = "operator@pw.live"
    s.paper_checker = checker
    s.portal_logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret, reveal=lambda stored: stored[4:])
    ap = autopilot.Autopilot(s, tmp_path)
    ap.run_poll, ap.retry_seconds, ap.sign_in_poll = 0.02, 0, 0.02
    s.autopilot = ap
    return s, ap


def finish(ap, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and (ap.running() or ap.state["status"] == "running"):
        time.sleep(0.02)
    assert not ap.running()


def test_pairs_take_whole_tests_in_sheet_order_until_none_is_left(tmp_path):
    s, ap = pilot(tmp_path)
    PairPortal.slow = 0.05
    checked, grade = [], s.grader

    def recording(folder, source, options, authorization):
        with PairPortal.lock:
            checked.append((options["evaluator_email"], folder.name))     # a run is one test
        return grade(folder, source, options, authorization)
    s.grader = recording
    rows = [copy(n, test) for n, test in enumerate("AABBCCD", 1)]
    plan = ap.plan(OWNER, rows, PAIRS)
    assert plan["windows"] == 4 and plan["copies"] == 7 and "pw-one" not in json.dumps(plan)
    ap.start(OWNER, plan["plan_id"])
    finish(ap)
    state = ap.status(OWNER)
    assert state["status"] == "finished" and state["counts"] == {"done": 4}
    runs = {test["test_name"]: s.read(test["run_id"]) for test in state["tests"]}
    for run in runs.values():
        assert run["status"] == "complete" and all(r["stage"] == "uploaded" for r in run["rows"])
        assert (run["options"]["admin_email"], run["options"]["evaluator_email"]) in {(p["admin"], p["evaluator"]) for p in PAIRS}
        assert run["options"]["subject"] == "Biology" and run["options"]["category"] == "school" and run["options"]["total_marks"] == "70.0"
        assert run["options"]["alert_email"] == "operator@pw.live"
    assert {test["pair"] for test in state["tests"]} == {0, 1}                      # both pairs worked
    # A pair checks one whole test at a time. Its uploads are not in blocks: they go on behind
    # it while it takes its next test (build 146).
    for evaluator in ("eval.one@pw.live", "eval.two@pw.live"):
        sequence = [test for who, test in checked if who == evaluator]
        blocks = [test for index, test in enumerate(sequence) if index == 0 or sequence[index - 1] != test]
        assert len(blocks) == len(set(blocks)), sequence
    fetched = [event for event in FakePortal.events if isinstance(event, tuple)]
    assert sorted(event[1] for event in fetched) == ["testA", "testB", "testC", "testD"]
    assert all(event[2] in ("eval.one@pw.live", "eval.two@pw.live") for event in fetched)
    assert s.portal_logins.password_for("eval.two@pw.live") == "pw-two" and s.portal_logins.password_for("ADMIN.ONE@pw.live") == "pw-one"
    assert s.alerts.raised[-1]["title"] == "Autopilot finished"


def test_a_test_it_cannot_do_is_skipped_with_the_reason_and_the_rest_go_on(tmp_path):
    def checker(paper, options, authorization):
        return {"ok": False, "message": "The questions add up to 60, the official total is 70."} if options["subject"] == "Chemistry" else {"ok": True}
    s, ap = pilot(tmp_path, checker)
    PairPortal.no_key = {"testB"}
    rows = [copy(1, "A"), copy(2, "B"), copy(3, "C", test_name="Board Subjective Test - C (Chemistry) (Infinity Pro)"),
            copy(4, "D", exam_kind="GATE", board_or_exam="Engineering"), copy(5, "E")]
    ap.start(OWNER, ap.plan(OWNER, rows, PAIRS)["plan_id"])
    finish(ap)
    tests = {test["key"].split("|")[2]: test for test in ap.status(OWNER)["tests"]}
    assert tests["testA"]["status"] == tests["testE"]["status"] == "done"
    # 19 Nov 2026: a test the portal has no Model Answer for waits for one instead of being skipped.
    assert tests["testB"]["status"] == "key_missing" and "no Model Answer" in tests["testB"]["detail"]
    assert tests["testC"]["status"] == "marks_problem" and "add up to 60" in tests["testC"]["detail"]
    assert tests["testD"]["status"] == "skipped" and "exam type" in tests["testD"]["detail"]
    assert not tests["testC"]["run_id"] and not tests["testD"]["run_id"]


def test_corrected_inputs_are_used(tmp_path):
    s, ap = pilot(tmp_path)
    rows = [copy(1, "D", exam_kind="GATE", board_or_exam="Engineering")]
    plan = ap.plan(OWNER, rows, PAIRS[:1])
    key = plan["tests"][0]["key"]
    ap.start(OWNER, plan["plan_id"], {key: {"category": "school", "subject": "Botany", "total_marks": "80"}})
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "done"
    options = s.read(test["run_id"])["options"]
    assert (options["category"], options["subject"], options["total_marks"]) == ("school", "Botany", "80.0")


def test_stop_ends_the_autopilot_and_its_runs(tmp_path):
    s, ap = pilot(tmp_path)
    PairPortal.slow = 0.3
    rows = [copy(n, test) for n, test in enumerate("AAAABBBBCCCC", 1)]
    ap.start(OWNER, ap.plan(OWNER, rows, PAIRS[:1])["plan_id"])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(t["status"] == "running" for t in ap.status(OWNER)["tests"]):
        time.sleep(0.02)
    ap.stop(OWNER)
    finish(ap)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and s.active:
        time.sleep(0.02)
    state = ap.status(OWNER)
    assert state["status"] == "stopped" and [t["status"] for t in state["tests"]][1:] == ["waiting", "waiting"]


def test_after_a_restart_the_autopilot_continues_at_the_next_sign_in(tmp_path):
    s, ap = pilot(tmp_path)
    rows = [copy(1, "A"), copy(2, "B")]
    plan = ap.plan(OWNER, rows, PAIRS[:1])
    with ap.lock:          # started, then the app closed before any pair ran
        ap.threads = []
    ap.launch = lambda: None
    ap.start(OWNER, plan["plan_id"])
    state = json.loads(ap.state_path.read_text(encoding="utf-8"))
    state["tests"][0].update(status="preparing", pair=0)          # was being prepared when the app closed
    ap.state_path.write_text(json.dumps(state), encoding="utf-8")
    restarted = autopilot.Autopilot(s, tmp_path)
    restarted.run_poll, restarted.retry_seconds = 0.02, 0
    restarted.resume("someone else")
    assert not restarted.running()
    restarted.resume(OWNER)
    finish(restarted)
    assert restarted.status(OWNER)["counts"] == {"done": 2}


# ------------------------------------------------------------------------------- API

def upload(client, auth, name, rows):
    data = io.StringIO()
    writer = csv.writer(data)
    writer.writerows(rows)
    return client.post("/api/workflow/sheet-tabs", headers=auth, files={"sheet": (name, data.getvalue().encode())}).json()


def test_the_autopilot_api_plans_starts_reports_and_stops(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), store(tmp_path).grader, tmp_path)
    s.portal_factory, s.downloader = PairPortal, store(tmp_path).downloader
    s.portal_logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret, reveal=lambda stored: stored[4:])
    s.autopilot.run_poll = 0.02
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    header = list(copy(1, "A"))
    students = upload(client, auth, "students.csv", [header] + [list(copy(n, test).values()) for n, test in enumerate("AB", 1)])
    accounts = upload(client, auth, "accounts.csv", [["admin", "evaluator", "password"], ["admin.one@pw.live", "eval.one@pw.live", "pw-one"]])
    bad = client.post("/api/workflow/autopilot/plan", headers=auth, json={"students": {"import_id": students["import_id"], "worksheet": "CSV data"}})
    assert bad.status_code == 400
    plan = client.post("/api/workflow/autopilot/plan", headers=auth, json={"students": {"import_id": students["import_id"], "worksheet": "CSV data"},
                                                                         "accounts": {"import_id": accounts["import_id"], "worksheet": "CSV data"}})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert [test["subject"] for test in body["tests"]] == ["Biology", "Biology"] and body["pairs"][0]["has_password"] is True
    assert "pw-one" not in plan.text
    assert client.get("/api/workflow/autopilot", headers=auth).json()["status"] == "none"
    started = client.post("/api/workflow/autopilot/start", headers=auth, json={"plan_id": body["plan_id"]})
    assert started.status_code == 200 and started.json()["status"] == "running"
    assert client.post("/api/workflow/autopilot/start", headers=auth, json={"plan_id": body["plan_id"]}).status_code in (404, 409)
    finish(s.autopilot)
    assert client.get("/api/workflow/autopilot", headers=auth).json()["counts"] == {"done": 2}
    assert client.post("/api/workflow/autopilot/stop", headers=auth).status_code == 200
