# Integrated copy workflow

**Packaged app (Windows).** Open `AICheckerWorkflow.exe`: it starts straight on this
page. (`AICheckerClient.exe` opens the checker home, which links here; both run the
same local server, and whichever is opened second simply opens its page on the copy
already running.) Nothing else needs installing: Python, the checker, the
spreadsheet readers and the Playwright browser driver are inside the EXE, and the
portal window uses Google Chrome, or Microsoft Edge (part of Windows) when Chrome is
not installed. The page runs on the app's usual port, whose Google origin is already
registered. Saved runs and portal logins are kept in `workflow_data` next to the EXE,
so keep the EXE in a folder you can write to (Desktop or Documents, not Program Files).
**macOS is not supported by the packaged app**: PyInstaller only builds for the system
it runs on, and the engine looks for Windows browser paths; a Mac version would need a
Mac-built bundle and Mac browser paths.

**From source.** Double-click `start_workflow.bat` to open the integrated workflow directly in your
browser. It starts the local server or reuses an existing matching instance. Sign
in directly on the workflow page with your authorized `@pw.live` Google account.
Saved runs return after sign-in, and signing out clears the displayed copies and
runs. Running `python run_client.py` directly opens the ordinary checker home.
The workflow is local-only because portal
automation uses Chrome installed on this computer.
The workflow shortcut uses port `8767`, matching the configured Google origin.
Register `http://127.0.0.1:8767` as an Authorized JavaScript origin on the existing
Google OAuth web client; the origin excludes `/workflow.html`.

1. Enter a Google Sheet URL or upload a CSV / XLSX file. For a private Google
   Sheet, click **Connect Google Sheets** and choose an account that can open it.
   Google requests the `spreadsheets` permission to read the selected sheets and
   save workflow statuses in the student sheet. Reconnect once after updating to
   allow edit access. The workflow uses the link you enter and keeps the short-lived
   access token only in memory. Connection loads
   the tabs automatically if a URL is entered. For file imports, click **Load
   tabs**, choose the **Sheet tab**, then click **Read selected tab**. Google
   Sheets and Excel workbooks show their worksheet names; CSV has one tab named
   **CSV data**. Excel upload also works without Google connection and retains all
   tabs; CSV contains one tab.
   Download the column template from the workflow page. Existing unique export
   headings such as `prog_name`, `tcname`, `testid`, and `studentfilename` are
   accepted. CSV and Excel files require unique headings. Connected Google Sheets
   accept repeated workflow columns when their populated values agree.
