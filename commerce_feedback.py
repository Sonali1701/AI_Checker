"""Evidence safeguards from the commerce review, including its mathematics copies.

This module makes no academic judgement and buys no provider calls. It removes
credit supported only by explicitly missing or cancelled work, and supplies
questions for the pipeline's existing second examiner to resolve. Mathematical
correctness, symbols and the allocation of a one-mark answer remain that examiner's
job: text matching here locates a live rewrite, never decides whether it is right.
"""
from __future__ import annotations

import re
import unicodedata
from copy import copy
from dataclasses import dataclass, field

from anchors import canonical_line_id
from exam_package import normalize_qid
from mathpix import is_header_line
from remark_quality import fragment_problem, remark_quality_problem


@dataclass
class CommerceFeedbackResult:
    notes: list[str] = field(default_factory=list)
    verification_qids: list[str] = field(default_factory=list)
    verification_reasons: dict[str, str] = field(default_factory=dict)
    math_verification_qids: list[str] = field(default_factory=list)
    changed: bool = False

    def require(self, qid: str, reason: str) -> None:
        if qid not in self.verification_qids:
            self.verification_qids.append(qid)
        previous = self.verification_reasons.get(qid, "")
        if reason not in previous:
            self.verification_reasons[qid] = (previous + " " + reason).strip()


_MATH_SUBJECT_RE = re.compile(r"math|quantitative|statistics", re.IGNORECASE)
_ACCOUNTS_SUBJECT_RE = re.compile(r"accountancy|accounting|book\s*keeping", re.I)
_POINTS_SUBJECT_RE = re.compile(r"econom|business\s+stud(?:y|ies)|\bbst\b", re.I)
_METHOD_RE = re.compile(r"\b(?:method|working|steps?|formula|substitution|calculation|algebra|derivation)\b", re.I)
_WORKING_ONLY_RE = re.compile(
    r"\b(?:no|missing|unshown|absent|without)\s+(?:the\s+)?(?:working|steps?|calculation)\b"
    r"|\b(?:working|steps?|calculation)\s+(?:is\s+|was\s+)?(?:not\s+shown|missing|absent)\b"
    r"|\b(?:too\s+brief|brief\s+(?:answer|response)|lacks?\s+explanation)\b", re.I)
_SUBSTANTIVE_FAULT_RE = re.compile(
    r"\b(?:wrong|incorrect|error|unreadable|unclear|illegible|contradict\w*|incomplete\s+(?:method|calculation))\b", re.I)

_MATH_REVIEW = (
    "Read the ORIGINAL image and recompute the exact expression, including signs, "
    "powers, subscripts, inequalities and matrix entries. Verify each live method "
    "step separately; missing or cancelled steps earn nothing. When a deduction "
    "depends on a symbol, quote the student's expression and the correct expression "
    "in a complete, specific correction. Unreadable symbols are uncertain, never a guess."
)
_ONE_MARK_REVIEW = (
    "This part is worth at most one mark. Check the official allocation and the "
    "live answer against the key. A correct final value earns the entire part; do "
    "not invent a half-mark method deduction solely for unshown working or brevity. "
    "If the question asks to show, prove or derive, assess that working instead."
)
_ACCOUNT_REVIEW = (
    "Inspect every account or statement on the ORIGINAL pages, including continuations. "
    "Check heads, debit/credit sides, columns, balances and totals against the key. "
    "An account genuinely drawn out of its required format earns nothing for that "
    "account; a proper carried-forward continuation is valid. Distinguish a single "
    "placement slip in an otherwise valid account from a whole account out of format. "
    "State the required format or corrected treatment specifically when deducting."
)
_POINTS_REVIEW = (
    "Check how many distinct valid points the question requests and what the student "
    "actually states. Check the FIRST N live points in physical writing order, "
    "including wrong points among that first N; ignore extras and never replace an "
    "earlier wrong point with a later correct point. Correct Economics or Business Studies points keep their full "
    "allocation despite brief wording; as many valid points as requested earns full "
    "marks. Follow the official 'any N' requirement and the independent OR-choice policy without "
    "inventing a key-only point or a deduction per brief point. Explain any substantive "
    "missing or wrong point specifically and do not silently pay extra points."
)


