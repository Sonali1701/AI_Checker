"""Gemini-backed grader. Mirrors grader.py's public interface so app.py can swap providers."""
from __future__ import annotations

import os

from google import genai
from google.genai import types

# Reuse the Pydantic schema + system prompts from the Claude grader so both backends
# produce structurally-identical GradeReport objects.
from grader import (
    GradeReport,
    MarksScheme,
    MARKS_SCHEME_PROMPT,
    RUBRIC_GEN_PROMPT,
    _downscale_for_model,
    _reconcile_report,
    build_system_prompt,
    exam_context_block,
    marks_scheme_block,
)
from mathpix import PageOCR, overlay_anchors_on_png

DEFAULT_MODEL = "gemini-2.0-flash-exp"

# --- Cost & consistency controls for the grading call ------------------------------
# On Gemini 2.5 the billed "output" is mostly the model's INTERNAL THINKING tokens, and
# at Pro's $10/1M that is ~80-90% of the per-sheet cost. Cap it: GEMINI_THINKING_BUDGET
# bounds thinking tokens (still leaves plenty of reasoning), which is the single biggest
# lever on Pro cost. SEED makes greedy decoding more reproducible run-to-run.
#   - Raise the budget if you see grading quality/consistency drop on hard papers.
#   - Lower it (e.g. 2048) to save more. Set 0 to disable the cap (full dynamic thinking).
_THINKING_BUDGET = int(os.environ.get("GEMINI_THINKING_BUDGET", "4096"))
_SEED = int(os.environ.get("GEMINI_SEED", "42"))


def _thinking_config():
    """A ThinkingConfig that caps thinking tokens, or None when disabled / unsupported by
    the installed SDK (so the code is safe on any google-genai version)."""
    if _THINKING_BUDGET <= 0:
        return None
    try:
        if "thinking_budget" in types.ThinkingConfig.model_fields:
            return types.ThinkingConfig(thinking_budget=_THINKING_BUDGET)
    except Exception:
        pass
    return None


def _make_client(api_key: str | None, use_vertex: bool,
                 project: str | None = None, location: str | None = None) -> genai.Client:
    """Three modes:
    1. Developer API (default): vertexai=False, api_key required
    2. Vertex AI Express: vertexai=True, api_key required (key starts with 'AQ.')
    3. Vertex AI with ADC: vertexai=True, project+location, no api_key
    """
    if use_vertex:
        key = api_key or os.environ.get("GOOGLE_API_KEY")
        if key:
            # Vertex Express Mode — API key auth, no project/location needed
            return genai.Client(vertexai=True, api_key=key)
        # ADC mode — needs project + location
        return genai.Client(
            vertexai=True,
            project=project or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))


def _image_part(png_bytes: bytes) -> types.Part:
    return types.Part.from_bytes(data=png_bytes, mime_type="image/png")


def _text_part(text: str) -> types.Part:
    return types.Part.from_text(text=text)


def orientation_probe(
    page_pngs: list[bytes],
    model: str = "gemini-2.5-flash",
    api_key: str | None = None,
    use_vertex: bool = False,
    project: str | None = None,
    location: str | None = None,
) -> list[int]:
    """Ask a cheap Flash call, in ONE batched request, how to straighten each page.

    Returns the counter-clockwise angle (0/90/180/270) to apply to each page so the
    writing is upright — same convention as orient.detect_rotation / PIL Image.rotate.
    Pages are downscaled hard first, so this costs a fraction of a paisa per sheet.
    Never raises: on any failure it returns all-zeros (leave pages as-is)."""
    import json
    import re

    if not page_pngs:
        return []
    try:
        client = _make_client(api_key, use_vertex, project, location)
        parts = [_text_part(
            "Below are page images of a handwritten exam, in order. For EACH image decide how "
            "many degrees of COUNTER-CLOCKWISE rotation make the writing upright and reading "
            "left-to-right. Allowed values: 0 (already upright), 90, 180, 270. "
            "Return ONLY a JSON array of integers, one per image, in order. "
            "Example for three images: [0, 90, 0]"
        )]
        for p in page_pngs:
            parts.append(_image_part(_downscale_for_model(p, max_edge=384)))
        resp = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=200),
        )
        m = re.search(r"\[[^\]]*\]", resp.text or "")
        arr = json.loads(m.group(0)) if m else []
        out = []
        for v in arr:
            try:
                iv = int(v) % 360
            except (TypeError, ValueError):
                iv = 0
            out.append(iv if iv in (0, 90, 180, 270) else 0)
    except Exception:
        out = []
    return (out + [0] * len(page_pngs))[: len(page_pngs)]


