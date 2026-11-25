"""A marks tag split by a page break, with the running footer between its halves.

PW-exported CS Executive JIGL MTS-1 (4 Nov 2026): "(2" ended page 1, the footer
"Android App | CS" followed, and "marks)" opened page 2. The tag count read 38 for
a 40-mark paper, so the right reading was refused as ai_mismatch.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import printed_tag_sum  # noqa: E402

PAPER = (
    "Q1\n1. Discuss vicarious liability.\n(5 Marks)\n"
    "2. Advise the Plaintiff Company.\n(5 Marks)\n"
    "Q4\n1. Discuss whether the circular issued by STA can\n"
    "be challenged on the ground of ultra vires. (2\n"
    "Android App\n\xa0\xa0\xa0|\xa0\xa0\xa0\nCS\n"
    "marks)\n2. Explain how the principles of natural justice\n"
    "are violated in this case. (3 marks)\n"
)


class SplitTagTest(unittest.TestCase):
    def test_split_tag_is_counted(self):
        self.assertEqual(printed_tag_sum(PAPER), (15.0, 4))

    def test_unsplit_paper_unchanged(self):
        self.assertEqual(printed_tag_sum("Q1 (5 Marks)\nQ2 (3 marks)\n"), (8.0, 2))

    def test_two_real_tags_are_not_merged(self):
        # A digit or bracket between the halves means they are not one tag.
        text = "Q1 answer (2\nQ2 (3 marks)\n"
        self.assertEqual(printed_tag_sum(text), (3.0, 1))

    def test_long_prose_between_is_not_a_footer(self):
        text = "(4\n" + "a line of the question that is much longer than a footer is\n" + "marks)\n"
        self.assertEqual(printed_tag_sum(text), (0, 0))


if __name__ == "__main__":
    unittest.main()
