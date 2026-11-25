"""Approval Management > Review, added 12 November 2026: each pair's reviewer account approves the copies
that pair uploaded, with grade A. From 25 November 2026 a test is approved as soon as its own uploads land
on the portal, with a sweep at the end for what that pass could not take. The steps per copy come from the
supplied Paper Uploader; the narrowing to this run's own copies is ours, so another evaluator's work in the
same program is left pending. A fake page and a fake desk stand in; nothing reaches the portal.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workflow_autopilot as autopilot
from portal_vendor import review_copies as reviews
from workflow_autopilot import account_pairs

HEADER = ["Admin", "Evaluator", "Password", "Reviewer"]


# --------------------------------------------------------------------------- the accounts tab

def test_the_reviewer_column_is_read():
    pairs = account_pairs([HEADER, ["a@pw.live", "e@pw.live", "secret", "r@pw.live"]])
    assert pairs[0]["reviewer"] == "r@pw.live" and pairs[0]["evaluator"] == "e@pw.live"


def test_a_tab_without_the_column_still_works_and_reviews_nothing():
    pairs = account_pairs([HEADER[:3], ["a@pw.live", "e@pw.live", "secret"]])
    assert pairs[0]["reviewer"] == ""


def test_a_reviewer_that_is_not_an_email_is_named():
    with pytest.raises(ValueError, match="row 2: the reviewer ID 'nobody' is not an email"):
        account_pairs([HEADER, ["a@pw.live", "e@pw.live", "secret", "nobody"]])


# --------------------------------------------------------------------------- one copy on the page

FIELDS = {'select[name="status"]': "status", 'select[name="grade"]': "grade"}


class FakeLocator:
    def __init__(self, page, name, count=1, value="", rows=()):
        self.page, self.name, self._count, self.value, self.rows = page, name, count, value, list(rows)

    def filter(self, **kwargs):
        return self

    def locator(self, selector, **kwargs):
        return FakeLocator(self.page, FIELDS.get(selector, f"{self.name}>{selector}"))

    def get_by_role(self, role, name="", exact=False):
        return FakeLocator(self.page, f"{self.name}:{role}:{name}")

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self):
        return self._count

    def wait_for(self, **kwargs):
        if self.name == reviews.REVIEW_SUCCESS and not self.page.submitted:
            raise TimeoutError("no toast")

    def is_visible(self):
        return self.name == reviews.REVIEW_SUCCESS and self.page.submitted

    def select_option(self, value):
        self.page.chosen[self.name] = value

    def input_value(self):
        return self.page.chosen.get(self.name, "")

    def evaluate(self, script):
        return self.page.invalid

    def click(self):
        self.page.submitted = True
        self.page.clicks.append(self.name)


class FakePage:
    """Only what review_copies touches."""
    def __init__(self, invalid=(), rows=1):
        self.chosen, self.clicks, self.invalid, self.rows = {}, [], list(invalid), rows
        self.submitted, self.searched, self.url = False, [], "https://portal/review/" + "a" * 24
        self.keyboard = type("K", (), {"press": staticmethod(lambda key: None)})()

    def locator(self, selector, **kwargs):
        name = FIELDS.get(selector, selector)
        count = self.rows if selector == "tbody tr" else 1
        return FakeLocator(self, name, count=count)

    def get_by_role(self, role, name="", exact=False):
        return FakeLocator(self, "submit")

    def get_by_text(self, text, exact=False):
        return FakeLocator(self, f"text:{text}")

    def goto(self, url, **kwargs):
        self.url = url


@pytest.fixture
def engine(monkeypatch):
    """The vendored engine's page helpers, replaced by ones that touch nothing."""
    monkeypatch.setattr(reviews.engine, "search", lambda page, value: page.searched.append(value))
    monkeypatch.setattr(reviews.engine, "click_menu_item", lambda page, row, item: page.clicks.append(item))
    monkeypatch.setattr(reviews.engine, "click_if_visible", lambda scope, selector, timeout=0: True)
    monkeypatch.setattr(reviews.engine, "should_stop", lambda: False)
    monkeypatch.setattr(reviews.engine, "on_status", lambda message: None)
    return reviews.engine


COPY = reviews.ReviewCopy(task_id="t1", student_id="S1", student_name="Asha", test_name="Test 1", category="Class 9")


