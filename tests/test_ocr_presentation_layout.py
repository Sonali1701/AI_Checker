"""OCR-first placement, visual escalation, and the clean answer-sheet contract."""
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from grader import AuditItem
from grading_quality import prepare_evidence, resolve_verified_anchors, apply_verified_anchors, check_render_manifest
from mathpix import WordBox, LineBox, PageOCR, word_target_bbox
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

from presentation_layout import ocr_evidence_box, score_candidates, near_evidence, MAX_HORIZONTAL_GAP
from test_evidence_quality import fixture
from test_teacher_presentation import table_fixture


class OCRPresentationTests(unittest.TestCase):
    def _crowded_copy(self, *, missing_tick=False):
        import pdf_renderer
        source, report, pages, _ = fixture()
        draw = pdf_renderer._draw_sheet_score
        def crowded(*args, **kwargs):
            if kwargs.get('role') == 'step' and 1 in kwargs.get('criteria', []):
                return False
            return draw(*args, **kwargs)
        with patch('pdf_renderer._draw_sheet_score', side_effect=crowded):
            if missing_tick:
                with patch('pdf_renderer._draw_tick'):
                    pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
            else:
                pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        return report, pdf

    def test_crowded_row_replaces_all_step_circles_without_holding_copy(self):
        from preflight import _check_rendered
        report, pdf = self._crowded_copy()
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertEqual(_check_rendered(pdf, report), [])
        self.assertEqual(report.unanchored_step_qids, [])
        self.assertFalse(any(r['role'] == 'step' for r in report.render_manifest))
        totals = [r for r in report.render_manifest if r.get('question_total')]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]['score'], report.questions[0].score)
        self.assertEqual(totals[0]['presentation_mode'], 'crowded_question_total')
        verdicts = [r for r in report.render_manifest if r.get('verdict')]
        self.assertEqual(sorted(i for r in verdicts for i in r['criteria']), [0, 1])
        # Inspect actual PDF paths, not just the manifest: discarded plans must
        # leave no painted step circles behind.
        with fitz.open(stream=pdf, filetype='pdf') as doc:
            circles = [d for page in doc for d in page.get_drawings()
                       if len(d.get('items', [])) == 4 and all(i[0] == 'c' for i in d['items'])]
        self.assertEqual(len(circles), 1)

    def test_crowded_fallback_still_requires_actual_ticks(self):
        report, pdf = self._crowded_copy(missing_tick=True)
        self.assertIn('verdict-not-visible', {i.code for i in check_render_manifest(report, pdf)})

    def test_crowded_fallback_cannot_hide_missing_criterion_requirements(self):
        report, pdf = self._crowded_copy()
        report.render_requirements = [r for r in report.render_requirements if r.get('criterion') != 1]
        report.render_manifest = [r for r in report.render_manifest if 1 not in r.get('criteria', [])]
        self.assertIn('verdict-not-visible', {i.code for i in check_render_manifest(report, pdf)})

    def test_crowded_fallback_rejects_duplicate_total(self):
        report, pdf = self._crowded_copy()
        total = next(r for r in report.render_manifest if r.get('question_total'))
        report.render_manifest.append(dict(total))
        self.assertIn('duplicate-sheet-total', {i.code for i in check_render_manifest(report, pdf)})

    def test_same_row_combines_awards_without_crossing_correct_work(self):
        source, report, pages, items = table_fixture()
        items[1].anchor_bbox = items[0].anchor_bbox[:]
        items[1].correct = .5
        report.questions[0].criteria[1].awarded = .5
        report.questions[0].score = report.total_score = 1.5
        apply_verified_anchors(report, items, pages, include_awards=True, selected_qids=None)
        with patch('pdf_renderer._draw_cross') as crossed:
            pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(crossed.call_count,0)
        steps = [r for r in report.render_manifest if r['role']=='step']
        self.assertEqual(len(steps),1)
        self.assertEqual(steps[0]['criteria'],[0,1])
        self.assertEqual(steps[0]['score'],1.5)
        self.assertEqual(check_render_manifest(report,pdf),[])

    def test_unique_measured_words_locate_a_table_without_visual_boxes(self):
        source, report, pages, items = table_fixture()
        pages[0].words = [WordBox('x', (60,135,70,155), .99), WordBox('=', (75,135,85,155), .99),
                          WordBox('2', (90,135,100,155), .99),
                          WordBox('answer', (60,185,100,205), .99), WordBox('=', (110,185,120,205), .99),
                          WordBox('4', (125,185,135,205), .99)]
        # Single-word anchors avoid OCR word-order ambiguity in a merged table.
        report.questions[0].criteria[0].anchor_quote = '2'
        report.questions[0].criteria[1].anchor_quote = '4'
        for item, c in zip(items, report.questions[0].criteria):
            item.anchor_quote = c.anchor_quote
            item.anchor_bbox = []
        prepare_evidence(report, pages)
        self.assertNotIn('Q1', report.placement_risk_qids)
        self.assertEqual(resolve_verified_anchors(report, items, pages, include_awards=True, selected_qids=None), {})
        pdf = build_evaluated_pdf(source, 'copy.pdf', [], report, pages)
        self.assertEqual(check_render_manifest(report, pdf), [])
        self.assertTrue(all(r['location_source'] == 'ocr_words'
                            for r in report.render_manifest if r['role'] == 'step'))

    def test_low_confidence_requires_verification_then_reuses_confirmed_line(self):
        _, report, pages, _ = fixture()
        pages[0].lines[1].confidence = .4
        prepare_evidence(report, pages)
        self.assertIn('Q1', report.placement_risk_qids)
        self.assertIsNone(ocr_evidence_box(pages[0], pages[0].lines[1], 'x = 2'))
        item = AuditItem(row=0, qid='Q1', correct=1, evidence_status='verified',
                         step_line_id='P1L2', anchor_quote='x = 2', anchor_verdict='correct')
        apply_verified_anchors(report, [item], pages, include_awards=True, selected_qids=None)
        prepare_evidence(report, pages)
        self.assertNotIn('Q1', report.placement_risk_qids)

    def test_repeated_word_or_cross_row_phrase_is_not_a_location(self):
        line = LineBox('P1D1', '100 100', (0,0,500,500), 1)
        page = PageOCR(1,500,700,[line], [WordBox('100',(20,20,50,40),.99),WordBox('100',(60,100,90,120),.99)])
        self.assertIsNone(ocr_evidence_box(page, line, '100'))
        self.assertIsNone(word_target_bbox(page, line, '100100'))

    def test_unique_embedded_ocr_line_can_refine_merged_region(self):
        line = LineBox('P1D1', 'large table', (0,0,500,500), 1)
        row = LineBox('P1L2', 'closing 100', (200,200,300,220), 1, confidence=.99)
        page = PageOCR(1,500,700,[line,row])
        self.assertEqual(ocr_evidence_box(page,line,'closing 100'), (row.bbox,'ocr_embedded_line'))

    def test_candidates_are_local_not_a_whole_sheet_search(self):
        anchor = (300,200,360,220)
        candidates = list(score_candidates(anchor,20,20))
        self.assertTrue(candidates)
        self.assertTrue(all(near_evidence(b,anchor) for b in candidates))
        self.assertTrue(all(b[2] >= anchor[0]-MAX_HORIZONTAL_GAP for b in candidates))
        self.assertFalse(near_evidence((5,200,25,220),anchor))
        self.assertFalse(near_evidence((365,260,385,280),anchor))

    def test_sheet_has_only_numeric_added_text_and_margin_has_no_calculation(self):
        source, report, pages, _ = fixture()
        report.questions[0].score = report.total_score = 1.5
        report.questions[0].criteria[1].awarded = .5
        report.questions[0].remark = 'Final result is incorrect.'
        pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertEqual(check_render_manifest(report,pdf), [])
        self.assertFalse(any(r.get('question_total') for r in report.render_manifest))
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            content = fitz.Rect(report.render_page_layouts['1']['content_bbox'])
            for block in doc[0].get_text('dict',clip=content)['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        if span.get('color',0) >> 16 > 150:
                            self.assertRegex(span['text'], r'^\d+(\.\d+)?$')
            margin = doc[0].get_textbox(fitz.Rect(report.render_page_layouts['1']['margin_bbox']))
            self.assertIn('1.5/2',margin)
            self.assertIn('Final result',margin)
            self.assertNotIn(' + ',margin)

    def test_validator_rejects_restored_on_sheet_label(self):
        source, report, pages, _ = fixture()
        pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        row = next(r for r in report.render_manifest if r['role']=='step')
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            box = fitz.Rect(row['bbox'])
            doc[row['page']-1].insert_text((box.x0,box.y1-2),'total',fontsize=5,color=(.8,.08,.1))
            changed = doc.tobytes()
        report.render_manifest_pdf_sha256 = hashlib.sha256(changed).hexdigest()
        self.assertIn('forbidden-sheet-label',{i.code for i in check_render_manifest(report,changed)})

    def test_all_blocked_slots_hold_copy_instead_of_using_distant_blank_space(self):
        source, report, pages, _ = fixture()
        with patch('pdf_renderer._sheet_slot_allowed', return_value=False):
            pdf = build_evaluated_pdf(source,'copy.pdf',[],report,pages)
        self.assertIn('required-mark-missing',{i.code for i in check_render_manifest(report,pdf)})

    def test_total_must_not_land_in_next_questions_region(self):
        import pdf_renderer
        source, report, pages, _ = fixture()
        from grader import QuestionMark
        report.questions.append(QuestionMark(qid='Q2',score=1,max_score=1,page=1,y_fraction=.3,anchor_line_id='P1L4'))
        pages[0].lines.append(LineBox('P1L4','Next answer',(220,130,300,170),1))
        report.answer_regions['q2']={'pages':[1],'line_ids':['P1L4']}
        with fitz.open(stream=source,filetype='pdf') as doc:
            token = pdf_renderer._RENDER_REPORT.set(report)
            words = pdf_renderer._OCR_WORDS.set({l.line_id:(pages[0],l) for l in pages[0].lines})
            try:
                own = {'lid':'P1L2','page':1,'x0':60,'y0':135,'x1':210,'y1':155}
                self.assertFalse(pdf_renderer._sheet_slot_allowed('Q1',own,fitz.Rect(225,140,245,160),doc[0]))
            finally:
                pdf_renderer._RENDER_REPORT.reset(token)
                pdf_renderer._OCR_WORDS.reset(words)


if __name__ == '__main__':
    unittest.main()
