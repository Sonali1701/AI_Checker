# Verifying the grader

The Model Answer step of 19 Nov 2026 is in `tests/test_autopilot_model_answer_1119.py`: a test the
portal has no Model Answer for waits for a person to give one instead of being skipped. A change to
`Autopilot.prepare` needs that suite and `test_workflow_autopilot.py` together, because the skip it
replaces is asserted there too. `docs/AUTOPILOT_MODEL_ANSWER.md` describes the step.

Build 162 keeps the paper's printed tags from refusing a scheme the paper's own total supports
(`test_tags_against_official_total_1105.py`) and lets a choice rule name a question whose rows are its
parts (`test_operator_choice_parts_1103.py`). A change to either check needs the CA protections in
`tests/test_scheme_fidelity.py` run as well: they are a script, not pytest.

Build 161 passes a copy taken elsewhere (`test_copy_moved_on_1102.py`), keeps runs of earlier Autopilots
out of a restart and within Stop's reach (`test_old_autopilot_runs_1102.py`), and names a wait for
memory (`test_memory_wait_1102.py`). Two timing tests in `test_integrated_workflow.py` can fail in a full
suite run beside a live run on a short-of-memory machine. Re-run them alone before calling them faults.

Build 160 keeps a workflow reading that lands on the official total, in `test_marks_scan_1102.py`. A
change to one marks gate needs a run through the whole of `detect_marks`, as later gates can stop the
same paper again.

Build 159 limits the pages the orientation probe sees, in `test_big_pages_1101.py`, and counts struck
rows at once, in `test_struck_lines_row_count_1101.py`, which keeps the old reading to compare with.
Beside a live run, run the start-up checks alone at normal priority.

Build 157 waits for a lost internet connection, in `test_workflow_copy_retries.py`, and has the
marks-problem list, in `test_marks_problem_links_1031.py` and `tools/smoke_workflow_autopilot.py`.
The double count is in `test_marks_double_count_1031.py`. On a busy PC run the start-up checks on
their own, not beside the suite.

Build 154 skips copies over 100 pages, unread: see `test_workflow_copy_retries.py`, including a copy
downloaded before a restart.

Build 153 unpacks outside Temp: `test_unpack_outside_temp_1031.py` covers the clearing of killed
copies and the retried autopilot save, and `tools/smoke_release.py` refuses an EXE that would unpack
into Temp.

The Autopilot's several student tabs are covered in `test_autopilot_tabs_1031.py`: order, a copy
in two tabs, statuses saved in each test's own tab, and the sheets read once.

Build 151 gives full-mark copies a comment, counts direct refusals, keeps reading a copy direct,
shares a test's paper and key across its copies, runs below normal priority and checks 10 copies
at once with a memory guard. Covered in `test_portal_api_1030.py`. Speed and memory not yet measured live.

Build 150 adds direct portal requests. `test_portal_api_1030.py` covers each step on a fake portal:
the guards, read-back, a retry not sending the PDF twice, the browser taking over, direct
requests turning off after a wrong answer, and a busy portal costing no attempt. The sign-in and
every read were run on the live portal as tab 3's IDs, and on 30 Oct four real copies went through
assign, check and upload (docs/WORKFLOW_TEST_REPORT.md). The Autopilot card itself was not run with it.

Build 149 is about running a test again. `test_workflow_rerun_1030.py` covers a finished check
moving to the new run with no second check, a changed paper, key or total being checked again,
skipped copies picked up, finished ones left out, a run never started, a PDF with no saved marks,
a student's new submission, and the totals. All on offline doubles.

Build 148 has AI read every workflow paper's marks. `test_marks_ai_first_1030.py` covers AI
keeping its finer rows, the paper's own marks taking over when AI fails or gives a question
other marks, a split the paper leaves open decided by the reader but still bound to the
official total, and the autopilot waiting out an AI outage. All on offline doubles.

Build 148 makes the check independent of assignment and upload.
`test_workflow_check_first_1030.py` covers a portal hanging on an assignment, a refused
evaluator stopping only the portal lanes and Resume not checking again, an already
evaluated copy found before and after its check, a lost assignment confirmation after
the check, rejections assigned before they are posted, older saved runs, and a checked
copy staying in its run. All on the offline doubles: not yet run on the live portal.

