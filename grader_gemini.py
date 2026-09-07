"""Gemini grading calls routed exclusively through the PW shared proxy."""
from __future__ import annotations

import base64
import json
import os
import re

import pw_access
from costs import record_usage_event
from grader import (
    AUDIT_SYSTEM_PROMPT,
    AuditItem,
    AuditSheet,
    GradeReport,
    MarksScheme,
    NO_QUESTION_PAPER_BLOCK,
    OutputLimitExceeded,
    ObjectiveChoiceObservation,
    ObjectiveChoiceSheet,
    REPAIR_SYSTEM_PROMPT,
    RepairItem,
    RepairSheet,
    audit_response_schema,
    build_audit_prompt,
    build_marks_scheme_prompt,
    build_repair_prompt,
    parse_audit,
    paper_text_block,
    parse_marks_scheme,
    parse_repair,
    parse_strict_marks_scheme,
    _downscale_for_model,
    _reconcile_report,
    build_rubric_prompt,
    compress_page_for_model,
    build_system_prompt,
    exam_context_block,
    marks_scheme_block,
    parse_grade_report,
    scheme_matches_total,
)
from mathpix import PageOCR, overlay_anchors_on_png

DEFAULT_MODEL = "gemini-2.5-flash"
_THINKING_BUDGET = int(os.environ.get("GEMINI_THINKING_BUDGET", "4096"))
_SEED = int(os.environ.get("GEMINI_SEED", "42"))


def _text_part(text: str) -> dict:
    return {"text": text}


def _image_part(image_bytes: bytes, mime_type: str = "image/png") -> dict:
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
        }
    }


