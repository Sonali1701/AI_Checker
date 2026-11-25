"""Server-owned proof for independently verified question/maxima graphs.

Browser rows are editable and therefore cannot be grading authority.  This module
keeps an opaque capability that binds a checked graph to the exact question-paper
bytes and official total. Successful proofs have no time limit by default; bounded
memory and process restarts still remove them. The browser may carry the token,
but it can neither create nor alter the graph behind it.
"""
from __future__ import annotations

import hashlib
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from choice_graph import ChoiceGraphError, build_choice_forest

from exam_package import (
    QUESTION_GRAPH_PROOF_VERSION,
    issue_question_graph_proof,
    normalize_qid,
)
from grader import MarksChoiceGroup, MarksChoiceOption, MarksItem, MarksScheme


ATTESTATION_VERSION = QUESTION_GRAPH_PROOF_VERSION


class MarksAttestationError(ValueError):
    """The supplied proof is absent, stale, forged, or no longer matches the form."""


def canonical_marks_signature(scheme: MarksScheme) -> tuple[tuple[str, float], ...]:
    """Stable qid/max signature; descriptions never decide a student's denominator."""
    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in list(getattr(scheme, "items", None) or []):
        qid = normalize_qid(str(getattr(item, "qid", "") or ""))
        maximum = float(getattr(item, "max", 0.0) or 0.0)
        if not qid or qid in seen or not math.isfinite(maximum) or maximum <= 0:
            raise MarksAttestationError("The detected question graph is invalid or duplicated.")
        seen.add(qid)
        rows.append((qid, round(maximum, 4)))
    if not rows:
        raise MarksAttestationError("The detected question graph is empty.")
    item_total = round(sum(maximum for _qid, maximum in rows), 4)
    reported_total = float(getattr(scheme, "total", 0.0) or 0.0)
    if not math.isfinite(reported_total) or abs(reported_total - item_total) > 0.25:
        raise MarksAttestationError(
            "The detected graph's reported total does not match its question rows.")
    return tuple(sorted(rows))


def canonical_choice_signature(
    scheme: MarksScheme,
) -> tuple[tuple[int, float, tuple[tuple[str, ...], ...]], ...]:
    """Stable scoring semantics for every internal-choice rule.

    Provider-written group and option labels are deliberately ignored. Only the
    selectable qids, how many count, and the effective contribution can change a
    student's score. The excess-attempt policy is server-owned and is attached when
    the ExamPackage is built.
    """
    rows = dict(canonical_marks_signature(scheme))
    try:
        build_choice_forest(rows, scheme.choice_groups, normalize_qid)
    except ChoiceGraphError as exc:
        raise MarksAttestationError(str(exc)) from exc
    result: list[tuple[int, float, tuple[tuple[str, ...], ...]]] = []
    seen_groups: set[tuple[int, float, tuple[tuple[str, ...], ...]]] = set()
    for group in list(getattr(scheme, "choice_groups", None) or []):
        try:
            select_count = int(getattr(group, "select_count", 0) or 0)
            contribution = round(float(
                getattr(group, "contribution_marks", 0.0) or 0.0), 4)
        except (TypeError, ValueError) as exc:
            raise MarksAttestationError("A choice rule contains invalid numbers.") from exc
        options: list[tuple[str, ...]] = []
        for option in list(getattr(group, "options", None) or []):
            qids = tuple(sorted(
                normalize_qid(str(qid or ""))
                for qid in list(getattr(option, "question_ids", None) or [])
            ))
            if not qids or any(not qid for qid in qids) or len(set(qids)) != len(qids):
                raise MarksAttestationError(
                    "Every choice option needs unique, non-empty question ids.")
            if any(qid not in rows for qid in qids):
                raise MarksAttestationError(
                    "A choice option refers to a question missing from the marks graph.")
            options.append(qids)
        if len(options) < 2 or len(set(options)) != len(options):
            raise MarksAttestationError(
                "A choice rule needs at least two distinct alternatives.")
        if select_count < 1 or select_count >= len(options):
            raise MarksAttestationError(
                "A choice rule has an invalid number of selectable alternatives.")
        if not math.isfinite(contribution) or contribution <= 0:
            raise MarksAttestationError("A choice rule has an invalid contribution.")
        signature = (select_count, contribution, tuple(sorted(options)))
        if signature in seen_groups:
            raise MarksAttestationError("The choice graph contains a duplicate rule.")
        seen_groups.add(signature)
        result.append(signature)
    return tuple(sorted(result))


