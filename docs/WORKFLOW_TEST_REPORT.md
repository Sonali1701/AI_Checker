# Integrated workflow test report

## 5 November 2026: a paper that contradicts its own total, and choice rules by question (build 162)

The NEEV Class 9 Hindi paper (Complete Subjective Test-01, 80 marks) heads ten questions "[10 x 2 = 20]"
and tags each of them "[1 M]", so its per-question tags add up to 70 against its stated 80. The reading
that follows the headings and lands on 80 passed the marks stage, and `preflight`'s tag check then refused
every copy for having no 1-mark question -- after each copy had been checked and paid for. All 317 came
back "error occurred" with nothing uploaded, 314 of the 335 such rows on the operator's 11 tab.

`scheme_tag_shortfall` now stays out of it when every question is tagged (tag count equals the scheme's
rows), the tags do NOT reconcile with the official total, and the scheme does. Papers whose tags are a
subset of the paper keep the check, because an untagged question explains their gap, and the disjointness
test is untouched.

A choice rule may also name a question plainly now. On 3 November "2 of Q7, Q8, Q9, Q10, Q11" was totalled
by the page as 14 and then refused by the server, which only knew the rows Q11.i to Q11.iii, and the test
went back to its marks problem a few seconds after Run. A question in a rule stands for its own row, or
for all its parts when it has none, and a question that is not listed at all is named as soon as the
operator presses the button.

| Check | Result |
|---|---|
| Full suite before the build | 2287 passed |
| `test_tags_against_official_total_1105.py` | 9 passed, including the CA Inter Taxation and CA Final Auditing protections |
| `test_operator_choice_parts_1103.py` | 7 passed |
| The real Hindi paper through `scheme_contradicts_paper` | refused before, accepted after |
| `tools/smoke_release.py`, both EXEs, alone at normal priority | passed first time |
| `tools/smoke_workflow_autopilot.py` | passed |
| `tools/smoke_workflow_pair.py build162` | passed |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme`, `test_scheme_fidelity` | passed |
| The 317 Hindi copies re-checked on 162 | not yet run |

The build stopped between its two EXEs overnight with no error written: the laptop sleeps after 300
seconds idle on battery. Resumed in the morning, keeping the EXE already built.

## 2 November 2026: taken copies, old Autopilot runs and low memory (build 161)

On 160 a copy that another computer finished after it was assigned here went to the browser, whose login
the direct sign-in had ended, and waited for ever on "Unable to fetch task". While it waited, every copy
of that evaluator waited too. Three such copies froze three accounts. Such a copy is now recorded and
passed: `already_evaluated` when finished elsewhere, `skipped` when another evaluator holds it. Nothing
is posted.

A Start replaces the Autopilot's list of tests, so runs of earlier Autopilots went on unseen, held the
checking slots, were out of Stop's reach, and came back on a restart. Stop now reaches them, and a
restart leaves them out. Their copies stay saved, and a Start on their tab picks them up.

Below 1,200 MB free no new check starts, and the page said only "waiting for grading slot". The wait now
names memory, with a notification at most every 30 minutes.

| Check | Result |
|---|---|
| Full suite on 161 | 2258 passed, 2 failed |
| The 2 failures: `test_copies_of_one_run_are_graded_in_parallel_and_uploaded_in_turn`, `test_an_unreadable_portal_file_skips_the_copy_and_the_run_goes_on` | timing under load, with a live run and paging. Both passed when re-run alone, and in a 128-test run of the workflow and portal files |
| `tools/smoke_release.py`, both EXEs, alone at normal priority | passed first time |
| `tools/smoke_workflow_autopilot.py` | passed |
| `tools/smoke_workflow_pair.py build161` | passed |
| A taken copy on the live portal | not yet seen on 161 |

## 2 November 2026: the text scan no longer stops a workflow test (build 160)

Live on 159, the 11 oct tab stopped 12 tests as marks problems, 2,504 copies. On eight papers, 1,980
copies, the reader's rows gave the official total exactly, and only the free text scan disagreed: 92
for 80 on SST, 222 for 80 on English, 51 for 30 on Biology, 27 for 25 on Science. In the workflow a
reading that lands on the official total is now kept and the disagreement is logged. The Checker page,
where a teacher is present, keeps its check.

The reader prompt also says that marks stated per case study, passage or set of linked questions
belong to the whole group. Two Maths tests read 110 for 80 because each question of a case study got
the case's full marks.

The eight were put through the real `server._workflow_read_marks` with their saved readings, no AI or
portal call, because gates after the scan could have stopped them again: a paper that prints a choice
instruction and has no choice groups stops at `ai_choice_unverified`. None of the eight carries a
choice signal, and all eight came back `ai_verified` with a proof.

By 02:00 two more had stopped on 159. Physics by Rakshak Sir, 693 copies, is the same false alarm and
came back `ai_verified` the same way. Complete Subjective Test 01 (English), 153 copies, still stops,
correctly: its sections print 8 + 10 + 10 + 12 = 40 marks under a header of 80.

| Check | Result |
|---|---|
| `test_marks_scan_1102.py`, with the existing reconciliation tests | 15 passed before the build; a whole-route test added after it |
| The 12 stopped papers through the whole workflow reading, no AI call | 8 `ai_verified` (1,980 copies). 4 still stop, as they should: Hindi 88 + 229 (printed 70, official 80), Maths 145 + 62 (110 for 80 until read again) |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme` | passed |
| Full suite on 160, which also covers 159's changes | 2233 passed |
| `tools/smoke_release.py`, both EXEs, alone at normal priority | passed first time |
| `tools/smoke_workflow_autopilot.py` | passed |
| `tools/smoke_workflow_pair.py build160` | passed |
| Case-study line on a live AI call | not yet measured |
| Check again on a live stopped test | not yet tried |

Speed of 159 on the live run, the figure left open below: about 150 copies an hour at 10 at once, and
255 an hour over the four minutes after this build finished. It fell well under that while the build
ran beside it.

## 1 November 2026: oversized pages and the struck-line check (build 159)

Live on 157 the run fell to about 123 copies an hour. A py-spy `--gil` profile over 30 seconds had
99.7% of the samples on one line, `orient.py:259`, and 74% of those in MuPDF's PNG writer, which keeps
the GIL. One copy had 54 pages of 2880 x 1620 points, 20 megapixels each at 150 dpi: 242 s and
1,168 MB for that step, every other copy waiting behind it. The probe shrinks each page to 900 pixels
anyway, so it now sees at most 2000 pixels, written by Pillow, whose PNG encoder releases the GIL.
That copy took 26 s and 151 MB, and returned the same file. A normal page went from 0.62 s to 0.19 s
with the same pixels. A page that does need turning is rendered at full size again, so it is rebuilt
as before.

A later profile had 25% in `_row_ink_coverage` in `mathpix.py`, a Python loop over pixels for each row
of each line. It now counts a numpy row. Twenty real pages gave the same result 3.1 times faster.
None of them held a struck line, so that case rests on `test_struck_lines_row_count_1101.py`.

| Check | Result |
|---|---|
| `test_big_pages_1101.py`, `test_struck_lines_row_count_1101.py` | 15 passed |
| Full suite, on 158 (no `mathpix.py` change) | 2220 passed, 1 failed: `test_sign_out_pauses_run_without_retrying_or_skipping_students[403]`, a second copy reached its download before the pause on a busy PC. Passed 6 of 6 alone |
| The 29 test files touching orientation or struck lines, on 159 | 375 passed |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme` | passed |
| `tools/smoke_release.py`, both EXEs, alone at normal priority | passed first time |
| `tools/smoke_workflow_autopilot.py` | passed |
| `tools/smoke_workflow_pair.py build159` | passed |
| Full suite on 159 | not run |
| Speed on a live run | not yet measured |

158 was built and not released: its client start-up check ran past 90 seconds at below-normal
priority beside a live run, and the second fix replaced it.

## 1 November 2026: a lost connection costs a copy nothing, marks entered from the list (build 157)

Live on 154: 115 copies skipped. 51 of them came from a 70-second Wi-Fi drop at 14:03, as each copy
used its three attempts within seconds on "Failed to resolve". A connection failure now counts
against a copy only when `internet_is_down()` says the internet is there: the two hosts a run needs
are tried, and either answering means the failure is the copy's own. Otherwise the copy waits without
using an attempt, and a wait of 10 minutes alerts the operator. A test being prepared waits the same
way. Server errors, such as Mathpix's 500, still use attempts.

The Autopilot card lists the marks problems so far with the portal's paper and Model Answer links.
**Enter marks** runs a test with typed marks and total, on every tab of that test, starting the pairs
again if the autopilot has finished. **Check again** reads the marks once more. A stopped autopilot
refuses both.

`printed_tag_sum` counted marks twice on two real papers, read offline to confirm: Parishram 2.0
English Test-03 gave 160 for 80, where a section tag shared its line with a title or sat under a long
one, and Victory Infinity Economics Test-02 gave 20 for 10, where "[2 + 2 + 3 + 3 = 10 Marks]" stood
over its own parts. Now 80 and 10. Victory Infinity SST Test-02 stays a marks problem, rightly: it
prints 40 and offers 30, and the other 10 is Section C in the separate Economics paper.

| Check | Result |
|---|---|
| `test_workflow_copy_retries.py`, `test_marks_problem_links_1031.py`, `test_marks_double_count_1031.py` | passed |
| Full suite | 2213 passed, 1 failed on a very busy PC: `test_copies_at_the_same_time_is_a_run_option` needs two 0.2 s jobs to overlap. It passed 3 of 3 alone, and its whole file passed |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme` | passed |
| `tools/smoke_release.py`, both EXEs | passed |
| `tools/smoke_workflow_autopilot.py`, a real mouse click on Run | passed |
| `tools/smoke_workflow_pair.py build157` | passed |
| Enter marks on a live test, an internet drop in a live run | not yet run |

