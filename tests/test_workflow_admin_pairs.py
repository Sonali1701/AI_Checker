"""Offline regressions for account-specific admin/evaluator workflow windows."""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest

import integrated_workflow as workflow
from test_integrated_workflow import another_run_row, options, pdf, report, wait_idle


def pair(admin="admin@example.com", evaluator="teacher@example.com", **updates):
    return {**options(), "admin_email": admin, "evaluator_email": evaluator, **updates}


def fixture_store(root, on_assign=None, grader=None):
    """Capture actual desk routing without launching Chrome or contacting a portal."""
    state = SimpleNamespace(windows=[], actions=[], closed=[], lock=threading.Lock())

    class AccountPortal:
        def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
            self.admin, self.evaluator = admin, evaluator
            self.window = {"admin": admin, "admin_port": admin_port,
                           "evaluator": evaluator, "evaluator_port": evaluator_port,
                           "thread": threading.get_ident()}
            with state.lock:
                state.windows.append(self.window)

        def perform(self, stage, row, selected, pdf=None, report=None, reason="", before_submit=None):
            assert threading.get_ident() == self.window["thread"], "A window crossed desk threads"
            expected = selected["admin_email"] if stage == "assign" else selected["evaluator_email"]
            assert (self.admin if stage == "assign" else self.evaluator).casefold() == expected.casefold()
            with state.lock:
                state.actions.append((stage, row["submission_id"], self.admin or self.evaluator, row.get("operation", "")))
            if stage == "assign" and on_assign:
                on_assign(self, row)
            if before_submit:
                before_submit()

        def close(self):
            assert threading.get_ident() == self.window["thread"]
            with state.lock:
                state.closed.append(self.window)

    def download(url, target):
        target.write_bytes(pdf())

    def grade(*args):
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}

    store = workflow.WorkflowStore(root, grader or grade, AccountPortal, download)
    store.retry_delay = 0
    return store, state


def test_distinct_admin_evaluator_pairs_assign_concurrently_in_their_own_windows(tmp_path):
    assignments = threading.Barrier(2)
    store, state = fixture_store(tmp_path, on_assign=lambda *args: assignments.wait(timeout=5))
    pairs = [pair("First.Admin@example.com", "first.teacher@example.com"),
             pair("Second.Admin@example.com", "second.teacher@example.com")]
    runs = [store.create([another_run_row(i)], selected, "owner", pdf(), pdf())
            for i, selected in enumerate(pairs)]
    for run in runs:
        store.start(run["id"], "token")
    wait_idle(store, seconds=20)
    assert [store.read(run["id"])["status"] for run in runs] == ["complete", "complete"]
    assert len(state.windows) == 4
    ports = [window["admin_port"] or window["evaluator_port"] for window in state.windows]
    assert len(set(ports)) == 4 and all(port > 9237 for port in ports)
    assert len(state.closed) == 4
    for i, selected in enumerate(pairs):
        routed = [action for action in state.actions if action[1] == f"copy{i}"]
        assert [action[0] for action in routed] == ["assign", "upload"]
        assert [action[2].casefold() for action in routed] == [selected["admin_email"].casefold(), selected["evaluator_email"]]
        assert selected["admin_email"].casefold() in routed[0][3].casefold()
        assert selected["evaluator_email"] in routed[1][3]


def test_same_admin_is_reused_case_insensitively_and_assignments_take_turns(tmp_path):
    first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()

    def hold_assignment(portal, row):
        if row["submission_id"] == "copy1":
            first_entered.set()
            assert release.wait(5), "The test did not release the first admin assignment"
        else:
            second_entered.set()

    store, state = fixture_store(tmp_path, on_assign=hold_assignment)
    first = store.create([another_run_row(1)], pair("Shared.Admin@example.com", "first.teacher@example.com"), "owner", pdf(), pdf())
    second = store.create([another_run_row(2)], pair("shared.admin@EXAMPLE.com", "second.teacher@example.com"), "owner", pdf(), pdf())
    try:
        store.start(first["id"], "token")
        assert first_entered.wait(5)
        store.start(second["id"], "token")
        assert not second_entered.wait(.2), "The same admin was driven by two threads at once"
    finally:
        release.set()
        wait_idle(store, seconds=20)
    assert second_entered.is_set()
    assert [store.read(run["id"])["status"] for run in (first, second)] == ["complete", "complete"]
    assert len([window for window in state.windows if window["admin"]]) == 1
    assert store.desks.administrator("SHARED.ADMIN@example.com") is store.desks.administrator("shared.admin@example.com")


