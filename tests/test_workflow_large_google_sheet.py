"""Large connected Sheets list metadata and fetch only one bounded worksheet."""
import asyncio
import base64
import json
import re

from fastapi import FastAPI, HTTPException
import httpx
import pytest

import integrated_workflow as workflow


URL = "https://docs.google.com/spreadsheets/d/large_workbook/edit?gid=711687345"
TOKEN = "ya29.synthetic_token_never_persisted"
TAB = "Day 2 / John's copies"
HEADERS = ["submission_id", "student_id", "student_name", "unused comment", "program_name",
           "test_series_name", "test_id", "test_name", "copy_url", "portal_status", "_id",
           "studentid", "email", "email", ""]


def row(identity="copy_first"):
    return [identity, "student123", "Synthetic", "ignored", "Program", "Batch Tests", "test123",
            "Maths Test", "https://example.cloudfront.net/copy.pdf", "Submitted", identity,
            "student123", "ignored@example.com", "ignored@example.com", ""]


def metadata(row_count=502, column_count=len(HEADERS)):
    return {"properties": {"title": "Huge workbook"}, "sheets": [
        {"properties": {"sheetId": 999, "title": "Huge unused history",
                        "gridProperties": {"rowCount": 900000, "columnCount": 2048}}},
        {"properties": {"sheetId": 711687345, "title": TAB,
                        "gridProperties": {"rowCount": row_count, "columnCount": column_count}}},
    ]}


class Response:
    def __init__(self, payload=None, status=200, data=None, redirect=False):
        self.data = data if data is not None else json.dumps(payload or {}).encode()
        self.status_code, self.is_redirect = status, redirect

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset:offset + chunk_size]


def column_number(value):
    number = 0
    for letter in value:
        number = number * 26 + ord(letter) - 64
    return number - 1


class Google:
    """If the old whole-workbook grid request recurs, fail before returning it."""
    def __init__(self, monkeypatch, rows=None, row_count=502):
        self.rows = rows if rows is not None else {1: row(), 501: row("copy_last")}
        self.metadata = metadata(row_count)
        self.calls, self.ranges = [], []
        self.override, self.statuses = None, []
        monkeypatch.setattr(workflow.requests, "get", self.get)

    def get(self, endpoint, **options):
        self.calls.append((endpoint, options))
        assert endpoint in ("https://sheets.googleapis.com/v4/spreadsheets/large_workbook",
                            "https://sheets.googleapis.com/v4/spreadsheets/large_workbook/values:batchGet")
        assert options["headers"] == {"Authorization": "Bearer " + TOKEN}
        assert options["stream"] is True and options["allow_redirects"] is False
        if self.statuses:
            status = self.statuses.pop(0)
            if status != 200:
                return Response({"error": {"message": TOKEN + " private data"}}, status=status)
        params = options["params"]
        if endpoint.endswith("/values:batchGet"):
            assert params["fields"] == "valueRanges(values)"
            assert params["valueRenderOption"] == "UNFORMATTED_VALUE" and params["majorDimension"] == "ROWS"
            value_ranges = []
            for cell_range in params["ranges"]:
                prefix = "'" + TAB.replace("'", "''") + "'!"
                assert cell_range.startswith(prefix)
                match = re.fullmatch(r"([A-Z]+)2:([A-Z]+)(\d+)", cell_range[len(prefix):])
                assert match is not None
                left, right, allocated = match.groups()
                column = column_number(left)
                assert left == right and int(allocated) == self.metadata["sheets"][1]["properties"]["gridProperties"]["rowCount"]
                assert HEADERS[column] in {a for aliases in workflow.ALIASES.values() for a in aliases}
                values = []
                for number in range(1, max(self.rows, default=0) + 1):
                    raw = self.rows.get(number, [])
                    values.append([raw[column]] if column < len(raw) and raw[column] else [])
                while values and not values[-1]:
                    values.pop()
                value_ranges.append({"values": values})
            return Response({"valueRanges": value_ranges})
        if "includeGridData" not in params:
            assert params == {"fields": workflow.GOOGLE_METADATA_FIELDS}
            return Response(self.metadata)
        assert params["includeGridData"] == "true"
        assert params["fields"] == workflow.GOOGLE_GRID_FIELDS
        assert params.get("ranges"), "whole workbook cells (> 50 MB) must never be fetched"
        grids = []
        for cell_range in params["ranges"]:
            self.ranges.append(cell_range)
            prefix = "'" + TAB.replace("'", "''") + "'!"
            assert cell_range.startswith(prefix), "cells from unused worksheets must not be requested"
            match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", cell_range[len(prefix):])
            assert match is not None
            left, first, right, last = match.groups()
            left, right, first, last = column_number(left), column_number(right), int(first) - 1, int(last) - 1
            if first == 0:
                assert last == 0 and left == 0 and right == len(HEADERS) - 1
            else:
                assert left == right and HEADERS[left] in {a for aliases in workflow.ALIASES.values() for a in aliases}
                assert last - first + 1 <= workflow.GOOGLE_ROWS_PER_PAGE
            raw_rows = []
            for number in range(first, last + 1):
                values = HEADERS if number == 0 else self.rows.get(number, [])
                cells = []
                for column in range(left, right + 1):
                    value = values[column] if column < len(values) else ""
                    cell = {"formattedValue": value} if value else {}
                    if number and column == 8 and value:
                        cell = {"formattedValue": "Download copy", "hyperlink": value}
                    if number and column == 4 and value:
                        cell["hyperlink"] = "https://example.com/program"
                    cells.append(cell)
                raw_rows.append({"values": cells})
            while raw_rows and not any(raw_rows[-1]["values"]):
                raw_rows.pop()
            grids.append({"startRow": first, "startColumn": left, "rowData": raw_rows})
        payload = {"sheets": [{"properties": {"sheetId": 711687345, "title": TAB}, "data": grids}]}
        if self.override:
            payload = self.override(payload)
        return Response(payload)


