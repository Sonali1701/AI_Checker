# -*- coding: utf-8 -*-
"""PW Skills workflow against a stand-in for the admin portal (recording of 4 Nov 2026)."""
import json
import os
import sys
import threading
import time

import fitz
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import skills_portal as portal  # noqa: E402
import skills_workflow as sw  # noqa: E402


def pdf(text="Q1. Do it. Total Marks: 100"):
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    return document.tobytes()


def row(sid, lesson="L1", email="a@b.com", status="submitted", file="assignment/x.pdf", out_of=100, title="SQL", received=None):
    return {"courseId": "C1", "lessonId": {"_id": lesson, "title": title},
            "userId": {"firstName": "Vipul", "lastName": "Pandey", "email": email},
            "assignmentSubmission": {"_id": sid, "status": status, "submissionDate": "2026-11-04T08:51:00Z",
                                     "data": {"url": file}, "points": {"outOf": out_of, "received": received}}}


class Answer:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class FakePortal:
    """The calls the embedded admin app makes, with what the recording showed they do."""
    def __init__(self, rows):
        self.rows, self.grades, self.fail_next_grade, self.requests = list(rows), [], None, []
        self.lesson_body = {"title": "SQL", "assignmentV2": {"assignment": "learn/paper.pdf", "answerSheet": "learn/key.pdf", "points": 100}}

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        assert headers["Authorization"] == "Bearer TOKEN"
        self.requests.append((method, url, json))
        if "/filterAssignment" in url:
            rows = [r for r in self.rows
                    if (not json.get("assignmentStatus") or r["assignmentSubmission"]["status"] == json["assignmentStatus"])
                    and (not json.get("userEmail") or r["userId"]["email"] == json["userEmail"])
                    and (not json.get("userPhone") or r["userId"].get("primaryNumber") == json["userPhone"])]
            page, limit = int(url.split("page=")[1].split("&")[0]), int(url.split("limit=")[1])
            return Answer(200, {"success": True, "data": [{"metadata": [{"total": len(rows)}],
                                                           "data": rows[(page - 1) * limit:page * limit]}]})
        if "/v2/lesson" in url and method == "GET":
            return Answer(200, {"data": self.lesson_body})
        if "/grade/" in url:
            if len(json["comment"]) > 500:   # as the live portal answered on 12 Nov 2026
                return Answer(400, {"success": False, "message": "comment must not exceed 500 characters"})
            if self.fail_next_grade:
                failure, self.fail_next_grade = self.fail_next_grade, None
                return failure(self, url, json)
            return self.apply(url, json)
        if "/allcourses" in url:
            return Answer(200, {"data": {"batches": [{"_id": "C1", "name": "Data Analytics", "byName": "Pro_ON_MSF"}]}})
        raise AssertionError(url)

    def apply(self, url, body):
        self.grades.append((url.split("/grade/")[1], body))
        sid = url.split("/grade/")[1].split("/")[0]
        for r in self.rows:
            if r["assignmentSubmission"]["_id"] == sid:
                r["assignmentSubmission"]["status"] = "evaluated"
                r["assignmentSubmission"]["points"]["received"] = body["points"]["received"]
        return Answer(200, {"success": True})


class SignedIn:
    def __init__(self, http):
        self.http = http

    def client(self):
        return portal.SkillsClient("TOKEN", session=self.http, sleep=lambda _s: None)

    def ended(self, _token):
        pass


def report(total=86.0, maximum=100.0):
    return {"total_score": total, "max_total": maximum, "overall_remarks": "Your queries are right.",
            "questions": [{"qid": "Q1", "score": 5.0, "max_score": 6.5, "remark": "Rohit's 64,000 is below the average."},
                          {"qid": "Q2", "score": 6.5, "max_score": 6.5, "remark": ""}]}


def graded(result=None):
    def grade(folder, source, options, authorization, stop=None, progress=lambda s: None):
        grade.calls.append((folder, source, options))
        return result if result is not None else {"report": report(), "pdf": pdf("checked"), "release": False,
                                                  "review_reasons": ["Mentor review"], "error": ""}
    grade.calls = []
    return grade


