"""The PW Skills run report: every copy with its outcome, the reason it was not posted,
and every link in the learner's submission with what the checker made of it.

The user asked for it on 24 Nov 2026 ("a report with all information at the end like how
many failed with reason, all the links"). It is worked out from the run's own files, so
it costs nothing: run.json, each copy's downloaded submission.pdf (its links) and its
checked.pdf (the "Linked work N of M: <file>" headings of what was read). A summary is
shown on the page when a run stops; the Excel file holds the summary, every copy and
every link, and is also written next to the run as report.xlsx.
"""
from __future__ import annotations

import io
import re
import time
from collections import Counter
from pathlib import Path

import skills_portal as portal

STAGES = {"queued": "Waiting", "checking": "Checking", "ready": "Checked, waiting for you",
          "posting": "Posting", "posted": "Posted", "held": "Needs a person",
          "already_evaluated": "Evaluated by someone else"}
IN_PROGRESS = ("queued", "checking", "posting")

# Why a copy was not posted, in the words a coordinator acts on. First match wins.
REASONS = (
    ("own solution file", "Uploaded the assignment's own solution file"),
    ("cover page and its work is elsewhere", "Work behind a link the checker could not open"),
    ("holds the learner's details and link", "Work behind a link the checker could not open"),
    ("holds the learner's details and a video", "A video for a person to watch"),
    ("0 marks", "0 marks: the portal does not accept 0"),
    ("outside the portal's range", "Marks outside the portal's range"),
    ("different test", "Answer for another assignment"),
    ("another assignment", "Answer for another assignment"),
    ("does not look like an answer", "Not an answer to this assignment"),
    ("more than an answer sheet could run to", "File too long to be an answer"),
    ("no question paper", "Assignment has no question paper on the portal"),
    ("question paper could not be opened", "Question paper could not be opened"),
    ("paper's marks could not be read", "Question paper's marks could not be read"),
    ("no maximum marks", "Assignment has no maximum marks on the portal"),
    ("under evaluation", "A mentor has it under evaluation"),
    ("no longer on the portal", "Submission removed from the portal"),
    ("the check did not finish", "The check failed twice"),
    ("the portal refused this post", "The portal refused the post"),
    ("feedback is", "Feedback too long for the portal"),
    ("changed the file after it was checked", "Learner replaced the file after the check"),
    ("could not be read", "File could not be opened"),
    ("could not be opened", "File could not be opened"),
)

_LINKED_HEADING = re.compile(r"Linked work (\d+) of (\d+): ([^\n]+)")


def reason_group(copy) -> str:
    """The coordinator's reason for a copy that is not posted, or "" for one that is."""
    stage = copy.get("stage")
    if stage in ("posted", "already_evaluated") or stage in IN_PROGRESS:
        return ""
    note = " ".join(str(copy.get("note") or "").split())
    if stage == "ready" and not note:
        return ""
    lowered = note.lower()
    for needle, label in REASONS:
        if needle in lowered:
            return label
    return note[:120] or "Not posted"


def submission_link(copy) -> str:
    value = str(copy.get("file") or "")
    return value if portal.is_learner_link(value) else portal.file_url(value)


def links_of(path: Path) -> list[str]:
    from linked_work import links_in_pdf
    try:
        data = path.read_bytes()
    except OSError:
        return []
    return links_in_pdf(data) if data[:5] == b"%PDF-" else []


def files_read(path: Path) -> list[str]:
    """The linked files the checker read, from the headings it wrote into the checked copy."""
    import fitz
    try:
        with fitz.open(path) as document:
            text = "\n".join(page.get_text() for page in document)
    except Exception:  # noqa: BLE001 - no checked copy, nothing read
        return []
    names: list[str] = []
    for match in _LINKED_HEADING.finditer(text):
        name = " ".join(match.group(3).split())
        if name not in names:
            names.append(name)
    return names


def link_status(url: str, copy) -> str:
    """What the checker made of one link in the learner's submission."""
    from linked_work import _followed, unfollowed_reason
    said = " ".join([str(copy.get("note") or "")] + [str(r) for r in copy.get("reasons") or []])
    match = re.search(re.escape(url[:90]) + r"\S*\s*\(([^)]*)\)", said)
    if match:
        return f"Not opened: {match.group(1)}"
    if not _followed(url):
        return f"Not opened: {unfollowed_reason(url)}"
    if copy.get("stage") in ("queued", "checking") or copy.get("marks") is None and copy.get("stage") != "held":
        return "Not reached yet"
    if copy.get("stage") == "held" and copy.get("marks") is None:
        return "Not opened: the copy was not marked"
    return "Opened and read"