def authorization(owner="alice", oauth=True):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": owner}).encode()).decode().rstrip("=")
    result = {"Authorization": f"Bearer h.{payload}.s"}
    if oauth:
        result["X-Google-Sheets-Token"] = TOKEN
    return result


class Client:
    def __init__(self, app):
        self.app = app

    def post(self, path, **options):
        async def send():
            transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.post(path, **options)
        return asyncio.run(send())


@pytest.fixture
def app_client(tmp_path):
    app = FastAPI()
    def allowed(auth):
        if not auth:
            raise HTTPException(401, "Sign in")
        return auth.removeprefix("Bearer ")
    store = workflow.install(app, allowed, lambda *args: {}, tmp_path)
    return Client(app), store


def load(client):
    response = client.post("/api/workflow/sheet-tabs", headers=authorization(), data={"sheet_url": URL})
    assert response.status_code == 200, response.text
    return response.json()


def preview(client, source, headers=None):
    return client.post("/api/workflow/preview", headers=headers if headers is not None else authorization(),
                       data={"import_id": source["import_id"], "worksheet": TAB})


def test_loading_tabs_never_downloads_large_unselected_sheet(app_client, monkeypatch):
    client, store = app_client
    google = Google(monkeypatch)
    source = load(client)
    assert source["requires_google_token"] is True
    assert source["tabs"] == [{"name": "Huge unused history"}, {"name": TAB}]
    assert len(google.calls) == 1 and not google.ranges
    assert TOKEN not in repr(store.sheet_imports.entries)
    assert "data" not in store.sheet_imports.entries[source["import_id"]]


