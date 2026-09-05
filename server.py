"""FastAPI backend used by both the desktop EXE and hosted web build.

Every main action requires a signed-in PW Google token and fails closed against
the PW app allowlist. All provider work is delegated to pw_access helpers.
"""
from __future__ import annotations

import ctypes
import base64
import copy
import dataclasses
import gc
import io
import json
import math
import os
import re
import sys
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import mathpix
import pw_access
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from costs import DEFAULT_USD_TO_INR, budget_for
from automatic_workflow import AutomaticWorkflow, WorkflowState, stage_label
from exam_package import ChoiceGroup, build_exam_package, normalize_qid
from marks_attestation import (
    ATTESTATION_VERSION,
    MarksAttestation,
    MarksAttestationError,
    MarksAttestationStore,
    effective_marks_total,
    marks_authority_scheme,
    marks_rows_agree,
    schemes_agree,
)
from grader import (
    MarksScheme,
    detect_exam_type,
    marks_scheme_from_items,
    marks_scheme_from_pdf,
    paper_total_from_text,
    pdf_or_image_to_pngs,
    paper_has_choice,
    pdf_text_layer,
    scheme_contradicts_paper,
)
from pipeline import (
    CLAUDE,
    GEMINI,
    ProviderConfig,
    _BUDGET_GRADING_MODEL,
    _LARGE_PAPER_GRADING_MODEL,
    _cache_ttl_for_batch,
    cfg_from_request,
    detect_marks_scheme,
    generate_rubric,
    grade_sheet,
)
from job_store import JobRepository
from strict_grading import StrictGradeError

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
STATIC_DIR = os.path.join(HERE, "static")
BUILD_ID = "2026.09.04.2-objective-feedback-layout-release"
API_PROTOCOL_VERSION = 5

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
async def _no_stale_local_ui(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/index.html", "/app.js", "/styles.css"}:
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


def _validate_document(filename: str | None, data: bytes, label: str) -> None:
    name = str(filename or "").strip()
    extension = os.path.splitext(name.lower())[1]
    if extension not in _DOCUMENT_EXTENSIONS:
        raise HTTPException(400, f"{label} must be PDF, PNG, JPG, or JPEG.")
    if not data:
        raise HTTPException(400, f"{label} came through empty; re-upload it.")
    if len(data) > _MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"{label} exceeds the 60 MB per-file limit.")
    signatures = {
        ".pdf": data.startswith(b"%PDF-"),
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
    }
    if not signatures.get(extension, False):
        raise HTTPException(400, f"{label} content does not match its {extension} filename.")


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


def _require_allowed(authorization: str | None) -> str:
    token = _google_token(authorization)
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
    }


@app.get("/api/config")
def config():
    return {
        "app_name": pw_access.APP_NAME,
        "build_id": BUILD_ID,
        "protocol_version": API_PROTOCOL_VERSION,
        "google_client_id": _oauth_client_id(),
        "default_usd_to_inr": DEFAULT_USD_TO_INR,
    }


@app.get("/api/auth/status")
def auth_status(authorization: str | None = Header(default=None)):
    _require_allowed(authorization)
    return {"allowed": True, "app": pw_access.APP_NAME}


def _marks_verifier_config(primary: ProviderConfig) -> ProviderConfig:
    """Use a different provider family while preserving the task boundary."""
    if primary.provider == CLAUDE:
        return dataclasses.replace(
            primary, provider=GEMINI, model="gemini-2.5-flash")
    return dataclasses.replace(
        primary, provider=CLAUDE, model=_BUDGET_GRADING_MODEL)


