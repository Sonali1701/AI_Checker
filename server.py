"""FastAPI backend used by both the desktop EXE and hosted web build.

Every main action requires a signed-in PW Google token and fails closed against
the PW app allowlist. All provider work is delegated to pw_access helpers.
"""
from __future__ import annotations

import pdf_thread_guard

pdf_thread_guard.install()   # before anything below imports fitz: see that module

import ctypes
import base64
import copy
import dataclasses
import gc
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

import mathpix
import categories
import grading_engine
import collections
import pw_access
import requests
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from costs import DEFAULT_USD_TO_INR
from automatic_workflow import (TERMINAL_STATES, AutomaticWorkflow, WorkflowState,
                                safe_stage_reporter, stage_label)
from exam_package import ChoiceGroup, build_exam_package, normalize_qid
import bulk
import marking_settings
import result_export
import vidyapeeth_fields
from pathlib import Path
from marks_attestation import (
    ATTESTATION_VERSION,
    MarksAttestation,
    MarksAttestationError,
    MarksAttestationStore,
    choice_total_gap,
    effective_marks_total,
    marks_authority_scheme,
    marks_rows_agree,
)
from grader import (
    MarksScheme,
    MarksExtractionError,
    OutputLimitExceeded,
    detect_exam_type,
    marks_scheme_from_items,
    marks_scheme_from_pdf,
    paper_total_from_text,
    pdf_or_image_to_pngs,
    paper_has_choice,
    section_header_gap,
    printed_tag_sum,
    pdf_text_layer,
    scheme_contradicts_paper,
)
from pipeline import (
    CLAUDE,
    ProviderConfig,
    _BUDGET_GRADING_MODEL,
    _LARGE_PAPER_GRADING_MODEL,
    _cache_ttl_for_batch,
    cfg_from_request,
    detect_marks_scheme,
    scanned_document_text,
    generate_rubric,
    grade_sheet,
)
from job_store import JobRepository
from pdf_renderer import _shown_total
from strict_grading import StrictGradeError
from pdf_page_ranges import PdfPageRangeError, select_pdf_pages

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
STATIC_DIR = os.path.join(HERE, "static")
BUILD_ID = "2026.11.25.175-review-per-test"
# PWSkillsChecker.exe (run_skills.py) is this same server showing only the PW Skills page,
# for the PW Skills team (14 Nov 2026). Its own build id keeps either launcher from joining
# the other's server, so the two can run side by side on one computer.
APP_MODE = "skills" if os.environ.get("AICHECKER_APP_MODE", "").strip().lower() == "skills" else "full"
if APP_MODE == "skills":
    BUILD_ID = f"{BUILD_ID}-skills"
# Uploads that are not a genuine answer sheet for this test. They are REJECTED with
# the reason before grading; they are neither released nor queued for faculty.
# Raised while marking a copy, but about the paper, key or marks graph the whole
# batch shares, so never one copy's faculty review: the copy is failed (tried again
# on the next run) and a folder run stops. MEASURED 2026-10-06: a worker-process bug
# raised INVALID_EXAM_PACKAGE on 466 copies, each filed as a finished review.
_BATCH_FAULT_CODES = frozenset({
    "MISSING_EXAM_PACKAGE", "INVALID_EXAM_PACKAGE", "MISSING_MARKS_SCHEME",
})
_REJECTED_UPLOAD_CODES = frozenset({
    "NOT_AN_ANSWER_SHEET",       # typed/printed pages, notes, an unrelated or blank upload
    "WRONG_QUESTION_PAPER",      # a real answer sheet, written for another test
    "DUPLICATE_UPLOAD",          # the same file already graded in this batch
    # A genuine copy with a line aimed at the grader is still a student's paper: it is
    # graded and flagged (pipeline.grade_prepared, 29 Oct 2026), never rejected.
})
# The department cap (costs.budget_for: school Rs.12, CA/commerce Rs.18) is a
# TARGET, not a gate. It was enforced for one afternoon on 2026-09-17 and the
# first live Accountancy copy was held: its re-grade of the choice rows quoted
# Rs.32 against Rs.0 left, and the operator's rule is that a checked paper
# matters more than its unit cost ("I won't be able to get the checked papers").
# The cheap second examiners (pipeline.verifier_ladder_for) keep the typical
# copy near the target; nothing is refused for money.
ENFORCE_GRADING_BUDGET = False
API_PROTOCOL_VERSION = 6

app = FastAPI(title="AI Copy Checker", version="3.2")
_CORS_ORIGINS = [origin.strip() for origin in
                 (os.environ.get("AICHECKER_ALLOWED_ORIGINS") or "").split(",")
                 if origin.strip()]
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def _skills_tool_pages(request, call_next):
    """The PW Skills tool shows one page: the checker home and the copy workflow lead there."""
    if APP_MODE == "skills" and request.url.path in {"/", "/index.html", "/workflow.html"}:
        return RedirectResponse("/skills.html", status_code=307)
    return await call_next(request)


@app.middleware("http")
async def _no_stale_local_ui(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/index.html", "/app.js", "/styles.css", "/workflow.html", "/workflow.js", "/workflow-auth.js", "/workflow-sheets.js", "/skills.html", "/skills.js"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

_JOB_REPOSITORY = JobRepository()
try:
    JOBS: dict[str, dict] = _JOB_REPOSITORY.load(max_age_seconds=3600)
except Exception:
    # Persistence must never make the evaluator itself unavailable. The log path is
    # initialized below, so retain the traceback on stderr during early startup.
    traceback.print_exc()
    JOBS = {}
_LOCK = threading.Lock()
_PERSIST_LOCK = threading.Lock()
_MARKS_ATTESTATIONS = MarksAttestationStore()


def _persist_job(job_id: str) -> None:
    try:
        # Serialize snapshot order across batch workers; an older sheet transition
        # must never overwrite a newer done-count after waiting on SQLite.
        with _PERSIST_LOCK:
            with _LOCK:
                job = JOBS.get(job_id)
                snapshot = copy.deepcopy(job) if job is not None else None
            if snapshot is not None:
                _JOB_REPOSITORY.save(job_id, snapshot)
    except Exception as exc:
        # A grading result remains valid in memory if the local disk is full or locked;
        # surface the durability loss but do not relabel a clean copy.
        print(f"[jobs] could not persist {job_id}: {exc}", file=sys.stderr, flush=True)

# How many sheets to grade at once after the first. Grading is ~99% waiting on the
# model (66-220s per copy), so this is a latency win, not a CPU one: a 30-copy batch
# that took 35-110 minutes strictly sequentially finishes in roughly a quarter of
# that. Kept modest on purpose — the proxy is shared, and every extra worker is
# another concurrent request against someone else's rate limit.
_BATCH_WORKERS = int(os.environ.get("AICHECKER_BATCH_WORKERS", "4") or 4)
_MAX_DOCUMENT_BYTES = 60 * 1024 * 1024
_MAX_BATCH_BYTES = 300 * 1024 * 1024
_DOCUMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
# Answer sheets a category that reads linked work also takes (School of Innovation:
# notebooks, workbooks, Power BI, Tableau, Word, code, and a .txt/.url of links).
_LINKED_WORK_EXTENSIONS = {".ipynb", ".xlsx", ".xlsm", ".csv", ".pbix", ".pbit", ".twb",
                           ".twbx", ".docx", ".txt", ".url", ".py", ".sql", ".js", ".html",
                           ".css", ".r", ".java"}
_ZIP_EXTENSIONS = {".xlsx", ".xlsm", ".pbix", ".pbit", ".twbx", ".docx"}


def _sheet_extensions(category: str) -> set[str]:
    """The answer-sheet file types a category takes."""
    try:
        linked = bool(category) and categories.get(category).intake.linked_work
    except categories.UnknownCategory:
        linked = False
    return _DOCUMENT_EXTENSIONS | (_LINKED_WORK_EXTENSIONS if linked else set())


def _validate_document(filename: str | None, data: bytes, label: str,
                       max_bytes: int = _MAX_DOCUMENT_BYTES,
                       extensions: set[str] | None = None) -> None:
    name = str(filename or "").strip()
    extension = os.path.splitext(name.lower())[1]
    allowed = extensions or _DOCUMENT_EXTENSIONS
    if extension not in allowed:
        if allowed - _DOCUMENT_EXTENSIONS:
            raise HTTPException(400, f"{label} must be PDF, PNG, JPG, a notebook, Excel, CSV, "
                                     "Power BI, Tableau, Word, a code file, or a text file of links.")
        raise HTTPException(400, f"{label} must be PDF, PNG, JPG, or JPEG.")
    if not data:
        raise HTTPException(400, f"{label} came through empty; re-upload it.")
    if len(data) > max_bytes:
        raise HTTPException(413, f"{label} exceeds the {max_bytes // (1024 * 1024)} MB "
                                 "per-file limit.")
    if extension in _LINKED_WORK_EXTENSIONS:
        head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
        ok = (data[:2] == b"PK" if extension in _ZIP_EXTENSIONS
              else head[:1] == b"{" if extension == ".ipynb"
              else head[:1] == b"<" if extension == ".twb"
              else b"\x00" not in data[:4096])
        if not ok:
            raise HTTPException(
                400, f"{label} is damaged or is not really a {extension[1:].upper()} file. "
                     "Download it again and re-upload it.")
        return
    signatures = {
        # The PDF standard lets the header sit anywhere in the first 1 KB.
        ".pdf": b"%PDF-" in data[:1024],
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
    }
    if not signatures.get(extension, False):
        raise HTTPException(
            400, f"{label} is damaged or is not really a {extension[1:].upper()} file "
                 "(often an incomplete download). Download it again and re-upload it.")


def _selected_document(data: bytes, filename: str | None, page_range: str, label: str) -> bytes:
    # Selection must not let an oversized original bypass the existing upload cap.
    if len(data) > _MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"{label} exceeds the 60 MB per-file limit.")
    try:
        data = select_pdf_pages(data, filename, page_range, label)
    except PdfPageRangeError as exc:
        raise HTTPException(400, str(exc)) from exc
    _validate_document(filename, data, label)
    return data


async def _selected_exam_uploads(question_paper, answer_key, question_page_range="",
                                 answer_page_range="", use_question_paper_as_key=False):
    """Prepare two upload inputs; all checking continues through existing routes."""
    original = await question_paper.read()
    name = question_paper.filename
    paper = await run_in_threadpool(_selected_document, original, name, question_page_range, "Question paper")
    if use_question_paper_as_key:
        if os.path.splitext(str(name or "").lower())[1] != ".pdf":
            raise HTTPException(400, "A combined question paper and answer key must be a PDF.")
        key = await run_in_threadpool(_selected_document, original, name, answer_page_range, "Answer key")
        answer_key = UploadFile(filename=name, file=io.BytesIO(key))
    elif str(answer_page_range or "").strip():
        if answer_key is None:
            raise HTTPException(400, "Upload the answer key before selecting its pages.")
        key = await run_in_threadpool(_selected_document, await answer_key.read(), answer_key.filename,
                                     answer_page_range, "Answer key")
        answer_key = UploadFile(filename=answer_key.filename, file=io.BytesIO(key))
    return paper, name, answer_key


