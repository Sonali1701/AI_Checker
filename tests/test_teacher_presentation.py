"""Presentation must be verified from actual PDF strokes, not score metadata."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from grader import AuditItem, GradeReport, audit_response_schema, parse_audit
from grading_quality import apply_verified_anchors, check_render_manifest, resolve_verified_anchors, prepare_evidence
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

from strict_grading import StrictGradeError, report_decision_digest
from test_evidence_quality import fixture


def table_fixture():
    source, report, pages, _ = fixture()
    pages[0].lines = [pages[0].lines[0], LineBox('P1D2',
        r'\begin{tabular}{ll} x = 2 \\ answer = 4 \end{tabular}', (50, 125, 550, 500), 1)]
    report.answer_regions['q1']['line_ids'] = ['P1L1', 'P1D2']
    for c in report.questions[0].criteria:
        c.step_line_id = 'P1D2'
    items = [AuditItem(row=i, qid='Q1', correct=1, evidence_status='verified',
        step_line_id='P1D2', anchor_quote=c.anchor_quote, anchor_verdict='correct',
        anchor_bbox=[60/595, (135 + 50*i)/842, 210/595, (155 + 50*i)/842])
        for i, c in enumerate(report.questions[0].criteria)]
    return source, report, pages, items


class TeacherPresentationTests(unittest.TestCase):
    def test_different_rows_inside_one_table_are_not_merged_at_midpoint(self):
        source, report, pages, items = table_fixture()
        before = report_decision_digest(report)
        apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertEqual(report_decision_digest(report), before)
        self.assertEqual(check_render_manifest(report, pdf), [])
        steps = [r for r in report.render_manifest if r['role'] == 'step']
        self.assertEqual([r['criteria'] for r in steps], [[0], [1]])
        self.assertGreater(steps[1]['anchor_bbox'][1] - steps[0]['anchor_bbox'][1], 45)
        self.assertTrue(all(r['anchor_bbox'][3] - r['anchor_bbox'][1] < 25 for r in steps))

    def test_whole_table_requires_visual_row_repair(self):
        source, report, pages, items = table_fixture()
        prepare_evidence(report, pages)
        self.assertIn('Q1', report.placement_risk_qids)
        for item in items:
            item.anchor_bbox = []
        gaps = resolve_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        self.assertEqual(set(gaps), {0, 1})
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertIn('required-mark-missing', {i.code for i in check_render_manifest(report, pdf)})
        # Since 2026-10-09 the table is not left bare (Parishram Geography: "2.5/3" in
        # the margin over one circled 0.5). It gets one mark at its end, carrying the
        # steps' awards, and certifies no row: the rows above stay unmarked for the
        # release check, which is what the assertion above still requires.
        steps = [r for r in report.render_manifest if r['role'] == 'step']
        self.assertTrue(steps)
        self.assertTrue(all(r['criteria'] == [] and r.get('location_source') == 'block_end'
                            for r in steps))

    def test_visual_boxes_roundtrip_and_stale_quotes_do_not_certify_rows(self):
        source, report, pages, items = table_fixture()
        parsed = parse_audit(json.dumps({'items': [i.model_dump() for i in items]}))
        self.assertEqual(parsed[0].anchor_bbox, items[0].anchor_bbox)
        self.assertEqual(parsed[0].anchor_verdict, 'correct')
        apply_verified_anchors(report, parsed, pages, include_awards=True, selected_qids=None)
        report = GradeReport.model_validate_json(report.model_dump_json())
        report.verified_step_boxes['q1:0']['quote'] = 'stale different answer'
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertIn('required-mark-missing', {i.code for i in check_render_manifest(report, pdf)})

    def test_outside_or_whole_page_visual_box_cannot_be_certified(self):
        for bad in ([0, 0, 1, 1], [.1, .2, .3], [.9, .2, 1, .21], [.1, .2, .3, float('nan')]):
            _, report, pages, items = table_fixture()
            items[0].anchor_bbox = bad
            before = report.model_dump()
            with self.assertRaises(StrictGradeError):
                apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
            self.assertEqual(report.model_dump(), before)

    def test_real_ticks_are_required_circles_alone_cannot_pass(self):
        source, report, pages, _ = fixture()
        with patch('pdf_renderer._draw_tick'):
            pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertIn('verdict-not-visible', {i.code for i in check_render_manifest(report, pdf)})

    def test_wrong_entry_can_earn_partial_credit_without_being_ticked_correct(self):
        source, report, pages, items = table_fixture()
        q = report.questions[0]
        q.criteria[1].awarded = .5
        q.criteria[1].met = False
        q.score = report.total_score = 1.5
        items[1].correct = .5
        items[1].anchor_verdict = 'wrong'
        apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertTrue(any(r.get('verdict') == 'wrong' and r['criteria'] == [1]
                            for r in report.render_manifest))
        self.assertTrue(any(r['role'] == 'step' and r['score'] == .5 and r['numeral_bbox']
                            for r in report.render_manifest))

    def test_total_is_on_last_answer_page_not_starting_heading(self):
        source, report, pages, _ = fixture()
        report.questions[0].mark_style = 'single'
        with fitz.open(stream=source, filetype='pdf') as doc:
            doc.new_page(width=595, height=842).insert_text((100, 200), 'Final conclusion')
            source = doc.tobytes()
        pages.append(PageOCR(2, 595, 842, [LineBox('P2L1', 'Final conclusion', (100, 185, 250, 205), 2)]))
        report.answer_regions['q1']['pages'].append(2)
        report.answer_regions['q1']['line_ids'].append('P2L1')
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertEqual(check_render_manifest(report, pdf), [])
        totals = [r for r in report.render_manifest if r.get('question_total')]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]['page'], 2)
        # On the last line's own row (the line is at y 185-205 on this page),
        # to the right of the writing, not under it and not on the heading.
        centre = (totals[0]['bbox'][1] + totals[0]['bbox'][3]) / 2
        header = report.render_page_layouts['2']['content_bbox'][1]
        self.assertTrue(185 - 12 <= centre - header <= 205 + 12, centre - header)
        self.assertGreater(totals[0]['bbox'][0], 250)

    def test_verifier_schema_requires_presentation_fields(self):
        definitions = audit_response_schema(True)['$defs'].values()
        row = next(d for d in definitions if 'anchor_bbox' in d.get('properties', {}))
        self.assertIn('anchor_bbox', row['required'])
        self.assertIn('anchor_verdict', row['required'])


if __name__ == '__main__':
    unittest.main()
