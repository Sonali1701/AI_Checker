"""Selected upload bytes reach unchanged marks, queue and workflow consumers."""
import asyncio
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI, UploadFile

import integrated_workflow as workflow
import server
from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationStore
from pdf_page_ranges import select_pdf_pages


def document():
    with fitz.open() as doc:
        for text in ("QUESTION SECTION Maximum Marks: 3\nQ1 [1]\nQ2 [2]",
                     "ANSWER SECTION page two", "ANSWER SECTION page three"):
            doc.new_page().insert_text((30, 30), text)
        return doc.tobytes()


def text(data):
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)


def post(path, *, app=None, data=None, files=None):
    async def send():
        transport = httpx.ASGITransport(app=app or server.app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.post(path, headers={"Authorization": "Bearer fixture-user"},
                                     data=data, files=files)
    return asyncio.run(send())


def uploads(original=None, separate_key=True):
    original = original or document()
    result = {"question_paper": ("combined.pdf", original, "application/pdf"),
              "answer_sheets": ("copy.pdf", document(), "application/pdf")}
    if separate_key:
        result["answer_key"] = ("separate.pdf", document(), "application/pdf")
    return result


@pytest.fixture(autouse=True)
def allowed(monkeypatch):
    monkeypatch.setattr(server, "_require_allowed", lambda authorization: "fixture-user")
    monkeypatch.setattr(server, "_persist_job", lambda *args: None)
    monkeypatch.setattr(server, "JOBS", {})


def test_evaluate_combined_ranges_feed_the_existing_queue(monkeypatch):
    captured = {}
    async def queue(background_tasks, **kwargs):
        captured.update(kwargs)
        captured["key_bytes"] = await kwargs["answer_key"].read()
        return {"job_id": "fixture"}
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    original = document()
    response = post("/api/evaluate", data={"question_page_range": "1", "answer_page_range": "2-3",
                     "use_question_paper_as_key": "true", "exam_type": "school"},
                    files=uploads(original, separate_key=False))
    assert response.status_code == 200, response.text
    assert captured["qp_bytes"] == select_pdf_pages(original, "combined.pdf", "1")
    assert captured["key_bytes"] == select_pdf_pages(original, "combined.pdf", "2-3")
    assert captured["key_source"] == "upload"
    assert captured["qp_name"] == captured["answer_key"].filename == "combined.pdf"
    assert "ANSWER SECTION" not in text(captured["qp_bytes"])
    assert "QUESTION SECTION" not in text(captured["key_bytes"])


def test_separate_uploads_and_blank_ranges_keep_their_exact_bytes(monkeypatch):
    captured = {}
    async def queue(background_tasks, **kwargs):
        captured["paper"] = kwargs["qp_bytes"]
        captured["key"] = await kwargs["answer_key"].read()
        captured["key_source"] = kwargs["key_source"]
        return {"job_id": "fixture"}
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    original, key = document(), document()
    files = uploads(original)
    files["answer_key"] = ("key.pdf", key, "application/pdf")
    response = post("/api/evaluate", files=files,
                    data={"exam_type": "school", "key_source": "upload"})
    assert response.status_code == 200, response.text
    assert captured == {"paper": original, "key": key, "key_source": "upload"}


def test_separate_key_range_uses_that_key_not_the_question_paper(monkeypatch):
    captured = {}
    async def queue(background_tasks, **kwargs):
        captured["key"] = await kwargs["answer_key"].read()
        return {"job_id": "fixture"}
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    key = document()
    files = uploads()
    files["answer_key"] = ("key.pdf", key, "application/pdf")
    response = post("/api/evaluate", files=files,
                    data={"exam_type": "school", "answer_page_range": "2-3", "key_source": "upload"})
    assert response.status_code == 200, response.text
    assert captured["key"] == select_pdf_pages(key, "key.pdf", "2-3")


def test_folder_mode_passes_selected_inputs_to_the_same_existing_queue(monkeypatch):
    original = document()
    captured = {}
    monkeypatch.setattr(server, "_bulk_plan", lambda *args: (
        {"target": "fixture-output", "source": "fixture-input", "todo": ["copy.pdf"],
         "already_done": 0, "this_part": 1}, 1, 1))
    monkeypatch.setattr(server.bulk, "FolderSink", lambda *args: object())
    async def queue(background_tasks, **kwargs):
        captured["paper"] = kwargs["qp_bytes"]
        captured["key"] = await kwargs["answer_key"].read()
        return {"job_id": "fixture"}
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    response = post("/api/bulk/start", files=uploads(original, separate_key=False),
                    data={"question_page_range": "1", "answer_page_range": "2-3",
                          "use_question_paper_as_key": "true", "exam_type": "school"})
    assert response.status_code == 200, response.text
    assert captured == {"paper": select_pdf_pages(original, "combined.pdf", "1"),
                        "key": select_pdf_pages(original, "combined.pdf", "2-3")}


def test_design_preview_and_evaluation_receive_independent_combined_selections(monkeypatch):
    original = document()
    captured = {}
    q = SimpleNamespace(qid="Q1", kind="drawing", brief="Fixture", max_marks=3, criteria=[])
    def read(paper, paper_name, key, key_name, **kwargs):
        captured.update(paper=paper, key=key)
        return SimpleNamespace(marked=[q], title="Fixture", notes=""), None
    monkeypatch.setattr(server, "_read_design_paper_cached", read)
    form = {"question_page_range": "1", "answer_page_range": "2-3",
            "use_question_paper_as_key": "true", "total_marks": "3", "question_marks": "3"}
    response = post("/api/design/read-paper", data=form, files=uploads(original, separate_key=False))
    assert response.status_code == 200, response.text
    expected = {"paper": select_pdf_pages(original, "combined.pdf", "1"),
                "key": select_pdf_pages(original, "combined.pdf", "2-3")}
    assert captured == expected
    workers = []
    monkeypatch.setattr(server, "_run_design_job", lambda payload: workers.append(payload))
    response = post("/api/evaluate-design", data=form, files=uploads(original, separate_key=False))
    assert response.status_code == 200, response.text
    assert workers[0]["qp_bytes"] == expected["paper"]
    assert workers[0]["ak_bytes"] == expected["key"]


def test_rubric_generation_renders_only_selected_question_pages(monkeypatch):
    original = document()
    captured = []
    monkeypatch.setattr(server, "pdf_or_image_to_pngs", lambda data, name: captured.append(data) or [b"png"])
    monkeypatch.setattr(server, "generate_rubric", lambda *args: "Fixture rubric")
    response = post("/api/generate-rubric", data={"question_page_range": "1", "exam_type": "school"},
                    files={"question_paper": ("combined.pdf", original, "application/pdf")})
    assert response.status_code == 200, response.text
    assert captured == [select_pdf_pages(original, "combined.pdf", "1")]


@pytest.mark.parametrize("path", ["/api/detect-marks", "/api/generate-rubric", "/api/evaluate",
                                  "/api/bulk/start", "/api/design/read-paper", "/api/evaluate-design"])
def test_bad_question_range_is_rejected_before_provider_folder_or_job_work(monkeypatch, path):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid range must fail before a consumer is invoked")
    for name in ("cfg_from_request", "detect_marks_scheme", "pdf_or_image_to_pngs",
                 "_queue_evaluation", "_bulk_plan", "_read_design_paper_cached", "_run_design_job"):
        monkeypatch.setattr(server, name, unexpected)
    response = post(path, data={"question_page_range": "4", "exam_type": "school"}, files=uploads())
    assert response.status_code == 400, response.text
    assert "3 pages" in response.json()["detail"]
    assert not server.JOBS


@pytest.mark.parametrize("path", ["/api/evaluate", "/api/bulk/start", "/api/design/read-paper", "/api/evaluate-design"])
def test_bad_answer_range_is_rejected_before_consumers(monkeypatch, path):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid range must fail before a consumer is invoked")
    for name in ("cfg_from_request", "_queue_evaluation", "_bulk_plan", "_read_design_paper_cached", "_run_design_job"):
        monkeypatch.setattr(server, name, unexpected)
    response = post(path, data={"answer_page_range": "2,2", "exam_type": "school"}, files=uploads())
    assert response.status_code == 400, response.text
    assert "repeats page 2" in response.json()["detail"]
    assert not server.JOBS


def test_selection_does_not_bypass_existing_source_file_size_limit(monkeypatch):
    original = document()
    monkeypatch.setattr(server, "_MAX_DOCUMENT_BYTES", len(original) - 1)
    response = post("/api/detect-marks", data={"question_page_range": "1", "exam_type": "school"},
                    files={"question_paper": ("combined.pdf", original, "application/pdf")})
    assert response.status_code == 413


def test_marks_proof_binds_selected_bytes_and_survives_evaluation_reselection(monkeypatch):
    store = MarksAttestationStore()
    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", store)
    scheme = MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=2)])
    provider_inputs = []
    def detect(data, *args, **kwargs):
        provider_inputs.append(data)
        return scheme, "ai"
    monkeypatch.setattr(server, "detect_marks_scheme", detect)
    monkeypatch.setattr(server, "paper_has_choice", lambda *args: False)
    workers = []
    monkeypatch.setattr(server, "_run_job", lambda payload: workers.append(payload))
    original = document()
    form = {"provider": "claude", "student_class": "Class 11", "subject": "Biology",
            "exam_type": "school", "total_marks": "3", "question_page_range": "1", "mode": "ai"}
    detected = post("/api/detect-marks", data=form,
                    files={"question_paper": ("combined.pdf", original, "application/pdf")})
    assert detected.status_code == 200, detected.text
    proof = detected.json()
    assert proof["method"] == "ai_verified", proof
    selected = select_pdf_pages(original, "combined.pdf", "1")
    assert provider_inputs == [selected]
    response = post("/api/evaluate", files=uploads(original, separate_key=False),
                    data={**form, "answer_page_range": "2-3", "use_question_paper_as_key": "true",
                          "marks_items": json.dumps(proof["items"]), "marks_source": proof["method"],
                          "marks_attestation": proof["marks_attestation"]})
    assert response.status_code == 200, response.text
    assert len(workers) == 1
    assert workers[0]["qp_bytes"] == selected
    assert workers[0]["ak_bytes"] == select_pdf_pages(original, "combined.pdf", "2-3")
    assert workers[0]["question_graph_proof"]
    # Changing the page selection cannot reuse the previous paper's proof.
    response = post("/api/evaluate", files=uploads(original, separate_key=False),
                    data={**form, "question_page_range": "1-2", "answer_page_range": "2-3",
                          "use_question_paper_as_key": "true", "marks_items": json.dumps(proof["items"]),
                          "marks_source": proof["method"], "marks_attestation": proof["marks_attestation"]})
    assert response.status_code == 400, response.text
    assert "paper" in response.json()["detail"].lower()
    assert len(workers) == 1


