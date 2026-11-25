# PW Skills copies: fetch, check, post back

Page: `/skills.html` in the desktop app (linked from the copy workflow page).
Code: `skills_portal.py` (portal calls, admin sign-in), `skills_workflow.py` (runs and routes).
Specification: the SME recording of 4 Nov 2026.

## What it does

1. An admin signs in once at `admin-v2.penpencil.co`, in a window the tool opens.
2. The tool lists the batch's "Not Evaluated" submissions, oldest first.
3. For each assignment it reads the lesson and downloads the question paper and solution.
   With no solution on the lesson, or a "solution" that is the question paper again (two Data
   Analytics assignments, 13 Nov 2026), the checker writes its own answer key.
4. Each learner's file (PDF, JPG, PNG, or a link) is checked under the School of Innovation rules.
5. Marks and feedback wait for a person, unless the run was started with automatic posting.
6. Posting sends marks and feedback only. The portal's backend sets pass or fail at 60%.
   The checked PDF stays on this computer: the portal has no place for it.

## Portal calls

Read from the admin app's public script on 9 Nov 2026. This is an internal API with no
published contract, so a portal release can change it. **Please confirm it is fine to use.**

Base `https://admin-api.penpencil.co`, header `Authorization: Bearer <pp-token>`. The token is
the signed-in admin's `pp-token` from `admin-v2.penpencil.co` local storage.

| Purpose | Call |
|---|---|
| Batches | `GET /v1/admin/skills/course/allcourses?limit=&skip=&name=` |
| Assignments of a batch | `GET /v1/admin/skills/v2/lesson/lecture/assignment/fetch/{courseId}` |
| Submissions | `POST .../assignment/filterAssignment?page=&limit=` with `{courseId, assignmentStatus, assignmentTitles?, userEmail?}` |
| Paper, solution, points | `GET /v1/admin/skills/v2/lesson?courseId=&lessonId=` (`assignmentV2.assignment`, `.answerSheet`, `.points`) |
| Files | `https://cdn.pwskills.com/{path}` |
| **Evaluate (the only write)** | `POST .../assignment/grade/{submissionId}/{courseId}/{lessonId}` with `{comment, points: {received, outOf}, assignmentStatus: "submitted"}` |

## The separate tool: PWSkillsChecker.exe (build 171)

For the PW Skills team: the same server in its PW Skills mode (`run_skills.py` sets
`AICHECKER_APP_MODE=skills`, read by `server.APP_MODE`). It opens on `/skills.html`; `/`,
`/index.html` and `/workflow.html` redirect there; the page hides its copy workflow link
(`/api/config` reports `app_mode`). It reports `BUILD_ID + "-skills"`, so neither launcher
joins the other's server (`run_client._sibling` passes over the other mode's port instead of
calling it an older copy), and the two can run side by side. Its runs are kept next to the EXE in
`pw_skills_data` and its saved jobs and caches in `%LOCALAPPDATA%\AI Copy Checker - PW Skills`.
Build: `AICHECKER_EXE_ENTRY=run_skills.py`, `AICHECKER_EXE_NAME=PWSkillsChecker` with
`AICheckerClient.spec`; `tools/smoke_release.py` knows it. The how-to guide sent with it is made
by `tools/make_skills_guide.py`. Users need their @pw.live email on the proxy allowlist for the
app "AI Copy Checker" (control sheet, Whitelisted tab), the PW VPN, Chrome or Edge, and an
admin-v2 account.

## The run report (build 174)

`skills_report.py`, asked for on 24 Nov 2026 ("how many failed with reason, all the links").
Worked out from the run's own files at no cost: run.json, each copy's submission.pdf (its
links, `linked_work.links_in_pdf`) and checked.pdf (the "Linked work N of M: <file>" headings
of what was read). Each link is "Opened and read", "Not opened: <reason>" (the reason quoted
in the copy's note or review reasons, or `linked_work.unfollowed_reason`) or "Not reached yet".
Copies not posted are grouped by reason (`skills_report.REASONS`, first match). The page shows
the summary at the bottom of a run that is not busy (`GET /api/skills/runs/{id}/report`), and
**Download report (Excel)** gets Summary, Copies and Links sheets
(`GET /api/skills/runs/{id}/report.xlsx`). `SkillsStore.finish` also writes `report.xlsx`
next to the run each time it stops.

## Finding the batch (build 169)

Many batches share a course name, so the search reads every page the portal returns for what
was typed (100 a page, up to 500) and ranks them on this computer: **exact** (the batch code,
the course name, or "Course name | Batch code" as sheets write it, case and spacing aside), then
starts-with, then contains. Exact matches are listed first with a badge, and a single exact
match is chosen by itself. "Name | Code" is searched by its code first.

## The page (build 169)

Admin sign-in status, then one "What to check" card: batch search with the chosen batch kept
on screen, assignments with a filter and Tick all / Clear, learners checked as typed (10-digit
phone or email), more options folded away, the auto-post warning shown only when it is ticked,
and a one-line summary of what Fetch and check will do. Runs show a progress bar. A run's copies
can be filtered by tiles (waiting for you, needs a person, in progress, posted), and a copy in
progress shows how long it has been in its stage (`stage_at`), flagged when checking passes
15 minutes.

`admin-api.penpencil.co` is an internal load balancer on a private address, so the tool
only works on a computer on PW's office network or VPN.

## Rules it keeps

- Evaluate is final for the learner, so nothing posts until a person approves, unless the run was started with automatic posting.
- Just before posting, the submission is read again. A replaced file is checked again. A copy that is evaluated or under evaluation is left alone.
- A post is recorded before it is sent. If the answer is lost, the portal is read to see what happened. A post is never sent twice.
- The portal's form refuses 0 marks, so a 0 waits for a person.
- A paper printing its own total (for example 200) is scaled to the portal's maximum, and the feedback says so.
- The portal refuses a comment over 500 characters, counted as sent (each line a `<p>` paragraph). The
  feedback is written to fit: the opening sentence of the overall remark, the questions that lost most
  with their reason, the other questions that lost marks named in one line, then more of the overall
  remark while there is room. A person's edit is counted live on the page and cannot be saved over the
  limit. Feedback written before build 167 is written again from the saved report when it is posted,
  unless a person wrote it.
- The admin token is held in memory and in that window's browser profile, and is never written to a run file or log.

## Live runs

12 Nov 2026 (build 166, FSDPWC_Feb_26, test learners): sign-in, batch search, learner filter, paper
and solution download, checking and refusal handling all worked. Both posts were refused with
"comment must not exceed 500 characters" (1,265 and 590 characters sent) and the copies stayed
waiting. Build 167 fits the feedback within the limit.
