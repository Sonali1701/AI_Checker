"""Provider-agnostic grading orchestration through the PW shared proxy."""
from __future__ import annotations

import os
import traceback
import io
import math
import re
from dataclasses import dataclass, replace

from costs import (BUDGET_SPLIT_MARKS, DEFAULT_USD_TO_INR, budget_for,
                   compute_cost, record_usage_event)
from answer_map import answer_map_prompt_block, build_answer_map
from budgeting import BudgetPolicy, CallPlan, Stage
from exam_package import ExamPackage, package_prompt_block
from grader import (MarksScheme, OutputLimitExceeded,
                    marks_scheme_from_pdf, marks_scheme_from_section_headers,
                    pdf_or_image_to_pngs)
from grader import (
    _is_commerce_subject,
    _norm_qid,
    apply_audit,
    audit_completion_gaps,
    build_audit_prompt,
    apply_repair_corrections,
    detect_exam_type,
    fill_unanswered_parts,
    finalise_student_remarks,
    find_unmarked_answers,
    enforce_evidence_against_awards,
    prune_contradicted_annotations,
    report_is_stub,
    verify_objective_choices,
    force_official_total,
    has_audit_rows,
    paper_total_from_text,
    pdf_text_layer,
    paper_has_choice,
    printed_tag_sum,
    scheme_contradicts_paper,
    scheme_sum_is_plausible,
    regex_scheme_is_trustworthy,
)
from grader import audit_deductions as claude_audit
from grader import extract_marks_scheme as claude_extract_marks
from grader import generate_rubric as claude_generate_rubric
from grader import grade_answer_sheet as claude_grade
from grader import grade_answer_sheet_chunked as claude_grade_chunked
from grader import repair_report_totals as claude_repair
from grader import read_objective_choices as claude_read_objectives
from grader_gemini import audit_deductions as gemini_audit
from grader_gemini import extract_marks_scheme as gemini_extract_marks
from grader_gemini import generate_rubric as gemini_generate_rubric
from grader_gemini import grade_answer_sheet as gemini_grade
from grader_gemini import repair_report_totals as gemini_repair
from grader_gemini import read_objective_choices as gemini_read_objectives
from mathpix import build_transcript, ocr_all_pages, synthesize_diagram_regions
from pdf_renderer import build_evaluated_pdf
from selective_verification import (select_verification_qids,
                                    verification_candidates,
                                    visual_evidence_qids)
from remark_quality import remark_quality_problem, report_remark_problems
from strict_grading import (StrictGradeError, automatic_release_decision,
                            apply_official_objective_evidence, _choice_letter,
                            reconcile_report_to_package, report_decision_digest)

CLAUDE = "claude"
GEMINI = "gemini"

# Per-question (page-window) grading. Off unless AICHECKER_FANOUT is set, because
# it changes how every copy is marked and has not yet been measured against the
# archived sample. Turn it on for one run with:  AICHECKER_FANOUT=1
_FANOUT_DEFAULT = (os.environ.get("AICHECKER_FANOUT", "") or "").strip().lower() in (
    "1", "true", "yes", "on")

_STUDENT_INSTRUCTION_RE = re.compile(
    r"\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system|grader)\s+"
    r"(?:instructions?|rules?|prompts?)\b"
    r"|\baward\s+(?:me\s+)?full\s+marks\b"
    r"|\breveal\s+(?:the\s+)?system\s+prompt\b"
    r"|\b(?:return|output)\s+(?:only\s+)?(?:this\s+)?json\b",
    re.IGNORECASE,
)


def student_instruction_signals(text: str) -> list[str]:
    """Exact adversarial instruction fragments found in student OCR."""
    return [match.group(0) for match in _STUDENT_INSTRUCTION_RE.finditer(text or "")]


@dataclass
class ProviderConfig:
    provider: str = GEMINI
    model: str = "gemini-2.5-flash"
    google_token: str = ""
    task_id: str = ""
    exam_type: str = ""  # "" = auto-detect from class/subject; "school" | "professional"
    # Prompt-cache TTL for the shared prefix: "5m", "1h", or "" for no caching.
    # Set per JOB from the batch size — see _cache_ttl_for_batch.
    cache_ttl: str = "5m"
    # Grade a page-window at a time instead of the whole paper in one call. OFF by
    # default until the A/B in tools/eval_tabs.py says it is better on real copies —
    # see grader.grade_answer_sheet_chunked for why it should be.
    fanout: bool = _FANOUT_DEFAULT
    # Additive v2 contract.  Legacy tests/tools keep their current semantics; the
    # production endpoint opts into strict package-bound grading explicitly.
    grading_contract: str = "legacy"       # "legacy" | "exam_package_v2"
    exam_package: ExamPackage | None = None
    budget_cap_inr: float | None = None

    @property
    def is_claude(self) -> bool:
        return self.provider == CLAUDE

    @property
    def is_strict(self) -> bool:
        return self.grading_contract == "exam_package_v2"


# Claude model ids the PW proxy actually has deployments for. A model missing from
# here fails at RUN time with a 404 DeploymentNotFound, after OCR has been paid for
# and part of the booklet already graded — which is how a one-line default change to
# claude-sonnet-5 would have killed every small-bracket copy in production. Re-probe
# with tools/probe_models.py when the proxy's roster changes.
PROXY_CLAUDE_MODELS = frozenset({
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-8",
})


def assert_proxy_model(model: str) -> str:
    """Fail loudly, at import time, for a Claude id the proxy cannot serve."""
    m = (model or "").strip()
    if m.startswith("claude-") and m not in PROXY_CLAUDE_MODELS:
        raise RuntimeError(
            f"{m!r} has no deployment on the PW proxy — grading would 404 mid-copy. "
            f"Available: {', '.join(sorted(PROXY_CLAUDE_MODELS))}. If PW has since "
            f"provisioned it, add it to PROXY_CLAUDE_MODELS."
        )
    return m


def _grading_model_for_size(total_marks: float | None) -> str:
    """Pick the grader from the paper's size, so each budget bracket gets the only
    model measured to fit it. See _LARGE_PAPER_GRADING_MODEL for the numbers."""
    try:
        marks = float(total_marks) if total_marks is not None else None
    except (TypeError, ValueError):
        marks = None
    if marks is not None and marks > _LARGE_PAPER_MARKS:
        return _LARGE_PAPER_GRADING_MODEL
    return _BUDGET_GRADING_MODEL


def cfg_from_request(
    provider: str,
    model: str,
    subject: str,
    *,
    google_token: str,
    task_id: str,
    student_class: str = "",
    exam_type: str = "",
    total_marks: float | None = None,
) -> ProviderConfig:
    provider = (provider or GEMINI).strip().lower()
    if provider not in (GEMINI, CLAUDE):
        provider = GEMINI
    resolved_exam = (exam_type or "").strip().lower()
    if resolved_exam not in ("school", "professional"):
        resolved_exam = detect_exam_type(student_class, subject)
    # An explicit claude request with no model gets the measured grader, not opus
    # (opus is ~2x the cost for no measured accuracy gain on these papers).
    default_model = "gemini-2.5-flash" if provider == GEMINI else _BUDGET_GRADING_MODEL
    chosen = (model or default_model).strip()
    # No model asked for? Use the measured default grader for EVERY paper. It used to
    # apply only to "reasoning" subjects, which quietly sent Business Studies — a core
    # commerce subject — to a different model than the Accountancy paper beside it.
    # An EXPLICITLY requested model always wins.
    if provider == GEMINI and not (model or "").strip():
        # The grader lives on ANOTHER PROVIDER: Claude bills only what it writes, while
        # Gemini bills its uncappable thinking (see the cost policy note above).
        chosen = _grading_model_for_size(total_marks)
        if chosen.startswith("claude"):
            provider = CLAUDE
    elif provider == CLAUDE and not (model or "").strip():
        chosen = _grading_model_for_size(total_marks)
    return ProviderConfig(
        provider=provider,
        model=chosen,
        google_token=google_token,
        task_id=task_id,
        exam_type=resolved_exam,
    )


def _cache_ttl_for_batch(sheet_count: int) -> str:
    """Which prompt-cache TTL a job of this size should buy.

    The shared prefix (exam context + paper + key + scheme + system prompt) is
    identical for every student sitting the same test, so it is worth caching —
    but the WRITE is charged, and the two TTLs price differently:

        read           0.10x base input      (either TTL)
        write, 5-min   1.25x                 break-even at 2 requests
        write, 1-hour  2.00x                 break-even at 3 requests

    1 sheet   nothing will ever read the entry back, so the write is pure loss.
    2 sheets  5-minute: the cheaper write, and one read pays it back.
    3+ sheets 1-hour. A 5-minute entry only survives if each copy STARTS within
              five minutes of the last, and copies take 66-220s today — an
              18-page booklet sails close to the edge, and one slow copy makes
              every later one pay the write again. MEASURED over the archived
              runs: only 29% got any cache read at all and just 14% of cacheable
              volume was served from cache, so misses are the norm rather than
              the exception, and insuring against them beats the 0.75x extra.
    """
    if sheet_count <= 1:
        return ""
    if sheet_count == 2:
        return "5m"
    return "1h"


def mark_scale_for(exam_type: str, subject: str | None) -> float:
    """How big the inline ticks/circled marks should be for this paper.

    CA/CMA/CS booklets are dense ledgers and tightly-spaced legal prose, so
    school-sized marks bury the working; school commerce papers (Accountancy,
    Book-keeping) sit in between.
    """
    if (exam_type or "").strip().lower() == "professional":
        return 0.6
    if _is_commerce_subject(subject):
        return 0.8
    return 1.0


