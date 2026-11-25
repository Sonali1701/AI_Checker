"""Successful proofs survive long runs while partial retry readings stay temporary."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import MarksItem, MarksScheme
from marks_attestation import MarksAttestationError, MarksAttestationStore


PAPER = b"exact-question-paper"
CONTEXT = "school|class-10|maths"


def scheme():
    return MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2", max=2)])


def issue(store, paper=PAPER):
    return store.create(paper, 3, scheme(), extractors=("claude:sonnet",), context=CONTEXT)


def test_successful_proof_has_no_default_expiry_after_more_than_twenty_five_hours():
    clock = [1000.0]
    store = MarksAttestationStore(clock=lambda: clock[0])
    record = issue(store)
    assert record.expires_at is None
    clock[0] += 25 * 60 * 60 + 1
    assert store.is_available(record.token)
    assert store.find(PAPER, 3, context=CONTEXT).token == record.token
    assert store.resolve(record.token, PAPER, 3, scheme(), context=CONTEXT).proof == record.proof


def test_explicit_numeric_expiry_still_removes_proof_and_lookup():
    clock = [1000.0]
    store = MarksAttestationStore(ttl_seconds=60, clock=lambda: clock[0])
    record = issue(store)
    assert record.expires_at == 1060
    clock[0] = 1059
    assert store.is_available(record.token)
    clock[0] = 1060
    assert not store.is_available(record.token)
    assert store.find(PAPER, 3, context=CONTEXT) is None
    with pytest.raises(MarksAttestationError, match="missing or expired"):
        store.resolve(record.token, PAPER, 3, scheme(), context=CONTEXT)


@pytest.mark.parametrize("ttl,expected_lifetime", [(None, 900), (60, 60), (3600, 900)])
def test_partial_retry_reading_remains_short_lived(ttl, expected_lifetime):
    clock = [1000.0]
    store = MarksAttestationStore(ttl_seconds=ttl, clock=lambda: clock[0])
    record = store.retain_for_retry(PAPER, 3, scheme(), context=CONTEXT,
                                   owner="owner", task_id="detect-1", method="ai",
                                   failure_code="verifier_busy", detail="Try again", retry_after=30)
    assert record.expires_at == 1000 + expected_lifetime
    assert store.find_retry(PAPER, 3, context=CONTEXT, owner="owner") is not None
    assert store.find(PAPER, 3, context=CONTEXT) is None
    clock[0] += expected_lifetime
    assert store.find_retry(PAPER, 3, context=CONTEXT, owner="owner") is None


@pytest.mark.parametrize("change,error", [
    ({"paper_bytes": b"other-paper"}, "different question paper"),
    ({"official_total": 4}, "official total changed"),
    ({"context": "school|class-11|maths"}, "exam context changed"),
    ({"submitted_scheme": MarksScheme(total=3, items=[MarksItem(qid="Q1", max=3)])},
     "question/maxima rows changed"),
    ({"token": "forged-token"}, "missing or expired"),
])
def test_nonexpiring_proof_retains_all_bindings_after_long_run(change, error):
    clock = [1000.0]
    store = MarksAttestationStore(clock=lambda: clock[0])
    record = issue(store)
    clock[0] += 25 * 60 * 60
    arguments = dict(token=record.token, paper_bytes=PAPER, official_total=3,
                     submitted_scheme=scheme(), context=CONTEXT)
    arguments.update(change)
    with pytest.raises(MarksAttestationError, match=error):
        store.resolve(**arguments)


def test_nonexpiring_cache_remains_bounded_and_reports_eviction():
    clock = [1000.0]
    store = MarksAttestationStore(max_entries=1, clock=lambda: clock[0])
    first = issue(store)
    clock[0] += 1
    second = issue(store, b"second-paper")
    assert not store.is_available(first.token)
    assert store.find(PAPER, 3, context=CONTEXT) is None
    assert store.is_available(second.token)
    assert store.resolve(second.token, b"second-paper", 3, scheme(), context=CONTEXT).token == second.token
