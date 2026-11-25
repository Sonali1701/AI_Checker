"""Durable local workflow: sheet -> assignment -> download -> grade -> upload.

Portal work is serialized per account; different accounts run in parallel.
Credentials live only in memory and are supplied again when resuming a run.
"""
from __future__ import annotations

import base64
import concurrent.futures
import csv
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import socket
import threading
import time
import uuid
from collections import Counter, deque
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse, parse_qs

import requests
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pdf_page_ranges import select_pdf_pages
from workflow_sheet_status import (STATUS_ALIASES, SheetStatusSync, normalized_status, terminal_status, status_for,
                                   retryable_saved_row)
from workflow_cleanup import cleanup_copy_artifacts
from portal_api import CopyMovedOn, MarksNotSaved, PortalApi, PortalApiError, PortalBusy, PortalLoginRefused, UseTheBrowser
from portal_recovery import FetchRecovery, ReadFailures

ALIASES = {
    "submission_id": ("submission_id", "_id"),
    "student_id": ("student_id", "studentid"),
    "student_name": ("student_name", "firstname"),
    "program_id": ("program_id", "programid"),
    "program_name": ("program_name", "prog_name"),
    "test_series_id": ("test_series_id", "tcid"),
    "test_series_name": ("test_series_name", "tcname"),
    "test_id": ("test_id", "testid", "tid"),
    "test_name": ("test_name", "testname"),
    "copy_url": ("copy_url", "studentfilename"),
    "portal_status": ("portal_status", "testactivitystatus"),
    "total_marks": ("total_marks", "totalmarks"),
    "board_or_exam": ("board_or_exam", "ctexname"),
    "exam_kind": ("exam_kind", "exam"),        # SCHOOL PREPARATION...: the autopilot's exam category
    "workflow_status": STATUS_ALIASES,
}
REQUIRED = ("submission_id", "student_id", "program_name", "test_series_name", "test_id", "test_name", "copy_url", "portal_status")
MAX_BYTES = 50 * 1024 * 1024
IMPORT_TTL_SECONDS = 10 * 60
EXAM_DOCUMENT_TTL_SECONDS = 30 * 60
IMPORT_CACHE_MAX_BYTES = 100 * 1024 * 1024
IMPORT_CACHE_MAX_COUNT = 10
GOOGLE_SNAPSHOT_FORMAT = "workflow-google-sheet-v1"
MAX_GOOGLE_ROWS = 100_000
MAX_GOOGLE_COLUMNS = 2_048
MAX_GOOGLE_CELLS = 1_000_000
GOOGLE_METADATA_FIELDS = "properties(title),sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))"
GOOGLE_GRID_FIELDS = "sheets(properties(sheetId,title),data(startRow,startColumn,rowData(values(formattedValue,effectiveValue,hyperlink))))"
MAX_GOOGLE_METADATA_BYTES = 2 * 1024 * 1024
MAX_GOOGLE_PAGE_BYTES = 4 * 1024 * 1024
GOOGLE_ROWS_PER_PAGE = 500
GOOGLE_PAGE_INTERVAL_SECONDS = 1.2


class GoogleSheetError(ValueError):
    """Safe Google access errors, separate from the app's authentication gate."""
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def google_snapshot(data, filename):
    if not filename.lower().endswith(".google-sheet.json"):
        return None
    check_sheet_data(data)
    try:
        snapshot = json.loads(data)
        tabs = snapshot["tabs"]
        if snapshot.get("format") != GOOGLE_SNAPSHOT_FORMAT or not isinstance(tabs, list) or not tabs:
            raise ValueError
        if any(not isinstance(tab, dict) or not isinstance(tab.get("name"), str) or
               not isinstance(tab.get("rows"), list) for tab in tabs):
            raise ValueError
        return tabs
    except (ValueError, TypeError, KeyError):
        raise ValueError("Google Sheet snapshot is invalid. Load tabs again.") from None


def submission_time(submission_id):
    """A 24-hex portal id carries its creation time in its first 8 hex digits; 0 for any other id."""
    return int(submission_id[:8], 16) if re.fullmatch(r"[0-9a-fA-F]{24}", submission_id or "") else 0


def duplicate_submissions(rows):
    """A student with several submissions of one test: the portal shows one row per student
    and cannot tell the attempts apart, so the latest submission (by its id's time, else the
    later sheet row) is processed and the others are recorded as duplicates.
    Returns {duplicate submission id: the submission id processed instead}."""
    by_student = {}
    for index, row in enumerate(rows):
        by_student.setdefault(row["student_id"].casefold(), []).append((index, row))
    duplicates = {}
    for group in by_student.values():
        if len(group) < 2:
            continue
        chosen = max(group, key=lambda pair: (submission_time(pair[1]["submission_id"]), pair[0]))[1]
        for _, row in group:
            if row is not chosen:
                duplicates[row["submission_id"]] = chosen["submission_id"]
    return duplicates


def normalize_rows(rows, *, numbered=False):
    result, seen = [], {}
    for ordinal, item in enumerate(rows, 2):
        number, raw = item if numbered else (ordinal, item)
        raw = {str(k).strip().lower(): str(v or "").strip() for k, v in raw.items() if k is not None}
        if not any(raw.values()):
            continue
        row = {key: next((raw[a] for a in aliases if raw.get(a)), "") for key, aliases in ALIASES.items()}
        missing = [key for key in REQUIRED if not row[key] and not (terminal_status(row) and key == "copy_url")]
        if missing:
            raise ValueError(f"Row {number}: missing {', '.join(missing)}")
        for key in ("submission_id", "student_id", "test_id"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", row[key]):
                raise ValueError(f"Row {number}: invalid {key}")
        if not terminal_status(row):
            validate_copy_url(row["copy_url"])
        identity = row["submission_id"].casefold()
        if identity in seen:
            if seen[identity] != row:
                raise ValueError(f"Row {number}: conflicting duplicate submission {identity}")
            continue
        seen[identity] = row
        result.append(row)
    if not result:
        raise ValueError("No copies found in the sheet")
    return result


def validate_copy_url(url):
    p = urlparse(url)
    # Known download host family from the supplied export. Do not fetch arbitrary
    # sheet URLs, local services, credentials in URLs or automatic redirects.
    if p.scheme != "https" or not (p.hostname or "").endswith(".cloudfront.net") or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Copy links must be HTTPS cloudfront.net URLs")


def check_sheet_data(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Sheet exceeds 50 MB")
    if not data.strip():
        raise ValueError("Sheet is empty")
    prefix = data[:512].decode("utf-8-sig", errors="ignore").lstrip().lower()
    if prefix.startswith(("<!doctype", "<html")):
        raise ValueError("Google returned a sign-in page. Download the workbook as XLSX or the tab as CSV and import it here.")


def sheet_tab_names(data: bytes, filename: str):
    """Read exported worksheet titles without assuming Excel IDs are Google gids."""
    check_sheet_data(data)
    snapshot = google_snapshot(data, filename)
    if snapshot is not None:
        return [tab["name"] for tab in snapshot]
    if not filename.lower().endswith(".xlsx"):
        data.decode("utf-8-sig")
        return ["CSV data"]
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        names = [worksheet.title for worksheet in wb.worksheets]
        if not names:
            raise ValueError("Workbook contains no worksheets")
        return names
    finally:
        wb.close()


def parse_sheet(data: bytes, filename: str, worksheet_name: str | None = None):
    check_sheet_data(data)
    snapshot = google_snapshot(data, filename)
    if snapshot is not None:
        tab = next((tab for tab in snapshot if tab["name"] == worksheet_name), None) if worksheet_name is not None else snapshot[0]
        if tab is None:
            raise ValueError("Selected worksheet is not in this Google Sheet. Load tabs again.")
        iterator = iter(tab["rows"])
        headers = next(iterator, None)
        if not headers or not any(str(value).strip() for value in headers):
            raise ValueError("Selected worksheet is empty")
        check_headers(headers)
        return normalize_rows(dict(zip(headers, values)) for values in iterator)
    if filename.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            if worksheet_name is not None and worksheet_name not in [w.title for w in wb.worksheets]:
                raise ValueError(f"Worksheet '{worksheet_name}' is not in this workbook. Load tabs again.")
            worksheet = wb[worksheet_name] if worksheet_name is not None else wb.active
            if worksheet is None or not hasattr(worksheet, "iter_rows"):
                raise ValueError("Select a worksheet containing copies")
            iterator = worksheet.iter_rows(values_only=True)
            first = next(iterator, None)
            if first is None or not any(value is not None and str(value).strip() for value in first):
                raise ValueError(f"Worksheet '{worksheet.title}' is empty")
            headers = [str(v or "").strip() for v in first]
            check_headers(headers)
            return normalize_rows(dict(zip(headers, values)) for values in iterator)
        finally:
            wb.close()
    if worksheet_name is not None and worksheet_name != "CSV data":
        raise ValueError("CSV files contain only the 'CSV data' tab")
    content = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    check_headers(reader.fieldnames or [])
    return normalize_rows(reader)


def check_headers(headers):
    normalized = [h.strip().lower() for h in headers]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Repeated column headers: use the workflow template with unique names")


def google_sheet_location(url):
    p = urlparse(url)
    match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]+)/(?:edit|export|view).*", p.path)
    if p.scheme != "https" or p.hostname != "docs.google.com" or p.username or p.password or p.port not in (None, 443) or not match:
        raise ValueError("Enter a Google Sheets document URL")
    gid = parse_qs(p.query).get("gid", parse_qs(p.fragment).get("gid", ["0"]))[0]
    if not gid.isdigit():
        raise ValueError("Invalid sheet tab ID")
    return match[1], gid


