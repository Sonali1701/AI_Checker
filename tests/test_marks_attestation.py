# -*- coding: utf-8 -*-
"""Dual-provider marks verification and paper-bound proof invariants."""
import asyncio
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import BackgroundTasks, HTTPException, UploadFile

import server
from exam_package import build_exam_package
from grader import MarksChoiceGroup, MarksChoiceOption, MarksItem, MarksScheme
from marks_attestation import (
    MarksAttestationError,
    MarksAttestationStore,
    canonical_choice_signature,
    canonical_marks_signature,
    effective_marks_total,
    marks_authority_scheme,
    marks_rows_agree,
    reconcile_marks_schemes,
    schemes_agree,
)
from pipeline import CLAUDE, GEMINI
import grader
import grader_gemini


FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


def scheme(*rows):
    items = [MarksItem(qid=qid, max=maximum, description=description)
             for qid, maximum, description in rows]
    return MarksScheme(total=sum(item.max for item in items), items=items)


BASE = scheme(("Q1", 1, "first"), ("Q2.a", 2, "second"))
SAME = scheme(("q2(a)", 2, "different description"), ("1", 1, "changed"))
EDITED = scheme(("Q1", 1, "first"), ("Q2.a", 3, "second"))
CHOICE = MarksScheme(
    total=5,
    items=[MarksItem(qid="Q1", max=1), MarksItem(qid="Q2.a", max=2),
           MarksItem(qid="Q2.b", max=2)],
    choice_groups=[MarksChoiceGroup(
        group_id="choice-q2",
        options=[MarksChoiceOption(option_id="a", question_ids=["Q2.a"]),
                 MarksChoiceOption(option_id="b", question_ids=["Q2.b"])],
        select_count=1,
        contribution_marks=2,
    )],
)
CHOICE_SAME = MarksScheme(
    total=5,
    items=[MarksItem(qid="q2(b)", max=2), MarksItem(qid="1", max=1),
           MarksItem(qid="q2.a", max=2)],
    choice_groups=[MarksChoiceGroup(
        group_id="different-model-label",
        options=[MarksChoiceOption(option_id="second", question_ids=["q2.b"]),
                 MarksChoiceOption(option_id="first", question_ids=["Q2(a)"])],
        select_count=1,
        contribution_marks=2,
    )],
)
CHOICE_CHANGED = CHOICE.model_copy(deep=True)
CHOICE_CHANGED.choice_groups[0].select_count = 2
CHOICE_FLAT = CHOICE.model_copy(update={"choice_groups": []})
CONTEXT = "school|class-11|economics|v1"


print("== canonical agreement ==")
check("order, spelling and descriptions do not change the denominator",
      schemes_agree(BASE, SAME), canonical_marks_signature(SAME))
check("a changed maximum is disagreement", not schemes_agree(BASE, EDITED))
check("choice labels/order do not affect scoring consensus",
      schemes_agree(CHOICE, CHOICE_SAME), canonical_choice_signature(CHOICE_SAME))
check("choice count changes are provider disagreement",
      not schemes_agree(CHOICE, CHOICE_CHANGED))
check("choice graph turns raw 5 marks into effective 3",
      effective_marks_total(CHOICE) == 3)
check("browser row comparison does not trust or require browser choice JSON",
      marks_rows_agree(CHOICE, marks_authority_scheme(CHOICE).model_copy(
          update={"choice_groups": []})))

ROLLUP_FINE = scheme(
    ("Q1", 1, "first"),
    ("Q16.i", 2, "part one"),
    ("Q16.ii", 2, "part two"),
    ("Q16.iii", 2, "part three"),
)
ROLLUP_COARSE = scheme(("Q1", 1, "first"), ("Q16", 6, "three parts"))
rollup = reconcile_marks_schemes(ROLLUP_FINE, ROLLUP_COARSE)
reverse_rollup = reconcile_marks_schemes(ROLLUP_COARSE, ROLLUP_FINE)
check("lossless parent/children disagreement produces consensus",
      rollup is not None and schemes_agree(ROLLUP_FINE, ROLLUP_COARSE))
check("roll-up authority is the common coarse graph, never one-provider leaves",
      canonical_marks_signature(rollup) == (("q1", 1.0), ("q16", 6.0)),
      canonical_marks_signature(rollup) if rollup else None)
