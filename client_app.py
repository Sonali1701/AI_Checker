"""Desktop client for the AI Checker (the part that ships as an EXE).

Same HTTP surface as server.py, so the existing static/ UI works unchanged — but the
heavy work runs HERE on the user's machine:
  - render PDFs -> page PNGs, build the evaluated PDFs, run the batch loop  (uses LOCAL RAM)
and the ONLY thing sent out is the AI call, to your hosted proxy (proxy_server.py), which
holds the real keys and does the usage logging. No secret is ever compiled into this app.

Config (proxy URL + this user's bearer token) — no secrets baked in, resolved in order:
  1. env vars  AICHECKER_PROXY_URL / AICHECKER_PROXY_TOKEN
  2. client_config.json next to the app  {"proxy_url": "...", "token": "..."}
A missing URL/token yields a clear error at grade time (not a crash).

Run:  uvicorn client_app:app  (the EXE launches this + opens a browser)
"""
from __future__ import annotations

import gc
import io
import json
import os
import sys
import threading
import uuid
import zipfile

import requests
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from io import BytesIO
from PIL import Image

from costs import DEFAULT_USD_TO_INR
from grader import (
    GradeReport, marks_scheme_from_items, marks_scheme_from_pdf, pdf_or_image_to_pngs,
)
from mathpix import pageocr_from_dict
from orient import normalize_orientation
from pdf_renderer import _CURSIVE_FONT_FILE, build_evaluated_pdf

# NOTE: this client deliberately does NOT import `pipeline` / the AI graders. It never
# calls an LLM directly (that's the proxy's job), so it stays free of the anthropic /
# google-genai SDKs — keeping the packaged EXE small and free of fragile SDK transitive
# deps (grpc/protobuf) that PyInstaller would otherwise have to bundle.

# When frozen by PyInstaller, data files (static/) live next to the executable in
# sys._MEIPASS; otherwise next to this source file.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
    os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

# A default proxy URL can be baked here so users only need to paste their token; an env
# var / config file still overrides it. Leave "" to require explicit configuration.
DEFAULT_PROXY_URL = ""

app = FastAPI(title="AI Checker (desktop client)", version="2.0")

JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _trim_memory() -> None:
    """Free memory back to the OS between sheets (matches server.py). On a desktop this
    is rarely needed, but it keeps RSS flat on long batches."""
    gc.collect()


def _client_config() -> dict:
    """Resolve {proxy_url, token} from env first, then client_config.json beside the app."""
    url = (os.environ.get("AICHECKER_PROXY_URL") or "").strip()
    token = (os.environ.get("AICHECKER_PROXY_TOKEN") or "").strip()
    if not (url and token):
        path = os.path.join(APP_DIR, "client_config.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
                url = url or str(cfg.get("proxy_url", "")).strip()
                token = token or str(cfg.get("token", "")).strip()
            except Exception:
                pass
    return {"proxy_url": url or DEFAULT_PROXY_URL, "token": token}


def _require_proxy() -> tuple[str, str]:
    cfg = _client_config()
    url, token = cfg["proxy_url"].rstrip("/"), cfg["token"]
    if not url or not token:
        raise HTTPException(
            400,
            "This app isn't connected to a grading server yet. Set AICHECKER_PROXY_URL and "
            "AICHECKER_PROXY_TOKEN (or fill client_config.json next to the app) with the URL "
            "you were given and your personal access token.",
        )
    return url, token


def _png_files(field: str, pngs: list[bytes]) -> list:
    return [(field, (f"{field}{i}.png", b, "image/png")) for i, b in enumerate(pngs)]


def _thumb_png(png: bytes, max_side: int = 1000) -> bytes:
    img = Image.open(BytesIO(png)).convert("RGB")
    w, h = img.size
    s = max_side / max(w, h)
    if s < 1:
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _orient_doc(url: str, token: str, data: bytes, filename: str) -> bytes:
    """Straighten a document upright at ingest using the proxy's cheap orientation probe.
    Sends only small thumbnails; rebuilds the doc locally from the returned angles. Returns
    the original bytes unchanged if nothing needs rotating or on any failure (graceful —
    a flaky probe must never block grading)."""
    try:
        pngs = pdf_or_image_to_pngs(data, filename, dpi=100)
        files = [("pages", (f"p{i}.png", _thumb_png(p), "image/png")) for i, p in enumerate(pngs)]
        r = requests.post(f"{url}/ai/orient", files=files,
                          headers={"Authorization": f"Bearer {token}"}, timeout=120)
        if r.status_code != 200:
            print(f"[orient] {filename}: /ai/orient HTTP {r.status_code} — left as-is "
                  f"(is the proxy redeployed with /ai/orient?)")
            return data
        body = r.json()
        rots = body.get("rotations") or []
        print(f"[orient] {filename}: rotations={rots} debug={body.get('debug')}")
        if not any(rots):
            return data
        new, _, changed = normalize_orientation(data, filename, rotations=rots)
        return new if changed else data
    except Exception as e:
        print(f"[orient] {filename}: skipped ({e})")
        return data


def _grade_via_proxy(*, url: str, token: str, qp_pngs, sa_pngs, ak_pngs, ak_text,
                     marks_scheme_json: str, provider: str, model: str,
                     student_class: str, subject: str, student_file: str,
                     sheet_id: str, sheet_tab: str) -> dict:
    """POST one prepared sheet to the proxy. Returns the proxy's JSON (report dict,
    ocr_pages, cost, log). Raises HTTPException with a readable message on failure."""
    files = _png_files("qp", qp_pngs) + _png_files("sa", sa_pngs) + _png_files("ak", ak_pngs or [])
    data = {
        "ak_text": ak_text or "", "marks_scheme": marks_scheme_json or "",
        "provider": provider, "model": model, "student_class": student_class or "",
        "subject": subject or "", "student_file": student_file,
        "sheet_id": sheet_id or "", "sheet_tab": sheet_tab or "",
    }
    try:
        r = requests.post(f"{url}/ai/grade-sheet", files=files, data=data,
                          headers={"Authorization": f"Bearer {token}"}, timeout=900)
    except requests.RequestException as e:
        raise HTTPException(502, f"Couldn't reach the grading server: {e}")
    if r.status_code == 401:
        raise HTTPException(401, "Your access token was rejected by the grading server.")
    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:200]
        raise HTTPException(502, f"Grading server error ({r.status_code}): {detail}")
    return r.json()


