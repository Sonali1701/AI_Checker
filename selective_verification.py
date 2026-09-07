"""Deterministic risk selection for bounded automatic second-pass verification."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from remark_quality import remark_quality_problem


@dataclass(frozen=True)
class VerificationCandidate:
    qid: str
    risk_score: float
    possible_delta: float
    reasons: tuple[str, ...]
    mandatory: bool = False

    @property
    def priority(self) -> float:
        return self.risk_score * max(0.5, self.possible_delta)


def _objective(question) -> bool:
    return bool(getattr(question, "objective", False)
                or (getattr(question, "correct_option", "") or "").strip())


_QUESTION_HEADER_RE = re.compile(
    r"(?m)^\s*(?:Q(?:uestion)?\s*)?(\d{1,3})\s*[.)]\s*$",
    re.IGNORECASE,
)
_VISUAL_INSTRUCTION_RE = re.compile(
    r"\b(?:draw|sketch|plot|construct|shade)\b.{0,100}"
    r"\b(?:diagram|graph|figure|number\s+line|venn)\b",
    re.IGNORECASE | re.DOTALL,
)


def visual_evidence_qids(question_paper_text: str, report) -> list[str]:
    """Questions that must be re-read from the page even after a full award."""
    text = str(question_paper_text or "")
    headers = list(_QUESTION_HEADER_RE.finditer(text))
    visual_numbers: set[int] = set()
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        if _VISUAL_INSTRUCTION_RE.search(text[header.end():end][:1200]):
            visual_numbers.add(int(header.group(1)))
    out: list[str] = []
    for question in list(getattr(report, "questions", None) or []):
        qid = str(getattr(question, "qid", "") or "")
        match = re.match(r"\s*[Qq]?(\d{1,3})(?:\D|$)", qid)
        if match and int(match.group(1)) in visual_numbers:
            out.append(qid)
    return out


def verification_candidates(report) -> list[VerificationCandidate]:
    false_blanks = set(getattr(report, "false_blank_qids", None) or [])
    unresolved_answers = set(getattr(report, "unresolved_answer_qids", None) or [])
    unverified_objectives = set(
        getattr(report, "unverified_objective_qids", None) or [])
    choice_candidates = set(getattr(report, "choice_candidate_qids", None) or [])
    visual_required = set(
        getattr(report, "mandatory_verification_qids", None) or [])
    candidates: list[VerificationCandidate] = []
    for question in list(getattr(report, "questions", None) or []):
        qid = str(getattr(question, "qid", "") or "")
        if _objective(question) and qid not in unverified_objectives:
            continue
        maximum = max(0.0, float(getattr(question, "max_score", 0.0) or 0.0))
        score = min(maximum, max(0.0, float(getattr(question, "score", 0.0) or 0.0)))
        if maximum <= 0:
            continue
        reasons: list[str] = []
        risk = 0.0
        mandatory = False
        criteria = list(getattr(question, "criteria", None) or [])
        if qid in visual_required:
            reasons.append("official-instruction-requires-visual-artifact")
            risk += 90
            mandatory = True
        if qid in unverified_objectives:
            reasons.append("provider-claimed-unofficial-objective")
            risk += 120
            mandatory = True
        if qid in choice_candidates:
            reasons.append("score-affects-choice-selection")
            risk += 60
            mandatory = True
        if qid in false_blanks:
            reasons.append("answer-called-blank-has-writing")
            risk += 100
            mandatory = True
        if qid in unresolved_answers and not bool(getattr(question, "attempted", True)):
            reasons.append("unresolved-location-called-blank")
            risk += 80
            mandatory = True
        if maximum > 1 and not criteria:
            reasons.append("no-criterion-breakdown")
            risk += 12
            mandatory = True
        if bool(getattr(question, "attempted", True)) and score <= 0 and maximum >= 2:
            reasons.append("attempted-high-value-zero")
            risk += 9
        if score >= maximum - 1e-9 and maximum >= 4:
            reasons.append("high-value-full-award")
            risk += 7
        if 0 < score < maximum:
            reasons.append("partial-credit-judgment")
            risk += 4
        remark_problem = ""
        if score < maximum and bool(getattr(question, "attempted", True)):
            remark_problem = remark_quality_problem(getattr(question, "remark", ""))
        if remark_problem:
            reasons.append(f"deduction-remark-{remark_problem}")
            risk += 10
            mandatory = True
        missing_evidence = 0
        invalid_anchors = 0
        provisional_points = 0
        for criterion in criteria:
            awarded = float(getattr(criterion, "awarded", 0.0) or 0.0)
            criterion_max = float(getattr(criterion, "max", 0.0) or 0.0)
            if awarded < criterion_max - 1e-9 and not (
                    getattr(criterion, "evidence", "") or "").strip():
                missing_evidence += 1
            if not (getattr(criterion, "step_line_id", "") or "").strip():
                invalid_anchors += 1
            if str(getattr(criterion, "description", "") or "").strip().lower() == (
                    "unallocated official rubric point"):
                provisional_points += 1
        if provisional_points:
            reasons.append(f"{provisional_points}-provisional-rubric-point(s)")
            risk += 20 + provisional_points
            mandatory = True
        if missing_evidence:
            reasons.append(f"{missing_evidence}-deduction(s)-without-evidence")
            risk += 8 + missing_evidence
            mandatory = True
        if criteria and invalid_anchors == len(criteria):
            reasons.append("no-step-anchors")
            risk += 6
        if not math.isfinite(score) or not math.isfinite(maximum):
            reasons.append("non-finite-marks")
            risk += 100
            mandatory = True
        if reasons:
            candidates.append(VerificationCandidate(
                qid=qid,
                risk_score=risk,
                possible_delta=max(score, maximum - score),
                reasons=tuple(reasons),
                mandatory=mandatory,
            ))
    return sorted(
        candidates,
        key=lambda item: (not item.mandatory, -item.priority, item.qid.lower()),
    )


def select_verification_qids(report, *, max_questions: int) -> tuple[list[str], list[str]]:
    """Return (selected qids, mandatory qids that could not fit the bound)."""
    candidates = verification_candidates(report)
    limit = max(0, int(max_questions))
    selected = candidates[:limit]
    selected_qids = [item.qid for item in selected]
    selected_set = set(selected_qids)
    omitted_mandatory = [item.qid for item in candidates
                         if item.mandatory and item.qid not in selected_set]
    return selected_qids, omitted_mandatory