check("hierarchical consensus is provider-order symmetric",
      reverse_rollup is not None and
      canonical_marks_signature(reverse_rollup) == canonical_marks_signature(rollup))
check("aggregate/children marks mismatch remains disagreement",
      not schemes_agree(ROLLUP_FINE, scheme(("Q1", 1, ""), ("Q16", 5, ""))))
check("a one-child rename is not treated as a roll-up",
      not schemes_agree(scheme(("Q16.i", 6, "")), scheme(("Q16", 6, ""))))
check("qid prefix boundary prevents Q1 from absorbing Q10",
      not schemes_agree(
          scheme(("Q10.i", 3, ""), ("Q10.ii", 3, "")),
          scheme(("Q1", 6, "")),
      ))
check("non-hierarchical equal totals remain disagreement",
      not schemes_agree(
          scheme(("Q16.i", 2, ""), ("Q16.ii", 2, ""), ("Q16.iii", 2, "")),
          scheme(("Q17", 6, "")),
      ))
check("an unexplained row beside a valid roll-up fails closed",
      not schemes_agree(
          scheme(("Q16.i", 2, ""), ("Q16.ii", 2, ""), ("Q16.iii", 2, ""),
                 ("Q17", 1, "")),
          scheme(("Q16", 6, ""), ("Q18", 1, "")),
      ))
check("a source mixing a scorable parent and descendant cannot be reconciled",
      not schemes_agree(
          scheme(("Q16", 6, ""), ("Q16.note", 1, "")),
          scheme(("Q16.i", 2, ""), ("Q16.ii", 2, ""), ("Q16.iii", 2, ""),
                 ("Q16.note", 1, "")),
      ))
nested_fine = scheme(("Q6.ii.a", 2, ""), ("Q6.ii.b", 2, ""))
nested_coarse = scheme(("Q6.ii", 4, ""))
check("an explicit nested hierarchy can be coarsened losslessly",
      canonical_marks_signature(reconcile_marks_schemes(nested_fine, nested_coarse))
      == (("q6.ii", 4.0),))

outside_choice_fine = MarksScheme(
    total=8,
    items=[MarksItem(qid="Q1.a", max=1), MarksItem(qid="Q1.b", max=1),
           MarksItem(qid="Q16.i", max=2), MarksItem(qid="Q16.ii", max=2),
           MarksItem(qid="Q16.iii", max=2)],
    choice_groups=[MarksChoiceGroup(
        group_id="choice-q1",
        options=[MarksChoiceOption(option_id="a", question_ids=["Q1.a"]),
                 MarksChoiceOption(option_id="b", question_ids=["Q1.b"])],
        select_count=1,
        contribution_marks=1,
    )],
)
outside_choice_coarse = MarksScheme(
    total=8,
    items=[MarksItem(qid="Q1.a", max=1), MarksItem(qid="Q1.b", max=1),
           MarksItem(qid="Q16", max=6)],
    choice_groups=[MarksChoiceGroup(
        group_id="different-label",
        options=[MarksChoiceOption(option_id="b", question_ids=["Q1.b"]),
                 MarksChoiceOption(option_id="a", question_ids=["Q1.a"])],
        select_count=1,
        contribution_marks=1,
    )],
)
outside_choice_consensus = reconcile_marks_schemes(
    outside_choice_fine, outside_choice_coarse)
check("matching choice semantics outside the roll-up are preserved",
      outside_choice_consensus is not None and
      effective_marks_total(outside_choice_consensus) == 7 and
      canonical_choice_signature(outside_choice_consensus)
      == canonical_choice_signature(outside_choice_fine))

choice_touched_fine = MarksScheme(
    total=6,
    items=[MarksItem(qid="Q16.a", max=3), MarksItem(qid="Q16.b", max=3)],
    choice_groups=[MarksChoiceGroup(
        group_id="choice-q16",
        options=[MarksChoiceOption(option_id="a", question_ids=["Q16.a"]),
                 MarksChoiceOption(option_id="b", question_ids=["Q16.b"])],
        select_count=1,
        contribution_marks=3,
    )],
)
check("a roll-up that would erase internal-choice semantics is rejected",
      reconcile_marks_schemes(choice_touched_fine, scheme(("Q16", 6, ""))) is None)

