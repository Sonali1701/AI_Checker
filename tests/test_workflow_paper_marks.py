"""Operator's rule of 26 Oct 2026 (build 137): the question paper's marks are checked before a
workflow run starts or resumes; a paper whose marks cannot be read or do not add up to the
official total starts nothing and says why; a person can enter the marks instead.

Workflow tests use a fixture checker. Server tests run the real check, entered-marks proof and
grading bridge with authentication, the paid marks reader and the grading worker replaced.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
from test_integrated_workflow import FakePortal, LocalClient, options, pdf, row, store, token, wait_idle

ENTERED = {"items": [{"qid": "Q1", "max": 10}, {"qid": "Q2", "max": 10}, {"qid": "Q3", "max": 10}, {"qid": "Q4", "max": 10}],
           "choices": [{"select": 2, "options": [["Q2"], ["Q3"], ["Q4"]]}]}


# ------------------------------------------------------------------------------ workflow

class Checker:
    def __init__(self, ok=True, message="", items=()):
        self.ok, self.message, self.items, self.calls = ok, message, list(items), []

    def __call__(self, paper, opts, authorization):
        self.calls.append((paper, dict(opts), authorization))
        if self.ok:
            return {"ok": True, "method": "ai_verified", "items": self.items}
        return {"ok": False, "message": self.message, "items": self.items,
                "choice_groups": [{"group_id": "g", "select_count": 2,
                                   "options": [{"option_id": "a", "question_ids": ["Q2"]}, {"option_id": "b", "question_ids": ["Q3"]},
                                               {"option_id": "c", "question_ids": ["Q4"]}]}],
                "paper_total": 40}


def app_with(tmp_path, checker):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), store(tmp_path).grader, tmp_path, paper_checker=checker)
    s.portal_factory, s.downloader = FakePortal, store(tmp_path).downloader
    return app, s, LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}


def start(client, auth, opts):
    return client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(opts)},
                       files={"question_paper": ("paper.pdf", pdf()), "answer_key": ("key.pdf", pdf())})


def test_a_paper_whose_marks_do_not_add_up_starts_nothing_and_says_why(tmp_path):
    checker = Checker(ok=False, message="The questions add up to 30, but the official total is 40.",
                      items=[{"qid": "Q1", "max": 10}, {"qid": "Q2", "max": 10}])
    app, s, client, auth = app_with(tmp_path, checker)
    response = start(client, auth, options())
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"].startswith("Not started: the question paper's marks need fixing first.")
    assert "add up to 30" in detail["message"] and detail["marks"]["items"][0] == {"qid": "Q1", "max": 10}
    assert detail["marks"]["choice_groups"][0]["select_count"] == 2
    assert client.get("/api/workflow/runs", headers=auth).json()["runs"] == []     # no run, nothing touched
    assert FakePortal.events == [] and not s.active


def test_a_paper_that_checks_out_starts_and_keeps_its_reading(tmp_path):
    checker = Checker(items=[{"qid": "Q1", "max": 40}])
    app, s, client, auth = app_with(tmp_path, checker)
    response = start(client, auth, options())
    assert response.status_code == 200, response.text
    wait_idle(s)
    run = s.read(response.json()["id"])
    assert run["marks_check"]["ok"] is True and run["marks_check"]["items"] == [{"qid": "Q1", "max": 40}]
    assert run["rows"][0]["stage"] == "uploaded" and len(checker.calls) == 2      # on Start, and before the run touched a copy
    assert checker.calls[0][0] == (s.folder(run["id"]) / "question_paper.pdf").read_bytes()


def test_entered_marks_are_checked_and_carry_who_entered_them(tmp_path):
    checker = Checker()
    app, s, client, auth = app_with(tmp_path, checker)
    s.owner_emails[workflow.owner_for(token("alice"))] = "alice@pw.live"
    chosen = options(); chosen["operator_marks"] = ENTERED; chosen["total_marks"] = "30"
    response = start(client, auth, chosen)
    assert response.status_code == 200, response.text
    wait_idle(s)
    sent = checker.calls[0][1]["operator_marks"]
    assert sent["by"] == "alice@pw.live" and sent["choices"] == [{"select": 2, "options": [["Q2"], ["Q3"], ["Q4"]]}]
    assert [item["max"] for item in sent["items"]] == [10.0, 10.0, 10.0, 10.0]
    for broken, reason in (({"items": [{"qid": "Q1", "max": 0}]}, "above 0"), ({"items": [{"qid": "", "max": 2}]}, "question number"),
                           ({"items": [{"qid": "Q1", "max": 2}, {"qid": "q1", "max": 2}]}, "listed twice"),
                           ({"items": [{"qid": "Q1", "max": 2}], "choices": [{"select": 2, "options": [["Q1"], ["Q2"]]}]}, "fewer than all")):
        bad = options(); bad["operator_marks"] = broken
        with pytest.raises(ValueError, match=reason):
            workflow.validate_options(bad)
    assert "operator_marks" not in workflow.validate_options({**options(), "operator_marks": None})


def test_resume_checks_the_paper_before_touching_any_copy(tmp_path):
    checker = Checker(ok=False, message="The questions add up to 30, but the official total is 40.", items=[{"qid": "Q1", "max": 30}])
    s = store(tmp_path); s.paper_checker = checker
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["status"] == "failed" and saved["error"].startswith("Stopped before touching any copy")
    assert "Change run inputs" in saved["error"] and saved["marks_check"]["items"] == [{"qid": "Q1", "max": 30}]
    assert FakePortal.events == [] and saved["rows"][0]["stage"] == "pending"
    assert s.alerts.raised[-1]["title"] == "Run could not continue: Maths Test"
    checker.ok = True
    s.work(run["id"], "token")
    assert s.read(run["id"])["rows"][0]["stage"] == "uploaded"


def test_change_run_inputs_takes_and_clears_entered_marks(tmp_path):
    app, s, client, auth = app_with(tmp_path, Checker())
    run = s.create([row()], options(), workflow.owner_for(token("alice")), pdf(), pdf())
    s.owner_emails[workflow.owner_for(token("alice"))] = "alice@pw.live"
    changed = client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"operator_marks": ENTERED, "total_marks": "30"})
    assert changed.status_code == 200, changed.text
    saved = s.read(run["id"])["options"]
    assert saved["operator_marks"]["by"] == "alice@pw.live" and saved["total_marks"] == "30.0"
    bad = client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"operator_marks": {"items": [{"qid": "Q1", "max": -1}]}})
    assert bad.status_code == 400 and "above 0" in bad.text
    cleared = client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"operator_marks": None})
    assert cleared.status_code == 200 and "operator_marks" not in s.read(run["id"])["options"]


# -------------------------------------------------------------------------------- server

def _pdf(text):
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


@pytest.fixture
def paper(tmp_path, monkeypatch):
    import server
    from grader import MarksItem, MarksScheme
    from marks_attestation import MarksAttestationStore
    reads, graded, lock = [], [], threading.Lock()
    found = {"scheme": MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=2)])}
    (tmp_path / "question_paper.pdf").write_bytes(_pdf("Physics\nMaximum Marks: 3\nAll questions are compulsory."))
    (tmp_path / "answer_key.pdf").write_bytes(_pdf("Q1: Official solution\nQ2: Official solution"))

    def read_marks(data, filename, config, *args, **kwargs):
        if config is None:          # the free reading the workflow checks AI against
            return None, "none"
        with lock:
            reads.append(data)
        return found["scheme"].model_copy(deep=True), "ai"

    def complete(payload):
        with lock:
            graded.append(payload)
            server.JOBS[payload["job_id"]].update(results=[dict(index=0, release=True)], pdfs={0: b"checked-copy"},
                                                  reports={0: dict(report={"total_score": 3})})

    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", MarksAttestationStore())
    monkeypatch.setattr(server, "_WORKFLOW_MARKS", {})
    monkeypatch.setattr(server, "_WORKFLOW_MARKS_LOCKS", {})
    monkeypatch.setattr(server, "_require_allowed", lambda _authorization: "offline-user")
    monkeypatch.setattr(server, "detect_marks_scheme", read_marks)
    monkeypatch.setattr(server, "_persist_job", lambda _job_id: None)
    monkeypatch.setattr(server, "_run_job", complete)
    monkeypatch.setattr(server, "JOBS", {})
    return server, tmp_path, reads, graded, found


def physics(**extra):
    return {"category": "school", "student_class": "Class 11", "subject": "Physics", "total_marks": "3", **extra}


def grade(server, folder, opts):
    source = folder / "student.pdf"
    source.write_bytes(_pdf("Student: Q1 and Q2 answers"))
    return server._workflow_grade(folder, source, opts, "Bearer offline")


def test_the_check_passes_a_paper_whose_marks_add_up_and_grading_reuses_that_reading(paper):
    server, folder, reads, graded, found = paper
    check = server._workflow_check_paper((folder / "question_paper.pdf").read_bytes(), physics(), "Bearer offline")
    assert check["ok"] is True and [item["qid"] for item in check["items"]] == ["Q1", "Q2"]
    assert grade(server, folder, physics())["release"] is True
    assert len(reads) == 1                                              # the check's reading is the one grading uses


def test_the_check_reports_marks_that_miss_the_total_with_what_was_read(paper):
    server, folder, reads, graded, found = paper
    from grader import MarksItem, MarksScheme
    found["scheme"] = MarksScheme(total=2, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=1)])
    check = server._workflow_check_paper((folder / "question_paper.pdf").read_bytes(), physics(), "Bearer offline")
    assert check["ok"] is False and check["message"] and [item["max"] for item in check["items"]] == [1.0, 1.0]
    (folder / "question_paper.pdf").write_bytes(_pdf("Physics\nMaximum Marks: 40\nAll questions are compulsory."))
    conflict = server._workflow_check_paper((folder / "question_paper.pdf").read_bytes(), physics(), "Bearer offline")
    assert conflict["ok"] is False and "conflicts with the paper header (40)" in conflict["message"]


def test_entered_marks_must_add_up_and_are_bound_to_the_paper(paper):
    server, folder, reads, graded, found = paper
    data = (folder / "question_paper.pdf").read_bytes()
    entered = {"items": [{"qid": "Q1", "max": 1}, {"qid": "Q2", "max": 1}], "choices": [], "by": "alice@pw.live"}
    with pytest.raises(ValueError, match="add up to 2, but the official total is 3"):
        server._workflow_operator_marks(data, physics(operator_marks=entered), "offline-user")
    choice = {"items": [{"qid": "Q1", "max": 1}, {"qid": "Q2", "max": 2}, {"qid": "Q3", "max": 2}],
              "choices": [{"select": 1, "options": [["Q2"], ["Q3"]]}], "by": "alice@pw.live"}
    detected = server._workflow_operator_marks(data, physics(operator_marks=choice), "offline-user")
    assert detected["method"] == "operator" and [item["qid"] for item in detected["items"]] == ["Q1", "Q2", "Q3"]
    proof = server._MARKS_ATTESTATIONS.find(data, 3, context=server._workflow_marks_context(physics(), "offline-user"))
    assert proof is not None and proof.token == detected["marks_attestation"] and proof.extractors == ("operator:alice@pw.live",)
    check = server._workflow_check_paper(data, physics(operator_marks=entered), "Bearer offline")
    assert check["ok"] is False and "add up to 2" in check["message"] and check["items"] == entered["items"]
    assert reads == []                                                   # entered marks never pay for a reading


def test_grading_with_entered_marks_uses_them_even_against_a_wrong_printed_total(paper):
    server, folder, reads, graded, found = paper
    (folder / "question_paper.pdf").write_bytes(_pdf("Physics\nMaximum Marks: 40\nAll questions are compulsory."))
    entered = {"items": [{"qid": "Q1", "max": 1}, {"qid": "Q2", "max": 2}], "choices": [], "by": "alice@pw.live"}
    assert server._workflow_check_paper((folder / "question_paper.pdf").read_bytes(), physics(operator_marks=entered),
                                        "Bearer offline")["ok"] is True
    assert grade(server, folder, physics(operator_marks=entered))["release"] is True
    assert reads == [] and len(graded) == 1
    rows = {item["qid"]: item["max"] for item in graded[0]["verified_marks_scheme"]["items"]}
    assert rows == {"Q1": 1.0, "Q2": 2.0}
