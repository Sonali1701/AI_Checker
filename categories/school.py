# -*- coding: utf-8 -*-
"""School boards (CBSE, ICSE, ISC, state boards), Class 1 to 12."""
from __future__ import annotations

from .base import Category, ChoicePolicy, CostPolicy, RenderPolicy

SCHOOL = Category(
    id="school",
    display_name="School board",
    ui_hint="CBSE, ICSE, ISC and state board papers. Where a student answers both "
            "sides of an OR, the better answer counts.",
    pipeline="written",
    department="school",
    legacy_exam_type="school",
    choice=ChoicePolicy(selection_policy="best_score"),
    render=RenderPolicy(mark_scale=1.0),
    cost=CostPolicy(budget_inr=12.0),
)