def generate_rubric(
    qp_pngs,
    cfg: ProviderConfig,
    student_class=None,
    subject=None,
    filename: str = "",
) -> str:
    common = dict(
        model=cfg.model,
        google_token=cfg.google_token,
        task_id=cfg.task_id,
        filename=filename,
        student_class=student_class,
        subject=subject,
        exam_type=cfg.exam_type,
    )
    if cfg.is_claude:
        return claude_generate_rubric(qp_pngs, **common)
    return gemini_generate_rubric(qp_pngs, **common)


def _extract_marks(
    qp_pngs,
    cfg: ProviderConfig,
    student_class=None,
    subject=None,
    filename: str = "",
    total_marks: float | None = None,
    paper_text: str = "",
    require_complete: bool = False,
) -> MarksScheme:
    common = dict(
        model=cfg.model,
        google_token=cfg.google_token,
        task_id=cfg.task_id,
        filename=filename,
        student_class=student_class,
        subject=subject,
        exam_type=cfg.exam_type,
        total_marks=total_marks,
        paper_text=paper_text,
        require_complete=require_complete,
    )
    if cfg.is_claude:
        return claude_extract_marks(qp_pngs, **common)
    return gemini_extract_marks(qp_pngs, **common)


def detect_marks_scheme(
    qp_bytes: bytes,
    filename: str,
    cfg: ProviderConfig | None = None,
    student_class=None,
    subject=None,
    total_marks: float | None = None,
    require_complete: bool = False,
) -> tuple[MarksScheme | None, str]:
    """Use the free PDF text layer first and AI only for scanned papers.

    Professional (CA/CMA/CS) papers skip the regex entirely: their tags sit on
    separate lines under nested (a)(i)(ii) parts and the papers carry internal
    choice, both of which the regex mis-associates — the AI extractor reads the
    structure (and the "answer any N" instruction) correctly.
    """
    paper_text = pdf_text_layer(qp_bytes, filename)
    # The paper usually prints its own total ("Maximum Marks– 50"); an explicit
    # user-entered total still wins over it.
    official_total = total_marks or paper_total_from_text(paper_text)
    resolved_exam_type = (
        cfg.exam_type if cfg is not None
        else detect_exam_type(student_class or "", subject or "")
    )
    professional = resolved_exam_type == "professional"
    choice_signal = paper_has_choice(paper_text, "")
    # Professional layouts and explicit choice instructions need a structural graph.
    # "Professional" is not itself proof that choice exists; the two extractors may
    # legitimately agree on an empty choice_groups list.
    needs_structural_graph = professional or choice_signal
    regex_scheme = (None if needs_structural_graph
                    else marks_scheme_from_pdf(qp_bytes, filename))
    # Use the free regex only when it corroborates against the paper's printed total or
    # its printed tags — an unverified reading is how a NEET paper became a 4-mark paper.
    if regex_scheme is not None and regex_scheme_is_trustworthy(
        regex_scheme, paper_text, official_total
    ):
        return regex_scheme, "regex"
    # The ordinary CBSE/school layout prints NO per-question tag at all — the marks
    # are stated once per section, "Short Answer Type Questions (11 - 15)(5 x 5 = 25)".
    # printed_tag_sum finds zero tags on such a paper, so every downstream check that
    # reads tags is vacuous and any AI extraction came back trusted by default.
    # MEASURED on the 70-mark CBSE Chemistry paper: four AI extractions gave 83, 81,
    # 78 and 73 marks, none of them right and no two alike, while the section headers
    # give 5+10+25+10+20 = 70 identically every time. Free, deterministic, and it
    # self-checks (the printed range and the multiplication must agree), so it goes
    # ahead of the paid call.
    section_scheme = (None if needs_structural_graph else
                      marks_scheme_from_section_headers(paper_text, official_total))
    if section_scheme is not None:
        return section_scheme, "sections"
    if cfg is None:
        # Free-only mode: an untrusted regex reading is worse than none — grading
        # reads per-question marks itself and anchors on the official total.
        return None, "none"
    qp_pngs = pdf_or_image_to_pngs(qp_bytes, filename)
    scheme = _extract_marks(
        qp_pngs, cfg, student_class, subject, filename, official_total, paper_text,
        require_complete,
    )
    # The tag VALUES in the text are exact even when their question association isn't,
    # so they verify the extraction: a disagreement means a part was dropped or merged.
    expected, tag_count = printed_tag_sum(paper_text)
    got = sum(i.max for i in scheme.items)
    extracted_choice = bool(getattr(scheme, "choice_groups", None))
    has_choice = choice_signal or extracted_choice
    if tag_count >= 3 and abs(got - expected) > 0.25:
        # ...but on a paper with internal choice the printed tags cover EVERY option,
        # so they necessarily add up to more than one student can score (129 marks of
        # tags on this 100-mark CA paper). Equality is simply the wrong test there, and
        # since every CA paper offers choice it rejected all of them — the sheet then
        # fell back to proportionally rescaled maxima like "IT 27.0/57.5". Accept the
        # extraction when it lands between the official total and the tags' upper bound.
        fits_choice = (
            has_choice
            and official_total is not None
            and float(official_total) - 0.25 <= got <= expected + 0.25
        )
        if not fits_choice:
            return scheme, "ai_mismatch"
    # The sum test above is satisfied by ANY decomposition that lands between the
    # official total and the tags' upper bound, which on a choice paper is most of
    # them. MEASURED: nine runs of one CA Inter Taxation paper, all with the paper
    # supplied, all passing the sum test, produced nine different schemes — seven
    # gave objective questions 1.5 marks each where the paper prints "(2 Marks)".
    # So also check the tag values one by one, not just their sum.
    conflict = scheme_contradicts_paper(scheme, paper_text, official_total)
    if conflict:
        print(f"[marks] extraction disagrees with {filename or 'the paper'}: {conflict}",
              flush=True)
        return scheme, "ai_tag_conflict"
    # Both checks above read the paper's PER-QUESTION tags, so both are vacuous on a
    # paper that prints its marks only in section headers — "(5 x 5 = 25)" — which is
    # the ordinary CBSE/school layout. tag_count is 0, the sum test is skipped, and
    # scheme_contradicts_paper has nothing to disagree with, so any extraction at all
    # came back "ai" (trusted). MEASURED on a 70-mark CBSE Chemistry paper: the parts
    # summed to 89.25 because every sub-part was given its PARENT's mark (Q11 10, Q16
    # 15, Q21 10) and Q15 was dropped altogether. Nothing downstream caught it either —
    # scheme_matches_total only demotes such a scheme from authoritative to "per-part
    # maxima", and it is still injected into the grading prompt, where those inflated
    # maxima produced a 75-mark report on a 70-mark paper. The repair pass then came
    # back empty and the deterministic rescale smeared 70/75 across every question,
    # printing maxima the paper never had (Q19 "/8", Q10 "/1.5").
    # So check the one number this layout does state: the official total.
    if not scheme_sum_is_plausible(
        got, official_total, has_choice
    ):
        print(f"[marks] extraction of {filename or 'the paper'} sums to {got:g}, not the "
              f"official {float(official_total):g} — grading will read the marks itself",
              flush=True)
        return scheme, "ai_mismatch"
    return scheme, "ai"


# A digital PDF yields roughly 1,500-3,000 characters of text per page; a scan yields
# almost none. Requiring this much per page is a reliable "the text layer really covers
# these pages" test, so we never drop images for a scanned or figure-only paper.
_TEXT_CHARS_PER_PAGE = 600


def _prefer_text_over_images(pngs, text: str):
    """(images_to_send, dropped?) — drop printed-page images when their text layer
    already covers every page. Never call this for student handwriting."""
    if not pngs:
        return pngs, False
    if len((text or "").strip()) >= _TEXT_CHARS_PER_PAGE * len(pngs):
        return [], True
    return pngs, False


def _estimated_image_tokens(images: list[bytes] | None) -> int:
    """Conservative Claude-style image-token quote after the model downscale.

    Anthropic vision tokenization is approximately pixel area / 750. The grading
    layer limits the long edge to 1568 px, so quoting that transformed area keeps a
    primary request from consuming the verification reserve before dispatch.
    """
    total = 0
    for data in images or []:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
            scale = min(1.0, 1568.0 / max(1, width, height))
            total += int(math.ceil((width * scale) * (height * scale) / 750.0))
        except Exception:
            # A normal downscaled A4 page is around 2.3k tokens. Unknown image shape
            # is priced above that rather than silently counted as zero.
            total += 3000
    return total


def _estimated_primary_input_tokens(*, qp_images, student_images, key_images,
                                    qp_text: str, key_text: str,
                                    ocr_transcript: str, marks_scheme) -> int:
    image_tokens = _estimated_image_tokens(
        list(qp_images or []) + list(student_images or []) + list(key_images or []))
    scheme_chars = sum(
        len(str(getattr(item, "qid", "")))
        + len(str(getattr(item, "description", ""))) + 32
        for item in list(getattr(marks_scheme, "items", None) or [])
    )
    text_chars = (len(qp_text or "") + len(key_text or "")
                  + len(ocr_transcript or "") + scheme_chars)
    # 2.5k covers the system prompt/schema and per-page labels. Four characters per
    # token is deliberately conservative for mixed English, Hindi, numbers and JSON.
    return max(1000, image_tokens + int(math.ceil(text_chars / 4.0)) + 2500)


