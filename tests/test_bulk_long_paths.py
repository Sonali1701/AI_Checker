# -*- coding: utf-8 -*-
"""Folder mode files graded copies whose paths pass Windows' 260-character limit.

MEASURED 2026-10-09 (CA folder run): "<folder> - evaluated/faculty_review/
REVIEW_<uuid>.pdf.part" was 274 characters, so FolderSink.file raised
FileNotFoundError for every copy of six folders after it had been graded and paid for.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bulk  # noqa: E402


class LongPaths(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        deep = root
        while len(str(deep)) < 200:
            deep = deep / "Business_Laws_Master_Test_Series_01"
        self.source = deep
        self.target = Path(str(deep) + " - evaluated")
        bulk._write_atomic(self.source / "x.pdf", b"%PDF-1.4 student")

    def test_a_graded_copy_is_filed_past_260_characters(self):
        sink = bulk.FolderSink(self.source, self.target, 1, 1)
        rel = "23a44745-8786-4627-93e8-18de96131d91.pdf"
        result = {"state": "faculty_review", "review_reasons": ["CA copies go to faculty review"],
                  "score": 30, "max": 50}
        out = sink.file(rel, result, b"%PDF-1.4 graded", {"report": {}})
        full = self.target / out
        self.assertGreater(len(str(full)), 260)
        with open(bulk._os_path(full), "rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 graded")
        self.assertIn(rel, bulk.finished_copies(self.target))

    def test_reading_a_deep_source_copy(self):
        sink = bulk.FolderSink(self.source, self.target, 1, 1)
        self.assertEqual(sink.load("x.pdf"), b"%PDF-1.4 student")

    def test_short_paths_are_left_alone(self):
        self.assertEqual(bulk._os_path(Path("C:/short/file.pdf")), str(Path("C:/short/file.pdf")))


if __name__ == "__main__":
    unittest.main()