def build(run, folder) -> dict:
    """Everything the page and the Excel file show about one run."""
    folder = Path(folder)
    rows, link_rows = [], []
    for copy in run["copies"]:
        target = folder / "copies" / copy["id"]
        links = links_of(target / "submission.pdf")
        read = files_read(target / "checked.pdf") if copy.get("has_checked_pdf") else []
        statuses = [(url, link_status(url, copy)) for url in links]
        for url, status in statuses:
            link_rows.append({"student": copy.get("student") or "", "email": copy.get("email") or "",
                              "assignment": copy.get("assignment") or "", "url": url, "status": status})
        shown = copy.get("posted_marks") if copy.get("stage") in ("posted", "already_evaluated") else copy.get("marks")
        rows.append({
            "student": copy.get("student") or "", "email": copy.get("email") or "", "phone": copy.get("phone") or "",
            "assignment": copy.get("assignment") or "", "submitted_at": copy.get("submitted_at") or "",
            "submission": submission_link(copy), "status": STAGES.get(copy.get("stage"), copy.get("stage") or ""),
            "marks": shown, "out_of": copy.get("out_of"),
            "reason": reason_group(copy), "note": " ".join(str(copy.get("note") or "").split()),
            "to_look_at": " ".join(str(r) for r in copy.get("reasons") or []),
            "links": statuses, "files_read": read, "attempts": int(copy.get("attempts") or 0),
            "stage_since": copy.get("stage_at"), "copy_id": copy["id"],
        })
    stages = Counter(copy.get("stage") for copy in run["copies"])
    reasons = Counter(row["reason"] for row in rows if row["reason"])
    posted = [row for row in rows if row["status"] == STAGES["posted"] and row["marks"] is not None and row["out_of"]]
    link_states = Counter("Opened and read" if s == "Opened and read" else
                          "Not reached yet" if s == "Not reached yet" else "Not opened"
                          for row in link_rows for s in [row["status"]])
    options = run.get("options") or {}
    return {
        "batch": (run.get("course") or {}).get("title", ""),
        "status": run.get("status", ""), "error": run.get("error", ""),
        "started": run.get("created_at"), "updated": run.get("updated_at"),
        "options": {"assignments": options.get("titles") or [], "learners": options.get("learners") or [],
                    "most_copies": options.get("most_copies") or 0, "auto_post": bool(options.get("auto_post"))},
        "counts": {
            "copies": len(rows),
            "posted": stages.get("posted", 0),
            "evaluated_elsewhere": stages.get("already_evaluated", 0),
            "waiting_for_you": stages.get("ready", 0),
            "needs_a_person": stages.get("held", 0),
            "in_progress": sum(stages.get(stage, 0) for stage in IN_PROGRESS),
            "left_out_other_runs": int(run.get("skipped_elsewhere") or 0),
        },
        "reasons": [{"reason": reason, "copies": count} for reason, count in reasons.most_common()],
        "average_percent": round(sum(100.0 * float(r["marks"]) / float(r["out_of"]) for r in posted) / len(posted), 1)
                           if posted else None,
        "links": {"copies_with_links": sum(1 for row in rows if row["links"]),
                  "total": len(link_rows), **{key: link_states.get(key, 0)
                                              for key in ("Opened and read", "Not opened", "Not reached yet")}},
        "rows": rows, "link_rows": link_rows,
    }


def _when(seconds) -> str:
    try:
        return time.strftime("%d %b %Y %H:%M", time.localtime(float(seconds)))
    except (TypeError, ValueError):
        return ""


