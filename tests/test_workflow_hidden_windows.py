"""Operator's request of 26 Oct 2026 (build 140): not sixteen Chrome windows on the screen.

The portal windows run hidden (Chrome's headless mode, with the same profiles and logins). One
comes on screen, at the page it was on, when it needs a person: its ID cannot sign itself in, or a
copy failed there on its last attempt or in a way that stops the run. It hides itself again after
the sign-in; Show / Hide on the workflow page, or every window on screen. A sign-in that only
missed (a slow portal) is tried again by itself. Chrome, Playwright and the portal are doubles.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
from portal_vendor import data_filler as real
from test_integrated_workflow import LocalClient, token

ADMIN, EVALUATOR = "admin.one@example.com", "eval.one@example.com"
PORTS = {"admin": (9601, "admin_profile"), "evaluator": (9602, "evaluator_profile")}   # never a live run's ports


class FakePage:
    def __init__(self, desktop=None):
        self.url, self.visits, self.closed, self.fronted, self.desktop = "about:blank", [], False, 0, desktop

    def wait_for_timeout(self, ms):
        if self.desktop is not None and self.desktop.on_wait is not None:
            self.desktop.on_wait()

    def is_closed(self):
        return self.closed

    def goto(self, url, wait_until=None):
        self.url = url
        self.visits.append(url)

    def bring_to_front(self):
        self.fronted += 1

    def evaluate(self, script, arg=None):
        return None


class Desktop:
    """Chrome as the desk sees it: every launch is a new window on its port, hidden or shown."""
    def __init__(self):
        self.launches, self.pages, self.on_wait = [], [], None   # on_wait: what happens while a page waits

    def start_chrome(self):
        Path(real.runtime_setting("USER_DATA_DIR")).mkdir(parents=True, exist_ok=True)
        self.launches.append((real.runtime_setting("CDP_PORT"), "hidden" if real.runtime_setting("HEADLESS") else "shown"))
        self.pages.append(FakePage(self))
        return SimpleNamespace(wait=lambda timeout=None: None, terminate=lambda: None, poll=lambda: None)

    def connect(self, url):
        context = SimpleNamespace(pages=[self.pages[-1]], set_default_timeout=lambda _: None, add_init_script=lambda _: None)
        return SimpleNamespace(contexts=[context], is_connected=lambda: True)

    def close_chrome(self, browser, chrome, page):
        page.closed = True


def make_portal(tmp_path, desktop, signed_in, admin="", evaluator=EVALUATOR, **extra):
    def wait_for_manual_login(page):
        """The engine's wait: the portal's login page, the status hook, then the give-up prompt."""
        page.goto(real.BASE_URL)
        if not signed_in["now"]:
            real.runtime_setting("on_status")("Waiting for you to log in to the evaluation site in the Chrome window...")
            if not signed_in["now"]:
                real.runtime_setting("ask_continue")("Site login was not detected.")
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=desktop.connect), stop=lambda: None)
    engine = SimpleNamespace(
        runtime_context=real.runtime_context, BASE_URL=real.BASE_URL, DEFAULT_TIMEOUT_MS=100, CHROME_CLOSE_TIMEOUT_S=1,
        StudentRecord=real.StudentRecord, AbortRun=real.AbortRun, validate_record=lambda record: None, whole_label=real.whole_label,
        log=SimpleNamespace(warning=lambda *args: None, info=lambda *args: None),
        start_chrome=desktop.start_chrome, close_chrome=desktop.close_chrome, chrome_is_listening=lambda: False,
        sync_playwright=lambda: SimpleNamespace(start=lambda: playwright),
        wait_for_manual_login=wait_for_manual_login, site_logged_in=lambda page: signed_in["now"],
        reset_page=lambda *args: None, save_screenshot=lambda page, student: f"shots/{student}.png", **extra)
    portal = workflow.Portal.__new__(workflow.Portal)
    portal.engine, portal.root, portal.rejections, portal.browser = engine, tmp_path, None, None
    portal.papers = SimpleNamespace(TEST_URL="{test}", api_get=lambda page, url: {"data": {"name": "Maths Test"}},
                                    site_files=lambda details: ([], None))
    portal.sessions, portal.connect_delay, portal.playwright, portal.windows = {}, 0, None, dict(PORTS)
    portal.admin, portal.evaluator, portal.should_stop = admin, evaluator, lambda: False
    portal.alert_hook, portal.login_password, portal.login_report = None, lambda account: "", None
    portal.board = workflow.PortalWindows(tmp_path)
    portal.SIGN_IN_CHECK_SECONDS = portal.PERSON_WAIT_SECONDS = 0
    return portal


def window(portal, role="evaluator"):
    return next(entry for entry in portal.board.status()["windows"] if entry["role"] == role)


ROW = {"program_name": "Program", "test_series_name": "Series", "test_name": "Maths Test", "student_id": "student123",
       "student_name": "Example", "failures": 0}
OPTIONS = {"evaluator_email": EVALUATOR, "deadline": "2027-01-01T12:00"}
TEST = {"test_id": "test1", "test_name": "Maths Test"}


# ------------------------------------------------------------------------ hidden, then on screen

def test_a_window_works_hidden_and_comes_on_screen_for_a_sign_in_then_hides_again(tmp_path):
    desktop, signed_in = Desktop(), {"now": False}
    portal = make_portal(tmp_path, desktop, signed_in)
    with pytest.raises(workflow.PortalLoginWait, match="is on screen now: sign in there"):
        portal.exam_documents(TEST)
    # Opened hidden; nobody can sign in there, so it was reopened on screen at the portal.
    assert desktop.launches == [(9602, "hidden"), (9602, "shown")]
    assert desktop.pages[0].closed and desktop.pages[1].visits == [real.BASE_URL] and desktop.pages[1].fronted == 1
    now = window(portal)
    assert now["open"] and now["shown"] and now["why"] == "sign_in" and EVALUATOR in now["reason"]
    assert (tmp_path / "evaluator_profile" / workflow.Portal.WINDOW_MODE_FILE).read_text(encoding="utf-8") == "shown"

    signed_in["now"] = True                                # the operator signed in there
    assert portal.exam_documents(TEST) == {"question_paper": [], "answer_key": [], "links": {"question_paper": None, "answer_key": []}}
    assert desktop.launches[-1] == (9602, "hidden") and len(desktop.launches) == 3   # hidden again, signed in
    now = window(portal)
    assert now["open"] and not now["shown"] and now["why"] == ""
    portal.close()
    assert portal.board.status()["windows"] == []                         # closed, no problem: not listed


def test_without_a_window_list_the_windows_are_on_screen_as_before(tmp_path):
    desktop = Desktop()
    portal = make_portal(tmp_path, desktop, {"now": True})
    portal.board = None
    portal.exam_documents(TEST)
    assert desktop.launches == [(9602, "shown")]


def test_every_window_on_screen_when_the_operator_wants_them(tmp_path):
    desktop = Desktop()
    portal = make_portal(tmp_path, desktop, {"now": True})
    portal.board.set_show_all(True)
    portal.exam_documents(TEST)
    assert desktop.launches == [(9602, "shown")]
    assert json.loads((tmp_path / "portal_windows.json").read_text(encoding="utf-8")) == {"show_all": True}
    assert workflow.PortalWindows(tmp_path).show_all is True          # kept for the next app start
    portal.board.set_show_all(False)
    portal.arrange_windows()
    assert desktop.launches[-1] == (9602, "hidden")


# --------------------------------------------------------------------- a copy that keeps failing

def failing_admin(tmp_path, message="Programs not visible"):
    desktop = Desktop()
    def open_allotment_test(page, record):
        page.goto(real.BASE_URL + "tev/allotment/students")
        raise RuntimeError(message)
    portal = make_portal(tmp_path, desktop, {"now": True}, admin=ADMIN, evaluator="", open_allotment_test=open_allotment_test)
    return portal, desktop


def test_a_copy_failing_on_its_last_attempt_brings_its_window_on_screen_at_that_page(tmp_path):
    portal, desktop = failing_admin(tmp_path)
    with pytest.raises(RuntimeError, match=r"Programs not visible \(screenshot: student123.png\)"):
        portal.perform("assign", dict(ROW, failures=0), OPTIONS)
    now = window(portal, "admin")
    assert desktop.launches == [(9601, "hidden")] and now["why"] == "" and not now["shown"]   # it tries again by itself
    assert now["problem"].startswith("Programs not visible") and now["screenshot"] == "student123.png" and "url" not in now

    with pytest.raises(RuntimeError):
        portal.perform("assign", dict(ROW, failures=workflow.COPY_FAILURE_LIMIT - 1), OPTIONS)
    now = window(portal, "admin")
    assert desktop.launches == [(9601, "hidden"), (9601, "shown")] and now["shown"] and now["why"] == "problem"
    assert desktop.pages[-1].visits == [real.BASE_URL + "tev/allotment/students"]     # the page where it failed

    portal.board.hide("admin", ADMIN)                                                 # the operator has looked
    portal.arrange_windows()
    assert desktop.launches[-1] == (9601, "hidden") and not window(portal, "admin")["shown"]


def test_a_failure_that_stops_the_run_shows_its_window_at_once(tmp_path):
    portal, desktop = failing_admin(tmp_path, "'x@example.com' was not found among the 3 evaluator options")
    with pytest.raises(RuntimeError):
        portal.perform("assign", dict(ROW), OPTIONS)
    assert desktop.launches == [(9601, "hidden"), (9601, "shown")] and window(portal, "admin")["why"] == "problem"


def test_checking_an_uncertain_assignment_uses_the_admin_window(tmp_path):
    portal, desktop = failing_admin(tmp_path)
    with pytest.raises(RuntimeError):
        portal.perform("verify_assign", dict(ROW), OPTIONS)
    assert desktop.launches[0] == (9601, "hidden")      # before build 140: the default evaluator window


def test_the_operators_show_outlasts_a_sign_in(tmp_path):
    board = workflow.PortalWindows(tmp_path)
    board.need("evaluator", EVALUATOR, "you", "Shown by you")
    board.need("evaluator", EVALUATOR, "sign_in", "Sign in here")
    board.signed_in("evaluator", EVALUATOR)
    assert board.wanted("evaluator", EVALUATOR) and board.status()["windows"] == []    # not open: not listed
    board.opened("evaluator", EVALUATOR, True)
    assert board.status()["windows"][0]["why"] == "you"
    board.closed("evaluator", EVALUATOR)                                                # closed by hand: a Hide
    assert not board.wanted("evaluator", EVALUATOR)


def test_a_window_left_open_by_an_earlier_session_reports_its_own_mode(tmp_path):
    portal = make_portal(tmp_path, Desktop(), {"now": True})
    assert portal.window_shown("evaluator", None, False) is True             # windows before build 140 were shown
    (tmp_path / "evaluator_profile").mkdir()
    assert portal.window_shown("evaluator", object(), False) is False
    assert portal.window_shown("evaluator", None, True) is False             # attached: as that launch recorded


# ---------------------------------------------------------------------------- signing itself in

def test_a_sign_in_that_missed_needs_a_person_once_but_is_tried_again_later_a_refused_one_is_not(tmp_path):
    """Operator, 26 Oct 2026 (build 141): eight windows could not sign themselves in; a person signs
    each in once. A miss still leaves the ID to sign itself in the next time its login ends."""
    logins = workflow.PortalLogins(tmp_path, protect=lambda secret: secret, reveal=lambda stored: stored)
    alerts = []
    logins.alert = lambda account, detail: alerts.append(account)
    logins.save("fixture-password")
    assert logins.report(EVALUATOR, False, "the portal did not open after Login", refused=False) is True
    assert alerts == [EVALUATOR] and logins.status()["suspended"] == [] and logins.password_for(EVALUATOR) == "fixture-password"
    assert logins.report(EVALUATOR, True) is False and logins.misses == {}
    assert logins.report(EVALUATOR, False, "the portal kept its login form after Login") is True
    assert logins.status()["suspended"] == [EVALUATOR] and logins.password_for(EVALUATOR) == ""


def test_a_window_that_could_not_sign_itself_in_comes_on_screen_and_is_not_reloaded_while_you_sign_in(tmp_path):
    desktop, signed_in = Desktop(), {"now": False}
    portal = make_portal(tmp_path, desktop, signed_in)
    logins = workflow.PortalLogins(tmp_path, protect=lambda secret: secret, reveal=lambda stored: stored)
    logins.save("fixture-password")
    portal.login_password, portal.login_report = logins.password_for, logins.report
    # FakePage has no login form: the automatic sign-in misses without the portal refusing anything.
    with pytest.raises(workflow.PortalLoginWait, match="is on screen now: sign in there"):
        portal.exam_documents(TEST)
    assert desktop.launches == [(9602, "hidden"), (9602, "shown")] and window(portal)["why"] == "sign_in"
    shown = desktop.pages[-1]
    with pytest.raises(workflow.PortalLoginWait, match="waiting for you to sign in there"):
        portal.exam_documents(TEST)                        # the run tries again while nobody has signed in yet
    assert shown.visits == [real.BASE_URL] and len(desktop.launches) == 2    # no reload under the person typing
    signed_in["now"] = True
    assert portal.exam_documents(TEST) == {"question_paper": [], "answer_key": [], "links": {"question_paper": None, "answer_key": []}}
    assert desktop.launches[-1] == (9602, "hidden") and window(portal)["signed_in"]


def test_sign_in_the_windows_keeps_signed_in_ones_hidden_and_shows_the_others_until_signed_in(tmp_path):
    desktop = Desktop()
    portal = make_portal(tmp_path / "a", desktop, {"now": True})
    assert portal.sign_in("evaluator") == "signed_in"
    assert desktop.launches == [(9602, "hidden")] and window(portal)["signed_in"] and not window(portal)["shown"]

    other_desktop, state = Desktop(), {"now": False}
    other = make_portal(tmp_path / "b", other_desktop, state)
    # The operator signs in once the window is on screen.
    other_desktop.on_wait = lambda: state.update(now=other_desktop.launches[-1][1] == "shown")
    other.SIGN_IN_WAIT_SECONDS = 60
    assert other.sign_in("evaluator") == "signed_in"
    assert other_desktop.launches == [(9602, "hidden"), (9602, "shown"), (9602, "hidden")]
    assert other_desktop.pages[1].visits == [real.BASE_URL] and window(other)["signed_in"] and window(other)["why"] == ""

    third_desktop = Desktop()
    third = make_portal(tmp_path / "c", third_desktop, {"now": False})
    third.SIGN_IN_WAIT_SECONDS = 0
    assert third.sign_in("evaluator") == "waiting"          # nobody signed in within the wait: it stays on screen
    assert third_desktop.launches[-1] == (9602, "shown") and window(third)["why"] == "sign_in"


def test_automatic_sign_ins_take_turns(tmp_path):
    held = []

    class Form:
        first = property(lambda self: self)

        def wait_for(self, **kwargs):
            held.append(workflow.Portal.LOGIN_TURN.locked())

        def fill(self, value):
            pass

        def click(self):
            pass

        def is_visible(self):
            return True
    page = SimpleNamespace(locator=lambda selector: Form(), get_by_role=lambda *args, **kwargs: Form(), wait_for_timeout=lambda ms: None)
    portal = make_portal(tmp_path, Desktop(), {"now": False})
    portal.login_password, portal.AUTO_LOGIN_SECONDS = (lambda account: "fixture-password"), 0.5
    assert portal.auto_login("evaluator", page) is False      # the form stayed: refused
    assert held == [True] and not workflow.Portal.LOGIN_TURN.locked()


# ------------------------------------------------------------------------- the launcher

@pytest.mark.parametrize("name, brand", [("chrome.exe", ""), ("msedge.exe", " Edg/155.0.0.0")])
def test_a_hidden_window_is_launched_headless_with_a_normal_browser_name(tmp_path, monkeypatch, name, brand):
    browser = tmp_path / "Application" / name
    (tmp_path / "Application" / "155.0.8059.85").mkdir(parents=True)
    (tmp_path / "Application" / "154.0.7000.1").mkdir()
    launched = []
    calls = iter([False, True, False, True])
    monkeypatch.setattr(real, "chrome_is_listening", lambda: next(calls))
    monkeypatch.setattr(real, "find_chrome", lambda: browser)
    monkeypatch.setattr(real.subprocess, "Popen", lambda args: launched.append(args) or object())
    with real.runtime_context(CDP_PORT=9603, USER_DATA_DIR=tmp_path / "profile", HEADLESS=True):
        real.start_chrome()
    with real.runtime_context(CDP_PORT=9603, USER_DATA_DIR=tmp_path / "profile"):
        real.start_chrome()
    hidden, shown = launched
    agent = ("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             f"Chrome/155.0.0.0 Safari/537.36{brand}")
    assert "--headless=new" in hidden and "--window-size=1920,1080" in hidden and agent in hidden
    assert "--start-maximized" not in hidden and hidden[-1] == "about:blank"
    assert "--start-maximized" in shown and not any(arg.startswith(("--headless", "--user-agent")) for arg in shown)


# ----------------------------------------------------------------------------- the page's API

def test_the_windows_api_lists_shows_hides_and_keeps_all_on_screen(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    assert client.get("/api/workflow/windows", headers=auth).json() == {"show_all": False, "windows": []}
    s.windows.opened("evaluator", EVALUATOR, False)
    s.windows.note("evaluator", EVALUATOR, "Programs not visible", "student123_1.png", real.BASE_URL + "tev/x")
    shown = client.post("/api/workflow/windows/show", headers=auth, json={"role": "evaluator", "account": EVALUATOR, "show": True}).json()
    entry = shown["windows"][0]
    assert entry["wanted"] and entry["why"] == "you" and not entry["shown"] and entry["screenshot"] == "student123_1.png" and "url" not in entry
    hidden = client.post("/api/workflow/windows/show", headers=auth, json={"role": "evaluator", "account": EVALUATOR, "show": False}).json()
    assert not hidden["windows"][0]["wanted"]
    assert client.post("/api/workflow/windows/show", headers=auth, json={"role": "boss", "account": "", "show": True}).status_code == 400
    assert client.post("/api/workflow/windows", headers=auth, json={"show_all": "yes"}).status_code == 400
    assert client.post("/api/workflow/windows", headers=auth, json={"show_all": True}).json()["show_all"] is True
    assert s.windows.wanted("evaluator", EVALUATOR)
    shots = tmp_path / "screenshots"
    shots.mkdir()
    (shots / "student123_1.png").write_bytes(b"\x89PNG fixture")
    picture = client.get("/api/workflow/windows/screenshot/student123_1.png", headers=auth)
    assert picture.status_code == 200 and picture.content == b"\x89PNG fixture" and picture.headers["content-type"] == "image/png"
    (tmp_path / "run.json").write_text("{}", encoding="utf-8")
    assert client.get("/api/workflow/windows/screenshot/run.json", headers=auth).status_code == 404
    assert client.get("/api/workflow/windows/screenshot/..%5Crun.png", headers=auth).status_code == 404


def test_sign_in_the_windows_opens_every_ids_window_on_its_desk(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    opened = []

    class DeskPortal:
        engine = object()

        def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
            self.account = admin or evaluator

        def sign_in(self, kind):
            opened.append((kind, self.account))
    s.portal_factory = DeskPortal
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    accounts = [{"role": "admin", "account": ADMIN}, {"role": "evaluator", "account": EVALUATOR},
                {"role": "evaluator", "account": EVALUATOR.upper()}]
    assert client.post("/api/workflow/windows/sign-in", headers=auth, json={"accounts": accounts}).status_code == 200
    for desk in s.desks.all():
        desk.submit(lambda: None)                                   # after the queued sign-ins
    assert sorted(opened) == [("admin", ADMIN), ("evaluator", EVALUATOR)]
    for bad in ({}, {"accounts": []}, {"accounts": [{"role": "boss", "account": ADMIN}]},
                {"accounts": [{"role": "admin", "account": "not an email"}]}):
        assert client.post("/api/workflow/windows/sign-in", headers=auth, json=bad).status_code == 400


def test_show_and_hide_reach_the_window_on_its_own_desk(tmp_path):
    arranged = []

    class DeskPortal:
        engine = object()
        def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
            self.evaluator = evaluator
        def perform(self, *args, **kwargs):
            return self.board
        def arrange_windows(self):
            arranged.append(self.evaluator)
    s = workflow.WorkflowStore(tmp_path, lambda *a: {}, DeskPortal)
    import threading
    desk = s.desks.evaluator(EVALUATOR)
    assert desk.call(threading.Event(), "perform", "upload") is s.windows      # the desk hands its window the list
    s.show_window("evaluator", EVALUATOR, True)
    desk.submit(lambda: None)                                                     # after the queued arrange
    assert arranged == [EVALUATOR]
    s.show_all_windows(False)
    desk.submit(lambda: None)
    assert arranged == [EVALUATOR, EVALUATOR]


def test_the_autopilot_shows_each_running_tests_copies_so_far(tmp_path):
    import workflow_autopilot
    store = workflow.WorkflowStore(tmp_path, lambda *a: {})
    run = {"id": "a" * 32, "options": {"evaluator_email": EVALUATOR}, "rows": [
        {"stage": "uploaded", "test_name": "Maths Test"}, {"stage": "uploaded", "flagged_for_review": True},
        {"stage": "pending", "operation": "waiting for a portal login: the window is on screen"}, {"stage": "assigned", "operation": "checking"}]}
    (tmp_path / run["id"]).mkdir()
    (tmp_path / run["id"] / "run.json").write_text(json.dumps(run), encoding="utf-8")
    pilot = workflow_autopilot.Autopilot(store, tmp_path)
    assert pilot.progress(run["id"]) == ("2 of 4 uploaded (1 flagged for faculty review); 2 in progress now "
                                         "(1 waiting for a portal sign-in)")
    assert pilot.progress("b" * 32) == ""
