"""Operator's report of 26 Oct 2026 (build 138): portal windows were logged out by themselves.

The supplied engine took any error reply to its login check as a cancelled login and wiped
the window's login, so a busy portal logged windows out; only 401/403 may do that now. And a
logged-out window signs itself in with its ID and the portal password saved on this computer,
never twice with a password that failed. The real engine runs against a fixture page; no
request reaches the portal.
"""
from __future__ import annotations

import json
import logging
import os
import sys

import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
from portal_vendor import data_filler as engine
from test_integrated_workflow import LocalClient, token

ACCOUNT = "teacher@example.com"
SECRET = "fixture-password"


class Response:
    def __init__(self, status):
        self.status, self.ok = status, 200 <= status < 300


class Locator:
    def __init__(self, page, name):
        self.page, self.name = page, name

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=0):
        if self.name == "Task Management" and not self.page.logged_in:
            raise engine.PWTimeout("login page")
        if self.name in ("email", "password") and self.page.logged_in:
            raise engine.PWTimeout("no login form")

    def is_visible(self):
        return self.page.logged_in if self.name == "Task Management" else True

    def fill(self, value):
        self.page.filled[self.name] = value

    def click(self):
        page = self.page
        page.clicks += 1
        if page.filled.get("email") == ACCOUNT and page.filled.get("password") == page.password:
            page.logged_in, page.token = True, "fresh-token"


class Page:
    """The portal as the engine sees it: the login form, or the signed-in pages under /tev/."""
    def __init__(self, logged_in=False, check_status=200, password=SECRET):
        self.logged_in, self.check_status, self.password = logged_in, check_status, password
        self.token = "saved-token" if logged_in else None
        self.filled, self.clicks, self.visits = {}, 0, []
        self.request = self

    @property
    def url(self):
        return engine.BASE_URL + ("tev/dashboard" if self.logged_in else "")

    def goto(self, url, wait_until=None):
        self.visits.append(url)

    def get(self, url, headers=None, timeout=None):
        if isinstance(self.check_status, Exception):
            raise self.check_status
        return Response(200 if self.token == "fresh-token" else self.check_status)   # a new login is valid

    def evaluate(self, script, arg=None):
        if "getItem('access_token')" in script:
            return self.token
        self.token, self.logged_in = None, False      # forget_site_login removes the saved login
        return None

    def get_by_text(self, text, exact=False):
        return Locator(self, text)

    def locator(self, selector):
        return Locator(self, "email" if "email" in selector else "password")

    def get_by_role(self, role, name=None):
        return Locator(self, "login")

    def wait_for_timeout(self, ms):
        pass


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "LOGIN_TIMEOUT_MS", 0)              # the manual wait gives up at once
    adapter = workflow.Portal(tmp_path, evaluator=ACCOUNT)          # patches the engine as a live run does
    logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret[::-1], reveal=lambda stored: stored[4:][::-1])
    alerts = []
    logins.alert = lambda account, detail: alerts.append((account, detail))
    adapter.login_password, adapter.login_report = logins.password_for, logins.report
    return adapter, logins, alerts


# ----------------------------------------------------------------------- the login check

@pytest.mark.parametrize("status, kept", [(200, True), (429, True), (500, True), (502, True), (503, True), (401, False), (403, False)])
def test_only_a_real_rejection_ends_a_windows_login(portal, status, kept, caplog):
    adapter, logins, alerts = portal
    with caplog.at_level(logging.WARNING, logger="data_filler"), adapter.runtime("evaluator"):
        assert engine.server_accepts_login(Page(logged_in=True, check_status=status)) is kept
    if status != 200:
        assert f"HTTP {status}" in caplog.text and ACCOUNT in caplog.text   # the log names the code and the window
    if kept and status != 200:
        assert "the login is kept" in caplog.text


def test_a_failed_login_check_keeps_the_login(portal):
    adapter, logins, alerts = portal
    with adapter.runtime("evaluator"):
        assert engine.server_accepts_login(Page(logged_in=True, check_status=engine.PWError("net::ERR_CONNECTION_RESET"))) is True


# --------------------------------------------------------------------- signing in itself

def wait_for_login(adapter, page):
    """The engine's own login wait in this window, as a live session runs it."""
    adapter.sessions = {"evaluator": {"page": page}}
    with adapter.runtime("evaluator"):
        engine.wait_for_manual_login(page)