def xlsx(report) -> bytes:
    """The report as an Excel workbook: Summary, Copies and Links sheets."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    book = Workbook()
    bold, head_fill = Font(bold=True), PatternFill("solid", fgColor="E8E7FF")
    wrap = Alignment(wrap_text=True, vertical="top")

    summary = book.active
    summary.title = "Summary"
    counts, options = report["counts"], report["options"]
    lines = [
        ("PW Skills run report", ""),
        ("Batch", report["batch"]),
        ("Started", _when(report["started"])),
        ("Last change", _when(report["updated"])),
        ("Run status", report["status"] + (f": {report['error']}" if report["error"] else "")),
        ("Assignments", ", ".join(options["assignments"]) or "All"),
        ("Learners", ", ".join(options["learners"]) or "All"),
        ("Posting", "By itself" if options["auto_post"] else "After a person approves"),
        ("", ""),
        ("Copies in this run", counts["copies"]),
        ("Posted to the portal", counts["posted"]),
        ("Evaluated by someone else", counts["evaluated_elsewhere"]),
        ("Checked, waiting for you", counts["waiting_for_you"]),
        ("Needs a person", counts["needs_a_person"]),
        ("Still in progress", counts["in_progress"]),
        ("Left out (another run has them)", counts["left_out_other_runs"]),
        ("Average marks of posted copies", f"{report['average_percent']}%" if report["average_percent"] is not None else "-"),
        ("", ""),
        ("Not posted, by reason", "Copies"),
        *[(item["reason"], item["copies"]) for item in report["reasons"]],
        ("", ""),
        ("Links in submissions", report["links"]["total"]),
        ("Copies with links", report["links"]["copies_with_links"]),
        ("Opened and read", report["links"]["Opened and read"]),
        ("Not opened", report["links"]["Not opened"]),
        ("Not reached yet", report["links"]["Not reached yet"]),
    ]
    for label, value in lines:
        summary.append([label, value])
    summary["A1"].font = Font(bold=True, size=14)
    for row in summary.iter_rows(min_row=2):
        if row[0].value in ("Copies in this run", "Not posted, by reason", "Links in submissions"):
            row[0].font = row[1].font = bold
    summary.column_dimensions["A"].width, summary.column_dimensions["B"].width = 38, 70

    sheet = book.create_sheet("Copies")
    columns = [("Learner", 22), ("Email", 28), ("Phone", 14), ("Assignment", 28), ("Submitted", 20),
               ("Status", 22), ("Marks", 8), ("Out of", 8), ("Reason not posted", 34), ("Details", 60),
               ("To look at", 50), ("Links in the submission", 70), ("Linked files read", 34),
               ("Submission file", 50), ("Check attempts", 9)]
    sheet.append([name for name, _width in columns])
    for index, (_name, width) in enumerate(columns, 1):
        sheet.cell(row=1, column=index).font, sheet.cell(row=1, column=index).fill = bold, head_fill
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    for row in report["rows"]:
        sheet.append([row["student"], row["email"], row["phone"], row["assignment"], row["submitted_at"],
                      row["status"], row["marks"], row["out_of"], row["reason"], row["note"], row["to_look_at"],
                      "\n".join(f"{url} - {status}" for url, status in row["links"]),
                      "\n".join(row["files_read"]), row["submission"], row["attempts"]])
        link_cell = sheet.cell(row=sheet.max_row, column=14)
        if row["submission"]:
            link_cell.hyperlink, link_cell.style = row["submission"], "Hyperlink"
        for cell in sheet[sheet.max_row]:
            cell.alignment = wrap
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions

    links = book.create_sheet("Links")
    link_columns = [("Learner", 22), ("Email", 28), ("Assignment", 28), ("Link", 70), ("What the checker did", 70)]
    links.append([name for name, _width in link_columns])
    for index, (_name, width) in enumerate(link_columns, 1):
        links.cell(row=1, column=index).font, links.cell(row=1, column=index).fill = bold, head_fill
        links.column_dimensions[links.cell(row=1, column=index).column_letter].width = width
    for row in report["link_rows"]:
        links.append([row["student"], row["email"], row["assignment"], row["url"], row["status"]])
        cell = links.cell(row=links.max_row, column=4)
        if row["url"].lower().startswith(("https://", "http://")):
            cell.hyperlink, cell.style = row["url"], "Hyperlink"
        for cell in links[links.max_row]:
            cell.alignment = wrap
    links.freeze_panes = "A2"
    links.auto_filter.ref = links.dimensions

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def summary(report) -> dict:
    """What the page shows: the report without its per-copy rows."""
    return {key: value for key, value in report.items() if key not in ("rows", "link_rows")}