def _oauth_client_id() -> str:
    value = (os.environ.get("PW_GOOGLE_CLIENT_ID") or "").strip()
    if value:
        return value
    for directory in (APP_DIR, HERE):
        path = os.path.join(directory, "pw_oauth_config.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            value = str(data.get("google_client_id") or "").strip()
            if not value:
                # Also accept the raw JSON downloaded from Google Cloud Console
                # ({"web": {"client_id": ...}} or {"installed": {"client_id": ...}}).
                for key in ("web", "installed"):
                    value = str((data.get(key) or {}).get("client_id") or "").strip()
                    if value:
                        break
            if value:
                return value
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return ""


def _google_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(401, "Sign in with your @pw.live Google account.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Sign in with your @pw.live Google account.")
    return token.strip()


def _unverified_google_claims(token: str) -> dict:
    """Decode JWT claims only to provide accurate local auth errors.

    These claims are never trusted for authorization.  The PW proxy still performs
    signature, issuer, audience, domain and allowlist verification.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError, TypeError, UnicodeError):
        return {}


APP_SESSION_ISSUER = "ai-checker-session"
APP_SESSION_MAX_SECONDS = 7 * 24 * 3600


class AppSessions:
    """Sign in once a week, not once an hour.

    Google's ID token lasts an hour, but the PW proxy exchanges it for a 7-day pass
    (pw_access._auth_token). After the sign-in checks pass, the app issues its own
    signed session token that lives as long as that pass. The page, and every run
    it starts, present the session token; the server keeps the Google token in
    memory and hands it to the proxy kit, which uses its cached pass. The proxy
    still authorizes every paid call, so a revoked account fails there. Sessions
    live in memory: restarting the app means signing in again.
    """
    def __init__(self, clock=time.time):
        self.clock, self.secret = clock, secrets.token_bytes(32)
        self.lock, self.entries = threading.Lock(), {}

    @staticmethod
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _sign(self, signing_input: str) -> str:
        return self._b64(hmac.new(self.secret, signing_input.encode("ascii"), hashlib.sha256).digest())

    def issue(self, google_token: str, claims: dict, expires_at: float) -> dict:
        now = self.clock()
        expires_at = min(float(expires_at or 0) or now + APP_SESSION_MAX_SECONDS, now + APP_SESSION_MAX_SECONDS)
        sid = secrets.token_hex(16)
        payload = {"iss": APP_SESSION_ISSUER, "sid": sid, "sub": str(claims.get("sub") or ""),
                   "email": str(claims.get("email") or ""), "iat": int(now), "exp": int(expires_at)}
        header = self._b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("ascii"))
        body = self._b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        token = f"{header}.{body}.{self._sign(f'{header}.{body}')}"
        with self.lock:
            for stale in [key for key, value in self.entries.items() if value["expires_at"] <= now]:
                del self.entries[stale]
            self.entries[sid] = {"google_token": google_token, "expires_at": expires_at, "email": payload["email"]}
        return {"session_token": token, "expires_at": expires_at, "email": payload["email"]}

    def resolve(self, token: str):
        """The entry behind a live app session token; None when the token is not one (a Google token)."""
        parts = token.split(".")
        claims = _unverified_google_claims(token) if len(parts) == 3 else {}
        if not isinstance(claims, dict) or claims.get("iss") != APP_SESSION_ISSUER:
            return None
        if not hmac.compare_digest(self._sign(f"{parts[0]}.{parts[1]}"), parts[2]):
            # A different signing secret: a forged token, or this app was restarted.
            raise HTTPException(401, "Your sign-in is not valid any more (this app may have been restarted). Sign in again, then resume your saved run.")
        sid = str(claims.get("sid") or "")
        with self.lock:
            entry = self.entries.get(sid)
            if entry is None or entry["expires_at"] <= self.clock():
                self.entries.pop(sid, None)
                raise HTTPException(401, "Your sign-in expired or this app was restarted. Sign in again, then resume your saved run.")
        return entry

    def revoke(self, token: str) -> bool:
        claims = _unverified_google_claims(token)
        sid = str(claims.get("sid") or "") if isinstance(claims, dict) else ""
        with self.lock:
            return self.entries.pop(sid, None) is not None


_APP_SESSIONS = AppSessions()


def _require_allowed(authorization: str | None) -> str:
    token = _google_token(authorization)
    session = _APP_SESSIONS.resolve(token)
    if session is not None:
        return session["google_token"]   # the proxy kit uses its cached 7-day pass for it
    claims = _unverified_google_claims(token)
    now = time.time()
    try:
        expires_at = float(claims.get("exp") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at and expires_at <= now:
        raise HTTPException(401, "Your Google session expired. Please sign in again.")

    try:
        not_before = float(claims.get("nbf") or 0)
    except (TypeError, ValueError):
        not_before = 0
    if not_before and not_before > now + 60:
        raise HTTPException(
            401,
            "The Google credential is not valid yet. Check the computer date and time, "
            "then sign in again.",
        )

    status, reason = pw_access.check_allowed_diagnostic(token)
    if status != "allowed":
        # Never log the credential or proxy response body.  This safe category is
        # enough to diagnose the boundary without leaking identity tokens.
        print(f"[auth] allowlist rejected: {reason}", flush=True)
        if status == "denied":
            raise HTTPException(
                403, "Your @pw.live account is not authorized for AI Copy Checker."
            )
        if reason == "credential_rejected":
            raise HTTPException(
                401,
                "PW authorization rejected this Google session. Sign out of Google, "
                "then sign in again with your @pw.live account.",
            )
        if reason in {"proxy_unavailable", "proxy_unreachable"}:
            raise HTTPException(
                503,
                "The PW authorization service is temporarily unavailable. No action "
                "was performed; please retry shortly.",
            )
        raise HTTPException(
            503,
            f"PW authorization could not be verified ({reason}). No action was performed.",
        )
    return token


def _log_error(what: str, exc: BaseException) -> None:
    """Write the real cause + traceback to stdout (tee'd into aichecker.log).

    Without this a provider/parse failure only ever surfaced as a generic
    "Could not detect marks" toast, leaving nothing to diagnose from.
    """
    print(f"[ERROR] {what}: {type(exc).__name__}: {exc}", flush=True)
    print(traceback.format_exc(), flush=True)


def _trim_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _cost_dict(cost) -> dict:
    result = dataclasses.asdict(cost)
    result["billed_input_tokens"] = cost.billed_input_tokens
    return result


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": pw_access.APP_NAME,
        "build_id": BUILD_ID,
        "protocol_version": API_PROTOCOL_VERSION,
        "pdf_threads_guarded": pdf_thread_guard.installed(),
    }


@app.get("/api/config")
def config():
    return {
        "app_name": pw_access.APP_NAME,
        "app_mode": APP_MODE,
        "build_id": BUILD_ID,
        "protocol_version": API_PROTOCOL_VERSION,
        "google_client_id": _oauth_client_id(),
        "default_usd_to_inr": DEFAULT_USD_TO_INR,
        "budget_cap_enabled": ENFORCE_GRADING_BUDGET,
        "categories": categories.ui_choices(),
        "marking_settings": marking_settings.ui_choices(),
        # The Vidyapeeth batch header dropdowns (vidyapeeth_fields.py).
        "vidyapeeth_fields": vidyapeeth_fields.load(),
    }


@app.get("/api/auth/status")
def auth_status(authorization: str | None = Header(default=None)):
    """Sign-in check. A Google token that passes is answered with the app's own
    session token, valid as long as the proxy's 7-day pass; the page uses that from
    then on. A session token presented here is confirmed as is."""
    result = {"allowed": True, "app": pw_access.APP_NAME}
    token = _google_token(authorization)
    session = _APP_SESSIONS.resolve(token)
    if session is not None:
        return {**result, "session_token": token, "expires_at": session["expires_at"], "email": session["email"]}
    google_token = _require_allowed(authorization)
    expires_at = pw_access.session_pass_expiry(google_token)   # mint the pass now, while the Google token is fresh
    if expires_at <= time.time():
        return result   # no proxy pass: the Google token itself remains the session, for its hour
    return {**result, **_APP_SESSIONS.issue(google_token, _unverified_google_claims(google_token), expires_at)}


@app.post("/api/auth/signout")
def auth_signout(authorization: str | None = Header(default=None)):
    return {"signed_out": _APP_SESSIONS.revoke(_google_token(authorization))}


# The question-wise marks are read ONCE, by Claude (user decision 2026-09-18). There
# used to be a second, independent reading (Gemini, then a second Claude model) that had
# to agree; the proxy rate-limited Gemini for hours and blocked all grading, and the
# user chose one Claude reading over two. The reading still has to pass every
# deterministic paper check (official total, printed tags, choice rules) before it is
# accepted.
def _category_or_400(category: str, exam_type: str) -> tuple[str, str]:
    """The operator's exam category id ("" = auto detect) and the exam type it implies.

    An unknown category is refused rather than graded under a guess, and a design
    paper has its own route."""
    if not isinstance(category, str) or not category.strip():
        return "", exam_type
    try:
        found = categories.get(category)
    except categories.UnknownCategory as exc:
        raise HTTPException(400, str(exc)) from exc
    if found.pipeline != "written":
        raise HTTPException(400, "Design papers are graded from the Design and drawing category.")
    return found.id, found.legacy_exam_type


def _marks_extraction_context(
    primary: ProviderConfig,
    student_class: str,
    subject: str,
) -> str:
    # Skills assignments have no class field. Detection receives a blank value,
    # while evaluation supplies the category's level label through _effective_level.
    # Bind both to that same label, retaining explicit classes and every other
    # context field so a real exam change still invalidates the proof.
    context_level = " ".join((student_class or "").split())
    if not context_level and primary.category:
        context_level = categories.get(primary.category).level_label
    return json.dumps({
        "version": ATTESTATION_VERSION,
        "exam_type": (primary.exam_type or "").strip().lower(),
        "category": primary.category or "",
        "student_class": context_level.casefold(),
        "subject": " ".join((subject or "").split()).casefold(),
        "extractors": [f"{CLAUDE}:{_MARKS_READING_MODEL}"],
    }, sort_keys=True, separators=(",", ":"))


# The question/maxima graph is read ONCE per paper, so it gets a model that reads
# choice structure reliably whatever the paper's size. Size routing sent every
# paper over 60 marks to Haiku, which on the CA 100-mark papers (Taxation MTS-05,
# CMA MT-05, FM-SM MT-05) dropped "attempt any 2 of the remaining 3" on every one
# of six tries, so none of those copies could be graded at all.
_MARKS_READING_MODEL = _BUDGET_GRADING_MODEL


def _marks_reader_config(cfg: ProviderConfig) -> ProviderConfig:
    return dataclasses.replace(cfg, provider=CLAUDE, model=_MARKS_READING_MODEL)


def _exam_choice_groups(scheme: MarksScheme | None) -> list[ChoiceGroup]:
    """Compile attested extraction groups into server-owned scoring rules.

    Provider labels and excess-attempt policy never become authority. The attestation
    canonicalizes membership/count/contribution; the app applies its fixed examiner
    policy of counting the first physically attempted alternatives.
    """
    if scheme is None:
        return []
    authority = marks_authority_scheme(scheme)
    return [ChoiceGroup.model_validate({
        **group.model_dump(mode="json"),
        "selection_policy": "first_attempted",
    }) for group in authority.choice_groups]


def _rows_for_log(scheme: MarksScheme | None) -> str:
    """Compact qid=max listing for the log; never truncated silently."""
    items = list(getattr(scheme, "items", None) or [])
    if not items:
        return "(no rows)"
    rows = " ".join(f"{item.qid}={float(item.max):g}" for item in items)
    return f"{len(items)} rows, sum {sum(float(i.max) for i in items):g}: {rows}"


def _log_marks_verification(
    method: str,
    detail: str,
    primary: MarksScheme | None,
    verifier: MarksScheme | None = None,
) -> None:
    """Say why a paper did not earn a proof, in the log, at the moment it happens.

    A blocked paper used to look identical whether a provider crashed, the two
    providers disagreed, or the rows missed the official total. That ambiguity cost
    real debugging time, so every non-verified outcome now names itself and prints
    the rows each provider actually returned.
    """
    if method in ("ai_verified", "paper_verified"):
        return
    print(f"[marks] not verified ({method}): {detail}", file=sys.stderr, flush=True)
    print(f"[marks]   primary : {_rows_for_log(primary)}", file=sys.stderr, flush=True)
    # Leaf tables alone cannot explain disagreement about equally weighted
    # alternatives. Record the structural rules too, without question prose.
    for label, reading in (("primary", primary), ("verifier", verifier)):
        for group in getattr(reading, "choice_groups", None) or []:
            alternatives = [list(option.question_ids) for option in group.options]
            print(f"[marks]   {label} choice {group.group_id}: "
                  f"select {group.select_count}; counted {group.contribution_marks:g}; "
                  f"options {json.dumps(alternatives)}", file=sys.stderr, flush=True)
    if verifier is not None:
        print(f"[marks]   verifier: {_rows_for_log(verifier)}", file=sys.stderr, flush=True)
        primary_rows = {
            normalize_qid(str(i.qid)): round(float(i.max), 4)
            for i in (getattr(primary, "items", None) or [])
        }
        verifier_rows = {
            normalize_qid(str(i.qid)): round(float(i.max), 4)
            for i in (getattr(verifier, "items", None) or [])
        }
        only_primary = sorted(set(primary_rows) - set(verifier_rows))
        only_verifier = sorted(set(verifier_rows) - set(primary_rows))
        differing = sorted(
            qid for qid in set(primary_rows) & set(verifier_rows)
            if primary_rows[qid] != verifier_rows[qid]
        )
        if only_primary:
            print(f"[marks]   only primary : {', '.join(only_primary)}",
                  file=sys.stderr, flush=True)
        if only_verifier:
            print(f"[marks]   only verifier: {', '.join(only_verifier)}",
                  file=sys.stderr, flush=True)
        for qid in differing:
            print(f"[marks]   {qid}: primary {primary_rows[qid]:g} "
                  f"vs verifier {verifier_rows[qid]:g}", file=sys.stderr, flush=True)


def _marks_response(
    scheme: MarksScheme | None,
    method: str,
    printed_total: float | None,
    *,
    attestation: MarksAttestation | None = None,
    verification_detail: str = "",
    choice_detected: bool = False,
    verifier_scheme: MarksScheme | None = None,
    failure_code: str = "",
    retry_after_seconds: int = 0,
    extraction_cached: bool = False,
) -> dict:
    # Every path out of detect-marks funnels through here, so this is the one place
    # that sees each non-verified outcome and can name it.
    _log_marks_verification(method, verification_detail, scheme, verifier_scheme)
    items = [] if scheme is None else [
        {"qid": item.qid, "max": float(item.max), "part": item.description}
        for item in scheme.items
    ]
    verified_groups = (
        _exam_choice_groups(attestation.scheme) if attestation is not None else []
    )
    effective_total = None
    if scheme is not None:
        try:
            effective_total = effective_marks_total(scheme)
        except MarksAttestationError:
            effective_total = None
    return {
        "items": items,
        "total": round(sum(float(item["max"]) for item in items), 2),
        "method": method,
        "paper_total": printed_total,
        "marks_attestation": attestation.token if attestation is not None else "",
        "marks_attestation_expires_at": (
            attestation.expires_at if attestation is not None else None
        ),
        "verification_detail": verification_detail,
        "failure_code": failure_code,
        "retry_after_seconds": retry_after_seconds,
        "extraction_cached": extraction_cached,
        "choice_detected": bool(
            choice_detected or getattr(scheme, "choice_groups", None)),
        "choice_verified": bool(attestation is not None and verified_groups),
        "choice_groups": [group.model_dump(mode="json") for group in verified_groups],
        # The choice rules as read, verified or not: the workflow's marks editor starts from them.
        "read_choice_groups": [group.model_dump(mode="json")
                               for group in (getattr(scheme, "choice_groups", None) or [])],
        "effective_total": effective_total,
    }


def _marks_extraction_failure(exc, provider):
    """Do not turn a failed structural reader into an unrelated regex table."""
    name = "Claude"
    status = getattr(exc, "status_code", None)
    if isinstance(exc, OutputLimitExceeded):
        return ("extractor_incomplete_output", 0,
                f"{name} stopped before completing the marks reading. No partial marks "
                "table was accepted. Retry marks verification.")
    if isinstance(exc, MarksExtractionError):
        return ("extractor_source_mismatch", 0, f"{name}: {exc} Retry marks verification.")
    if status in {429, 529}:
        delay = max(60, int(getattr(exc, "retry_after", None) or 0))
        return ("extractor_rate_limited", delay,
                f"{name} is temporarily busy. Wait a minute, then retry marks verification.")
    if status in {401, 403}:
        return ("extractor_access_denied", 0,
                f"{name} refused the marks request. Sign in again and retry marks verification.")
    reason = " ".join(str(exc).split())[:160]
    if isinstance(exc, (pw_access.PWAccessError, requests.RequestException)) and (status is None or status >= 500):
        return ("extractor_unavailable", 0,
                f"{name} could not finish the marks reading: the AI service did not answer ({reason}). "
                "No partial marks table was accepted. Retry marks verification.")
    return ("extractor_failed", 0,
            f"{name} could not finish the marks reading ({reason}). No partial marks table was accepted. "
            "Retry marks verification.")


@app.post("/api/detect-marks")
async def detect_marks(
    question_paper: UploadFile = File(...),
    question_page_range: Annotated[str, Form()] = "",
    provider: str = Form("claude"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    exam_type: str = Form(""),
    category: str = Form(""),
    total_marks: str = Form(""),
    # "auto" = free text-layer reading only, never an AI call (used on upload);
    # "ai" = full extraction with the AI fallback (the explicit button).
    mode: str = Form("ai"),
    authorization: str | None = Header(default=None),
):
    # Every blocking call in these async endpoints (the allowlist round trip, the
    # Claude marks reading) runs in the thread pool. On the event loop, one teacher's
    # 30-60 s marks reading froze the whole server: other uploads, job polls and
    # /api/health all queued behind it (16 papers at once took minutes to start).
    google_token = await run_in_threadpool(_require_allowed, authorization)
    task_id = pw_access.new_task_id("detect-marks")
    category, exam_type = _category_or_400(category, exam_type)
    try:
        paper_total = float(total_marks) if total_marks.strip() else None
    except ValueError as exc:
        raise HTTPException(400, "Total marks must be a number.") from exc
    data = await run_in_threadpool(_selected_document, await question_paper.read(), question_paper.filename,
                                   question_page_range, "Question paper")
    paper_text = pdf_text_layer(data, question_paper.filename)
    printed_total = paper_total_from_text(paper_text)
    official_total = paper_total if paper_total is not None else printed_total
    explicit_ai = (mode or "").strip().lower() != "auto"
    ai_first = (mode or "").strip().lower() == "ai_first"   # the workflow: _workflow_read_marks
    if (paper_total is not None and printed_total is not None
            and abs(float(paper_total) - float(printed_total)) > 0.01):
        raise HTTPException(
            400,
            f"Entered total {paper_total:g} conflicts with the paper header "
            f"({printed_total:g}).",
        )
    cfg = None if not explicit_ai else _marks_reader_config(cfg_from_request(
        # Production verification uses one fixed, costed model. Client-supplied model
        # names cannot upgrade this setup action to an arbitrary expensive model.
        CLAUDE,
        "",
        subject,
        google_token=google_token,
        task_id=task_id,
        student_class=student_class,
        exam_type=exam_type,
        total_marks=official_total,
        category=category,
    ))
    # This is a routing/evidence hint only. Exam type and ordinary prose must never
    # become a permanent choice lock; authority comes from the matched full graph.
    choice_detected = paper_has_choice(paper_text, "")
    marks_context = (
        _marks_extraction_context(cfg, student_class, subject)
        if cfg is not None else ""
    )

    # Re-clicking the button for the same bytes and total must not buy two more calls.
    if explicit_ai and official_total is not None:
        try:
            cached = _MARKS_ATTESTATIONS.find(
                data, official_total, context=marks_context)
        except MarksAttestationError:
            cached = None
        if cached is not None:
            return _marks_response(
                cached.scheme,
                "ai_verified",
                printed_total,
                attestation=cached,
                verification_detail="Cached Claude reading for this exact paper.",
                choice_detected=choice_detected,
            )
    try:
        scheme, method = await run_in_threadpool(
            detect_marks_scheme,
            data,
            question_paper.filename,
            cfg,
            student_class or None,
            subject or None,
            total_marks=official_total,
            require_complete=explicit_ai,
            exam_type=exam_type,
            **({"ai_first": True} if ai_first else {}),
        )
    except Exception as exc:
        _log_error("detect-marks AI extraction failed", exc)
        code, delay, detail = _marks_extraction_failure(exc, cfg.provider if cfg else provider)
        return _marks_response(
            None, "ai_extraction_failed", printed_total, verification_detail=detail,
            choice_detected=choice_detected, failure_code=code, retry_after_seconds=delay)
    decided = method == "ai_decided"   # the reader decided what the paper leaves open
    if decided:
        method = "ai"
    # The paper usually prints its own maximum — hand it back so the UI can fill the
    # "Paper total marks" box instead of relying on the teacher to type it.
    if scheme is None:
        # Setup may continue so an authoritative table can be submitted, but strict
        # evaluation will remain UNGRADABLE until the complete question/maxima graph
        # is supplied. It never asks the grader model to invent the denominator.
        return _marks_response(
            None, "none", printed_total, choice_detected=choice_detected)

    # Deterministic graphs need no paid consensus.  Failed AI paper checks can be
    # shown as a preview, but never gain release authority.
    if method in ("regex", "sections") or not explicit_ai:
        # A section graph that adds up to the official total with every question
        # counted leaves no choice between questions: any OR is inside a question.
        return _marks_response(
            scheme, method, printed_total,
            choice_detected=choice_detected and method != "sections")
    if method != "ai":
        if method == "ai_mismatch":
            tagged_total, _ = printed_tag_sum(paper_text)
            detail = (
                f"Extracted question rows total {sum(float(i.max) for i in scheme.items):g}; "
                f"the paper's readable mark allocations total {tagged_total:g}. "
                "The extraction did not reconcile these allocations. "
                + ("Internal-choice alternatives must each be read once before applying "
                   "the selection rules. " if choice_detected else "")
                + "Retry marks verification."
            )
        elif method == "ai_tag_conflict":
            detail = (scheme_contradicts_paper(scheme, paper_text, official_total)
                      or "The extracted question maxima contradict the printed allocations.")
        else:
            detail = "The first extraction did not pass its paper checks. Retry marks verification."
        return _marks_response(
            scheme,
            method,
            printed_total,
            verification_detail=detail,
            choice_detected=choice_detected,
        )
    # The reader receives every original page image, including scanned and mixed
    # PDFs. A text layer is optional; the graph must bind to the official total.
    if official_total is None:
        return _marks_response(
            scheme,
            "ai_unverified_total",
            printed_total,
            verification_detail="No entered or printed official total was available to bind the graph.",
            choice_detected=choice_detected,
        )
    if (choice_detected and not list(getattr(scheme, "choice_groups", None) or [])
            and _generic_choice_hint_disproved(
                data, question_paper.filename, paper_text, scheme, official_total)):
        choice_detected = False
    if choice_detected and not list(getattr(scheme, "choice_groups", None) or []):
        return _marks_response(
            scheme,
            "ai_choice_unverified",
            printed_total,
            verification_detail=(
                "The paper prints an internal-choice instruction, but Claude "
                "did not return its complete alternatives."
            ),
            choice_detected=True,
        )
    try:
        scheme_total = effective_marks_total(scheme)
    except MarksAttestationError as exc:
        return _marks_response(
            scheme,
            "ai_choice_unverified",
            printed_total,
            verification_detail=str(exc),
            choice_detected=choice_detected,
        )
    if abs(scheme_total - float(official_total)) > 0.01:
        return _marks_response(
            scheme,
            "ai_total_unverified",
            printed_total,
            verification_detail=(
                section_header_gap(paper_text, scheme, official_total)
                or ((choice_total_gap(scheme, official_total)
                     or "Fixed questions plus the extracted choice rules do not exactly "
                        "equal the official paper total.") + " Retry marks verification.")
            ),
            choice_detected=choice_detected,
        )

    # One Claude reading that passed every paper check above is the authority.
    try:
        attestation = _MARKS_ATTESTATIONS.create(
            data,
            official_total,
            scheme,
            extractors=(f"{cfg.provider}:{cfg.model}",),
            context=marks_context,
        )
    except MarksAttestationError as exc:
        return _marks_response(
            scheme, "ai_unverified", printed_total,
            verification_detail=str(exc), choice_detected=choice_detected)
    return {**_marks_response(
        attestation.scheme,
        "ai_verified",
        printed_total,
        attestation=attestation,
        verification_detail=(
            "Claude read the complete question graph"
            + (" and internal-choice rules" if getattr(attestation.scheme, "choice_groups", None)
               else "")
            + f"; it matches the official total of {float(official_total):g}."
            + (" The paper does not print every part's marks, so Claude decided those." if decided else "")
        ),
        choice_detected=choice_detected,
    ), "marks_decided": decided}


@app.post("/api/generate-rubric")
async def gen_rubric(
    question_paper: UploadFile = File(...),
    question_page_range: Annotated[str, Form()] = "",
    provider: str = Form("claude"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    exam_type: str = Form(""),
    category: str = Form(""),
    authorization: str | None = Header(default=None),
):
    google_token = await run_in_threadpool(_require_allowed, authorization)
    task_id = pw_access.new_task_id("generate-rubric")
    category, exam_type = _category_or_400(category, exam_type)
    data = await run_in_threadpool(_selected_document, await question_paper.read(), question_paper.filename,
                                   question_page_range, "Question paper")
    cfg = cfg_from_request(
        provider,
        model,
        subject,
        google_token=google_token,
        task_id=task_id,
        student_class=student_class,
        exam_type=exam_type,
        category=category,
    )
    try:
        pages = await run_in_threadpool(pdf_or_image_to_pngs, data, question_paper.filename)
        rubric = await run_in_threadpool(
            generate_rubric,
            pages,
            cfg,
            student_class or None,
            subject or None,
            question_paper.filename,
        )
    except Exception as exc:
        _log_error("generate-rubric failed", exc)
        raise HTTPException(500, f"Rubric generation failed: {exc}") from exc
    if not rubric.strip():
        raise HTTPException(502, "The model returned an empty rubric; try again.")
    return {"rubric": rubric}


@app.post("/api/evaluate")
async def evaluate(
    background_tasks: BackgroundTasks,
    answer_sheets: list[UploadFile] = File(...),
    question_paper: UploadFile | None = File(None),
    answer_key: UploadFile | None = File(None),
    question_page_range: Annotated[str, Form()] = "",
    answer_page_range: Annotated[str, Form()] = "",
    use_question_paper_as_key: Annotated[bool, Form()] = False,
    step_rubric: str = Form(""),
    provider: str = Form("claude"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    exam_type: str = Form(""),
    total_marks: str = Form(""),
    exam_level: str = Form(""),
    paper_code: str = Form(""),
    exam_attempt: str = Form(""),
    applicable_law_date: str = Form(""),
    key_source: str = Form("rubric"),
    marks_items: str = Form("[]"),
    marks_source: str = Form(""),
    marks_attestation: str = Form(""),
    usd_to_inr: str = Form(""),
    category: str = Form(""),
    answer_language: str = Form(""),
    leniency: str = Form("standard"),
    centre_name: str = Form(""),
    course_name: str = Form(""),
    test_date: str = Form(""),
    board: str = Form(""),
    vertical: str = Form(""),
    test_name: str = Form(""),
    paper_mode: str = Form(""),
    authorization: str | None = Header(default=None),
):
    google_token = await run_in_threadpool(_require_allowed, authorization)
    task_id = pw_access.new_task_id("evaluate")
    category, exam_type = _category_or_400(category, exam_type)
    batch_header = _batch_header_or_400(
        category, centre_name=centre_name, course_name=course_name, board=board, vertical=vertical,
        test_name=test_name, test_date=test_date, paper_mode=paper_mode,
        student_class=student_class, subject=subject)
    if category == "vidyapeeth" and not answer_language:
        answer_language = vidyapeeth_fields.answer_language_for(paper_mode)
    # Strict automatic release is evidence-bound. A booklet without its exact paper
    # and official key is not merely lower confidence; it is a different exam package.
    if question_paper is None:
        raise HTTPException(400, "Upload the official question paper.")
    qp_bytes, qp_name, answer_key = await _selected_exam_uploads(
        question_paper, answer_key, question_page_range, answer_page_range,
        use_question_paper_as_key)
    if use_question_paper_as_key:
        key_source = "upload"
    sheets = [(file.filename, await file.read()) for file in answer_sheets]
    if not sheets:
        raise HTTPException(400, "Upload at least one answer sheet.")
    # One damaged file must not stop the batch (2026-09-29: an incomplete download
    # among 33 English copies refused all 33). It is set aside with its reason and
    # shown in the results; the rest are graded.
    unreadable: list[tuple[str, str]] = []
    readable = []
    first_of: dict[str, str] = {}
    # A Vidyapeeth centre scans its whole stack of booklets into one PDF (218 pages,
    # 229 MB on 12 Oct 2026), refused here at the 60 MB limit before it could be
    # split. The stack may be as large as a batch; each booklet split from it must
    # still be within the per-file limit (below).
    sheet_limit = _MAX_BATCH_BYTES if category == "vidyapeeth" else _MAX_DOCUMENT_BYTES
    for name, data in sheets:
        try:
            _validate_document(name, data, f"“{name}”", max_bytes=sheet_limit,
                               extensions=_sheet_extensions(category))
        except HTTPException as exc:
            if exc.status_code == 413:
                raise
            unreadable.append((name, str(exc.detail)))
            continue
        # The same file twice is graded once (2026-09-30: one student's Physics sheet
        # sat under two teachers' lists and was marked and released twice).
        digest = hashlib.sha256(data).hexdigest()
        if digest in first_of:
            unreadable.append((name, f"Same file as “{first_of[digest]}”, graded once."))
            continue
        first_of[digest] = name
        readable.append((name, data))
    if not readable:
        raise HTTPException(400, " ".join(reason for _, reason in unreadable[:3]))
    sheets = readable
    if category == "vidyapeeth":
        # One scanned PDF can hold a whole stack of booklets, each opening with the
        # printed cover (vidyapeeth_intake.py): one copy per booklet.
        from vidyapeeth_intake import expand_upload
        booklets: list = []
        for name, data in sheets:
            found, aside = await run_in_threadpool(expand_upload, name, data)
            for part, part_data in found:
                if len(part_data) > _MAX_DOCUMENT_BYTES:
                    unreadable.append((part, "This booklet is over the 60 MB per-file limit "
                                             "after splitting; no cover pages were found to "
                                             "split it at. Check that it is a Vidyapeeth "
                                             "stack, or scan it at a lower resolution."))
                else:
                    booklets.append((part, part_data))
            unreadable += aside
        if not booklets:
            raise HTTPException(400, " ".join(reason for _, reason in unreadable[:3]))
        sheets = booklets
    if sum(len(data) for _, data in sheets) > _MAX_BATCH_BYTES:
        raise HTTPException(413, "Answer-sheet batch exceeds the 300 MB upload limit."
                            + (" Put the stacks in a folder and use folder mode instead."
                               if category == "vidyapeeth" else ""))
    return await _queue_evaluation(
        background_tasks, google_token=google_token, task_id=task_id,
        qp_bytes=qp_bytes, qp_name=qp_name, sheets=sheets, answer_key=answer_key,
        step_rubric=step_rubric, provider=provider, model=model,
        student_class=student_class, subject=subject, exam_type=exam_type,
        total_marks=total_marks, exam_level=exam_level, paper_code=paper_code,
        exam_attempt=exam_attempt, applicable_law_date=applicable_law_date,
        key_source=key_source, marks_items=marks_items, marks_source=marks_source,
        marks_attestation=marks_attestation, usd_to_inr=usd_to_inr,
        unreadable=unreadable, category=category, answer_language=answer_language,
        leniency=leniency, batch_fields=batch_header)


def _batch_header_or_400(category: str, *, centre_name: str, course_name: str, board: str,
                         vertical: str, test_name: str, test_date: str, paper_mode: str,
                         student_class: str, subject: str) -> dict:
    """The batch header for the results sheet. A Vidyapeeth batch must have every
    field (the team's rule, 15 Oct 2026: typed once, used for every student, never
    read from the covers); other categories carry whatever was typed."""
    fields = {"centre": centre_name, "course": course_name, "board": board, "vertical": vertical,
              "test_name": test_name, "test_date": test_date, "paper_mode": paper_mode}
    if category == "vidyapeeth":
        gaps = vidyapeeth_fields.missing({**fields, "student_class": student_class, "subject": subject})
        if gaps:
            raise HTTPException(400, "Vidyapeeth results sheet: fill in " + ", ".join(gaps) + ".")
    return fields


def _effective_level(exam_level: str, student_class: str, category: str) -> str:
    """The class or level the paper is graded for.

    A category whose papers have no class (School of Innovation assignments belong
    to a course) has no class field on the form; its own label stands in."""
    level = (exam_level or student_class or "").strip()
    if level:
        return level
    label = categories.get(category).level_label if category else ""
    if label:
        return label
    raise HTTPException(400, "Class or CA level is required.")


def _generates_key(category: str, key_source: str, has_key: bool) -> bool:
    """Whether the job writes its own model answer key (teaching_key.py): only for a
    category that allows it, and only when no key was uploaded for the paper."""
    return bool(category and categories.get(category).answer_key.may_generate
                and (key_source != "upload" or not has_key))


async def _queue_evaluation(
    background_tasks: BackgroundTasks, *, google_token: str, task_id: str,
    qp_bytes: bytes, qp_name: str, sheets: list, answer_key: UploadFile | None,
    step_rubric: str, provider: str, model: str, student_class: str, subject: str,
    exam_type: str, total_marks: str, exam_level: str, paper_code: str,
    exam_attempt: str, applicable_law_date: str, key_source: str, marks_items: str,
    marks_source: str, marks_attestation: str, usd_to_inr: str,
    bulk: dict | None = None, workers: int | None = None,
    unreadable: list[tuple[str, str]] | None = None,
    category: str = "",
    answer_language: str = "",
    leniency: str = "standard",
    batch_fields: dict | None = None,
    printed_total_override: bool = False,
    cache_ttl: str | None = None,
) -> dict:
    """Validate the exam package and queue the grading job.

    Shared by the upload route (sheets carry their bytes) and folder mode (bulk,
    where each sheet is read from disk only when its turn comes). cache_ttl, when
    given, replaces the TTL the job's own size would choose: the workflow sends one
    copy per job, and the other copies of its test are in other jobs."""
    try:
        # A direct call (tests, old clients) passes the Form() default object.
        answer_language = marking_settings.language(
            answer_language if isinstance(answer_language, str) else "")
        leniency = marking_settings.leniency(leniency if isinstance(leniency, str) else "")
    except marking_settings.SettingError as exc:
        raise HTTPException(400, str(exc)) from exc
    ak_bytes = ak_name = None
    # A category whose papers come with no key (teaching exams) has its model answer
    # key written from the paper at the start of the job (teaching_key.py), and every
    # copy goes to faculty review. An uploaded key still wins.
    generate_key = _generates_key(category, key_source, answer_key is not None)
    if not generate_key:
        if key_source != "upload" or answer_key is None:
            raise HTTPException(
                400,
                "Automatic release requires the uploaded official answer key/marking scheme; "
                "a generated rubric is draft-only.",
            )
        ak_bytes = await answer_key.read()
        ak_name = answer_key.filename
        _validate_document(ak_name, ak_bytes, "Answer key")

    try:
        items = json.loads(marks_items or "[]")
    except json.JSONDecodeError:
        items = []
    # Browser choice JSON is never grading authority. Choice rules are recovered only
    # from the paper-bound server attestation below. Keep the form field solely for
    # compatibility with older clients and deliberately ignore its contents.
    submitted_choice_groups: list[ChoiceGroup] = []
    try:
        rate = float(usd_to_inr) if usd_to_inr.strip() else DEFAULT_USD_TO_INR
    except ValueError:
        rate = DEFAULT_USD_TO_INR
    qp_text = pdf_text_layer(qp_bytes, qp_name)
    printed_paper_total = paper_total_from_text(qp_text)
    try:
        paper_total = float(total_marks) if total_marks.strip() else None
    except ValueError as exc:
        raise HTTPException(400, "Total marks must be a number.") from exc
    if paper_total is None:
        paper_total = printed_paper_total
    if paper_total is None or not math.isfinite(paper_total) or paper_total <= 0:
        raise HTTPException(
            400,
            "Enter the official positive total marks; it could not be read reliably "
            "from the paper.",
        )
    # printed_total_override: a person entered this paper's marks and total because its
    # printed ones are wrong (integrated workflow, build 137); their proof binds that total.
    if (printed_paper_total is not None and not printed_total_override
            and abs(float(printed_paper_total) - float(paper_total)) > 0.25):
        raise HTTPException(
            400,
            f"Entered total {paper_total:g} conflicts with the official paper header "
            f"({printed_paper_total:g}). Automatic grading will not override the paper.",
        )
    if not (subject or "").strip():
        raise HTTPException(400, "Subject/paper name is required for automatic grading.")
    effective_level = _effective_level(exam_level, student_class, category)
    requested_exam_type = (exam_type or "").strip().lower()
    resolved_exam_type = (categories.get(category).legacy_exam_type if category
                          else requested_exam_type if requested_exam_type in
                          {"school", "professional"}
                          else detect_exam_type(effective_level, subject))
    cfg = cfg_from_request(
        provider,
        model,
        subject,
        google_token=google_token,
        task_id=task_id,
        student_class=effective_level,
        exam_type=resolved_exam_type,
        total_marks=paper_total,
        category=category,
    )
    cfg = dataclasses.replace(cfg, answer_language=answer_language, leniency=leniency)
    marks_context = _marks_extraction_context(cfg, effective_level, subject)
    submitted_scheme = marks_scheme_from_items(items)
    normalized_marks_source = (marks_source or "").strip().lower()
    verified_marks: MarksAttestation | None = None
    # A submitted proof is still bound to this exact paper and these exact rows, so
    # editing maxima after verification is rejected. The token is server-owned; its
    # validity does not depend on the browser's editable provenance label.
    if str(marks_attestation or "").strip():
        try:
            verified_marks = _MARKS_ATTESTATIONS.resolve(
                marks_attestation,
                qp_bytes,
                paper_total,
                submitted_scheme,
                context=marks_context,
            )
        except MarksAttestationError as exc:
            raise HTTPException(400, str(exc)) from exc
        submitted_choice_groups = _exam_choice_groups(verified_marks.scheme)

    # Recover a valid proof when a browser refresh/race lost only the opaque token.
    # This lookup is still bound to the exact paper bytes, official total and exam
    # context, and the editable rows must match the server-owned graph.
    if verified_marks is None and submitted_scheme is not None:
        try:
            cached_marks = _MARKS_ATTESTATIONS.find(
                qp_bytes, paper_total, context=marks_context)
        except MarksAttestationError:
            cached_marks = None
        if cached_marks is not None:
            if not marks_rows_agree(cached_marks.scheme, submitted_scheme):
                raise HTTPException(
                    409,
                    "The question/maxima rows changed after verification. "
                    "Click Detect max marks again before grading.",
                )
            verified_marks = cached_marks
            submitted_choice_groups = _exam_choice_groups(verified_marks.scheme)

    # Never acknowledge a grading job that the worker is certain to reject. Free,
    # deterministic paper graphs remain valid; AI-derived rows require the paper-bound
    # dual-provider proof above. This turns the old 200 -> background 'Ungradable'
    # failure into an immediate, actionable response without trusting browser rows.
    if verified_marks is None:
        try:
            deterministic_scheme, deterministic_method = detect_marks_scheme(
                qp_bytes,
                qp_name,
                None,
                student_class=effective_level,
                subject=subject,
                total_marks=paper_total,
                exam_type=cfg.exam_type,
            )
        except Exception as exc:
            _log_error("pre-queue deterministic marks validation failed", exc)
            deterministic_scheme, deterministic_method = None, "none"
        deterministic_ok = (
            deterministic_scheme is not None
            and deterministic_method in {"regex", "sections"}
        )
        if deterministic_ok:
            if (submitted_scheme is not None
                    and not marks_rows_agree(deterministic_scheme, submitted_scheme)):
                raise HTTPException(
                    409,
                    "The displayed question/maxima rows differ from the official "
                    "paper. Click Detect max marks again before grading.",
                )
            if submitted_scheme is None:
                items = [
                    {"qid": item.qid, "max": float(item.max),
                     "part": item.description}
                    for item in deterministic_scheme.items
                ]
                submitted_scheme = deterministic_scheme
        else:
            raise HTTPException(
                409,
                "The question/maxima rows have not been read and checked against the "
                "paper, so grading was not started. Click Detect max marks from question "
                "paper once more.",
            )
    cfg = dataclasses.replace(
        cfg,
        provider=CLAUDE,
        # Haiku grades every paper; the department's ChatGPT deployment is the
        # second examiner (pipeline.verifier_model_for) under the department cap.
        model=_LARGE_PAPER_GRADING_MODEL,
        grading_contract="exam_package_v2",
        # A model first checks the booklet answers THIS paper (paper_match.py).
        paper_check=True,
        # ... and a cheap one lists which answers are written, and where
        # (answer_inventory.py), before the grader is paid.
        answer_inventory=True,
        # Language papers only: underline wrong words that the page image and the
        # OCR agree on (language_marks.py). Every other subject is untouched.
        language_marks=True,
        budget_cap_inr=None,
        enforce_budget=ENFORCE_GRADING_BUDGET,
        fanout=False,
    )
    # The attested graph, not exam type or a lexical "OR", decides whether this paper
    # contains scorable alternatives.
    has_choice = bool(submitted_choice_groups)
    from rubrics import parse_rubric_upload, rubric_rows
    from exam_package import QuestionNode, sha256_bytes
    try:
        parsed_rubric = parse_rubric_upload(step_rubric if isinstance(step_rubric, str) else "")
        if parsed_rubric is not None:
            rubric_rows("", [QuestionNode(qid=item.qid, max_marks=item.max)
                             for item in submitted_scheme.items], supplied=parsed_rubric,
                        key_hash=sha256_bytes(ak_bytes or b""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"Invalid step marking scheme: {exc}") from exc

    job_id = uuid.uuid4().hex
    download_key = uuid.uuid4().hex
    with _LOCK:
        _evict_jobs()
        JOBS[job_id] = {
            "status": "running",
            "total": len(sheets),
            "done": 0,
            "current": "",
            "error": None,
            "results": [],
            "pdfs": {},
            # The graded report and the OCR that placed its marks. Held so a renderer
            # fix can be verified by re-drawing the copy (tools/rerender.py) instead of
            # paying to grade the sheet a second time. Three copies went to faculty
            # carrying a strip fault that landed hours later, and could not be redrawn.
            "reports": {},
            "key": download_key,
            "created": time.time(),
            "state": WorkflowState.RECEIVED.value,
            "workflow": AutomaticWorkflow().snapshot(),
            "workflows": {},
            "package_id": None,
            # The exam category this batch was graded under, and how it was decided.
            "category": cfg.category,
            "category_source": cfg.category_source,
            "settings": {"answer_language": answer_language, "leniency": leniency},
            # PW Vidyapeeth: one results-sheet row per student (result_export.py).
            "export": ({"facts": result_export.paper_facts(qp_text),
                        "student_class": effective_level, "subject": subject,
                        "batch": result_export.batch_fields(batch_fields)}
                       if cfg.category == "vidyapeeth" else None),
        }
        # Files that could not even be opened: listed as rejected, never graded.
        for offset, (name, reason) in enumerate(unreadable or []):
            JOBS[job_id]["results"].append({
                "index": len(sheets) + offset, "student": name, "ok": False,
                "release": False, "rejected": True, "state": "rejected",
                "failure_code": "UNREADABLE_FILE", "error": reason})
        JOBS[job_id]["total"] += len(unreadable or [])
        JOBS[job_id]["done"] += len(unreadable or [])
        if bulk is not None:
            JOBS[job_id]["bulk"] = {
                "source": bulk["source"], "target": bulk["target"],
                "part": bulk["part"], "parts": bulk["parts"],
                "already_done": bulk["already_done"],
                "counts": {"released": 0, "faculty_review": 0, "rejected": 0, "failed": 0},
                "cost_inr": 0.0, "started": time.time(), "stopping": False,
                "stopped_reason": "",
            }
    _persist_job(job_id)
    background_tasks.add_task(
        _run_job,
        dict(
            job_id=job_id,
            qp_bytes=qp_bytes,
            qp_name=qp_name,
            ak_bytes=ak_bytes,
            ak_name=ak_name,
            generate_key=generate_key,
            step_rubric=parsed_rubric,
            items=items,
            marks_source=normalized_marks_source,
            verified_marks_scheme=(
                marks_authority_scheme(verified_marks.scheme).model_dump(mode="json")
                if verified_marks is not None else None
            ),
            question_graph_proof=(
                verified_marks.proof if verified_marks is not None else ""
            ),
            sheets=sheets,
            cfg=cfg,
            student_class=effective_level or None,
            subject=subject or None,
            rate=rate,
            total_marks=paper_total,
            has_choice=has_choice,
            choice_groups=submitted_choice_groups,
            exam_level=effective_level,
            # Optional descriptive metadata retained for older API clients. The exact
            # uploaded paper/key hashes identify the assessment, so the browser no
            # longer asks a user to type these labels.
            paper_code=(paper_code or subject).strip(),
            exam_attempt=(exam_attempt or "").strip(),
            applicable_law_date=(applicable_law_date or "").strip(),
            # Already extracted above for the choice check — don't pay for it twice.
            qp_text=qp_text,
            cache_ttl=cache_ttl,
            sink=bulk["sink"] if bulk is not None else None,
            workers=workers,
            # Each booklet's printed cover is read for the student's details.
            read_covers=cfg.category == "vidyapeeth",
        ),
    )
    return {"job_id": job_id, "download_key": download_key}


# ---------------------------------------------------------------------------
# Folder mode: thousands of copies from a folder on this computer (bulk.py)
# ---------------------------------------------------------------------------
# MEASURED 2026-10-05: 8 copies at a time graded 92 to 119 copies an hour, so a
# 1,000 copy folder took 8 to 11 hours. Above 4 at a time the copies are graded in
# several worker processes (grading_engine.py), which is what lets 20 to 30 at a time
# actually run 20 to 30 at a time instead of queueing behind one render lock.
_BULK_MAX_WORKERS = 30
_BULK_DEFAULT_WORKERS = 20


def _require_local(request: Request) -> None:
    """Folder paths name this computer's disk, so only this computer may send them."""
    host = str(getattr(request.client, "host", "") or "")
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Folder mode works only in the desktop app, on the "
                                 "computer that holds the folders.")


def _bulk_plan(input_folder, output_folder, machine, machines, split: bool = False,
               extensions: set[str] | None = None) -> tuple:
    try:
        part, parts = bulk.check_part(machine, machines)
        source, target = bulk.resolve_folders(input_folder, output_folder)
        splitter = None
        if split:
            from vidyapeeth_intake import plan_split as splitter
        return bulk.plan(source, target, part, parts, splitter=splitter,
                         extensions=extensions), part, parts
    except bulk.BulkError as exc:
        raise HTTPException(400, str(exc)) from exc


def _bulk_workers(workers: str) -> int:
    try:
        value = int(str(workers or "").strip() or _BULK_DEFAULT_WORKERS)
    except ValueError as exc:
        raise HTTPException(400, "Copies at a time must be a whole number.") from exc
    return max(1, min(_BULK_MAX_WORKERS, value))


@app.post("/api/bulk/preview")
async def bulk_preview(
    request: Request,
    input_folder: str = Form(""),
    output_folder: str = Form(""),
    machine: str = Form("1"),
    machines: str = Form("1"),
    workers: str = Form(""),
    exam_type: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    category: str = Form(""),
    authorization: str | None = Header(default=None),
):
    """What a folder run would do: copies found, already finished, left, and a
    rough time and cost. Reads file names only; nothing is graded or paid for."""
    _require_local(request)
    await run_in_threadpool(_require_allowed, authorization)
    category, exam_type = _category_or_400(category, exam_type)
    found, part, parts = await run_in_threadpool(
        _bulk_plan, input_folder, output_folder, machine, machines,
        category == "vidyapeeth", _sheet_extensions(category))
    count = _bulk_workers(workers)
    segments = found.pop("segments", {}) or {}
    kind = ((exam_type or "").strip().lower()
            or detect_exam_type(student_class or "", subject or ""))
    todo = found.pop("todo")
    return {**found, "to_grade": len(todo), "part": part, "parts": parts,
            "booklets_split": len(segments),
            "workers": count, "processes": grading_engine.plan_processes(count),
            "sample": todo[:5],
            "estimate": bulk.estimate(len(todo), count, kind)}


@app.post("/api/bulk/start")
async def bulk_start(
    request: Request,
    background_tasks: BackgroundTasks,
    input_folder: str = Form(""),
    output_folder: str = Form(""),
    machine: str = Form("1"),
    machines: str = Form("1"),
    workers: str = Form(""),
    question_paper: UploadFile | None = File(None),
    answer_key: UploadFile | None = File(None),
    question_page_range: Annotated[str, Form()] = "",
    answer_page_range: Annotated[str, Form()] = "",
    use_question_paper_as_key: Annotated[bool, Form()] = False,
    step_rubric: str = Form(""),
    provider: str = Form("claude"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    exam_type: str = Form(""),
    total_marks: str = Form(""),
    exam_level: str = Form(""),
    paper_code: str = Form(""),
    exam_attempt: str = Form(""),
    applicable_law_date: str = Form(""),
    key_source: str = Form("rubric"),
    marks_items: str = Form("[]"),
    marks_source: str = Form(""),
    marks_attestation: str = Form(""),
    usd_to_inr: str = Form(""),
    category: str = Form(""),
    answer_language: str = Form(""),
    leniency: str = Form("standard"),
    centre_name: str = Form(""),
    course_name: str = Form(""),
    test_date: str = Form(""),
    board: str = Form(""),
    vertical: str = Form(""),
    test_name: str = Form(""),
    paper_mode: str = Form(""),
    authorization: str | None = Header(default=None),
):
    """Grade every copy in a folder that is not finished yet. Same checks, same
    grading and same three outcomes as an upload; results go to the output folder."""
    _require_local(request)
    google_token = await run_in_threadpool(_require_allowed, authorization)
    task_id = pw_access.new_task_id("evaluate-folder")
    category, exam_type = _category_or_400(category, exam_type)
    batch_header = _batch_header_or_400(
        category, centre_name=centre_name, course_name=course_name, board=board, vertical=vertical,
        test_name=test_name, test_date=test_date, paper_mode=paper_mode,
        student_class=student_class, subject=subject)
    if category == "vidyapeeth" and not answer_language:
        answer_language = vidyapeeth_fields.answer_language_for(paper_mode)
    if question_paper is None:
        raise HTTPException(400, "Upload the official question paper.")
    qp_bytes, qp_name, answer_key = await _selected_exam_uploads(
        question_paper, answer_key, question_page_range, answer_page_range,
        use_question_paper_as_key)
    if use_question_paper_as_key:
        key_source = "upload"
    found, part, parts = await run_in_threadpool(
        _bulk_plan, input_folder, output_folder, machine, machines,
        category == "vidyapeeth", _sheet_extensions(category))
    # A refreshed or closed browser tab does not stop a run; pressing Start again
    # shows the run that is already saving to this folder instead of a second one.
    with _LOCK:
        running = [(job_id, job) for job_id, job in JOBS.items()
                   if job.get("status") == "running"
                   and (job.get("bulk") or {}).get("target") == found["target"]]
    if running:
        job_id, job = running[0]
        return {"job_id": job_id, "download_key": job.get("key") or "", "attached": True,
                "to_grade": int(job.get("total") or 0) - int(job.get("done") or 0),
                "already_done": job["bulk"]["already_done"], "output_folder": found["target"]}
    todo = found["todo"]
    if not todo:
        raise HTTPException(400, (
            f"Nothing left to grade: all {found['this_part']} copies for this computer "
            "are finished." if found["this_part"] else
            "No PDF, PNG or JPG answer sheets were found in that folder."))
    try:
        sink = bulk.FolderSink(Path(found["source"]), Path(found["target"]), part, parts,
                               found.get("segments"))
    except OSError as exc:
        raise HTTPException(400, f"Cannot write to the output folder: {exc}") from exc
    started = await _queue_evaluation(
        background_tasks, google_token=google_token, task_id=task_id,
        qp_bytes=qp_bytes, qp_name=qp_name, sheets=[(rel, None) for rel in todo],
        answer_key=answer_key, step_rubric=step_rubric, provider=provider, model=model,
        student_class=student_class, subject=subject, exam_type=exam_type,
        total_marks=total_marks, exam_level=exam_level, paper_code=paper_code,
        exam_attempt=exam_attempt, applicable_law_date=applicable_law_date,
        key_source=key_source, marks_items=marks_items, marks_source=marks_source,
        marks_attestation=marks_attestation, usd_to_inr=usd_to_inr,
        category=category, answer_language=answer_language, leniency=leniency,
        batch_fields=batch_header,
        bulk={"source": found["source"], "target": found["target"], "part": part,
              "parts": parts, "already_done": found["already_done"], "sink": sink},
        workers=_bulk_workers(workers),
    )
    return {**started, "to_grade": len(todo), "already_done": found["already_done"],
            "output_folder": found["target"]}


@app.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: str, k: str = ""):
    """Finish the copies being marked now, start no more. Folder mode only: an
    upload's copies live in memory and would be lost."""
    job = _job_or_404(job_id, k)
    with _LOCK:
        state = job.get("bulk")
        if state is None:
            raise HTTPException(409, "Only a folder run can be stopped.")
        if job.get("status") == "running" and not state["stopping"]:
            state["stopping"] = True
            state["stopped_reason"] = ("Stopped by you. Press Start with the same folders "
                                       "to continue; finished copies are skipped.")
    _persist_job(job_id)
    return {"stopping": True}


# ---------------------------------------------------------------------------
# Design and drawing papers (NID, NIFT, UCEED, NATA): design_grading.py
# ---------------------------------------------------------------------------
# A separate route on purpose. The written-answer route above proves a marks graph
# from the paper, requires a text answer key and anchors every mark to an OCR line.
# A drawing paper has none of those, so it gets its own small contract: the paper
# is read into questions and a rubric once, the operator enters the marks for each
# question (design papers rarely print them), and every copy is marked by vision.
_DESIGN_PAPERS: dict[str, dict] = {}
_DESIGN_PAPER_CACHE = 12


def _design_paper_key(qp_bytes: bytes, ak_bytes: bytes | None) -> str:
    import hashlib
    import design_grading
    digest = hashlib.sha256(qp_bytes)
    digest.update(b"|key|")
    digest.update(ak_bytes or b"")
    # A new rubric prompt reads the paper again instead of reusing an old rubric.
    digest.update(design_grading.DESIGN_PAPER_SYSTEM_PROMPT.encode("utf-8"))
    return digest.hexdigest()


def _read_design_paper_cached(qp_bytes, qp_name, ak_bytes, ak_name, *, google_token,
                              task_id, usage_out=None):
    """(DesignPaper, PaperContext) for this exact paper and key, read at most once
    per process: the preview button and the batch share one paid reading."""
    import design_grading as dg
    key = _design_paper_key(qp_bytes, ak_bytes)
    context = dg.paper_context(qp_bytes, qp_name, ak_bytes, ak_name)
    with _LOCK:
        saved = _DESIGN_PAPERS.get(key)
    if saved is not None:
        return dg.DesignPaper.model_validate(copy.deepcopy(saved)), context
    paper = dg.verify_design_paper(
        dg.read_design_paper(context, google_token=google_token, task_id=task_id,
                             filename=qp_name or "paper", usage_out=usage_out),
        context.text)
    with _LOCK:
        _DESIGN_PAPERS[key] = paper.model_dump(mode="json")
        while len(_DESIGN_PAPERS) > _DESIGN_PAPER_CACHE:
            _DESIGN_PAPERS.pop(next(iter(_DESIGN_PAPERS)))
    return paper, context


async def _optional_upload(upload: UploadFile | None, label: str):
    if upload is None:
        return None, None
    data = await upload.read()
    if not data:
        return None, None
    _validate_document(upload.filename, data, label)
    return data, upload.filename


@app.post("/api/design/read-paper")
async def design_read_paper(
    question_paper: UploadFile = File(...),
    answer_key: UploadFile | None = File(None),
    question_page_range: Annotated[str, Form()] = "",
    answer_page_range: Annotated[str, Form()] = "",
    use_question_paper_as_key: Annotated[bool, Form()] = False,
    authorization: str | None = Header(default=None),
):
    """The questions of a design paper, with the marks it prints (usually none), so
    the operator can enter the marks for each question before marking starts."""
    google_token = await run_in_threadpool(_require_allowed, authorization)
    qp_bytes, qp_name, answer_key = await _selected_exam_uploads(
        question_paper, answer_key, question_page_range, answer_page_range,
        use_question_paper_as_key)
    ak_bytes, ak_name = await _optional_upload(answer_key, "Answer key")
    try:
        paper, _ = await run_in_threadpool(
            lambda: _read_design_paper_cached(
                qp_bytes, qp_name, ak_bytes, ak_name,
                google_token=google_token,
                task_id=pw_access.new_task_id("design-paper")))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        _log_error("design paper could not be read", exc)
        raise HTTPException(502, f"The paper could not be read: {exc}") from exc
    if not paper.marked:
        raise HTTPException(
            422, "No drawing or written question could be read from this paper.")
    return {
        "title": paper.title,
        "notes": paper.notes,
        "questions": [
            {"qid": q.qid, "kind": q.kind, "brief": q.brief,
             "printed_marks": q.max_marks,
             "criteria": [{"name": c.name, "weight": c.weight, "looks_for": c.looks_for}
                          for c in q.criteria]}
            for q in paper.marked
        ],
    }


@app.post("/api/evaluate-design")
async def evaluate_design(
    background_tasks: BackgroundTasks,
    answer_sheets: list[UploadFile] = File(...),
    question_paper: UploadFile | None = File(None),
    answer_key: UploadFile | None = File(None),
    question_page_range: Annotated[str, Form()] = "",
    answer_page_range: Annotated[str, Form()] = "",
    use_question_paper_as_key: Annotated[bool, Form()] = False,
    question_marks: str = Form(""),
    total_marks: str = Form(""),
    usd_to_inr: str = Form(""),
    authorization: str | None = Header(default=None),
):
    import design_grading as dg
    google_token = await run_in_threadpool(_require_allowed, authorization)
    if question_paper is None:
        raise HTTPException(400, "Upload the question paper.")
    qp_bytes, qp_name, answer_key = await _selected_exam_uploads(
        question_paper, answer_key, question_page_range, answer_page_range,
        use_question_paper_as_key)
    ak_bytes, ak_name = await _optional_upload(answer_key, "Answer key")
    sheets = [(file.filename, await file.read()) for file in answer_sheets]
    if not sheets:
        raise HTTPException(400, "Upload at least one answer sheet.")
    for index, (name, data) in enumerate(sheets, start=1):
        _validate_document(name, data, f"Answer sheet {index}")
    if sum(len(data) for _, data in sheets) > _MAX_BATCH_BYTES:
        raise HTTPException(413, "Answer-sheet batch exceeds the 300 MB upload limit.")
    try:
        paper_total = float(total_marks) if total_marks.strip() else None
    except ValueError as exc:
        raise HTTPException(400, "Total marks must be a number.") from exc
    if paper_total is not None and (not math.isfinite(paper_total) or paper_total <= 0):
        raise HTTPException(400, "Total marks must be a positive number.")
    try:
        ordered, named = dg.parse_entered_marks(question_marks)
    except StrictGradeError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Refuse now what the worker is certain to refuse: no marks entered, no total,
    # and a paper that never uses the word. Design papers rarely print marks.
    nothing_entered = not ordered and not named and paper_total is None
    if nothing_entered and "mark" not in pdf_text_layer(qp_bytes, qp_name).lower():
        raise HTTPException(
            400,
            "This paper does not print the marks for each question. Enter them before "
            "evaluating, for example 15, 20, 15, 20.")
    try:
        rate = float(usd_to_inr) if usd_to_inr.strip() else DEFAULT_USD_TO_INR
    except ValueError:
        rate = DEFAULT_USD_TO_INR

    job_id = uuid.uuid4().hex
    download_key = uuid.uuid4().hex
    with _LOCK:
        _evict_jobs()
        JOBS[job_id] = {
            "status": "running", "mode": "design", "total": len(sheets), "done": 0,
            "current": "", "error": None, "results": [], "pdfs": {}, "reports": {},
            "key": download_key, "created": time.time(),
            "state": WorkflowState.RECEIVED.value,
            "workflow": AutomaticWorkflow().snapshot(), "workflows": {},
            "package_id": None,
        }
    _persist_job(job_id)
    background_tasks.add_task(
        _run_design_job,
        dict(job_id=job_id, qp_bytes=qp_bytes, qp_name=qp_name, ak_bytes=ak_bytes,
             ak_name=ak_name, sheets=sheets, question_marks=question_marks,
             total_marks=paper_total, rate=rate, google_token=google_token,
             task_id=pw_access.new_task_id("evaluate-design")),
    )
    return {"job_id": job_id, "download_key": download_key}


def _is_digital_pdf(data: bytes, filename: str) -> bool:
    """True when this file carries a real text layer (grading_engine.is_digital_pdf)."""
    return grading_engine.is_digital_pdf(data, filename)


def _paper_text_layer_complete(data: bytes, filename: str) -> bool:
    """Require usable text on every page before qid/max-only AI attestation.

    A textual cover plus scanned question pages would otherwise hide an OR clause
    from ``paper_has_choice`` while still satisfying a document-wide character count.
    """
    if not (filename or "").lower().endswith(".pdf"):
        return False
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            page_text = [re.sub(r"\s+", "", page.get_text("text") or "")
                         for page in doc]
        return bool(page_text) and all(len(text) >= 80 for text in page_text)
    except Exception:
        return False


def _generic_choice_hint_disproved(data, filename, paper_text, scheme, official_total):
    """A complete compulsory paper can contradict a generic cover template.

    The Economics Infinity Test 10 cover says both that all questions are compulsory
    and that some contain internal choice, but its actual questions offer none. Do
    not let that template sentence invalidate a complete visual marks reading. A
    concrete selection/OR, missing page text, existing choice graph or unequal total
    still requires choice verification.
    """
    if (getattr(scheme, "choice_groups", None) or official_total is None
            or not re.search(r"\ball\s+(?:the\s+)?questions\s+are\s+compulsory\b",
                             paper_text or "", re.IGNORECASE)):
        return False
    generic = re.compile(r"\binternal\s+choices?\b", re.IGNORECASE)
    if not generic.search(paper_text or ""):
        return False
    remaining = generic.sub("", paper_text)
    # Inline uppercase OR also counts here; the broader routing hint intentionally
    # ignores ordinary lowercase 'or' prose, which is not enough to disprove a hint.
    if (paper_has_choice(remaining, "")
            or re.search(r"\b(?:OR|Or)\b|अथवा", remaining)
            or not _paper_text_layer_complete(data, filename)):
        return False
    try:
        return (math.isfinite(float(official_total)) and float(official_total) > 0
                and abs(effective_marks_total(scheme) - float(official_total)) <= .01)
    except (MarksAttestationError, TypeError, ValueError):
        return False


def _job_cache_ttl(payload: dict) -> str:
    """The prompt-cache TTL a job buys: the caller's choice when it made one (the
    workflow, which knows how many copies of the test are still to check), else the
    one the job's own number of sheets calls for."""
    chosen = payload.get("cache_ttl")
    return chosen if chosen is not None else _cache_ttl_for_batch(len(payload["sheets"]))


def _read_exam_documents(payload, orient, cfg, qp_text, generated_key=None):
    """The paper and key as a job needs them: turned upright, as page images, and as text."""
    qp_bytes = orient(payload["qp_bytes"], payload["qp_name"])
    qp_pngs = pdf_or_image_to_pngs(qp_bytes, payload["qp_name"])
    ak_bytes = orient(payload["ak_bytes"], payload["ak_name"])
    ak_pngs = pdf_or_image_to_pngs(ak_bytes, payload["ak_name"])
    # Keep text for search and original images for visual step-mark allocation.
    # A written key is read from its own text: a generated PDF garbles Devanagari.
    ak_text_layer = (generated_key.text if generated_key is not None
                     else pdf_text_layer(payload["ak_bytes"], payload["ak_name"]))
    # MEASURED 2026-10-03: the Parishram Physics "Question & Solution" PDF is a scan
    # with no text layer, so the key was refused outright and the whole batch could
    # not be graded. A scanned key or paper is read with the same OCR the answer
    # sheets get; strict verification then works on that text as on a digital PDF.
    if len((ak_text_layer or "").strip()) < 40:
        ak_text_layer = scanned_document_text(ak_pngs, cfg=cfg, label="answer key")
    if len((qp_text or "").strip()) < 40:
        qp_text = scanned_document_text(qp_pngs, cfg=cfg, label="question paper") or qp_text
    return qp_bytes, qp_pngs, ak_bytes, ak_pngs, ak_text_layer, qp_text


_SHARED_DOCUMENTS: "collections.OrderedDict[str, tuple]" = collections.OrderedDict()
_SHARED_DOCUMENT_LOCKS: dict[str, threading.Lock] = {}
_SHARED_DOCUMENTS_LOCK = threading.Lock()
_SHARED_DOCUMENTS_KEPT = 6


def _shared_exam_documents(payload, orient, cfg, qp_text):
    """One test's paper and key, prepared once. Each workflow copy is a job of its own, so
    every copy rendered both PDFs again, kept its own page images while it waited, and paid
    again to OCR a scanned key (30 Oct 2026, operator: less memory, a free computer). Copies
    of a test wait for the first to finish preparing them, then share the same images."""
    key = hashlib.sha256(b"\0".join((payload["qp_bytes"], payload["ak_bytes"] or b"",
                                     str(payload.get("qp_name")).encode(), str(payload.get("ak_name")).encode()))).hexdigest()
    with _SHARED_DOCUMENTS_LOCK:
        gate = _SHARED_DOCUMENT_LOCKS.setdefault(key, threading.Lock())
    with gate:
        with _SHARED_DOCUMENTS_LOCK:
            found = _SHARED_DOCUMENTS.get(key)
            if found is not None:
                _SHARED_DOCUMENTS.move_to_end(key)
        if found is None:
            found = _read_exam_documents(payload, orient, cfg, qp_text)
            with _SHARED_DOCUMENTS_LOCK:
                _SHARED_DOCUMENTS[key] = found
                while len(_SHARED_DOCUMENTS) > _SHARED_DOCUMENTS_KEPT:
                    _SHARED_DOCUMENT_LOCKS.pop(_SHARED_DOCUMENTS.popitem(last=False)[0], None)
    qp_bytes, qp_pngs, ak_bytes, ak_pngs, ak_text_layer, shared_text = found
    return qp_bytes, list(qp_pngs), ak_bytes, list(ak_pngs), ak_text_layer, shared_text


def _make_orienter(cfg: ProviderConfig, usage_out: dict | None = None):
    """Turns one uploaded file's pages the right way up (grading_engine.make_orienter).

    The digital PDF test is looked up when a file is oriented, not now, so a test that
    replaces _is_digital_pdf here replaces it for the orienter too."""
    return grading_engine.make_orienter(
        cfg, usage_out, is_digital=lambda data, filename: _is_digital_pdf(data, filename))


# Folder mode keeps only the newest rows in the job record: the full record of every
# copy is on disk (results CSV and progress log), and the record is re-saved after
# every copy, so 10,000 rows in it would be rewritten 10,000 times.
_BULK_RECENT_ROWS = 100


# Folder mode at many copies at a time starts the first wave one copy every few
# seconds. All at once, 29 copies would each send the orientation probe and up to 8
# OCR pages to the proxy in the same second; staggered, the copies stay spread out.
_RAMP_SECONDS = 2.0
_PERSISTED_AT: dict[str, float] = {}
# A folder run with 30 copies in flight reports a stage change several times a second.
_PROGRESS_SAVE_SECONDS = 2.0


def _persist_progress(job_id: str) -> None:
    """Save a progress-only change, at most every few seconds per job. A copy's result
    is always saved at once (grade_one); this only drops intermediate stage labels."""
    now = time.monotonic()
    with _LOCK:
        if now - _PERSISTED_AT.get(job_id, 0.0) < _PROGRESS_SAVE_SECONDS:
            return
        _PERSISTED_AT[job_id] = now
    _persist_job(job_id)


def _bulk_stopping(job_id: str) -> bool:
    with _LOCK:
        return bool(((JOBS.get(job_id) or {}).get("bulk") or {}).get("stopping"))


def _file_bulk_result(job_id, sink, name, result, pdf_bytes, saved_report) -> None:
    """Write one finished copy to the output folder and count it."""
    try:
        output = sink.file(name, result, pdf_bytes, saved_report)
    except Exception as exc:  # disk full, folder removed: say so and stop the run
        _log_error(f"could not save the result for {name}", exc)
        output = ""
        result = dict(result, ok=False, release=False, state="failed",
                      error=f"The result could not be saved: {exc}")
        with _LOCK:
            JOBS[job_id]["bulk"]["stopping"] = True
            JOBS[job_id]["bulk"]["stopped_reason"] = (
                f"Stopped: results could not be saved to the output folder ({exc}).")
    state = result.get("state") or "failed"
    state = state if state in ("released", "faculty_review", "rejected") else "failed"
    export = (JOBS.get(job_id) or {}).get("export")
    if export:
        try:
            result_export.append_csv(sink.target, result_export.row_for(
                dict(result, state=state), source=name,
                report_link=str(sink.target / output) if output else "",
                facts=export.get("facts") or {}, student_class=export.get("student_class", ""),
                subject=export.get("subject", ""), batch=export.get("batch") or {}))
        except Exception as exc:  # noqa: BLE001 - the copy itself is filed
            _log_error(f"could not add {name} to the results sheet", exc)
    cost = (result.get("cost") or {}).get("total_inr")
    row = {"index": result.get("index"), "student": name, "state": state,
           "ok": bool(result.get("ok")), "release": result.get("release") is True,
           "score": result.get("score"), "max": result.get("max"),
           "percent": result.get("percent"), "output": output,
           "error": result.get("error") or "; ".join(result.get("review_reasons") or [])}
    with _LOCK:
        job = JOBS[job_id]
        bulk_state = job["bulk"]
        bulk_state["counts"][state] += 1
        if isinstance(cost, (int, float)):
            bulk_state["cost_inr"] = round(bulk_state["cost_inr"] + float(cost), 2)
        # An outage fails every copy in turn; stop instead of spending the batch.
        streak = bulk_state.get("failure_streak", 0) + 1 if state == "failed" else 0
        bulk_state["failure_streak"] = streak
        if streak >= bulk.MAX_CONSECUTIVE_FAILURES and not bulk_state["stopping"]:
            bulk_state["stopping"] = True
            bulk_state["stopped_reason"] = (
                f"Stopped after {streak} copies failed in a row. Last error: "
                f"{str(row['error'])[:300]}. Fix the cause (sign in again, check the "
                "internet or the output folder), then press Start: finished copies "
                "are skipped and failed ones are tried again.")
        job["results"].append(row)
        del job["results"][:-_BULK_RECENT_ROWS]
        # The copy's whole history is on disk now (progress log and results CSV).
        # Keeping it here grew the record that is copied and re-saved on every stage
        # of every copy: 352 copies made it 600 KB, 1,000 would make it 1.7 MB.
        job["workflows"].pop(result.get("index"), None)
        job["done"] += 1


def _with_marking_settings(qp_text: str, cfg) -> str:
    """The paper as the grader reads it, with the batch's language and step marking
    settings (marking_settings.py) when the operator chose any, and the board's own
    marking rules when a commerce paper names its board (categories/commerce.py)."""
    text = qp_text or ""
    if str(getattr(cfg, "category", "") or "") == "commerce":
        from categories.commerce import board_rules
        board = board_rules(text)
        if board:
            text += "\n\n" + board
    rules = marking_settings.rules(getattr(cfg, "answer_language", "") or "",
                                   getattr(cfg, "leniency", "") or "standard")
    if not rules:
        return text if text != (qp_text or "") else qp_text
    return text + "\n\n=== MARKING SETTINGS FOR THIS BATCH ===\n" + rules


def _write_results_sheet(target) -> None:
    """The folder's results sheet as .xlsx, from the CSV rows written copy by copy."""
    try:
        path = Path(target) / result_export.CSV_NAME
        if os.path.exists(bulk._os_path(path)):
            rows = result_export.latest_per_copy(result_export.read_csv(path))
            result_export.write_xlsx(rows, Path(target) / result_export.XLSX_NAME)
    except Exception as exc:  # noqa: BLE001 - the CSV is still there
        _log_error("could not write the results sheet", exc)


def _write_generated_key(payload: dict, qp_pngs: list, qp_text: str, cfg, job_id: str):
    """A paper with no key (teaching exams): its model answer key, written once from
    the paper (teaching_key.py). The PDF becomes the job's key and is kept for the
    faculty: beside the results in folder mode, in the download zip otherwise.
    Raises TeachingKeyError when no key that fits the paper could be written."""
    from job_store import default_data_dir
    from teaching_key import write_answer_key
    usage: dict = {}
    category = getattr(cfg, "category", "")
    policy = categories.get(category).answer_key if category else None
    key = write_answer_key(
        payload["qp_bytes"], qp_pngs, qp_text, payload.get("items") or [],
        google_token=cfg.google_token, task_id=cfg.task_id,
        subject=payload.get("subject") or "", level=payload.get("exam_level") or "",
        filename=payload.get("qp_name") or "paper", usage_out=usage,
        examination=policy.examination if policy else "",
        notes=policy.key_notes if policy else "")
    payload["ak_bytes"], payload["ak_name"] = key.pdf, key.filename
    saved = default_data_dir() / "teaching_keys" / f"{job_id}_{key.filename}"
    bulk._write_atomic(saved, key.pdf)
    if payload.get("sink") is not None:
        try:
            bulk._write_atomic(Path(payload["sink"].target) / key.filename, key.pdf)
        except Exception as exc:  # noqa: BLE001 - the zip and data folder still have it
            _log_error(f"could not write {key.filename} beside the results", exc)
    with _LOCK:
        if job_id in JOBS:
            JOBS[job_id]["generated_key"] = {
                "filename": key.filename, "path": str(saved), "cached": key.cached,
                "questions": len(key.sheet.questions)}
            JOBS[job_id]["generated_key_usage"] = usage
    _persist_job(job_id)
    return key


def _run_job(payload: dict) -> None:
    job_id = payload["job_id"]
    setup_flow = AutomaticWorkflow.from_snapshot(JOBS[job_id].get("workflow"))

    def save_setup(snapshot: dict) -> None:
        with _LOCK:
            if job_id in JOBS:
                JOBS[job_id]["workflow"] = snapshot
                JOBS[job_id]["state"] = snapshot["state"]
                JOBS[job_id]["current"] = stage_label(snapshot["state"])
        _persist_job(job_id)

    setup_flow.on_change = save_setup
    try:
        setup_flow.transition(
            WorkflowState.PACKAGE_VALIDATING,
            "Validating the exact paper, official key, question graph, and budget policy",
        )
        cfg = payload["cfg"]
        # The shared prefix is worth caching only when more than one sheet will
        # read it back, and which TTL to buy depends on how many will.
        cfg = dataclasses.replace(cfg, cache_ttl=_job_cache_ttl(payload))
        orient = _make_orienter(cfg)
        # The paper's text layer pins the printed "(N Marks)" tags to their questions —
        # it goes into every grading call so maxima can't drift. It was extracted from
        # the same bytes in the request handler, so it is passed through, not redone.
        qp_text = payload.get("qp_text", "")
        generated_key = None
        ak_text = None
        if payload.get("generate_key"):
            qp_bytes = orient(payload["qp_bytes"], payload["qp_name"])
            qp_pngs = pdf_or_image_to_pngs(qp_bytes, payload["qp_name"])
            with _LOCK:
                JOBS[job_id]["current"] = "Writing the model answer key from the question paper"
            generated_key = _write_generated_key(payload, qp_pngs, qp_text, cfg, job_id)
            qp_bytes, qp_pngs, ak_bytes, ak_pngs, ak_text_layer, qp_text = _read_exam_documents(
                payload, orient, cfg, qp_text, generated_key)
        else:
            qp_bytes, qp_pngs, ak_bytes, ak_pngs, ak_text_layer, qp_text = _shared_exam_documents(payload, orient, cfg, qp_text)
        submitted_scheme = marks_scheme_from_items(payload["items"])
        marks_scheme = None
        scheme_source = "unknown"
        verified_scheme_data = payload.get("verified_marks_scheme")
        if verified_scheme_data is not None:
            try:
                verified_scheme = MarksScheme.model_validate(verified_scheme_data)
            except (TypeError, ValueError) as exc:
                raise StrictGradeError(
                    "INVALID_VERIFIED_QUESTION_GRAPH",
                    "The server-owned verified graph could not be decoded.",
                ) from exc
            if not payload.get("question_graph_proof"):
                raise StrictGradeError(
                    "MISSING_QUESTION_GRAPH_PROOF",
                    "The independently verified graph lost its server proof.",
                )
            if not marks_rows_agree(verified_scheme, submitted_scheme):
                raise StrictGradeError(
                    "QUESTION_GRAPH_EDITED",
                    "The submitted rows changed after independent marks verification.",
                )
            marks_scheme = verified_scheme
            scheme_source = "ai_verified"
        # Client-supplied provenance is not evidence. Rebuild the graph from the exact
        # uploaded paper and require any browser-edited rows to agree with it. A
        # scanned/irregular paper needs a signed official manifest; plain JSON is
        # never promoted to an authoritative package.
        with _LOCK:
            JOBS[job_id]["current"] = "Reading the marks scheme off the paper…"
        try:
            detected_scheme, method = detect_marks_scheme(
                qp_bytes,
                payload["qp_name"],
                None,
                student_class=payload["student_class"],
                subject=payload["subject"],
                total_marks=payload.get("total_marks"),
                exam_type=cfg.exam_type,
            )
        except Exception as exc:
            _log_error("marks-scheme deterministic extraction failed", exc)
            detected_scheme, method = None, "none"

        def scheme_signature(scheme: MarksScheme | None) -> tuple[tuple[str, float], ...]:
            return tuple(sorted(
                (normalize_qid(item.qid), round(float(item.max), 4))
                for item in list(getattr(scheme, "items", None) or [])
            ))

        if marks_scheme is None and detected_scheme is not None and method in ("regex", "sections"):
            if (submitted_scheme is not None
                    and scheme_signature(submitted_scheme)
                    != scheme_signature(detected_scheme)):
                raise StrictGradeError(
                    "QUESTION_GRAPH_EDITED",
                    "The submitted question/maxima rows differ from the deterministic "
                    "graph in the uploaded official paper.",
                )
            marks_scheme = detected_scheme
            scheme_source = method
        elif marks_scheme is None and submitted_scheme is not None:
            raise StrictGradeError(
                "UNVERIFIED_SUBMITTED_QUESTION_GRAPH",
                "This paper's question graph cannot be proven from its document. Plain "
                "browser-entered rows are not authoritative; use a signed official exam "
                "package when manifest support is configured.",
            )

        if len((ak_text_layer or "").strip()) < 40:
            raise StrictGradeError(
                "ANSWER_KEY_TEXT_UNAVAILABLE",
                "The official answer key has no usable text layer. Upload a digital PDF "
                "or a versioned text marking scheme; strict verification will not guess "
                "from an unreadable key.",
            )
        if marks_scheme is None:
            raise StrictGradeError(
                "MISSING_QUESTION_GRAPH",
                "No deterministic question/maxima graph could be built from the "
                "official paper, so automatic release is unavailable for this package.",
            )
        conflict = scheme_contradicts_paper(
            marks_scheme, qp_text, payload.get("total_marks"))
        if conflict:
            raise StrictGradeError(
                "QUESTION_GRAPH_CONTRADICTS_PAPER",
                f"The submitted question/maxima graph disagrees with the official paper: {conflict}",
            )
        if payload.get("has_choice") and not payload.get("choice_groups"):
            raise StrictGradeError(
                "CHOICE_GRAPH_REQUIRED",
                "This paper contains internal choice. Submit its official choice groups "
                "and selection rule; automatic grading will not infer which alternatives count.",
            )

        exam_package = build_exam_package(
            exam_type=cfg.exam_type,
            student_class=payload.get("student_class") or "",
            subject=payload.get("subject") or "",
            level=payload.get("exam_level") or "",
            paper=payload.get("paper_code") or "",
            attempt=payload.get("exam_attempt") or "",
            applicable_law_date=payload.get("applicable_law_date") or None,
            total_marks=float(payload["total_marks"]),
            question_paper=payload.get("qp_bytes"),
            answer_key=payload.get("ak_bytes"),
            key_source="upload",
            marks_scheme=marks_scheme,
            scheme_source=scheme_source,
            question_graph_proof=payload.get("question_graph_proof") or "",
            choice_groups=payload.get("choice_groups") or [],
            question_paper_text=qp_text,
            answer_key_text=ak_text_layer,
            step_rubric=payload.get("step_rubric"),
            # An exam category the operator chose sets the department (and with it
            # the OR-choice rule); an auto-detected one keeps the old reading.
            department=(categories.get(cfg.category).department
                        if cfg.category and cfg.category_source == "explicit" else ""),
        )
        # The key's MCQ answers, read once for the batch by a cheap text-only model
        # and verified against the key text (key_answers.py). Without them an MCQ
        # is judged as prose, which is how wrong options were credited on the CA
        # batch. Fails open: the package stands as built.
        try:
            from key_answers import complete_objective_answers
            key_usage: dict = {}
            exam_package, typed = complete_objective_answers(
                exam_package, ak_text_layer, google_token=cfg.google_token,
                task_id=cfg.task_id, filename=payload.get("ak_name") or "key",
                usage_out=key_usage)
            if typed:
                with _LOCK:
                    JOBS[job_id]["key_answers_usage"] = key_usage
        except Exception as exc:  # noqa: BLE001 - see key_answers.py
            _log_error("official MCQ answers could not be read from the key", exc)
        # MEASURED 2026-10-01 (Radiant 2.0 SST Test 02): the key page printed Q4 (B)
        # while its own worked solution was option (A)'s text, and every correct
        # student lost the mark. The worked solution wins, and the teacher is told on
        # every copy of the batch (key_answers.correct_key_against_solutions).
        key_warnings: list[str] = []
        try:
            from key_answers import correct_key_against_solutions
            exam_package, key_warnings = correct_key_against_solutions(
                exam_package, qp_text, ak_text_layer)
        except Exception as exc:  # noqa: BLE001 - a check of the key never stops a batch
            _log_error("the answer key could not be checked against its solutions", exc)
        # Parts the worked solution does not label (Parishram Chemistry Q24, its part
        # labels shifted by one): named for the teacher in KEY_WARNINGS.txt only; no
        # mark is changed and no copy is held for it.
        key_doubts: list[str] = []
        try:
            from key_answers import solution_part_gaps
            key_doubts = solution_part_gaps(qp_text, ak_text_layer)
        except Exception as exc:  # noqa: BLE001 - a check of the key never stops a batch
            _log_error("the answer key's part labels could not be checked", exc)
        # The key's step marks, set once for the paper and frozen for every copy
        # (key_rubric.py). Without them the grader invented a different split of the
        # same question on each copy of a CA paper. Fails open: unfixed questions are
        # graded as before.
        from key_rubric import key_rubric_applies
        if key_rubric_applies(cfg, payload.get("subject") or "") and payload.get("ak_bytes"):
            try:
                from key_rubric import complete_step_rubric, rubric_flavor
                from pipeline import _category_rules
                rubric_usage: dict = {}
                exam_package, fixed_steps = complete_step_rubric(
                    exam_package, payload.get("ak_bytes"), ak_text_layer,
                    google_token=cfg.google_token, task_id=cfg.task_id,
                    filename=payload.get("ak_name") or "key", usage_out=rubric_usage,
                    subject=payload.get("subject") or "", flavor=rubric_flavor(cfg),
                    category_rules=_category_rules(cfg))
                if fixed_steps:
                    with _LOCK:
                        JOBS[job_id]["key_rubric_usage"] = rubric_usage
                        JOBS[job_id]["key_rubric_qids"] = list(fixed_steps)
            except Exception as exc:  # noqa: BLE001 - see key_rubric.py
                _log_error("the key's step marks could not be set for the paper", exc)
        # A key the tool wrote carries its own step marks: frozen into the package
        # through key_rubric's checks, so every copy of the paper gets the same split.
        if generated_key is not None:
            from key_rubric import fill_step_rubric, verify_key_rubric
            from teaching_key import as_key_rubric_sheet
            accepted, refused = verify_key_rubric(as_key_rubric_sheet(generated_key.sheet),
                                                  exam_package)
            exam_package, fixed_steps = fill_step_rubric(exam_package, accepted)
            for note in refused:
                print(f"[teaching-key] steps not used: {note}", flush=True)
            with _LOCK:
                JOBS[job_id].setdefault("generated_key", {})["fixed_qids"] = list(fixed_steps)
        for note in key_warnings + key_doubts:
            print(f"[key-check] {note}", flush=True)
        if key_warnings:
            with _LOCK:
                JOBS[job_id]["key_warnings"] = list(key_warnings)
        if (key_warnings or key_doubts) and payload.get("sink") is not None:
            try:
                bulk._write_atomic(Path(payload["sink"].target) / "KEY_WARNINGS.txt",
                                   "\n".join(key_warnings + key_doubts).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - the job record still carries it
                _log_error("could not write KEY_WARNINGS.txt", exc)
        package_blockers = exam_package.blockers()
        if package_blockers:
            raise StrictGradeError(
                "INVALID_EXAM_PACKAGE",
                "; ".join(issue.detail for issue in package_blockers[:5]),
            )
        cfg = dataclasses.replace(cfg, exam_package=exam_package)
        setup_flow.transition(
            WorkflowState.PACKAGE_VALIDATED,
            f"Exam package {exam_package.package_id[:12]} validated"
            + (f"; answer key corrected from its own solutions: {' '.join(key_warnings)}"
               if key_warnings else ""),
        )
        with _LOCK:
            JOBS[job_id]["package_id"] = exam_package.package_id
            JOBS[job_id]["exam_package"] = exam_package.model_dump(mode="json")
        _persist_job(job_id)
        sink = payload.get("sink")
        # What every copy of the batch is graded against (grading_engine.grade_copy).
        batch = {
            "cfg": cfg, "qp_pngs": qp_pngs, "ak_pngs": ak_pngs, "ak_text": ak_text,
            "marks_scheme": marks_scheme, "student_class": payload["student_class"],
            "subject": payload["subject"], "rate": payload["rate"],
            "total_marks": payload.get("total_marks"),
            "qp_text": _with_marking_settings(qp_text, cfg),
            "ak_text_layer": ak_text_layer,
        }
        workers = max(1, min(payload.get("workers") or _BATCH_WORKERS,
                             len(payload["sheets"])))
        engine = None
        processes = grading_engine.plan_processes(workers) if sink is not None else 1
        if processes > 1:
            # Folder mode at more than a few copies at a time: grade in worker
            # processes. If they cannot start, grade here as before.
            try:
                engine = grading_engine.EnginePool(
                    batch, processes, math.ceil(workers / processes))
                ready = engine.wait_ready()
                print(f"[batch] {ready} of {processes} grading processes ready, "
                      f"{math.ceil(workers / processes)} copies at a time each", flush=True)
                if not ready:
                    engine.close()
                    engine = None
                    print("[batch] grading processes did not start; grading in this "
                          "process", flush=True)
            except Exception as exc:  # noqa: BLE001 - the run goes on in this process
                _log_error("grading processes could not start; grading in this process", exc)
                if engine is not None:
                    engine.close()
                engine = None

        def grade_one(index: int, name: str, sa_bytes: bytes | None) -> None:
            """Grade one sheet and file its result. Safe to run concurrently."""
            if sink is not None and _bulk_stopping(job_id):
                return  # not started, so not counted: the next run picks it up
            flow = AutomaticWorkflow.from_snapshot(setup_flow.snapshot())

            def save_sheet(snapshot: dict) -> None:
                with _LOCK:
                    if job_id in JOBS:
                        JOBS[job_id]["workflows"][index] = snapshot
                        JOBS[job_id]["current"] = (
                            f"{name} · {stage_label(snapshot['state'])}"
                        )
                # Folder mode files every result on disk the moment it is ready, so
                # a stage label can wait a moment; an upload saves every change.
                if sink is not None:
                    _persist_progress(job_id)
                else:
                    _persist_job(job_id)

            flow.on_change = save_sheet
            save_sheet(flow.snapshot())

            # Progress display only, and it cannot fail a copy — one shared
            # implementation so a test can exercise the very function that runs here.
            stage_changed = safe_stage_reporter(flow)

            with _LOCK:
                JOBS[job_id]["current"] = name
            pdf_bytes = saved_report = cover = None
            try:
                if sink is not None and sink.segment_problem(name):
                    raise StrictGradeError("BOOKLET_SPLIT_REVIEW", sink.segment_problem(name))
                if sa_bytes is None and sink is not None:
                    # Folder mode: read from disk only now, so 10,000 copies never
                    # sit in memory together.
                    sa_bytes = sink.load(name)
                    _validate_document(name, sa_bytes, name,
                                       extensions=_sheet_extensions(cfg.category))
                    earlier = sink.claim(name, sa_bytes)
                    if earlier:
                        raise StrictGradeError(
                            "DUPLICATE_UPLOAD", f"Same file as “{earlier}”, graded once.")
                if not sa_bytes:
                    raise ValueError("file came through empty (0 bytes)")
                if payload.get("read_covers"):
                    from vidyapeeth_intake import cover_details
                    cover = cover_details(sa_bytes, name, google_token=cfg.google_token,
                                          task_id=cfg.task_id)
                if engine is not None:
                    result_data = engine.grade(name, sa_bytes, stage_changed)
                else:
                    result_data = grading_engine.grade_copy(
                        batch, name, sa_bytes, stage_changed,
                        orienter=_make_orienter, grade=grade_sheet)
                report, cost = result_data["report"], result_data["cost"]
                # Backstop only: grade_prepared already forces the denominator onto the
                # official total, so this should never fire — it exists so a regression
                # there shows up in the results table instead of passing as a clean score.
                official = payload.get("total_marks")
                warning = ""
                if official is not None and abs(report.max_total - float(official)) > 0.25:
                    warning = (
                        f"Graded out of {report.max_total:g} but official total is "
                        f"{official:g} — review before trusting this score."
                    )
                # The free release gate ran inside grade_sheet and returned its verdict.
                blocked = result_data.get("blocking") or []
                if blocked:
                    found = "; ".join(i.detail for i in blocked[:3])
                    if len(blocked) > 3:
                        found += f"; and {len(blocked) - 3} more"
                    warning = ((warning + " ") if warning else "") + (
                        f"Sent for faculty review ({len(blocked)} reason(s)): {found}"
                    )
                # A released copy can still carry things worth a look — a mark that
                # grazes the writing, a row the second examiner could not certify, an
                # MCQ read from the page rather than confirmed twice. They no longer
                # hold the copy, so this row is the only place the teacher meets them.
                held = {id(issue) for issue in blocked}
                advisories = [issue for issue in (result_data.get("issues") or [])
                              if id(issue) not in held]
                if advisories:
                    shown = "; ".join(str(issue.detail) for issue in advisories[:3])
                    if len(advisories) > 3:
                        shown += f"; and {len(advisories) - 3} more"
                    warning = ((warning + " ") if warning else "") + (
                        f"{len(advisories)} point(s) to check: {shown}")
                # Over-budget is a fact about the copy the operator should see in the
                # table, not only in a log line nobody reads after the run.
                ceiling = (result_data.get("budget") or {}).get("cap_inr")
                if ceiling is not None and cost.total_inr > ceiling:
                    warning = ((warning + " ") if warning else "") + (
                        f"Cost Rs.{cost.total_inr:.1f} against a Rs.{ceiling:g} ceiling."
                    )
                if key_warnings:
                    # The batch's key was corrected from its own solutions; every copy
                    # marked against it says so.
                    warning = ((warning + " ") if warning else "") + (
                        "Answer key corrected: " + " ".join(key_warnings))
                # A booklet that cannot be matched to its student waits for a person.
                cover_problems = list((cover or {}).get("problems") or [])
                released = result_data.get("release") is True and not cover_problems
                result = {
                    "index": index,
                    "student": name,
                    "ok": True,
                    # A copy the gate would not send to the student is NOT lost: it is
                    # marked, its PDF is kept, and it goes to a FACULTY REVIEWER with
                    # the reasons (operator's rule, 2026-09-19). `release` stays the
                    # student-facing verdict the download endpoints honour.
                    "release": released,
                    "review": not released,
                    "review_reasons": [i.detail for i in blocked] + cover_problems,
                    "held_reasons": [i.detail for i in blocked] + cover_problems,
                    "warning": warning,
                    "student_info": (cover or {}).get("info") or {},
                    "cover_problems": cover_problems,
                    # The total the student reads: whole marks on a CA paper.
                    "score": _shown_total(report),
                    "score_exact": report.total_score,
                    "max": report.max_total,
                    "percent": round(_shown_total(report) / report.max_total * 100, 1)
                    if report.max_total
                    else 0,
                    "remarks": report.overall_remarks,
                    "questions": [
                        {
                            "q": question.qid,
                            "score": question.score,
                            "max": question.max_score,
                            "remark": question.remark or "",
                        }
                        for question in report.questions
                    ],
                    "cost": _cost_dict(cost),
                    "model": cost.model or cfg.model,
                    "state": "released" if released else "faculty_review",
                    "package_id": result_data.get("package_id"),
                    "budget": result_data.get("budget") or {},
                    "verification": {
                        "complete": bool(result_data.get("verification_complete")),
                        "questions": result_data.get("verification_qids") or [],
                    },
                    "answer_map": result_data.get("answer_map"),
                }
                if result["release"]:
                    flow.transition(WorkflowState.RELEASED, "All automatic release checks passed")
                else:
                    flow.faculty_review(
                        "FACULTY_REVIEW",
                        ("; ".join(result["review_reasons"][:4])
                         or "The automatic release gate did not pass."),
                    )
                result["workflow"] = flow.snapshot()
                pdf_bytes = result_data["pdf_bytes"]
                saved_report = {
                    "report": report.model_dump(mode="json"),
                    "ocr": [mathpix.pageocr_to_dict(page)
                            for page in (result_data.get("ocr_pages") or [])],
                }
                if sink is None:
                    with _LOCK:
                        JOBS[job_id]["pdfs"][index] = pdf_bytes
                        JOBS[job_id]["reports"][index] = saved_report
            except StrictGradeError as exc:
                _log_error(f"automatic grading could not verify {name}", exc)
                if exc.code in _REJECTED_UPLOAD_CODES:
                    # NOT a genuine copy for this test: typed or printed pages,
                    # study notes, an unrelated document, a blank or unreadable
                    # scan, a booklet written for another paper. It is REJECTED
                    # before any grading money is spent and goes to nobody: not to
                    # the student and not into the faculty queue, which is for
                    # marked copies with a real question over them.
                    if flow.state not in TERMINAL_STATES:
                        flow.ungradable(exc.code, str(exc))
                    result = {
                        "index": index, "student": name, "ok": False,
                        "release": False, "rejected": True, "state": "rejected",
                        "failure_code": exc.code, "error": str(exc),
                        "workflow": flow.snapshot(),
                    }
                elif exc.code in _BATCH_FAULT_CODES:
                    if flow.state not in TERMINAL_STATES:
                        flow.fail(exc.code, str(exc))
                    result = {
                        "index": index, "student": name, "ok": False,
                        "release": False, "state": "failed",
                        "failure_code": exc.code, "error": str(exc),
                        "workflow": flow.snapshot(),
                    }
                    if sink is not None:
                        with _LOCK:
                            bulk_state = JOBS[job_id]["bulk"]
                            if not bulk_state["stopping"]:
                                bulk_state["stopping"] = True
                                bulk_state["stopped_reason"] = (
                                    "Stopped: the question paper's own check failed while "
                                    f"marking ({exc}). This is not about any one copy. "
                                    "Fix the cause, then press Start: finished copies are "
                                    "skipped.")
                else:
                    # A genuine copy that could not be marked automatically. There
                    # is no PDF, so this is a review request with the reason.
                    if flow.state not in TERMINAL_STATES:
                        flow.faculty_review(exc.code, str(exc))
                    result = {
                        "index": index, "student": name, "ok": False,
                        "release": False, "review": True, "state": "faculty_review",
                        "review_reasons": [str(exc)], "held_reasons": [str(exc)],
                        "failure_code": exc.code, "error": str(exc),
                        "workflow": flow.snapshot(),
                    }
            except Exception as exc:
                _log_error(f"grading failed for {name}", exc)
                if flow.state not in TERMINAL_STATES:
                    flow.fail("PIPELINE_FAILED", str(exc))
                result = {
                    "index": index, "student": name, "ok": False,
                    "release": False, "state": "failed", "error": str(exc),
                    "workflow": flow.snapshot(),
                }
            if cover and not result.get("student_info"):
                result["student_info"] = cover.get("info") or {}
            if sink is not None:
                _file_bulk_result(job_id, sink, name, result, pdf_bytes, saved_report)
            else:
                with _LOCK:
                    JOBS[job_id]["results"].append(result)
                    JOBS[job_id]["done"] += 1
            _persist_job(job_id)
            payload["sheets"][index] = (name, b"" if sink is None else None)
            _trim_memory()

        sheets = list(enumerate((n, b) for n, b in payload["sheets"]))
        try:
            # Grade the first sheet alone so it writes the shared paper/key/prompt
            # prefix to cache. The remaining concurrent calls can then read it at the
            # lower rate.
            if sheets:
                first_index, (first_name, first_bytes) = sheets[0]
                grade_one(first_index, first_name, first_bytes)
            rest = sheets[1:]
            if rest:
                # In one process the copies are mostly waiting on the model, so threads
                # suit them; pdf_renderer serialises its own work behind _RENDER_LOCK,
                # and every JOBS mutation above is under _LOCK. With the engine each
                # thread waits for a worker process instead.
                in_flight = max(1, min(workers, len(rest)))
                print(f"[batch] grading {len(rest)} remaining sheet(s), "
                      f"{in_flight} at a time", flush=True)
                ramp = (_RAMP_SECONDS if sink is not None
                        and in_flight > grading_engine.SINGLE_PROCESS_UP_TO else 0.0)

                def start_one(slot: int, index: int, name: str, data) -> None:
                    if ramp and slot < in_flight:
                        time.sleep(slot * ramp)
                    grade_one(index, name, data)

                with ThreadPoolExecutor(max_workers=in_flight) as pool:
                    futures = [pool.submit(start_one, slot, i, n, b)
                               for slot, (i, (n, b)) in enumerate(rest)]
                    for f in futures:
                        f.result()
        finally:
            if engine is not None:
                engine.close()
        with _LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["current"] = ""
            results = JOBS[job_id]["results"]
            bulk_state = JOBS[job_id].get("bulk")
        if bulk_state is not None:
            with _LOCK:
                counts = bulk_state["counts"]
                JOBS[job_id]["state"] = ("stopped" if bulk_state["stopping"]
                                         else "failed" if counts["failed"] else "done")
            if JOBS[job_id].get("export") and sink is not None:
                _write_results_sheet(sink.target)
            _persist_job(job_id)
            return
        with _LOCK:
            JOBS[job_id]["state"] = (
                "released" if results and all(r.get("release") is True for r in results)
                else "faculty_review" if any(r.get("state") == "faculty_review" for r in results)
                else "rejected" if any(r.get("state") == "rejected" for r in results)
                else "failed"
            )
        _persist_job(job_id)
    except StrictGradeError as exc:
        # The paper, key or marks graph could not be validated, so no copy in the
        # batch can be marked automatically. The whole batch is a faculty-review
        # request with the reason; nothing is silently dropped.
        _log_error("exam package could not be validated", exc)
        # MEASURED 2026-10-03 (Parishram Physics, a scanned key): the package step
        # cannot move to faculty_review (package_validating allows only validated,
        # ungradable or failed), so faculty_review raised INSIDE this handler, the
        # sibling "except Exception" below never runs for it, the job thread died and
        # the folder run showed "running, reading the marks scheme" for ever.
        if setup_flow.state not in TERMINAL_STATES:
            try:
                setup_flow.faculty_review(exc.code, str(exc))
            except RuntimeError:
                setup_flow.ungradable(exc.code, str(exc))
        if payload.get("sink") is not None:
            with _LOCK:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["state"] = "failed"
                JOBS[job_id]["current"] = ""
                JOBS[job_id]["error"] = f"{exc.code}: {exc}"
            _persist_job(job_id)
            return
        results = [
            {
                "index": index, "student": name, "ok": False, "release": False,
                "review": True, "state": "faculty_review", "review_reasons": [str(exc)],
                "held_reasons": [str(exc)], "failure_code": exc.code, "error": str(exc),
                "workflow": setup_flow.snapshot(),
            }
            for index, (name, _) in enumerate(payload.get("sheets") or [])
        ]
        with _LOCK:
            results += [row for row in JOBS[job_id]["results"]
                        if row.get("failure_code") == "UNREADABLE_FILE"]
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["state"] = "faculty_review"
            JOBS[job_id]["results"] = results
            JOBS[job_id]["done"] = len(results)
            JOBS[job_id]["current"] = ""
            JOBS[job_id]["error"] = f"{exc.code}: {exc}"
        _persist_job(job_id)
    except Exception as exc:
        _log_error("grading job failed", exc)
        if setup_flow.state not in (WorkflowState.UNGRADABLE, WorkflowState.FAILED,
                                    WorkflowState.RELEASED):
            setup_flow.fail("JOB_FAILED", str(exc))
        with _LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["state"] = "failed"
            JOBS[job_id]["error"] = str(exc)
        _persist_job(job_id)


def _run_design_job(payload: dict) -> None:
    """Mark a batch of design copies (design_grading.py). Same job record, same
    three outcomes and the same downloads as a written batch."""
    import design_grading as dg
    job_id = payload["job_id"]
    setup_flow = AutomaticWorkflow.from_snapshot(JOBS[job_id].get("workflow"))

    def latest(snapshot: dict) -> str:
        events = snapshot.get("events") or [{}]
        return events[-1].get("detail") or stage_label(snapshot["state"])

    def save_setup(snapshot: dict) -> None:
        with _LOCK:
            if job_id in JOBS:
                JOBS[job_id]["workflow"] = snapshot
                JOBS[job_id]["state"] = snapshot["state"]
                JOBS[job_id]["current"] = latest(snapshot)
        _persist_job(job_id)

    setup_flow.on_change = save_setup
    try:
        setup_flow.transition(WorkflowState.PACKAGE_VALIDATING,
                              "Reading the design paper and building the marking rubric")
        paper, context = _read_design_paper_cached(
            payload["qp_bytes"], payload["qp_name"], payload.get("ak_bytes"),
            payload.get("ak_name"), google_token=payload["google_token"],
            task_id=payload["task_id"])
        paper = dg.resolve_maxima(paper, payload.get("question_marks") or "",
                                  payload.get("total_marks"))
        setup_flow.transition(
            WorkflowState.PACKAGE_VALIDATED,
            f"{len(paper.marked)} question(s), {paper.max_total:g} marks")
        with _LOCK:
            JOBS[job_id]["design_paper"] = paper.model_dump(mode="json")
        _persist_job(job_id)
        cache_ttl = _job_cache_ttl(payload)

        def grade_one(index: int, name: str, sa_bytes: bytes) -> None:
            flow = AutomaticWorkflow.from_snapshot(setup_flow.snapshot())

            def save_sheet(snapshot: dict) -> None:
                with _LOCK:
                    if job_id in JOBS:
                        JOBS[job_id]["workflows"][index] = snapshot
                        JOBS[job_id]["current"] = f"{name} · {latest(snapshot)}"
                _persist_job(job_id)

            flow.on_change = save_sheet
            save_sheet(flow.snapshot())
            try:
                if not sa_bytes:
                    raise ValueError("file came through empty (0 bytes)")
                data = dg.grade_design_sheet(
                    paper=paper.model_copy(deep=True), context=context, sa_bytes=sa_bytes,
                    filename=name, google_token=payload["google_token"],
                    task_id=payload["task_id"], cache_ttl=cache_ttl,
                    usd_to_inr=payload["rate"], stage=safe_stage_reporter(flow))
                report, cost = data["report"], data["cost"]
                released = data["release"] is True
                reasons = list(data["review_reasons"])
                result = {
                    "index": index, "student": name, "ok": True, "mode": "design",
                    "release": released, "review": not released,
                    "review_reasons": reasons, "held_reasons": reasons, "warning": "",
                    "score": report.total_score, "max": report.max_total,
                    "percent": round(report.total_score / report.max_total * 100, 1)
                    if report.max_total else 0,
                    "remarks": report.overall,
                    "questions": [
                        {"q": a.qid, "score": a.score, "max": a.max_marks,
                         "remark": a.feedback if a.attempted else "Not attempted."}
                        for a in report.answers
                    ],
                    "cost": _cost_dict(cost), "model": cost.model,
                    "state": "released" if released else "faculty_review",
                    "budget": {"actual_inr": cost.total_inr, "enforced": False,
                               "cap_inr": None, "rates_found": cost.rates_found,
                               "usage_attested": cost.usage_attested},
                }
                if released:
                    flow.transition(WorkflowState.RELEASED, "Marked and released")
                else:
                    flow.faculty_review("FACULTY_REVIEW", " ".join(reasons[:3]))
                result["workflow"] = flow.snapshot()
                with _LOCK:
                    JOBS[job_id]["pdfs"][index] = data["pdf_bytes"]
                    JOBS[job_id]["reports"][index] = {
                        "report": report.model_dump(mode="json"),
                        "turns": data.get("turns"), "look": data.get("look"),
                        # Every statement the second examiner checked, with its verdict:
                        # the audit trail for "why did the feedback change".
                        "second_look": data.get("second_look"),
                    }
            except StrictGradeError as exc:
                _log_error(f"design copy refused: {name}", exc)
                if exc.code in _REJECTED_UPLOAD_CODES:
                    if flow.state not in TERMINAL_STATES:
                        flow.ungradable(exc.code, str(exc))
                    result = {"index": index, "student": name, "ok": False, "release": False,
                              "rejected": True, "state": "rejected", "failure_code": exc.code,
                              "error": str(exc), "workflow": flow.snapshot()}
                else:
                    if flow.state not in TERMINAL_STATES:
                        flow.faculty_review(exc.code, str(exc))
                    result = {"index": index, "student": name, "ok": False, "release": False,
                              "review": True, "state": "faculty_review",
                              "review_reasons": [str(exc)], "held_reasons": [str(exc)],
                              "failure_code": exc.code, "error": str(exc),
                              "workflow": flow.snapshot()}
            except Exception as exc:
                _log_error(f"design marking failed for {name}", exc)
                if flow.state not in TERMINAL_STATES:
                    flow.fail("PIPELINE_FAILED", str(exc))
                result = {"index": index, "student": name, "ok": False, "release": False,
                          "state": "failed", "error": str(exc), "workflow": flow.snapshot()}
            with _LOCK:
                JOBS[job_id]["results"].append(result)
                JOBS[job_id]["done"] += 1
            _persist_job(job_id)
            payload["sheets"][index] = (name, b"")
            _trim_memory()

        sheets = list(enumerate((n, b) for n, b in payload["sheets"]))
        # The first copy alone, so it writes the shared paper and rubric to cache.
        if sheets:
            first_index, (first_name, first_bytes) = sheets[0]
            grade_one(first_index, first_name, first_bytes)
        rest = sheets[1:]
        if rest:
            workers = max(1, min(_BATCH_WORKERS, len(rest)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for future in [pool.submit(grade_one, i, n, b) for i, (n, b) in rest]:
                    future.result()
        with _LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["current"] = ""
            results = JOBS[job_id]["results"]
            JOBS[job_id]["state"] = (
                "released" if results and all(r.get("release") is True for r in results)
                else "faculty_review" if any(r.get("state") == "faculty_review" for r in results)
                else "rejected" if any(r.get("state") == "rejected" for r in results)
                else "failed"
            )
        _persist_job(job_id)
    except Exception as exc:
        # The paper could not be read, or the marks entered do not fit it. That is
        # for the operator to correct, not for faculty: nothing was marked.
        _log_error("design job could not start", exc)
        if setup_flow.state not in TERMINAL_STATES:
            setup_flow.fail(str(getattr(exc, "code", "") or "JOB_FAILED"), str(exc))
        with _LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["state"] = "failed"
            JOBS[job_id]["error"] = str(exc)
        _persist_job(job_id)


# A finished batch holds every evaluated PDF in memory (tens of MB). Without eviction a
# long-lived instance is OOM-killed, which surfaces to users as "Lost the grading job".
_JOB_TTL_SECONDS = 3600
_MAX_JOBS = 20


def _evict_jobs() -> None:
    """Drop finished jobs older than the TTL, then cap the registry to the newest N.
    Callers hold _LOCK."""
    now = time.time()
    finished = [
        (job.get("created", 0.0), job_id)
        for job_id, job in JOBS.items()
        if job.get("status") in ("done", "error")
    ]
    for created, job_id in finished:
        if now - created > _JOB_TTL_SECONDS:
            JOBS.pop(job_id, None)
            try:
                _JOB_REPOSITORY.delete(job_id)
            except Exception:
                traceback.print_exc()
    if len(JOBS) > _MAX_JOBS:
        for _, job_id in sorted(
            (job.get("created", 0.0), job_id) for job_id, job in JOBS.items()
        )[: len(JOBS) - _MAX_JOBS]:
            if JOBS.get(job_id, {}).get("status") in ("done", "error"):
                JOBS.pop(job_id, None)
                try:
                    _JOB_REPOSITORY.delete(job_id)
                except Exception:
                    traceback.print_exc()


def _job_or_404(job_id: str, key: str = "") -> dict:
    """Look up a job, enforcing its download key.

    Results (evaluated student answer sheets) must not be readable by anyone who
    learns a job id, so each job carries a random key issued only to the client that
    created it. The key travels in the URL because the PDF/ZIP downloads are plain
    <a href> links that cannot carry an Authorization header — a random per-job token
    is used rather than the user's identity token, which must never end up in a URL.
    """
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id (it may have expired after a restart).")
    expected = job.get("key") or ""
    if expected and not _keys_match(expected, key):
        raise HTTPException(403, "This job belongs to a different session.")
    return job


def _keys_match(expected: str, supplied: str) -> bool:
    import hmac

    return hmac.compare_digest(str(expected), str(supplied or ""))


def _safe_export_stem(filename: str, fallback: str = "sheet") -> str:
    stem = os.path.basename(str(filename or "")).rsplit(".", 1)[0]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:80]
    return safe or fallback


def _export_stems(job: dict) -> dict[int, str]:
    """Each marked copy's file name in this job: the student's name and roll number
    when the cover was read (Vidyapeeth), else the uploaded file's name; one name
    per copy even when two covers read the same."""
    return result_export.unique_stems([
        (result["index"], result_export.copy_stem(
            result.get("student_info"),
            _safe_export_stem(result["student"], f"sheet{result['index']}")))
        for result in sorted(job["results"], key=lambda item: item["index"])])


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, k: str = ""):
    job = _job_or_404(job_id, k)
    public_results = []
    for internal in sorted(job["results"], key=lambda result: result["index"]):
        if job.get("bulk") is not None:
            public_results.append(internal)
            continue
        if internal.get("release") is True or internal.get("state") == "faculty_review":
            # A copy for the student, or a marked copy for a faculty reviewer: both
            # carry their marks, reasons and (when one exists) a downloadable PDF.
            # `release` stays False on a review copy, so the UI labels it as such.
            public_results.append(internal)
            continue
        public_results.append({
            "index": internal.get("index"),
            "student": internal.get("student", ""),
            "ok": False,
            "held": True,
            "release": False,
            "state": internal.get("state") or "failed",
            "error": internal.get("warning") or internal.get("error")
                     or "Automatic grading failed for this copy.",
            "budget": internal.get("budget") or {},
            "package_id": internal.get("package_id"),
        })
    return JSONResponse(
        {
            "status": job["status"],
            "total": job["total"],
            "done": job["done"],
            "current": job.get("current", ""),
            "error": job.get("error"),
            "state": job.get("state", ""),
            "workflow": job.get("workflow"),
            "package_id": job.get("package_id"),
            "key_warnings": list(job.get("key_warnings") or []),
            "results": public_results,
            "bulk": _bulk_status(job),
        }
    )


def _bulk_status(job: dict) -> dict | None:
    state = job.get("bulk")
    if state is None:
        return None
    elapsed = max(1.0, time.time() - float(state.get("started") or time.time()))
    done, total = int(job.get("done") or 0), int(job.get("total") or 0)
    left = max(0, total - done)
    return {
        "source": state["source"], "target": state["target"],
        "part": state["part"], "parts": state["parts"],
        "already_done": state["already_done"], "counts": dict(state["counts"]),
        "cost_inr": state["cost_inr"], "stopping": state["stopping"],
        "stopped_reason": state["stopped_reason"],
        # From this run's own pace once a few copies are in; none before that.
        "eta_hours": round(elapsed / done * left / 3600, 1) if done >= 3 else None,
    }


@app.get("/api/jobs/{job_id}/pdf/{index}")
def job_pdf(job_id: str, index: int, k: str = ""):
    job = _job_or_404(job_id, k)
    result = next(
        (item for item in job["results"] if item.get("index") == index), None
    )
    if result is None:
        raise HTTPException(404, "No result for that sheet.")
    review = result.get("state") == "faculty_review"
    if result.get("release") is not True and not review:
        raise HTTPException(
            409,
            "This copy was not marked and cannot be downloaded.",
        )
    pdf = job["pdfs"].get(index)
    if pdf is None:
        raise HTTPException(404, "No evaluated PDF for that sheet.")
    name = next(
        (result["student"] for result in job["results"] if result["index"] == index),
        f"sheet{index}",
    )
    stem = _export_stems(job).get(index) or _safe_export_stem(name, f"sheet{index}")
    # The file name says where the copy goes: a review copy is never mistaken
    # for one that may be handed to the student.
    prefix = "FACULTY_REVIEW_" if review else "evaluated_"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{prefix}{stem}.pdf"'},
    )


@app.get("/api/jobs/{job_id}/report/{index}")
def job_report(job_id: str, index: int, k: str = ""):
    """The graded report and cached OCR for one sheet, for a free re-render.

    Deliberately NOT part of the status payload: that is polled every two seconds
    for the life of the batch, and a report runs to hundreds of kilobytes.
    """
    job = _job_or_404(job_id, k)
    saved = (job.get("reports") or {}).get(index)
    if saved is None:
        raise HTTPException(404, "No saved report for that sheet.")
    return saved


@app.get("/api/jobs/{job_id}/zip")
def job_zip(job_id: str, k: str = ""):
    """Every marked copy, sorted into two folders: `to_students/` for the copies
    that passed every automatic release check and `faculty_review/` for the ones a
    reviewer must look at first (each with a REASONS text file beside it)."""
    job = _job_or_404(job_id, k)
    if not job["pdfs"]:
        raise HTTPException(404, "No evaluated PDFs yet.")
    buffer = io.BytesIO()
    released = review = 0
    stems = _export_stems(job)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in sorted(job["results"], key=lambda item: item["index"]):
            pdf = job["pdfs"].get(result["index"])
            if not pdf:
                continue
            stem = stems[result["index"]]
            if result.get("release") is True:
                archive.writestr(f"to_students/evaluated_{stem}.pdf", pdf)
                released += 1
            elif result.get("state") == "faculty_review":
                archive.writestr(f"faculty_review/REVIEW_{stem}.pdf", pdf)
                reasons = list(result.get("review_reasons") or [])
                archive.writestr(f"faculty_review/REVIEW_{stem}_reasons.txt",
                                 "\n".join(reasons) or "Automatic verification asked for a review.")
                review += 1
        if job.get("export"):
            export = job["export"]
            rows = []
            for result in sorted(job["results"], key=lambda item: item["index"]):
                stem = stems[result["index"]]
                link = (f"to_students/evaluated_{stem}.pdf" if result.get("release") is True
                        else f"faculty_review/REVIEW_{stem}.pdf"
                        if result.get("state") == "faculty_review" else "")
                rows.append(result_export.row_for(
                    result, source=result["student"], report_link=link,
                    facts=export.get("facts") or {},
                    student_class=export.get("student_class", ""),
                    subject=export.get("subject", ""), batch=export.get("batch") or {}))
            archive.writestr(result_export.XLSX_NAME, result_export.xlsx_bytes(rows))
        # The model answer key the copies were marked against, when the tool wrote it.
        written = job.get("generated_key") or {}
        if written.get("path") and os.path.exists(written["path"]):
            archive.write(written["path"], written.get("filename") or "AI_MODEL_ANSWER_KEY.pdf")
    if released == 0 and review == 0:
        raise HTTPException(409, "No copy in this job was marked.")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="evaluated_sheets.zip"'},
    )


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# Reuse each run's verified marks while its server-owned proof is available.
# Successful proofs have no time limit; a bounded store may still evict a record,
# in which case the next copy recovers it instead of reusing a missing token.
_WORKFLOW_MARKS: dict = {}
_WORKFLOW_MARKS_LOCKS: dict = {}
_WORKFLOW_MARKS_GUARD = threading.Lock()


