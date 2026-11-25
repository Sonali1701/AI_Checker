"""Pair rejected copies with TXT comments and automate their upload/submission.

Accept an evaluated ZIP, its extracted root, or the rejected folder itself.
Faculty-review notes and processing errors are deliberately excluded.
"""
from __future__ import annotations
# Vendored from Data_Filler v0.9.10; imports rewritten for the portal_vendor package.
# Integrated-workflow guard: submit_rejection(verify_row=...) checks the located row's
# student identity before any upload.

import csv
import re
import zipfile
from . import rejection_browser as browser
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class RejectionComment:
    student_id: str
    student_name: str
    comment: str
    source: str
    paper_path: Path | None = None


REASON_NAME = re.compile(
    r"^(?:(?P<name>.+)_)?(?P<id>[0-9a-f]{24})(?:\s*\(\d+\))?(?:_reason)?\.txt$",
    re.IGNORECASE,
)
MAX_REASON_BYTES = 1024 * 1024
STUDENT_ID = re.compile(r"(?<![0-9a-f])([0-9a-f]{24})(?![0-9a-f])", re.IGNORECASE)


def _record(name: str, data: bytes, source: str) -> RejectionComment:
    match = REASON_NAME.fullmatch(name)
    if not match:
        raise ValueError(f"Invalid rejection filename (expected Name_<Student ID>_reason.txt): {name}")
    comment = data.decode("utf-8-sig").strip()
    if not comment:
        raise ValueError(f"Empty rejection reason: {source}")
    return RejectionComment(match['id'].lower(), match['name'] or match['id'], comment, source)


def load_comments(source: str | Path) -> list[RejectionComment]:
    """Read exact reason text; reject ambiguous duplicate student IDs before any site work."""
    source = Path(source)
    records: list[RejectionComment] = []
    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if entry.is_dir() or path.parent.name.lower() != "rejected" or path.suffix.lower() != ".txt":
                    continue
                if entry.file_size > MAX_REASON_BYTES:
                    raise ValueError(f"Rejection reason is too large: {entry.filename}")
                records.append(_record(path.name, archive.read(entry), entry.filename))
    elif source.is_dir():
        folder = source / "rejected" if (source / "rejected").is_dir() else source
        if not folder.is_dir():
            raise ValueError("Choose the rejected folder, its evaluated parent folder, or the evaluated ZIP.")
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() == ".txt":
                if path.stat().st_size > MAX_REASON_BYTES:
                    raise ValueError(f"Rejection reason is too large: {path.name}")
                records.append(_record(path.name, path.read_bytes(), str(path)))
    else:
        raise ValueError(f"Choose an evaluated ZIP or a folder: {source}")
    if not records:
        raise ValueError("No rejection TXT files were found in the rejected folder.")
    seen: set[str] = set()
    for record in records:
        if record.student_id in seen:
            raise ValueError(f"More than one rejection reason file for student {record.student_id}; resolve before submitting.")
        seen.add(record.student_id)
    return sorted(records, key=lambda r: (r.student_name.casefold(), r.student_id))


def load_submissions(source: str | Path) -> list[RejectionComment]:
    """Use TXT reasons as the work list; ignore PDFs for all other students."""
    source = Path(source)
    if not source.is_dir():
        raise ValueError("Choose a folder containing the rejected PDFs and reason TXT files. Extract ZIPs first.")
    records = load_comments(source)
    reasons = {r.student_id for r in records}
    folder = source / "rejected" if (source / "rejected").is_dir() else source
    papers: dict[str, list[Path]] = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() == ".pdf":
            ids = STUDENT_ID.findall(path.stem)
            if not reasons.intersection(s.lower() for s in ids):
                continue
            if len(ids) != 1:
                raise ValueError(f"PDF filename must contain exactly one full Student ID: {path.name}")
            papers.setdefault(ids[0].lower(), []).append(path.resolve())
    paired = []
    for record in records:
        matches = sorted(papers.get(record.student_id, []), key=lambda p: (len(p.name), p.name.casefold(), p.name))
        if not matches:
            raise ValueError(f"Expected exactly one rejected PDF for {record.student_id}; found {len(matches)}.")
        if any(not _same_pdf(matches[0], other) for other in matches[1:]):
            raise ValueError(f"Different PDF contents for student {record.student_id}; choose the correct copy before submitting.")
        paired.append(replace(record, paper_path=matches[0]))
    return paired


def _same_pdf(first: Path, second: Path) -> bool:
    """Compare actual bytes so identical copies produce only one upload."""
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open('rb') as a, second.open('rb') as b:
        while True:
            block = a.read(1024 * 1024)
            if block != b.read(1024 * 1024):
                return False
            if not block:
                return True


