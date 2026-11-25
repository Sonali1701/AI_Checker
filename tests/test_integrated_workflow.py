import threading
import time
import base64
import asyncio
import io
import json
from datetime import datetime, timedelta

import fitz
import pytest
from fastapi import FastAPI, HTTPException
import httpx

import integrated_workflow as workflow


class LocalClient:
    """ASGI transport avoids the installed Starlette/httpx TestClient mismatch."""
    def __init__(self, app, host="127.0.0.1"):
        self.app, self.host = app, host

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app, client=(self.host, 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


def row(**updates):
    return dict(submission_id="copy123", student_id="student123", student_name="Example",
                program_name="Complete Programe", test_series_name="Batch Tests", test_id="test123",
                test_name="Maths Test", copy_url="https://example.cloudfront.net/copy.pdf",
                portal_status="Submitted", **updates)


def options():
    return dict(evaluator_email="teacher@example.com", deadline=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                category="school", student_class="Class 10", subject="Maths", total_marks="10")


def report():
    return {"questions": [{"qid": "Q1.a", "score": 2}, {"qid": "Q1.b", "score": 3}, {"qid": "Q2", "score": 4}],
            "total_score": 9, "overall_remarks": "Check your arithmetic."}


def pdf():
    with fitz.open() as doc:
        doc.new_page().insert_text((30, 30), "Example exam document")
        return doc.tobytes()


def test_normalizes_export_and_deduplicates():
    exported = {aliases[-1]: row().get(key, "") for key, aliases in workflow.ALIASES.items()}
    assert workflow.normalize_rows([exported, exported])[0]["submission_id"] == "copy123"
    assert len(workflow.normalize_rows([exported, exported])) == 1


def test_conflicting_duplicates_are_rejected():
    changed = row(); changed["copy_url"] = "https://example.cloudfront.net/other.pdf"
    with pytest.raises(ValueError, match="conflicting duplicate"):
        workflow.normalize_rows([row(), changed])


@pytest.mark.parametrize("url", ["http://example.cloudfront.net/a.pdf", "https://127.0.0.1/a.pdf", "https://cloudfront.net.evil.com/a.pdf", "https://user:pass@example.cloudfront.net/a.pdf", "https://example.cloudfront.net:8080/a.pdf"])
def test_copy_download_rejects_untrusted_hosts(url):
    with pytest.raises(ValueError):
        workflow.validate_copy_url(url)


def test_duplicate_headers_and_html_are_rejected():
    with pytest.raises(ValueError, match="Repeated"):
        workflow.parse_sheet(b"_id,_id\na,b\n", "file.csv")
    with pytest.raises(ValueError, match="sign-in"):
        workflow.parse_sheet(b"<!doctype html><html>login</html>", "file.csv")


def test_question_aggregation_and_total_gate():
    assert workflow.portal_marks(report()) == {1: 5, 2: 4}
    wrong = report(); wrong["total_score"] = 8
    with pytest.raises(ValueError, match="do not match"):
        workflow.portal_marks(wrong)
    wrong = report(); wrong["questions"][0]["qid"] = "Part A"
    with pytest.raises(ValueError, match="Cannot map"):
        workflow.portal_marks(wrong)


class FakePortal:
    events = []
    rejections = []
    fail_upload = False
    reject_mode = "ok"
    documents = None          # what exam_documents returns; an Exception instance is raised instead
    document_requests = []
    fail_before = None        # "assign" / "upload": fail before the Submit click (retryable)
    assign_outcome = None     # what perform("assign") reports: {"result": assigned|already|reassigned|finished, ...}
    verify_outcome = "unknown"   # what perform("verify_*") reports after a lost confirmation: done | not_done | unknown
    fail_after_assign_marker = False
    closed = 0
    windows = []              # (evaluator, port) of every portal instance created: the admin one has ("", None)

    def __init__(self, root, evaluator="", evaluator_port=None):
        self.evaluator = evaluator
        FakePortal.windows.append((evaluator, evaluator_port))

    def close(self):
        FakePortal.closed += 1

    def exam_documents(self, test, progress=lambda text: None):
        self.events.append("documents")
        self.document_requests.append(dict(test))
        progress("Downloading from the fixture portal")
        if isinstance(self.documents, Exception):
            raise self.documents
        return self.documents if self.documents is not None else {"question_paper": [("paper.pdf", pdf())], "answer_key": [("key.pdf", pdf())]}

    def perform(self, stage, row, options, pdf=None, report=None, reason="", before_submit=None):
        self.events.append(stage)
        if stage.startswith("verify_"):
            return {"result": self.verify_outcome, "detail": "fixture portal"}
        if stage == self.fail_before:
            raise RuntimeError(f"[{stage}] Portal row does not expose student ID; nothing submitted")
        if stage in ("assign", "upload") and before_submit:
            before_submit()  # the engine clicks Submit right after this
        if stage == "assign" and self.fail_after_assign_marker:
            raise RuntimeError("Confirmation lost")
        if stage == "assign":
            return self.assign_outcome
        if stage == "upload" and self.fail_upload:
            raise RuntimeError("Confirmation lost")
        if stage == "reject":
            if self.reject_mode == "fail_before":
                raise RuntimeError("[find student] Not Pending: matching test is in Evaluated")
            self.rejections.append((row["submission_id"], reason, pdf))
            before_submit()
            if self.reject_mode == "unverified":
                from portal_vendor.rejection_comments import SubmissionUnverified
                raise SubmissionUnverified("Submit was attempted, but success was not confirmed.")



def manual_rejections():
    """A run started with automatic rejection posting turned off: rejections wait for the button."""
    chosen = options(); chosen["submit_rejections"] = False
    return chosen

def rejected_grade(*args):
    FakePortal.events.append("grade")
    return {"release": False, "rejected": True, "pdf": None, "report": None,
            "error": "NOT_AN_ANSWER_SHEET: typed pages, no handwritten answers"}


def store(tmp_path, release=True):
    FakePortal.events = []; FakePortal.rejections = []; FakePortal.fail_upload = False; FakePortal.reject_mode = "ok"
    FakePortal.documents = None; FakePortal.document_requests = []; FakePortal.fail_before = None; FakePortal.closed = 0
    FakePortal.assign_outcome = None; FakePortal.windows = []; FakePortal.verify_outcome = "unknown"; FakePortal.fail_after_assign_marker = False
    def download(url, target):
        FakePortal.events.append("download"); target.write_bytes(pdf())
    def grade(*args):
        FakePortal.events.append("grade")
        return {"release": release, "pdf": pdf(), "report": report(), "error": "Needs review" if not release else ""}
    return workflow.WorkflowStore(tmp_path, grade, FakePortal, download)


def test_full_sequence_and_resume_skips_completed(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert FakePortal.events == ["assign", "download", "grade", "upload"]
    assert s.read(run["id"])["status"] == "complete"
    s.work(run["id"], "new token")
    assert FakePortal.events == ["assign", "download", "grade", "upload"]


def test_faculty_review_copies_are_uploaded_with_their_flag_unless_the_run_holds_them(tmp_path):
    # Operator's rule of 26 Oct 2026 (build 136): a faculty-review copy with marks is uploaded too.
    s = store(tmp_path, release=False)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert FakePortal.events == ["assign", "download", "grade", "upload"]
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "uploaded" and saved["rows"][0]["flagged_for_review"] is True
    assert saved["status"] == "complete"
    s = store(tmp_path / "strict", release=False)
    strict = options(); strict["review_min_confidence"] = 50       # a run may still hold them below a confidence
    run = s.create([row()], strict, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert FakePortal.events == ["assign", "download", "grade"]
    assert s.read(run["id"])["rows"][0]["stage"] == "faculty_review"
    assert s.copy_paths(run, "copy123")[1].exists()


def test_uncertain_upload_is_not_repeated(tmp_path):
    s = store(tmp_path); FakePortal.fail_upload = True
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])["rows"][0]
    assert saved["stage"] == "upload_uncertain" and "Could not tell" in saved["note"]   # the portal gave no answer: a person decides
    s.work(run["id"], "token")
    assert FakePortal.events.count("upload") == 1 and FakePortal.events.count("verify_upload") == 1
    assert FakePortal.events.count("grade") == 1


def test_download_failure_resumes_after_assignment(tmp_path):
    s = store(tmp_path)
    good = s.downloader
    s.downloader = lambda *a: (_ for _ in ()).throw(ValueError("offline"))
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["skipped_from"] == "assigned"
    assert saved["rows"][0]["failures"] == 3
    assert saved["status"] == "complete"
    s.downloader = good; s.work(run["id"], "token")
    assert FakePortal.events.count("assign") == 1
    assert s.read(run["id"])["status"] == "complete"


def test_stop_preserves_completed_stage(tmp_path):
    s = store(tmp_path)
    original = s.downloader
    def download(*args):
        original(*args); s.stop_run(run["id"])
    s.downloader = download
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert s.read(run["id"])["status"] == "stopped"
    assert s.read(run["id"])["rows"][0]["stage"] == "downloaded"
    assert "grade" not in FakePortal.events


def test_test_selection_and_existing_assignment_validation(tmp_path):
    s = store(tmp_path)
    other = row(); other["submission_id"] = "copy456"; other["test_id"] = "another"
    with pytest.raises(ValueError, match="one test"):
        s.create([row(), other], options(), "owner", pdf(), pdf())
    assigned = row(); assigned["portal_status"] = "Assigned"
    opts = options(); opts["already_assigned"] = True
    run = s.create([assigned], opts, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert "assign" not in FakePortal.events
    # Without the skip box, a sheet with non-Submitted copies is accepted: the run reads the portal's state per copy.
    later = row(); later.update(submission_id="copy789", student_id="student789", portal_status="In Evaluation")
    run = s.create([later], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert FakePortal.events.count("assign") == 1 and s.read(run["id"])["rows"][0]["stage"] == "uploaded"


def token(sub):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_api_auth_isolation_and_reconciliation(tmp_path):
    app = FastAPI()
    def allowed(auth):
        if not auth:
            raise HTTPException(401, "Sign in")
        return auth.removeprefix("Bearer ")
    s = workflow.install(app, allowed, lambda *a: {}, tmp_path)
    auth = {"Authorization": f"Bearer {token('alice')}"}
    client = LocalClient(app)
    run = s.create([row()], options(), workflow.owner_for(token("alice")), pdf(), pdf())
    assert client.get("/api/workflow/runs").status_code == 401
    assert client.get(f"/api/workflow/{run['id']}", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404
    assert client.get("/api/workflow/runs", headers=auth).json()["runs"][0]["id"] == run["id"]
    run["rows"][0]["stage"] = "upload_uncertain"; s.save(run)
    response = client.post(f"/api/workflow/{run['id']}/reconcile", headers=auth, json={"submission_id": "copy123", "outcome": "not_done"})
    assert response.json()["rows"][0]["stage"] == "checked"
    assert client.get("/api/workflow/not-a-run", headers=auth).status_code == 404
    assert client.post("/api/workflow/preview", headers=auth, files={"sheet": ("bad.csv", b"_id,_id\na,b\n")}).status_code == 400


@pytest.mark.parametrize("uncertain,target", [
    ("assignment_uncertain", "pending"),
    ("upload_uncertain", "checked"),
    ("rejection_uncertain", "rejected"),
])
def test_verified_not_done_restores_stage_and_resets_retry_allowance(tmp_path, uncertain, target):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    auth = {"Authorization": f"Bearer {token('alice')}"}
    run = s.create([row()], options(), workflow.owner_for(token("alice")), pdf(), pdf())
    run["status"] = "needs_attention"
    run["rows"][0].update(stage=uncertain, failures=3, error="Submit confirmation lost",
                          rejection_reason="DUPLICATE_UPLOAD: same file already graded")
    s.save(run)

    response = LocalClient(app).post(f"/api/workflow/{run['id']}/reconcile", headers=auth,
                                    json={"submission_id": "copy123", "outcome": "not_done"})

    assert response.status_code == 200
    result = response.json()["rows"][0]
    assert result["stage"] == target and result["failures"] == 0
    assert result["error"] == (result["rejection_reason"] if target == "rejected" else "")
    saved = s.read(run["id"])["rows"][0]
    assert saved["stage"] == target and saved["failures"] == 0


def test_remote_clients_cannot_control_portal(tmp_path):
    app = FastAPI()
    workflow.install(app, lambda auth: token("alice"), lambda *a: {}, tmp_path)
    client = LocalClient(app, "203.0.113.10")
    assert client.get("/api/workflow/runs").status_code == 403


def test_csv_and_excel_templates_parse():
    headers = list(row())
    import csv
    data = io.StringIO(); writer = csv.DictWriter(data, fieldnames=headers); writer.writeheader(); writer.writerow(row())
    assert workflow.parse_sheet(data.getvalue().encode(), "sheet.csv")[0]["test_id"] == "test123"
    import openpyxl
    wb = openpyxl.Workbook(); wb.active.append(headers); wb.active.append(list(row().values()))
    output = io.BytesIO(); wb.save(output)
    assert workflow.parse_sheet(output.getvalue(), "sheet.xlsx")[0]["student_id"] == "student123"


@pytest.mark.parametrize("released", [True, False])
def test_existing_grader_bridge_runs_attestation_and_background_job(tmp_path, monkeypatch, released):
    import server
    (tmp_path / "question_paper.pdf").write_bytes(pdf())
    (tmp_path / "answer_key.pdf").write_bytes(pdf())
    source = tmp_path / "student123.pdf"; source.write_bytes(pdf())
    events = []
    monkeypatch.setattr(server, "_require_allowed", lambda authorization: "validated-token")

    async def detected(**kwargs):
        events.append("attestation")
        assert kwargs["subject"] == "Maths"
        assert kwargs["mode"] == "ai_first"
        assert (await kwargs["question_paper"].read()).startswith(b"%PDF")
        return {"marks_attestation": "paper-bound-proof", "items": [{"qid": "Q1", "max": 10}], "method": "ai_verified"}

    async def queue(background, **kwargs):
        events.append("queue")
        assert kwargs["marks_attestation"] == "paper-bound-proof"
        assert kwargs["key_source"] == "upload"
        assert len(kwargs["sheets"]) == 1
        async def complete():
            events.append("grade")
            server.JOBS["workflow-bridge-test"] = {"key": "key", "results": [{"index": 0, "release": released, "review_reasons": [] if released else ["Needs faculty review"]}], "pdfs": {0: b"checked"}, "reports": {0: {"report": report()}}}
        background.add_task(complete)
        return {"job_id": "workflow-bridge-test", "download_key": "key"}

    monkeypatch.setattr(server, "detect_marks", detected)
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    outcome = server._workflow_grade(tmp_path, source, options(), "Bearer token")
    assert events == ["attestation", "queue", "grade"]
    assert outcome["release"] is released
    assert outcome["pdf"] == b"checked"
    assert outcome["report"]["total_score"] == 9
    server.JOBS.pop("workflow-bridge-test", None)


@pytest.mark.parametrize("submission_id", ["question_paper", "answer_key", "checked_copy123", "CON"])
def test_submission_filenames_cannot_replace_exam_documents(tmp_path, submission_id):
    s = store(tmp_path)
    selected = row(); selected["submission_id"] = submission_id
    paper, key = pdf(), pdf()
    run = s.create([selected], options(), "owner", paper, key)
    s.work(run["id"], "token")
    folder = s.folder(run["id"])
    assert (folder / "question_paper.pdf").read_bytes() == paper
    assert (folder / "answer_key.pdf").read_bytes() == key
    assert s.copy_paths(run, submission_id)[0].parent != folder
    assert s.read(run["id"])["status"] == "complete"


def test_case_collisions_and_ambiguous_attempts_are_rejected(tmp_path):
    changed = row(); changed["submission_id"] = "COPY123"
    with pytest.raises(ValueError, match="conflicting duplicate"):
        workflow.normalize_rows([row(), changed])
    changed["submission_id"] = "copy456"
    s = store(tmp_path)
    # A student with two submissions of the test: the latest copy is processed, the other is recorded as a duplicate.
    twice = row(); twice.update(submission_id="6ab905b7ba05fde8659f6774")        # later portal id (its first 8 hex digits are a time)
    earlier = row(); earlier.update(submission_id="5ab905b7ba05fde8659f6774")
    both = s.create([twice, earlier], options(), "owner", pdf(), pdf())
    assert [r["stage"] for r in both["rows"]] == ["pending", "duplicate"] and "6ab905b7ba05fde8659f6774" in both["rows"][1]["note"]
    s.work(both["id"], "token")
    assert [r["stage"] for r in s.read(both["id"])["rows"]] == ["uploaded", "duplicate"] and FakePortal.events.count("assign") == 1
    FakePortal.events.clear()


def test_new_run_cannot_repeat_a_saved_submission(tmp_path):
    s = store(tmp_path)
    s.create([row()], options(), "owner", pdf(), pdf())
    with pytest.raises(ValueError, match="saved workflow"):
        s.create([row()], options(), "owner", pdf(), pdf())


@pytest.mark.parametrize("qid", ["Q1a", "Q1.a", "Q1(a)", "Q.1(a)", "Question 1a", "Q1-a"])
def test_portal_marks_accepts_grader_question_spelling_variants(qid):
    assert workflow.portal_marks({"questions": [{"qid": qid, "score": 2}], "total_score": 2}) == {1: 2}
    assert workflow.portal_marks({"questions": [{"qid": "Q11", "score": 3}, {"qid": "Q1.1", "score": 2}], "total_score": 5}) == {11: 3, 1: 2}


@pytest.mark.parametrize("invalid", ["mapping", "missing_pdf", "invalid_pdf"])
def test_invalid_released_results_are_held_without_paid_regrading(tmp_path, invalid):
    s = store(tmp_path)
    def grade(*args):
        FakePortal.events.append("grade")
        graded = report()
        if invalid == "mapping":
            graded["questions"][0]["qid"] = "Part A"
        return {"release": True, "report": graded, "pdf": None if invalid == "missing_pdf" else b"bad PDF" if invalid == "invalid_pdf" else pdf()}
    s.grader = grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    # Skipped at once, not held for faculty (no faculty, 29 Oct 2026) and not tried
    # three times; its files stay for Resume / retry, which checks it again.
    assert saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["error"].startswith(workflow.SKIPPED_NO_MARKS)
    assert FakePortal.events.count("grade") == 1
    assert "upload" not in FakePortal.events
    assert saved["rows"][0]["has_checked_pdf"] is (invalid == "mapping")
    s.work(run["id"], "token")
    assert FakePortal.events.count("grade") == 2 and "upload" not in FakePortal.events


@pytest.mark.parametrize("fault", ["read", "initial_save", "final_save"])
def test_storage_faults_always_release_active_workflow(tmp_path, monkeypatch, fault):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.active[run["id"]] = {"stop": threading.Event(), "evaluator": None}
    def fail(*args):
        raise OSError("Storage unavailable")
    if fault == "read":
        monkeypatch.setattr(s, "read", fail)
        s.work(run["id"], "token")
    else:
        saved = s.save
        def save(value):
            if fault == "initial_save" or value["status"] != "running":
                fail()
            return saved(value)
        monkeypatch.setattr(s, "save", save)
        with pytest.raises(OSError, match="Storage"):
            s.work(run["id"], "token")
    assert not s.active


def test_successful_resume_clears_old_sign_in_error(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    run["error"] = "Sign in again before resuming."; s.save(run)
    s.work(run["id"], "fresh token")
    saved = s.read(run["id"])
    assert saved["status"] == "complete"
    assert "error" not in saved


def test_missing_exam_input_stops_before_portal_assignment(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    (s.folder(run["id"]) / "question_paper.pdf").unlink()
    s.work(run["id"], "token")
    assert s.read(run["id"])["status"] == "failed"
    assert FakePortal.events == []


def test_real_api_import_start_download_progress_and_duplicate_protection(tmp_path, monkeypatch):
    import time
    import csv
    source_pdf, checked_pdf, exam_pdf = pdf(), pdf(), pdf()
    FakePortal.events = []; FakePortal.fail_upload = False

    class Response:
        is_redirect = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield source_pdf

    monkeypatch.setattr(workflow.requests, "get", lambda url, **kw: Response())
    app = FastAPI()
    def grade(folder, source, opts, auth):
        assert source.read_bytes() == source_pdf
        assert (folder / "question_paper.pdf").read_bytes() == exam_pdf
        FakePortal.events.append("grade")
        return {"release": True, "pdf": checked_pdf, "report": report()}
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), grade, tmp_path)
    s.portal_factory = FakePortal
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    data = io.StringIO(); writer = csv.DictWriter(data, fieldnames=list(row())); writer.writeheader(); writer.writerow(row())
    preview = client.post("/api/workflow/preview", headers=auth, files={"sheet": ("copies.csv", data.getvalue().encode())})
    assert preview.status_code == 200
    def start():
        return client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps(preview.json()["rows"]), "options": json.dumps(options())},
                           files={"question_paper": ("paper.pdf", exam_pdf), "answer_key": ("key.pdf", exam_pdf)})
    response = start()
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    deadline = time.monotonic() + 5
    while s.active and time.monotonic() < deadline:
        time.sleep(.01)
    status = client.get(f"/api/workflow/{run_id}", headers=auth).json()
    assert status["status"] == "complete", status
    assert FakePortal.events == ["assign", "grade", "upload"]
    assert client.get(f"/api/workflow/{run_id}/copy/copy123", headers=auth).status_code == 404
    assert status["rows"][0]["files_cleaned"] is True
    assert not s.copy_paths(status, "copy123")[0].exists()
    assert s.copy_paths(status, "copy123")[2].exists()
    assert start().status_code == 400
    assert FakePortal.events.count("upload") == 1


def test_confirming_uncertain_upload_restores_total_and_completion(tmp_path):
    app = FastAPI()
    auth = {"Authorization": f"Bearer {token('alice')}"}
    s = workflow.install(app, lambda a: a.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    run = s.create([row()], options(), workflow.owner_for(token("alice")), pdf(), pdf())
    run["status"] = "needs_attention"; run["rows"][0]["stage"] = "upload_uncertain"
    s.copy_paths(run, "copy123")[2].write_text(json.dumps(report()), encoding="utf-8"); s.save(run)
    response = LocalClient(app).post(f"/api/workflow/{run['id']}/reconcile", headers=auth, json={"submission_id": "copy123", "outcome": "confirmed"})
    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert response.json()["rows"][0]["awarded_marks"] == 9


def test_grader_bridge_uses_real_queue_and_paper_bound_proof(tmp_path, monkeypatch):
    import server
    from grader import MarksItem, MarksScheme
    from marks_attestation import MarksAttestationStore
    paper, key, student = pdf(), pdf(), pdf()
    (tmp_path / "question_paper.pdf").write_bytes(paper)
    (tmp_path / "answer_key.pdf").write_bytes(key)
    source = tmp_path / "student.pdf"; source.write_bytes(student)
    monkeypatch.setattr(server, "_require_allowed", lambda auth: "validated-token")
    monkeypatch.setattr(server, "_MARKS_ATTESTATIONS", MarksAttestationStore())
    monkeypatch.setattr(server, "JOBS", {})
    monkeypatch.setattr(server, "_persist_job", lambda job_id: None)
    scheme = MarksScheme(total=10, items=[MarksItem(qid="Q1", max=10)])

    async def detect(**kwargs):
        cfg = server.cfg_from_request("claude", "", "Maths", google_token="validated-token", task_id="test",
                                      student_class="Class 10", exam_type="school", category="school", total_marks=10)
        attestation = server._MARKS_ATTESTATIONS.create(paper, 10, scheme, scheme,
            extractors=("claude:sonnet", "claude:opus"), context=server._marks_extraction_context(cfg, "Class 10", "Maths"))
        return {"marks_attestation": attestation.token, "items": [{"qid": "Q1", "max": 10}], "method": "ai_verified"}

    def run_job(payload):
        assert payload["qp_bytes"] == paper and payload["ak_bytes"] == key
        assert payload["question_graph_proof"]
        assert payload["cfg"].category == "school"
        assert payload["sheets"] == [("student.pdf", student)]
        job = server.JOBS[payload["job_id"]]
        job.update(results=[{"index": 0, "release": True}], pdfs={0: pdf()}, reports={0: {"report": report()}})

    monkeypatch.setattr(server, "detect_marks", detect)
    monkeypatch.setattr(server, "_run_job", run_job)
    outcome = server._workflow_grade(tmp_path, source, options(), "Bearer token")
    assert outcome["release"] is True
    assert outcome["report"]["total_score"] == 9


def test_transient_windows_snapshot_lock_is_retried(tmp_path, monkeypatch):
    from pathlib import Path
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    original = Path.replace
    attempts = []
    def locked(path, target):
        attempts.append(str(path))
        if len(attempts) <= 2:
            raise PermissionError("Windows snapshot temporarily locked")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(workflow.time, "sleep", lambda seconds: None)
    run["status"] = "stopped"
    s.save(run)
    assert len(attempts) == 3
    assert s.read(run["id"])["status"] == "stopped"


def test_rejected_copy_is_held_and_posted_only_on_request(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    run = s.create([row()], manual_rejections(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["status"] == "needs_attention"
    assert saved["rows"][0]["stage"] == "rejected"
    assert saved["rows"][0]["rejection_reason"] == saved["rows"][0]["error"] == rejected_grade()["error"]
    assert saved["rows"][0]["has_checked_pdf"] is False
    FakePortal.events = ["assign", "download", "grade"]
    s.work(run["id"], "token")  # resume never re-grades, uploads or posts a rejected copy by itself
    assert FakePortal.events == ["assign", "download", "grade"]
    s.work(run["id"], "token", mode="rejections")
    saved = s.read(run["id"])
    assert FakePortal.events == ["assign", "download", "grade", "reject"]
    assert FakePortal.rejections == [("copy123", rejected_grade()["error"], s.copy_paths(saved, "copy123")[0])]
    assert (saved["rows"][0]["stage"], saved["rows"][0]["awarded_marks"], saved["rows"][0]["error"]) == ("rejection_submitted", 0, "")
    assert saved["status"] == "complete"
    s.work(run["id"], "token", mode="rejections")
    s.work(run["id"], "token")
    assert FakePortal.events.count("reject") == 1


def test_unconfirmed_rejection_submit_is_never_repeated(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    run = s.create([row()], manual_rejections(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    FakePortal.reject_mode = "unverified"
    s.work(run["id"], "token", mode="rejections")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "rejection_uncertain"
    assert "not confirmed" in saved["rows"][0]["error"]
    assert saved["rows"][0]["rejection_reason"] == rejected_grade()["error"]
    assert saved["status"] == "needs_attention"
    FakePortal.reject_mode = "ok"
    s.work(run["id"], "token", mode="rejections"); s.work(run["id"], "token")
    assert FakePortal.events.count("reject") == 1
    assert s.read(run["id"])["rows"][0]["stage"] == "rejection_uncertain"


def test_rejection_failure_before_submit_keeps_the_copy_retryable(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    run = s.create([row()], manual_rejections(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    FakePortal.reject_mode = "fail_before"
    s.work(run["id"], "token", mode="rejections")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["skipped_from"] == "rejected"
    assert saved["rows"][0]["failures"] == 3
    assert "Not Pending" in saved["rows"][0]["error"]
    assert saved["rows"][0]["rejection_reason"] == rejected_grade()["error"]
    FakePortal.reject_mode = "ok"
    s.work(run["id"], "token")  # Resume restores the saved rejected stage.
    s.work(run["id"], "token", mode="rejections")
    assert s.read(run["id"])["rows"][0]["stage"] == "rejection_submitted"
    assert FakePortal.rejections[-1][1] == rejected_grade()["error"]


def test_rejection_needs_the_stored_original_copy(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    run = s.create([row()], manual_rejections(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    s.copy_paths(s.read(run["id"]), "copy123")[0].unlink()
    s.work(run["id"], "token", mode="rejections")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["skipped_from"] == "rejected"
    assert saved["rows"][0]["failures"] == 3
    assert "by hand" in saved["rows"][0]["error"]
    assert "reject" not in FakePortal.events


def test_stop_during_rejections_leaves_remaining_copies_waiting(tmp_path):
    s = store(tmp_path); s.grader = rejected_grade
    second = row(); second.update(submission_id="copy456", student_id="student456")
    run = s.create([row(), second], manual_rejections(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    original = FakePortal.perform
    def stopping(self, stage, *args, **kwargs):
        original(self, stage, *args, **kwargs); s.stop_run(run["id"])
    FakePortal.perform = stopping
    try:
        s.work(run["id"], "token", mode="rejections")
    finally:
        FakePortal.perform = original
    saved = s.read(run["id"])
    assert [r["stage"] for r in saved["rows"]] == ["rejection_submitted", "rejected"]
    assert saved["status"] == "stopped"


def test_api_submit_rejections_requires_rejected_copies_and_reconciles_uncertain(tmp_path):
    import time
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    s.portal_factory = FakePortal
    FakePortal.events = []; FakePortal.rejections = []; FakePortal.reject_mode = "ok"
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    run = s.create([row()], options(), workflow.owner_for(token("alice")), pdf(), pdf())
    assert client.post(f"/api/workflow/{run['id']}/submit-rejections", headers=auth).status_code == 400
    assert client.post(f"/api/workflow/{run['id']}/submit-rejections", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404
    run["rows"][0]["stage"], run["rows"][0]["rejection_reason"] = "rejection_uncertain", "DUPLICATE_UPLOAD: same file already graded"
    run["rows"][0]["portal_assigned"] = True
    s.save(run)
    response = client.post(f"/api/workflow/{run['id']}/reconcile", headers=auth, json={"submission_id": "copy123", "outcome": "not_done"})
    assert (response.json()["rows"][0]["stage"], response.json()["rows"][0]["error"]) == ("rejected", "DUPLICATE_UPLOAD: same file already graded")
    s.copy_paths(run, "copy123")[0].write_bytes(pdf())
    assert client.post(f"/api/workflow/{run['id']}/submit-rejections", headers=auth).status_code == 200
    deadline = time.monotonic() + 5
    while s.active and time.monotonic() < deadline:
        time.sleep(.01)
    status = client.get(f"/api/workflow/{run['id']}", headers=auth).json()
    assert (status["rows"][0]["stage"], status["status"]) == ("rejection_submitted", "complete")
    assert FakePortal.events == ["reject"]
    assert FakePortal.rejections[0][1] == "DUPLICATE_UPLOAD: same file already graded"
    run = s.read(run["id"]); run["rows"][0]["stage"] = "rejection_uncertain"; s.save(run)
    response = client.post(f"/api/workflow/{run['id']}/reconcile", headers=auth, json={"submission_id": "copy123", "outcome": "confirmed"})
    assert (response.json()["rows"][0]["stage"], response.json()["rows"][0]["awarded_marks"], response.json()["status"]) == ("rejection_submitted", 0, "complete")


def wait_idle(s, seconds=5):
    import time
    deadline = time.monotonic() + seconds
    while s.active and time.monotonic() < deadline:
        time.sleep(.01)
    assert not s.active


def pdf_pages(data):
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count


def test_portal_documents_are_fetched_merged_and_used_by_start(tmp_path):
    s = store(tmp_path)
    second = pdf()
    FakePortal.documents = {"question_paper": [("QP_Maths.pdf", pdf())],
                            "answer_key": [("solution-1.pdf", pdf()), ("solution-2.pdf", second)]}
    fetch_id = s.fetch_exam_documents("owner", {"test_id": "test123", "test_name": "Maths Test", "program_name": "Complete Programe", "test_series_name": "Batch Tests", "evaluator_email": "teacher@example.com"})
    wait_idle(s)
    entry = s.exam_documents.get("owner", fetch_id)
    assert entry["status"] == "ready", entry
    assert FakePortal.document_requests == [{"test_id": "test123", "test_name": "Maths Test", "program_name": "Complete Programe", "test_series_name": "Batch Tests", "evaluator_email": "teacher@example.com"}]
    assert entry["documents"]["question_paper"]["filename"] == "QP_Maths.pdf"
    assert (entry["documents"]["answer_key"]["files"], entry["documents"]["answer_key"]["pages"]) == (2, 2)
    assert entry["documents"]["answer_key"]["filename"] == "model-answer-merged.pdf"
    assert pdf_pages(entry["documents"]["answer_key"]["data"]) == 2
    assert "data" not in workflow.ExamDocumentCache.public(entry)["documents"]["answer_key"]
    assert s.exam_documents.get("someone-else", fetch_id) is None
    # The run is created from the fetched documents, with no uploaded files.
    app = FastAPI()
    api = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    api.exam_documents = s.exam_documents; api.portal_factory = FakePortal; api.downloader = s.downloader   # offline
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    owner = workflow.owner_for(token("alice"))
    s.exam_documents.entries[fetch_id]["owner"] = owner
    response = client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(options()), "exam_documents_id": fetch_id})
    assert response.status_code == 200, response.text
    run_folder = api.folder(response.json()["id"])
    assert (run_folder / "question_paper.pdf").read_bytes() == entry["documents"]["question_paper"]["data"]
    assert pdf_pages((run_folder / "answer_key.pdf").read_bytes()) == 2
    wait_idle(api)


def test_uploaded_file_takes_priority_over_fetched_document(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    s.portal_factory = FakePortal; s.downloader = lambda url, target: target.write_bytes(pdf())   # offline
    FakePortal.events = []; FakePortal.documents = {"question_paper": [("portal.pdf", pdf())], "answer_key": []}
    owner = workflow.owner_for(token("alice"))
    fetch_id = s.fetch_exam_documents(owner, {"test_id": "test123", "test_name": "Maths Test", "evaluator_email": "teacher@example.com"})
    wait_idle(s)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    # The portal had no Model Answer: the key must still be uploaded.
    missing = client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(options()), "exam_documents_id": fetch_id})
    assert missing.status_code == 400 and "Model Answer" in missing.text
    uploaded_key, uploaded_paper = pdf(), pdf()
    response = client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(options()), "exam_documents_id": fetch_id},
                           files={"question_paper": ("mine.pdf", uploaded_paper), "answer_key": ("key.pdf", uploaded_key)})
    assert response.status_code == 200, response.text
    run_folder = s.folder(response.json()["id"])
    assert (run_folder / "question_paper.pdf").read_bytes() == uploaded_paper
    assert (run_folder / "answer_key.pdf").read_bytes() == uploaded_key
    wait_idle(s)


def test_start_without_documents_or_fetch_is_refused(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    response = client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(options())})
    assert response.status_code == 400 and "question paper" in response.text
    stale = client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(options()), "exam_documents_id": "f" * 32})
    assert stale.status_code == 400 and "not available" in stale.text
    assert not s.active


def test_portal_document_fetch_failures_and_api_lifecycle(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    s.portal_factory = FakePortal
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    test = {"test_id": "test123", "test_name": "Maths Test", "program_name": "Complete Programe", "test_series_name": "Batch Tests", "evaluator_email": "teacher@example.com"}
    assert client.post("/api/workflow/exam-documents", headers=auth, json={"test_id": "", "test_name": ""}).status_code == 400
    without = client.post("/api/workflow/exam-documents", headers=auth, json={"test_id": "test123", "test_name": "Maths Test"})
    assert without.status_code == 400 and "evaluator email" in without.text
    FakePortal.events = []; FakePortal.documents = ValueError("The portal names test test123 'Other Test', not 'Maths Test'; upload the PDFs instead")
    started = client.post("/api/workflow/exam-documents", headers=auth, json=test)
    assert started.status_code == 200, started.text
    fetch_id = started.json()["fetch_id"]
    wait_idle(s)
    status = client.get(f"/api/workflow/exam-documents/{fetch_id}", headers=auth).json()
    assert status["status"] == "failed" and "Other Test" in status["error"]
    assert client.get(f"/api/workflow/exam-documents/{fetch_id}", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404
    FakePortal.documents = {"question_paper": [("broken.pdf", b"not a pdf")], "answer_key": []}
    fetch_id = client.post("/api/workflow/exam-documents", headers=auth, json=test).json()["fetch_id"]
    wait_idle(s)
    assert "not a readable PDF" in client.get(f"/api/workflow/exam-documents/{fetch_id}", headers=auth).json()["error"]
    FakePortal.documents = {"question_paper": [], "answer_key": []}
    fetch_id = client.post("/api/workflow/exam-documents", headers=auth, json=test).json()["fetch_id"]
    wait_idle(s)
    assert "no question paper or Model Answer" in client.get(f"/api/workflow/exam-documents/{fetch_id}", headers=auth).json()["error"]
    # Conflicting portal roles still block a fetch or run, without a run-count cap.
    s.active["other-run"] = {"stop": threading.Event(), "admin": test["evaluator_email"],
                              "evaluator": "other.teacher@example.com"}
    full = client.post("/api/workflow/exam-documents", headers=auth, json=test)
    assert full.status_code == 409 and "other portal role" in full.text
    blocked = client.post("/api/workflow/start", headers=auth, data={"rows": json.dumps([row()]), "options": json.dumps(options())},
                          files={"question_paper": ("paper.pdf", pdf()), "answer_key": ("key.pdf", pdf())})
    assert blocked.status_code == 409 and "other portal role" in blocked.text
    s.active.clear()
    assert client.post(f"/api/workflow/exam-documents/{fetch_id}/cancel", headers=auth).status_code == 200
    assert client.post("/api/workflow/exam-documents/" + "0" * 32 + "/cancel", headers=auth).status_code == 404


def test_fetched_documents_expire_and_cache_is_bounded(tmp_path):
    clock = {"now": 1000.0}
    cache = workflow.ExamDocumentCache(clock=lambda: clock["now"], ttl=60, max_count=2)
    first = cache.create("owner", {"test_id": "a"})
    cache.update(first, status="ready", documents={})
    second = cache.create("owner", {"test_id": "b"})
    cache.update(second, status="ready", documents={})
    third = cache.create("owner", {"test_id": "c"})
    assert cache.get("owner", first) is None and cache.get("owner", second) and cache.get("owner", third)
    cache.update(third, status="ready", documents={})
    clock["now"] += 61
    assert cache.get("owner", second) is None
    assert cache.get("owner", third) is None


@pytest.mark.parametrize("stage,previous", [("assign", "checked"), ("upload", "checked")])
def test_failure_before_the_submit_click_keeps_the_copy_retryable(tmp_path, stage, previous):
    """Build 99 marked copies 'uncertain' for failures that never reached Submit."""
    s = store(tmp_path); FakePortal.fail_before = stage
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "skipped"
    assert saved["rows"][0]["skipped_from"] == previous
    assert saved["rows"][0]["failures"] == 3
    assert "nothing submitted" in saved["rows"][0]["error"]
    assert saved["status"] == "complete"
    FakePortal.fail_before = None
    s.work(run["id"], "token")
    assert s.read(run["id"])["rows"][0]["stage"] == "uploaded"
    # Assignment: one look ahead of the check, three attempts behind it, the one that worked.
    assert FakePortal.events.count(stage) == (5 if stage == "assign" else 4) and FakePortal.events.count("grade") == 1


def test_portal_windows_are_closed_when_a_run_and_a_fetch_finish(tmp_path):
    s = store(tmp_path)
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    assert FakePortal.closed == 2   # the admin window and the evaluator window
    s.fetch_exam_documents("owner", {"test_id": "test123", "test_name": "Maths Test", "evaluator_email": "teacher@example.com"})
    wait_idle(s)
    assert FakePortal.closed == 3   # the evaluator window again


def test_fetched_and_run_documents_can_be_previewed(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    s.portal_factory = FakePortal
    paper, key = pdf(), pdf()
    FakePortal.events = []; FakePortal.documents = {"question_paper": [("QP.pdf", paper)], "answer_key": [("key.pdf", key)]}
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    owner = workflow.owner_for(token("alice"))
    fetch_id = s.fetch_exam_documents(owner, {"test_id": "test123", "test_name": "Maths Test", "evaluator_email": "teacher@example.com"})
    wait_idle(s)
    shown = client.get(f"/api/workflow/exam-documents/{fetch_id}/question_paper", headers=auth)
    assert shown.status_code == 200 and shown.headers["content-type"].startswith("application/pdf") and shown.content == paper
    assert "inline" in shown.headers["content-disposition"]
    assert client.get(f"/api/workflow/exam-documents/{fetch_id}/other", headers=auth).status_code == 404
    assert client.get(f"/api/workflow/exam-documents/{fetch_id}/answer_key", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404
    run = s.create([row()], options(), owner, paper, key)
    assert client.get(f"/api/workflow/{run['id']}/document/answer_key", headers=auth).content == key
    assert client.get(f"/api/workflow/{run['id']}/document/question_paper", headers=auth).content == paper
    assert client.get(f"/api/workflow/{run['id']}/document/marks", headers=auth).status_code == 404
    assert client.get(f"/api/workflow/{run['id']}/document/answer_key", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404


def test_grading_runs_even_while_the_worker_thread_has_a_running_event_loop(tmp_path):
    """Build 101 live run: the portal's Playwright loop stays running in the worker
    thread and the grading bridge's asyncio.run() refused to start inside it."""
    import asyncio
    import threading
    s = store(tmp_path)
    seen = {}
    def grade(*args):
        FakePortal.events.append("grade")
        seen["thread"] = threading.current_thread().name
        asyncio.run(asyncio.sleep(0))  # what server._workflow_grade does
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    async def worker_with_running_loop():
        s.work(run["id"], "token")  # synchronous, inside this thread's running loop
    asyncio.run(worker_with_running_loop())
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "uploaded", saved["rows"][0]
    assert seen["thread"].startswith("workflow-grade") and seen["thread"] != threading.current_thread().name


def another_run_row(n):
    changed = row()
    changed.update(submission_id=f"copy{n}", student_id=f"student{n}", test_id=f"test{n}", test_name=f"Test {n}")
    return changed


def test_runs_proceed_together_and_take_turns_on_the_portal(tmp_path):
    """Several tests at once (one per browser tab): grading overlaps, portal actions are serialized."""
    s = store(tmp_path)
    gauge = {"grading": 0, "max_grading": 0, "portal": 0, "max_portal": 0}
    counter = threading.Lock()
    def grade(*args):
        with counter:
            gauge["grading"] += 1; gauge["max_grading"] = max(gauge["max_grading"], gauge["grading"])
        time.sleep(.2)
        with counter:
            gauge["grading"] -= 1
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    original = FakePortal.perform
    def perform(self, stage, *args, **kwargs):
        with counter:
            gauge["portal"] += 1; gauge["max_portal"] = max(gauge["max_portal"], gauge["portal"])
        time.sleep(.03)
        try:
            return original(self, stage, *args, **kwargs)
        finally:
            with counter:
                gauge["portal"] -= 1
    FakePortal.perform = perform
    try:
        runs = [s.create([another_run_row(n)], options(), "owner", pdf(), pdf()) for n in range(3)]
        for run in runs:
            s.start(run["id"], "token")
        wait_idle(s, seconds=30)
    finally:
        FakePortal.perform = original
    assert [s.read(run["id"])["status"] for run in runs] == ["complete"] * 3
    assert gauge["max_grading"] >= 2, gauge          # grading of different runs overlapped
    assert gauge["max_portal"] == 1, gauge           # one evaluator: its window served one action at a time
    assert FakePortal.closed == 2                    # admin and evaluator windows closed once, when the last run finished
    assert FakePortal.events.count("upload") == 3


def test_a_fourth_run_starts_and_stop_affects_only_the_selected_run(tmp_path):
    s = store(tmp_path)
    hold = threading.Event()
    def grade(*args):
        hold.wait(10)
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    runs = [s.create([another_run_row(n)], options(), "owner", pdf(), pdf()) for n in range(4)]
    for run in runs:
        s.start(run["id"], "token")
    assert len(s.active) == 4
    with pytest.raises(workflow.BusyError, match="already active"):
        s.start(runs[0]["id"], "token")
    assert s.stop_run(runs[0]["id"]) and not s.stop_run("unknown-run")
    hold.set()
    wait_idle(s, seconds=30)
    assert s.read(runs[0]["id"])["status"] == "stopped"
    assert s.read(runs[0]["id"])["rows"][0]["stage"] != "uploaded"  # stopped at whatever step it had reached
    assert [s.read(run["id"])["status"] for run in runs[1:]] == ["complete"] * 3


def test_copies_of_one_run_are_graded_in_parallel_and_uploaded_in_turn(tmp_path):
    """Live SST run: grading took 5 to 8 minutes per copy and portal steps under a minute,
    one copy after another. Several copies of a run are now in flight at once."""
    s = store(tmp_path)
    gauge = {"grading": 0, "max_grading": 0, "portal": 0, "max_portal": 0}
    counter = threading.Lock()
    def grade(*args):
        with counter:
            gauge["grading"] += 1; gauge["max_grading"] = max(gauge["max_grading"], gauge["grading"])
        time.sleep(.25)
        with counter:
            gauge["grading"] -= 1
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    original = FakePortal.perform
    def perform(self, stage, *args, **kwargs):
        window = "admin" if stage == "assign" else "evaluator"   # each window has its own lane
        with counter:
            gauge[window] = gauge.get(window, 0) + 1
            gauge["max_" + window] = max(gauge.get("max_" + window, 0), gauge[window])
        time.sleep(.03)
        try:
            return original(self, stage, *args, **kwargs)
        finally:
            with counter:
                gauge[window] -= 1
    FakePortal.perform = perform
    try:
        rows = [another_run_row(n) for n in range(5)]
        for changed in rows:
            changed.update(test_id="test1", test_name="Test 1")   # one test, five copies
        run = s.create(rows, options(), "owner", pdf(), pdf())
        s.work(run["id"], "token")
    finally:
        FakePortal.perform = original
    saved = s.read(run["id"])
    assert saved["status"] == "complete" and [r["stage"] for r in saved["rows"]] == ["uploaded"] * 5
    assert all(r["operation"] == "" for r in saved["rows"]) and "current" not in saved
    assert gauge["max_grading"] >= 2, gauge   # copies graded at the same time
    assert gauge["max_admin"] == gauge["max_evaluator"] == 1, gauge    # portal steps one at a time in each window
    assert FakePortal.events.count("upload") == 5 and FakePortal.closed == 2


def test_consecutive_failures_skip_every_copy_after_three_attempts(tmp_path):
    s = store(tmp_path)
    def grade(*args):
        FakePortal.events.append("grade")
        raise RuntimeError("proxy down")
    s.grader = grade
    rows = [another_run_row(n) for n in range(8)]
    for changed in rows:
        changed.update(test_id="test1", test_name="Test 1")
    run = s.create(rows, options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["status"] == "complete" and "error" not in saved
    assert all(r["stage"] == "skipped" and r["skipped_from"] == "downloaded" for r in saved["rows"])
    assert all(r["failures"] == 3 and "proxy down" in r["error"] for r in saved["rows"])
    assert FakePortal.events.count("grade") == 24
    assert FakePortal.events.count("download") == 8
    assert FakePortal.events.count("assign") <= 8      # nothing checked, so nothing has to be assigned


def test_the_paper_marks_are_read_once_for_all_copies_of_a_run(tmp_path, monkeypatch):
    import server
    (tmp_path / "question_paper.pdf").write_bytes(pdf())
    (tmp_path / "answer_key.pdf").write_bytes(pdf())
    source = tmp_path / "student123.pdf"; source.write_bytes(pdf())
    events = []
    monkeypatch.setattr(server, "_require_allowed", lambda authorization: "validated-token")
    monkeypatch.setattr(server._MARKS_ATTESTATIONS, "is_available", lambda token: token == "paper-bound-proof")
    async def detected(**kwargs):
        events.append("attestation")
        return {"marks_attestation": "paper-bound-proof", "items": [{"qid": "Q1", "max": 10}], "method": "ai_verified"}
    async def queue(background, **kwargs):
        events.append("queue")
        async def complete():
            server.JOBS["workflow-marks-cache-test"] = {"key": "key", "results": [{"index": 0, "release": True, "review_reasons": []}], "pdfs": {0: b"checked"}}
        background.add_task(complete)
        return {"job_id": "workflow-marks-cache-test", "download_key": "key"}
    monkeypatch.setattr(server, "detect_marks", detected)
    monkeypatch.setattr(server, "_queue_evaluation", queue)
    for _ in range(3):
        assert server._workflow_grade(tmp_path, source, options(), "Bearer token")["release"] is True
    assert events == ["attestation", "queue", "queue", "queue"]
    server.JOBS.pop("workflow-marks-cache-test", None)


def test_copies_at_the_same_time_is_a_run_option(tmp_path):
    s = store(tmp_path)
    s.assign_head_start = 0        # the workers are the point here, not the pace of assignment
    gauge = {"grading": 0, "max_grading": 0}
    counter = threading.Lock()
    def grade(*args):
        with counter:
            gauge["grading"] += 1; gauge["max_grading"] = max(gauge["max_grading"], gauge["grading"])
        time.sleep(.2)
        with counter:
            gauge["grading"] -= 1
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    rows = [another_run_row(n) for n in range(6)]
    for changed in rows:
        changed.update(test_id="test1", test_name="Test 1")
    chosen = options(); chosen["copies_in_flight"] = "2"
    run = s.create(rows, chosen, "owner", pdf(), pdf())
    assert run["options"]["copies_in_flight"] == 2
    s.work(run["id"], "token")
    assert [r["stage"] for r in s.read(run["id"])["rows"]] == ["uploaded"] * 6
    assert gauge["max_grading"] == 2, gauge
    assert workflow.validate_options(options())["copies_in_flight"] == workflow.COPIES_IN_FLIGHT
    for bad in (0, 9, "five", None):
        with pytest.raises(ValueError, match="1 to 8"):
            broken = options(); broken["copies_in_flight"] = bad; workflow.validate_options(broken)


def test_a_copy_the_portal_already_evaluated_is_noted_and_skipped(tmp_path):
    s = store(tmp_path); FakePortal.assign_outcome = {"result": "finished", "status": "Finished", "evaluator": "x@example.com"}
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "already_evaluated" and "Finished" in saved["rows"][0]["note"]
    assert saved["status"] == "complete" and FakePortal.events == ["assign"]
    s.work(run["id"], "token")
    assert FakePortal.events == ["assign"]   # held on resume


def test_a_reassigned_copy_continues_with_a_note(tmp_path):
    s = store(tmp_path); FakePortal.assign_outcome = {"result": "reassigned", "status": "In Evaluation", "evaluator": "other@example.com"}
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["rows"][0]["stage"] == "uploaded" and "Reassigned on the portal from other@example.com" in saved["rows"][0]["note"]
    assert FakePortal.events == ["assign", "download", "grade", "upload"]


def test_runs_for_different_evaluators_run_together_in_their_own_windows(tmp_path):
    s = store(tmp_path)
    first = s.create([another_run_row(1)], options(), "owner", pdf(), pdf())
    other = options(); other["evaluator_email"] = "Second.Teacher@example.com"
    second = s.create([another_run_row(2)], other, "owner", pdf(), pdf())
    s.start(first["id"], "token"); s.start(second["id"], "token")
    wait_idle(s, seconds=30)
    assert [s.read(run["id"])["status"] for run in (first, second)] == ["complete", "complete"]
    windows = sorted(FakePortal.windows, key=lambda w: (w[1] or 0))
    # Concurrent workers may acquire their desks in either order. Both accounts
    # must still get separate evaluator windows on the two distinct ports.
    assert windows[0] == ("", None)
    assert {email for email, _ in windows[1:]} == {
        options()["evaluator_email"], "Second.Teacher@example.com"}
    assert [port for _, port in windows[1:]] == [9238, 9239]
    assert all(s.desks.evaluator(email).port == port for email, port in windows[1:])
    assert FakePortal.closed == 3   # the admin window and both evaluator windows, once the last run finished
    assert s.desks.evaluator("second.teacher@example.com") is s.desks.evaluator("Second.Teacher@example.com")
    with pytest.raises(ValueError, match="evaluator email"):
        s.desks.evaluator("")


def test_the_progress_table_names_the_window_of_each_portal_step(tmp_path):
    s = store(tmp_path)
    seen = []
    original = FakePortal.perform
    def perform(self, stage, row, *args, **kwargs):
        seen.append((stage, row["operation"]))
        return original(self, stage, row, *args, **kwargs)
    FakePortal.perform = perform
    try:
        run = s.create([row()], options(), "owner", pdf(), pdf())
        s.work(run["id"], "token")
    finally:
        FakePortal.perform = original
    assert seen == [("assign", "assigning in the admin window"),
                    ("upload", f"uploading from the evaluator window ({options()['evaluator_email']})")]


def test_failed_copies_are_retried_without_repeating_completed_steps(tmp_path):
    s = store(tmp_path)
    attempts = {}
    def grade(folder, source, *args):
        attempts[source.name] = attempts.get(source.name, 0) + 1
        if attempts[source.name] <= 2:
            raise RuntimeError("proxy down")
        FakePortal.events.append("grade")
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    rows = [another_run_row(n) for n in range(3)]
    for changed in rows:
        changed.update(test_id="test1", test_name="Test 1")
    run = s.create(rows, options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["status"] == "complete" and [r["stage"] for r in saved["rows"]] == ["uploaded"] * 3
    assert "error" not in saved and list(attempts.values()) == [3, 3, 3]
    assert FakePortal.events.count("assign") == 3 and FakePortal.events.count("download") == 3   # earlier steps are not repeated


def test_sign_out_halts_without_retry_and_other_errors_skip_after_three_attempts(tmp_path):
    s = store(tmp_path)
    class SignedOut(Exception):
        status_code = 401
    def signed_out(*args):
        FakePortal.events.append("grade"); raise SignedOut("expired")
    s.grader = signed_out; s.signin_poll = 0.05
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.start(run["id"], "token")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "Waiting for you to sign in" not in (s.read(run["id"]).get("error") or ""):
        time.sleep(.05)
    assert "Waiting for you to sign in" in s.read(run["id"])["error"]   # a sign-out pauses the run; nothing is retried meanwhile
    assert FakePortal.events.count("grade") == 1
    assert s.stop_run(run["id"])
    wait_idle(s, seconds=10)
    saved = s.read(run["id"])
    assert saved["status"] == "stopped"
    assert saved["rows"][0]["failures"] == 0       # sign-in does not consume a copy attempt
    assert saved["rows"][0]["stage"] == "downloaded"
    s = store(tmp_path / "exhausted")
    def down(*args):
        FakePortal.events.append("grade"); raise RuntimeError("proxy down")
    s.grader = down
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    # Every ordinary failure counts; no shared-outage heuristic resets the counter.
    assert saved["status"] == "complete" and "proxy down" in saved["rows"][0]["error"]
    assert saved["rows"][0]["stage"] == "skipped" and saved["rows"][0]["error"].startswith("Skipped after 3 failed attempts")
    assert FakePortal.events.count("grade") == 3 and FakePortal.events.count("assign") == 1


def test_stop_during_a_copy_retry_delay_ends_the_run(tmp_path):
    s = store(tmp_path); s.retry_delay = 30
    failed = threading.Event()
    def down(*args):
        failed.set()
        raise RuntimeError("proxy down")
    s.grader = down
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.start(run["id"], "token")
    assert failed.wait(10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not s.read(run["id"])["rows"][0]["failures"]:
        time.sleep(.05)
    assert s.read(run["id"])["rows"][0]["failures"] == 1
    assert s.stop_run(run["id"])
    wait_idle(s, seconds=10)
    saved = s.read(run["id"])
    assert saved["status"] == "stopped"
    assert saved["rows"][0]["stage"] == "downloaded"
    assert saved["rows"][0]["failures"] == 1


# --- skipped copies and bounded retries (user's decisions, 16 Oct 2026) ---------
# Live run of 15 Oct: 20 of 398 copies came back "Downloaded file is not a readable
# PDF" and held the run in retry rounds. A copy the portal cannot serve as a readable
# file is skipped immediately. Every other retryable copy is tried at most three
# times, then skipped without holding up the remaining students.

def test_an_unreadable_portal_file_skips_the_copy_and_the_run_goes_on(tmp_path):
    s = store(tmp_path)
    def download(url, target):
        FakePortal.events.append("download")
        if url.endswith("copy1.pdf"):
            raise workflow.UnreadableCopy("Downloaded file is not a readable PDF: the portal served a web page instead of the file")
        target.write_bytes(pdf())
    s.downloader = download
    rows = [another_run_row(n) for n in range(3)]
    for n, changed in enumerate(rows):
        changed.update(test_id="test1", test_name="Test 1", copy_url=f"https://example.cloudfront.net/copy{n}.pdf")
    run = s.create(rows, options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    by_id = {r["submission_id"]: r for r in saved["rows"]}
    assert by_id["copy1"]["stage"] == "skipped" and by_id["copy1"]["error"] == ""
    assert by_id["copy1"]["note"].startswith("Skipped: Downloaded file is not a readable PDF: the portal served a web page")
    assert by_id["copy1"]["skipped_from"] == "assigned"
    assert by_id["copy0"]["stage"] == by_id["copy2"]["stage"] == "uploaded"
    assert saved["status"] == "complete" and "error" not in saved     # not a run error
    assert FakePortal.events.count("grade") == 2 and FakePortal.events.count("upload") == 2
    # A permanently unreadable file stays skipped when the run is resumed.
    s.downloader = lambda url, target: (FakePortal.events.append("download"), target.write_bytes(pdf()))
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert {r["submission_id"]: r["stage"] for r in saved["rows"]} == {"copy0": "uploaded", "copy1": "skipped", "copy2": "uploaded"}
    assert FakePortal.events.count("grade") == FakePortal.events.count("upload") == 2
    assert FakePortal.events.count("assign") == 3                     # the assignment was never repeated


def test_readable_copy_accepts_photos_and_names_what_else_was_served():
    import fitz
    import pytest as _pytest
    served = pdf()
    assert workflow.readable_copy(served, "application/pdf") == served
    with fitz.open() as doc:
        doc.new_page().insert_text((30, 30), "photo")
        photo = doc[0].get_pixmap(dpi=40).tobytes("jpeg")
    as_pdf = workflow.readable_copy(photo, "image/jpeg")
    with fitz.open(stream=as_pdf, filetype="pdf") as doc:
        assert doc.is_pdf and doc.page_count == 1
    with _pytest.raises(workflow.UnreadableCopy, match="web page"):
        workflow.readable_copy(b"<!DOCTYPE html><html><body>Sign in</body></html>", "text/html")
    with _pytest.raises(workflow.UnreadableCopy, match="empty"):
        workflow.readable_copy(b"", "application/pdf")
    with _pytest.raises(workflow.UnreadableCopy, match="not a PDF or a photo"):
        workflow.readable_copy(b"PK\x03\x04 not a pdf", "application/zip")
    assert issubclass(workflow.UnreadableCopy, ValueError)


def test_a_copy_failing_three_times_on_its_own_is_skipped_while_the_rest_go_on(tmp_path):
    s = store(tmp_path)
    import hashlib
    attempts = {"copy1": 0}
    stored_as = hashlib.sha256(b"copy1").hexdigest()      # copies are stored under a digest of their id
    def grade(folder, source, *args):
        FakePortal.events.append("grade")
        if stored_as in str(source):
            attempts["copy1"] += 1
            raise RuntimeError("the checker could not read this copy")
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    rows = [another_run_row(n) for n in range(3)]
    for changed in rows:
        changed.update(test_id="test1", test_name="Test 1")
    run = s.create(rows, options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    by_id = {r["submission_id"]: r for r in saved["rows"]}
    assert by_id["copy0"]["stage"] == by_id["copy2"]["stage"] == "uploaded"
    assert by_id["copy1"]["stage"] == "skipped" and by_id["copy1"]["skipped_from"] == "downloaded"
    assert by_id["copy1"]["error"] == "Skipped after 3 failed attempts: the checker could not read this copy"
    assert attempts["copy1"] == 3 and saved["status"] == "complete"


def test_many_consecutive_bad_copies_do_not_block_the_next_student(tmp_path):
    s = store(tmp_path)
    import hashlib
    good_copy = hashlib.sha256(b"copy4").hexdigest()
    calls = {}
    def grade(folder, source, *args):
        calls[source.name] = calls.get(source.name, 0) + 1
        if good_copy not in source.name:
            raise RuntimeError("proxy down")
        FakePortal.events.append("grade")
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    rows = [another_run_row(n) for n in range(5)]
    for changed in rows:
        changed.update(test_id="test1", test_name="Test 1")
    opts = options(); opts["copies_in_flight"] = 1
    run = s.create(rows, opts, "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert [r["stage"] for r in saved["rows"]] == ["skipped"] * 4 + ["uploaded"]
    assert saved["status"] == "complete" and "error" not in saved
    assert [r["failures"] for r in saved["rows"]] == [3, 3, 3, 3, 0]
    assert sorted(calls.values()) == [1, 3, 3, 3, 3]


def test_a_lost_upload_confirmation_is_confirmed_on_the_portal(tmp_path):
    s = store(tmp_path); FakePortal.fail_upload = True; FakePortal.verify_outcome = "done"
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["status"] == "complete"
    assert saved["rows"][0]["stage"] == "uploaded" and saved["rows"][0]["awarded_marks"] == report()["total_score"]
    assert "Confirmed on the portal after a lost confirmation" in saved["rows"][0]["note"]
    assert FakePortal.events == ["assign", "download", "grade", "upload", "verify_upload"]
    assert (s.folder(run["id"]) / "report.csv").read_text(encoding="utf-8-sig").startswith("student_name,student_id,submission_id")


def test_a_lost_upload_confirmation_the_portal_did_not_take_is_retried_without_resending(tmp_path):
    s = store(tmp_path); FakePortal.verify_outcome = "not_done"
    original = FakePortal.perform
    uploads = {"n": 0}
    def perform(self, stage, row, *args, **kwargs):
        if stage == "upload":
            uploads["n"] += 1
            if uploads["n"] == 1:
                row["portal_upload"] = True   # what the real adapter records once the file is on the portal
                kwargs["before_submit"](); FakePortal.events.append(stage); raise RuntimeError("Confirmation lost")
            assert row["portal_upload"] is True   # the retry knows the file is already there
        return original(self, stage, row, *args, **kwargs)
    FakePortal.perform = perform
    try:
        run = s.create([row()], options(), "owner", pdf(), pdf())
        s.work(run["id"], "token")
    finally:
        FakePortal.perform = original
    saved = s.read(run["id"])
    assert saved["status"] == "complete" and saved["rows"][0]["stage"] == "uploaded"
    assert FakePortal.events == ["assign", "download", "grade", "upload", "verify_upload", "upload"]


def test_a_lost_assignment_confirmation_is_confirmed_and_the_copy_carries_on(tmp_path):
    s = store(tmp_path); FakePortal.fail_after_assign_marker = True; FakePortal.verify_outcome = "done"
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.work(run["id"], "token")
    saved = s.read(run["id"])
    assert saved["status"] == "complete" and saved["rows"][0]["stage"] == "uploaded"
    assert FakePortal.events == ["assign", "verify_assign", "download", "grade", "upload"]


def test_a_sign_out_pauses_the_run_until_the_next_sign_in(tmp_path):
    s = store(tmp_path); s.signin_poll = 0.05
    class SignedOut(Exception):
        status_code = 401
    seen = []
    def grade(folder, source, options, authorization):
        seen.append(authorization)
        if authorization == "Bearer old":
            raise SignedOut("expired")
        FakePortal.events.append("grade")
        return {"release": True, "pdf": pdf(), "report": report(), "error": ""}
    s.grader = grade
    run = s.create([row()], options(), "owner", pdf(), pdf())
    s.start(run["id"], "Bearer old")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "Waiting for you to sign in" not in (s.read(run["id"]).get("error") or ""):
        time.sleep(.05)
    assert "Waiting for you to sign in" in s.read(run["id"])["error"]
    s.latest_authorization["owner"] = "Bearer new"   # the owner signed in again on some tab
    wait_idle(s, seconds=10)
    saved = s.read(run["id"])
    assert saved["status"] == "complete" and "error" not in saved and seen == ["Bearer old", "Bearer new"]


def test_the_run_report_lists_every_copy_with_its_outcome(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    s.portal_factory = FakePortal; s.downloader = lambda url, target: target.write_bytes(pdf())
    s.grader = rejected_grade
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    owner = workflow.owner_for(token("alice"))
    run = s.create([row()], options(), owner, pdf(), pdf())
    s.work(run["id"], "token")
    assert s.latest_authorization == {}
    answer = client.get(f"/api/workflow/{run['id']}/report.csv", headers=auth)
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/csv")
    lines = answer.text.lstrip("\ufeff").splitlines()
    assert lines[0].split(",") == list(workflow.REPORT_COLUMNS)
    assert "rejection_submitted" in lines[1] and row()["student_id"] in lines[1] and "NOT_AN_ANSWER_SHEET" in lines[1]   # posted at once
    assert client.get(f"/api/workflow/{run['id']}/report.csv", headers={"Authorization": f"Bearer {token('bob')}"}).status_code == 404
    assert s.latest_authorization[owner] == f"Bearer {token('alice')}"


@pytest.mark.parametrize("where", ["evaluator", "marks"])
def test_an_input_problem_stops_the_pass_without_charging_copies(tmp_path, where):
    """Live run of 22 Oct: an evaluator missing from the test's dropdown, and a paper whose
    marks did not verify, each burned three attempts on every copy. Both come from the run's
    inputs, so the pass now stops at the first copy and says what to change."""
    s = store(tmp_path)
    if where == "evaluator":
        FakePortal.fail_before = "assign"
        original = FakePortal.perform
        def perform(self, stage, *args, **kwargs):
            if stage == "assign":
                FakePortal.events.append(stage)
                raise RuntimeError("Evaluator 'evaluator.d4@pw.live' was not found among 2745 readable evaluator options for this test. Check the exact email in this test's dropdown; no copies were submitted.")
            return original(self, stage, *args, **kwargs)
        FakePortal.perform = perform
    else:
        def grade(*args):
            FakePortal.events.append("grade")
            raise ValueError("The question rows add up to 40 marks including every alternative; after 1 choice rule one student can score 30, but the paper total is 40. Retry marks verification.")
        s.grader = grade
    rows = [another_run_row(n) for n in range(8)]
    for changed in rows:
        changed.update(test_id="test1", test_name="Test 1")
    try:
        run = s.create(rows, options(), "owner", pdf(), pdf())
        s.work(run["id"], "token")
    finally:
        if where == "evaluator":
            FakePortal.perform = original
    saved = s.read(run["id"])
    in_flight = saved["options"]["copies_in_flight"]
    assert saved["status"] == "needs_attention" and saved["error"].startswith("Stopped: the ")
    assert "Change run inputs" in saved["error"]
    assert all(r["failures"] == 0 and r["stage"] != "skipped" for r in saved["rows"])
    if where == "evaluator":
        # The portal refused the evaluator: one assignment was tried, and checking went on.
        assert "Checking goes on" in saved["error"]
        assert FakePortal.events.count("assign") == 1 and FakePortal.events.count("upload") == 0
        assert all(r["stage"] == "checked" for r in saved["rows"]) and FakePortal.events.count("grade") == 8
        return
    # The copies already in flight hit the same error once each; none is charged an
    # attempt or skipped, and the copies not yet started are not checked.
    tried = [r for r in saved["rows"] if r["error"]]
    assert 1 <= len(tried) <= in_flight < len(saved["rows"])
    assert all(r["stage"] in ("pending", "assigned") for r in saved["rows"] if not r["error"])
    assert FakePortal.events.count("grade") == len(tried)


def test_a_saved_runs_inputs_can_be_changed_before_resume(tmp_path):
    app = FastAPI()
    s = workflow.install(app, lambda auth: auth.removeprefix("Bearer "), lambda *a: {}, tmp_path)
    client, auth = LocalClient(app), {"Authorization": f"Bearer {token('alice')}"}
    owner = workflow.owner_for(token("alice"))
    run = s.create([row()], options(), owner, pdf(), pdf())
    run["options"]["deadline"] = "2020-01-01T10:00"; run["error"] = "Stopped: the evaluator email is not in this test's evaluator list"; s.save(run)
    answer = client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"evaluator_email": "new.teacher@example.com", "admin_email": "", "total_marks": "50"})
    assert answer.status_code == 200, answer.text
    saved = s.read(run["id"])
    assert saved["options"]["evaluator_email"] == "new.teacher@example.com" and saved["options"]["total_marks"] == "50.0"
    assert saved["options"]["deadline"] > "2026" and "error" not in saved   # a past deadline is replaced by a fresh one
    assert client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"evaluator_email": "not-an-email"}).status_code == 400
    assert client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={}).status_code == 400
    assert client.post(f"/api/workflow/{run['id']}/inputs", headers={"Authorization": f"Bearer {token('bob')}"}, json={"total_marks": "40"}).status_code == 404
    s.active[run["id"]] = {"stop": threading.Event(), "evaluator": "x"}
    assert client.post(f"/api/workflow/{run['id']}/inputs", headers=auth, json={"total_marks": "40"}).status_code == 409
    s.active.clear()


def test_the_workflow_gives_the_portal_engine_a_longer_timeout(tmp_path):
    portal = workflow.Portal(tmp_path)
    assert portal.engine.DEFAULT_TIMEOUT_MS == workflow.WORKFLOW_PORTAL_TIMEOUT_MS == 45000