def _marks_extraction_context(
    primary: ProviderConfig,
    student_class: str,
    subject: str,
) -> str:
    verifier = _marks_verifier_config(primary)
    return json.dumps({
        "version": ATTESTATION_VERSION,
        "exam_type": (primary.exam_type or "").strip().lower(),
        "student_class": " ".join((student_class or "").split()).casefold(),
        "subject": " ".join((subject or "").split()).casefold(),
        "extractors": [
            f"{primary.provider}:{primary.model}",
            f"{verifier.provider}:{verifier.model}",
        ],
    }, sort_keys=True, separators=(",", ":"))


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
    if method == "ai_verified":
        return
    print(f"[marks] not verified ({method}): {detail}", file=sys.stderr, flush=True)
    print(f"[marks]   primary : {_rows_for_log(primary)}", file=sys.stderr, flush=True)
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
        "choice_detected": bool(
            choice_detected or getattr(scheme, "choice_groups", None)),
        "choice_verified": bool(attestation is not None and verified_groups),
        "choice_groups": [group.model_dump(mode="json") for group in verified_groups],
        "effective_total": effective_total,
    }


@app.post("/api/detect-marks")
async def detect_marks(
    question_paper: UploadFile = File(...),
    provider: str = Form("gemini"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    exam_type: str = Form(""),
    total_marks: str = Form(""),
    # "auto" = free text-layer reading only, never an AI call (used on upload);
    # "ai" = full extraction with the AI fallback (the explicit button).
    mode: str = Form("ai"),
    authorization: str | None = Header(default=None),
):
    google_token = _require_allowed(authorization)
    task_id = pw_access.new_task_id("detect-marks")
    try:
        paper_total = float(total_marks) if total_marks.strip() else None
    except ValueError as exc:
        raise HTTPException(400, "Total marks must be a number.") from exc
    data = await question_paper.read()
    _validate_document(question_paper.filename, data, "Question paper")
    paper_text = pdf_text_layer(data, question_paper.filename)
    printed_total = paper_total_from_text(paper_text)
    official_total = paper_total if paper_total is not None else printed_total
    explicit_ai = (mode or "").strip().lower() != "auto"
    if (paper_total is not None and printed_total is not None
            and abs(float(paper_total) - float(printed_total)) > 0.01):
        raise HTTPException(
            400,
            f"Entered total {paper_total:g} conflicts with the paper header "
            f"({printed_total:g}).",
        )
    cfg = None if not explicit_ai else cfg_from_request(
        # Production verification uses one fixed, costed pair. Client-supplied model
        # names cannot upgrade this setup action to an arbitrary expensive model.
        GEMINI,
        "",
        subject,
        google_token=google_token,
        task_id=task_id,
        student_class=student_class,
        exam_type=exam_type,
        total_marks=official_total,
    )
    # This is a routing/evidence hint only. Exam type and ordinary prose must never
    # become a permanent choice lock; authority comes from the matched full graph.
    choice_detected = paper_has_choice(paper_text, "")
    text_evidence_usable = _paper_text_layer_complete(data, question_paper.filename)
    marks_context = (
        _marks_extraction_context(cfg, student_class, subject)
        if cfg is not None else ""
    )

    # Re-clicking the button for the same bytes and total must not buy two more calls.
    if explicit_ai and official_total is not None and text_evidence_usable:
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
                verification_detail="Cached Claude/Gemini agreement for this exact paper.",
                choice_detected=choice_detected,
            )
    try:
        scheme, method = detect_marks_scheme(
            data,
            question_paper.filename,
            cfg,
            student_class or None,
            subject or None,
            total_marks=official_total,
            require_complete=explicit_ai,
        )
    except Exception as exc:
        # AI extraction failed — log the real cause, then fall back to the printed-tag
        # reading so the teacher still gets an editable table instead of nothing.
        _log_error("detect-marks AI extraction failed", exc)
        scheme = marks_scheme_from_pdf(data, question_paper.filename)
        if scheme is None and cfg is not None:
            raise HTTPException(500, f"Could not detect marks: {exc}") from exc
        method = "regex_fallback"
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
        return _marks_response(
            scheme, method, printed_total, choice_detected=choice_detected)
    if method != "ai":
        return _marks_response(
            scheme,
            method,
            printed_total,
            verification_detail="The first extraction did not pass its paper checks.",
            choice_detected=choice_detected,
        )
    if not text_evidence_usable:
        return _marks_response(
            scheme,
            "ai_unverified_evidence",
            printed_total,
            verification_detail=(
                "The paper has no sufficiently complete text layer for strict "
                "question-by-question verification."
            ),
        )
    if official_total is None:
        return _marks_response(
            scheme,
            "ai_unverified_total",
            printed_total,
            verification_detail="No entered or printed official total was available to bind the graph.",
            choice_detected=choice_detected,
        )
    if choice_detected and not list(getattr(scheme, "choice_groups", None) or []):
        return _marks_response(
            scheme,
            "ai_choice_unverified",
            printed_total,
            verification_detail=(
                "The paper prints an internal-choice instruction, but the first "
                "provider did not return its complete alternatives."
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
                "Fixed questions plus the extracted choice rules do not exactly "
                "equal the official paper total."
            ),
            choice_detected=choice_detected,
        )

    # Both paid calls are one logical task and therefore deliberately share task_id.
    verifier_cfg = _marks_verifier_config(cfg)
    try:
        verifier_scheme, verifier_method = detect_marks_scheme(
            data,
            question_paper.filename,
            verifier_cfg,
            student_class or None,
            subject or None,
            total_marks=official_total,
            require_complete=True,
        )
    except Exception as exc:
        _log_error("detect-marks independent verification failed", exc)
        return _marks_response(
            scheme,
            "ai_unverified",
            printed_total,
            verification_detail="The independent provider could not verify the graph.",
            choice_detected=choice_detected,
        )
    if verifier_method != "ai" or not schemes_agree(scheme, verifier_scheme):
        return _marks_response(
            scheme,
            "ai_disagreement",
            printed_total,
            verification_detail=(
                "Claude and Gemini did not return the same complete question/choice graph."
                if verifier_method == "ai" else
                f"The verifying provider answered from '{verifier_method}', not its own "
                f"independent reading."
            ),
            choice_detected=choice_detected,
            verifier_scheme=verifier_scheme,
        )
    try:
        attestation = _MARKS_ATTESTATIONS.create(
            data,
            official_total,
            scheme,
            verifier_scheme,
            extractors=(
                f"{cfg.provider}:{cfg.model}",
                f"{verifier_cfg.provider}:{verifier_cfg.model}",
            ),
            context=marks_context,
        )
    except MarksAttestationError as exc:
        return _marks_response(
            scheme, "ai_unverified", printed_total,
            verification_detail=str(exc), choice_detected=choice_detected)
    return _marks_response(
        attestation.scheme,
        "ai_verified",
        printed_total,
        attestation=attestation,
        verification_detail=(
            (
                "Claude and Gemini independently agreed after equivalent parent and "
                "sub-part rows were safely combined."
                if not marks_rows_agree(scheme, verifier_scheme)
                else "Claude and Gemini independently returned the same complete graph"
                + (" and internal-choice rules."
                   if getattr(attestation.scheme, "choice_groups", None) else ".")
            )
        ),
        choice_detected=choice_detected,
    )


