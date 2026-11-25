"""Snapshot-based worksheet imports; every Google response is mocked."""
import asyncio
import base64
import csv
import io
import json

from fastapi import FastAPI, HTTPException
import httpx
import openpyxl
import pytest
import requests

import integrated_workflow as workflow


def row(copy="copy_first"):
    return dict(submission_id=copy, student_id="student123", student_name="Example",
                program_name="Program", test_series_name="Batch Tests", test_id="test123",
                test_name="Maths Test", copy_url="https://example.cloudfront.net/copy.pdf",
                portal_status="Submitted")


def workbook(empty_second=False):
    wb = openpyxl.Workbook()
    first = wb.active
    first.title = "First day"
    first.append(list(row()))
    first.append(list(row().values()))
    second = wb.create_sheet("Second day")
    if not empty_second:
        second.append(list(row()))
        second.append(list(row("copy_second").values()))
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


def csv_data():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(row()))
    writer.writeheader()
    writer.writerow(row())
    return output.getvalue().encode()


def authorization(owner="alice"):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": owner}).encode()).decode().rstrip("=")
    return {"Authorization": f"Bearer header.{payload}.signature"}


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


def load_workbook(client, data=None):
    return client.post("/api/workflow/sheet-tabs", headers=authorization(),
                       files={"sheet": ("copies.xlsx", data if data is not None else workbook())})


def test_explicit_second_worksheet_and_legacy_active_sheet():
    data = workbook()
    assert workflow.sheet_tab_names(data, "copies.xlsx") == ["First day", "Second day"]
    assert workflow.parse_sheet(data, "copies.xlsx")[0]["submission_id"] == "copy_first"
    assert workflow.parse_sheet(data, "copies.xlsx", "Second day")[0]["submission_id"] == "copy_second"


@pytest.mark.parametrize("name", ["Missing day", "second day"])
def test_invalid_worksheet_is_never_substituted(name):
    with pytest.raises(ValueError, match="not in this workbook"):
        workflow.parse_sheet(workbook(), "copies.xlsx", name)


def test_empty_worksheet_has_clear_error():
    with pytest.raises(ValueError, match="Second day.*empty"):
        workflow.parse_sheet(workbook(empty_second=True), "copies.xlsx", "Second day")


def test_endpoint_loads_tabs_and_previews_cached_second_sheet(app_client):
    _, client, _ = app_client
    response = load_workbook(client)
    assert response.status_code == 200
    source = response.json()
    assert source["tabs"] == [{"name": "First day"}, {"name": "Second day"}]
    assert source["source_name"] == "copies.xlsx"
    assert len(source["import_id"]) == 32
    preview = client.post("/api/workflow/preview", headers=authorization(),
                          data={"import_id": source["import_id"], "worksheet": "Second day"})
    assert preview.status_code == 200
    assert preview.json()["rows"][0]["submission_id"] == "copy_second"


def test_multi_tab_snapshot_requires_explicit_valid_worksheet(app_client):
    _, client, _ = app_client
    source = load_workbook(client).json()
    for worksheet in ("", "Missing day"):
        response = client.post("/api/workflow/preview", headers=authorization(),
                               data={"import_id": source["import_id"], "worksheet": worksheet})
        assert response.status_code == 400
    changed_source = client.post("/api/workflow/preview", headers=authorization(),
                                 data={"import_id": source["import_id"], "worksheet": "Second day",
                                       "sheet_url": "https://docs.google.com/spreadsheets/d/other/edit"})
    assert changed_source.status_code == 400


def test_snapshot_is_owner_bound_and_expired_entries_are_rejected(app_client):
    _, client, store = app_client
    current = [10.0]
    store.sheet_imports.clock = lambda: current[0]
    source = load_workbook(client).json()
    form = {"import_id": source["import_id"], "worksheet": "Second day"}
    hidden = client.post("/api/workflow/preview", headers=authorization("bob"), data=form)
    assert hidden.status_code == 404
    current[0] += workflow.IMPORT_TTL_SECONDS
    expired = client.post("/api/workflow/preview", headers=authorization(), data=form)
    assert expired.status_code == 404
    assert not store.sheet_imports.entries


def test_tab_discovery_requires_authentication_and_local_client(app_client):
    app, client, _ = app_client
    assert client.post("/api/workflow/sheet-tabs", files={"sheet": ("data.csv", csv_data())}).status_code == 401
    remote = Client(app, "203.0.113.10")
    assert remote.post("/api/workflow/sheet-tabs", headers=authorization(),
                       files={"sheet": ("data.csv", csv_data())}).status_code == 403