def effective_marks_total(scheme: MarksScheme) -> float:
    """Maximum one student can score after applying every choice rule."""
    rows = dict(canonical_marks_signature(scheme))
    canonical_choice_signature(scheme)
    return build_choice_forest(rows, scheme.choice_groups, normalize_qid).effective_total


_ALTERNATIVE_QID_RE = re.compile(r"(?:^|\.)(?:alt\d*|or\d*)$", re.IGNORECASE)


def choice_total_gap(scheme: MarksScheme | None, official_total: float | None) -> str:
    """Why a graph's one-student total misses the official total ("" when it matches).

    Written for the teacher and for the one recheck call: the numbers, and any row
    named as an alternative that no choice rule selects between (the usual cause —
    both OR alternatives then count as compulsory).
    """
    if scheme is None or official_total is None:
        return ""
    try:
        effective = effective_marks_total(scheme)
    except (MarksAttestationError, ChoiceGraphError, TypeError, ValueError) as exc:
        return f"The choice rules could not be applied: {exc}"
    official = float(official_total)
    if abs(effective - official) <= 0.01:
        return ""
    raw = sum(float(item.max) for item in scheme.items)
    grouped = {
        normalize_qid(str(qid or ""))
        for group in list(getattr(scheme, "choice_groups", None) or [])
        for option in list(getattr(group, "options", None) or [])
        for qid in list(getattr(option, "question_ids", None) or [])
    }
    loose = [item.qid for item in scheme.items
             if _ALTERNATIVE_QID_RE.search(str(item.qid or ""))
             and normalize_qid(item.qid) not in grouped]
    rules = len(list(getattr(scheme, "choice_groups", None) or []))
    parts = [
        f"The question rows add up to {raw:g} marks including every alternative; after "
        f"{rules} choice rule{'s' if rules != 1 else ''} one student can score "
        f"{effective:g}, but the paper total is {official:g}."
    ]
    if loose:
        parts.append("These alternatives are not in any choice rule, so both sides are "
                     "being counted: " + ", ".join(loose[:8]) + ".")
    elif effective > official:
        parts.append("An OR alternative may be counted as a compulsory question, or a "
                     "question may carry more marks than its section allows.")
    else:
        parts.append("A question or part may be missing, or carry fewer marks than its "
                     "section allows.")
    return " ".join(parts)


def marks_rows_agree(first: MarksScheme | None, second: MarksScheme | None) -> bool:
    """Compare browser-visible qid/max rows without trusting browser choice data."""
    if first is None or second is None:
        return False
    try:
        return canonical_marks_signature(first) == canonical_marks_signature(second)
    except (MarksAttestationError, TypeError, ValueError):
        return False


def _is_qid_descendant(qid: str, ancestor: str) -> bool:
    """True only for an explicit qid path boundary (Q1 never owns Q10)."""
    return bool(qid and ancestor and qid.startswith(ancestor + "."))


def _choice_qids(
    signature: tuple[tuple[int, float, tuple[tuple[str, ...], ...]], ...],
) -> set[str]:
    return {
        qid
        for _select_count, _contribution, options in signature
        for option in options
        for qid in option
    }


def _subtree_touches_choice(root: str, choice_qids: set[str]) -> bool:
    """A roll-up may not hide either a choice member or one of its ancestors."""
    return any(
        qid == root
        or _is_qid_descendant(qid, root)
        or _is_qid_descendant(root, qid)
        for qid in choice_qids
    )