def settle_disputed_choice(primary: str, independent: str, confirming: str) -> str:
    """The option a disputed objective row settles on, or "" to leave it unresolved.

    The three reads are not equal witnesses, so this is not a vote.

    ``primary`` is whatever option the grading call happened to name. That call is
    never told to ignore an answer the student struck out. Both key-blind readers are:
    their prompt says to ignore an option that is crossed out or overwritten. A
    student who writes (B), cancels it and answers (C) is therefore read as B by the
    careless pass and C by the careful ones - and counting hands would award the mark
    for the answer they cancelled, which is exactly what the moderation prompt means
    by "cancelled working earns nothing".

    So a row settles only when both careful readers agree, and the uninstructed read
    never carries it. When the careful readers disagree with each other the cell is
    genuinely unreadable and holding is right: guessing would either invent a mark or
    take one away.
    """
    options = {"A", "B", "C", "D"}
    primary = str(primary or "").strip().upper()
    independent = str(independent or "").strip().upper()
    confirming = str(confirming or "").strip().upper()
    if primary not in options or independent not in options:
        return ""
    if primary == independent:
        return ""                       # not disputed; nothing to settle here
    return independent if confirming == independent else ""


def _verify_output_budget(question_count: int, *, repair: bool = False) -> int:
    """Output tokens for one second-examiner pass.

    This is not a cost dial. Gemini bills its thinking against the same cap and the
    proxy strips thinkingConfig, so a tight budget cannot buy a cheaper answer - it
    buys a truncated one, and the field failure it produces is a certificate whose
    later rows carry a mark but no `reason`. The release gate then holds a copy that
    nobody is going to review by hand. Budget for the whole certificate; a copy is
    worth far more than the tokens that finish it.
    """
    rows = max(1, int(question_count or 0))
    budget = 4000 + 400 * rows
    if repair:
        budget *= 2                         # the repair also restates every row
    return int(min(32000, budget))


def _merge_audit_verdicts(first: list, repaired: list) -> list:
    """Overlay a repair pass on the first certificate, row by row.

    The repair asks for every row again so the numbering stays the one both passes
    were shown, but a second look can legitimately come back thinner. Keeping the
    first pass's ruling for any row the repair dropped means a repair can only add
    certainty, never lose a row that was already certified. For the same reason a
    repaired row that arrives without a usable reason does not overwrite a first-pass
    row that already had one.
    """
    merged = {int(item.row): item for item in (first or [])}
    for item in repaired or []:
        row = int(item.row)
        prior = merged.get(row)
        if (prior is not None
                and remark_quality_problem(item.reason)
                and not remark_quality_problem(prior.reason)):
            continue
        merged[row] = item
    return [merged[row] for row in sorted(merged)]


def _merge_usage(usage: dict, extra: dict) -> None:
    """Merge follow-up calls without losing their provider/model-specific prices."""
    events = [event for event in list((extra or {}).get("_events") or [])
              if isinstance(event, dict)]
    if events:
        for event in events:
            record_usage_event(
                usage,
                event,
                stage=str(event.get("stage") or "follow-up"),
            )
        return
    # Compatibility for an old provider adapter that has not emitted events yet.
    if extra:
        record_usage_event(usage, extra, stage="follow-up")


def moderation_applies(cfg: ProviderConfig, subject: str | None) -> bool:
    """The second-examiner pass runs for professional (CA/CMA/CS) papers and commerce
    subjects — the papers where method variance and unrecognised valid working cause
    false deductions. School papers in other subjects keep the single-call cost."""
    return bool(cfg.exam_type == "professional" or _is_commerce_subject(subject))


# --- Per-copy cost policy -------------------------------------------------------
#
# MEASURED (2026-08-21): this proxy strips `thinkingConfig`, so thinking cannot be
# capped on any model, and gemini-2.5-pro spends ~9,000 thinking tokens per 2 pages.
# Thinking is billed as output, so a 7-page copy costs ~Rs.25 on pro BEFORE any
# question-paper input — above the per-copy budget however much else is trimmed.
#
# So the bulk marking (which reads every page and writes the whole report) runs on a
# flash model, and the pro model is spent where it is small and decisive: auditing a
# compact TEXT summary of the marks. That audit is bidirectional, because a cheap
# grader's failure mode is awarding marks the working does not support.
# MEASURED on the CA + Commerce sample tabs (human-marked booklets), 2026-08-21/22.
#
# The costly part of Gemini is thinking, which this proxy will not let us cap and which
# is billed as output: a 7-page copy on gemini-2.5-pro produced 25,665 output tokens of
# which only ~1,553 were the actual report — Rs.24 of Rs.29.5. Claude never charges that
# (extended thinking is opt-in and we do not request it), so it bills only what it writes.
#
# Per 25-mark Commerce booklet, against the human evaluator:
#   claude-sonnet-4-6   9/9 graded   mean |delta| 2.2   Rs.10.8   0/9 over budget
#   claude-haiku-4-5    7/9 graded   mean |delta| 1.9   Rs. 4.2   (2 hard failures)
#   gemini-2.5-flash    8/9 graded   mean |delta| 3.9   Rs. 5.1   (worst row -15.5)
#   gemini-2.5-pro      ~Rs.29.5 — accurate but far over the per-copy budget
# Sonnet is the default: it is the only model that graded every booklet without failing,
# and it fits the budget. Haiku is cheaper and similarly accurate on Commerce but failed
# two rows outright, so it is not the default.
#
# CAVEAT, and it is the big one: on 100-mark CA papers WITHOUT their question paper and
# solution, every model over-awards badly (sonnet +25..+56, flash +14..+41.5). The one
# 100-mark row that had both came in at -6. That is a MATERIAL problem, not a model
# problem — no model swap fixes it. Get the paper and solution to the tool.
AUDIT_MODEL = "gemini-2.5-pro"
# The PW proxy is a DEPLOYMENT gateway, not the Anthropic API: a model id only works
# if someone has provisioned a deployment with that exact name. PROBED 2026-08-31
# against the live proxy:
#     claude-sonnet-4-6   available
#     claude-haiku-4-5    available
#     claude-opus-4-8     available (the audit/repair model)
#     claude-sonnet-5     404 DeploymentNotFound
#     claude-opus-5       404 DeploymentNotFound
#
# So sonnet-5 CANNOT be the grader here yet, however good the arithmetic looks. On
# the archived token counts it would take the 50-mark Business Law copy from Rs.31.8
# to Rs.21.9 ($2/$10 per MTok against sonnet-4-6's $3/$15, a third cheaper on a newer
# model) — worth roughly 31% of the LLM bill on every paper in the small bracket.
# That saving is available the day PW provisions the deployment and this constant
# changes; costs.py already carries the -5 rates so the accounting is ready for it.
# Until then, switching this to sonnet-5 makes every small-bracket copy 404.
_BUDGET_GRADING_MODEL = "claude-sonnet-4-6"
# Big papers (the Rs.20 bracket) grade on haiku instead. This is a COST decision that
# the one properly-measured 100-mark paper happened to also favour on accuracy.
# CA Inter Taxation, 18 pages, human 42/100, same fixes and material on both:
#     claude-sonnet-4-6   65.0 (+23.0)   Rs.50.1   <- 2.5x over the Rs.20 ceiling
#     claude-haiku-4-5    52.5 (+10.5)   Rs.19.8
# Sonnet CANNOT reach Rs.20 on an 18-page booklet in any configuration — its output
# tokens alone are ~Rs.17 — so for this bracket it is not a trade-off, it is the only
# model that fits. Accuracy here is n=1 paper; haiku's own run-to-run spread on it was
# 9.5 marks, so treat "haiku is also more accurate" as encouraging, not established.
#
# RE-MEASURED 2026-08-31 on Sonnet 5's cheaper rates, same archived token counts:
#     claude-sonnet-5   Rs.35.1 first copy   Rs.26.6 on a warm cache   <- still OVER
#     claude-haiku-4-5  Rs.19.2 first copy   Rs.15.0 on a warm cache
# So the conclusion above survives the price cut: haiku stays. The thing that WOULD
# change it is the Batch API's 50% discount (sonnet-5 lands at Rs.15.0 there), and
# that needs a batch endpoint on the PW proxy, which only exposes /api/claude/generate.
_LARGE_PAPER_GRADING_MODEL = "claude-haiku-4-5"
# Papers above 50 marks use the large-paper model regardless of department, because
# it is the only measured option that can approach either hard cap. The cap itself is
# department-aware and always comes from costs.budget_for().
_LARGE_PAPER_MARKS = BUDGET_SPLIT_MARKS
# The second-opinion model for Claude routes' text-only judgment calls (repair, audit).
# grader.repair_report_totals / audit_deductions already DEFAULT to this — it is the
# model those functions were designed to run on. pipeline used to override that default
# down to cfg.model (the grader auditing/repairing ITSELF); see audit_model_for below.
_CLAUDE_SECOND_OPINION_MODEL = "claude-opus-4-8"

# Checked here, at import, so a bad model id is a startup failure rather than a 404
# in the middle of a student's copy.
for _m in (_BUDGET_GRADING_MODEL, _LARGE_PAPER_GRADING_MODEL,
           _CLAUDE_SECOND_OPINION_MODEL):
    assert_proxy_model(_m)
del _m


