# Evidence-based grading and annotation

## CA reading order and interrupted extraction

The build-11 log identified the actual 120-mark error: Q5(A)'s separately tagged
"Further..." requirement lost 3 marks, while Q6(B)'s shared 5-mark tag was repeated
on both roman clauses. The next request stopped at Claude's output limit, and the
endpoint replaced that failed reading with an unrelated 54-mark regex preview.

`pdf_text_layer` now identifies real columns from line boundaries on either side
of a gutter. A long paragraph whose centre lies right of the page midpoint is not
a second column. Full-width lines separate reading bands, marks rails stay with
their questions, and mixed-font fragments on the same baseline read left to right.
This also preserves two-column official keys.

For a fully recognized digital Part A/B layout, `paper_structure.py` registers
every scorable allocation from its printed source. It accounts for shared tags,
separately tagged roman parts, and unnumbered "Further..." requirements. It has no
paper hash, filename, fixed question count, or fixed marks table. Unsupported layouts
keep the general extractor. This registry is not release authority.

Both provider adapters receive the same original source and stable allocation IDs,
and independently return maxima and choice membership. A compact schema requires
each registered ID and each choice-rule slot exactly once. The reader may reject
the source boundaries with `structure_ok=false`; missing confirmation, changed
allocations, omissions, malformed output and unfinished generations cannot authorize
grading. Totals are computed from the completed rows. Recognized digital layouts
do not redundantly send the page images for this marks-only reading.

An extraction failure now returns its reason with no replacement table. The UI
preserves the entered official total as awaiting verification. A completed first
reading is still retained when only the independent verifier needs a retry.

`tests/test_ca_extraction.py` replays the logged errors through the real provider
adapters, strict parsers, source checks, consensus and evaluation endpoint, using
simulated provider responses. Its geometry tests cover single-column marks rails,
true columns and mixed-font question labels. These are offline checks, not evidence
that a live provider completed the new request or that CA judgments match humans.

## Nested choices and mixed MCQ allocations

The supplied CA Inter Auditing MTS-04 paper offers 118 marks of alternatives:
30 compulsory MCQ marks, compulsory descriptive Q1 worth 14, Q2-Q6 worth 14
each, and an extra 4-mark alternative inside Q6. Its effective maximum is
30 + 14 + (4 × 14) = 100. The older flat graph could not represent the Q6
OR inside the outer "attempt any four" group.

`choice_graph.py` validates both disjoint groups and complete nesting inside
one parent option. Parent lists retain all leaves; inner contributions replace
their raw alternatives when computing the parent's effective maximum. Both
providers must independently agree on this full graph. Same-group overlaps,
crossing groups, missing inner rules, and unequal effective alternatives remain
blocked. Strict score compilation applies child rules only inside eligible parent
options, uses physical answer order, and never restores an excluded parent's marks.
Best-score rules, when present in an authoritative package, also compare scores
after nested choices; production setup retains its first-attempted policy.

The printed-mark cross-check includes case-scenario and general-MCQ summary
allocations when an explicitly delimited MCQ part has no individual mark tags.
The supplied PDF now reads as 118, rather than omitting its 30 MCQ marks. A
120-mark extraction is still rejected, with its actual mismatch shown in the UI.
Extraction prompts distinguish block totals from scorable questions and describe
the nested-choice representation.

`tests/test_nested_choices.py` covers the paper's transcribed mark structure,
all valid outer/inner combinations, blanks, extra attempts, provider disagreement,
and the detection-to-evaluation API flow. Provider outputs are simulated: these
tests establish counting and verification behavior, not live CA grading accuracy.

Production `/api/evaluate` now keeps the primary provider's raw awards until strict
validation. Legacy reconciliation (rescaling, half-mark rounding and filling missing
rows) is no longer run inside the primary provider call on this path.

## Completing omitted questions

