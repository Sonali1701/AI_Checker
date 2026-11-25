"""Read-only private Sheets imports; Google and portal traffic are mocked."""
import asyncio
import base64
import io
import json
import re

from fastapi import FastAPI, HTTPException
import httpx
import openpyxl
import pytest
import requests

import integrated_workflow as workflow


SHEET_URL = "https://docs.google.com/spreadsheets/d/private_example/edit?gid=711687345#gid=711687345"
OAUTH_TOKEN = "ya29.synthetic_readonly_token"
SECOND_TAB = "Day 2 / John's worksheet with a long exact name"


def copy_row(identity="copy_second"):
    return dict(submission_id=identity, student_id="student123", student_name="Synthetic",
                program_name="Program", test_series_name="Batch Tests", test_id="test123",
                test_name="Maths Test", copy_url="https://example.cloudfront.net/copy.pdf",
                portal_status="Submitted")


def cells(values):
    return {"values": [{"formattedValue": str(value)} for value in values]}


def google_payload():
    return {"properties": {"title": "Private workbook title"}, "sheets": [
        {"properties": {"title": "First day", "sheetId": 0, "gridProperties": {"rowCount": 2, "columnCount": len(copy_row())}},
         "data": [{"rowData": [cells(copy_row()), cells(copy_row("copy_first").values())]}]},
        {"properties": {"title": SECOND_TAB, "sheetId": 711687345, "gridProperties": {"rowCount": 2, "columnCount": len(copy_row())}},
         "data": [{"rowData": [cells(copy_row()), cells(copy_row().values())]}]},
    ]}


def authorization(owner="alice", oauth=True):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": owner}).encode()).decode().rstrip("=")
    headers = {"Authorization": f"Bearer header.{payload}.signature"}
    if oauth:
        headers["X-Google-Sheets-Token"] = OAUTH_TOKEN
    return headers


