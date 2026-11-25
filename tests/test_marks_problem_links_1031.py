"""Operator's request of 31 Oct 2026: a marks problem shows the paper and its link. The student sheet
has no paper link, so the link is the portal's, kept when the paper is fetched by test id. Marks
problems saved before that are read through the pair's own portal session. Fixture portal; no network.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot as autopilot
from portal_vendor import match_paper
from test_integrated_workflow import pdf
from test_autopilot_tabs_1031 import tabbed
from test_workflow_autopilot import OWNER, PAIRS, PairPortal, copy, finish, pilot

PAPER = "https://static.pw.live/paper-{}.pdf"
KEYS = ["https://static.pw.live/key-a.pdf", "https://static.pw.live/key-b.pdf"]


def refuse_the_marks(paper, options, authorization):
    return {"ok": False, "message": "The question rows add up to 60."}


def portal_with_links(monkeypatch, reads=None):
    def exam_documents(self, test, progress=lambda text: None):
        if reads is not None:
            reads.append(test["test_id"])
        return {"question_paper": [("paper.pdf", pdf())], "answer_key": [("key.pdf", pdf())],
                "links": {"question_paper": PAPER.format(test["test_id"]), "answer_key": KEYS}}
    monkeypatch.setattr(PairPortal, "exam_documents", exam_documents)


def run_to_a_marks_problem(tmp_path, rows):
    s, ap = pilot(tmp_path, refuse_the_marks)
    ap.start(OWNER, ap.plan(OWNER, rows, PAIRS[:1])["plan_id"])
    finish(ap)
    return s, ap


def test_the_portal_gives_the_paper_and_model_answer_links():
    portal = SimpleNamespace(engine=None, papers=match_paper, download=lambda url: pdf())
    details = {"questionFileId": {"key": "paper.pdf"}, "solutions": [{"fileId": {"baseUrl": "https://files.pw.live/", "key": "key.pdf"}}]}
    fetched = workflow.Portal.documents_of(portal, details, {"test_name": "Test", "test_id": "t"}, lambda text: None)
    assert fetched["links"] == {"question_paper": "https://static.pw.live/paper.pdf", "answer_key": ["https://files.pw.live/key.pdf"]}
    documents = workflow.prepare_exam_documents(fetched)
    assert sorted(documents) == ["answer_key", "question_paper"]


def test_a_marks_problem_shows_the_paper_and_its_links(tmp_path, monkeypatch):
    portal_with_links(monkeypatch)
    _, ap = run_to_a_marks_problem(tmp_path, [copy(1, "A")])
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "marks_problem" and "add up to 60" in test["detail"]
    assert test["paper_link"] == PAPER.format("testA") and test["key_links"] == KEYS


def test_problems_saved_without_links_are_read_once_per_test(tmp_path, monkeypatch):
    rows = tabbed(("first", [copy(1, "A")]), ("second", [copy(2, "A"), copy(3, "B")]))
    _, ap = run_to_a_marks_problem(tmp_path, rows)
    assert not any(test["paper_link"] for test in ap.status(OWNER)["tests"])
    reads = []
    portal_with_links(monkeypatch, reads)
    result = ap.paper_links(OWNER)
    assert sorted(reads) == ["testA", "testB"]
    assert [test["paper_link"] for test in result["tests"]] == [PAPER.format("testA"), PAPER.format("testA"), PAPER.format("testB")]
    assert ap.paper_links(OWNER)["tests"][0]["key_links"] == KEYS and len(reads) == 2


def test_the_links_are_read_after_a_restart_too(tmp_path, monkeypatch):
    s, ap = run_to_a_marks_problem(tmp_path, [copy(1, "A")])
    restarted = autopilot.Autopilot(s, tmp_path)
    s.autopilot = restarted
    portal_with_links(monkeypatch)
    assert restarted.paper_links(OWNER)["tests"][0]["paper_link"] == PAPER.format("testA")
    assert autopilot.Autopilot(s, tmp_path).status(OWNER)["tests"][0]["paper_link"] == PAPER.format("testA")


ENTERED = {"items": [{"qid": "Q1", "max": 10}, {"qid": "Q2", "max": 10}, {"qid": "Q3", "max": 10}, {"qid": "Q4", "max": 10}],
           "choices": [{"select": 2, "options": [["Q2"], ["Q3"], ["Q4"]]}]}


def checker_that_takes_entered_marks(seen=None):
    def check(paper, options, authorization):
        if seen is not None:
            seen.append(options.get("operator_marks"))
        if options.get("operator_marks"):
            return {"ok": True, "method": "operator", "items": options["operator_marks"]["items"]}
        return {"ok": False, "message": "The rows add up to 60.", "items": [{"qid": "Q1", "max": 60}],
                "choice_groups": [{"select_count": 1, "options": [{"question_ids": ["Q1"]}, {"question_ids": ["Q2"]}]}]}
    return check


def marks_problem_autopilot(tmp_path, rows, checker):
    s, ap = pilot(tmp_path, checker)
    ap.start(OWNER, ap.plan(OWNER, rows, PAIRS[:1])["plan_id"])
    finish(ap)
    return s, ap


def test_the_form_opens_with_what_was_read(tmp_path):
    _, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], checker_that_takes_entered_marks())
    test = ap.status(OWNER)["tests"][0]
    assert test["marks_items"] == [{"qid": "Q1", "max": 60}] and test["marks_choices"][0]["select_count"] == 1


def test_entered_marks_run_every_tab_of_the_test_after_the_autopilot_finished(tmp_path):
    seen = []
    rows = tabbed(("first", [copy(1, "A")]), ("second", [copy(2, "A"), copy(3, "B")]))
    s, ap = marks_problem_autopilot(tmp_path, rows, checker_that_takes_entered_marks(seen))
    assert ap.state["status"] == "finished"
    key = ap.status(OWNER)["tests"][0]["key"]
    assert ap.retry_test(OWNER, key, "30", ENTERED, "operator@pw.live")["status"] == "running"
    finish(ap)
    tests = ap.status(OWNER)["tests"]
    assert [test["status"] for test in tests] == ["done", "done", "marks_problem"] and ap.state["status"] == "finished"
    for test in tests[:2]:
        options = s.read(test["run_id"])["options"]
        assert float(options["total_marks"]) == 30 and options["operator_marks"]["by"] == "operator@pw.live"
        assert [row["qid"] for row in options["operator_marks"]["items"]] == ["Q1", "Q2", "Q3", "Q4"]
    assert seen[-1]["choices"] == ENTERED["choices"]


def test_marks_the_check_refuses_come_back_to_the_list_with_the_reason(tmp_path):
    def refuse_everything(paper, options, authorization):
        entered = options.get("operator_marks") or {}
        return {"ok": False, "message": "The marks entered add up to 40, but the official total is 30.",
                "items": entered.get("items") or [], "operator_choices": entered.get("choices") or []}
    _, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], refuse_everything)
    ap.retry_test(OWNER, ap.status(OWNER)["tests"][0]["key"], "30", ENTERED, "operator@pw.live")
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "marks_problem" and "add up to 40" in test["detail"]
    assert [row["qid"] for row in test["marks_items"]] == ["Q1", "Q2", "Q3", "Q4"] and test["marks_choices"] == ENTERED["choices"]


def test_check_again_reads_the_paper_again_and_drops_earlier_marks(tmp_path):
    seen = []

    def check(paper, options, authorization):
        seen.append(options.get("operator_marks"))
        return {"ok": False, "message": "A false alarm.", "items": []} if len(seen) == 1 else {"ok": True, "method": "ai", "items": []}

    _, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], check)
    ap.retry_test(OWNER, ap.status(OWNER)["tests"][0]["key"])
    finish(ap)
    assert ap.status(OWNER)["tests"][0]["status"] == "done" and not any(seen)


def test_only_a_marks_problem_can_be_sent_back(tmp_path):
    _, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], lambda *args: {"ok": True, "method": "ai", "items": []})
    assert ap.status(OWNER)["tests"][0]["status"] == "done"
    with pytest.raises(ValueError, match="no longer has a marks problem"):
        ap.retry_test(OWNER, ap.status(OWNER)["tests"][0]["key"])
    with pytest.raises(LookupError, match="not in the autopilot"):
        ap.retry_test(OWNER, "no such test")


def test_a_stopped_autopilot_is_not_started_by_a_correction(tmp_path):
    _, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], refuse_the_marks)
    ap.state["status"] = "stopped"
    with pytest.raises(ValueError, match="was stopped"):
        ap.retry_test(OWNER, ap.status(OWNER)["tests"][0]["key"], "30", ENTERED)
    assert ap.status(OWNER)["tests"][0]["status"] == "marks_problem" and not ap.running()


@pytest.mark.parametrize("total", ["", "0", "not a number", None])
def test_entered_marks_need_a_total(tmp_path, total):
    _, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], refuse_the_marks)
    with pytest.raises(ValueError, match="official total"):
        ap.retry_test(OWNER, ap.status(OWNER)["tests"][0]["key"], total, ENTERED)


def test_a_correction_after_a_restart_starts_the_pairs(tmp_path):
    s, ap = marks_problem_autopilot(tmp_path, [copy(1, "A")], checker_that_takes_entered_marks())
    restarted = autopilot.Autopilot(s, tmp_path)
    restarted.run_poll, restarted.retry_seconds, restarted.sign_in_poll = 0.02, 0, 0.02
    s.autopilot = restarted
    restarted.retry_test(OWNER, restarted.status(OWNER)["tests"][0]["key"], "30", ENTERED, "operator@pw.live")
    finish(restarted)
    assert restarted.status(OWNER)["tests"][0]["status"] == "done"


def test_a_test_waits_for_the_internet_without_using_a_try(tmp_path, monkeypatch):
    s, ap = pilot(tmp_path)
    failures = []

    def exam_documents(self, test, progress=lambda text: None):
        if len(failures) < 8:
            failures.append(test["test_id"])
            raise OSError("no internet")
        return {"question_paper": [("paper.pdf", pdf())], "answer_key": [("key.pdf", pdf())]}

    monkeypatch.setattr(PairPortal, "exam_documents", exam_documents)
    monkeypatch.setattr(workflow, "internet_is_down", lambda: len(failures) < 8)
    ap.start(OWNER, ap.plan(OWNER, [copy(1, "A")], PAIRS[:1])["plan_id"])
    finish(ap)
    assert ap.status(OWNER)["tests"][0]["status"] == "done" and len(failures) == 8


def test_no_autopilot_gives_no_links(tmp_path):
    _, ap = pilot(tmp_path)
    try:
        ap.paper_links(OWNER)
    except LookupError as exc:
        assert "No autopilot" in str(exc)
    else:
        raise AssertionError("expected no autopilot")


def test_the_page_names_each_problem_with_its_links():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "static", "workflow.html"), encoding="utf-8").read()
    script = open(os.path.join(root, "static", "workflow.js"), encoding="utf-8").read()
    assert all(f'id="{name}"' in page for name in ("apMarks", "apMarksTitle", "apMarksLinks", "apMarksRows"))
    assert "/api/workflow/autopilot/paper-links" in script and "showMarksProblems(plan ? [] : tests)" in script
