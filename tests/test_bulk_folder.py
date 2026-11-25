# -*- coding: utf-8 -*-
"""Folder mode (bulk.py): thousands of copies read from disk, results written to disk.

An upload holds about 60 copies (300 MB) and keeps every marked PDF in memory, so a
10,000-copy batch could not be graded (asked 2026-09-29). These tests run the real
job runner on a folder with grading faked out.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bulk  # noqa: E402
import grader  # noqa: E402
import server  # noqa: E402
from costs import compute_cost  # noqa: E402


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


PAPER = _pdf("Unit Test\nMaximum Marks: 4\n\n1. State Newton's first law. [2 Marks]\n\n"
             "2. Define inertia. [2 Marks]\n")
KEY = _pdf("Answer key\n1. A body stays at rest or in uniform motion unless a force acts on it. "
           "(2 marks)\n2. Inertia is the tendency of a body to resist a change in its state. "
           "(2 marks)\n")


class Layout(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.src = self.root / "copies"
        (self.src / "centre A").mkdir(parents=True)
        for rel in ("a.pdf", "centre A/b.PDF", "centre A/c.jpg", "notes.txt", "~$lock.pdf"):
            (self.src / rel).write_bytes(b"%PDF-1.4")

    def test_subfolders_are_read_and_the_output_folder_is_never_read_back(self):
        inside = self.src / "_out"
        source, target = bulk.resolve_folders(str(self.src), str(inside))
        (inside / "to_students").mkdir(parents=True)
        (inside / "to_students" / "evaluated_a.pdf").write_bytes(b"%PDF")
        self.assertEqual(bulk.list_sheets(source, target),
                         ["a.pdf", "centre A/b.PDF", "centre A/c.jpg"])

    def test_default_output_sits_next_to_the_input(self):
        _source, target = bulk.resolve_folders(str(self.src))
        self.assertEqual(target.name, "copies - evaluated")
        with self.assertRaises(bulk.BulkError):
            bulk.resolve_folders(str(self.src), str(self.src))
        with self.assertRaises(bulk.BulkError):
            bulk.resolve_folders(str(self.root / "missing"))

    def test_parts_split_every_copy_exactly_once(self):
        names = [f"centre {c}/roll {n}.pdf" for c in "ABC" for n in range(200)]
        for parts in (1, 2, 3, 7):
            shares = [[r for r in names if bulk.in_part(r, k, parts)] for k in range(1, parts + 1)]
            self.assertEqual(sorted(sum(shares, [])), sorted(names))
            if parts > 1:
                self.assertTrue(all(shares), "every computer gets work")
        with self.assertRaises(bulk.BulkError):
            bulk.check_part(3, 2)

    def test_a_failed_copy_is_not_finished_but_the_others_are(self):
        source, target = bulk.resolve_folders(str(self.src))
        sink = bulk.FolderSink(source, target, 1, 1)
        sink.file("a.pdf", {"state": "released", "release": True, "score": 3, "max": 4,
                            "cost": {"total_inr": 10.5}}, b"%PDF-marked", {"report": {}})
        sink.file("centre A/b.PDF", {"state": "failed", "error": "proxy down"}, None, None)
        sink.file("centre A/c.jpg", {"state": "faculty_review", "review_reasons": ["check Q2"]},
                  b"%PDF-review", None)
        self.assertTrue((target / "to_students" / "evaluated_a.pdf").exists())
        self.assertTrue((target / "_reports" / "a.json.gz").exists())
        self.assertTrue((target / "failed" / "centre A" / "b_error.txt").exists())
        self.assertEqual((target / "faculty_review" / "centre A" / "REVIEW_c_reasons.txt")
                         .read_text(encoding="utf-8"), "check Q2")
        self.assertEqual(set(bulk.finished_copies(target)), {"a.pdf", "centre A/c.jpg"})
        plan = bulk.plan(source, target, 1, 1)
        self.assertEqual((plan["already_done"], plan["todo"]), (2, ["centre A/b.PDF"]))
        rows = list(csv.DictReader(open(target / "results_part1of1.csv", encoding="utf-8-sig")))
        self.assertEqual([r["state"] for r in rows], ["released", "failed", "faculty_review"])

    def test_a_line_cut_short_by_a_crash_is_ignored(self):
        source, target = bulk.resolve_folders(str(self.src))
        sink = bulk.FolderSink(source, target, 1, 1)
        sink.file("a.pdf", {"state": "rejected", "error": "typed pages"}, None, None)
        with open(sink.ledger, "a", encoding="utf-8") as handle:
            handle.write('{"source": "centre A/b.PDF", "sta')
        self.assertEqual(set(bulk.finished_copies(target)), {"a.pdf"})


class FolderRun(unittest.TestCase):
    """The real /api/bulk/start and job runner, with the paid grading faked."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.src = self.root / "copies"
        (self.src / "centre A").mkdir(parents=True)
        for rel in ("r1.pdf", "r2.pdf", "centre A/r3.pdf", "centre A/r4.pdf", "junk.pdf"):
            (self.src / rel).write_bytes(_pdf(f"answers of {rel}"))
        self.graded: list[str] = []
        self.fail_all = False
        self.patch = patch.multiple(
            server, _require_allowed=lambda token: "user-one", JOBS={},
            _persist_job=lambda *a: None, _log_error=lambda *a: None,
            _make_orienter=lambda *a, **k: (lambda data, name: data),
            grade_sheet=self.fake_grade)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        keys = patch("key_answers.complete_objective_answers",
                     side_effect=lambda package, *a, **k: (package, False))
        keys.start()
        self.addCleanup(keys.stop)

    def fake_grade(self, *, filename, sa_bytes, stage_callback=None, **kwargs):
        self.graded.append(filename)
        for stage in ("normalizing", "ocr", "answer_mapping", "primary_grading", "verifying",
                      "aggregating", "rendering", "release_check"):
            stage_callback(stage)
        if self.fail_all:
            raise RuntimeError("proxy unreachable")
        if filename == "junk.pdf":
            raise server.StrictGradeError("NOT_AN_ANSWER_SHEET", "Typed pages, not handwriting.")
        report = grader.GradeReport(questions=[
            grader.QuestionMark(qid="Q1", score=2, max_score=2, page=1, anchor_line_id="", y_fraction=0.2,
                                remark="", criteria=[]),
            grader.QuestionMark(qid="Q2", score=1, max_score=2, page=1, anchor_line_id="", y_fraction=0.2,
                                remark="Define it fully", criteria=[])],
            total_score=3, max_total=4, overall_remarks="Good work.")
        cost = compute_cost({"provider": "claude", "model": "claude-haiku-4-5",
                             "input_tokens": 100000, "output_tokens": 2000})
        return {"report": report, "cost": cost, "pdf_bytes": b"%PDF-marked " + sa_bytes[:8],
                "blocking": [], "issues": [], "release": filename != "r2.pdf",
                "budget": {}, "package_id": "pkg", "ocr_pages": []}

    def start(self, **form):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://test") as client:
                response = await client.post("/api/bulk/start", headers={"Authorization": "Bearer t"},
                    data={"input_folder": str(self.src), "provider": "claude",
                          "student_class": "Class 9", "subject": "Physics",
                          "exam_type": "school", "total_marks": "4", "key_source": "upload",
                          "workers": "2", **form},
                    files={"question_paper": ("paper.pdf", PAPER, "application/pdf"),
                           "answer_key": ("key.pdf", KEY, "application/pdf")})
                return response
        return asyncio.run(send())

    def job(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return server.JOBS[response.json()["job_id"]]

    def test_every_copy_lands_in_its_folder_and_a_rerun_skips_them(self):
        job = self.job(self.start())
        self.assertEqual(job["status"], "done", job.get("error"))
        out = self.root / "copies - evaluated"
        self.assertTrue((out / "to_students" / "evaluated_r1.pdf").exists())
        self.assertTrue((out / "to_students" / "centre A" / "evaluated_r3.pdf").exists())
        self.assertTrue((out / "faculty_review" / "REVIEW_r2.pdf").exists())
        self.assertTrue((out / "rejected" / "junk_reason.txt").exists())
        self.assertEqual(job["bulk"]["counts"],
                         {"released": 3, "faculty_review": 1, "rejected": 1, "failed": 0})
        self.assertEqual(job["pdfs"], {}, "marked copies are on disk, not held in memory")
        again = self.start()
        self.assertEqual(again.status_code, 400)
        self.assertIn("Nothing left to grade", again.json()["detail"])

    def test_an_outage_stops_the_run_and_the_next_run_retries_the_failures(self):
        self.fail_all = True
        with patch.object(bulk, "MAX_CONSECUTIVE_FAILURES", 2):
            job = self.job(self.start(workers="1"))
        self.assertTrue(job["bulk"]["stopping"])
        self.assertIn("in a row", job["bulk"]["stopped_reason"])
        self.assertLess(len(self.graded), 5, "stopped before spending the whole folder")
        self.fail_all = False
        self.graded.clear()
        job = self.job(self.start())
        self.assertEqual(sorted(self.graded), sorted(
            ["r1.pdf", "r2.pdf", "centre A/r3.pdf", "centre A/r4.pdf", "junk.pdf"]))

    def test_two_computers_share_the_folder_without_overlap(self):
        first = self.job(self.start(machine="1", machines="2"))
        first_graded = list(self.graded)
        self.graded.clear()
        self.job(self.start(machine="2", machines="2"))
        self.assertEqual(sorted(first_graded + self.graded), sorted(
            ["r1.pdf", "r2.pdf", "centre A/r3.pdf", "centre A/r4.pdf", "junk.pdf"]))
        self.assertFalse(set(first_graded) & set(self.graded))
        ledgers = sorted(p.name for p in (self.root / "copies - evaluated" / "_progress").iterdir())
        self.assertEqual(ledgers, ["ledger_part1of2.jsonl", "ledger_part2of2.jsonl"])
        self.assertEqual(first["bulk"]["part"], 1)

    def test_start_again_during_a_run_shows_that_run(self):
        server.JOBS["live"] = {"status": "running", "key": "k1", "total": 5, "done": 2,
                               "bulk": {"target": str(self.root / "copies - evaluated"),
                                        "already_done": 0}}
        response = self.start()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["job_id"], response.json()["attached"]), ("live", True))
        self.assertEqual(self.graded, [], "no second run over the same folder")

    def test_one_damaged_upload_is_set_aside_and_the_rest_are_graded(self):
        # 2026-09-29: an incomplete download named .pdf refused a batch of 33 copies.
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://test") as client:
                return await client.post("/api/evaluate", headers={"Authorization": "Bearer t"},
                    data={"provider": "claude", "student_class": "Class 9", "subject": "Physics",
                          "exam_type": "school", "total_marks": "4", "key_source": "upload"},
                    files=[("question_paper", ("paper.pdf", PAPER, "application/pdf")),
                           ("answer_key", ("key.pdf", KEY, "application/pdf")),
                           ("answer_sheets", ("r1.pdf", _pdf("answers"), "application/pdf")),
                           ("answer_sheets", ("Poorvi.pdf", bytes([0xA6, 0x66, 0x8A, 0x9E]) + b" endobj", "application/pdf"))])
        response = asyncio.run(send())
        job = self.job(response)
        self.assertEqual(self.graded, ["r1.pdf"])
        rows = {r["student"]: r for r in job["results"]}
        self.assertEqual(rows["Poorvi.pdf"]["state"], "rejected")
        self.assertIn("“Poorvi.pdf” is damaged", rows["Poorvi.pdf"]["error"])
        self.assertEqual((job["total"], job["done"]), (2, 2))

    def test_the_same_file_twice_is_graded_once(self):
        # 2026-09-30: one Physics sheet listed under two teachers was marked twice.
        sheet = _pdf("answers of Shouryaveer")
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://test") as client:
                return await client.post("/api/evaluate", headers={"Authorization": "Bearer t"},
                    data={"provider": "claude", "student_class": "Class 9", "subject": "Physics",
                          "exam_type": "school", "total_marks": "4", "key_source": "upload"},
                    files=[("question_paper", ("paper.pdf", PAPER, "application/pdf")),
                           ("answer_key", ("key.pdf", KEY, "application/pdf")),
                           ("answer_sheets", ("Akash - Shouryaveer.pdf", sheet, "application/pdf")),
                           ("answer_sheets", ("Dev - Shouryaveer.pdf", sheet, "application/pdf"))])
        job = self.job(asyncio.run(send()))
        self.assertEqual(self.graded, ["Akash - Shouryaveer.pdf"])
        rows = {r["student"]: r for r in job["results"]}
        self.assertEqual(rows["Dev - Shouryaveer.pdf"]["state"], "rejected")
        self.assertIn("Same file as", rows["Dev - Shouryaveer.pdf"]["error"])

    def test_only_this_computer_may_name_its_folders(self):
        request = type("R", (), {"client": type("C", (), {"host": "10.0.0.8"})()})()
        with self.assertRaises(server.HTTPException) as caught:
            server._require_local(request)
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