def _field(value, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _matching_text(value: str) -> str:
    # Preserve mathematical operators. In particular, x<2 cannot locate x>2.
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("−", "-")
    text = re.sub(r"\s+", " ", text).strip()
    # Ignore spaces around operators, retaining word boundaries: deleting all
    # spaces would turn "Q10 answer = 4" into "q10answer=4" and hide the live quote.
    return re.sub(r"\s*([^\w\s])\s*", r"\1", text)


def _quote_matches(quote: str, text: str) -> bool:
    needle, haystack = _matching_text(quote), _matching_text(text)
    if not needle:
        return False
    # A bare 2 must not locate 12, 2.5 or a variable called x2. This is only an
    # evidence-location check; equivalent mathematical values are not compared.
    return bool(re.search(r"(?<![\w.])" + re.escape(needle) + r"(?!\w|\.\d)", haystack))


def _owned_lines(report, question, known):
    qid = normalize_qid(question.qid)
    regions = getattr(report, "answer_regions", None) or {}
    region = next((value for key, value in regions.items() if normalize_qid(key) == qid), {})
    ids = list(region.get("line_ids", []) or []) + list(region.get("evidence_line_ids", []) or [])
    # Without a mapped region, only the question's explicit references are safe.
    ids += [getattr(question, "anchor_line_id", "")]
    ids += [getattr(c, "step_line_id", "") for c in getattr(question, "criteria", []) or []]
    foreign = {canonical_line_id(lid) for key, other in regions.items()
               if normalize_qid(key) != qid
               for lid in (list(other.get("line_ids", []) or []) + list(other.get("label_line_ids", []) or []))}
    owned = {}
    for lid in ids:
        lid = canonical_line_id(lid)
        line = known.get(lid)
        if (line is not None and lid not in foreign and not getattr(line, "cancelled", False)
                and not is_header_line(str(getattr(line, "text", "") or ""))):
            owned[lid] = line
    return owned


def _zero_step(criterion, reason: str) -> None:
    criterion.awarded = 0.0
    criterion.met = False
    criterion.evidence_kind = "missing"
    criterion.evidence = reason


def _whole_one_mark_criterion(question, criteria, own, maximum):
    """Collapse an invented split without deciding whether the answer is correct.

    An examiner can assess the complete official one-mark deliverable only when
    its row maximum is the complete one mark. Separate .5 method/result rows make
    it impossible to award the correct result its full mark without paying for
    absent working. Preserve the existing earned score pending that assessment.
    """
    live = [c for c in criteria if canonical_line_id(c.step_line_id) in own
            and getattr(c, "evidence_kind", "") != "missing"]
    source = max(live or criteria, key=lambda c: float(c.awarded or 0))
    merged = source.model_copy(deep=True) if hasattr(source, "model_copy") else copy(source)
    merged.rubric_criterion_id = ""
    merged.description = "Complete one-mark numerical answer"
    merged.max = maximum
    merged.awarded = min(maximum, max(0.0, sum(float(c.awarded or 0) for c in criteria)))
    merged.met = merged.awarded >= maximum - 1e-9
    merged.evidence = " ".join(dict.fromkeys(
        str(c.evidence or "").strip() for c in criteria if str(c.evidence or "").strip()))
    if not live:
        merged.evidence_kind = "unresolved"
        merged.step_line_id = ""
        merged.anchor_quote = ""
    return merged


def apply_commerce_feedback_guards(report, ocr_pages, *, category: str,
                                   subject: str = "", exam_package=None) -> CommerceFeedbackResult:
    """Mutate provable evidence contradictions and return existing-examiner work.

    Call before moderation and authoritative reconciliation/certificate binding.
    Merge ``verification_qids`` into the existing selection, including awarded
    rows, and append each ``verification_reasons[qid]`` to that examiner's prompt.
    Unresolved verification must use the pipeline's existing faculty-review path.
    No new call is made here. Repeated calls are safe and do not change denominators.
    """
    result = CommerceFeedbackResult()
    if category != "commerce":
        return result
    known = {canonical_line_id(line.line_id): line for page in ocr_pages or [] for line in page.lines}
    is_math = bool(_MATH_SUBJECT_RE.search(subject or ""))
    is_accounts = bool(_ACCOUNTS_SUBJECT_RE.search(subject or ""))
    is_points = bool(_POINTS_SUBJECT_RE.search(subject or ""))
    official = {normalize_qid(str(_field(q, "qid", ""))): q
                for q in _field(exam_package, "questions", []) or []}
    visual_objectives = {normalize_qid(qid) for qid in
                         getattr(report, "objective_visual_verified_qids", None) or []}
    changed_questions = []

    for q in report.questions:
        criteria = list(getattr(q, "criteria", None) or [])
        own = _owned_lines(report, q, known)
        changed = False
        if is_math and getattr(q, "attempted", True) and not getattr(q, "objective", False):
            result.require(q.qid, _MATH_REVIEW)
            result.math_verification_qids.append(q.qid)
        if is_accounts and getattr(q, "attempted", True) and not getattr(q, "objective", False):
            result.require(q.qid, _ACCOUNT_REVIEW)
        if (is_points and getattr(q, "attempted", True) and not getattr(q, "objective", False)
                and float(q.score or 0) < float(q.max_score or 0)):
            result.require(q.qid, _POINTS_REVIEW)
        for index, criterion in enumerate(criteria):
            if float(criterion.awarded or 0) <= 0:
                continue
            if getattr(criterion, "evidence_kind", "") == "missing":
                _zero_step(criterion, "This required step is not shown in your working.")
                changed = True
                result.notes.append(f"{q.qid} step {index}: removed credit for explicitly missing work.")
                result.require(q.qid, "Check the omitted step on the original image and official rubric. "
                               "Do not award method marks for an absent step; a permitted answer-only result keeps its own marks.")
                continue
            lid = canonical_line_id(getattr(criterion, "step_line_id", ""))
            source = known.get(lid)
            if source is None or not getattr(source, "cancelled", False):
                continue
            quote = str(getattr(criterion, "anchor_quote", "") or "")
            live_matches = [line for line in own.values() if _quote_matches(quote, line.text)]
            if len(live_matches) == 1:
                criterion.step_line_id = live_matches[0].line_id
                changed = True
                result.notes.append(f"{q.qid} step {index}: moved cancelled anchor to the owned live rewrite.")
                result.require(q.qid, "Confirm the final live rewrite from the image. "
                               "The cited quote occurs on a live line, so do not deduct merely for its cancelled earlier copy.")
            elif live_matches:
                # Ambiguous live rewrites are evidence, but locating the final one
                # requires the image. Do not discard all live work over ambiguity.
                result.require(q.qid, "This step cites cancelled working and its quote occurs on several live lines. "
                               "Locate the final uncancelled attempt visually and replace the anchor before release.")
            else:
                _zero_step(criterion, "The cancelled step earns no marks. Only your final live working counts.")
                changed = True
                result.notes.append(f"{q.qid} step {index}: removed credit supported solely by cancelled working.")
                result.require(q.qid, "The credited anchor is cancelled and no owned live rewrite was located. "
                               "Read the original image for a surviving attempt; award only its live evidence.")

        # No-criteria answers need their own protection; a stale objective anchor
        # must instead be settled by the pipeline's independent choice readers.
        anchor = known.get(canonical_line_id(getattr(q, "anchor_line_id", "")))
        if (not criteria and float(q.score or 0) > 0 and anchor is not None
                and getattr(anchor, "cancelled", False)
                and not (getattr(q, "objective", False) and normalize_qid(q.qid) in visual_objectives)):
            result.require(q.qid, "The answer anchor is cancelled. Find and verify a live final attempt from the "
                           "original image; a cancelled answer alone earns no marks.")
            if not own and not getattr(q, "objective", False):
                q.score = 0.0
                # Keep the attempted flag until the original image is reviewed.
                # OCR cancellation detection can miss a rewrite; setting False
                # here would prevent an examiner from restoring its valid credit.
                q.remark = "No uncancelled answer is shown."
                changed = True
                result.notes.append(f"{q.qid}: removed an answer award supported solely by cancelled writing.")

        spec = official.get(normalize_qid(q.qid))
        maximum = float(_field(spec, "max_marks", getattr(q, "max_score", 0)) or 0)
        fixed = getattr(report, "fixed_rubric_rules", None) or {}
        has_fixed = bool(_field(spec, "criteria", []) or any(
            values for key, values in fixed.items() if normalize_qid(key) == normalize_qid(q.qid)))
        if (is_math and spec is not None and 0 < maximum <= 1 and len(criteria) > 1
                and not has_fixed and getattr(q, "attempted", True) and not getattr(q, "objective", False)
                and _field(spec, "question_type", "unknown") in ("numeric", "method", "mixed", "unknown")):
            merged = _whole_one_mark_criterion(q, criteria, own, maximum)
            q.criteria = criteria = [merged]
            changed = True
            result.notes.append(f"{q.qid}: replaced an invented one-mark split with the complete official part.")
            result.require(q.qid, _ONE_MARK_REVIEW)
            prompt = str(_field(spec, "prompt_text", "") or "").strip()
            if prompt:
                result.require(q.qid, "Official part to assess: " + prompt)
        text = " ".join([str(getattr(q, "remark", "") or ""),
                         *[str(getattr(c, "evidence", "") or "") for c in criteria]])
        if (is_math and 0 < maximum <= 1 and getattr(q, "attempted", True)
                and not getattr(q, "objective", False)
                and (any(getattr(c, "evidence_kind", "") == "missing"
                         and _METHOD_RE.search(str(c.description or "")) for c in criteria)
                     or (_WORKING_ONLY_RE.search(text) and not _SUBSTANTIVE_FAULT_RE.search(text)))):
            result.require(q.qid, _ONE_MARK_REVIEW)
        if (getattr(q, "attempted", True) and float(q.score or 0) < float(q.max_score or 0)
                and (remark_quality_problem(getattr(q, "remark", ""))
                     or fragment_problem(getattr(q, "remark", "")))):
            result.require(q.qid, "Return a complete correction naming the missing or wrong requirement and "
                           "the right expression or answer. Verify its academic meaning against the key and original image.")
        if changed:
            if criteria:
                q.score = round(sum(float(c.awarded or 0) for c in criteria), 4)
                for c in criteria:
                    c.met = float(c.max or 0) > 0 and float(c.awarded or 0) >= float(c.max) - 1e-9
            if not str(getattr(q, "remark", "") or "").strip() and float(q.score) < float(q.max_score):
                q.remark = next((c.evidence for c in criteria if float(c.awarded) < float(c.max) and c.evidence), "")
            changed_questions.append(q.qid)
    if changed_questions:
        report.total_score = round(sum(float(q.score or 0) for q in report.questions), 4)
        result.changed = True
    return result
