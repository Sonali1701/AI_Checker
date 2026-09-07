"""
pw_access.py - PW App Kit v2 client for Python backends/local apps.

Copy this file into an app, set APP_NAME, and route all paid provider calls
through the shared PW proxy. The app ships no provider keys. The proxy verifies
the signed-in PW user, checks the app whitelist, makes the provider call with
proxy-held keys, and writes trusted raw usage logs to MongoDB.

Raw logging model:
  - One successful proxy/provider call = one raw row in MongoDB/sheet export.
  - For multi-call tasks, create one task_id and pass it to every helper call.
  - Combine later in Sheets/AppScript by task_id + app + email + model.
  - Do not use client-side combined logging for normal AI calls.

Everywhere a google_token is accepted you may pass either:
  - the token string from login, or
  - a zero-arg function returning a fresh Google token.

The kit automatically exchanges the Google token for a proxy-issued 7-day
session pass, so long runs do not fail just because Google's token expires.
"""

import hashlib
import os
import random
import time
import uuid
from typing import Any, Dict, List, Optional

import requests


# ---------------------------------------------------------------------------
# Per-app config. APP_NAME must exactly match a header in Whitelisted row 1.
# ---------------------------------------------------------------------------
APP_NAME = "AI Copy Checker"

PROXY_BASE_URL = os.environ.get(
    "PW_PROXY_BASE_URL", "https://pw-apps-proxy.vercel.app"
).rstrip("/")

_TIMEOUT = 30
_AI_TIMEOUT = 300
_LARGE_REQUEST_BYTES = 3_500_000


class PWAccessError(Exception):
    """Raised when a paid proxy/provider call fails."""


def new_task_id(prefix: str = "task") -> str:
    """Create a stable id for one user task/run."""
    safe = "".join(ch for ch in str(prefix or "task") if ch.isalnum() or ch in "-_")
    return f"{safe or 'task'}-{uuid.uuid4().hex}"


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _resolve_token(google_token) -> str:
    return google_token() if callable(google_token) else google_token


_sessions: Dict[str, Dict[str, Any]] = {}


