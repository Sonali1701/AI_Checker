# -*- coding: utf-8 -*-
"""Everything the exam category decides (prompt bytes, mark size, budget, second
examiner, department, OR-choice rule) is pinned for 48 class/subject/exam-type
combinations. Moving the category logic into categories/ must not move any of it.

After a DELIBERATE change, re-record with `python tools/category_golden.py --write`
and say in the commit which setting moved and why.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.category_golden import GOLDEN, fingerprint  # noqa: E402


class CategoryGolden(unittest.TestCase):
    def test_nothing_the_category_decides_has_moved(self):
        old = json.load(open(GOLDEN, encoding="utf-8"))
        now = fingerprint()
        self.assertEqual(sorted(now), sorted(old))
        moved = {f"{paper}: {field}": (old[paper].get(field), value)
                 for paper, row in now.items() for field, value in row.items()
                 if old[paper].get(field) != value}
        self.assertEqual(moved, {})

    def test_the_fingerprint_tells_the_categories_apart(self):
        now = fingerprint()
        ca = now["auto | CA Inter | Taxation"]
        school = now["auto | Class 9 | Social Science"]
        self.assertNotEqual(ca["system_prompt"], school["system_prompt"])
        self.assertEqual((ca["choice_first"], school["choice_first"]), ("first_attempted", "best_score"))


if __name__ == "__main__":
    unittest.main()