def _workflow_marks_key(folder):
    paper = Path(folder) / "question_paper.pdf"
    stat = paper.stat()
    return (str(paper.resolve()), stat.st_mtime_ns, stat.st_size)


def _workflow_marks_lock(key):
    with _WORKFLOW_MARKS_GUARD:
        return _WORKFLOW_MARKS_LOCKS.setdefault(key, threading.Lock())


def _workflow_marks_context(options, google_token):
    """The exam context _queue_evaluation binds a marks proof to, for a workflow run's options."""
    category, _ = _category_or_400(options["category"], "")
    level = _effective_level("", options["student_class"], category)
    cfg = cfg_from_request(
        "claude", "", options["subject"], google_token=google_token,
        task_id=pw_access.new_task_id("workflow-marks"), student_class=level,
        exam_type=categories.get(category).legacy_exam_type, total_marks=float(options["total_marks"]),
        category=category)
    return _marks_extraction_context(cfg, level, options["subject"])


def _workflow_operator_marks(paper_bytes, options, google_token):
    """Marks a person entered for a paper whose printed marks are wrong or unreadable
    (integrated workflow, build 137). They pass the same checks as a reading of the paper
    and must add up to the official total after the choice rules; then they are bound to
    this paper and exam like a verified reading. Raises ValueError saying what to fix."""
    from grader import parse_strict_marks_scheme
    entered = options.get("operator_marks") or {}
    raw = {
        "items": [{"qid": row["qid"], "max": row["max"], "description": row.get("part", "")}
                  for row in entered.get("items") or []],
        "choice_groups": [{"group_id": f"choice-{number}", "select_count": int(choice["select"]),
                           "options": [{"option_id": f"o{index}", "question_ids": list(option)}
                                       for index, option in enumerate(choice["options"], 1)]}
                          for number, choice in enumerate(entered.get("choices") or [], 1)],
    }
    try:
        scheme = parse_strict_marks_scheme(json.dumps(raw))
        effective = effective_marks_total(scheme)
    except Exception as exc:  # noqa: BLE001 - every reason is the person's to fix
        raise ValueError(f"The marks entered for this paper cannot be used: {exc}") from exc
    total = float(options["total_marks"])
    if abs(effective - total) > 0.01:
        raise ValueError(f"The marks entered add up to {effective:g}"
                         + (" after the choice rules" if scheme.choice_groups else "")
                         + f", but the official total is {total:g}. Make them agree.")
    try:
        attestation = _MARKS_ATTESTATIONS.create(
            paper_bytes, total, scheme, extractors=(f"operator:{entered.get('by') or 'workflow'}",),
            context=_workflow_marks_context(options, google_token))
    except MarksAttestationError as exc:
        raise ValueError(f"The marks entered for this paper cannot be used: {exc}") from exc
    return {"items": [{"qid": item.qid, "max": float(item.max), "part": item.description}
                      for item in attestation.scheme.items],
            "marks_attestation": attestation.token, "method": "operator"}