@app.post("/api/generate-rubric")
async def gen_rubric(
    question_paper: UploadFile = File(...),
    provider: str = Form("gemini"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    exam_type: str = Form(""),
    authorization: str | None = Header(default=None),
):
    google_token = _require_allowed(authorization)
    task_id = pw_access.new_task_id("generate-rubric")
    data = await question_paper.read()
    _validate_document(question_paper.filename, data, "Question paper")
    cfg = cfg_from_request(
        provider,
        model,
        subject,
        google_token=google_token,
        task_id=task_id,
        student_class=student_class,
        exam_type=exam_type,
    )
    try:
        pages = pdf_or_image_to_pngs(data, question_paper.filename)
        rubric = generate_rubric(
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
    provider: str = Form("gemini"),
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
    authorization: str | None = Header(default=None),
):
    google_token = _require_allowed(authorization)
    task_id = pw_access.new_task_id("evaluate")
    # Strict automatic release is evidence-bound. A booklet without its exact paper
    # and official key is not merely lower confidence; it is a different exam package.
    if question_paper is None:
        raise HTTPException(400, "Upload the official question paper.")
    qp_bytes = await question_paper.read()
    qp_name = question_paper.filename
    _validate_document(qp_name, qp_bytes, "Question paper")
    sheets = [(file.filename, await file.read()) for file in answer_sheets]
    if not sheets:
        raise HTTPException(400, "Upload at least one answer sheet.")
    for index, (name, data) in enumerate(sheets, start=1):
        _validate_document(name, data, f"Answer sheet {index}")
    if sum(len(data) for _, data in sheets) > _MAX_BATCH_BYTES:
        raise HTTPException(413, "Answer-sheet batch exceeds the 300 MB upload limit.")

    ak_bytes = ak_name = None
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
    if (printed_paper_total is not None
            and abs(float(printed_paper_total) - float(paper_total)) > 0.25):
        raise HTTPException(
            400,
            f"Entered total {paper_total:g} conflicts with the official paper header "
            f"({printed_paper_total:g}). Automatic grading will not override the paper.",
        )
    if not (subject or "").strip():
        raise HTTPException(400, "Subject/paper name is required for automatic grading.")
    effective_level = (exam_level or student_class or "").strip()
    if not effective_level:
        raise HTTPException(400, "Class or CA level is required.")
    requested_exam_type = (exam_type or "").strip().lower()
    resolved_exam_type = (requested_exam_type if requested_exam_type in
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
    )
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
                "The question/maxima graph has not been independently verified, so "
                "grading was not started. Click Detect max marks from question paper "
                "once more; Claude and Gemini must agree before a score can be released.",
            )
    cfg = dataclasses.replace(
        cfg,
        provider=CLAUDE,
        # Strict release must also leave room for its independent verifier.  Under
        # the Rs.15/Rs.20 hard caps Sonnet's worst-case reservation cannot do that;
        # Haiku is the measured budget-feasible primary and Sonnet remains the
        # challenger.  Legacy/diagnostic routes still use size-based selection.
        model=_LARGE_PAPER_GRADING_MODEL,
        grading_contract="exam_package_v2",
        budget_cap_inr=budget_for(
            paper_total, exam_type=cfg.exam_type, subject=subject),
        # The strict route budgets one bounded primary call plus selective verification.
        # Window fan-out is useful diagnostically but cannot guarantee a per-copy cap.
        fanout=False,
    )
    # The attested graph, not exam type or a lexical "OR", decides whether this paper
    # contains scorable alternatives.
    has_choice = bool(submitted_choice_groups)

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
        ),
    )
    return {"job_id": job_id, "download_key": download_key}