Build 155 failed its checks and was not released: five tests compared the fetched documents exactly,
and both start-up checks ran past 90 seconds beside the suite with 1 GB of memory free. Build 156 was
built, but the session ended during its checks, so it was not released either.

## 31 October 2026: files too long for an answer sheet are skipped (build 154)

On 152 the checking fell from about 18 copies per 15 minutes to almost none between about 11:22 and
12:15. The PC was at 19% load and the app at one full core. A read-only stack viewer put the busy
worker on a 254-page upload of 1280 x 720 pt image pages, with `n: 254` in `normalize_orientation`,
and speed returned once it passed that stage. Why it took so long is not proven: alone, in its own
process, the same file renders in 46 s and its orientation images take 54 s more.

Copies over `MAX_COPY_PAGES` (100) are now skipped with the reason, unread and with nothing posted.
The check is at the start of checking, not at the download, so a copy downloaded before a restart
is caught. The longest other copy on the computer ran to 44 pages; the median was 5.

| Check | Result |
|---|---|
| `tests/test_workflow_copy_retries.py`, three new tests | passed |
| Full suite | 2177 passed |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme` | passed |
| `tools/smoke_release.py`, both EXEs | passed |
| `tools/smoke_workflow_pair.py build154` | passed |
| A long file skipped in a live run | not yet run |

## 31 October 2026: the EXE unpacks outside Temp (build 153)

At 06:43 a cleanup emptied Windows Temp under a running 152, about five hours into a run. Loaded DLLs
stayed, as Windows refuses to delete them, but the certificate file and fonts went. From then every
AI call failed, 111 tests (2,037 copies) were skipped and 58 more copies failed in 6 tests. Nothing
was uploaded or rejected after the first error. Which program did it is not proven: Storage Sense is
on but its task last ran on 30 October; Microsoft PC Manager's service was running; Windows logged a
Restart Manager session at 06:43:49, one second after the folder last changed.

The same night a save of `autopilot.json` refused for a moment ended a pair's thread, twice, so that
pair took no more tests. `run.json` already tried again and now this save does too.

| Check | Result |
|---|---|
| `tests/test_unpack_outside_temp_1031.py` | passed |
| Full suite | 2174 passed |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme` | passed |
| `tools/smoke_release.py`, both EXEs, now also checks the unpack option | passed |
| `tools/smoke_workflow_pair.py build153` | passed |
| Built EXE unpacks to `%LOCALAPPDATA%\AIChecker\runtime`, with the certificates and font there | passed |
| A second copy starting leaves a running copy's 4,516 files alone, with its folder aged two hours | passed |
| A killed copy's files are removed by the next start | passed |
| Temp cleaned under a live run | not repeated |

The first version told a running copy by trying to rename its folder. A tiny one-file EXE showed the
rename succeeds on a running copy, which would have deleted files under it. Deleting the interpreter
DLL is what Windows refuses, so that is the check.

## 31 October 2026: the autopilot goes on to the next tab (build 152)

Operator's request: "can u chnage in the code so it can go to next tab by itself after finishing the
one?" The student tab box takes several tabs in order, with a comma between them. The tabs are named
by the operator, never taken from the sheet's own order, because another computer may be working on
a tab left out.

| Check | Result |
|---|---|
| Fixture tabs (`tests/test_autopilot_tabs_1031.py`) | passed |
| Full suite | 2169 passed |
| `test_coverage_check`, `test_fanout`, `test_mark_parity`, `test_section_scheme` | passed |
| `tools/smoke_release.py`, both EXEs | passed |
| `tools/smoke_workflow_pair.py build152` | passed |
| Live run with several tabs | not yet run |

Last live run on build 151, stopped by the operator at 01:09: 517 copies planned, 270 uploaded, 167
rejected, none by the browser. Five copies need a person: 2 of Subjective Test 02 (Maths), where the
portal answered "No reviewer available for allotment"; 1 password-protected PDF; 2 of Subjective
Test 02 (SST), whose paper reads 30 marks against a total of 40.

## 30 October 2026: direct portal requests (build 150)

Operator's request: "let's try the backend method", in the order assign, check, then question-wise
marks and feedback on the portal. Requests read from the portal's page and compared with 288 saved
evaluations of `evaluator.a18@pw.live`, which showed marks stored question by question and that the
0-mark copies are mostly rejections.

| Check | Result |
|---|---|
| Fake portal (`tests/test_portal_api_1030.py`) | 25 passed |
| Live, read-only, tab 3 IDs: sign-in, evaluator list, copy row, verify calls | Passed; all five evaluators listed, `reviewer.a1@pw.live` refused |
| Live paper and Model Answer without Chrome | 1.4 s, no browser session |
| Live upload link | Given, not used |
| Live assign, on 4 copies of Subjective Test 09 (IOC), tab 3's first pair | Passed: 0.4 to 1.2 s each, read back as In Evaluation with the evaluator. The first call of a session takes about 12 s, reading the evaluator list |
| Live rejections, 2 copies (an APAAR ID, a typed Maths worksheet; both really junk) | Passed: 1.4 and 1.8 s, comment and 21 zero scores stored |
| Live marks, 2 handwritten copies, 33.5 and 39.5 of 70 | Passed: 1.7 and 2.4 s. Every question, the total and the feedback on the portal equal the tool's report, and the PDF the portal serves is byte for byte the checked PDF |
| AI marks reading on this paper | AI read Section E "(18-21) (5 x 4 = 20)" as 4 marks each (66 of 70); the paper's own headers gave 70 and were used. Correct, on reading the paper |
| Run from the Autopilot card, reassigning, browser take-over | Not run live |

Run from source with a test starter, not an EXE (the operator asked for the test before any build). Whether the tool's marks are right was not judged here: the check compares the portal with the tool.

## 30 October 2026: running a test again (build 149)

Operator's request: do not check a copy already in the checked folder, pick up skipped copies when
the same test is run again on the same system, and show totals of uploaded, already evaluated and
rejected copies. The new run leaves finished copies, carries finished checks over when the paper,
key and total are unchanged, and picks up the rest (docs/INTEGRATED_WORKFLOW.md, "Running a test
again"). The first version let a second tab take a run that had just been created; two existing
tests caught it, and a never-started run can now be taken only after a minute. Offline doubles
only (`tests/test_workflow_rerun_1030.py`); not yet run on the live portal.

## 30 October 2026: AI reads the marks of every paper (build 148)

Operator's request: "there are a lot of tests rejects because of the marks issue. i think we should
use AI to read marks of paper". Ten of fifty tests had stopped: six in a proxy outage ("over your
space quota"), three whose section headers gave the total exactly, and one (Board Subjective Test
10 Maths) whose case-study split is not printed. AI now reads every workflow paper and decides what
the paper leaves open, the paper's own marks check it and take over, and the autopilot waits up to
30 minutes for the AI service (docs/INTEGRATED_WORKFLOW.md). Offline doubles only
(`tests/test_marks_ai_first_1030.py`); not yet run on those ten papers.

## 30 October 2026: checking depends on neither assignment nor upload (build 148)

Operator's request, after that day's autopilot checked almost nothing while portal steps stopped
it: "the checking should run without depending on assign and uplaod". Of 999 copies, 126 stopped
at assignment because one evaluator was not in the portal's list. Checking, assigning and the
portal now run as three lanes (docs/INTEGRATED_WORKFLOW.md). Assignment stays alongside the check
because it alone tells that a copy is already evaluated: 27 of the 39 copies assigned that day
were, and the sheet still said Submitted for 24 of them.

Offline doubles only (`tests/test_workflow_check_first_1030.py`, 10 tests, plus the lanes and
retry tests updated to the new order). Not yet run on the live portal. Fetching the paper and
Model Answer still needs the portal once per test, so a pair that cannot sign in still skips
tests at that step.

## 29-30 October 2026: every copy decided, the paper cached, pages turned in batches (build 147)