def _workflow_free_reading(paper_bytes, options):
    """The paper's own section headers or printed tags as (scheme, method), when they give
    exactly the official total, else None. Free, and the same every time."""
    category, _ = _category_or_400(options["category"], "")
    total = float(options["total_marks"])
    try:
        scheme, method = detect_marks_scheme(
            paper_bytes, "question_paper.pdf", None,
            student_class=_effective_level("", options["student_class"], category),
            subject=options["subject"], total_marks=total,
            exam_type=categories.get(category).legacy_exam_type)
        if scheme is None or method not in ("regex", "sections"):
            return None
        return (scheme, method) if abs(effective_marks_total(scheme) - total) <= 0.01 else None
    except Exception as exc:  # noqa: BLE001 - only a check on the AI reading
        print(f"[marks] the paper's own marks could not be read: {exc}", flush=True)
        return None


def _marks_by_question(items):
    """{question number: marks}, the parts of a question added up; None for a row without one."""
    marks = {}
    for item in items or []:
        number = re.match(r"\s*Q?\s*(\d+)", str(item["qid"]), re.IGNORECASE)
        if number is None:
            return None
        marks[number.group(1)] = round(marks.get(number.group(1), 0) + float(item["max"]), 4)
    return marks


async def _workflow_read_marks(paper_bytes, options, exam_type, category, authorization, google_token):
    """AI reads every workflow paper (operator, 30 Oct 2026: tests stopped on marks, and three
    that day stopped though their section headers gave the total exactly, as a free reading
    never earned the proof a run needs). The free reading stays, for what AI alone got wrong:
    MEASURED, four AI readings of a 70-mark CBSE Chemistry paper gave 83, 81, 78 and 73 (see
    pipeline.detect_marks_scheme). It takes over when the AI reading fails, and when the AI
    reading gives a question other marks than the paper's own headers. Only a choice paper
    is not compared: its rows add up to more than one student can score."""
    detected = await detect_marks(
        question_paper=UploadFile(filename="question_paper.pdf", file=io.BytesIO(paper_bytes)),
        provider="claude", model="", student_class=options["student_class"],
        subject=options["subject"], exam_type=exam_type, category=category,
        total_marks=options["total_marks"], mode="ai_first", authorization=authorization)
    free = await run_in_threadpool(_workflow_free_reading, paper_bytes, options)
    if free is None:
        return detected
    scheme, method = free
    if detected.get("marks_attestation"):
        by_question = _marks_by_question(detected.get("items"))
        rows = [{"qid": item.qid, "max": float(item.max)} for item in scheme.items]
        if detected.get("choice_groups") or by_question is None or by_question == _marks_by_question(rows):
            return detected
        print(f"[marks] the AI reading gives other marks than the paper's own {method} reading; "
              f"using the paper's: AI {by_question}", flush=True)
    else:
        print(f"[marks] no AI reading ({detected.get('verification_detail') or detected.get('method')}); "
              f"using the paper's own {method} reading", flush=True)
    try:
        attestation = _MARKS_ATTESTATIONS.create(
            paper_bytes, float(options["total_marks"]), scheme, extractors=(f"paper:{method}",),
            context=_workflow_marks_context(options, google_token))
    except MarksAttestationError as exc:
        print(f"[marks] the paper's own reading has no proof: {exc}", flush=True)
        return detected
    return _marks_response(attestation.scheme, "paper_verified", detected.get("paper_total"), attestation=attestation,
                           verification_detail="Read from the paper's own printed marks; they match the official total.")


