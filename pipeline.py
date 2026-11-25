"""Provider-agnostic grading orchestration through the PW shared proxy."""
from __future__ import annotations

import os
import copy
import sys
import traceback
import io
import math
from types import SimpleNamespace

import categories
import pdf_safety
import re
from categories.base import IntakePolicy, MarksReadingPolicy
from dataclasses import dataclass, replace


def _tolerant_console() -> None:
    """Never let a log line decide a student's copy.

    A source or uvicorn run on Windows writes to a cp1252 stream, and one diagnostic
    print of a marks note containing "≥" raised UnicodeEncodeError inside
    grade_prepared: a graded, paid-for CA Business Laws copy came back as a failure.
    Unencodable characters are escaped instead. (The EXE's log tee already swallowed
    write errors; this covers every other entry point.)
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass


_tolerant_console()

from costs import (BUDGET_SPLIT_MARKS, DEFAULT_USD_TO_INR, budget_for,
                   compute_cost, record_usage_event)
from answer_map import answer_map_prompt_block, build_answer_map
from budgeting import BudgetPolicy, CallPlan, Stage
from exam_package import ExamPackage, package_prompt_block, normalize_qid, resolve_qid
from grader import (MarksScheme, MarksExtractionError, OutputLimitExceeded, EXACT_QUESTION_BATCH_SIZE,
                    marks_scheme_from_pdf, marks_scheme_from_section_headers,
                    pdf_or_image_to_pngs)
from grader import (Criterion,
    _is_commerce_subject,
    _norm_qid,
    apply_audit,
    audit_completion_gaps,
    audit_repair_rows,
    build_audit_prompt,
    apply_repair_corrections,
    detect_exam_type,
    fill_unanswered_parts,
    finalise_student_remarks,
    find_unmarked_answers,
    enforce_evidence_against_awards,
    enforce_commerce_point_order,
    prune_contradicted_annotations,
    report_is_stub,
    verify_objective_choices,
    force_official_total,
    has_audit_rows,
    humanise_remark,
    paper_total_from_text,
    pdf_text_layer, figure_pages_in_text,
    paper_has_choice,
    printed_tag_sum, extraction_reconciles_with_tags,
    scheme_contradicts_paper,
    scheme_sum_is_plausible,
    regex_scheme_is_trustworthy,
)
from grader import audit_deductions as claude_audit
from grader import check_paper_match as claude_check_paper
from grader import look_at_sheet as claude_look_at_sheet
from grader import extract_marks_scheme as claude_extract_marks
from grader import generate_rubric as claude_generate_rubric
from grader import grade_answer_sheet as claude_grade
from grader import grade_answer_sheet_chunked as claude_grade_chunked
from grader import repair_report_totals as claude_repair
from grader import read_objective_choices as claude_read_objectives
from mathpix import (build_transcript, ocr_all_pages, synthesize_diagram_regions,
                     overlay_anchors_on_png, ocr_page, mark_struck_lines, lines_run_vertically,
                     short_token_page,
                     question_labels_found)
from pdf_renderer import build_evaluated_pdf
from selective_verification import (select_verification_qids,
                                    verification_candidates,
                                    visual_evidence_qids)
from remark_quality import fragment_problem, remark_quality_problem, report_remark_problems
from rubrics import KEY_LAYOUT_NOTE, key_requires_visual_layout
from commerce_feedback import apply_commerce_feedback_guards
from paper_match import (BOOKLET_CHARS, MIN_BOOKLET_CHARS, MIN_PAPER_CHARS,
                         PAPER_CHARS, PAPER_CHECK_CLAUDE_MODEL, verified_mismatches,
                         wrong_paper_reason, typed_document_reason,
                         not_answer_sheet_reason, booklet_copies_paper,
                         paper_text_is_partial, booklet_script_mismatch,
                         first_read_flags, rejection_after_look, SHEET_LOOK_MODEL,
                         LOOK_PAGES, LOOK_EDGE)
from typed_sheets import (is_text_layer_page as _is_text_layer_page, link_only_reason,
                          text_layer_pages, too_many_pages_reason)
from strict_grading import (StrictGradeError, automatic_release_decision,
                            apply_official_objective_evidence, _choice_letter,
                            _observed_letter, option_texts_from_paper,
                            reconcile_report_to_package, report_decision_digest,
                            validate_question_marks)

CLAUDE = "claude"
GEMINI = "gemini"
GPT = "gpt"


def provider_of(model: str) -> str:
    """The proxy route a model id runs on (a label for plans and usage rows)."""
    m = str(model or "").strip().lower()
    if m.startswith("claude"):
        return CLAUDE
    if m.startswith("gpt"):
        return GPT
    return GEMINI

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
    provider: str = CLAUDE
    model: str = "claude-sonnet-4-6"
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
    enforce_budget: bool = True
    # Ask a model, before grading, whether the booklet answers this paper at all
    # (paper_match.py). Production turns it on with the strict contract in
    # server.py; a hand-built config (tests, tools) stays offline.
    paper_check: bool = False
    # Take a visual inventory of which answers are written, and where, before
    # grading (answer_inventory.py). Same switch policy as paper_check.
    answer_inventory: bool = False
    # On LANGUAGE papers only, underline wrong words that a vision reading and the
    # OCR agree on (language_marks.py). Same switch policy as paper_check.
    language_marks: bool = False
    # Exam category id (categories/): "ca", "commerce", "school", "vidyapeeth" or
    # "design", and how it was decided ("explicit" when the operator picked it).
    category: str = ""
    category_source: str = ""
    # Batch settings the operator chose (marking_settings.py): the language the
    # answers are written in ("" = English or Hindi) and Standard/Strict/Lenient steps.
    answer_language: str = ""
    leniency: str = "standard"

    def __post_init__(self):
        # Gemini is switched off (2026-09-18): the PW proxy rate-limited every Gemini
        # model for hours, which blocked marks verification and with it all grading.
        # Every config is Claude, whatever an old form, job record or tool asks for.
        if self.provider != CLAUDE or not str(self.model or "").startswith("claude"):
            self.provider, self.model = CLAUDE, "claude-sonnet-4-6"

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
# ChatGPT deployments the pw-app-kit of 2026-09-17 lists for /api/gpt/generate.
# What is live is GET /api/gpt/models; a missing deployment falls back to
# grader.GPT_SECOND_EXAMINER_FALLBACK at run time rather than failing the copy.
PROXY_GPT_MODELS = frozenset({
    "gpt-5.6-luna-ard-content",
    "gpt-5.6-terra-ard-content",
    "gpt-5.6-sol-ard-content",
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
    if m.startswith("gpt-") and m not in PROXY_GPT_MODELS:
        raise RuntimeError(
            f"{m!r} is not a ChatGPT deployment the PW proxy lists. "
            f"Available: {', '.join(sorted(PROXY_GPT_MODELS))}. Check GET /api/gpt/models "
            f"and add it to PROXY_GPT_MODELS."
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
    category: str = "",
) -> ProviderConfig:
    # Claude is the only provider. `provider` is still accepted so old forms and job
    # records keep working, but a Gemini request (or a Gemini model id) is ignored.
    provider = CLAUDE
    if not (model or "").strip().startswith("claude"):
        model = ""
    # The operator's category wins; otherwise the legacy exam type, otherwise the
    # class and subject fields (categories.resolve). Raises UnknownCategory.
    resolution = categories.resolve(category=category, exam_type=exam_type,
                                    student_class=student_class, subject=subject)
    resolved_exam = resolution.category.legacy_exam_type
    # No model asked for? Use the measured grader for the paper's size (opus is ~2x
    # the cost for no measured accuracy gain). An EXPLICIT Claude model always wins.
    chosen = (model or "").strip() or _grading_model_for_size(total_marks)
    return ProviderConfig(
        provider=provider,
        model=chosen,
        google_token=google_token,
        task_id=task_id,
        exam_type=resolved_exam,
        category=resolution.id,
        category_source=resolution.source,
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


def _category_of(cfg) -> str:
    """The config's exam category id; "" for a hand-built config without one."""
    return str(getattr(cfg, "category", "") or "")


def _intake_of(cfg) -> IntakePolicy:
    """The intake policy of the config's category (categories/<id>.py): the default
    (photographed handwriting, no page limit) for a hand-built config without one."""
    category = _category_of(cfg)
    return categories.get(category).intake if category else IntakePolicy()


def equal_shares(scheme, total: float):
    """The scheme with every top-level question an equal share of `total` in half
    marks, adding up exactly (the earlier questions take the odd half marks), and a
    question's parts sharing its share equally. None when the reading's own maxima are
    not near equal (the paper printed marks after all), or the total cannot be split.
    """
    items = list(getattr(scheme, "items", None) or [])
    units = round(float(total or 0) * 2)
    if not items or units <= 0 or abs(units / 2 - float(total)) > 1e-9:
        return None
    questions: dict[str, list] = {}
    for item in items:
        questions.setdefault(str(item.qid).split(".")[0], []).append(item)
    read = [sum(float(i.max) for i in parts) for parts in questions.values()]
    if max(read) - min(read) > 1.0 + 1e-9:
        return None

    def split(count: int, ways: int) -> list[int]:
        base, extra = divmod(count, ways)
        return [base + (1 if n < extra else 0) for n in range(ways)]
    shared = []
    for share, parts in zip(split(units, len(questions)), questions.values()):
        if share < len(parts):
            return None
        shared += [part.model_copy(update={"max": half / 2})
                   for part, half in zip(parts, split(share, len(parts)))]
    return scheme.model_copy(update={"items": shared, "total": units / 2})


def _marks_policy_of(cfg) -> MarksReadingPolicy:
    """The marks-reading policy of the config's category: the default for none."""
    category = _category_of(cfg)
    return categories.get(category).marks if category else MarksReadingPolicy()


def _objective_reads_by_qid(choices: dict, package) -> dict:
    """The key-blind objective readings keyed by the package's own qids.

    They are collected under grader._norm_qid, a loose key that flattens "Q1.1" to
    "11"; strict_grading and objective_locations look them up with exam_package's
    normalize_qid, which turns "11" into "q11" and never finds Q1.1. MEASURED on the
    Bihar Board Economics copies (10 Oct 2026): the ten MCQs of "1. Choose the correct
    option (i) to (x)" were read twice and the reads agreed, yet every one stayed
    unconfirmed (the copy was held) and their marks were drawn on one line."""
    if not choices or package is None:
        return choices
    out = {}
    for question in getattr(package, "questions", None) or []:
        key = _norm_qid(question.qid)
        if key in choices:
            out[question.qid] = choices[key]
    return out or choices


def _examiner_found_answers(report, rows, unverified_rows, package) -> list[str]:
    """Questions recorded as not attempted that the second examiner gave marks.

    Such a ruling cannot be applied (grader.audit_applicable_verdicts refuses credit
    on an unattempted row), so the first reading's 0 used to stand and the copy was
    released with it. MEASURED on Uday Bihar Board Accountancy copy 343f6e52
    (2026-10-10): Q11 was answered on a part of the photo the OCR never read; the
    re-read could not anchor it, the second examiner found it and gave it marks, and
    that ruling was dropped. A copy like that is held for faculty review. A ruling on
    an OR alternative the student did not take, while its sibling was answered, is the
    examiner crediting the taken answer twice, not a missed one."""
    from exam_package import normalize_qid
    found = []
    for row, problem in sorted((unverified_rows or {}).items()):
        if "still recorded as unattempted" not in str(problem) or not 0 <= row < len(rows):
            continue
        question = rows[row][0]
        if bool(getattr(question, "attempted", True)) or question.qid in found:
            continue
        found.append(question.qid)
    if not found:
        return []
    answered = {normalize_qid(q.qid) for q in (getattr(report, "questions", None) or [])
                if bool(getattr(q, "attempted", False))}
    kept = []
    for qid in found:
        key = normalize_qid(qid)
        siblings = set()
        for group in getattr(package, "choice_groups", None) or []:
            members = {normalize_qid(m) for option in group.options for m in option.question_ids}
            if key in members:
                siblings |= members - {key}
        if siblings & answered:
            continue
        kept.append(qid)
    return kept


def _category_rules(cfg) -> str:
    """The exam category's own marking rules, for the second examiner as well as the
    grader (categories/<id>.py PromptPolicy.extra_rules), with the batch's language and
    step marking settings (marking_settings.py) so both examiners mark alike."""
    import marking_settings
    settings = marking_settings.rules(getattr(cfg, "answer_language", "") or "",
                                      getattr(cfg, "leniency", "") or "standard")
    category = _category_of(cfg)
    rules = ""
    if category:
        try:
            rules = categories.get(category).prompt.extra_rules
        except categories.UnknownCategory:
            rules = ""
    return "\n".join(part for part in (rules.rstrip(), settings) if part)


def _category_rules_kwarg(cfg) -> dict:
    """{"category_rules": ...} for the second examiner, or {} when the category has
    none, so an examiner stand in without the parameter still works."""
    rules = _category_rules(cfg)
    return {"category_rules": rules} if rules.strip() else {}


def _repeated_question_hold(cfg) -> str | None:
    """Why a paper that prints one question twice holds its copies, or None.

    MEASURED 2026-10-22 (EMRS PGT Chemistry mock): Q15 repeats Q8 word for word, the
    model key itself wrote "(repeat of Q8)", and the copy was released at 33/60 with
    Q15 marked "not attempted" although the student had answered the question once.
    Whether that one answer counts twice, or Q15 leaves the total, is the faculty's
    decision. Questions with the same number (the parts of one question share its
    text) are not repeats; texts under 25 letters say too little to compare."""
    package = getattr(cfg, "exam_package", None)
    seen: dict[str, str] = {}
    repeats: list[tuple[str, str]] = []
    for question in list(getattr(package, "questions", None) or []):
        text = re.sub(r"[^\w]+", " ", str(getattr(question, "prompt_text", "") or "")).strip().casefold()
        top = re.match(r"(?i)q?(\d+)", str(question.qid).strip())
        if len(text) < 25 or not top:
            continue
        first = seen.get(text)
        if first is None:
            seen[text] = question.qid
        elif re.match(r"(?i)q?(\d+)", first).group(1) != top.group(1):
            repeats.append((first, question.qid))
    if not repeats:
        return None
    pairs = ", ".join(f"{later} repeats {earlier}" for earlier, later in repeats)
    return (f"The question paper prints the same question twice ({pairs}). Decide whether "
            "the one answer counts for both, or the repeat is left out of the total.")


def _category_review_hold(cfg, subject: str = "") -> str | None:
    """Why this paper's category sends every copy to faculty review, or None when its
    copies may be released automatically (categories/base.py ReviewPolicy). Copies
    in an answer language not yet checked on sample copies are held as well. A
    subject the category has measured against faculty marks (calibrated_subjects)
    is released like an auto_release category."""
    import marking_settings
    held = marking_settings.language_hold(getattr(cfg, "answer_language", "") or "")
    category = _category_of(cfg)
    if not category:
        return held
    try:
        review = categories.get(category).review
    except categories.UnknownCategory:
        return held
    if review.auto_release:
        return held
    calibrated = {str(s).strip().casefold() for s in (review.calibrated_subjects or ())}
    if str(subject or "").strip().casefold() in calibrated:
        return held
    return review.review_reason or "This exam category sends every copy to faculty review."


def _prepare_commerce_feedback(report, ocr_pages, cfg, subject: str = "") -> set[str]:
    """Apply evidence-only corrections and require the existing examiner's review."""
    if _category_of(cfg) != "commerce":
        return set()
    qids = {normalize_qid(q.qid): q.qid for q in report.questions}
    reasons_by_qid: dict[str, list[str]] = {}
    for qid, reasons in report.commerce_feedback_verification_reasons.items():
        target = qids.get(normalize_qid(qid), qid)
        previous = reasons_by_qid.setdefault(target, [])
        previous.extend(reason for reason in reasons if reason not in previous)
    report.commerce_feedback_verification_reasons = reasons_by_qid
    result = apply_commerce_feedback_guards(
        report, ocr_pages, category="commerce",
        subject=subject or getattr(cfg.exam_package, "subject", ""),
        exam_package=cfg.exam_package)
    if result.changed:
        from grader import _rebuild_section_totals
        _rebuild_section_totals(report)
    for qid, reason in result.verification_reasons.items():
        reasons = report.commerce_feedback_verification_reasons.setdefault(qid, [])
        if reason not in reasons:
            reasons.append(reason)
    enforce_commerce_point_order(report, ocr_pages, cfg.exam_package, category="commerce")
    required = set(result.verification_qids) | set(report.commerce_feedback_verification_reasons)
    required = {qids.get(normalize_qid(qid), qid) for qid in required}
    report.mandatory_verification_qids = sorted(
        {qids.get(normalize_qid(qid), qid) for qid in report.mandatory_verification_qids} | required)
    for note in result.notes:
        print(f"[commerce] {note}", flush=True)
    return required


def _commerce_certified_questions(report, verdicts, include_awards, selected_qids) -> set[str]:
    """A Commerce question is checked only when every requested row is certified."""
    from grader import _audit_rows
    rows = _audit_rows(report, include_awards, selected_qids)
    grouped: dict[str, set[int]] = {}
    for index, (question, _, _) in enumerate(rows):
        grouped.setdefault(question.qid, set()).add(index)
    by_row: dict[int, list] = {}
    for item in verdicts:
        by_row.setdefault(int(item.row), []).append(item)
    verified = {row for row, items in by_row.items()
                if len(items) == 1 and items[0].evidence_status == "verified"}
    return {qid for qid, indices in grouped.items() if indices and indices <= verified}


def _commerce_question_digests(report, qids) -> dict[str, str]:
    return {q.qid: report_decision_digest(SimpleNamespace(questions=[q]))
            for q in report.questions if q.qid in qids and float(q.max_score) > 0}


def _commerce_point_order_certificates(report, verdicts, include_awards, selected_qids) -> set[str]:
    """First-N order needs an explicit image ruling in addition to certified marks."""
    from grader import _audit_rows
    certified = _commerce_certified_questions(report, verdicts, include_awards, selected_qids)
    rows = _audit_rows(report, include_awards, selected_qids)
    confirmed_rows = {int(item.row) for item in verdicts
                      if getattr(item, "commerce_point_order_verified", False)}
    return {qid for qid in certified if all(
        index in confirmed_rows for index, (question, _, _) in enumerate(rows)
        if question.qid == qid)}


def _preserve_commerce_audit_feedback(verdicts) -> None:
    """Keep an examiner's concrete correction when margin shortening loses it."""
    for verdict in verdicts:
        if not verdict.reason:
            continue
        shortened = humanise_remark(verdict.reason, margin=True)
        if not (remark_quality_problem(shortened) or fragment_problem(shortened)):
            continue
        # Select only an existing actionable sentence; never invent a correction.
        original = humanise_remark(verdict.reason)
        for sentence in re.split(r"(?<=[.!?])\s+", original):
            candidate = humanise_remark(sentence, margin=True)
            if not (remark_quality_problem(candidate) or fragment_problem(candidate)):
                verdict.reason = candidate
                break


def _finish_commerce_feedback(report, ocr_pages, cfg, subject, required, certified,
                              certified_digests=None) -> None:
    """Never release a Commerce feedback check that was skipped or left uncertain."""
    if _category_of(cfg) != "commerce":
        return
    before = report_decision_digest(report)
    fresh_required = _prepare_commerce_feedback(report, ocr_pages, cfg, subject)
    active = {q.qid for q in report.questions if float(q.max_score) > 0}
    qids = {normalize_qid(qid): qid for qid in active}
    required = {qids.get(normalize_qid(qid), qid) for qid in required}
    certified = {qids.get(normalize_qid(qid), qid) for qid in certified}
    if certified_digests is not None:
        certified_digests = {qids.get(normalize_qid(qid), qid): digest
                             for qid, digest in certified_digests.items()}
    required = (set(required) | fresh_required) & active
    pending = required - set(certified)
    if report_decision_digest(report) != before:
        # A mechanical correction after verification invalidates its authority.
        pending.update(required)
    if certified_digests is not None:
        final_digests = _commerce_question_digests(report, set(certified))
        pending.update(qid for qid in required & set(certified)
                       if final_digests.get(qid) != certified_digests.get(qid))
    weak_feedback = [q.qid for q in report.questions
                     if q.qid in active and q.attempted
                     and float(q.score) < float(q.max_score) - 1e-9
                     and (remark_quality_problem(q.remark) or fragment_problem(q.remark))]
    if weak_feedback:
        reason = ("Commerce deduction feedback is missing, generic or incomplete for "
                  + ", ".join(sorted(weak_feedback))
                  + ". Supply a complete specific correction before release.")
        if reason not in report.review_reasons:
            report.review_reasons.append(reason)
    if pending:
        reason = ("Commerce feedback checks were not independently verified for "
                  + ", ".join(sorted(pending))
                  + ". Review the original answer, symbols, steps and correction before release.")
        if reason not in report.review_reasons:
            report.review_reasons.append(reason)


def _wrong_work_note(report, ocr_pages) -> str:
    """For a typed submission that holds plenty of work but earned almost nothing: the
    reviewer's note that it may be another assignment or the wrong file, or "".

    MEASURED 23 Oct 2026: a Statistics Basics copy linked a Statistics Advanced notebook,
    scored 0 of 200, and its only note said writing had not been read."""
    top = float(getattr(report, "max_total", 0) or 0)
    words = sum(len(str(line.text or "").split())
                for page in ocr_pages or [] for line in getattr(page, "lines", []) or [])
    if top <= 0 or float(report.total_score or 0) > 0.1 * top or words < 300:
        return ""
    return (f"Almost nothing in this submission answers the assignment's questions "
            f"({float(report.total_score or 0):g} of {top:g}) though it holds about {words} "
            "words of work. It may be another assignment or the wrong file: check it "
            "before releasing.")


_META_PHRASE_RE = re.compile(r"\s*\b(?:for|under|in) this criterion\b", re.IGNORECASE)


def _remarks_from_criteria(report) -> None:
    """A question that lost marks but carries no remark gets the reason its grader
    recorded for the largest lost criterion. In place; scores never change.

    PW SKILLS TEAM (22 Oct 2026): feedback must say why marks were lost. MEASURED on
    build 126: a React answer lost 5 of 20 with an empty remark, while its criterion
    evidence read "No separate component-composition benefit is stated"."""
    for question in report.questions:
        if question.score >= question.max_score - 1e-9 or (question.remark or "").strip():
            continue
        lost = sorted((c for c in question.criteria or [] if c.awarded < c.max - 1e-9),
                      key=lambda c: c.awarded - c.max)
        for criterion in lost:
            reason = _META_PHRASE_RE.sub("", str(criterion.evidence or "")).strip(" .")
            if reason:
                question.remark = reason + "."
                break


def mark_scale_for(exam_type: str, subject: str | None, category: str = "") -> float:
    """How big the inline ticks/circled marks should be for this paper.

    CA/CMA/CS booklets are dense ledgers and tightly-spaced legal prose, so
    school-sized marks bury the working; school commerce papers (Accountancy,
    Book-keeping) sit in between.
    """
    if category:
        return categories.mark_scale(categories.get(category), subject)
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
    return claude_generate_rubric(qp_pngs, **common)


