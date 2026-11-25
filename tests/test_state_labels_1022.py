# -*- coding: utf-8 -*-
"""Cover and margin labels read as the student's paper prints them (22 Oct 2026 audit).

* MP Board Accountancy covers read "Q9(alt2)" and "Q11(alt1): ... not attempted":
  internal ids for the paper's OR alternatives.
* MP Board papers number Q1 to Q11 straight through, with True/False and fill-ins at
  Q3 to Q6. The cover listed those after the MCQs under "Descriptive". The kind
  headings are for papers whose parts restart their numbers (CA Part I / Part II).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from grader import GradeReport, QuestionMark  # noqa: E402
from pdf_renderer import _kinds_restart_numbering, build_evaluated_pdf  # noqa: E402
from wording import question_label  # noqa: E402


def _cover_text(questions):
    doc = fitz.open()
    page = doc.new_page()
    for index, question in enumerate(questions):
        page.insert_text((80, 100 + 60 * index), f"{question.qid}) answer", fontsize=12)
    source = doc.tobytes()
    doc.close()
    report = GradeReport(total_score=sum(q.score for q in questions),
                         max_total=sum(q.max_score for q in questions),
                         overall_remarks="", questions=questions)
    pdf = build_evaluated_pdf(source, "copy.pdf", [], report)
    with fitz.open(stream=pdf, filetype="pdf") as rendered:
        return report, rendered[0].get_text()


class AlternativeLabels(unittest.TestCase):
    def test_or_alternatives_read_as_the_paper_prints_them(self):
        self.assertEqual(question_label("Q9.alt2"), "Q9 (OR)")
        self.assertEqual(question_label("Q11.alt1"), "Q11")
        self.assertEqual(question_label("Q10.alt3"), "Q10 (OR 2)")
        self.assertEqual(question_label("Q7.OR"), "Q7 (OR)")
        self.assertEqual(question_label("QB4.A"), "Q4(A)")       # unchanged
        self.assertEqual(question_label("Q1.ii"), "Q1(ii)")      # unchanged


class MathsInTheMargin(unittest.TestCase):
    """MH and MP Board Maths copies (22 Oct 2026): the margin drew "A□B" and "(f□g)",
    the cover "(fg)" and "f1(x)", and "B transpose = -B" read "= , B"."""

    def test_symbols_are_words_in_the_margin_and_on_the_cover(self):
        from pdf_renderer import _margin_reason, _sanitize_for_pdf
        self.assertEqual(_margin_reason("A∪B union set is omitted.", ""),
                         "A union B union set is omitted.")
        self.assertIn("(f o g)(x)", _sanitize_for_pdf("(f∘g)(x)=x"))
        self.assertIn("f inverse(x)", _sanitize_for_pdf("f⁻¹(x)=(x-3)/4"))
        self.assertIn("B transpose", _sanitize_for_pdf("Bᵀ = -B"))

    def test_a_minus_before_a_capital_letter_stays_a_minus(self):
        from wording import plain_sentences
        self.assertEqual(plain_sentences("B transpose = -B"), "B transpose = minus B.")
        self.assertEqual(plain_sentences("named - not explained"), "Named, not explained.")


class CoverSections(unittest.TestCase):
    def test_straight_numbering_is_listed_in_paper_order_without_kind_headings(self):
        questions = [QuestionMark(qid="Q1", score=1, max_score=1, objective=True, chosen_option="B",
                                  page=1, y_fraction=.1, attempted=True),
                     QuestionMark(qid="Q2", score=1, max_score=1, page=1, y_fraction=.2, attempted=True),
                     QuestionMark(qid="Q3", score=0, max_score=1, objective=True, chosen_option="A",
                                  page=1, y_fraction=.3, attempted=True),
                     QuestionMark(qid="Q4", score=2, max_score=3, page=1, y_fraction=.4, attempted=True)]
        report, text = _cover_text(questions)
        self.assertEqual([s["qid"] for s in report.section_totals], ["Q1", "Q2", "Q3", "Q4"])
        self.assertNotIn("Descriptive", text)
        self.assertNotIn("Objective", text)

    def test_restarted_numbers_keep_their_headings(self):
        # CA FM: Part A objective Q1 to Q4, Part B QB1 to QB3 printed "Part B Q1".
        self.assertTrue(_kinds_restart_numbering(
            [{"qid": "Q1", "objective": True}, {"qid": "QB1", "objective": False}]))
        self.assertFalse(_kinds_restart_numbering(
            [{"qid": "Q1", "objective": True}, {"qid": "Q3", "objective": False}]))


if __name__ == "__main__":
    unittest.main()
