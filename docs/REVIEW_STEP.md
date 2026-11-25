# Approval Management > Review

Added 12 November 2026. Each pair's **reviewer** account approves the copies that pair uploaded,
with grade **A**. Marks and comments are never changed.

## When it runs

Until 25 November 2026 this happened once, after the autopilot had taken every test, so a copy
uploaded in the first hour was not approved until every other test had ended. At the operator's
request it now runs **as each test's uploads land on the portal**:

| Pass | Where | What it takes |
|---|---|---|
| Per test | `approve_finished` / `review_round`, on the `autopilot-review` thread | A test whose status is `done`, which has copies on the portal and whose pair has a reviewer. One test at a time, in the order they finished. |
| Final sweep | `review_uploaded`, from `watch()` | Whatever the per-test pass could not take: a test that was stopped or failed, one finished by an earlier run of the app, and a count for the pairs that have no reviewer. |

`finish(test, run)` is the moment a test's last upload has landed, so that is what the per-test pass
waits for. It is on its own thread because a pair must never wait for the portal (build 146), and
one review runs at a time so a reviewer's window is never asked for two programs at once.

A test is marked `reviewed` **before** the portal is touched, so no test is reviewed twice, and the
final sweep skips a test the per-test pass has already taken. The tally the page shows
(`state["review"]`) is shared by both passes and is seeded from the saved state, so a restart adds
to what the earlier process approved instead of counting from zero.

The steps per copy come from the supplied Paper Uploader (`review_copies.py`, the instruction video
of 4 November 2026), vendored as `portal_vendor/review_copies.py`:

1. Approval Management > Review > the program > **Pending**, reading the program's id from the URL.
2. The program's Pending list from the site's own backend
   (`tests/tasks/review?type=pending&programId=...`), 100 rows a page.
3. The copy's row: search by Student ID, then by name, keeping the rows of that test and category.
   No row, or more than one, and the copy is reported for a person to review by hand.
4. **View Upload** from the row's menu, then in the pop-up: status **Approved**, Evaluator Grading
   **A**. Both are read back before anything is submitted.
5. Submit, then wait for "Reviewed successfully".

## What is ours, not the supplier's

The supplied tool approves **every** copy in a program's Pending list, whoever evaluated it. A
workflow run approves only the copies **it** uploaded (`stage == "uploaded"`), so another
evaluator's work in the same program is left pending. `pending_copies(..., wanted=None)` still
takes the whole list, for a caller that wants the supplier's behaviour.

A copy whose Submit could not be confirmed raises `SubmissionUnverified` and is never submitted
again; it is reported for a person to check. One copy failing never stops the rest, and one
program failing never stops the next.

## Setting it up

Add a **Reviewer** column to the accounts tab, beside Admin and Evaluator. The pair's password
column is used for the reviewer too, so the window can sign itself in. Without the column nothing
is reviewed and a run behaves exactly as it did before.

The reviewer gets a Chrome window of its own, like any other account.

## Not yet done

The review pass has not been run against the live portal.

The reviewer account needs **Reviewer access** on the portal, which someone grants: without it
Approval Management > Review is not on that account's menu and every copy comes back a failure
rather than approved. The team sheet of 25 November 2026 marks only one address as having it, so
confirm the access before a run with several pairs, and prove it on one pair and one test first. The per-copy steps are the supplier's and
are covered by `tests/test_review_copies_1112.py` against a fake page, but the selectors have not
been seen on the real Review page from inside this app.

## In the EXE

Build 175 (`2026.11.25.175-review-per-test`, 25 Nov 2026) ships the per-test timing and the two
fixes above in all three EXEs: client sha256 52b3df2c4bdb5d3d..., workflow a934e21d097441b6...,
PW Skills cce833cccb3a2f8b..., with the unsuffixed `dist/*.exe` byte-identical copies.
`workflow_autopilot` (81342 bytes) carries `approve_finished`, `review_round`, `review_record`,
`reviewer_for` and `uploaded_students`, and `static/workflow.js` carries the new status sentence
and the reviewer sign-in, read back out of the EXE bytes. Build 174 and earlier review only at the
end of the whole autopilot.

Build 166 (`2026.11.12.166-skills-test-learners`, 12 Nov 2026) first shipped the review step in
both EXEs, and build 167 (`2026.11.12.167-skills-feedback-limit`, same day) carries it unchanged:
`portal_vendor.review_copies` (sha256 e4e502cccfe1b59f...), `workflow_autopilot` (23b2bc86dd06524b...),
`integrated_workflow` (e7be02979a44efb3...), `static/workflow.html` (3f4f22be3d360907...) and
`static/workflow.js` (8f4910403771f8a7...) are byte-identical between the two builds, read back out
of the EXEs themselves. Build 167: client sha256 5f0c3fb5cb39099b..., workflow b2711a6d0013dbd9...,
with the unsuffixed `dist/*.exe` byte-identical copies. The step stays inactive until the accounts
tab has a Reviewer column.
