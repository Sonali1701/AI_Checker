"""Operator's request of 30 Oct 2026 (build 150): assign, upload, marks and feedback by the portal's
own requests. The requests are the page's, read from its code and from evaluations it saved.
A fake portal stands in here; nothing reaches the network."""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import portal_api
from portal_api import CopyMovedOn, EvaluatorNotListed, PortalApi, PortalBusy, PortalApiError, PortalLoginRefused, UseTheBrowser

TEST, TASK, STUDENT, EVALUATOR = "t" * 24, "c" * 24, "s" * 24, "e" * 24
ROW = {"test_id": TEST, "submission_id": TASK, "student_id": STUDENT, "student_name": "A Student", "program_name": "P",
       "test_series_name": "S", "test_name": "Maths"}
OPTIONS = {"evaluator_email": "teacher@pw.live", "deadline": "2026-10-31T12:00"}
DETAILS = {"_id": TEST, "name": "Maths", "totalQuestions": 3, "totalMarks": 10, "language": "English", "assignedTeachers": [],
           "subjectExpertise": {"_id": "sub"}, "categoryId": {"_id": "cat", "name": "Batch"}, "programId": {"_id": "pro", "name": "P"},
           "isAnnotationEnabled": False}


class Reply:
    def __init__(self, body=None, status=200, headers=None):
        self.body, self.status_code, self.headers = body or {}, status, headers or {}
        self.ok, self.text = status < 400, json.dumps(self.body)

    def json(self):
        return self.body


class FakePortal:
    """The portal's routes, with the state one copy goes through."""
    def __init__(self, status="Submitted", checked_by=None):
        self.status, self.checked_by, self.file, self.evaluation = status, checked_by, None, []
        self.calls, self.fail = [], {}
        self.logins = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.logins += 1
        return self.fail.get("login") or Reply({"data": {"access_token": f"token{self.logins}", "_id": EVALUATOR}})

    def put(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("S3", url, headers, len(data)))
        return self.fail.get("s3") or Reply()

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.replace(portal_api.BASE, "")
        self.calls.append((method, path, json))
        for piece, reply in self.fail.items():
            if piece in path:
                return reply
        if "/details" in path:
            return Reply({"data": DETAILS})
        if "allotment-map" in path:
            return Reply({"data": [{"id": EVALUATOR, "email": "Teacher@pw.live", "name": "T"}], "paginate": {"totalCount": 1}})
        if "/students?" in path:
            return Reply({"data": [{"_id": TASK, "testId": TEST, "studentObj": {"_id": STUDENT, "firstName": "A"},
                                    "testActivityStatus": self.status, "checkedBy": self.checked_by, "fileUrl": self.file,
                                    "evaluationObj": self.evaluation, "deadline": "d"}]})
        if "presigned-url" in path:
            return Reply({"data": {"uploadURL": "https://store.example/put"}})
        if path.endswith("/assign"):
            self.status, self.checked_by = "InEvaluation", json["assignId"]
        elif path.endswith("/file"):
            self.file = json["fileUrl"]
        elif "/tasks/evaluation/" in path:
            self.status = "InReview"
            self.evaluation = [{"checkedFile": json["checkedFile"], "score": json["score"]}]
        return Reply({"data": {}})

    def paths(self, method):
        return [call[1] for call in self.calls if call[0] == method]


@pytest.fixture(autouse=True)
def fresh_evaluator_list(monkeypatch):
    monkeypatch.setattr(portal_api, "_evaluators", {})
    monkeypatch.setattr(portal_api, "_evaluators_at", 0.0)


def client(portal):
    return PortalApi("teacher@pw.live", "secret", http=portal)


def checked(tmp_path):
    pdf = tmp_path / "checked.pdf"
    pdf.write_bytes(b"%PDF checked")
    return pdf