def portal_files(url):
    """The stand-in CDN: the lesson's solution reads differently from a learner's copy, as
    real ones do (a copy identical to the solution is not marked)."""
    return pdf("Answer key. Q1: 42") if "key" in url else pdf()


def make(tmp_path, http, grade=None, fetch=None):
    return sw.SkillsStore(tmp_path, grade or graded(), sign_in=SignedIn(http), fetch=fetch or portal_files, pause=0)


def run_to_end(store, run):
    store.start(run["id"], "Bearer app")
    store.active[run["id"]]["thread"].join(20)
    return store.read(run["id"])


def new_run(store, **options):
    return store.create("owner", {"id": "C1", "title": "Data Analytics"}, {"titles": [], "most_copies": 0, "copies_at_once": 2, "auto_post": False, **options})


def test_only_not_evaluated_copies_are_fetched_checked_and_left_waiting(tmp_path):
    http = FakePortal([row("S1"), row("S2", email="c@d.com"), row("S3", email="e@f.com", status="evaluated")])
    store = make(tmp_path, http)
    run = run_to_end(store, new_run(store))
    assert run["status"] == "done", run["error"]
    assert sorted(c["id"] for c in run["copies"]) == ["S1", "S2"]
    assert {c["stage"] for c in run["copies"]} == {"ready"}
    assert http.grades == [], "nothing is posted until a person says so"


def test_the_paper_and_solution_come_from_the_lesson_once_for_all_its_copies(tmp_path):
    http, urls = FakePortal([row("S1"), row("S2", email="c@d.com")]), []
    store = make(tmp_path, http, fetch=lambda url: urls.append(url) or pdf())
    run_to_end(store, new_run(store))
    assert urls.count("https://cdn.pwskills.com/learn/paper.pdf") == 1
    assert urls.count("https://cdn.pwskills.com/learn/key.pdf") == 1
    assert urls.count("https://cdn.pwskills.com/assignment/x.pdf") == 2


def test_a_lesson_without_a_solution_is_still_checked_with_no_key_file(tmp_path):
    http = FakePortal([row("S1")])
    http.lesson_body["assignmentV2"]["answerSheet"] = ""
    grade = graded()
    store = make(tmp_path, http, grade)
    run = run_to_end(store, new_run(store))
    assert run["copies"][0]["stage"] == "ready"
    folder = grade.calls[0][0]
    assert (folder / "question_paper.pdf").exists() and not (folder / "answer_key.pdf").exists()


def test_a_solution_that_is_the_paper_again_is_not_used_as_the_key(tmp_path):
    # 13 Nov 2026: two Data Analytics assignments carry the question paper as their solution.
    paper, answer = pdf("Q1. Do it. Total Marks: 100"), pdf("answer")
    for solution in ("learn/paper.pdf", "learn/copy_of_paper.pdf"):
        http = FakePortal([row("S1")])
        http.lesson_body["assignmentV2"]["answerSheet"] = solution
        grade = graded()
        store = make(tmp_path / solution.replace("/", "_"), http, grade,
                     fetch=lambda url: paper if "learn/" in url else answer)
        run = run_to_end(store, new_run(store))
        assert run["copies"][0]["stage"] == "ready"
        folder = grade.calls[0][0]
        assert (folder / "question_paper.pdf").exists() and not (folder / "answer_key.pdf").exists(), solution
        assert run["lessons"]["L1"]["has_solution"] is False


def test_a_real_solution_is_kept(tmp_path):
    http = FakePortal([row("S1")])
    grade = graded()
    store = make(tmp_path, http, grade, fetch=lambda url: pdf("Answer: 42") if "key" in url else pdf())
    run = run_to_end(store, new_run(store))
    assert (grade.calls[0][0] / "answer_key.pdf").exists() and run["lessons"]["L1"]["has_solution"] is True