Build 147 decides every copy without faculty holds, caches the shared part of the
grading prompt across copies of a test, and probes page orientation eight pages at
a time. `test_no_faculty_gates_1029.py` covers junk rejected only when the
page-image look agrees with the text check, genuine copies graded, and the
whole-test wrong-paper guard. `test_prompt_cache_1029.py` checks that two copies of
a test send the same cached prefix with the booklet notes after it, that copies
sent together pay one cache write, and the workflow's 5-minute TTL.
`test_orientation_batches_1029.py` covers batches of eight, a failed batch and
one-page replies. `python tools/try_sheet_look.py --token-file token.txt --paper
paper.pdf COPY.pdf ...` runs the page-image look live (about Rs 3 a copy); it has
not been run on real pages yet. Providers are mocked in these tests: the cache
saving shows in a live run's cache read tokens.

Build 145 adds shared account cooldowns and safe HTTP/network diagnostics for
portal task-list failures. `test_workflow_fetch_recovery.py` checks queued student
requests, recovery, separate accounts, Retry-After, Stop and the Submit boundary.
`python tools/smoke_workflow_fetch_recovery.py` exercises real headless Chrome
with intercepted HTTP 503/200 replies: eight queued copies make no requests during
cooldown, and a valid program row recovers despite a stale toast. External portal
traffic and paid grading are not used by this test.

Build 144 adds automatic Test Evaluation routing, student-sheet status writeback,
safe retries of failed saved rows, and cleanup of completed copy PDFs plus internal
workflow grading jobs. Focused regressions are in
`test_portal_vendor_collection_choice.py`, `test_workflow_status_writeback.py`,
`test_workflow_sheet_status.py`, `test_workflow_cleanup.py`, and
`test_workflow_job_cleanup.py`. `node --test tests/test_workflow_sheets_ui.cjs`
checks edit consent and terminal-row selection. The connected and sheet-access
browser smokes use offline Google/portal fixtures; they verify writeback and file
removal without touching live students. Windows asyncio/browser checks require
local loopback networking. Completed checked PDFs now return 404 after cleanup;
reports and shared exam documents remain. `smoke_release.py` verifies both new
modules and browser assets against the packaged source.

Build 117 fixes Skills marks verification with its intentionally hidden Class field.
Detection and evaluation bind a blank class to the category's same level label;
explicit classes and all other proof fields remain distinct. Run
`python -m pytest tests/test_skills_marks_context.py -q` for the ten-question,
200-mark assignment flow, proof reuse and rejection of changed contexts, papers,
question rows, totals and expired/forged proofs. `node --test tests/test_marks_ui.cjs`
also exercises one-click Skills verification and evaluation with a blank class.
Providers are mocked in these regressions; they do not measure live grading accuracy.

Build 90 adds optional PDF page ranges and a combined question-paper/answer-key
upload. `tests/test_pdf_page_ranges.py` and `tests/test_pdf_range_routes.py` verify
selection, unchanged blank-range bytes, deterministic proof hashes and existing
route consumers. `python tools/smoke_pdf_ranges_ui.py` checks both interfaces with
offline browser fixtures. `python tools/smoke_release.py <staged-exe>` checks the
packaged helper, optional forms, exact browser assets and frozen worker startup.
This feature does not change grading or release logic.

Build 35 adds `test_verification_ownership.py`: an unnumbered answer blocked by
overlapping primary anchors can be proposed from fresh examiner references. All
affected rows must receive another visual certificate before release; grades are
never copied from the ownership proposal. The checking-plan suite also covers
single prose explanations about transactions and recovery of checking/award IDs
only from unique quotes inside the same answer.

> Production note: `/api/evaluate` now uses the strict Exam Package v2 path described
> in `docs/AUTOMATIC_GRADING_ARCHITECTURE.md`. Several older suites intentionally lock
> down legacy repair/rescale behavior used by archived comparison tools; strict jobs
> reject those conditions instead of repairing them.
>
> As of build 15, the operator has disabled monetary caps for desktop grading.
> Historical cap tests and measurements below describe explicitly capped modes.
> `test_uncapped_grading.py` exercises expensive primary/recovery/verification work,
> PDF release, cost reporting, and continued rejection of unverified marks.