def _extract_marks(
    qp_pngs,
    cfg: ProviderConfig,
    student_class=None,
    subject=None,
    filename: str = "",
    total_marks: float | None = None,
    paper_text: str = "",
    require_complete: bool = False,
    visual_recheck: bool = False,
    recovery_reason: str = "",
    tolerate_allocation_notes: bool = False,
    examiner_decides: bool = False,
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
    if visual_recheck:
        common.update(visual_recheck=True, recovery_reason=recovery_reason)
    if tolerate_allocation_notes:
        common.update(tolerate_allocation_notes=True)
    if examiner_decides:
        common.update(examiner_decides=True)
    convention = _marks_policy_of(cfg).unprinted_convention
    if convention:
        common.update(convention=convention)
    return claude_extract_marks(qp_pngs, **common)


def detect_marks_scheme(
    qp_bytes: bytes,
    filename: str,
    cfg: ProviderConfig | None = None,
    student_class=None,
    subject=None,
    total_marks: float | None = None,
    require_complete: bool = False,
    exam_type: str = "",
    ai_first: bool = False,
) -> tuple[MarksScheme | None, str]:
    """Use the free PDF text layer first and AI only for scanned papers.

    `ai_first` (the integrated workflow, operator 30 Oct 2026): the reader reads every paper,
    and decides what the paper leaves open rather than stopping for a teacher who is not
    there ("ai_decided"). The workflow keeps the free readings as its check and fallback.

    Professional (CA/CMA/CS) papers skip the regex entirely: their tags sit on
    separate lines under nested (a)(i)(ii) parts and the papers carry internal
    choice, both of which the regex mis-associates — the AI extractor reads the
    structure (and the "answer any N" instruction) correctly.
    """
    paper_text = pdf_text_layer(qp_bytes, filename)
    # The paper usually prints its own total ("Maximum Marks– 50"); an explicit
    # user-entered total still wins over it.
    official_total = total_marks or paper_total_from_text(paper_text)
    # Without a config (the free deterministic readings), an exam type the operator
    # chose still wins over the class and subject text.
    resolved_exam_type = (
        cfg.exam_type if cfg is not None
        else (exam_type or "").strip().lower() if (exam_type or "").strip().lower() in (
            "school", "professional")
        else detect_exam_type(student_class or "", subject or "")
    )
    professional = resolved_exam_type == "professional"
    choice_signal = paper_has_choice(paper_text, "")
    # Professional layouts and explicit choice instructions need a structural graph.
    # "Professional" is not itself proof that choice exists; the two extractors may
    # legitimately agree on an empty choice_groups list.
    needs_structural_graph = professional or choice_signal
    regex_scheme = (None if needs_structural_graph or ai_first
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
    # An OR inside one numbered question does not stop this: the headers price the
    # question once, and a total that adds up with every question counted rules out
    # an "answer any N" choice (it would exceed the total).
    section_scheme = (None if professional or ai_first else
                      marks_scheme_from_section_headers(paper_text, official_total))
    if section_scheme is not None:
        return section_scheme, "sections"
    if cfg is None:
        # Free-only mode: an untrusted regex reading is worse than none — grading
        # reads per-question marks itself and anchors on the official total.
        return None, "none"
    qp_pngs = pdf_or_image_to_pngs(qp_bytes, filename)
    rechecked = decided = False
    try:
        scheme = _extract_marks(
            qp_pngs, cfg, student_class, subject, filename, official_total, paper_text,
            require_complete,
        )
    except (MarksExtractionError, OutputLimitExceeded, ValueError) as exc:
        if not require_complete or not qp_pngs:
            raise
        # One bounded recovery, within the original task/accounting boundary.
        # Change the evidence and schema instead of repeating the failed prompt.
        # Access/rate/network errors are not caught and must not buy more calls.
        reason = (str(exc) if isinstance(exc, (MarksExtractionError, OutputLimitExceeded))
                  else "The previous reply was not a complete readable marks graph")
        print(f"[marks] {cfg.provider}: automatic visual recheck: {reason}", flush=True)
        try:
            scheme = _extract_marks(
                qp_pngs, cfg, student_class, subject, filename, official_total, paper_text,
                require_complete, visual_recheck=True, recovery_reason=reason,
            )
        except MarksExtractionError as again:
            if not ai_first:
                raise
            # MEASURED 30 Oct 2026, Board Subjective Test 10 (Maths): "each case study carries
            # 5 marks" over four numbered questions, refused on every reading, so 26 copies
            # waited for a person. Code below still needs the official total.
            print(f"[marks] {cfg.provider}: the reader decides what the paper leaves open: {again}", flush=True)
            scheme = _extract_marks(
                qp_pngs, cfg, student_class, subject, filename, official_total, paper_text,
                require_complete, visual_recheck=True, recovery_reason=str(again),
                tolerate_allocation_notes=True, examiner_decides=True,
            )
            decided = True
        rechecked = True
    # A choice paper whose rows, after the choice rules, miss the official total can
    # never be attested, so the teacher was left with nothing to grade. MEASURED on a
    # Parishram ISC Chemistry paper (marks printed only in section headers, one OR in
    # Q19): the reading counted both alternatives. The same one recheck, told what
    # did not add up; code still decides whether the new reading matches.
    if require_complete and official_total is not None and qp_pngs and not rechecked:
        from marks_attestation import choice_total_gap
        gap = choice_total_gap(scheme, official_total)
        if gap:
            print(f"[marks] {cfg.provider}: automatic visual recheck: {gap}", flush=True)
            try:
                # The reader's notes do not decide: the check just below keeps this
                # reading only when its rows and choice rules give the official total.
                second = _extract_marks(
                    qp_pngs, cfg, student_class, subject, filename, official_total,
                    paper_text, require_complete, visual_recheck=True, recovery_reason=gap,
                    tolerate_allocation_notes=True,
                )
            except (MarksExtractionError, OutputLimitExceeded, ValueError) as exc:
                print(f"[marks] recheck failed ({exc}); keeping the first reading", flush=True)
            else:
                if not choice_total_gap(second, official_total):
                    scheme = second
    from grader import split_product_tagged_rows
    scheme = split_product_tagged_rows(scheme, paper_text)
    # The tag VALUES in the text are exact even when their question association isn't,
    # so they verify the extraction: a disagreement means a part was dropped or merged.
    expected, tag_count = printed_tag_sum(paper_text)
    # A category whose papers print no marks (PW Skills: an equal share of the entered
    # total each) has the shares worked out here, not by the reader. MEASURED 23 Oct
    # 2026: a 35-question Data Toolkit paper of 100 marks came back totalling 90 and
    # was refused, though only the reader's arithmetic was wrong.
    if (_marks_policy_of(cfg).unprinted_convention and not tag_count and official_total
            and not (choice_signal or getattr(scheme, "choice_groups", None))):
        even = equal_shares(scheme, official_total)
        if even is not None and even.items != scheme.items:
            print(f"[marks] {filename or 'the paper'}: no marks printed, "
                  f"{len(even.items)} rows given equal shares of {float(official_total):g}",
                  flush=True)
            scheme = even
    got = sum(i.max for i in scheme.items)
    extracted_choice = bool(getattr(scheme, "choice_groups", None))
    has_choice = choice_signal or extracted_choice
    # ...but on a paper with internal choice the printed tags cover EVERY option,
    # so they necessarily add up to more than one student can score (129 marks of
    # tags on this 100-mark CA paper); and a block priced "1 mark each" prints no
    # tags at all (the Accountancy unit test: 32 of its 41 marks are tagged). The
    # test lives in grader.extraction_reconciles_with_tags with both allowances.
    if not extraction_reconciles_with_tags(scheme, paper_text, official_total, has_choice):
        # 1 Nov 2026: eight papers (1,980 copies) stopped with the reader's rows exactly on the
        # official total, as this text scan gave 92 for 80 on SST, 222 for 80 on English and 51
        # for 30 on Biology. Where the reader lands on the total, the scan does not outrank it.
        if not (ai_first and official_total is not None
                and abs(got - float(official_total)) <= 0.25):
            return scheme, "ai_mismatch"
        print(f"[marks] {filename or 'the paper'}: the reading gives the official total, the text "
              f"scan gives {expected:g}; keeping the reading", flush=True)
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
    return scheme, "ai_decided" if decided else "ai"


# A digital PDF yields roughly 1,500-3,000 characters of text per page; a scan yields
# almost none. Requiring this much per page is a reliable "the text layer really covers
# these pages" test, so we never drop images for a scanned or figure-only paper.
_TEXT_CHARS_PER_PAGE = 600


def _prefer_text_over_images(pngs, text: str):
    """(images_to_send, dropped?) — drop printed-page images when their text layer
    already covers every page, keeping only the pages pdf_text_layer marked as
    printing a figure (structures, reaction schemes, diagrams), which no text layer
    carries. Never call this for student handwriting."""
    if not pngs:
        return pngs, False
    if len((text or "").strip()) >= _TEXT_CHARS_PER_PAGE * len(pngs):
        figures = figure_pages_in_text(text)
        return [png for number, png in enumerate(pngs, start=1) if number in figures], True
    return pngs, False


def _prepare_answer_key(pngs, supplied_text, text_layer):
    """Keep visual step allocations through primary, recovery and verification."""
    text = supplied_text or (text_layer or "").strip()
    if pngs and not supplied_text and not key_requires_visual_layout(text):
        kept, dropped = _prefer_text_over_images(pngs, text)
        if dropped:
            return kept, text, True
    if pngs:
        text = KEY_LAYOUT_NOTE + ("\n\n" + text if text else "")
    return pngs, text, False


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


def _coverage_verification_reserve(report, missing, *, cfg, common, usd_to_inr):
    """Reserve a whole-copy evidence review, including decisions still to be read.

    Missing decisions have no prompt yet. Allow 180 input tokens per expected step
    on top of the actual audit prompt and all original page images. The final
    verifier still quotes its exact completed report before dispatch.
    """
    from grader import _audit_rows
    model = verifier_model_for(cfg, common.get("subject") or "")
    steps = sum(max(1, len(q.criteria), math.ceil(q.max_marks)) for q in missing)
    rows = len(_audit_rows(report, True)) + steps
    prompt = build_audit_prompt(report,
        answer_key_text=common.get("answer_key_text") or "",
        ocr_transcript=common.get("ocr_transcript") or "",
        question_paper_text=(common.get("qp_text") or "") + (common.get("booklet_notes") or ""),
        include_awards=True)
    return CallPlan(stage=Stage.VERIFY,
        provider=provider_of(model), model=model,
        max_input_tokens=math.ceil(len(prompt) * .30) + 1000
                         + 3000 * (len(common["student_pngs"])
                                   + len(common.get("answer_key_pngs") or [])) + 180 * steps,
        max_output_tokens=min(32000, _evidence_output_budget(report, None, model=model) + 160 * steps),
        mandatory=True).quote_inr(usd_to_inr)


_UPRIGHT_READ_FLOOR = 0.45
SPARSE_PAGE_LINES = 2          # a page OCR returns with this few lines is read again


def blank_scanned_page(png: bytes) -> bool:
    """A white scanned page with no handwriting on it: ruling and show-through only.

    MEASURED 2026-10-01 (CBSE Class 9 Maths/SST Test-03, 12 scanned booklets): the
    blank answer pages after the last answer came back from the OCR with 0 lines, so
    every copy was held "page(s) 4, 5, 6 could not be read". Ink = pixels more than 80
    below the page's own paper tone (show-through from the back of the sheet is only
    30 to 55 below it), per mille of the page, after the printed ruling (rows and
    columns a quarter dark at 30 below paper) and a 3% border are taken out. 23 blank
    pages measured at most 0.85 (the worst one carries a whole mirrored page of
    show-through that the OCR even read as 14 lines); written pages at least 2.6 (a
    light pencil map). One synthetic line of pen measures 1.2. Only a white scan
    qualifies (paper tone 225 or more): a faint or grey photo is never called blank,
    so the second read and the hold still apply to it.
    """
    try:
        import numpy as np
        from PIL import Image
        image = Image.open(io.BytesIO(png)).convert("L")
        width = 800
        image = image.resize((width, max(1, int(image.height * width / max(1, image.width)))))
        pixels = np.asarray(image, dtype=np.float32)
    except Exception:  # noqa: BLE001 - an unreadable image is never "blank"
        return False
    paper = float(np.median(pixels))
    if paper < 225:
        return False

    printed = pixels < paper - 30
    def thin_rules(profile):
        # A photograph on a white PDF canvas can make hundreds of consecutive
        # rows/columns dark. That is content, not a printed ruling line.
        candidates = profile > 0.25
        edges = np.flatnonzero(np.diff(np.r_[False, candidates, False]))
        result = np.zeros_like(candidates)
        for start, end in zip(edges[::2], edges[1::2]):
            if end - start <= 4:
                result[start:end] = True
        return result

    rows = thin_rules(printed.mean(axis=1))
    cols = thin_rules(printed.mean(axis=0))
    ruled_rows, ruled_cols = rows.copy(), cols.copy()
    for shift in (1, 2):
        ruled_rows |= np.roll(rows, shift) | np.roll(rows, -shift)
        ruled_cols |= np.roll(cols, shift) | np.roll(cols, -shift)
    ink = pixels < paper - 80
    ink[ruled_rows, :] = False
    ink[:, ruled_cols] = False
    border = int(0.03 * min(ink.shape))
    ink[:border] = ink[-border:] = False
    ink[:, :border] = ink[:, -border:] = False
    return float(ink.mean()) * 1000 < 1.0


def scanned_document_text(pngs: list[bytes], *, cfg, label: str = "document") -> str:
    """Text of a scanned question paper or answer key, read page by page by the OCR.

    Returns "" when nothing could be read (the caller's own checks then refuse the
    document as before). Page order is kept; each page's lines in reading order.
    """
    from concurrent.futures import ThreadPoolExecutor
    from mathpix import ocr_page

    if not pngs or not getattr(cfg, "google_token", ""):
        return ""

    def read(index: int) -> str:
        try:
            page = ocr_page(pngs[index], index + 1, google_token=cfg.google_token,
                            task_id=cfg.task_id)
        except Exception as exc:  # noqa: BLE001 - one unreadable page is not fatal
            print(f"[ocr] {label} page {index + 1} could not be read ({exc})", flush=True)
            return ""
        return "\n".join(line.text for line in page.lines)

    with ThreadPoolExecutor(max_workers=min(6, len(pngs))) as pool:
        pages = list(pool.map(read, range(len(pngs))))
    text = "\n\n".join(p for p in pages if p.strip())
    print(f"[ocr] scanned {label}: {len(pngs)} page(s) read by OCR, {len(text)} chars",
          flush=True)
    return text


def _script_share(page) -> float:
    """Share of a page's OCR letters written in its most common script (1.0 for one).

    Upside-down handwriting does not read as nothing: Mathpix returns a mix of
    scripts ("कि probocif puro turad", Devanagari, Latin and Cyrillic in one line).
    An upright page of English, Hindi or any one language reads in one script."""
    import unicodedata
    counts: dict[str, int] = {}
    for line in getattr(page, "lines", None) or []:
        if getattr(line, "kind", "text") not in ("text", "math"):
            continue
        for ch in str(getattr(line, "text", "") or ""):
            if ch.isalpha():
                name = unicodedata.name(ch, "")
                script = name.split(" ")[0] if name else "OTHER"
                counts[script] = counts.get(script, 0) + 1
    total = sum(counts.values())
    return max(counts.values()) / total if total else 0.0


# What the OCR text of the two reads proves on its own. MEASURED on a Parishram
# English copy (8 Oct 2026): pages 6 to 11 were photographed upside down; their
# upright reads had confidence 0.05 to 0.13 and only 52 to 75 percent of their
# letters in one script, every upright page of the batch read at 0.28 or more in a
# single script. The probe called all six pages upright, they were kept, and Q12 to
# Q15 were marked from pages nobody could read.
_NOISE_READABILITY = 0.2
_NOISE_SCRIPT_SHARE = 0.8
_CLEAN_SCRIPT_SHARE = 0.95
# How well a TURNED read must read to prove the turn. Not _UPRIGHT_READ_FLOOR (0.45):
# MEASURED on the Parishram English copy 0de112e9 (build 97, 10 Oct 2026), its
# upside-down pages read 0.46 and 0.53 once turned, pages 6, 8, 9 and 11 read below
# 0.45 and stayed upside down, and an upright page of the same copy read 0.28. On
# the Economics copy 67266cbc, page 2 of a booklet photographed sideways throughout
# was refused its neighbours' quarter turn on the same floor and stayed unread.
_TURNED_READ_FLOOR = 0.25


def _text_says_upside_down(upright, turned) -> bool:
    """The upright read is mixed-script noise and the turned read is clean text."""
    if upright is None or turned is None:
        return False
    return (_ocr_readability(upright) < _NOISE_READABILITY
            and _script_share(upright) < _NOISE_SCRIPT_SHARE
            and _ocr_readability(turned) >= max(_TURNED_READ_FLOOR,
                                                2 * _ocr_readability(upright))
            and _script_share(turned) >= _CLEAN_SCRIPT_SHARE
            and len(getattr(turned, "lines", None) or []) > SPARSE_PAGE_LINES)


def _confirmed_upside_down(png: bytes, index: int, cfg, filename, usage_out,
                           upright=None, turned=None) -> bool:
    """Whether a page whose upside-down read beat its upright read really is upside down.

    MEASURED 2026-10-01: mat4's upright page 4 (a short Q16 attempt, 2 OCR lines) read
    as 4 lines of gibberish when turned ("1=6+n", "τ(ε+c)", "です", two of them at 0.98
    confidence), so the page was flipped and the released copy showed it upside down
    with a tick on it. A line count cannot tell real lines from noise; the page image
    can. The model probe decides; if it fails, the free heuristic must name 180.
    """
    from io import BytesIO
    from PIL import Image
    import orient
    try:
        from grader import orientation_probe
        angle = int((orientation_probe([png], google_token=cfg.google_token,
                                       task_id=cfg.task_id, filename=filename,
                                       usage_out=usage_out) or [0])[0])
    except Exception as exc:  # noqa: BLE001 - the probe is advice
        print(f"[orient] page {index + 1}: probe failed ({exc}); checking the flip locally",
              flush=True)
        angle = orient.detect_rotation(Image.open(BytesIO(png)))
    if angle != 180 and _text_says_upside_down(upright, turned):
        # The probe is wrong here and the text is not: gibberish in several scripts
        # one way up, clean single-script text the other. (The mat4 false flip of
        # 2026-10-01 stays refused: its TURNED read was the mixed-script noise.)
        print(f"[ocr] page {index + 1}: the probe said {angle}, but the upright read is "
              f"mixed-script noise and the turned read is clean text; turned 180", flush=True)
        return True
    if angle != 180:
        print(f"[ocr] page {index + 1}: the upside-down read had more lines, but the page "
              f"is not upside down (probe {angle}); kept upright", flush=True)
    return angle == 180


def _ocr_readability(page) -> float:
    """Mean OCR confidence of a page's lines (0 when it has none)."""
    values = [float(line.confidence) for line in (getattr(page, "lines", None) or [])
              if getattr(line, "confidence", None) is not None]
    return sum(values) / len(values) if values else 0.0


# A two-page photo whose OCR lines are all centred on one side of this share of the
# width (or of 1 minus it) has a facing page the OCR never read.
_FACING_PAGE_SHARE = 0.55


def _unread_facing_page(page, width: int, height: int) -> tuple[int, int] | None:
    """(x0, x1) of the facing page of a two-page photo the OCR returned no line for."""
    from mathpix import SPREAD_ASPECT
    if not width or not height or width / height < SPREAD_ASPECT:
        return None
    lines = [line for line in (getattr(page, "lines", None) or [])
             if getattr(line, "kind", "text") != "synthetic_region"]
    if len(lines) <= SPARSE_PAGE_LINES:
        return None  # an almost empty page is read again whole
    centres = [(line.bbox[0] + line.bbox[2]) / 2 for line in lines]
    if max(centres) < _FACING_PAGE_SHARE * width:
        x0 = max(max(line.bbox[2] for line in lines), (1 - _FACING_PAGE_SHARE) * width)
        return (int(x0), int(width)) if width - x0 > 0.2 * width else None
    if min(centres) > (1 - _FACING_PAGE_SHARE) * width:
        x1 = min(min(line.bbox[0] for line in lines), _FACING_PAGE_SHARE * width)
        return (0, int(x1)) if x1 > 0.2 * width else None
    return None


def _read_unread_facing_pages(sa_pngs, ocr_pages, *, cfg, filename, skip=()) -> None:
    """Read the facing page of a two-page photo that the OCR left out.

    Parishram Chemistry, 2026-09-20 (Saptarshi, page 1): the photo held two notebook
    pages and Mathpix returned the 27 lines of the left page only. The right page
    (the rest of Q8, then Q9, Q10 and Q11) had no OCR line at all, so no answer map,
    inventory span or anchor could reach it, and Q8 was marked on its first half.
    That page is cropped, read once more, and its lines join the page after (or
    before) the lines already read, so the left page still reads first. One OCR call;
    a failed or empty read changes nothing.
    """
    from io import BytesIO
    from PIL import Image
    for index, page in enumerate(list(ocr_pages or [])):
        number = index + 1
        if index >= len(sa_pngs) or number in skip:
            continue
        try:
            image = Image.open(BytesIO(sa_pngs[index]))
            width, height = image.size
            span = _unread_facing_page(page, width, height)
            if span is None:
                continue
            x0, x1 = span
            buf = BytesIO()
            image.crop((x0, 0, x1, height)).convert("RGB").save(buf, format="PNG")
            crop = buf.getvalue()
            half = ocr_page(crop, number, google_token=cfg.google_token,
                            task_id=cfg.task_id, filename=filename)
            if not half.lines:
                continue
            mark_struck_lines(half, crop)
        except Exception as exc:  # noqa: BLE001 - a second read never costs a copy
            print(f"[ocr] page {number}: facing page not read again ({type(exc).__name__}: {exc})",
                  flush=True)
            continue
        pattern = re.compile(rf"P{number}([LD])(\d+)$")
        top = max((int(m.group(2)) for line in page.lines
                   if (m := pattern.match(str(line.line_id)))), default=0)
        for line in half.lines:
            m = pattern.match(str(line.line_id))
            if m:
                line.line_id = f"P{number}{m.group(1)}{top + int(m.group(2))}"
            line.bbox = (line.bbox[0] + x0, line.bbox[1], line.bbox[2] + x0, line.bbox[3])
        for word in half.words:
            word.bbox = (word.bbox[0] + x0, word.bbox[1], word.bbox[2] + x0, word.bbox[3])
        page.lines[:] = (list(page.lines) + list(half.lines) if x0 > 0
                         else list(half.lines) + list(page.lines))
        page.words = list(page.words) + list(half.words)
        print(f"[ocr] page {number}: two facing pages, OCR read one; read the "
              f"{'right' if x0 > 0 else 'left'} page again ({len(half.lines)} lines)", flush=True)


# On a facing page of a two-page photo, a stretch with no OCR line this tall (as a
# share of the photo's height) between two read lines, or above the first or below the
# last, is read again. Notebook lines are about 6% of such a photo apart.
_UNREAD_GAP_SHARE = 0.20
_UNREAD_END_SHARE = 0.30


def _unread_bands(page, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) of each stretch of a facing page of a two-page photo that the
    OCR returned no line for, while it read the rest of that page."""
    from mathpix import SPREAD_ASPECT, spread_fold
    if not width or not height or width / height < SPREAD_ASPECT:
        return []
    lines = [line for line in (getattr(page, "lines", None) or [])
             if getattr(line, "kind", "text") != "synthetic_region"]
    fold = spread_fold(lines, width)
    if fold is None:
        return []
    crops = []
    for x0, x1 in ((0, fold), (fold, width)):
        side = sorted((line for line in lines if x0 <= (line.bbox[0] + line.bbox[2]) / 2 < x1),
                      key=lambda line: line.bbox[1])
        if len(side) <= SPARSE_PAGE_LINES:
            continue  # a facing page the OCR left out is _read_unread_facing_pages' job
        if side[0].bbox[1] >= _UNREAD_END_SHARE * height:
            crops.append((int(x0), 0, int(x1), int(side[0].bbox[1])))
        reached = side[0].bbox[3]
        for line in side[1:]:
            if line.bbox[1] - reached >= _UNREAD_GAP_SHARE * height:
                crops.append((int(x0), int(reached), int(x1), int(line.bbox[1])))
            reached = max(reached, line.bbox[3])
        if height - reached >= _UNREAD_END_SHARE * height:
            crops.append((int(x0), int(reached), int(x1), int(height)))
    return crops


def _read_unread_bands(sa_pngs, ocr_pages, *, cfg, filename, skip=()) -> None:
    """Read again the stretches of a facing page that the OCR skipped.

    Uday Bihar Board Accountancy, 2026-10-10 (copy 343f6e52, one photo of two curved
    notebook pages): the OCR read the right page only to 40% of its height, and on the
    left page nothing between "Section - A" and Q5. Drawings (the end of Q10) and all of
    Q11 had no OCR line, the answer map could not reach Q11, and the re-read of "called
    blank but written" could not anchor it, so Q11 went out as not attempted (the
    faculty gave it 2/3). Q1 to Q4 were placed by the visual reader's rough box, which
    put their ticks at the top of the right page. Each such stretch is cropped and read
    once more, and its lines join that page in reading order. One OCR call for each
    stretch; a failed or empty read changes nothing.
    """
    from io import BytesIO
    from PIL import Image
    for index, page in enumerate(list(ocr_pages or [])):
        number = index + 1
        if index >= len(sa_pngs) or number in skip:
            continue
        try:
            image = Image.open(BytesIO(sa_pngs[index]))
            crops = _unread_bands(page, *image.size)
        except Exception as exc:  # noqa: BLE001 - a second read never costs a copy
            print(f"[ocr] page {number}: unread stretches not checked "
                  f"({type(exc).__name__}: {exc})", flush=True)
            continue
        for x0, y0, x1, y1 in crops:
            where = (f"{'left' if x0 == 0 else 'right'} page, "
                     f"{y0 / image.size[1]:.0%} to {y1 / image.size[1]:.0%} down")
            try:
                buf = BytesIO()
                image.crop((x0, y0, x1, y1)).convert("RGB").save(buf, format="PNG")
                crop = buf.getvalue()
                part = ocr_page(crop, number, google_token=cfg.google_token,
                                task_id=cfg.task_id, filename=filename)
                if not part.lines:
                    print(f"[ocr] page {number}: unread stretch read again ({where}); "
                          f"nothing written there", flush=True)
                    continue
                mark_struck_lines(part, crop)
            except Exception as exc:  # noqa: BLE001 - a second read never costs a copy
                print(f"[ocr] page {number}: unread stretch not read again ({where}; "
                      f"{type(exc).__name__}: {exc})", flush=True)
                continue
            pattern = re.compile(rf"P{number}([LD])(\d+)$")
            top = max((int(m.group(2)) for line in page.lines
                       if (m := pattern.match(str(line.line_id)))), default=0)
            for line in part.lines:
                m = pattern.match(str(line.line_id))
                if m:
                    line.line_id = f"P{number}{m.group(1)}{top + int(m.group(2))}"
                line.bbox = (line.bbox[0] + x0, line.bbox[1] + y0,
                             line.bbox[2] + x0, line.bbox[3] + y0)
            for word in part.words:
                word.bbox = (word.bbox[0] + x0, word.bbox[1] + y0,
                             word.bbox[2] + x0, word.bbox[3] + y0)
            # In reading order: before the first line of that page below the stretch,
            # or after its last line.
            on_side = [i for i, line in enumerate(page.lines)
                       if x0 <= (line.bbox[0] + line.bbox[2]) / 2 < x1
                       and getattr(line, "kind", "text") != "synthetic_region"]
            below = [i for i in on_side if page.lines[i].bbox[1] >= y1 - 1]
            at = min(below) if below else (max(on_side) + 1 if on_side else len(page.lines))
            page.lines[at:at] = list(part.lines)
            page.words = list(page.words) + list(part.words)
            print(f"[ocr] page {number}: the OCR skipped a stretch ({where}); read it again "
                  f"({len(part.lines)} lines)", flush=True)


def _reread_sideways_pages(sa_pngs, ocr_pages, *, cfg, filename, usage_out) -> dict[int, int]:
    """Right the pages the OCR shows were read sideways, and read them again.

    The orientation pass runs before anything sees the sheet, but its model probe
    can be wrong: on the Accountancy copy c0f259bb (2026-09-17) it turned two
    upright pages by 90 degrees, the grader then read a capital account written
    "sideways", and the released copy showed both pages on their side. The OCR
    itself is the check: a sideways page comes back as tall, narrow boxes
    (mathpix.lines_run_vertically). Such a page is shown to the probe on its own;
    a quarter turn it names is used, otherwise orient.sideways_angle picks the
    turn that reads left to right. The page image is replaced in `sa_pngs` IN
    PLACE, read again, and its new lines replace the old ones, so everything
    downstream (the grader, the marks, the released copy) sees it upright.
    Returns {page number: CCW angle} for the caller to rotate the source PDF.
    """
    from io import BytesIO
    from PIL import Image, ImageOps
    import orient
    rotated: dict[int, int] = {}
    for index, page in enumerate(list(ocr_pages or [])):
        sideways, tall, total = lines_run_vertically(page)
        # A page of short tokens - an MCQ answer list - gives the shape test nothing to
        # judge, and the free heuristic reads the notebook's printed rules as lines of
        # text. Neither says the page is sideways; neither can say it is upright either.
        # So ask the probe, and unlike the sideways case BELIEVE it when it says upright.
        unsure = (not sideways) and short_token_page(page)
        if (not sideways and not unsure) or index >= len(sa_pngs):
            continue
        angle = 0
        try:
            from grader import orientation_probe
            angle = int((orientation_probe([sa_pngs[index]], google_token=cfg.google_token,
                                           task_id=cfg.task_id, filename=filename,
                                           usage_out=usage_out) or [0])[0])
        except Exception as exc:  # the probe is advice; the OCR evidence decides
            print(f"[orient] page {index + 1}: probe failed ({exc}); choosing the turn locally", flush=True)
        if angle not in (90, 270):
            if unsure:
                # Nothing here says the page is sideways: the OCR geometry did not, and
                # the probe does not. Forcing a quarter turn on that would be inventing
                # a fault, so leave the page exactly as it is.
                print(f"[orient] page {index + 1}: short-token page, probe says upright; "
                      "left as it is", flush=True)
                continue
            angle = orient.sideways_angle(Image.open(BytesIO(sa_pngs[index])))
        source = ImageOps.exif_transpose(Image.open(BytesIO(sa_pngs[index])))

        def read_turned(turn):
            buf = BytesIO()
            source.rotate(turn, expand=True).convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
            return png, ocr_page(png, index + 1, google_token=cfg.google_token,
                                 task_id=cfg.task_id, filename=filename)

        upright, reread = read_turned(angle)
        if _ocr_readability(reread) < _UPRIGHT_READ_FLOOR:
            # The turn itself is a guess. CA Accounting and Advanced Accounting
            # copies (2026-09-19) came out UPSIDE DOWN: their cash book and
            # investment ledger were read as garbage and never graded. Upside-down
            # text reads with near-zero confidence, so one read of the opposite
            # quarter turn settles it; keep whichever reads better.
            other = 360 - angle
            other_png, other_read = read_turned(other)
            if _ocr_readability(other_read) > _ocr_readability(reread):
                print(f"[orient] page {index + 1}: {angle} read poorly; {other} reads "
                      "better, using it", flush=True)
                upright, reread, angle = other_png, other_read, other
        mark_struck_lines(reread, upright)
        sa_pngs[index] = upright
        ocr_pages[index] = reread
        rotated[index + 1] = angle
        print(f"[orient] page {index + 1}: OCR read {tall} of {total} lines as vertical; "
              f"rotated {angle} and read again ({len(reread.lines)} lines)", flush=True)

    # A page the OCR returned almost empty: read it again cropped to the paper and
    # contrast stretched, then upside down. Answers on such pages were graded "not
    # attempted" (Radiant Class 9 Maths, 2026-09-30: Shaarav's upside-down page 4,
    # faint photos on Akriti's pages 7 and 8). Each retry is one OCR call.
    from mathpix import ocr_page_enhanced

    def score(page):
        return len(getattr(page, "lines", None) or []) * max(0.05, _ocr_readability(page))

    for index, page in enumerate(list(ocr_pages or [])):
        if index >= len(sa_pngs) or (index + 1) in rotated:
            continue
        if len(page.lines) > SPARSE_PAGE_LINES and _ocr_readability(page) >= _UPRIGHT_READ_FLOOR:
            continue
        best, best_png, best_turn = page, sa_pngs[index], 0
        try:
            enhanced = ocr_page_enhanced(sa_pngs[index], index + 1, google_token=cfg.google_token,
                                         task_id=cfg.task_id, filename=filename)
            if score(enhanced) > score(best):
                best = enhanced
            if ((len(best.lines) <= SPARSE_PAGE_LINES
                 or _ocr_readability(best) < _UPRIGHT_READ_FLOOR)
                    and not blank_scanned_page(sa_pngs[index])):
                # An empty OCR result has no line geometry to trigger the first
                # sideways pass. Ask about the actual writing, then verify the
                # indicated quarter turn with a readable OCR result. Counts
                # alone cannot justify rotating faint upright handwriting.
                from grader import orientation_probe
                angle = int((orientation_probe([sa_pngs[index]],
                            google_token=cfg.google_token, task_id=cfg.task_id,
                            filename=filename, usage_out=usage_out) or [0])[0])
                if angle not in (90, 270):
                    # A booklet photographed sideways is sideways throughout. MEASURED
                    # on a Parishram Economics copy (8 Oct 2026): pages 1, 3 and 5 were
                    # turned 90 from their vertical lines, the probe called pages 2
                    # and 4 upright, they read 1 and 0 lines, and three answers became
                    # "not attempted". The neighbours' quarter turn is only a
                    # candidate: the reads below still have to prove it.
                    neighbours = [rotated.get(n) for n in (index, index + 2)]
                    angle = next((a for a in neighbours if a in (90, 270)), angle)
                if angle in (90, 270):
                    source = ImageOps.exif_transpose(Image.open(BytesIO(sa_pngs[index])))
                    candidates = []
                    for turn in (angle, 360 - angle):
                        buf = BytesIO()
                        source.rotate(turn, expand=True).convert("RGB").save(buf, format="PNG")
                        png = buf.getvalue()
                        try:
                            read = ocr_page_enhanced(png, index + 1,
                                google_token=cfg.google_token, task_id=cfg.task_id,
                                filename=filename)
                        except Exception as exc:
                            print(f"[ocr] page {index + 1}: optional {turn}-degree read "
                                  f"unavailable ({type(exc).__name__})", flush=True)
                            continue
                        if (len(read.lines) > SPARSE_PAGE_LINES
                                and _ocr_readability(read) >= _TURNED_READ_FLOOR
                                and not lines_run_vertically(read)[0]):
                            candidates.append((score(read), read, png, turn))
                    if candidates:
                        strength, read, png, turn = max(candidates, key=lambda c: c[0])
                        if strength > 1.5 * score(best):
                            best, best_png, best_turn = read, png, turn
            if score(best) < SPARSE_PAGE_LINES + 1 or _ocr_readability(best) < _UPRIGHT_READ_FLOOR:
                source = ImageOps.exif_transpose(Image.open(BytesIO(sa_pngs[index])))
                buf = BytesIO()
                source.rotate(180, expand=True).convert("RGB").save(buf, format="PNG")
                flipped = buf.getvalue()
                turned = ocr_page_enhanced(flipped, index + 1, google_token=cfg.google_token,
                                           task_id=cfg.task_id, filename=filename)
                if (score(turned) > 1.5 * score(best) and len(turned.lines) > SPARSE_PAGE_LINES
                        and _confirmed_upside_down(sa_pngs[index], index, cfg, filename,
                                                   usage_out, upright=best, turned=turned)):
                    best, best_png, best_turn = turned, flipped, 180
        except Exception as exc:  # noqa: BLE001 - a second read never costs a copy
            print(f"[ocr] page {index + 1}: second read skipped ({type(exc).__name__}: {exc})",
                  flush=True)
            # Keep a successful earlier enhanced/quarter-turn read if a later
            # optional read failed. The original page remains when none succeeded.
        if best is page:
            continue
        mark_struck_lines(best, best_png)
        ocr_pages[index] = best
        if best_turn:
            sa_pngs[index] = best_png
            rotated[index + 1] = best_turn
        print(f"[ocr] page {index + 1}: read {len(page.lines)} line(s) at first; "
              f"{f'rotated {best_turn} degrees, ' if best_turn else ''}cropped and enhanced read "
              f"{len(best.lines)}", flush=True)
    _read_unread_facing_pages(sa_pngs, ocr_pages, cfg=cfg, filename=filename, skip=rotated)
    _read_unread_bands(sa_pngs, ocr_pages, cfg=cfg, filename=filename, skip=rotated)
    # Still empty after both reads: a page the student left blank is not "unread"
    # (preflight._check_unread_pages holds the copy only for pages with ink).
    # MEASURED 2026-10-01: SSt1's blank page 4 carried the whole next page as
    # mirrored show-through, which the OCR read as 14 garbage lines (mean confidence
    # 0.13; the lowest written page in the batch was 0.41), and the grader bound Q1 to
    # "1. salo dowslard" there. A page with no ink whose lines are that unsure holds
    # show-through, not answers: its lines are dropped.
    for index, page in enumerate(list(ocr_pages or [])):
        if index >= len(sa_pngs):
            continue
        lines = list(page.lines)
        scores = [getattr(l, "confidence", None) for l in lines]
        unsure = (bool(lines) and None not in scores
                  and sum(float(s) for s in scores) / len(scores) < 0.3)
        if not (len(lines) <= SPARSE_PAGE_LINES or unsure):
            continue
        if not blank_scanned_page(sa_pngs[index]):
            continue
        page.blank = True
        if unsure:
            page.lines = []
            page.words = []
        else:
            lines = []
        print(f"[ocr] page {index + 1}: blank (no handwriting on a white scan"
              f"{f'; {len(lines)} show-through line(s) dropped' if lines else ''})", flush=True)
    return rotated


def _index_question_decisions(report, package):
    official = {normalize_qid(q.qid): q for q in package.questions}
    present = {}
    for question in report.questions:
        qid = normalize_qid(question.qid)
        if qid not in official:
            # A spelling of a package question, not a question the package lacks:
            # "Q10.alt1" for the package's "Q10" (exam_package.resolve_qid).
            resolved = resolve_qid(question.qid, [q.qid for q in package.questions])
            if resolved is None:
                raise StrictGradeError("UNKNOWN_QUESTION", f"The grader returned unknown question {question.qid}.")
            print(f"[grader] read {question.qid!r} as the package's {resolved!r}", flush=True)
            question.qid = resolved
            qid = normalize_qid(resolved)
        if qid in present:
            # One answer that runs over two pages can come back as two rows. The copy
            # used to be refused outright (Akshat's 15-page Maths copy, 2026-09-30:
            # "The grader returned Q11 more than once", nothing marked). Keep the
            # fuller reading: more steps judged, then the higher score.
            kept = present[qid]
            def weight(row):
                return (len(getattr(row, "criteria", None) or []), float(row.score or 0))
            if weight(question) > weight(kept):
                present[qid] = question
            print(f"[grader] {question.qid} came back twice; kept the fuller reading "
                  f"({float(present[qid].score or 0):g})", flush=True)
            continue
        present[qid] = question
    if len(present) < len(report.questions):
        keep = {id(row) for row in present.values()}
        report.questions = [row for row in report.questions if id(row) in keep]
    return official, present


def _complete_missing_questions(report, *, cfg, common,
                                policy, hard_cap, usd_to_inr, use_mathpix, stage):
    """Complete bounded required-key batches, reserving the entire recovery first."""
    official, present = _index_question_decisions(report, cfg.exam_package)
    missing = [q for qid, q in official.items() if qid not in present]
    if not missing:
        return report
    present.update(_request_question_decisions(report, missing, cfg=cfg, common=common,
        policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
        use_mathpix=use_mathpix, stage=stage))
    report.questions = [present[qid] for qid in official]
    report.coverage_recovered_qids = [q.qid for q in missing]
    return report


def _request_question_decisions(report, requested, *, cfg, common,
                               policy, hard_cap, usd_to_inr, use_mathpix, stage,
                               regrading=False, model_override=None):
    """Read requested decisions from original evidence in bounded complete batches."""
    from grader import build_system_prompt
    recovery_model = model_override or cfg.model
    recovery_provider = provider_of(recovery_model)
    # Recovery uses concise grading instructions plus the SAME subject-specific
    # marking rules. Reserve the actual recovery context, not the much longer
    # whole-report prompt used by the primary call. Original evidence is retained.
    recovery_input = _estimated_primary_input_tokens(
        qp_images=common.get("question_paper_pngs") or [],
        student_images=common["student_pngs"],
        key_images=common.get("answer_key_pngs") or [],
        qp_text=(common.get("qp_text") or "") + (common.get("booklet_notes") or ""),
        key_text=common.get("answer_key_text") or "",
        ocr_transcript=common.get("ocr_transcript") or "",
        marks_scheme=common.get("marks_scheme"))
    recovery_input += max(0, math.ceil(len(build_system_prompt(
        common.get("subject"), common.get("student_class"), cfg.exam_type,
        recovery=True)) / 3) - 2500) + 400
    batches = [requested[i:i + EXACT_QUESTION_BATCH_SIZE]
               for i in range(0, len(requested), EXACT_QUESTION_BATCH_SIZE)]
    plans = []
    minimum_outputs = []
    for batch in batches:
        steps = sum(max(1, len(q.criteria), math.ceil(q.max_marks)) for q in batch)
        output_limit = min(15000, max(1800, 900 + 450 * len(batch) + 260 * steps))
        output_floor = 1800
        output_limit = max(output_floor, output_limit)
        minimum_outputs.append(min(output_limit, max(output_floor, 600 + 250 * len(batch) + 180 * steps)))
        plans.append(CallPlan(stage=Stage.RETRY, provider=recovery_provider, model=recovery_model,
            max_input_tokens=recovery_input, max_output_tokens=output_limit,
            question_ids=tuple(q.qid for q in batch), mandatory=True))
    quotes = [plan.quote_inr(usd_to_inr) for plan in plans]
    verification_reserve = max(policy.verification_reserve_inr,
        _coverage_verification_reserve(report, requested, cfg=cfg, common=common, usd_to_inr=usd_to_inr))

    def available_for_recovery():
        spent = compute_cost(common["usage_out"],
            mathpix_pages=len(common["student_pngs"]) if use_mathpix else 0,
            usd_to_inr=usd_to_inr).total_inr
        # Coverage completion itself consumes the recovery allowance; do not
        # reserve it twice while still protecting the full independent review.
        return hard_cap - spent - verification_reserve - policy.safety_inr

    available = available_for_recovery()
    if sum(quotes) > available:
        # A reservation is an output ceiling, not the predicted bill. Fit the
        # generous ceilings to the money left, while retaining a per-question /
        # per-step floor and protecting the full independent review. Never shrink
        # a six-question response to the old one-question token allowance.
        def at_fraction(part):
            return [replace(plan, max_output_tokens=minimum +
                    (plan.max_output_tokens - minimum) * part // 1024)
                    for plan, minimum in zip(plans, minimum_outputs)]
        minimum_plans = at_fraction(0)
        if sum(plan.quote_inr(usd_to_inr) for plan in minimum_plans) <= available:
            low, high = 0, 1024
            while low < high:
                mid = (low + high + 1) // 2
                if sum(plan.quote_inr(usd_to_inr) for plan in at_fraction(mid)) <= available:
                    low = mid
                else:
                    high = mid - 1
            plans = at_fraction(low)
            quotes = [plan.quote_inr(usd_to_inr) for plan in plans]

    def require_remaining_plan(index):
        available = available_for_recovery()
        remaining = sum(quotes[index:])
        if remaining > available + 1e-6:
            raise StrictGradeError("COVERAGE_RECOVERY_BUDGET_DENIED",
                f"Completing all remaining questions needs Rs.{remaining:.2f}; "
                f"Rs.{max(0, available):.2f} remains after protecting the full verification pass. "
                "No further recovery calls were started.")

    # Do not consume most of the budget on Q2-Q5 and discover at Q6 that the
    # remaining decisions or their independent verifier cannot fit.
    require_remaining_plan(0)
    purpose = "grading-repair" if regrading else "coverage"
    print(f"[{purpose}] reading {len(requested)} question(s) in {len(batches)} batch(es); "
          f"recovery reservation Rs.{sum(quotes):.2f}, verification Rs.{verification_reserve:.2f}", flush=True)
    grade = claude_grade
    decisions = {}
    failure_code = "GRADING_RECOVERY_INCOMPLETE" if regrading else "COVERAGE_RECOVERY_INCOMPLETE"
    # Batches of the WHOLE paper (a long paper that ran out of output) are the same
    # batches on every copy, so the next copy reads them from the cache. Any other
    # recovery asks for this copy's own questions: nothing would read that entry back.
    package = getattr(cfg, "exam_package", None)
    whole_paper = (not regrading and package is not None and
                   {normalize_qid(q.qid) for q in requested}
                   == {normalize_qid(q.qid) for q in package.questions})
    batch_ttl = cfg.cache_ttl if whole_paper else ""
    for index, (batch, plan) in enumerate(zip(batches, plans)):
        if index:
            require_remaining_plan(index)
        wanted = {normalize_qid(q.qid) for q in batch}
        action = "Rechecking question marks" if regrading else "Completing missing questions"
        stage("primary_grading", f"{action}: {', '.join(plan.question_ids)}")
        try:
            recovered = grade(**{**common, "model": recovery_model,
                                 "question_qids": list(plan.question_ids),
                                 "max_output_tokens": plan.max_output_tokens,
                                 "reconcile_output": False, "cache_ttl": batch_ttl})
        except OutputLimitExceeded as exc:
            raise StrictGradeError(failure_code,
                                   "The question-recovery response was cut off before completion.") from exc
        received = [normalize_qid(q.qid) for q in recovered.questions]
        if len(received) != len(wanted) or set(received) != wanted:
            raise StrictGradeError(failure_code,
                                   "The recovery batch did not return exactly one decision for every requested question.")
        # Recovered rows remain provisional. They pass through the same rubric,
        # objective, evidence, independent moderation and release checks below.
        for question in recovered.questions:
            decisions[normalize_qid(question.qid)] = question
    return decisions


def _graded_position(question) -> tuple[int, int] | None:
    """Where the grader says this answer starts: (page, y in ten-thousandths)."""
    try:
        page = int(getattr(question, "page", 0) or 0)
        y = float(getattr(question, "y_fraction", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if page < 1 or not (0.0 <= y <= 1.0):
        return None
    return (page, int(round(y * 10000)))


def _unlocated_surplus_attempts(rows, package, regions):
    """Locate the claimed extra alternatives that cannot yet be ordered."""
    if not regions:
        return {}
    problems = {}
    from strict_grading import effective_selection_policy
    for group in package.choice_groups:
        if effective_selection_policy(group, package) != "first_attempted":
            continue
        active = []
        for option in group.options:
            attempted = [normalize_qid(qid) for qid in option.question_ids
                         if normalize_qid(qid) in rows
                         and rows[normalize_qid(qid)].attempted]
            if attempted:
                # A label the map located, or the grader's own reading of where
                # the answer starts (page and y_fraction, which the renderer also
                # trusts). An OR alternative the paper leaves unlabelled has no
                # label of its own to locate, so on the Accountancy unit test every
                # alternative read as "surplus" and the copy bought a Rs.32
                # re-grade that could not tell it anything new (2026-09-17).
                located = any(regions.get(qid, {}).get("status") == "located"
                              and regions[qid].get("first_position") for qid in attempted)
                located = located or any(_graded_position(rows[qid]) is not None
                                         for qid in attempted)
                active.append((attempted, located))
        if len(active) > group.select_count:
            for attempted, located in active:
                if not located:
                    for qid in attempted:
                        problems[qid] = StrictGradeError("CHOICE_ORDER_UNRESOLVED",
                            f"{rows[qid].qid}: this claimed surplus alternative has no located "
                            "answer label. Re-read all pages and establish whether it was actually "
                            "attempted; do not reuse an answer to a different alternative. "
                            "Only a visually confirmed blank may be returned as unattempted.")
    return problems


_OPTION_CRITERION_RE = re.compile(r"^\s*option\s*\(?([A-Da-d])\)?\s*[:.\-]", re.IGNORECASE)
_OPTION_LETTER_RE = re.compile(r"^\s*\(?\s*(?:option\s*)?([A-Da-d])\s*[).:\-]")


def _same_written_answer(first, second, regions) -> bool:
    """Whether two graded rows point at the same writing on the sheet."""
    a = str(getattr(first, "anchor_line_id", "") or "").strip()
    b = str(getattr(second, "anchor_line_id", "") or "").strip()
    if a and b and a == b:
        return True
    ra = set((regions.get(normalize_qid(first.qid)) or {}).get("line_ids") or [])
    rb = set((regions.get(normalize_qid(second.qid)) or {}).get("line_ids") or [])
    if ra and rb and (ra & rb):
        return True
    pa, pb = _graded_position(first), _graded_position(second)
    return (pa is not None and pb is not None and pa[0] == pb[0]
            and abs(pa[1] - pb[1]) <= 800)


def _collapse_shared_alternative_attempts(report, package) -> list[str]:
    """One written answer counts under ONE alternative of an OR pair.

    The paper leaves its alternatives unlabelled, so the grader read the same
    answer under both and scored it against both keys: on c0f259bb Q12's
    adjustment entry scored 0/4 as "capital and current accounts" and its real
    alternative was dropped by the choice rule. Among attempted alternatives that
    share one piece of writing, the one the answer actually scored on stays and
    the others become unattempted. A wrong key scores near nothing, so the score
    is the grader's own verdict on which alternative was answered; ties keep the
    paper's order and are reported for the teacher.
    """
    notes: list[str] = []
    rows = {normalize_qid(q.qid): q for q in report.questions}
    regions = getattr(report, "answer_regions", None) or {}
    for group in getattr(package, "choice_groups", None) or []:
        if int(getattr(group, "select_count", 1) or 1) != 1:
            continue
        options = []
        for option in group.options:
            qids = [normalize_qid(q) for q in option.question_ids]
            attempted = [rows[q] for q in qids if q in rows and getattr(rows[q], "attempted", True)]
            if attempted:
                options.append(attempted)
        if len(options) < 2:
            continue
        # every attempted option shares its writing with every other one
        heads = [rows_[0] for rows_ in options]
        if not all(_same_written_answer(heads[i], heads[j], regions)
                   for i in range(len(heads)) for j in range(i + 1, len(heads))):
            continue
        scored = sorted(options, key=lambda rows_: -sum(float(q.score or 0.0) for q in rows_))
        best, rest = scored[0], scored[1:]
        best_total = sum(float(q.score or 0.0) for q in best)
        if rest and sum(float(q.score or 0.0) for q in rest[0]) == best_total:
            notes.append(f"{group.group_id}: one answer scored the same under two alternatives; "
                         f"kept {', '.join(q.qid for q in best)}")
        for rows_ in rest:
            for q in rows_:
                q.attempted = False
                q.score = 0.0
                q.remark = ""
                for c in list(getattr(q, "criteria", None) or []):
                    c.awarded = 0.0
                    c.met = False
        print(f"[choice] {group.group_id}: one written answer, kept {', '.join(q.qid for q in best)} "
              f"({best_total:g}); {', '.join(q.qid for rows_ in rest for q in rows_)} unattempted",
              flush=True)
    return notes


def _clear_phantom_attempts(report, cfg) -> list[str]:
    """A zero-mark "attempt" resting only on another answer's writing is not attempted.

    preflight.phantom_attempts says which, from the answer map's regions. On CA copies
    (faculty review, 17-18 Oct 2026) such phantoms drew crosses on other answers and,
    on "answer any N" papers, took a place from an answer the student did write: Q4
    dropped for a phantom Q3, the written Q3(a) and Q3(b) "not counted" for a phantom
    Q4(a). The score is 0 either way. Runs before the choice rule counts attempts, and
    again after the regrade. CA papers only for now.
    """
    if _category_of(cfg) != "ca":
        return []
    from preflight import phantom_attempts
    found = phantom_attempts(report)
    if not found:
        return []
    for q in report.questions:
        if normalize_qid(q.qid) in found:
            q.attempted = False
            q.score = 0.0
            q.remark = "Not attempted"
            q.criteria = []
            q.anchor_line_id = ""
    print("[phantom] not attempted: " + "; ".join(found.values()), flush=True)
    return list(found)


def _settle_option_questions(report) -> list[str]:
    """A one-mark option question is 1 or 0 by the grader's own evidence.

    The grader names the key's option in the criterion ("Option B: ...") and quotes
    the student's letter in the evidence ("A) Both A and R ..."). When both are
    readable, they decide: Q4 on c0f259bb was awarded 1 for option B with the
    student's A quoted, and Q9 got 0.5 for the right letter because an OCR
    fraction misread the working. Only rows with exactly one criterion whose
    maximum is the whole one mark are touched.
    """
    notes: list[str] = []
    for q in report.questions:
        try:
            if float(q.max_score) != 1.0 or not getattr(q, "attempted", True):
                continue
        except (TypeError, ValueError):
            continue
        criteria = list(getattr(q, "criteria", None) or [])
        if len(criteria) != 1 or float(criteria[0].max or 0) != 1.0:
            continue
        c = criteria[0]
        key = _OPTION_CRITERION_RE.match(str(c.description or ""))
        if not key:
            continue
        student = str(getattr(q, "chosen_option", "") or "").strip().upper()[:1]
        if student not in "ABCD" or not student:
            quoted = _OPTION_LETTER_RE.match(str(c.evidence or c.anchor_quote or ""))
            student = quoted.group(1).upper() if quoted else ""
        if not student:
            continue
        want = 1.0 if student == key.group(1).upper() else 0.0
        if float(q.score or 0.0) != want:
            notes.append(f"{q.qid}: option {student} against the key's {key.group(1).upper()}: "
                         f"{float(q.score or 0):g} -> {want:g}")
            print(f"[objective] {q.qid}: student wrote option {student}, key option "
                  f"{key.group(1).upper()}; score {float(q.score or 0):g} -> {want:g}", flush=True)
            q.score = want
            c.awarded = want
            c.met = want >= 1.0
            q.chosen_option = student
            q.is_correct = want >= 1.0
            if want >= 1.0:
                q.remark = ""
        # A row settled by its option letter IS an option question, whatever the
        # marks extraction typed it: the renderer then marks it as one and lists
        # it in the margin in the order it was written (Q4 sat below Q3).
        q.objective = True
        q.chosen_option = student
    return notes


_UNCOVERED_STEP = "Remaining required steps not shown in the answer"


def _record_uncovered_steps(decisions, official, unresolved) -> None:
    """Last resort after two readings: book a breakdown's missing marks at zero.

    Both the grading model and a stronger alternate reader returned steps that
    cover less than the question's marks (CA keys print step marks in scattered
    columns, "1, 0.5, 0.5, 5.5 ..."), so the whole copy was held. The shortfall is
    recorded as one explicit step worth the missing marks and awarded NOTHING: the
    score stays exactly what the readers found, it can never rise, and the
    breakdown now accounts for every mark. Only a free-form breakdown that falls
    SHORT is completed; fixed rubrics, over-allocation and every other defect
    still hold the copy.
    """
    for qid, message in list(unresolved.items()):
        question, spec = decisions.get(qid), official.get(qid)
        if question is None or spec is None or spec.criteria:
            continue
        if "step maxima total" not in message:
            continue
        criteria = list(getattr(question, "criteria", None) or [])
        shortfall = float(spec.max_marks) - sum(float(c.max) for c in criteria)
        if criteria and shortfall < 0:
            # Over-allocated (Q3.c 2.5 of 2): take the excess from marks nobody was
            # awarded. An award above the question's marks is a real error and holds.
            excess = -shortfall
            if sum(float(c.awarded) for c in criteria) > float(spec.max_marks) + 1e-6:
                continue
            for c in sorted(criteria, key=lambda c: float(c.max) - float(c.awarded), reverse=True):
                cut = min(excess, float(c.max) - float(c.awarded))
                if cut > 0:
                    c.max = round(float(c.max) - cut, 2)
                    c.met = float(c.awarded) >= float(c.max)
                    excess -= cut
            question.criteria = [c for c in criteria if float(c.max) > 0]
            print(f"[grading-repair] {qid}: {-shortfall:g} unawarded step mark(s) trimmed "
                  "to the question's marks after two readings", flush=True)
            continue
        if not criteria or shortfall <= 0 or shortfall > float(spec.max_marks) / 2:
            continue
        question.criteria = criteria + [Criterion(
            description=_UNCOVERED_STEP, evidence_kind="missing",
            awarded=0.0, max=round(shortfall, 2), met=False,
            evidence="No working for these steps was found in the answer.")]
        print(f"[grading-repair] {qid}: {shortfall:g} uncovered mark(s) recorded at zero "
              "after two readings", flush=True)


def _clamp_to_valid_marks(question, cap: float) -> None:
    """Bring a held question's marks inside the bounds the final check enforces.

    MEASURED 2026-10-06 (CA Inter Advanced Accounting, live): after two readings Q2
    still had a step outside 0 to its maximum, and the clamp here only lowered awards
    ABOVE the maximum. The final compiler then refused the whole copy ("Q2: invalid
    criterion award"), so it reached faculty with no marks and no PDF. A step below
    zero is read as zero and a step with no usable maximum carries no mark. Nothing is
    ever raised, and the copy still goes to faculty review with the reason.
    MEASURED 2026-10-10 (Parishram Political Science Q14, two readings): the steps
    were valid one by one but their maxima totalled 7 of 6, and the final check
    refused the copy the same way. The maxima are now fitted to the question's
    marks too: unawarded room is trimmed first, then the last awards, so the score
    is never above the marks; a breakdown that falls short gets a zero step.
    """
    score = float(question.score or 0.0)
    question.score = min(max(score if math.isfinite(score) else 0.0, 0.0), cap)
    kept = []
    for criterion in list(getattr(question, "criteria", None) or []):
        top = float(criterion.max or 0.0)
        if not math.isfinite(top) or top < 0:
            continue
        award = float(criterion.awarded or 0.0)
        criterion.awarded = min(max(award if math.isfinite(award) else 0.0, 0.0), top)
        kept.append(criterion)
    if not kept:
        question.criteria = kept
        return
    excess = round(sum(float(c.max) for c in kept) - cap, 4)
    for c in sorted(kept, key=lambda c: float(c.max) - float(c.awarded), reverse=True):
        cut = min(excess, float(c.max) - float(c.awarded))
        if cut > 1e-9:
            c.max = round(float(c.max) - cut, 4)
            excess = round(excess - cut, 4)
    for c in reversed(kept):
        if excess <= 1e-9:
            break
        cut = min(excess, float(c.max))
        c.max = round(float(c.max) - cut, 4)
        c.awarded = min(float(c.awarded), c.max)
        excess = round(excess - cut, 4)
    for c in kept:
        c.met = float(c.awarded) >= float(c.max) - 1e-9
    kept = [c for c in kept if float(c.max) > 0]
    shortfall = round(cap - sum(float(c.max) for c in kept), 4)
    if shortfall > 1e-9:
        kept.append(Criterion(description=_UNCOVERED_STEP, evidence_kind="missing",
                              awarded=0.0, max=shortfall, met=False,
                              evidence="No working for these steps was found in the answer."))
    question.criteria = kept


def _regrade_invalid_questions(report, *, cfg, common,
                              policy, hard_cap, usd_to_inr, use_mathpix, stage):
    """Regrade invalid breakdowns; allow one alternate reader for visual keys.

    Valid decisions are preserved. No step weights are rescaled, no award is
    assumed, and no partially repaired batch is merged into the report.
    """
    official, present = _index_question_decisions(report, cfg.exam_package)
    problems = {}
    for qid, question in present.items():
        try:
            validate_question_marks(question, official[qid])
        except StrictGradeError as exc:
            problems[qid] = exc
    problems.update(_unlocated_surplus_attempts(present, cfg.exam_package, report.answer_regions))
    # One piece of writing paid under two questions (preflight.double_credited): the
    # question resting on another answer's lines is marked again on its own writing.
    from preflight import credit_without_own_answer, double_credited
    for qid, why in double_credited(report).items():
        if qid in present and qid not in problems:
            problems[qid] = StrictGradeError("DOUBLE_CREDIT", why)
    # Marks for a part with no answer of its own (CA faculty, 18 Oct 2026: Business
    # Laws f87b56cc paid Q1(a) 2 marks with no Q1(a) anywhere on the copy), and one
    # answer paid on both photos of a page photographed twice. CA papers only for now.
    if _category_of(cfg) == "ca":
        for qid, why in credit_without_own_answer(report).items():
            if qid in present and qid not in problems:
                problems[qid] = StrictGradeError(
                    "CREDIT_WITHOUT_OWN_ANSWER",
                    why + ". Mark it only on writing that answers it, or as not attempted.")
        from repeated_pages import paid_twice
        for qid, why in paid_twice(report, common.get("ocr_pages")).items():
            if qid in present and qid not in problems:
                problems[qid] = StrictGradeError("REPEATED_PAGE", why)
    if not problems:
        return report
    requested = [spec for qid, spec in official.items() if qid in problems]
    reasons = "\n".join(str(problems[normalize_qid(spec.qid)]) for spec in requested)
    print("[grading-repair] " + reasons.replace("\n", "; "), flush=True)
    instructions = (
        "\n\n=== QUESTION REGRADING REQUIRED ===\n"
        "The earlier decisions below failed mark validation. Re-read these answers "
        "from the original student pages and official key, and grade each afresh. "
        "Use the immutable question maxima and fixed criterion IDs/weights when supplied. "
        "For an unfixed rubric, assess distinct required points whose weights sum to "
        "the official question maximum; do not multiply the maximum by the number of points. "
        "Do not rescale, trim, add placeholder marks or preserve the earlier award. "
        "Decide each award from the actual evidence. Supply a complete valid breakdown, "
        "original evidence anchors and actionable deduction remarks.\n" + reasons)
    # The invalid rows must not enter the verifier reservation's provisional
    # audit prompt (e.g. a nonfinite score). Reserve their fresh expected steps.
    context = report.model_copy(deep=True)
    context.questions = [question for qid, question in present.items() if qid not in problems]
    first_model = cfg.model
    if any(exc.code == "CHOICE_ORDER_UNRESOLVED" for exc in problems.values()):
        first_model = ("claude-opus-4-8" if cfg.model == _BUDGET_GRADING_MODEL
                       else _BUDGET_GRADING_MODEL)
    replacements = _request_question_decisions(context, requested, cfg=cfg,
        common={**common, "qp_text": (common.get("qp_text") or "") + instructions},
        policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
        use_mathpix=use_mathpix, stage=stage, regrading=True, model_override=first_model)
    def failures(decisions):
        invalid = {}
        for qid, question in decisions.items():
            try:
                validate_question_marks(question, official[qid])
            except StrictGradeError as exc:
                invalid[qid] = str(exc)
        combined = {**present, **decisions}
        invalid.update({qid: str(exc) for qid, exc in _unlocated_surplus_attempts(
            combined, cfg.exam_package, report.answer_regions).items() if qid in decisions})
        return invalid

    unresolved = failures(replacements)
    if unresolved:
        # Repeating the same reader can repeat the same omitted key paragraph.
        # A stronger Claude model reads only the still-invalid questions once.
        # This used to run only for keys sent as images. CA keys arrive as text,
        # so a Haiku breakdown short by a step (Q6 8.5 of 10, Q2.b 6 of 7) got one
        # retry on the same model and the copy was held: 5 of 10 CA copies in one
        # batch. The alternate reader gets the key in whatever form the call has.
        # Gemini Pro consumed its entire 8k output budget on reasoning during a
        # live two-question recovery, so use a model with a separate answer budget.
        # This is output recovery, never a replay of a timed-out/ambiguous paid call.
        remaining = [spec for spec in requested if normalize_qid(spec.qid) in unresolved]
        alternate = ("claude-opus-4-8" if first_model == _BUDGET_GRADING_MODEL
                     else _BUDGET_GRADING_MODEL)
        print(f"[grading-repair] {alternate}: alternate reading of "
              + ", ".join(spec.qid for spec in remaining), flush=True)
        retry_context = context.model_copy(deep=True)
        retry_context.questions.extend(q for qid, q in replacements.items() if qid not in unresolved)
        second = _request_question_decisions(retry_context, remaining, cfg=cfg,
            common={**common, "qp_text": (common.get("qp_text") or "") + instructions
                    + "\nThe first recheck still failed:\n" + "\n".join(unresolved.values())
                    + "\nFirst identify EVERY required step from the key images, including "
                    "case applications, conclusions and omitted requirements. For an 'any N' "
                    "question, the breakdown still covers N marks even if fewer than N points "
                    "are valid. Grade missing required work as zero against its actual maximum. "
                    "Do not shrink the question to the student's valid points."},
            policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
            use_mathpix=use_mathpix, stage=stage, regrading=True, model_override=alternate)
        replacements.update(second)
        unresolved = failures(replacements)
    if unresolved:
        _record_uncovered_steps(replacements, official, unresolved)
        unresolved = failures(replacements)
    if unresolved:
        # FACULTY REVIEW, never a lost copy (operator's rule, 2026-09-19: every
        # copy goes either to the student or to a faculty reviewer). Seven of the
        # sixteen CA improper copies died here after two paid readings - "Q6: step
        # maxima total 8.5; the paper allocates 10" - and nobody ever saw the
        # marks that HAD been decided. The best available decision is kept, an
        # award above the question's marks is clamped to them, and the reason
        # travels with the copy as a review reason (preflight turns it into a
        # score-integrity hold, so the copy is routed to faculty, not released).
        for qid, message in unresolved.items():
            question = replacements.get(qid) or present.get(qid)
            spec = official.get(qid)
            if question is None or spec is None:
                continue
            _clamp_to_valid_marks(question, float(spec.max_marks))
            report.review_reasons.append(f"{spec.qid}: marks breakdown could not be completed "
                                         f"automatically ({message})")
        print("[grading-repair] faculty review: " + "; ".join(unresolved.values()), flush=True)
    # Invalidate optional ink annotations created from the replaced decisions.
    # The normal renderer will draw ticks/marks from the fresh verified criteria.
    affected_lines, affected_pages = set(), set()
    for qid in problems:
        region = report.answer_regions.get(qid, {})
        affected_lines.update(region.get("line_ids", []))
        affected_lines.update(region.get("label_line_ids", []))
        affected_pages.update(region.get("pages", []))
        for question in (present[qid], replacements[qid]):
            affected_pages.add(question.page)
            affected_lines.update([question.anchor_line_id] + [c.step_line_id for c in question.criteria])
    affected_lines.discard("")
    report.annotations = [a for a in report.annotations
                          if a.target_line_id not in affected_lines
                          and not (not a.target_line_id and a.page in affected_pages)]
    present.update(replacements)
    report.questions = [present[qid] for qid in official if qid in present]
    report.regraded_qids = list(dict.fromkeys(
        list(getattr(report, "regraded_qids", []) or []) + [spec.qid for spec in requested]))
    return report


def _recheck_written_blanks(report, *, inventory, ocr_pages, cfg, common,
                            policy, hard_cap, usd_to_inr, use_mathpix, stage):
    """Read again, once, a written answer the grader returned as not attempted.

    Parishram Chemistry, 2026-09-20: Q12 (aniline to bromobenzene) is a drawn reaction
    scheme that the OCR keeps as one "[detected diagram]" block, and the grader said
    "Not attempted" on four copies. The visual inventory had seen the answer, and the
    second examiner saw it too but may not credit a row recorded as unattempted, so
    the copies went out at 0 or were held. A question the inventory saw written, or
    whose OCR label has writing under it (find_unmarked_answers), now gets a focused
    re-read on the recovery path before verification, and the second examiner checks
    it like any regraded row. Only a reading that finds the answer replaces the blank.
    Fails open: if the re-read cannot run, the first decision stands and the coverage
    check still raises it.
    """
    official, present = _index_question_decisions(report, cfg.exam_package)
    written = {normalize_qid(q) for q in (getattr(inventory, "written_qids", None) or [])}
    flagged = {normalize_qid(q) for q in find_unmarked_answers(report, ocr_pages)}
    # A label count never overrules the inventory's own reading of the pages. EMRS TGT
    # Maths (2026-10-10): a circled "15" was read "(5)", so Q5 counted as written
    # although the inventory had called Q5 not written and dropped it as a phantom; the
    # re-read then marked Q8's working as Q5 and drew a cross on it.
    ruled_out = {normalize_qid(q) for q in list(getattr(inventory, "absent_qids", None) or [])
                 + list(getattr(inventory, "dropped_qids", None) or [])}
    requested = [spec for qid, spec in official.items()
                 if qid in present and not present[qid].attempted
                 and spec.question_type != "objective"
                 and (qid in written or (qid in flagged and qid not in ruled_out))]
    if not requested:
        return report
    where = []
    for spec in requested:
        region = (getattr(report, "answer_regions", None) or {}).get(normalize_qid(spec.qid)) or {}
        lines = list(region.get("line_ids") or [])[:8]
        pages = list(region.get("pages") or [])
        where.append(spec.qid + (f" (page {', '.join(map(str, pages))}: {', '.join(lines)})"
                                 if lines and pages else ""))
    instructions = (
        "\n\n=== RECHECK: ANSWERS CALLED BLANK ===\n"
        "The first reading returned these questions as not attempted, but the booklet "
        f"shows writing for them: {'; '.join(where)}. Read every page image again. An "
        "answer may be drawn (a reaction scheme, structure, diagram or table that the OCR "
        "shows only as a DIAGRAM REGION or one table block), sit in a second column, or "
        "be on the facing page of a two page photo. Grade what is written against the "
        "key and anchor it to its own lines. Crossed-out work is not an answer. Return "
        "attempted=false only when no page has an answer to that question.")
    print(f"[recheck] {common.get('filename') or 'sheet'}: "
          f"{', '.join(spec.qid for spec in requested)} called blank but written; reading again",
          flush=True)
    try:
        decisions = _request_question_decisions(report, requested, cfg=cfg,
            common={**common, "qp_text": (common.get("qp_text") or "") + instructions},
            policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
            use_mathpix=use_mathpix, stage=stage, regrading=True)
    except Exception as exc:  # noqa: BLE001 - a second read never costs a copy
        print(f"[recheck] skipped ({type(exc).__name__}: {exc}); the first decision stands",
              flush=True)
        return report
    found = [qid for qid, question in decisions.items()
             if qid in present and bool(getattr(question, "attempted", False))]
    if not found:
        return report
    for qid in found:
        present[qid] = decisions[qid]
    report.questions = [present[qid] for qid in official if qid in present]
    report.regraded_qids = list(dict.fromkeys(
        list(getattr(report, "regraded_qids", []) or []) + [official[qid].qid for qid in found]))
    print(f"[recheck] found the answer to {', '.join(official[qid].qid for qid in found)}",
          flush=True)
    return report


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


def _primary_output_floor(package, *, ceiling: int = 15000) -> int:
    """The smallest output ceiling the whole-report primary call may be fitted to.

    MEASURED on the sixteen SST copies of 2026-09-16: the Haiku primary wrote
    4,497 tokens for a 26-question paper (about 170 a question, steps included),
    and a 25-mark Economics copy once truncated at a fixed 7k ceiling. The floor
    therefore grows with the paper (150 a question, 80 a step, 800 of framing)
    and never drops under 6k; above it the fitting only trims slack.
    """
    questions = [q for q in (getattr(package, "questions", None) or [])
                 if getattr(q, "scorable", True)]
    steps = sum(max(1, len(getattr(q, "criteria", None) or [])) for q in questions)
    floor = 800 + 150 * len(questions) + 80 * steps
    return int(max(6000, min(ceiling, floor)))


def _evidence_output_budget(report, qids, *, repair=False, only_rows=None, model=""):
    from grader import _audit_rows, is_gpt_model, GPT_REASONING_HEADROOM
    from presentation_layout import composite_criterion
    rows = _audit_rows(report, True, qids)
    # A ChatGPT deployment's reasoning tokens share the completion ceiling with
    # the JSON, so the ceiling (and its quote) carries that headroom.
    tokens = 600 + (GPT_REASONING_HEADROOM if is_gpt_model(model) else 0)
    for row, (question, index, _) in enumerate(rows):
        if only_rows is not None and row not in only_rows:
            continue
        criterion = question.criteria[index] if index >= 0 else None
        # A whole account can need twenty independently located checking points.
        # The old 160-token allowance could not even serialize their coordinates.
        points = 20 if criterion and composite_criterion(criterion) else 0
        tokens += (280 if repair else 220) + 100 * points
    return min(32000, tokens)


def _repair_evidence_pages(report, ocr_pages, selected_qids, only_rows, include_awards=True):
    """Keep complete answer pages for repaired rows, with original page/line IDs."""
    from grader import _audit_rows
    rows = _audit_rows(report, include_awards, selected_qids)
    available = {page.page for page in ocr_pages}
    wanted = set()
    for row in only_rows:
        q, _, _ = rows[row]
        region = report.answer_regions.get(normalize_qid(q.qid), {})
        if not q.attempted or not region.get("line_ids"):
            return available  # Resolving absence/ownership needs a search of all pages.
        wanted.update(region.get("pages", []))
        wanted.add(q.page)
        for lid in region.get("line_ids", []) + [q.anchor_line_id] + [c.step_line_id for c in q.criteria]:
            match = re.match(r"P(\d+)", str(lid or ""), re.I)
            if match:
                wanted.add(int(match[1]))
    return available.intersection(wanted)


def _fit_verification_repair(*, report, selected_qids, only_rows, prompt, page_count,
                             model, available_inr, usd_to_inr):
    """Fit a targeted response ceiling without underfunding its required JSON rows."""
    from grader import AUDIT_SYSTEM_PROMPT
    ceiling = _evidence_output_budget(report, selected_qids, repair=True, only_rows=only_rows, model=model)
    floor = min(ceiling, max(512,
        _evidence_output_budget(report, selected_qids, only_rows=only_rows, model=model) - 200))
    plan = CallPlan(stage=Stage.VERIFY, provider=provider_of(model),
        model=model, max_input_tokens=math.ceil((len(prompt) + len(AUDIT_SYSTEM_PROMPT)) * .30)
            + 1000 + 3000 * page_count,
        max_output_tokens=ceiling, mandatory=True)
    minimum = replace(plan, max_output_tokens=floor)
    if minimum.quote_inr(usd_to_inr) > available_inr + 1e-6:
        raise StrictGradeError("UNGRADABLE_BUDGET",
            f"Verifying {len(only_rows)} unresolved decision(s) needs at least "
            f"Rs.{minimum.quote_inr(usd_to_inr):.2f}; Rs.{max(0, available_inr):.2f} remains "
            "after the safety reserve. No repair call was charged.")
    low, high = floor, ceiling
    while low < high:
        mid = (low + high + 1) // 2
        if replace(plan, max_output_tokens=mid).quote_inr(usd_to_inr) <= available_inr:
            low = mid
        else:
            high = mid - 1
    return replace(plan, max_output_tokens=low)


def _merge_audit_verdicts(first: list, repaired: list, *, only_rows=None) -> list:
    """Overlay a repair pass on the first certificate, row by row.

    The repair asks for every row again so the numbering stays the one both passes
    were shown, but a second look can legitimately come back thinner. Keeping the
    first pass's ruling for any row the repair dropped means a repair can only add
    certainty, never lose a row that was already certified. For the same reason a
    repaired row that arrives without a usable reason does not overwrite a first-pass
    row that already had one.
    """
    if only_rows is not None:
        supplied = [int(item.row) for item in repaired or []]
        if len(supplied) != len(set(supplied)) or set(supplied) - set(only_rows):
            raise StrictGradeError("VERIFICATION_INCOMPLETE", "Verification repair returned duplicate or unrequested rows.")
        # A repaired full-credit row may legitimately have an empty reason. Replace
        # requested decisions and validate the resulting certificate as a whole.
        kept = [item for item in first or [] if item.row not in only_rows]
        return sorted(kept + list(repaired or []), key=lambda item: item.row)
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
    if _category_of(cfg):
        return categories.always_moderate(categories.get(_category_of(cfg)), subject)
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

# The second examiner for the strict (production) contract, by department. A
# ChatGPT deployment: a different vendor from the Haiku grader, so the check is a
# real second opinion, and priced to fit the department cap with every risk row
# verified. MEASURED on the sixteen SST copies of 2026-09-16 (49 pages), from the
# recorded token counts: the Opus second examiner cost Rs.518 (Rs.32 a copy, 79%
# of the bill); the same tokens cost Rs.225 on Terra and Rs.23 on Luna, before
# prefix caching. A school copy's other stages come to Rs.6-7, so under the
# Rs.12 cap Terra would have verified only a few rows and held copies whose
# mandatory rows did not fit; Luna verifies every row for about Rs.1.5. The Opus
# audit remains the legacy (non-strict) contract's second opinion (audit_model_for).
#
# CA and commerce papers ALSO verify on Luna (2026-09-19). MEASURED on the sixteen
# CA improper copies: Terra cost Rs.17-29 a copy (Rs.20.5 average, two thirds of
# the bill) and moved ZERO marks on all eleven released copies, because one row
# short of the certificate contract voided the whole reply. The same tokens cost
# Rs.2-3 on Luna (costs.py: 0.2/1.2 against 2/12 per MTok), which is what puts a
# 50-mark CA copy inside the operator's Rs.10-12 target. Its rulings are now
# applied row by row (grader.audit_applicable_verdicts), so a cheaper examiner
# that certifies most rows is worth more than a dearer one whose reply is dropped.
_SCHOOL_SECOND_EXAMINER = "gpt-5.6-luna-ard-content"
_PROFESSIONAL_SECOND_EXAMINER = "gpt-5.6-luna-ard-content"

# Checked here, at import, so a bad model id is a startup failure rather than a 404
# in the middle of a student's copy.
for _m in (_BUDGET_GRADING_MODEL, _LARGE_PAPER_GRADING_MODEL,
           _CLAUDE_SECOND_OPINION_MODEL, _SCHOOL_SECOND_EXAMINER,
           _PROFESSIONAL_SECOND_EXAMINER):
    assert_proxy_model(_m)
del _m


def verifier_ladder_for(cfg: ProviderConfig, subject: str = "") -> list[str]:
    """Second examiners for this copy, strongest first.

    Strict Claude routes (the app) verify on the department's ChatGPT
    deployment (both departments are on Luna since 2026-09-19, see the constants
    above); a different deployment for one department would be tried first and
    fall back to the school one when a long ledger's full verification would not
    fit the cap. Everything else keeps audit_model_for's stronger-same-provider
    rule. The planner takes the first model that can check EVERY risk row within
    the cap, so a cheaper examiner over all the rows is preferred to a stronger
    one over some of them."""
    if cfg.is_strict and cfg.is_claude:
        from costs import is_professional_or_commerce
        if (_category_of(cfg) in ("ca", "commerce")
                or is_professional_or_commerce(exam_type=cfg.exam_type or "", subject=subject or "")):
            return list(dict.fromkeys([_PROFESSIONAL_SECOND_EXAMINER, _SCHOOL_SECOND_EXAMINER]))
        return [_SCHOOL_SECOND_EXAMINER]
    return [audit_model_for(cfg)]


def _objective_reader_models(grader_model: str) -> tuple[str, str]:
    """The two key-blind MCQ readers: (independent reader, tie-break reader).

    Both are Claude now that Gemini is off, so independence comes from the MODEL: the
    first reader is never the grader itself, and the two readers never share a model,
    because a disputed row settles only when both of them agree.
    """
    reader = ("claude-haiku-4-5" if grader_model != "claude-haiku-4-5"
              else _BUDGET_GRADING_MODEL)
    tiebreak = (_BUDGET_GRADING_MODEL if reader != _BUDGET_GRADING_MODEL
                else _CLAUDE_SECOND_OPINION_MODEL)
    return reader, tiebreak


def verifier_model_for(cfg: ProviderConfig, subject: str = "") -> str:
    """The preferred second examiner for this copy (verifier_ladder_for's first)."""
    return verifier_ladder_for(cfg, subject)[0]


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
    for the app's actual route.
    """
    return _CLAUDE_SECOND_OPINION_MODEL


def audits_awarded_marks(cfg: ProviderConfig) -> bool:
    """True when the second-opinion model is genuinely different from (stronger
    than) the grader, so it can safely also re-check marks that were GIVEN, not
    only marks taken away. False only on the rare route where the grader already
    IS the strongest model available (nothing stronger exists to check it with)."""
    return audit_model_for(cfg) != cfg.model


# Shortfalls in the SECOND EXAMINER's own reply. The marks under review are already
# decided and evidenced, so these are reported with the copy, never held against it.
_VERIFICATION_SOFT_CODES = frozenset({
    "VERIFICATION_INCOMPLETE",          # some rows were not ruled on
    "VERIFICATION_EVIDENCE_MISSING",    # no student evidence to re-check against
    "VERIFICATION_EVIDENCE_UNBOUND",    # a row has no deterministic anchor
    "VERIFIED_ANCHOR_OUTSIDE_ANSWER",   # it quoted a line outside the answer
    "INVALID_PRESENTATION_ANCHOR",      # its tick box was loose or off the row
})


def _refuse_other_paper(*, cfg: ProviderConfig, qp_text: str, ocr_pages,
                        ocr_transcript: str | None, filename: str, usage_out: dict,
                        fits_budget, paper_pages: int = 0, sa_pngs=None,
                        qp_pngs=None) -> None:
    """Raise when this copy must not be marked against this paper; return to grade it.

    Three readings can suspect a copy: Mathpix's printed flags (a typed or printed
    document), a booklet written in another script than the paper, and the text check
    (claude_check_paper: another paper, notes, typed, an unrelated document). A
    suspected copy is looked at on its page images by a second model that is not told
    what the first reading said (_look_at_sheet), and it is rejected only when the two
    agree (paper_match.rejection_after_look). Disagreement, an unsure look or a failed
    call grades the copy: this check exists to stop a wrong mark, and must never itself
    cost a student their marks. Nothing is held for a reviewer, because there is no
    faculty to review copies (user, 29 Oct 2026). Without a look the verified-evidence
    rules decide (_decide_without_look).
    """
    paper = (qp_text or "").strip()
    booklet = (ocr_transcript or "").strip()
    name = filename or "sheet"
    if not cfg.paper_check:
        return

    def answers_located() -> int:
        """How many of this paper's answers the booklet visibly labels."""
        numbers = set()
        for question in list(getattr(getattr(cfg, "exam_package", None), "questions", None) or []):
            match = re.match(r"\s*q?\s*(\d{1,2})", str(getattr(question, "qid", "") or ""), re.IGNORECASE)
            if match:
                numbers.add(int(match.group(1)))
        counted = question_labels_found(ocr_pages, numbers) if numbers else 0
        # The answer map reads labels the simple counter misses ("Quiz B)", merged MCQ
        # blocks): on 1b51d418 the counter found none and the map located four. The
        # larger of the two is the evidence that this IS an answer sheet.
        try:
            located = sum(1 for loc in build_answer_map(
                              cfg.exam_package, ocr_pages,
                              typed=bool(_intake_of(cfg).typed_submissions)).locations
                          if getattr(loc, "status", "") == "located")
        except Exception:  # noqa: BLE001 - the map is advice here, never a reason to fail
            located = 0
        return max(counted, located)

    # Free and deterministic: Mathpix marks each line printed or handwritten. Only a
    # real answer sheet is checked (operator's rule, 2026-09-17), but the flags alone
    # no longer refuse a copy. MEASURED 2026-10-22 (Maharashtra HSC Book-keeping,
    # faculty mark 21/25): a phone scanner app's high-contrast scan of a handwritten
    # copy came back as 17 printed lines and 2 handwritten. For a category whose
    # submissions ARE typed documents (School of Innovation assignments) the document
    # is the answer sheet; what cannot be marked there is one that holds only a link
    # to the work, and it goes to a reviewer with the link.
    typed_allowed = bool(_intake_of(cfg).typed_submissions)
    typed_reason = ""
    if typed_allowed:
        link_reason = link_only_reason(ocr_pages)
        if link_reason:
            print(f"[paper-check] {name}: held for faculty, {link_reason}", flush=True)
            raise StrictGradeError("NOT_VERIFIED_ANSWER_SHEET", link_reason)
    else:
        typed_reason = typed_document_reason(ocr_pages)

    # Free and deterministic: an upload with almost nothing written on it and not
    # one of this paper's answers labelled (a blank page, a photo of something
    # else, a scan too faint to read) used to be graded, come out "not attempted"
    # throughout, and go to the student as 0 marks. It is not an answer sheet the
    # tool can mark, so it is refused with the reason and no model is paid. A short
    # MCQ-only sheet ("1) b  2) c ...") is tiny too, but it labels its answers, so
    # the labels, not the length, decide.
    if ocr_pages and len(booklet) < MIN_BOOKLET_CHARS and answers_located() == 0:
        blank_reason = (
            f"No readable {'answers' if typed_allowed else 'handwritten answers'} were "
            f"found in this upload ({len(booklet)} "
            f"characters read across {len(ocr_pages)} page(s), none of this paper's "
            "questions labelled). Upload a clear scan of the student's answer sheet; if "
            "the student handed in a blank sheet, record it as not attempted. No marks "
            "were given.")
        print(f"[paper-check] {name}: refused, {blank_reason}", flush=True)
        raise StrictGradeError("NOT_AN_ANSWER_SHEET", blank_reason)

    # A paper whose questions are printed as pictures cannot be compared as text; the
    # text check then judges only what the booklet IS, and the look sees the paper's
    # first pages instead (151 of 819 copies of 29 Oct 2026 were never checked at all).
    if typed_allowed and (len(paper) < MIN_PAPER_CHARS or len(booklet) < MIN_BOOKLET_CHARS
                          or paper_text_is_partial(paper, paper_pages)):
        print(f"[paper-check] {name}: skipped (no paper text layer to compare)", flush=True)
        return
    if len(paper) < MIN_PAPER_CHARS or paper_text_is_partial(paper, paper_pages):
        print(f"[paper-check] {name}: the paper's text layer holds {len(paper)} characters "
              f"for {paper_pages} page(s); only what the booklet is can be read from the text",
              flush=True)
        compared = ""
    else:
        compared = paper
    # MEASURED 2026-10-22 (MP Board Class 11, sheet rows 74 to 77): three Hindi
    # Economics booklets were filed under the English test. One was held as notes;
    # another labels Q1 to Q11, so the model's word could not hold it, and it was
    # graded against the English paper at 12/20. A paper that prints no Devanagari at
    # all, answered in a booklet that is mostly Devanagari, is suspected.
    script = booklet_script_mismatch(compared, booklet)
    verdict = None
    if len(booklet) >= MIN_BOOKLET_CHARS:
        model = PAPER_CHECK_CLAUDE_MODEL
        plan = CallPlan(
            stage=Stage.VERIFY, provider=CLAUDE, model=model,
            max_input_tokens=1500 + math.ceil(
                (min(len(compared), PAPER_CHARS) + min(len(booklet), BOOKLET_CHARS)) / 3),
            max_output_tokens=1500)
        if not fits_budget(plan):
            print(f"[paper-check] {name}: text check skipped (does not fit the remaining "
                  "budget)", flush=True)
        else:
            try:
                verdict = claude_check_paper(compared, booklet, model=model,
                                             google_token=cfg.google_token, task_id=cfg.task_id,
                                             filename=filename, usage_out=usage_out)
                if verdict is not None and not compared:
                    # Shown no paper, it cannot say "another paper" (row 80 of 22 Oct:
                    # refused as a different test while its answers were this paper's
                    # own options). Only what the booklet is counts.
                    verdict = verdict.model_copy(update={"verdict": "unsure"})
            except Exception as exc:  # noqa: BLE001 - see the docstring
                print(f"[paper-check] {name}: text check failed ({type(exc).__name__}: {exc})",
                      flush=True)
    labels = answers_located()
    if verdict is not None:
        found = len(verified_mismatches(verdict, ocr_pages, compared))
        print(f"[paper-check] {name}: {verdict.verdict} at {verdict.confidence:.2f}; "
              f"{found} of {len(verdict.mismatches)} quoted answers found on the booklet; "
              f"booklet {verdict.booklet_kind}, {labels} of this paper's question labels written"
              + (f" | {verdict.reason}" if verdict.reason else ""), flush=True)
    if typed_allowed:
        _decide_without_look(cfg=cfg, name=name, verdict=verdict, typed_reason="",
                             script=script, labels=labels, ocr_pages=ocr_pages, paper=compared,
                             typed_allowed=True)
        return
    suspects = [what for what in (
        typed_reason and "Mathpix reads it as printed",
        script and "its script is not the paper's",
        first_read_flags(verdict) and f"the text check says {verdict.verdict}, {verdict.booklet_kind}",
    ) if what]
    if not suspects:
        return
    look = _look_at_sheet(cfg=cfg, name=name, filename=filename, sa_pngs=sa_pngs,
                          paper=compared, qp_pngs=qp_pngs, usage_out=usage_out,
                          fits_budget=fits_budget)
    if look is None:
        _decide_without_look(cfg=cfg, name=name, verdict=verdict, typed_reason=typed_reason,
                             script=script, labels=labels, ocr_pages=ocr_pages, paper=compared,
                             typed_allowed=False)
        return
    print(f"[paper-check] {name}: look on the pages says {look.upload} at {look.confidence:.2f}, "
          f"{'handwritten' if look.handwritten else 'not handwritten'}, {look.document or 'no description'}"
          f" | suspected because {', '.join(suspects)}"
          + (f" | {look.reason}" if look.reason else ""), flush=True)
    decided = rejection_after_look(look, verdict=verdict, typed_reason=typed_reason, script=script)
    if decided:
        code, message = decided
        print(f"[paper-check] {name}: refused, both readings agree. {message}", flush=True)
        raise StrictGradeError(code, message)
    print(f"[paper-check] {name}: the readings do not agree; graded as normal", flush=True)


def _look_at_sheet(*, cfg: ProviderConfig, name: str, filename: str, sa_pngs, paper: str,
                   qp_pngs, usage_out: dict, fits_budget):
    """The second reading (grader.look_at_sheet), tried twice; None when it cannot be had."""
    if not sa_pngs:
        return None
    pages = min(len(sa_pngs), LOOK_PAGES)
    plan = CallPlan(
        stage=Stage.VERIFY, provider=CLAUDE, model=SHEET_LOOK_MODEL,
        # About 1,800 tokens a page at LOOK_EDGE; two paper pages when it has no text.
        max_input_tokens=2500 + 1800 * pages + (math.ceil(len(paper) / 3) if paper else 3000),
        max_output_tokens=800)
    if not fits_budget(plan):
        print(f"[paper-check] {name}: look skipped (does not fit the remaining budget)", flush=True)
        return None
    for attempt in (1, 2):
        try:
            return claude_look_at_sheet(sa_pngs, paper, qp_pngs, model=SHEET_LOOK_MODEL,
                                        google_token=cfg.google_token, task_id=cfg.task_id,
                                        filename=filename, usage_out=usage_out)
        except Exception as exc:  # noqa: BLE001 - see _refuse_other_paper
            print(f"[paper-check] {name}: look {attempt} of 2 failed ({type(exc).__name__}: {exc})",
                  flush=True)
    return None


def _decide_without_look(*, cfg: ProviderConfig, name: str, verdict, typed_reason: str,
                         script: str, labels: int, ocr_pages, paper: str,
                         typed_allowed: bool) -> None:
    """The verified-evidence rules: a copy whose look could not be had, and a typed
    category's copies (whose typed pages ARE the work, so the look does not apply)."""
    if typed_reason:
        # Two located answers made a printed copy an answer sheet, unless its lines
        # are the question paper's own text (the scanner app copy of 2026-10-22).
        if labels >= 2 and not booklet_copies_paper(ocr_pages, paper):
            print(f"[paper-check] {name}: Mathpix reads it as printed, but it labels "
                  f"{labels} of this paper's answers; graded as an answer sheet", flush=True)
        else:
            print(f"[paper-check] {name}: refused, {typed_reason}", flush=True)
            raise StrictGradeError("NOT_AN_ANSWER_SHEET", typed_reason)
    if script and typed_allowed:
        print(f"[paper-check] {name}: held for faculty, {script}", flush=True)
        raise StrictGradeError("NOT_VERIFIED_ANSWER_SHEET", script)
    if verdict is None:
        return
    reason = wrong_paper_reason(verdict, ocr_pages, paper, subject=str(getattr(getattr(cfg, "exam_package", None), "subject", "") or ""))
    if reason:
        print(f"[paper-check] {name}: refused, {reason}", flush=True)
        raise StrictGradeError("WRONG_QUESTION_PAPER", reason)
    kind_reason = not_answer_sheet_reason(verdict, ocr_pages, labels, typed_allowed=typed_allowed)
    if kind_reason:
        print(f"[paper-check] {name}: refused, {kind_reason}", flush=True)
        raise StrictGradeError("NOT_AN_ANSWER_SHEET", kind_reason)
    # Sure it is not this paper, but its quotes could not be matched on the page
    # (a Hindi notebook read by an English OCR). MEASURED 2026-09-30: Pratap's six
    # pages of Hindi stories and grammar notes were "notes, different paper, 0.99",
    # fell through, and were graded as Maths. A typed category's reviewer sees it.
    if (typed_allowed and verdict.verdict == "different_paper" and verdict.confidence >= 0.95
            and verdict.booklet_kind in ("notes", "other") and labels <= 1):
        why = (f"This upload does not look like answers to this paper "
               f"({' '.join((verdict.reason or '').split())[:160]}). A reviewer should "
               "check it before any marks are given.")
        print(f"[paper-check] {name}: held for faculty, {why}", flush=True)
        raise StrictGradeError("NOT_VERIFIED_ANSWER_SHEET", why)
    if verdict.verdict == "different_paper":
        print(f"[paper-check] {name}: not enough verified evidence, graded as normal", flush=True)


def _read_sheet_pages(sa_pngs, typed_pages, *, cfg, filename) -> tuple[list, int]:
    """Every page's reading, in page order, and how many pages Mathpix read.

    A typed submission's text pages come ready from the PDF text layer
    (typed_sheets.text_layer_pages); only its picture pages (screenshots, scans)
    are sent to Mathpix, under their own page numbers. Without typed pages this is
    ocr_all_pages, as before."""
    ready = dict(typed_pages or {})
    if not ready:
        pages = ocr_all_pages(sa_pngs, google_token=cfg.google_token, task_id=cfg.task_id,
                              filename=filename)
        return pages, len(sa_pngs)
    todo = [number for number in range(1, len(sa_pngs) + 1) if number not in ready]

    def read(number: int):
        page = ocr_page(sa_pngs[number - 1], number, google_token=cfg.google_token,
                        task_id=cfg.task_id, filename=filename)
        mark_struck_lines(page, sa_pngs[number - 1])
        return page

    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(6, len(todo)))) as pool:
            for number, page in zip(todo, pool.map(read, todo)):
                ready[number] = page
    print(f"[typed] {filename or 'sheet'}: {len(sa_pngs) - len(todo)} page(s) read from "
          f"the PDF text layer, {len(todo)} by OCR", flush=True)
    return [ready[number] for number in range(1, len(sa_pngs) + 1)], len(todo)


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
    typed_pages: dict | None = None,
    label_free_pages=frozenset(),
) -> dict:
    """Run OCR plus one grading call for already-rendered page images, then the
    official-total enforcement and the moderation pass over deductions.

    `typed_pages` are the pages of a typed submission already read from the PDF
    text layer (typed_sheets.text_layer_pages), by page number: Mathpix then reads
    only the pages not among them."""
    def stage(name: str, detail: str = "") -> None:
        if stage_callback:
            stage_callback(name, detail)

    # Flags for the checked copy that do not stop its marking (grade_sheet adds them to
    # the report's review reasons): a line aimed at the grader.
    sheet_notes: list[str] = []
    policy = None
    hard_cap = (float(cfg.budget_cap_inr or budget_for(
        total_marks, exam_type=cfg.exam_type, subject=subject or "", category=_category_of(cfg)))
        if cfg.enforce_budget else math.inf)
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
            category=_category_of(cfg),
        )
        hard_cap = float(cfg.budget_cap_inr or policy.cap_inr) if cfg.enforce_budget else math.inf

    ocr_pages = ocr_transcript = None
    answer_map = None
    inventory = None
    rotated_pages: dict[int, int] = {}
    sideways_usage: dict = {}
    # Pages Mathpix is paid for: every page, less those a typed submission's text
    # layer supplied (_read_sheet_pages).
    mathpix_pages = len(sa_pngs) if use_mathpix else 0
    if use_mathpix:
        stage("ocr", "Reading every answer-sheet page")
        ocr_pages, mathpix_pages = _read_sheet_pages(sa_pngs, typed_pages, cfg=cfg,
                                                     filename=filename)
        rotated_pages = _reread_sideways_pages(sa_pngs, ocr_pages, cfg=cfg, filename=filename,
                                               usage_out=sideways_usage)
        for page in ocr_pages:
            synthesize_diagram_regions(page)
        ocr_transcript = build_transcript(ocr_pages)
        if cfg.is_strict:
            injection_signals = student_instruction_signals(ocr_transcript)
            if _intake_of(cfg).typed_submissions:
                # In a typed assignment "return json.dumps(...)" or "output JSON" is
                # the learner's code, not a line aimed at the grader.
                injection_signals = [signal for signal in injection_signals
                                     if not re.match(r"(?:return|output)\s", signal, re.I)]
            if injection_signals:
                # Graded and flagged, not held: the grader's SECURITY BOUNDARY never
                # follows student writing, and there is no faculty to decide it
                # (user, 29 Oct 2026). It is still a student's paper.
                sheet_notes.append(
                    "Student writing contains instruction-like text aimed at the grader: "
                    + ", ".join(repr(item[:80]) for item in injection_signals[:3]))
                print(f"[injection] {filename or 'sheet'}: {sheet_notes[-1]}; graded and flagged",
                      flush=True)
    stage("answer_mapping", "Mapping visible answers to the official question graph")
    paper_check_usage: dict = {}
    if cfg.is_strict:
        # Before any grading money is spent: a booklet written for another test
        # (live: SST Test-08 scripts uploaded against the Test-06 paper) can only be
        # graded wrongly. Say so plainly instead of failing a later structural gate.
        def fits_budget(plan: CallPlan) -> bool:
            if not cfg.enforce_budget:
                return True
            spent = compute_cost(prior_usage or {},
                                 mathpix_pages=mathpix_pages,
                                 usd_to_inr=usd_to_inr).total_inr
            return spent + plan.quote_inr(usd_to_inr) + policy.safety_inr <= hard_cap

        _refuse_other_paper(cfg=cfg, qp_text=qp_text, ocr_pages=ocr_pages,
                            ocr_transcript=ocr_transcript, filename=filename,
                            usage_out=paper_check_usage, fits_budget=fits_budget,
                            paper_pages=len(qp_pngs or []), sa_pngs=sa_pngs, qp_pngs=qp_pngs)
        # A typed category's numbered points are never question labels (answer_map).
        answer_map = build_answer_map(cfg.exam_package, ocr_pages,
                                      typed=bool(_intake_of(cfg).typed_submissions),
                                      label_free_pages=label_free_pages)
        if getattr(cfg, "answer_inventory", False):
            # A cheap page-by-page look at which answers are actually written,
            # before any grading money is spent (answer_inventory.py). Fails open.
            stage("answer_mapping", "Taking a visual inventory of the written answers")
            from answer_inventory import inventory_model_for, take_answer_inventory
            inventory = take_answer_inventory(
                package=cfg.exam_package, answer_map=answer_map, ocr_pages=ocr_pages,
                student_pngs=sa_pngs, google_token=cfg.google_token, task_id=cfg.task_id,
                filename=filename, usage_out=paper_check_usage,
                model=inventory_model_for(cfg.exam_type), question_paper_text=qp_text)
            if inventory is not None:
                answer_map = inventory.answer_map

    # Dense prose can travel as text. Step-mark labels also need their visual
    # association with paragraphs/groups, even on a fully digital solution PDF.
    qp_pngs, dropped_qp = _prefer_text_over_images(qp_pngs, qp_text)
    key_text_layer = (ak_text_layer or "").strip()
    ak_pngs, ak_text, dropped_key = _prepare_answer_key(ak_pngs, ak_text, key_text_layer)
    if dropped_qp or dropped_key:
        kept = ((len(qp_pngs or []) if dropped_qp else 0)
                + (len(ak_pngs or []) if dropped_key else 0))
        print(f"[payload] sent as text instead of images: "
              f"{'question paper ' if dropped_qp else ''}{'answer key' if dropped_key else ''}"
              .strip() + (f" ({kept} figure page image(s) kept)" if kept else ""), flush=True)

    provider_qp_text = qp_text
    # What is known about THIS booklet (answer map, inventory, repeated pages) is kept
    # apart from the paper text: the paper text is the same for every copy of the test
    # and is cached, the notes differ on every copy (grader: booklet_notes).
    booklet_notes = ""
    if cfg.is_strict:
        provider_qp_text = package_prompt_block(cfg.exam_package) + (
            "\n\n" + qp_text if (qp_text or "").strip() else ""
        )
        booklet_notes = answer_map_prompt_block(answer_map)
        if inventory is not None:
            from answer_inventory import inventory_prompt_block
            block = inventory_prompt_block(inventory)
            if block:
                booklet_notes += "\n\n" + block
        if _category_of(cfg) == "ca" and ocr_pages:
            from repeated_pages import ocr_repeated_pages, prompt_block as repeated_block
            repeats = ocr_repeated_pages(ocr_pages)
            if repeats:
                print("[repeated] " + ", ".join(f"page {a} repeats page {b}"
                                                for a, b in sorted(repeats.items())), flush=True)
                booklet_notes += "\n\n" + repeated_block(repeats)
    usage: dict = {
        "_events": [dict(event) for event in list((prior_usage or {}).get("_events") or [])
                    if isinstance(event, dict)]
    } if prior_usage else {}
    _merge_usage(usage, paper_check_usage)
    _merge_usage(usage, sideways_usage)
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
        booklet_notes=booklet_notes,
        cache_ttl=cfg.cache_ttl,
        category=_category_of(cfg),
    )
    if cfg.is_strict:
        # Bound the largest billable component before the request leaves the app.
        common["reconcile_output"] = False
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
            qp_text=provider_qp_text + booklet_notes,
            key_text=ak_text or key_text_layer,
            ocr_transcript=ocr_transcript or "",
            marks_scheme=marks_scheme,
        )
        from grader import build_system_prompt
        # The grading instructions have grown beyond the old 2.5k allowance.
        # Include their current size when reserving primary and recovery calls.
        quoted_input += max(0, math.ceil(len(build_system_prompt(
            subject, student_class, cfg.exam_type, category=_category_of(cfg))) / 3) - 2500)
        def primary_quote_for(limit: int) -> float:
            return CallPlan(
                stage=Stage.PRIMARY,
                provider=cfg.provider,
                model=cfg.model,
                max_input_tokens=quoted_input,
                max_output_tokens=limit,
                mathpix_pages=mathpix_pages,
                mandatory=True,
            ).quote_inr(usd_to_inr)
        primary_quote = primary_quote_for(primary_output_limit)
        prior_cost = compute_cost(usage, usd_to_inr=usd_to_inr).total_inr
        primary_allowance = (
            hard_cap - policy.verification_reserve_inr - policy.recovery_reserve_inr
            - policy.safety_inr - prior_cost
        )
        if primary_quote > primary_allowance + 1e-6:
            # The 15k ceiling is slack, not need (see _primary_output_floor). Under
            # the Rs.12 school cap it quoted Rs.7 of output for a call that writes
            # about Rs.2, and the copy was refused before grading. Fit the ceiling
            # to the money left, down to the paper's floor; refuse only below it.
            floor = _primary_output_floor(cfg.exam_package, ceiling=primary_output_limit)
            if primary_quote_for(floor) <= primary_allowance + 1e-6:
                low, high = floor, primary_output_limit
                while low < high:
                    mid = (low + high + 1) // 2
                    if primary_quote_for(mid) <= primary_allowance + 1e-6:
                        low = mid
                    else:
                        high = mid - 1
                print(f"[budget] {filename or 'sheet'}: primary output ceiling fitted "
                      f"{primary_output_limit} -> {low} tokens under Rs.{hard_cap:g}", flush=True)
                primary_output_limit = low
                common["max_output_tokens"] = primary_output_limit
                primary_quote = primary_quote_for(primary_output_limit)
        if primary_quote > primary_allowance + 1e-6:
            raise StrictGradeError(
                "PRIMARY_BUDGET_RESERVATION_DENIED",
                f"The main grading request was not sent: its maximum estimated cost "
                f"is Rs.{primary_quote:.2f}, but only Rs.{primary_allowance:.2f} "
                "remains after verification and safety reserves. This estimate is "
                "not a charge. Earlier OCR or setup calls may already have incurred costs.",
            )
    # Fan-out is a Claude-route feature (it leans on prompt caching to stay cheap)
    # and needs OCR to know where each question's answer sits. Without either, the
    # single call is still the right — and only — path.
    use_fanout = bool(getattr(cfg, "fanout", False)) and cfg.is_claude and bool(ocr_pages)
    claude_call = claude_grade_chunked if use_fanout else claude_grade
    stage("primary_grading", "Grading the mapped questions")
    try:
        report = claude_call(**common)
    except OutputLimitExceeded as exc:
        # The grader ran away mid-reply. Losing the copy is the worst outcome, so retry
        # ONCE on the steadier model. This is the known haiku failure mode (3 of 10
        # Commerce Accountancy booklets), and it is why routing big papers to haiku is
        # safe: a runaway costs one wasted call, not the sheet. The strict route used
        # to refuse the copy outright here; it now takes the same one retry, because
        # a copy nobody receives is the one outcome the operator has ruled out.
        package = cfg.exam_package
        long_paper = (cfg.is_strict and package is not None
                      and len(package.questions) > EXACT_QUESTION_BATCH_SIZE)
        fallback = _BUDGET_GRADING_MODEL if cfg.model != _BUDGET_GRADING_MODEL else None
        if long_paper:
            # A paper too long for one reply is graded in bounded batches instead:
            # the empty report below has every question missing, and the completion
            # step right after reads them EXACT_QUESTION_BATCH_SIZE at a time, each
            # with its own output ceiling, through the same checks as any row.
            # MEASURED 2026-10-23 (UP LT Grade Home Science, 20 questions, 200 marks,
            # 50 pages): haiku ran out of output, and the one-call retry on sonnet
            # was aborted by the proxy after 270 s, so the copy failed outright.
            print(f"[grader] {exc} — grading {len(package.questions)} questions in "
                  f"batches of {EXACT_QUESTION_BATCH_SIZE} instead", flush=True)
            from grader import GradeReport
            report = GradeReport(total_score=0.0,
                                 max_total=float(total_marks or package.total_marks or 0.0),
                                 overall_remarks="", questions=[])
        else:
            if fallback is None:
                if cfg.is_strict:
                    raise StrictGradeError(
                        "PRIMARY_OUTPUT_LIMIT",
                        "The primary grader's response was cut off before it finished.",
                    ) from exc
                raise
            print(f"[grader] {exc} — retrying on {fallback}", flush=True)
            common["model"] = fallback
            report = claude_grade(**common)
            cfg = replace(cfg, model=fallback, provider=CLAUDE)

    if cfg.is_strict and answer_map is not None:
        from grading_quality import initialize_evidence, prepare_evidence
        initialize_evidence(report, cfg.exam_package, answer_map)
        report.unresolved_answer_qids = list(answer_map.unresolved_qids)

    if cfg.is_strict:
        report = _complete_missing_questions(
            report, cfg=cfg, common=common,
            policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
            use_mathpix=use_mathpix, stage=stage)
        # Before the blank recheck: the writing a phantom sat on may be a real answer
        # the grader called blank (Q4(b) under a phantom Q3), which the recheck reads.
        _clear_phantom_attempts(report, cfg)
        report = _recheck_written_blanks(
            report, inventory=inventory, ocr_pages=ocr_pages, cfg=cfg, common=common,
            policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
            use_mathpix=use_mathpix, stage=stage)
        _collapse_shared_alternative_attempts(report, cfg.exam_package)
        _settle_option_questions(report)
        report = _regrade_invalid_questions(
            report, cfg=cfg, common=common,
            policy=policy, hard_cap=hard_cap, usd_to_inr=usd_to_inr,
            use_mathpix=use_mathpix, stage=stage)
        _clear_phantom_attempts(report, cfg)

    # A reply that went through the motions — one remark repeated across the paper,
    # no criteria — is worth nothing to a student, so it is worth re-buying on the
    # sturdier model even though that breaks the per-copy budget. Shipping 9/70 on a
    # copy the same model marked properly elsewhere is the more expensive mistake.
    stub = report_is_stub(report)
    if stub:
        retry = _BUDGET_GRADING_MODEL if cfg.model != _BUDGET_GRADING_MODEL else None
        if retry:
            print(f"[grader] the reply looks unmarked ({stub}) — regrading on {retry}",
                  flush=True)
            common["model"] = retry
            second = claude_grade(**common)
            if not report_is_stub(second):
                report = second
                stub = ""
                cfg = replace(cfg, model=retry, provider=CLAUDE)
            else:
                print("[grader] the second reply is no better — keeping the first",
                      flush=True)
        else:
            print(f"[grader] the reply looks unmarked ({stub}) and there is no sturdier "
                  f"model to fall back to", flush=True)
        if stub and cfg.is_strict:
            # The strict route used to refuse the copy here. It now keeps what was
            # marked and sends the copy to faculty with the reason.
            report.review_reasons.append(
                f"The grader's report is incomplete ({stub}); a faculty reviewer "
                "should mark this copy.")

    if cfg.is_strict:
        report = reconcile_report_to_package(
            report, cfg.exam_package, answer_map=answer_map,
            finalize_choices=not bool(cfg.exam_package.choice_groups))

    # An MCQ is all-or-nothing and trivially checkable, so a misread option letter
    # hands out marks for a plainly wrong answer — the first thing a reviewer tests.
    # Moderation cannot catch it: that pass is text-only and skips objective items by
    # design. The OCR of the very line the choice was read from is already in hand.
    objective_usage: dict = {}
    visual_choices: dict = {}
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
        objective_page_numbers = sorted(evidence_pages)
        objective_pngs = [sa_pngs[page - 1] for page in objective_page_numbers]
        stage(
            "verifying",
            "Independently reading checkbox/objective choices omitted by OCR",
        )
        reader = claude_read_objectives
        reader_model, tiebreak_model = _objective_reader_models(cfg.model)
        # The printed options (never the key), so a reader can turn "Indica" into B.
        paper_options = option_texts_from_paper(qp_text)
        options_by_qid: dict[str, dict[str, str]] = {}
        for qid in objective_qids:
            number = re.fullmatch(r"q(\d{1,3})", _norm_qid(qid))
            if number and paper_options.get(int(number.group(1))):
                options_by_qid[qid] = paper_options[int(number.group(1))]
        options_by_norm = {_norm_qid(qid): value for qid, value in options_by_qid.items()}
        def quote_objective_read(model):
            spent_usage = copy.deepcopy(usage)
            _merge_usage(spent_usage, objective_usage)
            spent = compute_cost(spent_usage, mathpix_pages=len(sa_pngs), usd_to_inr=usd_to_inr)
            calls = 1
            quote = CallPlan(
                stage=Stage.VERIFY, provider=provider_of(model),
                model=model, max_input_tokens=calls * (1000 + 3000 * len(objective_pngs)),
                max_output_tokens=calls * max(1400, 240 * len(objective_qids)),
                question_ids=tuple(objective_qids), mandatory=True,
            ).quote_inr(usd_to_inr)
            if cfg.enforce_budget and (not spent.usage_attested
                    or spent.total_inr + quote + policy.safety_inr > hard_cap):
                raise StrictGradeError("UNGRADABLE_BUDGET", "Independent objective reading exceeds the remaining budget.")
        def read_objectives(read, model):
            """One key-blind reading, or none. A reader that cannot run (budget,
            proxy error, unparseable reply) leaves the row to the page below; it
            never takes the whole copy down with it."""
            try:
                quote_objective_read(model)
                observed = read(objective_pngs, objective_qids, model=model,
                                google_token=cfg.google_token, task_id=cfg.task_id,
                                filename=filename, usage_out=objective_usage,
                                options=options_by_qid)
                # The reader sees a subset, numbered 1..N. Bind locations back to
                # actual booklet pages before consensus or rendering uses them.
                from grader import map_objective_image_pages
                return map_objective_image_pages(observed, objective_page_numbers)
            except Exception as exc:  # noqa: BLE001 - see the docstring
                print(f"[objective] key-blind read on {model} unavailable "
                      f"({type(exc).__name__}: {exc})", flush=True)
                return []

        observations = read_objectives(reader, reader_model)
        if inventory is not None and inventory.mcq_options:
            # The inventory read the MCQ letters key-blind from the page images
            # (its prompt never sees the key). It stands in only for rows the
            # key-blind reader could not settle, and faces the same agreement test.
            clear = {_norm_qid(item.qid) for item in observations
                     if bool(getattr(item, "clear", False))}
            from grader import ObjectiveChoiceObservation
            for qid, (letter, page) in inventory.mcq_options.items():
                if _norm_qid(qid) in objective_norm and _norm_qid(qid) not in clear:
                    observations.append(ObjectiveChoiceObservation(
                        qid=qid, chosen_option=letter, clear=True,
                        page=max(1, min(len(sa_pngs) or 1, int(page or 1)))))
        visual_choices = {
            _norm_qid(item.qid): item for item in observations
            if _norm_qid(item.qid) in objective_norm
        }
        primary_choices = {
            _norm_qid(question.qid): _choice_letter(
                str(getattr(question, "chosen_option", "") or ""))
            for question in report.questions
            if _norm_qid(question.qid) in objective_norm
        }
        independent_choices = {
            qid: (_observed_letter(item, options_by_norm.get(qid, {}))
                  if bool(getattr(item, "clear", False)) else "")
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
            # A reader that skipped rows cannot confirm them; the rows it did return
            # still count, and the rest fall back to the page below.
            print("[objective] the key-blind reader did not return every row: "
                  f"{len(visual_choices)} of {len(objective_norm)}", flush=True)
        try:
            objective_notes = apply_official_objective_evidence(
                report, cfg.exam_package, ocr_pages, answer_map=answer_map,
                visual_choices=_objective_reads_by_qid(visual_choices, cfg.exam_package),
                question_paper_text=qp_text,
            )
        except StrictGradeError as second_pass:
            if second_pass.code not in {"OBJECTIVE_VISUAL_DISAGREEMENT",
                                        "OBJECTIVE_ANSWER_UNRESOLVED"}:
                raise
            # One undecided box is a question to settle, not a reason to throw
            # away a whole marked copy. Nobody reviews a held copy, so refusing here
            # loses every mark on the sheet over one MCQ. Two shapes reach here:
            # the readers DISAGREE, or one of them named nothing (live 2026-09-14:
            # the grading pass said "?" for Q3 of an SST booklet whose "[3B]" was
            # overwritten, while the key-blind reader clearly saw B).
            #
            # The tie is NOT settled by a show of hands, because the reads are not
            # of equal standing. `primary` is the option the grading call happened
            # to mention; that call was never told to ignore a cancelled answer.
            # Both key-blind readers were: their prompt says in terms to ignore an
            # option that is crossed out or overwritten. A student who writes (B),
            # strikes it out and answers (C) is therefore read as B by the
            # cancellation-blind pass and C by the careful ones - and a majority of
            # three would hand them the mark for the answer they cancelled, which is
            # precisely what AUDIT_SYSTEM_PROMPT means by "cancelled working earns
            # nothing". So a disputed row settles only when BOTH careful readers say
            # the same thing, and the loud, uninstructed read never carries it.
            stage("verifying",
                  "An objective choice is undecided; taking a second key-blind read")
            third = read_objectives(claude_read_objectives, tiebreak_model)
            third_by_qid = {
                _norm_qid(item.qid): item for item in third
                if bool(getattr(item, "clear", False))
            }
            third_choices = {
                qid: _observed_letter(item, options_by_norm.get(qid, {}))
                for qid, item in third_by_qid.items()
            }
            settled: list[str] = []
            for qid in sorted(objective_norm):
                primary = primary_choices.get(qid, "")
                independent = independent_choices.get(qid, "")
                confirming_read = third_choices.get(qid, "")
                confirming = settle_disputed_choice(
                    primary, independent, confirming_read)
                if (not confirming and not primary and independent
                        and confirming_read == independent):
                    # The grading pass named nothing; both key-blind reads agree.
                    confirming = independent
                if confirming:
                    for question in report.questions:
                        if _norm_qid(question.qid) == qid:
                            question.chosen_option = confirming
                    observation = visual_choices.get(qid)
                    if observation is not None:
                        try:
                            observation.chosen_option = confirming
                            observation.clear = True
                        except (AttributeError, ValueError):
                            pass
                    settled.append(
                        f"{qid}:{confirming} (both key-blind reads; grading pass said "
                        f"{primary or 'nothing'})")
                elif not independent and qid in third_by_qid:
                    # The first key-blind read saw nothing usable on this row. The
                    # second stands in for it and faces the same test the first
                    # pass does (agreement with the grading pass or the page OCR).
                    visual_choices[qid] = third_by_qid[qid]
                    settled.append(f"{qid}:{confirming_read} (second key-blind read)")
            if settled:
                print("[objective] second key-blind read resolved "
                      f"{len(settled)} row(s): {', '.join(settled)}",
                      flush=True)
            # Whatever three readings could not agree on, resolve from the student's
            # own answer line and flag it. One unreadable box is worth a note on the
            # copy, never the loss of every other mark the student earned.
            objective_notes = apply_official_objective_evidence(
                report, cfg.exam_package, ocr_pages, answer_map=answer_map,
                visual_choices=_objective_reads_by_qid(visual_choices, cfg.exam_package),
                question_paper_text=qp_text, allow_unconfirmed=True,
            )
    finally:
        _merge_usage(usage, objective_usage)
    if visual_choices:
        from objective_locations import bind_objective_locations
        answer_map, location_notes = bind_objective_locations(
            report, ocr_pages, answer_map,
            _objective_reads_by_qid(visual_choices, cfg.exam_package), student_pngs=sa_pngs,
            question_paper_text=qp_text)
        objective_notes.extend(location_notes)
    for note in objective_notes:
        print(f"[objective] {filename or 'sheet'}: {note}", flush=True)

    # The official total is a HARD contract — never ship a report graded out of the
    # wrong denominator. Layer 1 (an authoritative scheme forced in _reconcile_report)
    # usually guarantees it already; when the report still disagrees, layer 2 is one
    # cheap text-only repair call that re-reads the paper's printed tags; layer 3
    # rescales deterministically so the total is right by construction.
    if total_marks is not None and abs(float(report.max_total) - float(total_marks)) > 0.25:
        if cfg.is_strict:
            # The strict route used to refuse the copy here ("will not repair or
            # rescale"). A copy nobody receives is the one outcome ruled out now, so
            # the deterministic layers below run, the rescale is flagged
            # (maxima_rescaled holds the copy for FACULTY REVIEW, with its PDF), and
            # only the model repair call is skipped: the package's maxima are
            # authoritative and a text-only re-read cannot overrule them.
            report.review_reasons.append(
                f"The graded questions total {float(report.max_total):g} but the paper "
                f"is out of {float(total_marks):g}; the per-question maxima need a "
                "faculty check.")
        else:
            repair_usage: dict = {}
            try:
                repair = claude_repair
                # Same fix as audit_model_for: repair is a text-only "re-read the
                # printed tags carefully" judgment call, not a re-run of the grading
                # call, so it should not be pinned to the (possibly cheap) grader's
                # own model either. grader.repair_report_totals defaults to
                # claude-opus-4-8 for exactly this reason; this used to override that
                # default down to cfg.model.
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

    # Resolve explicit missing/cancelled awards before the examiner sees this copy.
    commerce_required_qids = _prepare_commerce_feedback(report, ocr_pages, cfg, subject or "")
    commerce_certified_qids: set[str] = set()
    commerce_certified_digests: dict[str, str] = {}

    # In strict mode no score may change after the second examiner has certified it.
    # Apply deterministic evidence contradictions first, then bind the certificate to
    # the resulting decisions. Legacy mode retains its historical post-audit cleanup.
    if cfg.is_strict:
        for note in enforce_evidence_against_awards(report, ocr_pages):
            print(f"[marks] {filename or 'sheet'}: {note}", flush=True)
        report = reconcile_report_to_package(
            report, cfg.exam_package, answer_map=answer_map,
            finalize_choices=not bool(cfg.exam_package.choice_groups))
        report.mandatory_verification_qids = sorted(
            set(report.mandatory_verification_qids) | set(visual_evidence_qids(qp_text, report)))
        prepare_evidence(report, ocr_pages)
        # Credit whose own evidence the page does not bear out goes to the second
        # examiner with the reason; unconfirmed, it is removed after that pass.
        from grading_quality import find_unsupported_credit
        report.unsupported_credit = find_unsupported_credit(report, ocr_pages)
        for key, why in report.unsupported_credit.items():
            print(f"[evidence] {filename or 'sheet'}: {key} credit unsupported: {why}",
                  flush=True)
        if inventory is not None:
            # Where the visual inventory and the label map disagreed, the second
            # examiner checks the question; where the inventory found no answer,
            # the release gate refuses credit that has no evidence of its own.
            report.inventory_absent_qids = list(inventory.absent_qids)
            report.inventory_disputed_qids = list(inventory.disputed_qids)
            report.placement_risk_qids = sorted(
                set(report.placement_risk_qids) | set(inventory.disputed_qids))

    # Strict v2 selects high-risk questions before buying a text-only verification
    # call. False blanks are mandatory risks, so compute coverage before selection.
    report.false_blank_qids = find_unmarked_answers(report, ocr_pages)
    verification_complete = not cfg.is_strict
    # What the second examiner could not certify, in the teacher's words. These
    # travel with the released copy instead of holding it (see _VERIFICATION_SOFT_CODES).
    verification_notes: list[str] = []
    # Remarks that came out thin. Worth telling the teacher, never worth refusing a
    # copy whose marks are sound.
    feedback_notes: list[str] = []
    verification_digest: str | None = None
    verification_qids: list[str] = []
    verification_omitted: list[str] = []

    # Moderation: in legacy mode this remains the existing second-examiner pass. In
    # strict mode it is a bounded evidence verifier over risk-selected questions; a
    # mandatory risk that cannot fit becomes UNGRADABLE rather than silently keeping
    # the primary model's answer.
    key_text = (ak_text or ak_text_layer or "").strip()
    check_awards = audits_awarded_marks(cfg) or bool(commerce_required_qids)
    selected_norm: set[str] | None = None
    verify_model_quote: str | None = None      # the examiner the plan settled on
    if cfg.is_strict:
        base_cost = compute_cost(
            usage,
            mathpix_pages=mathpix_pages,
            usd_to_inr=usd_to_inr,
        )
        budget_left = True
        if cfg.enforce_budget and (not base_cost.rates_found or not base_cost.usage_attested
                or base_cost.total_inr >= hard_cap):
            # Used to refuse the copy (UNGRADABLE_BUDGET). Money is not a fault of
            # the copy: the marks stand as the first examiner's, the teacher is told,
            # and the deterministic gate below still decides what a student may see.
            budget_left = False
            verification_notes.append(
                f"Grading used Rs.{base_cost.total_inr:.2f} of the Rs.{hard_cap:g} cap, "
                "so the second examiner's pass was skipped; the marks are the first "
                "examiner's.")
            print(f"[moderation] {filename or 'sheet'}: no budget left for the second "
                  f"examiner (Rs.{base_cost.total_inr:.2f} of Rs.{hard_cap:g})", flush=True)
        verify_ladder = verifier_ladder_for(cfg, subject)
        verify_model_quote = verify_ladder[0]
        candidates = verification_candidates(report) if budget_left else []
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
                if not question.attempted:
                    pages.update(range(1, len(sa_pngs) + 1))
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

        def verification_quote(model: str, count: int) -> float:
            quoted_qids = {item.qid for item in candidates[:count]}
            output_limit = _evidence_output_budget(
                report, {_norm_qid(q) for q in quoted_qids}, model=model)
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
                + 1000 + evidence_image_tokens + 3000 * len(ak_pngs or [])
            return CallPlan(
                stage=Stage.VERIFY,
                provider=provider_of(model),
                model=model,
                max_input_tokens=verify_input,
                max_output_tokens=output_limit,
                question_ids=tuple(item.qid for item in candidates[:count]),
                mandatory=True,
            ).quote_inr(usd_to_inr)

        def fits(quote: float) -> bool:
            return (base_cost.total_inr + quote + policy.recovery_reserve_inr
                    + policy.safety_inr <= hard_cap + 1e-6)

        # The strongest examiner that can check EVERY risk row within the cap;
        # only when none can, the cheapest one over as many rows as fit.
        affordable = 0
        if risk_count:
            for model in verify_ladder:
                if fits(verification_quote(model, risk_count)):
                    verify_model_quote, affordable = model, risk_count
                    break
            else:
                verify_model_quote = verify_ladder[-1]
                for count in range(1, risk_count + 1):
                    if fits(verification_quote(verify_model_quote, count)):
                        affordable = count
                    else:
                        break
            print(f"[moderation] {filename or 'sheet'}: second examiner {verify_model_quote} "
                  f"for {affordable}/{risk_count} risk rows under Rs.{hard_cap:g}", flush=True)
        verification_qids, verification_omitted = select_verification_qids(
            report, max_questions=affordable)
        selected_norm = {_norm_qid(qid) for qid in verification_qids}
        selected_keys = {_norm_qid(qid) for qid in verification_qids}
        uncovered_risks = [item.qid for item in candidates
                           if _norm_qid(item.qid) not in selected_keys]
        if verification_omitted or uncovered_risks:
            # Used to refuse the copy. The rows that fit are still verified; the
            # rest are named to the teacher, and the deterministic gate decides.
            skipped = list(dict.fromkeys(list(verification_omitted) + list(uncovered_risks)))
            verification_notes.append(
                f"The budget could not cover the second examiner's check of "
                f"{', '.join(skipped[:10])}"
                + (f" and {len(skipped) - 10} more" if len(skipped) > 10 else "")
                + "; those marks are the first examiner's.")
            print(f"[moderation] {filename or 'sheet'}: {len(skipped)} risk row(s) not "
                  f"verified within Rs.{hard_cap:g}: {', '.join(skipped[:10])}", flush=True)
        verification_complete = not verification_qids

    should_verify = bool(
        (cfg.is_strict or moderation_applies(cfg, subject))
        and has_audit_rows(report, check_awards, selected_norm)
    )
    if cfg.is_strict and not should_verify:
        stage("verifying", "No provider verification call was required by the risk policy")
    if should_verify and (not key_text or not (ocr_transcript or "").strip()):
        if cfg.is_strict:
            verification_notes.append(
                "The second examiner's pass needs both the official solution text and "
                "the student's OCR evidence; one of them was missing, so the marks on "
                "this copy are the first examiner's.")
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
                    evidence_pages.update(range(1, len(sa_pngs) + 1))
                    continue
                anchors = [getattr(question, "anchor_line_id", "")] + [
                    getattr(criterion, "step_line_id", "")
                    for criterion in list(getattr(question, "criteria", None) or [])
                ]
                anchors = [str(anchor or "").strip() for anchor in anchors if anchor]
                location = mapped_locations.get(_norm_qid(question.qid))
                evidence_pages.update(getattr(location, "pages", ()) or ())
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
                # Those rows simply cannot be certified; the ones that can still are.
                verification_notes.append(
                    "No student-evidence anchor exists for "
                    + ", ".join(missing_evidence_qids[:12])
                    + ", so the second examiner could not re-check them.")
            if evidence_pages:
                verification_transcript = build_transcript([
                    page for page in ocr_pages if int(page.page) in evidence_pages
                ])
                verification_pngs = [
                    overlay_anchors_on_png(
                        sa_pngs[page - 1], next(p for p in ocr_pages if p.page == page))
                    for page in sorted(evidence_pages)
                    if 1 <= page <= len(sa_pngs)
                ]
        try:
            if cfg.is_strict and not verification_transcript.strip():
                # Raised INSIDE the block so it is handled like every other
                # verification shortfall: note it, do not buy the call, do not lose
                # the copy over a check that could not be run.
                raise StrictGradeError(
                    "VERIFICATION_EVIDENCE_MISSING",
                    "No student evidence could be isolated for the selected verification rows.",
                )
            # A ChatGPT second examiner goes through the Claude-shaped audit,
            # which dispatches on the model id (grader.audit_deductions).
            audit = claude_audit
            verify_model = verify_model_quote or verifier_model_for(cfg, subject)
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
                answer_key_pngs=ak_pngs,
                usage_out=audit_usage,
                include_awards=check_awards,
                selected_qids=selected_norm,
                max_output_tokens=(_evidence_output_budget(report, selected_norm, model=verify_model)
                                   if cfg.is_strict else 32000),
                **_category_rules_kwarg(cfg),
            )
            unverified_rows: dict[int, str] = {}
            unsupported_keys: set[str] = set()
            if cfg.is_strict:
                from grading_quality import (apply_verified_anchors, resolve_verified_anchors,
                                             propose_verifier_regions)
                from grader import _audit_rows, audit_applicable_verdicts
                ownership_gaps = propose_verifier_regions(report, verdicts, ocr_pages,
                    include_awards=check_awards, selected_qids=selected_norm)
                anchor_gaps = resolve_verified_anchors(report, verdicts, ocr_pages,
                    include_awards=check_awards, selected_qids=selected_norm)
                anchor_gaps.update(ownership_gaps)
                incomplete, reason_gaps = audit_completion_gaps(
                    report, verdicts, include_awards=check_awards,
                    selected_qids=selected_norm)
                incomplete.extend(anchor_gaps.values())
                expected_count = len(_audit_rows(report, check_awards, selected_norm))
                if incomplete:
                    reason_gaps = audit_repair_rows(report, verdicts,
                        include_awards=check_awards, selected_qids=selected_norm)
                    reason_gaps.update(anchor_gaps)
                    repair_indices = set(reason_gaps)
                # One bounded repair call for the rows that came back short. A
                # repair that cannot be bought or that misfires costs nothing but
                # itself: the first pass's sound rows are applied below regardless.
                if incomplete and repair_indices:
                    try:
                        stage("verifying", f"Rechecking {len(repair_indices)} unresolved decisions")
                        repair_pages = _repair_evidence_pages(report, ocr_pages, selected_norm,
                                                             repair_indices, check_awards)
                        retry_transcript = build_transcript([p for p in ocr_pages if p.page in repair_pages])
                        retry_pngs = [overlay_anchors_on_png(sa_pngs[p.page - 1], p)
                                      for p in sorted(ocr_pages, key=lambda p: p.page)
                                      if p.page in repair_pages]
                        retry_usage: dict = {}
                        retry_prompt = build_audit_prompt(
                            report, answer_key_text=key_text, ocr_transcript=retry_transcript,
                            question_paper_text=qp_text, include_awards=check_awards,
                            selected_qids=selected_norm, repair_rows=reason_gaps, only_rows=repair_indices)
                        spent_usage = copy.deepcopy(usage)
                        _merge_usage(spent_usage, audit_usage)
                        spent = compute_cost(spent_usage,
                                             mathpix_pages=mathpix_pages,
                                             usd_to_inr=usd_to_inr)
                        if cfg.enforce_budget and not spent.usage_attested:
                            raise StrictGradeError("UNGRADABLE_BUDGET",
                                                   "Verification usage was not attested; a repair cannot be reserved.")
                        print(f"[moderation] {filename or 'sheet'}: planning repair rows {sorted(repair_indices)} "
                              f"on pages {sorted(repair_pages)}; spent Rs.{spent.total_inr:.2f}; "
                              f"gaps={reason_gaps}", flush=True)
                        retry_plan = _fit_verification_repair(report=report, selected_qids=selected_norm,
                            only_rows=repair_indices, prompt=retry_prompt,
                            page_count=len(retry_pngs) + len(ak_pngs or []),
                            model=verify_model, available_inr=hard_cap - spent.total_inr - policy.safety_inr,
                            usd_to_inr=usd_to_inr)
                        print(f"[moderation] repair reservation Rs.{retry_plan.quote_inr(usd_to_inr):.2f}; "
                              f"output ceiling {retry_plan.max_output_tokens} tokens", flush=True)
                        try:
                            repaired = audit(
                                report, model=verify_model, google_token=cfg.google_token,
                                task_id=cfg.task_id, filename=filename, answer_key_text=key_text,
                                ocr_transcript=retry_transcript, question_paper_text=qp_text,
                                student_evidence_pngs=retry_pngs, usage_out=retry_usage,
                                answer_key_pngs=ak_pngs,
                                include_awards=check_awards, selected_qids=selected_norm,
                                max_output_tokens=retry_plan.max_output_tokens,
                                repair_rows=reason_gaps, only_rows=repair_indices,
                                **_category_rules_kwarg(cfg))
                        finally:
                            _merge_usage(audit_usage, retry_usage)
                        # Unknown extra first-pass rows are discarded only after all
                        # expected rows have been requested anew and checked below.
                        verdicts = _merge_audit_verdicts(
                            [v for v in verdicts if 0 <= v.row < expected_count], repaired,
                            only_rows=repair_indices)
                        anchor_gaps = resolve_verified_anchors(report, verdicts, ocr_pages,
                            include_awards=check_awards, selected_qids=selected_norm)
                        incomplete, still_missing = audit_completion_gaps(
                            report, verdicts, include_awards=check_awards,
                            selected_qids=selected_norm)
                        incomplete.extend(anchor_gaps.values())
                        still_missing.update(anchor_gaps)
                        print(f"[moderation] {filename or 'sheet'}: repair returned "
                              f"{len(repaired)} row(s); "
                              f"{len(reason_gaps) - len(still_missing)} of "
                              f"{len(reason_gaps)} explained"
                              + (f"; still unexplained {sorted(still_missing)}"
                                 if still_missing else ""),
                              flush=True)
                    except StrictGradeError as exc:
                        if exc.code not in _VERIFICATION_SOFT_CODES | {"UNGRADABLE_BUDGET"}:
                            raise
                        print(f"[moderation] {filename or 'sheet'}: repair pass skipped "
                              f"({exc.code}: {exc}); applying the first pass row by row",
                              flush=True)
                # ROW BY ROW. Until 2026-09-19 one row short of the contract voided the
                # whole certificate and apply_audit never ran: eleven CA certificates,
                # Rs.20 or more each, moved zero marks. The operator's rule is that the
                # second examiner CORRECTS the copy, so every sound ruling is applied
                # and only the rows it could not certify stay the first examiner's.
                ruled = [v for v in verdicts if 0 <= v.row < expected_count]
                verdicts, unverified_rows = audit_applicable_verdicts(
                    report, ruled,
                    include_awards=check_awards, selected_qids=selected_norm)
                examiner_found = _examiner_found_answers(
                    report, _audit_rows(report, check_awards, selected_norm),
                    unverified_rows, cfg.exam_package)
                if examiner_found:
                    report.review_reasons = list(report.review_reasons or []) + [
                        f"{qid} was marked not attempted, but the second examiner found an "
                        "answer on the page and gave it marks. Check the page and mark it."
                        for qid in examiner_found]
                    print(f"[moderation] {filename or 'sheet'}: the second examiner found "
                          f"answers marked not attempted: {', '.join(examiner_found)}",
                          flush=True)
                # Except flagged credit: the page did not bear it out and the examiner
                # who looked could not confirm it either, so it is not kept.
                from grading_quality import uncertified_credit_keys
                unsupported_keys = uncertified_credit_keys(
                    report, ruled, unverified_rows,
                    include_awards=check_awards, selected_qids=selected_norm)
                if unverified_rows:
                    rows = _audit_rows(report, check_awards, selected_norm)
                    names = list(dict.fromkeys(
                        str(rows[i][0].qid) for i in sorted(unverified_rows) if i < len(rows)))
                    verification_notes.append(
                        f"The second examiner could not certify {len(unverified_rows)} of "
                        f"{expected_count} decision(s) ({', '.join(names[:10])}"
                        + (f" and {len(names) - 10} more" if len(names) > 10 else "")
                        + "); those marks are the first examiner's.")
                    for row in sorted(unverified_rows)[:12]:
                        print(f"[moderation] {filename or 'sheet'}: row {row} not certified: "
                              f"{unverified_rows[row][:160]}", flush=True)
                print(f"[moderation] {filename or 'sheet'}: applying {len(verdicts)} of "
                      f"{expected_count} certified decision(s)", flush=True)
                # The tick moves only where the examiner's evidence line resolves
                # inside the answer; a ruling whose anchor cannot be placed keeps the
                # first examiner's anchor and still counts.
                for verdict in verdicts:
                    try:
                        apply_verified_anchors(report, [verdict], ocr_pages,
                                               include_awards=check_awards, selected_qids=selected_norm)
                    except StrictGradeError as exc:
                        print(f"[moderation] {filename or 'sheet'}: row {verdict.row} anchor kept "
                              f"from the first examiner ({exc})", flush=True)
                    except Exception as exc:  # noqa: BLE001 - an anchor is placement, never a mark
                        print(f"[moderation] {filename or 'sheet'}: row {verdict.row} anchor "
                              f"skipped ({type(exc).__name__}: {exc})", flush=True)
            if commerce_required_qids:
                commerce_certified_qids = _commerce_certified_questions(
                    report, verdicts, check_awards, selected_norm)
                report.verified_commerce_point_order_qids = sorted(
                    _commerce_point_order_certificates(
                        report, verdicts, check_awards, selected_norm))
            if _category_of(cfg) == "commerce":
                _preserve_commerce_audit_feedback(verdicts)
            report, moved = apply_audit(
                report, verdicts, include_awards=check_awards,
                selected_qids=selected_norm)
            if moved:
                print(f"[moderation] moved {moved:+g} marks on {filename or 'sheet'}")
            if unsupported_keys:
                from grading_quality import remove_unsupported_credit
                for note in remove_unsupported_credit(report, unsupported_keys):
                    verification_notes.append(note)
                    print(f"[moderation] {filename or 'sheet'}: {note}", flush=True)
            commerce_certified_digests = _commerce_question_digests(
                report, commerce_certified_qids)
            verification_complete = not unverified_rows
        except StrictGradeError as exc:
            # The second examiner re-checks marks that are already decided and
            # evidenced. When its own reply falls short - a quote from the wrong OCR
            # line, a missing bbox, a row it did not rule on - the marks are still
            # the first examiner's, which is worth SAYING, not worth destroying the
            # copy over. MEASURED 2026-09-16: two of six copies were refused for
            # exactly this, after the marking itself had finished and been paid for.
            if not (cfg.is_strict and exc.code in _VERIFICATION_SOFT_CODES):
                raise
            verification_notes.append(str(exc))
            print(f"[moderation] {filename or 'sheet'}: not certified — {exc}",
                  flush=True)
        except Exception as exc:
            traceback.print_exc()
            if cfg.is_strict:
                verification_notes.append(f"The second examiner's pass failed: {exc}")
        finally:
            # A malformed/truncated verifier response was still a billable call.
            # Merge its attested usage even when strict parsing holds the copy.
            _merge_usage(usage, audit_usage)

    if cfg.is_strict:
        _clear_phantom_attempts(report, cfg)  # nothing the second examiner zeroed takes a place
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
        print(f"[denominator] {report.max_total:g} != official {float(total_marks):g} "
              f"after enforcement — forcing", flush=True)
        if cfg.is_strict:
            # Used to refuse the copy. The forced total is flagged (maxima_rescaled)
            # so the release gate sends the copy to FACULTY REVIEW with its PDF.
            report.review_reasons.append(
                f"The final denominator {report.max_total:g} differed from the official "
                f"{float(total_marks):g} and was forced; the split needs a faculty check.")
            report = force_official_total(report, float(total_marks))
            report.maxima_rescaled = True
        else:
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
            report, cfg.exam_package, answer_map=answer_map, finalize_choices=True,
            choices_already_finalized=bool(cfg.exam_package.choice_groups))
        # Only meaningful when a certificate was actually taken. When the second
        # examiner could not certify, there is no certificate to contradict.
        if verification_digest is not None and verification_digest != report_decision_digest(report):
            # The marks are the ones shown on the copy; what is stale is the
            # certificate taken before the final compile. Say so, do not shred a
            # marked copy over its own bookkeeping.
            verification_complete = False
            verification_notes.append(
                "A mark changed after the second examiner's pass, so its certificate "
                "no longer covers this copy.")

    # CA, CMA and CS: the SMEs asked for an overall remark on every copy with one
    # sentence on presentation and exam writing (2026-09-23). MEASURED on the live CA
    # check of 6 Oct 2026: all 14 cover remarks were the code's one line fallback
    # ("Before the next test, review Q1(c): ..."), because the strict path grades in
    # batches and no batch writes the cover. Written once here, from the final marks.
    # A text only call over remarks already in hand; it fails open to the fallback.
    # Vidyapeeth copies too: their cover was the same one line fallback ("Before the
    # next test, review Q9: ..."), and their faculty ask on nearly every answer for the
    # presentation a remark can name (Parishram 20 Sept review, 2026-10-09). Teaching
    # exam candidates get the same remark (teacher recruitment mocks, 2026-10-10).
    # PW Skills assignments too, with an appreciation on a full-mark copy (PW Skills
    # team, 22 Oct 2026: full-mark submissions received no feedback at all).
    # Gurukul diagnostic tests (Grades 1 to 8, 20 Nov 2026): the cover says what the
    # child knows and what to revise, in words a young child reads, with no sentence
    # on headings and conclusions (that one is for Class 11 and 12 answers).
    professional = _category_of(cfg) == "ca" or cfg.exam_type == "professional"
    assignment = _category_of(cfg) == "innovation"
    diagnostic = _category_of(cfg) == "gurukul"
    if assignment:
        _remarks_from_criteria(report)
    if cfg.is_claude and (professional or assignment or diagnostic
                          or _category_of(cfg) in ("vidyapeeth", "teaching")):
        from grader import _compose_overall_remarks
        cover_usage: dict = {}
        try:
            cover = _compose_overall_remarks(
                report, model=cfg.model, google_token=cfg.google_token,
                task_id=cfg.task_id, filename=filename, subject=subject,
                usage=cover_usage, professional=professional,
                presentation=not (professional or assignment or diagnostic),
                assignment=assignment, diagnostic=diagnostic)
            if cover.strip():
                report.overall_remarks = cover
        except Exception as exc:  # noqa: BLE001 - a cover remark never costs a copy
            print(f"[remarks] {filename or 'sheet'}: cover remark skipped "
                  f"({type(exc).__name__}: {exc})", flush=True)
        finally:
            _merge_usage(usage, cover_usage)

    # Scores are now final. Keep post-audit feedback short, aligned to deductions,
    # and useful even when the single-call grader left the cover comment blank.
    report = finalise_student_remarks(report)
    # The student reads plain sentences only: no semicolons, no dashes, and a wrong
    # MCQ says which option was right in the paper's own words.
    from wording import finalise_wording, paper_numbers_options
    try:
        report = finalise_wording(report, option_texts_from_paper(qp_text) if qp_text else {},
                                  numbered=paper_numbers_options(qp_text))
    except Exception as exc:  # noqa: BLE001 - wording must never cost a copy
        print(f"[remarks] wording pass skipped ({type(exc).__name__}: {exc})", flush=True)
    feedback_problems = report_remark_problems(report)
    if feedback_problems:
        if cfg.is_strict:
            # A thin remark is a worse copy, not a wrong one. MEASURED 2026-09-16:
            # "Q7.b: missing" - one question's note - refused a copy on which every
            # other question was marked, evidenced and explained.
            feedback_notes.extend(feedback_problems[:8])
            print(f"[remarks] {filename or 'sheet'}: weak feedback on "
                  + "; ".join(feedback_problems[:8]), flush=True)
        else:
            raise ValueError(
                "Refusing to release non-actionable student feedback: "
                + "; ".join(feedback_problems[:8])
            )

    # CA: a step docked for a figure OCR misread is re-read from its line, and gets its
    # marks back when the page shows the step's own result (value_recheck.py,
    # restore_misread_steps; review of the CA FM MT01 copies, 22 Oct 2026). Fails open.
    if cfg.is_strict and ocr_pages and sa_pngs and _category_of(cfg) == "ca":
        from value_recheck import restore_misread_steps
        restore_usage: dict = {}
        try:
            restored = restore_misread_steps(
                report, sa_pngs, ocr_pages, google_token=cfg.google_token,
                task_id=cfg.task_id, filename=filename, usage_out=restore_usage)
            if restored:
                report.review_reasons = list(report.review_reasons or []) + restored
                print(f"[value-recheck] {filename or 'sheet'}: " + "; ".join(restored), flush=True)
        except Exception as exc:  # noqa: BLE001 - a second look never costs a copy
            print(f"[value-recheck] {filename or 'sheet'}: restore skipped "
                  f"({type(exc).__name__}: {exc})", flush=True)
        finally:
            _merge_usage(usage, restore_usage)

    # A deduction that quotes the student's value ("29.5, not 20.5") is re-read from a
    # crop of that line; a page that does not show the quoted value sends the copy to a
    # faculty reviewer (value_recheck.py). Fails open.
    if cfg.is_strict and ocr_pages and sa_pngs:
        from value_recheck import quoted_values, recheck_quoted_values
        if quoted_values(report):
            recheck_usage: dict = {}
            try:
                disputed = recheck_quoted_values(
                    report, sa_pngs, ocr_pages, google_token=cfg.google_token,
                    task_id=cfg.task_id, filename=filename, usage_out=recheck_usage)
                if disputed:
                    report.review_reasons = list(report.review_reasons or []) + disputed
                    print(f"[value-recheck] {filename or 'sheet'}: " + "; ".join(disputed),
                          flush=True)
            except Exception as exc:  # noqa: BLE001 - a second look never costs a copy
                print(f"[value-recheck] {filename or 'sheet'}: skipped "
                      f"({type(exc).__name__}: {exc})", flush=True)
            finally:
                _merge_usage(usage, recheck_usage)

    # LANGUAGE PAPERS ONLY: underline the wrong words two independent readings
    # agree on. Runs after every mark is final because it never changes one, and
    # fails open: a copy without underlines is still a correctly marked copy.
    if getattr(cfg, "language_marks", False) and ocr_pages:
        from language_marks import is_language_paper, take_word_marks
        if is_language_paper(subject, student_class):
            stage("verifying", "Marking clearly misspelt words on the page")
            word_usage: dict = {}
            unplaced: list[dict] = []
            try:
                report.word_marks = take_word_marks(
                    report=report, ocr_pages=ocr_pages, student_pngs=sa_pngs,
                    subject=subject, student_class=student_class,
                    reference_text=f"{qp_text or ''}\n{ak_text or ak_text_layer or ''}",
                    google_token=cfg.google_token, task_id=cfg.task_id,
                    filename=filename, usage_out=word_usage, notes_out=unplaced)
                # The student is told every confirmed slip, underlined or not
                # (Bihar Board English SME, 21 Oct 2026). No mark changes.
                from language_marks import spelling_sentence
                listed = spelling_sentence(list(report.word_marks or []) + unplaced)
                if listed:
                    report.overall_remarks = " ".join(
                        part for part in (str(report.overall_remarks or "").strip(), listed) if part)
            except Exception as exc:  # noqa: BLE001 - see language_marks.take_word_marks
                print(f"[language-marks] {filename or 'sheet'}: skipped "
                      f"({type(exc).__name__}: {exc})", flush=True)
            finally:
                _merge_usage(usage, word_usage)

    _finish_commerce_feedback(report, ocr_pages, cfg, subject or "",
                              commerce_required_qids, commerce_certified_qids,
                              commerce_certified_digests)
    stage("aggregating", "Recomputing totals and pricing every provider call")
    cost = compute_cost(usage, mathpix_pages=mathpix_pages, usd_to_inr=usd_to_inr)
    return {
        "report": report,
        "sheet_notes": sheet_notes,
        "ocr_pages": ocr_pages,
        "mathpix_pages": mathpix_pages,
        "rotated_pages": rotated_pages,
        "usage": usage,
        "cost": cost,
        "verification_complete": verification_complete,
        "verification_notes": verification_notes,
        "feedback_notes": feedback_notes,
        "verification_qids": verification_qids,
        "budget": {
            # Infinity is only an internal planning value, never JSON or a bill.
            "cap_inr": hard_cap if cfg.enforce_budget else None,
            "enforced": cfg.enforce_budget,
            "actual_inr": float(cost.total_inr),
            "within_cap": bool(cost.rates_found and cost.usage_attested
                               and cost.total_inr <= hard_cap + 1e-6) if cfg.enforce_budget else None,
            "rates_found": bool(cost.rates_found),
            "usage_attested": bool(cost.usage_attested),
        },
        "answer_map": answer_map.as_dict() if answer_map is not None else None,
    }