def reconcile_marks_schemes(
    first: MarksScheme | None,
    second: MarksScheme | None,
) -> MarksScheme | None:
    """Return only the qid/max graph independently supported by both providers.

    Exact agreement is preferred.  The sole tolerated structural difference is a
    lossless one-to-many hierarchy roll-up, for example ``Q16 = 6`` versus
    ``Q16.i/Q16.ii/Q16.iii = 2``.  In that case the returned authority is the *common
    coarsened* row ``Q16 = 6``.  The finer split is never attested because only one
    provider supplied it.

    Roll-ups are deliberately narrow and fail closed: every non-matching row must be
    consumed by a disjoint explicit-dot descendant group; the sums must be exact;
    neither input may mix a scorable parent and its descendants; and no affected
    subtree may participate in internal choice.  Choice semantics elsewhere must
    still agree exactly.
    """
    if first is None or second is None:
        return None
    # Alternatives the paper leaves unlabelled are matched by structure first
    # (_alternative_alignment); the literal comparison below then decides.
    second = _aligned_to(first, second)
    try:
        first_signature = canonical_marks_signature(first)
        second_signature = canonical_marks_signature(second)
        first_choices = canonical_choice_signature(first)
        second_choices = canonical_choice_signature(second)
    except (MarksAttestationError, TypeError, ValueError):
        return None

    if first_choices != second_choices:
        return None
    if first_signature == second_signature:
        return MarksScheme.model_validate(first.model_dump())

    first_rows = dict(first_signature)
    second_rows = dict(second_signature)
    first_qids = set(first_rows)
    second_qids = set(second_rows)

    # A source that emits both Q16 and Q16.i is not a leaf graph.  Treating Q16 as an
    # aggregate would silently double count or erase a separately scorable row.
    for qids in (first_qids, second_qids):
        if any(
            _is_qid_descendant(child, parent)
            for parent in qids
            for child in qids
            if parent != child
        ):
            return None

    exact_qids = {
        qid for qid in first_qids & second_qids
        if first_rows[qid] == second_rows[qid]
    }
    unmatched_first = first_qids - exact_qids
    unmatched_second = second_qids - exact_qids
    if not unmatched_first or not unmatched_second:
        return None

    choice_qids = _choice_qids(first_choices)
    consumed_first: set[str] = set()
    consumed_second: set[str] = set()
    # canonical consensus qid -> (maximum, source item)
    consensus_rows: dict[str, tuple[float, MarksItem]] = {}
    first_items = {
        normalize_qid(item.qid): item
        for item in list(getattr(first, "items", None) or [])
    }
    second_items = {
        normalize_qid(item.qid): item
        for item in list(getattr(second, "items", None) or [])
    }
    for qid in exact_qids:
        consensus_rows[qid] = (first_rows[qid], first_items[qid])

    def add_rollup(
        aggregate_qid: str,
        aggregate_rows: dict[str, float],
        aggregate_items: dict[str, MarksItem],
        descendants: set[str],
        aggregate_consumed: set[str],
        descendant_consumed: set[str],
    ) -> bool:
        if len(descendants) < 2:
            return False
        if aggregate_qid in aggregate_consumed or descendants & descendant_consumed:
            return False
        if _subtree_touches_choice(aggregate_qid, choice_qids):
            return False
        descendant_rows = (
            second_rows if aggregate_rows is first_rows else first_rows
        )
        descendant_total = round(sum(descendant_rows[qid] for qid in descendants), 4)
        if abs(aggregate_rows[aggregate_qid] - descendant_total) > 0.01:
            return False
        aggregate_consumed.add(aggregate_qid)
        descendant_consumed.update(descendants)
        consensus_rows[aggregate_qid] = (
            aggregate_rows[aggregate_qid], aggregate_items[aggregate_qid])
        return True

    # A roll-up can appear on either provider.  Same-source nesting was rejected
    # above, so candidate descendant groups cannot overlap unless the graph is invalid.
    for qid in sorted(unmatched_first):
        descendants = {
            other for other in unmatched_second
            if _is_qid_descendant(other, qid)
        }
        if descendants and not add_rollup(
            qid, first_rows, first_items, descendants,
            consumed_first, consumed_second,
        ):
            return None
    for qid in sorted(unmatched_second):
        descendants = {
            other for other in unmatched_first
            if _is_qid_descendant(other, qid)
        }
        if descendants and not add_rollup(
            qid, second_rows, second_items, descendants,
            consumed_second, consumed_first,
        ):
            return None

    if consumed_first != unmatched_first or consumed_second != unmatched_second:
        return None

    # Preserve paper order using the first provider's positions.  A second-provider
    # aggregate inherits the position of its first descendant in the first graph.
    first_order = {
        normalize_qid(item.qid): index
        for index, item in enumerate(list(getattr(first, "items", None) or []))
    }

    def order_key(qid: str) -> tuple[int, str]:
        positions = [
            index for child, index in first_order.items()
            if child == qid or _is_qid_descendant(child, qid)
        ]
        return (min(positions) if positions else len(first_order), qid)

    items = []
    for qid in sorted(consensus_rows, key=order_key):
        maximum, source = consensus_rows[qid]
        values = source.model_dump()
        values["max"] = maximum
        items.append(MarksItem.model_validate(values))
    consensus = MarksScheme(
        total=round(sum(float(item.max) for item in items), 4),
        items=items,
        choice_groups=[
            MarksChoiceGroup.model_validate(group.model_dump())
            for group in list(getattr(first, "choice_groups", None) or [])
        ],
    )
    try:
        if (
            abs(effective_marks_total(consensus) - effective_marks_total(first)) > 0.01
            or abs(effective_marks_total(consensus) - effective_marks_total(second)) > 0.01
        ):
            return None
    except (MarksAttestationError, TypeError, ValueError):
        return None
    return consensus


