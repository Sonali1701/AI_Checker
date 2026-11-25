# -*- coding: utf-8 -*-
"""The page posted for a copy whose file the checker could not read (operator, 20 Nov 2026).

Such a copy was skipped with the reason, which left it pending on the portal for good: no
one evaluated it and the student was never told why. In its place the run now posts a
one-page notice in English, with 0 marks and a short reason as the comment, so the copy
leaves the pending list and the student knows what to upload again.

The page says only what the portal's file was and what to do about it. Nothing is said about
the answers, which were never read, and no mark is ever described as earned.

English only, by the operator's decision of 23 Nov 2026.
"""
from __future__ import annotations

import html
from datetime import date

PASSWORD = "password"
EMPTY = "empty"
PHOTO = "photo"
GONE = "gone"
WRONG_TYPE = "wrong_type"

# code -> (what happened, what to do). Student-facing wording: plain sentences, no
# semicolons and no dashes.
REASONS = {
    PASSWORD: (
        "The PDF uploaded for this answer sheet is protected by a password, so it could not be opened.",
        "Please upload the same answer sheet again as a PDF with no password on it.",
    ),
    EMPTY: (
        "The file uploaded for this answer sheet has no pages in it, so the upload did not finish.",
        "Please upload the answer sheet again and wait until the portal shows the upload as complete.",
    ),
    PHOTO: (
        "The photo uploaded for this answer sheet could not be opened.",
        "Please upload one PDF of the full answer sheet, or clear photos of every page.",
    ),
    GONE: (
        "The file uploaded for this answer sheet is no longer available at its place on the portal.",
        "Please upload the answer sheet again.",
    ),
    WRONG_TYPE: (
        "The file uploaded for this answer sheet is not a PDF or a photo, so it could not be opened.",
        "Please upload the answer sheet as one PDF, or as clear photos of every page.",
    ),
}

# What goes in the portal's comment box. Short: the site has its own maximum and the vendor
# code refuses to shorten a comment itself (portal_vendor.rejection_comments.fill_comment).
COMMENTS = {
    PASSWORD: "Not evaluated. The uploaded PDF is password protected. Please upload it again with no password.",
    EMPTY: "Not evaluated. The uploaded file has no pages. Please upload the answer sheet again.",
    PHOTO: "Not evaluated. The uploaded photo could not be opened. Please upload a clear PDF or clear photos.",
    GONE: "Not evaluated. The uploaded file is no longer available on the portal. Please upload it again.",
    WRONG_TYPE: "Not evaluated. The uploaded file is not a PDF or a photo. Please upload the answer sheet as a PDF.",
}

TITLE = "This answer sheet could not be evaluated"
SIGN_OFF = "Regards"
TEAM = "PW Evaluation Team"


def reason_code(message: str) -> str:
    """Which notice fits an UnreadableCopy message. Its wording is ours, in
    integrated_workflow.readable_copy and the download, so this reads it back."""
    text = str(message or "").lower()
    if "password protected" in text:
        return PASSWORD
    if "no pages" in text or "empty file" in text:
        return EMPTY
    if "photo could not be read" in text or "heic photo" in text:
        return PHOTO
    if "web page instead of the file" in text or "no longer downloadable" in text:
        return GONE
    return WRONG_TYPE


# A file that is missing, or served as a sign-in page, may well be there on the next try: the
# link had expired, or the portal was between deployments. Those copies stay skipped, so Resume
# and a re-run fetch them again, and nothing is posted in a student's name over a passing fault.
RETRYABLE = {GONE}


def worth_a_notice(message: str) -> bool:
    """Whether this refusal is about the file itself, which another download cannot mend."""
    return reason_code(message) not in RETRYABLE


def notice_comment(message: str) -> str:
    """The portal comment for this copy."""
    return COMMENTS[reason_code(message)]


def _row_lines(row, options):
    """The identifying lines, for whoever opens the copy on the portal."""
    lines = []
    for label, value in (("Student", row.get("student_name")), ("Student ID", row.get("student_id")),
                         ("Test", row.get("test_name") or (options or {}).get("subject")),
                         ("Submission", row.get("submission_id")),
                         ("Date", date.today().strftime("%d %b %Y"))):
        value = str(value or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    return lines


def notice_pdf(row, options, message: str) -> bytes:
    """One A4 page for this copy, in English and Hindi, naming what to upload again."""
    import fitz

    code = reason_code(message)
    what, todo = REASONS[code]
    name = str(row.get("student_name") or "").strip()
    greeting = f"Dear {name}," if name else "Dear Student,"
    trace = " · ".join(_row_lines(row, options))
    body = "".join([
        f'<p class="t">{html.escape(TITLE)}</p>',
        f'<p class="g">{html.escape(greeting)}</p>',
        f"<p>{html.escape(what)}</p>",
        f"<p>{html.escape(todo)}</p>",
        f'<p class="g">{html.escape(SIGN_OFF)}<br>{html.escape(TEAM)}</p>',
        f'<p class="f">{html.escape(trace)}</p>',
        f'<p class="f">{html.escape("What the file was: " + str(message or "").strip())}</p>',
    ])
    css = ("* {font-family: sans-serif; font-size: 12.5px; line-height: 1.55; color: #1a1a2e;}"
           "p {margin: 0 0 11px 0;}"
           ".t {font-size: 19px; font-weight: bold; margin-bottom: 22px;}"
           ".g {margin-bottom: 14px;}"
           ".f {font-size: 9.5px; color: #666677; line-height: 1.4;}")
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(40, 40, 555, 802), color=(0.82, 0.82, 0.88), width=0.8)
    page.insert_htmlbox(fitz.Rect(62, 66, 533, 776), body, css=css, scale_low=0)
    data = document.tobytes(garbage=4, deflate=True, no_new_id=True)
    document.close()
    return data