Two levels: **offline suites** that must pass before every build, and an
**accuracy run** against human-marked booklets that needs a signed-in token.

## 1. Offline — always, no token, no network

```bash
python tools/selftest.py
```

Runs everything in `tests/`. Each suite reproduces a defect that actually reached a
teacher's screen, so a green run means those cannot come back silently:

- `test_teacher_layout.py` — the teacher layout (build 41): one tick size per copy from the
  student's row pitch; a tick or cross AND a circled mark beside every answer, to the RIGHT of
  it (after the last line of the point, else past the divider, never on the writing); a
  point anchored on its first line is ticked where it ends; the margin reads "Q7.c 1.5/3"
  with the reason under it and entries never overlap; the question anchor on the last line
  of an answer does not swallow the previous point.
- `test_wording.py` — nothing a student reads carries a semicolon or a dash; remarks are
  plain sentences ("Ans: C" becomes "Correct answer is (C).").
- `test_on_sheet_scores.py` — circled question totals and step marks remain on the
  original answer sheet; the right strip also contains question scores and feedback. Includes dense
  rows, dark borders, false table continuations, missing totals and full/partial/zero awards.

- `test_automatic_architecture.py` — immutable package, strict compilation, internal
  choice, terminal workflow, and fail-closed release.
- `test_integrated_workflow.py`, `test_workflow_portal_adapter.py` — the sheet → assign →
  download → grade → upload workflow (docs/INTEGRATED_WORKFLOW.md): stage order, resume,
  uncertain portal outcomes, rejected copies posted only on request, and the identity guards
  on the vendored portal engine. Use the default temp dir or a short `--basetemp`.
- `test_portal_vendor_*.py` — the Data_Filler supplier's 90 regression tests, run against
  `portal_vendor/` (bundle assignment, evaluator dropdown recovery, rejection upload/comment).
- `test_answer_map.py` — unresolved OCR mapping never becomes an assumed blank.
- `test_budget_ledger.py` — ₹15/₹20 policy, mixed-model accounting, and protected
  verification reserves.
- `test_job_store.py` — restart durability without ambiguous provider-call replay.
- `test_selective_verification.py` — deterministic mandatory-risk selection.
- `test_marks_visual_recovery.py` — boundary rejection and truncated replies recover
  once from original page images; scanned/mixed/image papers can be attested;
  unresolved evidence, provider disagreement and rate limits remain explicit.
- `test_marks_identity.py` — recorded Q5(A) unnumbered/roman label drift and explicit
  nested group references reach verification without another provider call;
  printed numerals, wrong maxima, ambiguous references and changed choices remain distinct.
- `test_question_regrading.py` — all invalid step breakdowns are collected before
  compilation and regraded in bounded batches against the original evidence;
  accepted rows stay intact, fixed rubrics remain immutable, and fresh decisions
  must pass independent verification before PDF release. Includes the 38-row CA graph.
- `test_step_key_layout.py` — dense digital solution PDFs with separate step-mark
  labels retain their images. Primary grading, question regrading and both independent
  verifiers receive the visual marking key; reservations include those images.
  A 28-page scanned-evidence regression checks compression against the 32 MB
  request limit for both verifiers.
- Visual-key regrading also permits one stronger-model pass for still-invalid
  breakdowns. It retains successful repairs, reserves the actual alternate model,
  and leaves the original report untouched if any final breakdown remains invalid.
- The question-regrading suite also checks an “any four” answer with four correct
  points and two wrong surplus attempts: the denominator and earned score stay four.
- `test_choice_answer_recovery.py` — `C2)` labels and a misread digit inside a roman
  list retain the answer region; unsupported surplus-alternative claims require
  re-reading and are never deterministically converted to blank answers.
  It also covers Part-A numeric MCQ keys, restarted Part-B labels, lists within
  descriptive answers, and final choice reports checked against all printed options.