def test_a_copy_is_approved_with_grade_a_and_nothing_else_is_touched(engine):
    page = FakePage()
    said = reviews.approve_copy(page, COPY)
    assert page.chosen == {"status": "approved", "grade": "A"}
    assert "View Upload" in page.clicks and page.submitted and "Approved, grade A" in said


def test_the_operator_is_asked_before_the_click(engine):
    page, asked = FakePage(), []
    reviews.approve_copy(page, COPY, before_submit=asked.append)
    assert asked and "Asha" in asked[0] and page.submitted


def test_a_copy_that_is_no_longer_pending_is_reported_not_submitted(engine):
    page = FakePage(rows=0)
    with pytest.raises(LookupError, match="Not in the Review > Pending list"):
        reviews.approve_copy(page, COPY)
    assert not page.submitted


def test_two_matching_rows_are_left_for_a_person(engine):
    page = FakePage(rows=2)
    with pytest.raises(LookupError, match="review this copy by hand"):
        reviews.approve_copy(page, COPY)
    assert not page.submitted


def test_a_form_the_site_would_refuse_is_not_submitted(engine):
    page = FakePage(invalid=["remark"])
    with pytest.raises(RuntimeError, match="wants more filled in before Submit: remark"):
        reviews.approve_copy(page, COPY)
    assert not page.submitted


def test_a_submit_without_its_message_is_never_repeated(engine, monkeypatch):
    page = FakePage()
    monkeypatch.setattr(FakeLocator, "click", lambda self: self.page.clicks.append("submit"))   # no toast
    with pytest.raises(reviews.SubmissionUnverified, match="will not be submitted again"):
        reviews.approve_copy(page, COPY)


# --------------------------------------------------------------------------- the pending list

def task(student="S1", name="Asha", test="Test 1"):
    return {"_id": "t" + student, "studentObj": {"studentId": student, "firstName": name},
            "testDetails": {"_id": "test1", "name": test}, "categoryDetails": {"name": "Class 9"}}


def test_only_this_runs_copies_are_taken_from_the_pending_list(engine, monkeypatch):
    answer = {"data": {"data": [task("S1"), task("S2", "Bilal"), task("S3", "Chen")],
                       "paginate": {"totalCount": 3}}}
    monkeypatch.setattr(reviews.match_paper, "api_get", lambda page, url: answer)
    found = reviews.pending_copies(FakePage(), "p" * 24, wanted={"S1", "S3"})
    assert [copy.student_id for copy in found] == ["S1", "S3"]


def test_without_a_list_of_ours_every_pending_copy_is_taken(engine, monkeypatch):
    answer = {"data": {"data": [task("S1"), task("S2", "Bilal")], "paginate": {"totalCount": 2}}}
    monkeypatch.setattr(reviews.match_paper, "api_get", lambda page, url: answer)
    assert len(reviews.pending_copies(FakePage(), "p" * 24, wanted=None)) == 2


def test_a_row_without_a_student_id_is_left_out():
    assert reviews.copy_from_task({"studentObj": {}, "testDetails": {"name": "Test 1"}}) is None
    assert reviews.copy_from_task(task()).student_name == "Asha"


# --------------------------------------------------------------------------- the autopilot's pass

def pilot(tmp_path, pairs, tests, runs, stopped=False):
    made = autopilot.Autopilot.__new__(autopilot.Autopilot)
    made.lock, made.stop_event = threading.RLock(), threading.Event()
    if stopped:
        made.stop_event.set()
    made.state = {"id": "a1", "owner": "o", "status": "running", "pairs": pairs, "tests": tests}
    made.save = lambda: None
    made.calls = []

    class Desk:
        def __init__(self, email):
            self.email = email

        def call(self, stop, method, program, students, report):
            made.calls.append((self.email, method, program, sorted(students)))
            report(f"Approved, grade A: {len(students)} copies")
            return {"counts": {"approved": len(students)}}

    made.store = type("Store", (), {
        "desks": type("Desks", (), {"evaluator": staticmethod(Desk)})(),
        "read": staticmethod(lambda run_id: runs[run_id]),
    })()
    return made


def run_rows(*students, stage="uploaded"):
    return {"rows": [{"student_id": student, "stage": stage} for student in students]}