Operator's requests: keep nothing for faculty review, reject typed documents and templates
without rejecting genuine copies, cache the prompts without spending more, and check that
copies with mixed rotations are turned the right way. Built on build 146. Junk is rejected only
when a page-image look (Sonnet) agrees with the text check; held outcomes are graded and
flagged or skipped with one automatic retry; a test whose copies keep reading as another
paper stops before posting (docs/INTEGRATED_WORKFLOW.md, "Every copy is decided"). The booklet
notes moved after the prompt cache boundary, the workflow buys the 5-minute cache while other
copies of the test remain, and the first request for a prefix goes alone ("The paper and key are
paid for once per test"). The orientation probe sends eight numbered pages a call.

| Check | Result |
| --- | --- |
| `tests/test_no_faculty_gates_1029.py` (18), `tests/test_prompt_cache_1029.py` (24), `tests/test_orientation_batches_1029.py` (8) | Passed |
| Full offline suite | 2075 passed; `test_copies_at_the_same_time_is_a_run_option` failed once while both EXEs were building (timing) and passed on rerun, with its whole file (89) |
| Release smoke on `build/stage147c/AICheckerClient.exe` and `build/stage147w/AICheckerWorkflow.exe` | Both passed: modules fresh, assets byte-identical, engine and workflow self-tests (Chrome 156 launched), correct start page |
| `tools/smoke_workflow_pair.py build147` | Passed |
| The page-image look on real copies (`tools/try_sheet_look.py`) | Not run: needs a token |

## 29 October 2026: checking never waits for uploads (build 146)

Operator's request: keep checking copies while the upload step meets "Unable to fetch task", and
keep the checked copies in a folder named after the batch and test. Built on build 145 (Codex:
portal fetch recovery, sheet status, file cleanup). `WorkflowStore.work_round` now has a checking
lane (assignment, download, check; copies in flight) and a portal lane (one worker: uploads and
rejection posts); `process_copy` takes a phase. `file_checked_copy` saves each checked PDF in
`Checked copies/<batch> - <test>/` (faculty-review copies in `Faculty review/`), `run["checked_folder"]`
and `/api/workflow/{run}/open-folder` open it. The autopilot moves a pair on when the run sets
`checking_done`; tests then show "uploading" until their run ends (`settle_uploads`), and the
autopilot finishes after the last one. A garbled "Change run inputs…" from build 145 is fixed.

| Check | Result |
| --- | --- |
| `tests/test_workflow_checking_lane.py` (6): six copies are all checked while the first upload is stuck on the portal, then all uploaded, each assigned first; checked PDFs in the batch-test folder named after the students, the flagged one in Faculty review; the folder sits next to the app; the page opens a run's folder (and not another owner's); a pair moves on when every copy is checked and its test is done when the uploads end; uploads left after a restart are started again and the autopilot's end waits for them | Passed |
| `tools/smoke_workflow_autopilot.py`: the checked copies line, "checked, waiting to upload", Open checked copies and Open folder | Passed |

## 28 October 2026: crashed portal windows are opened again (build 143)

Operator's report (screenshots): many copies skipped with "Locator.is_visible: Target crashed" and
"Timeout 45000ms ... Search"; one test ended with 1123 of 1239 copies skipped. "Target crashed" is a
page Chrome ended (short of memory, with 16 portal windows and grading on one computer). The run
reused the dead page (`is_closed()` stays false after a crash), so every later copy of that window
failed three times, and the follow-up rounds met the same page. Changes: `Portal` marks a crashed
page (Chrome's crash event or the error text) and `_session` opens a fresh window instead; a crash
before Submit raises `PortalWindowCrashed` (a portal wait: no attempt used, 10 s, at most
`WINDOW_CRASH_RETRIES` = 3 per copy, then attempts count); the portal is loaded afresh every 25
steps per window; an hourly alert. The operator's run was on another computer; its log was not seen.

| Check | Result |
| --- | --- |
| `tests/test_workflow_window_crash.py` (6): a crashed window is opened again instead of reused; Chrome's crash event marks it; a crash while reading a test's files is a wait; the portal is loaded afresh every few steps; a copy whose window crashed twice is uploaded with no attempt used and one alert key without student names; a copy that keeps crashing is skipped after 3 crash retries and its 3 attempts | Passed |
| `tools/smoke_workflow_hidden_windows.py` (real Chrome): the page's renderer processes ended as Windows does when memory runs out; the next step waited once, then a fresh window did the step | Passed |

## 26 October 2026: sign in the windows once (build 141)

Operator's report from the first hidden run (build 140, eight pairs): the windows could not sign
themselves in, and the operator proposed signing in each ID once. The app log showed one window
signing itself in, one password refused by the portal (evaluator.c3@pw.live), and eight windows
that tried at the same moment: "the portal did not open after Login", then no login form on the
page at every later try. Changes: automatic sign-ins take turns (`Portal.LOGIN_TURN`); the first
miss brings the window on screen (`MISSES_BEFORE_PERSON = 1`, without suspending the ID); a window
on screen for a sign-in is never reloaded by the run's next try (`Portal.ensure_signed_in` only
waits); **Sign in the windows** (`/api/workflow/windows/sign-in`, `Portal.sign_in`) opens every ID
of the autopilot's plan, keeps signed-in windows hidden and puts the others on screen until signed
in. The windows list shows "signed in".

| Check | Result |
| --- | --- |
| `tests/test_workflow_hidden_windows.py` (now 18): a miss needs a person once but the ID is not suspended; a window that could not sign itself in comes on screen and is not reloaded while the run tries again, then hides once signed in; Sign in the windows keeps a signed-in window hidden, shows the others until signed in, leaves one on screen after the wait; automatic sign-ins hold the turn; the sign-in API opens each ID once on its desk and refuses bad IDs | Passed |
| `tools/smoke_workflow_hidden_windows.py` (real Chrome): as in build 140, plus Sign in the windows: on screen (not headless) for the sign-in, hidden again once signed in (13 s including the simulated person) | Passed |
| `tools/smoke_workflow_autopilot.py`: Sign in the windows sends every ID of the plan and shows "signed in" / "on screen: Sign in here" | Passed |
| Workflow and portal suites (21 files) | 461 passed |
| The 10 other Chrome smokes | 10 of 10 passed |
| `tools/selftest.py` (every offline suite) | 191 of 191 passed |
| Release smokes on both EXEs and `tools/smoke_workflow_pair.py build141` | Passed |

Not tested live: signing the real portal IDs in through Sign in the windows.

## 26 October 2026: hidden portal windows (build 140)

Operator's request: not sixteen Chrome windows for eight pairs; one main window with the inputs and
every test's status; a window opens where an error needs a person, and it signs itself in with its
password. The windows run in Chrome's headless mode on their own profiles (the engine's launcher
takes a `HEADLESS` runtime setting: `--headless=new`, a 1920 x 1080 page and the browser's normal
name instead of "HeadlessChrome"). `PortalWindows` says which windows must be on screen (a sign-in
needed, a copy's last attempt failed or a failure stops the run, the operator's Show, or every
window); the desk reopens a window in the other mode between portal steps. A sign-in that missed
without the portal refusing the password is tried again instead of suspending the ID.
`verify_assign` now uses the admin window (it opened the default evaluator window).

| Check | Result |
| --- | --- |
| `tests/test_workflow_hidden_windows.py` (15): hidden window comes on screen for a sign-in and hides itself after; on screen as before without a window list; all on screen and back; a copy's last failed attempt shows its window at the failed page, earlier ones do not; a run-stopping failure shows it at once; verify_assign in the admin window; the operator's Show outlasts a sign-in, closing is a Hide; mode of a window left open; missed vs refused sign-ins; a missed one keeps the window hidden; headless launch arguments for Chrome and Edge; the windows API (list, Show, Hide, all on screen, pictures only by name); Show / Hide reach the window's own desk; the autopilot's copies so far | Passed |
| `tools/smoke_workflow_hidden_windows.py` (real Chrome on port 9347, a local page in place of the portal): hidden window (headless on its command line, normal browser name), real failure picture 1898 x 982, on screen at the failed page on the last attempt in 5.7 s, hidden again on Hide in 4.5 s, its login (localStorage) kept through both switches, closed at the end | Passed |
| Workflow and portal suites (21 files) | 458 passed |
| Chrome smokes: `tools/smoke_workflow_autopilot.py` now also drives the Portal windows card (needs-you line, picture, Hide, Show, all on screen) and each test's copies so far; the 11 others | 12 of 12 passed |
| `tools/selftest.py` (every offline suite) | 191 of 191 passed |
| Release smokes on both EXEs (every bundled module and page file matches the source) and `tools/smoke_workflow_pair.py build140` | Passed |

Not tested live: the portal itself in a hidden window (login form, uploads), and a day of eight
pairs.

## 26 October 2026: the autopilot, two sheets and no person (build 139)

Operator's request: a student sheet and an accounts sheet (admin, evaluator, password) in;
every test fetched, checked and uploaded by the pairs for 24 hours without a person. Decided
with the operator: each accounts row is an admin + evaluator pair; pairs take whole tests in
sheet order; the student tab is read once; every row is tried and the portal decides.
`workflow_autopilot.py` holds the input rules, the accounts reader, the plan and the pilot
(one thread per pair, state in `autopilot.json`, continuing after a restart). Per-ID
passwords join `PortalLogins`; the sheet's `exam` column joins the workflow's columns. The
student sheet supplied (`new priority sheet ( Sept + Oct )`, tab `master data`) was looked at
through its Drive preview only, for its columns: exam, ctexname, testname, totalmarks.

