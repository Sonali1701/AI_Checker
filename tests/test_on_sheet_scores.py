"""Regression for the seven-page accountancy copy with scores in the gutter."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from grader import GradeReport, QuestionMark
from grading_quality import answer_mark_pages, check_render_manifest
from mathpix import LineBox, PageOCR
from pdf_renderer import build_evaluated_pdf

# A legacy-contract suite: marks stay inside the sheet and within reach of their
# evidence row, and checking plans are drawn. The teacher layout (the product
# default) has its own contract in test_teacher_layout.py.
import pdf_renderer as _pr
_LAYOUT_BEFORE = _pr.TEACHER_LAYOUT


def setUpModule():
    _pr.TEACHER_LAYOUT = False


def tearDownModule():
    _pr.TEACHER_LAYOUT = _LAYOUT_BEFORE

from test_evidence_quality import fixture


class OnSheetScoreTests(unittest.TestCase):
    def test_full_partial_and_zero_each_have_one_circled_total_on_original_paper(self):
        for score in (2, 1.5, 0):
            with self.subTest(score=score):
                source, report, ocr, _ = fixture()
                q = report.questions[0]
                q.mark_style = 'single'
                q.score = report.total_score = score
                q.remark = 'Calculation needs correction' if score < 2 else ''
                pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr)
                self.assertEqual(check_render_manifest(report, pdf), [])
                totals = [r for r in report.render_manifest if r.get('question_total')]
                self.assertEqual(len(totals), 1)
                self.assertEqual(totals[0]['score_style'], 'circled_award')
                self.assertEqual(totals[0]['score'], score)
                content = fitz.Rect(report.render_page_layouts['1']['content_bbox'])
                self.assertTrue(content.contains(fitz.Rect(totals[0]['bbox'])))
                with fitz.open(stream=pdf, filetype='pdf') as doc:
                    margin = fitz.Rect(report.render_page_layouts['1']['margin_bbox'])
                    feedback = doc[0].get_textbox(margin)
                    self.assertIn(f'{score:g}/2', feedback)
                    if score < 2:
                        self.assertIn('Calculation', feedback)

    def test_dense_evidence_row_uses_clear_paper_instead_of_white_strip(self):
        source, report, ocr, _ = fixture()
        with fitz.open(stream=source, filetype='pdf') as doc:
            # Occupy the entire right side, leaving clear paper on the left.
            doc[0].draw_rect((160, 115, 595, 220), color=None, fill=(0, 0, 0))
            source = doc.tobytes()
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr, safe_layout=True)
        self.assertEqual(check_render_manifest(report, pdf), [])
        content = fitz.Rect(report.render_page_layouts['1']['content_bbox'])
        for row in report.render_manifest:
            if row['role'] in {'answer', 'step', 'total'}:
                self.assertTrue(content.contains(fitz.Rect(row['bbox'])))
        for row in report.render_manifest:
            if row['role'] == 'step':
                self.assertLess(row['bbox'][2], 160)

    def test_release_rejects_a_score_recorded_in_the_white_strip(self):
        source, report, ocr, _ = fixture()
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr)
        row = next(r for r in report.render_manifest if r['role'] == 'step')
        row['bbox'][0], row['bbox'][2] = 600, 625
        self.assertIn('mark-outside-sheet', {i.code for i in check_render_manifest(report, pdf)})

    def test_release_requires_a_visible_circled_total_for_descriptive_answers(self):
        source, report, ocr, _ = fixture()
        report.questions[0].mark_style = 'single'
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr)
        for row in report.render_manifest:
            if row.get('question_total'):
                row['numeral_bbox'] = None
        self.assertIn('question-total-not-marked', {i.code for i in check_render_manifest(report, pdf)})

    def test_margin_score_is_required_even_when_sheet_circle_exists(self):
        source, report, ocr, _ = fixture()
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr)
        report.render_manifest = [r for r in report.render_manifest if not r.get('margin_score')]
        self.assertIn('margin-score-not-marked', {i.code for i in check_render_manifest(report, pdf)})

    def test_long_breakdown_keeps_fraction_on_one_line(self):
        source, report, ocr, _ = fixture()
        from grader import Criterion
        q = report.questions[0]
        q.max_score = report.max_total = 10
        q.score = report.total_score = 9.5
        q.criteria = [Criterion(description='Substitution', awarded=1, max=1, met=True,
                                step_line_id='P1L2', anchor_quote='x = 2') for _ in range(9)]
        q.criteria.append(Criterion(description='Result', awarded=.5, max=1, met=False,
                                     step_line_id='P1L3', anchor_quote='answer = 4'))
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        with fitz.open(stream=pdf, filetype='pdf') as doc:
            margin = fitz.Rect(report.render_page_layouts['1']['margin_bbox'])
            lines = [line for block in doc[0].get_text('dict', clip=margin)['blocks']
                     for line in block.get('lines', [])]
            self.assertTrue(any('9.5/10' in ''.join(s['text'] for s in line['spans']) for line in lines))

    def test_overlapping_table_is_not_a_continuation_of_previous_answer(self):
        q = QuestionMark(qid='Q4', score=1, max_score=2, page=1,
                         y_fraction=.5, anchor_line_id='P1L1')
        report = GradeReport(total_score=1, max_total=2, overall_remarks='', questions=[q],
            evidence_contract=True, answer_regions={
                'q4': {'pages': [1, 2], 'line_ids': ['P1L1', 'P2D5'], 'label_line_ids': ['P1L1']},
                'q5': {'pages': [2], 'line_ids': ['P2L8'], 'label_line_ids': ['P2L8']}})
        self.assertEqual(answer_mark_pages(report, q), {1})
        # Explicit evidence on the second page must never be dropped.
        q.anchor_line_id = 'P2D5'
        self.assertEqual(answer_mark_pages(report, q), {1, 2})

    def test_continuation_is_labelled_on_sheet_without_bracket_or_duplicate_total(self):
        source, report, ocr, _ = fixture()
        report.questions[0].mark_style = 'single'
        with fitz.open(stream=source, filetype='pdf') as doc:
            doc.new_page(width=595, height=842).insert_text((100, 150), 'Working continues')
            source = doc.tobytes()
        ocr.append(PageOCR(2, 595, 842, [LineBox('P2L1', 'Working continues', (100, 135, 240, 155), 2)]))
        report.answer_regions['q1']['line_ids'].append('P2L1')
        report.answer_regions['q1']['pages'].append(2)
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, ocr)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(sum(bool(r.get('question_total')) for r in report.render_manifest), 1)
        with fitz.open(stream=pdf, filetype='pdf') as doc:
            content = fitz.Rect(report.render_page_layouts['2']['content_bbox'])
            self.assertNotIn('Q1 continued', doc[1].get_textbox(content))
            self.assertNotIn('Q1 total', doc[1].get_textbox(content))
            self.assertFalse(any(len(d['items']) == 3 and all(i[0] == 'l' for i in d['items'])
                                 for d in doc[1].get_drawings()))


if __name__ == '__main__':
    unittest.main()