def test_each_pairs_reviewer_approves_that_pairs_uploaded_copies(tmp_path):
    tests = [{"run_id": "r1", "program_name": "Programme A", "pair": 0},
             {"run_id": "r2", "program_name": "Programme B", "pair": 1}]
    runs = {"r1": run_rows("S1", "S2"), "r2": run_rows("S9")}
    made = pilot(tmp_path, [{"reviewer": "r1@pw.live"}, {"reviewer": "r2@pw.live"}], tests, runs)
    made.review_uploaded()
    assert made.calls == [("r1@pw.live", "review", "Programme A", ["S1", "S2"]),
                          ("r2@pw.live", "review", "Programme B", ["S9"])]
    assert made.state["review"]["status"] == "finished"
    assert made.state["review"]["counts"]["approved"] == 3


def test_copies_this_run_did_not_upload_are_not_approved(tmp_path):
    runs = {"r1": {"rows": [{"student_id": "S1", "stage": "uploaded"},
                            {"student_id": "S2", "stage": "already_evaluated"},
                            {"student_id": "S3", "stage": "rejection_submitted"},
                            {"student_id": "S4", "stage": "skipped"}]}}
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}],
                 [{"run_id": "r1", "program_name": "Programme A", "pair": 0}], runs)
    made.review_uploaded()
    assert made.calls[0][3] == ["S1"]


def test_a_run_with_no_reviewer_column_reviews_nothing(tmp_path):
    made = pilot(tmp_path, [{"reviewer": ""}], [{"run_id": "r1", "program_name": "A", "pair": 0}],
                 {"r1": run_rows("S1")})
    made.review_uploaded()
    assert not made.calls and "review" not in made.state


def test_one_pair_without_a_reviewer_is_reported_and_the_others_still_run(tmp_path):
    tests = [{"run_id": "r1", "program_name": "A", "pair": 0}, {"run_id": "r2", "program_name": "B", "pair": 1}]
    made = pilot(tmp_path, [{"reviewer": ""}, {"reviewer": "r@pw.live"}], tests,
                 {"r1": run_rows("S1"), "r2": run_rows("S2")})
    made.review_uploaded()
    assert [call[0] for call in made.calls] == ["r@pw.live"]
    assert any("no reviewer" in line for line in made.state["review"]["lines"])
    assert made.state["review"]["counts"]["no reviewer"] == 1


def test_a_program_that_fails_does_not_stop_the_next_one(tmp_path):
    tests = [{"run_id": "r1", "program_name": "A", "pair": 0}, {"run_id": "r2", "program_name": "B", "pair": 0}]
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], tests, {"r1": run_rows("S1"), "r2": run_rows("S2")})
    calls = []

    class Desk:
        def __init__(self, email):
            self.email = email

        def call(self, stop, method, program, students, report):
            calls.append(program)
            if program == "A":
                raise RuntimeError("the portal did not answer")
            return {"counts": {"approved": len(students)}}

    made.store.desks.evaluator = Desk
    made.review_uploaded()
    assert calls == ["A", "B"] and made.state["review"]["counts"] == {"failed": 1, "approved": 1}
    assert any("review could not run" in line for line in made.state["review"]["lines"])


def test_stop_ends_the_review_pass(tmp_path):
    tests = [{"run_id": "r1", "program_name": "A", "pair": 0}]
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], tests, {"r1": run_rows("S1")}, stopped=True)
    made.review_uploaded()
    assert not made.calls and made.state["review"]["status"] == "finished"


def test_a_run_that_cannot_be_read_is_passed_over(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}],
                 [{"run_id": "gone", "program_name": "A", "pair": 0}], {})
    made.review_uploaded()
    assert not made.calls


def test_the_review_is_saved_so_the_page_can_show_it(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}],
                 [{"run_id": "r1", "program_name": "A", "pair": 0}], {"r1": run_rows("S1")})
    saved = []
    made.save = lambda: saved.append(json.dumps(made.state["review"]))
    made.review_uploaded()
    assert len(saved) >= 2 and json.loads(saved[0])["status"] == "running"
    assert json.loads(saved[-1])["status"] == "finished"


# ------------------------------------------- per test, as its uploads land (operator, 25 Nov 2026)
# Until then the whole autopilot had to finish first, so a copy uploaded in the first hour waited
# for every other test to end. review_round() takes one finished test; review_uploaded() stays as
# the final sweep for what it could not take.