| Check | Result |
| --- | --- |
| `tests/test_workflow_autopilot.py` (9): inputs from the export (Biology, SST, Hindi A; school, Vidyapeeth, commerce, CA, unknown); accounts tab to pairs with its errors; plan in sheet order leaving saved copies to their runs; two pairs take whole tests until none is left, one test at a time per pair, right accounts and inputs on every run, passwords saved per ID, finished alert; no Model Answer, marks problem and unknown exam type skipped with reasons while the rest go on; corrected inputs used; Stop; continuing after a restart; the API (plan without passwords, start, status, stop) | Passed |
| Workflow suites (21 files) | 452 passed |
| Chrome smokes: new `tools/smoke_workflow_autopilot.py` (both tabs with the Sheets token, editable plan, corrected inputs sent, status, Show here, Stop) and the 10 others | 11 of 11 passed |
| `tools/selftest.py` (every offline suite) | 190 of 190 passed |
| Release smokes on both EXEs (every bundled module, incl. `workflow_autopilot`, and page file matches the source) and `tools/smoke_workflow_pair.py build139` | Passed |

Not tested live: reading the real 7 MB student tab, the portal paper fetch for many tests in
a row, and a 24-hour run.

## 26 October 2026: logins kept on busy answers, windows sign themselves in (build 138)

Operator's report: portal windows on different IDs were logged out by themselves. The
supplied engine's `server_accepts_login` returned False for every non-2xx answer, so a busy
portal (429/5xx) made it delete the window's login (`forget_site_login`). The adapter now
replaces it (`_portal_accepts_login`): only 401/403 end a login, everything else keeps it,
and every non-OK answer is logged with the window's ID. Operator's request: windows sign in
by themselves with the IDs' common password. `PortalLogins` keeps it DPAPI-encrypted per
computer (page section 6, `/api/workflow/portal-login`); `Portal.on_login_wait`, called by
the engine exactly when it starts waiting for a person (after its own navigation and login
check), fills the portal's Email ID / Password / Login form (read from the public login page
without signing in) and stops trying an ID after one failure until a password is saved.

| Check | Result |
| --- | --- |
| `tests/test_workflow_portal_logins.py` (17), real engine on a fixture page: 200/429/500/502/503 keep the login and 401/403 end it, each logged with the code and the window's ID; a network error keeps it; a logged-out window signs itself in; a login the portal ended is signed in again; a wrong password is tried once, alerted, suspended and retried only after a new password; no password means the old manual wait; a signed-in window is left alone; the password is stored encrypted and removable; desks hand windows the password; the API never returns it; a failed sign-in alerts | Passed |
| Workflow, portal-adapter, engine, copy-retries, many-windows, admin-pairs, marks-lifetime, paper-marks, app-session and sheet suites (20 files) | 443 passed |
| Chrome smokes: UI, saved runs (section 6: save, never shown again, remove, failed ID listed), paper marks, connected, portal DOM, sessions (real engine, stubbed login: no portal address opened), rejection, exam documents, auth, sheet access | 10 of 10 passed |
| `tools/selftest.py` (every offline suite) | 189 of 189 passed |
| Release smokes on both EXEs (every bundled module and page file matches the source) and `tools/smoke_workflow_pair.py build138` | Passed |

Not tested live: the automatic sign-in on the real portal form, and the 401/403-only rule
against real portal answers.

## 26 October 2026: silent alerts, the run resolving problems itself, the paper's marks checked first (build 137)

