"""Approval Management > Review: approve copies with grade A.

Vendored from the supplied Paper Uploader (review_copies.py, the instruction video of
2026-11-04); the steps per copy are the supplier's. One difference, and it is deliberate: the
supplied tool approves EVERY copy in a program's Pending list, whoever evaluated it, while a
workflow run approves only the copies it uploaded itself (operator, 12 Nov 2026). `wanted`
carries that choice: the student ids this run is allowed to approve, or None for all of them.

Marks and comments are never touched here. The pop-up's own status and grade lists are set,
read back, and only then submitted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import data_filler as engine
from . import match_paper

GRADE = "A"
STATUS = "approved"
REVIEW_PAGE_URL = engine.BASE_URL + "tev/approval-management/review"
REVIEW_TASKS_URL = (match_paper.BACKEND + "tests/tasks/review?type=pending&programId={program}"
                    "&search=&page={page}&limit={limit}")
TASKS_PER_PAGE = 100
REVIEW_SUCCESS = "text=/reviewed successfully/i"
PROGRAM_IN_URL = re.compile(r"/review/([0-9a-f]{24})")
# Required fields the site would refuse on, named the way the page names them.
INVALID_FIELDS_JS = """button => {
    const form = button.form || button.closest('form');
    if (!form || form.noValidate) return [];
    return [...form.elements].filter(el => el.willValidate && !el.checkValidity())
        .map(el => el.name || el.id || el.getAttribute('aria-label') || el.tagName.toLowerCase());
}"""


class SubmissionUnverified(RuntimeError):
    """Submit may have reached the site; never repeat it automatically."""


@dataclass
class ReviewCopy:
    """One row of the site's Pending review list."""
    task_id: str = ""
    student_id: str = ""
    student_name: str = ""
    test_name: str = ""
    category: str = ""
    test_id: str = ""

    @property
    def label(self):
        return f"{self.student_name} | {self.student_id}".strip(" |")


def _text(value):
    return " ".join(str(value or "").split())


def copy_from_task(task):
    """One Pending row, or None when it has no Student ID or test name."""
    student = task.get("studentObj") or {}
    student_id = _text(student.get("studentId") or student.get("_id") or student.get("id"))
    test = task.get("testDetails") or {}
    test_name = _text(test.get("name"))
    if not student_id or not test_name:
        return None
    category = task.get("categoryDetails") or {}
    return ReviewCopy(
        task_id=_text(task.get("_id")),
        student_id=student_id,
        student_name=_text(student.get("firstName") or student.get("name")),
        test_name=test_name,
        category=_text(category.get("name") if isinstance(category, dict) else category),
        test_id=_text(test.get("_id") or task.get("testId")),
    )


def pending_copies(page, program_id, wanted=None):
    """The program's Pending review list, newest page first. `wanted`: student ids to keep."""
    keep = None if wanted is None else {str(value).strip() for value in wanted if str(value).strip()}
    found, seen, total, number = [], 0, 0, 1
    while True:
        if engine.should_stop():
            raise engine.AbortRun("Stopped by you")
        answer = match_paper.api_get(page, REVIEW_TASKS_URL.format(
            program=program_id, page=number, limit=TASKS_PER_PAGE))
        data = answer.get("data") or {}
        tasks = data.get("data") if isinstance(data, dict) else data
        tasks = tasks or []
        total = ((data.get("paginate") or {}).get("totalCount") if isinstance(data, dict) else 0) or total
        seen += len(tasks)
        for task in tasks:
            copy = copy_from_task(task)
            if copy is not None and (keep is None or copy.student_id in keep):
                found.append(copy)
        engine.on_status(f"Reading the Review > Pending list: {seen} of {total} copies")
        if not tasks or seen >= total:
            return found
        number += 1


def open_review(page, program):
    """Approval Management > Review > the program > Pending. Returns the program's id."""
    page.goto(REVIEW_PAGE_URL, wait_until="domcontentloaded")
    engine.wait_heading(page, "Programs")
    engine.search(page, program)
    engine.open_row(engine.row_with_cell(page, program))
    engine.wait_heading(page, "Review")
    page.get_by_text("Pending", exact=True).first.click()
    engine.wait_list_settled(page)
    found = PROGRAM_IN_URL.search(page.url)
    if not found:
        raise RuntimeError("Could not see which program is open on the Review page.")
    engine.on_status(f"Opened Approval Management > Review > {program} (Pending)")
    return found.group(1)