# A digital PDF yields roughly 1,500-3,000 characters of text per page; a scan yields
# almost none. Same threshold pipeline._prefer_text_over_images uses to decide a text
# layer really covers the pages.
_DIGITAL_CHARS_PER_PAGE = 600


def _is_digital_pdf(data: bytes, filename: str) -> bool:
    """True when this file carries a real text layer, so it was generated, not photographed.

    A generated PDF is upright in its own coordinate space by construction — there is
    no camera angle to correct — so spending a model call to ask which way up it is
    buys nothing. Students photograph their sheets and genuinely need the probe; the
    question paper and the answer key are digital almost every time.
    """
    if not (filename or "").lower().endswith(".pdf"):
        return False
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
        return pages > 0 and len(pdf_text_layer(data, filename).strip()) >= (
            _DIGITAL_CHARS_PER_PAGE * pages)
    except Exception:
        return False


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


def _make_orienter(cfg: ProviderConfig, usage_out: dict | None = None):
    from grader_gemini import orientation_probe
    from orient import normalize_orientation, split_two_page_spreads

    def orient(data: bytes, filename: str) -> bytes:
        def probe(pages):
            return orientation_probe(
                pages,
                model="gemini-2.5-flash",
                google_token=cfg.google_token,
                task_id=cfg.task_id,
                filename=filename,
                usage_out=usage_out,
            )

        # MEASURED on the CA Inter Taxation set: the question paper is 14 pages and the
        # answer key 21, both digital (34,723 and 39,482 characters of text), and both
        # were rasterised at 150 dpi and sent to the orientation model on every single
        # copy — 35 page renders and 2 model calls per student, to straighten pages that
        # were never crooked and that the grader then reads as TEXT, not images.
        # normalize_orientation falls back to its free local heuristic when probe is
        # None, so a rotated digital page is still handled; it just costs nothing.
        # Scanned student pages need the four-way visual probe even in strict mode.
        # The free projection heuristic rotated two already-upright ruled notebook
        # pages (270 and 180 degrees) in a released copy. Digital paper/key PDFs stay
        # free because their text layer proves the page coordinate system is upright.
        use_probe = None if _is_digital_pdf(data, filename) else probe
        try:
            # A photograph of the OPEN notebook is two facing pages in one image.
            # Split it FIRST, so each half is then judged for rotation on its own —
            # and so OCR stops interleaving the left page with the right one. A
            # digital PDF is never a spread, so it is not even looked at.
            if not _is_digital_pdf(data, filename):
                data, split = split_two_page_spreads(data, filename)
                if split:
                    print(f"[orient] {filename or 'sheet'}: two-page spread(s) split "
                          f"into single pages", flush=True)
            return normalize_orientation(data, filename, probe=use_probe)[0]
        except Exception:
            return data

    return orient


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
        cfg = dataclasses.replace(
            cfg, cache_ttl=_cache_ttl_for_batch(len(payload["sheets"])))
        orient = _make_orienter(cfg)
        # The paper's text layer pins the printed "(N Marks)" tags to their questions —
        # it goes into every grading call so maxima can't drift. It was extracted from
        # the same bytes in the request handler, so it is passed through, not redone.
        qp_text = payload.get("qp_text", "")
        qp_bytes = orient(payload["qp_bytes"], payload["qp_name"])
        qp_pngs = pdf_or_image_to_pngs(qp_bytes, payload["qp_name"])
        ak_bytes = orient(payload["ak_bytes"], payload["ak_name"])
        ak_pngs = pdf_or_image_to_pngs(ak_bytes, payload["ak_name"])
        ak_text = None
        # The solution's text layer (digital PDFs have one) feeds the moderation
        # pass, which verifies deductions against the key without any images.
        ak_text_layer = pdf_text_layer(payload["ak_bytes"], payload["ak_name"])
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
        )
        package_blockers = exam_package.blockers()
        if package_blockers:
            raise StrictGradeError(
                "INVALID_EXAM_PACKAGE",
                "; ".join(issue.detail for issue in package_blockers[:5]),
            )
        cfg = dataclasses.replace(cfg, exam_package=exam_package)
        setup_flow.transition(
            WorkflowState.PACKAGE_VALIDATED,
            f"Exam package {exam_package.package_id[:12]} validated",
        )
        with _LOCK:
            JOBS[job_id]["package_id"] = exam_package.package_id
            JOBS[job_id]["exam_package"] = exam_package.model_dump(mode="json")
        _persist_job(job_id)
        def grade_one(index: int, name: str, sa_bytes: bytes) -> None:
            """Grade one sheet and file its result. Safe to run concurrently."""
            flow = AutomaticWorkflow.from_snapshot(setup_flow.snapshot())

            def save_sheet(snapshot: dict) -> None:
                with _LOCK:
                    if job_id in JOBS:
                        JOBS[job_id]["workflows"][index] = snapshot
                        JOBS[job_id]["current"] = (
                            f"{name} · {stage_label(snapshot['state'])}"
                        )
                _persist_job(job_id)

            flow.on_change = save_sheet
            save_sheet(flow.snapshot())

            def stage_changed(state: str, detail: str = "") -> None:
                target = WorkflowState(state)
                if flow.state != target:
                    flow.transition(target, detail)

            with _LOCK:
                JOBS[job_id]["current"] = name
            try:
                if not sa_bytes:
                    raise ValueError("file came through empty (0 bytes)")
                orientation_usage: dict = {}
                oriented = _make_orienter(cfg, orientation_usage)(sa_bytes, name)
                result_data = grade_sheet(
                    qp_pngs=qp_pngs,
                    sa_bytes=oriented,
                    filename=name,
                    ak_pngs=ak_pngs,
                    ak_text=ak_text,
                    marks_scheme=marks_scheme,
                    cfg=cfg,
                    student_class=payload["student_class"],
                    subject=payload["subject"],
                    use_mathpix=True,
                    usd_to_inr=payload["rate"],
                    total_marks=payload.get("total_marks"),
                    qp_text=qp_text,
                    ak_text_layer=ak_text_layer,
                    stage_callback=stage_changed,
                    prior_usage=orientation_usage,
                )
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
                        f"Automatic release found {len(blocked)} blocking issue(s); "
                        f"no copy was released: {found}"
                    )
                # Over-budget is a fact about the copy the operator should see in the
                # table, not only in a log line nobody reads after the run.
                ceiling = budget_for(
                    official, exam_type=cfg.exam_type,
                    subject=payload.get("subject") or "")
                if cost.total_inr > ceiling:
                    warning = ((warning + " ") if warning else "") + (
                        f"Cost Rs.{cost.total_inr:.1f} against a Rs.{ceiling:g} ceiling."
                    )
                result = {
                    "index": index,
                    "student": name,
                    "ok": True,
                    # A blocked copy is NOT released. The results table used to show it
                    # with a warning string beside an otherwise normal-looking score,
                    # which reads as advice; "held" is a state, and the batch download
                    # honours it.
                    "release": result_data.get("release") is True,
                    "held_reasons": [i.detail for i in blocked],
                    "warning": warning,
                    "score": report.total_score,
                    "max": report.max_total,
                    "percent": round(report.total_score / report.max_total * 100, 1)
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
                    "state": "released" if result_data.get("release") is True else "ungradable",
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
                    flow.ungradable(
                        "RELEASE_BLOCKED",
                        ("; ".join(result["held_reasons"][:4])
                         or "The automatic release gate did not pass."),
                    )
                result["workflow"] = flow.snapshot()
                with _LOCK:
                    JOBS[job_id]["pdfs"][index] = result_data["pdf_bytes"]
                    JOBS[job_id]["reports"][index] = {
                        "report": report.model_dump(mode="json"),
                        "ocr": [mathpix.pageocr_to_dict(page)
                                for page in (result_data.get("ocr_pages") or [])],
                    }
            except StrictGradeError as exc:
                _log_error(f"automatic grading could not verify {name}", exc)
                if flow.state not in (WorkflowState.UNGRADABLE, WorkflowState.FAILED,
                                      WorkflowState.RELEASED):
                    flow.ungradable(exc.code, str(exc))
                result = {
                    "index": index, "student": name, "ok": False,
                    "release": False, "state": "ungradable",
                    "failure_code": exc.code, "error": str(exc),
                    "workflow": flow.snapshot(),
                }
            except Exception as exc:
                _log_error(f"grading failed for {name}", exc)
                if flow.state not in (WorkflowState.UNGRADABLE, WorkflowState.FAILED,
                                      WorkflowState.RELEASED):
                    flow.fail("PIPELINE_FAILED", str(exc))
                result = {
                    "index": index, "student": name, "ok": False,
                    "release": False, "state": "failed", "error": str(exc),
                    "workflow": flow.snapshot(),
                }
            with _LOCK:
                JOBS[job_id]["results"].append(result)
                JOBS[job_id]["done"] += 1
            _persist_job(job_id)
            payload["sheets"][index] = (name, b"")
            _trim_memory()

        sheets = list(enumerate((n, b) for n, b in payload["sheets"]))
        # Grade the first sheet alone so it writes the shared paper/key/prompt prefix
        # to cache. The remaining concurrent calls can then read it at the lower rate.
        if sheets:
            first_index, (first_name, first_bytes) = sheets[0]
            grade_one(first_index, first_name, first_bytes)
        rest = sheets[1:]
        if rest:
            # Grading is almost entirely waiting on the model, so threads are the
            # right tool and the GIL is not in the way. pdf_renderer serialises its
            # own work behind _RENDER_LOCK, and every JOBS mutation above is already
            # under _LOCK.
            workers = max(1, min(_BATCH_WORKERS, len(rest)))
            print(f"[batch] grading {len(rest)} remaining sheet(s), "
                  f"{workers} at a time", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(grade_one, i, n, b) for i, (n, b) in rest]
                for f in futures:
                    f.result()
        with _LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["current"] = ""
            results = JOBS[job_id]["results"]
            JOBS[job_id]["state"] = (
                "released" if results and all(r.get("release") is True for r in results)
                else "ungradable" if any(r.get("state") == "ungradable" for r in results)
                else "failed"
            )
        _persist_job(job_id)
    except StrictGradeError as exc:
        _log_error("exam package could not be validated", exc)
        if setup_flow.state not in (WorkflowState.UNGRADABLE, WorkflowState.FAILED,
                                    WorkflowState.RELEASED):
            setup_flow.ungradable(exc.code, str(exc))
        results = [
            {
                "index": index, "student": name, "ok": False, "release": False,
                "state": "ungradable", "failure_code": exc.code, "error": str(exc),
                "workflow": setup_flow.snapshot(),
            }
            for index, (name, _) in enumerate(payload.get("sheets") or [])
        ]
        with _LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["state"] = "ungradable"
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


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, k: str = ""):
    job = _job_or_404(job_id, k)
    public_results = []
    for internal in sorted(job["results"], key=lambda result: result["index"]):
        if internal.get("release") is True:
            public_results.append(internal)
            continue
        # A held mark is not a student result. Keeping it in the status payload made
        # the UI an alternate release channel even though PDF/ZIP downloads correctly
        # denied the copy. Preserve only operational state and the hold reason.
        public_results.append({
            "index": internal.get("index"),
            "student": internal.get("student", ""),
            "ok": False,
            "held": True,
            "release": False,
            "error": internal.get("warning") or internal.get("error")
                     or "Automatic verification held this copy.",
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
            "results": public_results,
        }
    )