The primary request ends with an explicit checklist of official question IDs and
maxima. Up to eight questions use an object with every official question key
required, sharing a small decision definition. The old array accepted a response
containing only Q1 even when seven questions were requested. Larger primary reports
retain a compact array to avoid expanding the entire paper into the schema grammar.
If the primary omits rows, recovery groups the missing questions into batches of at
most eight, each with required keys. A six-question omission now takes one completion
request. All original student pages and their original numbering are retained; OCR
is reused. The parser rejects an entry whose embedded ID conflicts with its key.

This follows the provider's documented subset: array length constraints beyond
minItems 0/1 are unsupported, while required object properties are supported.
See [Claude structured-output limits](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
The bounded schema avoids optional or union fields in its decision definitions.

Completed primary rows are preserved. A wrong, duplicate or still-missing response
is rejected, and an omitted subpart is never converted to zero just because its
sibling was answered. The full merged report goes through the existing strict
rubric and evidence checks. Recovered subjective judgments require independent
verification even when they award full marks; objective answers retain their
official-key validation.

The complete recovery plan is quoted before the first retry, protecting a whole-copy
verification estimate and the safety reserve. This prevents spending on Q2-Q5 and
only discovering at Q6 that completion cannot fit. Recovery uses concise instructions
with the existing subject-specific marking rules. Output ceilings adapt to the
remaining budget, with a floor based on question and expected step counts. The
original cap is unchanged. All calls retain the task ID and usage ledger; actual
spending is checked before subsequent batches and exact verification before release.
Recovery can still be withheld if its minimum budget or evidence checks fail.

`tests/test_coverage_recovery.py` tests the three-page, seven-question omission using
realistic simulated token usage, including independent verification and a rendered
PDF under Rs.15. A second integrated case rotates the pages 90/180/270 degrees and
runs the real OCR request, grading and audit adapters, parsers, accounting and renderer
with simulated remote responses. Coverage includes duplicate/conflicting IDs,
complete primary reports, multiple batches and refusal before an unaffordable plan.
These are regression tests, not a live result or grading-accuracy measurement.

## Resolving verifier evidence references

The verifier receives each question's allowed evidence IDs in the original page
numbering. Brackets, case and spacing in returned IDs are normalized. Before any
marks change, the app can repair a bad ID from a unique quoted-text match within
that question's active region. It never searches other answers, changes ownership,
or selects among duplicate matches. Cancelled writing and standalone question
headings cannot certify a step. Unresolved references enter the existing bounded
verification repair, with the offending row and its permitted IDs explicitly listed.

OCR presentation wrappers no longer hide question labels, and standalone roman
subpart labels work at the end of a line. A question number followed by actual
answer text is evidence, rather than a heading-only line. Mathematical formatting
normalization preserves comparison operators, numeric boundaries and signs.

Prompts still target 12-word margin remarks. The feedback gate and PDF preflight
share a 16-word hard limit, so a concrete 13-word correction does not require a
paid verification repeat. Longer multi-sentence feedback first uses the existing
local sentence-shortening logic; generic or unresolved long feedback still blocks.

`tests/test_verifier_anchors.py` exercises the Q2 failure pattern through PDF release,
including wrong IDs, wrapped math, a 13-word correction and a bounded repair when
local resolution is impossible. Remote judgments are simulated; this does not
establish a live result for the user's original PDF.

## Retrying question-wise marks verification

The Evaluate button requires either a complete deterministic marks table or a live
server proof of independent agreement. An AI preview, provider disagreement, expired
proof, wrong total or unresolved choice rule does not unlock it. The checklist shows
the missing verification and the provider's actionable failure reason.

If the independent reader is rate-limited, the server keeps the completed first
reading for up to 15 minutes in memory, bound to the exact paper, total, exam context
and signed-in session. The retry button counts down for the cooldown; rapid clicks
do not repeat provider calls. An explicit retry runs only the missing independent
reading under the original task ID. The cached preview cannot authorize grading.
A restart, changed context/session or cache expiry requires fresh extraction.

Run `python tests/test_marks_retry.py` for API recovery checks and
`node --test tests/test_marks_ui.cjs` for the UI gate, cooldown and stale-proof checks.
These checks mock providers; they do not establish that an external rate limit has
cleared.

## Fixed step marking

Setup reads unambiguous allocations such as `Valid method [1 mark]` under a question
heading in the uploaded key. It freezes them only when their sum exactly matches
that question's official maximum. Bare numbers and partial extractions do not become
official step weights. The package hash includes the resulting criteria.

The Answer key panel also accepts an optional reviewed JSON step rubric. Start with
`static/step-rubric-example.json`, replace the question IDs, descriptions and weights
with this paper's actual rules, and upload it alongside the official answer key.
The API equivalent is the `step_rubric` form field. Unknown questions, duplicate
criterion IDs, invalid increments, and totals that disagree with the paper are
rejected before a grading job is queued. An optional `answer_key_sha256` binds a
rubric to an exact key file.

The model must return every fixed criterion ID exactly once. Its maximum cannot be
redistributed to another step. Accepted answers, explicit marking rules and score
increments are supplied to both examiners; moderation preserves quarter marks when
the rubric permits them.

When no fixed rubric can be extracted or supplied, the question is explicitly
selected for independent verification. Its model-proposed allocation is not labeled
an official step scheme. The production compiler rejects inconsistent step totals
instead of trimming maxima to make a plausible breakdown.

## Reading and placing marks

OCR retains line confidence, cancelled lines and detected diagrams/tables. Production
requests `include_line_data` for answer ownership and mark placement; see the
[official image OCR reference](https://docs.mathpix.com/reference/post-v3-text).
It does not combine this with `include_word_data`: the live service rejects that
combination. There is one OCR request per page, without an additional word-data call.
The cache can retain word contours when supplied, but normal line-only OCR has none.
Exact-word underlines are then omitted; production never guesses word coordinates.
Question ticks, step marks and awarded totals continue to use line geometry.

The answer map tracks line ownership and disjoint regions across pages and nested
subparts. Repaired labels and low-confidence lines trigger visual verification.
An unresolved label is never proof of a blank answer.

Step anchors must match a student quote within that answer's region or be explicitly
verified against the original page image. A unique quote repairs a misplaced anchor.
Ambiguous matches remain unresolved. Multiple criteria on one real evidence line
can share a mark; an opaque diagram is never split into imagined evenly spaced rows.
Omitted work gets its score without crossing correct neighboring writing.

The verifier receives the original answer pages with anchor labels. It must explicitly
certify readable evidence and return corrected step locations. Uncertain verdicts
cannot pass as verified unchanged scores. Blank-answer verification searches all pages.
Verification and any repair are quoted against the remaining budget before dispatch.

Rendering produces a PDF-bound drawing record containing question, criterion, page,
evidence line, actual mark rectangle and normalized coordinates. Required step marks
also apply to two-mark answers. The release check verifies required marks, answer
ownership, continuation-page coverage, page bounds, and visible drawing paths.
Single-mark prose answers receive a continuation bracket without duplicating their
score. Layout repair retains step marking in the adjacent margin; it cannot waive
missing marks by falling back to a score-only report.

## Targeted verification repair

Missing OCR headings may incorrectly extend the preceding answer's continuation.
When the primary grader supplies distinct anchors for an otherwise unlocated answer,
the pipeline proposes a bounded region without consuming another answer's known
label or cited primary evidence. Both affected regions are flagged for independent
visual ownership verification. Conflicting anchors remain unresolved; a proposal
alone cannot certify an award.

Verification repair includes uncertain, missing, duplicate and malformed decisions,
as well as missing explanations and misplaced anchors. It requests only affected
rows, preserving their original row numbers in the prompt and response schema.
Complete answer pages and relevant official material accompany those rows; blank
answers or unresolved locations still require all pages. Completed decisions are
preserved, and the combined certificate must pass every original release check.

Primary grading and initial verification protect the configured recovery allowance.
Mandatory missing-question completion can spend that allowance while protecting the
full verification pass. A targeted repair's output ceiling is fitted to the remaining
budget above a per-row minimum; the cap and independent verifier are unchanged.
Repair usage is recorded even when parsing fails. Logs retain target rows, pages,
reasons, spent cost and the reservation so a hold can be diagnosed.

`tests/test_targeted_verification.py` covers missed Q4/Q6 headings, an eleven-row
three-page paper with an uncertain decision, provider request constraints, strict
merging and budget refusal. Provider responses are simulated; these are engineering
regressions, not a live regrade or a measured agreement score against human markers.

## Annotation layout

Correct MCQs carry a tick with a circled awarded mark such as `1` or `2`.
Wrong MCQs and wrong steps have a cross alone, without a circled zero after it;
the numeric zero remains in the margin score or calculation. Partial awards show
a circled mark without a full-credit tick. Question
fractions such as `1/1`, `1/2` and step calculations appear only in the right margin
strip. The header lists awarded marks and uses "out of" for the grand total.

Placement avoids student writing and neighbouring answers; the release check reads
the awarded numeral back from the PDF and verifies the visible circle. A tick plus
a distant summary total cannot satisfy this requirement. A separate margin canvas
preserves circles at the paper edge and places question totals in a right-hand
column attached directly to each original page. There is one score strip, with
no extra gutter or padding to force different scan sizes to share a page width.
MCQ ticks, crosses and awarded numerals are placed to the right of their answers.
The drawing record tracks final page coordinates and the original normalized
evidence coordinates separately. `tests/test_mcq_scores.py` covers multiple page
sizes, two columns, dense rows, missing circles/text and right-margin fractions.
Ink clearance uses local contrast
to distinguish shaded paper from writing, and step numerals are checked again
after their placement is pinned to the evidence row.
The desktop bundle includes NumPy, which the ink checks require; excluding it
previously made the packaged app silently fall back to OCR geometry alone.

## Teacher comparison

`tools/benchmark_quality.py` submits jobs through `/api/evaluate`, including normal
paper verification, and compares its saved production reports with question-level
human marks. It does not use the older `eval_ca.py` legacy grading route.

Create a dataset with a `copies` array. Each copy supplies `id`, `subject`,
`student_class`, `total_marks`, local paths `question_paper`, `answer_key`, `student`,
optional `step_rubric`, and a `questions` array containing `qid`, `score`, `max_score`,
and `attempted`. Use independently marked, adjudicated teacher scores.

Optional `mark_regions` on each question contain `{ "page": 1, "bbox": [x0,y0,x1,y1] }`.
These are acceptable locations for actual ticks/marks, normalized by the original
page width and height. The added right margin has x coordinates greater than 1;
the added report header is excluded. Include these labels to measure placement;
without them `placement_checked` is zero, not evidence of perfect positioning.

```powershell
python tools/benchmark_quality.py dataset.json --results benchmark_results --run --token-file token.txt --server http://127.0.0.1:8765 --repeats 3
python tools/benchmark_quality.py dataset.json --results benchmark_results --repeats 3
```

The first command makes billable production calls; the second compares saved results
offline. Use the port shown by your running desktop client if it differs from 8765.
Existing results are reused. Submitted jobs are never blindly replayed after
timeouts. The summary reports per-question absolute error, exact agreement, bias,
missing/false-blank answers, misplaced marks, repeated-run variation and withheld rate,
including per-subject breakdowns. Thresholds are configurable and are evaluation
targets, not measured accuracy claims.

Offline regression checks cover these contracts with mocked providers and real PDF
rendering. Subject-level grading accuracy and live provider schema compatibility still
need the production benchmark on representative human-marked copies before rollout.
