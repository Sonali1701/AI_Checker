"""The PW Skills admin portal: Lessons > Assignment submission, as direct requests.

admin-v2.penpencil.co/lesson/list is a frame around course-admin.pwskills.com, handed the
signed-in admin's "pp-token". Every call below is the one that app makes (read from its
public script, assets/index.d227e0d0.js, 9 Nov 2026): https://admin-api.penpencil.co with
"Authorization: Bearer <token>". It is an internal API and can change with a portal release.

The only write is grade(), the portal's Evaluate button. After it the learner's page says
"Evaluated" and takes no more submissions, so nothing here retries a write by itself.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

API_BASE = "https://admin-api.penpencil.co"
CDN_BASE = "https://cdn.pwskills.com"
ADMIN_SITE = "https://admin-v2.penpencil.co/dashboard"
SKILLS = "/v1/admin/skills"
ASSIGNMENTS = f"{SKILLS}/v2/lesson/lecture/assignment"
TOKEN_KEY = "pp-token"
PAGE_SIZE = 50
READ_ATTEMPTS = 3
REQUEST_TIMEOUT = (10, 60)
SIGN_IN_WAIT_SECONDS = 15 * 60
# Sent as the embedded app sends them, so a request looks like the page's own.
PAGE_HEADERS = {"Origin": "https://course-admin.pwskills.com", "Referer": "https://course-admin.pwskills.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/126.0 Safari/537.36", "Accept": "application/json, text/plain, */*"}

NOT_EVALUATED, UNDER_EVALUATION, EVALUATED = "submitted", "under_evaluation", "evaluated"


class SkillsPortalError(RuntimeError):
    """The portal answered and refused; the message is the portal's own."""


class SkillsSignInNeeded(SkillsPortalError):
    """No admin is signed in, or the portal ended the sign-in (401/403)."""


class SkillsPortalBusy(SkillsPortalError):
    """No usable answer (429, 5xx, a dropped connection): worth trying again later."""


def file_url(path):
    """The address of a file the portal stores by its path ("assignment/<id>.pdf")."""
    text = str(path or "").strip()
    if not text:
        return ""
    return text if text.lower().startswith(("https://", "http://")) else f"{CDN_BASE}/{text.lstrip('/')}"


def is_learner_link(value):
    """True when a submission is a link the learner gave (GitHub, Drive), not a stored file."""
    url = str(value or "").strip()
    return url.lower().startswith("https:") and urlparse(url).hostname != urlparse(CDN_BASE).hostname


