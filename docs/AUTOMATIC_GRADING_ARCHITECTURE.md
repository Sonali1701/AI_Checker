# Automatic grading architecture (Exam Package v2)

The production `/api/evaluate` path is now strict, package-bound, fully automatic,
and fail-closed. It does not silently repair a doubtful paper into a plausible score.
Legacy recovery helpers remain for archived evaluation tools and regression comparison,
but the EXE does not opt into them.

## Release flow

```text
official paper + official key + marks/choice graph
                     |
             immutable ExamPackage hash
                     |
page normalization -> Mathpix OCR -> deterministic answer map
                     |
       bounded Haiku question-by-question grading
                     |
 deterministic checks + risk-ranked Sonnet verification
                     |
 strict graph compiler -> PDF render -> preflight -> release gate
                                               |             |
                                            RELEASED     UNGRADABLE
```

Every state change is legal only through `AutomaticWorkflow`. `RELEASED`,
`UNGRADABLE`, and `FAILED` are terminal. A crash during an ambiguous provider call is
persisted as failed and is not replayed automatically, because replay could bill the
same call twice.

## Exam package contract

An automatically releasable package requires:

- the exact official question paper and an uploaded official answer key;
- positive official total marks;
- level/class and subject;
- a question/maxima graph either deterministically reproduced from the uploaded paper
  or read once by Claude and checked against the official total and printed tags;
- explicit, independently verified choice groups for “OR” / “answer any” papers;
  the app applies the server-owned first-attempted policy when a student exceeds the
  allowed number of alternatives.

Paper name/code, attempt/session, and applicable-law date are optional descriptive
metadata, not user-input requirements or release gates. The immutable hashes of the
uploaded official paper and answer key bind the exact assessment and its applicable
answer version. Older API clients and prebuilt packages may still supply those labels;
when supplied they are retained, and when absent they are omitted.

The canonical package is SHA-256 hashed. Provider question IDs, maxima, criterion
totals, choice contribution, and final denominator must compile back to that graph.
Unknown, duplicate, omitted, or rescaled questions end as `UNGRADABLE`.

Generated rubrics remain useful drafts, but are never treated as official release
material. A scanned official key without a usable text layer is also rejected before
student grading; upload a digital/versioned marking scheme instead.

Plain browser-entered marks or choice rows are not authoritative. When deterministic
extraction is unavailable—or the paper contains structural choice—the explicit
verification action runs ONE Claude reading under one PW `task_id` (no second reader
since 2026-09-18). It returns every offered scorable row and the complete choice
membership/count graph, which must reproduce the official total exactly. Each option must reference known,
qids and carry the same effective maximum. Alternatives within a group cannot
overlap. A child choice may sit wholly inside one parent option, whose qid list
includes every child alternative. The child's effective contribution replaces its
raw leaves when calculating that parent option. Crossing or partial overlaps remain
invalid. Fixed questions plus root choice contributions must exactly equal the total
printed in the paper; nested contributions are never added a second time.

Each provider supplies only printed leaf maxima, choice membership, and selection
counts. Code derives the raw sum and every choice contribution, resolving inner
choices before outer choices. Provider-supplied redundant totals are ignored at this
extraction boundary. Membership and maxima must still independently agree; persisted
schemes and packages still reject contributions that differ from code's calculation.
For MTS-04 Audit, the 118 available marks count as 30 MCQ + 14 compulsory +
4 x 14 optional = 100; Q6's internal 4-mark OR is counted once inside its 14.

The server canonicalizes the matched graph, attaches the fixed first-attempted policy,
and issues a short-lived opaque capability bound to the PDF hash, official total,
normalized exam context, extractor pair, rows, and choice rules. Evaluation recovers
choice authority only from that capability and embeds a process-signed proof in the
immutable package; the browser never supplies authoritative choice JSON. Editing a row,
changing context/total/PDF, expiry, or restart invalidates the capability. A normal
sentence containing “either … or” and a professional exam label alone are not treated
as internal choice. Both extractors receive every original page image; scanned,
image and mixed-text question papers can receive the same paper-bound proof when
the full independent graphs agree. A digital text layer is no longer a prerequisite
for question-paper attestation. Official answer-key readability is checked separately.