Operator's requests after build 136: the alarm was too loud, so no sound at all; the
tool should resolve errors and work on its own; it should check the paper's marks
before anything starts, show the problem at once, and let a person enter the marks.
The siren, volume control and sound player were removed from `workflow_alerts.py`
(the notification was already silent; the page banner's button is now Dismiss).
`PortalLoginWait` (a window that waited its full time for a login) joins
`PortalFetchError` under `PortalWait`: neither costs the copy an attempt, and a login
wait keeps its window open. `WorkflowStore.follow_up` runs up to three automatic
rounds (2, 10, 30 minutes) for copies skipped after failures (one round for a paid
check) and re-reads unconfirmed portal actions. `resume_interrupted` continues a run
interrupted by an app restart at its owner's first sign-in, within 24 hours.
`server._workflow_check_paper` reads and checks the marks on Start (and before a
Resume touches a copy); `server._workflow_operator_marks` validates entered marks with
`parse_strict_marks_scheme`, requires the official total after choice rules, and
issues the paper-bound proof (`operator:<email>`); `_queue_evaluation` accepts a
total that differs from the printed one only for such marks.

| Check | Result |
| --- | --- |
| `tests/test_workflow_paper_marks.py` (9): a paper whose marks miss the total starts nothing (422 with the marks read and choice rules, no run, no portal action); a good paper starts and keeps its reading; entered marks validated and carry who entered them; Resume stops before any copy and continues once fixed; Change run inputs takes and clears entered marks; the real check passes a good paper and grading reuses its reading; reports a total miss and a header conflict; entered marks must add up, are bound to the paper, never pay for a reading; grading with entered marks against a wrong printed total | Passed |
| `tests/test_workflow_alerts_and_review.py` (31): no sound code, silent toast; login waits cost no attempt; the adapter's login prompt waits; a skipped copy retried by the run keeps its faculty-review note; rounds end after three, a failed check gets one; an unconfirmed action re-read and continued, never resubmitted; Stop during a round's wait; an interrupted run continues at the owner's sign-in, not another account's, not when stale, once per start; the runs list triggers it | Passed |
| Workflow, portal-adapter, engine, copy-retries, many-windows, admin-pairs, marks-lifetime, app-session and sheet suites (19 files) | 426 passed |
| Chrome smokes: UI, saved runs (Dismiss banner), new `tools/smoke_workflow_paper_marks.py` (refused Start, editor prefilled, sum line, second Start sends the marks, Change run inputs), connected, portal DOM, sessions, rejection, exam documents, auth, sheet access | 10 of 10 passed |
| `tools/selftest.py` (every offline suite) | 188 of 188 passed |
| Release smokes on both EXEs (every bundled module and page file matches the source) and `tools/smoke_workflow_pair.py build137` | Passed |

## 26 October 2026: faculty-review copies uploaded, plain statuses, portal fetch waits (build 136)

Operator's requests after build 135: upload the copies that come under faculty
review too, show proper statuses, and explain the portal's "Unable to fetch task".
Every faculty-review copy with a checked PDF and portal marks is now uploaded with
a faculty-review flag, its confidence and its reasons (row, status line, saved
runs, report columns); the minimum confidence is a run option, 0 by default, and
build 135's 20% threshold is dropped so its runs resume under the new rule. A copy
the checker could not mark stays held and says why; a check that failed is retried
and then skipped instead of being held. "Unable to fetch task" with the login still
accepted becomes `PortalFetchError` in the adapter (only before a Submit click): the
copy waits and retries without using an attempt, with an alert after 10 minutes.

| Check | Result |
| --- | --- |
| `tests/test_workflow_alerts_and_review.py` (24): faculty-review copy uploaded with flag, note and report columns; whole-paper doubt uploaded unless the run sets a minimum; no-marks copy held, no paid regrading on Resume; minimum validated, build 135 threshold dropped; failed check tried three times, skipped, re-checked on Resume; build 135 held copy uploaded on Resume; five "Unable to fetch task" in a row cost no attempt, one alert; the wait shows on the copy and Stop ends it; the adapter converts the engine's abort only before Submit, one picture per ten minutes | Passed |
| Workflow, portal-adapter, engine, copy-retries, many-windows, admin-pairs, app-session and sheet suites (18 files) | 410 passed |
| Chrome smokes: UI, saved runs (flagged and held copies, plain statuses), connected backend, portal DOM, sessions (ports 9337/9338), rejection, exam documents, auth, sheet access | All passed |
| `tools/selftest.py` (every offline suite) | 187 of 187 passed |
| Release smokes on both EXEs (every bundled module and page file matches the source) and `tools/smoke_workflow_pair.py build136` | Passed |

The auth and sheet-access smokes had drifted from the page (build 106's sign-out
request, build 135's Show here, the required admin login) and were updated; the
evaluator-dropdown test now pins the engine's 15 s timeout, which an earlier
adapter test in the same session had raised to 45 s.

## 26 October 2026: confidence uploads, rejections at once, alerts, this tab's run (build 135)

Operator's requests after the 23 and 26 October live runs on build 122: (1) step 3
shows only the run of this tab, saved runs in a section of their own; (2) a Windows
notification with an alarm loud enough to hear across a room, and an email from
evaluator.b4@pw.live, when a run stops; (3) rejected copies posted to the portal
at once; (4) faculty-review copies uploaded unless the checker's confidence is
below 20%. The grader has no confidence figure; the workflow defines it as the
share of the paper's marks no review reason questions (`review_confidence`), from
the full reason list `_workflow_grade` now returns, or for older held copies from
the saved message with a cautious count of the hidden reasons. Alerts live in
`workflow_alerts.py`: siren WAV through winsound with the Windows volume raised
through Core Audio (comtypes) and restored, a reminder toast through PowerShell's
notification API, Gmail SMTP with an app password stored under DPAPI.

| Check | Result |
| --- | --- |
| `tests/test_workflow_alerts_and_review.py` (18): confidence from full and legacy reasons, Q18 covers its parts and Q1 never Q10; held copies uploaded at or above the threshold with a note, held below it, never without a PDF; Resume reconsiders older held copies; rejections posted at once and on Resume, the option respected; alerts on run end with counts and no student names, none on Stop, the signed-in email as default, on a sign-out pause and a portal login wait; the alert centre's alarm, notification and email with an encrypted password, failures isolated, repeats dropped; the siren file; volume raised and restored; the alerts API | Passed |
| Workflow, adapter, copy-retries, many-windows and admin-pairs suites (the five manual-rejection tests now start runs with automatic posting off) | 190 passed |
| Chrome smokes: page, fetch and previews, connected backend (quiet alerts, run end alert recorded), new `tools/smoke_workflow_saved_runs.py` (this tab's run, Show here, two tabs, alarm banner and Stop alarm) | All passed |
| Read-only checks on this computer: DPAPI round trip, Core Audio volume read (26%, not muted), notification script parse | Passed |
| `tools/selftest.py` | 187 suites passed |
| Release smoke on `dist/AICheckerClient-build135.exe` and `dist/AICheckerWorkflow-build135.exe` | Both passed: 57 modules fresh (incl. `workflow_alerts`), assets byte-identical, engine and workflow self-tests; `winsound.pyd` and comtypes bundled |
| Both EXEs together (`tools/smoke_workflow_pair.py build135`) | Passed |

Not tested live: the alarm at full volume, the notification and the email (needs the
sender's app password); the first live run should use **Send test alert** first.

## 22 October 2026: input problems stop the pass at once (build 128)

Two live runs on build 122 (another computer, seven Chrome windows open): one
test skipped five copies after three attempts each, with "Evaluator
'evaluator.d4@pw.live' was not found among 2745 readable evaluator options
for this test" and "Locator.wait_for: Timeout 15000ms exceeded … waiting for
Programs"; the other skipped fourteen copies with "The question rows add up to
40 marks including every alternative; after 1 choice rule one student can score
30, but the paper total is 40". The evaluator and the paper's marks are inputs
of the whole run: every copy would fail the same way, and three attempts per
copy only spent time and grading calls. Build 128 recognises both and stops the
pass at the first copy, charges no copy an attempt, and says what to change in a
status line starting "Stopped:". **Change run inputs…** (POST
/api/workflow/{run}/inputs) changes a saved run's evaluator, admin, official
total or deadline (a past deadline is replaced), and Resume / retry continues.
The supplied engine's 15 s waits become 45 s in the workflow, since several
Chrome windows on one machine made the Programs page slow to appear.

| Check | Result |
| --- | --- |
| Workflow tests: an evaluator missing from the dropdown and unverifiable paper marks each stop the pass with copies in flight tried once, none charged or skipped, unstarted copies untouched; a saved run's inputs changed (validation, owner, refused while active, stale deadline replaced); the engine's timeout raised | Passed |
| Workflow, adapter, copy-retries, many-windows and admin-pairs suites together | 190 passed |
| Server and vendored regression; launcher and release-policy scripts; Chrome smokes (page, fetch, runs list, connected backend) | 139 passed, all checks passed |
| `tools/selftest.py` on the shared tree (with ai-checker-36's two grader.py fixes since 127) | 178 suites passed (and 178 in ai-checker-36's own run) |
| Release smoke on `dist/AICheckerClient-build128.exe` and `dist/AICheckerWorkflow-build128.exe` | Both passed: 54 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build128`) | Passed |

## 21 October 2026: runs that never stop for a lost confirmation, a sign-out or a duplicate submission (build 122)

Live run of 16 October (Subjective Test 11, 272 copies, 88 uploaded): one copy
rested as **upload uncertain** because the portal's "evaluated successfully"
message did not appear within 15 seconds of the Submit click, and seven copies
were retried with "[find student] Not in your Pending list" (the evaluator
window did not list them yet, or was signed in as another account). The operator
asked that the run never stop or wait for a person over such errors, and that
every exception be reported with the copy's details.

Build 122: a Submit click whose confirmation was lost is now verified on the
portal before anything is repeated or anyone is asked. Uploads and rejections:
the copy is looked up in the evaluator's Pending list and the In Review,
Evaluated and Re Assigned lists (the supplied engine's lookups); left Pending
means done, still Pending means not done and the copy goes back a step and is
retried, and a checked copy already on the portal is not uploaded again.
Assignments: the copy's status and evaluator on the Students page decide.
Only when the portal gives no answer does the copy wait for a person. Before
every upload or rejection the run reads which account the evaluator window is
signed in as and names both accounts if it is the wrong one. A sign-out of the
app pauses the run with a message and continues by itself after the next sign-in
on any tab. Every run writes `report.csv` (student, IDs, test, batch, stage,
marks, note, error) and the page offers **Download run report (CSV)**; the
status line explains each end state.

| Check | Result |
| --- | --- |
| Adapter tests: lost upload and rejection confirmations read from the Pending and Evaluated lists (done, not done, unknown); lost assignment confirmations read from the Students page; a retried upload does not resend a copy already on the portal; uploads from the wrong evaluator account refused naming both; the signed-in email read from the portal's user details | Passed |
| Workflow tests: a lost upload confirmation confirmed on the portal completes the copy; one the portal did not take is retried without resending; a lost assignment confirmation confirmed carries the copy on; a sign-out pauses the run until the next sign-in; the run report lists every copy and is owner-bound | Passed |
| Workflow tests: a student with two submissions of a test is started with the latest copy processed and the other recorded as a duplicate (previously the whole run was refused) | Passed |
| Workflow, server and vendored regression; launcher and release-policy scripts; all Chrome smokes | 287 passed, all checks passed |
| `tools/selftest.py` alone on the shared tree (every session's finished edits) | 171 suites passed, after two test files were adapted: the copy-retries suite's two sign-out tests expected the old halt and waited forever on the pause, and the many-windows suite lacked `import pytest` and the repo-root path insert, so the selftest ran it as a bare script |
| Release smoke on `dist/AICheckerClient-build122.exe` and `dist/AICheckerWorkflow-build122.exe` | Both passed: 52 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build122`) | Passed |

Build 122 carries, besides the workflow changes above, the other sessions'
finished work of 16 to 21 October: CA feedback (repeated_pages.py), Bihar
commerce fixes, School of Innovation, and teaching with the inventory overlap
change.

## 16 October 2026: three attempts per student, then skip (build 120)

The operator's run repeatedly failed to find students in the evaluator's Pending
list. The previous three-consecutive-failure guard treated these copy failures
as a shared outage, cancelled their attempt counts, and paused the run for
increasing retry rounds. The run could therefore keep waiting instead of
reaching the promised three-attempt skip limit.

Each student now has three total attempts, with a one-second interruptible pause
between retries. After the third ordinary failure the copy is skipped with its
last error and processing moves on, including when several students fail in a
row. Completed stages are reused. A run with only completed or skipped copies
finishes as complete, and manual Resume restores skipped copies at their saved
stage with a fresh allowance. Unreadable files still skip immediately.

Authentication failures pause for sign-in. Stop is not charged as a failure.
Actions whose Submit confirmation is missing remain uncertain and are never
submitted again automatically; remaining students continue. The same bounded
policy applies to failures before submitting a rejected copy.

Validation: all **145 targeted Python regression tests** passed, including 18
dedicated per-copy retry cases. Coverage includes continuation after consecutive
failed students, recovery on the third attempt without repeating completed
steps, Stop and sign-in preserving consumed attempts, manual Resume, uncertain
submissions and reconciliation granting a fresh allowance after **Verified not
done**. Account isolation, many simultaneous windows and long-lived marks proofs
remain covered. All 13 marks UI tests and the offline workflow browser smoke
passed, including skipped-copy reasons and checked PDF access. These checks use
offline fixtures and do not make paid grading calls or real portal submissions.
Additional release checks are recorded with the packaged build.

Both build 120 Windows EXEs passed isolated release smoke checks: bundled
source/UI and marks-proof code match, frozen grading workers complete their
self-test, the portal browser launches, and local startup routes report the
correct build identity. Versioned and default EXEs were published with matching
SHA256 hashes; results are under `build/retry120/`.

The sections below record historical builds. Their timed retry rounds and
three-consecutive-failure pauses do not apply to build 120.

## 16 October 2026: no fixed workflow window limit (build 119)

The operator requested more than three workflow windows. The active-run and
portal-fetch count limit has been removed: each workflow tab or window can start
another run using its selected admin/evaluator pair. All runs still share fifteen
AI grading slots, so copies beyond that concurrency wait and proceed when a slot
is free. Per-run copy concurrency remains 1 to 8. This removes an app limit; it
does not establish a live portal or computer capacity limit.

Account isolation, one serialized portal window per account, the cross-role login
guard, independent Stop controls and duplicate-copy/run protections remain in
place. Several windows may select the same test with manually divided copy
selections; automatic copy distribution is not part of this change.

Portal document fetches no longer have a five-entry default cache count limit.
Unfinished fetches are retained while queued, waiting for login or downloading;
the thirty-minute cache expiry applies after a fetch completes or fails.

Validation: 90 existing workflow/account-pair regressions and seven new
multi-window regressions passed. The new checks held eight runs active together,
verified the shared fifteen-copy grading limit with 24 copies across eight runs,
stopped queued work without invoking grading, exercised API start/resume/fetch
beyond three active jobs, and retained eight parallel document fetches. The
offline desktop/mobile browser check also passed. These checks used fixtures,
with no paid grading or real portal submissions.

The sections below record earlier builds and their results; their historical
run-count limits do not apply to build 119.

## 15 October 2026: one window per evaluator, named logins, retry rounds (build 112)

Three operator requests. (1) Runs for different evaluators now run together:
each evaluator account gets its own Chrome window, saved profile and debugging
port (`PortalDesks`: the admin desk plus one desk per evaluator, each with its
own thread and Playwright instance), so uploads for different evaluators no
longer wait for one another; the same account still never drives two windows,
because the portal keeps one live session per account. (2) Every login moment
says which account to use: a banner and window title on the portal's login page
("AI Checker: sign in here with the ADMIN account…" / "…the EVALUATOR account
<email>"), the progress table ("assigning in the admin window", "uploading from
the evaluator window (…)"), Start's message, and the fetch, which now requires
the evaluator email so it opens that evaluator's window. (3) Unattended
operation: failed copies are retried in timed rounds (2, 5, 10, 20, 30, 30
minutes), each round resuming from the last completed step; a sign-out, Stop and
lost-confirmation actions are never retried automatically.

| Check | Result |
| --- | --- |
| Adapter tests: an evaluator portal gets its own port and profile; the admin and evaluator login pages carry the right banner; existing session, identity and assignment tests unchanged | Passed |
| Workflow tests: two evaluators' runs complete together in their own windows (ports 9238 and 9239); the progress table names the window of each step; failed copies retried in rounds until done with no step repeated; a sign-out or exhausted rounds end in needs-attention; Stop during a retry wait ends the run; fetch requires the evaluator email | Passed |
| Workflow, server and vendored regression; launcher and release-policy scripts; all Chrome smokes | 264 passed, all checks passed |
| `tools/selftest.py` before the build | 156 suites passed |
| Release smoke on `dist/AICheckerClient-build112.exe` and `dist/AICheckerWorkflow-build112.exe` | Both passed: 50 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build112`) | Passed |

Not tested live: the banner on the real portal login page (an init script on the
CDP-connected context) and a second evaluator window on the live portal.

## 14 October 2026: assignment reads the portal's state (build 111)

Running the tool on another computer with a different evaluator email failed:
the sheet exported after the first run shows the copies as In Evaluation, and
the run refused non-Submitted copies unless "Already assigned" was ticked,
which then skipped assignment altogether and left the copies with the first
evaluator, so the uploads could not find them. Each copy's assignment step now
looks the copy up under each status filter of the test's Students page and acts
on what it finds: Submitted is assigned; In Evaluation for this evaluator is
left; In Evaluation for another evaluator is reassigned (row ticked, evaluator
and deadline set, the intent marker right before Submit, the supplied engine's
submit confirmation); In Review, Finished or any later status is noted as
**already evaluated** and skipped; a copy listed nowhere stops with the portal
screenshot. The sheet-level refusal is gone; the checkbox is now "Skip the
assignment step" for the rare case the Students page cannot be used.

| Check | Result |
| --- | --- |
| Adapter tests through the engine's primitives: assigned; already with this evaluator (by email and by name); reassigned with the marker before Submit; unacknowledged reassignment fails after the marker; finished copy untouched; nowhere or twice stops; page without the filter | 7 passed |
| Workflow tests: finished copy noted and held on resume; reassigned copy continues with its note; non-Submitted sheet accepted | Passed |
| Workflow, server and vendored regression; launcher and release-policy scripts; all Chrome smokes | 257 passed, all checks passed |
| `tools/selftest.py` before the build | 156 suites passed |
| Release smoke on `dist/AICheckerClient-build111.exe` and `dist/AICheckerWorkflow-build111.exe` | Both passed: 50 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build111`) | Passed |

Not tested live: the Students page's status filter names and the evaluator
column are known from the supplied engine's bulk code (`choose_status`,
`_allotment_snapshot`), not from a run of this per-copy path.

## 14 October 2026: several copies of a run in flight (build 110)

Measured on the live SST Test 05 run (file times in the run folder): the AI
grading took 5 to 8 minutes per copy (median 8.1 over the six graded copies) and
the portal steps from one copy's checked PDF to the next copy's download took
0.6 minutes. A run processed one copy at a time, so a copy took about 9 minutes
from assignment to upload. Build 110 keeps several copies of a run in flight,
a run option "Copies checked at the same time" (default 5, 1 to 8; assignment,
download, grading, upload per copy, with the portal desk still serving one action
at a time and at most fifteen copies grading across all runs),
reads the question paper's marks once per run instead of once per copy, and
shows each copy's current step in the progress table. Expected: about five
copies every 8 minutes per run instead of one every 9. The PW proxy's behaviour
at fifteen copies in flight has not been measured; folder mode runs 20 to 30.

| Check | Result |
| --- | --- |
| Workflow tests: five copies complete with grading overlapping and one portal action at a time; the option limits copies in flight and rejects 0, 9 or text; three failures in a row halt the run with copies in flight; marks read once for three copies | Passed |
| Workflow, server and vendored regression | 247 passed; launcher and release-policy scripts all checks passed |
| Chrome smokes: page, fetch and previews (preview scroll made instant: a click during the smooth scroll missed its button), runs list, connected backend, real engine with two windows | All passed |
| `tools/selftest.py` before the build | 156 suites passed |
| Release smoke on `dist/AICheckerClient-build110.exe` and `dist/AICheckerWorkflow-build110.exe` | Both passed: 50 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build110`) | Passed |

## 13 October 2026: the runs list follows the selected test (build 107)

The operator chose another test in step 1 and clicked Refresh runs, and step 3
kept showing the previous run: the runs list never looked at the step-1
selection. Now choosing a test, or clicking Refresh runs, selects that test's
latest run; a run picked by hand in the list holds across Refresh until the test
changes; Start selects the new run.

| Check | Result |
| --- | --- |
| `tools/smoke_workflow_runs_follow_test.py` (Chrome, two tests and two saved runs, offline fixtures) | Passed |
| Chrome smokes: page, fetch and previews, connected backend | All passed |
| `tools/selftest.py` before the build | 154 suites passed |
| Release smoke on `dist/AICheckerClient-build107.exe` and `dist/AICheckerWorkflow-build107.exe` | Both passed: 46 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build107`) | Passed |

## 13 October 2026: sign in once a week (build 106)

The operator was signed out every hour and running jobs stopped with "Sign in
again before resuming": Google's ID token lasts an hour and the app rejected it
after that on every request, although the PW proxy had already exchanged it for a
7-day pass. The app now answers a successful Google sign-in with its own signed
session token (`AppSessions` in `server.py`) that lives as long as that pass; the
page keeps it in local storage shared by every tab, running jobs present it, and
the server hands the stored Google token to the proxy kit, which uses its cached
pass. The proxy still authorizes every paid call. Sessions are in memory: an app
restart, a forged token or seven days mean one more sign-in. **Sign out** revokes
the session on the server. The main checker page stores the same token.

| Check | Result |
| --- | --- |
| `tests/test_app_session.py` | 9 passed: week-long session issued and confirmed; outlives the Google token; same run owner; tampered, expired and post-restart tokens refused; sign-out revokes; no proxy pass means no session; Google tokens and a script's bare proxy pass unchanged |
| Auth diagnostics script, workflow and server regression | 182 passed, all checks passed |
| Chrome smokes: page, fetch and previews, connected backend | All passed |
| `tools/selftest.py` before the build | 154 suites passed |
| Release smoke on `dist/AICheckerClient-build106.exe` and `dist/AICheckerWorkflow-build106.exe` | Both passed: 46 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build106`) | Passed |

Not tested live: the first real sign-in on build 106 should show "Signed in as …"
and still be signed in after an hour; a run started then should pass its first
grading call after the hour without "Sign in again before resuming".

## 13 October 2026: several runs at once (build 105)

The operator asked to drive the workflow from several browser tabs. In build 105,
up to three runs or portal fetches could be active at once (this count limit was
removed in build 119). Each run had its own worker and
stop flag, so downloads and grading of different tests overlap. Portal actions
cannot: the supplied engine drives Chrome through Playwright's sync API, which is
bound to the thread that started it, and the portal signs an account out of every
other browser. So one thread (`PortalDesk`) owns the admin and evaluator windows
and executes every run's portal action in turn, and runs that are active together
must use the same evaluator account (a run for another evaluator gets 409 until
the first finishes). Stop acts on the selected run only; the windows close when
the last active run finishes.

| Check | Result |
| --- | --- |
| Workflow, adapter and vendored regression suite | 272 passed (new: three runs complete together with grading overlapping and never more than one portal action in flight; a fourth run and a repeat start are refused; stop affects only the selected run; capacity and evaluator refusals through the API) |
| Chrome smokes: connected backend, page, fetch and previews, real engine with two headless windows | All passed |
| Launcher and release-policy scripts | All checks passed |
| `tools/selftest.py` before the build | 153 suites passed |
| Release smoke on `dist/AICheckerClient-build105.exe` and `dist/AICheckerWorkflow-build105.exe` | Both passed: 46 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build105`) | Passed |