def download_google_sheet(url, export_format="csv"):
    document_id, gid = google_sheet_location(url)
    if export_format not in ("csv", "xlsx"):
        raise ValueError("Unsupported sheet export format")
    export = f"https://docs.google.com/spreadsheets/d/{document_id}/export?format={export_format}"
    if export_format == "csv":
        export += f"&gid={gid}"
    # Google exports can redirect to Google's own content hosts; inspect each hop.
    for _ in range(5):
        with requests.get(export, timeout=(10, 60), stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                export = urljoin(export, response.headers["Location"])
                destination = urlparse(export)
                host = destination.hostname or ""
                if destination.scheme != "https" or destination.username or destination.password or destination.port not in (None, 443) or not (host == "docs.google.com" or host.endswith(".googleusercontent.com")):
                    raise GoogleSheetError("Sheet requires sign-in. Connect Google Sheets, or export the workbook as XLSX or this tab as CSV and import it here.", 403)
                continue
            if response.status_code in (401, 403):
                raise GoogleSheetError("Sheet requires sign-in or export permission. Connect Google Sheets, or download the workbook as XLSX or the tab as CSV and import it here.", 403)
            response.raise_for_status()
            data = read_limited(response)
            try:
                check_sheet_data(data)
            except ValueError as exc:
                if "sign-in page" in str(exc):
                    raise GoogleSheetError("Sheet requires sign-in. Connect Google Sheets, or download the workbook as XLSX or the tab as CSV and import it here.", 403) from None
                raise
            return data
    raise ValueError("Too many Google export redirects; import a CSV instead")


def read_google_sheet(url):
    return parse_sheet(download_google_sheet(url), "sheet.csv")


def _google_cell_text(cell):
    if not isinstance(cell, dict):
        raise GoogleSheetError("Google Sheets returned an invalid cell response. Load tabs again.")
    value = cell.get("formattedValue")
    if value is not None:
        if not isinstance(value, (str, int, float, bool)):
            raise GoogleSheetError("Google Sheets returned an invalid cell response. Load tabs again.")
        return str(value)
    effective = cell.get("effectiveValue") or {}
    if not isinstance(effective, dict):
        raise GoogleSheetError("Google Sheets returned an invalid cell response. Load tabs again.")
    if "stringValue" in effective:
        return str(effective["stringValue"])
    if "numberValue" in effective:
        return str(effective["numberValue"])
    if "boolValue" in effective:
        return "TRUE" if effective["boolValue"] else "FALSE"
    return ""


def _google_snapshot_bytes(payload):
    """Preserve exact titles and sparse positions without Excel title restrictions."""
    if not isinstance(payload, dict) or not isinstance(payload.get("sheets"), list) or not payload["sheets"]:
        raise GoogleSheetError("Google Sheets returned no worksheets. Check the sheet and load tabs again.")
    tabs, names, total_rows, total_cells = [], set(), 0, 0
    for sheet in payload["sheets"]:
        if not isinstance(sheet, dict) or not isinstance(sheet.get("properties"), dict):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        properties = sheet["properties"]
        name, gid = properties.get("title"), properties.get("sheetId")
        if not isinstance(name, str) or not name or name in names or not isinstance(gid, int) or isinstance(gid, bool) or gid < 0:
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        names.add(name)
        sparse, hyperlinks, last_row = {}, {}, -1
        grids = sheet.get("data", [])
        if not isinstance(grids, list):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        for grid in grids:
            if not isinstance(grid, dict):
                raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
            start_row, start_column = grid.get("startRow", 0), grid.get("startColumn", 0)
            row_data = grid.get("rowData", [])
            if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (start_row, start_column)) or not isinstance(row_data, list):
                raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
            if start_row + len(row_data) > MAX_GOOGLE_ROWS or start_column > MAX_GOOGLE_COLUMNS:
                raise GoogleSheetError("Google Sheet exceeds the supported row or column limit. Import a smaller workbook.")
            for offset, raw_row in enumerate(row_data):
                if not isinstance(raw_row, dict) or not isinstance(raw_row.get("values", []), list):
                    raise GoogleSheetError("Google Sheets returned an invalid row response. Load tabs again.")
                values = raw_row.get("values", [])
                end_column = start_column + len(values)
                if end_column > MAX_GOOGLE_COLUMNS:
                    raise GoogleSheetError("Google Sheet exceeds the supported row or column limit. Import a smaller workbook.")
                total_cells += end_column
                if total_cells > MAX_GOOGLE_CELLS:
                    raise GoogleSheetError("Google Sheet exceeds the supported cell limit. Import a smaller workbook.")
                number = start_row + offset
                last_row = max(last_row, number)
                row = sparse.setdefault(number, [])
                if len(row) < end_column:
                    row.extend([""] * (end_column - len(row)))
                row[start_column:end_column] = [_google_cell_text(cell) for cell in values]
                for column, cell in enumerate(values, start_column):
                    if isinstance(cell.get("hyperlink"), str):
                        hyperlinks[(number, column)] = cell["hyperlink"]
        total_rows += last_row + 1
        if total_rows > MAX_GOOGLE_ROWS:
            raise GoogleSheetError("Google Sheet exceeds the supported row limit. Import a smaller workbook.")
        # HYPERLINK formulas can display "Download" instead of the copy URL.
        # Only copy-link columns use their hyperlink target; other labels stay text.
        copy_columns = {number for number, header in enumerate(sparse.get(0, []))
                        if header.strip().lower() in ALIASES["copy_url"]}
        for (number, column), link in hyperlinks.items():
            if number > 0 and column in copy_columns:
                sparse[number][column] = link
        tabs.append({"name": name, "gid": gid, "rows": [sparse.get(number, []) for number in range(last_row + 1)]})
    data = json.dumps({"format": GOOGLE_SNAPSHOT_FORMAT, "tabs": tabs}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise GoogleSheetError("Google Sheet snapshot exceeds 50 MB. Import a smaller workbook.")
    return data


def download_connected_google_sheet(url, access_token):
    """Use a short-lived OAuth token only on the fixed Google API host."""
    document_id, _ = google_sheet_location(url)
    if not isinstance(access_token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", access_token):
        raise GoogleSheetError("Google Sheets connection is invalid. Connect Google Sheets again.", 403)
    endpoint = f"https://sheets.googleapis.com/v4/spreadsheets/{document_id}"
    fields = "properties(title),sheets(properties(sheetId,title),data(startRow,startColumn,rowData(values(formattedValue,effectiveValue,hyperlink))))"
    try:
        with requests.get(endpoint, headers={"Authorization": "Bearer " + access_token},
                          params={"includeGridData": "true", "fields": fields}, timeout=(10, 60),
                          stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                raise GoogleSheetError("Google Sheets returned an unexpected redirect. Connect Google Sheets again.")
            if response.status_code == 401:
                raise GoogleSheetError("Google Sheets connection expired. Connect Google Sheets again.", 403)
            if response.status_code == 403:
                # Inspect only fixed API reason codes; never expose Google's message or payload.
                body = read_limited(response)
                try:
                    error = json.loads(body).get("error", {})
                    reasons = {item.get("reason") for key in ("details", "errors") for item in error.get(key, []) if isinstance(item, dict)}
                except (ValueError, TypeError, AttributeError):
                    reasons = set()
                if reasons.intersection({"SERVICE_DISABLED", "accessNotConfigured"}):
                    raise GoogleSheetError("Google Sheets API is disabled for this app. Enable Google Sheets API in its Google Cloud project, then Connect Google Sheets again.", 403)
                raise GoogleSheetError("Google Sheets access was denied. Connect Google Sheets with an account that can read this sheet and allow Sheets access.", 403)
            if response.status_code == 404:
                raise GoogleSheetError("Google Sheet was not found or this account cannot read it. Check the link and Connect Google Sheets again.", 403)
            if response.status_code != 200:
                raise GoogleSheetError("Google Sheets is temporarily unavailable. Try loading tabs again.")
            body = read_limited(response)
    except requests.Timeout:
        raise GoogleSheetError("Google Sheets request timed out. Try loading tabs again.") from None
    except requests.RequestException:
        raise GoogleSheetError("Could not connect to Google Sheets. Check your connection and try again.") from None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        raise GoogleSheetError("Google Sheets returned an invalid response. Load tabs again.") from None
    return _google_snapshot_bytes(payload)


def _google_api_json(document_id, access_token, params, byte_limit, resource=""):
    """Bound each Google response and never forward the token to a redirect."""
    if not isinstance(access_token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", access_token):
        raise GoogleSheetError("Connect Google Sheets again before reading this tab.", 403)
    if resource not in ("", "/values:batchGet"):
        raise ValueError("Unsupported Google Sheets resource")
    endpoint = f"https://sheets.googleapis.com/v4/spreadsheets/{document_id}{resource}"
    for attempt in range(3):
        try:
            with requests.get(endpoint, headers={"Authorization": "Bearer " + access_token},
                              params=params, timeout=(10, 60), stream=True,
                              allow_redirects=False) as response:
                if response.is_redirect:
                    raise GoogleSheetError("Google Sheets returned an unexpected redirect. Connect Google Sheets again.")
                if response.status_code == 401:
                    raise GoogleSheetError("Google Sheets connection expired. Connect Google Sheets again.", 403)
                if response.status_code == 403:
                    try:
                        error = json.loads(_read_google_response(response, MAX_GOOGLE_METADATA_BYTES)).get("error", {})
                        reasons = {item.get("reason") for key in ("details", "errors") for item in error.get(key, []) if isinstance(item, dict)}
                    except (ValueError, TypeError, AttributeError):
                        reasons = set()
                    if reasons.intersection({"SERVICE_DISABLED", "accessNotConfigured"}):
                        raise GoogleSheetError("Google Sheets API is disabled for this app. Enable Google Sheets API in its Google Cloud project, then Connect Google Sheets again.", 403)
                    raise GoogleSheetError("Google Sheets access was denied. Connect Google Sheets with an account that can read this sheet and allow Sheets access.", 403)
                if response.status_code == 404:
                    raise GoogleSheetError("Google Sheet was not found or this account cannot read it. Check the link and Connect Google Sheets again.", 403)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt < 2:
                        delay = (2.0, 5.0)[attempt] if response.status_code == 429 else (0.25, 0.75)[attempt]
                        try:
                            retry_after = float(getattr(response, "headers", {}).get("Retry-After", ""))
                            if math.isfinite(retry_after):
                                delay = max(delay, min(30.0, retry_after))
                        except (TypeError, ValueError):
                            pass
                        time.sleep(delay)
                        continue
                    raise GoogleSheetError("Google Sheets is temporarily unavailable. Try loading tabs again.")
                if response.status_code != 200:
                    raise GoogleSheetError("Google Sheets could not read the selected tab. Load tabs again and retry.")
                body = _read_google_response(response, byte_limit)
        except requests.Timeout:
            if attempt < 2:
                time.sleep((0.25, 0.75)[attempt])
                continue
            raise GoogleSheetError("Google Sheets request timed out. Try loading tabs again.") from None
        except requests.RequestException:
            raise GoogleSheetError("Could not connect to Google Sheets. Check your connection and try again.") from None
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            raise GoogleSheetError("Google Sheets returned an invalid response. Load tabs again.") from None
        if not isinstance(payload, dict):
            raise GoogleSheetError("Google Sheets returned an invalid response. Load tabs again.")
        return payload


def _read_google_response(response, byte_limit):
    data = bytearray()
    for chunk in response.iter_content(65536):
        data.extend(chunk)
        if len(data) > byte_limit:
            raise GoogleSheetError("Google Sheets returned too much data for one selected-tab request. Import a smaller tab as CSV.")
    return bytes(data)


def fetch_connected_google_metadata(url, access_token):
    """Loading tab names reads properties only, irrespective of workbook size."""
    document_id, _ = google_sheet_location(url)
    payload = _google_api_json(document_id, access_token, {"fields": GOOGLE_METADATA_FIELDS}, MAX_GOOGLE_METADATA_BYTES)
    sheets = payload.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise GoogleSheetError("Google Sheets returned no worksheets. Check the sheet and load tabs again.")
    tabs, names, ids = [], set(), set()
    for sheet in sheets:
        properties = sheet.get("properties") if isinstance(sheet, dict) else None
        if not isinstance(properties, dict):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        name, gid, grid = properties.get("title"), properties.get("sheetId"), properties.get("gridProperties", {})
        if not isinstance(name, str) or not name or name in names or not isinstance(gid, int) or isinstance(gid, bool) or gid < 0 or gid in ids or not isinstance(grid, dict):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        row_count, column_count = grid.get("rowCount", 0), grid.get("columnCount", 0)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (row_count, column_count)):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet size. Load tabs again.")
        names.add(name)
        ids.add(gid)
        tabs.append({"name": name, "gid": gid, "row_count": row_count, "column_count": column_count})
    title = (payload.get("properties") or {}).get("title", "")
    return {"document_id": document_id, "source_url": url, "title": title if isinstance(title, str) else "", "tabs": tabs}


def _google_column_name(number):
    result = ""
    number += 1
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _google_grid_cells(payload, tab, first_row, last_row, columns):
    """Reject mismatched tab IDs and cells outside the exact requested ranges."""
    sheets = payload.get("sheets")
    if not isinstance(sheets, list) or len(sheets) != 1:
        raise GoogleSheetError("Selected worksheet changed or Google returned an unexpected tab. Load tabs again.")
    sheet = sheets[0]
    properties = sheet.get("properties") if isinstance(sheet, dict) else None
    if not isinstance(properties, dict) or properties.get("sheetId") != tab["gid"] or properties.get("title") != tab["name"]:
        raise GoogleSheetError("Selected worksheet changed or was renamed. Load tabs again.")
    grids = sheet.get("data", [])
    if not isinstance(grids, list):
        raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
    cells = {}
    for grid in grids:
        if not isinstance(grid, dict):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        start_row, start_column = grid.get("startRow", 0), grid.get("startColumn", 0)
        row_data = grid.get("rowData", [])
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (start_row, start_column)) or not isinstance(row_data, list):
            raise GoogleSheetError("Google Sheets returned an invalid worksheet response. Load tabs again.")
        if row_data and (start_row < first_row or start_row + len(row_data) > last_row + 1):
            raise GoogleSheetError("Google Sheets returned rows outside the selected range. Load tabs again.")
        for offset, row in enumerate(row_data):
            values = row.get("values", []) if isinstance(row, dict) else None
            if not isinstance(values, list):
                raise GoogleSheetError("Google Sheets returned an invalid row response. Load tabs again.")
            for column, cell in enumerate(values, start_column):
                if column not in columns or not isinstance(cell, dict):
                    raise GoogleSheetError("Google Sheets returned cells outside the selected columns. Load tabs again.")
                key = (start_row + offset, column)
                if key in cells and cells[key] != cell:
                    raise GoogleSheetError("Google Sheets returned conflicting cell data. Load tabs again.")
                cells[key] = cell
    return cells


def read_connected_google_tab(descriptor, worksheet, access_token):
    """Import one worksheet in bounded pages of only workflow columns."""
    original = next((tab for tab in descriptor["tabs"] if tab["name"] == worksheet), None)
    if original is None:
        raise ValueError(f"Worksheet '{worksheet}' is not in this import. Load tabs again.")
    current = fetch_connected_google_metadata(descriptor["source_url"], access_token)
    tab = next((tab for tab in current["tabs"] if tab["gid"] == original["gid"]), None)
    if tab is None or tab["name"] != worksheet:
        raise GoogleSheetError("Selected worksheet changed or was renamed. Load tabs again.")
    if not tab["row_count"] or not tab["column_count"]:
        raise ValueError("Selected worksheet is empty")
    if tab["column_count"] > MAX_GOOGLE_COLUMNS:
        raise GoogleSheetError("Selected tab exceeds the supported column limit. Import a smaller tab as CSV.")
    quoted = "'" + worksheet.replace("'", "''") + "'"
    header_range = f"{quoted}!A1:{_google_column_name(tab['column_count'] - 1)}1"
    header = _google_api_json(descriptor["document_id"], access_token,
                              {"includeGridData": "true", "fields": GOOGLE_GRID_FIELDS, "ranges": [header_range]}, MAX_GOOGLE_PAGE_BYTES)
    header_cells = _google_grid_cells(header, tab, 0, 0, set(range(tab["column_count"])))
    aliases = {alias: key for key, values in ALIASES.items() for alias in values}
    columns = {column: aliases[text] for (number, column), cell in header_cells.items()
               if (text := _google_cell_text(cell).strip().lower()) in aliases}
    if not columns:
        raise ValueError("Selected worksheet has no workflow column headers")
    missing = [key for key in REQUIRED if key not in columns.values()]
    if missing:
        raise ValueError("Selected worksheet is missing columns: " + ", ".join(missing))
    # rowCount includes the worksheet's empty allocation. Values responses trim
    # trailing blanks, so a million allocated rows with a few copies stays small.
    # Read every relevant column here: an incomplete row with a missing ID must
    # still be imported and rejected instead of disappearing from an ID-only scan.
    if tab["row_count"] == 1:
        raise ValueError("No copies found in the sheet")
    extent_ranges = [f"{quoted}!{_google_column_name(column)}2:{_google_column_name(column)}{tab['row_count']}" for column in sorted(columns)]
    extent = _google_api_json(descriptor["document_id"], access_token,
                             {"fields": "valueRanges(values)", "valueRenderOption": "UNFORMATTED_VALUE",
                              "majorDimension": "ROWS", "ranges": extent_ranges}, MAX_BYTES, "/values:batchGet")
    value_ranges = extent.get("valueRanges")
    if not isinstance(value_ranges, list) or len(value_ranges) != len(columns):
        raise GoogleSheetError("Google Sheets returned an invalid selected-tab range response. Load tabs again.")
    occupied, nonempty_cells = set(), 0
    for column, value_range in zip(sorted(columns), value_ranges):
        values = value_range.get("values", []) if isinstance(value_range, dict) else None
        if not isinstance(values, list) or len(values) > tab["row_count"] - 1:
            raise GoogleSheetError("Google Sheets returned rows outside the selected range. Load tabs again.")
        for number, cells in enumerate(values, 1):
            if not isinstance(cells, list) or len(cells) > 1 or any(not isinstance(value, (str, int, float, bool)) for value in cells):
                raise GoogleSheetError("Google Sheets returned an invalid selected-tab cell response. Load tabs again.")
            if cells and str(cells[0]).strip() and columns[column] != "workflow_status":
                occupied.add(number)
                nonempty_cells += 1
                if len(occupied) > MAX_GOOGLE_ROWS:
                    raise GoogleSheetError("Selected tab exceeds the supported copy-row limit. Import a smaller tab as CSV.")
                if nonempty_cells > MAX_GOOGLE_CELLS:
                    raise GoogleSheetError("Selected tab exceeds the supported workflow cell limit. Import a smaller tab as CSV.")
    del extent, value_ranges
    if not occupied:
        raise ValueError("No copies found in the sheet")
    normalized_bytes, actual_rows, actual_cells = 0, 0, 0
    last_occupied = max(occupied)

    def rows():
        nonlocal normalized_bytes, actual_rows, actual_cells
        last_page_request = None
        for window in sorted({(number - 1) // GOOGLE_ROWS_PER_PAGE for number in occupied}):
            first = window * GOOGLE_ROWS_PER_PAGE + 1
            last = min(first + GOOGLE_ROWS_PER_PAGE - 1, last_occupied)
            ranges = [f"{quoted}!{_google_column_name(column)}{first + 1}:{_google_column_name(column)}{last + 1}" for column in sorted(columns)]
            if last_page_request is not None:
                delay = GOOGLE_PAGE_INTERVAL_SECONDS - (time.monotonic() - last_page_request)
                if delay > 0:
                    time.sleep(delay)
            last_page_request = time.monotonic()
            payload = _google_api_json(descriptor["document_id"], access_token,
                                      {"includeGridData": "true", "fields": GOOGLE_GRID_FIELDS, "ranges": ranges}, MAX_GOOGLE_PAGE_BYTES)
            cells = _google_grid_cells(payload, tab, first, last, set(columns))
            for number in range(first, last + 1):
                raw = {}
                for column, key in columns.items():
                    cell = cells.get((number, column), {})
                    value = _google_cell_text(cell).strip()
                    if key == "copy_url" and isinstance(cell.get("hyperlink"), str):
                        value = cell["hyperlink"].strip()
                    if not value:
                        continue
                    actual_cells += 1
                    if actual_cells > MAX_GOOGLE_CELLS:
                        raise GoogleSheetError("Selected tab exceeds the supported workflow cell limit. Import a smaller tab as CSV.")
                    if raw.get(key) and raw[key] != value:
                        raise ValueError(f"Row {number + 1}: conflicting duplicate columns for {key}")
                    raw[key] = value
                if any(value for key, value in raw.items() if key != "workflow_status"):
                    actual_rows += 1
                    if actual_rows > MAX_GOOGLE_ROWS:
                        raise GoogleSheetError("Selected tab exceeds the supported copy-row limit. Import a smaller tab as CSV.")
                normalized_bytes += len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if normalized_bytes > MAX_BYTES:
                    raise GoogleSheetError("Selected tab's imported copies exceed 50 MB. Split this tab into smaller imports.")
                if any(value for key, value in raw.items() if key != "workflow_status"):
                    yield number + 1, raw
    return normalize_rows(rows(), numbered=True)


class SheetImportCache:
    """Owner-bound, expiring sheet snapshots; no browser/API credentials persist."""
    def __init__(self, clock=None, ttl=IMPORT_TTL_SECONDS, max_bytes=IMPORT_CACHE_MAX_BYTES,
                 max_count=IMPORT_CACHE_MAX_COUNT):
        self.clock = clock or time.monotonic
        self.ttl, self.max_bytes, self.max_count = ttl, max_bytes, max_count
        self.entries = {}
        self.lock = threading.Lock()

    def prune(self, now):
        for import_id in [key for key, value in self.entries.items() if value["expires_at"] <= now]:
            del self.entries[import_id]

    @staticmethod
    def entry_size(entry):
        return len(entry.get("data", b"")) + entry.get("google_bytes", 0)

    def reserve(self, extra_bytes, preserve=None):
        if extra_bytes > self.max_bytes or self.max_count < 1:
            raise ValueError("Sheet exceeds the available import cache size")
        while self.entries and (len(self.entries) >= self.max_count and preserve is None or
                sum(self.entry_size(entry) for entry in self.entries.values()) + extra_bytes > self.max_bytes):
            candidate = next((key for key in self.entries if key != preserve), None)
            if candidate is None:
                raise ValueError("Selected tab exceeds the available import cache size")
            del self.entries[candidate]

    def put(self, owner, data, filename, source_name=None):
        filename = Path(filename or "sheet.csv").name
        tabs = sheet_tab_names(data, filename)
        if len(data) > self.max_bytes or self.max_count < 1:
            raise ValueError("Sheet exceeds the available import cache size")
        with self.lock:
            now = self.clock()
            self.prune(now)
            self.reserve(len(data))
            import_id = uuid.uuid4().hex
            self.entries[import_id] = {"owner": owner, "data": bytes(data), "filename": filename,
                                       "tabs": tabs, "expires_at": now + self.ttl,
                                       "source_name": source_name or filename}
        return {"import_id": import_id, "tabs": [{"name": name} for name in tabs],
                "source_name": source_name or filename}

    def put_google(self, owner, descriptor):
        """Store small metadata and later selected-tab results, never OAuth tokens."""
        descriptor = json.loads(json.dumps(descriptor))
        size = len(json.dumps(descriptor, ensure_ascii=False).encode("utf-8"))
        with self.lock:
            now = self.clock()
            self.prune(now)
            self.reserve(size)
            import_id = uuid.uuid4().hex
            tabs = [tab["name"] for tab in descriptor["tabs"]]
            self.entries[import_id] = {"owner": owner, "descriptor": descriptor, "google_rows": {},
                                       "google_bytes": size, "tabs": tabs, "expires_at": now + self.ttl,
                                       "source_name": "Connected Google Sheet"}
        return {"import_id": import_id, "tabs": [{"name": name} for name in tabs],
                "source_name": "Connected Google Sheet", "requires_google_token": True}

    def preview(self, owner, import_id, worksheet="", access_token=None):
        with self.lock:
            self.prune(self.clock())
            entry = self.entries.get(import_id)
            if not entry or entry["owner"] != owner:
                raise LookupError("Sheet import not found or expired. Load tabs again.")
            if not worksheet:
                if len(entry["tabs"]) != 1:
                    raise ValueError("Select a worksheet before importing copies")
                worksheet = entry["tabs"][0]
            if worksheet not in entry["tabs"]:
                raise ValueError(f"Worksheet '{worksheet}' is not in this import. Load tabs again.")
            if "descriptor" in entry:
                if worksheet in entry["google_rows"]:
                    return json.loads(json.dumps(entry["google_rows"][worksheet]))
                if not access_token:
                    raise GoogleSheetError("Connect Google Sheets again before reading this tab.", 403)
                descriptor = entry["descriptor"]
        if "descriptor" in entry:
            rows = read_connected_google_tab(descriptor, worksheet, access_token)
            size = len(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            with self.lock:
                self.prune(self.clock())
                if self.entries.get(import_id) is not entry or entry["owner"] != owner:
                    raise LookupError("Sheet import not found or expired. Load tabs again.")
                if worksheet not in entry["google_rows"]:
                    self.reserve(size, preserve=import_id)
                    entry["google_rows"][worksheet] = rows
                    entry["google_bytes"] += size
                return json.loads(json.dumps(entry["google_rows"][worksheet]))
        return parse_sheet(entry["data"], entry["filename"], worksheet)

    def source(self, owner, import_id, worksheet=""):
        """Bind writes to owner-checked imported metadata, never client-supplied row ranges."""
        with self.lock:
            self.prune(self.clock())
            entry = self.entries.get(import_id)
            if not entry or entry["owner"] != owner:
                raise LookupError("Sheet import not found or expired. Load tabs again.")
            descriptor = entry.get("descriptor")
            if not descriptor:
                return None
            if not worksheet and len(entry["tabs"]) == 1:
                worksheet = entry["tabs"][0]
            tab = next((tab for tab in descriptor["tabs"] if tab["name"] == worksheet), None)
            if tab is None:
                raise ValueError("Select the imported student worksheet before starting")
            return {"source_url": descriptor["source_url"], "worksheet": worksheet, "gid": tab["gid"]}


def read_limited(response):
    data = bytearray()
    for chunk in response.iter_content(65536):
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise ValueError("Download exceeds 50 MB")
    return bytes(data)


def download_pdf(url, target):
    validate_copy_url(url)
    with requests.get(url, timeout=(10, 90), stream=True, allow_redirects=False) as response:
        if response.is_redirect:
            raise ValueError("Copy link redirects; provide its direct CloudFront PDF URL")
        if getattr(response, "status_code", 200) in (404, 410):
            raise UnreadableCopy("The copy file is no longer downloadable (HTTP " + str(response.status_code) + ")")
        response.raise_for_status()
        data = read_limited(response)
        headers = getattr(response, "headers", None) or {}
        content_type = str(headers.get("Content-Type") or "").split(";")[0].strip().lower()
    target.write_bytes(readable_copy(data, content_type))


def too_many_pages(path):
    """The page count of a copy longer than MAX_COPY_PAGES, else 0."""
    import fitz
    try:
        with fitz.open(path) as document:
            return document.page_count if document.page_count > MAX_COPY_PAGES else 0
    except Exception:  # noqa: BLE001 - the check itself reports a file it cannot read
        return 0


UNREADABLE = "Downloaded file is not a readable PDF: "
# The HEIF family's ftyp brands. An iPhone uploads HEIC from the camera roll unless the student
# has changed the camera setting, so these are ordinary answer sheets, not odd files. Video
# (ftypisom, ftypmp42) is deliberately not here: a video is not an answer sheet.
HEIF_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis")
HEIF_TYPES = ("image/heic", "image/heif", "image/avif", "image/heic-sequence", "image/heif-sequence")


def is_heif_photo(head, content_type=""):
    """Whether this file is an iPhone photo (HEIC, HEIF or AVIF)."""
    if str(content_type or "").split(";")[0].strip().lower() in HEIF_TYPES:
        return True
    return bytes(head[4:8]) == b"ftyp" and bytes(head[8:12]) in HEIF_BRANDS


def heif_to_pdf(data):
    """An iPhone photo as a one-page PDF (operator, 23 Nov 2026). iOS uploads HEIC by default,
    and until now such a copy was refused outright although the student had done nothing wrong.
    The photo is saved as JPEG at 92 rather than PNG: a 12 MP page is about 30 MB lossless, and
    a ten-page copy of those would not fit back through the portal."""
    import io

    import fitz
    try:
        import pillow_heif
        from PIL import Image
    except ImportError:
        raise UnreadableCopy(UNREADABLE + "the HEIC photo cannot be read by this version of the "
                             "app, which has no HEIC support built in") from None
    try:
        pillow_heif.register_heif_opener()
        with Image.open(io.BytesIO(data)) as photo:
            jpeg = io.BytesIO()
            photo.convert("RGB").save(jpeg, format="JPEG", quality=92)
        with fitz.open(stream=jpeg.getvalue(), filetype="jpg") as image:
            return image.convert_to_pdf()
    except Exception as exc:  # noqa: BLE001 - a broken photo reads like any other broken photo
        raise UnreadableCopy(UNREADABLE + f"the HEIC photo could not be read ({exc})") from None


def readable_copy(data, content_type=""):
    """The copy as PDF bytes: a PDF as served, or a photo (JPEG, PNG, or HEIC from an iPhone)
    turned into a one-page PDF. Raises UnreadableCopy for anything else, naming what the portal
    served (live run of 15 Oct 2026: 20 of 398 copies came back "not a readable
    PDF" and held up the run in retry rounds)."""
    import fitz
    head = bytes(data[:1024])
    unreadable = "Downloaded file is not a readable PDF: "
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.is_pdf:
                if doc.needs_pass:
                    raise UnreadableCopy(unreadable + "the PDF is password protected")
                if doc.page_count == 0:
                    raise UnreadableCopy(unreadable + "the PDF has no pages")
                return bytes(data)
    except UnreadableCopy:
        raise
    except Exception:  # noqa: BLE001 - not a PDF: a photo, a web page, an empty file
        pass
    if is_heif_photo(head, content_type):
        return heif_to_pdf(data)
    kind = "jpg" if head.startswith(b"\xff\xd8") else "png" if head.startswith(b"\x89PNG") else ""
    if kind:
        try:
            with fitz.open(stream=data, filetype=kind) as image:
                return image.convert_to_pdf()
        except Exception as exc:  # noqa: BLE001
            raise UnreadableCopy(unreadable + f"the {kind.upper()} photo could not be read ({exc})") from None
    if not data:
        raise UnreadableCopy(unreadable + "the portal served an empty file")
    if b"<html" in head.lower() or content_type.startswith("text/html"):
        raise UnreadableCopy(unreadable + "the portal served a web page instead of the file, "
                             "usually an expired or wrong link")
    raise UnreadableCopy(unreadable + f"the portal served {content_type or 'an unknown file type'} "
                         f"of {len(data)} bytes, not a PDF or a photo")


def download_exam_file(url):
    """One exam document the portal's own API pointed at (its static file store)."""
    p = urlparse(url)
    if p.scheme != "https" or p.username or p.password:
        raise ValueError("The portal returned an unsupported file address")
    with requests.get(url, timeout=(10, 90), stream=True, allow_redirects=True) as response:
        response.raise_for_status()
        if not str(response.url).startswith("https://"):
            raise ValueError("The portal file address redirected to an unsupported location")
        return read_limited(response)


def prepare_exam_documents(fetched):
    """Validate the portal's files and merge several Model Answer PDFs in order.

    `fetched` maps "question_paper" / "answer_key" to [(filename, bytes), ...].
    Returns cache entries {"data", "filename", "pages", "files", "sources"} for the
    kinds the portal had; raises when a file is not a readable PDF.
    """
    import fitz
    documents = {}
    for kind, files in fetched.items():
        if kind == "links":      # where the files are, not a file
            continue
        files = [(str(name or f"{kind}.pdf"), data) for name, data in (files or []) if data]
        if not files:
            continue
        merged = fitz.open()
        try:
            for name, data in files:
                try:
                    with fitz.open(stream=data, filetype="pdf") as document:
                        if not document.is_pdf or document.page_count == 0 or document.needs_pass:
                            raise ValueError("unreadable")
                        merged.insert_pdf(document)
                except Exception:
                    raise ValueError(f"The portal file {name} is not a readable PDF") from None
            pages = merged.page_count
            data = merged.tobytes() if len(files) > 1 else files[0][1]
        finally:
            merged.close()
        if len(data) > MAX_BYTES:
            raise ValueError("The portal document exceeds 50 MB")
        label = {"question_paper": "question-paper", "answer_key": "model-answer"}.get(kind, kind)
        documents[kind] = {"data": data, "pages": pages, "files": len(files), "sources": [name for name, _ in files],
                           "filename": Path(files[0][0]).name if len(files) == 1 else f"{label}-merged.pdf"}
    if not documents:
        raise ValueError("The portal has no question paper or Model Answer file for this test; upload the PDFs instead")
    return documents


class ExamDocumentCache:
    """Owner-bound, expiring exam PDFs fetched from the portal for one test."""
    def __init__(self, clock=None, ttl=EXAM_DOCUMENT_TTL_SECONDS, max_count=None):
        self.clock = clock or time.monotonic
        self.ttl, self.max_count = ttl, max_count
        self.entries = {}
        self.lock = threading.Lock()

    def prune(self, now):
        for fetch_id in [key for key, value in self.entries.items()
                         if value["status"] in ("ready", "failed") and value["expires_at"] <= now]:
            del self.entries[fetch_id]

    def create(self, owner, test):
        with self.lock:
            now = self.clock()
            self.prune(now)
            # An optional completed-result limit must never evict a live fetch.
            # The app uses TTL cleanup, without a fixed number of fetch windows.
            if self.max_count is not None:
                completed = [key for key, entry in self.entries.items() if entry["status"] in ("ready", "failed")]
                while len(self.entries) >= self.max_count and completed:
                    del self.entries[completed.pop(0)]
            fetch_id = uuid.uuid4().hex
            self.entries[fetch_id] = {"fetch_id": fetch_id, "owner": owner, "test": dict(test), "status": "queued",
                                      "detail": "", "error": "", "documents": {}, "expires_at": now + self.ttl}
        return fetch_id

    def update(self, fetch_id, **fields):
        with self.lock:
            entry = self.entries.get(fetch_id)
            if entry is not None:
                entry.update(fields)
                if fields.get("status") in ("ready", "failed"):
                    entry["expires_at"] = self.clock() + self.ttl

    def get(self, owner, fetch_id):
        with self.lock:
            self.prune(self.clock())
            entry = self.entries.get(fetch_id)
            return entry if entry and entry["owner"] == owner else None

    @staticmethod
    def public(entry):
        return {"fetch_id": entry["fetch_id"], "status": entry["status"], "detail": entry["detail"],
                "error": entry["error"], "test": entry["test"],
                "documents": {kind: {key: value for key, value in document.items() if key != "data"}
                              for kind, document in entry["documents"].items()}}


def portal_marks(report):
    """Aggregate subquestions into the portal's Q.1, Q.2 ... fields."""
    from exam_package import normalize_qid
    marks = {}
    for question in report.get("questions", []):
        match = re.fullmatch(r"q(\d+)(?:\..*)?", normalize_qid(str(question["qid"])))
        score = float(question["score"])
        if not match or not math.isfinite(score) or score < 0:
            raise ValueError(f"Cannot map question {question['qid']} to portal marks")
        q = int(match[1])
        marks[q] = marks.get(q, 0) + score
    total = float(report["total_score"])
    if not marks or not math.isfinite(total) or abs(sum(marks.values()) - total) > 1e-6:
        raise ValueError("Portal question marks do not match the checked total")
    return marks


def portal_feedback(report):
    """The comment for the portal, which refuses an evaluation without one ("Comment is
    required"). A copy with nothing to improve has no remarks: 3 of 32 copies on 30 Oct 2026,
    all of them full marks."""
    remarks = str(report.get("overall_remarks") or "").strip()
    if remarks:
        return remarks
    full = float(report.get("total_score") or 0) >= float(report.get("max_total") or math.inf)
    return "Excellent work. You earned full marks." if full else "Your copy has been checked. See the marks on each answer."


def account_slug(email):
    return re.sub(r"[^a-z0-9]+", "_", (email or "").strip().lower()).strip("_") or "evaluator"


def account_profile(kind, email):
    identity = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]
    return f"{kind}_profile_{account_slug(email)}_{identity}"


# Shown on the portal's login page (and as the window title) until the account is
# signed in, so the operator never has to guess which account a window wants.
LOGIN_BANNER_JS = """(() => {
  const label = __LABEL__, loggedIn = __LOGGED_IN__;
  function show() {
    const old = document.getElementById("ai-checker-login-banner");
    if (location.href.includes(loggedIn)) { if (old) old.remove(); return; }
    if (!document.body || old) return;
    const banner = document.createElement("div");
    banner.id = "ai-checker-login-banner";
    banner.textContent = label;
    banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1d3557;color:#fff;" +
      "font:bold 18px/1.4 Arial,sans-serif;padding:14px 20px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.4)";
    document.body.prepend(banner);
    document.title = label;
  }
  setInterval(show, 1000);
  if (document.readyState !== "loading") show(); else document.addEventListener("DOMContentLoaded", show);
})();"""


def _runtime_settings(engine):
    return getattr(getattr(engine, "_RUNTIME", None), "settings", {}) or {}


def _portal_accepts_login(engine):
    """The engine's login check, except that only 401/403 mean the portal ended the login.
    The supplied check treated every error reply as a cancelled login and wiped the window's
    saved login, so a busy portal (429, 5xx; more likely with many windows) logged windows
    out by itself (operator's report, 26 Oct 2026, build 138). Each outcome is logged with
    the window's account, so aichecker.log shows which one happened."""
    def accepts(page):
        token = page.evaluate("() => localStorage.getItem('access_token')")
        if not token:
            return True
        account = _runtime_settings(engine).get("ACCOUNT_LABEL", "this window")
        try:
            response = page.request.get(engine.SESSION_CHECK_URL, headers={"authorization": token},
                                        timeout=engine.DEFAULT_TIMEOUT_MS)
        except engine.PWError as exc:
            engine.log.warning("Could not check the portal login of %s (%s); the login is kept.", account, engine.first_line(exc))
            return True
        if response.ok:
            return True
        if response.status in (401, 403):
            engine.log.warning("The portal ended the login of %s (HTTP %s).", account, response.status)
            return False
        engine.log.warning("The portal answered HTTP %s to the login check of %s (busy or failing); the login is kept.",
                           response.status, account)
        return True
    return accepts


class Portal:
    """The supplied Data_Filler engine driven one copy at a time.

    One Chrome window per account stays open for the whole run, as in the supplied
    tool: the admin window assigns copies, and one evaluator window per evaluator
    account uploads checked copies, posts rejections and reads portal documents
    (the portal keeps one live session per account, so the same account never
    runs in two windows, while different evaluators get windows of their own).
    Reopening Chrome for every copy raced the previous window's shutdown and
    failed with ECONNREFUSED on the debugging port (first live run).

    With a desk's PortalWindows (build 140) the windows run hidden (Chrome's headless
    mode, same profiles and logins) and one comes on screen, at the page it was on,
    when it needs a person (PortalWindows); without one they are on screen as before.
    """
    SESSIONS = {"admin": (9237, "admin_profile"), "evaluator": (9238, "evaluator_profile")}
    CONNECT_ATTEMPTS = 3

    def __init__(self, root, evaluator="", evaluator_port=None, admin="", admin_port=None):
        from portal_vendor import data_filler as engine
        from portal_vendor import match_paper as papers
        from portal_vendor import rejection_browser as browser
        from portal_vendor import rejection_comments as rejections
        self.engine, self.rejections, self.papers, self.root = engine, rejections, papers, root
        self.browser = browser   # the supplied Pending/Evaluated lookups and no-resend upload check
        self.alert_hook = None   # set by the desk: called when this window waits for a portal login
        self.login_password = None   # set by the desk: the saved portal password for an account ("" when none)
        self.login_report = None     # set by the desk: (account, ok, detail, refused) after each automatic login
        self.direct_clients, self.direct_off = {}, ""   # portal_api sessions by account; why direct requests stopped
        self.direct_refusals = 0     # in a row: a refused request saved nothing, so one is not worth stopping for
        self.board = None            # set by the desk: the PortalWindows saying which windows are on screen (build 140)
        if not getattr(engine, "_workflow_status_dispatch", False):
            # The engine reports "waiting for you to log in" through one module-level
            # function; route it to the hook of the desk (thread) that is waiting.
            original = engine.on_status
            def dispatch(message, _original=original, _engine=engine):
                hook = getattr(getattr(_engine, "_RUNTIME", None), "settings", {}).get("on_status")
                (hook or _original)(message)
            engine.on_status = dispatch
            engine._workflow_status_dispatch = True
        if not getattr(engine, "_workflow_login_check", False):
            engine.server_accepts_login = _portal_accepts_login(engine)
            engine._workflow_login_check = True
        # Unattended runs keep several Chrome windows busy on one machine; the supplied
        # tool's 15 s produced false "Programs not visible" failures and lost confirmations
        # under that load (live run of 22 October).
        engine.DEFAULT_TIMEOUT_MS = max(int(getattr(engine, "DEFAULT_TIMEOUT_MS", 0) or 0), WORKFLOW_PORTAL_TIMEOUT_MS)
        self.download = download_exam_file
        self.sessions = {}
        self.evaluator = (evaluator or "").strip()
        self.admin = (admin or "").strip()
        self.should_stop = lambda: False
        self.windows = dict(self.SESSIONS)
        if self.admin:
            self.windows["admin"] = (admin_port or self.SESSIONS["admin"][0],
                                     account_profile("admin", self.admin))
        if self.evaluator:
            self.windows["evaluator"] = (evaluator_port or self.SESSIONS["evaluator"][0],
                                         account_profile("evaluator", self.evaluator))
        self.playwright = None   # ONE instance per run: a second sync_playwright() in the
        self.connect_delay = 1.5  # same thread is refused ("Sync API inside the asyncio loop")
    def on_login_wait(self, kind, message):
        """The engine is about to wait for someone to log in to this window (it has already
        opened the portal and dropped a login the portal ended): the window signs itself in
        when a portal password is saved (build 138); otherwise the operator is alerted. A
        sign-in that missed without the portal refusing the password is tried again by
        itself; a hidden window that needs a person comes on screen first (build 140)."""
        if "log in" not in str(message).lower():
            return
        page = (getattr(self, "sessions", {}).get(kind) or {}).get("page")
        signed_in = self.auto_login(kind, page) if page is not None else False
        if signed_in:
            return
        label = self.account_label(kind)
        if signed_in is None:
            # The copy waits (WorkflowStore.portal_wait) and the next step signs in again.
            raise PortalLoginWait(f"The portal window of {label} could not sign itself in this time; it tries again by itself.")
        if callable(getattr(self, "alert_hook", None)):
            account = (self.admin if kind == "admin" else self.evaluator) or ""
            try:
                self.alert_hook("ADMIN" if kind == "admin" else "EVALUATOR", account)
            except Exception:  # noqa: BLE001 - an alert never interrupts the login wait
                pass
        if self.need_screen(kind, "sign_in", f"Sign in here with {label}"):
            # Nobody can sign in to a hidden window: it comes on screen when this step ends
            # (arrange_window) and the copy waits for the login.
            raise PortalLoginWait(f"The portal window of {label} is on screen now: sign in there; the run continues by itself.")

    @contextmanager
    def runtime(self, kind):
        """The vendor helpers share one module, but each desk owns its settings."""
        e = self.engine
        port, profile = self.windows[kind]
        def no_terminal_prompt(message):
            raise PortalLoginWait(f"{' '.join(str(message).split())} Sign in there with {self.account_label(kind)}; "
                                  "the run continues by itself.")
        settings = dict(CDP_PORT=port, USER_DATA_DIR=self.root / profile,
                        SCREENSHOT_DIR=self.root / "screenshots", CONFIRM_FIRST_N=0,
                        should_stop=getattr(self, "should_stop", lambda: False), ask_continue=no_terminal_prompt,
                        on_status=lambda message: self.on_login_wait(kind, message),
                        ACCOUNT_LABEL=self.account_label(kind))
        if hasattr(e, "runtime_context"):
            with e.runtime_context(**settings):
                yield
        else:  # compatibility with injected portal engines
            for key, value in settings.items():
                setattr(e, key, value)
            yield

    def session(self, kind):
        with self.runtime(kind):
            return self._session(kind)

    def _session(self, kind, login=True):
        e = self.engine
        live = self.sessions.get(kind)
        if live is not None:
            try:
                if not live["page"].is_closed() and live["browser"].is_connected() and not live.get("crashed"):
                    return live
            except Exception:  # noqa: BLE001 - a dead connection is reopened below
                pass
            if live.get("crashed"):
                self.close_window(kind)   # a fresh Chrome on the same profile: the login is kept
            else:
                self.close(kind)
        port, profile = self.windows[kind]
        if self.playwright is None:
            self.playwright = e.sync_playwright().start()
        shown, failure = self.wants_screen(kind), None
        for attempt in range(self.CONNECT_ATTEMPTS):
            with self.window_mode(shown):
                chrome = e.start_chrome()
            try:
                browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                break
            except Exception as exc:  # noqa: BLE001 - the port was still closing or not up yet
                failure = exc
                if chrome is not None:
                    try:
                        chrome.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(self.connect_delay)
        else:
            raise RuntimeError(f"Could not connect to the {kind} Chrome window on port {port} after {self.CONNECT_ATTEMPTS} attempts: {failure}")
        context = browser.contexts[0]
        context.set_default_timeout(e.DEFAULT_TIMEOUT_MS)
        page = context.pages[0] if context.pages else context.new_page()
        self.announce_login(context, kind)
        live = {"browser": browser, "context": context, "page": page, "chrome": chrome,
                "shown": self.window_shown(kind, chrome, shown)}
        self.sessions[kind] = live
        self.watch_crash(page, live)
        self.window_event(kind, "opened", live["shown"])
        if not login:
            return live
        try:
            e.wait_for_manual_login(page)
        except PortalWait:
            raise   # the window stays open for the login; the next attempt finds it signed in
        except BaseException:
            self.close(kind)
            raise
        self.window_event(kind, "signed_in")
        return live

    # ------------------------------------------------- crashed pages (build 143)
    REFRESH_EVERY_STEPS = 25   # the portal loaded afresh every so many steps: its page grows over hours

    @staticmethod
    def watch_crash(page, live):
        try:
            page.on("crash", lambda *args: live.__setitem__("crashed", True))
        except Exception:  # noqa: BLE001 - the error text is checked as well
            pass

    @staticmethod
    def page_crashed(live, exc):
        return bool(live.get("crashed")) or "Target crashed" in str(exc)

    def window_crashed(self, kind, live):
        """Mark this window's page dead, so the next step opens a fresh window (_session)."""
        live["crashed"] = True
        return PortalWindowCrashed(f"The portal window of {self.account_label(kind)} crashed (its page ran out of "
                                   "memory); it is opened again and the copy tried again")

    def refresh_now_and_then(self, live):
        """Every REFRESH_EVERY_STEPS steps, load the portal afresh: the portal's page keeps what it
        loaded for hours, and pages that had grown crashed (28 Oct 2026). Never raises."""
        live["steps"] = live.get("steps", 0) + 1
        if live["steps"] % self.REFRESH_EVERY_STEPS:
            return
        try:
            live["page"].goto(self.engine.BASE_URL, wait_until="domcontentloaded")
            live["page"].get_by_text("Task Management", exact=True).first.wait_for(state="visible", timeout=15_000)
        except Exception:  # noqa: BLE001 - the step's own login check and navigation follow
            pass

    # ------------------------------------------------- hidden windows (build 140)
    WINDOW_MODE_FILE = "aichecker-window-mode.txt"

    def window_id(self, kind):
        return kind, ((self.admin if kind == "admin" else self.evaluator) or "")

    def wants_screen(self, kind):
        board = getattr(self, "board", None)
        return True if board is None else board.wanted(*self.window_id(kind))

    def window_mode(self, shown):
        """The engine's launcher starts the window hidden or on screen."""
        e = self.engine
        return e.runtime_context(HEADLESS=not shown) if hasattr(e, "runtime_context") else nullcontext()

    def window_shown(self, kind, chrome, shown):
        """Whether this window is on screen: as it was launched, or for a Chrome an earlier app
        session left open, as that launch recorded it in the profile (every window of the builds
        before 140 was on screen)."""
        marker = self.root / self.windows[kind][1] / self.WINDOW_MODE_FILE
        try:
            if chrome is None:
                return marker.read_text(encoding="utf-8").strip() != "hidden"
            marker.write_text("shown" if shown else "hidden", encoding="utf-8")
        except OSError:
            return shown if chrome is not None else True
        return shown

    def window_event(self, kind, event, *args):
        """Tell the PortalWindows (if any) what happened to this window; never raises."""
        board = getattr(self, "board", None)
        if board is not None:
            try:
                getattr(board, event)(*self.window_id(kind), *args)
            except Exception:  # noqa: BLE001 - the window list is information only
                pass

    def need_screen(self, kind, why, reason):
        """Ask for this window on screen; True when it is hidden now (arrange_window shows it
        once the current step has ended)."""
        if getattr(self, "board", None) is None:
            return False
        self.window_event(kind, "need", why, reason)
        live = self.sessions.get(kind)
        return live is not None and not live.get("shown", True)

    def window_problem(self, kind, row, error, shot, page):
        """A portal step failed in this window. The window comes on screen at that page when
        the operator is needed: it was the copy's last attempt, or the failure stops the run.
        Otherwise the copy tries again and the window stays as it is."""
        if getattr(self, "board", None) is None:
            return
        try:
            url = "" if page.is_closed() else str(page.url)
        except Exception:  # noqa: BLE001
            url = ""
        self.window_event(kind, "note", str(error), Path(shot).name if shot else "", url)
        if int(row.get("failures") or 0) + 1 >= COPY_FAILURE_LIMIT or run_level_cause(str(error)):
            self.need_screen(kind, "problem", f"A portal step failed here: {error}")

    def arrange_windows(self):
        for kind in list(self.sessions):
            self.arrange_window(kind)

    def arrange_window(self, kind):
        """Put this window on screen, or hide it again, as the PortalWindows wants. Chrome
        cannot switch a running window, so it is closed (its login stays in its profile) and
        opened again in the wanted mode at the problem's page or the page it was on. Runs on
        the desk thread between portal steps; never raises, the run goes on either way."""
        live, board, e = self.sessions.get(kind), getattr(self, "board", None), self.engine
        if live is None or board is None:
            return
        shown = self.wants_screen(kind)
        if bool(live.get("shown", True)) == shown:
            return
        try:
            url = board.problem_url(*self.window_id(kind)) if shown else ""
            if not url:
                try:
                    url = str(live["page"].url)
                except Exception:  # noqa: BLE001
                    url = ""
            if not url.startswith(e.BASE_URL):
                url = e.BASE_URL
            with self.runtime(kind):
                self.close_window(kind)
                page = self._session(kind, login=False)["page"]
                page.goto(url, wait_until="domcontentloaded")
                if shown:
                    page.bring_to_front()
        except Exception as exc:  # noqa: BLE001 - the next portal step opens the window again
            log = getattr(e, "log", None)
            if log is not None:
                log.warning("Could not %s the portal window of %s: %s", "show" if shown else "hide",
                            self.account_label(kind), exc)

    def close_window(self, kind):
        """Close this account's Chrome cleanly (its login is kept) and wait until its port and
        profile are free, so that it can open again on them at once."""
        e, live = self.engine, self.sessions.pop(kind, None)
        if live is None:
            return
        chrome = live.get("chrome")
        try:
            if chrome is None:   # a window an earlier app session left open
                if live["browser"].is_connected():
                    live["browser"].new_browser_cdp_session().send("Browser.close")
            else:
                if not live["page"].is_closed():
                    e.close_chrome(live["browser"], chrome, live["page"])
                try:
                    chrome.wait(timeout=getattr(e, "CHROME_CLOSE_TIMEOUT_S", 10))
                except Exception:  # noqa: BLE001 - still running: end it
                    chrome.terminate()
        except Exception:  # noqa: BLE001 - the port check below decides
            pass
        listening = getattr(e, "chrome_is_listening", None)
        deadline = time.monotonic() + getattr(e, "CHROME_CLOSE_TIMEOUT_S", 10)
        while callable(listening) and listening() and time.monotonic() < deadline:
            time.sleep(0.25)
        time.sleep(self.connect_delay)

    AUTO_LOGIN_SECONDS = 30
    # One automatic sign-in at a time across every window (live run of 26 Oct 2026: eight
    # windows signing in at the same moment all failed; the one before them succeeded).
    LOGIN_TURN = threading.Lock()

    def auto_login(self, kind, page):
        """Sign this window in with its account and the portal password saved on this computer
        (build 138), when the portal shows its login form (Email ID, Password, Login). Does
        nothing without a saved password, or for an account whose password the portal refused
        until a password is saved again (a wrong password must not lock the account). True when
        signed in; None when it missed but tries again by itself (build 140); False when a
        person is needed."""
        e = self.engine
        account = ((getattr(self, "admin", "") if kind == "admin" else getattr(self, "evaluator", "")) or "").strip()
        lookup = getattr(self, "login_password", None)
        password = lookup(account) if account and callable(lookup) else ""
        if not password:
            return False
        report = getattr(self, "login_report", None) or (lambda *args: None)
        refused = False
        try:
            with Portal.LOGIN_TURN:
                email = page.locator('input[name="email"], input[type="email"]').first
                email.wait_for(state="visible", timeout=15_000)
                email.fill(account)
                page.locator('input[name="password"], input[type="password"]').first.fill(password)
                page.get_by_role("button", name=re.compile(r"^\s*log\s*in\s*$", re.IGNORECASE)).first.click()
                for _ in range(int(self.AUTO_LOGIN_SECONDS * 2)):
                    if e.site_logged_in(page):
                        e.log.warning("The %s window signed itself in to the portal.", account)
                        report(account, True, "")
                        return True
                    page.wait_for_timeout(500)
                # The login form still there after Login: the portal did not take the password.
                refused = self.login_form_shown(page)
            detail = ("the portal kept its login form after Login: check the portal password saved for this ID" if refused
                      else "the portal did not open after Login")
        except Exception as exc:  # noqa: BLE001 - the manual login wait still applies
            detail = e.first_line(exc) if hasattr(e, "first_line") else str(exc)
        e.log.warning("The %s window could not sign itself in: %s", account, detail)
        # A refused password is never tried again (no lockout); a slow or unsettled portal is,
        # until it has missed too often (PortalLogins.report: False = no person needed yet).
        needs_person = report(account, False, detail, refused)
        return None if needs_person is False else False

    PERSON_WAIT_SECONDS = 300      # one wait for the operator's sign-in, as the engine's own login wait
    SIGN_IN_CHECK_SECONDS = 10     # a saved login shows the portal within this
    SIGN_IN_WAIT_SECONDS = 15 * 60

    def ensure_signed_in(self, kind, page):
        """Before a portal step: the engine's login wait when this window is not signed in (it
        reloads the portal and the window signs itself in), except while the window is on
        screen for the operator to sign in: then it only waits, so a run's next try never
        reloads the page under what they are typing (build 141)."""
        e, board = self.engine, getattr(self, "board", None)
        if e.site_logged_in(page):
            return
        if board is None or not (self.sessions.get(kind) or {}).get("shown") or not board.waiting_for_sign_in(*self.window_id(kind)):
            e.wait_for_manual_login(page)
            return
        if not str(page.url).startswith(e.BASE_URL):
            page.goto(e.BASE_URL, wait_until="domcontentloaded")
        if not self.wait_until_signed_in(page, self.PERSON_WAIT_SECONDS):
            raise PortalLoginWait(f"The portal window of {self.account_label(kind)} is on screen, waiting for you to sign in "
                                  "there; the run continues by itself.")

    def wait_until_signed_in(self, page, seconds):
        e, give_up = self.engine, time.monotonic() + seconds
        while not e.site_logged_in(page):
            if getattr(self, "should_stop", lambda: False)():
                raise e.AbortRun("Stopped by you")
            if time.monotonic() >= give_up:
                return False
            page.wait_for_timeout(1000)
        return True

    def sign_in(self, kind):
        """The operator's "Sign in the windows" (build 141): open this account's window and make
        sure it is signed in. A window whose saved login works stays hidden; one that is not
        signed in signs itself in when it can, and otherwise comes on screen for the operator
        and hides itself once they have signed in there (waiting up to SIGN_IN_WAIT_SECONDS).
        Returns "signed_in", "waiting" or "failed"; never raises."""
        e = self.engine
        try:
            with self.runtime(kind):
                page = self._session(kind, login=False)["page"]
                page.goto(e.BASE_URL, wait_until="domcontentloaded")
                signed = self.wait_until_signed_in(page, self.SIGN_IN_CHECK_SECONDS)
                if signed and not getattr(e, "server_accepts_login", lambda page: True)(page):
                    signed = False   # the dashboard from an ended login: show the login form instead
                    if hasattr(e, "forget_site_login"):
                        e.forget_site_login(page)
                if not signed and self.login_form_shown(page):
                    signed = bool(self.auto_login(kind, page))
                if not signed:
                    self.need_screen(kind, "sign_in", f"Sign in here with {self.account_label(kind)}")
                    self.arrange_window(kind)
                    signed = self.wait_until_signed_in(self.sessions[kind]["page"], self.SIGN_IN_WAIT_SECONDS)
                if not signed:
                    return "waiting"
                self.window_event(kind, "signed_in")
                self.arrange_window(kind)
                return "signed_in"
        except Exception as exc:  # noqa: BLE001 - the run's own steps open the window again
            log = getattr(e, "log", None)
            if log is not None:
                log.warning("Could not open the portal window of %s to sign in: %s", self.account_label(kind), exc)
            return "failed"

    @staticmethod
    def login_form_shown(page):
        try:
            return page.locator('input[name="password"], input[type="password"]').first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    def account_label(self, kind):
        if kind == "admin":
            email = getattr(self, "admin", "")
            return f"the ADMIN account {email}" if email else "the ADMIN account (it assigns copies to evaluators)"
        return f"the EVALUATOR account {self.evaluator}" if self.evaluator else "the EVALUATOR account given in step 2"

    def announce_login(self, context, kind):
        """A banner on the portal's login page and the window title naming the account to sign in with."""
        try:
            script = (LOGIN_BANNER_JS.replace("__LABEL__", json.dumps(f"AI Checker: sign in here with {self.account_label(kind)}"))
                      .replace("__LOGGED_IN__", json.dumps(getattr(self.engine, "LOGGED_IN_URL_PART", "/tev/"))))
            context.add_init_script(script)
            # add_init_script covers later navigations; label the page already open too.
            for page in context.pages:
                page.evaluate(script)
        except Exception as exc:  # noqa: BLE001 - the banner is guidance; the login itself does not depend on it
            log = getattr(self.engine, "log", None)
            if log is not None:
                log.warning("Could not add the login banner: %s", exc)

    def close(self, kind=None):
        for name in ([kind] if kind else list(self.sessions)):
            live = self.sessions.pop(name, None)
            if live is None:
                continue
            self.window_event(name, "closed")
            try:
                if live["chrome"] is not None and not live["page"].is_closed():
                    self.engine.close_chrome(live["browser"], live["chrome"], live["page"])
            except Exception:  # noqa: BLE001 - closing must never fail the run
                pass
        if not self.sessions and self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self.playwright = None

    def perform(self, stage, row, options, pdf=None, report=None, reason="", before_submit=None):
        kind = "admin" if stage in ("assign", "verify_assign") else "evaluator"
        with self.runtime(kind):
            try:
                return self._perform(stage, row, options, pdf, report, reason, before_submit)
            finally:
                self.arrange_window(kind)

    def direct_client(self, kind):
        """This desk's portal_api session. It uses the password the windows sign in with."""
        account = self.admin if kind == "admin" else self.evaluator
        lookup = self.login_password
        password = lookup(account) if account and callable(lookup) else ""
        if not password:
            raise UseTheBrowser(f"No portal password is saved for {account or 'this window'}")
        if account not in self.direct_clients:
            self.direct_clients[account] = PortalApi(account, password)
        return self.direct_clients[account]

    def perform_direct(self, stage, row, options, pdf=None, report=None, reason="", before_submit=None):
        """perform, by the portal's own requests and with no Chrome window (portal_api.py, the
        run option portal_direct). The browser takes over a copy when nothing was changed and
        the client cannot handle it, and takes over for good when marks do not save or
        DIRECT_REFUSALS_ALLOWED copies in a row are refused, so one wrong request cannot repeat
        on every copy. Reading a copy stays direct whatever happened: it changes nothing."""
        intent, clicked = before_submit or (lambda: None), []
        def submitted():
            clicked.append(True)
            intent()
        def browser(why):
            print(f"[direct] {stage} of {row.get('submission_id')} by the browser: {why}", flush=True)
            row["note"] = (str(row.get("note") or "") + " Done through the browser: " + str(why)[:160]).strip()
            return self.perform(stage, row, options, pdf, report, reason, before_submit)
        if self.direct_off and not stage.startswith("verify"):
            return browser(self.direct_off)
        kind = "admin" if stage in ("assign", "verify_assign") else "evaluator"
        started = time.monotonic()
        try:
            client = self.direct_client(kind)
            if stage == "assign":
                outcome = client.assign(row, options, submitted)
            elif stage == "verify_assign":
                outcome = client.verify_assignment(row, options)
            elif stage in ("verify_upload", "verify_reject"):
                outcome = client.verify_evaluation(row)
            else:
                def uploaded(key):
                    row["portal_upload"], row["portal_file"] = True, key
                if stage == "reject":
                    if not str(reason or "").strip():
                        raise ValueError("A rejection comment is required before anything is submitted")
                    outcome = client.submit(row, pdf, {}, 0.0, str(reason).strip(), submitted, uploaded)
                else:
                    outcome = client.submit(row, pdf, portal_marks(report), float(report["total_score"]),
                                            portal_feedback(report), submitted, uploaded)
        except PortalLoginRefused as exc:
            if callable(self.login_report):
                self.login_report(self.admin if kind == "admin" else self.evaluator, False, str(exc), True)
            return browser(exc)
        except CopyMovedOn as exc:
            print(f"[direct] {stage} of {row.get('submission_id')} not posted: {exc}", flush=True)
            return {"result": "moved_on", "status": exc.status, "finished": exc.finished}
        except UseTheBrowser as exc:
            return browser(exc)
        except (PortalBusy, requests.RequestException) as exc:
            if clicked:   # it may have gone through: read from the portal, never sent twice
                raise RuntimeError(str(exc)) from exc
            if isinstance(exc, requests.ConnectionError) and internet_is_down():
                raise InternetWait("this computer cannot reach the internet") from exc
            raise PortalFetchError(f"The portal did not answer: {exc}", retry_after=getattr(exc, "retry_after", None)) from exc
        except PortalApiError as exc:
            self.direct_refusals += 1
            if isinstance(exc, MarksNotSaved) or self.direct_refusals >= DIRECT_REFUSALS_ALLOWED:
                self.direct_off = str(exc)
            print(f"[direct] {stage} of {row.get('submission_id')} refused: {exc}", flush=True)
            if clicked:
                raise RuntimeError(str(exc)) from exc
            return browser(exc)
        self.direct_refusals = 0
        print(f"[direct] {stage} of {row.get('submission_id')}: {time.monotonic() - started:.1f} s", flush=True)
        return outcome

    def _perform(self, stage, row, options, pdf=None, report=None, reason="", before_submit=None):
        e = self.engine
        intent, clicked = before_submit or (lambda: None), []
        def before_submit():
            clicked.append(True)
            intent()
        if stage == "reject":
            if not str(reason or "").strip():
                raise ValueError("A rejection comment is required before anything is submitted")
            # The supplied rejection flow posts the ORIGINAL copy with the reason as
            # the comment and total marks 0; it never fills question marks or ratings.
            record = self.rejections.RejectionComment(
                student_id=row["student_id"], student_name=row.get("student_name") or row["student_id"],
                comment=str(reason).strip(), source="ai-checker", paper_path=pdf)
        else:
            record = e.StudentRecord(
                program=row["program_name"], test_series=row["test_series_name"], test_name=row["test_name"],
                student_id=row["student_id"], evaluator=options["evaluator_email"], deadline=options["deadline"],
                total_marks="", comment="", paper_path=pdf, student_name=row.get("student_name") or "")
        if report:
            record.marks = {q: str(v) for q, v in portal_marks(report).items()}
            record.total_marks = str(report["total_score"])
            record.comment = portal_feedback(report)
            # The supplied uploader allows ratings to be omitted. Do not invent
            # competence ratings from total marks.
            e.validate_record(record)
        # verify_assign reads the admin's Allotment page: the admin window, like assign (it
        # opened the default evaluator window before build 140).
        kind = "admin" if stage in ("assign", "verify_assign") else "evaluator"
        recovery = self.recovery_for(kind)
        if recovery.remaining():
            raise PortalFetchError(recovery.message + "; all copies using this account are waiting together",
                                   retry_after=recovery.remaining())
        live = self.session(kind)
        page, context = live["page"], live["context"]
        outcome, fetch_error = None, False
        failures = ReadFailures(getattr(e, "SESSION_CHECK_URL", ""))
        watch = failures.watch(page)
        watch.__enter__()
        try:
            if recovery.reload_pending:
                recovery.reload_pending = False
                page.goto(e.BASE_URL, wait_until="domcontentloaded")
            else:
                self.refresh_now_and_then(live)
            self.ensure_signed_in(kind, page)  # the portal signed this window out mid-run
            self.window_event(kind, "signed_in")
            if stage == "assign":
                e.open_allotment_test(page, record)
                outcome = self.assign_copy(page, record, before_submit)
            elif stage == "verify_assign":
                e.open_allotment_test(page, record)
                outcome = self.verify_assignment(page, record)
            elif stage in ("verify_upload", "verify_reject"):
                self.require_evaluator_login(page, options.get("evaluator_email", ""))
                outcome = self.verify_evaluation(page, record)
            elif stage == "reject":
                self.require_evaluator_login(page, options.get("evaluator_email", ""))
                e.open_evaluate(page, row["program_name"])
                # verify_row keeps the integrated-workflow identity guard on the
                # located Pending row; before_submit persists the attempt right
                # before the click so a lost confirmation is never resubmitted.
                def verify_row(located, student_id):
                    e.verify_student_row(located, student_id, page=page, student_name=row.get("student_name") or "")
                self.rejections.submit_rejection(
                    page, record, row["program_name"], row["test_series_name"], row["test_name"],
                    before_submit=before_submit, verify_row=verify_row)
            else:
                self.require_evaluator_login(page, record.evaluator)
                e.open_evaluate(page, record.program)
                def uploaded_then_submit():
                    row["portal_upload"] = True   # the checked copy is on the portal from here on
                    before_submit()
                if row.get("portal_upload"):
                    self.evaluate_copy(page, context, record, row, uploaded_then_submit)
                else:
                    e.evaluate_student(page, context, record, False, before_submit=uploaded_then_submit)
        except PortalWait:
            raise
        except e.AbortRun as exc:
            if not clicked and getattr(e, "SITE_LOGIN_EXPIRED", "Unable to fetch task") in str(exc):
                fetch_error = True
                raise self.fetch_failed(page, row, kind, failures) from exc
            self.window_problem(kind, row, str(exc), "", page)
            raise
        except Exception as exc:
            if self.page_crashed(live, exc):
                crashed = self.window_crashed(kind, live)
                if not clicked:
                    raise crashed from exc
                # After the Submit click the copy is checked on the portal (uncertain) as usual.
            elif not clicked and (failures.detail or self.fetch_toast_visible(page)):
                fetch_error = True
                raise self.fetch_failed(page, row, kind, failures) from exc
            # The next live failure must be diagnosable: a picture of the portal at
            # that moment goes to workflow_data/screenshots.
            shot = "" if page.is_closed() or live.get("crashed") else e.save_screenshot(page, row["student_id"])
            note = f" (screenshot: {Path(shot).name})" if shot else ""
            self.window_problem(kind, row, f"{exc}{note}", shot, page)
            raise RuntimeError(f"{exc}{note}") from exc
        finally:
            watch.__exit__(None, None, None)
            if not fetch_error and not page.is_closed() and not live.get("crashed"):
                e.reset_page(page, context)
        recovery.succeeded()
        return outcome

    def review(self, program, wanted=None, report=lambda line: None, before_submit=None):
        """Approval Management > Review: approve copies of `program` with grade A (12 Nov 2026).

        `wanted` is the set of student ids this run uploaded, so other people's copies in the
        same program are left alone; None approves the whole Pending list. Marks and comments
        are never changed. Returns {"counts", "lines"}; a copy that fails is reported and the
        rest go on, and one whose Submit could not be confirmed is never submitted again.
        """
        from portal_vendor import review_copies as reviews
        kind = "evaluator"   # a reviewer is just a signed-in window of its own account
        live = self.session(kind)
        page = live["page"]
        self.ensure_signed_in(kind, page)
        self.window_event(kind, "signed_in")
        program_id = reviews.open_review(page, program)
        copies = reviews.pending_copies(page, program_id, wanted)
        counts = Counter()
        for number, copy in enumerate(copies, 1):
            if getattr(self, "should_stop", lambda: False)():
                raise self.engine.AbortRun("Stopped by you")
            self.engine.on_progress("Approving copies", number, len(copies))
            try:
                report(reviews.approve_copy(page, copy, before_submit=before_submit))
                counts["approved"] += 1
            except self.engine.AbortRun:
                raise
            except reviews.SubmissionUnverified as exc:
                counts["unverified"] += 1
                report(f"UNVERIFIED {copy.label}: {exc}")
            except LookupError as exc:
                counts["skipped"] += 1
                report(f"SKIP {copy.label}: {exc}")
            except Exception as exc:  # noqa: BLE001 - one copy never stops the rest
                counts["failed"] += 1
                report(f"FAIL {copy.label}: {self.engine.first_line(exc)}")
        return {"counts": dict(counts), "pending": len(copies)}

    def recovery_for(self, kind):
        if not hasattr(self, "fetch_recovery"):
            self.fetch_recovery = {}
        return self.fetch_recovery.setdefault(kind, FetchRecovery())

    def fetch_toast_visible(self, page):
        try:
            return page.get_by_text(getattr(self.engine, "SITE_LOGIN_EXPIRED", "Unable to fetch task"), exact=False).first.is_visible()
        except Exception:
            return False

    def fetch_failed(self, page, row, kind="admin", failures=None):
        """Pause the entire account. Reload once after cooldown, never on each queued copy."""
        e, shot = self.engine, ""
        recovery = self.recovery_for(kind)
        recovery.failed(PORTAL_FETCH_DELAYS, getattr(failures, "detail", ""), getattr(failures, "retry_after", 0))
        try:
            if not page.is_closed():
                now = time.monotonic()
                if now - recovery.shot_at >= 600:
                    recovery.shot_at = now
                    shot = e.save_screenshot(page, f"unable_to_fetch_{row.get('student_id', '')}")
        except Exception:  # noqa: BLE001 - the next attempt opens the page again anyway
            pass
        note = f" (screenshot: {Path(shot).name})" if shot else ""
        if hasattr(e, "log"):
            e.log.warning("Portal list recovery for %s: %s; account cooldown %.0f s", self.account_label(kind),
                          recovery.message, recovery.remaining())
        if getattr(self, "board", None) is not None:
            self.board.note(*self.window_id(kind), recovery.message, shot, str(page.url))
        return PortalFetchError(recovery.message + note, retry_after=recovery.remaining())

    UNASSIGNED, REASSIGNABLE = "Submitted", "In Evaluation"

    def assign_copy(self, page, record, before_submit):
        """Get one copy assigned to the record's evaluator, whatever the portal shows for it.

        Submitted: assigned. In Evaluation for this evaluator: left as it is. In
        Evaluation for another evaluator: reassigned (the row ticked, the evaluator and
        deadline set, Submit; the intent marker runs right before the click). Any
        later status (In Review, Finished...): nothing to do, the portal already holds
        this copy's evaluation. Not listed under any status: the copy stops with the
        portal screenshot. Returns {"result", "status", "evaluator"} with result one
        of assigned, already, reassigned, finished.
        """
        e = self.engine
        found = self.locate_copy(page, record)
        if found is None:
            raise ValueError(f"Student {record.student_id} is not listed under any status of this test on the Allotment page")
        row, status = found
        cells = row.locator("td")
        evaluator = " ".join(cells.nth(3).inner_text().split()) if cells.count() > 3 else ""
        if e._status_pattern(self.UNASSIGNED).fullmatch(status):
            if e.allot_student(page, record, False, before_submit=before_submit) is not True:
                raise ValueError("The portal assignment changed while processing. Verify this evaluator before confirming the assignment.")
            return {"result": "assigned", "status": status, "evaluator": record.evaluator}
        if not e._status_pattern(self.REASSIGNABLE).fullmatch(status):
            return {"result": "finished", "status": status, "evaluator": evaluator}
        if e._email_option_matches(evaluator, record.evaluator):
            return {"result": "already", "status": status, "evaluator": evaluator}
        box = row.locator(e.SEL["row_checkbox"]).first
        box.check()
        deadline = page.locator(e.SEL["deadline"]).first
        deadline.wait_for(state="visible")   # Evaluator + Deadline appear only after a row is ticked
        option = e.select_evaluator(page, record.evaluator)
        if e._evaluator_text_matches(evaluator, option):
            box.uncheck()   # the row shows this evaluator by name: nothing to change
            return {"result": "already", "status": status, "evaluator": evaluator}
        deadline.fill(record.deadline)
        before_submit()
        if not e.submit_bundle(page, deadline):
            raise ValueError(f"The portal did not acknowledge the reassignment of {record.student_id} to {record.evaluator}")
        return {"result": "reassigned", "status": status, "evaluator": evaluator}

    def locate_copy(self, page, record):
        """The copy's row and shown status on the Students page, looking under each status filter in turn."""
        e = self.engine
        for status in self.status_filters(page):
            try:
                e.choose_status(page, status)
            except LookupError:
                if status != self.UNASSIGNED:   # a page without the filter has one list, searched as it is
                    continue
            e.search(page, record.student_id)
            rows = page.locator(e.SEL["data_rows"]).filter(has=page.locator(e.SEL["row_checkbox"]))
            count = rows.count()
            if count == 0:
                continue
            if count > 1:
                raise ValueError(f"Student ID search returned {count} rows under '{status}' (expected 1)")
            e.verify_student_row(rows.first, record.student_id, page=page, student_name=record.student_name)
            shown = " ".join(rows.first.locator("td").last.inner_text().split()) or status
            return rows.first, shown
        return None

    def status_filters(self, page):
        """The Students page's status filter options, Submitted and In Evaluation first."""
        try:
            selects = page.locator("select")
            for i in range(selects.count()):
                options = [o.strip() for o in selects.nth(i).locator("option").all_inner_texts()]
                if self.UNASSIGNED in options:
                    rest = [o for o in options if o and o not in (self.UNASSIGNED, self.REASSIGNABLE)]
                    return [self.UNASSIGNED, self.REASSIGNABLE] + rest
        except Exception:  # noqa: BLE001 - no readable filter: the two statuses the engine knows
            pass
        return [self.UNASSIGNED, self.REASSIGNABLE]

    def evaluate_copy(self, page, context, record, row, before_submit):
        """The supplied evaluate_student, except that a checked copy this run already put on
        the portal (an attempt whose confirmation was lost) is not uploaded again."""
        e = self.engine
        stage = "find student"
        try:
            located = e.find_evaluate_row(page, record)
            stage = "upload"
            if self.browser.has_view_upload(page, located):
                e.log.info("The checked copy of %s is already on the portal; not sending it again.", record.student_id)
            else:
                e.upload_paper(page, located, record.paper_path)
            stage = "open View Upload"
            e.click_menu_item(page, located, "View Upload")
            stage = "fill marks"
            e.fill_evaluation_modal(page, record, False, before_submit)
        except e.AbortRun:
            raise
        except Exception as exc:
            raise RuntimeError(f"[{stage}] {exc}") from exc

    def verify_evaluation(self, page, record):
        """After a lost confirmation: did the portal take the submission? "done" when the copy
        has left the Pending list (In Review, Evaluated, Re Assigned), "not_done" while it is
        still Pending, "unknown" otherwise. Nothing is submitted."""
        e, browser = self.engine, self.browser
        e.open_evaluate(page, record.program)
        try:
            located = browser.find_pending_row(page, record.program, record.test_series, record.test_name, record.student_id)
        except LookupError as exc:
            text = str(exc)
            if "Not Pending: matching test is in" in text:
                return {"result": "done", "detail": text.split(".")[0]}
            return {"result": "unknown", "detail": e.first_line(exc)}
        e.verify_student_row(located, record.student_id, page=page, student_name=record.student_name)
        return {"result": "not_done", "detail": "still in the Pending list"}

    def verify_assignment(self, page, record):
        """After a lost confirmation on the Allotment page: "done" when the copy is In Evaluation
        with this evaluator or in a later status, "not_done" while Submitted or with another
        evaluator (the next attempt assigns or reassigns), "unknown" when it is not listed."""
        e = self.engine
        found = self.locate_copy(page, record)
        if found is None:
            return {"result": "unknown", "detail": "not listed under any status of the test"}
        located, status = found
        if e._status_pattern(self.UNASSIGNED).fullmatch(status):
            return {"result": "not_done", "detail": "still Submitted"}
        cells = located.locator("td")
        evaluator = " ".join(cells.nth(3).inner_text().split()) if cells.count() > 3 else ""
        if not e._status_pattern(self.REASSIGNABLE).fullmatch(status):
            return {"result": "done", "detail": f"status {status}"}
        if e._email_option_matches(evaluator, record.evaluator):
            return {"result": "done", "detail": f"In Evaluation with {evaluator}"}
        box = located.locator(e.SEL["row_checkbox"]).first
        box.check()
        page.locator(e.SEL["deadline"]).first.wait_for(state="visible")
        option = e.select_evaluator(page, record.evaluator)
        box.uncheck()
        if e._evaluator_text_matches(evaluator, option):
            return {"result": "done", "detail": f"In Evaluation with {evaluator}"}
        return {"result": "not_done", "detail": f"In Evaluation with {evaluator or 'another evaluator'}"}

    def signed_in_email(self, page):
        """The email of the account signed in to this window, from the portal's saved user
        details; "" when they are absent or hold no email."""
        try:
            raw = page.evaluate("() => localStorage.getItem('user-details') || ''")
            details = json.loads(raw) if isinstance(raw, str) and raw else {}
        except Exception:  # noqa: BLE001 - nothing readable: nothing to compare
            return ""
        found = []
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and "@" in item and ("email" in key.lower() or "username" in key.lower()):
                        found.append(item.strip())
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(details)
        return found[0] if found else ""

    def require_evaluator_login(self, page, email):
        """Uploads and rejections only make sense from the run's evaluator account."""
        who = self.signed_in_email(page)
        if who and email and who.strip().lower() != email.strip().lower():
            raise ValueError(f"The evaluator window is signed in as {who}, but this run's evaluator is {email}. "
                             f"Sign out in that window (top right) and sign in as {email}; the run retries by itself.")

    def exam_documents(self, test, progress=lambda text: None):
        """The selected test's question paper and Model Answer files, read from the
        portal's own test-details request through the evaluator's Chrome login (the
        supplied match_paper helpers; read-only). Returns
        {"question_paper": [(name, bytes)], "answer_key": [(name, bytes)]}."""
        with self.runtime("evaluator"):
            try:
                return self._exam_documents(test, progress)
            finally:
                self.arrange_window("evaluator")

    def _exam_documents(self, test, progress):
        live = self.session("evaluator")
        try:
            return self._read_exam_documents(live["page"], test, progress)
        except PortalWait:
            raise
        except Exception as exc:
            if self.page_crashed(live, exc):
                raise self.window_crashed("evaluator", live) from exc
            raise

    def _read_exam_documents(self, page, test, progress):
        e, papers = self.engine, self.papers
        self.ensure_signed_in("evaluator", page)
        self.window_event("evaluator", "signed_in")
        progress("Reading the test's files from the portal")
        details = papers.api_get(page, papers.TEST_URL.format(test=test["test_id"])).get("data") or {}
        return self.documents_of(details, test, progress)

    def exam_documents_direct(self, test, progress=lambda text: None):
        """exam_documents with no Chrome window: the same details by portal_api."""
        try:
            details = self.direct_client("evaluator").details(test["test_id"])
        except (UseTheBrowser, PortalApiError) as exc:
            if isinstance(exc, PortalBusy):
                raise PortalFetchError(str(exc), retry_after=exc.retry_after) from exc
            print(f"[direct] question paper by the browser: {exc}", flush=True)
            return self.exam_documents(test, progress)
        return self.documents_of(details, test, progress)

    def documents_of(self, details, test, progress):
        e, papers = self.engine, self.papers
        # Identity guard: the details must be the selected test's, not another
        # id's. A differently named test stops here; the operator can upload.
        portal_name = " ".join(str(details.get("name") or "").split())
        if portal_name and not e.whole_label(test["test_name"]).search(portal_name):
            raise ValueError(f"The portal names test {test['test_id']} '{portal_name}', not '{test['test_name']}'; upload the PDFs instead")
        solutions, question = papers.site_files(details)
        fetched = {"question_paper": [], "answer_key": [], "links": {"question_paper": question, "answer_key": solutions}}
        for kind, urls in (("question_paper", [question] if question else []), ("answer_key", solutions)):
            for url in urls:
                name = Path(urlparse(url).path).name or f"{kind}.pdf"
                progress(f"Downloading {name}")
                fetched[kind].append((name, self.download(url)))
        return fetched


# Checked copies are also kept, named after the student, in a folder per batch and test next to
# the app (operator's request, 29 Oct 2026, build 146): "Checked copies/<batch> - <test>/", and
# "Faculty review" inside it for the copies the checker flags or holds for faculty review.
CHECKED_COPIES_DIRNAME = "Checked copies"


def _file_safe(text, limit):
    text = " ".join(re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(text or "")).split()).strip(" .")
    return text[:limit].rstrip(" .")


def batch_name(test_series):
    """The batch of a portal test series: "VICTORY 2027 (Class 10th) - Batch Tests" -> "VICTORY 2027 (Class 10th)"."""
    series = str(test_series or "").strip()
    return re.sub(r"\s*[-\u2013]\s*batch\s+tests?\s*$", "", series, flags=re.IGNORECASE).strip() or series


def checked_copies_folder(root, row):
    name = _file_safe(f"{batch_name(row.get('test_series_name'))} - {row.get('test_name') or ''}", 150)
    return Path(root) / (name or "Untitled test")


def nothing_left(row):
    """The sheet says this copy needs nothing more. A copy that could not be downloaded is tried
    again whenever its test is run again (operator, 30 Oct 2026), if its link is a good one."""
    if not terminal_status(row):
        return False
    if normalized_status(row.get("workflow_status")) != "not downloadable file":
        return True
    try:
        validate_copy_url(row["copy_url"])
    except ValueError:
        return True
    return False


def same_document(first, second):
    """Two PDFs of the same pages. A Model Answer the portal gives in parts is merged again on
    every fetch, and each merge gets a new file id, so equal bytes alone would miss it."""
    if first == second:
        return True
    import fitz
    try:
        with fitz.open(stream=first, filetype="pdf") as one, fitz.open(stream=second, filetype="pdf") as other:
            return one.page_count == other.page_count and all(
                a.get_text() == b.get_text() and a.get_pixmap(dpi=36).samples == b.get_pixmap(dpi=36).samples
                for a, b in zip(one, other))
    except Exception:  # noqa: BLE001 - not comparable: treated as a different paper, so checked again
        return False


def checked_file_name(row):
    """TANYA_SINGH_23217614.pdf, like the checker's student-named PDFs."""
    name = _file_safe(row.get("student_name"), 60).upper().replace(" ", "_")
    ident = _file_safe(row.get("student_id") or row.get("submission_id"), 40)
    return "_".join(part for part in (name, ident) if part) + ".pdf"


# Workflow tabs/runs have no fixed count limit. Grading concurrency is separate.
COPIES_IN_FLIGHT = 5       # copies of one run processed at the same time (default; a run option)
MAX_COPIES_IN_FLIGHT = 8
# Copies graded at the same time on this computer, across all runs (the autopilot's option). In one
# process a copy's own work queues behind one render lock and one interpreter (grading_engine.py: past
# about 8 at a time, more only made each wait), so more here adds memory, not speed.
GRADING_SLOTS = 10
MAX_GRADING_SLOTS = 30
MIN_FREE_MEMORY_MB = 1200  # below this no further copy starts: each one holds its page images
# Retry each student independently, then skip it and keep processing the others.
# A brief wait avoids hammering a failed portal page and is interruptible by Stop.
COPY_RETRY_DELAY_SECONDS = 1.0
ASSIGN_HEAD_START_SECONDS = 60   # checking waits this long at most for the assigning lane before taking unassigned copies
DIRECT_REFUSALS_ALLOWED = 3      # copies refused in a row before an ID goes back to the browser for good
UNSTARTED_RUN_SECONDS = 60       # a run created this long ago and never started has been left: a new run may take its copies
COPY_FAILURE_LIMIT = 3
# A 254-page upload held the checking of every other copy back for about 50 minutes on
# 31 Oct 2026. The longest real copy on the computer then ran to 44 pages.
MAX_COPY_PAGES = 100
# A copy the checker sends to faculty review is uploaded too when it has marks, with a
# faculty-review flag and its reasons (operator's rule, 26 Oct 2026, build 136). A run may
# still hold copies below a confidence it chooses (review_min_confidence; 0 uploads all).
REVIEW_UPLOAD_THRESHOLD = 0
# The portal's "Unable to fetch task" (its task list did not load) is not the copy's fault:
# the copy waits and tries again without using an attempt, for as long as the run goes on,
# and the operator is alerted once it has lasted PORTAL_FETCH_ALERT_SECONDS.
PORTAL_FETCH_DELAYS = (20, 40, 60, 120)
# A portal window whose page crashed is opened again at once; the copy waits this long and
# tries again, without using an attempt, up to this many times (build 143).
WINDOW_CRASH_DELAY_SECONDS = 10
WINDOW_CRASH_RETRIES = 3
PORTAL_FETCH_ALERT_SECONDS = 600
# Before a run finishes it tries again by itself (operator's rule, 26 Oct 2026, build 137):
# copies skipped after three failed attempts get another round after each of these waits,
# and portal actions whose confirmation was lost are checked on the portal again (read
# only). A copy skipped at the checking step gets one automatic round only, as each
# attempt there is a paid check. Resume / retry starts the rounds afresh.
AUTO_RETRY_DELAYS = (120, 600, 1800)
# A run this app was running when it closed continues by itself at the next sign-in, when
# it was saved within this time; an older one waits for Resume / retry.
AUTO_RESUME_WITHIN_SECONDS = 24 * 3600
UNCERTAIN_STAGES = ("assignment_uncertain", "upload_uncertain", "rejection_uncertain")


def _qnorm(text):
    value = re.sub(r"[^a-z0-9]", "", str(text).lower())
    return value if value.startswith("q") else "q" + value


_QID_TOKEN = re.compile(r"(?<![A-Za-z0-9])Q\s?\d+(?:\s?(?:\.\s?[A-Za-z0-9]+|\(\s?[A-Za-z0-9]+\s?\)))*", re.IGNORECASE)


def _questions_named(text, qids):
    """The paper's questions a review reason names ("Q18" names Q18.i, Q18.ii...; "Q1" never Q10)."""
    named = set()
    for token in _QID_TOKEN.findall(str(text or "")):
        mention = _qnorm(token)
        for qid in qids:
            full = _qnorm(qid)
            if full == mention or (full.startswith(mention) and not full[len(mention)].isdigit()):
                named.add(qid)
    return named


def review_confidence(report, reasons=None, legacy_text=""):
    """How confident the checker is in a copy it sent to faculty review, in percent:
    the share of the paper's marks that no review reason puts in doubt. A reason
    that names no question (a cover problem, an unreadable photo) doubts the whole
    paper. `reasons` is the full list (copies graded from build 135); for older
    copies only the first three reasons survive in the row's message, and each of
    the "and N more" is assumed to doubt the largest question not yet counted.
    None when it cannot be worked out (no report, no reasons)."""
    questions = [(str(q.get("qid")), float(q.get("max_score") or 0)) for q in (report or {}).get("questions") or []
                 if q.get("qid")]
    if not questions:
        return None
    total = float((report or {}).get("max_total") or 0) or sum(mark for _, mark in questions)
    if total <= 0:
        return None
    qids, maxima, hidden = [qid for qid, _ in questions], dict(questions), 0
    flagged = set()
    if reasons:
        for reason in reasons:
            named = _questions_named(reason, qids)
            if not named:
                return 0
            flagged |= named
    else:
        found = re.search(r"Sent for faculty review \((\d+) reason\(s\)\):\s*(.*)", str(legacy_text or ""), re.DOTALL)
        if not found:
            return None
        shown = re.split(r"\s\d+ point\(s\) to check:|\sCost Rs\.|\sAnswer key corrected:", found.group(2))[0]
        more = re.search(r";\s*and (\d+) more\s*$", shown.strip())
        hidden = int(more.group(1)) if more else 0
        flagged = _questions_named(shown, qids)
        if not flagged:
            return 0
    risk = sum(maxima[qid] for qid in flagged)
    for _, mark in sorted(((qid, mark) for qid, mark in questions if qid not in flagged), key=lambda item: -item[1])[:hidden]:
        risk += mark
    return max(0, min(100, int(round(100 * (1 - risk / total)))))


def review_threshold(options):
    """The confidence below which this run holds a faculty-review copy; 0 holds none. Build 135's
    review_upload_threshold is ignored on purpose: its runs resume under the new rule."""
    try:
        return float((options or {}).get("review_min_confidence", REVIEW_UPLOAD_THRESHOLD))
    except (TypeError, ValueError):
        return float(REVIEW_UPLOAD_THRESHOLD)


def review_held(confidence, threshold):
    return threshold > 0 and (confidence is None or confidence < threshold)


def review_flag_note(confidence, threshold, reason):
    shown = f"{confidence}%" if confidence is not None else "not worked out"
    rule = f", at least this run's {threshold:g}%" if threshold > 0 else ""
    return f"Faculty review flagged (confidence {shown}{rule}); uploaded with its marks. The checker asked for a review: {reason[:300]}"


def review_held_note(confidence, threshold):
    if confidence is None:
        return f"Held for faculty review: the checker's confidence could not be worked out (this run holds copies below {threshold:g}%)"
    return f"Held for faculty review: confidence {confidence}% is below this run's {threshold:g}%"


NO_MARKS_NOTE = "Held for faculty review: the checker could not mark this copy, so there are no marks to upload. A teacher checks it on the portal."
# From 29 Oct 2026 such a copy is skipped instead (no faculty to review it).
SKIPPED_NO_MARKS = "Skipped, no marks to upload (Resume / retry checks it again):"


def auto_rejections(options):
    """Rejected copies are posted to the portal as soon as they are found (operator's rule, 26 Oct 2026)."""
    return (options or {}).get("submit_rejections", True) is not False


def notice_for_unreadable(options):
    """A copy whose file cannot be read is posted as a notice page for the student, with 0 marks
    and the reason as the comment (operator, 20 Nov 2026)."""
    return (options or {}).get("notice_for_unreadable", True) is not False


class PortalLogins:
    """The portal password the windows sign in with by themselves (operator, 26 Oct 2026:
    every ID uses the same password; build 138). Kept in workflow_data/portal_login.json,
    encrypted with Windows DPAPI for this Windows user; never sent to the page or logged.
    A password the portal refused is not tried again for that ID until a password is saved
    again, so a wrong password cannot lock the account; the operator is alerted instead. A
    sign-in that missed otherwise (a slow portal) needs the operator too: its window comes
    on screen for them to sign in once (operator, 26 Oct 2026, build 141: eight windows
    could not sign themselves in), but that ID is tried automatically again the next time
    its login ends."""
    MISSES_BEFORE_PERSON = 1

    def __init__(self, root, protect=None, reveal=None, clock=None):
        self.path = Path(root) / "portal_login.json"
        self._protect, self._reveal = protect, reveal
        self.clock = clock or time.time
        self.lock = threading.Lock()
        self.failed, self.recent, self.misses = {}, {}, {}
        self.alert = None   # (account, detail) after an automatic login that needs the operator

    def protect(self, secret):
        if self._protect is not None:
            return self._protect(secret)
        import workflow_alerts
        return workflow_alerts.protect_secret(secret)

    def reveal(self, stored):
        if self._reveal is not None:
            return self._reveal(stored)
        import workflow_alerts
        return workflow_alerts.reveal_secret(stored)

    def data(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def stored(self, account=""):
        """The encrypted password for an ID: its own (from the autopilot's accounts sheet),
        else the one saved for every ID on the page."""
        data = self.data()
        own = (data.get("accounts") or {}).get(str(account or "").strip().lower()) if account else None
        return str(own or data.get("password") or "")

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.path.write_text(json.dumps(data), encoding="utf-8")

    def save(self, password):
        """The password for every ID; an empty one removes every saved password."""
        secret = str(password or "").strip("\r\n")
        data = self.data() if secret else {}
        data["password"] = self.protect(secret) if secret else ""
        self.write(data)
        with self.lock:
            self.failed.clear()   # a new password: every ID may try again
            self.misses.clear()

    def remember(self, account, password):
        """One ID's own password (the autopilot's accounts sheet, build 139)."""
        key, secret = str(account or "").strip().lower(), str(password or "").strip("\r\n")
        if not key or not secret:
            return
        data = self.data()
        data.setdefault("accounts", {})[key] = self.protect(secret)
        self.write(data)
        with self.lock:
            self.failed.pop(key, None)
            self.misses.pop(key, None)

    def configured(self):
        data = self.data()
        return bool(data.get("password") or data.get("accounts"))

    def password_for(self, account):
        key = str(account or "").strip().lower()
        with self.lock:
            if not key or key in self.failed:
                return ""
        stored = self.stored(key)
        if not stored:
            return ""
        try:
            return self.reveal(stored)
        except Exception:  # noqa: BLE001 - another Windows user or a damaged file: sign in by hand
            return ""

    def report(self, account, ok, detail="", refused=True):
        """Record an automatic login; True when the operator is needed for this ID."""
        key = str(account or "").strip().lower()
        stamp = datetime.fromtimestamp(self.clock()).isoformat(timespec="seconds")
        with self.lock:
            self.recent[key] = {"account": account, "ok": bool(ok), "detail": str(detail or "")[:300], "at": stamp}
            if ok:
                self.failed.pop(key, None)
                self.misses.pop(key, None)
            elif refused:
                self.failed[key] = str(detail or "")
                self.misses.pop(key, None)
            else:
                self.misses[key] = self.misses.get(key, 0) + 1
            needs_person = not ok and (refused or self.misses[key] >= self.MISSES_BEFORE_PERSON)
        if needs_person and callable(self.alert):
            try:
                self.alert(account, detail)
            except Exception:  # noqa: BLE001 - an alert never interrupts a login
                pass
        return needs_person

    def status(self):
        with self.lock:
            recent = sorted(self.recent.values(), key=lambda entry: entry["at"], reverse=True)
            suspended = sorted(self.failed)
        return {"configured": self.configured(), "accounts": len(self.data().get("accounts") or {}),
                "recent": recent, "suspended": suspended}


class PortalWindows:
    """Which portal Chrome windows are on screen (operator's request, 26 Oct 2026; build 140).

    The windows run hidden (Chrome's headless mode with the same profiles and logins), so a
    day of eight admin + evaluator pairs is not sixteen windows on the screen. A window comes
    on screen, at the page it was on, when it needs a person: its ID cannot sign itself in, or
    a copy failed there on its last attempt or in a way that stops its run. It hides itself
    again after a sign-in; a window shown for a problem or by the operator stays until Hide
    (or until it is closed). The workflow page lists the windows with Show / Hide and can keep
    every window on screen (show_all, saved in portal_windows.json)."""

    def __init__(self, root, clock=None):
        self.path = Path(root) / "portal_windows.json"
        self.clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))
        self.lock = threading.Lock()
        self.entries = {}   # "role:account" -> that window's state
        try:
            self.show_all = bool(json.loads(self.path.read_text(encoding="utf-8")).get("show_all"))
        except (OSError, ValueError, AttributeError):
            self.show_all = False

    def entry(self, role, account):   # with self.lock held
        key = f"{role}:{str(account or '').strip().lower()}"
        return self.entries.setdefault(key, {"role": role, "account": str(account or "").strip(), "open": False, "shown": False,
                                             "why": "", "reason": "", "since": "", "problem": "", "screenshot": "", "url": "", "at": "",
                                             "signed_in": False})

    def wanted(self, role, account):
        with self.lock:
            return self.show_all or bool(self.entry(role, account)["why"])

    def opened(self, role, account, shown):
        with self.lock:
            self.entry(role, account).update(open=True, shown=bool(shown))

    def closed(self, role, account):
        """Closed: by the run's end, or by hand (which is a Hide)."""
        with self.lock:
            self.entry(role, account).update(open=False, shown=False, why="", reason="", since="", signed_in=False)

    def need(self, role, account, why, reason):
        """On screen for a sign-in ("sign_in"), a problem ("problem") or the operator ("you");
        a sign-in never replaces a problem or the operator's Show, which outlast it."""
        with self.lock:
            entry = self.entry(role, account)
            if why == "sign_in":
                entry["signed_in"] = False
                if entry["why"] in ("problem", "you"):
                    return
            if entry["why"] != why:
                entry["since"] = self.clock()
            entry.update(why=why, reason=str(reason)[:400])

    def note(self, role, account, problem, screenshot="", url=""):
        """The window's latest problem, with its picture and page; does not show the window."""
        with self.lock:
            self.entry(role, account).update(problem=str(problem)[:400], screenshot=screenshot, url=url, at=self.clock())

    def problem_url(self, role, account):
        with self.lock:
            entry = self.entry(role, account)
            return entry["url"] if entry["why"] == "problem" else ""

    def signed_in(self, role, account):
        with self.lock:
            entry = self.entry(role, account)
            entry["signed_in"] = True
            if entry["why"] == "sign_in":
                entry.update(why="", reason="", since="")

    def waiting_for_sign_in(self, role, account):
        with self.lock:
            return self.entry(role, account)["why"] == "sign_in"

    def hide(self, role, account):
        with self.lock:
            self.entry(role, account).update(why="", reason="", since="")

    def set_show_all(self, show):
        with self.lock:
            self.show_all = bool(show)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"show_all": self.show_all}), encoding="utf-8")

    def status(self):
        with self.lock:
            windows = [{key: value for key, value in entry.items() if key != "url"}
                       for entry in self.entries.values() if entry["open"] or entry["problem"]]
            for window in windows:
                window["wanted"] = self.show_all or bool(window["why"])
            show_all = self.show_all
        windows.sort(key=lambda window: (not window["open"], window["role"] != "admin", window["account"].lower()))
        return {"show_all": show_all, "windows": windows}


