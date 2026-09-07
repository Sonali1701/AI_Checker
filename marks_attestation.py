"""Server-owned proof for independently verified question/maxima graphs.

Browser rows are editable and therefore cannot be grading authority.  This module
keeps a short-lived, opaque capability that binds a two-provider consensus graph to
the exact question-paper bytes and official total.  The browser may carry the token,
but it can neither create nor alter the graph behind it.
"""
from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

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
    grouped_qids: set[str] = set()
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
        option_totals: list[float] = []
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
            overlap = grouped_qids.intersection(qids)
            if overlap:
                raise MarksAttestationError(
                    "A question cannot belong to more than one choice option.")
            grouped_qids.update(qids)
            options.append(qids)
            option_totals.append(round(sum(rows[qid] for qid in qids), 4))
        if len(options) < 2 or len(set(options)) != len(options):
            raise MarksAttestationError(
                "A choice rule needs at least two distinct alternatives.")
        if select_count < 1 or select_count >= len(options):
            raise MarksAttestationError(
                "A choice rule has an invalid number of selectable alternatives.")
        if not math.isfinite(contribution) or contribution <= 0:
            raise MarksAttestationError("A choice rule has an invalid contribution.")
        if any(abs(total - option_totals[0]) > 0.01 for total in option_totals[1:]):
            raise MarksAttestationError(
                "All alternatives in one choice rule must carry equal marks.")
        expected = round(option_totals[0] * select_count, 4)
        if abs(contribution - expected) > 0.01:
            raise MarksAttestationError(
                "A choice rule contribution does not match its alternatives.")
        signature = (select_count, contribution, tuple(sorted(options)))
        if signature in seen_groups:
            raise MarksAttestationError("The choice graph contains a duplicate rule.")
        seen_groups.add(signature)
        result.append(signature)
    return tuple(sorted(result))


def effective_marks_total(scheme: MarksScheme) -> float:
    """Maximum one student can score after applying every choice rule."""
    rows = dict(canonical_marks_signature(scheme))
    choices = canonical_choice_signature(scheme)
    grouped = {
        qid
        for _select_count, _contribution, options in choices
        for option in options
        for qid in option
    }
    fixed = sum(maximum for qid, maximum in rows.items() if qid not in grouped)
    return round(fixed + sum(group[1] for group in choices), 4)


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
    extractors: tuple[str, str]
    created_at: float
    expires_at: float


class MarksAttestationStore:
    """Thread-safe bounded in-memory capability store.

    Losing this cache on restart is intentional: the user can rerun detection, while a
    stale or copied client claim can never turn itself into server authority.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 2 * 60 * 60,
        max_entries: int = 128,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[str, MarksAttestation] = {}
        self._by_paper: dict[tuple[str, float, str], str] = {}

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
        expired = [token for token, record in self._records.items()
                   if record.expires_at <= now]
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
        verifier_scheme: MarksScheme,
        *,
        extractors: tuple[str, str],
        context: str,
    ) -> MarksAttestation:
        canonical_marks_signature(scheme)
        consensus = reconcile_marks_schemes(scheme, verifier_scheme)
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
        provider_families = tuple(
            value.split(":", 1)[0].strip().casefold() for value in provider_pair)
        if (len(provider_pair) != 2 or not all(provider_pair)
                or len(provider_families) != 2
                or not all(provider_families)
                or provider_families[0] == provider_families[1]):
            raise MarksAttestationError(
                "Two different provider families are required for marks verification.")
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
            extractors=(provider_pair[0], provider_pair[1]),
            created_at=now,
            expires_at=now + self.ttl_seconds,
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
