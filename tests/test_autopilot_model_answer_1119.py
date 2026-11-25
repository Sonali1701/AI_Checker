"""Operator's request of 19 Nov 2026: the portal has no Model Answer for some tests. Such a test
was skipped before; now it waits for a person to give the Model Answer on the page, and every tab
of that test runs with the file given. Fixture portal, grader and checker; no network.
"""
from __future__ import annotations

import os
import sys

import fitz
import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot as autopilot
from test_integrated_workflow import LocalClient, pdf, store, token
from test_autopilot_tabs_1031 import tabbed
from test_workflow_autopilot import OWNER, PAIRS, PairPortal, copy, finish, pilot, upload

PAPER_LINK = "https://static.pw.live/paper-{}.pdf"


def given_pdf(words="Model Answer given by hand", pages=1):
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page().insert_text((30, 30), words)
        return doc.tobytes()


def waiting_for_a_key(tmp_path, rows, tests=("testA",), checker=None):
    """An autopilot run to its end: the portal gives a paper but no Model Answer for `tests`."""
    s, ap = pilot(tmp_path, checker or (lambda paper, options, authorization: {"ok": True, "method": "ai", "items": []}))
    PairPortal.no_key = set(tests)
    ap.start(OWNER, ap.plan(OWNER, rows, PAIRS[:1])["plan_id"])
    finish(ap)
    return s, ap


def key_of(saved, run_id):
    return (saved.folder(run_id) / "answer_key.pdf").read_bytes()


# ------------------------------------------------------------------ waiting, not skipped

def test_a_test_without_a_model_answer_waits_instead_of_being_skipped(tmp_path):
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "key_missing" and "no Model Answer" in test["detail"]
    assert "Give the Model Answer below" in test["detail"] and not test["run_id"]


def test_the_waiting_test_shows_the_portal_link_of_its_paper(tmp_path, monkeypatch):
    def exam_documents(self, test, progress=lambda text: None):
        return {"question_paper": [("paper.pdf", pdf())], "links": {"question_paper": PAPER_LINK.format(test["test_id"])}}

    monkeypatch.setattr(PairPortal, "exam_documents", exam_documents)
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    assert ap.status(OWNER)["tests"][0]["paper_link"] == PAPER_LINK.format("testA")


def test_the_other_tests_go_on_while_one_waits(tmp_path):
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A"), copy(2, "B")], tests=("testA",))
    tests = {test["key"].split("|")[2]: test for test in ap.status(OWNER)["tests"]}
    assert tests["testA"]["status"] == "key_missing" and tests["testB"]["status"] == "done"


# ------------------------------------------------------------------ the file given

def test_the_model_answer_given_runs_the_test_with_that_file(tmp_path):
    s, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    key = ap.status(OWNER)["tests"][0]["key"]
    given = given_pdf(pages=3)
    assert ap.supply_answer_key(OWNER, key, given, "Model Answer.pdf", "operator@pw.live")["status"] == "running"
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "done" and test["key_name"] == "Model Answer.pdf"
    assert key_of(s, test["run_id"]) == given


def test_the_pages_of_the_file_are_counted(tmp_path):
    assert autopilot.pdf_page_count(given_pdf(pages=2)) == 2
    assert autopilot.pdf_page_count(b"not a PDF") == 0 and autopilot.pdf_page_count(b"") == 0
    s, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    ap.supply_answer_key(OWNER, ap.status(OWNER)["tests"][0]["key"], given_pdf(pages=2), "key.pdf")
    finish(ap)
    assert autopilot.pdf_page_count(key_of(s, ap.status(OWNER)["tests"][0]["run_id"])) == 2


def test_every_tab_of_the_test_takes_the_same_file(tmp_path):
    rows = tabbed(("first", [copy(1, "A")]), ("second", [copy(2, "A"), copy(3, "B")]))
    s, ap = waiting_for_a_key(tmp_path, rows, tests=("testA",))
    waiting = [test for test in ap.status(OWNER)["tests"] if test["status"] == "key_missing"]
    assert len(waiting) == 2                           # one entry per tab, one paper
    given = given_pdf()
    ap.supply_answer_key(OWNER, waiting[0]["key"], given, "key.pdf")
    finish(ap)
    tests = ap.status(OWNER)["tests"]
    assert [test["status"] for test in tests] == ["done", "done", "done"]
    assert all(key_of(s, test["run_id"]) == given for test in tests[:2])


def test_a_model_answer_given_by_hand_beats_a_portal_one(tmp_path):
    s, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    given = given_pdf()
    ap.supply_answer_key(OWNER, ap.status(OWNER)["tests"][0]["key"], given, "key.pdf")
    PairPortal.no_key = set()                          # the portal has one by the time it runs
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "done" and key_of(s, test["run_id"]) == given


def test_the_file_is_still_there_after_a_restart(tmp_path):
    s, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    given = given_pdf()
    ap.supply_answer_key(OWNER, ap.status(OWNER)["tests"][0]["key"], given, "key.pdf")
    ap.stop(OWNER)
    restarted = autopilot.Autopilot(s, tmp_path)
    restarted.run_poll, restarted.retry_seconds, restarted.sign_in_poll = 0.02, 0, 0.02
    s.autopilot = restarted
    assert restarted.status(OWNER)["tests"][0]["key_name"] == "key.pdf"
    saved, _ = restarted.load()
    assert restarted.given_answer_key(saved["tests"][0]) == given


