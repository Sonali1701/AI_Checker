"""The header details of a Vidyapeeth batch, typed once by the operator.

The Vidyapeeth team asked (PRD sheet, "The new fields we need", 15 Oct 2026) for the
board, vertical, centre, class, subject, test name and mode of paper as dropdowns on
the form, all mandatory, so the results sheet carries one consistent value for the
whole batch. Reading them from each student's cover gave "ISkon Temple", "9sHon
P.W" and "Taskcon" for one centre in one scan. The lists live in
assets/vidyapeeth_fields.json; a value that is not in a list is still accepted, so a
new centre or subject does not block a batch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent
_BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", _PROJECT_DIR))
FIELDS_FILE = next((p for p in (_BUNDLE_DIR / "assets" / "vidyapeeth_fields.json",
                                _PROJECT_DIR / "assets" / "vidyapeeth_fields.json") if p.exists()),
                   _PROJECT_DIR / "assets" / "vidyapeeth_fields.json")

# Form field -> the label the operator sees. Every one is required for a Vidyapeeth batch.
REQUIRED = {
    "board": "Board",
    "vertical": "Vertical",
    "centre": "Centre name",
    "course": "Course",
    "student_class": "Class",
    "subject": "Subject",
    "test_name": "Test name",
    "test_date": "Test date",
    "paper_mode": "Mode of paper",
}

# Mode of paper -> the batch's answer language (marking_settings.LANGUAGES). Hinglish
# is Hindi and English mixed, which the default reading already covers.
MODE_LANGUAGE = {
    "english": "english", "hindi": "hindi", "hinglish": "", "urdu": "urdu",
    "gujrati": "gujarati", "gujarati": "gujarati", "marathi": "marathi", "bengali": "bengali",
}


def load() -> dict:
    """The dropdown lists, for the form and the results sheet."""
    try:
        data = json.loads(FIELDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: list(value) for key, value in data.items() if isinstance(value, list)}


def _text(value) -> str:
    return " ".join(str(value or "").split())


def missing(fields: dict) -> list[str]:
    """The labels of the required fields that are empty, in form order."""
    return [label for key, label in REQUIRED.items() if not _text(fields.get(key))]


def answer_language_for(mode: str) -> str:
    """The answer language a mode of paper implies, or "" (auto)."""
    return MODE_LANGUAGE.get(_text(mode).lower(), "")