Not measured live: three runs grading at once against the PW proxy. Folder mode
on build 83 saw connection errors at 75 copies in flight; three copies at a time
is far below that, but the first parallel session should be watched.

## 12 October 2026, late: build 104 (restart hygiene and list waiting)

Build 103 uploaded its first copy on the live portal. The operator then opened
build 103 while build 102's server was still running: the new EXE started a second
server on the next port sharing the same saved runs, so Resume and Stop appeared
dead (they reached the old process). Build 104: the launcher refuses to start
beside an AI Checker of another build and names it, Stop reports when a run is not
active in this app, and the page labels an interrupted run with the Resume hint.
Also hardened: the exact-row lookup on the portal's lists now waits for the list
to render (the supplier's version waited; the 6 October strict version raised at
once). One "found 0" failure in the operator's run was caused by a development
check that attached to the live admin window; that check now uses its own ports.

| Check | Result |
| --- | --- |
| Workflow, adapter and vendored regression suite | 270 passed |
| Launcher script (older-build refusal), release-policy script, page smokes | All passed |

## 12 October 2026, night: third live run (build 102) and build 103

Build 102 graded again, then the first upload failed with Playwright's own
"It looks like you are using Playwright Sync API inside the asyncio loop". Cause:
the admin window's Playwright instance stays alive in the worker thread, and a
second `sync_playwright()` for the evaluator window is refused in that thread.
Fix (build 103): one Playwright instance per run connects both windows; it is
stopped when the last window closes. `tools/smoke_workflow_sessions.py` now drives
the real adapter, engine and Playwright against two headless Chrome windows (on
ports 9337/9338, never a live run's 9237/9238) from one thread: admin, grading
with `asyncio.run()` in between, evaluator, close. A unit test documents the
refusal itself.

| Check | Result |
| --- | --- |
| Workflow, adapter and vendored regression suite | 269 passed |
| `tools/smoke_workflow_sessions.py` (real engine, real Playwright, two headless windows) | Passed |
| Launcher and release-policy scripts | All checks passed |
| `tools/selftest.py` before the build | 152 suites passed |
| Release smoke on `dist/AICheckerClient-build103.exe` and `dist/AICheckerWorkflow-build103.exe` | Both passed: 43 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build103`) | Passed |

## 12 October 2026, evening: second live run (build 101) and build 102

Build 101 assigned and downloaded copies on the live portal (the identity and
Chrome-window fixes held), then every copy failed at grading with
"asyncio.run() cannot be called from a running event loop". Cause: the Chrome
window now stays open, so the supplied engine's Playwright keeps an event loop
running in the worker thread, and the grading bridge starts with `asyncio.run()`.
Fix (build 102): the workflow runs the grader on a thread of its own
(`WorkflowStore.grade`); a regression test reproduces the live error without the
fix. Resume continues the stopped run from the downloaded copies.

| Check | Result |
| --- | --- |
| Workflow, adapter and vendored regression suite | 268 passed |
| Connected Chrome and backend smoke | Passed |
| `tools/selftest.py` before the build | 152 suites passed |
| Release smoke on `dist/AICheckerClient-build102.exe` and `dist/AICheckerWorkflow-build102.exe` | Both passed: 43 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build102`) | Passed |