def test_resume_after_restart_uses_the_account_pair_saved_with_the_run(tmp_path):
    store, _ = fixture_store(tmp_path)
    selected = pair("saved.admin@example.com", "saved.teacher@example.com")
    run = store.create([another_run_row(1)], selected, "owner", pdf(), pdf())
    # Simulate a server restart after creation and before the first portal step.
    restarted, state = fixture_store(tmp_path)
    restarted.start(run["id"], "new-token")
    wait_idle(restarted, seconds=20)
    saved = restarted.read(run["id"])
    assert saved["status"] == "complete"
    assert saved["options"]["admin_email"] == selected["admin_email"]
    assert saved["options"]["evaluator_email"] == selected["evaluator_email"]
    assert [(action[0], action[2]) for action in state.actions] == [
        ("assign", selected["admin_email"]), ("upload", selected["evaluator_email"])]
    restarted.start(run["id"], "third-token")
    wait_idle(restarted)
    assert len(state.actions) == 2, "Resume repeated already completed portal submissions"


@pytest.mark.parametrize("selected", [pair("not-an-email"), pair("admin@example.com", "ADMIN@example.com")])
def test_invalid_or_shared_pair_is_rejected_before_creating_a_run(tmp_path, selected):
    store, state = fixture_store(tmp_path)
    with pytest.raises(ValueError):
        store.create([another_run_row(1)], selected, "owner", pdf(), pdf())
    assert state.windows == []
    assert list(tmp_path.glob("*/run.json")) == []


@pytest.mark.parametrize("new_pair", [
    pair("first.teacher@example.com", "second.teacher@example.com"),
    pair("second.admin@example.com", "first.admin@example.com"),
])
def test_active_account_cannot_be_reused_in_the_other_role(tmp_path, new_pair):
    grading, release = threading.Event(), threading.Event()

    def hold_grade(*args):
        grading.set()
        assert release.wait(5)
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}

    store, state = fixture_store(tmp_path, grader=hold_grade)
    first = store.create([another_run_row(1)], pair("first.admin@example.com", "first.teacher@example.com"), "owner", pdf(), pdf())
    second = store.create([another_run_row(2)], new_pair, "owner", pdf(), pdf())
    try:
        store.start(first["id"], "token")
        assert grading.wait(5)
        with pytest.raises(workflow.BusyError):
            store.start(second["id"], "token")
        assert store.read(second["id"])["status"] == "ready"
    finally:
        release.set()
        wait_idle(store, seconds=20)
    assert all(action[1] == "copy1" for action in state.actions)


def test_admin_profiles_do_not_collide_and_login_banner_names_the_exact_id(tmp_path):
    # Punctuation variants share a slug. Their account hashes must keep sessions separate.
    first = workflow.Portal(tmp_path, admin="Admin+One@example.com", admin_port=9430)
    second = workflow.Portal(tmp_path, admin="admin.one@example.com", admin_port=9431)
    repeated = workflow.Portal(tmp_path, admin=" ADMIN+ONE@EXAMPLE.COM ", admin_port=9430)
    assert first.windows["admin"][0] == 9430 and second.windows["admin"][0] == 9431
    assert first.windows["admin"][1] != second.windows["admin"][1]
    assert first.windows["admin"][1] == repeated.windows["admin"][1]
    assert hashlib.sha256(b"admin+one@example.com").hexdigest()[:12] in first.windows["admin"][1]
    assert "admin+one@example.com" in first.account_label("admin").casefold()
    scripts, evaluated = [], []
    first.announce_login(SimpleNamespace(add_init_script=scripts.append,
                                         pages=[SimpleNamespace(evaluate=evaluated.append)]), "admin")
    assert len(scripts) == 1
    assert "ADMIN" in scripts[0] and "admin+one@example.com" in scripts[0].casefold()
    assert "__LABEL__" not in scripts[0] and "__LOGGED_IN__" not in scripts[0]
    assert evaluated == scripts, "The existing login page must be labelled immediately"


