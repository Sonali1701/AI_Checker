"""Append per-run grading costs to a Google Sheet via a service account.

Auth model (unattended, no browser): a Google Cloud *service account*. Put its JSON
key somewhere the app can read it (env var GOOGLE_SERVICE_ACCOUNT_JSON, or a file
named service_account.json next to this module), and share the target sheet with the
service account's client_email as an Editor. See README/setup notes printed in the app.

Uses google-api-python-client + google-auth, which are already installed (deps of
google-genai) — no extra package required.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

# The sheet the user asked to log into (from the URL they provided). Overridable in the UI.
DEFAULT_SPREADSHEET_ID = "1ri0uOSS98ZXhrBHv5x7ljXz_jQdpIwKV5PxzX2OTGuo"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "Timestamp", "Student file", "Provider", "Model", "Class", "Subject",
    "Score", "Max score", "Input tokens", "Output tokens", "Mathpix pages",
    "LLM cost (USD)", "Mathpix cost (USD)", "Total (USD)", "USD/INR", "Total (INR)",
]


def _local_key_file() -> str | None:
    p = os.path.join(os.path.dirname(__file__), "service_account.json")
    return p if os.path.exists(p) else None


def resolve_sa_info(explicit: str | dict | None = None) -> dict | None:
    """Resolve the service-account info dict, in order: an explicit path/JSON/dict,
    the GOOGLE_SERVICE_ACCOUNT_JSON env var (a file path OR inline JSON — this is how
    Streamlit Cloud secrets arrive), or a local service_account.json file. None if none.

    Supporting inline JSON is what makes Sheets logging work on Streamlit Cloud, where
    you can't commit a key file — you paste the JSON into st.secrets instead.
    """
    if isinstance(explicit, dict):
        return explicit
    src = explicit or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or _local_key_file()
    if not src:
        return None
    s = str(src).strip()
    if s.startswith("{"):                        # inline JSON (secrets / env)
        try:
            return json.loads(s)
        except Exception:
            return None
    if os.path.exists(s):                         # a path to a key file
        try:
            with open(s, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def has_service_account(explicit: str | dict | None = None) -> bool:
    return resolve_sa_info(explicit) is not None


def service_account_email(explicit: str | dict | None = None) -> str:
    """The client_email to share the sheet with (read from whichever source resolves)."""
    info = resolve_sa_info(explicit)
    return info.get("client_email", "") if info else ""


def cost_row(
    *, student_file: str, provider: str, model: str,
    student_class: str | None, subject: str | None,
    score: float, max_score: float,
    input_tokens: int, output_tokens: int, mathpix_pages: int,
    llm_cost_usd: float, mathpix_cost_usd: float, total_usd: float,
    usd_to_inr: float, total_inr: float, timestamp: str | None = None,
) -> list:
    """Build one row in HEADER order (keeps the sheet columns in sync)."""
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        ts, student_file, provider, model, student_class or "", subject or "",
        score, max_score, int(input_tokens), int(output_tokens), int(mathpix_pages),
        round(float(llm_cost_usd), 6), round(float(mathpix_cost_usd), 6),
        round(float(total_usd), 6), float(usd_to_inr), round(float(total_inr), 2),
    ]


def _sheets_values(info: dict):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return svc.spreadsheets().values()


def append_cost_row(
    row: list,
    spreadsheet_id: str = DEFAULT_SPREADSHEET_ID,
    sa: str | dict | None = None,
    worksheet: str | None = None,
) -> None:
    """Append `row` to the sheet, writing the HEADER first if the sheet is empty.

    `sa` may be a key-file path, inline JSON, or a dict. Raises RuntimeError with an
    actionable message when the service account isn't configured, so the caller can
    surface it to the user.
    """
    info = resolve_sa_info(sa)
    if not info:
        raise RuntimeError(
            "No service-account credentials found. On Streamlit Cloud, paste the key JSON "
            "into st.secrets as GOOGLE_SERVICE_ACCOUNT_JSON. Locally, set that env var to the "
            "JSON path or drop service_account.json next to the app. Then share the sheet "
            "(Editor) with the service account's client_email."
        )

    values = _sheets_values(info)
    rng = f"'{worksheet}'!A1" if worksheet else "A1"
    head_rng = f"'{worksheet}'!A1:Z1" if worksheet else "A1:Z1"

    try:
        existing = values.get(spreadsheetId=spreadsheet_id, range=head_rng).execute().get("values")
    except Exception:
        existing = None
    if not existing:
        values.append(
            spreadsheetId=spreadsheet_id, range=rng, valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS", body={"values": [HEADER]},
        ).execute()

    values.append(
        spreadsheetId=spreadsheet_id, range=rng, valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS", body={"values": [row]},
    ).execute()