def audit_model_for(cfg: ProviderConfig) -> str:
    """Model for the repair and moderation (second-examiner) passes.

    Must be a genuinely STRONGER model than the grader, never the grader checking
    itself — a model re-checking its own work just confirms its own judgment either
    way. MEASURED on the CA Inter Taxation booklet (human 42/100), everything else
    identical:
        self-audit (grader==auditor)   haiku 59.0 (+17.0), moderation moved +8.5
        bidirectional SELF-audit       haiku 67.5 (+25.5), moderation moved +5.5 (WORSE)
    Letting haiku reconsider its own awards made the over-award worse. This used to
    return `cfg.model` for every Claude route, which is exactly that self-audit — a
    leftover from before Claude became the default grader, when this was dead code
    for the app's actual route. Gemini routes were never affected: a cheap
    gemini-2.5-flash grade was already checked by the stronger gemini-2.5-pro
    (AUDIT_MODEL), which is the pattern this now applies to Claude too.
    """
    return _CLAUDE_SECOND_OPINION_MODEL if cfg.is_claude else AUDIT_MODEL


def audits_awarded_marks(cfg: ProviderConfig) -> bool:
    """True when the second-opinion model is genuinely different from (stronger
    than) the grader, so it can safely also re-check marks that were GIVEN, not
    only marks taken away. False only on the rare route where the grader already
    IS the strongest model available (nothing stronger exists to check it with)."""
    return audit_model_for(cfg) != cfg.model