def _auth_token(google_token, force_new: bool = False) -> str:
    """Return this user's cached 7-day proxy pass, or mint one from Google.

    The kit is used by a multi-user backend here, so proxy sessions must never be
    shared between signed-in users. The cache key is a one-way digest of the Google
    token; raw Google tokens are not retained as dictionary keys.
    """
    import time

    g = _resolve_token(google_token)
    cache_key = hashlib.sha256(str(g or "").encode("utf-8")).hexdigest()
    session = _sessions.get(cache_key, {"token": "", "expiry": 0.0})
    if not force_new and session["token"] and time.time() < session["expiry"] - 60:
        return session["token"]

    try:
        r = requests.post(
            f"{PROXY_BASE_URL}/api/session",
            headers=_headers(g),
            json={},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            token = data.get("session_token") or ""
            if token:
                _sessions[cache_key] = {
                    "token": token,
                    "expiry": float(data.get("expires_at_ms") or 0) / 1000.0,
                }
                return token
    except Exception:
        pass
    return g


# Paid calls are retried only when the proxy explicitly says they were rejected before
# execution. A generic 5xx, connection reset, or timeout is billing-ambiguous: the
# provider may already have run, so replaying it can create duplicate raw ledger rows
# and break the hard per-copy cap.
# 429 belongs here: OCR fires several pages at once, so rate limiting is a NORMAL
# response to our own concurrency, not a server fault. Without it a throttled page
# raised straight out of the pool and that page lost every mark anchor on it — the
# one failure mode that gets worse the more we parallelise.
# 529 is Anthropic's "overloaded_error" — the upstream model is at capacity. Like 429 it
# means the request was REJECTED, never run, so retrying it cannot bill a grading call
# twice; and like 429 it clears on its own. Losing a copy to one of these throws away
# every OCR page already paid for and every minute already spent, so these two get a
# longer, jittered schedule than a 5xx: one bad hop usually clears immediately, an
# overload needs real time to pass. Reported from the field as
# "claude proxy error 529: overloaded_error" killing a sheet outright.
_CAPACITY_STATUSES = frozenset((429, 529))
_RETRY_STATUSES = _CAPACITY_STATUSES
_RETRY_ATTEMPTS = 5
_RETRY_BACKOFF_SECONDS = 1.5
_CAPACITY_BACKOFF_SECONDS = 5.0
_MAX_BACKOFF_SECONDS = 60.0


def _retry_after(r) -> float | None:
    """The server's own instruction on when to come back, when it gives one."""
    if r is None:
        return None
    # Anything can come back here — a requests.Response, a stub, an object with no
    # headers at all. Never let reading an optional hint break the retry that is trying
    # to save the copy.
    try:
        value = float((getattr(r, "headers", None) or {}).get("Retry-After", ""))
    except (AttributeError, TypeError, ValueError):
        return None
    return max(0.0, min(value, _MAX_BACKOFF_SECONDS))


def _backoff_seconds(r, attempt: int) -> float:
    """How long to wait before attempt+1, honouring Retry-After when the server sends it.

    Jittered, because OCR fires several pages concurrently: without it every page in the
    wave backs off for exactly the same interval and they all collide again on the
    retry, which is how a rate limit turns into a rate limit loop.
    """
    told = _retry_after(r)
    if told is not None:
        return told
    status = getattr(r, "status_code", None)
    base = (_CAPACITY_BACKOFF_SECONDS if status in _CAPACITY_STATUSES
            else _RETRY_BACKOFF_SECONDS)
    delay = base * (2 ** attempt) * (0.75 + random.random() * 0.5)
    # Clamp AFTER jittering: capping first let the +25% push a "60s maximum" wait to 75s.
    return min(delay, _MAX_BACKOFF_SECONDS)


def _post_once(path: str, google_token, payload: Dict[str, Any], timeout: int,
               force_new: bool = False):
    return requests.post(
        f"{PROXY_BASE_URL}{path}",
        headers=_headers(_auth_token(google_token, force_new=force_new)),
        json=payload,
        timeout=timeout,
    )


def _post(path: str, google_token, payload: Dict[str, Any], timeout: int,
          retry_network: bool = False):
    """POST to the proxy. On 401, mint a fresh proxy session pass once.

    Retries only explicit pre-execution capacity rejections (429/529). Every other
    failure, including 5xx, connection errors, and timeouts, is returned/raised once.

    ``retry_network`` additionally retries a dropped connection or timeout. It is
    opt-in per endpoint and belongs only where replaying the call cannot meaningfully
    double-charge a copy: the caller must be able to say what one duplicate costs. A
    grading call is not such a place. One OCR page is: it is billed per page in paise,
    it is fired several at a time from a thread pool so a reset is a normal collision
    rather than a fault, and losing it discards every mark on the whole answer book.
    """
    last_exc = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            r = _post_once(path, google_token, payload, timeout)
            if r.status_code == 401:
                r = _post_once(path, google_token, payload, timeout, force_new=True)
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as exc:
            if not retry_network or attempt >= _RETRY_ATTEMPTS - 1:
                raise
            last_exc = exc
            wait = _RETRY_BACKOFF_SECONDS * (2 ** attempt) * (0.75 + random.random() * 0.5)
            wait = min(wait, _MAX_BACKOFF_SECONDS)
            print(f"[proxy] {path} — {type(exc).__name__}; the payload never "
                  f"completed, retrying in {wait:.0f}s "
                  f"({attempt + 1}/{_RETRY_ATTEMPTS - 1})", flush=True)
            time.sleep(wait)
            continue
        if r is not None and r.status_code not in _RETRY_STATUSES:
            return r
        if attempt < _RETRY_ATTEMPTS - 1:
            if r is None:
                detail = "connection refused"
            elif r.status_code == 529:
                detail = "the model is overloaded"
            elif r.status_code == 429:
                detail = "rate limited"
            else:
                detail = f"HTTP {r.status_code}"
            wait = _backoff_seconds(r, attempt)
            print(f"[proxy] {path} — {detail}; waiting {wait:.0f}s then retrying "
                  f"({attempt + 1}/{_RETRY_ATTEMPTS - 1})", flush=True)
            time.sleep(wait)
    if r is None and last_exc is not None:
        raise last_exc
    return r


def check_allowed(google_token, app: str = APP_NAME) -> bool:
    """Call before every paid/main action. Fail closed."""
    return check_allowed_status(google_token, app) == "allowed"


def check_allowed_diagnostic(google_token, app: str = APP_NAME) -> tuple[str, str]:
    """Return ``(decision, reason)`` without exposing credentials or bodies.

    Normal callers should continue to use :func:`check_allowed`.  The local app's
    sign-in endpoint uses this diagnostic surface so an expired/rejected Google
    credential is not presented as an indistinguishable generic allowlist failure.
    Every non-``allowed`` result still fails closed.
    """
    if not google_token:
        return "denied", "missing_token"
    try:
        r = _post("/api/allowlist", google_token, {"app": app}, _TIMEOUT)
        if r.status_code == 200:
            return ("allowed", "allowed") if bool(r.json().get("allowed")) else (
                "denied", "not_allowlisted"
            )
        if r.status_code == 403:
            return "denied", "not_allowlisted"
        if r.status_code == 401:
            return "error", "credential_rejected"
        if 500 <= r.status_code <= 599:
            return "error", "proxy_unavailable"
        return "error", f"proxy_http_{int(r.status_code)}"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return "error", "proxy_unreachable"
    except Exception:
        return "error", "proxy_error"


def check_allowed_status(google_token, app: str = APP_NAME) -> str:
    """Return one of: allowed, denied, error."""
    return check_allowed_diagnostic(google_token, app)[0]


def log_usage(
    google_token,
    *,
    filename: str,
    input_unit: str,
    count: Any,
    items: List[Dict[str, Any]],
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
) -> Optional[dict]:
    """Legacy/manual audit endpoint.

    Do not use this for normal AI calls. The provider helpers below already
    create trusted raw logs inside the proxy. This function exists only for
    unusual/manual audit cases and never writes trusted cost rows to the raw
    export sheet.
    """
    try:
        r = _post(
            "/api/usage-log",
            google_token,
            {
                "app": app,
                "filename": filename,
                "task_id": task_id,
                "input_unit": input_unit,
                "count": count,
                "items": items,
                "video_duration": video_duration,
            },
            _TIMEOUT,
        )
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


class UsageSession:
    """Backward-compatible task context.

    Old kit versions used UsageSession to suppress per-call logging and write a
    combined manual row on flush(). In v2, the proxy logs every raw provider
    call. This class now only provides one shared task_id plus optional default
    metadata for old code that already passes session=.
    """

    def __init__(
        self,
        google_token,
        *,
        filename: str = "",
        input_unit: str = "",
        count: Any = None,
        video_duration: str = "",
        app: str = APP_NAME,
        task_id: str = "",
    ):
        self.token = google_token
        self.task_id = task_id or new_task_id()
        self.filename = filename
        self.input_unit = input_unit
        self.count = count
        self.video_duration = video_duration
        self.app = app

    def add(self, model="", tokens_in=0, tokens_out=0, cost_inr=None):
        return None

    def flush(self):
        """Compatibility no-op. Raw provider rows are already logged."""
        return {
            "ok": True,
            "task_id": self.task_id,
            "note": "raw provider calls already logged by proxy",
        }


def _apply_task_context(payload: Dict[str, Any], session=None, task_id: str = "") -> None:
    if session is not None:
        if not task_id:
            task_id = getattr(session, "task_id", "")
        if payload.get("app") == APP_NAME and getattr(session, "app", APP_NAME) != APP_NAME:
            payload["app"] = getattr(session, "app")
        for key in ("filename", "input_unit", "video_duration"):
            if not payload.get(key) and getattr(session, key, ""):
                payload[key] = getattr(session, key)
        if (
            (payload.get("count") is None or payload.get("count") == "")
            and getattr(session, "count", None) is not None
        ):
            payload["count"] = getattr(session, "count")
    if task_id:
        payload["task_id"] = task_id


def _raise_proxy_error(name: str, r) -> None:
    if r.status_code == 200:
        return
    # These two survived every retry, so the capacity problem is real and ongoing. The
    # teacher can do something about that — wait and re-run the sheet — but only if we
    # say so. The raw body ("claude proxy error 529: {"error":"claude error","detail":
    # {"type":"error","error":{"type":"overloaded_error"...") tells them nothing they
    # can act on, and reads like the tool is broken when nothing here is broken.
    if r.status_code in _CAPACITY_STATUSES:
        what = ("is busy right now" if r.status_code == 529
                else "is rate limiting us")
        raise PWAccessError(
            f"The {name} model {what} and did not recover after "
            f"{_RETRY_ATTEMPTS} attempts. Nothing is wrong with this copy — "
            f"re-run it in a few minutes."
        )
    raise PWAccessError(f"{name} proxy error {r.status_code}: {r.text[:300]}")


def _upload_large_json(google_token, app: str, request: dict, provider_name: str) -> str:
    import json

    request_bytes = json.dumps(request).encode("utf-8")
    if len(request_bytes) <= _LARGE_REQUEST_BYTES:
        return ""

    # Handing the payload to blob storage is not the provider call: nothing has run
    # and nothing is billed until the request that references this blob is sent. A
    # reset here is therefore free to replay, and not replaying it lost whole copies
    # on the largest answer books - exactly the ones that need the blob path.
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            up = _post("/api/gemini/upload-url", google_token, {"app": app}, _TIMEOUT,
                       retry_network=True)
            if up.status_code != 200:
                raise PWAccessError(
                    f"{provider_name} upload-url error {up.status_code}: {up.text[:300]}")

            pr = requests.put(
                up.json()["upload_url"],
                data=request_bytes,
                headers={"Content-Type": "application/json"},
                timeout=_AI_TIMEOUT,
            )
            if pr.status_code != 200:
                raise PWAccessError(
                    f"{provider_name} upload error {pr.status_code}: {pr.text[:300]}")
            return pr.json().get("url", "")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            last_exc = exc
            if attempt >= _RETRY_ATTEMPTS - 1:
                break
            wait = min(_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                       * (0.75 + random.random() * 0.5), _MAX_BACKOFF_SECONDS)
            print(f"[proxy] {provider_name} payload upload dropped "
                  f"({type(exc).__name__}); retrying in {wait:.0f}s "
                  f"({attempt + 1}/{_RETRY_ATTEMPTS - 1})", flush=True)
            time.sleep(wait)
    raise PWAccessError(
        f"{provider_name} payload upload failed after {_RETRY_ATTEMPTS} attempts: "
        f"{last_exc}")


def _unwrap_envelope(data):
    """Return the provider payload from a proxy reply.

    Small requests come back as the raw provider JSON; blob-uploaded (large) requests
    come back wrapped in the proxy's envelope {"ok": ..., "request_id": ...,
    "result": <provider response>, "usage": ..., "cost_inr": ...}.

    The envelope is detected by ITS OWN shape (a `result` object plus the proxy's
    `request_id`/`ok` bookkeeping), not by sniffing provider-specific keys inside it.
    Sniffing meant every new provider or response variant had to be listed here, and a
    miss returned the wrapper silently — that is exactly how Mathpix replies read as
    "zero OCR lines" for weeks with no error anywhere.
    """
    if (
        isinstance(data, dict)
        and isinstance(data.get("result"), (dict, list))
        and ("request_id" in data or "ok" in data)
    ):
        return data["result"]
    return data


# Kept as names for the two call paths; both use the single envelope rule above.
_unwrap_provider_response = _unwrap_envelope


def gemini_generate(
    google_token,
    *,
    model: str,
    request: dict,
    filename: str = "",
    input_unit: str = "",
    count: Any = None,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call Gemini through the proxy. Returns a Gemini-shaped result."""
    payload = {
        "app": app,
        "model": model,
        "request": request,
        "filename": filename,
        "input_unit": input_unit,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    blob_url = _upload_large_json(google_token, payload["app"], request, "gemini")
    if blob_url:
        payload.pop("request")
        payload["request_blob_url"] = blob_url

    r = _post("/api/gemini/generate", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("gemini", r)
    return _unwrap_provider_response(r.json())


def mathpix_ocr(
    google_token,
    *,
    request: dict,
    filename: str = "",
    count: Any = 1,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call Mathpix OCR through the proxy."""
    payload = {
        "app": app,
        "request": request,
        "filename": filename,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    r = _post("/api/mathpix/ocr", google_token, payload, _AI_TIMEOUT,
              retry_network=True)
    _raise_proxy_error("mathpix", r)
    return _unwrap_envelope(r.json())


def sarvam_tts(
    google_token,
    *,
    request: dict,
    filename: str = "",
    count: Any = None,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call Sarvam TTS through the proxy."""
    payload = {
        "app": app,
        "request": request,
        "filename": filename,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    r = _post("/api/sarvam/tts", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("sarvam", r)
    return r.json()


def elevenlabs_tts(
    google_token,
    *,
    voice_id: str,
    request: dict,
    output_format: str = "mp3_44100_128",
    filename: str = "",
    count: Any = None,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call ElevenLabs TTS through the proxy."""
    payload = {
        "app": app,
        "voice_id": voice_id,
        "request": request,
        "output_format": output_format,
        "filename": filename,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    r = _post("/api/elevenlabs/tts", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("elevenlabs", r)
    return r.json()


def claude_generate(
    google_token,
    *,
    model: str,
    request: dict,
    filename: str = "",
    input_unit: str = "",
    count: Any = None,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call Claude through the proxy."""
    payload = {
        "app": app,
        "model": model,
        "request": request,
        "filename": filename,
        "input_unit": input_unit,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    blob_url = _upload_large_json(google_token, payload["app"], request, "claude")
    if blob_url:
        payload.pop("request")
        payload["request_blob_url"] = blob_url

    r = _post("/api/claude/generate", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("claude", r)
    return _unwrap_provider_response(r.json())


def gemini_tts(
    google_token,
    *,
    text: str,
    voice: str = "Kore",
    model: str = "gemini-3.1-flash-tts-preview",
    filename: str = "",
    count: Any = None,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call Gemini TTS through the proxy."""
    payload = {
        "app": app,
        "model": model,
        "text": text,
        "voice": voice,
        "filename": filename,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    r = _post("/api/gemini/tts", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("gemini tts", r)
    return r.json()


def gemini_image(
    google_token,
    *,
    prompt: str,
    model: str = "gemini-3.1-flash-image",
    filename: str = "",
    count: Any = 1,
    task_id: str = "",
    video_duration: str = "",
    app: str = APP_NAME,
    session: UsageSession = None,
) -> dict:
    """Call Gemini image generation through the proxy."""
    payload = {
        "app": app,
        "model": model,
        "prompt": prompt,
        "filename": filename,
        "count": count,
        "video_duration": video_duration,
    }
    _apply_task_context(payload, session, task_id)
    r = _post("/api/gemini/image", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("gemini image", r)
    return r.json()