class QuietAlerts:
    """Records alerts without sound, notification or email (tests, and before install())."""
    def __init__(self, root=None):
        self.raised, self.acknowledged = [], 0

    def raise_alert(self, key, title, body, to=(), once_within=0.0):
        self.raised.append({"key": key, "title": title, "body": body, "to": sorted(set(to))})
        return True

    def acknowledge(self):
        self.acknowledged += 1

    def save_email_settings(self, sender, app_password):
        self.sender = sender

    def status(self):
        return {"active": self.raised[-1] if self.raised else None, "recent": self.raised[-10:][::-1],
                "email": {"sender": getattr(self, "sender", "evaluator.b4@pw.live"), "configured": False, "ok": None, "detail": "", "at": ""}}


def _alert_center(root):
    import workflow_alerts
    return workflow_alerts.AlertCenter(root)


ALERT_CENTER_FACTORY = _alert_center
WORKFLOW_PORTAL_TIMEOUT_MS = 45_000   # the supplied engine's default is 15 s, tuned for one window at a time

# Errors that come from the run's inputs rather than from one copy: every copy would fail
# the same way, so the pass stops at the first one, charges no copy an attempt, and the
# status line says what to change (Change run inputs, then Resume / retry).
RUN_LEVEL_CAUSES = (
    (("was not found among", "evaluator options"), "the evaluator email is not in this test's evaluator list on the portal"),
    (("Retry marks verification",), "the question paper's marks could not be verified against the official total"),
    (("marks could not be verified",), "the question paper's marks could not be verified against the official total"),
    (("look like answers to a different test",), "many copies of this test look like answers to a different test, "
                                                 "so the question paper given to the checker may be the wrong one"),
)
# The whole-test guard (29 Oct 2026). Copies are rejected without a teacher now, and a
# wrong question paper on the portal (or chosen in step 1) would make every copy of the
# test "another test". Before this many such rejections, and this share of the test's
# decided copies, are posted, the run stops for the operator to check the paper.
PAPER_GUARD_MIN = 3
PAPER_GUARD_SHARE = 0.3
DECIDED_STAGES = ("checked", "uploaded", "upload_uncertain", "rejected", "rejection_submitted", "rejection_uncertain")