def done(run_id, program, pair=0, name="", status="done"):
    return {"run_id": run_id, "program_name": program, "pair": pair, "status": status,
            "test_name": name or program}


def test_a_test_is_approved_as_soon_as_its_uploads_land(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "Programme A")],
                 {"r1": run_rows("S1", "S2")})
    assert made.review_round() is True
    assert made.calls == [("r@pw.live", "review", "Programme A", ["S1", "S2"])]
    assert made.state["review"]["counts"]["approved"] == 2


def test_a_test_whose_uploads_are_still_going_is_not_approved_yet(tmp_path):
    tests = [done("r1", "A", status="uploading"), done("r2", "B", status="running")]
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], tests, {"r1": run_rows("S1"), "r2": run_rows("S2")})
    assert made.review_round() is False and not made.calls


def test_one_test_is_never_approved_twice(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A")], {"r1": run_rows("S1")})
    assert made.review_round() is True
    assert made.review_round() is False
    assert len(made.calls) == 1


def test_each_pair_is_reviewed_by_its_own_reviewer_as_it_finishes(tmp_path):
    tests = [done("r1", "Programme A", pair=0), done("r2", "Programme B", pair=1)]
    made = pilot(tmp_path, [{"reviewer": "r1@pw.live"}, {"reviewer": "r2@pw.live"}], tests,
                 {"r1": run_rows("S1"), "r2": run_rows("S9")})
    while made.review_round():
        pass
    assert made.calls == [("r1@pw.live", "review", "Programme A", ["S1"]),
                          ("r2@pw.live", "review", "Programme B", ["S9"])]


def test_the_final_sweep_does_not_approve_a_test_again(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A")], {"r1": run_rows("S1")})
    made.review_round()
    made.review_uploaded()
    assert len(made.calls) == 1
    assert made.state["review"]["status"] == "finished"
    assert made.state["review"]["counts"]["approved"] == 1


def test_the_final_sweep_still_takes_a_test_that_failed(tmp_path):
    tests = [done("r1", "A"), done("r2", "B", status="failed")]
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], tests, {"r1": run_rows("S1"), "r2": run_rows("S2")})
    while made.review_round():
        pass
    assert [call[2] for call in made.calls] == ["A"]
    made.review_uploaded()
    assert [call[2] for call in made.calls] == ["A", "B"]


def test_a_review_that_fails_is_reported_and_the_test_is_not_tried_again(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A", name="Maths ST-05")],
                 {"r1": run_rows("S1", "S2")})

    class Desk:
        def __init__(self, email):
            self.email = email

        def call(self, stop, method, program, students, report):
            made.calls.append((self.email, method, program, sorted(students)))
            raise RuntimeError("the portal did not answer")

    made.store.desks.evaluator = Desk
    assert made.review_round() is True
    assert made.review_round() is False          # taken before the portal, so never run twice
    assert len(made.calls) == 1
    assert made.state["review"]["counts"] == {"failed": 2}
    assert any("Maths ST-05: review could not run" in line for line in made.state["review"]["lines"])


def test_a_pair_without_a_reviewer_waits_for_the_sweep_to_report_it(tmp_path):
    tests = [done("r1", "A", pair=0), done("r2", "B", pair=1)]
    made = pilot(tmp_path, [{"reviewer": ""}, {"reviewer": "r@pw.live"}], tests,
                 {"r1": run_rows("S1"), "r2": run_rows("S2")})
    while made.review_round():
        pass
    assert [call[2] for call in made.calls] == ["B"]
    made.review_uploaded()
    assert [call[2] for call in made.calls] == ["B"]
    assert made.state["review"]["counts"]["no reviewer"] == 1
    assert any("no reviewer" in line for line in made.state["review"]["lines"])


def test_the_tally_of_both_passes_is_added_up(tmp_path):
    tests = [done("r1", "A"), done("r2", "B", status="stopped")]
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], tests,
                 {"r1": run_rows("S1", "S2"), "r2": run_rows("S3")})
    while made.review_round():
        pass
    assert made.state["review"]["counts"]["approved"] == 2
    made.review_uploaded()
    assert made.state["review"]["counts"]["approved"] == 3


