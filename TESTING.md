# Verifying the grader

> Production note: `/api/evaluate` now uses the strict Exam Package v2 path described
> in `docs/AUTOMATIC_GRADING_ARCHITECTURE.md`. Several older suites intentionally lock
> down legacy repair/rescale behavior used by archived comparison tools; strict jobs
> reject those conditions instead of repairing them.

Two levels: **offline suites** that must pass before every build, and an
**accuracy run** against human-marked booklets that needs a signed-in token.

## 1. Offline — always, no token, no network

```bash
python tools/selftest.py
```

Runs everything in `tests/`. Each suite reproduces a defect that actually reached a
teacher's screen, so a green run means those cannot come back silently:

- `test_automatic_architecture.py` — immutable package, strict compilation, internal
  choice, terminal workflow, and fail-closed release.
- `test_answer_map.py` — unresolved OCR mapping never becomes an assumed blank.
- `test_budget_ledger.py` — ₹15/₹20 policy, mixed-model accounting, and protected
  verification reserves.
- `test_job_store.py` — restart durability without ambiguous provider-call replay.
- `test_selective_verification.py` — deterministic mandatory-risk selection.

| Suite | What it locks down |
|---|---|
| `test_marks_enforcement.py` | The official paper total is enforced, not just warned about: an authoritative scheme forces per-question maxima, a repair pass re-allocates when they still disagree, and a deterministic rescale guarantees the denominator by construction. Reproduces the 58.5/64.5-on-a-50-mark-paper report. |
| `test_moderation.py` | The second-examiner pass restores marks for deductions it can disprove, never deducts further, never touches objective (MCQ) items or forced maxima, and caps every restore at the original deduction. |
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
