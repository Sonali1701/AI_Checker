# The notice page for a copy whose file cannot be read

A copy whose file the checker could not open used to be skipped with the reason. That left it
**pending on the portal for good**: no one evaluated it, nobody noticed it later, and the student
was never told why. From 20 Nov 2026 the run posts a one-page notice in its place.

## What the student gets

One A4 page, in English, saying what the file was and what to upload again. It is
posted like any rejection: the evaluator account submits it with **total marks 0** and a short
reason in the comment box. Nothing is said about the answers, which were never read, and no mark
is ever described as earned.

```
This answer sheet could not be evaluated

Dear Aarav Sharma,

The PDF uploaded for this answer sheet is protected by a password, so it could not be opened.

Please upload the same answer sheet again as a PDF with no password on it.

Regards
PW Evaluation Team

Student: Aarav Sharma · Student ID: 23217614 · Test: ... · Submission: ... · Date: 20 Nov 2026
What the file was: Downloaded file is not a readable PDF: the PDF is password protected
```

The last two lines are for whoever opens the copy on the portal. English only, by the
operator's decision of 23 Nov 2026.

## Which files get a notice

| What the portal served | Notice | Why |
|---|---|---|
| A PDF with a password on it | yes | Another download cannot open it either |
| A file with no pages, or 0 bytes | yes | The upload did not finish |
| A JPEG or PNG that cannot be opened | yes | The photo itself is broken |
| Anything that is not a PDF or a photo (Word, zip, video) | yes | Only PDF, JPEG, PNG and HEIC are read |
| HTTP 403 or 404, or a sign-in page instead of the file | **no** | May be an expired link, so the copy stays skipped and Resume fetches it again |
| More than 100 pages | **no** | Skipped unread for a person to open. The file may be perfectly readable |

Nothing is ever posted in a student's name over a fault that may pass. `worth_a_notice` in
`unreadable_notice.py` holds that line.

## Turning it off

Step 1 has a checkbox, on by default: *Post a notice page for a copy whose file cannot be read*.
Turned off, such a copy is skipped exactly as before and nothing is posted. The run option is
`notice_for_unreadable`, and the Autopilot takes the default, so sheet runs post notices too.
A run with automatic rejection posting turned off keeps the notice ready and posts nothing until
the operator does.

## On the sheet

The copy still reads **not downloadable file**, which it did before and which is already a
terminal status, so nothing downstream changes. The row's note names the reason and says a notice
was posted, and the report CSV carries both.

## Where it is in the code

| Piece | Where |
|---|---|
| The page, the reasons and the comments | `unreadable_notice.py` |
| Which refusals are worth a notice | `unreadable_notice.worth_a_notice` |
| Marking the copy, writing the page | `WorkflowStore.mark_notice`, `notice_path` in `integrated_workflow.py` |
| Posting it in place of the original | `WorkflowStore.process_rejection` |
| The sheet word | `workflow_sheet_status.status_for` |
| The checkbox | `noticeForUnreadable` in `static/workflow.html`, `static/workflow.js` |
| Tests | `tests/test_unreadable_notice_1120.py` (20) |

## iPhone photos are converted, not refused

iOS uploads HEIC from the camera roll unless the student has changed the camera setting, so
until 23 Nov 2026 those copies were refused as "not a PDF or a photo" although the student had
done nothing wrong. A HEIC, HEIF or AVIF file is now converted to a one-page PDF like a JPEG,
by `is_heif_photo` and `heif_to_pdf` in `integrated_workflow.py`, which need `pillow-heif`
(in `requirements.txt`, and named in the PyInstaller spec so the EXE carries it). The photo is
saved as JPEG at quality 92 on the way in: a 12 MP page is about 30 MB lossless and a ten-page
copy of those would not fit back through the portal. Video (`ftypisom`, `ftypmp42`) is left
out of the family on purpose and is still refused. A build without the library says so in its
own words, and that copy gets the photo notice rather than being told it uploaded a file that
is not a photo. Tests: `tests/test_heic_copy_1123.py` (22).

## Not yet done

No notice has been posted to the live portal yet. The posting path is the rejection path, which
is live-proven, but the page itself and the 0-mark submission for this reason have only been seen
against the fixture portal.