_QID_BASE_RE = re.compile(r"^(q\d+)")


def _alternative_alignment(first: MarksScheme, second: MarksScheme) -> dict[str, str] | None:
    """How the second reader's question ids map onto the first reader's.

    Two independent readers do not spell question ids the same way, and a name is
    not a mark: the operator's rule (2026-09-17) is that a naming difference never
    blocks a paper. What must agree is the structure. Choice groups correspond
    when their options belong to the same base question, select the same number
    and carry the same marks option by option in paper order; their ids are then
    mapped position by position. The remaining rows that no name matches are
    mapped in paper order when both readers list them with the same marks in the
    same order. The second reader's ids take the first reader's names; a
    difference in marks, in row count or in a choice rule is left for the exact
    comparison, which then fails as before. MEASURED on the Parishram Accountancy
    unit test: Claude Q10 / Q10.OR against Gemini Q10.alt1 / Q10.alt2, four
    times, for one graph.
    """
    try:
        first_rows = dict(canonical_marks_signature(first))
        second_rows = dict(canonical_marks_signature(second))
    except MarksAttestationError:
        return None

    def keyed(scheme, rows):
        out: dict[tuple, list] = {}
        for group in list(getattr(scheme, "choice_groups", None) or []):
            options = []
            bases: set[str] = set()
            for option in list(getattr(group, "options", None) or []):
                qids = [normalize_qid(str(q or "")) for q in list(getattr(option, "question_ids", None) or [])]
                if not qids or any(q not in rows for q in qids):
                    return None
                for q in qids:
                    match = _QID_BASE_RE.match(q)
                    if match:
                        bases.add(match.group(1))
                options.append(qids)
            shape = tuple(tuple(round(rows[q], 4) for q in qids) for qids in options)
            key = (tuple(sorted(bases)), int(getattr(group, "select_count", 0) or 0), shape)
            out.setdefault(key, []).append(options)
        return out

    first_groups = keyed(first, first_rows)
    second_groups = keyed(second, second_rows)
    if first_groups is None or second_groups is None:
        return None
    mapping: dict[str, str] = {}

    def bind(source: str, target: str) -> bool:
        if source == target:
            return True
        if mapping.get(source, target) != target or target in second_rows:
            return False
        mapping[source] = target
        return True

    in_first_options: set[str] = set()
    in_second_options: set[str] = set()
    for key, groups in first_groups.items():
        for options in groups:
            in_first_options.update(q for option in options for q in option)
        others = second_groups.get(key)
        if not others or len(groups) != 1 or len(others) != 1:
            continue
        for first_option, second_option in zip(groups[0], others[0]):
            if len(first_option) != len(second_option):
                break
            for target, source in zip(first_option, second_option):
                if not bind(source, target):
                    return None
    for options in second_groups.values():
        for group_options in options:
            in_second_options.update(q for option in group_options for q in option)

    # Rows outside every choice rule that no name matched: the same marks in the
    # same paper order is the same question under another spelling.
    first_order = [normalize_qid(str(item.qid)) for item in list(getattr(first, "items", None) or [])]
    second_order = [normalize_qid(str(item.qid)) for item in list(getattr(second, "items", None) or [])]
    renamed = set(mapping)
    named_both = set(first_order) & (set(second_order) - renamed)
    first_left = [q for q in first_order if q not in named_both and q not in in_first_options]
    second_left = [q for q in second_order if q not in named_both and q not in in_second_options
                   and q not in renamed]
    if first_left and len(first_left) == len(second_left):
        if all(first_rows[a] == second_rows[b] for a, b in zip(first_left, second_left)):
            for target, source in zip(first_left, second_left):
                if not bind(source, target):
                    return None
    if len(set(mapping.values())) != len(mapping):
        return None
    return mapping