def test_a_copy_is_assigned_with_the_pages_request_and_read_back():
    portal = FakePortal()
    before = []
    outcome = client(portal).assign(ROW, OPTIONS, lambda: before.append(portal.status))
    assert outcome["result"] == "assigned" and before == ["Submitted"]
    put = next(call for call in portal.calls if call[0] == "PUT")
    assert put[1] == f"/tev/tests/{TEST}/students/{TASK}/assign"
    assert put[2]["assignId"] == EVALUATOR and put[2]["totalQuestions"] == 3 and len(put[2]["deadline"]) == 19
    assert "/tev/v2/notifications" in portal.paths("POST")


def test_assignment_follows_the_portal_state():
    assert client(FakePortal("InEvaluation", EVALUATOR)).assign(ROW, OPTIONS, lambda: None)["result"] == "already"
    assert client(FakePortal("InEvaluation", "someone")).assign(ROW, OPTIONS, lambda: None)["result"] == "reassigned"
    finished = FakePortal("InReview", "someone")
    assert client(finished).assign(ROW, OPTIONS, lambda: None)["result"] == "finished" and not finished.paths("PUT")


def test_an_id_that_is_not_an_evaluator_stops_as_the_browser_path_does():
    with pytest.raises(EvaluatorNotListed) as stopped:
        client(FakePortal()).assign(ROW, {**OPTIONS, "evaluator_email": "reviewer@pw.live"}, lambda: None)
    assert workflow.run_level_cause(str(stopped.value)) is not None


def test_another_students_row_is_never_written_to():
    portal = FakePortal()
    with pytest.raises(UseTheBrowser):
        client(portal).assign({**ROW, "student_id": "x" * 24}, OPTIONS, lambda: None)
    assert not portal.paths("PUT")


def test_marks_feedback_and_the_pdf_go_up_in_the_pages_order(tmp_path):
    portal = FakePortal("InEvaluation", EVALUATOR)
    before, keys = [], []
    outcome = client(portal).submit(ROW, checked(tmp_path), {1: 2, 2: 1.5}, 3.5, "Good work", lambda: before.append(portal.file),
                                    keys.append)
    assert outcome == {"result": "done", "status": "InReview"} and before == keys and keys[0].startswith(f"{TEST}/{TASK}/")
    assert [call[0] for call in portal.calls if call[0] in ("S3", "PUT")] == ["S3", "PUT", "PUT"]
    s3 = next(call for call in portal.calls if call[0] == "S3")
    assert s3[2] == {"Content-Type": "application/pdf"}
    assert portal.paths("PUT")[0] == f"/tev//tests/{TEST}/students/{TASK}/file"
    body = next(call[2] for call in portal.calls if call[0] == "PUT" and "/tasks/evaluation/" in call[1])
    assert body["questionsScore"] == ["2", "1.5", "0"] and body["score"] == 3.5 and body["comment"] == "Good work"
    assert body["checkedFile"] == keys[0] and body["evaluatorId"] == EVALUATOR and body["type"] == "test"
    assert [entry["tag"] for entry in body["rating"]] == list(portal_api.RATING_TAGS) and all(entry["rating"] == 0 for entry in body["rating"])
    assert body["subjectExpertise"] == ["sub"] and body["language"] == ["English"] and body["testCategoryId"] == "cat"
    assert "teacherId" not in body


def test_nothing_is_uploaded_for_a_copy_that_is_not_this_evaluators(tmp_path):
    portal = FakePortal("InEvaluation", "someone")
    with pytest.raises(CopyMovedOn):
        client(portal).submit(ROW, checked(tmp_path), {1: 2}, 2, "", lambda: None)
    assert not portal.paths("PUT") and not portal.paths("S3")


@pytest.mark.parametrize("marks,total", [({1: 5}, 4), ({9: 1}, 1), ({1: 11}, 11)])
def test_marks_the_portal_would_refuse_never_leave(tmp_path, marks, total):
    portal = FakePortal("InEvaluation", EVALUATOR)
    with pytest.raises(UseTheBrowser):
        client(portal).submit(ROW, checked(tmp_path), marks, total, "", lambda: None)
    assert not portal.paths("PUT")


