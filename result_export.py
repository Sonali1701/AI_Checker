# -*- coding: utf-8 -*-
"""The results sheet the Vidyapeeth team asked for (requirements, 10 Oct 2026):
one row per student with centre, course, class, board, subject, test date,
registration number, name, phone, score, total, percentage, review status and a link
to the checked copy.

A CSV row is appended as each copy finishes (a run that stops keeps what is done),
and an .xlsx with the same rows is written at the end of the run; uploaded to Google
Drive it opens as a Google Sheet. The student's details come from the booklet's
cover page (vidyapeeth_intake.py); board and test date from the question paper.
"""
from __future__ import annotations

import csv
import os
import re
import threading
from pathlib import Path

# The PRD's columns, plus the three the team's header details add (Vertical, Test
# Name, Mode of Paper; PRD sheet "The new fields we need", 15 Oct 2026).
COLUMNS = ["Center Name", "Course", "Class", "Board", "Vertical", "Subject", "Mode of Paper",
           "Test Date", "Test Name",
           "Registration Number", "Student Name", "Phone Number", "Subject-wise Score",
           "Total Marks", "Percentage", "Review Status", "Report Link", "Source File",
           "Notes"]
CSV_NAME = "student_results.csv"
XLSX_NAME = "student_results.xlsx"
_STATUS = {"released": "Checked, sent to student", "faculty_review": "Faculty review",
           "rejected": "Rejected", "failed": "Not graded"}
_LOCK = threading.Lock()

_BOARD_RE = re.compile(r"\b(CBSE|ICSE|ISC|IGCSE|NIOS|State Board|[A-Z]{2,4}SE)\b")
# "DATE: 03 / 05 / 2026": the Vidyapeeth paper spaces its date out (14 Oct 2026), and
# the sheet then took each student's own handwritten date (3/4/26, 31512026).
_DATE_RE = re.compile(r"\bdate\s*[:\-–]?\s*(\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4})",
                      re.IGNORECASE)


def paper_facts(paper_text: str) -> dict:
    """Board and test date printed on the question paper, where it prints them."""
    text = str(paper_text or "")[:6000]
    board = _BOARD_RE.search(text)
    date = _DATE_RE.search(text)
    return {"board": board.group(1) if board else "",
            "test_date": date.group(1) if date else ""}


def _text(value) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def normal_date(value) -> str:
    """DD/MM/YYYY for 2026-05-03 (the form's date box), 03/05/2026, 3.5.26, 3-5-2026,
    03|05|2026; anything else as written."""
    text = _text(value)
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    found = re.fullmatch(r"(\d{1,2})\s*[/.\-|]\s*(\d{1,2})\s*[/.\-|]\s*(\d{2}|\d{4})", text)
    if iso:
        year, month, day = (int(v) for v in iso.groups())
    elif found:
        day, month, year = (int(v) for v in found.groups())
        year += 2000 if year < 100 else 0
    else:
        return text
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return text
    return f"{day:02d}/{month:02d}/{year}"


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
          "ix": 9, "x": 10, "xi": 11, "xii": 12}
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def normal_class(value) -> str:
    """"8" for 8, 8th, VIII, VIIIth, Eight (8th), Vidyapeeth class 8th; as written
    otherwise."""
    text = _text(value)
    for token in re.findall(r"[A-Za-z]+|\d+", text.lower()):
        token = re.sub(r"(st|nd|rd|th)$", "", token) if not token.isdigit() else token
        number = (int(token) if token.isdigit() else _ROMAN.get(token) or _WORDS.get(token))
        if number and 1 <= number <= 12:
            return str(number)
    return text


def normal_phone(value) -> tuple[str, bool]:
    """(the number, whether it is a certain 10-digit number). "+91" or a leading 0 in
    front of ten digits is dropped."""
    text = re.sub(r"[^\d?]", "", _text(value))
    if len(text) == 12 and text.startswith("91"):
        text = text[2:]
    elif len(text) == 11 and text.startswith("0"):
        text = text[1:]
    return text, len(text) == 10 and text.isdigit()


def copy_stem(info: dict | None, fallback: str) -> str:
    """The checked copy's file name: "TANYA_SINGH_23217614" from the cover's name and
    roll number, so a copy is found by its student, not by its place in a scanned
    stack (user's request, 13 Oct 2026: "20260504114826_1_01" said nothing). A roll
    digit not read for certain shows as X. With neither on the cover, the fallback
    (the uploaded file's own name)."""
    info = info or {}
    name = re.sub(r"[^A-Z0-9]+", "_", _text(info.get("student_name")).upper()).strip("_")[:60]
    roll = re.sub(r"[^\dX]", "", _text(info.get("roll_number")).upper().replace("?", "X"))[:16]
    return "_".join(part for part in (name.strip("_"), roll) if part) or fallback


def unique_stems(stems: list[tuple[object, str]]) -> dict:
    """{key: stem} with a second copy of the same stem as stem_2, the third stem_3."""
    out, used = {}, set()
    for key, stem in stems:
        candidate, n = stem, 1
        while candidate.casefold() in used:
            n += 1
            candidate = f"{stem}_{n}"
        used.add(candidate.casefold())
        out[key] = candidate
    return out


def batch_fields(fields: dict | None) -> dict:
    """The header details typed once for a whole upload: centre, course, board,
    vertical, test name, test date and mode of paper (vidyapeeth_fields.py)."""
    fields = fields or {}
    return {"centre": _text(fields.get("centre")), "course": _text(fields.get("course")),
            "board": _text(fields.get("board")), "vertical": _text(fields.get("vertical")),
            "test_name": _text(fields.get("test_name")), "paper_mode": _text(fields.get("paper_mode")),
            "test_date": normal_date(fields.get("test_date"))}


