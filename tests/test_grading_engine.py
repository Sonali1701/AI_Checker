# -*- coding: utf-8 -*-
"""Folder mode grades 20 to 30 copies at a time in worker processes (grading_engine.py).

Asked 2026-10-05: "we can only use 8 at a time, we should increase that to 20-30 so
that it takes 1-2 hr for 1k answer sheets". MEASURED the same day: folder runs did 92
to 119 copies an hour, and in one process the local work (page images, the evaluated
PDF, the release check, about 5 seconds a page) queues behind one render lock and one
interpreter, so more threads alone could not reach 500 to 1,000 copies an hour.

These tests start real worker processes. Only the paid grading is replaced, by
grading_engine.selftest_grade or tests/engine_fake_grade.py, inside the worker.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import grading_engine  # noqa: E402
from strict_grading import StrictGradeError  # noqa: E402


class Plan(unittest.TestCase):
    def test_a_few_copies_stay_in_one_process(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": ""}):
            for workers in (1, 2, 4):
                self.assertEqual(grading_engine.plan_processes(workers, cpus=12,
                                                               memory_gb=16, busy=0), 1)

    def test_one_process_per_five_copies_within_the_cores(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": ""}):
            def plan(workers, cpus):
                return grading_engine.plan_processes(workers, cpus=cpus, memory_gb=64,
                                                     busy=0)
            self.assertEqual(plan(8, 12), 2)
            self.assertEqual(plan(20, 12), 4)
            self.assertEqual(plan(30, 12), 6)
            self.assertEqual(plan(30, 4), 2, "two cores stay free")
            self.assertEqual(plan(30, 2), 1, "no spare core: one process")
            self.assertEqual(plan(60, 64), grading_engine.MAX_PROCESSES)

    def test_and_within_the_memory(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": ""}):
            def plan(workers, cpus, memory_gb):
                return grading_engine.plan_processes(workers, cpus=cpus, memory_gb=memory_gb,
                                                     busy=0)
            self.assertEqual(plan(30, cpus=12, memory_gb=16), 6, "this 16 GB laptop")
            self.assertEqual(plan(30, cpus=12, memory_gb=8), 3)
            self.assertEqual(plan(30, cpus=12, memory_gb=4), 1)
            self.assertEqual(plan(30, cpus=12, memory_gb=0), 6, "unknown: cores decide")

    def test_folder_runs_open_together_share_the_budget(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": ""}):
            def plan(workers, busy):
                return grading_engine.plan_processes(workers, cpus=12, memory_gb=16,
                                                     busy=busy)
            self.assertEqual(plan(20, 0), 4)
            self.assertEqual(plan(25, 4), 2, "the second run gets what is left")
            self.assertEqual(plan(30, 6), 1, "the third grades in this process")
        before = grading_engine.active_processes()
        pool = grading_engine.EnginePool({}, 1, 1, grade=grading_engine.selftest_grade,
                                         log=lambda line: None)
        try:
            self.assertEqual(grading_engine.active_processes(), before + 1)
        finally:
            pool.close()
        self.assertEqual(grading_engine.active_processes(), before)
        self.assertGreater(grading_engine.total_memory_gb(), 1)

    def test_the_operator_can_override_it(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": "1"}):
            self.assertEqual(grading_engine.plan_processes(30, cpus=12), 1)
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": "3"}):
            self.assertEqual(grading_engine.plan_processes(30, cpus=12), 3)
            self.assertEqual(grading_engine.plan_processes(2, cpus=12), 2)


class Pool(unittest.TestCase):
    """Real worker processes, stand-in grading."""

    @classmethod
    def setUpClass(cls):
        cls.lines: list[str] = []
        cls.pool = grading_engine.EnginePool({"label": "batch"}, 2, 3,
                                             grade=grading_engine.selftest_grade,
                                             log=cls.lines.append)
        cls.ready = cls.pool.wait_ready(120)

    @classmethod
    def tearDownClass(cls):
        cls.pool.close()

    def test_every_worker_starts(self):
        self.assertEqual(self.ready, 2)

    def test_a_copy_is_graded_in_another_process_and_only_result_fields_come_back(self):
        stages: list[tuple] = []
        out = self.pool.grade("copy-a", b"abc", lambda *a: stages.append(a))
        self.assertEqual(out["pdf_bytes"], b"cba")
        self.assertEqual(out["report"]["batch"], "batch")
        self.assertNotEqual(out["report"]["pid"], os.getpid())
        self.assertNotIn("not_sent", out)
        self.assertEqual(stages, [("ocr", "reading copy-a")])
        self.assertTrue(any("graded copy-a" in line for line in self.lines))

    def test_copies_run_side_by_side(self):
        start = time.monotonic()
        with ThreadPoolExecutor(6) as pool:
            pids = list(pool.map(lambda n: self.pool.grade(f"slow-1-{n}", b"x")["report"]["pid"],
                                 range(6)))
        elapsed = time.monotonic() - start
        self.assertEqual(len(set(pids)), 2, "both workers took copies")
        self.assertLess(elapsed, 4.5, f"six one second copies took {elapsed:.1f}s")

    def test_a_rejected_copy_keeps_its_code(self):
        with self.assertRaises(StrictGradeError) as caught:
            self.pool.grade("strict-copy", b"x")
        self.assertEqual(caught.exception.code, "NOT_AN_ANSWER_SHEET")
        self.assertEqual(str(caught.exception), "strict-copy is typed pages")

    def test_a_failed_copy_keeps_its_message(self):
        with self.assertRaises(grading_engine.EngineError) as caught:
            self.pool.grade("boom-copy", b"x")
        self.assertEqual(str(caught.exception), "boom-copy could not be read")
        self.assertTrue(any("ValueError" in line for line in self.lines))

    def test_a_result_that_cannot_cross_fails_the_copy_instead_of_hanging(self):
        with self.assertRaises(grading_engine.EngineError):
            self.pool.grade("unpicklable-copy", b"x")


class Crash(unittest.TestCase):
    def test_a_dead_worker_fails_only_its_copies_and_is_replaced(self):
        lines: list[str] = []
        pool = grading_engine.EnginePool({}, 2, 1, grade=grading_engine.selftest_grade,
                                         log=lines.append)
        try:
            self.assertEqual(pool.wait_ready(120), 2)
            with ThreadPoolExecutor(2) as threads:
                crashed = threads.submit(pool.grade, "crash-copy", b"x")
                fine = threads.submit(pool.grade, "slow-1-copy", b"x")
                with self.assertRaises(grading_engine.EngineError) as caught:
                    crashed.result(60)
                self.assertIn("tried again on the next run", str(caught.exception))
                self.assertEqual(fine.result(60)["pdf_bytes"], b"x")
            self.assertEqual(pool.grade("after-crash", b"ok")["pdf_bytes"], b"ko")
            end = time.monotonic() + 30
            while pool.processes < 2 and time.monotonic() < end:
                time.sleep(0.2)
            self.assertEqual(pool.processes, 2, "the dead worker was replaced")
            self.assertTrue(any("stopped (exit code 3)" in line for line in lines))
        finally:
            pool.close()

    def test_a_worker_is_replaced_after_its_share_of_copies(self):
        lines: list[str] = []
        pool = grading_engine.EnginePool({}, 1, 1, grade=grading_engine.selftest_grade,
                                         log=lines.append, copies_per_worker=2)
        try:
            self.assertEqual(pool.wait_ready(120), 1)
            pids = [pool.grade(f"copy-{n}", b"x")["report"]["pid"] for n in range(5)]
            self.assertEqual(len(set(pids)), 3, pids)
            self.assertEqual([pids[0], pids[2], pids[4]], sorted(set(pids), key=pids.index))
            self.assertFalse(any("stopped (exit code" in line for line in lines), lines)
        finally:
            pool.close()

    def test_a_closed_pool_refuses_new_copies(self):
        pool = grading_engine.EnginePool({}, 1, 1, grade=grading_engine.selftest_grade,
                                         log=lambda line: None)
        pool.close()
        with self.assertRaises(grading_engine.EngineError):
            pool.grade("late", b"x")


def _verified_batch() -> dict:
    """A paper whose marks graph two models agreed on: the server signs it with a proof."""
    from types import SimpleNamespace
    from exam_package import build_exam_package, issue_question_graph_proof
    from grader import MarksItem, MarksScheme
    scheme = MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1, description="first"),
                                         MarksItem(qid="Q2", max=2, description="second")])

    def package(proof: str):
        return build_exam_package(
            exam_type="school", student_class="Class 9", subject="Physics", level="9",
            paper="", attempt="", applicable_law_date=None, total_marks=3,
            question_paper=b"paper", answer_key=b"key", key_source="upload",
            marks_scheme=scheme, scheme_source="ai_verified", question_graph_proof=proof)

    unsigned = package("")
    signed = package(issue_question_graph_proof(
        unsigned.question_paper_sha256, unsigned.total_marks, unsigned.questions,
        unsigned.choice_groups))
    return {"cfg": SimpleNamespace(exam_package=signed)}


class VerifiedPaper(unittest.TestCase):
    """MEASURED 2026-10-06, build 82's first live run: every worker made its own proof
    key, refused the main process's proof, and 466 copies went to faculty review with
    no PDF before any grading."""

    def test_the_main_process_judges_the_paper_valid(self):
        self.assertEqual(_verified_batch()["cfg"].exam_package.blockers(), [])

    def test_a_worker_accepts_the_main_process_proof(self):
        from engine_fake_grade import package_check
        pool = grading_engine.EnginePool(_verified_batch(), 1, 1, grade=package_check,
                                         log=lambda line: None)
        try:
            self.assertEqual(pool.wait_ready(120), 1)
            self.assertEqual(pool.grade("copy", b"x")["report"], [])
        finally:
            pool.close()

    def test_a_worker_that_judges_the_paper_differently_is_not_used(self):
        from engine_fake_grade import package_check
        real = grading_engine._handover

        def wrong_key(batch):
            return dict(real(batch), proof_key=b"another process's key" * 2)

        lines: list[str] = []
        with patch.object(grading_engine, "_handover", wrong_key):
            pool = grading_engine.EnginePool(_verified_batch(), 2, 1, grade=package_check,
                                             log=lines.append)
        try:
            start = time.monotonic()
            self.assertEqual(pool.wait_ready(120), 0)
            self.assertLess(time.monotonic() - start, 60, "refusal is reported, not waited out")
            self.assertTrue(any("invalid-question-graph-proof" in line for line in lines), lines)
            time.sleep(2.5)
            self.assertFalse(any("stopped (exit code" in line for line in lines),
                             "a refusing worker is not restarted")
        finally:
            pool.close()


class Selftest(unittest.TestCase):
    def test_the_release_smoke_check_passes_from_source(self):
        result = grading_engine.selftest()
        self.assertEqual(result["ready"], 2)
        for key in ("result_ok", "worker_pid_differs", "stage_forwarded", "log_forwarded",
                    "package_ok"):
            self.assertTrue(result[key], key)
        self.assertEqual(result["strict_code"], "NOT_AN_ANSWER_SHEET")


class FolderRunInWorkers(unittest.TestCase):
    """The real /api/bulk/start and job runner with real worker processes."""

    def setUp(self):
        import fitz
        import server
        self.server = server
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.src = self.root / "copies"
        (self.src / "centre A").mkdir(parents=True)

        def pdf(text: str) -> bytes:
            doc = fitz.open()
            doc.new_page().insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=11)
            data = doc.tobytes()
            doc.close()
            return data

        self.paper = pdf("Unit Test\nMaximum Marks: 4\n\n1. State Newton's first law. "
                         "[2 Marks]\n\n2. Define inertia. [2 Marks]\n")
        self.key = pdf("Answer key\n1. A body stays at rest or in uniform motion unless a "
                       "force acts on it. (2 marks)\n2. Inertia is the tendency of a body to "
                       "resist a change in its state. (2 marks)\n")
        for rel in ("r1.pdf", "r2.pdf", "centre A/r3.pdf", "centre A/r4.pdf", "junk.pdf",
                    "broken.pdf"):
            (self.src / rel).write_bytes(pdf(f"answers of {rel}"))
        self.saves = 0

        def count_save(*_args):
            self.saves += 1

        self.pools: list[tuple[int, int]] = []
        real_pool = grading_engine.EnginePool

        def pool(batch, processes, threads, **kwargs):
            self.pools.append((processes, threads))
            from engine_fake_grade import fake_grade
            return real_pool(batch, processes, threads, grade=fake_grade, **kwargs)

        for patcher in (
            patch.multiple(server, _require_allowed=lambda token: "user-one", JOBS={},
                           _persist_job=count_save, _log_error=lambda *a: None,
                           _make_orienter=lambda *a, **k: (lambda data, name: data)),
            patch.object(grading_engine, "EnginePool", pool),
            patch("key_answers.complete_objective_answers",
                  side_effect=lambda package, *a, **k: (package, False)),
            patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": "2"}),
            patch.object(server, "_RAMP_SECONDS", 0.2),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, workers: str):
        import httpx

        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.server.app),
                                         base_url="http://test") as client:
                return await client.post(
                    "/api/bulk/start", headers={"Authorization": "Bearer t"},
                    data={"input_folder": str(self.src), "provider": "claude",
                          "student_class": "Class 9", "subject": "Physics",
                          "exam_type": "school", "total_marks": "4", "key_source": "upload",
                          "workers": workers},
                    files={"question_paper": ("paper.pdf", self.paper, "application/pdf"),
                           "answer_key": ("key.pdf", self.key, "application/pdf")})
        response = asyncio.run(send())
        self.assertEqual(response.status_code, 200, response.text)
        return self.server.JOBS[response.json()["job_id"]]

    def test_a_folder_is_graded_in_worker_processes(self):
        job = self.start("8")
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(self.pools, [(2, 3)], "six copies at 8 at a time: 2 processes of 3")
        self.assertEqual(job["bulk"]["counts"],
                         {"released": 3, "faculty_review": 1, "rejected": 1, "failed": 1})
        out = self.root / "copies - evaluated"
        self.assertTrue((out / "to_students" / "centre A" / "evaluated_r3.pdf").exists())
        self.assertTrue((out / "faculty_review" / "REVIEW_r2.pdf").exists())
        self.assertTrue((out / "rejected" / "junk_reason.txt").exists())
        saved = json.load(gzip.open(out / "_reports" / "r1.json.gz", "rt", encoding="utf-8"))
        marked_in = int(saved["report"]["overall_remarks"].split("process ")[1].split()[0])
        self.assertNotEqual(marked_in, os.getpid(), "graded in a worker process")
        with open(out / "results_part1of1.csv", encoding="utf-8-sig") as handle:
            rows = {r["source"]: r for r in csv.DictReader(handle)}
        self.assertEqual(rows["broken.pdf"]["state"], "failed")
        self.assertIn("Schema is too complex", rows["broken.pdf"]["reasons"])
        self.assertIn("handwriting", rows["junk.pdf"]["reasons"])
        self.assertEqual(job["workflows"], {}, "finished copies leave the job record")
        self.assertLess(self.saves, 6 * 9, "stage labels are not saved one by one")

    def test_the_first_wave_starts_one_copy_at_a_time(self):
        started = []
        real = self.server._file_bulk_result

        def note(job_id, sink, name, result, *rest):
            started.append(time.monotonic())
            return real(job_id, sink, name, result, *rest)

        with patch.object(self.server, "_RAMP_SECONDS", 0.5), \
                patch.object(self.server, "_file_bulk_result", note):
            self.start("8")
        # The first copy runs alone; the other five start 0.5 s apart.
        self.assertGreater(max(started) - min(started), 1.5)

    def test_a_paper_fault_while_marking_stops_the_run_instead_of_filing_reviews(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": ""}), \
                patch.object(self.server, "grade_sheet") as grade_here:
            grade_here.side_effect = self.server.StrictGradeError(
                "INVALID_EXAM_PACKAGE", "The question/maxima proof was not issued for this "
                "exact paper, total, and graph.")
            job = self.start("2")
        counts = job["bulk"]["counts"]
        self.assertEqual(counts["faculty_review"], 0, counts)
        self.assertGreaterEqual(counts["failed"], 1)
        self.assertLess(grade_here.call_count, 6, "the run stopped")
        self.assertIn("question paper's own check failed", job["bulk"]["stopped_reason"])
        out = self.root / "copies - evaluated"
        self.assertFalse(list(out.glob("faculty_review/**/*")), "no review requests filed")
        self.assertEqual(self.server.bulk.plan(self.src.resolve(), out.resolve(), 1, 1)
                         ["already_done"], 0, "every copy is graded on the next run")

    def test_a_few_at_a_time_still_grade_in_this_process(self):
        with patch.dict(os.environ, {"AICHECKER_ENGINE_PROCESSES": ""}), \
                patch.object(self.server, "grade_sheet") as grade_here:
            grade_here.side_effect = RuntimeError("graded here")
            job = self.start("2")
        self.assertEqual(self.pools, [], "no worker processes for 2 at a time")
        self.assertEqual(grade_here.call_count, 6)
        self.assertEqual(job["bulk"]["counts"]["failed"], 6)


class Limits(unittest.TestCase):
    def test_folder_mode_allows_thirty_and_defaults_to_twenty(self):
        import server
        self.assertEqual(server._bulk_workers(""), 20)
        self.assertEqual(server._bulk_workers("30"), 30)
        self.assertEqual(server._bulk_workers("50"), 30)
        self.assertEqual(server._bulk_workers("0"), 1)

    def test_the_form_offers_thirty(self):
        root = os.path.dirname(HERE)
        with open(os.path.join(root, "static", "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn('id="bulkWorkers" type="number" min="1" max="30" value="20"', html)

    def test_the_exe_entry_point_lets_worker_processes_start(self):
        root = os.path.dirname(HERE)
        with open(os.path.join(root, "run_client.py"), encoding="utf-8") as handle:
            source = handle.read()
        guard = source.index("multiprocessing.freeze_support()")
        self.assertLess(guard, source.index("import uvicorn"),
                        "a worker must stop before the server is imported")


if __name__ == "__main__":
    unittest.main()
