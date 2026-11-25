"""Replay the build-15 label mismatch and explicit nested-choice representation.

Provider transports are simulated; source parsing, both adapters, reconciliation,
proof issuance and evaluation queue validation use production code.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader
import pipeline
from marks_attestation import reconcile_marks_schemes, effective_marks_total, schemes_agree
from paper_structure import ca_paper_structure
import test_ca_extraction as ca
from test_marks_visual_recovery import general_reading, page_png


def recorded_verifier_reading(*, general=False):
    """Build-15 log: same 38 maxima/choices; Q5.A.iii versus Q5.a.remaining."""
    reading = general_reading() if general else copy.deepcopy(ca.GOOD)

    def label(qid):
        return qid.upper().replace("Q5.A.REMAINING", "Q5.A.iii")

    if general:
        for item in reading["items"]:
            item["qid"] = label(item["qid"])
        groups = reading["choice_groups"]
    else:
        reading["items"] = {label(qid): value for qid, value in reading["items"].items()}
        groups = reading["choice_groups"].values()
    for group in groups:
        for option in group["options"]:
            option["question_ids"] = [label(qid) for qid in option["question_ids"]]
    return reading


def reference_reading():
    """Explicit fixed qids plus a child group; no invented memberships or count."""
    reading = copy.deepcopy(ca.GOOD)
    outer, inner = reading["choice_groups"].values()
    outer["options"][-1] = {"qids": ["Q6.a", "Q6.b"], "groups": ["G2"]}
    for option in outer["options"][:-1] + inner["options"]:
        option["qids"] = option.pop("question_ids")
        option.pop("option_id")
    return reading


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.source = ca_paper_structure(ca.PAPER)
        self.expected = grader.parse_strict_marks_scheme(json.dumps(ca.GOOD), structure=self.source)

    def parse(self, reading, **kwargs):
        return grader.parse_strict_marks_scheme(json.dumps(reading), **kwargs)

    def test_recorded_alias_maps_rows_and_every_choice_reference(self):
        for general in (False, True):
            with self.subTest(general=general):
                result = self.parse(recorded_verifier_reading(general=general), structure=self.source)
                self.assertTrue(schemes_agree(self.expected, result))
                self.assertEqual({item.qid: item.max for item in result.items}, ca.EXPECTED)
                self.assertEqual((result.total, effective_marks_total(result)), (118, 100))
                self.assertIn("Q5.a.remaining", result.choice_groups[0].options[3].question_ids)

    def test_visual_recheck_uses_source_names_without_enforcing_registry_roster(self):
        reading = recorded_verifier_reading(general=True)
        # A visual reader can correct a roster; source_structure only supplies names.
        reading["items"].append({"qid": "Q99", "max": 2})
        result = self.parse(reading, source_structure=self.source)
        self.assertIn("Q5.a.remaining", {item.qid for item in result.items})
        self.assertEqual(effective_marks_total(result), 102)
        self.assertFalse(schemes_agree(self.expected, result))

    def test_no_source_means_no_invented_roman_alias(self):
        result = self.parse(recorded_verifier_reading())
        # The parser keeps the reader's own spelling when no source names exist...
        self.assertIn("Q5.A.iii", {item.qid for item in result.items})
        # ...and the agreement check still passes, because the marks and the choice
        # rules are the same graph: a name is not a mark (operator's rule,
        # 2026-09-17), and the attested consensus carries the primary's names.
        self.assertTrue(schemes_agree(self.expected, result))
        consensus = reconcile_marks_schemes(self.expected, result)
        self.assertIn("Q5.a.remaining", {item.qid for item in consensus.items})

    def test_an_actual_printed_roman_numeral_is_never_an_unnumbered_alias(self):
        source = ca_paper_structure(ca.PAPER.replace("Further, explain", "(iii) Further, explain"))
        unit = next(u for u in source.units if u.qid == "Q5.a.iii")
        self.assertEqual(unit.aliases, ())
        self.parse(recorded_verifier_reading(), structure=source)
        with self.assertRaises(grader.MarksExtractionError):
            self.parse(ca.GOOD, structure=source)

    def test_noncontiguous_numerals_do_not_generate_an_alias(self):
        source = ca_paper_structure(ca.PAPER.replace("(ii) Second assignment", "(iv) Second assignment"))
        unit = next(u for u in source.units if u.qid == "Q5.a.remaining")
        self.assertEqual(unit.aliases, ())

    def test_wrong_marks_cannot_be_repaired_by_label_matching(self):
        reading = recorded_verifier_reading()
        reading["items"]["Q5.A.iii"] = 4
        with self.assertRaisesRegex(grader.MarksExtractionError, "Q5.a.remaining: read 4, printed 3"):
            self.parse(reading, structure=self.source)

    def test_alias_and_canonical_rows_cannot_collapse_a_duplicate(self):
        reading = recorded_verifier_reading()
        reading["items"]["Q5.a.remaining"] = 3
        for kwargs in ({"structure": self.source}, {"source_structure": self.source}):
            with self.subTest(kwargs=kwargs), self.assertRaises(grader.MarksExtractionError):
                self.parse(reading, **kwargs)

    def test_aliases_never_match_another_question_by_its_maximum(self):
        reading = recorded_verifier_reading()
        reading["items"]["Q4.A.iii"] = reading["items"].pop("Q5.A.iii")
        with self.assertRaises(grader.MarksExtractionError):
            self.parse(reading, structure=self.source)

    def test_label_normalization_does_not_hide_changed_choice_memberships(self):
        reading = recorded_verifier_reading()
        options = reading["choice_groups"]["G1"]["options"]
        options[0]["question_ids"][-1], options[1]["question_ids"][-1] = (
            options[1]["question_ids"][-1], options[0]["question_ids"][-1])
        result = self.parse(reading, structure=self.source)
        self.assertEqual(effective_marks_total(result), 100)
        self.assertFalse(schemes_agree(self.expected, result))


class NestedReferenceTests(unittest.TestCase):
    def parse(self, reading):
        return grader.parse_strict_marks_scheme(json.dumps(reading))

    def test_explicit_references_and_non_scoring_labels_preserve_the_graph(self):
        expected = self.parse(ca.GOOD)
        for reverse in (False, True):
            reading = reference_reading()
            if reverse:
                reading["choice_groups"] = dict(reversed(list(reading["choice_groups"].items())))
            with self.subTest(reverse=reverse):
                result = self.parse(reading)
                self.assertTrue(schemes_agree(expected, result))
                self.assertEqual((result.total, effective_marks_total(result)), (118, 100))

    def test_group_only_options_and_multiple_levels_expand_in_any_order(self):
        groups = {
            "outer": {"select_count": 1, "options": [{"qids": ["Q1"]},
                {"qids": ["Q2"], "groups": ["middle"]}]},
            "middle": {"select_count": 1, "options": [{"qids": ["Q3"]},
                {"groups": ["inner"]}]},
            "inner": {"select_count": 1, "options": [{"qids": ["Q4"]}, {"qids": ["Q5"]}]},
        }
        for order in (groups, dict(reversed(list(groups.items())))):
            result = self.parse({"items": {"Q1": 3, "Q2": 1, "Q3": 2, "Q4": 2, "Q5": 2},
                                 "choice_groups": order})
            self.assertEqual(effective_marks_total(result), 3)

    def test_malformed_or_ambiguous_references_are_not_ignored(self):
        for defect in ("unknown", "cycle", "repeated-ref", "duplicate-leaf", "bad-type",
                       "conflicting-qids", "conflicting-camelcase", "missing-count", "unknown-leaf"):
            reading = reference_reading()
            outer, inner = reading["choice_groups"].values()
            option = outer["options"][-1]
            if defect == "unknown":
                option["groups"] = ["not-a-group"]
            elif defect == "cycle":
                inner["options"][0]["groups"] = ["G1"]
            elif defect == "repeated-ref":
                option["groups"] = ["G2", "G2"]
            elif defect == "duplicate-leaf":
                option["qids"].append("Q6.c2")
            elif defect == "bad-type":
                option["groups"] = "G2"
            elif defect == "conflicting-qids":
                option["question_ids"] = ["Q5.b", "Q5.c"]
            elif defect == "conflicting-camelcase":
                option["questionIds"] = ["Q5.b", "Q5.c"]
                option["question_ids"] = ["Q6.a", "Q6.b"]
            elif defect == "missing-count":
                del inner["select_count"]
            else:
                inner["options"][0]["qids"].append("Q99")
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                self.parse(reading)

    def test_ambiguous_group_labels_cannot_resolve_a_reference(self):
        reading = reference_reading()
        groups = [dict(group, group_id=gid) for gid, group in reading["choice_groups"].items()]
        groups.append(copy.deepcopy(groups[-1]))
        reading["choice_groups"] = groups
        with self.assertRaisesRegex(grader.MarksExtractionError, "ambiguous nested choice"):
            self.parse(reading)

    def test_generated_label_cannot_steal_an_explicit_reference_target(self):
        reading = reference_reading()
        outer, inner = reading["choice_groups"].values()
        outer["options"][-1]["groups"] = ["choice-1"]
        reading["choice_groups"] = [outer, dict(inner, group_id="choice-1")]
        result = self.parse(reading)
        self.assertTrue(schemes_agree(self.parse(ca.GOOD), result))


class ProviderIdentityTests(unittest.TestCase):
    def setUp(self):
        self.flow = ca.ProviderFlowTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def assert_ready(self, result, calls):
        self.assertEqual(result["method"], "ai_verified", result)
        self.assertEqual((len(result["items"]), result["total"], result["effective_total"]), (38, 118, 100))
        self.assertEqual(len(self.flow.calls), calls)
        queued = self.flow.post("/api/evaluate", {**self.flow.form, "key_source": "upload",
            "marks_items": json.dumps(result["items"]), "marks_source": result["method"],
            "marks_attestation": result["marks_attestation"]},
            {"question_paper": ("audit.pdf", b"%PDF-paper", "application/pdf"),
             "answer_key": ("key.pdf", b"%PDF-key", "application/pdf"),
             "answer_sheets": ("copy.pdf", b"%PDF-copy", "application/pdf")})
        self.assertTrue(queued["job_id"])

    def test_recorded_case_and_unnumbered_labels_need_no_extra_call(self):
        self.flow.reply = recorded_verifier_reading()
        self.assert_ready(self.flow.detect(), 1)

    def test_nested_reference_form_needs_no_extra_call(self):
        self.flow.reply = reference_reading()
        self.assert_ready(self.flow.detect(), 1)

    def test_recorded_visual_recheck_labels_also_reach_evaluation(self):
        def reply(*args, **kwargs):
            self.flow.reply = (dict(copy.deepcopy(ca.GOOD), structure_ok=False)
                               if not self.flow.calls else recorded_verifier_reading(general=True))
            return self.flow.claude(*args, **kwargs)
        with patch.object(grader, "_claude_call", reply), \
             patch.object(pipeline, "pdf_or_image_to_pngs", return_value=[page_png()]):
            self.assert_ready(self.flow.detect(), 2)


if __name__ == "__main__":
    unittest.main()