def test_a_logged_out_window_signs_itself_in(portal):
    adapter, logins, alerts = portal
    logins.save(SECRET)
    page = Page(logged_in=False)
    wait_for_login(adapter, page)                                     # returns: no person needed
    assert page.logged_in and page.filled == {"email": ACCOUNT, "password": SECRET} and page.clicks == 1
    assert logins.status()["recent"][0]["ok"] is True and alerts == []
    assert SECRET not in json.dumps(logins.status()) and SECRET not in logins.path.read_text(encoding="utf-8")


def test_a_login_the_portal_ended_is_signed_in_again(portal):
    adapter, logins, alerts = portal
    logins.save(SECRET)
    page = Page(logged_in=True, check_status=401)                    # the portal ended this login
    wait_for_login(adapter, page)
    assert page.logged_in and page.token == "fresh-token" and page.clicks == 1


def test_a_wrong_password_is_tried_once_then_the_window_waits_for_a_person(portal):
    adapter, logins, alerts = portal
    logins.save("wrong")
    page = Page(logged_in=False)
    with pytest.raises(workflow.PortalLoginWait):
        wait_for_login(adapter, page)                                 # the manual wait, as before
    assert page.clicks == 1 and alerts and alerts[0][0] == ACCOUNT
    assert logins.status()["suspended"] == [ACCOUNT]
    with pytest.raises(workflow.PortalLoginWait):
        wait_for_login(adapter, page)
    assert page.clicks == 1                                            # never a second try with that password
    logins.save(SECRET)                                                # a new password: it may try again
    wait_for_login(adapter, page)
    assert page.logged_in and page.clicks == 2 and logins.status()["suspended"] == []


def test_without_a_saved_password_the_window_waits_as_before(portal):
    adapter, logins, alerts = portal
    page = Page(logged_in=False)
    with pytest.raises(workflow.PortalLoginWait):
        wait_for_login(adapter, page)
    assert page.clicks == 0 and page.filled == {}


def test_a_signed_in_window_is_left_alone(portal):
    adapter, logins, alerts = portal
    logins.save(SECRET)
    page = Page(logged_in=True)
    wait_for_login(adapter, page)
    assert page.clicks == 0 and page.filled == {}


# --------------------------------------------------------------------- store, desk, API

def test_the_password_is_stored_encrypted_and_removable(tmp_path):
    logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret[::-1], reveal=lambda stored: stored[4:][::-1])
    assert logins.password_for(ACCOUNT) == "" and not logins.configured()
    logins.save(SECRET)
    assert logins.password_for(ACCOUNT) == SECRET and logins.password_for(ACCOUNT.upper()) == SECRET
    assert SECRET not in (tmp_path / "portal_login.json").read_text(encoding="utf-8")
    logins.save("")
    assert logins.password_for(ACCOUNT) == "" and not logins.configured()


def test_desks_hand_their_windows_the_saved_password(tmp_path):
    class EnginePortal:
        engine = object()
        def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
            self.evaluator = evaluator
        def perform(self, *args, **kwargs):
            return self.login_password(self.evaluator)
    s = workflow.WorkflowStore(tmp_path, lambda *a: {}, EnginePortal)
    s.portal_logins = workflow.PortalLogins(tmp_path, protect=lambda secret: secret, reveal=lambda stored: stored)
    s.portal_logins.save(SECRET)
    import threading
    desk = s.desks.evaluator(ACCOUNT)
    assert desk.call(threading.Event(), "perform", "upload") == SECRET


def test_the_portal_login_api_saves_reports_and_never_returns_the_password(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    s.portal_logins = workflow.PortalLogins(tmp_path, protect=lambda secret: "enc:" + secret[::-1], reveal=lambda stored: stored[4:][::-1])
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    assert client.get("/api/workflow/portal-login", headers=auth).json()["configured"] is False
    saved = client.post("/api/workflow/portal-login", headers=auth, json={"password": SECRET})
    assert saved.status_code == 200 and saved.json()["configured"] is True and SECRET not in saved.text
    s.portal_logins.report(ACCOUNT, False, "the portal did not open after Login")
    status = client.get("/api/workflow/portal-login", headers=auth).json()
    assert status["suspended"] == [ACCOUNT] and status["recent"][0]["detail"].startswith("the portal did not open")
    assert client.post("/api/workflow/portal-login", headers=auth, json={"password": 5}).status_code == 400
    assert client.post("/api/workflow/portal-login", headers=auth, json={"password": ""}).json()["configured"] is False


def test_a_failed_sign_in_alerts_the_runs_of_this_app(tmp_path):
    s = workflow.WorkflowStore(tmp_path, lambda *a: {})
    s.portal_logins.report(ACCOUNT, False, "the portal did not open after Login")
    assert s.alerts.raised[-1]["title"] == "Automatic portal login failed" and ACCOUNT in s.alerts.raised[-1]["body"]
