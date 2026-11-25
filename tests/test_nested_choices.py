"""Audit MTS-04: 118 marks of alternatives, 100 after outer and inner choices.

Offline provider responses are fixtures; these tests verify structure and counting,
not a live model's subject judgment or extraction accuracy.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pipeline
import server
from answer_map import AnswerLocation, AnswerMap
from exam_package import ChoiceGroup, build_exam_package, normalize_qid
from grader import (GradeReport, MarksChoiceGroup, MarksChoiceOption, MarksItem,
                    MarksScheme, QuestionMark, printed_tag_sum)
from marks_attestation import (MarksAttestationError, MarksAttestationStore,
                               effective_marks_total, schemes_agree)
from strict_grading import reconcile_report_to_package


def audit_scheme():
    rows = [MarksItem(qid=f"Q1.{i}", max=2) for i in range(1, 16)]
    allocations = {
        1: [("a", 5), ("b.i", 3), ("b.ii", 2), ("c", 4)],
        2: [("a", 5), ("b", 5), ("c", 4)],
        3: [("a", 5), ("b", 5), ("c", 4)],
        4: [("a", 5), ("b", 5), ("c", 4)],
        5: [("a.i", 1), ("a.ii", 1), ("a.remaining", 3), ("b", 5), ("c", 4)],
        6: [("a", 5), ("b", 5), ("c1.i", 2), ("c1.ii", 2), ("c2", 4)],
    }
    rows += [MarksItem(qid=f"Q{number}.{part}", max=maximum)
             for number, parts in allocations.items() for part, maximum in parts]
    outer = MarksChoiceGroup(group_id="part-b", select_count=4, contribution_marks=56,
        options=[MarksChoiceOption(option_id=f"q{i}", question_ids=[
            row.qid for row in rows if row.qid.startswith(f"Q{i}.")]) for i in range(2, 7)])
    inner = MarksChoiceGroup(group_id="q6-c", select_count=1, contribution_marks=4,
        options=[MarksChoiceOption(option_id="c1", question_ids=["Q6.c1.i", "Q6.c1.ii"]),
                 MarksChoiceOption(option_id="c2", question_ids=["Q6.c2"])])
    return MarksScheme(total=118, items=rows, choice_groups=[outer, inner])


SCHEME = audit_scheme()
# Mark-bearing structure transcribed from the supplied 8-page paper, without its
# lengthy question prose. Part A has case totals instead of per-question tags.
PAPER = """Maximum Marks- 100
Part - A
Multiple choice question (MCQ)
(Case Scenario 1 carries 6 Marks)
(Case Scenario 2 carries 10 Marks)
(Case Scenario 3 carries 8 Marks)
General MCQs (3 MCQs × 2 Marks each = 6 Marks)
Case Scenario – I
Directions (1-3)
Case Scenario – II
Directions (4-8)
Case Scenario – III
Directions (9-12)
General MCQs
13. Question
14. Question
15. Question
Part – B
Descriptive Questions
Question No. 1 is compulsory.
Attempt any four questions from the Rest.
""" + "\n".join(
    f"{row.qid}\n({row.max:g} Marks)" for row in SCHEME.items[15:]
).replace("Q6.c2", "OR\nQ6.c2")


def make_package(scheme=None, policy="first_attempted"):
    scheme = scheme or audit_scheme()
    return build_exam_package(
        exam_type="professional", student_class="CA Inter", subject="Auditing",
        level="Intermediate", paper="MTS-04", attempt="", applicable_law_date=None,
        total_marks=100, question_paper=b"paper", answer_key=b"key",
        key_source="upload", marks_scheme=scheme, scheme_source="text",
        choice_groups=[ChoiceGroup(**group.model_dump(), selection_policy=policy)
                       for group in scheme.choice_groups])


def make_report(qids=None, scores=None):
    rows = {row.qid: row for row in SCHEME.items}
    qids = qids if qids is not None else list(rows)
    return GradeReport(total_score=0, max_total=100, overall_remarks="", questions=[
        QuestionMark(qid=qid, score=(scores or {}).get(qid, rows[qid].max),
                     max_score=rows[qid].max, page=1, y_fraction=.3, attempted=True)
        for qid in qids])


class NestedChoiceTests(unittest.TestCase):
    def test_complete_printed_allocations_include_untagged_mcq_cases(self):
        self.assertEqual(printed_tag_sum(PAPER)[0], 118)
        self.assertEqual(printed_tag_sum(PAPER.replace("Part – B", "Part - B"))[0], 118)

    def test_mcq_summaries_do_not_duplicate_individual_tags(self):
        tagged = PAPER.replace("13. Question", "13. Question\n(2 Marks)")
        self.assertEqual(printed_tag_sum(tagged)[0], 90)
        bad_product = PAPER.replace("= 6 Marks", "= 8 Marks")
        self.assertEqual(printed_tag_sum(bad_product)[0], 112)

    def test_nested_maxima_and_provider_consensus_are_order_independent(self):
        scheme = audit_scheme()
        second = scheme.model_copy(deep=True)
        second.choice_groups.reverse()
        second.items.reverse()
        for group in second.choice_groups:
            group.group_id += "-other"
            group.options.reverse()
        self.assertEqual(effective_marks_total(scheme), 100)
        self.assertEqual(effective_marks_total(second), 100)
        self.assertTrue(schemes_agree(scheme, second))
        self.assertFalse(make_package(second).blockers())

    def test_missing_child_choice_cannot_be_attested(self):
        broken = audit_scheme()
        broken.choice_groups.pop()
        with self.assertRaises(MarksAttestationError):
            effective_marks_total(broken)
        self.assertFalse(schemes_agree(broken, broken))
        self.assertTrue(make_package(broken).blockers())

    def test_crossing_and_same_group_overlaps_stay_blocked(self):
        for mode in ("crossing", "duplicate", "split-child"):
            with self.subTest(mode=mode):
                broken = audit_scheme()
                if mode == "crossing":
                    broken.choice_groups[1].options[1].question_ids = ["Q2.c"]
                elif mode == "duplicate":
                    broken.choice_groups[0].options[1].question_ids += ["Q2.a"]
                else:
                    broken.choice_groups[0].options[-1].question_ids.remove("Q6.c2")
                    broken.choice_groups[0].options[0].question_ids.append("Q6.c2")
                self.assertFalse(schemes_agree(broken, broken))
                self.assertIn("overlapping-choice-membership",
                              {issue.code for issue in make_package(broken).blockers()})

    def test_each_valid_combination_can_earn_exactly_100(self):
        for selected in itertools.combinations(range(2, 7), 4):
            for inner in ("c1", "c2"):
                with self.subTest(selected=selected, inner=inner):
                    qids = [row.qid for row in SCHEME.items
                            if row.qid.startswith("Q1.") or
                            any(row.qid.startswith(f"Q{i}.") for i in selected)]
                    excluded = "Q6.c2" if inner == "c1" else "Q6.c1"
                    qids = [qid for qid in qids if not qid.startswith(excluded)]
                    result = reconcile_report_to_package(make_report(qids), make_package())
                    self.assertEqual(result.total_score, 100)
                    self.assertEqual(result.max_total, 100)
                    self.assertEqual(sum(q.max_score for q in result.questions), 100)

    def test_child_cannot_reintroduce_excluded_q6(self):
        for reverse in (False, True):
            scheme = audit_scheme()
            if reverse:
                scheme.choice_groups.reverse()
            package = make_package(scheme)
            pending = reconcile_report_to_package(make_report(), package, finalize_choices=False)
            self.assertFalse(any(qid.startswith("Q6.") for qid in pending.choice_candidate_qids))
            result = reconcile_report_to_package(pending, package)
            self.assertEqual(result.total_score, 100)
            self.assertFalse(any(q.qid.startswith("Q6.") for q in result.questions))

    def test_physical_order_selects_q6_and_c2_even_when_provider_lists_c1_first(self):
        package = make_package()
        positions = {qid: (1, i + 1) for i, qid in enumerate(
            [row.qid for row in SCHEME.items if row.qid.startswith("Q1.")]
            + ["Q6.c2", "Q6.a", "Q6.b", "Q6.c1.i", "Q6.c1.ii"]
            + [row.qid for row in SCHEME.items if row.qid.startswith(("Q2.", "Q3.", "Q4.", "Q5."))])}
        answer_map = AnswerMap(package_id=package.package_id, locations=tuple(
            AnswerLocation(qid=qid, status="located", first_position=position)
            for qid, position in positions.items()))
        result = reconcile_report_to_package(make_report(), package, answer_map=answer_map)
        qids = {q.qid for q in result.questions}
        self.assertIn("Q6.c2", qids)
        self.assertNotIn("Q6.c1.i", qids)
        self.assertFalse(any(qid.startswith("Q5.") for qid in qids))
        self.assertEqual(result.total_score, 100)

    def test_blank_inner_choice_loses_four_marks_without_shrinking_total(self):
        qids = [row.qid for row in SCHEME.items
                if not row.qid.startswith(("Q5.", "Q6.c"))]
        result = reconcile_report_to_package(make_report(qids), make_package())
        self.assertEqual(result.total_score, 96)
        self.assertEqual(result.max_total, 100)

    def test_best_score_uses_only_one_inner_alternative(self):
        package = make_package(policy="best_score")
        # Q6 earns 8/14 after its OR, despite 12 marks across both alternatives.
        # Q2 earns 10, Q3 11, Q4 12, Q5 14; the four better questions must win.
        scores = {"Q6.a": 2, "Q6.b": 2, "Q2.a": 1, "Q3.a": 2, "Q4.a": 3}
        result = reconcile_report_to_package(make_report(scores=scores), package)
        self.assertFalse(any(q.qid.startswith("Q6.") for q in result.questions))
        self.assertEqual(result.total_score, 91)


class AuditDetectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.scheme = audit_scheme()
        self.store = MarksAttestationStore()
        self.server_patch = patch.multiple(server,
            _require_allowed=lambda token: token or "tester", _MARKS_ATTESTATIONS=self.store,
            JOBS={}, _persist_job=lambda *a: None, _run_job=lambda *a: None,
            pdf_text_layer=lambda *a: PAPER, _paper_text_layer_complete=lambda *a: True)
        self.server_patch.start()
        self.addCleanup(self.server_patch.stop)
        self.pipeline_patch = patch.multiple(pipeline,
            pdf_text_layer=lambda *a: PAPER, pdf_or_image_to_pngs=lambda *a: [],
            _extract_marks=self.extract)
        self.pipeline_patch.start()
        self.addCleanup(self.pipeline_patch.stop)
        self.form = dict(provider="claude", model="", student_class="CA Inter",
                         subject="Auditing", exam_type="professional", total_marks="100", mode="ai")

    def extract(self, pngs, cfg, *args):
        self.calls.append(cfg.provider)
        return self.scheme.model_copy(deep=True)

    def post(self, path, data, files):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://test") as client:
                return await client.post(path, data=data, files=files,
                                         headers={"Authorization": "tester"})
        return asyncio.run(send())

    def detect(self):
        result = self.post("/api/detect-marks", self.form,
                           {"question_paper": ("audit.pdf", b"%PDF-paper", "application/pdf")})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_both_readers_verify_118_alternatives_as_100_and_evaluation_can_queue(self):
        result = self.detect()
        self.assertEqual(result["method"], "ai_verified", result)
        self.assertEqual(result["total"], 118)
        self.assertEqual(result["effective_total"], 100)
        self.assertEqual(len(result["choice_groups"]), 2)
        self.assertTrue(result["marks_attestation"])
        response = self.post("/api/evaluate", {
            **self.form, "key_source": "upload", "marks_items": json.dumps(result["items"]),
            "marks_source": result["method"], "marks_attestation": result["marks_attestation"]},
            {"question_paper": ("audit.pdf", b"%PDF-paper", "application/pdf"),
             "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
             "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.calls, [server.CLAUDE])  # one reading, no second reader

    def test_120_mark_extraction_stays_blocked_with_the_actual_reason(self):
        self.scheme.items[16].max += 2
        self.scheme.total = 120
        result = self.detect()
        self.assertEqual(result["method"], "ai_mismatch")
        self.assertFalse(result["marks_attestation"])
        self.assertIn("120", result["verification_detail"])
        self.assertIn("118", result["verification_detail"])
        self.assertEqual(self.calls, [server.CLAUDE])


if __name__ == "__main__":
    unittest.main()