commerce_items = [MarksItem(qid=f"Q{n}", max=1) for n in range(1, 12)]
commerce_items += [
    MarksItem(qid=f"Q{n}.{letter}", max=3)
    for n in range(12, 15) for letter in ("a", "b")
]
commerce_items.append(MarksItem(qid="Q15", max=5))
commerce_groups = [MarksChoiceGroup(
    group_id=f"choice-q{n}",
    options=[MarksChoiceOption(option_id=letter,
                               question_ids=[f"Q{n}.{letter}"])
             for letter in ("a", "b")],
    select_count=1,
    contribution_marks=3,
) for n in range(12, 15)]
COMMERCE_25 = MarksScheme(
    total=sum(item.max for item in commerce_items),
    items=commerce_items,
    choice_groups=commerce_groups,
)
check("realistic 25-mark paper keeps all 34 offered marks but scores out of 25",
      COMMERCE_25.total == 34 and effective_marks_total(COMMERCE_25) == 25,
      (COMMERCE_25.total, effective_marks_total(COMMERCE_25)))

for name, broken in (
    ("unknown qid in a choice is rejected", COMMERCE_25.model_copy(deep=True)),
    ("overlapping choice membership is rejected", COMMERCE_25.model_copy(deep=True)),
    ("wrong choice contribution is rejected", COMMERCE_25.model_copy(deep=True)),
):
    if name.startswith("unknown"):
        broken.choice_groups[0].options[0].question_ids = ["Q99"]
    elif name.startswith("overlapping"):
        broken.choice_groups[1].options[0].question_ids = ["Q12.a"]
    else:
        broken.choice_groups[0].contribution_marks = 6
    try:
        effective_marks_total(broken)
        check(name, False)
    except MarksAttestationError:
        check(name, True)
semantic = MarksScheme(total=1, items=[MarksItem(
    qid="Q1", max=1, description="model claim", question_type="objective",
    correct_answer="A")])
authority = marks_authority_scheme(semantic)
check("unverified semantic fields cannot enter the authoritative package",
      authority.items[0].description == "" and
      authority.items[0].question_type == "unknown" and
      authority.items[0].correct_answer == "")
duplicate = scheme(("Q1", 1, ""), ("1", 1, ""))
try:
    canonical_marks_signature(duplicate)
    check("duplicate normalized qids are rejected", False)
except MarksAttestationError:
    check("duplicate normalized qids are rejected", True)


print("== opaque paper-bound attestation ==")
store = MarksAttestationStore(ttl_seconds=60)
record = store.create(
    b"paper-a", 3, BASE, SAME,
    extractors=("claude:claude-sonnet-4-6", "gemini:gemini-2.5-flash"),
    context=CONTEXT,
)
check("exact paper, total and rows resolve",
      schemes_agree(store.resolve(
          record.token, b"paper-a", 3, SAME, context=CONTEXT).scheme, BASE))
check("same paper lookup reuses the verified record",
      store.find(b"paper-a", 3, context=CONTEXT).token == record.token)
for name, paper, total, submitted in (
    ("different PDF is rejected", b"paper-b", 3, SAME),
    ("changed official total is rejected", b"paper-a", 4, SAME),
    ("edited browser rows are rejected", b"paper-a", 3, EDITED),
):
    try:
        store.resolve(record.token, paper, total, submitted, context=CONTEXT)
        check(name, False)
    except MarksAttestationError:
        check(name, True)
try:
    store.resolve("forged", b"paper-a", 3, SAME, context=CONTEXT)
    check("forged token is rejected", False)
except MarksAttestationError:
    check("forged token is rejected", True)
try:
    store.resolve(record.token, b"paper-a", 3, SAME, context="professional|ca")
    check("changed extraction context is rejected", False)
except MarksAttestationError:
    check("changed extraction context is rejected", True)
try:
    store.create(
        b"paper-a", 3, BASE, SAME,
        extractors=("claude:sonnet", "claude:haiku"),
        context=CONTEXT,
    )
    check("two models from one provider are not independent", False)
except MarksAttestationError:
    check("two models from one provider are not independent", True)

rollup_record = store.create(
    b"rollup-paper", 7, ROLLUP_FINE, ROLLUP_COARSE,
    extractors=("claude:sonnet", "gemini:flash"), context=CONTEXT,
)
check("attestation stores and proves only the common coarsened rows",
      canonical_marks_signature(rollup_record.scheme)
      == (("q1", 1.0), ("q16", 6.0)))