def token_expiry(token):
    """The token's expiry as epoch seconds, or 0 when it does not say."""
    try:
        payload = str(token).split(".")[1]
        return int(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp") or 0)
    except (IndexError, ValueError, TypeError, AttributeError):
        return 0


def _record_id(value):
    return str(value.get("_id") or "") if isinstance(value, dict) else str(value or "")


def phone_digits(value):
    """A phone number as the portal stores it: the last 10 digits ("+91 79747 48131")."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:] if len(digits) > 10 else digits


def learner_filter(value):
    """The portal's filter for one learner given by phone number or email."""
    text = str(value or "").strip()
    return {"userEmail": text} if "@" in text else {"userPhone": phone_digits(text)}


def is_learner(fields, wanted):
    """Whether a row belongs to one of these learners, checked here and not only by the
    portal's filter: a filter the portal ignored must not let other learners in."""
    emails = {str(value).strip().casefold() for value in wanted if "@" in str(value)}
    phones = {phone_digits(value) for value in wanted if "@" not in str(value)}
    return fields["email"].casefold() in emails or (bool(fields["phone"]) and fields["phone"] in phones)


def submission_fields(row):
    """One row of the portal's list as the fields the checker uses."""
    submission, lesson = row.get("assignmentSubmission") or {}, row.get("lessonId")
    user = row.get("userId") if isinstance(row.get("userId"), dict) else {}
    points, data = submission.get("points") or {}, submission.get("data") or {}
    return {"id": _record_id(submission), "course_id": _record_id(row.get("courseId")),
            "lesson_id": _record_id(lesson),
            "assignment": str(lesson.get("title") or "") if isinstance(lesson, dict) else "",
            "student": " ".join(str(user.get(key) or "").strip() for key in ("firstName", "lastName")).strip(),
            "email": str(user.get("email") or "").strip(), "phone": phone_digits(user.get("primaryNumber")),
            "submitted_at": str(submission.get("submissionDate") or ""),
            "file": str(data.get("url") or "").strip(), "out_of": points.get("outOf"),
            "received": points.get("received"), "status": str(submission.get("status") or "")}


class SkillsClient:
    """One signed-in admin's calls. Reads are tried again when the portal is busy."""

    def __init__(self, token, session=None, sleep=time.sleep):
        self.token, self.http, self.sleep = str(token or "").strip(), session or requests.Session(), sleep
        if not self.token:
            raise SkillsSignInNeeded("Sign in to the admin portal first.")

    def _send(self, method, path, **kwargs):
        headers = {**PAGE_HEADERS, "Authorization": f"Bearer {self.token}"}
        try:
            response = self.http.request(method, API_BASE + path, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
        except (requests.ConnectTimeout, requests.ConnectionError):
            raise SkillsPortalBusy("This computer could not reach admin-api.penpencil.co. It is on PW's private "
                                   "network: connect to the office network or VPN and try again.") from None
        except requests.RequestException as exc:
            raise SkillsPortalBusy(f"The admin portal did not answer ({type(exc).__name__}).") from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, (dict, list)) else {}
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("msg") or body.get("error") or "")
        if response.status_code in (401, 403):
            raise SkillsSignInNeeded("The admin portal ended this sign-in. Sign in again.")
        if response.status_code == 429 or response.status_code >= 500:
            raise SkillsPortalBusy(f"The admin portal is busy (HTTP {response.status_code}).")
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
            raise SkillsPortalError(message or f"The admin portal refused the request (HTTP {response.status_code}).")
        return body

    def _read(self, method, path, **kwargs):
        for attempt in range(READ_ATTEMPTS):
            try:
                return self._send(method, path, **kwargs)
            except SkillsPortalBusy:
                if attempt == READ_ATTEMPTS - 1:
                    raise
                self.sleep(2 * (attempt + 1))

    def courses(self, name="", limit=50, skip=0):
        params = {"limit": int(limit)}
        if skip:
            params["skip"] = int(skip)      # the admin app pages its batch list the same way
        if name.strip():
            params["name"] = name.strip()
        data = self._read("GET", f"{SKILLS}/course/allcourses", params=params).get("data") or {}
        return [{"id": str(course.get("_id") or ""), "title": str(course.get("name") or course.get("title") or ""),
                 "batch": str(course.get("byName") or "")}
                for course in data.get("batches") or [] if course.get("_id")]

    def assignment_titles(self, course_id):
        """The assignments of a batch, as the portal's filter lists them."""
        names = (str(item.get("name") or item.get("title") or "") if isinstance(item, dict) else str(item or "")
                 for item in self._read("GET", f"{ASSIGNMENTS}/fetch/{course_id}").get("data") or [])
        return sorted({name.strip() for name in names if name.strip()}, key=str.casefold)

    def submissions_page(self, filters, page, limit=PAGE_SIZE):
        """(rows, total) for one page: the same request as Fetch assignments."""
        body = self._read("POST", f"{ASSIGNMENTS}/filterAssignment?page={int(page)}&limit={int(limit)}", json=filters)
        block = (body.get("data") or [{}])[0] if isinstance(body.get("data"), list) else {}
        total = ((block.get("metadata") or [{}])[0] or {}).get("total") or 0
        return list(block.get("data") or []), int(total)

    def submissions(self, course_id, status=NOT_EVALUATED, titles=(), most=0, learners=()):
        """Every submission of a batch in this status, each once; only these learners'
        (phone numbers or emails) when some are given.

        The list is newest first and a new submission moves rows down a page, so the same
        row can come back twice while this reads."""
        if learners:
            found = {}
            for learner in learners:
                for fields in self._submissions(course_id, status, titles, learner_filter(learner)):
                    if is_learner(fields, learners):
                        found.setdefault(fields["id"], fields)
            ordered = list(found.values())
            return ordered[:most] if most else ordered
        return self._submissions(course_id, status, titles, {}, most)

    def _submissions(self, course_id, status, titles, extra, most=0):
        filters = {"courseId": course_id, **extra}
        if status:
            filters["assignmentStatus"] = status
        if titles:
            filters["assignmentTitles"] = list(titles)
        found, page, limit = {}, 1, PAGE_SIZE
        while True:
            rows, total = self.submissions_page(filters, page, limit)
            if page == 1 and rows and len(rows) < min(limit, total):
                limit = len(rows)   # the portal caps the page size, and counts its pages in that size
            for row in rows:
                fields = submission_fields(row)
                if fields["id"]:
                    found.setdefault(fields["id"], {**fields, "course_id": fields["course_id"] or course_id})
            if not rows or len(found) >= total or (most and len(found) >= most):
                break
            page += 1
        ordered = list(found.values())
        return ordered[:most] if most else ordered

    def lesson(self, course_id, lesson_id):
        """The lesson's assignment details: paper, solution, points."""
        data = self._read("GET", f"{SKILLS}/v2/lesson", params={"courseId": course_id, "lessonId": lesson_id}).get("data") or {}
        assignment = data.get("assignmentV2") or {}
        return {"title": str(data.get("title") or ""), "paper": file_url(assignment.get("assignment")),
                "solution": file_url(assignment.get("answerSheet")), "points": assignment.get("points"),
                "passing_percentage": assignment.get("passingPercentage")}

    def current(self, course_id, lesson_id, email):
        """This learner's submission of this assignment as it stands now, or None."""
        rows, _total = self.submissions_page({"courseId": course_id, "userEmail": email}, 1)
        wanted = email.strip().casefold()
        for row in rows:
            fields = submission_fields(row)
            if fields["lesson_id"] == lesson_id and fields["email"].casefold() == wanted:
                return {**fields, "course_id": fields["course_id"] or course_id}
        return None

    def grade(self, submission_id, course_id, lesson_id, marks, out_of, comment):
        """The Evaluate button. Sent once; the caller works out what happened if it fails."""
        return self._send("POST", f"{ASSIGNMENTS}/grade/{submission_id}/{course_id}/{lesson_id}", json={
            "comment": comment, "points": {"received": float(marks), "outOf": float(out_of)},
            "assignmentStatus": NOT_EVALUATED})


