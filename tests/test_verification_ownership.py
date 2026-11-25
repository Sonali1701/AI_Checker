"""Unnumbered answers need fresh ownership checks, not repeated impossible anchors."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
import pipeline
from answer_map import build_answer_map
from costs import record_usage_event
from exam_package import build_exam_package
from grader import AuditItem, Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark
from grading_quality import initialize_evidence, prepare_evidence, propose_verifier_regions
from mathpix import LineBox, PageOCR


def fixture():
    qids = ['Q5.a.i', 'Q5.a.ii', 'Q5.a.remaining', 'Q6.a']
    scheme = MarksScheme(total=4, items=[MarksItem(qid=qid, max=1) for qid in qids])
    package = build_exam_package(exam_type='school', student_class='10', subject='Accounting',
        level='10', paper='', attempt='', applicable_law_date=None, total_marks=4,
        question_paper=b'paper', answer_key=b'key', key_source='upload',
        marks_scheme=scheme, scheme_source='text')
    texts = ['Q5.a.i', 'Preparation standard', 'Q5.a.ii', 'Prospective standard',
             'Reasonable assurance is higher than limited assurance', 'Q6.a',
             'Significant events influence the auditor judgment']
    pages = [PageOCR(1, 595, 842, [LineBox(f'P1L{i}', text,
        (60, 60 + 65*i, 530, 80 + 65*i), 1) for i, text in enumerate(texts, 1)])]
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for line in pages[0].lines:
        page.insert_text((line.bbox[0], line.bbox[3]-3), line.text, fontsize=9)
    source = doc.tobytes()
    doc.close()
    questions = [QuestionMark(qid=qid, score=1, max_score=1, page=1, y_fraction=.2,
        anchor_line_id=f'P1L{lid}', mark_style='per_step', criteria=[Criterion(
            description='Relevant explanation', awarded=1, max=1, met=True,
            step_line_id=f'P1L{lid}', anchor_quote=texts[lid-1])])
        for qid, lid in zip(qids, [2, 4, 5, 7])]
    # A broad/stale primary answer anchor overlaps the unnumbered continuation.
    questions[1].anchor_line_id = 'P1L5'
    report = GradeReport(total_score=4, max_total=4, overall_remarks='', questions=questions)
    initialize_evidence(report, package, build_answer_map(package, pages))
    prepare_evidence(report, pages)
    items = [AuditItem(row=i, qid=q.qid, correct=1, evidence_status='verified',
        step_line_id=q.criteria[0].step_line_id, anchor_quote=q.criteria[0].anchor_quote)
        for i, q in enumerate(questions)]
    return source, report, pages, package, scheme, items


class VerificationOwnershipTests(unittest.TestCase):
    def test_fresh_references_propose_remaining_and_recheck_displaced_answer(self):
        _, report, pages, _, _, items = fixture()
        self.assertEqual(report.answer_regions['q5.a.remaining']['line_ids'], [])
        before = [q.model_dump() for q in report.questions]
        gaps = propose_verifier_regions(report, items, pages, include_awards=True, selected_qids=None)
        self.assertEqual(set(gaps), {1, 2})
        self.assertEqual(report.answer_regions['q5.a.remaining']['line_ids'], ['P1L5'])
        self.assertNotIn('P1L5', report.answer_regions['q5.a.ii']['line_ids'])
        self.assertEqual([q.model_dump() for q in report.questions], before)
        self.assertTrue(report.answer_regions['q5.a.remaining']['ownership_proposed'])

    def test_foreign_question_heading_cannot_be_adopted(self):
        _, report, pages, _, _, items = fixture()
        items[2].step_line_id = 'P1L6'
        before = report.model_dump()
        self.assertEqual(propose_verifier_regions(report, items, pages,
            include_awards=True, selected_qids=None), {})
        self.assertEqual(report.model_dump(), before)

    def test_uncertain_or_duplicate_references_cannot_reassign_ownership(self):
        for duplicate in (False, True):
            _, report, pages, _, _, items = fixture()
            if duplicate:
                items.append(items[2].model_copy())
            else:
                items[2].evidence_status = 'uncertain'
            before = report.model_dump()
            self.assertEqual(propose_verifier_regions(report, items, pages,
                include_awards=True, selected_qids=None), {})
            self.assertEqual(report.model_dump(), before)

    def test_pipeline_requires_new_certification_after_region_proposal(self):
        source, primary, pages, package, scheme, items = fixture()
        cfg = pipeline.ProviderConfig(provider='claude', model='claude-haiku-4-5',
            google_token='test', task_id='test', exam_type='school',
            grading_contract='exam_package_v2', exam_package=package)
        def usage(kwargs):
            record_usage_event(kwargs['usage_out'], {'provider':'claude', 'model':kwargs['model'],
                'input_tokens':1000, 'output_tokens':500}, stage='verification')
        def grade(**kwargs):
            usage(kwargs)
            return primary.model_copy(deep=True)
        calls = []
        def audit(current, **kwargs):
            usage(kwargs)
            calls.append(kwargs.get('only_rows'))
            if len(calls) == 2:
                self.assertEqual(kwargs['only_rows'], {1, 2})
                self.assertIn('ownership', kwargs['repair_rows'][2])
                self.assertEqual(current.answer_regions['q5.a.remaining']['line_ids'], ['P1L5'])
            return [item.model_copy(deep=True) for item in items
                    if kwargs.get('only_rows') is None or item.row in kwargs['only_rows']]
        with patch.object(pipeline, 'ocr_all_pages', return_value=pages), \
             patch.object(pipeline, '_reread_sideways_pages', return_value={}), \
             patch.object(pipeline, 'claude_grade', side_effect=grade), \
             patch.object(pipeline, 'claude_audit', side_effect=audit):
            result = pipeline.grade_sheet(qp_pngs=[], sa_bytes=source, filename='copy.pdf', ak_pngs=[],
                ak_text='Each correct explanation earns one mark.', marks_scheme=scheme,
                cfg=cfg, student_class='10', subject='Accounting', total_marks=4,
                use_mathpix=True, qp_text='Maximum Marks: 4')
        self.assertEqual(calls, [None, {1, 2}])
        self.assertTrue(result['release'], result['issues'])
        self.assertEqual(result['report'].total_score, 4)


if __name__ == '__main__':
    unittest.main()