def generate_rubric(
    question_paper_pngs: list[bytes],
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    use_vertex: bool = False,
    project: str | None = None,
    location: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
) -> str:
    client = _make_client(api_key, use_vertex, project, location)

    parts: list[types.Part] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        parts.append(_text_part(ctx))
    parts.append(_text_part("Question paper:"))
    for png in question_paper_pngs:
        parts.append(_image_part(png))
    parts.append(_text_part("Produce the marking rubric for this paper as described in the system instruction."))

    resp = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=RUBRIC_GEN_PROMPT,
            temperature=0,
            max_output_tokens=8000,
        ),
    )
    return resp.text


def extract_marks_scheme(
    question_paper_pngs: list[bytes],
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    use_vertex: bool = False,
    project: str | None = None,
    location: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
) -> MarksScheme:
    """Read the authoritative per-part maximum-marks scheme off the question paper."""
    client = _make_client(api_key, use_vertex, project, location)

    parts: list[types.Part] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        parts.append(_text_part(ctx))
    parts.append(_text_part("Question paper:"))
    for png in question_paper_pngs:
        parts.append(_image_part(png))
    parts.append(_text_part("Extract the official maximum-marks scheme as described."))

    resp = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=MARKS_SCHEME_PROMPT,
            temperature=0,
            response_mime_type="application/json",
            response_schema=MarksScheme,
            max_output_tokens=8000,
        ),
    )
    parsed = getattr(resp, "parsed", None)
    if not isinstance(parsed, MarksScheme):
        parsed = MarksScheme.model_validate_json(resp.text)
    return parsed