def write_preview(records: list[RejectionComment], target: str | Path) -> Path:
    """Export a review list. This does not submit comments or create evaluation marks."""
    target = Path(target)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Student_ID", "Student_Name", "Comment", "Source"])
        for record in records:
            writer.writerow([record.student_id, record.student_name, record.comment, record.source])
    return target


def fill_comment(page, record: RejectionComment) -> None:
    """Fill only the comment, preserving any marks and ratings on the form."""
    boxes = page.locator('textarea:visible')
    if boxes.count() != 1:
        raise ValueError("The uploaded-paper form must contain exactly one visible comment box.")
    box = boxes.first
    existing = box.input_value().strip()
    if existing and existing != record.comment:
        raise ValueError("This comment box already contains different text; left unchanged for manual review.")
    limit = box.get_attribute('maxlength')
    if limit and int(limit) >= 0 and len(record.comment) > int(limit):
        raise ValueError("The rejection reason exceeds the site's comment limit; it was not truncated or entered.")
    box.fill(record.comment)
    if box.input_value().replace('\r\n', '\n') != record.comment.replace('\r\n', '\n'):
        raise ValueError("The site did not retain the complete rejection comment. Check it manually.")
    box.blur()


PROGRESS_FILE = Path("rejection_progress.csv")


class SubmissionUnverified(RuntimeError):
    """Submit may have reached the server; never repeat it automatically."""


def _progress() -> dict[tuple[str, ...], str]:
    if not PROGRESS_FILE.exists():
        return {}
    with PROGRESS_FILE.open(newline="", encoding="utf-8") as handle:
        return {tuple(r[k] for k in ("Program", "Series", "Test", "Student_ID")): r["Result"]
                for r in csv.DictReader(handle)}


def _save_progress(key: tuple[str, ...], result: str) -> None:
    import os
    new = not PROGRESS_FILE.exists() or not PROGRESS_FILE.stat().st_size
    with PROGRESS_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["Program", "Series", "Test", "Student_ID", "Result"])
        writer.writerow([*key, result])
        handle.flush()
        os.fsync(handle.fileno())


def submit_rejection(page, record: RejectionComment, program: str, series: str,
                     test: str, before_submit=lambda: None, verify_row=None) -> None:
    """Upload the rejected copy, enter its comment and set total marks to zero.

    `verify_row(row, student_id)` (integrated-workflow guard) runs on every located
    row before anything is uploaded; it raises when the row's identity is not the
    requested student.
    """
    from . import data_filler as engine
    stage, uploaded = "find student", False
    try:
        row = browser.find_pending_row(page, program, series, test, record.student_id)
        if verify_row is not None:
            verify_row(row, record.student_id)
        if engine.should_stop():
            raise engine.AbortRun("Stopped by you")
        stage = "upload"
        def restore_row():
            browser.reopen_pending(page, program)
            found = browser.find_pending_row(page, program, series, test, record.student_id)
            if verify_row is not None:
                verify_row(found, record.student_id)
            return found
        row = browser.upload_paper(page, row, record.paper_path, restore_row)
        uploaded = True
        stage = "open View Upload"
        engine.click_menu_item(page, row, "View Upload")
        stage = "fill comment"
        # Same modal as normal evaluation; wait for its visible comment field.
        boxes = page.locator('textarea:visible')
        boxes.first.wait_for(state="visible")
        fill_comment(page, record)
        stage = "set Total Marks to zero"
        modal = page.locator(engine.SEL["modal"]).filter(has_text=engine.SEL["modal_title"]).last
        scope = modal if modal.is_visible() else page
        total = scope.locator(engine.SEL["total_marks"] + ':visible')
        if total.count() != 1:
            raise ValueError("The uploaded-paper form must contain exactly one visible Total Marks field.")
        engine.fill_field(total, "0")
        _require_zero_total(total)
        stage = "prepare Submit"
        # Exclude an old evaluation toast from this student's confirmation.
        success = page.locator(engine.SEL["eval_success"]).first
        success.wait_for(state="hidden", timeout=engine.TOAST_CLEAR_TIMEOUT_MS)
        button = scope.get_by_role("button", name="Submit", exact=True)
        if button.count() != 1:
            raise ValueError("The uploaded-paper form does not have exactly one Submit button.")
        if engine.should_stop():
            raise engine.AbortRun("Stopped before Submit; the PDF was uploaded but no comment was submitted.")
        _require_zero_total(total)  # Blur or site recalculation must not restore a nonzero total.
        invalid = button.evaluate("""button => {
            const form = button.form || button.closest('form');
            if (!form || form.noValidate || button.formNoValidate) return [];
            return [...form.elements].filter(el => el.willValidate && !el.validity.valid)
                .map(el => el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.name || el.tagName);
        }""")
        if invalid:
            raise ValueError("Website validation blocked Submit: " + ", ".join(invalid)
                             + ". Nothing submitted; correct the required fields before retrying.")
        before_submit()  # Durable marker before any potentially successful click.
        try:
            button.click()
            success.wait_for(state="visible", timeout=engine.DEFAULT_TIMEOUT_MS)
        except Exception as exc:
            raise SubmissionUnverified(
                "Submit was attempted, but success was not confirmed. Check this student on the site before retrying. "
                + engine.first_line(exc)) from exc
    except (engine.AbortRun, SubmissionUnverified):
        raise
    except Exception as exc:
        note = " PDF upload was confirmed; comment was not submitted." if uploaded else ""
        raise RuntimeError(f"[{stage}] {engine.first_line(exc)}{note}") from exc