MIN_PHOTO_EDGE = 800
# The typed-page test (a printed page is not a photographed answer sheet) lives
# in typed_sheets.is_text_layer_page, imported above as _is_text_layer_page.


def largest_photo_edge(data: bytes, filename: str) -> int | None:
    """Long edge in pixels of the biggest photo in the upload; None when it has none
    (a digital PDF, or one made mostly of printed text pages) or cannot be read."""
    try:
        name = (filename or "").lower()
        if name.endswith((".png", ".jpg", ".jpeg")):
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                return max(img.size)
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        pages = list(doc)
        # An upload made mostly of printed text pages is a document, not photos of an
        # answer: the typed-document and not-an-answer-sheet checks decide it, after
        # OCR (_refuse_other_paper). Sweep of 12,035 stored uploads: 635 printed
        # documents no longer reach faculty review as PHOTO_TOO_SMALL and no upload
        # newly does. (Counting only the photo pages of such a document instead sent
        # 14 printed notes and books, a 616 px diagram on one page, to faculty review.)
        text_pages = sum(1 for page in pages if _is_text_layer_page(page))
        if text_pages and text_pages * 2 > len(pages):
            doc.close()
            return None
        edges = [max(int(info[2]), int(info[3]))
                 for page in pages for info in page.get_images(full=True)]
        doc.close()
        return max(edges) if edges else None
    except Exception:
        return None


