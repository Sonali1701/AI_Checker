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
  or independently matched by the approved Claude and Gemini extractors;
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
verification action runs Claude and Gemini under one PW `task_id`. Both independently
return every offered scorable row and the complete choice membership/count graph.
Provider labels and ordering are ignored; normalized qids, maxima, option membership,
`select_count`, and contribution must match exactly. Each option must reference known,
non-overlapping qids and carry the same maximum, and fixed questions plus verified
choice contributions must exactly equal the total printed in the paper.

The server canonicalizes the matched graph, attaches the fixed first-attempted policy,
and issues a short-lived opaque capability bound to the PDF hash, official total,
normalized exam context, extractor pair, rows, and choice rules. Evaluation recovers
choice authority only from that capability and embeds a process-signed proof in the
immutable package; the browser never supplies authoritative choice JSON. Editing a row,
changing context/total/PDF, expiry, or restart invalidates the capability. A normal
sentence containing “either … or” and a professional exam label alone are not treated
as internal choice. Scanned/mixed-text papers still require a signed institution-issued
package manifest because strict setup requires a complete paper text layer.

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
   preflight, known prices, and the hard cost ceiling.

This improves reliability but does not make AI infallible. The product guarantee is:
**no known-invalid, unverified, structurally ambiguous, or over-budget result is
released.** It is not a promise that every released subjective judgment equals every
human examiner's judgment. Accuracy still requires blinded benchmarking by subject,
level, attempt, and paper type before broad rollout.

## Cost policy

| Copy | Hard provider ceiling |
|---|---:|
| School/non-commerce, any marks | ₹15 |
| CA/Commerce, up to 50 marks | ₹15 |
| CA/Commerce above 50 marks | ₹20 |

Strict jobs use a bounded Claude Haiku primary and reserve money for a targeted Claude
Sonnet challenger. Image/text input is quoted conservatively before dispatch, primary
output is capped (7k tokens up to 50 marks; 15k above 50), and actual provider events
are priced independently by their real models. Mathpix pages are included. A mandatory
risk that cannot fit the remaining budget makes the copy `UNGRADABLE`; known risks are
never silently omitted to fit the price.

Dual-provider question-graph detection is a one-time setup cost per unique paper, not a
student-copy grading call. Its two raw provider rows are logged by the PW proxy under
one task ID and the result is cached in-process for two hours. Per-copy ₹15/₹20 claims
must disclose or amortize this setup cost when only a small batch uses a paper.

The app-side ceiling controls dispatch and release. A provider/proxy-side atomic spend
limit would still be required for a contractual guarantee that an abnormal upstream
billing event can never charge beyond the cap.

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
created at job start and reused for OCR, primary grading, and verification. Gemini,
Claude, and Mathpix calls use `pw_access` helpers; no provider credentials are packaged.
Normal calls rely on proxy raw rows and do not use client-side combined usage logging.