def grade_answer_sheet(
    question_paper_pngs: list[bytes],
    student_pngs: list[bytes],
    answer_key_pngs: list[bytes] | None = None,
    answer_key_text: str | None = None,
    ocr_transcript: str | None = None,
    ocr_pages: list[PageOCR] | None = None,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    use_vertex: bool = False,
    project: str | None = None,
    location: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
    marks_scheme: MarksScheme | None = None,
    usage_out: dict | None = None,
) -> GradeReport:
    if not answer_key_pngs and not answer_key_text:
        raise ValueError("Provide either answer_key_pngs or answer_key_text.")

    client = _make_client(api_key, use_vertex, project, location)

    parts: list[types.Part] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        parts.append(_text_part(ctx))
    parts.append(_text_part("=== QUESTION PAPER ==="))
    for png in question_paper_pngs:
        parts.append(_image_part(_downscale_for_model(png)))

    parts.append(_text_part("=== ANSWER KEY / MARKING SCHEME ==="))
    if answer_key_text:
        parts.append(_text_part(answer_key_text))
    for png in (answer_key_pngs or []):
        parts.append(_image_part(_downscale_for_model(png)))

    msb = marks_scheme_block(marks_scheme)
    if msb:
        parts.append(_text_part(msb))

    overlaid_pngs: list[bytes] = []
    if ocr_pages:
        ocr_by_page = {p.page: p for p in ocr_pages}
        for i, png in enumerate(student_pngs, start=1):
            po = ocr_by_page.get(i)
            overlaid_pngs.append(overlay_anchors_on_png(png, po) if po else png)
    else:
        overlaid_pngs = list(student_pngs)

    parts.append(_text_part(f"=== STUDENT'S ANSWER SHEET ({len(overlaid_pngs)} pages, 1-indexed) ==="))
    if ocr_pages:
        parts.append(_text_part(
            "Each line on these page images is overlaid with its OCR line_id (e.g. [P3L7]) in BLUE. "
            "Question/section header lines are labelled in RED — never anchor a step there. "
            "Diagram regions are outlined in ORANGE with an anchor like [P7D1] — use those when the "
            "step's evidence is a figure, table, or hand-drawn diagram (no text). "
            "Pick step_line_id by LOOKING at the page — match each rubric step to the visible line "
            "(or diagram region) where the student actually demonstrated that step."
        ))
    for i, png in enumerate(overlaid_pngs, start=1):
        parts.append(_text_part(f"--- Page {i} ---"))
        parts.append(_image_part(png))

    if ocr_transcript:
        parts.append(_text_part(
            "=== OCR TRANSCRIPT OF STUDENT'S ANSWER SHEET (lines labelled [PnLk], diagrams [PnDk]) ===\n"
            "This transcript mirrors the labels overlaid on the page images. Lines tagged (HEADER) "
            "are question/section labels — never anchor step ticks to them. Lines tagged "
            "(DIAGRAM REGION) are blank bands where a figure/table lives — use them when the step "
            "is satisfied by a drawing.\n\n" + ocr_transcript
        ))

    parts.append(_text_part(
        "Now grade the student's answers against the answer key. Return a complete GradeReport: "
        "every sub-question in `questions`, every top-level question in `section_totals`, total_score "
        "must equal the sum of sub-question scores."
        + (" Populate anchor_line_id and target_line_id from the OCR transcript wherever possible." if ocr_transcript else "")
    ))

    # Use streaming: the new per-sub-part rubric pushes response size up, and
    # the non-streaming endpoint drops the connection mid-body on long replies
    # (RemoteProtocolError / "incomplete chunked read"). Streaming reads chunks
    # as they arrive and survives those long generations.
    config_kwargs = dict(
        system_instruction=build_system_prompt(subject),
        temperature=0,
        seed=_SEED,                       # reproducible greedy decoding (consistency)
        response_mime_type="application/json",
        response_schema=GradeReport,
        max_output_tokens=64000,
    )
    _tc = _thinking_config()              # cap thinking tokens — the main Pro cost lever
    if _tc is not None:
        config_kwargs["thinking_config"] = _tc
    config = types.GenerateContentConfig(**config_kwargs)

    text_chunks: list[str] = []
    finish_reason = None
    last_chunk = None
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=config,
    ):
        last_chunk = chunk
        if chunk.text:
            text_chunks.append(chunk.text)
        try:
            fr = chunk.candidates[0].finish_reason
            if fr is not None:
                finish_reason = fr
        except (AttributeError, IndexError):
            pass

    if finish_reason and str(finish_reason).endswith("MAX_TOKENS"):
        raise RuntimeError(
            "Gemini hit the output token limit before finishing the GradeReport. "
            "The answer sheet is too long for one call at this rubric verbosity. "
            "Try (a) switching to gemini-2.5-flash for higher throughput, "
            "(b) splitting the sheet into smaller batches, or "
            "(c) using a tighter rubric with fewer criteria per question."
        )

    full_text = "".join(text_chunks)
    if not full_text.strip():
        raise RuntimeError(
            f"Gemini returned no text (finish_reason={finish_reason}). "
            "This is often a safety filter or empty response. Try the other provider or a different model."
        )

    # Prefer the SDK's pre-parsed object on the last chunk if available; fall
    # back to validating the concatenated JSON ourselves.
    if usage_out is not None:
        um = getattr(last_chunk, "usage_metadata", None) if last_chunk is not None else None
        prompt = int(getattr(um, "prompt_token_count", 0) or 0) if um else 0
        cached = int(getattr(um, "cached_content_token_count", 0) or 0) if um else 0
        cand = int(getattr(um, "candidates_token_count", 0) or 0) if um else 0
        thoughts = int(getattr(um, "thoughts_token_count", 0) or 0) if um else 0
        usage_out.update({
            "provider": "gemini",
            "model": model,
            # Gemini's prompt_token_count INCLUDES the cached portion — split it out.
            "input_tokens": max(0, prompt - cached),
            "cached_input_tokens": cached,
            "cache_write_tokens": 0,
            # Thinking tokens are billed as output.
            "output_tokens": cand + thoughts,
        })

    parsed = getattr(last_chunk, "parsed", None) if last_chunk is not None else None
    if not isinstance(parsed, GradeReport):
        parsed = GradeReport.model_validate_json(full_text)
    return _reconcile_report(parsed, marks_scheme)