2. Select one test and the copies to process. Provide the **Admin login ID (email)**
   and **Evaluator login ID (email)** for this run, a future
   deadline in local time, category, class, subject, total marks, and the official
   question paper and answer key PDFs. Start a separate run for each test.
   **Fetch from portal** fills the two PDFs from the portal itself: it opens the
   evaluator Chrome window, reads the selected test's own question paper and Model
   Answer files (the same read-only test-details request the portal page makes,
   by the sheet's test ID) and shows their names and page counts. Several Model
   Answer files are merged in order. **View question paper** and **View Model
   Answer** open the fetched PDFs in a preview on the page, so what the checker
   will mark against can be checked before Start. The fetch stops if the portal
   names that test ID differently from the sheet, and an uploaded file always takes
   priority over a fetched one, so a wrong or missing portal file is replaced by
   uploading. Completed fetched documents are kept in memory for 30 minutes for
   the account that fetched them. Waiting for login or fetching in another window
   does not discard an unfinished fetch. For a saved run, **View run's question paper** and **View
   run's answer key** show the documents that run uses.
   If questions and solutions share one PDF, upload it as the question paper,
   enable **Question paper and answer key are in the same PDF**, and enter the
   question and answer page ranges. Page numbers start at 1; blank means the
   whole file. See [PDF page ranges](PDF_PAGE_RANGES.md).
3. Start. Log in with the admin email entered in step 2 in the admin Chrome window
   for assignment, and with the evaluator email in the evaluator Chrome window
   for upload. Each window's login banner and title name its role and exact login
   ID. Separate browser
   profiles and debugging ports keep these sessions apart. The tool operates only
   on the selected students; it does not assign all copies in the portal test.
4. Watch the progress table. Release-approved checked copies are uploaded with
   top-level question marks, total and the grading report's comment. Competence
   ratings are omitted rather than fabricated from total marks. If the portal
   requires them, its form will need an explicit ratings input before submission.
   A copy the checker sends to **faculty review** is uploaded too, with its marks
   and a faculty-review flag; see
   [Faculty-review copies](#faculty-review-copies). Copies the
   checker **rejects** (typed or printed pages, a booklet for another paper, a
   duplicate upload, an unreadable file) are posted to the portal at once with the
   reason as the comment and 0 marks; see [Rejected copies](#rejected-copies) below.

Only direct HTTPS CloudFront PDF links are supported initially, matching the
provided sheet. Downloads are limited to 50 MB and validated as readable PDFs.
Workflow sign-in and Google Sheets connection serve different purposes: the first
authorizes AI checker access and saved runs, while the second authorizes reading
private sheets. Public exportable links can still load without connection. If
Google reports that its Sheets API is disabled, the app's Google Cloud project
owner must enable that API and configure the Sheets scope on its OAuth consent
screen. File import works while that configuration is unavailable. Denied access,
closed popups and loading failures appear beside the tab controls.

Connected Google Sheets load worksheet metadata only when you click **Load tabs**.
**Read selected tab** then fetches only that worksheet's workflow columns in
bounded row batches. Other worksheets, including large archives and feedback
data, are not downloaded. Each batch verifies the selected worksheet ID and name.
Empty allocated rows do not count toward the copy limit. A compact scan of the
needed columns locates populated rows, including copies after long blank gaps;
only their row windows are fetched with hyperlinks and cell details. Temporary
Google rate-limit and server errors are retried with a bounded wait.
Successful imported rows are cached for 10 minutes, and retries reuse that snapshot.
Click **Load tabs** again to refresh the source. Changing the source or selected
tab clears previously displayed copies.

Unrelated repeated headings in Google exports are ignored. Repeated workflow
columns are merged when their nonempty values agree; conflicting identity values
produce an explicit error. CSV and Excel file imports still require unique headers.
PDF/file size limits remain separate from Google metadata and batch limits.

## Autopilot: two sheets, no one needed

### When the portal says “Unable to fetch task” (build 145)

All copies using that portal account now share one cooldown: roughly 20, 40, 60,
then 120 seconds, with a small stagger between accounts. A server `Retry-After`
response can extend the wait. Queued students do not keep reloading the same
broken list. The window reloads once when its next attempt is due; a successful
step clears the cooldown. Stop interrupts the wait immediately.

The copy's progress and the window's latest problem include the observed HTTP
status or a network failure when available. A generic toast alone is reported as
an unknown server cause. The sheet shows `error occurred`, so the row remains
retryable. These waits do not consume a student's failure attempts. Actions whose
Submit confirmation was lost still go through reconciliation before any retry.

An old toast does not block a valid program row after recovery, and searches no
longer send an unnecessary blank search before the intended search. A 429 or
server failure keeps the saved login. If the portal itself keeps failing, its
service still needs to recover; a denied read may need the account's login or
permissions checked. Avoid using the same portal account concurrently in another
browser or computer if that service replaces its existing login.

### Student statuses, safe reruns, and storage (build 144)

The tool automatically chooses **Test Evaluation** when the portal shows its
two-option collections page, for both admin and evaluator logins.

Connected student sheets receive a `workflow_status` column (an existing
`automation_status`, `ai_checker_status`, `processing_status`, or `status` column
is also accepted). The portal's original status column stays separate.

| Workflow status | On the next import/run |
| --- | --- |
| uploaded | Skip; upload confirmed or portal already evaluated it |
| rejected | Skip; rejection confirmed on the portal |
| not downloadable file | Skip; unreadable, missing (404/410), or unsupported file |
| error occurred, not login | Retry the failed copy |
| awaiting confirmation | Keep the saved run; check the portal before any resubmission |
| blank, processing | Process unfinished copies, respecting active saved runs |

Both manual runs and Autopilot skip completed statuses. Safe failed copies in an
ended saved run can enter a new run; the previous row is retired so Resume cannot
process it twice. Unconfirmed portal submissions stay with their existing run for
reconciliation. Status writes rematch the current submission, student, and test IDs,
so sorting the sheet does not direct a result to another student. Pending writes
survive app restart; reconnect Google Sheets to flush them when access expires.
Temporary write failures receive bounded retries and remain visible if unresolved.

For uploaded CSV/XLSX files, download the run's **Report CSV** and use that for the
next import. It includes the original workflow columns and `workflow_status`.
The browser cannot overwrite the original local file automatically.

After a confirmed upload or rejection, the downloaded student PDF and generated
checked PDF are deleted automatically, including temporary workflow grading jobs.
The row's **PDF** download is then unavailable. Lightweight reports, run progress,
and shared question paper/answer key remain. Errors, held review copies, and
unconfirmed submissions retain the files needed for retry or reconciliation.

The **Autopilot** card at the top of the workflow page runs every test of a student
sheet without a person (operator's request, 26 October 2026; build 139). Give:

* the **student sheet** link and tab name: the portal export with one row per copy
  (the same columns as step 1, plus `exam`, the exam type such as SCHOOL PREPARATION);
* the **accounts sheet** link and tab name: one row per **admin + evaluator pair**, with
  columns named like `admin`, `evaluator` and `password` (any order, any case). An ID
  cannot be an admin in one pair and an evaluator in another; an empty password uses
  the one saved in section 6.

**Check sheets** reads both tabs once (with the Google Sheets connection) and shows
the plan: every test in sheet order with the inputs worked out from the sheet, which
can be corrected before starting:

| Input | From |
| --- | --- |
| Exam type | `exam` and the board column: SCHOOL / board / CBSE / class → school, Vidyapeeth, commerce, CA / CMA / CS |
| Class | `ctexname`, such as "Class 12th - Science - CBSE Board" |
| Subject | the test name's brackets: "(Biology) (Infinity Pro)" → Biology, "(SST by Aditya Sir)" → SST; the whole name when no bracket names a subject |
| Total marks | `totalmarks` (the value most rows give) |
| Deadline | a day after the test's run starts |

**Start autopilot** saves each pair's password (encrypted, for both of its IDs) and
gives every pair whole tests in sheet order. For its next test a pair fetches the
question paper and Model Answer from the portal through its evaluator window, checks
the paper's marks ([The paper's marks are checked first](#the-papers-marks-are-checked-first)),
and starts an ordinary run with its admin and evaluator; every copy is tried and the
portal decides (copies already evaluated are left alone). When that run has ended,
the pair takes the next test, until none is left; then a notification and email say
"Autopilot finished" with the counts. The card shows each pair's current test and
each test's status (waiting, preparing, running, done, skipped, marks problem); **Show
here** opens a test's run in step 3.

A test the autopilot cannot do is **skipped with the reason** and the pair goes on: no
question paper or Model Answer on the portal, marks that do not add up (**marks
problem**: start that test by hand with the marks entered), an exam type it does not
know, a fetch that failed three times. A portal login wait or an expired app sign-in
does not skip a test: the pair waits and continues. Copies already in a saved run are
left to that run. **Stop autopilot** stops its runs after their current step and starts
no new test. The autopilot's state is kept in `workflow_data/autopilot.json`; after the
app restarts it continues by itself at the owner's next sign-in (within a day), with
the runs it had started. The student tab is read once, at Check sheets: copies added
later are not picked up.

## Hidden portal windows

From build 140 the portal Chrome windows **work hidden** (operator's request, 26 October
2026: eight admin + evaluator pairs were sixteen windows on the screen). Each window is
the same Chrome with the same profile and login as before, started in Chrome's headless
mode (the page laid out at 1920 x 1080 and the normal browser name, so the portal sees an
ordinary Chrome). The workflow page is the only window needed: its **Portal windows**
card lists every open window (admin or evaluator, ID, hidden or on screen, its latest
problem with the picture taken at that moment) and each test's copies so far are shown
in the Autopilot card.

A window **comes on screen by itself**, at the page it was on, when it needs a person:

| When | What you do | Then |
| --- | --- | --- |
| Its ID is not signed in and could not sign itself in (no password saved, the portal refused it, or the automatic sign-in missed) | Sign in in that window, once; the page is never reloaded while it waits for you | It hides itself again after its next step; the copies waiting for it continue |
| A copy failed there on its last attempt (the third), or in a way that stops the run (for example the evaluator is not in the test's list) | Look at the page it shows (the failed page) and the picture under **Latest problem** | Click **Hide** when you have looked; closing the window by hand also hides it |

A copy that fails once or twice is tried again with the window hidden, as before. **Show**
puts any open window on screen to watch it; **Hide** hides it again (it keeps working).
**Keep every portal window on screen** brings back the windows of builds before 140 (kept
in `workflow_data/portal_windows.json`). Chrome cannot switch a running window between
hidden and on screen, so a switch closes that window cleanly and opens it again in the
other mode on the same profile: about five seconds, between two portal steps, with its
login kept. A Chrome window left open by an earlier app session keeps the mode it was
opened in (recorded in its profile as `aichecker-window-mode.txt`).

**Sign in the windows** (Autopilot card, build 141; operator, 26 October 2026: in the first
hidden run eight of nine windows could not sign themselves in) opens the window of every ID
of the plan before or while the autopilot runs. A window whose saved login works stays
hidden; one that is not signed in comes on screen at the portal's login page for you to
sign in once, then hides itself (it waits up to 15 minutes; after that the run's own steps
keep waiting for you without reloading the page). The login is kept in that ID's profile
on this computer, so the windows then work hidden for as long as the portal keeps the login.
Automatic sign-ins are still tried first ([Portal logins](#portal-logins)), one window at a
time. Checking an uncertain assignment on the portal now reads the
admin's window, as the assignment itself does (before build 140 it opened the default
evaluator window).

## Checking never waits for uploads

Checking a copy gave no errors; the portal's upload step did ("Unable to fetch task"), and while
the copies in flight waited to upload, no other copy was checked (operator, 29 October 2026).
From build 146 uploads had a lane of their own. On 30 October 2026 one evaluator missing from the
portal's list stopped six tests at assignment with no copy checked, and the operator asked for
the check to depend on neither step. The copy comes from the sheet's link, so from build 148 a run
has three lanes:

* **checking**: the run's copies in flight (default 5) take each copy through download and the
  check, then go on to the next copy. Nothing here needs the portal;
* **assigning**: one worker assigns copies in the admin window, ahead of the checking when it can.
  It is there to learn that the portal already holds a copy before that copy is paid for: 27 of
  the 39 copies assigned on 30 October were held already, and the sheet still said Submitted for
  24 of them, so the sheet cannot tell;
* **portal**: one worker assigns any copy the assigning worker did not reach, uploads checked
  copies and posts rejections, one at a time, with every wait and retry as before (a portal wait
  costs a copy no attempt).

Checking takes a copy the assigning worker has finished with first. If that worker has had no
answer from the portal for 60 seconds (`ASSIGN_HEAD_START_SECONDS`), or has stopped, checking takes
the next copy unassigned. A failed look ahead costs the copy nothing; the portal lane assigns it
after its check. The one copy in the admin window at that moment waits for that window's answer.
A copy found already evaluated after its check is not uploaded, and that check was paid for.

When the portal refuses the evaluator, the two portal lanes stop and checking goes on: every copy
of the test ends **checked, waiting to upload** and the run shows **needs attention**. Fix the
evaluator with **Change run inputs**, then **Resume / retry**: changing an account assigns the
copies again, and nothing is checked or paid for twice. Running the test again with a corrected
accounts sheet does the same ([Running a test again](#running-a-test-again-30-october-2026-build-149)).

The question paper and Model Answer still come from the portal once per test, before any copy:
without them there is nothing to check against.

## Direct portal requests (30 October 2026, build 150)

The run option **Use direct portal requests** (on the Autopilot card and in step 2, off unless
ticked) does the portal's work by the same requests its web page sends, with no Chrome window
(`portal_api.py`). The order of a copy does not change: assign the evaluator, check the copy, then
put the checked PDF, each question's marks and the feedback on the portal.

| Step | Request | Account |
|---|---|---|
| Sign in | `POST /auth/login`, with the password saved on this computer | each ID, once |
| Paper and Model Answer | `GET /tev/v2/tests/{test}/details`, then the two files | evaluator |
| Find the copy | `GET /tev/v2/tests/{test}/students?search={student}` | both |
| Evaluators | `GET /tev/v2/users/allotment-map` (every page, kept for 30 minutes) | admin |
| Assign | `PUT /tev/tests/{test}/students/{copy}/assign`, then a notification | admin |
| Upload | presigned-url, `PUT` to the file store, `PUT /tev//tests/{test}/students/{copy}/file` | evaluator |
| Marks and feedback | `PUT /tev/tests/{test}/tasks/evaluation/{copy}`, then a notification | evaluator |

The requests were read from the portal's page and checked against evaluations the browser path
saved. Every write is guarded: the row must carry this student, test and submission id, and an
upload needs the copy to be In Evaluation with this evaluator. Marks that do not add up, or go
past the test's total or questions, never leave the app. After each write the copy is read back.

The browser takes over when nothing was changed and the client cannot handle a copy: no saved
password, another evaluator's copy, a PDF over 150 MB, or a test on the portal's AI hybrid form.
The copy's note says so. A reply the client does not understand turns direct requests off for
that ID until the app restarts, so one wrong answer cannot repeat on every copy. A lost reply
after the marks were sent is read from the portal, never sent twice. A 429 or a 5xx waits and
costs the copy no attempt. An ID missing from the evaluator list stops the portal lanes with the
usual message: `reviewer.a1@pw.live` has reviewer access only, which stopped 126 copies on 30 October.

The portal refuses an evaluation with an empty comment, and a full-mark copy has no remarks: from
build 151 such a copy gets a short positive comment, by either path. A refused copy costs direct
requests only after three in a row, or at once when the portal says yes and the marks are not there.

It is not a published API, and a portal release can change it. `[direct]` lines in `aichecker.log`
give each step's time.

## Marks problems on the Autopilot card (build 157)

**Marks problems so far** lists each test held back, with its tab, copies, reason and the portal's
links to its paper and Model Answer. **Get the paper links** reads them for a problem saved without
them, through the pair's own session. **Enter marks** opens a form filled with what was read, and
**Run this test with these marks** sends every tab of that test back to the queue with those marks
and that total. **Check again** sends it back to be read once more. Marks that do not add up return
it to the list with the reason, and the entries kept. If the autopilot has finished, its pairs start
again for that test. A stopped autopilot refuses: check the sheets again and start it.

## A lost internet connection (build 157)

A copy that fails to connect uses an attempt only when the internet is there. If neither host a run
needs can be reached, the copy waits and tries again, and after 10 minutes the operator is alerted.
A test being prepared waits too.

## Files too long for an answer sheet (build 154)

A copy over 100 pages (`MAX_COPY_PAGES`) is skipped with the reason as checking starts, never read
and never posted, and a person opens it. All copies share one core for their image work, so one
very long file held back every other copy for about 50 minutes on 31 October 2026.

## Where the EXE unpacks (build 153)

Both EXEs unpack into `%LOCALAPPDATA%\AIChecker\runtime`, not Temp, because a cleanup of Temp under a running copy takes the
certificates and fonts and every later copy fails. Nothing else cleans that folder, so each start
removes what killed copies left there: folders over an hour old whose interpreter DLL can be
deleted. Windows refuses that for a running copy, so one is never touched.

## Several student tabs (31 October 2026)

The Autopilot card's student tab box takes several tabs in order, with a comma between them:
`27 sept, 27 sept 5k-6k`. All are read once at **Check sheets**, because the Google connection may
have ended by the time a later tab is reached. The plan lists each tab's tests in that order, so
when one tab's tests are all taken, the pairs go on to the next by themselves. Each test's statuses
are saved in the tab it came from. A copy listed in two tabs is kept in the first.

The tabs are named by the operator and never taken from the sheet's own order: a tab left out may
be another computer's, and two computers on one tab would check the same copies twice.

## Copies at the same time (build 151)

**Copies checked at the same time on this computer** on the Autopilot card sets how many copies are
checked at once, 10 by default and up to 30. More does not go faster: in one process a copy's own
work queues behind one render lock and one interpreter (`grading_engine.py`), so past about 8 at
once more copies only hold more memory while they wait. No new copy starts while the computer has
under 1200 MB free (`MIN_FREE_MEMORY_MB`), so a busy computer waits instead of failing.

The app runs at below normal priority, and a test's paper and key are prepared once and shared by
its copies (`_shared_exam_documents`), which also pays for OCR of a scanned key only once.

MEASURED 30 October at 15 at once, with most evaluators on Chrome: 298 copies an hour decided, of
which 68 were full checks. More full checks an hour need more computers, each with its own accounts
tab and student tab, or the worker processes folder mode has.

## Running a test again (30 October 2026, build 149)

Run the same test again on the same computer, with **Check sheets** and **Start autopilot** or by
hand, and the new run takes only what is left:

| The copy in its earlier run | In the new run |
|---|---|
| Uploaded, rejection posted, already evaluated, duplicate | Left out |
| Checked or rejected, not yet on the portal | Uploaded or posted without being checked again |
| Skipped, whatever the reason | Picked up, from its finished check if it has one |
| Failed, or in a run that was never started or was cut off | Picked up |
| Portal action not confirmed | Stays in its run, for Resume / retry |

A finished check moves with the copy only when the new run has the same question paper, answer key
and official total. Change any of them and the copy is checked again against the new one. A run
another tab or the autopilot is working on is never touched, and neither is one created in the last
minute (`UNSTARTED_RUN_SECONDS`), because its Start is about to start it.

The checked PDF's name comes from the student, so a student's new submission has the same name as
the old one. Saved runs tell them apart by submission id. A checked PDF in **Checked copies** that no
saved run knows about (its run was deleted) is not checked again, and it cannot be uploaded either,
because its marks are gone: the copy shows **skipped** with the reason, and the student sheet says
"already checked". Upload that PDF by hand, or delete it and run the test again.

**Totals.** The autopilot card and the saved-runs card show copies uploaded, already evaluated and
rejected, plus any waiting or skipped. The autopilot's totals cover that autopilot; the saved-runs
line covers every run on this computer. Step 3 shows them for its run, and the saved-runs table has
an **Already evaluated** column.

## AI reads the marks of every paper (30 October 2026, build 148)

Ten of that day's fifty tests stopped on marks. The operator asked for AI to read the marks. The
ordinary checker still reads printed marks for free; the workflow now does this for every paper:

1. **AI reads the paper.** If its reading misses the total or the paper's printed tags, it reads
   the page images again, as before.
2. **AI decides what the paper leaves open.** When a paper does not print the marks of every part
   and two readings stop at that, a third reading is told to split them as the paper's setter would
   and list what it decided. Only this reading may decide, and code keeps it only when the rows and
   choice rules give exactly the official total. The test's line on the page ends "Claude decided
   those" so that a person can look.
3. **The paper's own printed marks check it.** When the section headers or printed tags give the
   official total, they are read too, free. They take over when the AI reading fails, and when the
   AI reading gives any question other marks (four AI readings of one 70-mark CBSE paper gave 83,
   81, 78 and 73). AI's finer rows are kept when each question's parts add up to the same marks.
   A paper with choice rules is not compared: its rows add up to more than one student can score.

A test that the AI service cannot read waits for it: the autopilot tries again every minute for up
to 30 minutes (`MARKS_SERVICE_WAIT_SECONDS`) with the real reason on the test's line, and only
then shows **marks problem**. On 30 October the proxy's storage filled up ("you are over your space
quota"), which the page showed only as "Claude could not finish the marks reading". Marks that
still will not add up to the official total stop the test as before: the total or the paper is
wrong, and a person looks.

A copy shows **checked, waiting to upload** between the two, on the page and in the student sheet's
`workflow_status` column (build 145's sheet statuses; it said "processing" before). Every checked PDF is also saved,
named after the student (`TANYA_SINGH_23217614.pdf`), in **Checked copies\<batch> - <test>**
next to the app, where the batch is the test series without " - Batch Tests" (for example
`Checked copies\VICTORY 2027 (Class 10th) - Board Subjective Test 10 (Maths) (Infinity Pro) By
Shivangi Ma'am`). Copies the checker flags or holds for faculty review are in its **Faculty
review** folder. These files stay after the upload (the run's own working copies are still
removed once uploaded). **Open checked copies** in step 3, and **Open folder** on an autopilot
test, open the folder. With the autopilot, a pair takes its next test as soon as every copy of
its test is checked; that test shows **uploading** until its uploads have ended, and the
autopilot finishes only after the last upload.

## A crashed portal window is opened again

When Chrome is short of memory it ends a page ("Target crashed"). Up to build 142 the run kept
using that dead page: every later copy of the window failed three times and was skipped (live run
of 28 October 2026: 1123 of 1239 copies of one test). From build 143:

* the window is closed and opened again on its profile (its login is kept), and the copy tries
  again after 10 seconds **without using one of its three attempts**; a copy whose window keeps
  crashing counts its attempts again after three such retries, so a run never loops;
* a crash after the Submit click is checked on the portal like any lost confirmation;
* each window loads the portal afresh every 25 portal steps, as the portal's page keeps growing
  while it stays open for hours;
* one notification and email per hour, "A portal window crashed": run fewer admin + evaluator
  pairs at once or close other programs if it keeps happening.

Copies skipped by the crashes of build 142 or earlier are tried again with **Resume / retry** on
their run (Show here on the Autopilot card).

## Faculty-review copies

From build 136 every copy the checker sends to faculty review is **uploaded with
its marks** when it has a checked PDF and marks the portal can take. Its row
reads **uploaded, flagged for faculty review**, its details say "Faculty review
flagged (confidence N%); uploaded with its marks. The checker asked for a review:
…", and the run report (`report.csv`) has the columns `flagged_for_review`,
`confidence` and `review_reasons`, so a teacher can find and recheck these copies
on the portal. The status line and the saved-runs list count them ("40/50
uploaded (12 flagged for faculty review)"; "Faculty review (uploaded / held)").

From 29 October 2026 **no copy is held for faculty review**, because there is no
faculty to review them (see [Every copy is decided](#every-copy-is-decided-29-october-2026)).
A copy the checker could not mark, or whose marks cannot be entered on the portal,
is **skipped** at once with the reason ("Skipped, no marks to upload…") instead of
being tried three times; like a check that failed, the run's follow-up round tries it
once more and **Resume / retry** checks it again. A copy an older build held without
marks is checked again on the next pass.
A run may still hold copies below a confidence it chooses: **Hold faculty-review
copies below this confidence** in step 2 or **Change run inputs…** (0, the
default, uploads them all). Build 135's 20% threshold is not carried over: its
runs resume under the new rule, so Resume uploads the copies that build held.

A check that **failed** (the grader's own "failed" state, such as a dropped proxy
connection) is not faculty review: the copy is tried again like any other failure
and, after three attempts, **skipped** with the reason; Resume / retry checks it
again. A released copy whose PDF or marks came out broken is still held without
paying for another check.

### Confidence

The grader gives no confidence figure of its own, so the workflow works one out
from what it does record: every reason it gave for holding the copy, each naming
the questions it doubts. **Confidence is the share of the paper's marks that no
review reason questions.** A reason that names a question doubts that question
(and "Q18" doubts all of Q18.i, Q18.ii...); a reason that names no question (a
cover that does not match the student, an unreadable photo) doubts the whole
paper, so the confidence is 0. "Points to check" are not reasons and do not count.

The confidence is shown and reported for every faculty-review copy and decides
only for a run that sets a minimum above 0. Copies held by builds before 135 kept
only the first three reasons; for those, each of the "and N more" is counted as
doubting the largest question not yet counted. Resume reconsiders every held copy
against the run's current minimum.

## Alerts

When a run ends by itself (complete, finished with copies needing a decision, or
unable to continue), pauses because the app sign-in expired, or a portal Chrome
window waits for a login, the computer running the app, **without any sound**:

* shows a silent Windows notification that stays until it is dismissed, and a
  red banner at the top of the workflow page until someone clicks **Dismiss**;
* emails the run's **alert email** (step 2; the signed-in account by default) from
  the sender in section 5, **evaluator.b4@pw.live** by default, through Gmail SMTP
  with that account's Google **app password**.

Set up the email once per computer in section 5: the sender's account needs 2-Step
Verification, then an app password (Google Account → Security → App passwords;
the Workspace administrator must allow them). The password is stored in
`workflow_data/alerts.json` encrypted with Windows DPAPI for the signed-in Windows
user. **Send test alert** tries the notification, the banner and the email; the line beside it shows
whether the last email went out and, if not, why. Alerts carry the test name,
status and counts, never student names. Pressing **Stop** on a run raises no alert.
Build 135 also sounded a siren at full volume; the operator found it too loud, and
from build 137 the alerts make no sound at all.

## The paper's marks are checked first

**Start** first reads the question paper's marks (the same reading grading uses, so
the first copy does not pay for it again) and checks that they add up to the
official total. If they cannot be read, do not add up, or the total entered
conflicts with the total printed on the paper, **nothing starts**: no run is saved,
no copy is assigned, and the message says what is wrong ("Not started: the
question paper's marks need fixing first. …"). The **marks editor** opens in step 2
with what was read: one row per question or part with its marks, and the choice
rules ("2 of Q21, Q22, Q23"; an alternative made of two parts is written
"Q5.a+Q5.b"). Its sum line shows what the marks add up to against the official
total. Correct the rows, the choice rules or the official total, keep **Use these
marks for this run** ticked, and press **Start** again. **Enter the paper's marks
myself** opens the editor empty when you already know the printed marks are wrong.

Entered marks pass the same checks as a reading of the paper (positive marks, no
question twice, complete choice rules) and must add up to the official total after
the choice rules; the server then binds them to that paper like a verified
reading, records who entered them, and grades every copy against them. They may
differ from the total printed on the paper (SST Test-02 printed 40 while its
questions add up to 30): that is what they are for. A reading that failed for a
passing reason (the proxy) is tried three times before Start reports it.

**Resume / retry** checks the marks again before it touches any copy. If they need
fixing, the run stops with "Stopped before touching any copy: …"; **Change run
inputs…** opens the same editor for the run (with its entered marks, or with what
was read), and Resume continues once they add up.

## This tab's run and saved runs

Step 3 shows one run: the run this browser tab started, or opened with **Show
here** in section 4. Each tab keeps its own run (also across a page reload), so
several tabs can follow several tests. Section 4 lists every saved run with its
status and counts; choosing a test in step 1 no longer changes step 3.

Stages and statuses read as what happened: **waiting**, **uploaded**,
**uploaded, flagged for faculty review**, **held for faculty review**,
**rejected, not posted yet**, **rejection posted**, **skipped**, **upload not
confirmed: check the portal** and so on. A run reads **complete** when every copy
was uploaded or posted, **finished, N skipped** when some copies were skipped
(Resume / retry tries them again), and **finished, N need a decision** when some
copies are held or unconfirmed.

## Resume and review

Runs and source/checked PDFs are stored under `workflow_data` next to the source
application or executable. `AI_CHECKER_WORKFLOW_DIR` can override this directory.
The directory also contains saved portal login profiles; keep it private. It is
excluded from Git. Grading credentials are kept only in memory and supplied again
when the operator resumes after signing in. Saved runs belong to the authorized
Google account that created them.

Stop takes effect between operations (and during portal login polling); grading
already in progress finishes. Resume skips successful assignment, downloading,
checking and upload. A copy held for faculty review (no marks to upload) can be
downloaded when it has a checked PDF but is not uploaded. If a portal action times out after it may have
submitted, its state is **assignment uncertain** or **upload uncertain**. Check the
portal and choose **Confirmed in portal** or **Verified not done** before resuming.
This prevents retries from blindly repeating a possibly successful submission.

For copies already assigned before importing the sheet, verify the evaluator in
the portal and enable **Skip the assignment step**. An admin login ID is optional
when assignment is skipped; only the evaluator's portal window is needed. The
supplied portal UI adapter matches
the program, test series, test name and student ID. Submission and test IDs are
retained for tracking, but the adapter cannot select portal records directly by
submission ID; ambiguous matches stop for attention.
The adapter requires the exact evaluator email and whole test/category labels.
Before it ticks or uploads for a row it verifies the student's identity: the
student ID in the row's text or markup, or (the live portal shows names, not IDs)
the search box still holding exactly that ID so the row is that search's single
result, with every word of the sheet's student name present in the row. A row that
fails this check stops the copy before anything is submitted. Multiple attempts
for one student in a test must be narrowed to one copy. Copies already included
in a saved run must be resumed there instead of added to a second run.

One Chrome window per account (the saved admin for assignment, the saved evaluator
for uploads, rejections and portal documents) stays open while runs use it. Each
admin email and each evaluator email has its own saved browser profile and port;
runs using the same account reuse its window. A debugging-port connection that
fails is retried before the run stops. A
copy becomes **assignment uncertain** or **upload uncertain** only once the portal's
Submit button has actually been clicked; a failure before that (student not found,
upload control missing, the program list not loading) keeps the copy at its
previous completed stage while the tool retries, up to three total attempts.
If it still fails, the copy is skipped with its error and the run continues.
Resume gives a skipped copy another three-attempt opportunity from that saved
stage. Every portal failure
saves a picture of the Chrome window as `workflow_data/screenshots/<student id>_<time>.png`,
and the row's error names that file; send it along when a step fails.

New runs keep exam inputs, source copies, checked PDFs and reports in separate
directories, with hashed artifact filenames to avoid collisions and Windows
reserved names. Existing runs retain their prior file layouts.

## Signing in once a week

Google's sign-in token lasts an hour, and until build 105 the page signed out and
every running job stopped with "Sign in again before resuming" when it expired.
The PW proxy exchanges that token for a pass that lasts seven days, so the app
now answers a successful Google sign-in with its own signed session token that
lives as long as the pass. The page keeps it in the browser's local storage, so
every tab of the app shares one sign-in, and a run started with it keeps grading
for the whole week. The proxy still authorizes every paid call, so a removed
account stops at the proxy. Sessions are kept in memory: after the app is
restarted, or after seven days, sign in once more and resume the saved run.
**Sign out** ends the session on the server as well.

## Continuous grading

From build 118, a successful question-paper marks verification has no two-hour
expiry. A long workflow keeps using that verified paper and marks graph after
24 hours. If its cached proof is removed to make room for other papers, the
workflow recovers it automatically before continuing. A restart also causes
verification to run again when the saved run resumes. The checks against the
paper, official total, question marks and exam context still apply.

Keep the app open and the computer awake during a long run. Portal sessions,
network access and the app's weekly sign-in still need to remain available.

## Assignment reads the portal's state

Each copy's assignment step looks the copy up on the test's Students page under
each status filter in turn and acts on what it finds:

| Portal shows | The run does |
| --- | --- |
| Submitted | Assigns the copy to the evaluator given in step 2 |
| In Evaluation, this evaluator | Leaves it; the copy continues to download, grading and upload |
| In Evaluation, another evaluator | Reassigns it to the given evaluator (ticks the row, sets evaluator and deadline, Submit) |
| In Review, Finished or any later status | Marks the copy **already evaluated**: the portal already holds its evaluation, nothing is uploaded |
| Not listed under any status | Retries up to three total attempts, then skips the copy with the portal screenshot and continues |

So a sheet exported after a partial run, or a run on another computer with a
different evaluator, works without preparation: copies already assigned to that
evaluator continue, copies assigned to someone else are moved, and finished
copies are skipped. The progress table notes what happened to each copy. The
**Skip the assignment step** box is only for the rare case where the portal's
Students page cannot be used at all and every copy is known to be assigned.

## Several copies of a run in flight

The AI grading of one copy takes five to eight minutes; its portal steps take
under a minute. A run therefore keeps several copies in flight at once, **Copies
checked at the same time** in step 2 (default 5, from 1 to 8): each worker walks
one copy through assignment, download, grading and upload, so the grading of one
copy overlaps the others' grading and portal steps. Portal actions still happen
one at a time in each account's Chrome window, and across all runs at most fifteen
copies are graded at the same time. If copies start failing with network or
proxy errors, lower the number for the next run. The question paper's marks
are read once per run instead of once per copy. The progress table shows what
each copy in flight is doing. Every student has an independent three-attempt
limit; several students failing consecutively do not halt the run. An
authentication failure pauses the run for sign-in after the copies already in
flight finish their current operation.

## The paper and key are paid for once per test (29 October 2026)

Every grading call of a test starts with the same material: the marking rules, the
question paper, the answer key and the marks scheme. Claude keeps that part in its
prompt cache, and a later copy pays a tenth of the input price for it instead of
the full price. Before 29 October the workflow cached nothing (each copy is its own
one-copy job), and the stored upload jobs show later copies reading back only the
marking rules, because the notes on the student's own booklet (the answer map, the
visual inventory, repeated pages) were written into the paper text and made
everything after them differ per copy.

- The booklet notes now come after the cached part, just before the student's pages.
- A workflow copy buys the 5-minute cache while other copies of its test are still
  to check, and none for the last copy, which nothing would read back. Each read
  restarts the five minutes, and the copies in flight reach the grading call a
  minute or two apart. The 5-minute write costs 1.25 times the input price, the
  1-hour one twice, so the longer one is not bought.
- Copies that reach the grading call together would each pay the write and none
  read it. The first request for a test therefore goes alone and the others wait
  for its answer (at most four minutes), then read the cache.
- A long paper graded in batches of 8 is cached batch by batch, since every copy
  asks for the same batches. Recovery of one copy's own missed questions is not
  cached: nothing would read it back.
- Not cached: calls whose shared part is below the model's minimum (Haiku caches
  nothing under 4,096 tokens; the orientation probe's instructions are a few hundred
  and the paper check's paper text is cut at 12,000 characters) and the second
  examiner, whose ChatGPT route caches on its own and has never reported a cache hit.

## Several runs at once

Each browser tab or window can drive its own run: open the workflow page again,
import the sheet, select the test and copies, enter that run's admin and evaluator login
IDs in step 2, and Start. For example, tab 1 can use admin A and evaluator A while
tab 2 uses admin B and evaluator B. Each run saves its account pair, so Resume and
rejection posts continue with the original accounts even if step 2 is edited.
The selected saved run's login IDs are shown in step 3. Runs created before admin
selection was available keep the original shared admin login; the page labels
these as legacy runs because their admin email was not recorded.

Step 3 follows the run of its own tab (see
[This tab's run and saved runs](#this-tabs-run-and-saved-runs)). From build 119, there is no app-imposed limit on the
number of active workflow windows, runs or portal document fetches. Open as many
workflow tabs or windows as the computer can support. Downloads and portal work
proceed in parallel, while all runs share fifteen AI grading slots; additional
copies wait for a slot and continue automatically. This is a shared app setting,
not a per-account quota.
Different admins can assign in parallel, and different evaluators can upload,
post rejections or fetch documents in parallel. Portal actions using the same
account are queued through that account's one Chrome window: the portal keeps one
live session per account. To avoid a shared admin becoming the assignment
bottleneck, use a different admin/evaluator pair in each active workflow tab.
Stop acts on the run selected in that tab only. The Chrome windows close when
the last active run finishes.

Several windows can process the same test when each selects a different set of
copies. Divide the selections manually: the app does not distribute one test's
copies between windows. A copy already included in a saved workflow cannot be
added to another run; resume its existing run instead. Starting the same saved
run twice is also refused. Keep each account in one portal role while runs are
active; an admin account cannot also be used as an evaluator in another window.

## Unattended runs

From build 120, a run tries each student's retryable operation **three times in
total**, with a one-second, Stop-aware pause between attempts. A portal lookup
failure, missing upload control, download error or grading error counts toward
that student's limit, even when several students fail consecutively. After the
third failure the copy becomes **skipped**, its last error stays visible, and the
worker moves to the next student, so one bad copy never holds up the others.
Earlier successful assignment, download and grading steps are retained, so
retrying a portal upload does not grade the copy again.

A portal file that cannot be read (a web page from an expired link, an empty,
corrupted or password-protected PDF) is skipped immediately with its reason. A
JPEG or PNG photo is accepted and turned into a one-page PDF. The status line
counts skipped copies, and a run finishes as **complete** when only uploaded,
already-evaluated and skipped copies remain. **Resume / retry** gives each
skipped copy a fresh three-attempt allowance from its last completed stage.

A portal action whose confirmation was lost stays **assignment uncertain**,
**upload uncertain** or **rejection uncertain**, without another automatic
Submit. Other students continue. Stop interrupts the pause between attempts and
does not count as a failed attempt.

### The run resolves problems by itself (build 137)

* **Follow-up rounds.** When every copy has had its turn, a run does not finish
  while it still has copies skipped after three failures or portal actions whose
  confirmation was lost. It waits 2 minutes, then puts the skipped copies back at
  the step they failed and reads the unconfirmed actions on the portal again (read
  only: done, not done and retried, or still unknown); then again after 10 and 30
  minutes. The status line says "Trying 2 skipped copies and 1 unconfirmed portal
  action again by itself in 2 min (round 1 of 3)", and each retried copy's row says
  "Tried again by the run itself (round N)". A copy skipped at the checking step
  gets one round only, as each check is paid. Copies whose file cannot be read are
  not retried. Stop ends the wait at once; Resume / retry starts the rounds afresh.
* **Portal logins cost nothing.** A Chrome window that waits its full five minutes
  for a portal login no longer counts as a failed attempt (it used to skip copies
  after three). The window stays open, the copy waits and tries again, its row
  says it is waiting for a login with which account, and the alert goes out once
  every 15 minutes. The run continues by itself once someone signs in.
* **"Unable to fetch task"** is waited out the same way (see below).
* **A restart continues the run.** A run the app was running when it closed (the
  computer restarted, the window was closed) continues by itself the first time
  its owner signs in to the workflow page again, when it was saved within the last
  24 hours; an older one waits for **Resume / retry**.

What still needs a person: a portal login when no portal password is saved (see
[Portal logins](#portal-logins)), the weekly app sign-in, inputs only a person can correct
(an evaluator not in the test's list on the portal, a paper whose marks need
entering), copies the checker could not mark, and portal actions the portal still
cannot confirm after every round. For those, check the portal and choose
**Confirmed in portal** or **Verified not done**.

## Portal logins

Windows using **different IDs never log each other out**; the portal's one-login
rule is per ID. Up to build 137 the windows were still logged out by the tool
itself: when the portal said "Unable to fetch task", the supplied engine asked the
portal whether the window's login was valid and treated **any** error answer as
"login cancelled", including the "busy" answers a loaded portal gives (HTTP 429,
500, 502, 503). It then deleted the window's saved login and showed the login page.
More windows mean a busier portal, so this happened more with many windows.

From build 138 only **401 or 403** (the portal really ended the login) clears a
window's login; a busy or failing answer keeps it, and the copy waits and retries
as for "Unable to fetch task". Each check that is not a plain yes is written to
`aichecker.log` with the window's ID and the portal's answer, for example "The
portal ended the login of the EVALUATOR account x@pw.live (HTTP 401)" or "The
portal answered HTTP 503 to the login check of … (busy or failing); the login is
kept". A real 401/403 means the ID was signed in somewhere else or its login
expired.

**Windows sign themselves in.** Section 6 of the workflow page, **Portal logins**,
takes the portal password used by the IDs (the same for every ID). It is stored in
`workflow_data/portal_login.json`, encrypted with Windows DPAPI for this Windows
user, never shown again and never written to a log; **Remove** deletes it. When a
window needs a login (it opens for the first time without a saved login, or the
portal ended its login), it fills the portal's own form (Email ID: the window's
ID; Password; Login) instead of waiting for a person, and carries on when the
portal opens. If the portal still shows its login form 30 seconds after Login (the
password was not accepted), that ID is **not tried again** until a password is saved
again, so a wrong password cannot lock the account; a notification and email say so,
the window comes on screen for a person, and section 6 lists the ID and the reason. A
sign-in that only missed (the form did not load, or the portal had not opened yet) also
brings the window on screen for a person (build 141), but that ID still signs itself in
automatically the next time its login ends. Automatic sign-ins take turns, one window at a
time (eight windows signing in at the same moment all missed on 26 October). Without a
saved password a logged-out window comes on screen for a person to sign in.

## "Unable to fetch task" on the portal

"Unable to fetch task" is the portal's own message: its Programs page asked the
portal's server for the task list and got an error back. The supplied engine sees
it each time it opens **Task Management → Allotment / Evaluation → Programs**,
which it does for every copy, and then asks the portal's server whether this
window's login is still valid:

* **The login was cancelled** (the portal answers 401 or 403). The portal keeps
  one live login per account, so the same account signed in on another computer,
  in another browser or in your everyday Chrome logs this window out. The window
  signs itself in again when a portal password is saved, or asks for a fresh login
  (with a notification and email, see [Alerts](#alerts)), and continues.
* **The login is still accepted.** The task list failed for another reason: the
  portal's server is slow or failing, or it is under heavy load from many windows
  opening the Programs page at once. The engine cannot tell which.

Up to build 135 the second case counted as one of the copy's three attempts, so a
spell of it skipped every copy it hit (58 copies of one Hindi run on build 122).
From build 136 it costs the copy nothing: the window reloads the portal, a
picture goes to `workflow_data/screenshots` (at most one per window every ten
minutes), and the copy waits 20 s, 40 s, 60 s, then every 2 minutes, and tries
again for as long as the run goes on. Its row shows "waiting for the portal,
which says 'Unable to fetch task' (try N, M min so far)". After 10 minutes a
notification and the alert email say so; the run keeps trying and continues by
itself once the portal loads its tasks again. Grading of other copies goes on
meanwhile. Stop ends the wait at once.

If it keeps happening, make sure each portal account is signed in **only in the
workflow's Chrome window** while a run is going (not on another computer, not in
your everyday browser), and try fewer copies at the same time.

## Problems with the run's inputs stop the pass at once

Two failures come from the run itself, not from a copy: the evaluator email is
not in that test's evaluator dropdown on the portal, and the question paper's
marks cannot be verified against the official total (from build 137 that one is
caught before the run starts; see [The paper's marks are checked
first](#the-papers-marks-are-checked-first)). Every copy would fail the
same way, so the pass stops at the first one, charges no copy an attempt, and
the status line starts with "Stopped:" and names the cause. **Change run
inputs…** in step 3 then takes a new evaluator or admin login, official total or
deadline for the saved run, and **Resume / retry** continues: copies already
assigned to the old evaluator are moved to the new one by the assignment step.
The portal engine's waits are 45 seconds in the workflow (15 in the supplied
tool), because several Chrome windows on one machine made pages slow to show.

## Lost confirmations are checked on the portal

A Submit click whose confirmation never arrived (the page did not show its
success message in time, the window lost its connection) used to leave the copy
**upload uncertain**, **assignment uncertain** or **rejection uncertain** for a
person. The run now asks the portal first: for an upload or a rejection it looks
the copy up in the evaluator's Pending list and the In Review, Evaluated and Re
Assigned lists (the supplied engine's lookups); for an assignment it reads the
copy's status and evaluator on the Students page. If the portal took the action,
the copy is marked done with a note; if it did not, the copy goes back to its
previous step and is retried, and a checked copy already on the portal is not
uploaded again. Only when the portal gives no answer does the copy wait for a
person. Before every upload or rejection the run also checks which account the
evaluator window is signed in as, and names both accounts if it is the wrong
one. A sign-out of the app no longer stops a run: it waits, says so in the
status line, and continues by itself after the next sign-in on any tab. At the
end of every run `report.csv` in the run folder, and **Download run report
(CSV)** on the page, list every copy with its test, batch, student, stage,
marks, note and error.

## Which account to sign in

Two kinds of Chrome window open during a run, and each one's login page carries
a banner (and window title) naming the account it wants:

| Window | Opens at | Sign in with |
| --- | --- | --- |
| Admin | the first assignment, unless skipped | the exact **Admin login ID (email)** saved when the run started |
| Evaluator | **Fetch from portal**, the first upload, and rejection posts | the **Evaluator login ID (email)** in step 2 for a fetch or new run, or the saved evaluator for Resume and rejection posts |

Each admin and evaluator account has its own saved profile, so a computer that
has used several account pairs keeps their logins. No portal password is entered
in the workflow form; sign in directly in each account's portal Chrome window.
Build 116 creates distinct profiles even for account IDs with similar punctuation.
The first use after this update may ask for evaluator portal sign-in once again;
the new account profiles keep that login for later runs.
Step 2 displays the login IDs for a new run as they are entered; step 3 displays
the selected saved run's accounts, and Start and Resume name the accounts to use.
The progress table also names the account handling each portal step. With
assignment skipped, the page clearly says no admin login is needed. Older saved
runs without an admin email continue using the original shared admin login.

## Every copy is decided (29 October 2026)

There is no faculty to review copies, so every copy is either **checked and uploaded**
(flagged when the checker had a doubt) or **rejected** with a reason. What decided
copies until then, measured on `aichecker.log` of 29 October (819 school copies):

* The text check (Haiku, on the OCR text) called 276 uploads another paper, notes,
  typed or an unrelated document, at 0.95 to 0.99, and 196 of them were **graded
  anyway**: an IRCTC ticket, a residence certificate form, school notices,
  timetables, a syllabus and typed study guides. Code had to verify the model's
  evidence, and such documents have no answers to quote; their printed numbering
  counted as this paper's question labels (18 on the form).
* 269 copies Mathpix read as printed were graded because they showed two or more
  question numbers; the text check called 177 of them something other than this
  paper's answers.
* 151 copies were never checked at all, because the question paper's text layer was
  missing (its questions printed as pictures).
* Other holds went to faculty review: a booklet in Hindi for an English only paper,
  "not answers to this paper" without verifiable evidence, a photo under 800 px
  (several of those were typed PDFs whose only picture is a logo), and a line in the
  student's writing aimed at the grader.

**How a copy is decided now** (`pipeline._refuse_other_paper`, `paper_match.py`):

1. Free gates as before: a near-blank upload with no question labelled is refused.
2. A copy is **suspected** when Mathpix reads it as printed, when its script is not
   the paper's, or when the text check says another paper or not an answer sheet
   (at 0.8 or more). Without the paper's text the text check still says what the
   booklet is.
3. A suspected copy is **looked at on its page images** by a second model
   (`claude-sonnet-4-6`, the first four pages; the paper's first two pages go with
   it when the paper has no text). It is not told what the first reading said.
4. It is **rejected only when both readings agree** (at 0.9 or more for the look):
   not an answer sheet, answers to another test, or typed answers where Mathpix also
   reads print. Anything else (the look says it answers this paper, is unsure, or
   fails twice) **grades the copy**. Only when the look cannot be had at all do the
   earlier verified-evidence rules decide, without holding anything.

A small photo, and a line aimed at the grader, are checked and **flagged** instead of
held. The School of Innovation category keeps its own rules (typed work is its answer
sheet; link-only work still goes to a reviewer).

**Whole-test guard.** A wrong question paper on the portal would make every copy of a
test "another test", and those rejections would reach the students. When rejecting a
copy as another test's would make **3 such rejections and 30% of the test's decided
copies**, the copy is not rejected and the run stops: "many copies of this test look
like answers to a different test, so the question paper given to the checker may be
the wrong one". Check the paper (**Change run inputs…** to replace it), then
**Resume / retry**: the copies refused as another test's and not yet posted are
checked again, against the paper the run has then, and the guard is off for the rest
of that run.

Cost: the look costs about Rs.3 and runs only on suspected copies; a rejected
junk upload no longer pays for a full check (Rs.10 or more).

## Rejected copies

A rejected copy is not a genuine answer sheet for this paper, so it is neither
uploaded with marks nor queued for faculty review. Its row shows **rejected** and
the checker's reason (the same text folder mode writes to `rejected/<name>_reason.txt`).
From build 135 the run posts it to the portal at once (the option "Post rejected
copies to the portal as soon as they are found" in step 2, on by default; runs
from older builds are treated as on, and **Change run inputs…** turns it off for
a run). The post is the same as the button's below, with the same three attempts,
lost-confirmation check and skip. A rejection posts 0 marks and the reason as the
student's comment, so a wrong rejection reaches the student; turn the option off
for a test whose rejections you want to check first.

**Submit rejected copies to portal** (in the saved-run actions, after a
confirmation prompt) posts every rejected copy of the run from the evaluator
Chrome window, one at a time: it searches the Pending list by student ID, uploads
the student's **original** PDF, enters the rejection reason as the comment, sets
Total Marks to 0 and clicks Submit. Question marks and ratings are never filled,
and an existing different comment stops that copy for manual review. This is the
supplied Data_Filler "Rejection comments" flow (`rejection_comments.submit_rejection`),
with the integrated workflow's student-identity guard on the located row and its
intent journal: the row becomes **rejection uncertain** right before Submit, so a
lost confirmation is reconciled by the operator (**Confirmed in portal** /
**Verified not done**) instead of being posted twice. A confirmed post shows
**rejection submitted** with 0 marks. The comment text is what the student will
read on the portal; check the reason before submitting.

A failure before Submit (student not in Pending, duplicate website rows, search
not settling within 45 seconds, upload not confirmed) is retried up to three
total attempts. If it still fails, the copy is **skipped** with its failure and
rejection reason retained, and the next rejected student is processed. Use
**Resume / retry** to restore the rejected stage before trying its rejection
submission again.
The original copy must still be stored under the run; otherwise submit that
rejection by hand.

Install dependencies using the updated requirements file. Playwright attaches to
system Chrome, so a separate Playwright browser download is not required. The EXE
spec now includes the workflow and spreadsheet dependencies, and builds both
`AICheckerClient.exe` and `AICheckerWorkflow.exe` (the command is in the comment at
the top of `AICheckerClient.spec`). Rebuild the EXEs to distribute this feature;
existing executables are not modified by source edits.

## Portal engine (Data_Filler)

The portal automation is the supplied Data_Filler "Paper Uploader" source,
vendored under `portal_vendor/`: `data_filler.py` (assignment, evaluation upload,
Chrome session), `rejection_browser.py` and `rejection_comments.py` (rejected
copies) and `match_paper.py` (only its Chrome-session helper is used). The current
copy is **v0.9.10** (received 9 October 2026); the unmodified sources and the
supplier's 90 regression tests are kept beside the repository in
`../integration_sources/Data_Filler`. Compared with the v0.6 engine integrated on
6 October, v0.9.10 resolves the evaluator by exact email from the evaluator control
only (native selects and searchable comboboxes, lazy lists reopened once, the
status filter never mistaken for it), refuses ambiguous evaluator options, reads a
whole Students table in one browser call, and reports an unconfirmed Submit as
`Unverified` instead of success. Its bulk "In Evaluation" reassignment and the
Tkinter app are not used by this workflow, which assigns the selected students one
by one.

Four integrated-workflow guards are re-applied on top of the supplied source and
are listed at the top of `portal_vendor/data_filler.py`: whole-label test and
category matching, exact unique row navigation, and `verify_student_row` before an
assignment tick or an evaluation upload. `rejection_comments.submit_rejection`
gained an optional `verify_row` hook for the same guard. When a newer Data_Filler
arrives, vendor it the same way and keep these guards; `tests/test_workflow_portal_adapter.py`
fails if any of them is missing.

## Validation

`python -m pytest tests/test_workflow_large_google_sheet.py tests/test_workflow_google_oauth.py tests/test_workflow_storage_read.py tests/test_workflow_sheet_tabs.py tests/test_integrated_workflow.py tests/test_workflow_portal_adapter.py` covers sheet mapping, large-sheet paging, Google OAuth token handling, worksheet selection and snapshot isolation, identity
conflicts, download host restrictions, marks aggregation, stage ordering, resumable
failures, stop behavior, faculty review, rejected copies (held, posted only on
request, unconfirmed posts never repeated, reconciliation), uncertain uploads,
account isolation and local-only API access, storage failures, duplicate runs,
question-ID variants and real multipart API progression. The portal adapter tests
exercise actual selector helpers through offline locator stubs. The supplier's own
tests run against the vendored engine as `tests/test_portal_vendor_*.py` (90 tests:
bundle assignment and pagination, evaluator dropdown loading and recovery, rejection
search/upload recovery, reason pairing and zero-total submission). Run them with the
default temporary directory or a short `--basetemp`; a deep temporary path makes
the saved-run files exceed the Windows path limit and fails unrelated tests. `python tools/smoke_workflow_ui.py` checks
desktop/mobile layouts, sign-in, selection validation, starting, progress and review
states in Chrome with simulated responses. `python tools/smoke_workflow_auth.py`
checks on-page sign-in, expired/denied sessions, sign-out, retry and saved-run
restoration against offline fixtures. A real supplied CloudFront copy was
downloaded and validated locally during testing (7 pages, 3,765,418 bytes).
Portal interactions use the supplied Data_Filler v0.9.10 selectors with additional identity guards.
`python tools/smoke_workflow_portal.py` exercises assignment, the file chooser,
marks form and submission toasts against an offline browser fixture, and
`python tools/smoke_workflow_rejection.py` (the supplier's browser check) exercises
the rejection upload, comment, zero total, required-field validation and
missed-toast recovery against an isolated local page.
`python tools/smoke_workflow_exam_documents.py` drives **Fetch from portal** on the
real page against fixture responses: progress, fetched documents replacing the file
requirements, Start sending the fetch id, fallback to uploads and cancellation. See
`WORKFLOW_TEST_REPORT.md` for the observed results and live-testing limits.
A real portal run with authorized accounts and matching exam PDFs is still needed
to verify those selectors and the portal's current required form fields.

`python tools/smoke_workflow_connected.py` runs the actual Chrome frontend through
the production workflow backend: connect, load tabs, import the selected tab,
start one copy, complete all stages, download checked output and resume. External
Google responses, PDF bytes, grading and portal operations are fixtures; this is
a connected application test and does not establish live portal acceptance.
