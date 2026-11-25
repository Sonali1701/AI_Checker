# -*- coding: utf-8 -*-
"""Every copy goes to the student or to a faculty reviewer; the second examiner
corrects marks row by row; a visual inventory stops phantom answers; the key's
MCQ answers are read once per package (2026-09-19, after the CA improper batch).

Each check reproduces a failure from that batch:
  * eleven Terra certificates, Rs.20+ each, moved ZERO marks (all-or-nothing);
  * "3) Re-order level" inside Q2's answer became a phantom Q3 and took a slot;
  * the key's "7.\\n(d)\\nThe correct option is (d)" never reached the MCQ scorer;
  * an unresolved regrade or a forced denominator threw the whole copy away.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402
import pipeline  # noqa: E402
import preflight  # noqa: E402
from answer_inventory import (AnswerInventory, InventoryItem, inventory_prompt_block,  # noqa: E402
                              merge_inventory, take_answer_inventory, verify_inventory)
from answer_map import build_answer_map  # noqa: E402
from automatic_workflow import AutomaticWorkflow, WorkflowState, stage_label  # noqa: E402
from exam_package import _official_objective_answers, build_exam_package  # noqa: E402
from grader import (AuditItem, Criterion, GradeReport, MarksItem, MarksScheme,  # noqa: E402
                    QuestionMark, apply_audit, audit_applicable_verdicts)
from key_answers import (KeyAnswer, KeyAnswerSheet, complete_objective_answers,  # noqa: E402
                         fill_objective_answers, verify_key_answers)
from mathpix import LineBox, PageOCR  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _q(qid, score, max_score, criteria=(), anchor="", attempted=True, objective=False):
    return QuestionMark(qid=qid, score=score, max_score=max_score, page=1, y_fraction=0.0,
                        anchor_line_id=anchor, attempted=attempted, objective=objective,
                        criteria=list(criteria))


def _report(questions, **extra):
    return GradeReport(total_score=sum(q.score for q in questions),
                       max_total=sum(q.max_score for q in questions),
                       overall_remarks="", questions=questions, **extra)


def _package(items, key_text="Official key. Q1 answer: the contract is void. Q2 ..."):
    scheme = MarksScheme(total=sum(m for _, m in items),
                         items=[MarksItem(qid=q, max=m) for q, m in items])
    return build_exam_package(
        exam_type="professional", student_class="CA Inter", subject="Costing", level="",
        paper="", attempt="", applicable_law_date=None, total_marks=scheme.total,
        question_paper=b"%PDF-paper", answer_key=b"%PDF-key", key_source="upload",
        marks_scheme=scheme, scheme_source="regex", question_paper_text="Q1 ... Q2 ...",
        answer_key_text=key_text)


def _page(page, texts, start=1):
    lines = [LineBox(line_id=f"P{page}L{i}", text=t, bbox=(10, 20 * i, 500, 20 * i + 15), page=page)
             for i, t in enumerate(texts, start=start)]
    return PageOCR(page=page, image_width=600, image_height=800, lines=lines)


class RowByRowApplication(unittest.TestCase):
    """The second examiner's sound rulings are applied; the rest stay the first
    examiner's. Before: one bad row voided every row and nothing was applied."""

    def report(self):
        q1 = _q("Q1", 3, 5, [Criterion(description="a", awarded=3, max=3, met=True, step_line_id="P1L1"),
                            Criterion(description="b", awarded=0, max=2, met=False,
                                      evidence="missing conclusion", step_line_id="P1L2")], anchor="P1L1")
        q2 = _q("Q2", 4, 4, [Criterion(description="c", awarded=4, max=4, met=True, step_line_id="P1L3")],
                anchor="P1L3")
        return _report([q1, q2], evidence_contract=True)

    def test_sound_rows_apply_and_unsound_rows_are_named(self):
        report = self.report()
        verdicts = [
            # row 0: Q1 step a, kept at full marks, verified, no anchor: applies.
            AuditItem(row=0, evidence_status="verified", correct=3),
            # row 1: Q1 step b, RESTORED to 2 with a reason and no anchor: applies.
            AuditItem(row=1, evidence_status="verified", correct=2, reason=""),
            # row 2: Q2, uncertain: the first examiner's mark stands.
            AuditItem(row=2, evidence_status="uncertain", correct=1, reason="cannot read"),
        ]
        applicable, unverified = audit_applicable_verdicts(report, verdicts, include_awards=True)
        self.assertEqual([v.row for v in applicable], [0, 1])
        self.assertEqual(set(unverified), {2})
        self.assertIn("not been verified", unverified[2])
        report, moved = apply_audit(report, applicable, include_awards=True)
        self.assertEqual(moved, 2)
        self.assertEqual(report.questions[0].score, 5)
        self.assertEqual(report.questions[1].score, 4)

    def test_a_quarter_mark_a_missing_row_and_a_bare_deduction_stay_with_the_first_examiner(self):
        report = self.report()
        verdicts = [
            AuditItem(row=0, evidence_status="verified", correct=2.25, reason="arithmetic slip"),
            AuditItem(row=2, evidence_status="verified", correct=2, reason=""),
        ]
        applicable, unverified = audit_applicable_verdicts(report, verdicts, include_awards=True)
        self.assertEqual(applicable, [])
        self.assertIn("not a half-mark step", unverified[0])
        self.assertEqual(unverified[1], "no verdict was returned")
        self.assertIn("reason", unverified[2])
        _, moved = apply_audit(report, applicable, include_awards=True)
        self.assertEqual(moved, 0)

    def test_the_pipeline_no_longer_voids_a_certificate_and_verifies_ca_on_luna(self):
        source = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
        self.assertNotIn('"Second examiner did not certify every requested decision: "', source)
        self.assertIn("audit_applicable_verdicts", source)
        self.assertTrue(pipeline._PROFESSIONAL_SECOND_EXAMINER.startswith("gpt-5.6-luna"))