_MARKS_SERVICE_FAILURES = ("extractor_unavailable", "extractor_rate_limited")   # gone by themselves: worth waiting for


def _workflow_check_paper(paper_bytes, options, authorization):
    """Before a workflow run touches the portal: can this paper's marks be read, and do they
    add up to the official total? The same reading grading uses (cached, so the first copy
    does not pay for it again), or the marks a person entered. A reading that failed for a
    passing reason (the proxy) is tried up to three times. Returns {"ok": True, ...} or
    {"ok": False, "retry", "message", "items", "choice_groups", "paper_total"}; "retry" says
    the AI service failed, not the paper, so the same check can pass later."""
    import asyncio

    async def check():
        google_token = await run_in_threadpool(_require_allowed, authorization)
        if options.get("operator_marks"):
            try:
                detected = await run_in_threadpool(_workflow_operator_marks, paper_bytes, options, google_token)
            except ValueError as exc:
                entered = options["operator_marks"]
                return {"ok": False, "message": str(exc), "items": entered.get("items") or [],
                        "choice_groups": [], "operator_choices": entered.get("choices") or [], "paper_total": None}
            return {"ok": True, "method": "operator", "items": detected["items"]}
        category, exam_type = _category_or_400(options["category"], "")
        detected = {}
        for attempt in range(3):
            try:
                detected = await _workflow_read_marks(paper_bytes, options, exam_type, category, authorization, google_token)
            except HTTPException as exc:
                if exc.status_code in (401, 403):
                    raise
                return {"ok": False, "message": str(exc.detail), "items": [], "choice_groups": [], "paper_total": None}
            if (detected.get("marks_attestation") or detected.get("failure_code") not in _MARKS_SERVICE_FAILURES
                    or attempt == 2):
                break
            await asyncio.sleep(min(30, max(2, int(detected.get("retry_after_seconds") or 0))))
        if detected.get("marks_attestation"):
            return {"ok": True, "method": detected.get("method"), "items": detected.get("items") or [],
                    "note": detected.get("verification_detail") if detected.get("marks_decided") else ""}
        return {"ok": False, "retry": detected.get("failure_code") in _MARKS_SERVICE_FAILURES,
                "message": detected.get("verification_detail")
                           or "The question paper's marks could not be read and checked against the official total.",
                "items": detected.get("items") or [], "choice_groups": detected.get("read_choice_groups") or [],
                "paper_total": detected.get("paper_total")}

    return asyncio.run(check())