def _request(
    parts: list[dict],
    *,
    system_instruction: str = "",
    max_output_tokens: int,
    response_schema: type | dict | None = None,
    thinking_budget: int | None = None,
) -> dict:
    generation_config: dict = {
        "temperature": 0,
        "maxOutputTokens": max_output_tokens,
    }
    if response_schema is not None:
        generation_config.update(
            {
                "responseMimeType": "application/json",
                "responseJsonSchema": (
                    response_schema if isinstance(response_schema, dict)
                    else response_schema.model_json_schema()
                ),
            }
        )
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {
            "thinkingBudget": max(0, int(thinking_budget))
        }
    request = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }
    if system_instruction:
        request["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    return request


def _response_text(response: dict) -> str:
    try:
        parts = response["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        # Surface what actually came back (block reason, error body, finish reason)
        # instead of a blind "no output" — essential for diagnosing proxy issues.
        detail = ""
        if isinstance(response, dict):
            feedback = response.get("promptFeedback") or response.get("prompt_feedback")
            err = response.get("error")
            finish = _finish_reason(response)
            bits = [
                f"blockReason={feedback.get('blockReason')}" if isinstance(feedback, dict) and feedback.get("blockReason") else "",
                f"finishReason={finish}" if finish else "",
                f"error={err}" if err else "",
            ]
            detail = "; ".join(b for b in bits if b) or f"response snippet: {str(response)[:400]}"
        raise RuntimeError(f"Gemini returned no model output ({detail}).") from exc
    return "".join(str(part.get("text") or "") for part in parts).strip()


def _finish_reason(response: dict) -> str:
    try:
        candidate = response["candidates"][0]
        return str(candidate.get("finishReason") or candidate.get("finish_reason") or "")
    except (KeyError, IndexError, TypeError):
        return ""


def _usage(response: dict, model: str) -> dict:
    meta = response.get("usageMetadata") or response.get("usage_metadata") or {}

    def value(camel: str, snake: str) -> int:
        return int(meta.get(camel) or meta.get(snake) or 0)

    prompt = value("promptTokenCount", "prompt_token_count")
    cached = value("cachedContentTokenCount", "cached_content_token_count")
    candidates = value("candidatesTokenCount", "candidates_token_count")
    thoughts = value("thoughtsTokenCount", "thoughts_token_count")
    return {
        "provider": "gemini",
        "model": model,
        "input_tokens": max(0, prompt - cached),
        "cached_input_tokens": cached,
        "cache_write_tokens": 0,
        "output_tokens": candidates + thoughts,
    }


def _generate(
    google_token: str,
    *,
    model: str,
    request: dict,
    filename: str,
    count: int,
    task_id: str,
) -> dict:
    if not google_token or not task_id:
        raise PermissionError("A signed-in PW user and task_id are required.")
    return pw_access.gemini_generate(
        google_token,
        model=model,
        request=request,
        filename=filename,
        input_unit="No. of pages",
        count=count,
        task_id=task_id,
    )


def orientation_probe(
    page_pngs: list[bytes],
    model: str = "gemini-2.5-flash",
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    debug: dict | None = None,
    usage_out: dict | None = None,
) -> list[int]:
    """Return the CCW rotation for each page; degrade to no rotation on failure."""
    if not page_pngs:
        return []
    raw = ""
    err = ""
    out: list[int] = []
    try:
        parts = [_text_part(
            "Each image is one page of a HANDWRITTEN exam. Some pages were photographed "
            "SIDEWAYS or upside-down. For EACH image, in order, output the COUNTER-CLOCKWISE "
            "rotation (0, 90, 180, or 270 degrees) that makes the handwriting upright.\n"
            "Judge from the GEOMETRY of the writing, not from whether you can read it. Look at "
            "which way the TOPS of the letters point:\n"
            "- Lines run left-to-right, letter-tops point UP (already upright) -> 0\n"
            "- Writing runs top-to-bottom, letter-tops point RIGHT -> 90\n"
            "- Writing is upside-down, letter-tops point DOWN -> 180\n"
            "- Writing runs bottom-to-top, letter-tops point LEFT -> 270\n"
            "Reply with ONLY a JSON array, one integer per image, e.g. [90, 270, 0]."
        )]
        parts.extend(_image_part(_downscale_for_model(p, max_edge=900)) for p in page_pngs)
        response = _generate(
            google_token,
            model=model,
            request=_request(parts, max_output_tokens=2000, thinking_budget=0),
            filename=filename,
            count=len(page_pngs),
            task_id=task_id,
        )
        record_usage_event(usage_out, _usage(response, model), stage="orientation")
        raw = _response_text(response)
        match = re.search(r"\[[^\]]*\]", raw) or re.search(r"\[[^\]]*$", raw)
        segment = match.group(0) if match else raw
        for value in re.findall(r"-?\d+", segment):
            angle = int(value) % 360
            out.append(angle if angle in (0, 90, 180, 270) else 0)
    except Exception as exc:  # orientation must never block grading
        err = f"{type(exc).__name__}: {exc}"
    if debug is not None:
        debug.update({"raw": raw[:200], "error": err, "model": model})
    return (out + [0] * len(page_pngs))[: len(page_pngs)]


def generate_rubric(
    question_paper_pngs: list[bytes],
    model: str = DEFAULT_MODEL,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    student_class: str | None = None,
    subject: str | None = None,
    exam_type: str | None = None,
) -> str:
    parts: list[dict] = []
    context = exam_context_block(student_class, subject, exam_type)
    if context:
        parts.append(_text_part(context))
    parts.append(_text_part("Question paper:"))
    parts.extend(_image_part(png) for png in question_paper_pngs)
    parts.append(_text_part("Produce the marking rubric for this paper as described in the system instruction."))
    response = _generate(
        google_token,
        model=model,
        request=_request(
            parts,
            system_instruction=build_rubric_prompt(subject, student_class, exam_type),
            max_output_tokens=16000,
            thinking_budget=1024,
        ),
        filename=filename,
        count=len(question_paper_pngs),
        task_id=task_id,
    )
    return _response_text(response)


def extract_marks_scheme(
    question_paper_pngs: list[bytes],
    model: str = DEFAULT_MODEL,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    student_class: str | None = None,
    subject: str | None = None,
    exam_type: str | None = None,
    total_marks: float | None = None,
    paper_text: str = "",
    require_complete: bool = False,
) -> MarksScheme:
    parts: list[dict] = []
    context = exam_context_block(student_class, subject, exam_type, total_marks)
    if context:
        parts.append(_text_part(context))
    block = paper_text_block(paper_text)
    if block:
        parts.append(_text_part(block))
    parts.append(_text_part("Question paper:"))
    parts.extend(_image_part(png) for png in question_paper_pngs)
    parts.append(_text_part("Extract the official maximum-marks scheme as described."))
    # 2.5-pro always spends thinking tokens and they count against maxOutputTokens —
    # cap thinking and leave real headroom, or the visible answer comes back empty.
    response = _generate(
        google_token,
        model=model,
        request=_request(
            parts,
            system_instruction=build_marks_scheme_prompt(subject, student_class, exam_type),
            max_output_tokens=16000,
            response_schema=MarksScheme,
            thinking_budget=1024,
        ),
        filename=filename,
        count=len(question_paper_pngs),
        task_id=task_id,
    )
    raw = _response_text(response)
    if require_complete:
        if _finish_reason(response).strip().upper() != "STOP":
            raise OutputLimitExceeded(
                "Gemini did not finish the marks graph cleanly; it cannot be attested.")
        try:
            return parse_strict_marks_scheme(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(
                "Gemini's marks graph was not complete strict JSON.") from exc
    return parse_marks_scheme(raw)


def repair_report_totals(
    report: GradeReport,
    model: str = DEFAULT_MODEL,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    total_marks: float,
    marks_scheme: MarksScheme | None = None,
    qp_text: str = "",
    usage_out: dict | None = None,
) -> list[RepairItem]:
    """Text-only follow-up that corrects a report's per-question maxima to the
    official paper total. Cheap: no images, small output."""
    prompt = build_repair_prompt(report, total_marks, marks_scheme, qp_text)
    response = _generate(
        google_token,
        model=model,
        request=_request(
            [_text_part(prompt)],
            system_instruction=REPAIR_SYSTEM_PROMPT,
            max_output_tokens=8000,
            response_schema=RepairSheet,
            thinking_budget=2048,
        ),
        filename=filename,
        count=1,
        task_id=task_id,
    )
    record_usage_event(usage_out, _usage(response, model), stage="repair")
    return parse_repair(_response_text(response))


def audit_deductions(
    report: GradeReport,
    model: str = DEFAULT_MODEL,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    answer_key_text: str = "",
    ocr_transcript: str = "",
    question_paper_text: str = "",
    student_evidence_pngs: list[bytes] | None = None,
    usage_out: dict | None = None,
    include_awards: bool = False,
    selected_qids: set[str] | None = None,
    max_output_tokens: int = 32000,
    repair_rows: dict[int, str] | None = None,
) -> list[AuditItem]:
    """Text-only second-examiner pass over the deductions (see AUDIT_SYSTEM_PROMPT)."""
    prompt = build_audit_prompt(report, answer_key_text, ocr_transcript,
                                question_paper_text=question_paper_text,
                                include_awards=include_awards,
                                selected_qids=selected_qids,
                                repair_rows=repair_rows)
    parts: list[dict] = [_text_part(prompt)]
    if student_evidence_pngs:
        parts.append(_text_part(
            "=== STUDENT ANSWER PAGE IMAGE(S) ===\n"
            "Inspect the live handwriting as well as the transcript. The image "
            "decides whether text is crossed out and whether an example is present."
        ))
        parts.extend(_image_part(png) for png in student_evidence_pngs)
    response = _generate(
        google_token,
        model=model,
        request=_request(
            parts,
            system_instruction=AUDIT_SYSTEM_PROMPT,
            # Thinking is billed against this cap and cannot be limited (the proxy
            # strips thinkingConfig), so leave room or the JSON comes back truncated.
            max_output_tokens=max(512, min(32000, int(max_output_tokens))),
            response_schema=audit_response_schema(),
            thinking_budget=2048,
        ),
        filename=filename,
        count=max(1, len(student_evidence_pngs or [])),
        task_id=task_id,
    )
    record_usage_event(usage_out, _usage(response, model), stage="verify")
    return parse_audit(_response_text(response))


def read_objective_choices(
    student_pngs: list[bytes],
    qids: list[str],
    model: str = DEFAULT_MODEL,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    usage_out: dict | None = None,
) -> list[ObjectiveChoiceObservation]:
    """Independent vision-only read of active MCQ choices; never receives the key."""
    wanted = [str(qid).strip() for qid in qids if str(qid).strip()]
    prompt = (
        "Read the student's FINAL ACTIVE selected option for every requested question. "
        "Ignore an option that is crossed out or overwritten; a tick/check box beside "
        "an option means that option was selected. Do not grade and do not infer from "
        "subject knowledge. Return exactly one item per qid, chosen_option A-D only when "
        "visually clear; otherwise return an empty option and clear=false. Estimate the "
        "page and vertical fraction of the visible answer row.\nRequested qids: "
        + ", ".join(wanted)
    )
    parts: list[dict] = [_text_part(prompt)]
    parts.extend(_image_part(png) for png in student_pngs)
    def dispatch(*, retry: bool = False) -> dict:
        request_parts = list(parts)
        if retry:
            request_parts[0] = _text_part(
                prompt
                + "\nYour previous response was not valid JSON. Return only the schema "
                  "object with an items array; do not add prose or markdown."
            )
        response = _generate(
            google_token,
            model=model,
            request=_request(
                request_parts,
                system_instruction="You are an independent visual transcription examiner.",
                max_output_tokens=max(1200, 160 * len(wanted)),
                response_schema=ObjectiveChoiceSheet,
                thinking_budget=0 if retry else 1024,
            ),
            filename=filename,
            count=max(1, len(student_pngs)),
            task_id=task_id,
        )
        record_usage_event(
            usage_out, _usage(response, model),
            stage="objective_verify_retry" if retry else "objective_verify",
        )
        return response

    def parse(response: dict) -> list[ObjectiveChoiceObservation]:
        raw = json.loads(_response_text(response))
        if isinstance(raw, list):
            raw = {"items": raw}
        for item in list(raw.get("items") or []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            if "y_fraction" not in item and "vertical_fraction" in item:
                item["y_fraction"] = item.pop("vertical_fraction")
            if "clear" not in item:
                item["clear"] = str(item.get("chosen_option") or "").strip().upper() in {
                    "A", "B", "C", "D"
                }
        return ObjectiveChoiceSheet.model_validate(raw).items

    try:
        return parse(dispatch())
    except (json.JSONDecodeError, ValueError):
        # A malformed structured reply is billable but contains no trustworthy
        # certificate. Retry once under the same job/task id; never guess or repair
        # selected options from broken JSON.
        return parse(dispatch(retry=True))


def grade_answer_sheet(
    question_paper_pngs: list[bytes],
    student_pngs: list[bytes],
    answer_key_pngs: list[bytes] | None = None,
    answer_key_text: str | None = None,
    ocr_transcript: str | None = None,
    ocr_pages: list[PageOCR] | None = None,
    model: str = DEFAULT_MODEL,
    *,
    google_token: str,
    task_id: str,
    filename: str = "",
    student_class: str | None = None,
    subject: str | None = None,
    marks_scheme: MarksScheme | None = None,
    usage_out: dict | None = None,
    thinking_budget: int | None = None,
    exam_type: str | None = None,
    total_marks: float | None = None,
    qp_text: str = "",
    cache_ttl: str = "",
    max_output_tokens: int = 64000,
) -> GradeReport:
    # cache_ttl is a Claude prompt-caching control; Gemini's context cache is a
    # different mechanism this route does not use. Accepted and ignored so the
    # pipeline can pass one `common` kwargs dict to either provider.
    del cache_ttl
    if not answer_key_pngs and not answer_key_text:
        raise ValueError("Provide either answer_key_pngs or answer_key_text.")

    parts: list[dict] = []
    context = exam_context_block(student_class, subject, exam_type, total_marks)
    if context:
        parts.append(_text_part(context))
    qp_block = paper_text_block(qp_text)
    if qp_block:
        parts.append(_text_part(qp_block))
    if question_paper_pngs:
        parts.append(_text_part("=== QUESTION PAPER ==="))
        parts.extend(_image_part(_downscale_for_model(png)) for png in question_paper_pngs)
    elif not qp_block:
        parts.append(_text_part(NO_QUESTION_PAPER_BLOCK))
    parts.append(_text_part("=== ANSWER KEY / MARKING SCHEME ==="))
    if answer_key_text:
        parts.append(_text_part(answer_key_text))
    parts.extend(_image_part(_downscale_for_model(png)) for png in (answer_key_pngs or []))
    scheme_block = marks_scheme_block(marks_scheme, total_marks)
    if scheme_block:
        parts.append(_text_part(scheme_block))

    if ocr_pages:
        by_page = {page.page: page for page in ocr_pages}
        student_images = [
            overlay_anchors_on_png(png, by_page[i]) if i in by_page else png
            for i, png in enumerate(student_pngs, start=1)
        ]
    else:
        student_images = list(student_pngs)
    parts.append(_text_part(f"=== STUDENT'S ANSWER SHEET ({len(student_images)} pages, 1-indexed) ==="))
    if ocr_pages:
        parts.append(_text_part(
            "Each visible line has a blue OCR line_id; headers are red and diagram regions are orange. "
            "Match each rubric step to its actual evidence and never anchor a step to a red header."
        ))
    for index, png in enumerate(student_images, start=1):
        page_bytes, mime_type = compress_page_for_model(png)
        parts.extend((_text_part(f"--- Page {index} ---"), _image_part(page_bytes, mime_type)))
    if ocr_transcript:
        parts.append(_text_part(
            "=== OCR TRANSCRIPT OF STUDENT'S ANSWER SHEET ===\n" + ocr_transcript
        ))
    parts.append(_text_part(
        "Grade the student's answers against the answer key. Return a complete GradeReport: "
        "every sub-question in questions, every top-level question in section_totals, and "
        "total_score equal to the sum of all sub-question scores."
    ))

    budget = _THINKING_BUDGET if thinking_budget is None else thinking_budget
    request = _request(
        parts,
        system_instruction=build_system_prompt(subject, student_class, exam_type),
        max_output_tokens=max(512, min(64000, int(max_output_tokens))),
        response_schema=GradeReport,
        thinking_budget=budget if budget > 0 else None,
    )
    request["generationConfig"]["seed"] = _SEED
    response = _generate(
        google_token,
        model=model,
        request=request,
        filename=filename,
        count=len(student_pngs),
        task_id=task_id,
    )
    # Account for a truncated/bad response before any fallback decision.
    record_usage_event(usage_out, _usage(response, model), stage="primary")
    finish_reason = _finish_reason(response)
    if finish_reason.upper().endswith("MAX_TOKENS"):
        raise OutputLimitExceeded(
            "Gemini hit the output token limit before finishing the GradeReport."
        )
    text = _response_text(response)
    # With internal choice the scheme lists alternatives a student may skip — enforcing
    # it verbatim would add phantom 0-mark entries and inflate the denominator.
    authoritative = marks_scheme if scheme_matches_total(marks_scheme, total_marks) else None
    return _reconcile_report(parse_grade_report(text), authoritative, total_marks)