class VisualInventory(unittest.TestCase):
    """CMA MTS-02: "3) Re-order level system" inside Q2's answer became Q3."""

    def fixture(self):
        pkg = _package([("Q1", 5), ("Q2", 5), ("Q3", 5), ("Q4", 5)])
        pages = [_page(1, ["Q1) Marginal costing is", "a technique of costing", "Q2) Inventory control",
                           "1) ABC analysis", "2) VED analysis", "3) Re-order level system",
                           "4) Economic order quantity", "so inventory is controlled"]),
                 _page(2, ["Contribution is sales less variable cost", "so the answer is 40,000"])]
        return pkg, pages, build_answer_map(pkg, pages)

    def test_a_phantom_heading_inside_another_answer_is_dropped(self):
        pkg, pages, amap = self.fixture()
        # The label map's own guard may or may not read "3)" as Q3; force the
        # phantom so the inventory's rule is what is under test.
        from answer_map import AnswerLocation, AnswerMap
        forced = AnswerMap(package_id=amap.package_id, locations=tuple(
            AnswerLocation(qid="Q3", status="located", pages=(1,), label_line_ids=("P1L6",),
                           line_ids=("P1L6", "P1L7"), confidence=0.9)
            if loc.qid == "Q3" else loc for loc in amap.locations))
        inventory = AnswerInventory(items=[
            InventoryItem(qid="Q1", written=True, pages=[1], first_line_id="P1L1", last_line_id="P1L2"),
            InventoryItem(qid="Q2", written=True, pages=[1], first_line_id="P1L3", last_line_id="P1L8"),
            InventoryItem(qid="Q3", written=False),
            InventoryItem(qid="Q4", written=True, pages=[2], first_line_id="P2L1", last_line_id="P2L2"),
        ])
        spans, absent, letters = verify_inventory(inventory, pages, pkg)
        self.assertEqual(sorted(spans), ["Q1", "Q2", "Q4"])
        self.assertEqual(absent, {"Q3"})
        result = merge_inventory(forced, spans, absent, pages)
        self.assertEqual(result.dropped_qids, ["Q3"])
        self.assertIn("Q3", result.absent_qids)
        self.assertEqual(next(l for l in result.answer_map.locations if l.qid == "Q3").status, "unresolved")
        # Q4 had no written heading; the inventory's span locates it, flagged for a visual re-read.
        q4 = next(l for l in result.answer_map.locations if l.qid == "Q4")
        self.assertEqual(q4.status, "located")
        self.assertEqual(list(q4.line_ids), ["P2L1", "P2L2"])
        self.assertEqual(set(q4.uncertain_line_ids), {"P2L1", "P2L2"})
        self.assertIn("Q4", result.disputed_qids)
        block = inventory_prompt_block(result)
        self.assertIn("NOT WRITTEN", block)
        self.assertIn("Q3", block)

    def test_unverifiable_claims_change_nothing(self):
        pkg, pages, amap = self.fixture()
        inventory = AnswerInventory(items=[
            InventoryItem(qid="Q1", written=True, first_line_id="P9L9", last_line_id="P9L9"),
            InventoryItem(qid="Q7", written=True, first_line_id="P1L1", last_line_id="P1L2"),
            InventoryItem(qid="Q2", written=True, first_line_id="P1L1", last_line_id="P2L2"),
        ])
        spans, absent, _ = verify_inventory(inventory, pages, pkg)
        # Q1's ids do not exist; Q7 is not official; Q2 claims 100% of the booklet.
        self.assertEqual(spans, {})
        self.assertEqual(absent, set())
        result = merge_inventory(amap, spans, absent, pages)
        self.assertEqual([l.as_dict() for l in result.answer_map.locations],
                         [l.as_dict() for l in amap.locations])

    def test_the_pass_fails_open(self):
        pkg, pages, amap = self.fixture()

        def broken(*a, **k):
            raise RuntimeError("proxy 502")

        self.assertIsNone(take_answer_inventory(
            package=pkg, answer_map=amap, ocr_pages=pages, student_pngs=[b"png"],
            google_token="t", task_id="k", reader=broken))

        def reads(*a, **k):
            return AnswerInventory(items=[
                InventoryItem(qid="Q1", written=True, first_line_id="P1L1", last_line_id="P1L2",
                              mcq_option="b")])

        result = take_answer_inventory(
            package=pkg, answer_map=amap, ocr_pages=pages, student_pngs=[b"png"],
            google_token="t", task_id="k", reader=reads)
        self.assertEqual(result.mcq_options, {"Q1": ("B", 0)})

    def test_credit_on_an_absent_answer_with_no_evidence_of_its_own_goes_to_faculty(self):
        phantom = _q("Q3", 4, 5, [Criterion(description="x", awarded=4, max=5, met=False,
                                            step_line_id="P1L6")], anchor="P1L6")
        real = _q("Q2", 5, 5, [Criterion(description="y", awarded=5, max=5, met=True,
                                         step_line_id="P1L3")], anchor="P1L3")
        report = _report([real, phantom], inventory_absent_qids=["Q3"],
                         answer_regions={"q2": {"qid": "Q2", "line_ids": ["P1L3", "P1L4", "P1L5", "P1L6"]},
                                         "q3": {"qid": "Q3", "line_ids": []}})
        codes = {i.code for i in preflight.release_blocking(preflight.check_copy(report))}
        self.assertIn("inventory-absent-credited", codes)
        # With a region of its own (the grader found it visually) nothing fires.
        report.answer_regions["q3"]["line_ids"] = ["P2L1"]
        codes = {i.code for i in preflight.release_blocking(preflight.check_copy(report))}
        self.assertNotIn("inventory-absent-credited", codes)