def _workflow_cache_ttl(options: dict) -> str:
    """The cache TTL for one workflow copy: 5 minutes while other copies of the test
    are still to be checked, none for the last one (nothing would read it back).

    5 minutes, not the 1 hour a big upload buys: the workflow checks several copies of
    the test at once, so the shared prefix is read again within a minute or two, and
    each read restarts the five minutes. The 1-hour write costs 2x the input price,
    the 5-minute one 1.25x. A run that does not say (an older caller) is not cached."""
    try:
        others = int(options.get("copies_to_check") or 1) - 1
    except (TypeError, ValueError):
        others = 0
    return "5m" if others > 0 else ""


def _workflow_grade(folder, source, options, authorization):
    """Run the existing paper attestation and release gates for one portal copy."""
    import asyncio

    def upload(path):
        return UploadFile(filename=path.name, file=io.BytesIO(path.read_bytes()))

    async def grade():
        google_token = await run_in_threadpool(_require_allowed, authorization)
        category, exam_type = _category_or_400(options["category"], "")
        key = (_workflow_marks_key(folder), category, exam_type,
               " ".join(options["student_class"].split()).casefold(),
               " ".join(options["subject"].split()).casefold(), float(options["total_marks"]),
               json.dumps(options.get("operator_marks") or {}, sort_keys=True))

        async def verified_marks():
            # Each copy has its own event loop/thread. This lock makes concurrent
            # copies share one reading, including recovery after store eviction.
            with _workflow_marks_lock(key):
                detected = _WORKFLOW_MARKS.get(key)
                if detected is not None and not _MARKS_ATTESTATIONS.is_available(detected.get("marks_attestation")):
                    _WORKFLOW_MARKS.pop(key, None)
                    detected = None
                if detected is None and options.get("operator_marks"):
                    detected = await run_in_threadpool(
                        _workflow_operator_marks, (folder / "question_paper.pdf").read_bytes(), options, google_token)
                    _WORKFLOW_MARKS[key] = detected
                if detected is None:
                    detected = await _workflow_read_marks(
                        (folder / "question_paper.pdf").read_bytes(), options, exam_type, category,
                        authorization, google_token)
                    if detected.get("marks_attestation"):
                        _WORKFLOW_MARKS[key] = detected
                if not detected.get("marks_attestation"):
                    raise ValueError(detected.get("verification_detail") or "Question marks could not be verified")
                return detected

        for attempt in range(2):
            detected = await verified_marks()
            background = BackgroundTasks()
            try:
                queued = await _queue_evaluation(
                    background, google_token=google_token, task_id=pw_access.new_task_id("workflow"),
                    qp_bytes=(folder / "question_paper.pdf").read_bytes(), qp_name="question_paper.pdf",
                    sheets=[(source.name, source.read_bytes())],
                    # A PW Skills lesson with no solution on the portal: the job writes its own key.
                    answer_key=upload(folder / "answer_key.pdf") if (folder / "answer_key.pdf").exists() else None,
                    step_rubric="", provider="claude", model="", student_class=options["student_class"],
                    subject=options["subject"], exam_type=exam_type, total_marks=options["total_marks"],
                    exam_level="", paper_code="", exam_attempt="", applicable_law_date="",
                    key_source="upload" if (folder / "answer_key.pdf").exists() else "generate",
                    marks_items=json.dumps(detected["items"]), marks_source=detected.get("method", "ai_verified"),
                    marks_attestation=detected["marks_attestation"], usd_to_inr="", category=category,
                    printed_total_override=detected.get("method") == "operator",
                    cache_ttl=_workflow_cache_ttl(options))
                break
            except HTTPException as exc:
                # A proof can be evicted between lookup and queue validation. That
                # exact error happens before a job is created; retry it once only.
                # Background grading is outside this block and is never repeated.
                if (attempt or exc.status_code != 400 or
                        exc.detail != "The verified marks proof is missing or expired; detect marks again."):
                    raise
                with _workflow_marks_lock(key):
                    current = _WORKFLOW_MARKS.get(key)
                    if current is not None and current.get("marks_attestation") == detected["marks_attestation"]:
                        _WORKFLOW_MARKS.pop(key, None)
        await background()
        job = _job_or_404(queued["job_id"], queued["download_key"])
        result = next(iter(job.get("results") or []), {})
        index = result.get("index", 0)
        outcome = {
            "release": result.get("release") is True,
            # REJECTED (not an answer sheet, another paper, a duplicate) is its own
            # workflow stage: never uploaded, never queued for faculty.
            "rejected": result.get("rejected") is True or result.get("state") == "rejected",
            # Why it was rejected or held (WRONG_QUESTION_PAPER, NOT_AN_ANSWER_SHEET...):
            # the workflow stops a test whose copies keep coming back as another paper.
            "failure_code": result.get("failure_code") or "",
            "pdf": (job.get("pdfs") or {}).get(index),
            "report": copy.deepcopy(((job.get("reports") or {}).get(index) or {}).get("report")),
            "error": result.get("error") or result.get("warning") or job.get("error")
                     or "; ".join(result.get("review_reasons") or []),
            # Every reason in full: the workflow works out its confidence from them.
            "review_reasons": list(result.get("review_reasons") or []),
            # The check itself broke (no marks because of an error, not a review decision):
            # the workflow tries the copy again and then skips it.
            "failed": result.get("state") == "failed" or (not result and bool(job.get("error"))),
        }
        # This job was created only as the workflow's grading bridge. Its result
        # now belongs to the workflow; retaining another PDF in JOBS and SQLite
        # would keep consuming memory and disk after the copy's files are cleaned.
        # Serialize against persistence so a late snapshot cannot recreate it.
        try:
            with _PERSIST_LOCK:
                _JOB_REPOSITORY.delete(queued["job_id"])
                with _LOCK:
                    JOBS.pop(queued["job_id"], None)
        except Exception as exc:
            # Keep the registry entry for normal expiry to retry the deletion.
            # A locked database must never turn successful checking into a retry.
            print(f"[workflow] could not remove internal job {queued['job_id']}: {exc}",
                  file=sys.stderr, flush=True)
        return outcome

    return asyncio.run(grade())


import integrated_workflow


def _default_workflow_dir() -> Path:
    """Next to the app: workflow_data, or pw_skills_data for the PW Skills tool, so the two
    never share saved runs when they sit in one folder."""
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    return here / ("pw_skills_data" if APP_MODE == "skills" else "workflow_data")


_WORKFLOW_STORE = integrated_workflow.install(
    app, _require_allowed, _workflow_grade,
    Path(os.environ.get("AI_CHECKER_WORKFLOW_DIR", "").strip() or str(_default_workflow_dir())),
    paper_checker=_workflow_check_paper)

import skills_workflow

_SKILLS_STORE = skills_workflow.install(
    app, _require_allowed, _WORKFLOW_STORE.grade, _WORKFLOW_STORE.root / "pw_skills", paper_checker=_workflow_check_paper)

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