def test_a_run_prepared_before_the_check_drops_a_paper_saved_as_its_key(tmp_path):
    folder = tmp_path / "lesson"
    folder.mkdir()
    paper = pdf()
    (folder / "question_paper.pdf").write_bytes(paper)
    (folder / "answer_key.pdf").write_bytes(paper)
    assert sw.drop_paper_as_key(folder) and not (folder / "answer_key.pdf").exists()
    (folder / "answer_key.pdf").write_bytes(pdf("Answer: 42"))
    assert not sw.drop_paper_as_key(folder) and (folder / "answer_key.pdf").exists()


def test_a_lesson_without_a_paper_holds_its_copies_and_spends_nothing(tmp_path):
    http = FakePortal([row("S1")])
    http.lesson_body["assignmentV2"]["assignment"] = ""
    grade = graded()
    store = make(tmp_path, http, grade)
    run = run_to_end(store, new_run(store))
    assert run["copies"][0]["stage"] == "held" and "question paper" in run["copies"][0]["note"]
    assert grade.calls == []


def test_posting_sends_what_the_evaluate_button_sends(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http)
    run = run_to_end(store, new_run(store))
    store.start_posting(run["id"], ["S1"])
    store.active[run["id"]]["thread"].join(20)
    run = store.read(run["id"])
    assert run["copies"][0]["stage"] == "posted"
    path, body = http.grades[0]
    assert path == "S1/C1/L1"
    assert body["points"] == {"received": 86.0, "outOf": 100.0} and body["assignmentStatus"] == "submitted"
    assert "<p>Your queries are right.</p>" in body["comment"]
    assert "Q1: 5/6.5. Rohit's 64,000 is below the average." in body["comment"]
    assert "Q2" not in body["comment"], "a full-mark question needs no line"


def test_automatic_posting_needs_no_person(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http)
    run = run_to_end(store, new_run(store, auto_post=True))
    assert run["copies"][0]["stage"] == "posted" and len(http.grades) == 1


def test_a_paper_with_its_own_total_is_scaled_to_the_portals(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http, graded({"report": report(150.0, 200.0), "pdf": pdf("x"), "release": False, "error": ""}))
    run = run_to_end(store, new_run(store))
    copy = run["copies"][0]
    assert copy["marks"] == 75.0 and copy["out_of"] == 100.0
    assert "out of the paper's total of 200" in copy["feedback"]


def test_zero_marks_is_held_because_the_portal_form_refuses_it(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http, graded({"report": report(0.0, 100.0), "pdf": pdf("x"), "release": False, "error": ""}))
    run = run_to_end(store, new_run(store, auto_post=True))
    assert run["copies"][0]["stage"] == "held" and "0 marks" in run["copies"][0]["note"]
    assert http.grades == []


def test_a_cover_whose_work_cannot_be_reached_goes_to_a_person_at_once(tmp_path):
    # 19 Nov 2026: such a copy went to "Will be tried again"; a second check cannot change it.
    http = FakePortal([row("S1")])
    grade = graded({"report": None, "pdf": None, "release": False, "failure_code": "NOT_VERIFIED_ANSWER_SHEET",
                    "error": "This submission is a cover page and its work is elsewhere: no link. No marks were given."})
    store = make(tmp_path, http, grade)
    run = run_to_end(store, new_run(store, auto_post=True))
    copy = run["copies"][0]
    assert copy["stage"] == "held" and copy["attempts"] == 0 and "cover page" in copy["note"]
    assert len(grade.calls) == 1 and http.grades == []


def linked_cover(link, title="Probability Basics - Assignment Solution"):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), title)
    page.insert_text((72, 90), "Click to open the Excel file")
    page.insert_link({"kind": fitz.LINK_URI, "uri": link, "from": fitz.Rect(72, 78, 300, 94)})
    return document.tobytes()