def test_evaluator_profiles_keep_distinct_accounts_separate(tmp_path):
    first = workflow.Portal(tmp_path, evaluator="a.b@example.com", evaluator_port=9440)
    second = workflow.Portal(tmp_path, evaluator="a+b@example.com", evaluator_port=9441)
    repeated = workflow.Portal(tmp_path, evaluator=" A.B@EXAMPLE.COM ", evaluator_port=9440)
    assert first.windows["evaluator"][1] != second.windows["evaluator"][1]
    assert first.windows["evaluator"][1] == repeated.windows["evaluator"][1]


def test_simultaneous_create_of_the_same_copy_saves_only_one_run(tmp_path, monkeypatch):
    store, state = fixture_store(tmp_path)
    first_save, release = threading.Event(), threading.Event()
    original_save = store.save

    def hold_first_save(run):
        if not first_save.is_set():
            first_save.set()
            assert release.wait(5)
        return original_save(run)

    monkeypatch.setattr(store, "save", hold_first_save)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(store.create, [another_run_row(1)], pair(), "owner", pdf(), pdf())
        assert first_save.wait(5)
        second = workers.submit(store.create, [another_run_row(1)], pair("second.admin@example.com", "second.teacher@example.com"), "owner", pdf(), pdf())
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=.2)
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "ready"
        with pytest.raises(ValueError, match="already belong"):
            second.result(timeout=5)
    assert len(list(tmp_path.glob("*/run.json"))) == 1
    assert state.windows == []


@pytest.mark.parametrize("switched", [
    pair("stale.teacher@example.com", "new.teacher@example.com"),
    pair("new.admin@example.com", "stale.admin@example.com"),
])
def test_finished_run_account_waits_for_its_open_window_before_switching_roles(tmp_path, switched):
    grading, release = threading.Event(), threading.Event()

    def keep_an_unrelated_run_active(folder, source, selected, authorization):
        if selected["evaluator_email"] == "keeper.teacher@example.com":
            grading.set()
            assert release.wait(10)
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}

    store, state = fixture_store(tmp_path, grader=keep_an_unrelated_run_active)
    keeper = store.create([another_run_row(1)], pair("keeper.admin@example.com", "keeper.teacher@example.com"), "owner", pdf(), pdf())
    finished = store.create([another_run_row(2)], pair("stale.admin@example.com", "stale.teacher@example.com"), "owner", pdf(), pdf())
    switched_run = store.create([another_run_row(3)], switched, "owner", pdf(), pdf())
    try:
        store.start(keeper["id"], "token")
        assert grading.wait(5)
        store.start(finished["id"], "token")
        deadline = time.monotonic() + 5
        while store.slot(finished["id"]) is not None and time.monotonic() < deadline:
            time.sleep(.01)
        assert store.slot(finished["id"]) is None
        assert store.read(finished["id"])["status"] == "complete"
        assert not state.closed, "The unrelated active run should keep account windows open"
        with pytest.raises(workflow.BusyError, match="window open"):
            store.start(switched_run["id"], "token")
    finally:
        release.set()
        wait_idle(store, seconds=20)
    # Once all previous windows have closed, moving the account to the other role is safe.
    store.start(switched_run["id"], "token")
    wait_idle(store, seconds=20)
    assert store.read(switched_run["id"])["status"] == "complete"


def test_skip_assignment_uses_only_the_evaluator_window(tmp_path):
    store, state = fixture_store(tmp_path)
    run = store.create([another_run_row(1)], pair("", already_assigned=True), "owner", pdf(), pdf())
    store.work(run["id"], "token")
    assert store.read(run["id"])["status"] == "complete"
    assert [action[0] for action in state.actions] == ["upload"]
    assert len(state.windows) == 1 and state.windows[0]["evaluator"] == "teacher@example.com"