def row_for(result: dict, *, source: str, report_link: str, facts: dict,
            student_class: str = "", subject: str = "", batch: dict | None = None) -> dict:
    """One results-sheet row for one copy.

    Centre, course and test date are the same for a whole centre's stack, but every
    student writes them differently on the cover ("ISkon Temple", "9sHon P.W",
    "Taskcon" in one 36-booklet scan, 12 Oct 2026), so what the operator typed for
    the upload wins; the class is the one the paper was graded for. The board,
    vertical, test name and mode of paper come only from the operator (15 Oct 2026)."""
    cover = result.get("student_info") or {}
    batch = batch or {}
    state = result.get("state") or ("released" if result.get("release") else "failed")
    notes = list(result.get("cover_problems") or [])
    phone, phone_certain = normal_phone(cover.get("phone", ""))
    if phone and not phone_certain:
        notes.append(f"The phone number on the cover reads \"{phone}\", not a certain "
                     "10-digit number.")
    if state != "released":
        notes += [str(r) for r in (result.get("review_reasons") or [])][:3]
        if result.get("error") and not notes:
            notes.append(str(result["error"])[:300])
    return {
        "Center Name": batch.get("centre") or _text(cover.get("centre")),
        "Course": batch.get("course") or _text(cover.get("batch")),
        "Class": _text(student_class) or normal_class(cover.get("class_name", "")),
        "Board": batch.get("board") or facts.get("board", ""),
        "Vertical": batch.get("vertical", ""),
        "Subject": subject or cover.get("subject", ""),
        "Mode of Paper": batch.get("paper_mode", ""),
        "Test Date": (batch.get("test_date") or normal_date(facts.get("test_date", ""))
                      or normal_date(cover.get("date", ""))),
        "Test Name": batch.get("test_name", ""),
        "Registration Number": cover.get("roll_number", ""),
        "Student Name": _text(cover.get("student_name")).upper(),
        "Phone Number": phone,
        "Subject-wise Score": result.get("score", "") if state != "failed" else "",
        "Total Marks": result.get("max", "") if state != "failed" else "",
        "Percentage": result.get("percent", "") if state != "failed" else "",
        "Review Status": _STATUS.get(state, state),
        "Report Link": report_link,
        "Source File": source,
        "Notes": " | ".join(notes),
    }


def _os(path: Path) -> str:
    """The path as the OS opens it, past 260 characters too (bulk._os_path)."""
    try:
        from bulk import _os_path
        return _os_path(Path(path))
    except Exception:  # noqa: BLE001 - a plain path still works below the limit
        return str(path)


def append_csv(folder: Path, row: dict) -> Path:
    """Add one row to the folder's results CSV (created with its header)."""
    path = Path(folder) / CSV_NAME
    with _LOCK:
        new = not os.path.exists(_os(path))
        with open(_os(path), "a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            if new:
                writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in COLUMNS})
    return path


def read_csv(path: Path) -> list[dict]:
    with open(_os(path), encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_per_copy(rows: list[dict]) -> list[dict]:
    """One row per copy: a copy graded again on a later run keeps its newest row."""
    latest: dict[str, dict] = {}
    for row in rows:
        latest[str(row.get("Source File") or "")] = row
    return list(latest.values())


def mark_duplicates(rows: list[dict]) -> list[dict]:
    """Name every registration number that appears on more than one copy."""
    seen: dict[str, list[str]] = {}
    for row in rows:
        number = str(row.get("Registration Number") or "").strip()
        if number:
            seen.setdefault(number, []).append(str(row.get("Source File") or ""))
    out = []
    for row in rows:
        number = str(row.get("Registration Number") or "").strip()
        others = [s for s in seen.get(number, []) if s != row.get("Source File")]
        if others:
            note = "Same registration number as " + ", ".join(others[:3])
            row = dict(row, Notes=" | ".join(x for x in (row.get("Notes"), note) if x))
        out.append(row)
    return out


def write_xlsx(rows: list[dict], path: Path) -> Path:
    """The results sheet as .xlsx: header row frozen, filters on, links clickable."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    book = Workbook()
    sheet = book.active
    sheet.title = "Results"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAD3")
    link_col = COLUMNS.index("Report Link") + 1
    for row in mark_duplicates(rows):
        values = [row.get(key, "") for key in COLUMNS]
        for key in ("Subject-wise Score", "Total Marks", "Percentage"):
            i = COLUMNS.index(key)
            try:
                values[i] = float(values[i]) if str(values[i]).strip() != "" else ""
            except ValueError:
                pass
        sheet.append(values)
        link = str(row.get("Report Link") or "")
        if link:
            cell = sheet.cell(row=sheet.max_row, column=link_col)
            cell.hyperlink = link if "://" in link else Path(link).as_uri() if Path(link).is_absolute() else link
            cell.font = Font(color="1155CC", underline="single")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for i, key in enumerate(COLUMNS, 1):
        width = max([len(key)] + [len(str(c.value or "")) for c in sheet[get_column_letter(i)][1:200]])
        sheet.column_dimensions[get_column_letter(i)].width = min(48, max(10, width + 2))
    path = Path(path)
    os.makedirs(_os(path.parent), exist_ok=True)
    book.save(_os(path))
    return path


def xlsx_bytes(rows: list[dict]) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        target = write_xlsx(rows, Path(folder) / XLSX_NAME)
        return Path(target).read_bytes()
