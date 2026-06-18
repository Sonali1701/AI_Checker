"""Thin AI proxy for the desktop (EXE) client.

Holds ALL secrets (Gemini/Claude, Mathpix, Google service account) server-side and
exposes ONLY the secret-using steps: OCR + grade, and rubric generation. The heavy work
(PDF rendering, building the evaluated PDF, the batch loop) runs in the client, so this
service stays tiny — it processes ONE sheet per request and frees its images immediately,
which is why it can't OOM the way the all-in-one server did on big batches.

Auth: per-user bearer token (see proxy_auth). Usage is logged to Google Sheets HERE,
server-side and automatically, so a tampered client can never skip logging.

Run:  uvicorn proxy_server:app --host 0.0.0.0 --port $PORT
Env:  PROXY_TOKENS="alice:tok_...,bob:tok_..."  GOOGLE_API_KEY=...  MATHPIX_APP_KEY=...
      GOOGLE_SERVICE_ACCOUNT_JSON=...  (optional) ANTHROPIC_API_KEY=...
"""
from __future__ import annotations

import dataclasses
import os

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

from costs import DEFAULT_USD_TO_INR
from grader import MarksScheme
from mathpix import pageocr_to_dict
from pipeline import cfg_from_request, generate_rubric, grade_prepared
from proxy_auth import user_from_authorization

app = FastAPI(title="AI Checker — AI Proxy")


def _require_user(authorization: str | None) -> str:
    user = user_from_authorization(authorization)
    if not user:
        raise HTTPException(401, "Invalid or missing bearer token.")
    return user


def _log_usage(*, user: str, student_file: str, cost, report,
               provider: str, student_class: str, subject: str,
               sheet_id: str, sheet_tab: str) -> dict:
    """Append the run to the usage sheet, tagged with the authenticated user. Never
    raises — a logging failure must not fail the grade response."""
    from sheets_log import has_service_account
    if not has_service_account():
        return {"ok": False, "msg": "No service account configured; not logged."}
    try:
        from sheets_log import DEFAULT_SPREADSHEET_ID, append_cost_row, cost_row
        row = cost_row(
            student_file=f"[{user}] {student_file}",
            provider=provider, model=cost.model or "",
            student_class=student_class or None, subject=subject or None,
            score=report.total_score, max_score=report.max_total,
            input_tokens=cost.billed_input_tokens, output_tokens=cost.output_tokens,
            mathpix_pages=cost.mathpix_pages, llm_cost_usd=cost.llm_cost_usd,
            mathpix_cost_usd=cost.mathpix_cost_usd, total_usd=cost.total_usd,
            usd_to_inr=cost.usd_to_inr, total_inr=cost.total_inr,
        )
        append_cost_row(row, spreadsheet_id=sheet_id or DEFAULT_SPREADSHEET_ID,
                        worksheet=sheet_tab or None)
        return {"ok": True, "msg": "Logged to Google Sheet."}
    except Exception as e:  # noqa: BLE001 — surface as status, don't fail the grade
        return {"ok": False, "msg": f"Sheet log failed: {e}"}


@app.get("/ai/health")
def health():
    """Liveness + a hint at whether the server is configured (no secret values leaked)."""
    return {
        "ok": True,
        "tokens_configured": bool(os.environ.get("PROXY_TOKENS")),
        "has_google_key": bool(os.environ.get("GOOGLE_API_KEY")),
        "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "has_mathpix_key": bool(os.environ.get("MATHPIX_APP_KEY")),
    }


@app.post("/ai/grade-sheet")
async def grade_sheet_ep(
    qp: list[UploadFile] = File(..., description="Question-paper page PNGs"),
    sa: list[UploadFile] = File(..., description="Student answer-sheet page PNGs"),
    ak: list[UploadFile] = File(default=[], description="Optional answer-key page PNGs"),
    ak_text: str = Form(""),
    marks_scheme: str = Form(""),          # JSON of MarksScheme, or "" for none
    provider: str = Form("gemini"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    student_file: str = Form("sheet"),
    sheet_id: str = Form(""),
    sheet_tab: str = Form(""),
    authorization: str | None = Header(default=None),
):
    """Grade ONE prepared sheet. The client renders the PNGs and builds the evaluated PDF;
    this endpoint only runs the secret-using OCR + LLM grade and returns the report plus
    the OCR anchors the client needs to place its marks. Keys come from THIS server's
    environment only — the client never sends them."""
    user = _require_user(authorization)

    qp_pngs = [await f.read() for f in qp]
    sa_pngs = [await f.read() for f in sa]
    ak_pngs = [await f.read() for f in ak] or None
    if not qp_pngs or not sa_pngs:
        raise HTTPException(400, "Need at least one question-paper page and one answer page.")

    ms = None
    if marks_scheme.strip():
        try:
            ms = MarksScheme.model_validate_json(marks_scheme)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Bad marks_scheme JSON: {e}")

    # api_key='' → the key is taken from this server's env (GOOGLE_API_KEY / ANTHROPIC_API_KEY).
    cfg = cfg_from_request(provider, model, "", subject)
    use_mathpix = bool(os.environ.get("MATHPIX_APP_KEY"))

    try:
        res = grade_prepared(
            qp_pngs=qp_pngs, sa_pngs=sa_pngs, ak_pngs=ak_pngs, ak_text=ak_text or None,
            marks_scheme=ms, cfg=cfg, student_class=student_class or None,
            subject=subject or None, use_mathpix=use_mathpix,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Grading failed: {e}")

    report, cost = res["report"], res["cost"]
    log = _log_usage(user=user, student_file=student_file, cost=cost, report=report,
                     provider=cfg.provider, student_class=student_class, subject=subject,
                     sheet_id=sheet_id, sheet_tab=sheet_tab)

    cost_d = dataclasses.asdict(cost)
    cost_d["billed_input_tokens"] = cost.billed_input_tokens
    return {
        "report": report.model_dump(),
        "ocr_pages": [pageocr_to_dict(p) for p in (res["ocr_pages"] or [])],
        "cost": cost_d,
        "log": log,
        "user": user,
    }


@app.post("/ai/generate-rubric")
async def rubric_ep(
    qp: list[UploadFile] = File(...),
    provider: str = Form("gemini"),
    model: str = Form(""),
    student_class: str = Form(""),
    subject: str = Form(""),
    authorization: str | None = Header(default=None),
):
    _require_user(authorization)
    qp_pngs = [await f.read() for f in qp]
    if not qp_pngs:
        raise HTTPException(400, "No question-paper pages supplied.")
    cfg = cfg_from_request(provider, model, "", subject)
    try:
        rubric = generate_rubric(qp_pngs, cfg, student_class or None, subject or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Rubric generation failed: {e}")
    if not rubric or not rubric.strip():
        raise HTTPException(502, "The model returned an empty rubric — try again.")
    return {"rubric": rubric}