Source excerpts are navigation aids, not an additional yes/no authority. Models
report specific unresolved allocations; code verifies the rows, choices and totals.
An incomplete, malformed or unresolved extraction gets one automatic visual reread
using the general schema and the original pages, without the excerpt registry or
its fixed row roster. This runs in the same provider task and uses no other reader's
answers. Unresolved errors and semantic disagreements cannot issue a proof; rate or
access errors do not trigger paid recovery. The main button can verify then evaluate
in one action, but it never queues grading without a valid proof or deterministic
scheme. File/context changes during verification cancel that pending evaluation.

## Human-like checks without a human reviewer

The system separates jobs that a teacher performs mentally:

1. OCR reads every student page and preserves line/page anchors.
2. The answer map locates written question labels against the official graph. Missing
   labels mean *unresolved*, never blank.
3. The primary grader applies criteria and records evidence/anchors.
4. Deterministic code checks package-declared objective marks from anchored page
   evidence, totals, duplicates, false blanks,
   criteria arithmetic, remarks, render placement, and paper fidelity.
5. A different model verifies every policy-selected risk and must return exactly one
   valid verdict for every requested decision: suspected false blanks,
   unresolved blanks, high-value full/zero awards, missing criteria/evidence, and
   partial-credit judgments.
6. One release decision checks package validity, exact verification coverage, PDF
   preflight, and any enabled monetary ceiling.

This improves reliability but does not make AI infallible. The product guarantee is:
**no known-invalid, unverified, or structurally ambiguous result is
released.** It is not a promise that every released subjective judgment equals every
human examiner's judgment. Accuracy still requires blinded benchmarking by subject,
level, attempt, and paper type before broad rollout.

## Cost policy

The operator temporarily disabled the desktop grading budget cap on 2026-09-09.
`server.ENFORCE_GRADING_BUDGET=False` propagates `enforce_budget=False` into the
grading worker. Monetary limits cannot stop primary grading, question completion,
objective verification, evidence repairs, or release of a verified PDF. All
policy-selected risks remain mandatory and provider calls/output sizes stay bounded.

Cost accounting remains active, using the actual returned usage and model-specific
rates, including Mathpix pages. Results report `cap_inr=null`, `enforced=false`, and
the recorded cost; missing billing data is shown as unavailable, not as a zero bill.
The app footer shows "budget cap off". Internal unlimited planning values are never
serialized as JSON Infinity. A reservation quote is an estimate before dispatch,
not evidence of a charge; OCR/setup may already have run before a primary quote.

The reference caps below remain available to explicitly capped diagnostic runs:

| Copy | Reference ceiling (inactive in the desktop app) |
|---|---:|
| School/non-commerce, any marks | ₹15 |
| CA/Commerce, up to 50 marks | ₹15 |
| CA/Commerce above 50 marks | ₹20 |

The existing Claude Haiku primary and Sonnet challenger are unchanged. Primary
output is bounded at 15k tokens. When monetary enforcement is explicitly enabled,
conservative input/output quotes protect verification reserves and the final release
check enforces the selected ceiling. With enforcement disabled, cost telemetry cannot
block an otherwise valid result.

Dual-provider question-graph detection is a one-time setup cost per unique paper, not a
student-copy grading call. Both readings and any bounded visual recovery are logged
by the PW proxy under one task ID and the result is cached in-process for two hours.
This setup cost is separate from the displayed student-copy cost.

Re-enabling an app-side ceiling alone would not guarantee a provider's invoice:
a provider/proxy-side atomic spend limit would be needed for a contractual limit.

## EXE and server behavior

- Jobs, workflow snapshots, keys, and PDF artifacts are stored in SQLite under
  `%LOCALAPPDATA%/AI Copy Checker` (or `AICHECKER_DATA_DIR`).
- Completed results survive an EXE restart. In-flight calls are not replayed.
- A held PDF returns HTTP 409 even with the correct job download key.
- Held provisional marks are redacted from the status API and therefore cannot leak
  through the UI as an alternate release channel.
- Batch ZIPs contain only explicitly released copies.
- Uploaded names are sanitized before becoming response headers or ZIP paths.
- Cross-origin access is disabled by default; deployments may explicitly set
  `AICHECKER_ALLOWED_ORIGINS`.

## PW proxy invariants

The FastAPI action checks the PW allowlist before accepting the task. One `task_id` is
created at job start and reused for OCR, primary grading, and verification. Claude,
ChatGPT (second examiner) and Mathpix calls use `pw_access` helpers; Gemini is not
called at all since 2026-09-18; no provider credentials are packaged.
Normal calls rely on proxy raw rows and do not use client-side combined usage logging.