def test_csv_cached_and_direct_preview_remain_compatible(app_client):
    _, client, _ = app_client
    loaded = client.post("/api/workflow/sheet-tabs", headers=authorization(),
                         files={"sheet": ("copies.csv", csv_data())})
    source = loaded.json()
    assert source["tabs"] == [{"name": "CSV data"}]
    cached = client.post("/api/workflow/preview", headers=authorization(), data={"import_id": source["import_id"]})
    direct = client.post("/api/workflow/preview", headers=authorization(), files={"sheet": ("copies.csv", csv_data())})
    assert cached.status_code == direct.status_code == 200
    assert cached.json() == direct.json()
    with pytest.raises(ValueError, match="only"):
        workflow.parse_sheet(csv_data(), "copies.csv", "Other tab")


def test_cache_count_byte_limits_and_per_snapshot_size():
    data = csv_data()
    current = [0.0]
    cache = workflow.SheetImportCache(clock=lambda: current[0], max_count=2, max_bytes=len(data) * 2)
    first = cache.put("alice", data, "first.csv")
    cache.put("alice", data, "second.csv")
    cache.put("alice", data, "third.csv")
    assert len(cache.entries) == 2
    assert sum(len(entry["data"]) for entry in cache.entries.values()) <= len(data) * 2
    with pytest.raises(LookupError):
        cache.preview("alice", first["import_id"])
    small = workflow.SheetImportCache(max_bytes=len(data) - 1)
    with pytest.raises(ValueError, match="cache size"):
        small.put("alice", data, "large.csv")


class Response:
    def __init__(self, data=b"", status=200, location=None):
        self.data, self.status_code = data, status
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None and 300 <= status < 400

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def google_responses(monkeypatch, responses):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        return responses.pop(0)
    monkeypatch.setattr(workflow.requests, "get", get)
    return calls


def test_google_tabs_follow_safe_redirect_and_use_one_cached_snapshot(app_client, monkeypatch):
    _, client, _ = app_client
    calls = google_responses(monkeypatch, [Response(status=302, location="https://export.googleusercontent.com/workbook"),
                                          Response(workbook())])
    source = client.post("/api/workflow/sheet-tabs", headers=authorization(),
                         data={"sheet_url": "https://docs.google.com/spreadsheets/d/example/edit?gid=123"})
    assert source.status_code == 200
    assert calls[0] == "https://docs.google.com/spreadsheets/d/example/export?format=xlsx"
    assert source.json()["source_name"] == "Google Sheet export"
    preview = client.post("/api/workflow/preview", headers=authorization(),
                          data={"import_id": source.json()["import_id"], "worksheet": "Second day"})
    assert preview.status_code == 200
    assert preview.json()["rows"][0]["submission_id"] == "copy_second"
    assert len(calls) == 2


def test_legacy_google_csv_preview_keeps_gid(app_client, monkeypatch):
    _, client, _ = app_client
    calls = google_responses(monkeypatch, [Response(csv_data())])
    preview = client.post("/api/workflow/preview", headers=authorization(),
                          data={"sheet_url": "https://docs.google.com/spreadsheets/d/example/edit#gid=1473017682"})
    assert preview.status_code == 200
    assert calls == ["https://docs.google.com/spreadsheets/d/example/export?format=csv&gid=1473017682"]


@pytest.mark.parametrize("location", ["https://accounts.google.com/signin", "https://evil.example/x",
                                       "http://export.googleusercontent.com/x", "https://user:pass@docs.google.com/x",
                                       "https://docs.google.com:8080/x"])
def test_unsafe_google_redirect_is_not_followed(monkeypatch, location):
    calls = google_responses(monkeypatch, [Response(status=302, location=location)])
    with pytest.raises(ValueError, match="sign-in"):
        workflow.download_google_sheet("https://docs.google.com/spreadsheets/d/example/edit", "xlsx")
    assert len(calls) == 1


@pytest.mark.parametrize("response", [Response(b"<!doctype html><html>Sign in</html>"),
                                      Response(status=401), Response(status=403)])
def test_google_login_response_explains_file_fallback(monkeypatch, response):
    google_responses(monkeypatch, [response])
    with pytest.raises(ValueError, match="sign-in.*XLSX.*CSV"):
        workflow.download_google_sheet("https://docs.google.com/spreadsheets/d/example/edit", "xlsx")


def test_uploaded_login_html_and_empty_sheet_are_rejected(app_client):
    _, client, _ = app_client
    for data in (b"<html>Login</html>", b""):
        response = load_workbook(client, data)
        assert response.status_code == 400


def test_google_size_and_redirect_limits_are_preserved(monkeypatch):
    monkeypatch.setattr(workflow, "MAX_BYTES", 10)
    google_responses(monkeypatch, [Response(b"a" * 11)])
    with pytest.raises(ValueError, match="exceeds"):
        workflow.download_google_sheet("https://docs.google.com/spreadsheets/d/example/edit", "xlsx")
    calls = google_responses(monkeypatch, [Response(status=302, location="https://docs.google.com/loop") for _ in range(5)])
    with pytest.raises(ValueError, match="Too many"):
        workflow.download_google_sheet("https://docs.google.com/spreadsheets/d/example/edit", "xlsx")
    assert len(calls) == 5