- `test_mcq_scores.py` also checks warning-level ink clearance and separate
  objective/descriptive totals when both parts use the same question numbers.
  MCQ verdicts stay to the right of their own answers, including tight columns.
  The strip attaches directly to each original page with no additional padding;
  the release check rejects a gap between the page and its score strip.
- `test_marks_ui.cjs` — one-click verification followed by evaluation, including
  failed verification, concurrent clicks, stale proofs and changed uploads.

| Suite | What it locks down |
|---|---|
| `test_marks_enforcement.py` | The official paper total is enforced, not just warned about: an authoritative scheme forces per-question maxima, a repair pass re-allocates when they still disagree, and a deterministic rescale guarantees the denominator by construction. Reproduces the 58.5/64.5-on-a-50-mark-paper report. |
| `test_moderation.py` | The second-examiner pass restores marks for deductions it can disprove, never deducts further, never touches objective (MCQ) items or forced maxima, and caps every restore at the original deduction. |
| `test_mark_parity.py` | Every answer comes back marked, in one marker's hand. A cross is drawn the same size as a tick (the two primitives scale differently, so passing the cross a fraction of the tick's size drew it at under half the extent — 25.1pt against 12.8pt on the released Physics copy). A part-marked answer and a step-marked answer whose steps all went unplaced each used to come back with nothing on them at all. And `_fit_beside_marks` no longer slides a mark clear of its neighbour and then clamps it straight back on top. |
| `test_routing_and_jobs.py` | CA/commerce/STEM papers route to `gemini-2.5-pro` — including an assertion that `static/app.js` does **not** pin a model (a hard-coded `gemini-2.5-flash` there silently defeated the routing for every sheet graded in the app). Also: tolerant JSON parsing for all four model replies, job results unreadable without their download key, and job eviction. |

## 2. Accuracy against human evaluators — needs a token

```bash
python tools/eval_ca.py --spread --jobs 4 --n 12 --token-file token.txt --out eval_out
```

Grades booklets from the ground-truth Excel (student PDF + human evaluator PDF +
human marks) and prints AI-vs-human deltas, overall and per subject.

- `--spread` samples round-robin across subjects, so 12 booklets cover Accounting,
  Law, Tax, Costing, FM and Audit instead of 12 of the same paper.
- `--jobs 4` grades four booklets at once (the calls are I/O-bound).
- `--skip K` skips the first K rows of each subject, to avoid re-grading samples
  already measured.
- Writes `results.csv`, each `*_ai.pdf`, its `*_human.pdf`, and `*_report.json`.

Get a token from the signed-in app's browser console:

```js
copy(sessionStorage.getItem("pwGoogleToken"))
```

Tokens last about an hour. Save it to `token.txt` (git-ignored).

## 3. One paper at a time

```bash
python tools/detect_marks_cli.py "paper.pdf" --token-file token.txt \
    --class "CA Foundation" --subject "Accounting" --total 50
```

Prints the per-question marks scheme and how it was obtained (`regex` = free and
corroborated, `ai` = extracted and cross-checked against the printed tags,
`ai_mismatch` = extracted but disagrees, `none` = grading reads the marks itself).
Full traceback on failure — diagnose from this, not from the UI toast.

## 4. Everything at once — one token, one table

```bash
python tools/verify_all.py --token-file token.txt
```

The offline suites, the free marks-scheme extraction, both grading arms on a real
copy, and every claim about the result turned into a check with an observed number
beside it. Ends in a PASS/FAIL table and exits non-zero if anything failed, so it
can gate a build.

It deliberately does not lead with the score. A copy can come back with the right
total and be unusable: the Class 12 Chemistry sheet that started this scored 25/70
with twenty answered sub-parts filed as never attempted, and 25 looked like an
ordinary row. What it checks instead:

| Check | Why it is the bar |
|---|---|
| every question on the paper is in the report | absent is worse than wrong — an absent question is scored **zero** |
| no answered question filed as blank | the 25/70 defect, read off the OCR rather than the report |
| denominator matches the paper | a wrong denominator mis-scores every student on the sheet |
| marks carry rubric steps | a bare score tells the student nothing and leaves every downstream check blind |
| steps are anchored to the page | without a `[PnLk]` there is no line to draw the tick beside |
| gate releases the copy | a held copy is a copy nobody can send |
| inside the budget | Rs.15 at 50 marks or fewer, Rs.20 above (`costs.budget_for`) |
| fan-out costs no more than one call | measured: Rs.5.1 vs Rs.8.2 on the Chemistry copy — the shared prefix is cached |