def _require_zero_total(field) -> None:
    try:
        value = Decimal(field.input_value().strip())
    except (InvalidOperation, ValueError):
        raise ValueError("The site did not retain Total Marks = 0; nothing submitted.") from None
    if not value.is_finite() or value != 0:
        raise ValueError("The site did not retain Total Marks = 0; nothing submitted.")


def run_comments(records: list[RejectionComment], program: str, series: str, test: str) -> dict:
    """Upload each paired copy and submit its comment with zero total and unchanged ratings."""
    from datetime import datetime
    from . import data_filler as engine
    from .match_paper import site_session

    # Validate again in case a caller bypasses the GUI's pairing check.
    ids = set()
    for record in records:
        if record.student_id in ids or not record.comment.strip():
            raise ValueError("Duplicate student or empty rejection reason; nothing submitted.")
        ids.add(record.student_id)
        if record.paper_path is None or not record.paper_path.is_file():
            raise ValueError(f"Rejected PDF is missing for {record.student_id}; nothing submitted.")
        if [s.lower() for s in STUDENT_ID.findall(record.paper_path.stem)] != [record.student_id.lower()]:
            raise ValueError(f"PDF Student ID does not match the TXT for {record.student_id}.")
    rows = []
    counts = {"Done": 0, "Done earlier": 0, "Unverified": 0, "Failed": 0, "Stopped": 0}
    stopped, report = "", None
    progress = _progress()
    engine.log.info("Loaded %d rejected PDF/TXT pairs for %s", len(records), test)
    try:
        with site_session() as (context, page):
            engine.wait_for_manual_login(page)
            for index, record in enumerate(records, 1):
                if engine.should_stop():
                    raise engine.AbortRun("Stopped by you")
                engine.on_progress("Rejection copies", index - 1, len(records))
                result, details = "Failed", ""
                key = (program, series, test, record.student_id)
                try:
                    previous = progress.get(key)
                    if previous == "Done":
                        result = "Done earlier"
                        continue
                    if previous == "Unverified":
                        result = "Unverified"
                        details = "Skipped: a previous Submit was not confirmed. Other students will continue; check this student's status before retrying."
                        engine.log.warning("SKIP Unverified rejection for %s: %s", record.student_id, details)
                        continue
                    engine.open_evaluate(page, program)
                    submit_rejection(page, record, program, series, test,
                                     before_submit=lambda: _save_progress(key, "Unverified"))
                    result = "Done"
                    try:
                        _save_progress(key, "Done")
                    except OSError as exc:
                        details = "Website confirmed success, but local progress could not be saved: " + str(exc)
                        stopped = details
                        engine.log.error(details)
                        break
                    engine.log.info("OK Rejection copy and comment for %s", record.student_id)
                except SubmissionUnverified as exc:
                    result, details = "Unverified", str(exc)
                    engine.log.error("UNVERIFIED Rejection for %s: %s", record.student_id, details)
                    engine.save_screenshot(page, record.student_id)
                    continue
                except engine.AbortRun as exc:
                    result, details = "Stopped", str(exc)
                    raise
                except Exception as exc:
                    details = engine.first_line(exc)
                    engine.log.error("FAIL Rejection for %s: %s", record.student_id, details)
                    engine.save_screenshot(page, record.student_id)
                finally:
                    counts[result] += 1
                    rows.append([record.student_id, record.student_name, test, result,
                                 str(record.paper_path), record.comment, details])
                    engine.on_progress("Rejection copies", index, len(records))
    except engine.AbortRun as exc:
        stopped = str(exc)
    finally:
        if rows:
            report = Path(f"rejection_report_{datetime.now():%Y%m%d_%H%M%S_%f}.csv")
            with report.open('w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(['Student_ID', 'Student_Name', 'Test_Name', 'Result', 'PDF', 'Comment', 'Details'])
                writer.writerows(rows)
        engine.log.info("Finished rejection copies: %s", "; ".join(f"{n} {k}" for k, n in counts.items()))
    return {"counts": counts, "stopped": stopped, "report": report}