def test_selected_tab_pages_include_first_and_last_rows_and_only_workflow_columns(app_client, monkeypatch):
    client, store = app_client
    google = Google(monkeypatch)
    source = load(client)
    response = preview(client, source)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert [item["submission_id"] for item in rows] == ["copy_first", "copy_last"]
    assert rows[0]["copy_url"] == row()[8] and rows[0]["program_name"] == "Program"
    assert len(google.calls) == 6  # load properties, revalidate, headers, compact extent, two pages
    assert any(cell_range.endswith("A2:A501") for cell_range in google.ranges)
    assert any(cell_range.endswith("A502:A502") for cell_range in google.ranges)
    assert TOKEN not in repr(store.sheet_imports.entries)
    assert not list(store.root.iterdir())
    calls = len(google.calls)
    assert preview(client, source).json() == response.json()
    assert preview(client, source, authorization(oauth=False)).json() == response.json()
    assert len(google.calls) == calls, "cached selected-tab preview must not re-read Google"


def test_connected_metadata_cache_requires_token_owner_and_unexpired_import(app_client, monkeypatch):
    client, store = app_client
    google = Google(monkeypatch)
    source = load(client)
    assert preview(client, source, authorization(oauth=False)).status_code == 403
    assert preview(client, source, authorization("bob")).status_code == 404
    assert len(google.calls) == 1
    entry = store.sheet_imports.entries[source["import_id"]]
    entry["expires_at"] = store.sheet_imports.clock() - 1
    assert preview(client, source).status_code == 404
    assert not store.sheet_imports.entries


@pytest.mark.parametrize("change", ["rename", "replace", "missing"])
def test_changed_selected_tab_is_not_silently_replaced(app_client, monkeypatch, change):
    client, _ = app_client
    google = Google(monkeypatch)
    source = load(client)
    if change == "rename":
        google.metadata["sheets"][1]["properties"]["title"] = "Renamed"
    elif change == "replace":
        google.metadata["sheets"][1]["properties"]["sheetId"] = 10
    else:
        google.metadata["sheets"].pop()
    response = preview(client, source)
    assert response.status_code == 400 and "changed or was renamed" in response.json()["detail"]
    assert not google.ranges


def test_response_id_change_after_header_does_not_import_another_sheet(app_client, monkeypatch):
    client, _ = app_client
    google = Google(monkeypatch)
    source = load(client)
    def replace(payload):
        payload["sheets"][0]["properties"]["sheetId"] = 10
        return payload
    google.override = replace
    response = preview(client, source)
    assert response.status_code == 400 and "changed or was renamed" in response.json()["detail"]


def test_equal_duplicate_identity_aliases_coalesce_but_conflicting_ids_fail(app_client, monkeypatch):
    client, _ = app_client
    conflicting = row()
    conflicting[10] = "wrong_submission"
    google = Google(monkeypatch, rows={1: conflicting}, row_count=2)
    source = load(client)
    response = preview(client, source)
    assert response.status_code == 400 and "conflicting duplicate columns for submission_id" in response.json()["detail"]


@pytest.mark.parametrize("status", [429, 503])
def test_transient_google_error_retries_without_exposing_response(monkeypatch, status):
    google = Google(monkeypatch)
    google.statuses = [status, status, 200]
    delays = []
    monkeypatch.setattr(workflow.time, "sleep", delays.append)
    descriptor = workflow.fetch_connected_google_metadata(URL, TOKEN)
    assert descriptor["tabs"][1]["name"] == TAB
    assert len(google.calls) == 3 and delays == ([2.0, 5.0] if status == 429 else [0.25, 0.75])


def test_import_limit_applies_to_selected_normalized_copies(app_client, monkeypatch):
    client, store = app_client
    google = Google(monkeypatch, rows={1: row()}, row_count=2)
    source = load(client)
    get = google.get
    def shrink_after_extent(endpoint, **options):
        response = get(endpoint, **options)
        if endpoint.endswith("/values:batchGet"):
            monkeypatch.setattr(workflow, "MAX_BYTES", 100)
        return response
    monkeypatch.setattr(workflow.requests, "get", shrink_after_extent)
    response = preview(client, source)
    assert response.status_code == 400 and "Selected tab's imported copies exceed" in response.json()["detail"]
    assert not store.sheet_imports.entries[source["import_id"]]["google_rows"]


