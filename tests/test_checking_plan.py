"""Teacher-level coverage and separate checking/award locations."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from grader import CheckingPoint, PresentationAnchor, parse_audit, audit_response_schema
from grading_quality import apply_verified_anchors, check_render_manifest, resolve_verified_anchors
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

from presentation_layout import same_written_entry
from test_teacher_presentation import table_fixture
from strict_grading import report_decision_digest


def section_fixture():
    source, report, pages, items = table_fixture()
    report.questions[0].criteria[0].description = 'Transaction entries (2 transactions)'
    points = [CheckingPoint(line_id=i.step_line_id, quote=i.anchor_quote,
                           bbox=i.anchor_bbox, verdict='correct') for i in items]
    items[0].award_scope = 'section'
    items[0].checking_points = points
    items[1].checking_points = [points[1]]
    return source, report, pages, items


class CheckingPlanTests(unittest.TestCase):
    def test_auditing_explanation_about_transactions_is_one_written_point(self):
        from presentation_layout import composite_criterion
        _, report, pages, items = table_fixture()
        criterion = report.questions[0].criteria[0]
        criterion.description = 'Significant events or transactions during period'
        self.assertFalse(composite_criterion(criterion))
        self.assertEqual(resolve_verified_anchors(report, items, pages,
            include_awards=True, selected_qids=None), {})
        # An explicit multi-entry certificate still needs its complete plan.
        items[0].award_scope = 'section'
        self.assertIn(0, resolve_verified_anchors(report, items, pages,
            include_awards=True, selected_qids=None))
        for description in ('Transactions', 'Cash and bank transactions', 'Transaction entries (6 transactions)'):
            criterion.description = description
            self.assertTrue(composite_criterion(criterion))

    def test_secondary_checking_and_award_ids_use_owned_quote_recovery(self):
        from grader import PresentationAnchor
        _, report, pages, items = section_fixture()
        items[0].checking_points[0].line_id = '[p 1 d 2]'
        items[0].checking_points[1].line_id = 'P99L1'
        items[0].award_anchor = PresentationAnchor(line_id='P99L1', quote='answer = 4',
            bbox=items[1].anchor_bbox)
        self.assertEqual(resolve_verified_anchors(report, items, pages,
            include_awards=True, selected_qids=None), {})
        self.assertTrue(all(p.line_id == 'P1D2' for p in items[0].checking_points))
        self.assertEqual(items[0].award_anchor.line_id, 'P1D2')

    def test_owned_visual_box_does_not_jump_to_repeated_amount_on_next_page(self):
        from mathpix import LineBox, PageOCR
        _,report,pages,items=table_fixture()
        pages.append(PageOCR(2,595,842,[LineBox('P2L1','12480',(60,100,200,120),2)]))
        report.answer_regions['q1']['line_ids'].append('P2L1')
        report.answer_regions['q1']['pages'].append(2)
        items[0].anchor_quote='12480'  # omitted by OCR on the first page
        self.assertEqual(resolve_verified_anchors(report,items,pages,include_awards=True,selected_qids=None),{})
        self.assertEqual(items[0].step_line_id,'P1D2')

    def test_whole_question_uses_certified_ending_not_padded_table_bottom(self):
        source,report,pages,items=section_fixture()
        report.questions[0].mark_style='single'
        items[0].award_anchor=PresentationAnchor(line_id='P1D2',quote='answer = 4',bbox=items[1].anchor_bbox)
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        pdf=build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf),[])
        total=next(r for r in report.render_manifest if r.get('question_total'))
        layout=report.render_page_layouts.get('1') or report.render_page_layouts[1]
        self.assertLess(total['anchor_bbox'][3]-layout['content_bbox'][1],210)

    def test_repeated_reason_prints_one_margin_score_and_one_explanation(self):
        source,report,pages,items=section_fixture()
        report.questions[0].score=report.total_score=1.5
        report.questions[0].criteria[0].awarded=items[0].correct=.5
        for point in items[0].checking_points:
            point.verdict='wrong'
            point.reason='Carry 205460 after correcting drawings.'
        items[1].checking_points=[]
        items[1].anchor_verdict='wrong'
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        pdf=build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf),[])
        feedback=[r for r in report.render_manifest if r['role']=='feedback']
        self.assertEqual(len(feedback),1)
        self.assertTrue(feedback[0]['margin_score'])

    def test_exclusive_table_parent_is_proposed_for_its_child_answer(self):
        from grading_quality import prepare_evidence
        from mathpix import LineBox
        for previously_owned in (False, True):
            _, report, pages, _ = table_fixture()
            pages[0].lines.extend([LineBox('P1L3','debit 2',(60,150,150,170),1),
                                   LineBox('P1L4','credit 4',(300,190,400,210),1)])
            report.answer_regions['q1']['line_ids']=['P1L1','P1L3','P1L4']
            report.questions[0].criteria[0].step_line_id='P1L3'
            report.questions[0].criteria[1].step_line_id='P1L4'
            report.answer_regions['q0']={'line_ids':['P1D2'] if previously_owned else [],'pages':[1]}
            prepare_evidence(report,pages)
            self.assertIn('P1D2',report.answer_regions['q1']['line_ids'])
            self.assertNotIn('P1D2',report.answer_regions['q0']['line_ids'])
            self.assertTrue(report.answer_regions['q1']['ownership_proposed'])
            self.assertIn('Q1',report.placement_risk_qids)

    def test_parent_spanning_other_answer_is_not_adopted(self):
        from grading_quality import prepare_evidence
        from mathpix import LineBox
        _, report, pages, _ = table_fixture()
        pages[0].lines.extend([LineBox('P1L3','debit 2',(60,150,150,170),1),
                               LineBox('P1L4','credit 4',(300,190,400,210),1),
                               LineBox('P1L5','other answer',(60,300,250,320),1)])
        report.answer_regions['q1']['line_ids']=['P1L1','P1L3','P1L4']
        report.answer_regions['q2']={'line_ids':['P1L5'],'pages':[1]}
        prepare_evidence(report,pages)
        self.assertNotIn('P1D2',report.answer_regions['q1']['line_ids'])

    def test_omission_reason_survives_alongside_an_explicit_wrong_point(self):
        source,report,pages,items=section_fixture()
        for c,i in zip(report.questions[0].criteria,items):
            c.awarded=i.correct=.5
        report.questions[0].score=report.total_score=1
        items[0].reason='First collection entry missing.'
        items[1].checking_points[0]=items[1].checking_points[0].model_copy(
            update={'verdict':'wrong','reason':'Interest should be 480.'})
        items[0].checking_points=items[0].checking_points[:1]
        items[0].award_scope='entry'
        report.questions[0].criteria[0].description='Collection entry'
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        pdf=build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf),[])
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            content=' '.join(p.get_text() for p in doc)
        self.assertIn('First collection',content)
        self.assertIn('480',content)

    def test_section_award_has_all_checks_and_one_end_total(self):
        source, report, pages, items = section_fixture()
        before = report_decision_digest(report)
        apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(report_decision_digest(report), before)
        totals = [r for r in report.render_manifest if r.get('question_total')]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]['score'], 2)
        self.assertEqual(totals[0]['presentation_mode'], 'section_question_total')
        self.assertFalse(any(r['role'] == 'step' for r in report.render_manifest))
        checks = [r for r in report.render_manifest if r.get('checking_points')]
        self.assertEqual(len(checks), 2)  # shared evidence is not ticked twice
        self.assertEqual(sorted(p for r in checks for p in r['checking_points']), ['0:0','0:1','1:0'])

    def test_missing_point_cannot_be_waived_by_deleting_requirements(self):
        source, report, pages, items = section_fixture()
        apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        report.render_requirements = []
        for row in report.render_manifest:
            if '0:0' in row.get('checking_points', []):
                row['checking_points'].remove('0:0')
        self.assertIn('checking-point-missing', {i.code for i in check_render_manifest(report, pdf)})

    def test_six_transactions_need_six_locations(self):
        _, report, pages, items = section_fixture()
        report.questions[0].criteria[0].description = 'Transaction entries (6 transactions)'
        gaps = resolve_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        self.assertIn('6 transactions', gaps[0])

    def test_legacy_section_anchor_is_not_full_coverage(self):
        source, report, pages, items = section_fixture()
        items[0].checking_points = []
        gaps = resolve_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        self.assertIn(0, gaps)
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertIn('incomplete-checking-coverage', {i.code for i in check_render_manifest(report, pdf)})

    def test_verifier_can_confirm_absent_section_without_inventing_ticks(self):
        _, report, pages, items = section_fixture()
        items[0].checking_points=[]
        items[0].evidence_kind='missing'
        items[0].anchor_verdict='missing'
        items[0].correct=0
        gaps=resolve_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        self.assertEqual(gaps,{})

    def test_debit_and_credit_on_same_row_remain_separate(self):
        left = dict(lid='P1D1',page=1,x0=60,y0=100,x1=140,y1=120)
        right = {**left, 'x0':350, 'x1':430}
        self.assertFalse(same_written_entry(left, right))
        self.assertTrue(same_written_entry(left, dict(left)))
        source, report, pages, items = table_fixture()
        items[1].anchor_bbox = [.65,135/842,.8,155/842]
        apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf), [])
        self.assertEqual([r['criteria'] for r in report.render_manifest if r['role']=='step'], [[0],[1]])

    def test_section_award_cannot_point_before_its_final_check(self):
        _, report, pages, items = section_fixture()
        items[0].award_anchor = PresentationAnchor(line_id='P1D2',quote='x = 2',bbox=items[0].anchor_bbox)
        gaps = resolve_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        self.assertIn('section ending', gaps[0])

    def test_entry_award_ending_is_independent_of_its_checking_row(self):
        source, report, pages, items = table_fixture()
        items[0].award_anchor = PresentationAnchor(line_id='P1D2',quote='answer = 4',bbox=items[1].anchor_bbox)
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf), [])
        score = next(r for r in report.render_manifest if r['role']=='step' and r['criteria']==[0])
        verdict = next(r for r in report.render_manifest if r.get('verdict') and r['criteria']==[0])
        self.assertGreater(score['anchor_bbox'][1]-verdict['anchor_bbox'][1],40)

    def test_single_mark_style_still_checks_every_certified_entry(self):
        source, report, pages, items = section_fixture()
        report.questions[0].mark_style='single'
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf), [])
        self.assertEqual(len([r for r in report.render_manifest if r.get('checking_points')]),2)

    def test_changed_anchor_invalidates_old_checking_plan(self):
        source, report, pages, items = section_fixture()
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        report.verified_step_boxes['q1:0']['quote']='stale'
        pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertIn('stale-checking-plan',{i.code for i in check_render_manifest(report,pdf)})

    def test_section_verifier_budget_accounts_for_many_checking_points(self):
        import pipeline
        _, report, _, _ = section_fixture()
        detailed=pipeline._evidence_output_budget(report,None)
        report.questions[0].criteria[0].description='Correct result'
        simple=pipeline._evidence_output_budget(report,None)
        self.assertGreaterEqual(detailed-simple,2000)

    def test_foreign_or_duplicate_points_rejected_before_certification(self):
        for foreign in (True, False):
            _, report, pages, items = section_fixture()
            if foreign:
                items[0].checking_points[0].line_id = 'P99L1'
                items[0].checking_points[0].quote = 'Writing found only in another answer'
            else:
                items[0].checking_points[1] = items[0].checking_points[0]
            gaps = resolve_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
            self.assertIn(0, gaps)
            self.assertFalse(report.verified_step_boxes)

    def test_schema_and_parser_keep_distinct_checking_and_award_locations(self):
        _, _, _, items = section_fixture()
        parsed = parse_audit(json.dumps({'items':[i.model_dump() for i in items]}))
        self.assertEqual(parsed[0].checking_points,items[0].checking_points)
        self.assertEqual(parsed[0].award_scope,'section')
        fields = audit_response_schema(True)['$defs']['AuditItem']['required']
        self.assertTrue({'checking_points','award_scope','award_anchor'} <= set(fields))

    def test_error_reason_follows_earlier_page_not_end_of_answer(self):
        source, report, pages, items = section_fixture()
        with fitz.open(stream=source, filetype='pdf') as doc:
            doc.new_page(width=595,height=842).insert_text((60,200),'Conclusion')
            source=doc.tobytes()
        from mathpix import PageOCR,LineBox
        pages.append(PageOCR(2,595,842,[LineBox('P2L1','Conclusion',(60,185,210,205),2)]))
        report.answer_regions['q1']['pages']=[1,2]
        report.answer_regions['q1']['line_ids'].append('P2L1')
        report.questions[0].score=report.total_score=1.5
        report.questions[0].criteria[0].awarded=.5
        items[0].correct=.5
        items[0].checking_points[0].verdict='wrong'
        items[0].checking_points[0].reason='Expected 3, not 2.'
        apply_verified_anchors(report,items,pages,include_awards=True,selected_qids=None)
        pdf=build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf), [])
        self.assertEqual([r['page'] for r in report.render_manifest if r.get('reason_page')],[1])
        # The score is written once, beside the question total on page 2; the reason
        # on page 1 names its question without repeating the score (CA faculty
        # review, 17 Oct 2026: a score on two pages read as marks given twice).
        self.assertEqual([r['page'] for r in report.render_manifest if r.get('margin_score')],[2])
        self.assertEqual([r['page'] for r in report.render_manifest if r.get('label_only')],[1])
        self.assertEqual([r['page'] for r in report.render_manifest if r.get('question_total')],[2])


if __name__ == '__main__':
    unittest.main()