class Client:
    def __init__(self, app, host="127.0.0.1"):
        self.app, self.host = app, host

    def post(self, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app, client=(self.host, 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.post(path, **kwargs)
        return asyncio.run(send())


@pytest.fixture
def app_client(tmp_path):
    app = FastAPI()
    def allowed(auth):
        if not auth:
            raise HTTPException(401, "Sign in")
        return auth.removeprefix("Bearer ")
    store = workflow.install(app, allowed, lambda *args: {}, tmp_path)
    return app, Client(app), store


class Response:
    def __init__(self, payload=None, status=200, data=None, location=None):
        self.status_code = status
        self.data = data if data is not None else json.dumps(payload or {}).encode()
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None and 300 <= status < 400

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for number in range(0, len(self.data), chunk_size):
            yield self.data[number:number + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP error")


def mock_google(monkeypatch, response):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(workflow.requests, "get", get)
    return calls


def mock_lazy_google(monkeypatch, payload=None):
    """Fail if tab discovery reads cells or selected-tab reads are unbounded."""
    payload = payload or google_payload()
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        params = kwargs["params"]
        if url.endswith("/values:batchGet"):
            assert params["valueRenderOption"] == "UNFORMATTED_VALUE"
            assert params["majorDimension"] == "ROWS"
            assert params["fields"] == "valueRanges(values)"
            value_ranges = []
            for requested in params["ranges"]:
                match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)2:([A-Z]+)(\d+)", requested)
                assert match, f"Unexpected extent range: {requested}"
                title, first_column, last_column, last_row = match.groups()
                assert first_column == last_column
                title = title.replace("''", "'")
                sheet = next(sheet for sheet in payload["sheets"] if sheet["properties"]["title"] == title)
                assert int(last_row) == sheet["properties"]["gridProperties"]["rowCount"]
                column = openpyxl.utils.column_index_from_string(first_column) - 1
                values = []
                for row in sheet["data"][0].get("rowData", [])[1:]:
                    cells = row.get("values", [])
                    value = workflow._google_cell_text(cells[column]) if len(cells) > column else ""
                    values.append([value] if value else [])
                while values and not values[-1]:
                    values.pop()
                value_ranges.append({"values": values})
            return Response({"valueRanges": value_ranges})
        if "ranges" not in params:
            assert params.get("includeGridData") != "true", "Tab listing downloaded cell data"
            assert "rowData" not in params.get("fields", "")
            return Response({"properties": payload.get("properties", {}), "sheets": [
                {"properties": sheet["properties"]} for sheet in payload["sheets"]]})
        assert params["includeGridData"] == "true"
        selected = None
        data = []
        for requested in params["ranges"]:
            match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)", requested)
            assert match, f"Unexpected or unbounded range: {requested}"
            title, start_column, start_row, end_column, end_row = match.groups()
            title = title.replace("''", "'")
            sheet = next(sheet for sheet in payload["sheets"] if sheet["properties"]["title"] == title)
            assert selected is None or selected == sheet
            selected = sheet
            start, end = int(start_row) - 1, int(end_row)
            assert end - start <= 500
            first_col = openpyxl.utils.column_index_from_string(start_column) - 1
            last_col = openpyxl.utils.column_index_from_string(end_column)
            source_rows = sheet["data"][0].get("rowData", [])
            rows = [{"values": row.get("values", [])[first_col:last_col]} for row in source_rows[start:end]]
            data.append({"startRow": start, "startColumn": first_col, "rowData": rows})
        return Response({"sheets": [{"properties": selected["properties"], "data": data}]})

    monkeypatch.setattr(workflow.requests, "get", get)
    return calls


def load(client, **kwargs):
    return client.post("/api/workflow/sheet-tabs", headers=kwargs.pop("headers", authorization()),
                       data={"sheet_url": SHEET_URL}, **kwargs)


def test_connected_metadata_then_selected_tab_uses_readonly_api_and_cache(app_client, monkeypatch):
    _, client, store = app_client
    payload = google_payload()
    payload["sheets"][1]["properties"]["gridProperties"]["rowCount"] = 1000000
    calls = mock_lazy_google(monkeypatch, payload)
    loaded = load(client)
    assert loaded.status_code == 200
    source = loaded.json()
    assert source["tabs"] == [{"name": "First day"}, {"name": SECOND_TAB}]
    assert source["source_name"] == "Connected Google Sheet"
    assert source["requires_google_token"] is True
    assert len(calls) == 1
    url, options = calls[0]
    assert url == "https://sheets.googleapis.com/v4/spreadsheets/private_example"
    assert options["headers"] == {"Authorization": "Bearer " + OAUTH_TOKEN}
    assert options["allow_redirects"] is False and options["stream"] is True
    assert "includeGridData" not in options["params"]
    assert "gridProperties(rowCount,columnCount)" in options["params"]["fields"]
    preview = client.post("/api/workflow/preview", headers=authorization(),
                          data={"import_id": source["import_id"], "worksheet": SECOND_TAB})
    assert preview.status_code == 200
    assert preview.json()["rows"][0]["submission_id"] == "copy_second"
    assert len(calls) > 1
    assert len(calls) < 10, "Allocated empty rows must not trigger thousands of downloads"
    assert any(url.endswith("/values:batchGet") for url, _ in calls)
    assert all(SECOND_TAB.replace("'", "''") in requested for _, kwargs in calls
               for requested in kwargs.get("params", {}).get("ranges", []))
    request_count = len(calls)
    cached = client.post("/api/workflow/preview", headers=authorization(oauth=False),
                         data={"import_id": source["import_id"], "worksheet": SECOND_TAB})
    assert cached.status_code == 200
    assert cached.json() == preview.json()
    assert len(calls) == request_count
    assert OAUTH_TOKEN not in repr(store.sheet_imports.entries)
    assert not list(store.root.iterdir())


def test_connected_cache_remains_owner_bound(app_client, monkeypatch):
    _, client, _ = app_client
    mock_lazy_google(monkeypatch)
    source = load(client).json()
    preview = client.post("/api/workflow/preview", headers=authorization("bob", oauth=False),
                          data={"import_id": source["import_id"], "worksheet": SECOND_TAB})
    assert preview.status_code == 404


def test_direct_connected_preview_uses_link_gid_and_missing_tab_is_not_replaced(app_client, monkeypatch):
    _, client, _ = app_client
    mock_lazy_google(monkeypatch)
    response = client.post("/api/workflow/preview", headers=authorization(), data={"sheet_url": SHEET_URL})
    assert response.status_code == 200
    assert response.json()["rows"][0]["submission_id"] == "copy_second"
    missing = client.post("/api/workflow/preview", headers=authorization(),
                          data={"sheet_url": SHEET_URL.replace("711687345", "1234")})
    assert missing.status_code == 400
    explicit = client.post("/api/workflow/preview", headers=authorization(),
                           data={"sheet_url": SHEET_URL, "worksheet": "First day"})
    assert explicit.status_code == 200
    assert explicit.json()["rows"][0]["submission_id"] == "copy_first"


def test_sparse_rows_cells_and_effective_values_preserve_column_positions():
    payload = google_payload()
    values = list(copy_row().values())
    values[2] = ""
    missing_name = cells(values)
    missing_name["values"][2] = {}
    missing_name["values"][0] = {"effectiveValue": {"stringValue": "copy_sparse"}}
    payload["sheets"][1]["data"] = [
        {"rowData": [cells(copy_row()), missing_name, {}]},
        {"startRow": 3, "startColumn": 0, "rowData": [cells(copy_row("copy_later").values())]},
    ]
    data = workflow._google_snapshot_bytes(payload)
    rows = workflow.parse_sheet(data, "sheet.google-sheet.json", SECOND_TAB)
    assert [row["submission_id"] for row in rows] == ["copy_sparse", "copy_later"]
    assert rows[0]["student_name"] == ""
    assert rows[0]["test_id"] == "test123"
    assert workflow._google_cell_text({"effectiveValue": {"numberValue": 0}}) == "0"
    assert workflow._google_cell_text({"effectiveValue": {"boolValue": False}}) == "FALSE"


def test_hyperlink_formula_preserves_copy_target_without_replacing_other_labels():
    payload = google_payload()
    row = payload["sheets"][1]["data"][0]["rowData"][1]["values"]
    columns = list(copy_row())
    row[columns.index("copy_url")] = {"formattedValue": "Download copy", "hyperlink": copy_row()["copy_url"]}
    row[columns.index("program_name")] = {"formattedValue": "Program", "hyperlink": "https://example.com/program"}
    data = workflow._google_snapshot_bytes(payload)
    imported = workflow.parse_sheet(data, "sheet.google-sheet.json", SECOND_TAB)[0]
    assert imported["copy_url"] == copy_row()["copy_url"]
    assert imported["program_name"] == "Program"


@pytest.mark.parametrize("status,phrase", [(401, "connection expired"), (403, "access was denied"),
                                           (404, "not found"), (429, "temporarily unavailable"),
                                           (503, "temporarily unavailable")])
def test_google_failures_do_not_invalidate_app_auth_or_leak_payload(app_client, monkeypatch, status, phrase):
    _, client, store = app_client
    mock_google(monkeypatch, Response({"error": {"message": OAUTH_TOKEN + " private@example.com"}}, status=status))
    response = load(client)
    assert response.status_code == (403 if status in (401, 403, 404) else 400)
    assert phrase in response.json()["detail"]
    assert OAUTH_TOKEN not in response.text and "private@example.com" not in response.text
    assert not store.sheet_imports.entries


@pytest.mark.parametrize("error", [{"details": [{"reason": "SERVICE_DISABLED"}]},
                                   {"errors": [{"reason": "accessNotConfigured"}]}])
def test_disabled_google_api_has_actionable_safe_error(app_client, monkeypatch, error):
    _, client, _ = app_client
    error["message"] = OAUTH_TOKEN + " project and student information"
    mock_google(monkeypatch, Response({"error": error}, status=403))
    response = load(client)
    assert response.status_code == 403
    assert "Google Sheets API is disabled" in response.json()["detail"]
    assert "Enable Google Sheets API" in response.json()["detail"]
    assert OAUTH_TOKEN not in response.text


@pytest.mark.parametrize("status,data,location", [(401, b"private login page", None), (403, b"private login page", None),
                                               (200, b"<!doctype html><html>private login page</html>", None),
                                               (302, b"", "https://accounts.google.com/signin")])
def test_anonymous_private_sheet_errors_ask_to_connect(app_client, monkeypatch, status, data, location):
    _, client, _ = app_client
    calls = mock_google(monkeypatch, Response(status=status, data=data, location=location))
    response = load(client, headers=authorization(oauth=False))
    assert response.status_code == 403
    assert "Connect Google Sheets" in response.json()["detail"]
    assert "private login page" not in response.text
    assert "headers" not in calls[0][1]
    assert len(calls) == 1


@pytest.mark.parametrize("location", ["https://sheets.googleapis.com/another", "https://evil.example/secret"])
def test_google_oauth_redirects_never_forward_credentials(app_client, monkeypatch, location):
    _, client, _ = app_client
    calls = mock_google(monkeypatch, Response(status=302, location=location))
    response = load(client)
    assert response.status_code == 400
    assert "unexpected redirect" in response.json()["detail"]
    assert len(calls) == 1
    assert OAUTH_TOKEN not in response.text


@pytest.mark.parametrize("exc,phrase", [(requests.Timeout(OAUTH_TOKEN), "timed out"),
                                        (requests.ConnectionError(OAUTH_TOKEN), "Could not connect")])
def test_network_errors_are_sanitized(app_client, monkeypatch, exc, phrase):
    _, client, _ = app_client
    mock_google(monkeypatch, exc)
    response = load(client)
    assert response.status_code == 400
    assert phrase in response.json()["detail"]
    assert OAUTH_TOKEN not in response.text


@pytest.mark.parametrize("token", ["", "invalid token", "token\r\nInjected: value", "x" * 8193])
def test_invalid_google_token_is_not_sent(token, monkeypatch):
    calls = mock_google(monkeypatch, Response(google_payload()))
    with pytest.raises(workflow.GoogleSheetError, match="Connect Google Sheets"):
        workflow.download_connected_google_sheet(SHEET_URL, token)
    assert not calls


def test_google_token_does_not_bypass_pw_or_local_access_gate(app_client, monkeypatch):
    app, client, _ = app_client
    calls = mock_google(monkeypatch, Response(google_payload()))
    assert load(client, headers={"X-Google-Sheets-Token": OAUTH_TOKEN}).status_code == 401
    assert load(Client(app, "203.0.113.10")).status_code == 403
    assert not calls


def test_public_export_and_uploaded_workbook_ignore_oauth_header(app_client, monkeypatch):
    _, client, _ = app_client
    output = io.BytesIO()
    workbook = openpyxl.Workbook()
    workbook.active.title = "Public tab"
    workbook.save(output)
    workbook.close()
    calls = mock_google(monkeypatch, Response(data=output.getvalue()))
    public = load(client, headers=authorization(oauth=False))
    assert public.status_code == 200
    assert "headers" not in calls[0][1]
    uploaded = client.post("/api/workflow/sheet-tabs", headers=authorization(),
                           files={"sheet": ("local.xlsx", output.getvalue())})
    assert uploaded.status_code == 200
    assert len(calls) == 1


def test_google_metadata_body_and_legacy_canonical_snapshot_are_size_limited(app_client, monkeypatch):
    _, client, _ = app_client
    monkeypatch.setattr(workflow, "MAX_BYTES", 50)
    monkeypatch.setattr(workflow, "MAX_GOOGLE_METADATA_BYTES", 50)
    mock_google(monkeypatch, Response(data=b"x" * 51))
    response = load(client)
    assert response.status_code == 400
    assert "too much data" in response.json()["detail"]
    with pytest.raises(workflow.GoogleSheetError, match="snapshot exceeds"):
        workflow._google_snapshot_bytes(google_payload())


@pytest.mark.parametrize("change", ["row", "column", "cells"])
def test_sparse_sheet_expansion_is_bounded(change, monkeypatch):
    payload = google_payload()
    grid = payload["sheets"][1]["data"][0]
    if change == "row":
        grid["startRow"] = workflow.MAX_GOOGLE_ROWS
    elif change == "column":
        grid["startColumn"] = workflow.MAX_GOOGLE_COLUMNS
    else:
        monkeypatch.setattr(workflow, "MAX_GOOGLE_CELLS", 5)
    with pytest.raises(workflow.GoogleSheetError, match="supported.*limit"):
        workflow._google_snapshot_bytes(payload)


@pytest.mark.parametrize("payload", [{}, {"sheets": []}, {"sheets": [{"properties": {"title": "Bad", "sheetId": True}}]},
                                     {"sheets": [{"properties": {"title": "Bad", "sheetId": 1,
                                                                "gridProperties": {"rowCount": 1, "columnCount": 1}},
                                                   "data": [{}], "other": "private"}]}])
def test_bad_or_empty_api_response_is_safe(payload, app_client, monkeypatch):
    _, client, _ = app_client
    mock_google(monkeypatch, Response(payload))
    response = load(client)
    if payload.get("sheets") and type(payload["sheets"][0].get("properties", {}).get("sheetId")) is int and payload["sheets"][0]["properties"]["sheetId"] == 1:
        assert response.status_code == 200
        preview = client.post("/api/workflow/preview", headers=authorization(),
                              data={"import_id": response.json()["import_id"]})
        assert preview.status_code == 400
        assert "no workflow column headers" in preview.json()["detail"]
    else:
        assert response.status_code == 400
    assert "private" not in response.text