CA_KEY = """Part A
7.
(d)
The correct option is (d) because the perquisite is taxable.
8.
(a)
The correct option is (a) since the deduction is not available.
Part B
1. (a) Computation of total income ...
"""


class KeyAnswers(unittest.TestCase):
    def test_the_ca_key_shape_is_read_for_a_declared_objective_number(self):
        paper = "Questions 7 to 8 are MCQs of 2 marks each.\n7. Which ... (a) x (b) y (c) z (d) w\n8. ..."
        self.assertEqual(_official_objective_answers(CA_KEY, paper), {7: "D", 8: "A"})

    def test_a_claim_needs_a_quote_that_is_in_the_key_and_names_the_letter(self):
        pkg = _package([("Q7", 2), ("Q8", 2), ("Q9", 5)], key_text=CA_KEY)
        sheet = KeyAnswerSheet(items=[
            KeyAnswer(qid="Q7", objective=True, option="d", quote="The correct option is (d)"),
            KeyAnswer(qid="Q8", objective=True, option="c", quote="The correct option is (a) since"),
            KeyAnswer(qid="Q9", objective=True, option="b", quote="Answer is (b) obviously"),
        ])
        self.assertEqual(verify_key_answers(sheet, CA_KEY, pkg), {"Q7": "D"})
        updated, changed = fill_objective_answers(pkg, {"Q7": "D"})
        self.assertEqual(changed, ["Q7"])
        q7 = next(q for q in updated.questions if q.qid == "Q7")
        self.assertEqual((q7.question_type, q7.objective_answer), ("objective", "D"))
        self.assertNotEqual(updated.package_id, pkg.package_id)
        self.assertEqual(updated.blockers(), [])

    def test_the_step_fails_open_and_runs_once_per_package(self):
        pkg = _package([("Q7", 2), ("Q9", 5)], key_text=CA_KEY)

        def broken(*a, **k):
            raise RuntimeError("proxy down")

        same, changed = complete_objective_answers(pkg, CA_KEY, google_token="t", task_id="k",
                                                   reader=broken)
        self.assertIs(same, pkg)
        self.assertEqual(changed, [])
        calls = []

        def reads(package, key, **k):
            calls.append(package.package_id)
            return KeyAnswerSheet(items=[
                KeyAnswer(qid="Q7", objective=True, option="D", quote="the correct option is (d)")])

        updated, changed = complete_objective_answers(pkg, CA_KEY, google_token="t", task_id="k",
                                                      reader=reads)
        self.assertEqual(changed, ["Q7"])
        self.assertEqual(len(calls), 1)
        source = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        self.assertIn("complete_objective_answers(", source)


