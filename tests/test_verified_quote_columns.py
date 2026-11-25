"""Owned table evidence must win over a similar column heading."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anchors import _quote_score
from grading_quality import _owned_verifier_line_id
from mathpix import LineBox


QUOTE = "Socialism follow social welfare, it mainly focus on equality"


class VerifiedQuoteColumnTests(unittest.TestCase):
    def test_wrapped_content_outweighs_similar_heading(self):
        heading = LineBox("P1L1", "Capitalism", (50, 50, 150, 70), 1)
        content = LineBox("P1L2", "Socialism follow social", (300, 100, 520, 120), 1)
        self.assertGreater(_quote_score(QUOTE, content.text), _quote_score(QUOTE, heading.text))
        self.assertEqual(_owned_verifier_line_id("P1L1", QUOTE, [], [heading, content]), "P1L2")

    def test_repeated_strong_quote_does_not_choose_between_rows(self):
        rows = [LineBox(f"P1L{i}", "Socialism follow social", (50, 30*i, 300, 30*i+20), 1)
                for i in (1, 2)]
        self.assertEqual(_owned_verifier_line_id("P1L99", QUOTE, [], rows), "P1L99")

    def test_owned_visual_box_keeps_its_entry_despite_repeated_words(self):
        rows = [LineBox("P1L1", "Unreliable transcription", (50, 50, 300, 70), 1),
                LineBox("P1L2", QUOTE, (50, 150, 500, 170), 1)]
        self.assertEqual(_owned_verifier_line_id("P1L1", QUOTE, [.1, .1, .3, .12], rows), "P1L1")

    def test_common_three_word_intro_cannot_supply_strong_evidence(self):
        self.assertLess(_quote_score("This is the reason for all remaining examples afterwards", "This is the"), 1.5)


if __name__ == "__main__":
    unittest.main()