def find_row(page, copy):
    """The copy's row: search by Student ID, then by name, keeping this test and category."""
    for term in dict.fromkeys(value for value in (copy.student_id, copy.student_name) if value):
        engine.search(page, term)
        rows = page.locator(engine.SEL["data_rows"])
        if copy.test_name:
            rows = rows.filter(has_text=engine.loose(copy.test_name))
        if copy.category:
            rows = rows.filter(has_text=engine.loose(copy.category))
        if copy.student_name:
            rows = rows.filter(has=page.locator("td", has_text=engine.exact(copy.student_name)))
        count = rows.count()
        if count == 1:
            return rows.first
        if count > 1:
            raise LookupError(f"{count} rows of this test match '{term}'; review this copy by hand")
    raise LookupError("Not in the Review > Pending list any more (already reviewed?)")


def _popup(page):
    popup = page.locator(engine.SEL["modal"]).filter(has_text=engine.SEL["modal_title"]).last
    try:
        popup.wait_for(state="visible", timeout=engine.DEFAULT_TIMEOUT_MS)
    except engine.PWTimeout:
        return page.locator("body")
    return popup


def _close_popup(page, popup):
    try:
        if not engine.click_if_visible(popup, engine.SEL["modal_close"], timeout=2_000):
            page.keyboard.press("Escape")
    except engine.PWError:
        pass


def approve_copy(page, copy, before_submit=None):
    """Open the copy, choose Approved + grade A and Submit. Marks and comments are unchanged."""
    stage, popup = "find copy", None
    try:
        row = find_row(page, copy)
        stage = "open View Upload"
        engine.click_menu_item(page, row, "View Upload")
        popup = _popup(page)

        stage = "choose Approved"
        status = popup.locator('select[name="status"]')
        status.first.wait_for(state="visible", timeout=engine.DEFAULT_TIMEOUT_MS)
        if status.count() != 1:
            raise LookupError("The pop-up does not have exactly one status list (Approved / Rejected)")
        status.select_option(STATUS)

        stage = "choose grade"
        grade = popup.locator('select[name="grade"]')
        grade.first.wait_for(state="visible", timeout=engine.DEFAULT_TIMEOUT_MS)
        if grade.count() != 1:
            raise LookupError("The pop-up does not have exactly one Evaluator Grading list")
        grade.select_option(GRADE)
        if status.input_value() != STATUS or grade.input_value() != GRADE:
            raise RuntimeError(f"The site did not keep 'Approved' / grade {GRADE}")

        stage = "Submit"
        button = popup.get_by_role("button", name="Submit", exact=True)
        if button.count() != 1:
            raise LookupError("The pop-up does not have exactly one Submit button")
        invalid = button.first.evaluate(INVALID_FIELDS_JS)
        if invalid:
            raise RuntimeError("The site wants more filled in before Submit: " + ", ".join(invalid))
        if page.locator(REVIEW_SUCCESS).first.is_visible():
            raise RuntimeError("The previous 'Reviewed successfully' message did not go away; not submitted")
        if callable(before_submit):
            before_submit(f"Approved, grade {GRADE} for {copy.label}")
        if engine.should_stop():
            raise engine.AbortRun("Stopped by you")
        button.first.click()
        try:
            page.locator(REVIEW_SUCCESS).first.wait_for(state="visible", timeout=engine.DEFAULT_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - the click may still have reached the site
            raise SubmissionUnverified(
                "Submit was clicked but 'Reviewed successfully' did not appear. Check this copy on the "
                f"site; it will not be submitted again. {engine.first_line(exc)}") from exc
        return f"Approved, grade {GRADE}: {copy.label}"
    except (engine.AbortRun, engine.SkippedByUser, SubmissionUnverified, LookupError):
        raise
    except Exception as exc:  # noqa: BLE001 - say which step failed
        raise RuntimeError(f"[{stage}] {engine.first_line(exc)}") from exc
    finally:
        if popup is not None:
            _close_popup(page, popup)
