"""Offline regressions for workflow windows without a fixed active-job limit."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the app folder, however this file is run
import json
import threading
import time

from fastapi import FastAPI
import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest only when it imports pytest

import integrated_workflow as workflow
from test_integrated_workflow import LocalClient, another_run_row, pdf, report, token, wait_idle
from test_workflow_admin_pairs import fixture_store, pair


def selected_pair(index, **updates):
    return pair(f"admin{index}@example.com", f"teacher{index}@example.com", **updates)


def eventually(predicate, message, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), message


def held_grader(expected):
    """Wait until the requested number of graders really overlap, then release all."""
    release, entered = threading.Event(), threading.Event()
    lock = threading.Lock()
    counts = {"active": 0, "peak": 0, "calls": 0}

    def grade(*args):
        with lock:
            counts["active"] += 1
            counts["calls"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            if counts["active"] >= expected:
                entered.set()
        try:
            assert release.wait(30), "The test did not release the offline grader"
            return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
        finally:
            with lock:
                counts["active"] -= 1

    return grade, release, entered, counts


def test_eight_distinct_account_pairs_run_together_and_close_each_window_once(tmp_path):
    grade, release, entered, counts = held_grader(8)
    store, state = fixture_store(tmp_path, grader=grade)
    runs = [store.create([another_run_row(index)], selected_pair(index), "owner", pdf(), pdf())
            for index in range(8)]
    try:
        for run in runs:
            store.start(run["id"], "token")
        assert entered.wait(15), "Eight distinct account pairs did not reach grading together"
        assert len(store.active) == 8
        assert all(store.read(run["id"])["status"] == "running" for run in runs)
        assert not state.closed, "A window closed while another run was active"
    finally:
        release.set()
        wait_idle(store, seconds=30)

    assert counts["peak"] == counts["calls"] == 8
    assert [store.read(run["id"])["status"] for run in runs] == ["complete"] * 8
    assert len(state.windows) == 16
    assert len({window["admin_port"] or window["evaluator_port"] for window in state.windows}) == 16
    assert len({id(window) for window in state.closed}) == 16
    assert {id(window) for window in state.closed} == {id(window) for window in state.windows}
    assert len([action for action in state.actions if action[0] == "upload"]) == 8


def test_stop_one_of_many_windows_leaves_the_other_runs_active(tmp_path):
    grade, release, entered, _ = held_grader(5)
    store, state = fixture_store(tmp_path, grader=grade)
    runs = [store.create([another_run_row(index)], selected_pair(index), "owner", pdf(), pdf())
            for index in range(5)]
    try:
        for run in runs:
            store.start(run["id"], "token")
        assert entered.wait(15)
        assert store.stop_run(runs[0]["id"])
        assert store.slot(runs[0]["id"])["stop"].is_set()
        assert all(not store.slot(run["id"])["stop"].is_set() for run in runs[1:])
        assert len(store.active) == 5
    finally:
        release.set()
        wait_idle(store, seconds=30)

    assert store.read(runs[0]["id"])["status"] == "stopped"
    assert [store.read(run["id"])["status"] for run in runs[1:]] == ["complete"] * 4
    assert not any(stage == "upload" and copy == "copy0" for stage, copy, *_ in state.actions)
    assert len([action for action in state.actions if action[0] == "upload"]) == 4


def test_many_runs_keep_the_global_fifteen_copy_grading_limit(tmp_path):
    grade, release, entered, counts = held_grader(15)
    store, _ = fixture_store(tmp_path, grader=grade)
    store.grading_slots.limit = 15
    runs = []
    for index in range(8):
        rows = [another_run_row(index * 3 + copy) for copy in range(3)]
        for row in rows:
            row.update(test_id=f"test{index}", test_name=f"Test {index}")
        runs.append(store.create(rows, selected_pair(index, copies_in_flight=3), "owner", pdf(), pdf()))
    try:
        for run in runs:
            store.start(run["id"], "token")
        assert entered.wait(15), "The app did not fill the existing fifteen grading slots"
        assert len(store.active) == 8
        eventually(lambda: all(store.read(run["id"])["status"] == "running" for run in runs),
                   "Some of the eight windows never became active")
        # The remaining workers have work ready, but may not enter the grader yet.
        time.sleep(.15)
        assert counts["active"] == counts["calls"] == counts["peak"] == 15
    finally:
        release.set()
        wait_idle(store, seconds=30)

    assert counts["calls"] == 24 and counts["peak"] == 15
    assert [store.read(run["id"])["status"] for run in runs] == ["complete"] * 8
    assert all(row["stage"] == "uploaded" for run in runs for row in store.read(run["id"])["rows"])


def test_stop_while_waiting_for_a_grading_slot_does_not_wait_for_other_copies(tmp_path):
    calls = []
    store, state = fixture_store(tmp_path, grader=lambda *args: calls.append(args))
    store.grading_slots.limit = 15
    run = store.create([another_run_row(1)], selected_pair(1), "owner", pdf(), pdf())
    held = 0
    try:
        # Simulate fifteen paid jobs that cannot be interrupted by this run's Stop.
        for _ in range(15):
            assert store.grading_slots.acquire(blocking=False)
            held += 1
        store.start(run["id"], "token")
        eventually(lambda: store.read(run["id"])["rows"][0].get("operation") == "waiting for grading slot",
                   "The queued copy did not show why grading was waiting")
        assert store.stop_run(run["id"])
        wait_idle(store, seconds=3)
        saved = store.read(run["id"])
        assert saved["status"] == "stopped"
        assert saved["rows"][0]["stage"] == "downloaded"
        assert not calls, "A stopped queued copy consumed an AI grading call"
        assert not any(action[0] == "upload" for action in state.actions)
    finally:
        for _ in range(held):
            store.grading_slots.release()
        wait_idle(store, seconds=15)


def document_portal(store, fetch_release, fetch_entered, expected):
    """Give every evaluator its own held document fetch without opening Chrome."""
    base = store.portal_factory
    entered = set()
    lock = threading.Lock()

    class Portal(base):
        def exam_documents(self, test, progress=lambda text: None):
            index = int(test["test_id"].removeprefix("test"))
            # Exercise both waiting-for-login and fetching while a job is active.
            if index % 2:
                progress("Downloading the official question paper")
            with lock:
                entered.add(test["test_id"])
                if len(entered) >= expected:
                    fetch_entered.set()
            assert fetch_release.wait(30), "The test did not release document fetching"
            return {"question_paper": [("paper.pdf", pdf())], "answer_key": [("key.pdf", pdf())]}

    store.portal_factory = Portal


def test_start_resume_and_fetch_api_work_with_more_than_three_active_jobs(tmp_path):
    grade, release, entered, _ = held_grader(5)
    offline, _ = fixture_store(tmp_path / "fixture", grader=grade)
    app = FastAPI()
    store = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), grade, tmp_path / "api")
    store.portal_factory, store.downloader = offline.portal_factory, offline.downloader
    fetch_release, fetch_entered = threading.Event(), threading.Event()
    document_portal(store, fetch_release, fetch_entered, 1)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    owner = workflow.owner_for(token("alice"))
    runs = [store.create([another_run_row(index)], selected_pair(index), owner, pdf(), pdf())
            for index in range(3)]
    resumed = store.create([another_run_row(4)], selected_pair(4), owner, pdf(), pdf())
    try:
        for run in runs:
            store.start(run["id"], auth["Authorization"])
        started = client.post("/api/workflow/start", headers=auth,
                              data={"rows": json.dumps([another_run_row(3)]), "options": json.dumps(selected_pair(3))},
                              files={"question_paper": ("paper.pdf", pdf()), "answer_key": ("key.pdf", pdf())})
        assert started.status_code == 200, started.text
        runs.append(started.json())
        response = client.post(f"/api/workflow/{resumed['id']}/resume", headers=auth)
        assert response.status_code == 200, response.text
        runs.append(resumed)
        fetched = client.post("/api/workflow/exam-documents", headers=auth,
                              json={"test_id": "test5", "test_name": "Test 5", "evaluator_email": "teacher5@example.com"})
        assert fetched.status_code == 200, fetched.text
        assert entered.wait(15) and fetch_entered.wait(15)
        assert len(store.active) == 6
        duplicate = client.post(f"/api/workflow/{resumed['id']}/resume", headers=auth)
        assert duplicate.status_code == 409 and "already active" in duplicate.text
    finally:
        release.set()
        fetch_release.set()
        wait_idle(store, seconds=30)

    assert [store.read(run["id"])["status"] for run in runs] == ["complete"] * 5
    status = client.get(f"/api/workflow/exam-documents/{fetched.json()['fetch_id']}", headers=auth)
    assert status.status_code == 200 and status.json()["status"] == "ready"


def test_more_than_five_document_fetches_survive_waiting_and_remain_ready(tmp_path):
    store, state = fixture_store(tmp_path)
    release, entered = threading.Event(), threading.Event()
    document_portal(store, release, entered, 8)
    clock = {"now": 1000.0}
    store.exam_documents = workflow.ExamDocumentCache(clock=lambda: clock["now"], ttl=60)
    fetched = []
    try:
        for index in range(8):
            fetched.append(store.fetch_exam_documents("owner", {"test_id": f"test{index}",
                          "test_name": f"Test {index}", "evaluator_email": f"teacher{index}@example.com"}))
        assert entered.wait(15), "Eight document fetches did not reach their own portal windows"
        assert len(store.active) == 8
        clock["now"] += 61
        entries = [store.exam_documents.get("owner", fetch_id) for fetch_id in fetched]
        assert all(entry is not None for entry in entries), "Active fetches expired while waiting in their windows"
        assert {entry["status"] for entry in entries} == {"waiting_login", "fetching"}
        assert not state.closed
    finally:
        release.set()
        wait_idle(store, seconds=30)

    entries = [store.exam_documents.get("owner", fetch_id) for fetch_id in fetched]
    assert all(entry is not None and entry["status"] == "ready" for entry in entries)
    assert all(entry["documents"]["question_paper"]["data"].startswith(b"%PDF") for entry in entries)
    # Creating the next window must not evict documents still selected in earlier windows.
    ninth = store.fetch_exam_documents("owner", {"test_id": "test8", "test_name": "Test 8",
                                                "evaluator_email": "teacher8@example.com"})
    wait_idle(store, seconds=15)
    assert all(store.exam_documents.get("owner", fetch_id)["status"] == "ready" for fetch_id in fetched + [ninth])
    assert len({id(window) for window in state.closed}) == len(state.windows) == 9


def test_queued_document_fetches_are_kept_until_they_finish():
    clock = {"now": 1000.0}
    cache = workflow.ExamDocumentCache(clock=lambda: clock["now"], ttl=60)
    ids = [cache.create("owner", {"test_id": f"test{index}"}) for index in range(8)]
    clock["now"] += 61
    assert all(cache.get("owner", fetch_id)["status"] == "queued" for fetch_id in ids)
    for fetch_id in ids:
        cache.update(fetch_id, status="ready", documents={})
    clock["now"] += 61
    assert all(cache.get("owner", fetch_id) is None for fetch_id in ids)