def grade_prepared(
    *,
    qp_pngs: list[bytes],
    sa_pngs: list[bytes],
    ak_pngs: list[bytes] | None,
    ak_text: str | None,
    marks_scheme: MarksScheme | None,
    cfg: ProviderConfig,
    filename: str = "",
    student_class=None,
    subject=None,
    use_mathpix: bool = False,
    usd_to_inr: float = DEFAULT_USD_TO_INR,
    total_marks: float | None = None,
    qp_text: str = "",
    ak_text_layer: str = "",
    stage_callback=None,
    prior_usage: dict | None = None,
) -> dict:
    """Run OCR plus one grading call for already-rendered page images, then the
    official-total enforcement and the moderation pass over deductions."""
    def stage(name: str, detail: str = "") -> None:
        if stage_callback:
            stage_callback(name, detail)

    policy = None
    hard_cap = float(cfg.budget_cap_inr or budget_for(
        total_marks, exam_type=cfg.exam_type, subject=subject or ""))
    if cfg.is_strict:
        if cfg.exam_package is None:
            raise StrictGradeError(
                "MISSING_EXAM_PACKAGE",
                "Strict automatic grading requires a validated ExamPackage.",
            )
        package_blockers = cfg.exam_package.blockers()
        if package_blockers:
            raise StrictGradeError(
                "INVALID_EXAM_PACKAGE",
                "; ".join(issue.detail for issue in package_blockers[:4]),
            )
        if marks_scheme is None:
            raise StrictGradeError(
                "MISSING_MARKS_SCHEME",
                "Strict automatic grading requires an authoritative question graph.",
            )
        policy = BudgetPolicy.for_copy(
            total_marks=total_marks or cfg.exam_package.total_marks,
            exam_type=cfg.exam_type,
            subject=subject or "",
        )
        hard_cap = float(cfg.budget_cap_inr or policy.cap_inr)

    ocr_pages = ocr_transcript = None
    answer_map = None
    if use_mathpix:
        stage("ocr", "Reading every answer-sheet page")
        ocr_pages = ocr_all_pages(
            sa_pngs,
            google_token=cfg.google_token,
            task_id=cfg.task_id,
            filename=filename,
        )
        for page in ocr_pages:
            synthesize_diagram_regions(page)
        ocr_transcript = build_transcript(ocr_pages)
        if cfg.is_strict:
            injection_signals = student_instruction_signals(ocr_transcript)
            if injection_signals:
                raise StrictGradeError(
                    "STUDENT_PROMPT_INJECTION",
                    "Student writing contains instruction-like text aimed at the grader: "
                    + ", ".join(repr(item[:80]) for item in injection_signals[:3]),
                )
    stage("answer_mapping", "Mapping visible answers to the official question graph")
    if cfg.is_strict:
        answer_map = build_answer_map(cfg.exam_package, ocr_pages)

    # A printed page we already hold as TEXT costs nothing to send as text and a
    # thousand-odd input tokens to send as a picture. Question papers and solution
    # PDFs are digital documents with a full text layer, so when that layer is rich
    # enough to account for the pages, send the words and drop the images. Student
    # handwriting is never dropped — it only exists as pixels.
    qp_pngs, dropped_qp = _prefer_text_over_images(qp_pngs, qp_text)
    key_text_layer = (ak_text_layer or "").strip()
    dropped_key = False
    if ak_pngs and not ak_text:
        kept, dropped_key = _prefer_text_over_images(ak_pngs, key_text_layer)
        if dropped_key:
            ak_pngs, ak_text = None, key_text_layer
    if dropped_qp or dropped_key:
        print(f"[payload] sent as text instead of images: "
              f"{'question paper ' if dropped_qp else ''}{'answer key' if dropped_key else ''}"
              .strip(), flush=True)

    provider_qp_text = qp_text
    if cfg.is_strict:
        provider_qp_text = package_prompt_block(cfg.exam_package) + (
            "\n\n" + qp_text if (qp_text or "").strip() else ""
        )
        provider_qp_text += "\n\n" + answer_map_prompt_block(answer_map)
    usage: dict = {
        "_events": [dict(event) for event in list((prior_usage or {}).get("_events") or [])
                    if isinstance(event, dict)]
    } if prior_usage else {}
    common = dict(
        question_paper_pngs=qp_pngs,
        student_pngs=sa_pngs,
        answer_key_pngs=ak_pngs,
        answer_key_text=ak_text,
        ocr_transcript=ocr_transcript,
        ocr_pages=ocr_pages,
        marks_scheme=marks_scheme,
        usage_out=usage,
        model=cfg.model,
        google_token=cfg.google_token,
        task_id=cfg.task_id,
        filename=filename,
        student_class=student_class,
        subject=subject,
        exam_type=cfg.exam_type,
        total_marks=total_marks,
        qp_text=provider_qp_text,
        cache_ttl=cfg.cache_ttl,
    )
    if cfg.is_strict:
        # Bound the largest billable component before the request leaves the app.
        # The release gate still uses actual usage, because input/image tokens vary.
        # Even a short paper can need a long JSON report: every objective row still
        # carries page/evidence coordinates, and a few descriptive answers add
        # criteria.  The old 7k small-paper ceiling truncated this real 25-mark
        # Economics copy.  Strict production grades on the budget-feasible Haiku
        # primary, for which a 15k ceiling still fits the protected verifier reserve.
        primary_output_limit = 15000
        common["max_output_tokens"] = primary_output_limit
        quoted_input = _estimated_primary_input_tokens(
            qp_images=qp_pngs,
            student_images=sa_pngs,
            key_images=ak_pngs,
            qp_text=provider_qp_text,
            key_text=ak_text or key_text_layer,
            ocr_transcript=ocr_transcript or "",
            marks_scheme=marks_scheme,
        )
        primary_quote = CallPlan(
            stage=Stage.PRIMARY,
            provider=cfg.provider,
            model=cfg.model,
            max_input_tokens=quoted_input,
            max_output_tokens=primary_output_limit,
            mathpix_pages=len(sa_pngs) if use_mathpix else 0,
            mandatory=True,
        ).quote_inr(usd_to_inr)
        prior_cost = compute_cost(usage, usd_to_inr=usd_to_inr).total_inr
        primary_allowance = (
            hard_cap - policy.verification_reserve_inr - policy.safety_inr - prior_cost
        )
        if primary_quote > primary_allowance + 1e-6:
            raise StrictGradeError(
                "PRIMARY_BUDGET_RESERVATION_DENIED",
                f"Worst-case primary request is Rs.{primary_quote:.2f}, but only "
                f"Rs.{primary_allowance:.2f} is available after the mandatory "
                "verification and safety reserves.",
            )
    # Fan-out is a Claude-route feature (it leans on prompt caching to stay cheap)
    # and needs OCR to know where each question's answer sits. Without either, the
    # single call is still the right — and only — path.
    use_fanout = bool(getattr(cfg, "fanout", False)) and cfg.is_claude and bool(ocr_pages)
    claude_call = claude_grade_chunked if use_fanout else claude_grade
    stage("primary_grading", "Grading the mapped questions")
    try:
        report = claude_call(**common) if cfg.is_claude else gemini_grade(**common)
    except OutputLimitExceeded as exc:
        # The grader ran away mid-reply. Losing the copy is the worst outcome, so retry
        # ONCE on the steadier model. This is the known haiku failure mode (3 of 10
        # Commerce Accountancy booklets), and it is why routing big papers to haiku is
        # safe: a runaway costs one wasted call, not the sheet.
        if cfg.is_strict:
            raise StrictGradeError(
                "PRIMARY_OUTPUT_LIMIT",
                "The primary grader did not finish inside the reserved output budget.",
            ) from exc
        fallback = _BUDGET_GRADING_MODEL if cfg.model != _BUDGET_GRADING_MODEL else None
        if fallback is None:
            raise
        print(f"[grader] {exc} — retrying on {fallback}", flush=True)
        common["model"] = fallback
        report = claude_grade(**common) if fallback.startswith("claude") else gemini_grade(**common)
        cfg = replace(cfg, model=fallback, provider=CLAUDE if fallback.startswith("claude")
                      else GEMINI)

    if cfg.is_strict and answer_map is not None:
        report.unresolved_answer_qids = list(answer_map.unresolved_qids)

    # A reply that went through the motions — one remark repeated across the paper,
    # no criteria — is worth nothing to a student, so it is worth re-buying on the
    # sturdier model even though that breaks the per-copy budget. Shipping 9/70 on a
    # copy the same model marked properly elsewhere is the more expensive mistake.
    stub = report_is_stub(report)
    if stub:
        if cfg.is_strict:
            raise StrictGradeError(
                "PRIMARY_REPORT_INCOMPLETE",
                f"The primary grader returned an incomplete report ({stub}).",
            )
        retry = _BUDGET_GRADING_MODEL if cfg.model != _BUDGET_GRADING_MODEL else None
        if retry:
            print(f"[grader] the reply looks unmarked ({stub}) — regrading on {retry}",
                  flush=True)
            common["model"] = retry
            second = claude_grade(**common) if retry.startswith("claude") else gemini_grade(**common)
            if not report_is_stub(second):
                report = second
                cfg = replace(cfg, model=retry,
                              provider=CLAUDE if retry.startswith("claude") else GEMINI)
            else:
                print("[grader] the second reply is no better — keeping the first",
                      flush=True)
        else:
            print(f"[grader] the reply looks unmarked ({stub}) and there is no sturdier "
                  f"model to fall back to", flush=True)

    if cfg.is_strict:
        report = reconcile_report_to_package(
            report, cfg.exam_package, answer_map=answer_map,
            finalize_choices=not bool(cfg.exam_package.choice_groups))

    # An MCQ is all-or-nothing and trivially checkable, so a misread option letter
    # hands out marks for a plainly wrong answer — the first thing a reviewer tests.
    # Moderation cannot catch it: that pass is text-only and skips objective items by
    # design. The OCR of the very line the choice was read from is already in hand.
    objective_usage: dict = {}
    try:
        objective_notes = (
            apply_official_objective_evidence(
                report, cfg.exam_package, ocr_pages, answer_map=answer_map,
                question_paper_text=qp_text)
            if cfg.is_strict else verify_objective_choices(report, ocr_pages)
        )
    except StrictGradeError as exc:
        if not cfg.is_strict or exc.code != "OBJECTIVE_ANSWER_UNRESOLVED":
            raise
        objective_qids = [
            item.qid for item in cfg.exam_package.questions
            if item.question_type == "objective"
        ]
        objective_norm = {_norm_qid(qid) for qid in objective_qids}
        evidence_pages = {
            max(1, min(len(sa_pngs), int(getattr(question, "page", 1) or 1)))
            for question in report.questions
            if _norm_qid(question.qid) in objective_norm and sa_pngs
        }
        if not evidence_pages and sa_pngs:
            evidence_pages = {1}
        objective_pngs = [sa_pngs[page - 1] for page in sorted(evidence_pages)]
        stage(
            "verifying",
            "Independently reading checkbox/objective choices omitted by OCR",
        )
        reader = gemini_read_objectives if cfg.is_claude else claude_read_objectives
        reader_model = "gemini-2.5-flash" if cfg.is_claude else _BUDGET_GRADING_MODEL
        observations = reader(
            objective_pngs,
            objective_qids,
            model=reader_model,
            google_token=cfg.google_token,
            task_id=cfg.task_id,
            filename=filename,
            usage_out=objective_usage,
        )
        visual_choices = {
            _norm_qid(item.qid): item for item in observations
            if _norm_qid(item.qid) in objective_norm
        }
        primary_choices = {
            _norm_qid(question.qid): str(
                getattr(question, "chosen_option", "") or ""
            ).strip().upper()
            for question in report.questions
            if _norm_qid(question.qid) in objective_norm
        }
        independent_choices = {
            qid: str(getattr(item, "chosen_option", "") or "").strip().upper()
            for qid, item in visual_choices.items()
        }
        print(
            "[objective] key-blind visual reads: primary="
            + ",".join(f"{qid}:{primary_choices.get(qid) or '?'}"
                       for qid in sorted(objective_norm))
            + " independent="
            + ",".join(f"{qid}:{independent_choices.get(qid) or '?'}"
                       for qid in sorted(objective_norm)),
            flush=True,
        )
        if len(visual_choices) != len(objective_norm):
            raise StrictGradeError(
                "OBJECTIVE_VISUAL_VERIFICATION_INCOMPLETE",
                "The independent visual reader did not return every objective row.",
            )
        try:
            objective_notes = apply_official_objective_evidence(
                report, cfg.exam_package, ocr_pages, answer_map=answer_map,
                visual_choices=visual_choices, question_paper_text=qp_text,
            )
        except StrictGradeError as disagreement:
            if disagreement.code != "OBJECTIVE_VISUAL_DISAGREEMENT":
                raise
            # Two readers disagreeing on one box is a question to settle, not a
            # reason to throw away a whole marked copy. Nobody reviews a held copy,
            # so refusing here loses every mark on the sheet over one MCQ.
            #
            # But the tie is NOT settled by a show of hands, because the three reads
            # are not of equal standing. `primary` is the option the grading call
            # happened to mention; that call was never told to ignore a cancelled
            # answer. Both key-blind readers were: their prompt says in terms to
            # ignore an option that is crossed out or overwritten. A student who
            # writes (B), strikes it out and answers (C) is therefore read as B by
            # the cancellation-blind pass and C by the careful ones - and a majority
            # of three would hand them the mark for the answer they cancelled, which
            # is precisely what AUDIT_SYSTEM_PROMPT means by "cancelled working earns
            # nothing". So the row is settled only when BOTH careful readers say the
            # same thing, and the loud, uninstructed read never carries it.
            stage("verifying",
                  "Two readers disagree on a choice; taking a second key-blind read")
            tiebreak_reader = (claude_read_objectives if cfg.is_claude
                               else gemini_read_objectives)
            tiebreak_model = (_BUDGET_GRADING_MODEL if cfg.is_claude
                              else "gemini-2.5-flash")
            third = tiebreak_reader(
                objective_pngs,
                objective_qids,
                model=tiebreak_model,
                google_token=cfg.google_token,
                task_id=cfg.task_id,
                filename=filename,
                usage_out=objective_usage,
            )
            third_choices = {
                _norm_qid(item.qid): _choice_letter(
                    str(getattr(item, "chosen_option", "") or ""))
                for item in third
                if bool(getattr(item, "clear", False))
            }
            settled: list[str] = []
            for qid in sorted(objective_norm):
                primary = _choice_letter(primary_choices.get(qid, ""))
                independent = _choice_letter(independent_choices.get(qid, ""))
                confirming = settle_disputed_choice(
                    primary, independent, third_choices.get(qid, ""))
                if not confirming:
                    continue
                for question in report.questions:
                    if _norm_qid(question.qid) == qid:
                        question.chosen_option = confirming
                observation = visual_choices.get(qid)
                if observation is not None:
                    try:
                        observation.chosen_option = confirming
                    except (AttributeError, ValueError):
                        pass
                settled.append(
                    f"{qid}:{confirming} (both key-blind reads; grading pass said "
                    f"{primary})")
            if not settled:
                raise
            print("[objective] second key-blind read settled "
                  f"{len(settled)} disputed row(s): {', '.join(settled)}",
                  flush=True)
            objective_notes = apply_official_objective_evidence(
                report, cfg.exam_package, ocr_pages, answer_map=answer_map,
                visual_choices=visual_choices, question_paper_text=qp_text,
            )
    finally:
        _merge_usage(usage, objective_usage)
    for note in objective_notes:
        print(f"[objective] {filename or 'sheet'}: {note}", flush=True)

    # The official total is a HARD contract — never ship a report graded out of the
    # wrong denominator. Layer 1 (an authoritative scheme forced in _reconcile_report)
    # usually guarantees it already; when the report still disagrees, layer 2 is one
    # cheap text-only repair call that re-reads the paper's printed tags; layer 3
    # rescales deterministically so the total is right by construction.
    if total_marks is not None and abs(float(report.max_total) - float(total_marks)) > 0.25:
        if cfg.is_strict:
            raise StrictGradeError(
                "OFFICIAL_TOTAL_MISMATCH",
                f"The graded questions total {float(report.max_total):g}, but exam package "
                f"{cfg.exam_package.package_id[:12]} requires {float(total_marks):g}. "
                "Strict mode will not repair or proportionally rescale maxima.",
            )
        repair_usage: dict = {}
        try:
            repair = claude_repair if cfg.is_claude else gemini_repair
            # Same fix as audit_model_for: repair is a text-only "re-read the printed
            # tags carefully" judgment call, not a re-run of the grading call, so it
            # should not be pinned to the (possibly cheap) grader's own model either.
            # grader.repair_report_totals defaults to claude-opus-4-8 for exactly this
            # reason; this used to override that default down to cfg.model.
            corrections = repair(
                report,
                model=audit_model_for(cfg),
                google_token=cfg.google_token,
                task_id=cfg.task_id,
                filename=filename,
                total_marks=float(total_marks),
                marks_scheme=marks_scheme,
                qp_text=qp_text,
                usage_out=repair_usage,
            )
            report = apply_repair_corrections(report, corrections, float(total_marks))
        except Exception:
            traceback.print_exc()
        _merge_usage(usage, repair_usage)
        # Layer 2b: the report may simply be SHORT because the student left a part
        # of an answered question blank — on a choice paper nothing has accounted for
        # it, because the scheme cannot be authoritative there. Book those parts as
        # 0/max before considering a rescale: they belong to the student, not spread
        # across the questions they did answer.
        if (abs(float(report.max_total) - float(total_marks)) > 0.25
                and fill_unanswered_parts(report, marks_scheme, float(total_marks))):
            print(f"[marks] {filename or 'sheet'}: booked unanswered part(s) of answered "
                  f"questions as 0 — denominator is now the official "
                  f"{float(total_marks):g} with every printed maximum intact", flush=True)
        if abs(float(report.max_total) - float(total_marks)) > 0.25:
            # Layer 3 is deterministic but APPROXIMATE — force_official_total's own
            # docstring says the per-question split it leaves behind is. On every one
            # of the nine archived 100-mark runs layer 2 raised "repair maxima sum to
            # N, not the official 100" (N was 96, 110, 111 and 120), so the smear was
            # the normal path on large papers, not an edge case, and it was silent:
            # the grand total came out exact while the marks under it had been
            # rescaled. Say so, so it reads as approximate rather than as measured.
            print(f"[marks] {filename or 'sheet'}: per-question maxima rescaled from "
                  f"{float(report.max_total):g} to {float(total_marks):g} — the total is "
                  f"exact but the split under it is approximate", flush=True)
            report = force_official_total(report, float(total_marks))
            report.maxima_rescaled = True

    # In strict mode no score may change after the second examiner has certified it.
    # Apply deterministic evidence contradictions first, then bind the certificate to
    # the resulting decisions. Legacy mode retains its historical post-audit cleanup.
    if cfg.is_strict:
        for note in enforce_evidence_against_awards(report, ocr_pages):
            print(f"[marks] {filename or 'sheet'}: {note}", flush=True)
        report = reconcile_report_to_package(
            report, cfg.exam_package, answer_map=answer_map,
            finalize_choices=not bool(cfg.exam_package.choice_groups))
        report.mandatory_verification_qids = visual_evidence_qids(qp_text, report)

    # Strict v2 selects high-risk questions before buying a text-only verification
    # call. False blanks are mandatory risks, so compute coverage before selection.
    report.false_blank_qids = find_unmarked_answers(report, ocr_pages)
    verification_complete = not cfg.is_strict
    verification_digest: str | None = None
    verification_qids: list[str] = []
    verification_omitted: list[str] = []

    # Moderation: in legacy mode this remains the existing second-examiner pass. In
    # strict mode it is a bounded evidence verifier over risk-selected questions; a
    # mandatory risk that cannot fit becomes UNGRADABLE rather than silently keeping
    # the primary model's answer.
    key_text = (ak_text_layer or ak_text or "").strip()
    check_awards = audits_awarded_marks(cfg)
    selected_norm: set[str] | None = None
    if cfg.is_strict:
        base_cost = compute_cost(
            usage,
            mathpix_pages=len(sa_pngs) if use_mathpix else 0,
            usd_to_inr=usd_to_inr,
        )
        if (not base_cost.rates_found or not base_cost.usage_attested
                or base_cost.total_inr >= hard_cap):
            raise StrictGradeError(
                "UNGRADABLE_BUDGET",
                f"Primary grading used Rs.{base_cost.total_inr:.2f} of the Rs.{hard_cap:g} "
                "cap, leaving no verified release path.",
            )
        verify_model_quote = audit_model_for(cfg)
        if cfg.is_claude:
            verify_model_quote = (_BUDGET_GRADING_MODEL
                                  if cfg.model == _LARGE_PAPER_GRADING_MODEL
                                  else _LARGE_PAPER_GRADING_MODEL)
        candidates = verification_candidates(report)
        risk_count = len(candidates)

        def evidence_page_numbers(qids: set[str]) -> set[int]:
            """Pages the independent examiner must actually see for these qids."""
            wanted = {_norm_qid(qid) for qid in qids}
            pages: set[int] = set()
            mapped = {
                _norm_qid(item.qid): item for item in answer_map.locations
            } if answer_map is not None else {}
            for question in report.questions:
                qid = _norm_qid(question.qid)
                if qid not in wanted:
                    continue
                try:
                    pages.add(int(question.page))
                except (TypeError, ValueError):
                    pass
                location = mapped.get(qid)
                pages.update(int(page) for page in
                             list(getattr(location, "pages", None) or []))
                anchors = [getattr(question, "anchor_line_id", "")] + [
                    getattr(criterion, "step_line_id", "")
                    for criterion in list(getattr(question, "criteria", None) or [])
                ]
                for anchor in anchors:
                    match = re.match(r"P(\d+)", str(anchor or ""), re.IGNORECASE)
                    if match:
                        pages.add(int(match.group(1)))
            return {page for page in pages if 1 <= page <= len(sa_pngs)}

        affordable = 0
        for count in range(1, risk_count + 1):
            output_limit = min(3000, 700 + 250 * count)
            quoted_qids = {item.qid for item in candidates[:count]}
            # A text-only challenger can repeat an OCR mistake. Give it the exact
            # answer pages for the rows it is certifying. At 150 dpi a full page is
            # conservatively quoted as 3k input tokens so the budget gate remains
            # fail-closed before the images leave the app.
            evidence_image_tokens = 3000 * len(evidence_page_numbers(quoted_qids))
            quote_prompt = build_audit_prompt(
                report,
                answer_key_text=key_text,
                ocr_transcript=ocr_transcript,
                question_paper_text=qp_text,
                include_awards=check_awards,
                selected_qids={_norm_qid(qid) for qid in quoted_qids},
            )
            # Quote the exact bounded prompt with a 20% tokenizer/schema margin,
            # plus conservative page-image tokens. The old fixed 50k-character
            # assumption rejected an actual Rs.6 Core paper before its diagram
            # checks could run.
            verify_input = int(math.ceil(len(quote_prompt) / 4.0 * 1.20)) \
                + 1000 + evidence_image_tokens
            quote = CallPlan(
                stage=Stage.VERIFY,
                provider=(CLAUDE if verify_model_quote.startswith("claude") else GEMINI),
                model=verify_model_quote,
                max_input_tokens=verify_input,
                max_output_tokens=output_limit,
                question_ids=tuple(item.qid for item in
                                   verification_candidates(report)[:count]),
                mandatory=True,
            ).quote_inr(usd_to_inr)
            if base_cost.total_inr + quote + policy.safety_inr <= hard_cap + 1e-6:
                affordable = count
            else:
                break
        verification_qids, verification_omitted = select_verification_qids(
            report, max_questions=affordable)
        selected_norm = {_norm_qid(qid) for qid in verification_qids}
        selected_keys = {_norm_qid(qid) for qid in verification_qids}
        uncovered_risks = [item.qid for item in candidates
                           if _norm_qid(item.qid) not in selected_keys]
        if verification_omitted or uncovered_risks:
            raise StrictGradeError(
                "UNGRADABLE_BUDGET",
                "Every policy-selected risk must be verified; the budget could not cover: "
                + ", ".join(uncovered_risks[:10]),
            )
        verification_complete = not verification_qids

    should_verify = bool(
        (cfg.is_strict or moderation_applies(cfg, subject))
        and has_audit_rows(report, check_awards, selected_norm)
    )
    if cfg.is_strict and not should_verify:
        stage("verifying", "No provider verification call was required by the risk policy")
    if should_verify and (not key_text or not (ocr_transcript or "").strip()):
        if cfg.is_strict:
            raise StrictGradeError(
                "VERIFICATION_MATERIAL_MISSING",
                "Automatic verification requires both official solution text and the "
                "student's OCR evidence; an unchanged guess is not a verified decision.",
            )
    elif should_verify:
        stage("verifying", "Checking high-risk marks against official evidence")
        audit_usage: dict = {}
        verification_transcript = ocr_transcript or ""
        verification_pngs: list[bytes] = []
        if cfg.is_strict and ocr_pages and selected_norm:
            evidence_pages: set[int] = set()
            available_line_ids = {
                str(getattr(line, "line_id", "") or "")
                for page in ocr_pages
                for line in list(getattr(page, "lines", None) or [])
            }
            mapped_locations = {
                _norm_qid(item.qid): item for item in answer_map.locations
            } if answer_map is not None else {}
            missing_evidence_qids: list[str] = []
            for question in report.questions:
                if _norm_qid(question.qid) not in selected_norm:
                    continue
                # An answer the student never wrote has no evidence to anchor to, and
                # no award to verify. On a paper with internal choice this is the
                # normal case, not a defect: the Corporate Laws master test asks for
                # any 2 of Q2/Q3/Q4, so the alternatives not taken are unattempted by
                # design, and demanding an anchor for them held the whole copy.
                # Credit on an unattempted answer is a real fault, and strict mode
                # already refuses that separately as unattempted-answer-has-credit.
                attempted = bool(getattr(question, "attempted", True))
                awarded = float(getattr(question, "score", 0.0) or 0.0)
                if not attempted and awarded <= 1e-9:
                    continue
                anchors = [getattr(question, "anchor_line_id", "")] + [
                    getattr(criterion, "step_line_id", "")
                    for criterion in list(getattr(question, "criteria", None) or [])
                ]
                anchors = [str(anchor or "").strip() for anchor in anchors if anchor]
                location = mapped_locations.get(_norm_qid(question.qid))
                mapped_anchors = list(getattr(location, "label_line_ids", None) or [])
                if not any(anchor in available_line_ids
                           for anchor in anchors + mapped_anchors):
                    missing_evidence_qids.append(question.qid)
                try:
                    evidence_pages.add(int(question.page))
                except (TypeError, ValueError):
                    pass
                for anchor in anchors + mapped_anchors:
                    match = re.match(r"P(\d+)", str(anchor or ""), re.IGNORECASE)
                    if match:
                        evidence_pages.add(int(match.group(1)))
            if missing_evidence_qids:
                raise StrictGradeError(
                    "VERIFICATION_EVIDENCE_UNBOUND",
                    "No deterministic student-evidence anchor exists for: "
                    + ", ".join(missing_evidence_qids[:12]),
                )
            if evidence_pages:
                verification_transcript = build_transcript([
                    page for page in ocr_pages if int(page.page) in evidence_pages
                ])
                verification_pngs = [
                    sa_pngs[page - 1] for page in sorted(evidence_pages)
                    if 1 <= page <= len(sa_pngs)
                ]
            if not verification_transcript.strip():
                raise StrictGradeError(
                    "VERIFICATION_EVIDENCE_MISSING",
                    "No student evidence could be isolated for the selected verification rows.",
                )
        try:
            audit = claude_audit if cfg.is_claude else gemini_audit
            verify_model = audit_model_for(cfg)
            if cfg.is_strict and cfg.is_claude:
                # Whole-report Opus moderation cannot fit the Rs.15/Rs.20 contract.
                # Use a bounded challenger: Sonnet for a Haiku primary, otherwise
                # Haiku over the selected evidence rows.
                verify_model = (_BUDGET_GRADING_MODEL
                                if cfg.model == _LARGE_PAPER_GRADING_MODEL
                                else _LARGE_PAPER_GRADING_MODEL)
            verdicts = audit(
                report,
                model=verify_model,
                google_token=cfg.google_token,
                task_id=cfg.task_id,
                filename=filename,
                answer_key_text=key_text,
                ocr_transcript=verification_transcript,
                question_paper_text=qp_text,
                student_evidence_pngs=verification_pngs,
                usage_out=audit_usage,
                include_awards=check_awards,
                selected_qids=selected_norm,
                max_output_tokens=(_verify_output_budget(len(verification_qids))
                                   if cfg.is_strict else 32000),
            )
            if cfg.is_strict:
                incomplete, reason_gaps = audit_completion_gaps(
                    report, verdicts, include_awards=check_awards,
                    selected_qids=selected_norm)
                if incomplete:
                    # A bounded repair, not a blind retry. The old second attempt
                    # re-sent the identical prompt under the identical output budget,
                    # so a reply that ran out of room the first time ran out of room
                    # the second time too, and the copy was refused over a missing
                    # sentence that nobody was ever going to write by hand. Name the
                    # rows that came back unusable and give the answer real room.
                    stage("verifying",
                          "The second examiner left rows unexplained; repairing automatically")
                    # A held copy is only actionable if the hold explains itself. The
                    # verdicts live in memory for the length of the job and are never
                    # persisted, so without this line a repair that ran and failed is
                    # indistinguishable from a repair that never ran at all.
                    structural = [problem for problem in incomplete
                                  if 'requires a concise' not in problem]
                    print(f"[moderation] {filename or 'sheet'}: repairing "
                          f"{len(reason_gaps)} unexplained row(s) "
                          f"{sorted(reason_gaps)}; structural={structural}",
                          flush=True)
                    retry_usage: dict = {}
                    repaired = audit(
                        report,
                        model=verify_model,
                        google_token=cfg.google_token,
                        task_id=cfg.task_id,
                        filename=filename,
                        answer_key_text=key_text,
                        ocr_transcript=verification_transcript,
                        question_paper_text=qp_text,
                        student_evidence_pngs=verification_pngs,
                        usage_out=retry_usage,
                        include_awards=check_awards,
                        selected_qids=selected_norm,
                        max_output_tokens=_verify_output_budget(
                            len(verification_qids), repair=True),
                        repair_rows=reason_gaps or None,
                    )
                    _merge_usage(audit_usage, retry_usage)
                    verdicts = _merge_audit_verdicts(verdicts, repaired)
                    incomplete, still_missing = audit_completion_gaps(
                        report, verdicts, include_awards=check_awards,
                        selected_qids=selected_norm)
                    print(f"[moderation] {filename or 'sheet'}: repair returned "
                          f"{len(repaired)} row(s); "
                          f"{len(reason_gaps) - len(still_missing)} of "
                          f"{len(reason_gaps)} explained"
                          + (f"; still unexplained {sorted(still_missing)}"
                             if still_missing else ""),
                          flush=True)
                if incomplete:
                    raise StrictGradeError(
                        "VERIFICATION_INCOMPLETE",
                        "Second examiner did not certify every requested decision: "
                        + "; ".join(incomplete[:6]),
                    )
            report, moved = apply_audit(
                report, verdicts, include_awards=check_awards,
                selected_qids=selected_norm)
            if moved:
                print(f"[moderation] moved {moved:+g} marks on {filename or 'sheet'}")
            verification_complete = True
        except StrictGradeError:
            raise
        except Exception as exc:
            traceback.print_exc()
            if cfg.is_strict:
                raise StrictGradeError(
                    "VERIFICATION_FAILED",
                    f"Mandatory automatic verification failed: {exc}",
                ) from exc
        finally:
            # A malformed/truncated verifier response was still a billable call.
            # Merge its attested usage even when strict parsing holds the copy.
            _merge_usage(usage, audit_usage)

    if cfg.is_strict and cfg.exam_package.choice_groups:
        # Keep every score-relevant attempted alternative through the challenger.
        # Only now may the deterministic official choice rule select what counts.
        report = reconcile_report_to_package(
            report, cfg.exam_package, answer_map=answer_map, finalize_choices=True)
    if cfg.is_strict:
        # Again, and this time it sticks. The same enforcement ran before the
        # challenger, but VERIFICATION may re-award a step it had zeroed, and the
        # certificate is bound below - so the copy was certified with the award
        # restored and then held by the release gate over the very contradiction
        # this layer had already resolved. A Corporate Laws copy lost three marks
        # to that round trip and was refused for it.
        #
        # Running here matches what the earlier call site says it is doing: apply
        # deterministic evidence contradictions FIRST, then bind the certificate to
        # the resulting decisions. Nothing is re-judged; credit is only removed
        # where the page contradicts the marker's own citation.
        for note in enforce_evidence_against_awards(report, ocr_pages):
            print(f"[marks] {filename or 'sheet'}: post-verification {note}", flush=True)
    if cfg.is_strict and verification_complete:
        verification_digest = report_decision_digest(report)

    # COVERAGE. Every layer above reads the model's REPORT; this is the only one that
    # goes back to the PAGE and asks whether an answer the marker called blank is
    # actually sitting there in the OCR. It is the check whose absence let a Class 12
    # Chemistry copy ship at 25/70 with twenty answered sub-parts — 25 marks — filed
    # as `attempted: false`. Nothing is re-scored by regex: strict mode sends the risk
    # to automatic verification and refuses release if it cannot be settled.
    # Re-run after verification because a verdict may have changed attempted/score
    # fields, and this value feeds the fail-closed release gate.
    report.false_blank_qids = find_unmarked_answers(report, ocr_pages)
    if report.false_blank_qids:
        shown = ", ".join(report.false_blank_qids[:6])
        more = (f" and {len(report.false_blank_qids) - 6} more"
                if len(report.false_blank_qids) > 6 else "")
        print(f"[coverage] {filename or 'sheet'}: {len(report.false_blank_qids)} "
              f"question(s) marked unattempted have writing on the sheet: "
              f"{shown}{more}", flush=True)

    # Surface, to the operator only, how many questions came back with no answer at
    # all. On a sheet the student visibly wrote on, a high count means the grader
    # MISSED answers rather than the student skipping them. Deliberately not printed
    # on the student's copy: their sheet already says "Not attempted" question by
    # question, and a bracketed machine notice there helps nobody.
    ungraded = int(getattr(report, "ungraded_count", 0) or 0)
    if ungraded:
        print(f"[grader] {ungraded} question(s) had no answer found on "
              f"{filename or 'sheet'} — filed as 'Not attempted'", flush=True)

    # FLOOR. Two archived rows read "AI 0/0 vs human 67.5/100" and "AI 0/0 vs human
    # 22/25": the report came back with no questions in it at all, and the pipeline
    # priced it, logged it inside budget and returned it as a graded copy. Every
    # other invariant here is about the DENOMINATOR being right; none of them notice
    # that there is nothing underneath it.
    if not report.questions:
        raise ValueError(
            f"{filename or 'sheet'}: the grader returned a report with no questions "
            f"— nothing was marked. Refusing to ship an empty copy."
        )

    # LAST LINE: the denominator invariant is asserted at the EXIT, not only midway.
    # One booklet in a sweep still shipped 25/27 on a 25-mark paper, and the mid-function
    # guards above could not be made to reproduce it — so the check is repeated here,
    # after every step that can touch the report. Whatever the cause, nothing leaves this
    # function graded out of the wrong total.
    if total_marks is not None and abs(float(report.max_total) - float(total_marks)) > 0.25:
        if cfg.is_strict:
            raise StrictGradeError(
                "FINAL_DENOMINATOR_MISMATCH",
                f"Final denominator {report.max_total:g} differs from official "
                f"{float(total_marks):g}; strict mode refuses to force it.",
            )
        print(f"[denominator] {report.max_total:g} != official {float(total_marks):g} "
              f"after enforcement — forcing", flush=True)
        report = force_official_total(report, float(total_marks))

    # Make the marks agree with the evidence the marker itself recorded, after
    # moderation has had its say. Three reviewer complaints on one Business Laws copy
    # survived three attempts to fix them by instruction, because they are all the
    # same failure: the marker sees the fault, writes it in the remark, and pays
    # anyway. This is not a fresh judgement — it only removes credit the marker's own
    # evidence contradicts.
    if not cfg.is_strict:
        for note in enforce_evidence_against_awards(report, ocr_pages):
            print(f"[marks] {filename or 'sheet'}: {note}", flush=True)

    # Last, once every mark is final: the inline annotations were written during
    # grading and know nothing of what moderation or the objective re-read changed.
    for note in prune_contradicted_annotations(report):
        print(f"[annotation] {filename or 'sheet'}: dropped {note}", flush=True)

    if cfg.is_strict:
        # Evidence enforcement and verification are both allowed to alter awards, but
        # never the official graph. Compile once more at the exit so every released
        # number is still bound to the immutable package.
        report = reconcile_report_to_package(
            report, cfg.exam_package, answer_map=answer_map, finalize_choices=True)
        if verification_digest != report_decision_digest(report):
            raise StrictGradeError(
                "POST_VERIFICATION_MUTATION",
                "A score-bearing decision changed after automatic verification; the "
                "verification certificate no longer matches the released report.",
            )

    # Scores are now final. Keep post-audit feedback short, aligned to deductions,
    # and useful even when the single-call grader left the cover comment blank.
    report = finalise_student_remarks(report)
    feedback_problems = report_remark_problems(report)
    if feedback_problems:
        if cfg.is_strict:
            raise StrictGradeError(
                "REMARK_QUALITY_FAILED",
                "Automatic re-marking did not produce actionable student feedback: "
                + "; ".join(feedback_problems[:8]),
            )
        raise ValueError(
            "Refusing to release non-actionable student feedback: "
            + "; ".join(feedback_problems[:8])
        )

    stage("aggregating", "Recomputing totals and pricing every provider call")
    mathpix_pages = len(sa_pngs) if use_mathpix else 0
    cost = compute_cost(usage, mathpix_pages=mathpix_pages, usd_to_inr=usd_to_inr)
    return {
        "report": report,
        "ocr_pages": ocr_pages,
        "usage": usage,
        "cost": cost,
        "verification_complete": verification_complete,
        "verification_qids": verification_qids,
        "budget": {
            "cap_inr": hard_cap,
            "actual_inr": float(cost.total_inr),
            "within_cap": bool(cost.rates_found and cost.usage_attested
                               and cost.total_inr <= hard_cap + 1e-6),
            "rates_found": bool(cost.rates_found),
            "usage_attested": bool(cost.usage_attested),
        },
        "answer_map": answer_map.as_dict() if answer_map is not None else None,
    }