def test_direct_route_calls_keep_real_empty_range_defaults(monkeypatch):
    captured = {}
    async def queue(background_tasks, **kwargs):
        captured["paper"] = kwargs["qp_bytes"]
        captured["key"] = await kwargs["answer_key"].read()
        return {"job_id": "fixture"}
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    original, key = document(), document()
    asyncio.run(server.evaluate(BackgroundTasks(),
                answer_sheets=[UploadFile(filename="copy.pdf", file=io.BytesIO(document()))],
                question_paper=UploadFile(filename="paper.pdf", file=io.BytesIO(original)),
                answer_key=UploadFile(filename="key.pdf", file=io.BytesIO(key)),
                category="", exam_type="school", authorization="Bearer fixture"))
    assert captured == {"paper": original, "key": key}


def workflow_fixture(tmp_path, monkeypatch):
    app = FastAPI()
    store = workflow.install(app, lambda authorization: "header.eyJzdWIiOiJmaXh0dXJlIn0.signature",
                             lambda *args: (_ for _ in ()).throw(AssertionError("No live grading")), tmp_path)
    starts = []
    monkeypatch.setattr(store, "start", lambda *args: starts.append(args))
    row = dict(submission_id="fixture-copy", student_id="fixture-student", test_id="fixture-test",
               test_name="Fixture Biology", program_name="Fixture program", test_series_name="Fixture batch",
               copy_url="https://example.cloudfront.net/copy.pdf", portal_status="Submitted")
    options = dict(evaluator_email="fixture@example.com",
                   deadline=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                   category="school", student_class="Class 11", subject="Biology", total_marks="3")
    return app, store, starts, {"rows": json.dumps([row]), "options": json.dumps(options)}