def test_a_retry_after_the_upload_does_not_send_the_pdf_twice(tmp_path):
    portal = FakePortal("InEvaluation", EVALUATOR)
    portal.file = "existing/key.pdf"
    client(portal).submit({**ROW, "portal_file": "existing/key.pdf"}, checked(tmp_path), {1: 1}, 1, "", lambda: None)
    assert not portal.paths("S3") and not any(path.endswith("/file") for path in portal.paths("PUT"))


def test_a_lost_login_signs_in_once_and_a_wrong_password_only_once(tmp_path):
    portal = FakePortal()
    api = client(portal)
    api.sign_in()
    portal.fail["/details"] = Reply(status=401)
    with pytest.raises(PortalApiError):
        api.details(TEST)
    assert portal.logins == 2
    refused = FakePortal()
    refused.fail["login"] = Reply(status=400)
    with pytest.raises(PortalLoginRefused):
        client(refused).details(TEST)
    assert refused.logins == 1


def test_a_busy_portal_says_when_to_come_back():
    portal = FakePortal()
    portal.fail["/details"] = Reply(status=429, headers={"Retry-After": "30"})
    with pytest.raises(PortalBusy) as busy:
        client(portal).details(TEST)
    assert busy.value.retry_after == 30


def test_a_lost_notification_changes_nothing(tmp_path):
    portal = FakePortal()
    portal.fail["notifications"] = Reply(status=500)
    assert client(portal).assign(ROW, OPTIONS, lambda: None)["result"] == "assigned"


def test_marks_the_portal_did_not_keep_are_noticed(tmp_path):
    portal = FakePortal("InEvaluation", EVALUATOR)
    portal.fail["/tasks/evaluation/"] = Reply({"data": {}})      # 200, but nothing changed
    with pytest.raises(PortalApiError, match="did not save"):
        client(portal).submit(ROW, checked(tmp_path), {1: 1}, 1, "", lambda: None)


def test_verification_reads_the_copy_and_changes_nothing():
    assert client(FakePortal("Submitted")).verify_assignment(ROW, OPTIONS)["result"] == "not_done"
    assert client(FakePortal("InEvaluation", EVALUATOR)).verify_assignment(ROW, OPTIONS)["result"] == "done"
    assert client(FakePortal("InEvaluation", "x")).verify_assignment(ROW, OPTIONS)["result"] == "not_done"
    assert client(FakePortal("InEvaluation", EVALUATOR)).verify_evaluation(ROW)["result"] == "not_done"
    done = FakePortal("InReview", EVALUATOR)
    assert client(done).verify_evaluation(ROW)["result"] == "done" and not done.paths("PUT")


# ------------------------------------------------------------- the wrapper on Portal

def wrapper(portal_fake, tmp_path):
    """A Portal with no Chrome: its browser path only records that it was used."""
    portal = workflow.Portal.__new__(workflow.Portal)
    portal.direct_clients, portal.direct_off = {"teacher@pw.live": PortalApi("teacher@pw.live", "secret", http=portal_fake)}, ""
    portal.admin, portal.evaluator = "teacher@pw.live", "teacher@pw.live"
    portal.login_password, portal.login_report = (lambda account: "secret"), None
    portal.direct_refusals = 0
    portal.browser_calls = []
    portal.perform = lambda stage, *args: portal.browser_calls.append(stage) or {"result": "browser"}
    return portal


REPORT = {"questions": [{"qid": "Q1", "score": 2}], "total_score": 2, "overall_remarks": "Well done"}


def test_a_copy_is_uploaded_with_its_marks_through_the_wrapper(tmp_path):
    portal, row = wrapper(FakePortal("InEvaluation", EVALUATOR), tmp_path), dict(ROW)
    assert portal.perform_direct("upload", row, OPTIONS, checked(tmp_path), REPORT)["result"] == "done"
    assert row["portal_upload"] is True and row["portal_file"] and not portal.browser_calls