def _render_copy(data, filename):
    """(the copy's bytes, its page images), mending a PDF that MuPDF refuses (pdf_safety).
    Done here, before the paid check: a page that cannot be copied used to fail only when
    the checked copy was built, after the check. A file that cannot be mended fails as
    before, so a copy is never marked with a page missing."""
    is_pdf = str(filename or "").lower().endswith(".pdf")
    try:
        pngs = pdf_or_image_to_pngs(data, filename)
    except Exception as refused:
        if not is_pdf:
            raise
        try:
            mended = pdf_safety.repair(data)
        except Exception:
            raise refused from None
        if mended == data:
            raise
        print(f"[pdf] {filename}: mended after {refused}", flush=True)
        return mended, pdf_or_image_to_pngs(mended, filename)
    if is_pdf and not pdf_safety.every_page_copies(data):
        try:
            mended = pdf_safety.repair(data)
            redrawn = pdf_or_image_to_pngs(mended, filename)
        except Exception as exc:  # noqa: BLE001 - its pages drew, so it goes on as it came
            print(f"[pdf] {filename}: a page could not be copied and the mend failed: {exc}", flush=True)
            return data, pngs
        print(f"[pdf] {filename}: mended, a page could not be copied", flush=True)
        return mended, redrawn
    return data, pngs


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
    # A photo this small cannot be read reliably, whatever it shows (Anuj's Biology
    # upload, 2026-09-30: two 720 px images stretched over A4, graded and released).
    # It goes to a faculty reviewer before anything is paid for.
    intake = _intake_of(cfg)
    # A typed category's page limit (School of Innovation: a learner's spreadsheet
    # printed to 480 pages of data rows). Checked before anything is rendered or paid.
    if intake.max_pages:
        oversize = too_many_pages_reason(sa_bytes, filename, intake.max_pages)
        if oversize:
            raise StrictGradeError("NOT_VERIFIED_ANSWER_SHEET", oversize)
    converted = False
    label_free_pages: frozenset = frozenset()
    linked_notes: list[str] = []
    if intake.linked_work:
        # The learner's linked work (GitHub, Drive, Sheets, Colab, OneDrive) is opened
        # and notebooks, workbooks, Power BI and Tableau files are read, all into ONE
        # PDF that is graded and marked like a typed submission (linked_work.py).
        if stage_callback:
            stage_callback("normalizing", "Opening the learner's linked work and files")
        from linked_work import prepare_submission, review_notes, unopened_reason
        prepared = prepare_submission(sa_bytes, filename)
        # Before anything is paid for: a cover whose work could not be reached is a
        # person's to mark, whether or not a document was built (a cover with no link
        # the checker can follow builds none).
        unopened = unopened_reason(prepared)
        if unopened:
            print(f"[linked-work] {filename or 'sheet'}: not marked, {prepared.problems}", flush=True)
            raise StrictGradeError("NOT_VERIFIED_ANSWER_SHEET", unopened)
        if prepared.converted:
            print(f"[linked-work] {filename or 'sheet'}: read "
                  f"{', '.join(f.name for f in prepared.files) or 'no file'}"
                  + (f"; not opened: {prepared.problems}" if prepared.problems else ""), flush=True)
            sa_bytes, filename, converted = prepared.data, prepared.filename, True
            label_free_pages = prepared.label_free_pages
            linked_notes = review_notes(prepared)
    # The photo gate is for photographed handwriting. A document the checker built
    # embeds the learner's own pictures (notebook plots, screenshots) at their size.
    # A small photo is checked all the same and flagged: there is no faculty to mark
    # it by hand (user, 29 Oct 2026), and an upload too faint or small to read is
    # refused by the blank-booklet gate after OCR. Of the 15 held on 29 Oct, the
    # 16 to 232 px ones were printed documents whose only picture is a logo; the
    # typed-document check decides those.
    edge = None if converted else largest_photo_edge(sa_bytes, filename)
    if edge is not None and edge < MIN_PHOTO_EDGE:
        print(f"[photo] {filename or 'sheet'}: largest photo {edge} px (under {MIN_PHOTO_EDGE}); "
              "checked and flagged", flush=True)
        linked_notes.append(
            f"The largest photo in this upload is {edge} px on its long side, under "
            f"{MIN_PHOTO_EDGE} px, so some handwriting may have been misread.")
    sa_bytes, sa_pngs = _render_copy(sa_bytes, filename)
    # A typed submission's text pages are read from the PDF's own text layer
    # (typed_sheets.py), so Mathpix reads only its picture pages.
    typed_pages = (text_layer_pages(sa_bytes, filename, sa_pngs)
                   if intake.typed_submissions else {})
    # grade_prepared rights any page its OCR shows sideways, in sa_pngs in place,
    # and names those pages; the source PDF the released copy is drawn on must
    # then turn the same way (_reread_sideways_pages).
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
        typed_pages=typed_pages,
        label_free_pages=label_free_pages,
    )
    # A video the checker did not watch, or a link it could not open, is named for the
    # reviewer on a copy that was still marked from the rest of the work.
    for note in result.get("sheet_notes") or []:
        linked_notes.append(note)
    if intake.typed_submissions:
        linked_notes.append(_wrong_work_note(result["report"], result.get("ocr_pages")))
    for note in linked_notes:
        if note and note not in result["report"].review_reasons:
            result["report"].review_reasons.append(note)
    rotated = result.get("rotated_pages") or {}
    if rotated:
        from orient import normalize_orientation
        rotations = [int(rotated.get(i + 1, 0) or 0) for i in range(len(sa_pngs))]
        sa_bytes = normalize_orientation(sa_bytes, filename, rotations=rotations)[0]
    if stage_callback:
        stage_callback("rendering", "Rendering the evaluated copy")
    # ICAI awards a CA paper's total in whole marks (SME review, 2026-09-23: "ICAI
    # does not award total marks in decimals. It must be rectified and rounded off.")
    result["report"].whole_total = (cfg.exam_type or "").strip().lower() == "professional"
    pdf_bytes = build_evaluated_pdf(
        student_pdf_bytes=sa_bytes,
        student_filename=filename,
        student_pngs=sa_pngs,
        report=result["report"],
        ocr_pages=result["ocr_pages"],
        mark_scale=mark_scale_for(cfg.exam_type, subject, _category_of(cfg)),
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
    # MCQ rows the gate below cannot confirm get one close read of their own row
    # first (objective_confirm.py). A confirmed row whose verdict sat on the wrong
    # line is re-anchored to the numbered row, and a row two close reads corrected is
    # re-scored, so in either case the copy is drawn again. A failure here changes
    # nothing: the gate then holds the copy as before. MEASURED 21 Oct 2026 (CA FM
    # MT01 63c82ae8): a corrected Q3 was not drawn again, so the copy read 22 of 50
    # while its recorded score was 20.
    try:
        from objective_confirm import confirm_objective_rows, rows_to_confirm
        if rows_to_confirm(result["report"], result.get("ocr_pages"),
                           answer_map=result.get("answer_map"), paper_text=qp_text or ""):
            if stage_callback:
                stage_callback("confirming_mcq", "Reading the unconfirmed MCQ rows once more")
            confirm_usage: dict = {}
            confirmed = confirm_objective_rows(
                result["report"], result.get("ocr_pages") or [], sa_pngs,
                answer_map=result.get("answer_map"), paper_text=qp_text or "",
                google_token=cfg.google_token, task_id=cfg.task_id, filename=filename,
                usage_out=confirm_usage)
            for note in confirmed.notes:
                print(f"[objective-confirm] {filename or 'sheet'}: {note}", flush=True)
            if confirm_usage:
                _merge_usage(result["usage"], confirm_usage)
                result["cost"] = compute_cost(result["usage"],
                                              mathpix_pages=result.get(
                                                  "mathpix_pages",
                                                  len(sa_pngs) if use_mathpix else 0),
                                              usd_to_inr=usd_to_inr)
            if confirmed.reanchored or confirmed.corrected:
                pdf_bytes = build_evaluated_pdf(
                    student_pdf_bytes=sa_bytes, student_filename=filename, student_pngs=sa_pngs,
                    report=result["report"], ocr_pages=result["ocr_pages"],
                    mark_scale=mark_scale_for(cfg.exam_type, subject, _category_of(cfg)),
                    safe_layout=False)
    except Exception:
        traceback.print_exc()
    # Free release gate. Fourteen of the eighteen defects found on real evaluated
    # copies were mechanical — wrong denominator, a question graded twice under one
    # label, a tick on a partly-earned step, a remark printed over the line above.
    # None of those need a model, so none of them cost anything to catch.
    if stage_callback:
        stage_callback("release_check", "Running the fail-closed automatic release gate")
    layout_repaired = False
    try:
        from preflight import blocking, check_copy, release_blocking, summarise
        issues = check_copy(result["report"], pdf_bytes=pdf_bytes,
                            ocr_pages=result.get("ocr_pages"),
                            answer_map=result.get("answer_map"),
                            exam_package=cfg.exam_package if cfg.is_strict else None,
                            total_marks=total_marks, paper_text=qp_text or "")
        # A layout collision is not an academic failure. Re-render once in the
        # conservative margin-only mode and run the complete release gate again.
        # This repairs the copy automatically instead of returning an avoidable
        # "Ungradable" result to the user.
        # Margin notes written over each other are unreadable in every category: a CA
        # Taxation copy reached the faculty with its Q1(a) remarks stacked on one
        # another (SME review, 17 Oct 2026), because only commerce copies were
        # repaired for it.
        repairable_layout_codes = {
            "mark-overlaps-writing", "text-overflow", "question-not-shown",
            "mark-away-from-evidence", "required-mark-missing", "answer-not-marked",
            "mark-collision", "invisible-mark", "mark-on-another-row", "feedback-overlap",
        }
        if _category_of(cfg) == "commerce":
            repairable_layout_codes.update({
                "printed-award-mismatch", "step-award-mismatch", "award-circle-missing",
                "margin-score-not-marked", "step-awards-do-not-reconcile",
                "mark-outside-answer", "mark-outside-sheet", "question-total-not-marked",
                "duplicate-sheet-total", "feedback-outside-right-margin",
                "verdict-not-visible",
            })
        if any(i.code in repairable_layout_codes for i in blocking(issues)):
            if stage_callback:
                stage_callback("repairing_layout",
                               "Repairing annotation placement and checking the copy again")
            repaired_report = result["report"].model_copy(deep=True)
            repaired_pdf = build_evaluated_pdf(
                student_pdf_bytes=sa_bytes,
                student_filename=filename,
                student_pngs=sa_pngs,
                report=repaired_report,
                ocr_pages=result["ocr_pages"],
                mark_scale=mark_scale_for(cfg.exam_type, subject, _category_of(cfg)),
                safe_layout=True,
            )
            repaired_issues = check_copy(
                repaired_report, pdf_bytes=repaired_pdf,
                ocr_pages=result.get("ocr_pages"),
                answer_map=result.get("answer_map"), total_marks=total_marks,
                exam_package=cfg.exam_package if cfg.is_strict else None,
                paper_text=qp_text or "")
            old_layout_blocks = sum(
                1 for i in blocking(issues) if i.code in repairable_layout_codes)
            new_layout_blocks = sum(
                1 for i in blocking(repaired_issues) if i.code in repairable_layout_codes)
            if (new_layout_blocks < old_layout_blocks
                    and len(blocking(repaired_issues)) < len(blocking(issues))):
                pdf_bytes = repaired_pdf
                result["report"] = repaired_report
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
        # Layout and wording defects are repaired above and reported below; only a
        # wrong or unprovable SCORE holds the copy (preflight.SCORE_INTEGRITY_CODES).
        blocked = release_blocking(issues, _category_of(cfg))
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
            budget_cap_inr=result["budget"]["cap_inr"],
            mandatory_verification_complete=bool(result["verification_complete"]),
            category=_category_of(cfg),
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
        # Concerns that do not hold the copy still have to reach the teacher.
        known = {(str(getattr(issue, "code", "")), str(getattr(issue, "detail", "")))
                 for issue in issues}
        from preflight import Issue
        for note in strict_decision.warnings:
            if (note.code, note.detail) not in known:
                issues.append(Issue("warn", note.code, note.detail))
                known.add((note.code, note.detail))
        for detail in result.get("verification_notes") or []:
            if ("not-certified", str(detail)) not in known:
                issues.append(Issue("warn", "not-certified", str(detail)))
                known.add(("not-certified", str(detail)))
        for detail in result.get("feedback_notes") or []:
            if ("weak-feedback", str(detail)) not in known:
                issues.append(Issue("warn", "weak-feedback",
                                    f"thin written feedback: {detail}"))
                known.add(("weak-feedback", str(detail)))

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
    # The exam category's own release rule (categories/<id>.py). A category whose
    # marking is not yet calibrated against faculty marks (CA since the 6 Oct 2026
    # live check) sends every copy to a faculty reviewer, marked and with its PDF.
    held_by_category = _category_review_hold(cfg, str(subject or ""))
    if held_by_category is not None:
        from preflight import Issue
        issue = Issue("block", "category-review", held_by_category)
        issues.append(issue)
        blocked.append(issue)
        release = False
    repeated = _repeated_question_hold(cfg)
    if repeated is not None:
        from preflight import Issue
        issue = Issue("block", "repeated-question", repeated)
        issues.append(issue)
        blocked.append(issue)
        release = False
    return {"report": result["report"], "cost": result["cost"], "pdf_bytes": pdf_bytes,
            "issues": issues, "blocking": blocked, "release": release,
            "ocr_pages": result.get("ocr_pages") or [],
            "verification_complete": result.get("verification_complete", False),
            "verification_qids": result.get("verification_qids") or [],
            "layout_repaired": layout_repaired,
            "budget": result.get("budget") or {},
            "answer_map": result.get("answer_map"),
            "package_id": cfg.exam_package.package_id if cfg.exam_package else None}
