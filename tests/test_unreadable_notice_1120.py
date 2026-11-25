# -*- coding: utf-8 -*-
"""Operator's request of 20 Nov 2026: a copy whose file cannot be read was skipped, which left it
pending on the portal for good and told the student nothing. A one-page notice now goes up in its
place, in English and Hindi, with 0 marks and the reason as the comment. Offline fixtures.
"""
from __future__ import annotations

import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import unreadable_notice as notice
from test_workflow_copy_retries import WorkflowFixture

ROW = {"student_name": "Aarav Sharma", "student_id": "23217614", "submission_id": "copy1",
       "test_name": "Complete Subjective Test 01 (Maths)"}
PASSWORD_MESSAGE = "Downloaded file is not a readable PDF: the PDF is password protected"


def page_text(data):
    """The page's text as one line, with ligatures opened out: the shaper writes "file" as one
    ﬁ glyph, and the box wraps a sentence wherever it runs out of width."""
    import re
    import unicodedata
    with fitz.open(stream=data, filetype="pdf") as document:
        assert document.page_count == 1
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", document[0].get_text()))


def devanagari(text):
    return sum(1 for ch in text if "ऀ" <= ch <= "ॿ")


def encrypted_pdf():
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "Answer sheet")
        return document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")


def refusal(data, content_type=""):
    """What the download says about a file it cannot use."""
    with pytest.raises(workflow.UnreadableCopy) as caught:
        workflow.readable_copy(data, content_type)
    return str(caught.value)


# ------------------------------------------------------------------ which notice fits

def test_every_file_the_download_refuses_gets_the_notice_that_fits_it():
    assert notice.reason_code(refusal(encrypted_pdf())) == notice.PASSWORD
    assert notice.reason_code(refusal(b"")) == notice.EMPTY
    assert notice.reason_code(refusal(b"\xff\xd8\xff not really a jpeg")) == notice.PHOTO
    assert notice.reason_code(refusal(b"<html><body>Sign in to continue</body></html>")) == notice.GONE
    assert notice.reason_code(refusal(b"PK\x03\x04 a docx, not an answer sheet")) == notice.WRONG_TYPE
    # These two the download words itself, away from readable_copy.
    assert notice.reason_code("Downloaded file is not a readable PDF: the PDF has no pages") == notice.EMPTY
    assert notice.reason_code("The copy file is no longer downloadable (HTTP 403)") == notice.GONE


def test_an_unknown_reason_still_asks_for_a_pdf():
    assert notice.reason_code("something nobody has seen yet") == notice.WRONG_TYPE
    assert notice.reason_code("") == notice.WRONG_TYPE
    assert "upload the answer sheet as a PDF" in notice.notice_comment("")


# ------------------------------------------------------------------ the page itself

def test_the_page_tells_the_student_what_happened_and_what_to_upload_again():
    text = page_text(notice.notice_pdf(ROW, {"subject": "Maths"}, PASSWORD_MESSAGE))
    assert "Aarav Sharma" in text and "PW Evaluation Team" in text and "Regards" in text
    assert "protected by a password" in text and "with no password on it" in text
    assert devanagari(text) == 0                      # English only (operator, 23 Nov 2026)
    assert "23217614" in text and "copy1" in text     # the trace line for whoever opens it
    assert PASSWORD_MESSAGE.split(": ")[1] in text    # what the file was, for the evaluator


def test_the_page_never_speaks_about_marks_or_the_answers():
    for message in (PASSWORD_MESSAGE, "The copy file is no longer downloadable (HTTP 404)",
                    "Downloaded file is not a readable PDF: the portal served an empty file"):
        text = page_text(notice.notice_pdf(ROW, {}, message)).lower()
        assert "mark" not in text and "score" not in text and "wrong" not in text


def test_a_copy_with_no_student_name_still_gets_a_page():
    text = page_text(notice.notice_pdf({"submission_id": "copy9"}, {}, PASSWORD_MESSAGE))
    assert "Dear Student," in text and "copy9" in text


def test_a_name_with_html_in_it_is_printed_and_not_obeyed():
    text = page_text(notice.notice_pdf({**ROW, "student_name": "<b>Ravi</b>"}, {}, PASSWORD_MESSAGE))
    assert "Ravi" in text and "b>" in text


@pytest.mark.parametrize("code", sorted(notice.REASONS))
def test_each_reason_has_a_page_a_short_comment_and_student_wording(code):
    what, todo = notice.REASONS[code]
    for line in (what, todo):
        # Student-facing wording: plain sentences, no semicolons and no dashes.
        assert ";" not in line and " - " not in line and "—" not in line and "–" not in line
        assert devanagari(line) == 0                  # English only (operator, 23 Nov 2026)
    comment = notice.COMMENTS[code]
    assert 0 < len(comment) <= 200 and comment.endswith(".") and "Not evaluated" in comment


# ------------------------------------------------------------------ in a run