@app.get("/api/jobs/{job_id}/pdf/{index}")
def job_pdf(job_id: str, index: int, k: str = ""):
    job = _job_or_404(job_id, k)
    result = next(
        (item for item in job["results"] if item.get("index") == index), None
    )
    if result is None:
        raise HTTPException(404, "No result for that sheet.")
    if result.get("release") is not True:
        raise HTTPException(
            409,
            "This copy did not pass automatic verification and cannot be downloaded.",
        )
    pdf = job["pdfs"].get(index)
    if pdf is None:
        raise HTTPException(404, "No evaluated PDF for that sheet.")
    name = next(
        (result["student"] for result in job["results"] if result["index"] == index),
        f"sheet{index}",
    )
    stem = _safe_export_stem(name, f"sheet{index}")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="evaluated_{stem}.pdf"'},
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
    """Download only copies that passed every automatic release check."""
    job = _job_or_404(job_id, k)
    if not job["pdfs"]:
        raise HTTPException(404, "No evaluated PDFs yet.")
    buffer = io.BytesIO()
    released = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in sorted(job["results"], key=lambda item: item["index"]):
            pdf = job["pdfs"].get(result["index"])
            if not pdf:
                continue
            stem = _safe_export_stem(result["student"], f"sheet{result['index']}")
            if result.get("release") is True:
                archive.writestr(f"evaluated_{stem}.pdf", pdf)
                released += 1
    if released == 0:
        raise HTTPException(409, "No copy in this job passed automatic verification.")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="evaluated_sheets.zip"'},
    )


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
