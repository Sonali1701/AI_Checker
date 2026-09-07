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

import os
import uuid
from typing import Any, Dict, List, Optional

import requests


# ---------------------------------------------------------------------------
# Per-app config. APP_NAME must exactly match a header in Whitelisted row 1.
# ---------------------------------------------------------------------------
APP_NAME = "SET-YOUR-APP-NAME"

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


_session = {"token": "", "expiry": 0.0}


def _auth_token(google_token, force_new: bool = False) -> str:
    """Return a cached 7-day proxy session pass, or mint one from Google."""
    import time

    if not force_new and _session["token"] and time.time() < _session["expiry"] - 60:
        return _session["token"]

    g = _resolve_token(google_token)
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
                _session["token"] = token
                _session["expiry"] = float(data.get("expires_at_ms") or 0) / 1000.0
                return token
    except Exception:
        pass
    return g


def _post(path: str, google_token, payload: Dict[str, Any], timeout: int):
    """POST to the proxy. On 401, mint a fresh proxy session pass once."""
    r = requests.post(
        f"{PROXY_BASE_URL}{path}",
        headers=_headers(_auth_token(google_token)),
        json=payload,
        timeout=timeout,
    )
    if r.status_code == 401:
        r = requests.post(
            f"{PROXY_BASE_URL}{path}",
            headers=_headers(_auth_token(google_token, force_new=True)),
            json=payload,
            timeout=timeout,
        )
    return r


def check_allowed(google_token, app: str = APP_NAME) -> bool:
    """Call before every paid/main action. Fail closed."""
    return check_allowed_status(google_token, app) == "allowed"


def check_allowed_status(google_token, app: str = APP_NAME) -> str:
    """Return one of: allowed, denied, error."""
    if not google_token:
        return "denied"
    try:
        r = _post("/api/allowlist", google_token, {"app": app}, _TIMEOUT)
        if r.status_code == 200:
            return "allowed" if bool(r.json().get("allowed")) else "denied"
        if r.status_code == 403:
            return "denied"
        return "error"
    except Exception:
        return "error"


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
    if r.status_code != 200:
        raise PWAccessError(f"{name} proxy error {r.status_code}: {r.text[:300]}")


def _upload_large_json(google_token, app: str, request: dict, provider_name: str) -> str:
    import json

    request_bytes = json.dumps(request).encode("utf-8")
    if len(request_bytes) <= _LARGE_REQUEST_BYTES:
        return ""

    up = _post("/api/gemini/upload-url", google_token, {"app": app}, _TIMEOUT)
    if up.status_code != 200:
        raise PWAccessError(f"{provider_name} upload-url error {up.status_code}: {up.text[:300]}")

    pr = requests.put(
        up.json()["upload_url"],
        data=request_bytes,
        headers={"Content-Type": "application/json"},
        timeout=_AI_TIMEOUT,
    )
    if pr.status_code != 200:
        raise PWAccessError(f"{provider_name} upload error {pr.status_code}: {pr.text[:300]}")
    return pr.json().get("url", "")


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
    return r.json()


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
    r = _post("/api/mathpix/ocr", google_token, payload, _AI_TIMEOUT)
    _raise_proxy_error("mathpix", r)
    return r.json()


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
    return r.json()


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
