"""Sociology Q4: the written statement set (B)(C)(D) is option B, not D."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exam_package import build_exam_package
from grader import GradeReport, MarksItem, MarksScheme, QuestionMark
from mathpix import LineBox, PageOCR
from strict_grading import (_objective_choice_from_line, _observed_letter,
    apply_official_objective_evidence, option_named_by_words, option_texts_from_paper,
    reconcile_report_to_package, StrictGradeError)


PAPER = """Q4. Identify the correct statements:
(A) Varna is the generic term most used to refer to the institution of caste in Indian languages.
(B) The 1901 Census, directed by Herbert Risley, focused on collecting information regarding the social hierarchy and rank order of castes.
(C) Recruitment to industrial jobs often continued to be organized along caste and kinship-based lines.
(D) Endogamy has remained strongest in cultural and domestic spheres.
Choose the correct combination:
(A) (A), (C), (D)
(B) (B), (C), (D)
(C) (A), (B), (C)
(D) (A), (B), (D)
Q5. Ordinary MCQ.
(A) Matriliny
(B) Matriarchy
(C) Only inheritance is traced through women.
(D) Both are theoretical.
"""
COMBINATIONS = {'A': '(A), (C), (D)', 'B': '(B), (C), (D)',
                'C': '(A), (B), (C)', 'D': '(A), (B), (D)'}


class StatementCombinationTests(unittest.TestCase):
    def test_source_parser_separates_statements_from_full_options(self):
        options = option_texts_from_paper(PAPER)
        self.assertEqual(options[4], COMBINATIONS)
        self.assertEqual(options[5]['A'], 'Matriliny')
        self.assertEqual(options[5]['D'], 'Both are theoretical.')

    def test_full_combination_matches_exactly_before_marker_removal(self):
        for value in ['(B)(C)(D)', '4 Ans (B)(C)(D)', 'Q4. (D), (C), (B)',
                      r'4) \( (B)(C) \) (D)', 'B, C and D']:
            with self.subTest(value=value):
                self.assertEqual(option_named_by_words(value, COMBINATIONS), ('B', 1.0))
        for value in ['(B)(C)', '(A)(B)(C)(D)', '(B)(B)(C)(D)']:
            with self.subTest(value=value):
                self.assertEqual(option_named_by_words(value, COMBINATIONS)[0], '')
        ambiguous = dict(COMBINATIONS, A='(D)(C)(B)')
        self.assertEqual(option_named_by_words('(B)(C)(D)', ambiguous)[0], '')

    def test_key_blind_full_value_outweighs_legacy_guessed_single_letter(self):
        observed = SimpleNamespace(chosen_option='D', written_answer='(B)(C)(D)',
                                   answer_kind='statement_combination')
        self.assertEqual(_observed_letter(observed, COMBINATIONS), 'B')
        observed = SimpleNamespace(chosen_option='(B)(C)(D)', written_answer='')
        self.assertEqual(_observed_letter(observed, COMBINATIONS), 'B')
        observed = SimpleNamespace(chosen_option='D', written_answer='(B)(C)(D)',
                                   answer_kind='option_label')
        self.assertEqual(_observed_letter(observed, COMBINATIONS), '')

    def test_multiple_symbols_never_collapse_to_last_letter(self):
        self.assertEqual(_objective_choice_from_line('4) (B)(C)(D)', 4), '')
        self.assertEqual(_objective_choice_from_line('4) (A) maybe (C)', 4), '')
        self.assertEqual(_objective_choice_from_line('4) (B)', 4), 'B')
        self.assertEqual(_objective_choice_from_line('4) (D) (A) is false but (R) true', 4), 'D')

    def grade(self, answer):
        scheme = MarksScheme(total=1, items=[MarksItem(qid='Q4', max=1)])
        package = build_exam_package(exam_type='school', student_class='12', subject='Sociology',
            level='12', paper='', attempt='', applicable_law_date=None, total_marks=1,
            question_paper=b'paper', answer_key=b'key', key_source='upload', marks_scheme=scheme,
            scheme_source='text', question_paper_text=PAPER, answer_key_text='Q4\n(B)\n')
        report = GradeReport(total_score=0, max_total=1, overall_remarks='', questions=[
            QuestionMark(qid='Q4', score=0, max_score=1, page=1, y_fraction=0.5, anchor_line_id='P1L1',
                         chosen_option='D', objective=True)])
        report = reconcile_report_to_package(report, package)
        page = PageOCR(page=1, image_width=1000, image_height=1400, lines=[
            LineBox('P1L1', answer, (60, 40, 900, 75), 1, confidence=0.99)])
        apply_official_objective_evidence(report, package, [page], question_paper_text=PAPER)
        return report.questions[0]

    def test_actual_false_wrong_becomes_one_mark_from_full_student_value(self):
        question = self.grade('4) (B)(C)(D)')
        self.assertEqual((question.chosen_option, question.correct_option, question.score), ('B', 'B', 1))
        wrong = self.grade('4) (A)(B)(D)')
        self.assertEqual((wrong.chosen_option, wrong.score), ('D', 0))
        with self.assertRaises(StrictGradeError):
            self.grade('4) (A) maybe (C)')


if __name__ == '__main__':
    unittest.main()
