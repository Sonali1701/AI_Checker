"""Alerts for unattended workflow runs: a Windows notification and an email, never a sound.

When a run ends, pauses for a sign-in, or a portal window waits for a login, the
operator may be away from the screen. Each alert:

* shows a silent Windows notification that stays on screen until it is dismissed,
  and a banner on the workflow page until someone clicks **Dismiss**;
* emails the run's alert address from the configured sender (by default
  evaluator.b4@pw.live through Gmail SMTP with an app password).

The app password is stored in workflow_data/alerts.json encrypted with Windows
DPAPI, so it is readable only by the same Windows user on the same computer.
Messages carry the test, status and counts, never student names or marks.
Every channel fails independently: an email error never hides the notification.
The build 135 siren was removed in build 137 (operator: too loud, no sound at all).
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import platform
import smtplib
import ssl
import subprocess
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from xml.sax.saxutils import escape

DEFAULT_SENDER = "evaluator.b4@pw.live"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465
RECENT_LIMIT = 30


# ------------------------------------------------------------------ password protection

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI for the current user; raises OSError when unavailable."""
    if os.name != "nt":
        raise OSError("Stored passwords need Windows")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = call(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target))   # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("Windows could not protect or read the stored password")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def protect_secret(secret: str) -> str:
    return base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii")


def reveal_secret(stored: str) -> str:
    return _dpapi(base64.b64decode(stored), False).decode("utf-8")


# ---------------------------------------------------------------------- notification

POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
TOAST_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null;"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:AICHECKER_TOAST_XML);"
    "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:AICHECKER_TOAST_APP).Show($t)"
)


def toast_xml(title: str, body: str) -> str:
    """A reminder-style toast: it stays on screen until dismissed, and makes no sound."""
    return ('<toast scenario="reminder" duration="long"><visual><binding template="ToastGeneric">'
            f"<text>{escape(title)}</text><text>{escape(body)}</text></binding></visual>"
            '<actions><action activationType="system" arguments="dismiss" content=""/></actions>'
            '<audio silent="true"/></toast>')


def show_toast(title: str, body: str) -> None:
    env = dict(os.environ, AICHECKER_TOAST_XML=toast_xml(title, body), AICHECKER_TOAST_APP=POWERSHELL_APP_ID)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", TOAST_SCRIPT],
                   env=env, timeout=30, capture_output=True, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ----------------------------------------------------------------------------- email

def send_email(settings: dict, password: str, to: list[str], subject: str, body: str, smtp_factory=None) -> None:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings["sender"], ", ".join(to), subject
    message.set_content(body)
    factory = smtp_factory or (lambda host, port: smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30))
    with factory(settings.get("smtp_host") or DEFAULT_SMTP_HOST, int(settings.get("smtp_port") or DEFAULT_SMTP_PORT)) as server:
        server.login(settings["sender"], password)
        server.send_message(message)


# ------------------------------------------------------------------------- the center

class AlertCenter:
    """Raises, remembers and acknowledges alerts; holds the email settings."""

    def __init__(self, root, toaster=None, mailer=None, protect=None, reveal=None, clock=None):
        self.root = Path(root)
        self.toaster = toaster or show_toast
        self.mailer = mailer or send_email
        self.protect, self.reveal = protect or protect_secret, reveal or reveal_secret
        self.clock = clock or time.time
        self.lock = threading.Lock()
        self.active = None
        self.recent = []
        self.seen = {}
        self.last_email = {"ok": None, "detail": "", "at": ""}

    # settings ------------------------------------------------------------------
    @property
    def settings_path(self):
        return self.root / "alerts.json"

    def settings(self) -> dict:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("sender", DEFAULT_SENDER)
        data.setdefault("smtp_host", DEFAULT_SMTP_HOST)
        data.setdefault("smtp_port", DEFAULT_SMTP_PORT)
        return data

    def save_email_settings(self, sender: str, app_password: str | None):
        sender = (sender or DEFAULT_SENDER).strip()
        if "@" not in sender:
            raise ValueError("Enter the sender's email address")
        data = self.settings()
        data["sender"] = sender
        if app_password is not None:
            secret = "".join(app_password.split())         # Google shows app passwords in groups of four
            data["password"] = self.protect(secret) if secret else ""
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def email_configured(self) -> bool:
        return bool(self.settings().get("password"))

    # alerts --------------------------------------------------------------------
    def raise_alert(self, key: str, title: str, body: str, to=(), once_within: float = 0.0):
        """Notification and email for one event. `key` with `once_within` seconds
        drops a repeat of the same event (a login wait polled every second)."""
        now = self.clock()
        with self.lock:
            last = self.seen.get(key)
            if last is not None and once_within and now - last < once_within:
                return False
            self.seen[key] = now
            entry = {"key": key, "title": title, "body": body, "at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                     "to": sorted({address.strip() for address in to if address and "@" in address})}
            self.active = entry
            self.recent = ([entry] + self.recent)[:RECENT_LIMIT]
        threading.Thread(target=self._deliver, args=(entry,), name="workflow-alert-delivery", daemon=True).start()
        return True

    def _deliver(self, entry):
        try:
            self.toaster(entry["title"], entry["body"])
        except Exception:  # noqa: BLE001 - no notification centre (older Windows, a locked-down PC)
            pass
        if not entry["to"]:
            return
        settings = self.settings()
        stamp = datetime.fromtimestamp(self.clock()).isoformat(timespec="seconds")
        if not settings.get("password"):
            self.last_email = {"ok": False, "detail": "Email alerts are not set up: save the sender's app password on the workflow page.", "at": stamp}
            return
        try:
            password = self.reveal(settings["password"])
            body = f"{entry['body']}\n\nComputer: {platform.node()}\nTime: {entry['at']}\n\nSent by the AI Checker workflow."
            self.mailer(settings, password, entry["to"], f"AI Checker: {entry['title']}", body)
            self.last_email = {"ok": True, "detail": f"Sent to {', '.join(entry['to'])}", "at": stamp}
        except Exception as exc:  # noqa: BLE001 - shown on the page; the notification already went out
            self.last_email = {"ok": False, "detail": f"Email could not be sent: {exc}", "at": stamp}

    def acknowledge(self):
        with self.lock:
            self.active = None

    def status(self) -> dict:
        settings = self.settings()
        with self.lock:
            return {"active": dict(self.active) if self.active else None,
                    "recent": [dict(entry) for entry in self.recent[:10]],
                    "email": {"sender": settings["sender"], "configured": bool(settings.get("password")), **self.last_email}}