@pytest.mark.parametrize("same", ["file", "links"])
def test_the_assignments_own_solution_uploaded_as_work_is_not_marked(tmp_path, same):
    solution = linked_cover("https://docs.google.com/spreadsheets/d/SOLUTIONsheet123/edit")
    learner = solution if same == "file" else linked_cover("https://docs.google.com/spreadsheets/d/SOLUTIONsheet123/edit",
                                                           title="My copy")
    http, grade = FakePortal([row("S1")]), graded()
    store = make(tmp_path, http, grade, fetch=lambda url: solution if "key" in url else learner if "assignment/" in url else pdf())
    run = run_to_end(store, new_run(store, auto_post=True))
    copy = run["copies"][0]
    assert copy["stage"] == "held" and "own solution file" in copy["note"]
    assert grade.calls == [] and http.grades == []


def test_a_learners_own_linked_work_is_checked(tmp_path):
    solution = linked_cover("https://docs.google.com/spreadsheets/d/SOLUTIONsheet123/edit")
    learner = linked_cover("https://docs.google.com/spreadsheets/d/LEARNERsheet456/edit", title="Asha Rao")
    http, grade = FakePortal([row("S1")]), graded()
    store = make(tmp_path, http, grade, fetch=lambda url: solution if "key" in url else learner if "assignment/" in url else pdf())
    run = run_to_end(store, new_run(store))
    assert run["copies"][0]["stage"] == "ready" and len(grade.calls) == 1


def test_a_wrong_assignment_is_held_not_posted(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http, graded({"rejected": True, "error": "Another paper's answers", "report": None}))
    run = run_to_end(store, new_run(store, auto_post=True))
    assert run["copies"][0]["stage"] == "held" and "Another paper" in run["copies"][0]["note"]
    assert http.grades == []


def ready_copy(tmp_path, http):
    store = make(tmp_path, http)
    run = run_to_end(store, new_run(store))
    return store, run, run["copies"][0]


