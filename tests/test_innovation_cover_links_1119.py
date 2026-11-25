# -*- coding: utf-8 -*-
"""A PW Skills cover PDF whose work sits behind a link (19 Nov 2026).

Three copies came back 0 "Not attempted" on every question. Each learner's PDF was one
short page, "<Assignment> - Assignment Solution / Click to open the Excel file", and the
link was either to a site off the share-site list or missing (a document printed to PDF
loses its links), so nothing was opened and the cover itself was marked. Now a direct
link to a file is opened on any public site, and a cover whose work cannot be reached
goes to a person with the reason instead of being marked.

Offline: the network and the address lookup are replaced.
"""
import io
import os
import socket
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fitz  # noqa: E402

import linked_work as lw  # noqa: E402


def cover(link=None, lines=("Probability Basics - Assignment Solution", "Click to open the Excel file")):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 72
    for text in lines:
        page.insert_text((72, y), text, fontsize=11)
        y += 16
    if link:
        page.insert_link({"kind": fitz.LINK_URI, "uri": link, "from": fitz.Rect(72, y - 27, 300, y - 13)})
    return doc.tobytes()


def workbook():
    from openpyxl import Workbook
    book = Workbook()
    book.active.title = "Q1 Probability"
    book.active.append(["Event", "P"])
    book.active.append(["Heads", "=1/2"])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class Response:
    def __init__(self, status, body=b"", location="", ctype="application/octet-stream"):
        self.status_code, self._body = status, body
        self.headers = {"Location": location, "Content-Type": ctype}

    def iter_content(self, size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def addresses(mapping):
    """socket.getaddrinfo answering from a table: host -> IP."""
    def lookup(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]
    return lookup


def test_which_links_count_as_files():
    assert lw.is_file_link("https://files.example-cdn.com/learners/Probability_Basics.xlsx")
    assert lw.is_file_link("https://bucket.s3.amazonaws.com/a/b/answers.ipynb?X-Amz-Signature=1")
    assert lw.is_file_link("https://cdn.pwskills.com/assets/uploads/abc123")      # PW's own files
    assert not lw.is_file_link("http://files.example-cdn.com/a.xlsx")             # not https
    assert not lw.is_file_link("https://example.org/my-profile")                  # a web page
    assert not lw.is_file_link("https://docs.google.com/spreadsheets/d/x/edit")   # opened its own way
    assert lw._followed("https://files.example-cdn.com/learners/Probability_Basics.xlsx")


def test_a_cover_linking_straight_to_a_file_on_another_site_is_opened(monkeypatch):
    import requests
    monkeypatch.setattr(socket, "getaddrinfo", addresses({"files.example-cdn.com": "93.184.216.34"}))
    monkeypatch.setattr(requests, "get", lambda url, **kw: Response(200, workbook()))
    prepared = lw.prepare_submission(cover("https://files.example-cdn.com/learners/Probability_Basics.xlsx"), "c.pdf")
    assert prepared.converted and [f.name for f in prepared.files] == ["Probability_Basics.xlsx"]
    assert lw.unopened_reason(prepared) == ""
    with fitz.open(stream=prepared.data, filetype="pdf") as doc:
        text = " ".join(page.get_text() for page in doc)
    assert "Q1 Probability" in text and "1/2" in text


def test_a_file_link_to_an_internal_address_is_never_fetched(monkeypatch):
    import requests

    def no_request(url, **kw):
        raise AssertionError("an internal address was contacted")
    monkeypatch.setattr(socket, "getaddrinfo", addresses({"files.corp.example": "10.1.2.3"}))
    monkeypatch.setattr(requests, "get", no_request)
    with pytest.raises(lw.LinkError, match="not on the public internet"):
        lw.open_link("https://files.corp.example/answers.xlsx")


def test_a_redirect_from_a_file_link_to_an_internal_address_stops(monkeypatch):
    import requests
    hops = []

    def fake(url, **kw):
        hops.append(url)
        if "public" in url:
            return Response(302, location="https://intranet.example/answers.xlsx")
        raise AssertionError("the internal hop was contacted")
    monkeypatch.setattr(socket, "getaddrinfo", addresses({"public.example": "93.184.216.34",
                                                          "intranet.example": "192.168.1.10"}))
    monkeypatch.setattr(requests, "get", fake)
    with pytest.raises(lw.LinkError, match="not on the public internet"):
        lw.open_link("https://public.example/answers.xlsx")
    assert hops == ["https://public.example/answers.xlsx"]


def test_a_cover_that_says_click_to_open_but_holds_no_link_goes_to_a_person():
    prepared = lw.prepare_submission(cover(link=None), "c.pdf")
    assert prepared.link_only and not prepared.files and not prepared.converted
    reason = lw.unopened_reason(prepared)
    assert '"Click to open the Excel file"' in reason and "printed to PDF" in reason
    assert reason.endswith("No marks were given.")


def test_a_cover_linking_to_a_web_page_elsewhere_goes_to_a_person():
    prepared = lw.prepare_submission(cover("https://example.org/my-profile"), "c.pdf")
    reason = lw.unopened_reason(prepared)
    assert "example.org is not a site the checker opens" in reason and "No marks were given." in reason


def test_a_typed_answer_with_a_reference_link_is_marked_as_usual():
    lines = ["Q1. CSS specificity decides which rule wins when two rules target one element."] * 12
    typed = cover("https://www.w3schools.com/css/css_specificity.asp", lines=lines)
    prepared = lw.prepare_submission(typed, "a.pdf")
    assert prepared.data == typed and not prepared.problems and lw.unopened_reason(prepared) == ""


def test_a_short_answer_without_link_words_is_marked_as_usual():
    prepared = lw.prepare_submission(cover(lines=("Q1. P(heads) = 0.5", "Q2. P(six) = 1/6")), "a.pdf")
    assert not prepared.problems and lw.unopened_reason(prepared) == ""


def test_grading_holds_a_cover_with_no_link_before_anything_is_paid(monkeypatch):
    import dataclasses
    import pipeline
    from strict_grading import StrictGradeError

    def no_ai(*args, **kwargs):
        raise AssertionError("an AI call was made for a cover with no reachable work")
    for name in ("_gpt_call", "call_claude"):
        if hasattr(pipeline, name):
            monkeypatch.setattr(pipeline, name, no_ai)
    cfg = dataclasses.replace(pipeline.cfg_from_request("claude", "", "Excel", google_token="", task_id="",
                                                        category="innovation"), paper_check=True)
    with pytest.raises(StrictGradeError) as err:
        pipeline.grade_sheet(qp_pngs=[], sa_bytes=cover(link=None), filename="cover.pdf", ak_pngs=None,
                             ak_text=None, marks_scheme=None, cfg=cfg)
    assert err.value.code == "NOT_VERIFIED_ANSWER_SHEET" and "Click to open the Excel file" in str(err.value)
