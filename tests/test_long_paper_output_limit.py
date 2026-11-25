"""A strict paper too long for one reply is graded in bounded batches, not lost."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grader
import pipeline
from costs import record_usage_event
from grader import AuditItem, OutputLimitExceeded
from test_coverage_recovery import fixture


def run_copy(test, qids):
    source, images, pages, rows, report, cfg, scheme = fixture(qids)
    calls = []

    def grade(**kwargs):
        scope = kwargs.get("question_qids")
        calls.append((kwargs["model"], scope))
        if scope is None and kwargs["model"] == cfg.model:
            # The whole-copy reply ran out of output, as haiku did on the
            # 20-question UP LT Grade Home Science paper (23 Oct 2026).
            raise OutputLimitExceeded("runaway")
        record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
            "input_tokens": 12000, "output_tokens": 3000}, stage="retry" if scope else "primary")
        chosen = rows[:1] if scope is None else [q for q in rows if q.qid in scope]
        return report.model_copy(deep=True, update={
            "questions": [q.model_copy(deep=True) for q in chosen]})

    def audit(current, **kwargs):
        record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
            "input_tokens": 14000, "output_tokens": 1600}, stage="verify")
        return [AuditItem(row=i, correct=maximum, evidence_status="verified",
            step_line_id=q.criteria[index].step_line_id, anchor_quote=q.criteria[index].anchor_quote)
            for i, (q, index, maximum) in enumerate(grader._audit_rows(
                current, True, kwargs.get("selected_qids")))]

    cfg.enforce_budget = False
    with patch.object(pipeline, "ocr_all_pages", return_value=pages), \
         patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
         patch.object(pipeline, "claude_grade", side_effect=grade), \
         patch.object(pipeline, "claude_audit", side_effect=audit):
        result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename="copy.pdf", ak_pngs=[],
            ak_text="Award the stated maximum for each correct result.", marks_scheme=scheme,
            cfg=cfg, student_class="10", subject="Maths", total_marks=scheme.total, use_mathpix=True,
            qp_text=f"Maximum Marks: {scheme.total:g}\n" + "\n".join(
                f"{q.qid}. Calculate. ({q.max_score:g} Marks)" for q in rows))
    return result, calls, rows, cfg, scheme


class LongPaperOutputLimitTests(unittest.TestCase):
    def test_long_paper_is_graded_in_batches_after_a_runaway(self):
        qids = [f"Q{i}" for i in range(1, 11)]
        result, calls, rows, cfg, scheme = run_copy(self, qids)
        # One failed whole-copy call, then every question in batches of eight on
        # the same model. No second whole-copy call that a proxy could time out.
        self.assertEqual(calls, [(cfg.model, None),
                                 (cfg.model, qids[:pipeline.EXACT_QUESTION_BATCH_SIZE]),
                                 (cfg.model, qids[pipeline.EXACT_QUESTION_BATCH_SIZE:])])
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["report"].total_score, scheme.total)
        self.assertEqual([q.qid for q in result["report"].questions], qids)
        self.assertEqual(result["report"].coverage_recovered_qids, qids)
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))

    def test_short_paper_still_takes_the_one_call_retry(self):
        result, calls, rows, cfg, scheme = run_copy(self, None)
        qids = [q.qid for q in rows]
        self.assertEqual(calls[:2], [(cfg.model, None), (pipeline._BUDGET_GRADING_MODEL, None)])
        # The retry returned one row; the rest are completed on the retry model.
        self.assertEqual(calls[2:], [(pipeline._BUDGET_GRADING_MODEL, qids[1:])])
        self.assertTrue(result["release"], result["issues"])
        self.assertEqual(result["report"].total_score, scheme.total)


if __name__ == "__main__":
    unittest.main()