def grade_sheet(
    *,
    qp_pngs: list[bytes],
    sa_bytes: bytes,
    filename: str,
    ak_pngs: list[bytes] | None,
    ak_text: str | None,
    marks_scheme: MarksScheme | None,
    cfg: ProviderConfig,
    student_class=None,
    subject=None,
    use_mathpix: bool = False,
    usd_to_inr: float = DEFAULT_USD_TO_INR,
    total_marks: float | None = None,
    qp_text: str = "",
    ak_text_layer: str = "",
    stage_callback=None,
    prior_usage: dict | None = None,
) -> dict:
    if stage_callback:
        stage_callback("normalizing", "Rendering and normalizing the uploaded answer sheet")
    sa_pngs = pdf_or_image_to_pngs(sa_bytes, filename)
    result = grade_prepared(
        qp_pngs=qp_pngs,
        sa_pngs=sa_pngs,
        ak_pngs=ak_pngs,
        ak_text=ak_text,
        marks_scheme=marks_scheme,
        cfg=cfg,
        filename=filename,
        student_class=student_class,
        subject=subject,
        use_mathpix=use_mathpix,
        usd_to_inr=usd_to_inr,
        total_marks=total_marks,
        qp_text=qp_text,
        ak_text_layer=ak_text_layer,
        stage_callback=stage_callback,
        prior_usage=prior_usage,
    )
    if stage_callback:
        stage_callback("rendering", "Rendering the evaluated copy")
    pdf_bytes = build_evaluated_pdf(
        student_pdf_bytes=sa_bytes,
        student_filename=filename,
        student_pngs=sa_pngs,
        report=result["report"],
        ocr_pages=result["ocr_pages"],
        mark_scale=mark_scale_for(cfg.exam_type, subject),
        # A teacher marks ON the answer. Making the strip the ONLY annotation
        # surface kept every tick off the handwriting "by construction" — and on the
        # released Economics copy that left thirteen ticks stacked in a column down
        # the margin and not one on a line the student had written. The strip still
        # carries each question's score and remark; what goes back on the sheet is
        # the tick, the cross and the step mark, beside the working that earned them.
        # The ink-clearance rule still applies to the one mark that can be misread as
        # the student's own figure — a circled numeral.
        safe_layout=False,
    )
    # Free release gate. Fourteen of the eighteen defects found on real evaluated
    # copies were mechanical — wrong denominator, a question graded twice under one
    # label, a tick on a partly-earned step, a remark printed over the line above.
    # None of those need a model, so none of them cost anything to catch.
    if stage_callback:
        stage_callback("release_check", "Running the fail-closed automatic release gate")
    layout_repaired = False
    try:
        from preflight import blocking, check_copy, summarise
        issues = check_copy(result["report"], pdf_bytes=pdf_bytes,
                            ocr_pages=result.get("ocr_pages"),
                            answer_map=result.get("answer_map"),
                            total_marks=total_marks, paper_text=qp_text or "")
        # A layout collision is not an academic failure. Re-render once in the
        # conservative margin-only mode and run the complete release gate again.
        # This repairs the copy automatically instead of returning an avoidable
        # "Ungradable" result to the user.
        repairable_layout_codes = {
            "mark-overlaps-writing", "text-overflow", "question-not-shown",
        }
        if any(i.code in repairable_layout_codes for i in blocking(issues)):
            if stage_callback:
                stage_callback("repairing_layout",
                               "Repairing annotation placement and checking the copy again")
            repaired_pdf = build_evaluated_pdf(
                student_pdf_bytes=sa_bytes,
                student_filename=filename,
                student_pngs=sa_pngs,
                report=result["report"],
                ocr_pages=result["ocr_pages"],
                mark_scale=mark_scale_for(cfg.exam_type, subject),
                safe_layout=True,
            )
            repaired_issues = check_copy(
                result["report"], pdf_bytes=repaired_pdf,
                ocr_pages=result.get("ocr_pages"),
                answer_map=result.get("answer_map"), total_marks=total_marks,
                paper_text=qp_text or "")
            old_layout_blocks = sum(
                1 for i in blocking(issues) if i.code in repairable_layout_codes)
            new_layout_blocks = sum(
                1 for i in blocking(repaired_issues) if i.code in repairable_layout_codes)
            if new_layout_blocks < old_layout_blocks:
                pdf_bytes = repaired_pdf
                issues = repaired_issues
                layout_repaired = True
        if issues:
            message = f"[preflight] {filename or 'sheet'}: {summarise(issues)}"
            try:
                print(message, flush=True)
            except UnicodeEncodeError:
                # Console/log encoding is operational telemetry, never grading
                # authority.  A Windows cp1252 stream cannot encode symbols such as
                # ₹; escape them rather than turning a valid release decision into a
                # PRECHECK_FAILED result.
                print(message.encode("ascii", "backslashreplace").decode("ascii"),
                      flush=True)
        blocked = blocking(issues)
    except Exception as exc:
        traceback.print_exc()
        # A broken safety check is itself a blocking safety defect.  The previous
        # behaviour treated an exception as a clean copy and therefore failed open.
        from preflight import Issue
        issues = [Issue("block", "preflight-error",
                        f"Automatic release checks failed: {exc}")]
        blocked = list(issues)

    strict_decision = None
    if cfg.is_strict:
        strict_decision = automatic_release_decision(
            package=cfg.exam_package,
            report=result["report"],
            preflight_issues=issues,
            cost=result["cost"],
            budget_cap_inr=float(result["budget"]["cap_inr"]),
            mandatory_verification_complete=bool(result["verification_complete"]),
        )
        if strict_decision.blockers:
            existing = {(str(getattr(issue, "code", "")),
                         str(getattr(issue, "detail", ""))) for issue in blocked}
            from preflight import Issue
            for blocker in strict_decision.blockers:
                key = (blocker.code, blocker.detail)
                if key not in existing:
                    issue = Issue("block", blocker.code, blocker.detail)
                    issues.append(issue)
                    blocked.append(issue)
                    existing.add(key)

    # The gate now has teeth. Until this line it computed a verdict, printed it, and
    # handed the copy back indistinguishable from a clean one — so a defect the gate
    # itself calls "must not reach a student" reached the student anyway. `release`
    # is that verdict as a value the caller cannot miss; the PDF remains an internal
    # artifact, while the server download endpoints deny it unless release is true.
    if blocked:
        print(f"[preflight] {filename or 'sheet'}: UNGRADABLE — {len(blocked)} blocking "
              f"issue(s); this copy will not be released",
              flush=True)

    # ocr_pages goes back with the rest: it is the only record of what was actually
    # ON the page, it has already been paid for, and without it a finished run cannot
    # be re-checked — every coverage question ("was this answer really blank?") needs
    # the page, not the report.
    release = bool(strict_decision.release if strict_decision is not None else not blocked)
    return {"report": result["report"], "cost": result["cost"], "pdf_bytes": pdf_bytes,
            "issues": issues, "blocking": blocked, "release": release,
            "ocr_pages": result.get("ocr_pages") or [],
            "verification_complete": result.get("verification_complete", False),
            "verification_qids": result.get("verification_qids") or [],
            "layout_repaired": layout_repaired,
            "budget": result.get("budget") or {},
            "answer_map": result.get("answer_map"),
            "package_id": cfg.exam_package.package_id if cfg.exam_package else None}
