"""Generic cover text must not invent choices or hide real/unreadable choices."""
import asyncio
import io
from types import SimpleNamespace

import fitz
import pytest
import requests
from starlette.datastructures import UploadFile

import server
from grader import MarksScheme, marks_scheme_from_items, pdf_text_layer
from marks_attestation import MarksAttestationStore


HEADER = (
    "Maximum Marks: 2\nAll questions are compulsory.\n"
    "There is an internal choice in some questions.\n"
)
QUESTIONS = (
    "Q1. Define an intermediate good with an appropriate economic example. (1 Mark)\n"
    "Q2. Explain the meaning of a final good with an appropriate example. (1 Mark)\n"
)


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("This regression suite must make no HTTP requests")
    monkeypatch.setattr(requests.Session, "request", fail)


def pdf(pages):
    with fitz.open() as doc:
        for text in pages:
            page = doc.new_page()
            page.insert_textbox(fitz.Rect(40, 40, 550, 790), text, fontsize=11)
        return doc.tobytes()


def graph():
    return marks_scheme_from_items([{"qid": "Q1", "max": 1}, {"qid": "Q2", "max": 1}])


def disproved(pages, scheme=None, total=2, filename="questions.pdf"):
    data = pdf(pages)
    return server._generic_choice_hint_disproved(
        data, filename, pdf_text_layer(data, filename), scheme or graph(), total)


def test_generic_template_with_complete_compulsory_questions():
    assert disproved([HEADER + QUESTIONS])
    assert server.paper_has_choice(HEADER + QUESTIONS)


@pytest.mark.parametrize("cue", [
    "OR", "Q1. Define income. OR Q1. Define expenditure.",
    "Answer any ONE of the following two questions.",
    "Attempt any two of the three remaining questions.",
    "Choose one of these alternatives.",
])
def test_real_selection_still_requires_graph(cue):
    assert not disproved([HEADER + QUESTIONS + "\n" + cue])


def test_incomplete_or_scanned_question_page_does_not_clear_hint():
    assert not disproved([HEADER + QUESTIONS, ""])
    assert not disproved([HEADER, "Q1. (image)"])
    assert not disproved([HEADER + QUESTIONS], filename="questions.png")


def test_other_instructions_or_unreconciled_graph_do_not_clear_hint():
    assert not disproved([HEADER.replace("All questions are compulsory.", "") + QUESTIONS])
    assert not disproved([HEADER + QUESTIONS], total=3)
    assert not disproved([HEADER + QUESTIONS], total=None)
    assert not disproved([HEADER.replace("internal choice", "choice") + QUESTIONS])


def test_extracted_choice_graph_is_never_overridden():
    scheme = MarksScheme.model_validate({
        "total": 3,
        "items": [{"qid": "Q1.a", "max": 1}, {"qid": "Q1.b", "max": 1},
                  {"qid": "Q2", "max": 1}],
        "choice_groups": [{"group_id": "q1", "select_count": 1,
                           "contribution_marks": 1,
                           "options": [{"option_id": "a", "question_ids": ["Q1.a"]},
                                       {"option_id": "b", "question_ids": ["Q1.b"]}]}],
    })
    assert not disproved([HEADER + QUESTIONS], scheme)


@pytest.mark.parametrize("cue,expected", [
    ("", "ai_verified"), ("\nOR\nQ1. Define consumption instead.", "ai_choice_unverified"),
])
def test_detect_endpoint_retains_attestation_checks(monkeypatch, cue, expected):
    data = pdf([HEADER + QUESTIONS + cue])
    monkeypatch.setattr(server, "_require_allowed", lambda auth: "local-fixture")
    monkeypatch.setattr(server, "cfg_from_request", lambda *args, **kw: SimpleNamespace(provider="claude", model="fixture"))
    monkeypatch.setattr(server, "_marks_reader_config", lambda cfg: cfg)
    monkeypatch.setattr(server, "_marks_extraction_context", lambda *args: "fixture")
    monkeypatch.setattr(server, "detect_marks_scheme", lambda *args, **kw: (graph(), "ai"))
    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", MarksAttestationStore())
    response = asyncio.run(server.detect_marks(
        question_paper=UploadFile(filename="questions.pdf", file=io.BytesIO(data)),
        question_page_range="", provider="claude", model="", student_class="12",
        subject="Economics", exam_type="school", category="school", total_marks="2",
        mode="ai", authorization="fixture",
    ))
    assert response["method"] == expected
    assert bool(response["marks_attestation"]) == (expected == "ai_verified")
    assert response["effective_total"] == 2