def test_a_restart_adds_to_what_the_earlier_process_approved(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A")], {"r1": run_rows("S1")})
    made.state["review"] = {"status": "running", "counts": {"approved": 4}, "lines": ["earlier"]}
    made.review_round()
    assert made.state["review"]["counts"]["approved"] == 5
    assert made.state["review"]["lines"][0] == "earlier"


def test_a_test_with_nothing_uploaded_is_taken_without_touching_the_portal(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A")],
                 {"r1": run_rows("S1", stage="skipped")})
    assert made.review_round() is True and not made.calls


def test_no_reviewer_column_means_the_per_test_pass_does_nothing(tmp_path):
    made = pilot(tmp_path, [{"reviewer": ""}], [done("r1", "A")], {"r1": run_rows("S1")})
    assert made.review_round() is False and not made.calls and "review" not in made.state


def test_the_review_thread_ends_with_the_autopilot(tmp_path):
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A")], {"r1": run_rows("S1")})
    made.review_poll = 0
    made.state["status"] = "finished"
    made.approve_finished()                      # returns rather than polling for ever
    assert not made.calls


def test_the_review_thread_approves_every_finished_test_then_waits(tmp_path):
    tests = [done("r1", "A"), done("r2", "B")]
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], tests, {"r1": run_rows("S1"), "r2": run_rows("S2")})
    made.review_poll = 0
    original = made.review_round

    def round_then_stop():
        taken = original()
        if not taken:
            made.state["status"] = "finished"    # nothing left: let the thread end
        return taken

    made.review_round = round_then_stop
    made.approve_finished()
    assert [call[2] for call in made.calls] == ["A", "B"]


def test_the_sweep_marks_what_it_took_so_the_thread_cannot_repeat_it(tmp_path):
    """The sweep runs while the per-test thread may still be awake: a done test it approves must
    not be approved a second time by review_round()."""
    made = pilot(tmp_path, [{"reviewer": "r@pw.live"}], [done("r1", "A")], {"r1": run_rows("S1")})
    made.review_uploaded()                       # the per-test pass never got to it
    assert [call[2] for call in made.calls] == ["A"]
    assert made.review_round() is False
    assert [call[2] for call in made.calls] == ["A"]


def test_the_reviewer_is_counted_in_the_windows_the_plan_names(tmp_path):
    """The operator reads the window count to judge this computer's memory, so a reviewer window
    must be in it (25 Nov 2026: a run with reviewers was understated by a third)."""
    pairs = [{"admin": "a1@pw.live", "evaluator": "e1@pw.live", "reviewer": "r1@pw.live"},
             {"admin": "a2@pw.live", "evaluator": "e2@pw.live", "reviewer": "r2@pw.live"}]
    assert autopilot.Autopilot.public_plan([], pairs)["windows"] == 6


def test_without_a_reviewer_the_window_count_is_unchanged(tmp_path):
    pairs = [{"admin": "a1@pw.live", "evaluator": "e1@pw.live", "reviewer": ""},
             {"admin": "a2@pw.live", "evaluator": "e2@pw.live", "reviewer": ""}]
    assert autopilot.Autopilot.public_plan([], pairs)["windows"] == 4


def test_one_reviewer_shared_by_two_pairs_is_one_window(tmp_path):
    pairs = [{"admin": "a1@pw.live", "evaluator": "e1@pw.live", "reviewer": "r@pw.live"},
             {"admin": "a2@pw.live", "evaluator": "e2@pw.live", "reviewer": "R@pw.live"}]
    assert autopilot.Autopilot.public_plan([], pairs)["windows"] == 5


def test_the_sheet_the_operator_filled_in_is_read_as_pairs_with_reviewers():
    """Tab 3 of the team sheet, 25 Nov 2026: Admin | Evaluator | Password | Reviewer."""
    rows = [["Admin", "Evaluator", "Password", "Reviewer"],
            ["evaluator.c3@pw.live", "evaluator.a18@pw.live", "secret", "Reviewer.c3@pw.live"],
            ["evaluator.e5@pw.live", "evaluator.f4@pw.live", "secret", "reviewer.b2@pw.live"]]
    pairs = account_pairs(rows)
    assert [pair["reviewer"] for pair in pairs] == ["Reviewer.c3@pw.live", "reviewer.b2@pw.live"]
    assert all(pair["password"] for pair in pairs)        # the reviewer window signs itself in with it