def test_workflow_stores_only_selected_exam_documents_for_resuming(tmp_path, monkeypatch):
    app, store, starts, form = workflow_fixture(tmp_path, monkeypatch)
    original = document()
    response = post("/api/workflow/start", app=app,
                    data={**form, "question_page_range": "1", "answer_page_range": "2-3",
                          "use_question_paper_as_key": "true"},
                    files={"question_paper": ("combined.pdf", original, "application/pdf")})
    assert response.status_code == 200, response.text
    folder = store.folder(response.json()["id"])
    assert (folder / "question_paper.pdf").read_bytes() == select_pdf_pages(original, "combined.pdf", "1")
    assert (folder / "answer_key.pdf").read_bytes() == select_pdf_pages(original, "combined.pdf", "2-3")
    assert len(starts) == 1


def test_workflow_invalid_range_creates_no_run_or_portal_start(tmp_path, monkeypatch):
    app, store, starts, form = workflow_fixture(tmp_path, monkeypatch)
    response = post("/api/workflow/start", app=app,
                    data={**form, "question_page_range": "1-9", "use_question_paper_as_key": "true"},
                    files={"question_paper": ("combined.pdf", document(), "application/pdf")})
    assert response.status_code == 400, response.text
    assert not list(tmp_path.glob("*/run.json"))
    assert not starts


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