def test_a_file_replaced_after_checking_is_not_posted(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    http.rows[0]["assignmentSubmission"]["data"]["url"] = "assignment/new.pdf"
    assert store.post_one(run, copy, SignedIn(http).client()) == "queued"
    assert http.grades == [] and copy["file"] == "assignment/new.pdf" and copy["marks"] is None


def test_a_copy_someone_else_evaluated_is_left_alone(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    http.rows[0]["assignmentSubmission"].update(status="evaluated")
    http.rows[0]["assignmentSubmission"]["points"]["received"] = 70
    assert store.post_one(run, copy, SignedIn(http).client()) == "already_evaluated"
    assert http.grades == [] and copy["posted_marks"] == 70


def test_a_copy_under_evaluation_is_left_alone(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    http.rows[0]["assignmentSubmission"]["status"] = "under_evaluation"
    assert store.post_one(run, copy, SignedIn(http).client()) == "held"
    assert http.grades == []


def test_a_post_whose_answer_was_lost_is_settled_by_reading_never_sent_twice(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)

    def lands_then_drops(portal_, url, body):
        portal_.apply(url, body)
        return Answer(503, {})
    http.fail_next_grade = lands_then_drops
    assert store.post_one(run, copy, SignedIn(http).client()) == "posted"
    assert len(http.grades) == 1


def test_a_post_that_never_arrived_goes_back_to_ready(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    http.fail_next_grade = lambda portal_, url, body: Answer(503, {})
    assert store.post_one(run, copy, SignedIn(http).client()) == "ready"
    assert http.grades == [] and "did not reach" in copy["note"]


def test_a_refusal_keeps_the_portals_own_words(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    http.fail_next_grade = lambda portal_, url, body: Answer(400, {"message": "Marks are invalid"})
    assert store.post_one(run, copy, SignedIn(http).client()) == "ready"
    assert "Marks are invalid" in copy["note"]


def test_an_app_restart_during_a_post_asks_the_portal(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    http.rows[0]["assignmentSubmission"]["status"] = "evaluated"
    store.change(run, copy, stage="posting")
    run = run_to_end(store, run)
    assert run["copies"][0]["stage"] == "posted"
    assert http.grades == []


def test_a_person_can_change_marks_and_feedback(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    store.edit(run["id"], "S1", 90, "Well done.")
    assert store.read(run["id"])["copies"][0]["marks"] == 90
    with pytest.raises(ValueError):
        store.edit(run["id"], "S1", 101, "Too many")
    with pytest.raises(ValueError):
        store.edit(run["id"], "S1", 0, "Portal refuses zero")


def long_report():
    """Ten questions that all lost marks, each with a long reason, out of 200."""
    questions = [{"qid": f"Q{n}", "score": float(10 - (n % 4)), "max_score": 10.0,
                  "remark": f"Question {n} misses the part where the layout must adapt on a small screen. "
                            f"The second part of the reason for Q{n} is here too."} for n in range(1, 11)]
    return {"total_score": 152.0, "max_total": 200.0, "questions": questions,
            "overall_remarks": "Good structure overall. Your HTML is semantic & tidy. "
                               "Work on responsive layouts and on naming things clearly. Keep practising."}


def test_feedback_fits_what_the_portal_takes(tmp_path):
    text = sw.feedback_text(long_report(), 100.0)
    assert sw.posted_length(text) <= sw.COMMENT_LIMIT == 500
    assert text.startswith("Good structure overall.")
    assert "Question marks are out of the paper's total of 200" in text
    # Q3 and Q7 lost most (3 each), so they come first with their reason.
    assert "Q3: 7/10. Question 3 misses" in text and "Q7: 7/10." in text
    named = text.split("Marks were also lost in ")[1]
    for n in range(1, 11):
        if n % 4:
            assert f"Q{n}:" in text or f"Q{n}" in named, f"Q{n} lost marks and is named"
        else:
            assert f"Q{n}:" not in text and f"Q{n}" not in named, f"Q{n} lost nothing"
    assert ";" not in text


def test_short_feedback_is_kept_whole():
    text = sw.feedback_text(report(), 100.0)
    assert text == "Your queries are right.\n\nWhere marks were lost:\nQ1: 5/6.5. Rohit's 64,000 is below the average."


def test_feedback_never_passes_the_limit_whatever_the_report():
    one_long = {**long_report(), "overall_remarks": "word " * 400}
    assert sw.posted_length(sw.feedback_text(one_long, 100.0)) <= 500
    many = {**long_report(), "questions": [{"qid": f"Q{n}.part({n})", "score": 1.0, "max_score": 2.0, "remark": "x " * 90}
                                           for n in range(1, 80)]}
    assert sw.posted_length(sw.feedback_text(many, 200.0)) <= 500


def test_feedback_written_before_the_limit_is_written_again_when_posted(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http, graded({"report": long_report(), "pdf": pdf("x"), "release": False, "error": ""}))
    run = run_to_end(store, new_run(store))
    copy = run["copies"][0]
    store.change(run, copy, feedback="Old long feedback. " * 60)   # as build 166 wrote it
    assert store.post_one(run, copy, SignedIn(http).client()) == "posted"
    comment = http.grades[0][1]["comment"]
    assert len(comment) <= 500 and comment.startswith("<p>Good structure overall.")


def test_feedback_a_person_wrote_is_never_cut_by_the_tool(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    with pytest.raises(ValueError, match="at most 500"):
        store.edit(run["id"], "S1", 90, "Too long. " * 60)
    store.edit(run["id"], "S1", 90, "Well done.")
    run, copy = store.find(run["id"], "S1")
    store.change(run, copy, feedback="Too long. " * 60)
    assert store.post_one(run, copy, SignedIn(http).client()) == "ready"
    assert http.grades == [] and "Shorten it" in copy["note"]


def test_the_page_is_told_the_limit(tmp_path):
    import asyncio
    import base64
    from types import SimpleNamespace
    from fastapi import FastAPI
    token = "h." + base64.urlsafe_b64encode(json.dumps({"sub": "me"}).encode()).decode().rstrip("=") + ".s"
    app = FastAPI()
    store = sw.install(app, lambda authorization: token, graded(), tmp_path / "s")
    run = new_run(store)
    store.save({**store.read(run["id"]), "owner": sw.owner_for(token)})
    detail = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/skills/runs/{run_id}")
    page = asyncio.run(detail(run["id"], SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")), "Bearer app"))
    assert page["comment_limit"] == 500 and "owner" not in page


class Batches:
    """The portal's batch search: a name filter over title and code, paged by skip and limit."""
    def __init__(self, courses, honours_skip=True):
        self.all, self.honours_skip, self.calls = courses, honours_skip, []

    def courses(self, name="", limit=50, skip=0):
        self.calls.append((name, limit, skip))
        hits = [c for c in self.all if name.casefold() in f"{c['title']} {c['batch']}".casefold()]
        start = skip if self.honours_skip else 0
        return hits[start:start + limit]


def many_batches():
    courses = [{"id": f"B{n:03d}", "title": "Data Analytics with  AI Course", "batch": f"Pro_ON_MSF_{n:03d}"} for n in range(240)]
    courses += [{"id": "EXACT", "title": "Data Analytics with  AI Course", "batch": "Pro_ON_MSF_Sept26"},
                {"id": "LONGER", "title": "Data Analytics with AI Course", "batch": "Pro_ON_MSF_Sept26_B"},
                {"id": "OTHER", "title": "Full Stack Development with AI", "batch": "FSDPWC_Feb_26"}]
    return courses


def test_the_exact_batch_comes_first_however_many_the_portal_finds():
    portal_ = Batches(many_batches())
    found = sw.find_batches(portal_, "pro_on_msf_sept26")
    assert found["courses"][0]["id"] == "EXACT" and found["courses"][0]["match"] == "exact"
    assert found["courses"][1]["id"] == "LONGER" and found["courses"][1]["match"] == "starts"
    assert found["exact"] == 1


def test_every_page_of_a_broad_search_is_read():
    portal_ = Batches(many_batches())
    found = sw.find_batches(portal_, "Data Analytics")
    assert len(found["courses"]) == 242 and not found["more"]
    assert [call[2] for call in portal_.calls] == [0, 100, 200]
    assert found["exact"] == 0 and {c["match"] for c in found["courses"]} == {"starts"}


def test_a_search_with_more_than_the_cap_says_so():
    found = sw.find_batches(Batches(many_batches()), "Data Analytics", most=150)
    assert len(found["courses"]) == 150 and found["more"]


def test_name_and_code_as_sheets_write_them_find_the_one_batch():
    portal_ = Batches(many_batches())
    found = sw.find_batches(portal_, "Data Analytics with AI Course | Pro_ON_MSF_Sept26")
    assert portal_.calls[0][0] == "Pro_ON_MSF_Sept26", "the code is searched first"
    assert found["exact"] == 1 and found["courses"][0]["id"] == "EXACT"


def test_a_portal_that_ignores_skip_is_not_read_forever():
    portal_ = Batches(many_batches(), honours_skip=False)
    found = sw.find_batches(portal_, "Data Analytics")
    assert len(found["courses"]) == 100 and len(portal_.calls) == 2


def test_an_empty_search_lists_the_first_batches_unranked():
    found = sw.find_batches(Batches(many_batches()), "  ")
    assert len(found["courses"]) == 50 and found["exact"] == 0


def test_a_copy_records_when_it_entered_its_stage(tmp_path):
    http = FakePortal([row("S1")])
    store, run, copy = ready_copy(tmp_path, http)
    before = copy["stage_at"]
    store.change(run, copy, note="only a note")
    assert copy["stage_at"] == before
    store.change(run, copy, stage="held")
    assert copy["stage_at"] >= before


def test_a_copy_a_second_run_finds_is_not_checked_twice(tmp_path):
    http, grade = FakePortal([row("S1")]), graded()
    store = make(tmp_path, http, grade)
    run_to_end(store, new_run(store))
    second = run_to_end(store, new_run(store))
    assert second["copies"] == [] and second["skipped_elsewhere"] == 1
    assert len(grade.calls) == 1


def test_most_copies_takes_the_oldest_first(tmp_path):
    http = FakePortal([row("NEW", email="n@x.com"), row("MID", email="m@x.com"), row("OLD", email="o@x.com")])
    store = make(tmp_path, http)
    run = run_to_end(store, new_run(store, most_copies=2))
    assert [c["id"] for c in run["copies"]] == ["OLD", "MID"]


def test_a_learners_link_is_handed_to_the_link_reader(tmp_path):
    http, urls = FakePortal([row("S1", file="https://github.com/a/b")]), []
    grade = graded()
    store = make(tmp_path, http, grade, fetch=lambda url: urls.append(url) or pdf())
    run_to_end(store, new_run(store))
    source = grade.calls[0][1]
    assert source.suffix == ".url" and "https://github.com/a/b" in source.read_text(encoding="utf-8")
    assert "https://github.com/a/b" not in urls


def test_a_photo_answer_is_turned_into_a_pdf(tmp_path):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    http, grade = FakePortal([row("S1", file="assignment/p.png")]), graded()
    store = make(tmp_path, http, grade, fetch=lambda url: pix.tobytes("png") if url.endswith(".png") else pdf())
    run_to_end(store, new_run(store))
    assert grade.calls[0][1].read_bytes().startswith(b"%PDF")


def test_no_token_ever_reaches_the_disk(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http)
    run_to_end(store, new_run(store))
    for path in tmp_path.rglob("*.json"):
        assert "TOKEN" not in path.read_text(encoding="utf-8")


def test_a_save_waits_out_a_file_another_program_has_open(tmp_path, monkeypatch):
    http = FakePortal([])
    store = make(tmp_path, http)
    run, real, refused = new_run(store), os.replace, []

    def replace(source, target):
        if len(refused) < 3:
            refused.append(target)
            raise PermissionError("in use")
        real(source, target)
    monkeypatch.setattr(sw.os, "replace", replace)
    store.finish(run, "stopped")
    assert len(refused) == 3 and store.read(run["id"])["status"] == "stopped"


def test_the_runs_list_reads_under_the_same_lock_as_a_save(tmp_path):
    store = make(tmp_path, FakePortal([]))
    new_run(store)
    finished = threading.Event()
    with store.lock:
        reader = threading.Thread(target=lambda: (store.runs_of("owner"), finished.set()), daemon=True)
        reader.start()
        assert not finished.wait(.3), "the list must wait for a save in progress"
    assert finished.wait(5)


def test_the_categorys_blanket_review_reason_is_not_shown_on_every_copy(tmp_path):
    import categories
    general = categories.get("innovation").review.review_reason
    http = FakePortal([row("S1")])
    store = make(tmp_path, http, graded({"report": report(), "pdf": pdf("x"), "release": False, "error": "",
                                         "review_reasons": [general, "Q1 could not be matched."]}))
    assert run_to_end(store, new_run(store))["copies"][0]["reasons"] == ["Q1 could not be matched."]


def test_a_computer_off_the_private_network_is_told_so():
    class NoRoute(FakePortal):
        def request(self, method, url, **kwargs):
            raise requests.ConnectTimeout("no route")
    with pytest.raises(portal.SkillsPortalBusy, match="private network"):
        SignedIn(NoRoute([])).client().courses()


def test_a_refused_sign_in_stops_the_run_for_a_new_one(tmp_path):
    class Expired(FakePortal):
        def request(self, method, url, **kwargs):
            return Answer(401, {"message": "jwt expired"})
    store = make(tmp_path, Expired([row("S1")]))
    run = run_to_end(store, new_run(store))
    assert run["status"] == "needs_sign_in"


def test_every_page_of_a_long_batch_is_read_each_row_once():
    http = FakePortal([row(f"S{n}", email=f"{n}@x.com") for n in range(120)])
    client = SignedIn(http).client()
    assert len(client.submissions("C1")) == 120


def test_a_portal_that_caps_the_page_size_loses_no_row():
    class Capped(FakePortal):
        def request(self, method, url, **kwargs):
            if "/filterAssignment" in url:
                url = url.split("limit=")[0] + "limit=" + str(min(int(url.split("limit=")[1]), 5))
            return super().request(method, url, **kwargs)
    http = Capped([row(f"S{n}", email=f"{n}@x.com") for n in range(23)])
    assert len(SignedIn(http).client().submissions("C1")) == 23


def test_a_run_for_test_learners_takes_only_their_copies(tmp_path):
    rows = [row("REAL1", email="real@x.com"), row("T1", email="t1@x.com"), row("T2", email="t2@x.com"), row("REAL2", email="r2@x.com")]
    rows[1]["userId"]["primaryNumber"] = "7974748131"
    rows[2]["userId"]["primaryNumber"] = "6204197002"
    http, grade = FakePortal(rows), graded()
    store = make(tmp_path, http, grade)
    run = run_to_end(store, new_run(store, learners=["+91 79747 48131", "6204197002"]))
    assert sorted(c["id"] for c in run["copies"]) == ["T1", "T2"] and len(grade.calls) == 2


def test_a_portal_that_ignores_the_learner_filter_lets_no_one_else_in(tmp_path):
    class Ignores(FakePortal):
        def request(self, method, url, json=None, **kwargs):
            if json:
                json = {key: value for key, value in json.items() if key not in ("userPhone", "userEmail")}
            return super().request(method, url, json=json, **kwargs)
    rows = [row("REAL1", email="real@x.com"), row("T1", email="t1@x.com")]
    rows[1]["userId"]["primaryNumber"] = "7974748131"
    client = SignedIn(Ignores(rows)).client()
    assert [c["id"] for c in client.submissions("C1", learners=["7974748131"])] == ["T1"]
    assert [c["id"] for c in client.submissions("C1", learners=["t1@x.com"])] == ["T1"]


def test_a_busy_read_is_tried_again():
    class Busy(FakePortal):
        tries = 0

        def request(self, method, url, **kwargs):
            Busy.tries += 1
            if Busy.tries < 3:
                raise requests.ConnectionError("dropped")
            return super().request(method, url, **kwargs)
    assert len(SignedIn(Busy([row("S1")])).client().submissions("C1")) == 1


def test_files_stored_by_path_are_read_from_the_cdn():
    assert portal.file_url("assignment/x.pdf") == "https://cdn.pwskills.com/assignment/x.pdf"
    assert portal.file_url("https://cdn.pwskills.com/a.pdf") == "https://cdn.pwskills.com/a.pdf"
    assert portal.is_learner_link("https://github.com/a") and not portal.is_learner_link("https://cdn.pwskills.com/a.pdf")
    assert not portal.is_learner_link("assignment/x.pdf")


def test_feedback_is_escaped_for_the_portals_editor():
    assert sw.comment_html("a < b\n\nWhere marks were lost:\nQ1 lost") == \
        "<p>a &lt; b</p><p><strong>Where marks were lost:</strong></p><p>Q1 lost</p>"


def test_the_sign_in_is_kept_only_while_the_portal_takes_it(tmp_path):
    class Client:
        def __init__(self, token):
            self.token = token

        def courses(self, limit=1):
            if self.token == "BAD":
                raise portal.SkillsSignInNeeded("no")
            return []
    opened = threading.Event()

    def opener(profile, headless, accept, on_waiting):
        on_waiting()
        assert not accept("BAD")
        opened.set()
        return "GOOD" if accept("GOOD") else ""
    sign_in = portal.AdminSignIn(tmp_path / "p", opener=opener, client_factory=Client)
    assert sign_in.status()["state"] == "signed_out"
    sign_in.start()
    sign_in.thread.join(5)
    assert opened.is_set() and sign_in.status()["state"] == "signed_in" and sign_in.token() == "GOOD"
    sign_in.ended("OLDER")
    assert sign_in.status()["state"] == "signed_in", "a refusal of an older token does not end a newer sign-in"
    sign_in.ended("GOOD")
    with pytest.raises(portal.SkillsSignInNeeded):
        sign_in.token()


def test_an_expired_token_counts_as_signed_out(tmp_path):
    import base64
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) - 5}).encode()).decode().rstrip("=")
    sign_in = portal.AdminSignIn(tmp_path / "p")
    sign_in.value, sign_in.state = f"h.{claims}.s", "signed_in"
    assert sign_in.status()["state"] == "signed_out"