def _aligned_to(first: MarksScheme, second: MarksScheme) -> MarksScheme:
    """The second scheme with its ids renamed to the first's, where the structure aligns."""
    mapping = _alternative_alignment(first, second)
    if not mapping:
        return second
    raw = {normalize_qid(str(item.qid)): str(item.qid)
           for item in list(getattr(first, "items", None) or [])}
    data = second.model_dump()
    for item in data.get("items") or []:
        key = normalize_qid(str(item.get("qid") or ""))
        if key in mapping:
            item["qid"] = raw.get(mapping[key], mapping[key])
    for group in data.get("choice_groups") or []:
        for option in group.get("options") or []:
            option["question_ids"] = [
                raw.get(mapping[normalize_qid(str(q))], mapping[normalize_qid(str(q))])
                if normalize_qid(str(q)) in mapping else q
                for q in option.get("question_ids") or []]
    try:
        return MarksScheme.model_validate(data)
    except Exception:
        return second


def schemes_agree(first: MarksScheme | None, second: MarksScheme | None) -> bool:
    """Whether the providers share an exact or safely coarsened authority graph."""
    return reconcile_marks_schemes(first, second) is not None


def marks_authority_scheme(scheme: MarksScheme) -> MarksScheme:
    """Keep only fields the two-provider proof actually covers.

    Model-written descriptions, question types, and objective answers are useful as a
    preview but are not part of qid/max consensus and must not enter the release package.
    """
    canonical_marks_signature(scheme)
    items = [MarksItem(qid=item.qid, max=float(item.max)) for item in scheme.items]
    by_qid = {normalize_qid(item.qid): item.qid for item in items}
    groups: list[MarksChoiceGroup] = []
    for group_index, (select_count, contribution, options) in enumerate(
            canonical_choice_signature(scheme), start=1):
        groups.append(MarksChoiceGroup(
            group_id=f"choice-{group_index}",
            options=[MarksChoiceOption(
                option_id=f"option-{option_index}",
                question_ids=[by_qid[qid] for qid in qids],
            ) for option_index, qids in enumerate(options, start=1)],
            select_count=select_count,
            contribution_marks=contribution,
        ))
    return MarksScheme(
        total=sum(float(item.max) for item in items),
        items=items,
        choice_groups=groups,
    )