def test_a_rejection_goes_up_with_zero_marks_and_its_reason(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    portal = wrapper(fake, tmp_path)
    portal.perform_direct("reject", dict(ROW), OPTIONS, checked(tmp_path), reason="Typed pages")
    body = next(call[2] for call in fake.calls if call[0] == "PUT" and "/tasks/evaluation/" in call[1])
    assert body["score"] == 0 and body["questionsScore"] == ["0", "0", "0"] and body["comment"] == "Typed pages"


def test_the_browser_takes_over_a_copy_the_client_cannot_handle(tmp_path):
    portal, row = wrapper(FakePortal("Submitted"), tmp_path), dict(ROW)
    assert portal.perform_direct("upload", row, OPTIONS, checked(tmp_path), REPORT) == {"result": "browser"}
    assert portal.browser_calls == ["upload"] and "through the browser" in row["note"] and not portal.direct_off


def test_one_refused_copy_is_not_worth_stopping_for_but_three_in_a_row_are(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    fake.fail["/file"] = Reply(status=400)
    portal = wrapper(fake, tmp_path)
    for tries in range(workflow.DIRECT_REFUSALS_ALLOWED - 1):
        assert portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT) == {"result": "browser"}
        assert not portal.direct_off
    assert portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT) == {"result": "browser"}
    assert "refused" in portal.direct_off
    calls = len(fake.calls)
    portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT)
    assert len(fake.calls) == calls and portal.browser_calls == ["upload"] * 4


def test_a_success_clears_the_refusals(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    fake.fail["/file"] = Reply(status=400)
    portal = wrapper(fake, tmp_path)
    portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT)
    assert portal.direct_refusals == 1
    del fake.fail["/file"]
    portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT)
    assert portal.direct_refusals == 0


def test_marks_that_do_not_save_stop_direct_requests_at_once(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    fake.fail["/tasks/evaluation/"] = Reply({"data": {}})
    portal = wrapper(fake, tmp_path)
    with pytest.raises(RuntimeError):
        portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT, before_submit=lambda: None)
    assert "did not save" in portal.direct_off


def test_reading_a_copy_stays_direct_after_direct_requests_stop(tmp_path):
    portal = wrapper(FakePortal("InReview", EVALUATOR), tmp_path)
    portal.direct_off = "stopped"
    assert portal.perform_direct("verify_upload", dict(ROW), OPTIONS)["result"] == "done" and not portal.browser_calls


def test_a_full_mark_copy_gets_a_comment_the_portal_accepts(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    portal = wrapper(fake, tmp_path)
    full = {"questions": [{"qid": "Q1", "score": 10}], "total_score": 10, "max_total": 10, "overall_remarks": ""}
    assert portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), full)["result"] == "done"
    body = next(call[2] for call in fake.calls if call[0] == "PUT" and "/tasks/evaluation/" in call[1])
    assert body["comment"] == "Excellent work. You earned full marks."


def test_feedback_is_never_blank_and_never_replaces_real_remarks():
    assert workflow.portal_feedback({"overall_remarks": "  Review Q3.  ", "total_score": 5, "max_total": 10}) == "Review Q3."
    assert workflow.portal_feedback({"overall_remarks": "", "total_score": 5, "max_total": 10}).startswith("Your copy has been checked")
    assert workflow.portal_feedback({"total_score": 10}).startswith("Your copy has been checked")
    full = workflow.portal_feedback({"total_score": 10, "max_total": 10})
    assert ";" not in full and "-" not in full


def test_a_wrong_answer_after_submit_is_never_sent_again(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    fake.fail["/tasks/evaluation/"] = Reply(status=400)
    portal = wrapper(fake, tmp_path)
    with pytest.raises(RuntimeError):
        portal.perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT, before_submit=lambda: None)
    assert not portal.browser_calls


def test_a_busy_portal_waits_without_costing_the_copy_an_attempt(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    fake.fail["/students?"] = Reply(status=429, headers={"Retry-After": "20"})
    with pytest.raises(workflow.PortalFetchError) as waited:
        wrapper(fake, tmp_path).perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT)
    assert waited.value.retry_after == 20