check("the coarsened rows returned to the browser resolve",
      store.resolve(
          rollup_record.token, b"rollup-paper", 7, rollup_record.scheme,
          context=CONTEXT,
      ).proof == rollup_record.proof)
try:
    store.resolve(
        rollup_record.token, b"rollup-paper", 7, ROLLUP_FINE, context=CONTEXT)
    check("one-provider fine leaves cannot be submitted under a coarse proof", False)
except MarksAttestationError:
    check("one-provider fine leaves cannot be submitted under a coarse proof", True)

clock = [100.0]
short = MarksAttestationStore(ttl_seconds=2, clock=lambda: clock[0])
expiring = short.create(
    b"paper", 3, BASE, SAME,
    extractors=("claude:sonnet", "gemini:flash"),
    context=CONTEXT,
)
clock[0] = 103.0
try:
    short.resolve(expiring.token, b"paper", 3, BASE, context=CONTEXT)
    check("expired proof is rejected", False)
except MarksAttestationError:
    check("expired proof is rejected", True)


print("== package release provenance ==")
package_record = store.create(
    b"paper", 3, BASE, SAME,
    extractors=("claude:sonnet", "gemini:flash"), context=CONTEXT)
def package(source, proof=""):
    return build_exam_package(
        exam_type="school", student_class="Class 11", subject="Economics",
        level="11", paper="", attempt="", applicable_law_date=None,
        total_marks=3, question_paper=b"paper", answer_key=b"key",
        key_source="upload", marks_scheme=BASE, scheme_source=source,
        question_graph_proof=proof,
    )


check("one-provider AI package remains blocked",
      "unverified-question-graph" in {i.code for i in package("ai").blockers()})
check("verified label without server proof remains blocked",
      "missing-question-graph-proof" in
      {i.code for i in package("ai_verified").blockers()})
check("dual-provider package with proof is eligible",
      not package("ai_verified", package_record.proof).blockers(),
      package("ai_verified", package_record.proof).blockers())
check("an arbitrary non-empty proof cannot establish authority",
      "invalid-question-graph-proof" in
      {i.code for i in package("ai_verified", "not-a-proof").blockers()})

choice_record = store.create(
    b"choice-paper", 3, CHOICE, CHOICE_SAME,
    extractors=("claude:sonnet", "gemini:flash"), context=CONTEXT)
choice_authority = marks_authority_scheme(CHOICE)
choice_groups = server._exam_choice_groups(choice_authority)
choice_package = build_exam_package(
    exam_type="school", student_class="Class 11", subject="Economics",
    level="11", paper="", attempt="", applicable_law_date=None,
    total_marks=3, question_paper=b"choice-paper", answer_key=b"key",
    key_source="upload", marks_scheme=choice_authority,
    scheme_source="ai_verified", question_graph_proof=choice_record.proof,
    choice_groups=choice_groups,
)
check("proof binds and releases the complete choice graph",
      not choice_package.blockers(), choice_package.blockers())
tampered_groups = [group.model_copy(deep=True) for group in choice_groups]
tampered_groups[0].selection_policy = "best_score"
tampered_package = build_exam_package(
    exam_type="school", student_class="Class 11", subject="Economics",
    level="11", paper="", attempt="", applicable_law_date=None,
    total_marks=3, question_paper=b"choice-paper", answer_key=b"key",
    key_source="upload", marks_scheme=choice_authority,
    scheme_source="ai_verified", question_graph_proof=choice_record.proof,
    choice_groups=tampered_groups,
)
check("choice-policy tampering invalidates the graph proof",
      "invalid-question-graph-proof" in
      {issue.code for issue in tampered_package.blockers()})


print("== endpoint uses one task id and two provider families ==")
originals = {
    "require": server._require_allowed,
    "new_task": server.pw_access.new_task_id,
    "pdf_text": server.pdf_text_layer,
    "paper_total": server.paper_total_from_text,
    "text_complete": server._paper_text_layer_complete,
    "paper_choice": server.paper_has_choice,
    "detect": server.detect_marks_scheme,
    "store": server._MARKS_ATTESTATIONS,
    "persist": server._persist_job,
    "jobs": server.JOBS,
}
calls = []
detected = [BASE, BASE]


def fake_detect(data, filename, cfg, student_class=None, subject=None,
                total_marks=None, require_complete=False):
    calls.append((cfg.provider, cfg.model, cfg.task_id))
    index = min(len(calls) - 1, len(detected) - 1)
    return detected[index], "ai"