# ---------------------------------------------------------------------------
# Endpoints — identical surface to server.py so the existing UI is unchanged
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    """Keys live on the proxy, so the UI shouldn't ask for them — report them present.
    Also tell the UI whether this app is connected to a proxy yet."""
    cfg = _client_config()
    return {
        "has_google_key": True, "has_anthropic_key": True, "has_mathpix_key": True,
        "default_usd_to_inr": DEFAULT_USD_TO_INR,
        "mode": "client",
        "proxy_configured": bool(cfg["proxy_url"] and cfg["token"]),
        # which handwriting font the evaluated PDF will use (Kalam-Regular.ttf when bundled)
        "remark_font": os.path.basename(_CURSIVE_FONT_FILE) if _CURSIVE_FONT_FILE else None,
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
    """Deterministic regex detection off the PDF text layer — runs fully LOCAL, no AI,
    no secret. Scanned papers (no text layer) return 422 so the user enters marks manually."""
    data = await question_paper.read()
    if not data:
        raise HTTPException(400, "Question paper came through empty — re-upload it.")
    try:
        scheme = marks_scheme_from_pdf(data, question_paper.filename)   # regex, local, no AI
    except Exception as e:
        raise HTTPException(500, f"Could not detect marks: {e}")
    if scheme is None:
        raise HTTPException(
            422, "This paper looks scanned (no text layer to read marks from). "
                 "Please enter the maximum marks manually.")
    items = [{"qid": it.qid, "max": float(it.max), "part": it.description} for it in scheme.items]
    return {"items": items, "total": round(scheme.total, 2), "method": "regex"}


@app.post("/api/generate-rubric")
async def gen_rubric(
    question_paper: UploadFile = File(...),
    provider: str = Form("gemini"),
    model: str = Form(""),
    api_key: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
):
    """Rubric generation needs the model, so it's proxied (render locally, AI on the proxy)."""
    url, token = _require_proxy()
    data = await question_paper.read()
    if not data:
        raise HTTPException(400, "Question paper came through empty — re-upload it.")
    qp_pngs = pdf_or_image_to_pngs(data, question_paper.filename)
    files = _png_files("qp", qp_pngs)
    form = {"provider": provider, "model": model,
            "student_class": student_class or "", "subject": subject or ""}
    try:
        r = requests.post(f"{url}/ai/generate-rubric", files=files, data=form,
                          headers={"Authorization": f"Bearer {token}"}, timeout=300)
    except requests.RequestException as e:
        raise HTTPException(502, f"Couldn't reach the grading server: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"Rubric generation failed ({r.status_code}).")
    return {"rubric": r.json().get("rubric", "")}


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
    key_source: str = Form("rubric"),
    rubric_text: str = Form(""),
    marks_items: str = Form("[]"),
    use_mathpix: str = Form("false"),
    mathpix_key: str = Form(""),
    usd_to_inr: str = Form(""),
    log_to_sheet: str = Form("false"),
    sheet_id: str = Form(""),
    sheet_tab: str = Form(""),
):
    url, token = _require_proxy()           # fail fast if not connected
    qp_bytes = await question_paper.read()
    if not qp_bytes:
        raise HTTPException(400, "Question paper came through empty — re-upload it.")
    sheets: list[tuple[str, bytes]] = []
    for f in answer_sheets:
        sheets.append((f.filename, await f.read()))
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

    try:
        items = json.loads(marks_items or "[]")
    except json.JSONDecodeError:
        items = []

    job_id = uuid.uuid4().hex
    with _LOCK:
        JOBS[job_id] = {"status": "running", "total": len(sheets), "done": 0,
                        "current": "", "error": None, "results": [], "pdfs": {}}

    payload = dict(
        job_id=job_id, url=url, token=token,
        qp_bytes=qp_bytes, qp_name=question_paper.filename,
        ak_bytes=ak_bytes, ak_name=ak_name, key_source=key_source, rubric_text=rubric_text,
        items=items, sheets=sheets, provider=provider, model=model,
        student_class=student_class or "", subject=subject or "",
        sheet_id=sheet_id.strip(), sheet_tab=sheet_tab.strip(),
    )
    background_tasks.add_task(_run_job, payload)
    return {"job_id": job_id}


def _run_job(p: dict) -> None:
    """Render shared inputs once locally, grade each sheet via the proxy, build the
    evaluated PDF locally, update the in-memory job."""
    jid = p["job_id"]
    url, token = p["url"], p["token"]
    try:
        # Straighten shared inputs ONCE (a rotated/sideways phone photo otherwise grades
        # badly and its marks land askew). Per-sheet straightening happens in the loop.
        qp_bytes = _orient_doc(url, token, p["qp_bytes"], p["qp_name"])
        qp_pngs = pdf_or_image_to_pngs(qp_bytes, p["qp_name"])
        if p["key_source"] == "upload":
            ak_bytes = _orient_doc(url, token, p["ak_bytes"], p["ak_name"])
            ak_pngs = pdf_or_image_to_pngs(ak_bytes, p["ak_name"])
            ak_text = None
        else:
            ak_pngs = None
            ak_text = p["rubric_text"]
        marks_scheme = marks_scheme_from_items(p["items"])
        ms_json = marks_scheme.model_dump_json() if marks_scheme else ""

        for idx, (name, sa_bytes) in enumerate(p["sheets"]):
            with _LOCK:
                JOBS[jid]["current"] = name
            try:
                if not sa_bytes:
                    raise ValueError("file came through empty (0 bytes)")
                sa_bytes = _orient_doc(url, token, sa_bytes, name)       # straighten if sideways
                sa_pngs = pdf_or_image_to_pngs(sa_bytes, name)           # LOCAL render
                resp = _grade_via_proxy(
                    url=p["url"], token=p["token"], qp_pngs=qp_pngs, sa_pngs=sa_pngs,
                    ak_pngs=ak_pngs, ak_text=ak_text, marks_scheme_json=ms_json,
                    provider=p["provider"], model=p["model"], student_class=p["student_class"],
                    subject=p["subject"], student_file=name,
                    sheet_id=p["sheet_id"], sheet_tab=p["sheet_tab"],
                )
                report = GradeReport.model_validate(resp["report"])
                ocr_pages = [pageocr_from_dict(d) for d in resp.get("ocr_pages", [])] or None
                pdf_bytes = build_evaluated_pdf(                          # LOCAL build
                    student_pdf_bytes=sa_bytes, student_filename=name,
                    student_pngs=sa_pngs, report=report, ocr_pages=ocr_pages,
                )
                cost = resp.get("cost", {})
                result = {
                    "index": idx, "student": name, "ok": True,
                    "score": report.total_score, "max": report.max_total,
                    "percent": round(report.total_score / report.max_total * 100, 1) if report.max_total else 0,
                    "remarks": report.overall_remarks,
                    "questions": [
                        {"q": q.qid, "score": q.score, "max": q.max_score, "remark": q.remark or ""}
                        for q in report.questions
                    ],
                    "cost": cost, "model": cost.get("model") or p["model"],
                    "log": resp.get("log"),
                }
                with _LOCK:
                    JOBS[jid]["pdfs"][idx] = pdf_bytes
            except HTTPException as e:
                result = {"index": idx, "student": name, "ok": False, "error": e.detail}
            except Exception as e:
                result = {"index": idx, "student": name, "ok": False, "error": str(e)}
            with _LOCK:
                JOBS[jid]["results"].append(result)
                JOBS[jid]["done"] += 1
            p["sheets"][idx] = (name, b"")     # release this sheet's bytes
            _trim_memory()
        with _LOCK:
            JOBS[jid]["status"] = "done"
            JOBS[jid]["current"] = ""
    except Exception as e:
        with _LOCK:
            JOBS[jid]["status"] = "error"
            JOBS[jid]["error"] = str(e)


def _job_or_404(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id.")
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


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
