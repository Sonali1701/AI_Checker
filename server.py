"""FastAPI backend for the AI Checker.

Replaces the Streamlit app. Serves a static HTML/CSS/JS frontend and exposes the
grading pipeline as HTTP endpoints. Grading takes minutes, so /api/evaluate submits
a background job and the client polls /api/jobs/{id}.

Run locally:  uvicorn server:app --reload
Secrets come from environment variables (GOOGLE_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON,
MATHPIX_APP_KEY, ANTHROPIC_API_KEY) — set them in your shell / .env / Render dashboard.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import threading
import uuid
import zipfile
from datetime import datetime

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from costs import DEFAULT_USD_TO_INR
from grader import pdf_or_image_to_pngs
from pipeline import (
    ProviderConfig, detect_marks_scheme, generate_rubric, grade_sheet,
    marks_scheme_from_items,
)

load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

app = FastAPI(title="AI Checker", version="2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory job store. Single-instance only (Render free tier is one instance);
# jobs are lost on restart/sleep. Swap for Redis/DB if you scale to many workers.
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _cfg_from_form(provider: str, model: str, api_key: str) -> ProviderConfig:
    provider = (provider or "gemini").strip().lower()
    if provider not in ("gemini", "claude"):
        provider = "gemini"
    default_model = "gemini-2.5-flash" if provider == "gemini" else "claude-opus-4-7"
    key = (api_key or "").strip() or None
    cfg = ProviderConfig(provider=provider, model=(model or default_model).strip(), api_key=key)
    if provider == "gemini":
        # Vertex AI Express keys start with "AQ" and MUST hit the Vertex endpoint
        # (genai.Client(vertexai=True, api_key=...)). AI Studio keys ("AIza") use the
        # Developer API. Auto-detect from the effective key so no UI/env toggle is needed.
        effective = key or os.environ.get("GOOGLE_API_KEY") or ""
        if effective.startswith("AQ") or _truthy(os.environ.get("GEMINI_USE_VERTEX")):
            cfg.gemini_vertex = True
            cfg.project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
            cfg.location = os.environ.get("GOOGLE_CLOUD_LOCATION") or None
    return cfg


def _cost_dict(cost) -> dict:
    d = dataclasses.asdict(cost)
    d["billed_input_tokens"] = cost.billed_input_tokens
    return d


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    """What the frontend needs to pre-fill: which keys are already set server-side."""
    return {
        "has_google_key": bool(os.environ.get("GOOGLE_API_KEY")),
        "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_mathpix_key": bool(os.environ.get("MATHPIX_APP_KEY")),
        "default_usd_to_inr": DEFAULT_USD_TO_INR,
    }


@app.post("/api/detect-marks")
async def detect_marks(
    question_paper: UploadFile = File(...),
    provider: str = Form("gemini"),
    model: str = Form(""),
    api_key: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
):
    data = await question_paper.read()
    if not data:
        raise HTTPException(400, "Question paper came through empty — re-upload it.")
    cfg = _cfg_from_form(provider, model, api_key)
    try:
        scheme, method = detect_marks_scheme(data, question_paper.filename, cfg,
                                             student_class or None, subject or None)
    except Exception as e:
        raise HTTPException(500, f"Could not detect marks: {e}")
    if scheme is None:
        raise HTTPException(
            422, "This paper has no text layer (looks scanned) and no API key was given "
                 "for AI fallback. Add a key or enter marks manually.")
    items = [{"qid": it.qid, "max": float(it.max), "part": it.description} for it in scheme.items]
    return {"items": items, "total": round(scheme.total, 2), "method": method}


@app.post("/api/generate-rubric")
async def gen_rubric(
    question_paper: UploadFile = File(...),
    provider: str = Form("gemini"),
    model: str = Form(""),
    api_key: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
):
    data = await question_paper.read()
    if not data:
        raise HTTPException(400, "Question paper came through empty — re-upload it.")
    cfg = _cfg_from_form(provider, model, api_key)
    try:
        qp_pngs = pdf_or_image_to_pngs(data, question_paper.filename)
        rubric = generate_rubric(qp_pngs, cfg, student_class or None, subject or None)
    except Exception as e:
        raise HTTPException(500, f"Rubric generation failed: {e}")
    if not rubric or not rubric.strip():
        raise HTTPException(502, "The model returned an empty rubric — try again.")
    return {"rubric": rubric}


@app.post("/api/evaluate")
async def evaluate(
    background_tasks: BackgroundTasks,
    question_paper: UploadFile = File(...),
    answer_sheets: list[UploadFile] = File(...),
    answer_key: UploadFile | None = File(None),
    provider: str = Form("gemini"),
    model: str = Form(""),
    api_key: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    key_source: str = Form("rubric"),          # "upload" | "rubric"
    rubric_text: str = Form(""),
    marks_items: str = Form("[]"),
    use_mathpix: str = Form("false"),
    mathpix_key: str = Form(""),
    usd_to_inr: str = Form(""),
    log_to_sheet: str = Form("false"),
    sheet_id: str = Form(""),
    sheet_tab: str = Form(""),
):
    qp_bytes = await question_paper.read()
    if not qp_bytes:
        raise HTTPException(400, "Question paper came through empty — re-upload it.")
    sheets: list[tuple[str, bytes]] = []
    for f in answer_sheets:
        b = await f.read()
        sheets.append((f.filename, b))
    if not sheets:
        raise HTTPException(400, "Upload at least one answer sheet.")

    ak_bytes = ak_name = None
    if key_source == "upload":
        if answer_key is None:
            raise HTTPException(400, "Answer key file is required when key source is 'upload'.")
        ak_bytes = await answer_key.read()
        ak_name = answer_key.filename
        if not ak_bytes:
            raise HTTPException(400, "Answer key came through empty — re-upload it.")
    elif not rubric_text.strip():
        raise HTTPException(400, "Provide a rubric (generate or paste one) when not uploading a key.")

    if mathpix_key.strip():
        os.environ["MATHPIX_APP_KEY"] = mathpix_key.strip()

    try:
        items = json.loads(marks_items or "[]")
    except json.JSONDecodeError:
        items = []
    try:
        rate = float(usd_to_inr) if usd_to_inr.strip() else DEFAULT_USD_TO_INR
    except ValueError:
        rate = DEFAULT_USD_TO_INR

    cfg = _cfg_from_form(provider, model, api_key)
    job_id = uuid.uuid4().hex
    with _LOCK:
        JOBS[job_id] = {"status": "running", "total": len(sheets), "done": 0,
                        "current": "", "error": None, "results": [], "pdfs": {}}

    payload = dict(
        job_id=job_id, qp_bytes=qp_bytes, qp_name=question_paper.filename,
        ak_bytes=ak_bytes, ak_name=ak_name, key_source=key_source, rubric_text=rubric_text,
        items=items, sheets=sheets, cfg=cfg, student_class=student_class or None,
        subject=subject or None, use_mathpix=_truthy(use_mathpix), rate=rate,
        log_to_sheet=_truthy(log_to_sheet), sheet_id=sheet_id.strip(), sheet_tab=sheet_tab.strip(),
    )
    background_tasks.add_task(_run_job, payload)
    return {"job_id": job_id}


def _run_job(p: dict) -> None:
    """Background grader: render shared inputs once, grade each sheet, update the job."""
    jid = p["job_id"]
    try:
        qp_pngs = pdf_or_image_to_pngs(p["qp_bytes"], p["qp_name"])
        if p["key_source"] == "upload":
            ak_pngs = pdf_or_image_to_pngs(p["ak_bytes"], p["ak_name"])
            ak_text = None
        else:
            ak_pngs = None
            ak_text = p["rubric_text"]
        marks_scheme = marks_scheme_from_items(p["items"])
        cfg = p["cfg"]
        provider_label = "Claude" if cfg.is_claude else "Gemini"

        for idx, (name, sa_bytes) in enumerate(p["sheets"]):
            with _LOCK:
                JOBS[jid]["current"] = name
            try:
                if not sa_bytes:
                    raise ValueError("file came through empty (0 bytes)")
                out = grade_sheet(
                    qp_pngs=qp_pngs, sa_bytes=sa_bytes, filename=name,
                    ak_pngs=ak_pngs, ak_text=ak_text, marks_scheme=marks_scheme, cfg=cfg,
                    student_class=p["student_class"], subject=p["subject"],
                    use_mathpix=p["use_mathpix"], usd_to_inr=p["rate"],
                )
                report, cost = out["report"], out["cost"]
                log = _maybe_log_sheet(p, name, report, cost, provider_label)
                result = {
                    "index": idx, "student": name, "ok": True,
                    "score": report.total_score, "max": report.max_total,
                    "percent": round(report.total_score / report.max_total * 100, 1) if report.max_total else 0,
                    "remarks": report.overall_remarks,
                    "questions": [
                        {"q": q.qid, "score": q.score, "max": q.max_score, "remark": q.remark or ""}
                        for q in report.questions
                    ],
                    "cost": _cost_dict(cost), "model": cost.model or cfg.model, "log": log,
                }
                with _LOCK:
                    JOBS[jid]["pdfs"][idx] = out["pdf_bytes"]
            except Exception as e:  # one bad sheet must not abort the batch
                result = {"index": idx, "student": name, "ok": False, "error": str(e)}
            with _LOCK:
                JOBS[jid]["results"].append(result)
                JOBS[jid]["done"] += 1
        with _LOCK:
            JOBS[jid]["status"] = "done"
            JOBS[jid]["current"] = ""
    except Exception as e:
        with _LOCK:
            JOBS[jid]["status"] = "error"
            JOBS[jid]["error"] = str(e)


def _maybe_log_sheet(p, name, report, cost, provider_label) -> dict | None:
    if not p["log_to_sheet"]:
        return None
    try:
        from sheets_log import append_cost_row, cost_row, DEFAULT_SPREADSHEET_ID
        row = cost_row(
            student_file=name, provider=provider_label, model=cost.model or p["cfg"].model,
            student_class=p["student_class"], subject=p["subject"],
            score=report.total_score, max_score=report.max_total,
            input_tokens=cost.billed_input_tokens, output_tokens=cost.output_tokens,
            mathpix_pages=cost.mathpix_pages, llm_cost_usd=cost.llm_cost_usd,
            mathpix_cost_usd=cost.mathpix_cost_usd, total_usd=cost.total_usd,
            usd_to_inr=cost.usd_to_inr, total_inr=cost.total_inr,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        append_cost_row(row, spreadsheet_id=p["sheet_id"] or DEFAULT_SPREADSHEET_ID,
                        worksheet=p["sheet_tab"] or None)
        return {"ok": True, "msg": "Logged to Google Sheet."}
    except Exception as e:
        return {"ok": False, "msg": f"Sheet log failed: {e}"}


def _job_or_404(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id (it may have expired after a restart).")
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _job_or_404(job_id)
    return JSONResponse({
        "status": job["status"], "total": job["total"], "done": job["done"],
        "current": job.get("current", ""), "error": job.get("error"),
        "results": sorted(job["results"], key=lambda r: r["index"]),
    })


@app.get("/api/jobs/{job_id}/pdf/{index}")
def job_pdf(job_id: str, index: int):
    job = _job_or_404(job_id)
    pdf = job["pdfs"].get(index)
    if pdf is None:
        raise HTTPException(404, "No evaluated PDF for that sheet.")
    name = next((r["student"] for r in job["results"] if r["index"] == index), f"sheet{index}")
    stem = name.rsplit(".", 1)[0]
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="evaluated_{stem}.pdf"'})


@app.get("/api/jobs/{job_id}/zip")
def job_zip(job_id: str):
    job = _job_or_404(job_id)
    if not job["pdfs"]:
        raise HTTPException(404, "No evaluated PDFs yet.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in sorted(job["results"], key=lambda r: r["index"]):
            pdf = job["pdfs"].get(r["index"])
            if pdf:
                zf.writestr(f"evaluated_{r['student'].rsplit('.', 1)[0]}.pdf", pdf)
    return Response(content=buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="evaluated_sheets.zip"'})


# ---------------------------------------------------------------------------
# Static frontend (mounted last so /api/* wins)
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