def run_level_cause(message):
    text = str(message or "")
    for needles, cause in RUN_LEVEL_CAUSES:
        if all(needle in text for needle in needles):
            return cause
    return None


# The hosts a run needs. Neither in reach means this computer has no internet, not a bad copy.
INTERNET_HOSTS = ("pw-apps-proxy.vercel.app", "d2bps9p1kiy4ka.cloudfront.net")
NETWORK_FAILURE_RE = re.compile(r"NameResolutionError|Failed to resolve|Max retries exceeded|Connection aborted|ConnectTimeout|"
                                r"getaddrinfo failed|Failed to establish a new connection", re.IGNORECASE)


def internet_is_down():
    """Live run of 1 Nov 2026: a 70-second Wi-Fi drop used up the three attempts of 51 copies,
    as each failed at once. A failure counts against a copy only when the internet is there."""
    for host in INTERNET_HOSTS:
        try:
            with socket.create_connection((host, 443), timeout=5):
                return False
        except OSError:
            continue
    return True


def free_memory_mb():
    """Free memory on this computer, or None where it cannot be read."""
    if os.name != "nt":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    try:
        return status.available / (1024 * 1024) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    except Exception:  # noqa: BLE001 - a guard, never a reason to stop
        return None


class GradingSlots:
    """How many copies are graded at once. The limit can change, and while memory is short no
    new copy starts, so a busy computer waits instead of failing. One copy always may run."""
    def __init__(self, limit, free_memory=free_memory_mb):
        self.limit, self.used, self.free_memory = int(limit), 0, free_memory
        self.changed = threading.Condition()

    def available(self):
        if self.used >= self.limit:
            return False
        free = self.free_memory() if self.used else None
        return free is None or free >= MIN_FREE_MEMORY_MB

    def acquire(self, blocking=True, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.changed:
            while not self.available():
                left = None if deadline is None else deadline - time.monotonic()
                if not blocking or (left is not None and left <= 0):
                    return False
                self.changed.wait(min(left, 1.0) if left is not None else 1.0)
            self.used += 1
            return True

    def release(self):
        with self.changed:
            self.used -= 1
            self.changed.notify_all()


class BusyError(ValueError):
    """The run is already active, or an account has a conflicting portal role."""


class PaperMarksProblem(ValueError):
    """The question paper's marks could not be read or do not add up to the official total:
    the run is not started (operator's rule, 26 Oct 2026, build 137)."""
    def __init__(self, check):
        self.check = check
        super().__init__(f"Not started: the question paper's marks need fixing first. {check.get('message') or ''} "
                         "Correct them in the marks editor in step 2 (or enter them yourself), then press Start again.")


class PortalWait(RuntimeError):
    """The portal needs time or a person, not the copy: the copy waits and tries again without
    using one of its attempts (WorkflowStore.portal_wait)."""


class PortalFetchError(PortalWait):
    """The portal showed 'Unable to fetch task' before anything was submitted: its task list
    did not load."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class InternetWait(PortalWait):
    """This computer cannot reach the internet: the copy waits for it without using an attempt."""


class PortalLoginWait(PortalWait):
    """A portal window has waited its full time for someone to log in. The window stays open
    and the copy waits for the login instead of counting a failure (build 137)."""


class PortalWindowCrashed(PortalWait):
    """A portal window's page crashed ("Target crashed": Chrome short of memory) before anything
    was submitted. Live run of 28 Oct 2026: the run kept using the dead page, so every later copy
    of that window failed three times and was skipped (1123 of 1239 copies of one test). The
    window is opened again and the copy tries again without using one of its attempts, up to
    WINDOW_CRASH_RETRIES times per copy (build 143)."""


class UnreadableCopy(ValueError):
    """The portal's file for a copy is not a PDF or a photo the checker can read.

    A copy-level outcome, not a run error (user's decision, 15 Oct 2026): the copy
    is skipped with the reason, and the run goes on to the next one."""


class PortalDesk:
    """One thread owns one Chrome window: the admin window, or one evaluator account's.

    The supplied engine drives Chrome through Playwright's sync API, which must be
    used from the thread that started it, and the portal keeps one live session
    per account. So every portal action for an account is queued here and
    executed in turn in that account's window, while actions for other accounts,
    and every run's downloads and grading, proceed in parallel. The window is
    closed once nothing is active.
    """
    def __init__(self, store, name="admin", evaluator="", port=None, admin=""):
        self.store, self.portal = store, None
        self.name, self.evaluator, self.port, self.admin = name, evaluator, port, admin
        self.queue = queue.SimpleQueue()
        threading.Thread(target=self.serve, name=f"workflow-portal-{name}", daemon=True).start()

    def serve(self):
        while True:
            job, future = self.queue.get()
            try:
                future.set_result(job())
            except BaseException as exc:  # noqa: BLE001 - handed to the run that is waiting
                future.set_exception(exc)

    def submit(self, job):
        future = concurrent.futures.Future()
        self.queue.put((job, future))
        return future.result()

    def post(self, job):
        """Queue a job on this desk's thread without waiting for it (its failure is dropped)."""
        self.queue.put((job, concurrent.futures.Future()))

    def arrange(self):
        """Show or hide this account's window as now wanted, after the step in progress."""
        def job():
            if self.portal is not None and hasattr(self.portal, "arrange_windows"):
                self.portal.arrange_windows()
        self.post(job)

    def ensure_portal(self):
        """This desk's Portal, made on first use (on the desk thread)."""
        if self.portal is None:
            if self.admin:
                self.portal = self.store.portal_factory(self.store.root, admin=self.admin, admin_port=self.port)
            elif self.evaluator:
                self.portal = self.store.portal_factory(self.store.root, evaluator=self.evaluator, evaluator_port=self.port)
            else:
                self.portal = self.store.portal_factory(self.store.root)
            if hasattr(self.portal, "engine"):
                self.portal.alert_hook = self.store.alert_login_wait
                self.portal.login_password = self.store.portal_logins.password_for
                self.portal.login_report = self.store.portal_logins.report
                self.portal.board = getattr(self.store, "windows", None)
        return self.portal

    def sign_in(self):
        """Queue the operator's sign-in check for this account's window (Portal.sign_in)."""
        kind = "evaluator" if self.evaluator else "admin"
        def job():
            portal = self.ensure_portal()
            if hasattr(portal, "sign_in"):
                portal.should_stop = lambda: False
                portal.sign_in(kind)
        self.post(job)

    def call(self, stop, method, *args, **kwargs):
        def job():
            self.ensure_portal()
            if hasattr(self.portal, "engine"):
                self.portal.should_stop = stop.is_set
            if stop.is_set():
                raise RuntimeError("Stopped by you")
            return getattr(self.portal, method)(*args, **kwargs)
        return self.submit(job)

    def close(self):
        """Close the portal windows. Runs on the desk thread only (see WorkflowStore.release_slot)."""
        portal, self.portal = self.portal, None
        if portal is not None and hasattr(portal, "close"):
            portal.close()


class PortalDesks:
    """One desk per account, plus the original admin for legacy saved runs."""
    EVALUATOR_PORTS_FROM = 9238

    def __init__(self, store):
        self.store, self.lock = store, threading.Lock()
        self.admin = PortalDesk(store, "admin")
        self.evaluators, self.administrators = {}, {}
        self.next_port = self.EVALUATOR_PORTS_FROM

    def administrator(self, email):
        key = (email or "").strip().lower()
        if not key:
            return self.admin
        with self.lock:
            desk = self.administrators.get(key)
            if desk is None:
                desk = self.administrators[key] = PortalDesk(self.store, f"admin:{key}", admin=email.strip(),
                                                            port=self.next_port)
                self.next_port += 1
            return desk

    def evaluator(self, email):
        key = (email or "").strip().lower()
        if not key:
            raise ValueError("The evaluator email is needed: the portal is used through that evaluator's Chrome window")
        with self.lock:
            desk = self.evaluators.get(key)
            if desk is None:
                desk = self.evaluators[key] = PortalDesk(self.store, f"evaluator:{key}", evaluator=email.strip(),
                                                         port=self.next_port)
                self.next_port += 1
            return desk

    def all(self):
        with self.lock:
            return [self.admin] + list(self.administrators.values()) + list(self.evaluators.values())

    def find(self, role, account):
        """The desk that owns this window, if it exists."""
        key = (account or "").strip().lower()
        with self.lock:
            if role == "admin":
                return self.administrators.get(key) if key else self.admin
            return self.evaluators.get(key)


class DeskHandle:
    """What a run sees as its portal: its admin's desk for assignment, its evaluator's desk
    for everything else, plus the run's own stop flag."""
    def __init__(self, desks, stop, evaluator="", admin="", direct=False):
        self.desks, self.stop, self.evaluator, self.admin, self.direct = desks, stop, evaluator or "", admin or "", bool(direct)

    def perform(self, stage, *args, **kwargs):
        desk = self.desks.administrator(self.admin) if stage in ("assign", "verify_assign") else self.desks.evaluator(self.evaluator)
        return desk.call(self.stop, "perform_direct" if self.direct else "perform", stage, *args, **kwargs)

    def exam_documents(self, *args, **kwargs):
        return self.desks.evaluator(self.evaluator).call(self.stop, "exam_documents_direct" if self.direct else "exam_documents", *args, **kwargs)


class WorkflowStore:
    def __init__(self, root, grader, portal_factory=Portal, downloader=download_pdf):
        self.root, self.grader = Path(root), grader
        self.portal_factory, self.downloader = portal_factory, downloader
        self.lock = threading.Lock()
        self.create_lock = threading.Lock()
        # key -> {"stop": Event, "admin": email, "evaluator": email or None}: every run or portal
        # fetch active in this app; each owns its stop flag.
        self.active = {}
        self.desks = PortalDesks(self)
        self.desk = self.desks.admin   # the desk that frees slots and closes the windows
        self.exam_documents = ExamDocumentCache()
        self.save_locks, self.save_locks_guard = {}, threading.Lock()   # never self.lock: callers may hold it while saving
        self.grading_slots = GradingSlots(GRADING_SLOTS)
        self.retry_delay = COPY_RETRY_DELAY_SECONDS
        self.assign_head_start = ASSIGN_HEAD_START_SECONDS
        self.latest_authorization = {}   # owner -> the last Bearer seen by access(): a paused run continues with it
        self.resumed_owners = set()      # owners whose interrupted runs were already continued in this process
        self.paper_checker = None        # install(): the paper's marks checked before a run starts (build 137)
        self.portal_logins = PortalLogins(root)   # the saved portal password windows sign in with (build 138)
        self.portal_logins.alert = self.alert_login_failed
        self.windows = PortalWindows(root)        # which portal windows are on screen (build 140)
        self.signin_poll = 5.0
        # Next to the app (its data folder is "workflow_data"), otherwise inside the data folder.
        self.checked_root = (self.root.parent if self.root.name == "workflow_data" else self.root) / CHECKED_COPIES_DIRNAME
        self.alerts = QuietAlerts()      # install() gives the app the real notification and email
        self.owner_emails = {}           # owner -> the signed-in Google account's email, the default alert address
        self.sheet_status = SheetStatusSync(self)

    def slot(self, key, create=False):
        with self.lock:
            found = self.active.get(key)
            if found is None and create:
                found = self.active[key] = {"stop": threading.Event(), "evaluator": None}
            return found

    def check_accounts(self, evaluator, admin=""):
        """Caller holds self.lock. Only conflicting portal accounts prevent a new run."""
        admin_key, evaluator_key = (admin or "").strip().lower(), (evaluator or "").strip().lower()
        if admin_key and admin_key == evaluator_key:
            raise ValueError("Use different accounts for the admin and evaluator logins")
        for slot in self.active.values():
            if ((admin_key and admin_key == (slot.get("evaluator") or "").strip().lower()) or
                    (evaluator_key and evaluator_key == (slot.get("admin") or "").strip().lower())):
                raise BusyError("This account is active in the other portal role. Wait for that run or fetch to finish, or choose a different account.")
        # An idle account's window stays open until the last active run finishes.
        # Do not open a second session for it in the other role in the meantime.
        with self.desks.lock:
            opposite = [self.desks.evaluators.get(admin_key), self.desks.administrators.get(evaluator_key)]
            if any(desk is not None and desk.portal is not None for desk in opposite):
                raise BusyError("This account still has a window open for the other portal role. Finish the active runs before switching roles, or choose a different account.")

    def reserve(self, key, evaluator, admin=""):
        """Caller holds self.lock."""
        if key in self.active:
            raise BusyError("This run is already active.")
        self.check_accounts(evaluator, admin)
        self.active[key] = {"stop": threading.Event(), "evaluator": evaluator, "admin": admin}

    def release_slot(self, key):
        """Free the slot and, when nothing else is active, close the portal windows.

        Both happen in one step on the portal thread, queued behind the actions in
        progress, so that "nothing active" always means the windows are closed and
        two runs finishing together cannot both skip the close."""
        def job():   # on the admin desk's thread
            try:
                with self.lock:
                    idle = not any(other != key for other in self.active)
                if idle:
                    # Close before freeing the slot, so "nothing active" means the windows
                    # are closed; a run starting meanwhile simply reopens them.
                    self.desks.admin.close()
                    for desk in self.desks.all():
                        if desk is not self.desks.admin:
                            desk.submit(desk.close)   # each window closes on its own thread
            except Exception:  # noqa: BLE001 - closing Chrome must never change a saved run
                pass
            finally:
                with self.lock:
                    self.active.pop(key, None)
        try:
            self.desk.submit(job)
        except Exception:  # noqa: BLE001 - the slot is freed even if the desk is unavailable
            with self.lock:
                self.active.pop(key, None)

    def show_window(self, role, account, show):
        """The operator's Show / Hide for one portal window (build 140)."""
        if role not in ("admin", "evaluator"):
            raise ValueError("Unknown portal window")
        if show:
            self.windows.need(role, account, "you", "Shown by you")
        else:
            self.windows.hide(role, account)
        desk = self.desks.find(role, account)
        if desk is not None:
            desk.arrange()
        return self.windows.status()

    def sign_in_windows(self, accounts):
        """The operator's "Sign in the windows" (build 141): every ID's window opens; a window not
        signed in comes on screen for the operator once. accounts: [(role, email)]."""
        for role, account in accounts:
            desk = self.desks.administrator(account) if role == "admin" else self.desks.evaluator(account)
            desk.sign_in()
        return self.windows.status()

    def show_all_windows(self, show):
        """Keep every portal window on screen (as before build 140), or hidden until needed."""
        self.windows.set_show_all(show)
        for desk in self.desks.all():
            desk.arrange()
        return self.windows.status()

    def stop_run(self, key):
        """Ask an active run or fetch to stop after its current operation; False when it is not active here."""
        slot = self.slot(key)
        if slot is None:
            return False
        slot["stop"].set()
        return True

    def fetch_exam_documents(self, owner, test):
        """Start the portal fetch of one test's question paper and Model Answer."""
        direct = test.get("portal_direct") is True
        test = {key: str(test.get(key) or "").strip() for key in ("test_id", "test_name", "program_name", "test_series_name", "evaluator_email")}
        if direct:
            test["portal_direct"] = True
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", test["test_id"]) or not test["test_name"]:
            raise ValueError("Select a test from the imported sheet first")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", test["evaluator_email"]):
            raise ValueError("Enter the evaluator email in step 2 first: the portal is read through that evaluator's Chrome window")
        with self.lock:
            self.check_accounts(test["evaluator_email"])
            fetch_id = self.exam_documents.create(owner, test)
            self.reserve(f"documents:{fetch_id}", test["evaluator_email"])
        threading.Thread(target=self.work_documents, args=(fetch_id,), daemon=True).start()
        return fetch_id

    def work_documents(self, fetch_id):
        cache = self.exam_documents
        key = f"documents:{fetch_id}"
        try:
            entry = cache.entries[fetch_id]
            cache.update(fetch_id, status="waiting_login",
                         detail=(f"Signing in as {entry['test'].get('evaluator_email', '')}" if entry["test"].get("portal_direct") else
                                 f"Log in to the EVALUATOR account {entry['test'].get('evaluator_email', '')} in the Chrome window that opened."))
            portal = DeskHandle(self.desks, self.slot(key, create=True)["stop"], entry["test"].get("evaluator_email", ""),
                                direct=entry["test"].get("portal_direct"))
            fetched = portal.exam_documents(entry["test"], progress=lambda text: cache.update(fetch_id, status="fetching", detail=text))
            documents = prepare_exam_documents(fetched)
            summary = ", ".join(f"{kind.replace('_', ' ')} {document['pages']} pages" + (f" from {document['files']} files" if document["files"] > 1 else "")
                                for kind, document in documents.items())
            cache.update(fetch_id, status="ready", documents=documents, detail=f"Fetched from the portal: {summary}")
        except Exception as exc:
            cache.update(fetch_id, status="failed", error=str(getattr(exc, "detail", exc)) or type(exc).__name__)
        finally:
            self.release_slot(key)

    def folder(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("Invalid workflow ID")
        return self.root / run_id

    def read(self, run_id):
        target = self.folder(run_id) / "run.json"
        for attempt in range(6):
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except PermissionError:
                # Atomic replacement can briefly deny opens on Windows while a
                # progress poll overlaps the worker's saved-state update.
                if attempt == 5:
                    raise
                time.sleep(.02 * (2 ** attempt))

    def save_lock(self, run_id):
        with self.save_locks_guard:
            return self.save_locks.setdefault(run_id, threading.RLock())

    def save(self, run):
        with self.save_lock(run["id"]):
            for row in run["rows"]:
                row["workflow_status"] = status_for(row, run)
            self.write_run(run)
            cleaned = False
            for row in run["rows"]:
                if row.get("files_cleaned"):
                    continue
                result = cleanup_copy_artifacts(self, run, row)
                if result["eligible"]:
                    row["cleanup_error"] = "; ".join(result["errors"])
                    if not result["errors"]:
                        row["files_cleaned"] = True
                        row["has_checked_pdf"] = False
                    cleaned = True
            if cleaned:
                self.write_run(run)
        self.sheet_status.queue(run)

    def write_run(self, run, merge_sync=True):
        folder = self.folder(run["id"])
        folder.mkdir(parents=True, exist_ok=True)
        if merge_sync and (folder / "run.json").exists():
            previous = self.read(run["id"])
            synced = {row["submission_id"]: row for row in previous["rows"]}
            for row in run["rows"]:
                old = synced.get(row["submission_id"], {})
                for key in ("sheet_synced_status", "sheet_sync_error"):
                    if key in old:
                        row[key] = old[key]
        temp = folder / "run.tmp"
        data = json.dumps(run, ensure_ascii=False, indent=2)
        for attempt in range(6):
            try:
                with temp.open("w", encoding="utf-8") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                temp.replace(folder / "run.json")
                return
            except PermissionError:
                # Windows readers and virus scanners can briefly lock the target
                # during atomic replacement. Keep the previous snapshot intact.
                if attempt == 5:
                    raise
                time.sleep(.02 * (2 ** attempt))

    def copy_paths(self, run, submission_id):
        folder = self.folder(run["id"])
        if run.get("layout_version", 1) == 1:
            # Existing saved runs keep their previous paths.
            return (folder / f"{submission_id}.pdf", folder / f"checked_{submission_id}.pdf",
                    folder / f"{submission_id}.report.json")
        for name in ("copies", "outputs", "reports"):
            (folder / name).mkdir(exist_ok=True)
        artifact_id = submission_id if run.get("layout_version") == 2 else hashlib.sha256(submission_id.encode()).hexdigest()
        return (folder / "copies" / f"{artifact_id}.pdf", folder / "outputs" / f"{artifact_id}.pdf",
                folder / "reports" / f"{artifact_id}.json")

    def create(self, rows, options, owner, paper, key, source_sheet=None):
        # Two tabs must not both accept the same submissions before either saves.
        with self.create_lock:
            return self._create(rows, options, owner, paper, key, source_sheet)

    def _create(self, rows, options, owner, paper, key, source_sheet=None):
        options = validate_options(options)
        rows = normalize_rows(rows)
        rows = [row for row in rows if not nothing_left(row)]
        if not rows:
            raise ValueError("Every selected copy already has a completed workflow status")
        if len({(r["program_name"], r["test_series_name"], r["test_id"], r["test_name"]) for r in rows}) != 1:
            raise ValueError("Select one test per run; each test needs its own paper and key")
        duplicates = duplicate_submissions(rows)   # a student's extra submissions: recorded, never processed
        selected = {r["submission_id"].casefold() for r in rows}
        retry_runs, finished, filed = [], set(), set()
        for saved in self.root.glob("*/run.json"):
            try:
                previous = self.read(saved.parent.name)
            except (ValueError, OSError):
                raise ValueError("A saved workflow is unreadable. Repair it before creating a new run so completed submissions cannot be repeated.")
            filed.update(self.checked_copy_key(r) for r in previous["rows"] if r.get("checked_file"))
            overlapping = [r for r in previous["rows"] if r["submission_id"].casefold() in selected and r.get("stage") != "superseded"]
            if overlapping:
                if previous["id"] in self.active or previous.get("owner") != owner:
                    raise ValueError("These copies already belong to a saved workflow. Resume that run instead of starting a duplicate.")
                # Running a test again takes what is left and leaves what is done.
                idle = self.left_alone(previous)
                moving = [r for r in overlapping if retryable_saved_row(previous, r, idle=idle)]
                finished.update(r["submission_id"].casefold() for r in overlapping if not any(r is m for m in moving))
                if moving:
                    retry_runs.append((previous, moving))
        rows = [row for row in rows if row["submission_id"].casefold() not in finished]
        retry_runs = [(previous, [r for r in moving if r["submission_id"].casefold() not in finished])
                      for previous, moving in retry_runs]
        retry_runs = [(previous, moving) for previous, moving in retry_runs if moving]
        if not rows:
            raise ValueError("These copies already belong to a saved workflow. Resume that run instead of starting a duplicate.")
        run_id = uuid.uuid4().hex
        run = {"id": run_id, "owner": owner, "status": "ready", "options": options, "layout_version": 3,
               "created_at": datetime.now().isoformat(),
               "rows": [dict(r, stage="duplicate" if r["submission_id"] in duplicates else "pending", error="", has_checked_pdf=False,
                             portal_assigned=False, assign_from="",
                             note=(f"Duplicate submission of this student for this test; the latest copy ({duplicates[r['submission_id']]}) is the one processed"
                                   if r["submission_id"] in duplicates else "")) for r in rows]}
        if source_sheet:
            run["source_sheet"] = source_sheet
        if retry_runs:
            # The claim is durable before the old run changes, including a disk
            # failure between the two saves. Resume checks it before doing work.
            run["retry_from"] = {previous["id"]: [row["submission_id"] for row in overlapping]
                                 for previous, overlapping in retry_runs}
        folder = self.folder(run_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "question_paper.pdf").write_bytes(paper)
        (folder / "answer_key.pdf").write_bytes(key)
        carried = {}
        for previous, overlapping in retry_runs:
            if self.same_exam(previous, options, paper, key):
                carried.update((r["submission_id"], (previous, r)) for r in overlapping)
        for row in run["rows"]:
            if row["stage"] != "pending":
                continue
            previous, old = carried.get(row["submission_id"], (None, None))
            if not (old and self.carry_check(run, row, previous, old)):
                self.mark_checked_before(row, filed)
        self.save(run)
        for previous, overlapping in retry_runs:
            for row in overlapping:
                row["stage"], row["error"], row["note"] = "superseded", "", f"Retried in workflow {run_id}"
                row["replaced_by"] = run_id
            self.save(previous)
        return run

    def start(self, run_id, authorization, mode="run"):
        # A retry may move failed rows into a new run. Serialize that move with Resume.
        with self.create_lock:
            run = self.read(run_id)
            moved = False
            for saved in self.root.glob("*/run.json"):
                replacement = self.read(saved.parent.name)
                claimed = set(replacement.get("retry_from", {}).get(run_id, []))
                for row in run["rows"]:
                    if row["submission_id"] in claimed and row.get("stage") != "superseded":
                        row["stage"], row["error"] = "superseded", ""
                        row["replaced_by"] = replacement["id"]
                        row["note"] = f"Retried in workflow {replacement['id']}"
                        moved = True
            if moved:
                self.save(run)
            options = run.get("options") or {}
            evaluator, admin = options.get("evaluator_email"), options.get("admin_email", "")
            with self.lock:
                self.reserve(run_id, evaluator, admin)
        thread = threading.Thread(target=self.work, args=(run_id, authorization, mode), daemon=True)
        thread.start()

    def _mark_skipped(self, row, note, keep_error=False):
        """A copy the run goes past: the portal's file cannot be read, or the copy alone
        kept failing. One that kept failing is tried again by the run's follow-up rounds
        (follow_up); Resume / retry gives any skipped copy another go from the step it was
        skipped at (work)."""
        row["skipped_from"] = row["stage"]
        row["stage"] = "skipped"
        row["operation"] = ""
        row["auto_retry"] = bool(keep_error)
        if keep_error:
            row["error"] = note
        else:
            row["note"], row["error"] = note, ""

    def notice_path(self, run, submission_id):
        """Where this copy's notice page is kept. Worked out from the run, never from a name
        stored in the saved run, which a hand-edited run.json could point anywhere."""
        source, _, _ = self.copy_paths(run, submission_id)
        return source.with_name(source.stem + ".notice.pdf")

    def mark_notice(self, run, row, exc):
        """The portal's file for this copy cannot be read: a one-page notice for the student in
        its place, marked for posting with 0 marks like any rejection (operator, 20 Nov 2026)."""
        from unreadable_notice import notice_comment, notice_pdf
        reason = str(exc)
        path = self.notice_path(run, row["submission_id"])
        path.write_bytes(notice_pdf(row, run.get("options") or {}, reason))
        row["notice"] = True
        row["rejection_code"] = "FILE_NOT_READABLE"
        row["rejection_reason"] = notice_comment(reason)
        row["stage"], row["error"] = "rejected", ""
        row["note"] = f"The portal's file could not be read, so a notice page is posted for the student. {reason}"
        row["operation"] = ""
        self.save(run)

    def skip(self, run, row, note):
        self._mark_skipped(row, note)
        self.save(run)

    def paper_problem(self, run, folder, authorization):
        """The question paper's marks checked before a run (or Resume) touches any copy: what
        is wrong, or "" (also "" when no checker is installed, or the sign-in has expired,
        which the first copy's sign-in path handles). The outcome is kept as run["marks_check"]
        for the page's marks editor."""
        if self.paper_checker is None:
            return ""
        try:
            check = self.paper_checker((folder / "question_paper.pdf").read_bytes(), run.get("options") or {}, authorization)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", 0) in (401, 403):
                return ""
            raise
        run["marks_check"] = {key: check.get(key) for key in ("ok", "method", "message", "items", "choice_groups",
                                                              "operator_choices", "paper_total") if key in check}
        self.save(run)
        return "" if check.get("ok") else str(check.get("message") or "The marks could not be checked.")

    def follow_up(self, run, portal, stop):
        """Before the run finishes, it tries again by itself: copies skipped after failing go
        back to the step they failed at, and portal actions whose confirmation was lost are
        read on the portal again. One round per AUTO_RETRY_DELAYS entry, after that wait.
        True when copies were put back to work; False when nothing is left to try, the
        rounds are used up or the run was stopped."""
        rounds = int(run.get("auto_rounds") or 0)
        if rounds >= len(AUTO_RETRY_DELAYS):
            return False
        retry = [row for row in run["rows"] if row["stage"] == "skipped" and row.get("auto_retry")
                 and not (row.get("skipped_from") == "downloaded" and rounds >= 1)]
        unsure = [row for row in run["rows"] if row["stage"] in UNCERTAIN_STAGES]
        if not retry and not unsure:
            return False
        delay = AUTO_RETRY_DELAYS[rounds]
        run["auto_rounds"] = rounds + 1
        parts = [f"{len(retry)} skipped cop{'y' if len(retry) == 1 else 'ies'}"] if retry else []
        parts += [f"{len(unsure)} unconfirmed portal action{'' if len(unsure) == 1 else 's'}"] if unsure else []
        run["notice"] = (f"Trying {' and '.join(parts)} again by itself in {round(delay / 60) or 1} min "
                         f"(round {rounds + 1} of {len(AUTO_RETRY_DELAYS)})")
        self.save(run)
        if stop.wait(delay):
            return False
        lock = self.save_lock(run["id"])
        with lock:
            for row in retry:
                kept = str(row.get("note") or "")   # e.g. a faculty-review flag and its reasons
                row["note"] = (kept + " Â· " if kept else "") + f"Tried again by the run itself (round {rounds + 1}) after: {row['error'][:200]}"
                row["stage"], row["failures"], row["error"], row["auto_retry"] = row.get("skipped_from") or "pending", 0, "", False
            run["notice"] = f"Round {rounds + 1} of {len(AUTO_RETRY_DELAYS)}: trying again"
            self.save(run)
        for row in unsure:
            if stop.is_set():
                return False
            self.reconcile(run, row, portal, lock)   # read only: done, not done (back to work) or still unknown
        run.pop("notice", None)
        self.save(run)
        return True

    def autopilot_in_charge(self):
        """The id of the Autopilot last started here, "" when there is none."""
        try:
            return str(json.loads((self.root / "autopilot.json").read_text(encoding="utf-8")).get("id") or "")
        except (OSError, ValueError, AttributeError):
            return ""

    def autopilot_runs(self):
        """Active runs any Autopilot started, this one or an earlier one."""
        found = []
        for key in list(self.active):
            try:
                if self.read(key).get("autopilot"):
                    found.append(key)
            except Exception:  # noqa: BLE001 - a fetch, or a run not saved yet
                continue
        return found

    def resume_interrupted(self, owner, authorization):
        """Runs this app was running when it closed (status still "running") continue by
        themselves at the owner's first sign-in after a restart (build 137), when saved
        within AUTO_RESUME_WITHIN_SECONDS. A run of an Autopilot since replaced does not: on
        2 Nov 2026 seven such runs held every checking slot ahead of the tab the operator had
        chosen. Its copies stay saved, and a Start on its tab picks them up."""
        with self.lock:
            if owner in self.resumed_owners:
                return
            self.resumed_owners.add(owner)
        in_charge = self.autopilot_in_charge()
        for path in self.root.glob("*/run.json"):
            try:
                if time.time() - path.stat().st_mtime > AUTO_RESUME_WITHIN_SECONDS:
                    continue
                run = self.read(path.parent.name)
                if run.get("owner") != owner or run.get("status") != "running" or run["id"] in self.active:
                    continue
                if run.get("autopilot") and in_charge and run["autopilot"] != in_charge:
                    continue
                run["notice"] = "Continued by itself after this app was restarted"
                self.save(run)
                self.start(run["id"], authorization)
            except Exception:  # noqa: BLE001 - a run that cannot start stays interrupted for Resume / retry
                pass

    def settle(self, run, stop):
        run.pop("notice", None)
        attention = any(r["stage"] in ATTENTION_STAGES or r["stage"] == "checked"
                        or (r["error"] and r["stage"] not in FINISHED_STAGES)
                        for r in run["rows"])
        run["status"] = "stopped" if stop.is_set() else ("needs_attention" if attention else "complete")

    def work(self, run_id, authorization, mode="run"):
        """mode "run": assign, download, grade and upload every unfinished copy.
        mode "rejections": post each copy the checker REJECTED to the portal with
        its reason as the comment and total marks 0 (operator-triggered only)."""
        run = None
        stop = self.slot(run_id, create=True)["stop"]
        portal = None
        try:
            run = self.read(run_id)
            folder = self.folder(run_id)
            options = run.get("options") or {}
            portal = DeskHandle(self.desks, stop, options.get("evaluator_email", ""), admin=options.get("admin_email", ""),
                                direct=options.get("portal_direct"))
            run["status"] = "running"
            run.pop("error", None)
            run["current"] = {}
            run["auto_rounds"] = 0
            for row in run["rows"]:
                # Keys a copy worker may set later exist from here on, created single-threaded:
                # the JSON writer may be iterating a row while another worker assigns a value,
                # which is safe only when no key is added meanwhile.
                for key, default in (("operation", ""), ("has_checked_pdf", None), ("awarded_marks", ""), ("rejection_reason", ""), ("note", ""),
                                     ("failures", 0), ("skipped_from", ""), ("portal_upload", False),
                                     ("confidence", None), ("review_reasons", []), ("flagged_for_review", False),
                                     ("auto_retry", False), ("workflow_status", ""), ("sheet_synced_status", ""),
                                     ("sheet_sync_error", ""), ("files_cleaned", False), ("cleanup_error", ""),
                                     ("checked_file", ""), ("assign_from", ""), ("portal_file", "")):
                    row.setdefault(key, default)
                if "portal_assigned" not in row:
                    # Saved by a build that assigned first: only a copy still at the start is not assigned.
                    reached = row.get("skipped_from") if row["stage"] == "skipped" else row["stage"]
                    row["portal_assigned"] = reached not in ("", "pending", "assignment_uncertain")
                row["operation"] = ""
                if mode == "run" and row["stage"] == "skipped" and row.get("auto_retry"):
                    # Resume / retry by a person gives a skipped copy another go from the
                    # step it was skipped at; automatic processing never does.
                    row["stage"] = row.get("skipped_from") or "pending"
                    row["failures"], row["note"], row["error"] = 0, "", ""
            if mode == "run":
                self.resume_paper_guard(run)
                for row in run["rows"]:
                    if row["stage"] == "faculty_review":
                        self.reconsider_review(run, row)
            run["checking_done"] = False
            if run["rows"]:
                run["checked_folder"] = str(checked_copies_folder(self.checked_root, run["rows"][0]))
            self.save(run)
            if mode == "run" and any(row["stage"] in ("pending", "assigned", "downloaded") for row in run["rows"]):
                import fitz
                for name in ("question_paper.pdf", "answer_key.pdf"):
                    with fitz.open(folder / name) as document:
                        if not document.is_pdf or not document.page_count or document.needs_pass:
                            raise ValueError(f"Saved {name} is not readable; no portal action was started")
                problem = self.paper_problem(run, folder, authorization)
                if problem:
                    raise ValueError(f"Stopped before touching any copy: the question paper's marks need fixing first. {problem} "
                                     "Open Change run inputs… to correct or enter the marks, then Resume / retry.")
            while True:
                if mode == "rejections":
                    todo = [row for row in run["rows"] if row["stage"] == "rejected"]
                else:
                    posting = auto_rejections(run.get("options"))
                    todo = [row for row in run["rows"] if row["stage"] not in HELD_STAGES or (posting and row["stage"] == "rejected")]
                self.work_round(run, run_id, mode, todo, folder, portal, authorization, stop)
                if stop.is_set():
                    break
                if run.get("error") != "Sign in again before resuming.":
                    if mode == "run" and not run.get("error") and self.follow_up(run, portal, stop):
                        continue
                    break
                # The app sign-in expired or the app was restarted: the run waits for the
                # next sign-in on any tab and continues with it instead of stopping.
                fresh = self.wait_for_sign_in(run, authorization, stop)
                if fresh is None:
                    break
                authorization = fresh
                run.pop("error", None)
                self.save(run)
            self.settle(run, stop)
            self.write_report(run)
        except Exception as exc:
            if run is not None:
                run["status"], run["error"] = "failed", str(exc)
        finally:
            try:
                if run is not None:
                    run.pop("current", None)
                    self.save(run)
                    self.alert_run_end(run)
            finally:
                self.release_slot(run_id)

    def alert_recipients(self, run):
        address = str((run.get("options") or {}).get("alert_email") or "").strip()
        return [address] if address else [email for email in [self.owner_emails.get(run.get("owner"))] if email]

    @staticmethod
    def run_summary(run):
        rows = run.get("rows") or []
        count = lambda *stages: sum(1 for row in rows if row.get("stage") in stages)
        test = (rows[0].get("test_name") if rows else "") or "a test"
        flagged = sum(1 for row in rows if row.get("stage") == "uploaded" and row.get("flagged_for_review"))
        parts = [f"{count('uploaded')} of {len(rows)} uploaded" + (f" ({flagged} flagged for faculty review)" if flagged else "")]
        for label, stages in (("checked, waiting to upload", ("checked",)), ("rejections posted", ("rejection_submitted",)),
                              ("rejected, waiting", ("rejected",)),
                              ("held for faculty review", ("faculty_review",)), ("skipped", ("skipped",)),
                              ("need a check in the portal", ("upload_uncertain", "assignment_uncertain", "rejection_uncertain")),
                              ("already evaluated", ("already_evaluated",)), ("duplicates", ("duplicate",))):
            if count(*stages):
                parts.append(f"{count(*stages)} {label}")
        evaluator = (run.get("options") or {}).get("evaluator_email") or ""
        return test, ", ".join(parts) + (f". Evaluator: {evaluator}." if evaluator else ".")

    def alert_run_end(self, run):
        """Alarm, notification and email when a run ends by itself (not when someone pressed Stop)."""
        try:
            status = run.get("status")
            if status in ("stopped", "running", "ready"):
                return
            test, counts = self.run_summary(run)
            skipped = sum(1 for row in run.get("rows") or [] if row.get("stage") == "skipped")
            title = {"complete": f"Run complete: {test}" if not skipped else f"Run finished, {skipped} skipped: {test}",
                     "failed": f"Run could not continue: {test}"}.get(
                status, f"Run finished, some copies need a decision: {test}")
            body = counts + (f" {run['error']}" if run.get("error") else "")
            self.alerts.raise_alert(f"{run['id']}:end:{datetime.now().isoformat(timespec='seconds')}", title, body,
                                    to=self.alert_recipients(run))
        except Exception:  # noqa: BLE001 - an alert never changes a saved run
            pass

    def alert_login_wait(self, role, account):
        """A portal Chrome window waits for a login: tell everyone whose active run uses this app."""
        try:
            with self.lock:
                keys = [key for key in self.active if not str(key).startswith("documents:")]
            recipients = set()
            for key in keys:
                try:
                    recipients.update(self.alert_recipients(self.read(key)))
                except Exception:  # noqa: BLE001
                    pass
            who = f"{role} login ID {account}" if account else f"the {role} account"
            self.alerts.raise_alert(f"login:{role}:{account}", "Portal login needed",
                                    f"The {role} Chrome window is on screen, waiting for a portal login. Sign in there with {who}; "
                                    "the run continues by itself.", to=sorted(recipients), once_within=15 * 60)
        except Exception:  # noqa: BLE001
            pass

    def alert_login_failed(self, account, detail):
        """A window could not sign itself in: tell everyone whose active run uses this app."""
        try:
            with self.lock:
                keys = [key for key in self.active if not str(key).startswith("documents:")]
            recipients = set()
            for key in keys:
                try:
                    recipients.update(self.alert_recipients(self.read(key)))
                except Exception:  # noqa: BLE001
                    pass
            self.alerts.raise_alert(f"login-failed:{str(account).lower()}", "Automatic portal login failed",
                                    f"The portal window for {account} could not sign itself in ({detail}). Sign in there by hand, "
                                    "and check the portal password saved on the workflow page; that ID is not tried again "
                                    "automatically until a password is saved.", to=sorted(recipients), once_within=30 * 60)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def paper_guard_count(run):
        return sum(1 for row in run["rows"] if row.get("rejection_code") == "WRONG_QUESTION_PAPER"
                   and row["stage"] in ("rejected", "rejection_submitted", "rejection_uncertain"))

    def paper_guard_trips(self, run, row):
        """Whether rejecting this copy as another test's would make PAPER_GUARD_MIN such
        rejections and PAPER_GUARD_SHARE of the test's decided copies. Off once the operator
        has resumed after it (resume_paper_guard): they have looked at the paper."""
        if run.get("paper_guard_cleared"):
            return False
        others = self.paper_guard_count(run) + 1
        decided = sum(1 for other in run["rows"] if other is not row and other["stage"] in DECIDED_STAGES) + 1
        return others >= PAPER_GUARD_MIN and others >= PAPER_GUARD_SHARE * decided

    def resume_paper_guard(self, run):
        """A pass after the guard stopped the run: the copies it refused as another test's
        and not yet posted are checked again, against the paper the run has now."""
        if not run.get("paper_guard_tripped") or run.get("paper_guard_cleared"):
            return
        for row in run["rows"]:
            if row["stage"] == "rejected" and row.get("rejection_code") == "WRONG_QUESTION_PAPER":
                row["stage"], row["error"], row["rejection_reason"], row["failures"] = "downloaded", "", "", 0
                row["note"] = "Checked again after the question paper was checked"
        run["paper_guard_cleared"] = True

    def reconsider_review(self, run, row):
        """Resume: a faculty-review copy held by an older build (or below this run's confidence)
        is uploaded with its flag when it has a checked PDF and marks the portal takes; one
        without marks is checked again, as nothing is held for faculty review any more
        (user, 29 Oct 2026)."""
        try:
            error = str(row.get("error") or "")
            source, _, report_path = self.copy_paths(run, row["submission_id"])
            if (error.startswith("Portal preparation requires review")
                    or not row.get("has_checked_pdf") or not report_path.exists()):
                row["stage"] = "downloaded" if source.exists() else "assigned"
                row["failures"], row["error"] = 0, ""
                row["note"] = "Checked again: copies are no longer held for faculty review"
                return
            report = json.loads(report_path.read_text(encoding="utf-8"))
            confidence = review_confidence(report, row.get("review_reasons") or None, error)
            threshold = review_threshold(run.get("options"))
            row["confidence"] = confidence
            if review_held(confidence, threshold):
                row["note"] = review_held_note(confidence, threshold)
                return
            portal_marks(report)
            row["stage"], row["failures"], row["flagged_for_review"] = "checked", 0, True
            row["note"] = review_flag_note(confidence, threshold, error)
            row["error"] = ""
        except Exception:  # noqa: BLE001 - a copy that cannot be reconsidered stays where it is
            pass

    def wait_for_sign_in(self, run, authorization, stop):
        """Block until the run's owner signs in again (a new Bearer seen by access()); None when stopped."""
        run["error"] = ("Waiting for you to sign in to the app again (the sign-in expired or the app was restarted). "
                        "The run continues by itself once you are signed in.")
        self.save(run)
        try:
            test, counts = self.run_summary(run)
            self.alerts.raise_alert(f"{run['id']}:signin", f"Run paused, sign in again: {test}",
                                    "The AI Checker sign-in expired or the app was restarted. Open the workflow page and sign in; "
                                    f"the run continues by itself. {counts}", to=self.alert_recipients(run), once_within=30 * 60)
        except Exception:  # noqa: BLE001
            pass
        while not stop.wait(self.signin_poll):
            fresh = self.latest_authorization.get(run.get("owner"))
            if fresh and fresh != authorization:
                return fresh
        return None

    def report_rows(self, run):
        def cell(value):
            if value is None:
                return ""
            if isinstance(value, (list, tuple)):
                return " | ".join(str(item) for item in value)
            return "yes" if value is True else "" if value is False else str(value)
        return [{key: cell(row.get(key, "")) for key in REPORT_COLUMNS} for row in run["rows"]]

    def write_report(self, run):
        """report.csv in the run folder: every copy with its stage, marks, note and error."""
        try:
            with (self.folder(run["id"]) / "report.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
                writer.writeheader()
                writer.writerows(self.report_rows(run))
        except Exception:  # noqa: BLE001 - the report must never change the run's outcome
            pass

    def reconcile(self, run, row, portal, lock):
        """A Submit click whose confirmation was lost: ask the portal what it holds.
        Returns "hold" (done, or still unknown: the copy rests), "retry" (not done: the copy
        goes back to its previous step) or "continue" (assigned: carry straight on)."""
        stage = row["stage"]
        verify = {"assignment_uncertain": "verify_assign", "upload_uncertain": "verify_upload", "rejection_uncertain": "verify_reject"}[stage]
        prior = {"assignment_uncertain": row.get("assign_from") or "pending", "upload_uncertain": "checked",
                 "rejection_uncertain": "rejected"}[stage]
        try:
            outcome = portal.perform(verify, row, run["options"]) or {}
        except Exception as exc:  # noqa: BLE001 - the portal could not be read: a person decides
            outcome = {"result": "unknown", "detail": str(getattr(exc, "detail", exc))}
        result, detail = outcome.get("result"), str(outcome.get("detail") or "")[:160]
        with lock:
            if result == "done":
                if stage == "upload_uncertain":
                    _, _, report_path = self.copy_paths(run, row["submission_id"])
                    if report_path.exists():
                        row["awarded_marks"] = json.loads(report_path.read_text(encoding="utf-8"))["total_score"]
                    row["stage"] = "uploaded"
                elif stage == "rejection_uncertain":
                    row["stage"], row["awarded_marks"] = "rejection_submitted", 0
                else:
                    row["stage"], row["portal_assigned"] = "assigned" if prior == "pending" else prior, True
                row["error"], row["failures"] = "", 0
                row["note"] = f"Confirmed on the portal after a lost confirmation: {detail}"
                self.save(run)
                return "continue" if stage == "assignment_uncertain" else "hold"
            if result == "not_done":
                row["stage"] = prior
                row["note"] = f"The portal shows it was not done ({detail}); retried"
                self.save(run)
                return "retry"
            row["note"] = f"Could not tell from the portal whether it went through ({detail}); check it there"
            self.save(run)
            return "hold"

    def work_round(self, run, run_id, mode, todo, folder, portal, authorization, stop):
        """Process every copy, with at most three independent attempts per copy."""
        lock, halt, portal_halt = self.save_lock(run_id), threading.Event(), threading.Event()
        uncertain = ("assignment_uncertain", "upload_uncertain", "rejection_uncertain")

        def attempt(row, phase="all"):
            fetch = {}   # this copy's wait on the portal's "Unable to fetch task": since when, tries
            lane_halt = portal_halt if phase in ("assign", "portal") else halt
            while not stop.is_set() and not halt.is_set() and not lane_halt.is_set():
                with lock:
                    if int(row.get("failures") or 0) >= COPY_FAILURE_LIMIT:
                        self._mark_skipped(row, f"Skipped after {COPY_FAILURE_LIMIT} failed attempts: {row['error']}", keep_error=True)
                        self.save(run)
                        return
                    row["error"] = ""
                try:
                    try:
                        if mode == "rejections":
                            self.process_rejection(run, row, portal)
                        else:
                            self.process(run, row, folder, portal, authorization, stop, phase)
                    except PortalWindowCrashed as exc:
                        # The window is opened again and the copy waits without using an attempt;
                        # a copy that keeps crashing its window counts failures after that.
                        fetch["crashes"] = fetch.get("crashes", 0) + 1
                        if fetch["crashes"] > WINDOW_CRASH_RETRIES:
                            raise RuntimeError(f"{exc} (its window crashed {fetch['crashes']} times on this copy)") from exc
                        raise
                    with lock:
                        if not stop.is_set() or row["stage"] in HELD_STAGES:
                            row["failures"] = 0
                    return
                except PortalWait as exc:
                    if phase == "assign":   # the assigning lane never waits: the copy goes on to its check
                        print(f"[workflow] assigning {row['submission_id']} after its check: {exc}", flush=True)
                        return
                    if stop.is_set() or halt.is_set() or lane_halt.is_set() or not self.portal_wait(run, row, lock, stop, fetch, exc):
                        return
                    continue
                except Exception as exc:
                    if stop.is_set():
                        return
                    with lock:
                        row["error"] = str(getattr(exc, "detail", exc))
                        signed_out = getattr(exc, "status_code", 0) in (401, 403)
                        cause = None if signed_out else run_level_cause(row["error"])
                        ahead = phase == "assign" and not signed_out and not cause and row["stage"] not in uncertain
                        if signed_out:
                            run["error"] = "Sign in again before resuming."
                            halt.set()
                        elif cause and lane_halt is portal_halt:
                            run["error"] = (f"Stopped: {cause}. No copy can be assigned or uploaded until that is fixed, so no more "
                                            f"portal attempts were made. Checking goes on, and the checked copies wait in this run. "
                                            f"Use Change run inputs, then Resume / retry. Detail: {row['error'][:200]}")
                            portal_halt.set()
                        elif cause:
                            run["error"] = (f"Stopped: {cause}. Every copy would fail the same way, so no more attempts were made. "
                                            f"Use Change run inputs, then Resume / retry. Detail: {row['error'][:200]}")
                            halt.set()
                        elif ahead:
                            # Costs the copy nothing: the portal lane assigns it after its check,
                            # with the copy's attempts.
                            print(f"[workflow] assigning {row['submission_id']} after its check: {row['error'][:200]}", flush=True)
                            row["error"] = ""
                        else:
                            row["failures"] = int(row.get("failures") or 0) + 1
                            # Submit may have succeeded. Hold only this student for
                            # reconciliation, while other students keep moving.
                            if row["stage"] not in uncertain and row["failures"] >= COPY_FAILURE_LIMIT:
                                self._mark_skipped(row, f"Skipped after {COPY_FAILURE_LIMIT} failed attempts: {row['error']}", keep_error=True)
                        self.save(run)
                        if signed_out or cause or ahead or row["stage"] == "skipped":
                            return
                    if row["stage"] in uncertain:
                        verdict = self.reconcile(run, row, portal, lock)
                        if verdict == "hold":
                            return
                        if verdict == "continue":
                            continue
                    if stop.is_set() or halt.is_set() or lane_halt.is_set():
                        return
                    row["operation"] = f"retrying attempt {row['failures'] + 1} of {COPY_FAILURE_LIMIT}"
                    self.save(run)
                    if stop.wait(self.retry_delay):
                        return
            row["operation"] = ""

        if mode == "rejections":
            for row in todo:
                if stop.is_set() or halt.is_set():
                    break
                attempt(row)
                row["operation"] = ""
            return

        # Three lanes (operator's requests, 29 and 30 Oct 2026, builds 146 and 148). The check needs
        # neither the assignment nor the upload: the copy comes from the sheet's link.
        #   checking: the copies in flight take each copy through download and the check.
        #   assigning: one worker goes ahead of them. It is there for one reason, to learn that the
        #     portal already holds a copy before it is paid for: 27 of the 39 copies assigned on
        #     30 Oct were held already, and the sheet still said Submitted for 24 of them.
        #   portal: one worker assigns what the assigning worker did not reach, uploads checked
        #     copies and posts rejections.
        # Checking takes a copy the assigning worker has finished with first, and any other copy
        # once that worker has said nothing for assign_head_start seconds or has stopped. A portal
        # that is slow, down or refuses the evaluator stops only the two portal lanes.
        posting = auto_rejections(run.get("options"))
        def portal_lane(row):
            return row["stage"] == "checked" or (posting and row["stage"] == "rejected")
        uploads, checked_all, assign_done = queue.Queue(), threading.Event(), threading.Event()
        checking = [row for row in todo if not portal_lane(row)]
        for row in todo:
            if portal_lane(row):
                uploads.put(row)
        ahead = not run["options"].get("already_assigned")
        unassigned = deque(row for row in checking if ahead and row["stage"] == "pending" and not row.get("portal_assigned"))
        ready = deque(row for row in checking if not any(row is other for other in unassigned))
        heard = [time.monotonic()]   # when the assigning worker last had an answer from the portal

        def assigner():
            try:
                while not stop.is_set() and not halt.is_set() and not portal_halt.is_set():
                    with lock:
                        row = unassigned.popleft() if unassigned else None
                    if row is None:
                        return
                    attempt(row, "assign")
                    row["operation"] = ""
                    with lock:
                        if row["stage"] in ("pending", "assigned"):
                            ready.append(row)
                        answered, more = row["stage"] != "pending", bool(unassigned)
                        if answered:
                            heard[0] = time.monotonic()
                    if not answered and more and stop.wait(self.assign_head_start):
                        return   # the portal is in trouble: not one failed call per copy
            finally:
                assign_done.set()

        def checker():
            while not stop.is_set() and not halt.is_set():
                with lock:
                    done = assign_done.is_set()   # read first: nothing is added to the lists after it
                    row = ready.popleft() if ready else None
                    if row is None and unassigned and (done or portal_halt.is_set()
                                                       or time.monotonic() - heard[0] >= self.assign_head_start):
                        row = unassigned.popleft()
                if row is None:
                    if done:
                        return
                    stop.wait(.05)
                    continue
                attempt(row, "check")
                row["operation"] = ""
                if portal_lane(row) and not stop.is_set():
                    uploads.put(row)

        def uploader():
            while not stop.is_set() and not halt.is_set() and not portal_halt.is_set():
                try:
                    row = uploads.get(timeout=0.25)
                except queue.Empty:
                    if checked_all.is_set() and uploads.empty():
                        return
                    continue
                attempt(row, "portal")
                row["operation"] = ""

        in_flight = int(run["options"].get("copies_in_flight") or COPIES_IN_FLIGHT)
        workers = max(1, min(in_flight, MAX_COPIES_IN_FLIGHT, len(checking))) if checking else 0
        threads = [threading.Thread(target=checker, name=f"workflow-copy-{n}", daemon=True) for n in range(workers)]
        upload_thread = threading.Thread(target=uploader, name="workflow-upload", daemon=True)
        assign_thread = threading.Thread(target=assigner, name="workflow-assign", daemon=True)
        for thread in [assign_thread] + threads + [upload_thread]:
            thread.start()
        for thread in threads:
            thread.join()
        with lock:
            run["checking_done"] = True   # every copy is checked: an autopilot pair takes its next test
            self.save(run)
        checked_all.set()
        upload_thread.join()

    def portal_wait(self, run, row, lock, stop, fetch, exc):
        """The portal needs time or a person (PortalWait): the portal said 'Unable to fetch
        task' before anything was submitted, or a window waited its full time for a login.
        The copy keeps its attempts: it waits (PORTAL_FETCH_DELAYS) and tries again for as
        long as the run goes on. A fetch problem lasting PORTAL_FETCH_ALERT_SECONDS alerts the
        operator; a login wait has its own alert (alert_login_wait). False when stopped."""
        now = time.monotonic()
        started = fetch.setdefault("since", now)
        fetch["tries"] = tries = fetch.get("tries", 0) + 1
        crashed = isinstance(exc, PortalWindowCrashed)
        delay = WINDOW_CRASH_DELAY_SECONDS if crashed else PORTAL_FETCH_DELAYS[min(tries, len(PORTAL_FETCH_DELAYS)) - 1]
        if isinstance(exc, PortalFetchError) and exc.retry_after is not None:
            delay = max(0, exc.retry_after)
        minutes = int((now - started) // 60)
        waited = f" (try {tries}" + (f", {minutes} min so far" if minutes else "") + f"); trying again in {delay:g} s"
        with lock:
            row["error"] = ""
            if isinstance(exc, PortalLoginWait):
                row["operation"] = f"waiting for a portal login: {exc}{waited}"
            elif crashed:
                row["operation"] = f"the portal window crashed and is opened again{waited}"
            elif isinstance(exc, InternetWait):
                row["operation"] = f"waiting for the internet{waited}"
            else:
                row["operation"] = f"waiting for the portal: {exc}{waited}"
            self.save(run)
        if crashed:
            try:
                self.alerts.raise_alert(
                    "window-crash", "A portal window crashed",
                    "A portal Chrome window's page crashed, most likely because this computer is short of memory. It was "
                    "opened again and the copies continue; none is skipped for it. If this keeps happening, run fewer admin + "
                    "evaluator pairs at once or close other programs.", to=self.alert_recipients(run), once_within=60 * 60)
            except Exception:  # noqa: BLE001 - an alert never changes a run
                pass
        if isinstance(exc, InternetWait) and now - started >= PORTAL_FETCH_ALERT_SECONDS and not fetch.get("alerted"):
            fetch["alerted"] = True
            try:
                self.alerts.raise_alert(
                    "internet", "No internet on this computer",
                    f"For {minutes} minutes this computer has not been able to reach the internet. Nothing is skipped for it, "
                    "and the copies carry on when it is back. Check the Wi-Fi, and that the computer has not gone to sleep.",
                    to=self.alert_recipients(run), once_within=30 * 60)
            except Exception:  # noqa: BLE001 - an alert never changes a run
                pass
        if isinstance(exc, PortalFetchError) and now - started >= PORTAL_FETCH_ALERT_SECONDS and not fetch.get("alerted"):
            fetch["alerted"] = True
            try:
                test, counts = self.run_summary(run)
                self.alerts.raise_alert(
                    f"{run['id']}:fetch", f"Portal not loading tasks: {test}",
                    f"For {minutes} minutes the portal has said 'Unable to fetch task' (its task list does not load). "
                    "The run keeps trying by itself and no copy is skipped for it. If the same portal account is signed in "
                    f"on another computer or browser, sign it out there. {counts}",
                    to=self.alert_recipients(run), once_within=30 * 60)
            except Exception:  # noqa: BLE001 - an alert never changes a run
                pass
        return not stop.wait(delay)

    def process(self, run, row, folder, portal, authorization, stop=None, phase="all"):
        try:
            self.process_copy(run, row, folder, portal, authorization, stop, phase)
        finally:
            row["operation"] = ""

    def process_copy(self, run, row, folder, portal, authorization, stop=None, phase="all"):
        """phase "assign": the assignment alone (the assigning lane); "check": download and the
        check (the checking lane); "portal": the assignment if it is still to do, then the upload
        or the rejection post (the portal lane); "all": the three in that order."""
        stop = stop or threading.Event()
        stem = row["submission_id"]
        source, checked, report_path = self.copy_paths(run, stem)
        options = run["options"]
        assigning, checking, posting = phase in ("all", "assign"), phase in ("all", "check"), phase in ("all", "portal")
        def operation(stage):
            row["operation"] = stage   # shown per row: several copies are in flight
            run["current"] = {"submission_id": stem, "operation": stage}
            self.save(run)
        if assigning and row["stage"] == "pending" and not self.assign_copy(run, row, portal):
            return
        if stop.is_set() or phase == "assign":
            return
        if checking and row["stage"] in ("pending", "assigned"):
            operation("downloading")
            try:
                self.downloader(row["copy_url"], source)
            except UnreadableCopy as exc:
                # Not a run error: the copy is skipped with the reason and the run
                # goes on (user's decision, 15 Oct 2026). From 20 Nov 2026 a notice
                # page for the student goes up in its place, so the copy does not stay
                # pending on the portal for good; the portal lane posts it as it posts
                # any rejection, which honours the operator's automatic-posting choice.
                from unreadable_notice import worth_a_notice
                if notice_for_unreadable(options) and worth_a_notice(str(exc)):
                    self.mark_notice(run, row, exc)
                else:
                    self.skip(run, row, f"Skipped: {exc}")
                return
            except requests.ConnectionError as exc:
                if internet_is_down():
                    raise InternetWait("this computer cannot reach the internet") from exc
                raise
            row["stage"] = "downloaded"
            self.save(run)
        if stop.is_set():
            return
        if checking and row["stage"] == "downloaded":
            pages = too_many_pages(source)
            if pages:
                # Here and not at the download, so a copy downloaded before a restart is caught.
                self.skip(run, row, f"Skipped: the file has {pages} pages, more than the {MAX_COPY_PAGES} an answer "
                                    "sheet could run to, so it was not read. A person should open it.")
                return
            operation("checking")
            # How many copies of this test are still to check, this one included: the
            # grader caches the paper and key only when another copy will read them back.
            to_check = 1 + sum(1 for other in run["rows"] if other is not row
                               and other["stage"] in ("pending", "assignment_uncertain", "assigned", "downloaded"))
            outcome = self.grade(folder, source, {**options, "copies_to_check": to_check},
                                 authorization, stop=stop, progress=operation)
            if stop.is_set() and outcome is None:
                return
            row["has_checked_pdf"] = False
            if outcome.get("pdf"):
                checked.write_bytes(outcome["pdf"])
                import fitz
                try:
                    with fitz.open(checked) as document:
                        row["has_checked_pdf"] = bool(document.is_pdf and document.page_count and not document.needs_pass)
                except Exception:
                    pass
            if outcome.get("report"):
                report_path.write_text(json.dumps(outcome["report"]), encoding="utf-8")
            if outcome.get("rejected") is True:
                # Not a genuine copy for this paper (typed pages, another test, a
                # duplicate). Posted to the portal at once with the reason as the
                # comment and 0 marks (operator's rule, 26 Oct 2026), unless the run
                # was started with automatic rejection posting turned off.
                row["rejection_code"] = str(outcome.get("failure_code") or "")
                if row["rejection_code"] == "WRONG_QUESTION_PAPER" and self.paper_guard_trips(run, row):
                    # Not marked rejected, so the portal lane never posts it: checked
                    # again after the operator has looked at the paper (resume_paper_guard).
                    run["paper_guard_tripped"] = True
                    self.save(run)
                    raise RuntimeError(f"Not posted: {self.paper_guard_count(run) + 1} copies of this test look like answers to a "
                                       "different test. Check that the question paper and answer key are this test's, then "
                                       "Resume / retry (those copies are checked again).")
                row["rejection_reason"] = str(outcome.get("error") or "").strip() or "Not a genuine answer sheet for this paper."
                row["stage"], row["error"] = "rejected", row["rejection_reason"]
                self.save(run)
                if posting and auto_rejections(options) and not stop.is_set():
                    self.process_rejection(run, row, portal)
                return
            if outcome.get("release") is not True:
                reasons = [str(reason) for reason in (outcome.get("review_reasons") or []) if str(reason).strip()]
                error = outcome.get("error") or "AI release checks require faculty review"
                if not outcome.get("report") or not row["has_checked_pdf"]:
                    if outcome.get("failed"):
                        # The check itself broke (the grader's "failed" state): a copy failure,
                        # tried again and then skipped like any other, unless the internet went.
                        if NETWORK_FAILURE_RE.search(str(error)) and internet_is_down():
                            raise InternetWait("this computer cannot reach the internet")
                        raise RuntimeError(f"The checker could not finish this copy: {error}")
                    # One of the checker's own gates stopped it: nothing to upload. Skipped at
                    # once with the reason (not three paid attempts), its files kept for the
                    # run's follow-up round and Resume / retry, like a check that failed.
                    # Nothing waits for faculty review: there is no faculty (user, 29 Oct 2026).
                    row["review_reasons"] = reasons
                    self._mark_skipped(row, f"{SKIPPED_NO_MARKS} {error}", keep_error=True)
                    self.save(run)
                    return
                confidence = review_confidence(outcome.get("report"), reasons or None, error)
                threshold = review_threshold(options)
                row["review_reasons"], row["confidence"] = reasons, confidence
                if review_held(confidence, threshold):
                    row["stage"], row["error"] = "faculty_review", error
                    row["note"] = review_held_note(confidence, threshold)
                    self.file_checked_copy(run, row, checked)
                    self.save(run)
                    return
                row["flagged_for_review"] = True
                row["note"] = review_flag_note(confidence, threshold, error)
            try:
                if not row["has_checked_pdf"]:
                    raise ValueError("The grader did not produce a readable checked PDF")
                portal_marks(outcome["report"])
            except Exception as exc:
                # Skipped at once (not three paid attempts), kept for Resume / retry.
                self._mark_skipped(row, f"{SKIPPED_NO_MARKS} the checked copy could not be prepared for the portal: {exc}",
                                   keep_error=True)
                self.save(run)
                return
            row["stage"] = "checked"
            self.file_checked_copy(run, row, checked)
            self.save(run)
        if stop.is_set():
            return
        if posting and row["stage"] == "rejected" and auto_rejections(options):
            self.process_rejection(run, row, portal)   # a rejection whose earlier post failed before Submit
            return
        if posting and row["stage"] == "checked":
            if not self.assign_copy(run, row, portal):
                return
            operation(f"uploading from the evaluator window ({options['evaluator_email']})")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            def attempting_upload():
                # Persist intent right before the Submit click. Ambiguous portal
                # outcomes are reconciled by the operator instead of resubmitted;
                # a failure before the click (upload, menu, form) is retried.
                row["stage"] = "upload_uncertain"
                self.save(run)
            outcome = portal.perform("upload", row, options, checked, report, before_submit=attempting_upload)
            if self.copy_moved_on(run, row, outcome):
                return
            row["stage"], row["awarded_marks"] = "uploaded", report["total_score"]
            self.save(run)

    def copy_moved_on(self, run, row, outcome):
        """Another computer or evaluator took this copy after it was assigned here: nothing was
        posted, and the run goes past it (2 Nov 2026). True when that is what happened."""
        if (outcome or {}).get("result") != "moved_on":
            return False
        row["operation"] = ""
        if outcome.get("finished"):
            row["stage"], row["error"] = "already_evaluated", ""
            row["note"] = (f"'{outcome.get('status') or 'Evaluated'}' on the portal: someone else evaluated it "
                           "while this computer was checking it. Nothing was posted")
        else:
            self._mark_skipped(row, "Skipped: another evaluator took this copy on the portal after it was "
                                    "assigned here. Nothing was posted")
        self.save(run)
        return True

    def left_alone(self, run):
        """Nothing in this app is working on this saved run. One just created is not left alone:
        the Start that made it, here or in another tab, is about to start it."""
        if run.get("id") in self.active:
            return False
        if run.get("status") != "ready":
            return True
        try:
            return (datetime.now() - datetime.fromisoformat(run["created_at"])).total_seconds() >= UNSTARTED_RUN_SECONDS
        except (KeyError, TypeError, ValueError):
            return False

    def same_exam(self, previous, options, paper, key):
        """A check stands only against the paper, key and total it was made with."""
        folder = self.folder(previous["id"])
        try:
            return (float((previous.get("options") or {}).get("total_marks")) == float(options["total_marks"])
                    and same_document((folder / "question_paper.pdf").read_bytes(), paper)
                    and same_document((folder / "answer_key.pdf").read_bytes(), key))
        except (OSError, TypeError, ValueError):
            return False

    def carry_check(self, run, row, previous, old):
        """A copy checked in an earlier run of its test goes to the new run as checked, or as
        rejected, so it is uploaded or posted without being checked and paid for again
        (operator, 30 Oct 2026). False when that run holds no finished check of it."""
        reached = old.get("skipped_from") if old.get("stage") == "skipped" else old.get("stage")
        files = self.copy_paths(previous, old["submission_id"])
        source, checked, report = files
        if not ((reached == "checked" and old.get("has_checked_pdf") and checked.exists() and report.exists())
                or (reached == "rejected" and old.get("rejection_reason") and source.exists())):
            return False
        for origin, target in zip(files, self.copy_paths(run, row["submission_id"])):
            if origin.exists():
                shutil.copyfile(origin, target)
        for field in ("has_checked_pdf", "checked_file", "confidence", "review_reasons", "flagged_for_review",
                      "rejection_reason", "rejection_code"):
            if field in old:
                row[field] = old[field]
        row["stage"], row["error"] = reached, row.get("rejection_reason", "") if reached == "rejected" else ""
        row["note"] = "Checked in an earlier run of this test; not checked again"
        return True

    def checked_copy_key(self, row):
        return checked_copies_folder(self.checked_root, row).name, checked_file_name(row)

    def mark_checked_before(self, row, filed):
        """A checked PDF for this student is in the Checked copies folder and no saved run knows
        it, so its marks are gone. It is not paid for again; a person decides. A PDF a saved run
        does know belongs to another submission of the student, which is a new copy."""
        folder, name = checked_copies_folder(self.checked_root, row), checked_file_name(row)
        found = next((path for path in (folder / name, folder / "Faculty review" / name) if path.exists()), None)
        if found is None or self.checked_copy_key(row) in filed:
            return
        row.update(stage="skipped", skipped_from="pending", auto_retry=False, checked_before=True, checked_file=str(found),
                   note="Not checked again: its checked PDF is already in the Checked copies folder, but this computer has "
                        "no saved marks for it. Upload that PDF by hand, or delete it and run the test again to check the copy.")

    def assign_copy(self, run, row, portal):
        """Assign this copy in the admin window, once. False when the portal already holds its
        evaluation: there is nothing to do, whether or not it has been checked here."""
        options = run["options"]
        if not options.get("already_assigned") and not row.get("portal_assigned"):
            row["operation"] = "assigning in the admin window" + (f" ({options['admin_email']})" if options.get("admin_email") else "")
            run["current"] = {"submission_id": row["submission_id"], "operation": row["operation"]}
            self.save(run)
            came_from = row["stage"]
            def attempting_assignment():
                # Persist intent right before the Submit click: a lost toast
                # leaves the copy "assignment uncertain" for the operator, while
                # a failure before the click keeps it where it was and retried.
                row["stage"], row["assign_from"] = "assignment_uncertain", came_from
                self.save(run)
            outcome = portal.perform("assign", row, options, before_submit=attempting_assignment) or {}
            if outcome.get("result") == "finished":
                # The portal already holds this copy's evaluation (In Review, Finished...).
                row["stage"] = "already_evaluated"
                row["note"] = f"Already '{outcome.get('status') or 'evaluated'}' on the portal; nothing to do"
                self.save(run)
                return False
            if outcome.get("result") == "reassigned":
                row["note"] = f"Reassigned on the portal from {outcome.get('evaluator') or 'another evaluator'} to {options['evaluator_email']}"
            elif outcome.get("result") == "already":
                row["note"] = "Already assigned to this evaluator on the portal"
            row["stage"] = came_from
        if row["stage"] == "pending":
            row["stage"] = "assigned"
        row["portal_assigned"] = True
        self.save(run)
        return True

    def file_checked_copy(self, run, row, checked):
        """Keep the checked PDF in the run's batch-test folder (checked_copies_folder), named after the
        student; a copy for faculty review in its "Faculty review" folder. Never fails the copy."""
        if not row.get("has_checked_pdf") or not checked.exists():
            return
        try:
            target = checked_copies_folder(self.checked_root, row)
            if row.get("flagged_for_review") or row["stage"] == "faculty_review":
                target = target / "Faculty review"
            target.mkdir(parents=True, exist_ok=True)
            target = target / checked_file_name(row)
            shutil.copyfile(checked, target)
            row["checked_file"] = str(target)
        except OSError as exc:
            row["checked_file"] = ""
            row["note"] = (row.get("note") + " · " if row.get("note") else "") + f"Not saved to the checked-copies folder: {exc}"

    def slot_wait_reason(self):
        """What a copy waits for. Low memory used to be silent: on 2 Nov 2026 a run went one copy
        at a time for hours while the page said only "waiting for grading slot"."""
        slots = self.grading_slots
        free = slots.free_memory()
        if free is None or free >= MIN_FREE_MEMORY_MB or slots.used >= slots.limit:
            return "waiting for grading slot"
        print(f"[memory] {free:.0f} MB free, under {MIN_FREE_MEMORY_MB} MB: no new check starts", flush=True)
        try:
            self.alerts.raise_alert(
                "memory", "This computer is short of memory",
                f"Only {free:.0f} MB of memory is free and {MIN_FREE_MEMORY_MB} MB is needed, so no further check "
                "starts until the running ones end, and then they go one at a time. Nothing is lost. Close other "
                "programs, such as browsers and chat apps, or restart Windows.", once_within=30 * 60)
        except Exception:  # noqa: BLE001 - an alert never changes a run
            pass
        return f"waiting for free memory ({free:.0f} MB free, {MIN_FREE_MEMORY_MB} MB needed)"

    def grade(self, folder, source, options, authorization, stop=None, progress=lambda stage: None):
        """Run the grader on a thread of its own.

        The portal engine keeps a Playwright event loop running in this worker
        thread while a Chrome window stays open, and the grading bridge starts with
        asyncio.run(), which refuses to start inside a running loop (first live run
        on build 101: "asyncio.run() cannot be called from a running event loop").
        """
        stop = stop or threading.Event()
        acquired = self.grading_slots.acquire(blocking=False)
        if not acquired:
            progress(self.slot_wait_reason())
            while not stop.is_set():
                if self.grading_slots.acquire(timeout=.25):
                    acquired = True
                    break
        if not acquired:
            return None
        try:   # across all runs: the PW proxy sees at most grading_slots.limit copies at once
            if stop.is_set():
                return None
            progress("checking")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-grade") as pool:
                return pool.submit(self.grader, folder, source, options, authorization).result()
        finally:
            self.grading_slots.release()

    def process_rejection(self, run, row, portal):
        stem = row["submission_id"]
        source, _, _ = self.copy_paths(run, stem)
        if row.get("notice"):
            # There is no readable original: the notice page goes up in its place.
            source = self.notice_path(run, stem)
        if not source.exists():
            raise ValueError("The original copy is no longer stored for this run; submit this rejection in the portal by hand")
        if not self.assign_copy(run, row, portal):
            return
        row["operation"] = f"submitting rejection from the evaluator window ({run['options'].get('evaluator_email', '')})"
        run["current"] = {"submission_id": stem, "operation": row["operation"]}
        self.save(run)
        def attempting():
            # Persist intent right before Submit. A lost confirmation leaves the
            # copy "rejection uncertain" for the operator instead of a second post.
            row["stage"] = "rejection_uncertain"
            self.save(run)
        try:
            outcome = portal.perform("reject", row, run["options"], source, reason=row.get("rejection_reason", ""), before_submit=attempting)
        finally:
            row["operation"] = ""
        if self.copy_moved_on(run, row, outcome):
            return
        row["stage"], row["awarded_marks"], row["error"] = "rejection_submitted", 0, ""
        self.save(run)


REPORT_COLUMNS = ("student_name", "student_id", "submission_id", "program_name", "test_series_name", "test_name",
                  "stage", "awarded_marks", "flagged_for_review", "confidence", "note", "error", "review_reasons",
                  "rejection_reason", "portal_status", "program_id", "test_series_id", "test_id", "copy_url",
                  "total_marks", "board_or_exam", "exam_kind", "workflow_status", "sheet_sync_error", "files_cleaned", "cleanup_error")
ATTENTION_STAGES = ("faculty_review", "rejected", "upload_uncertain", "assignment_uncertain", "rejection_uncertain")
# "skipped": the portal's file could not be read, or the copy alone kept failing
# (integrated_workflow.WorkflowStore.skip). Not an attention stage: the run goes
# past it; Resume / retry by a person gives the copy another go.
HELD_STAGES = ATTENTION_STAGES + ("uploaded", "rejection_submitted", "already_evaluated", "skipped", "duplicate", "superseded")
FINISHED_STAGES = ("uploaded", "rejection_submitted", "already_evaluated", "skipped", "duplicate", "superseded")


def normalize_operator_marks(value):
    """Marks a person entered for the paper because its printed ones are wrong or unreadable
    (build 137): {"items": [{"qid", "max", "part"}], "choices": [{"select": n, "options":
    [[qid, ...], ...]}], "by": email}. None when nothing was entered. The server checks them
    against the official total (server._workflow_operator_marks)."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("The paper's marks must be a list of questions with their marks")
    items, seen = [], set()
    for row in value.get("items") or []:
        if not isinstance(row, dict):
            raise ValueError("Every marks row needs a question number and its marks")
        qid = " ".join(str(row.get("qid") or "").split())
        if not qid:
            raise ValueError("Every marks row needs a question number")
        if qid.casefold() in seen:
            raise ValueError(f"{qid} is listed twice in the paper's marks")
        seen.add(qid.casefold())
        try:
            mark = float(row.get("max"))
        except (TypeError, ValueError):
            raise ValueError(f"{qid} needs a number of marks") from None
        if not math.isfinite(mark) or mark <= 0:
            raise ValueError(f"{qid} needs marks above 0")
        items.append({"qid": qid, "max": mark, "part": str(row.get("part") or "")[:80]})
    if not items:
        raise ValueError("Enter at least one question and its marks")

    def rows_named(qid):
        """The rows a choice rule's question stands for: the row itself, or, when there is no
        such row, all its parts. "2 of Q7, ..., Q11" with rows Q11.i to Q11.iii was totalled
        by the page and refused by the server (operator, 3 Nov 2026)."""
        key = qid.casefold()
        exact = [row["qid"] for row in items if row["qid"].casefold() == key]
        parts = exact or [row["qid"] for row in items if row["qid"].casefold().startswith((key + ".", key + "("))]
        if not parts:
            raise ValueError(f"{qid} is in a choice rule but not among the questions listed")
        return parts

    choices = []
    for choice in value.get("choices") or []:
        if not isinstance(choice, dict):
            raise ValueError("A choice rule needs how many to answer and its questions")
        alternatives = [[" ".join(str(qid).split()) for qid in (option if isinstance(option, list) else [option])
                         if str(qid).strip()] for option in choice.get("options") or []]
        alternatives = [option for option in alternatives if option]
        try:
            select = int(choice.get("select"))
        except (TypeError, ValueError):
            raise ValueError("A choice rule needs how many questions the student answers") from None
        if len(alternatives) < 2 or not 1 <= select < len(alternatives):
            raise ValueError("A choice rule needs at least two questions and asks for fewer than all of them")
        alternatives = [list(dict.fromkeys(part for qid in option for part in rows_named(qid))) for option in alternatives]
        choices.append({"select": select, "options": alternatives})
    return {"items": items, "choices": choices, "by": str(value.get("by") or "")[:120]}


def validate_options(options):
    options = dict(options)
    options["admin_email"] = str(options.get("admin_email") or "").strip()
    options["evaluator_email"] = str(options.get("evaluator_email") or "").strip()
    if options["admin_email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", options["admin_email"]):
        raise ValueError("Enter a valid admin email")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(options.get("evaluator_email", ""))):
        raise ValueError("Enter a valid evaluator email")
    if options["admin_email"].lower() == options["evaluator_email"].lower():
        raise ValueError("Use different accounts for the admin and evaluator logins")
    deadline = datetime.fromisoformat(str(options.get("deadline", "")))
    if deadline.tzinfo is not None or deadline <= datetime.now():
        raise ValueError("Choose a future deadline in this computer's local time")
    if options.get("category") not in ("school", "vidyapeeth", "commerce", "ca"):
        raise ValueError("Choose a supported written exam category")
    try:
        copies = int(options.get("copies_in_flight", COPIES_IN_FLIGHT))
    except (TypeError, ValueError):
        copies = 0
    if not 1 <= copies <= MAX_COPIES_IN_FLIGHT:
        raise ValueError(f"Copies checked at the same time must be a whole number from 1 to {MAX_COPIES_IN_FLIGHT}")
    options["copies_in_flight"] = copies
    if not str(options.get("subject", "")).strip() or not str(options.get("student_class", "")).strip():
        raise ValueError("Enter subject and class / exam level")
    total = float(options.get("total_marks", 0))
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Enter the official total marks")
    options["total_marks"] = str(total)
    options["already_assigned"] = options.get("already_assigned") is True
    options["submit_rejections"] = options.get("submit_rejections", True) is not False
    # 20 Nov 2026: a copy whose file cannot be read gets a notice page in its place, so it does
    # not stay pending on the portal for good. Turned off, such a copy is skipped as before.
    options["notice_for_unreadable"] = options.get("notice_for_unreadable", True) is not False
    options["portal_direct"] = options.get("portal_direct") is True
    options.pop("review_upload_threshold", None)   # build 135's threshold: replaced by the rule below
    try:
        threshold = float(options.get("review_min_confidence", REVIEW_UPLOAD_THRESHOLD))
    except (TypeError, ValueError):
        threshold = -1
    if not 0 <= threshold <= 100:
        raise ValueError("The faculty-review minimum confidence must be a percentage from 0 to 100")
    options["review_min_confidence"] = threshold
    marks = normalize_operator_marks(options.get("operator_marks"))
    if marks:
        options["operator_marks"] = marks
    else:
        options.pop("operator_marks", None)
    options["alert_email"] = str(options.get("alert_email") or "").strip()
    if options["alert_email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", options["alert_email"]):
        raise ValueError("Enter a valid alert email")
    return options


def owner_for(token):
    # Token has already passed the existing server allowlist/authentication gate.
    payload = token.split(".")[1]
    identity = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
    return hashlib.sha256(identity.encode()).hexdigest()


def selftest():
    """Offline proof that the portal engine can run from this installation.

    Imports the vendored engine, locates the Playwright driver this process will use
    (inside the EXE when frozen), finds the browser the engine would start (Chrome,
    else Edge) and launches it headless on a data: page. No portal, no network.
    """
    result = {"modules_ok": False, "driver_ok": False, "browser": "", "browser_version": "",
              "browser_launch_ok": False, "error": ""}
    try:
        from portal_vendor import data_filler as engine
        from portal_vendor import match_paper, rejection_browser, rejection_comments  # noqa: F401
        result["modules_ok"] = True
        from playwright._impl._driver import compute_driver_executable
        driver = compute_driver_executable()
        paths = driver if isinstance(driver, (tuple, list)) else (driver,)
        result["driver_ok"] = bool(paths) and all(Path(p).is_file() for p in paths)
        result["browser"] = str(engine.find_chrome())
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=result["browser"], headless=True,
                args=["--disable-background-networking", "--disable-component-update"])
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.abort())
                page.goto("data:text/html,<table><tbody><tr><td>student123</td></tr></tbody></table>")
                result["browser_version"] = browser.version
                result["browser_launch_ok"] = page.locator("td").count() == 1
            finally:
                browser.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def install(app, require_allowed, grader, root, paper_checker=None):
    store = WorkflowStore(root, grader)
    store.paper_checker = paper_checker
    import workflow_autopilot
    store.autopilot = workflow_autopilot.Autopilot(store, Path(root))
    store.sheet_imports = SheetImportCache()
    try:
        store.alerts = ALERT_CENTER_FACTORY(Path(root))
    except Exception:  # noqa: BLE001 - without alerts the workflow still runs
        store.alerts = QuietAlerts()
    router = APIRouter(prefix="/api/workflow")

    async def access(request, authorization):
        if not request.client or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "Portal workflow runs only in the local desktop app")
        token = await run_in_threadpool(require_allowed, authorization)
        owner = owner_for(token)
        store.latest_authorization[owner] = authorization   # a run paused on a sign-out continues with the next sign-in
        try:
            payload = token.split(".")[1]
            email = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
            if email:
                store.owner_emails[owner] = str(email)   # the default address for this account's alerts
        except Exception:  # noqa: BLE001
            pass
        return owner

    def owned(run_id, owner):
        try:
            run = store.read(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "Workflow not found")
        if run["owner"] != owner:
            raise HTTPException(404, "Workflow not found")
        return run

    def public(run):
        result = dict(run)
        result.pop("owner", None)
        # Interrupted runs are resumable after restarting the desktop app.
        if result["status"] == "running" and result["id"] not in store.active:
            result["status"] = "interrupted"
        return result

    @router.post("/sheets/connect")
    async def sheets_connect(request: Request, authorization: str | None = Header(None),
                             x_google_sheets_token: str | None = Header(None)):
        owner = await access(request, authorization)
        payload = await request.json()
        try:
            await run_in_threadpool(store.sheet_status.connect, owner, x_google_sheets_token,
                                    payload.get("expires_in", 3500) if isinstance(payload, dict) else 3500)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"connected": True}

    @router.post("/sheet-tabs")
    async def sheet_tabs(request: Request, sheet: UploadFile | None = File(None), sheet_url: str = Form(""),
                         authorization: str | None = Header(None), x_google_sheets_token: str | None = Header(None)):
        owner = await access(request, authorization)
        try:
            if sheet and sheet_url:
                raise ValueError("Choose a sheet file or a Google Sheet URL")
            if sheet:
                data = await sheet.read(MAX_BYTES + 1)
                filename = sheet.filename or "sheet.csv"
                source_name = Path(filename).name
            elif x_google_sheets_token is not None:
                descriptor = await run_in_threadpool(fetch_connected_google_metadata, sheet_url, x_google_sheets_token)
                return await run_in_threadpool(store.sheet_imports.put_google, owner, descriptor)
            else:
                data = await run_in_threadpool(download_google_sheet, sheet_url, "xlsx")
                filename, source_name = "google-sheet.xlsx", "Google Sheet export"
            return await run_in_threadpool(store.sheet_imports.put, owner, data, filename, source_name)
        except GoogleSheetError as exc:
            raise HTTPException(exc.status_code, str(exc))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @router.post("/preview")
    async def preview(request: Request, sheet: UploadFile | None = File(None), sheet_url: str = Form(""),
                      import_id: str = Form(""), worksheet: str = Form(""), authorization: str | None = Header(None),
                      x_google_sheets_token: str | None = Header(None)):
        owner = await access(request, authorization)
        try:
            if import_id:
                if sheet or sheet_url:
                    raise ValueError("Use the loaded sheet import, or load tabs again after changing the source")
                rows = await run_in_threadpool(store.sheet_imports.preview, owner, import_id, worksheet, x_google_sheets_token)
            elif sheet:
                rows = await run_in_threadpool(parse_sheet, await sheet.read(MAX_BYTES + 1), sheet.filename or "sheet.csv", worksheet or None)
            elif x_google_sheets_token is not None:
                descriptor = await run_in_threadpool(fetch_connected_google_metadata, sheet_url, x_google_sheets_token)
                if not worksheet:
                    _, gid = google_sheet_location(sheet_url)
                    tab = next((tab for tab in descriptor["tabs"] if str(tab["gid"]) == gid), None)
                    if tab is None:
                        raise ValueError("The linked tab is not in this Google Sheet. Load tabs and select a worksheet.")
                    worksheet = tab["name"]
                rows = await run_in_threadpool(read_connected_google_tab, descriptor, worksheet, x_google_sheets_token)
            elif worksheet:
                data = await run_in_threadpool(download_google_sheet, sheet_url, "xlsx")
                rows = await run_in_threadpool(parse_sheet, data, "google-sheet.xlsx", worksheet)
            else:
                rows = await run_in_threadpool(read_google_sheet, sheet_url)
            return {"rows": rows}
        except GoogleSheetError as exc:
            raise HTTPException(exc.status_code, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @router.post("/start")
    async def start(request: Request, rows: str = Form(...), options: str = Form(...),
                    question_paper: UploadFile | None = File(None), answer_key: UploadFile | None = File(None),
                    question_page_range: Annotated[str, Form()] = "",
                    answer_page_range: Annotated[str, Form()] = "",
                    use_question_paper_as_key: Annotated[bool, Form()] = False,
                    exam_documents_id: Annotated[str, Form()] = "",
                    import_id: Annotated[str, Form()] = "", worksheet: Annotated[str, Form()] = "",
                    authorization: str | None = Header(None), x_google_sheets_token: str | None = Header(None)):
        owner = await access(request, authorization)
        try:
            import fitz
            # Uploaded files take priority; documents fetched from the portal fill
            # in whichever of the two was not uploaded.
            fetched = {}
            if exam_documents_id:
                entry = store.exam_documents.get(owner, exam_documents_id)
                if entry is None or entry["status"] != "ready":
                    raise ValueError("The portal documents are not available any more. Fetch them again or upload the PDFs.")
                fetched = entry["documents"]
            if question_paper is not None:
                paper_source, paper_name = await question_paper.read(MAX_BYTES + 1), question_paper.filename
            elif fetched.get("question_paper"):
                paper_source, paper_name = fetched["question_paper"]["data"], fetched["question_paper"]["filename"]
            else:
                raise ValueError("Upload the official question paper or fetch it from the portal")
            if use_question_paper_as_key:
                key_source, key_name = paper_source, paper_name
            elif answer_key is not None:
                key_source, key_name = await answer_key.read(MAX_BYTES + 1), answer_key.filename
            elif fetched.get("answer_key"):
                key_source, key_name = fetched["answer_key"]["data"], fetched["answer_key"]["filename"]
            else:
                raise ValueError("Upload the official answer key/marking scheme or fetch the Model Answer from the portal")
            for data in (paper_source, key_source):
                if len(data) > MAX_BYTES:
                    raise ValueError("Exam document exceeds 50 MB")
            documents = [
                await run_in_threadpool(select_pdf_pages, paper_source, paper_name,
                                        question_page_range, "Question paper"),
                await run_in_threadpool(select_pdf_pages, key_source, key_name,
                                        answer_page_range, "Answer key"),
            ]
            for data in documents:
                with fitz.open(stream=data, filetype="pdf") as doc:
                    if not doc.is_pdf or doc.page_count == 0 or doc.needs_pass:
                        raise ValueError("Upload readable PDF exam documents")
            parsed = json.loads(options)
            with store.lock:
                store.check_accounts(parsed.get("evaluator_email") if isinstance(parsed, dict) else None,
                                     parsed.get("admin_email", "") if isinstance(parsed, dict) else "")
            check = None
            if store.paper_checker is not None:
                # Before anything touches the portal (operator's rule, 26 Oct 2026): the paper's
                # marks must read and add up to the official total, or the run is not created.
                parsed = validate_options(parsed)
                if parsed.get("operator_marks"):
                    parsed["operator_marks"]["by"] = store.owner_emails.get(owner, "")
                check = await run_in_threadpool(store.paper_checker, documents[0], parsed, authorization)
                if not check.get("ok"):
                    raise PaperMarksProblem(check)
            source = await run_in_threadpool(store.sheet_imports.source, owner, import_id, worksheet) if import_id else None
            if x_google_sheets_token:
                store.sheet_status.connect(owner, x_google_sheets_token)
            run = await run_in_threadpool(store.create, json.loads(rows), parsed, owner, *documents, source)
            if check is not None:
                run["marks_check"] = {key: check.get(key) for key in ("ok", "method", "items") if key in check}
                store.save(run)
            store.start(run["id"], authorization)
            return public(run)
        except PaperMarksProblem as exc:
            raise HTTPException(422, {"message": str(exc), "marks": exc.check})
        except HTTPException:
            raise
        except BusyError as exc:
            raise HTTPException(409, str(exc))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @router.post("/exam-documents")
    async def exam_documents(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        payload = await request.json()
        try:
            fetch_id = store.fetch_exam_documents(owner, payload if isinstance(payload, dict) else {})
        except BusyError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return ExamDocumentCache.public(store.exam_documents.get(owner, fetch_id))

    @router.get("/exam-documents/{fetch_id}")
    async def exam_documents_status(fetch_id: str, request: Request, authorization: str | None = Header(None)):
        entry = store.exam_documents.get(await access(request, authorization), fetch_id)
        if entry is None:
            raise HTTPException(404, "Portal documents not found or expired")
        return ExamDocumentCache.public(entry)

    @router.get("/exam-documents/{fetch_id}/{kind}")
    async def exam_document_file(fetch_id: str, kind: str, request: Request, authorization: str | None = Header(None)):
        entry = store.exam_documents.get(await access(request, authorization), fetch_id)
        document = (entry or {}).get("documents", {}).get(kind) if kind in ("question_paper", "answer_key") else None
        if document is None:
            raise HTTPException(404, "Portal document not found or expired")
        return Response(content=document["data"], media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{document["filename"]}"'})

    @router.post("/exam-documents/{fetch_id}/cancel")
    async def exam_documents_cancel(fetch_id: str, request: Request, authorization: str | None = Header(None)):
        if store.exam_documents.get(await access(request, authorization), fetch_id) is None:
            raise HTTPException(404, "Portal documents not found or expired")
        store.stop_run(f"documents:{fetch_id}")
        return {"status": "stopping"}

    @router.get("/runs")
    async def runs(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        await run_in_threadpool(store.resume_interrupted, owner, authorization)
        if getattr(store, "autopilot", None) is not None:
            await run_in_threadpool(store.autopilot.resume, owner)
        found = []
        for path in store.root.glob("*/run.json"):
            run = store.read(path.parent.name)
            if run["owner"] == owner:
                found.append(public(run))
        return {"runs": sorted(found, key=lambda r: r["created_at"], reverse=True)}

    @router.post("/autopilot/plan")
    async def autopilot_plan(request: Request, authorization: str | None = Header(None),
                             x_google_sheets_token: str | None = Header(None)):
        """Read the student tab and the accounts tab once; the tests, their inputs and the pairs."""
        owner = await access(request, authorization)
        payload = await request.json()
        import workflow_autopilot
        try:
            students, accounts = payload["students"], payload["accounts"]
            students["import_id"], accounts["import_id"]
        except (KeyError, TypeError):
            raise HTTPException(400, "Send the student sheet and the accounts sheet, each with its tab")
        try:
            tabs = workflow_autopilot.tab_names(students.get("worksheet")) or [""]
            read, sources = [], {}
            for tab in tabs:
                read.append((tab, await run_in_threadpool(store.sheet_imports.preview, owner, students["import_id"], tab, x_google_sheets_token)))
                sources[tab] = await run_in_threadpool(store.sheet_imports.source, owner, students["import_id"], tab)
            rows = workflow_autopilot.rows_of_tabs(read)
            google = {"column_name": _google_column_name, "api_json": _google_api_json, "max_bytes": MAX_BYTES, "snapshot": google_snapshot}
            values = await run_in_threadpool(workflow_autopilot.tab_values, store.sheet_imports, owner, accounts["import_id"],
                                             str(accounts.get("worksheet") or ""), x_google_sheets_token, google)
            pairs = workflow_autopilot.account_pairs(values)
            if x_google_sheets_token:
                store.sheet_status.connect(owner, x_google_sheets_token)
            return await run_in_threadpool(store.autopilot.plan, owner, rows, pairs, sources[tabs[0]], sources)
        except GoogleSheetError as exc:
            raise HTTPException(exc.status_code, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/autopilot/start")
    async def autopilot_start(request: Request, authorization: str | None = Header(None),
                              x_google_sheets_token: str | None = Header(None)):
        owner = await access(request, authorization)
        payload = await request.json()
        try:
            if x_google_sheets_token:
                store.sheet_status.connect(owner, x_google_sheets_token)
            return await run_in_threadpool(store.autopilot.start, owner, str(payload.get("plan_id") or ""), payload.get("overrides") or {},
                                           payload.get("direct") is True, payload.get("copies_at_once"))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.get("/autopilot")
    async def autopilot_status(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        return store.autopilot.status(owner)

    @router.post("/autopilot/stop")
    async def autopilot_stop(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        try:
            return await run_in_threadpool(store.autopilot.stop, owner)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/autopilot/paper-links")
    async def autopilot_paper_links(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        try:
            return await run_in_threadpool(store.autopilot.paper_links, owner)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:  # noqa: BLE001 - the portal's own reason, for the page
            raise HTTPException(502, f"The portal did not give the paper links: {exc}")

    @router.post("/autopilot/marks")
    async def autopilot_marks(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        body = await request.json()
        try:
            return await run_in_threadpool(store.autopilot.retry_test, owner, str(body.get("key") or ""), body.get("total_marks"),
                                           body.get("operator_marks"), store.owner_emails.get(owner, ""))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/autopilot/answer-key")
    async def autopilot_answer_key(request: Request, key: Annotated[str, Form()] = "",
                                   answer_key: UploadFile | None = File(None),
                                   authorization: str | None = Header(None)):
        """The Model Answer for a test the portal has none for (operator, 19 Nov 2026)."""
        owner = await access(request, authorization)
        data = await answer_key.read(MAX_BYTES + 1) if answer_key is not None else b""
        try:
            return await run_in_threadpool(store.autopilot.supply_answer_key, owner, key, data,
                                           (answer_key.filename if answer_key is not None else "") or "",
                                           store.owner_emails.get(owner, ""))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/portal-login")
    async def portal_login_status(request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        return store.portal_logins.status()

    @router.post("/portal-login")
    async def portal_login_save(request: Request, authorization: str | None = Header(None)):
        """Save (or with an empty password, remove) the portal password the windows sign in with."""
        await access(request, authorization)
        payload = await request.json()
        password = payload.get("password") if isinstance(payload, dict) else None
        if not isinstance(password, str):
            raise HTTPException(400, "Send the portal password as text (empty removes it)")
        try:
            await run_in_threadpool(store.portal_logins.save, password)
        except OSError as exc:
            raise HTTPException(400, f"The password could not be stored on this computer: {exc}")
        return store.portal_logins.status()

    @router.get("/windows")
    async def windows_status(request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        return store.windows.status()

    @router.post("/windows")
    async def windows_settings(request: Request, authorization: str | None = Header(None)):
        """{"show_all": bool}: every portal window on screen, or hidden until one needs a person."""
        await access(request, authorization)
        payload = await request.json()
        show_all = payload.get("show_all") if isinstance(payload, dict) else None
        if not isinstance(show_all, bool):
            raise HTTPException(400, "Send show_all as true or false")
        try:
            return await run_in_threadpool(store.show_all_windows, show_all)
        except OSError as exc:
            raise HTTPException(400, f"The setting could not be saved: {exc}")

    @router.post("/windows/show")
    async def windows_show(request: Request, authorization: str | None = Header(None)):
        """{"role", "account", "show"}: put one portal window on screen, or hide it again."""
        await access(request, authorization)
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("show"), bool) or not isinstance(payload.get("account", ""), str):
            raise HTTPException(400, "Send role, account and show")
        try:
            return await run_in_threadpool(store.show_window, payload.get("role"), payload.get("account", ""), payload["show"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/windows/sign-in")
    async def windows_sign_in(request: Request, authorization: str | None = Header(None)):
        """{"accounts": [{"role", "account"}]}: open each ID's window; those not signed in come on screen."""
        await access(request, authorization)
        payload = await request.json()
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if not isinstance(accounts, list) or not accounts or len(accounts) > 100:
            raise HTTPException(400, "Send the IDs to sign in")
        wanted = []
        for item in accounts:
            role = item.get("role") if isinstance(item, dict) else None
            account = str(item.get("account") or "").strip() if isinstance(item, dict) else ""
            if role not in ("admin", "evaluator") or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", account):
                raise HTTPException(400, "Each ID needs a role (admin or evaluator) and an email")
            if (role, account.lower()) not in {(r, a.lower()) for r, a in wanted}:
                wanted.append((role, account))
        return await run_in_threadpool(store.sign_in_windows, wanted)

    @router.get("/windows/screenshot/{name}")
    async def windows_screenshot(name: str, request: Request, authorization: str | None = Header(None)):
        """The picture a portal window took when a step failed (workflow_data/screenshots)."""
        await access(request, authorization)
        target = store.root / "screenshots" / name
        if not re.fullmatch(r"[\w.-]+\.png", name) or not target.is_file():
            raise HTTPException(404, "Picture not found")
        return FileResponse(target, media_type="image/png")

    @router.get("/alerts")
    async def alerts_status(request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        return store.alerts.status()

    @router.post("/alerts/ack")
    async def alerts_ack(request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        await run_in_threadpool(store.alerts.acknowledge)
        return store.alerts.status()

    @router.post("/alerts/email")
    async def alerts_email(request: Request, authorization: str | None = Header(None)):
        """Sender address and its Google app password (stored encrypted for this Windows user)."""
        await access(request, authorization)
        payload = await request.json()
        try:
            store.alerts.save_email_settings(str(payload.get("sender") or ""),
                                             payload.get("app_password") if isinstance(payload.get("app_password"), str) else None)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))
        return store.alerts.status()

    @router.post("/alerts/test")
    async def alerts_test(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        payload = await request.json()
        to = str((payload or {}).get("to") or store.owner_emails.get(owner) or "").strip()
        store.alerts.raise_alert(f"test:{datetime.now().isoformat()}", "Test alert",
                                 "This is a test of the AI Checker workflow alerts: Windows notification and email.",
                                 to=[to] if to else [])
        return store.alerts.status()

    @router.post("/{run_id}/open-folder")
    async def open_checked_folder(run_id: str, request: Request, authorization: str | None = Header(None)):
        """Open the run's checked-copies folder in Windows Explorer (build 146)."""
        owner = await access(request, authorization)
        run = owned(run_id, owner)
        folder = Path(run.get("checked_folder") or "")
        if not run.get("checked_folder") or not folder.is_dir():
            raise HTTPException(404, "No checked copy of this run has been saved yet")
        if not hasattr(os, "startfile"):
            raise HTTPException(400, f"Open this folder yourself: {folder}")
        await run_in_threadpool(os.startfile, str(folder))
        return {"folder": str(folder)}

    @router.get("/{run_id}")
    async def status(run_id: str, request: Request, authorization: str | None = Header(None)):
        return public(owned(run_id, await access(request, authorization)))

    @router.post("/{run_id}/resume")
    async def resume(run_id: str, request: Request, authorization: str | None = Header(None),
                     x_google_sheets_token: str | None = Header(None)):
        owner = await access(request, authorization)
        owned(run_id, owner)
        try:
            if x_google_sheets_token:
                store.sheet_status.connect(owner, x_google_sheets_token)
            store.start(run_id, authorization)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"status": "running"}

    @router.post("/{run_id}/stop")
    async def stop(run_id: str, request: Request, authorization: str | None = Header(None)):
        owned(run_id, await access(request, authorization))
        if store.stop_run(run_id):
            return {"status": "stopping after current operation"}
        return {"status": "not running in this app"}

    @router.post("/{run_id}/inputs")
    async def change_inputs(run_id: str, request: Request, authorization: str | None = Header(None)):
        """Change a saved run's evaluator, admin, official total or deadline; Resume / retry then
        continues with them (copies assigned to the old evaluator are moved by the assignment step)."""
        owner = await access(request, authorization)
        payload = await request.json()
        with store.lock:
            if run_id in store.active:
                raise HTTPException(409, "Stop this run before changing its inputs")
            run = owned(run_id, owner)
            allowed = ("evaluator_email", "admin_email", "total_marks", "deadline", "submit_rejections",
                       "review_min_confidence", "alert_email", "operator_marks")
            changes = {key: payload[key] for key in allowed if isinstance(payload, dict) and key in payload}
            if isinstance(changes.get("operator_marks"), dict):
                changes["operator_marks"] = {**changes["operator_marks"], "by": store.owner_emails.get(owner, "")}
            if not changes:
                raise HTTPException(400, "Nothing to change: " + ", ".join(allowed))
            merged = {**run["options"], **changes}
            if "deadline" not in changes:
                try:
                    stale = datetime.fromisoformat(str(merged.get("deadline", ""))) <= datetime.now()
                except ValueError:
                    stale = True
                if stale:   # an old run's deadline has passed: a fresh one, as the portal needs a date
                    merged["deadline"] = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
            try:
                run["options"] = validate_options(merged)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            if any(key in changes for key in ("evaluator_email", "admin_email")):
                for row in run["rows"]:   # assigned to the old evaluator: the assignment step moves them
                    if row["stage"] not in FINISHED_STAGES:
                        row["portal_assigned"] = False
            run.pop("error", None)
            store.save(run)
        return public(run)

    @router.post("/{run_id}/reconcile")
    async def reconcile(run_id: str, request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        payload = await request.json()
        with store.lock:
            if run_id in store.active:
                raise HTTPException(409, "Stop this run before reconciling its portal outcomes")
            run = owned(run_id, owner)
            row = next((r for r in run["rows"] if r["submission_id"] == payload.get("submission_id")), None)
            came_from = (row or {}).get("assign_from") or "pending"
            transitions = {"assignment_uncertain": {"confirmed": "assigned" if came_from == "pending" else came_from, "not_done": came_from},
                           "upload_uncertain": {"confirmed": "uploaded", "not_done": "checked"},
                           "rejection_uncertain": {"confirmed": "rejection_submitted", "not_done": "rejected"}}
            target = transitions.get((row or {}).get("stage"), {}).get(payload.get("outcome"))
            if not target:
                raise HTTPException(400, "This copy has no uncertain portal action to reconcile")
            if row["stage"] == "assignment_uncertain" and payload.get("outcome") == "confirmed":
                row["portal_assigned"] = True
            row["stage"], row["error"] = target, ""
            row["failures"] = 0
            if target == "uploaded":
                _, _, report_path = store.copy_paths(run, row["submission_id"])
                if report_path.exists():
                    row["awarded_marks"] = json.loads(report_path.read_text(encoding="utf-8"))["total_score"]
            elif target == "rejection_submitted":
                row["awarded_marks"] = 0
            elif target == "rejected":
                row["error"] = row.get("rejection_reason", "")
            if all(r["stage"] in FINISHED_STAGES for r in run["rows"]):
                run["status"] = "complete"
                run.pop("error", None)
            store.save(run)
        return public(run)

    @router.post("/{run_id}/submit-rejections")
    async def submit_rejections(run_id: str, request: Request, authorization: str | None = Header(None)):
        run = owned(run_id, await access(request, authorization))
        if not any(r["stage"] == "rejected" for r in run["rows"]):
            raise HTTPException(400, "This run has no rejected copies waiting for a portal comment")
        try:
            store.start(run_id, authorization, mode="rejections")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"status": "running"}

    @router.get("/{run_id}/report.csv")
    async def run_report(run_id: str, request: Request, authorization: str | None = Header(None)):
        run = owned(run_id, await access(request, authorization))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(store.report_rows(run))
        return Response(content="\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="run-{run_id[:8]}-report.csv"'})

    @router.get("/{run_id}/document/{kind}")
    async def run_document(run_id: str, kind: str, request: Request, authorization: str | None = Header(None)):
        run = owned(run_id, await access(request, authorization))
        names = {"question_paper": "question_paper.pdf", "answer_key": "answer_key.pdf"}
        if kind not in names or not (store.folder(run["id"]) / names[kind]).exists():
            raise HTTPException(404, "Document not found")
        return FileResponse(store.folder(run["id"]) / names[kind], media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{names[kind]}"'})

    @router.get("/{run_id}/copy/{submission_id}")
    async def copy(run_id: str, submission_id: str, request: Request, authorization: str | None = Header(None)):
        run = owned(run_id, await access(request, authorization))
        if not any(r["submission_id"] == submission_id for r in run["rows"]):
            raise HTTPException(404, "Copy not found")
        _, path, _ = store.copy_paths(run, submission_id)
        if not path.exists():
            raise HTTPException(404, "Checked PDF is not available")
        return FileResponse(path, media_type="application/pdf", filename=f"checked_{submission_id}.pdf")

    app.include_router(router)
    return store