def test_million_allocated_rows_with_few_copies_are_not_rejected(app_client, monkeypatch):
    client, _ = app_client
    google = Google(monkeypatch, row_count=1_000_000)
    source = load(client)
    response = preview(client, source)
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) == 2
    assert len(google.calls) == 6


def test_actual_nonempty_copy_rows_have_a_limit(app_client, monkeypatch):
    client, _ = app_client
    google = Google(monkeypatch, row_count=1_000_000)
    source = load(client)
    monkeypatch.setattr(workflow, "MAX_GOOGLE_ROWS", 1)
    response = preview(client, source)
    assert response.status_code == 400 and "copy-row limit" in response.json()["detail"]
    assert len(google.ranges) == 1  # header only; limit checked before grid pages


def test_late_copy_after_blank_gap_keeps_physical_row_and_skips_empty_windows(app_client, monkeypatch):
    client, _ = app_client
    google = Google(monkeypatch, rows={1: row(), 5001: row("copy_late")}, row_count=1_000_000)
    source = load(client)
    response = preview(client, source)
    assert response.status_code == 200, response.text
    assert [item["submission_id"] for item in response.json()["rows"]] == ["copy_first", "copy_late"]
    assert len(google.calls) == 6
    assert any(cell_range.endswith("A5002:A5002") for cell_range in google.ranges)
    assert not any(cell_range.endswith("A502:A1001") for cell_range in google.ranges)


def test_partial_late_row_with_no_id_is_rejected_at_its_actual_sheet_row(app_client, monkeypatch):
    client, _ = app_client
    partial = row("copy_late")
    partial[0] = partial[10] = ""
    Google(monkeypatch, rows={1: row(), 5001: partial}, row_count=1_000_000)
    source = load(client)
    response = preview(client, source)
    assert response.status_code == 400
    assert "Row 5002: missing submission_id" in response.json()["detail"]


def test_cells_outside_requested_columns_and_rows_are_rejected(app_client, monkeypatch):
    client, _ = app_client
    google = Google(monkeypatch, rows={1: row()}, row_count=2)
    source = load(client)
    def extra_column(payload):
        if any(grid["startRow"] for grid in payload["sheets"][0]["data"]):
            payload["sheets"][0]["data"].append({"startRow": 1, "startColumn": 3,
                                                      "rowData": [{"values": [{"formattedValue": "private unused data"}]}]})
        return payload
    google.override = extra_column
    response = preview(client, source)
    assert response.status_code == 400 and "outside the selected columns" in response.json()["detail"]


def test_failed_page_is_not_cached_and_retry_starts_from_complete_metadata(app_client, monkeypatch):
    client, store = app_client
    google = Google(monkeypatch, rows={1: row()}, row_count=2)
    source = load(client)
    google.override = lambda payload: {}
    assert preview(client, source).status_code == 400
    assert not store.sheet_imports.entries[source["import_id"]]["google_rows"]
    google.override = None
    assert preview(client, source).status_code == 200


def test_cache_normalized_bytes_are_bounded_without_evicting_current_import(monkeypatch):
    Google(monkeypatch, rows={1: row()}, row_count=2)
    descriptor = workflow.fetch_connected_google_metadata(URL, TOKEN)
    cache = workflow.SheetImportCache(max_bytes=700)
    source = cache.put_google("owner", descriptor)
    cache.max_bytes = cache.entry_size(cache.entries[source["import_id"]]) + 10
    with pytest.raises(ValueError, match="cache size"):
        cache.preview("owner", source["import_id"], TAB, TOKEN)
    assert source["import_id"] in cache.entries
    assert not cache.entries[source["import_id"]]["google_rows"]