def test_no_answer_after_the_submit_is_read_from_the_portal(tmp_path):
    fake = FakePortal("InEvaluation", EVALUATOR)
    original = fake.request

    def lost(method, url, **kwargs):
        if "/tasks/evaluation/" in url:
            raise requests.ConnectionError("connection lost")
        return original(method, url, **kwargs)
    fake.request = lost
    with pytest.raises(RuntimeError):
        wrapper(fake, tmp_path).perform_direct("upload", dict(ROW), OPTIONS, checked(tmp_path), REPORT, before_submit=lambda: None)


def test_no_saved_password_means_the_browser(tmp_path):
    portal = wrapper(FakePortal(), tmp_path)
    portal.direct_clients, portal.login_password = {}, (lambda account: "")
    assert portal.perform_direct("assign", dict(ROW), OPTIONS) == {"result": "browser"}


def test_a_run_asks_for_direct_requests_only_when_chosen():
    calls = []
    desk = SimpleNamespace(call=lambda stop, method, *args, **kwargs: calls.append(method))
    desks = SimpleNamespace(evaluator=lambda email: desk, administrator=lambda email: desk)
    workflow.DeskHandle(desks, None, "e@pw.live", "a@pw.live").perform("upload")
    workflow.DeskHandle(desks, None, "e@pw.live", "a@pw.live", direct=True).perform("upload")
    workflow.DeskHandle(desks, None, "e@pw.live", direct=True).exam_documents({})
    assert calls == ["perform", "perform_direct", "exam_documents_direct"]


def test_the_limit_on_copies_at_once_can_change_and_short_memory_holds_new_copies(monkeypatch):
    monkeypatch.setattr(workflow, "MIN_FREE_MEMORY_MB", 1200)
    free = {"mb": 5000}
    slots = workflow.GradingSlots(2, free_memory=lambda: free["mb"])
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False) and not slots.acquire(blocking=False)
    slots.limit = 3
    free["mb"] = 100
    assert not slots.acquire(blocking=False)
    free["mb"] = 5000
    assert slots.acquire(timeout=.01)
    for _ in range(3):
        slots.release()
    free["mb"] = 100
    assert slots.acquire(blocking=False)                 # one copy always may run


def test_a_tests_paper_and_key_are_prepared_once_for_all_its_copies(monkeypatch):
    import threading
    import time
    import server
    renders, orients = [], []

    def render(data, name):
        renders.append(name)
        time.sleep(.02)
        return [b"page"]

    def orient(data, name):
        orients.append(name)
        return data
    monkeypatch.setattr(server, "pdf_or_image_to_pngs", render)
    monkeypatch.setattr(server, "pdf_text_layer", lambda data, name: "a key with a real text layer, long enough to be read")
    monkeypatch.setattr(server, "_SHARED_DOCUMENTS", type(server._SHARED_DOCUMENTS)())
    payload = {"qp_bytes": b"paper", "qp_name": "question_paper.pdf", "ak_bytes": b"key", "ak_name": "answer_key.pdf"}
    text = "a paper with a real text layer, long enough to be read"
    results = []
    threads = [threading.Thread(target=lambda: results.append(server._shared_exam_documents(payload, orient, None, text))) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 6 and renders == ["question_paper.pdf", "answer_key.pdf"] and len(orients) == 2
    assert all(result[1] == [b"page"] and result[0] == b"paper" for result in results)
    assert results[0][1] is not results[1][1]                   # each job may change its own list
    server._shared_exam_documents({**payload, "ak_bytes": b"another key"}, orient, None, text)
    assert len(renders) == 4                                    # another key is another test


def test_direct_requests_are_a_run_option_that_stays_off_unless_ticked():
    base = {"evaluator_email": "e@pw.live", "deadline": "2099-01-01T10:00", "category": "school", "student_class": "10",
            "subject": "Maths", "total_marks": "10"}
    assert workflow.validate_options(base)["portal_direct"] is False
    assert workflow.validate_options({**base, "portal_direct": True})["portal_direct"] is True
