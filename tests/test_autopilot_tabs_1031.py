"""Operator's request of 31 Oct 2026: the autopilot goes on to the next tab by itself.

The student tab box takes several tabs in order. All are read at Check sheets, because the Sheets
connection may have ended by the time a later tab is reached, and each test's statuses go back to
the tab it came from. Fixture portal, grader and checker; no network.
"""
from __future__ import annotations

import io
import os
import sys

import openpyxl
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot as autopilot
from test_integrated_workflow import LocalClient, store, token
from test_workflow_autopilot import OWNER, PAIRS, PairPortal, copy, finish, pilot


def tabbed(*tabs):
    return autopilot.rows_of_tabs([(name, rows) for name, rows in tabs])


def test_the_box_gives_the_tabs_in_order():
    assert autopilot.tab_names("27 sept, 27 sept 5k-6k ,27 sept 1k-2k") == ["27 sept", "27 sept 5k-6k", "27 sept 1k-2k"]
    assert autopilot.tab_names("27 sept") == ["27 sept"]
    assert autopilot.tab_names("a, a,\nb,") == ["a", "b"]
    assert autopilot.tab_names("") == []


def test_a_tabs_tests_all_come_before_the_next_tabs():
    rows = tabbed(("first", [copy(1, "A"), copy(2, "B")]), ("second", [copy(3, "C")]))
    tests, by_test = autopilot.build_plan(rows, PAIRS)
    assert [(test["tab"], test["test_id"]) for test in tests] == [("first", "testA"), ("first", "testB"), ("second", "testC")]
    assert [len(by_test[test["key"]]) for test in tests] == [1, 1, 1]


def test_a_copy_listed_in_two_tabs_stays_in_the_first():
    rows = tabbed(("first", [copy(1, "A")]), ("second", [copy(1, "A"), copy(2, "A")]))
    assert [(row["sheet_tab"], row["submission_id"]) for row in rows] == [("first", "copy1"), ("second", "copy2")]


def test_a_test_in_two_tabs_is_one_run_per_tab_so_statuses_go_to_the_right_tab(tmp_path):
    s, ap = pilot(tmp_path)
    first, second = {"worksheet": "first", "gid": "1"}, {"worksheet": "second", "gid": "2"}
    rows = tabbed(("first", [copy(1, "A")]), ("second", [copy(2, "A"), copy(3, "B")]))
    plan = ap.plan(OWNER, rows, PAIRS[:1], first, {"first": first, "second": second})
    assert [test["tab"] for test in plan["tests"]] == ["first", "second", "second"]
    ap.start(OWNER, plan["plan_id"])
    finish(ap)
    state = ap.status(OWNER)
    assert state["counts"] == {"done": 3} and [test["tab"] for test in state["tests"]] == ["first", "second", "second"]
    assert [s.read(test["run_id"])["source_sheet"]["worksheet"] for test in state["tests"]] == ["first", "second", "second"]


def test_one_tab_works_as_it_always_did(tmp_path):
    s, ap = pilot(tmp_path)
    sheet = {"worksheet": "only"}
    ap.start(OWNER, ap.plan(OWNER, [copy(1, "A")], PAIRS[:1], sheet)["plan_id"])
    finish(ap)
    test = ap.status(OWNER)["tests"][0]
    assert test["status"] == "done" and test["tab"] == "" and s.read(test["run_id"])["source_sheet"] == sheet


def test_check_sheets_reads_every_tab_of_a_workbook(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), store(tmp_path).grader, tmp_path)
    s.portal_factory, s.downloader = PairPortal, store(tmp_path).downloader
    s.portal_logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret, reveal=lambda stored: stored[4:])
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    book = openpyxl.Workbook()
    header = list(copy(1, "A"))
    for sheet, rows in ((book.active, [copy(1, "A")]), (book.create_sheet("second"), [copy(2, "B"), copy(3, "B")])):
        sheet.append(header)
        for row in rows:
            sheet.append(list(row.values()))
    book.active.title = "first"
    data = io.BytesIO()
    book.save(data)
    students = client.post("/api/workflow/sheet-tabs", headers=auth, files={"sheet": ("students.xlsx", data.getvalue())}).json()
    accounts_file = "admin,evaluator,password\nadmin.one@pw.live,eval.one@pw.live,pw-one\n".encode()
    accounts = client.post("/api/workflow/sheet-tabs", headers=auth, files={"sheet": ("accounts.csv", accounts_file)}).json()
    plan = client.post("/api/workflow/autopilot/plan", headers=auth, json={
        "students": {"import_id": students["import_id"], "worksheet": "first, second"},
        "accounts": {"import_id": accounts["import_id"], "worksheet": "CSV data"}})
    assert plan.status_code == 200, plan.text
    assert [(test["tab"], test["copies"]) for test in plan.json()["tests"]] == [("first", 1), ("second", 2)]
    missing = client.post("/api/workflow/autopilot/plan", headers=auth, json={
        "students": {"import_id": students["import_id"], "worksheet": "first, no such tab"},
        "accounts": {"import_id": accounts["import_id"], "worksheet": "CSV data"}})
    assert missing.status_code == 400 and "no such tab" in missing.text
