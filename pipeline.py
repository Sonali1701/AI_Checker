"""Provider-agnostic grading orchestration.

This is the framework-free core the FastAPI server (and anything else) calls. It wraps
the Claude / Gemini graders behind one ProviderConfig so callers never branch on provider.
The Streamlit UI's old `_dispatch_*` helpers live here now.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from grader import (
    MarksItem, MarksScheme, marks_scheme_from_pdf, pdf_or_image_to_pngs,
)
from grader import extract_marks_scheme as claude_extract_marks
from grader import generate_rubric as claude_generate_rubric
from grader import grade_answer_sheet as claude_grade
from grader_gemini import extract_marks_scheme as gemini_extract_marks
from grader_gemini import generate_rubric as gemini_generate_rubric
from grader_gemini import grade_answer_sheet as gemini_grade
from mathpix import build_transcript, ocr_all_pages, synthesize_diagram_regions
from pdf_renderer import build_evaluated_pdf
from costs import compute_cost, DEFAULT_USD_TO_INR

CLAUDE = "claude"
GEMINI = "gemini"


@dataclass
class ProviderConfig:
    provider: str = GEMINI                  # "claude" | "gemini"
    model: str = "gemini-2.5-flash"
    api_key: str | None = None
    gemini_vertex: bool = False
    project: str | None = None
    location: str | None = None

    @property
    def is_claude(self) -> bool:
        return self.provider == CLAUDE

    def resolved_key(self) -> str | None:
        """The API key, falling back to the provider's env var."""
        if self.api_key:
            return self.api_key
        return os.environ.get("ANTHROPIC_API_KEY" if self.is_claude else "GOOGLE_API_KEY") or None


def generate_rubric(qp_pngs, cfg: ProviderConfig, student_class=None, subject=None) -> str:
    if cfg.is_claude:
        return claude_generate_rubric(qp_pngs, model=cfg.model, api_key=cfg.resolved_key(),
                                      student_class=student_class, subject=subject)
    return gemini_generate_rubric(qp_pngs, model=cfg.model, api_key=cfg.resolved_key(),
                                  use_vertex=cfg.gemini_vertex, project=cfg.project,
                                  location=cfg.location, student_class=student_class, subject=subject)


def _extract_marks(qp_pngs, cfg: ProviderConfig, student_class=None, subject=None) -> MarksScheme:
    if cfg.is_claude:
        return claude_extract_marks(qp_pngs, model=cfg.model, api_key=cfg.resolved_key(),
                                    student_class=student_class, subject=subject)
    return gemini_extract_marks(qp_pngs, model=cfg.model, api_key=cfg.resolved_key(),
                                use_vertex=cfg.gemini_vertex, project=cfg.project,
                                location=cfg.location, student_class=student_class, subject=subject)


def detect_marks_scheme(qp_bytes: bytes, filename: str, cfg: ProviderConfig | None = None,
                        student_class=None, subject=None) -> tuple[MarksScheme | None, str]:
    """Regex-first (free, deterministic) marks detection off the PDF text layer;
    LLM fallback only for scanned papers. Returns (scheme, method)."""
    scheme = marks_scheme_from_pdf(qp_bytes, filename)
    if scheme is not None:
        return scheme, "regex"
    if cfg is None:
        return None, "none"
    qp_pngs = pdf_or_image_to_pngs(qp_bytes, filename)
    return _extract_marks(qp_pngs, cfg, student_class, subject), "ai"


def marks_scheme_from_items(items: list[dict]) -> MarksScheme | None:
    """Build a MarksScheme from the editable table the UI sends (qid/max/part rows)."""
    m_items = []
    for r in items or []:
        qid = str(r.get("qid", "")).strip()
        if not qid:
            continue
        try:
            m_items.append(MarksItem(qid=qid, max=float(r.get("max", 0) or 0),
                                     description=str(r.get("part") or "")))
        except (TypeError, ValueError):
            continue
    if not m_items:
        return None
    return MarksScheme(total=sum(i.max for i in m_items), items=m_items)


def grade_sheet(*, qp_pngs: list[bytes], sa_bytes: bytes, filename: str,
                ak_pngs: list[bytes] | None, ak_text: str | None,
                marks_scheme: MarksScheme | None, cfg: ProviderConfig,
                student_class=None, subject=None, use_mathpix: bool = False,
                usd_to_inr: float = DEFAULT_USD_TO_INR) -> dict:
    """Grade ONE answer sheet against the shared paper/key/scheme.

    Returns a dict with the report, cost breakdown, and the evaluated PDF bytes.
    Raises on failure so the caller can record a per-sheet error and continue.
    """
    sa_pngs = pdf_or_image_to_pngs(sa_bytes, filename)

    ocr_pages = ocr_transcript = None
    if use_mathpix:
        ocr_pages = ocr_all_pages(sa_pngs)
        for p in ocr_pages:
            synthesize_diagram_regions(p)
        ocr_transcript = build_transcript(ocr_pages)

    usage: dict = {}
    common = dict(
        question_paper_pngs=qp_pngs, student_pngs=sa_pngs,
        answer_key_pngs=ak_pngs, answer_key_text=ak_text,
        ocr_transcript=ocr_transcript, ocr_pages=ocr_pages,
        marks_scheme=marks_scheme, usage_out=usage,
    )
    if cfg.is_claude:
        report = claude_grade(**common, model=cfg.model, api_key=cfg.resolved_key(),
                              student_class=student_class, subject=subject)
    else:
        report = gemini_grade(**common, model=cfg.model, api_key=cfg.resolved_key(),
                              use_vertex=cfg.gemini_vertex, project=cfg.project,
                              location=cfg.location, student_class=student_class, subject=subject)

    mathpix_pages = len(sa_pngs) if use_mathpix else 0
    cost = compute_cost(usage, mathpix_pages=mathpix_pages, usd_to_inr=usd_to_inr)
    pdf_bytes = build_evaluated_pdf(student_pdf_bytes=sa_bytes, student_filename=filename,
                                    student_pngs=sa_pngs, report=report, ocr_pages=ocr_pages)
    return {"report": report, "cost": cost, "pdf_bytes": pdf_bytes}
