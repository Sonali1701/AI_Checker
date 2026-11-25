# Giving a Model Answer the portal has not got

The autopilot fetches each test's question paper and Model Answer from the portal by test id.
Some tests have no Model Answer there. Until 19 Nov 2026 such a test was skipped outright with
"the portal has no Model Answer for this test, and the checker needs one": its copies dropped out
of the sheet run, and the only way to check them was to do that test by hand in steps 1 to 3.

Now the test waits instead, and a person gives the file on the page.

## What happens

1. The pair fetches the documents. The question paper is there, the Model Answer is not.
2. The test's status becomes `key_missing` and the pair goes on to its next test. Nothing is
   paid for: the marks check runs only once a key is in hand.
3. The page shows a **Tests waiting for a Model Answer** card, one row per test, with the
   portal's own link to the question paper and a file box.
4. You choose the Model Answer PDF and press **Use this Model Answer**. Every tab's entry of
   that test takes the same file (one paper, one Model Answer), the test goes back to `waiting`,
   and the next free pair picks it up. A finished autopilot starts its pairs again for it, exactly
   as entered marks do.
5. From there the test runs as any other: the marks are checked, then the copies.

## Where the file lives

`workflow_data/autopilot_keys/<test id>-<first 8 of its sha1>.pdf`, beside `autopilot.json`, so
it survives an app restart and two tabs of one test share it. If the file is gone when the test
runs (a Temp cleanup, or the data folder moved), the test returns to the waiting list with
"the Model Answer given earlier cannot be read any more" and asks for it again.

A file given by hand takes priority over one the portal has, matching the rule in step 1 that an
uploaded file always beats a fetched one. The file must be a readable PDF of at most 50 MB.

## Where it is in the code

| Piece | Where |
|---|---|
| Waiting instead of skipping | `Autopilot.prepare` in `workflow_autopilot.py` |
| Taking the file, every tab of the test | `Autopilot.supply_answer_key` |
| Reading it back, and noticing it has gone | `Autopilot.given_answer_key`, `save_answer_key`, `keys_path` |
| The route | `POST /api/workflow/autopilot/answer-key` in `integrated_workflow.py` |
| The card | `apKeys` in `static/workflow.html`, `showKeyProblems` in `static/workflow.js` |
| Tests | `tests/test_autopilot_model_answer_1119.py` (19) |

## In the EXE

Build 172 (`2026.11.19.172-cover-links`, 19 Nov 2026, built by ai-checker-f7) ships this step in
both the client and the workflow EXE. Read back out of the EXEs themselves: `workflow_autopilot`
(sha256 aecf51cd34a3c34d..., carrying `supply_answer_key`, `given_answer_key`, `save_answer_key`,
`keys_path`, `pdf_page_count`, `key_missing`, `autopilot_keys`), `integrated_workflow`
(b8b69dce6ae23fa9..., with the `autopilot/answer-key` route), `static/workflow.html`
(2620137b9f68b0f0..., with `apKeys`) and `static/workflow.js` (90a0fabd7385d712..., with
`showKeyProblems`). Both EXEs carry identical copies. Build 172: client sha256
bfadb508777bd2af..., workflow d3c44b5d0e713d47..., PW Skills 46e900f29cc73293..., and the
unsuffixed `dist/AICheckerClient.exe` and `dist/AICheckerWorkflow.exe` are byte-identical copies.
Selftest 237 of 237 suites, run on the final tree by me and again by the builder.

## Not yet done

This has not been run against the live portal. The tests use the fixture portal, so the shape of
a real "no Model Answer" fetch is covered only as far as `prepare_exam_documents` reports it.