def test_a_file_that_has_gone_is_asked_for_again(tmp_path):
    """The state kept a Model Answer whose file a Temp cleanup, or a moved folder, has taken."""
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    waiting = ap.state["tests"][0]
    waiting["status"], waiting["detail"] = "waiting", "Will run with the Model Answer you gave"
    waiting["operator_key"], waiting["key_name"] = str(tmp_path / "autopilot_keys" / "gone.pdf"), "key.pdf"
    ap.state["status"] = "running"
    ap.save()
    ap.launch()
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "key_missing" and "cannot be read any more" in test["detail"]
    assert not test["key_name"]


# ------------------------------------------------------------------ what is refused

@pytest.mark.parametrize("data, message", [(b"", "Choose the Model Answer"), (None, "Choose the Model Answer"),
                                           (b"not a PDF at all", "readable PDF"),
                                           (b"%PDF-1.4 truncated", "readable PDF")])
def test_only_a_readable_pdf_is_taken(tmp_path, data, message):
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    with pytest.raises(ValueError, match=message):
        ap.supply_answer_key(OWNER, ap.status(OWNER)["tests"][0]["key"], data)
    assert ap.status(OWNER)["tests"][0]["status"] == "key_missing"


def test_a_file_over_the_size_limit_is_refused(tmp_path, monkeypatch):
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    monkeypatch.setattr(workflow, "MAX_BYTES", 1024)
    with pytest.raises(ValueError, match="larger than 50 MB"):
        ap.supply_answer_key(OWNER, ap.status(OWNER)["tests"][0]["key"], given_pdf() + b" " * 1024)


def test_only_a_test_that_is_waiting_takes_a_model_answer(tmp_path):
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A"), copy(2, "B")])
    done = next(test for test in ap.status(OWNER)["tests"] if test["status"] == "done")
    with pytest.raises(ValueError, match="not waiting for a Model Answer"):
        ap.supply_answer_key(OWNER, done["key"], given_pdf())
    with pytest.raises(LookupError, match="not in the autopilot"):
        ap.supply_answer_key(OWNER, "no such test", given_pdf())


def test_a_stopped_autopilot_is_not_started_by_a_model_answer(tmp_path):
    _, ap = waiting_for_a_key(tmp_path, [copy(1, "A")])
    ap.state["status"] = "stopped"
    with pytest.raises(ValueError, match="was stopped"):
        ap.supply_answer_key(OWNER, ap.status(OWNER)["tests"][0]["key"], given_pdf())
    assert ap.status(OWNER)["tests"][0]["status"] == "key_missing" and not ap.running()


def test_no_autopilot_takes_no_model_answer(tmp_path):
    _, ap = pilot(tmp_path)
    with pytest.raises(LookupError, match="No autopilot"):
        ap.supply_answer_key(OWNER, "any", given_pdf())


# ------------------------------------------------------------------ the page and the API

def test_the_page_offers_a_file_for_each_waiting_test():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "static", "workflow.html"), encoding="utf-8").read()
    script = open(os.path.join(root, "static", "workflow.js"), encoding="utf-8").read()
    assert all(f'id="{name}"' in page for name in ("apKeys", "apKeysTitle", "apKeysRows"))
    assert "/api/workflow/autopilot/answer-key" in script and "showKeyProblems(plan ? [] : tests)" in script
    assert 'test.status === "key_missing"' in script


def test_the_api_takes_the_model_answer_for_a_waiting_test(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), store(tmp_path).grader, tmp_path)
    s.portal_factory, s.downloader = PairPortal, store(tmp_path).downloader
    s.portal_logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret, reveal=lambda stored: stored[4:])
    s.autopilot.run_poll, s.autopilot.sign_in_poll = 0.02, 0.02
    PairPortal.no_key = {"testA"}
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    header = list(copy(1, "A"))
    students = upload(client, auth, "students.csv", [header, list(copy(1, "A").values())])
    accounts = upload(client, auth, "accounts.csv", [["admin", "evaluator", "password"], ["admin.one@pw.live", "eval.one@pw.live", "pw-one"]])
    plan = client.post("/api/workflow/autopilot/plan", headers=auth,
                       json={"students": {"import_id": students["import_id"], "worksheet": "CSV data"},
                             "accounts": {"import_id": accounts["import_id"], "worksheet": "CSV data"}}).json()
    assert client.post("/api/workflow/autopilot/start", headers=auth, json={"plan_id": plan["plan_id"]}).status_code == 200
    finish(s.autopilot)
    test = client.get("/api/workflow/autopilot", headers=auth).json()["tests"][0]
    assert test["status"] == "key_missing"
    junk = client.post("/api/workflow/autopilot/answer-key", headers=auth, data={"key": test["key"]},
                       files={"answer_key": ("key.pdf", b"not a PDF")})
    assert junk.status_code == 400 and "readable PDF" in junk.text
    missing = client.post("/api/workflow/autopilot/answer-key", headers=auth, data={"key": "no such test"},
                          files={"answer_key": ("key.pdf", given_pdf())})
    assert missing.status_code == 404
    given = client.post("/api/workflow/autopilot/answer-key", headers=auth, data={"key": test["key"]},
                        files={"answer_key": ("Model Answer.pdf", given_pdf())})
    assert given.status_code == 200, given.text
    assert given.json()["tests"][0]["key_name"] == "Model Answer.pdf"
    finish(s.autopilot)
    assert client.get("/api/workflow/autopilot", headers=auth).json()["tests"][0]["status"] == "done"
