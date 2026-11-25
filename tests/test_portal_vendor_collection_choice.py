"""The optional collection chooser never needs an operator, including after Login."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import integrated_workflow as workflow
from portal_vendor import data_filler as engine


class TextLocator:
    def __init__(self, page, text, exact=True, index=None):
        self.page, self.text, self.exact, self.index = page, text, exact, index

    @property
    def first(self):
        return self.nth(0)

    def matches(self):
        if self.text == "Task Management":
            return [("Task Management", "/tev/" in self.page.url)]
        return [(text, visible) for text, visible in self.page.options
                if text == self.text or (not self.exact and self.text in text)]

    def nth(self, index):
        return TextLocator(self.page, self.text, self.exact, index)

    def count(self):
        return len(self.matches())

    def is_visible(self):
        matches = self.matches()
        return self.index is not None and self.index < len(matches) and matches[self.index][1]

    def wait_for(self, state="visible", timeout=0):
        self.page.waits.append((self.text, state))
        if not self.is_visible():
            raise engine.PWTimeout("not visible")

    def click(self):
        assert self.is_visible()
        self.page.clicks.append(self.text)
        if self.page.route_after_click:
            self.page.url = engine.BASE_URL + "tev/dashboard"


class Input:
    def __init__(self, page, name):
        self.page, self.name = page, name

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        pass

    def fill(self, value):
        self.page.filled[self.name] = value

    def click(self):
        self.page.clicks.append("Login")
        self.page.url = engine.BASE_URL + "collections"


class CollectionPage:
    def __init__(self, url=None, options=None, route_after_click=True):
        self.url = url or engine.BASE_URL + "collections"
        self.options = options if options is not None else [
            ("Error Reporting Portal", True), ("Test Evaluation", True)]
        self.route_after_click = route_after_click
        self.clicks, self.waits, self.filled, self.queries = [], [], {}, []

    def get_by_text(self, text, exact=False):
        self.queries.append((text, exact))
        return TextLocator(self, text, exact)

    def wait_for_url(self, pattern, timeout=0):
        assert timeout == engine.DEFAULT_TIMEOUT_MS
        if not pattern.search(self.url):
            raise engine.PWTimeout("evaluation route did not open")

    def goto(self, url, **kwargs):
        # Opening the root with a saved login redirects to the chooser.
        self.url = engine.BASE_URL + "collections"

    def wait_for_timeout(self, milliseconds):
        pass

    def locator(self, selector):
        return Input(self, "email" if "email" in selector else "password")

    def get_by_role(self, role, name=None):
        assert role == "button"
        return Input(self, "Login")


def test_exact_evaluation_choice_continues_only_after_its_dashboard_opens():
    page = CollectionPage(options=[("Error Reporting Portal", True),
                                   ("Test Evaluation (old)", True),
                                   ("Test Evaluation", True)])
    assert engine.site_logged_in(page) is True
    assert page.clicks == ["Test Evaluation"]
    assert ("Task Management", "visible") in page.waits
    assert all(exact for _, exact in page.queries)


def test_hidden_duplicate_does_not_block_the_visible_choice():
    page = CollectionPage(options=[("Test Evaluation", False), ("Test Evaluation", True)])
    assert engine.select_test_evaluation(page) is True
    assert page.clicks == ["Test Evaluation"]


def test_option_rendered_after_navigation_is_selected_without_manual_login():
    page = CollectionPage(options=[])
    page.wait_for_timeout = lambda milliseconds: page.options.append(("Test Evaluation", True))
    assert engine.site_logged_in(page) is True
    assert page.clicks == ["Test Evaluation"]


@pytest.mark.parametrize("destination, signed_in", [("", False), ("tev/dashboard", True)])
def test_redirect_away_from_a_loading_chooser_uses_normal_login_detection(destination, signed_in):
    page = CollectionPage(options=[])
    page.wait_for_timeout = lambda milliseconds: setattr(page, "url", engine.BASE_URL + destination)
    assert engine.site_logged_in(page) is signed_in
    assert page.clicks == []


@pytest.mark.parametrize("url", [engine.BASE_URL, engine.BASE_URL + "tev/dashboard",
                                engine.BASE_URL + "tev/evaluation",
                                "https://another.example/collections",
                                "http://special-ops-content-frontend.penpencil.co/collections",
                                engine.BASE_URL + "collections/archive"])
def test_other_pages_are_not_clicked_or_queried(url):
    page = CollectionPage(url=url)
    assert engine.select_test_evaluation(page) is False
    assert page.clicks == [] and page.queries == []


def test_trailing_slash_and_query_parameters_are_supported():
    page = CollectionPage(url=engine.BASE_URL + "collections/?from=login")
    assert engine.select_test_evaluation(page) is True


@pytest.mark.parametrize("options, message", [
    ([("Error Reporting Portal", True), ("Test Evaluation (old)", True)], "no visible"),
    ([("Test Evaluation", False)], "no visible"),
    ([("Test Evaluation", True), ("Test Evaluation", True)], "more than one"),
])
def test_missing_or_ambiguous_choice_never_opens_another_portal(monkeypatch, options, message):
    monkeypatch.setattr(engine, "DEFAULT_TIMEOUT_MS", 0)
    page = CollectionPage(options=options)
    with pytest.raises(LookupError, match=message):
        engine.site_logged_in(page)
    assert page.clicks == []


def test_click_without_evaluation_navigation_is_a_failure():
    page = CollectionPage(route_after_click=False)
    with pytest.raises(engine.PWTimeout, match="evaluation route did not open"):
        engine.site_logged_in(page)
    assert page.waits == []


def test_cancellation_does_not_click_a_collection(monkeypatch):
    monkeypatch.setattr(engine, "should_stop", lambda: True)
    page = CollectionPage()
    with pytest.raises(engine.AbortRun, match="Stopped by you"):
        engine.site_logged_in(page)
    assert page.clicks == []


def test_saved_session_chooser_never_requests_manual_login(monkeypatch):
    page = CollectionPage()
    monkeypatch.setattr(engine, "server_accepts_login", lambda page: True)
    monkeypatch.setattr(engine, "on_status", lambda message: pytest.fail("manual login was requested"))
    engine.wait_for_manual_login(page)
    assert page.clicks == ["Test Evaluation"]


@pytest.mark.parametrize("kind", ["admin", "evaluator"])
def test_fresh_automatic_login_selects_evaluation_for_both_account_roles(tmp_path, kind):
    portal = workflow.Portal(tmp_path, evaluator="evaluator@example.com", admin="admin@example.com")
    portal.login_password = lambda account: "fixture-password"
    reports = []
    portal.login_report = lambda *args: reports.append(args)
    page = CollectionPage(url=engine.BASE_URL)
    assert portal.auto_login(kind, page) is True
    assert page.clicks == ["Login", "Test Evaluation"]
    assert page.filled == {"email": kind + "@example.com", "password": "fixture-password"}
    assert reports == [(kind + "@example.com", True, "")]