## 12 October 2026, afternoon: first live run (build 99) and its fixes

The operator ran build 99 on a real test (206 copies). Nothing was uploaded; the
run surfaced four problems, all fixed in build 101:

| Live finding | Cause | Fix |
| --- | --- | --- |
| "Portal row does not expose student ID …" on every assignment | The live Allotment table shows names, not IDs; the 6 October identity guard could never pass | The guard now also accepts the search box still holding exactly that ID (single result) plus the sheet's student name in the row, or the ID anywhere in the row markup |
| "connect ECONNREFUSED 127.0.0.1:9237" | Chrome was opened and closed for every copy and the next copy raced the previous window's shutdown | One Chrome window per account for the whole run, connection retried up to three times |
| Copies marked "assignment uncertain" although nothing was submitted | The uncertain marker was written before the portal work began | The marker is written by the engine right before the Submit click; earlier failures keep the copy retryable |
| "'Complete Programe' was not found in the list on the site" (evaluator window, rejection post) | Not diagnosable from the log | Every portal failure now saves a screenshot under `workflow_data/screenshots` and names it in the row's error |

Also added: PDF previews of fetched portal documents and of a saved run's documents.

| Check | Result |
| --- | --- |
| Workflow, adapter and vendored regression suite | 267 passed (12 new: identity evidence, one window per account, port retry, screenshots, submit markers, previews) |
| Chrome smokes: portal helpers, rejection flow, fetch and previews, page, connected backend | All passed |
| Launcher, routing and release-policy scripts | All checks passed |
| `tools/selftest.py` before the build | 152 suites passed |
| Release smoke on `dist/AICheckerClient-build101.exe` and `dist/AICheckerWorkflow-build101.exe` | Both passed: 43 modules fresh, assets byte-identical, engine and workflow self-tests, correct start page |
| Both EXEs together (`tools/smoke_workflow_pair.py build101`) | Passed |

## 12 October 2026: question paper and Model Answer fetched from the portal

**Fetch from portal** on the workflow page reads the selected test's own question
paper and Model Answer files through the evaluator's Chrome login (the supplied
`match_paper` test-details request, keyed by the sheet's test ID), merges several
Model Answer files in order, keeps the PDFs in memory for 30 minutes bound to the
signed-in account, and lets Start use them in place of uploads. An uploaded file
always wins over a fetched one; a portal test named differently from the sheet
stops the fetch; a fetch and a run cannot hold the portal at the same time.

| Check | Result |
| --- | --- |
| Workflow, adapter and vendored regression suite | 255 passed (5 new fetch/start tests, 4 new adapter and document tests) |
| `tools/smoke_workflow_exam_documents.py` (Chrome, offline fixtures) | Passed: fetch progress, requirements dropped for fetched documents, start sends the fetch id without file parts, fallback to uploads, cancel on test change |
| `tools/smoke_workflow_ui.py`, `tools/smoke_workflow_connected.py` | Passed with the new controls present |
| Launcher and routing regression scripts | All checks passed |

Not tested live: the portal's test-details response and file addresses are known
only from the supplier's code; the first real fetch should be checked against the
uploaded PDFs before a run uses it.

### Build 100 (`2026.10.12.100-portal-exam-documents`)

`dist/AICheckerClient-build100.exe` and `dist/AICheckerWorkflow-build100.exe`, built
from the shared tree after `tools/selftest.py` passed 152 of 152 suites. Two other
sessions edited files while the first PyInstaller run was in progress; the smoke's
bundled-module check caught the stale client EXE (`server`), which was rebuilt from
the current tree, and the workflow EXE was verified by hand to carry the late edits.
`tools/smoke_release.py` now also checks `objective_confirm`, `key_rubric` and
`vidyapeeth_intake` for freshness and every file under `assets/` byte for byte.

| Check | Result |
| --- | --- |
| Release smoke, client EXE | Passed: home page, 43 modules fresh, assets fresh, engine and workflow self-tests |
| Release smoke, workflow EXE | Passed: opens `/workflow.html`, 43 modules fresh, assets fresh, engine and workflow self-tests |
| Both EXEs together | Passed: the workflow EXE reuses the running checker and opens the workflow page |

## 9 October 2026: Data_Filler v0.9.10 engine and rejected-copies flow

The portal engine was replaced by the supplied Data_Filler v0.9.10 sources
(`portal_vendor/`), with the four identity guards re-applied, and copies the
checker rejects now have their own stage with an operator-triggered portal
submission (original PDF, reason as comment, total marks 0). Source only; the EXE
was not rebuilt.

| Check | Result |
| --- | --- |
| Workflow/API, sheet-tab, Google OAuth, large-sheet, portal-adapter, storage-read and vendored-engine regression suite | 245 passed (6 new rejected-copy tests, 2 new adapter tests, 90 supplier tests) |
| Vendored engine versus the supplied v0.9.10 file | Only the documented guard hunks differ (54 lines) |
| Portal helpers against offline HTML in Chrome (`smoke_workflow_portal.py`) | 7 scenarios passed, zero network requests |
| Rejection flow against an isolated local page in Chrome (`smoke_workflow_rejection.py`) | Passed: required-field validation, zero total, delayed spinner search, missed-toast upload recovery, no duplicate upload |
| Workflow interface in desktop and mobile Chrome (`smoke_workflow_ui.py`) | Passed with the new rejected-copies button present |
| Connected Chrome and real local FastAPI workflow (`smoke_workflow_connected.py`) | Passed: assign, download, grade, upload, durable run, resume, CSV import |
| Workflow startup launcher and grader routing regression scripts | All checks passed |
| Python compilation | Passed |

Observed while testing: running the regression suite with a deep `--basetemp`
path (the session scratch directory) failed 15 unrelated saved-run tests because
the hashed artifact paths exceeded the Windows path limit; the same tests pass
with the default temporary directory or a short `--basetemp`.

