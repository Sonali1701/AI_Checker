# -*- coding: utf-8 -*-
"""Fingerprint of everything the exam category decides today.

    python tools/category_golden.py           # compare with tests/category_golden.json
    python tools/category_golden.py --write   # record it (only after a deliberate change)

Prompt texts are hashed, settings are kept as values. tests/test_category_golden.py
fails when any of them moves, so the move of the category logic into categories/
can be shown to change no prompt byte, no mark size, no budget and no examiner.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOLDEN = os.path.join(ROOT, "tests", "category_golden.json")

PAPERS = [
    ("CA Inter", "Taxation"), ("CA Foundation", "Accounting"), ("CA Final", "Financial Reporting"),
    ("CMA Inter", "Cost Accounting"), ("Class 11", "Accountancy"), ("Class 12", "Business Studies"),
    ("Class 12", "Economics"), ("Class 12", "Applied Mathematics"), ("Class 9", "Economics"),
    ("Class 9", "Social Science"), ("Class 10", "Physics"), ("Class 12", "Chemistry"),
    ("Class 12", "English"), ("Class 10", "Hindi"), ("Class 9", "Biology"), ("", ""),
]
EXAM_TYPES = ("", "school", "professional")


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprint() -> dict:
    import costs
    import exam_package
    import grader
    import pipeline
    import strict_grading
    out = {}
    for et in EXAM_TYPES:
        for cls, subj in PAPERS:
            cfg = pipeline.cfg_from_request("claude", "", subj, google_token="", task_id="",
                                            student_class=cls, exam_type=et)
            strict = pipeline.ProviderConfig(exam_type=cfg.exam_type, grading_contract="exam_package_v2",
                                             category=cfg.category)
            dept = exam_package.department_for(et, cls, subj)
            out[f"{et or 'auto'} | {cls or '-'} | {subj or '-'}"] = {
                "exam_type": cfg.exam_type,
                "system_prompt": _h(grader.build_system_prompt(subj, cls, et)),
                "recovery_prompt": _h(grader.build_system_prompt(subj, cls, et, recovery=True)),
                "context": _h(grader.exam_context_block(cls, subj, et, 80)),
                "rubric_prompt": _h(grader.build_rubric_prompt(subj, cls, et)),
                "marks_prompt": _h(grader.build_marks_scheme_prompt(subj, cls, et)),
                # Read through the category the config resolved, the way the app does.
                "mark_scale": pipeline.mark_scale_for(cfg.exam_type, subj, cfg.category),
                "budget_inr": costs.budget_for(None, exam_type=cfg.exam_type, subject=subj,
                                               category=cfg.category),
                "moderation": pipeline.moderation_applies(cfg, subj),
                "examiners": pipeline.verifier_ladder_for(strict, subj),
                "department": dept,
                "choice_first": strict_grading.effective_selection_policy(
                    NS(selection_policy="first_attempted"), NS(department=dept)),
                "choice_best": strict_grading.effective_selection_policy(
                    NS(selection_policy="best_score"), NS(department=dept)),
            }
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    now = fingerprint()
    if "--write" in sys.argv:
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(now, f, indent=1, sort_keys=True)
        print("recorded", len(now), "papers to", GOLDEN)
        return 0
    old = json.load(open(GOLDEN, encoding="utf-8"))
    moved = [(k, f, old.get(k, {}).get(f), v) for k, row in now.items() for f, v in row.items()
             if old.get(k, {}).get(f) != v]
    for k, f, a, b in moved:
        print(f"{k}: {f} {a} -> {b}")
    print("no change" if not moved else f"{len(moved)} settings changed")
    return 1 if moved else 0


if __name__ == "__main__":
    raise SystemExit(main())
