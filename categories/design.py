# -*- coding: utf-8 -*-
"""Design and drawing papers. Graded by design_grading and drawn by design_renderer;
this only names the category and its release rule."""
from __future__ import annotations

from .base import Category, DetectionRules, ReviewPolicy

DESIGN = Category(
    id="design",
    display_name="Design / drawing",
    ui_hint="Hand drawn design papers. Every copy goes to faculty review until the "
            "marking is calibrated.",
    pipeline="design",
    department="school",
    legacy_exam_type="school",
    detection=DetectionRules(auto_detectable=False),
    review=ReviewPolicy(auto_release=False),
)