async def call_endpoint():
    return await server.detect_marks(
        question_paper=UploadFile(file=io.BytesIO(b"%PDF-paper"), filename="paper.pdf"),
        provider="gemini", model="", student_class="Class 11",
        subject="Economics", exam_type="school", total_marks="3", mode="ai",
        authorization="Bearer test",
    )


async def call_evaluate(token, items):
    tasks = BackgroundTasks()
    result = await server.evaluate(
        background_tasks=tasks,
        answer_sheets=[UploadFile(
            file=io.BytesIO(b"%PDF-sheet"), filename="sheet.pdf")],
        question_paper=UploadFile(
            file=io.BytesIO(b"%PDF-paper"), filename="paper.pdf"),
        answer_key=UploadFile(
            file=io.BytesIO(b"%PDF-key"), filename="key.pdf"),
        provider="gemini", model="", student_class="Class 11",
        subject="Economics", exam_type="school", total_marks="3",
        exam_level="Class 11", paper_code="", exam_attempt="",
        applicable_law_date="", key_source="upload",
        marks_items=json.dumps(items), marks_source="ai_verified",
        marks_attestation=token, usd_to_inr="",
        authorization="Bearer test",
    )
    return result, tasks


try:
    server._require_allowed = lambda _authorization: "google-token"
    server.pw_access.new_task_id = lambda _prefix: "one-task"
    server.pdf_text_layer = lambda _data, _name: "Maximum Marks: 3"
    server.paper_total_from_text = lambda _text: 3.0
    server._paper_text_layer_complete = lambda _data, _name: True
    server.paper_has_choice = lambda _text, _exam_type="": False
    server.detect_marks_scheme = fake_detect
    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    server._persist_job = lambda _job_id: None
    server.JOBS = {}
    response = asyncio.run(call_endpoint())
    check("matching providers produce ai_verified", response["method"] == "ai_verified",
          response)
    check("verified response carries an opaque token", bool(response["marks_attestation"]))
    check("two provider calls were made", len(calls) == 2, calls)
    check("provider families are independent",
          {call[0] for call in calls} == {CLAUDE, GEMINI}, calls)
    check("both provider calls share one task_id",
          {call[2] for call in calls} == {"one-task"}, calls)
    started, background = asyncio.run(call_evaluate(
        response["marks_attestation"], response["items"]))
    payload = background.tasks[0].args[0]
    check("evaluate resolves the exact attestation before queueing",
          bool(started["job_id"]) and bool(payload["question_graph_proof"]))
    check("only attested qid/max fields enter the background package",
          all(not row.get("description") and row.get("question_type") == "unknown"
              for row in payload["verified_marks_scheme"]["items"]),
          payload["verified_marks_scheme"])
    tampered = [dict(item) for item in response["items"]]
    tampered[0]["max"] = 2
    try:
        asyncio.run(call_evaluate(response["marks_attestation"], tampered))
        check("evaluate rejects rows edited after verification", False)
    except HTTPException as exc:
        check("evaluate rejects rows edited after verification", exc.status_code == 400)
    calls.clear()
    cached = asyncio.run(call_endpoint())
    check("repeat detection uses the paper cache", cached["method"] == "ai_verified"
          and not calls, calls)

    endpoint_fine = scheme(
        ("Q16.i", 1, "one"), ("Q16.ii", 1, "two"),
        ("Q16.iii", 1, "three"))
    endpoint_coarse = scheme(("Q16", 3, "three parts"))
    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    detected[:] = [endpoint_fine, endpoint_coarse]
    calls.clear()
    rolled_up = asyncio.run(call_endpoint())
    check("endpoint returns ai_verified for a lossless hierarchy roll-up",
          rolled_up["method"] == "ai_verified" and
          bool(rolled_up["marks_attestation"]), rolled_up)
    check("endpoint exposes only the common coarsened row",
          [(item["qid"], item["max"]) for item in rolled_up["items"]]
          == [("Q16", 3.0)], rolled_up["items"])
    rolled_started, rolled_background = asyncio.run(call_evaluate(
        rolled_up["marks_attestation"], rolled_up["items"]))
    rolled_payload = rolled_background.tasks[0].args[0]
    check("evaluation receives the same coarsened proof authority",
          bool(rolled_started["job_id"]) and
          canonical_marks_signature(MarksScheme.model_validate(
              rolled_payload["verified_marks_scheme"])) == (("q16", 3.0),))

    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    server.paper_has_choice = lambda _text, _exam_type="": True
    calls.clear()
    choice = asyncio.run(call_endpoint())
    check("an explicit choice cue cannot receive a row-only attestation",
          choice["method"] == "ai_choice_unverified" and
          not choice["marks_attestation"] and len(calls) == 1, choice)

    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    detected[:] = [CHOICE, CHOICE_SAME]
    calls.clear()
    verified_choice = asyncio.run(call_endpoint())
    check("matching complete choice graphs receive an attestation",
          verified_choice["method"] == "ai_verified" and
          verified_choice["choice_verified"] and
          len(verified_choice["choice_groups"]) == 1 and
          verified_choice["effective_total"] == 3 and len(calls) == 2,
          verified_choice)
    choice_started, choice_background = asyncio.run(call_evaluate(
        verified_choice["marks_attestation"], verified_choice["items"]))
    choice_payload = choice_background.tasks[0].args[0]
    check("evaluate takes choice authority from the server attestation",
          bool(choice_started["job_id"]) and choice_payload["has_choice"] and
          len(choice_payload["choice_groups"]) == 1,
          choice_payload.get("choice_groups"))

    server._MARKS_ATTESTATIONS = MarksAttestationStore()
    detected[:] = [CHOICE, CHOICE_FLAT]
    calls.clear()
    disagreement = asyncio.run(call_endpoint())
    check("row-equal but choice-different providers cannot unlock evaluation",
          disagreement["method"] == "ai_disagreement" and
          not disagreement["marks_attestation"], disagreement)