class TwoOutcomes(unittest.TestCase):
    def test_a_review_reason_holds_the_copy_for_faculty_not_the_bin(self):
        report = _report([_q("Q1", 3, 5, [Criterion(description="a", awarded=3, max=5, met=False,
                                                   evidence="missed the conclusion")])],
                         review_reasons=["Q1: marks breakdown could not be completed automatically"])
        held = preflight.release_blocking(preflight.check_copy(report))
        self.assertEqual([i.code for i in held], ["faculty-review"])
        self.assertIn("faculty-review", preflight.SCORE_INTEGRITY_CODES)

    def test_the_workflow_can_send_a_copy_to_faculty_from_the_gate_or_earlier(self):
        flow = AutomaticWorkflow()
        for state in (WorkflowState.PACKAGE_VALIDATING, WorkflowState.PACKAGE_VALIDATED,
                      WorkflowState.NORMALIZING, WorkflowState.OCR, WorkflowState.ANSWER_MAPPING,
                      WorkflowState.PRIMARY_GRADING, WorkflowState.VERIFYING,
                      WorkflowState.AGGREGATING, WorkflowState.RENDERING, WorkflowState.RELEASE_CHECK):
            flow.transition(state)
        flow.faculty_review("FACULTY_REVIEW", "Q3 needs a look")
        self.assertEqual(flow.state, WorkflowState.FACULTY_REVIEW)
        self.assertEqual(stage_label(flow.state), "Sent for faculty review")
        early = AutomaticWorkflow()
        early.transition(WorkflowState.PACKAGE_VALIDATING)
        early.transition(WorkflowState.PACKAGE_VALIDATED)
        early.transition(WorkflowState.NORMALIZING)
        early.faculty_review("WRONG_QUESTION_PAPER", "another paper")
        self.assertEqual(early.state, WorkflowState.FACULTY_REVIEW)

    def test_strict_paths_that_used_to_refuse_a_copy_now_keep_it(self):
        source = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
        for code in ("GRADING_RECOVERY_INCOMPLETE", "OFFICIAL_TOTAL_MISMATCH",
                     "FINAL_DENOMINATOR_MISMATCH", "PRIMARY_REPORT_INCOMPLETE"):
            self.assertIsNone(re.search(rf'raise StrictGradeError\(\s*"{code}"', source), code)
        self.assertIn("review_reasons.append", source)
        server = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        self.assertIn('"faculty_review"', server)
        self.assertNotIn('"state": "ungradable"', server)

    def test_an_upload_that_is_not_a_genuine_copy_is_rejected_not_queued_for_faculty(self):
        import server
        # Typed pages, notes, a blank scan and a booklet for another test are refused
        # outright; a genuine copy with a line aimed at the grader goes to faculty.
        # ...and the same file a second time in one batch is graded once (build 72).
        self.assertEqual(server._REJECTED_UPLOAD_CODES,
                         frozenset({"NOT_AN_ANSWER_SHEET", "WRONG_QUESTION_PAPER",
                                    "DUPLICATE_UPLOAD"}))
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"rejected": True, "state": "rejected"', source)
        with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as handle:
            browser = handle.read()
        self.assertIn('r.state === "rejected"', browser)
        self.assertIn("Rejected (not genuine)", browser)
        # A rejected upload's status names the reason and never carries marks or a PDF.
        server.JOBS["rej"] = {"status": "done", "total": 1, "done": 1, "key": "k", "pdfs": {},
                              "results": [{"index": 0, "student": "notes.pdf", "ok": False,
                                           "release": False, "rejected": True, "state": "rejected",
                                           "error": "This upload looks like study notes."}]}
        try:
            import json
            public = json.loads(server.job_status("rej", "k").body)["results"][0]
            self.assertEqual((public["state"], public["release"]), ("rejected", False))
            self.assertIn("study notes", public["error"])
            self.assertNotIn("score", public)
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                server.job_pdf("rej", 0, "k")
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            server.JOBS.pop("rej", None)

    def test_an_mcq_without_an_official_answer_keeps_the_graders_mark(self):
        from strict_grading import apply_official_objective_evidence
        scheme = MarksScheme(total=7, items=[MarksItem(qid="Q1", max=2, question_type="objective"),
                                             MarksItem(qid="Q2", max=5)])
        pkg = build_exam_package(
            exam_type="professional", student_class="CA Inter", subject="Tax", level="", paper="",
            attempt="", applicable_law_date=None, total_marks=7, question_paper=b"p", answer_key=b"k",
            key_source="upload", marks_scheme=scheme, scheme_source="regex",
            question_paper_text="1. Which? (a) x (b) y", answer_key_text="Worked solutions only.")
        pages = [_page(1, ["1) (b)", "Q2) Income is computed"])]
        q1 = _q("Q1", 2, 2, anchor="P1L1")
        q1.chosen_option = "B"
        report = _report([q1, _q("Q2", 3, 5, anchor="P1L2")], evidence_contract=True)
        notes = apply_official_objective_evidence(report, pkg, pages, question_paper_text="1. Which? (a) x (b) y")
        self.assertEqual(report.questions[0].score, 2)      # the grader's mark stands
        self.assertIn("Q1", report.objective_unconfirmed_qids)
        self.assertTrue(any("official key gives no option" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