Useful flags: `--runs 3` grades the same copy three times and reports the spread
(the tool's largest measured error is instability, not judgement); `--skip-single`
drops the control arm; `--human 20.5` adds an accuracy check; `--orient` exercises
the orientation pass instead of assuming a straightened file.

Every run now also writes `ocr.json` beside `report.json`, so a finished run can be
re-checked for free afterwards — coverage and "was that answer really blank?" both
need the page, not the report.

## Page-window grading (fan-out)

Off by default; `AICHECKER_FANOUT=1`, or `--fanout` on the eval tools. It grades a
page window at a time instead of the whole booklet in one call, which is what
produces per-step criteria and anchors at all: the single call on this Chemistry
paper returned 0 criteria and 0 anchors, the fan-out arm returned 22 and 13.

Three things keep it honest, each of which was a real failure first:

1. **The windows know the paper's roster, not just what the OCR read.** `qnums` is
   built from student labels the reader found, so a misread label put a question in
   no window's brief — and every window is told the questions not in its brief
   belong to another call. Nobody marked it. The marks scheme now supplies the real
   roster, and anything it lists that no label placed is offered to every window.
2. **A re-grade pass for anything no window returns.** A question absent from every
   window is scored zero by `_reconcile_report`. The pages that carry it go back in
   one focused call. Bounded: at most 2 extra calls over 4 pages each.
3. **A re-mark pass for marks with no evidence.** A question that lost marks with no
   rubric steps, or steps with no anchor, is asked again — for the reasoning, not for
   a different total: the replacement keeps the original `max_score`, and a reply
   that comes back just as bare is discarded.

Each pass fires only on a real gap, so a copy that was marked properly the first
time pays for none of them.

## Cost policy (measured, 2026-08-21)

Per-copy budget: **Rs.15** at 50 marks or fewer, **Rs.20** above 50. `costs.budget_for()` is the single source of truth — the split used to be written out separately in the eval tools at 60 marks, so a 55-mark paper was measured against a different ceiling than the product used.

This proxy **strips `thinkingConfig`**, so thinking cannot be capped on any model, and
thinking is billed as output. Measured on gemini-2.5-pro: ~9,000 thinking tokens per
2 pages; a 7-page copy produced 25,665 billed output tokens of which only ~1,553 were
the actual report. That is Rs.24 of thinking, and the pro floor is Rs.25.3 even with
no question paper and no audit — over budget however much else is trimmed.

So the bulk marking runs on `_BUDGET_GRADING_MODEL` (flash) and the pro model is spent
where it is small and decisive: `AUDIT_MODEL` moderating a compact text summary of the
marks. Because a cheap grader's failure mode is *over*-awarding, that audit reviews
awarded marks as well as deductions (`audits_awarded_marks`, `include_awards`).

### Measured verdict (human-marked booklets from the CA + Commerce tabs)

Gemini's cost is thinking, which this proxy will not let us cap and which is billed as
output: a 7-page copy on gemini-2.5-pro returned 25,665 output tokens of which only
~1,553 were the report. **Claude never charges that** — extended thinking is opt-in and
we do not request it — so it bills only what it writes.

Commerce, 9 booklets of 25 marks, against the human evaluator:

| model | graded | mean abs delta | cost | over budget |
|---|---|---|---|---|
| **claude-sonnet-4-6** | **9/9** | **2.2** | **Rs.10.8** | **0/9** |
| claude-haiku-4-5 | 7/9 | 1.9 | Rs.4.2 | 0/7 |
| gemini-2.5-flash | 8/9 | 3.9 | Rs.5.1 | 0/8 |
| gemini-2.5-pro | — | ~2 | ~Rs.29.5 | all |

`_BUDGET_GRADING_MODEL = "claude-sonnet-4-6"` — the only model that graded every
booklet without failing, and it fits the budget. Haiku is cheaper and similarly
accurate here but failed rows outright (twice on the same Accountancy booklet, with
"Claude hit the output token limit"), so it is not a candidate.

**Every paper** uses it when the caller names no model. It used to apply only to
"reasoning" subjects, which quietly sent Business Studies — a commerce subject — to a
different model than the Accountancy paper beside it. An explicitly requested model
still wins, and the Gemini path is unchanged for anyone who asks for it.

### The material is worth more than the model

Class 11 Accountancy (human 22/25), same booklet, same model, only the material differs:

| material | AI | error | input tokens | cost |
|---|---|---|---|---|
| none | 19/25 | -3.0 | 27,947 | Rs.14.1 |
| **question paper + solution** | **21.5/25** | **-0.5** | 42,639 | **Rs.17.3** |

The material adds ~14,700 input tokens (+Rs.3.2) and cuts the error six-fold. A fully
supplied 25-mark copy therefore costs about Rs.17 — Rs.2 over the ceiling. Cost is now
**70% input**, dominated by the student page images; the paper and solution travel as
text (see `_prefer_text_over_images`) and are comparatively cheap.

### The material matters more than the model

claude-sonnet-4-6 on CA, split by paper:

| | n | mean abs delta | cost | over budget |
|---|---|---|---|---|
| 50-mark | 4 | 3.8 | Rs.14.5 | 2/4 |
| 100-mark | 7 | **37.6** | Rs.24.2 | 5/7 |

Every 100-mark CA paper **without** its question paper and solution was over-awarded by
+25 to +56 marks (Auditing 85 where the human gave 29; Cost & Management 82 vs 26).
The one 100-mark row that **had** both came in at -6. gemini-2.5-flash shows the same
pattern (+14 to +41.5).

Without the official solution the model cannot tell wrong advanced tax/costing/audit
work from right, and the weakest papers are inflated hardest. **No model swap fixes
this.** For CA, get the question paper and solution into the tool.

Re-run any comparison with:

```bash
python tools/eval_tabs.py --tab CA --prepare                      # offline, no token
python tools/eval_tabs.py --tab CA --token-file token.txt --jobs 3
# A/B another model on the SAME booklets, no Excel needed:
python tools/eval_tabs.py --reuse sweep_ca --tab CA --provider claude     --model claude-haiku-4-5 --token-file token.txt
```

## How marks are kept honest

1. **Denominator** — the paper's own printed total and `(N Marks)` tags bind the
   maxima. A trusted free reading is used when it corroborates; otherwise one AI
   extraction, cross-checked against the tag sum.
2. **Grading** — the extracted scheme is forced per question in code, so the model
   cannot invent a maximum.
3. **Repair** — if the totals still disagree, a text-only call re-allocates from the
   printed tags.
4. **Rescale** — if that fails too, maxima are rescaled proportionally so the
   denominator equals the official total by construction, and the report says so.
5. **Moderation** — every deduction must carry evidence (the student's figure vs what
   was expected; internal only, never printed on the student's copy). A second-examiner
   call re-checks the deductions against the solution text and the OCR transcript and
   restores marks where one is demonstrably wrong. It can only give marks back, and
   upholds anything it cannot verify. Runs for professional and commerce papers.

   Deductions are addressed by the **row number** printed in the prompt (`#0`, `#1`, …),
   not by (qid, criterion): live gemini-2.5-pro replies with a bare array keyed by
   `id`, which a qid-based parser silently dropped — the pass looked like it ran and
   changed nothing.

## Parsing model replies

`response_schema` is **not** reliably enforced on these calls, so the parser is the
contract. All four replies (grade report, marks scheme, repair, audit) go through
`_loads_model_json`, which tolerates code fences, wrapper keys and surrounding prose.
Numbers may arrive as `"7 Marks"`; prose fields may arrive as objects or lists
(`evidence` came back as `{"student_value": …, "expected": …}` and failed a whole
sheet before `_as_text` flattened it). When a new drift shows up, fix it in the parser
and add the exact observed shape to `tests/`.

Diagnose failures from `aichecker.log` (tracebacks via `_log_error`), never by guessing.