def _read_token(page):
    return page.evaluate(f"() => localStorage.getItem({json.dumps(TOKEN_KEY)})") or ""


def browser_session(profile, *, headless, accept, on_waiting=lambda: None, should_stop=lambda: False,
                    wait_seconds=SIGN_IN_WAIT_SECONDS):
    """Open the admin portal in a window with its own saved profile and return the token
    of whoever is signed in there, once accept(token) says the portal takes it. A hidden
    look (headless) only reads a saved sign-in. Always runs on a thread of its own: the
    workflow's Playwright loop owns its thread."""
    from playwright.sync_api import Error as BrowserError, sync_playwright
    Path(profile).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context, failure = None, None
        for channel in ("chrome", "msedge"):
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), channel=channel, headless=headless, no_viewport=True, args=["--start-maximized"])
                break
            except BrowserError as exc:
                failure = exc
        if context is None:
            raise SkillsPortalError(f"Neither Chrome nor Edge could be opened: {str(failure).splitlines()[0]}")
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(ADMIN_SITE, wait_until="domcontentloaded", timeout=60_000)
            except BrowserError:
                raise SkillsPortalError("admin-v2.penpencil.co did not open on this computer. "
                                        "Connect to the office network or VPN and try again.") from None
            deadline, told = time.monotonic() + (15 if headless else wait_seconds), False
            while time.monotonic() < deadline and not should_stop():
                try:
                    token = _read_token(page)
                except BrowserError:   # the page is mid navigation, or the window was closed
                    if page.is_closed():
                        return ""
                    token = ""
                if token and accept(token):
                    return token
                if not told and not headless:
                    told = True
                    on_waiting()
                time.sleep(1.5)
            return ""
        finally:
            try:
                context.close()
            except BrowserError:
                pass


class AdminSignIn:
    """The one admin sign-in this app holds, kept in memory and in that window's profile."""

    def __init__(self, profile, opener=browser_session, client_factory=SkillsClient):
        self.profile, self.opener, self.client_factory = Path(profile), opener, client_factory
        self.lock = threading.Lock()
        self.value, self.state, self.detail, self.thread = "", "signed_out", "", None

    def _accepts(self, token):
        try:
            self.client_factory(token).courses(limit=1)
            return True
        except SkillsSignInNeeded:
            return False
        except SkillsPortalError:   # busy or failing: says nothing about the sign-in
            return True

    def status(self):
        with self.lock:
            expires = token_expiry(self.value)
            if self.value and expires and expires <= time.time():
                self.value, self.state, self.detail = "", "signed_out", "The admin sign-in expired. Sign in again."
            return {"state": self.state, "detail": self.detail, "expires_at": expires if self.value else 0}

    def token(self):
        if self.status()["state"] != "signed_in":
            raise SkillsSignInNeeded(self.detail or "Sign in to the admin portal first.")
        with self.lock:
            return self.value

    def client(self):
        return self.client_factory(self.token())

    def ended(self, token):
        """The portal refused this token: only that sign-in is forgotten, not a newer one."""
        with self.lock:
            if self.value == token:
                self.value, self.state, self.detail = "", "signed_out", "The admin portal ended this sign-in. Sign in again."

    def _run(self, headless):
        def waiting():
            with self.lock:
                self.state, self.detail = "waiting", "Sign in in the window that opened. The tool continues by itself."
        try:
            token = self.opener(self.profile, headless=headless, accept=self._accepts, on_waiting=waiting)
        except Exception as exc:  # noqa: BLE001 - shown on the page; the app goes on
            with self.lock:
                # A hidden look that fails only means no saved sign-in could be read.
                self.state, self.detail = "signed_out" if headless else "failed", " ".join(str(exc).split())[:300]
            return
        with self.lock:
            if token:
                self.value, self.state, self.detail = token, "signed_in", ""
            else:
                self.state = "signed_out"
                self.detail = "" if headless else "The sign-in window closed before an admin signed in."

    def start(self, headless=False):
        """Open the window, or take a hidden look for a saved sign-in. One at a time."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            if not headless:
                self.state, self.detail = "opening", "Opening the admin portal window."
            self.thread = threading.Thread(target=self._run, args=(headless,), name="skills-admin-sign-in", daemon=True)
            self.thread.start()
        return True