finally:
    server._require_allowed = originals["require"]
    server.pw_access.new_task_id = originals["new_task"]
    server.pdf_text_layer = originals["pdf_text"]
    server.paper_total_from_text = originals["paper_total"]
    server._paper_text_layer_complete = originals["text_complete"]
    server.paper_has_choice = originals["paper_choice"]
    server.detect_marks_scheme = originals["detect"]
    server._MARKS_ATTESTATIONS = originals["store"]
    server._persist_job = originals["persist"]
    server.JOBS = originals["jobs"]
    detected[:] = [BASE, BASE]


print("== browser gate ==")
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(root, "static", "app.js"), encoding="utf-8") as handle:
    app_js = handle.read()
check("browser sends the opaque attestation", 'fd.append("marks_attestation"' in app_js)
check("AI verification requires both method and token",
      'state.marksMethod === "ai_verified"' in app_js and
      'Boolean(state.marksAttestation)' in app_js)
check("verified choice is not hard-blocked by the lexical signal",
      '!state.choiceDetected && ["regex", "sections", "ai_verified"]' not in app_js)
check("browser does not submit authoritative choice JSON",
      'fd.append("choice_groups"' not in app_js)
check("row edits erase the attestation", 'state.marksAttestation = ""' in app_js)


print("== truncated provider output cannot be attested ==")
valid_json = '{"total":1,"items":[{"qid":"Q1","max":1}]}'
old_claude_call = grader._claude_call
old_gemini_generate = grader_gemini._generate
try:
    grader._claude_call = lambda *args, **kwargs: {
        "stop_reason": "max_tokens",
        "content": [{"type": "text", "text": valid_json}],
    }
    try:
        grader.extract_marks_scheme(
            [], google_token="token", task_id="task", require_complete=True)
        check("truncated Claude JSON is rejected", False)
    except grader.OutputLimitExceeded:
        check("truncated Claude JSON is rejected", True)

    grader_gemini._generate = lambda *args, **kwargs: {
        "candidates": [{
            "finishReason": "MAX_TOKENS",
            "content": {"parts": [{"text": valid_json}]},
        }]
    }
    try:
        grader_gemini.extract_marks_scheme(
            [], google_token="token", task_id="task", require_complete=True)
        check("truncated Gemini JSON is rejected", False)
    except grader.OutputLimitExceeded:
        check("truncated Gemini JSON is rejected", True)
finally:
    grader._claude_call = old_claude_call
    grader_gemini._generate = old_gemini_generate


if FAILS:
    print(f"\n{len(FAILS)} failure(s): {', '.join(FAILS)}")
    raise SystemExit(1)
print("\nAll marks-attestation tests passed.")