def _paper_hash(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def _context_hash(context: str) -> str:
    value = str(context or "").strip()
    if not value:
        raise MarksAttestationError("The extraction context is required for verification.")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normal_total(value: float) -> float:
    total = float(value)
    if not math.isfinite(total) or total <= 0:
        raise MarksAttestationError("A positive official total is required for verification.")
    return round(total, 4)


@dataclass(frozen=True)
class MarksAttestation:
    token: str
    proof: str
    paper_sha256: str
    official_total: float
    context_sha256: str
    scheme: MarksScheme
    extractors: tuple[str, ...]
    created_at: float
    expires_at: float | None


@dataclass(frozen=True)
class MarksVerificationRetry:
    """A completed first reading, never proof of independent agreement."""
    scheme: MarksScheme
    task_id: str
    method: str
    failure_code: str
    detail: str
    retry_at: float
    expires_at: float


class MarksAttestationStore:
    """Thread-safe bounded in-memory capability store.

    Losing this cache on restart is intentional: the user can rerun detection, while a
    stale or copied client claim can never turn itself into server authority.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        max_entries: int = 128,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = None if ttl_seconds is None else float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[str, MarksAttestation] = {}
        self._by_paper: dict[tuple[str, float, str], str] = {}
        self._retries: dict[tuple, MarksVerificationRetry] = {}

    def _retry_key(self, paper_bytes, official_total, context, owner):
        return (_paper_hash(paper_bytes), _normal_total(official_total),
                _context_hash(context), _context_hash(owner))

    def retain_for_retry(self, paper_bytes, official_total, scheme, *, context,
                         owner, task_id, method, failure_code, detail, retry_after=0):
        canonical_marks_signature(scheme)
        if abs(effective_marks_total(scheme) - _normal_total(official_total)) > .01:
            raise MarksAttestationError("An incomplete extraction cannot be retained for retry.")
        key = self._retry_key(paper_bytes, official_total, context, owner)
        now = self._clock()
        record = MarksVerificationRetry(
            scheme=scheme.model_copy(deep=True), task_id=task_id, method=method,
            failure_code=failure_code, detail=detail,
            retry_at=now + max(0, min(float(retry_after), 600)),
            expires_at=now + min(self.ttl_seconds if self.ttl_seconds is not None else 900, 900),
        )
        with self._lock:
            self._retries[key] = record
            self._purge_locked(now)
        return replace(record, scheme=record.scheme.model_copy(deep=True))

    def find_retry(self, paper_bytes, official_total, *, context, owner):
        key = self._retry_key(paper_bytes, official_total, context, owner)
        with self._lock:
            self._purge_locked(self._clock())
            record = self._retries.get(key)
            return replace(record, scheme=record.scheme.model_copy(deep=True)) if record else None

    def clear_retry(self, paper_bytes, official_total, *, context, owner):
        key = self._retry_key(paper_bytes, official_total, context, owner)
        with self._lock:
            self._retries.pop(key, None)

    def retry_delay(self, record):
        return max(0, math.ceil(record.retry_at - self._clock()))

    @staticmethod
    def _copy(record: MarksAttestation) -> MarksAttestation:
        return MarksAttestation(
            token=record.token,
            proof=record.proof,
            paper_sha256=record.paper_sha256,
            official_total=record.official_total,
            context_sha256=record.context_sha256,
            scheme=MarksScheme.model_validate(record.scheme.model_dump()),
            extractors=record.extractors,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    def _purge_locked(self, now: float) -> None:
        for key in [key for key, item in self._retries.items() if item.expires_at <= now]:
            self._retries.pop(key, None)
        overflow = len(self._retries) - self.max_entries
        for key in sorted(self._retries, key=lambda key: self._retries[key].expires_at)[:max(0, overflow)]:
            self._retries.pop(key, None)
        expired = [token for token, record in self._records.items()
                   if record.expires_at is not None and record.expires_at <= now]
        for token in expired:
            record = self._records.pop(token)
            key = (record.paper_sha256, record.official_total, record.context_sha256)
            if self._by_paper.get(key) == token:
                self._by_paper.pop(key, None)
        overflow = len(self._records) - self.max_entries
        if overflow > 0:
            oldest = sorted(self._records.values(), key=lambda item: item.created_at)
            for record in oldest[:overflow]:
                self._records.pop(record.token, None)
                key = (record.paper_sha256, record.official_total, record.context_sha256)
                if self._by_paper.get(key) == record.token:
                    self._by_paper.pop(key, None)

    def create(
        self,
        paper_bytes: bytes,
        official_total: float,
        scheme: MarksScheme,
        verifier_scheme: MarksScheme | None = None,
        *,
        extractors: tuple[str, ...],
        context: str,
    ) -> MarksAttestation:
        """Issue the proof for a checked graph.

        The app reads the marks ONCE (one Claude model), so ``verifier_scheme`` is
        normally None and the reading is reconciled with itself, which applies the same
        canonicalisation as a two-reader agreement. A second reading, when one is given,
        must still agree and must come from a different model.
        """
        canonical_marks_signature(scheme)
        consensus = reconcile_marks_schemes(
            scheme, verifier_scheme if verifier_scheme is not None else scheme)
        if consensus is None:
            raise MarksAttestationError(
                "The two providers did not return the same complete question/choice graph.")
        paper_sha256 = _paper_hash(paper_bytes)
        total = _normal_total(official_total)
        if abs(effective_marks_total(consensus) - total) > 0.01:
            raise MarksAttestationError(
                "The verified fixed questions and choice rules do not equal the official total.")
        context_sha256 = _context_hash(context)
        provider_pair = tuple(str(value or "").strip() for value in extractors)
        expected = 1 if verifier_scheme is None else 2
        if (len(provider_pair) != expected or not all(provider_pair)
                or len({value.casefold() for value in provider_pair}) != expected):
            raise MarksAttestationError(
                "Name the model that read the marks (and a different second model "
                "when a second reading is supplied).")
        authority = marks_authority_scheme(consensus)
        proof = issue_question_graph_proof(
            paper_sha256,
            total,
            list(getattr(authority, "items", None) or []),
            list(getattr(authority, "choice_groups", None) or []),
        )
        now = self._clock()
        record = MarksAttestation(
            token=secrets.token_urlsafe(32),
            proof=proof,
            paper_sha256=paper_sha256,
            official_total=total,
            context_sha256=context_sha256,
            # Preserve display-only fields from the agreed/coarsened graph, but replace
            # arbitrary group/option labels with the server's canonical graph.
            scheme=MarksScheme.model_validate({
                **consensus.model_dump(),
                "choice_groups": [
                    group.model_dump() for group in authority.choice_groups
                ],
            }),
            extractors=provider_pair,
            created_at=now,
            expires_at=None if self.ttl_seconds is None else now + self.ttl_seconds,
        )
        with self._lock:
            self._purge_locked(now)
            cache_key = (paper_sha256, total, context_sha256)
            self._records[record.token] = record
            self._by_paper[cache_key] = record.token
            self._purge_locked(now)
        return self._copy(record)

    def find(
        self,
        paper_bytes: bytes,
        official_total: float,
        *,
        context: str,
    ) -> MarksAttestation | None:
        key = (
            _paper_hash(paper_bytes),
            _normal_total(official_total),
            _context_hash(context),
        )
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            token = self._by_paper.get(key)
            record = self._records.get(token or "")
            return self._copy(record) if record else None

    def is_available(self, token: str) -> bool:
        """Check cache presence, never the paper or submitted marks' authority."""
        with self._lock:
            self._purge_locked(self._clock())
            return str(token or "").strip() in self._records

    def resolve(
        self,
        token: str,
        paper_bytes: bytes,
        official_total: float,
        submitted_scheme: MarksScheme | None,
        *,
        context: str,
    ) -> MarksAttestation:
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            record = self._records.get(str(token or "").strip())
            if record is None:
                raise MarksAttestationError(
                    "The verified marks proof is missing or expired; detect marks again.")
            if record.paper_sha256 != _paper_hash(paper_bytes):
                raise MarksAttestationError(
                    "The verified marks proof belongs to a different question paper.")
            if record.official_total != _normal_total(official_total):
                raise MarksAttestationError(
                    "The official total changed after marks verification; detect marks again.")
            if record.context_sha256 != _context_hash(context):
                raise MarksAttestationError(
                    "The exam context changed after marks verification; detect marks again.")
            if not marks_rows_agree(record.scheme, submitted_scheme):
                raise MarksAttestationError(
                    "The question/maxima rows changed after verification; detect marks again.")
            return self._copy(record)