### 10 October 2026: packaged EXE (build `2026.10.10.98-portal-workflow-exe`)

`dist/AICheckerClient-build98.exe` carries the integrated workflow with nothing to
install: the spec now bundles the Playwright driver explicitly (`collect_all`), and
the engine falls back to Microsoft Edge when Google Chrome is absent (verified from
source over CDP: launch flags, connection, navigation, fill and evaluate; Edge only
rejects Playwright's `set_content`, which the engine never uses). A new frozen
check, `AICHECKER_WORKFLOW_SELFTEST=1`, imports the vendored engine, locates the
bundled driver and launches the detected browser headless on a data: page;
`tools/smoke_release.py` runs it and asserts the result.

| Check | Result |
| --- | --- |
| `python tools/selftest.py` (every offline suite) | 146 suites passed |
| `tools/smoke_release.py` on the staged build 98 EXE | Passed: health, bundled UI assets, engine self-test, workflow self-test (driver ok, Chrome 154 launched), optional page-range forms |
| Edge fallback (Chrome paths hidden, from source) | Passed: Edge 155 found, launched, page read |
| `dist/AICheckerWorkflow-build98.exe` (same spec, entry `run_workflow.py`) on the staged EXE | Passed: same build id, announces and opens `/workflow.html`, engine and workflow self-tests, 40 bundled modules fresh |
| Both EXEs together (checker running, workflow EXE opened second) | Passed: the second reports "already running at .../workflow.html" and opens that page instead of starting a second server |
| macOS | Not supported by the packaged app (Windows-only build and browser paths) |

Still required live: the v0.9.10 evaluator control detection, the one-student
assignment and marks upload, and the rejection submission have only been run
against fixtures. The rejection comment is the checker's reason text, so a live
trial should review that wording on the portal before copies are submitted in bulk.

## 7 October 2026

Updated on 7 October 2026, build `2026.10.07.89-workflow-large-sheets`.
The large-sheet regressions, connected browser/backend fixture, desktop/mobile UI
and Sheets-access checks were completed on 7 October. The earlier live CloudFront
download and offline portal helper checks were completed on 6 October.

## Results

| Check | Result |
| --- | --- |
| Workflow/API, sheet-tab, Google OAuth, large-sheet, portal-adapter and storage-read regression suite | 147 passed |
| Workflow interface in desktop and mobile Chrome | Passed: selected tab, source/tab reset, start and progress |
| Workflow page sign-in in offline Chrome | 7 scenarios passed, no external requests |
| Google Sheets access in offline Chrome | 7 scenarios passed: token handling, file imports, denied access, expiry and stale responses |
| Connected Chrome and real local FastAPI workflow | Passed: metadata-only tab discovery, sparse selected-tab import and complete fixture pipeline |
| Portal helpers against offline HTML in Chrome | 7 scenarios passed, zero network requests |
| Existing grader routing and job-access regression script | All checks passed |
| Workflow startup launcher regression script | All checks passed |
| Download of one supplied CloudFront copy | Passed: readable PDF, 7 pages, 3,765,418 bytes |
| Python compilation and JavaScript syntax | Passed |
| Restarted local service on port 8767 | Health reports build 89; updated page/client served with no-store caching |

The connected browser test uses the actual workflow JavaScript, FastAPI routes,
owner-bound import cache, background workflow, PDF validation and persistent
files. Its synthetic workbook contains an unrelated tab with 9 million cells and
a selected tab with 1 million allocated rows. Only rows 2 and 502 contain copies.
Loading tabs requests metadata alone. Reading the selected tab makes one compact
extent lookup and three grid requests: the header and two bounded row windows.
The fixture rejects requests for another tab and unexpected or unbounded grid
ranges, so the test cannot silently fall back to downloading the whole workbook
or scanning every allocated blank row.

The test selects one copy and verifies assignment, PDF download, grading and
upload in that order. It then verifies a durable completed run, downloading the
checked PDF, resuming without repeating completed actions, reload, CSV import and
discarding a delayed Google callback after sign-out. Google identity and Sheets
responses, remote source PDF bytes, AI grading and portal actions are fixtures.
No live Google or portal request occurs in this test.

Separate API tests import CSV, submit multipart exam PDFs and exercise the
background workflow. A grading-queue test uses a paper-bound marks proof while
simulating the provider result. The earlier supplied CloudFront PDF was downloaded
live and verified as a readable 7-page, 3,765,418-byte PDF; that check did not grade
or upload a student's copy.

The sign-in browser fixture verifies new and saved credentials, refused and
expired credentials, missing OAuth configuration, reauthentication and stale
saved-run responses after sign-out. The Sheets access fixture verifies that the
read-only Sheets token stays separate from the checker session, is not persisted
or sent with file imports, and cannot be restored by a stale callback. It also
checks declined access, popup cancellation, Sheets-token expiry and source changes
while a tab request is still pending.

The portal browser fixture exercises real locator helpers, evaluator selection,
assignment Submit, file chooser upload, View Upload, question marks, total, comment
and evaluation Submit. It verifies that Q.1 does not accidentally fill Q.10. The
browser is offline and all external requests are blocked. Its DOM is a fixture,
so these results do not establish compatibility with the current live portal DOM.

## Problems found and fixed

- Wrong evaluator selected by email substring, and similarly named tests/categories
  selected by substring. Complete identities are now required.
- Stale student searches accepted without verifying the student ID. Missing or
  mismatching identity now stops before submission.
- A concurrent assignment change incorrectly treated as successful assignment.
- Mark precision altered before upload, and valid question-ID spellings rejected.
- Submission filenames overwriting shared exam documents, plus Windows filename
  collisions and reserved names. Artifacts now use separate folders and hashed names.
- Duplicate saved workflows and ambiguous multiple submissions for one student.
- Storage faults leaving the workflow permanently busy, and temporary Windows file
  locks breaking snapshot replacement. Active state is always cleared and transient
  locks are retried; snapshots are flushed before publication.
- Unmappable/missing checked output repeating AI grading on resume. Such results
  are now held for review, with successful grading retained.
- Stale errors after resume and missing marks/completion after upload reconciliation.
- Old polling responses displaying a previously selected run.
- Workflow requiring navigation to the checker to sign in. Google sign-in is now
  on the workflow page, with server-side account validation, sign-out and expiry.
- Excel imports silently choosing the active tab. The operator can choose an exact
  worksheet name from the loaded workbook before importing copies.
- Delayed requests restoring a prior account's data after sign-out. Responses from
  an earlier sign-in session are discarded.
- Loading tab names downloading all cell data and failing at 50 MB. Connected
  Google Sheets now load worksheet metadata first and read only the chosen tab's
  recognized workflow columns.
- Large numbers of allocated blank rows being treated as imported copies. A
  compact selected-column lookup finds populated rows, and only their bounded
  grid windows are read. Actual imported rows and cells determine the limits.
- Repeated recognized columns in connected Google Sheet imports containing
  matching IDs being rejected. Matching or empty aliases are combined;
  conflicting identity values still stop the import.
- Partial imports surviving failed Google requests. Rows are cached only after
  the entire selected-tab import succeeds, and temporary Google failures receive
  bounded retries with actionable errors.
- Brief Windows file locks breaking saved-run progress reads. Transient sharing
  violations now receive bounded retries; missing and corrupt state is still
  reported.

## Reproduce

From the AI_Checker source directory:

```powershell
python -m pytest tests/test_workflow_sheet_tabs.py tests/test_integrated_workflow.py tests/test_workflow_portal_adapter.py tests/test_workflow_google_oauth.py tests/test_workflow_large_google_sheet.py tests/test_workflow_storage_read.py -q --basetemp=.workflow-test-local
python tools/smoke_workflow_connected.py
python tools/smoke_workflow_ui.py
python tools/smoke_workflow_auth.py
python tools/smoke_workflow_sheet_access.py
python tools/smoke_workflow_portal.py
python tools/smoke_workflow_rejection.py
python tools/smoke_workflow_exam_documents.py
python tools/smoke_workflow_pair.py build101
python tests/test_routing_and_jobs.py
python tests/test_launcher.py
```

Chrome browser tests write their evidence into ignored `.workflow-test-*`
directories. The connected test writes `connected-workflow-complete.png` and
`connected-workflow-report.json` into `.workflow-test-connected`; the report
includes each fixture Google request and the extent/grid request counts. The
regression suite uses local temporary files. No real assignment or portal upload
was performed. Existing PyMuPDF deprecation warnings and a requests dependency
warning appeared; the checks completed successfully.

## Live validation still required

A live trial needs authorized admin/evaluator logins, a selected submission and
its exact question paper and marking scheme. It must establish which student IDs
the current portal exposes and whether its evaluation form requires competence
ratings. AI marking accuracy for that real exam also needs review. Private Google
Sheets can now be imported through read-only Google authorization; CSV/Excel
upload remains available. The latest code still needs a retest against the user's
live sheet in an authorized Google session. The large-sheet browser results above
use explicit fixtures and do not establish that live Google access, real AI
grading or live portal submission has completed. The existing packaged EXE was
not rebuilt during this test.