def recording_portal(posts):
    class Portal:
        def __init__(self, root, **kwargs):
            pass

        def perform(self, stage, row, options, *args, before_submit=None, **kwargs):
            posts.append((stage, row["submission_id"], args[0] if args else None))
            if before_submit:
                before_submit()

        def close(self):
            pass

    return Portal


def run_with_an_unreadable_first_copy(tmp_path, message=PASSWORD_MESSAGE, notices=True):
    fixture = WorkflowFixture(tmp_path)
    posts = []
    fixture.store.portal_factory = recording_portal(posts)

    def unreadable(stage, student, before_submit):
        if stage == "download" and student == "copy0":
            raise workflow.UnreadableCopy(message)

    fixture.hook = unreadable
    run = fixture.create()
    if not notices:
        run["options"]["notice_for_unreadable"] = False
        fixture.store.save(run)
    return fixture, posts, fixture.work(run)


def test_an_unreadable_copy_is_posted_as_a_notice_with_no_marks(tmp_path):
    fixture, posts, saved = run_with_an_unreadable_first_copy(tmp_path)
    row = saved["rows"][0]
    assert row["stage"] == "rejection_submitted" and row["awarded_marks"] == 0
    assert row["rejection_code"] == "FILE_NOT_READABLE" and not row["error"]
    assert "password protected" in row["note"] and "notice page is posted" in row["note"]
    assert "with no password" in row["rejection_reason"]
    posted = [path for stage, student, path in posts if stage == "reject" and student == "copy0"]
    assert len(posted) == 1
    assert "protected by a password" in page_text(open(posted[0], "rb").read())
    assert fixture.calls["grade", "copy0"] == 0          # nothing was checked, so nothing was paid for
    assert saved["rows"][1]["stage"] == "uploaded"       # the rest of the run goes on


def test_the_notice_names_what_the_portal_served(tmp_path):
    _, posts, saved = run_with_an_unreadable_first_copy(
        tmp_path, "Downloaded file is not a readable PDF: the portal served an empty file")
    assert "has no pages in it" in saved["rows"][0]["rejection_reason"] or \
           "no pages" in saved["rows"][0]["rejection_reason"]
    posted = next(path for stage, student, path in posts if stage == "reject")
    assert "upload did not finish" in page_text(open(posted, "rb").read())


def test_a_file_that_may_come_back_is_left_for_another_try(tmp_path):
    """A missing file or a sign-in page may be an expired link, so nothing is posted in the
    student's name: the copy stays skipped and Resume or a re-run fetches it again."""
    assert notice.worth_a_notice(PASSWORD_MESSAGE) is True
    assert notice.worth_a_notice("The copy file is no longer downloadable (HTTP 403)") is False
    assert notice.worth_a_notice("Downloaded file is not a readable PDF: the portal served a web page "
                                 "instead of the file, usually an expired or wrong link") is False
    _, posts, saved = run_with_an_unreadable_first_copy(tmp_path, "The copy file is no longer downloadable (HTTP 403)")
    assert saved["rows"][0]["stage"] == "skipped"
    assert not [stage for stage, student, _ in posts if stage == "reject" and student == "copy0"]


def test_the_sheet_still_says_why_no_one_evaluated_the_copy(tmp_path):
    import workflow_sheet_status as sheet
    _, _, saved = run_with_an_unreadable_first_copy(tmp_path)
    assert sheet.status_for(saved["rows"][0]) == "not downloadable file"
    assert sheet.status_for({"stage": "rejection_submitted"}) == "rejected"
    assert "not downloadable file" in sheet.TERMINAL_STATUSES


def test_with_the_option_off_the_copy_is_skipped_and_nothing_is_posted(tmp_path):
    _, posts, saved = run_with_an_unreadable_first_copy(tmp_path, notices=False)
    row = saved["rows"][0]
    assert row["stage"] == "skipped" and "Skipped:" in row["note"]
    assert not [stage for stage, student, _ in posts if stage == "reject" and student == "copy0"]


def test_the_notice_file_is_kept_inside_the_run_folder(tmp_path):
    fixture = WorkflowFixture(tmp_path)
    run = fixture.create()
    path = fixture.store.notice_path(run, "copy0")
    assert path.is_relative_to(fixture.store.folder(run["id"])) and path.name.endswith(".notice.pdf")
    source, checked, report = fixture.store.copy_paths(run, "copy0")
    assert path not in (source, checked, report)


def test_the_option_is_on_unless_it_is_turned_off():
    assert workflow.notice_for_unreadable({}) is True
    assert workflow.notice_for_unreadable({"notice_for_unreadable": True}) is True
    assert workflow.notice_for_unreadable({"notice_for_unreadable": False}) is False
    assert workflow.notice_for_unreadable(None) is True


def test_the_page_offers_the_choice():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "static", "workflow.html"), encoding="utf-8").read()
    script = open(os.path.join(root, "static", "workflow.js"), encoding="utf-8").read()
    assert 'id="noticeForUnreadable"' in page and "notice page" in page
    assert 'notice_for_unreadable: el("noticeForUnreadable").checked' in script
