"""The PW Skills run report (24 Nov 2026): every copy, why it was not posted, and every link
in each submission with what the checker did with it."""
import io
import os
import sys

import fitz
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

import skills_report  # noqa: E402
import skills_workflow as sw  # noqa: E402
from test_skills_workflow import FakePortal, graded, make, new_run, row, run_to_end  # noqa: E402

SHEET = "https://docs.google.com/spreadsheets/d/LEARNERsheet456/edit"
ELSEWHERE = "https://example.org/my-profile"


def pdf(lines, link=None):
    document = fitz.open()
    page = document.new_page()
    for n, line in enumerate(lines):
        page.insert_text((72, 72 + 16 * n), line)
    if link:
        page.insert_link({"kind": fitz.LINK_URI, "uri": link, "from": fitz.Rect(72, 60, 300, 76)})
    return document.tobytes()


def copy(cid, stage, **fields):
    return {"id": cid, "stage": stage, "student": f"Learner {cid}", "email": f"{cid}@x.com", "phone": "9000000000",
            "assignment": "Excel", "file": f"assignment/{cid}.pdf", "out_of": 100.0, "marks": None,
            "note": "", "reasons": [], "attempts": 0, **fields}


@pytest.fixture()
def run_folder(tmp_path):
    copies = [
        copy("A", "posted", marks=80.0, posted_marks=80.0, has_checked_pdf=True),
        copy("B", "posted", marks=60.0, posted_marks=60.0),
        copy("C", "ready", marks=70.0),
        copy("D", "held", marks=0.0, note="0 marks: the portal's form does not accept 0. A person should decide."),
        copy("E", "held", note=f"Not posted: This submission holds the learner's details and links, and the links "
                               f"could not be opened: {ELSEWHERE} (example.org is not a site the checker opens). "
                               "No marks were given."),
        copy("F", "held", note="The learner uploaded the assignment's own solution file, not their work."),
        copy("G", "already_evaluated", posted_marks=55.0),
        copy("H", "queued"),
        copy("I", "ready", marks=90.0, note="The portal refused this post: comment must not exceed 500 characters"),
    ]
    run = {"id": "r1", "course": {"title": "Data Analytics | Pro_ON_MSF_Sept26"}, "status": "done", "error": "",
           "created_at": 1794552800, "updated_at": 1794556400, "skipped_elsewhere": 2, "copies": copies,
           "options": {"titles": ["Excel"], "learners": ["9036331566"], "most_copies": 0, "auto_post": False}}
    for cid, data in {"A": pdf(["Link to my sheet"], SHEET), "E": pdf(["Click to open my profile"], ELSEWHERE),
                      "B": pdf(["Q1. Answer typed here"])}.items():
        (tmp_path / "copies" / cid).mkdir(parents=True)
        (tmp_path / "copies" / cid / "submission.pdf").write_bytes(data)
    (tmp_path / "copies" / "A" / "checked.pdf").write_bytes(pdf(["Linked work 1 of 1: PW_ASSIGNMENT.xlsx", "Sheet 1 of 3"]))
    return run, tmp_path


def test_counts_and_reasons(run_folder):
    run, folder = run_folder
    report = skills_report.build(run, folder)
    assert report["counts"] == {"copies": 9, "posted": 2, "evaluated_elsewhere": 1, "waiting_for_you": 2,
                                "needs_a_person": 3, "in_progress": 1, "left_out_other_runs": 2}
    reasons = {item["reason"]: item["copies"] for item in report["reasons"]}
    assert reasons == {"0 marks: the portal does not accept 0": 1,
                       "Work behind a link the checker could not open": 1,
                       "Uploaded the assignment's own solution file": 1,
                       "The portal refused the post": 1}
    assert report["average_percent"] == 70.0


def test_every_link_is_listed_with_what_the_checker_did(run_folder):
    run, folder = run_folder
    report = skills_report.build(run, folder)
    by_learner = {row["student"]: row["status"] for row in report["link_rows"]}
    assert by_learner["Learner A"] == "Opened and read"
    assert by_learner["Learner E"] == "Not opened: example.org is not a site the checker opens"
    assert report["links"] == {"copies_with_links": 2, "total": 2, "Opened and read": 1, "Not opened": 1,
                               "Not reached yet": 0}
    rows = {row["copy_id"]: row for row in report["rows"]}
    assert rows["A"]["files_read"] == ["PW_ASSIGNMENT.xlsx"]
    assert rows["A"]["submission"] == "https://cdn.pwskills.com/assignment/A.pdf"


def test_the_excel_file_holds_summary_copies_and_links(run_folder):
    from openpyxl import load_workbook
    run, folder = run_folder
    book = load_workbook(io.BytesIO(skills_report.xlsx(skills_report.build(run, folder))))
    assert book.sheetnames == ["Summary", "Copies", "Links"]
    summary = {row[0]: row[1] for row in book["Summary"].iter_rows(values_only=True) if row[0]}
    assert summary["Needs a person"] == 3 and summary["Work behind a link the checker could not open"] == 1
    copies = list(book["Copies"].iter_rows(values_only=True))
    assert len(copies) == 10 and copies[0][0] == "Learner"
    assert book["Copies"].cell(row=2, column=14).hyperlink.target == "https://cdn.pwskills.com/assignment/A.pdf"
    links = list(book["Links"].iter_rows(values_only=True))
    assert [row[3] for row in links[1:]] == [SHEET, ELSEWHERE]


def test_a_finished_run_leaves_its_report_next_to_it(tmp_path):
    http = FakePortal([row("S1")])
    store = make(tmp_path, http)
    run = run_to_end(store, new_run(store))
    assert (store.folder(run["id"]) / "report.xlsx").exists()


def test_the_page_gets_the_summary_and_the_file(tmp_path):
    import asyncio
    import base64
    import json
    from types import SimpleNamespace
    from fastapi import FastAPI
    token = "h." + base64.urlsafe_b64encode(json.dumps({"sub": "me"}).encode()).decode().rstrip("=") + ".s"
    app = FastAPI()
    store = sw.install(app, lambda authorization: token, graded(), tmp_path / "s")
    run = new_run(store)
    store.save({**store.read(run["id"]), "owner": sw.owner_for(token)})
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    page = asyncio.run(routes["/api/skills/runs/{run_id}/report"](run["id"], request, "Bearer app"))
    assert page["counts"]["copies"] == 0 and "rows" not in page
    answer = asyncio.run(routes["/api/skills/runs/{run_id}/report.xlsx"](run["id"], request, "Bearer app"))
    assert answer.body[:2] == b"PK" and "PW Skills report" in answer.headers["content-disposition"]
