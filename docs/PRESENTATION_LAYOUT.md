# OCR-first presentation policy

The top report strip is unchanged. The original answer canvas contains only
checking ticks/crosses and circled awarded numbers. Question IDs, fractions and
deduction explanations belong in the right margin. Step-marked answers do not
receive another on-sheet total; simple answers have one circled award. Crowded
questions switch completely to one circled question total, retaining row verdicts.

## Responsibilities

- `presentation_layout.py`: pure evidence-location and local candidate policy.
  A unique measured word contour, reliable single OCR line, or unique embedded
  OCR row can supply coordinates. It does not estimate character widths inside
  merged table boxes. Repeated targets, cancelled text and unconfirmed low OCR
  confidence do not yield a location.
- `grading_quality.prepare_evidence`: unresolved locations become mandatory
  verification risks before the existing visual-audit call is scheduled.
- The existing verifier returns the original-page row box and the verdict for
  that entry. Confirmed narrow OCR lines can retain their measured geometry;
  broad regions require a tight visual box. Certificate line/quote signatures
  must still match the current criterion.
- A verifier criterion now contains `checking_points` (all individually assessed
  entries), `award_scope` (entry/section), and a separate optional `award_anchor`
  (the final written line earning that award). A six-transaction criterion cannot
  be certified using one representative transaction. Checking points carry their
  own correct/wrong verdicts; losing section marks never implies crossing every
  entry. Section awards without an unambiguous ending use a single question total.
- Debit and credit entries cannot merge merely because their y-coordinates overlap.
  Identical checked evidence can share one stroke while retaining all coverage IDs.
  Explicit deduction reasons follow their actual error page, not the final page
  of a multi-page answer.
- `pdf_renderer`: resolves evidence, groups criteria assessing the same actual
  row, reserves score positions, then draws checking strokes. A table-format
  award refers to the verified header row's full table width. The grader's
  decisions are never changed by this process.
- `check_render_manifest`: reads actual PDF text and vector strokes, checks
  circles and numerical reconciliation, original-sheet containment, local
  proximity, missing verdicts and forbidden on-sheet labels.
  It independently requires every certified checking point, even if a renderer
  accidentally drops its requirements. Old single-row certificates do not certify
  multi-entry coverage. These are structural checks, not a substitute for comparing
  handwriting and grading decisions with the actual official key.

## Placement and failure handling

Score candidates stay beside the assessed row. Horizontal clearance has a fixed
64-point maximum; there is no whole-page search for blank space. Circle movement
is bounded vertically and all candidates must pass the handwriting/text-core,
annotation-collision and foreign-question-region checks. The score font has an
8-point minimum for crowded steps. Numerals are reserved before verdict strokes
so a tick cannot consume the only available circle slot in the following row.

If a step circle cannot fit but every required evidence row is located, the
renderer discards all reserved step circles for that question before painting.
It places one circled question total after the answer and keeps every row's
tick/cross. The right margin still carries the score and deduction reasons.
Crowded step rows alone do not hold the copy. The explicit fallback is validated
for exactly one visible total, no leftover step awards, and all criterion verdicts.
Unknown evidence locations, missing actual strokes, or absence of any safe
question-total location remain real verification failures, not waived checks.
A heading-only
page does not receive an invented correctness tick or a `continued` label.

## Verification

Run `python tools/selftest.py`. New coverage in
`tests/test_ocr_presentation_layout.py` exercises measured OCR, repeated targets,
low-confidence escalation, foreign-answer collisions, local search bounds,
numeric-only answer annotations, removal of duplicate totals and held layouts.
`tests/test_checking_plan.py` covers multi-entry completeness, section endings,
separate ledger columns, stale/foreign locations, shared strokes and earlier-page
deduction explanations. Verifier output budgets include room for checking points.

`tools/render_presentation_preview.py` replays an existing saved report through
the production renderer without changing its evidence or making paid calls. It
records that this is **not fresh AI verification**, preserves prior evidence
provenance, and writes page previews and release-check failures. A replay that
fails those checks is a diagnostic draft, not an approved evaluated copy.

Check all rendered sample pages and run the full regression suite before building
the executable. Saved-evidence replay does not claim a fresh grading audit.

## Build 34 review corrections

- An owned tight visual box cannot jump to a different page merely because OCR
  recognized the same amount there. Invalid boxes still fail validation.
- Parent tables omitted or incorrectly assigned by OCR are proposed for the
  exclusive owner of their child rows. Mixed-question overlaps and explicit
  foreign evidence prevent reassignment; proposals require visual verification.
- Complete-work award endings and nearby verified final entries take priority
  over padded table borders. They cannot move totals before later checked work.
- Omission explanations use the primary written context without inventing a
  cross. Earlier-page explanations stay there, with a score repeated on the final
  answer page. Same-page follow-up reasons share one independently checked score
  heading; duplicate correction explanations are suppressed.

The authorized accounting regrade is an explicit visual-review fixture outside
the production app (`../pdf_diagnostics/review_accounting_sample.py`). It records
source/key hashes, revised scores, partial-credit judgements and every reviewed
checking location. It is not a fresh paid-provider or EXE grading run, and these
sample-specific coordinates are not bundled in the executable.
