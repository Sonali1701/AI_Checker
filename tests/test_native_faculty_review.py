"""Offline corrections preserve native rendering without provider certification."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import socket
import sys

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grader import Criterion, GradeReport, QuestionMark
from mathpix import LineBox, PageOCR, WordBox, pageocr_to_dict
import pdf_renderer
from tools.render_reviewed_evaluation import native_questions, render_saved_review, rotate_cache_page


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('A saved faculty correction must not call the network')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def case(tmp_path):
    doc = fitz.open()
    first = doc.new_page(width=595, height=842)
    first.insert_text((60, 120), 'Q1 (B) A stock concept', fontsize=13)
    first.insert_text((60, 170), 'GDP ignores externalities.', fontsize=13)
    second = doc.new_page(width=595, height=842)
    second.insert_text((60, 150), 'Unpaid household work is omitted.', fontsize=13)
    source_raw = doc.tobytes()
    doc.close()
    official_doc = fitz.open()
    official_doc.new_page().insert_text((60, 120), 'Explain unpaid production and GDP welfare.')
    official_raw = official_doc.tobytes()
    official_doc.close()

    def check(page, box, quote, verdict='correct', reason=''):
        return dict(page=page, bbox=box, quote=quote, verdict=verdict, reason=reason)

    questions = [
        dict(qid='Q1', max_score=1, score=1, attempted=True, objective=True,
             answer_pages=[1], feedback='Correct option: B.',
             checks=[check(1, [.10, .12, .43, .16], 'B')]),
        dict(qid='Q2', max_score=3, score=2, attempted=True, objective=False,
             answer_pages=[1, 2], feedback='Unpaid work is omitted. Externalities are a different limitation.',
             checks=[check(1, [.10, .185, .44, .215], 'GDP ignores externalities.', 'wrong',
                           'Explain unpaid production instead.'),
                     check(2, [.10, .16, .48, .19], 'Unpaid household work is omitted.')]),
        dict(qid='Q3', max_score=2, score=0, attempted=False, objective=False,
             answer_pages=[2], feedback='Not answered.', checks=[]),
    ]
    review = dict(sample_id='synthetic-review', subject='Economics',
                  source_sha256=digest(source_raw), official_sha256=digest(official_raw),
                  reviewer='Explicit faculty-style source review', page_rotations={},
                  original_pages_reviewed=[1, 2], official_pages_reviewed=[1],
                  all_questions_reviewed=True, unresolved_issues=[], questions=questions)
    native = [
        QuestionMark(qid='Q1', score=1, max_score=1, page=1, y_fraction=.14,
                     anchor_line_id='faculty:q1:0', objective=True, attempted=True,
                     is_correct=True, correct_option='B', chosen_option='B', mark_style='single'),
        QuestionMark(qid='Q2', score=2, max_score=3, page=2, y_fraction=.175,
                     anchor_line_id='faculty:q2:1', attempted=True, mark_style='single',
                     remark='Explain unpaid production instead.',
                     criteria=[Criterion(description='Explain production and welfare', max=3,
                                         awarded=2, met=False, evidence='Incomplete explanation',
                                         anchor_quote='Unpaid household work is omitted.',
                                         step_line_id='faculty:q2:1')]),
        QuestionMark(qid='Q3', score=0, max_score=2, page=2, y_fraction=.5,
                     attempted=False, remark='Not answered.'),
    ]
    report = GradeReport(total_score=3, max_total=6, overall_remarks='Revise GDP welfare.',
                         questions=native, evidence_contract=True)
    evidence = dict(reviewer=review['reviewer'], review_sha256=digest(json.dumps(review).encode()),
                    original_source_sha256=digest(source_raw), official_sha256=digest(official_raw),
                    render_source_sha256=digest(source_raw), original_pages_reviewed=[1, 2],
                    all_questions_reviewed=True, unresolved_issues=[], questions=copy.deepcopy(questions))
    cache = [PageOCR(1, 1190, 1684, [
                 LineBox('P1L1', 'Q1 (B) A stock concept', (120, 215, 512, 245), 1),
                 LineBox('P1L2', 'GDP ignores externalities.', (120, 315, 530, 345), 1)]),
             PageOCR(2, 1190, 1684, [
                 LineBox('P2L1', 'Unpaid household work is omitted.', (120, 275, 650, 305), 2)])]
    return dict(tmp=tmp_path, source=source_raw, official=official_raw, review=review,
                report=report, evidence=evidence, cache=cache)


def test_native_render_has_original_pages_strip_and_unpriced_verdict(case):
    report = case['report']
    pdf = pdf_renderer.build_evaluated_pdf(case['source'], 'student.pdf', [], report,
                                         ocr_pages=case['cache'], reviewed_evidence=case['evidence'])
    with fitz.open(stream=pdf, filetype='pdf') as output:
        assert len(output) == 2, 'Native output retains original page count, without substitute feedback pages'
        assert 'EVALUATED' in output[0].get_text()
        assert 'Total Score: 3 out of 6' in output[0].get_text()
        assert 'GDP ignores externalities.' in output[0].get_text()
        assert 'Unpaid household work is omitted.' in output[1].get_text()
    assert {v['layout'] for v in report.render_page_layouts.values()} == {'teacher'}
    bare = [m for m in report.render_manifest if m['role'] == 'faculty_check']
    assert len(bare) == 1
    assert bare[0]['kind'] == 'cross'
    assert bare[0]['score'] is None and bare[0]['score_text'] is None
    assert bare[0]['numeral_bbox'] is None and bare[0]['criteria'] == []
    assert bare[0]['location_source'] == 'faculty_source_review'
    assert bare[0]['origin_review_sha256'] == case['evidence']['review_sha256']
    assert bare[0]['source_quote'] == 'GDP ignores externalities.'
    assert bare[0]['score_style'] == 'bare_verdict'
    assert not report.verified_step_boxes and not report.objective_visual_verified_qids
    first_header = report.render_page_layouts['1']['content_bbox'][1]
    assert bare[0]['anchor_bbox'][1] == pytest.approx(.185 * 842 + first_header)


def test_full_credit_extra_error_still_has_native_correction_note(case):
    report = case['report']
    report.questions[1].score = 3
    report.questions[1].criteria[0].awarded = 3
    report.questions[1].criteria[0].met = True
    report.questions[1].remark = ''
    evidence = copy.deepcopy(case['evidence'])
    evidence['questions'][1]['score'] = 3
    evidence['questions'][1]['checks'][0]['show_reason'] = True
    pdf_renderer.build_evaluated_pdf(case['source'], 'student.pdf', [], report,
                                     ocr_pages=case['cache'], reviewed_evidence=evidence)
    notes = [m for m in report.render_manifest if m.get('faculty_correction_note')]
    assert len(notes) == 1
    assert report.total_score == 4
    assert notes[0]['qid'] == 'Q2'
    bare = [m for m in report.render_manifest if m['role'] == 'faculty_check']
    assert len(bare) == 1 and bare[0]['score'] is None


def test_reviewed_mcq_scores_stay_beside_their_own_rows_with_a_long_wrong_reason(case):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    checks, native, cache_lines = [], [], []
    long_reason = 'Correct: B. ' + ('Stratification is systematic rather than an accidental individual difference. ' * 12)
    for n in range(1, 7):
        y = 130 + (n - 1) * 70
        choice = 'A' if n == 2 else 'B'
        page.insert_text((60, y), f'Q{n} ({choice}) An actual selected option', fontsize=13)
        check = dict(page=1, bbox=[.10, (y - 15) / 842, .43, (y + 3) / 842],
                     quote=choice, verdict='wrong' if n == 2 else 'correct',
                     reason=long_reason if n == 2 else '')
        checks.append(dict(qid=f'Q{n}', max_score=1, score=0 if n == 2 else 1,
                           attempted=True, objective=True, answer_pages=[1],
                           feedback=long_reason if n == 2 else 'Correct option: B.', checks=[check]))
        native.append(QuestionMark(qid=f'Q{n}', max_score=1, score=0 if n == 2 else 1,
                        page=1, y_fraction=(y - 6) / 842, anchor_line_id=f'faculty:q{n}:0',
                        objective=True, attempted=True, is_correct=n != 2,
                        correct_option='B', chosen_option=choice,
                        remark=long_reason if n == 2 else '', mark_style='single'))
        cache_lines.append(LineBox(f'P1L{n}', f'Q{n} ({choice})',
                                   (120, 2 * (y - 15), 512, 2 * (y + 3)), 1))
    source = doc.tobytes()
    doc.close()
    evidence = copy.deepcopy(case['evidence'])
    evidence.update(original_source_sha256=digest(source), render_source_sha256=digest(source),
                    original_pages_reviewed=[1], questions=checks)
    report = GradeReport(total_score=5, max_total=6, questions=native, evidence_contract=True,
                         overall_remarks='Revise stratification.')
    pdf_renderer.build_evaluated_pdf(source, 'mcqs.pdf', [], report,
                ocr_pages=[PageOCR(1, 1190, 1684, cache_lines)], reviewed_evidence=evidence)
    marks = {m['qid']: m for m in report.render_manifest if m['role'] == 'answer'}
    margins = {m['qid']: m for m in report.render_manifest if m['role'] == 'feedback'}
    assert len(marks) == len(margins) == 6
    for qid, mark in marks.items():
        anchor = fitz.Rect(mark['anchor_bbox'])
        margin = fitz.Rect(margins[qid]['bbox'])
        assert abs((margin.y0 + margin.y1) / 2 - (anchor.y0 + anchor.y1) / 2) < 20
        assert margin.height < 30, 'Long objective reasons belong in the native report strip, not beside the next row'
    assert marks['Q2']['kind'] == 'cross' and marks['Q3']['kind'] == 'tick'


def test_adjacent_valid_cause_and_wrong_outcome_have_distinct_native_marks(case):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((264, 480), 'Jawaharlal Nehru', fontsize=13)
    page.insert_text((129, 510), 'other political party were very weak', fontsize=13)
    page.insert_text((270, 530), 'won maximum seats', fontsize=13)
    source = doc.tobytes()
    doc.close()
    # Actual PS01 Q12 source geometry: the weak-opposition row and the next
    # incorrect outcome are closer than a full native tick/cross stroke height.
    checks = [dict(page=1, bbox=[.445, .548, .748, .579], quote='Jawaharlal Nehru', verdict='correct', reason=''),
              dict(page=1, bbox=[.217, .589, .761, .617], quote='other political party were', verdict='correct', reason=''),
              dict(page=1, bbox=[.455, .611, .708, .640], quote='won maximum seats', verdict='wrong',
                   reason='Winning seats is the result, not a cause.')]
    question = dict(qid='Q12', max_score=4, score=2, attempted=True, objective=False,
                    answer_pages=[1], feedback='Two relevant causes.', checks=checks)
    evidence = copy.deepcopy(case['evidence'])
    evidence.update(original_source_sha256=digest(source), render_source_sha256=digest(source),
                    original_pages_reviewed=[1], questions=[question])
    report = GradeReport(total_score=2, max_total=4, evidence_contract=True, overall_remarks='Develop distinct causes.',
                questions=[QuestionMark(qid='Q12', max_score=4, score=2, page=1,
                      y_fraction=.603, anchor_line_id='faculty:q12:1', objective=False,
                      attempted=True, mark_style='single')])
    pitch_lines = [LineBox(f'P1L{n}', 'actual handwriting row', (120, 2 * y, 350, 2 * (y + 15)), 1)
                   for n, y in enumerate(range(220, 430, 26), 1)]
    pdf_renderer.build_evaluated_pdf(source, 'adjacent.pdf', [], report,
                    ocr_pages=[PageOCR(1, 1190, 1684, pitch_lines)], reviewed_evidence=evidence)
    total = next(m for m in report.render_manifest if m['role'] == 'answer')
    wrong = next(m for m in report.render_manifest if m['role'] == 'faculty_check' and m['kind'] == 'cross')
    assert total['kind'] == 'tick' and total['score'] == 2
    assert wrong['score'] is None
    tick_rect, wrong_rect = fitz.Rect(total['verdict_bbox']), fitz.Rect(wrong['verdict_bbox'])
    assert (tick_rect & wrong_rect).get_area() <= .005 * min(tick_rect.get_area(), wrong_rect.get_area())


def test_two_table_columns_falling_back_to_the_gutter_keep_distinct_verdicts(case):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Dense source ink deliberately forces native marks into the blank gutter.
    page.draw_rect(page.rect, fill=(.2, .2, .2), color=None)
    page.insert_text((98, 170), 'Capitalism follow privatisation', fontsize=12, color=(1, 1, 1))
    page.insert_text((306, 171), 'Socialism follow social welfare', fontsize=12, color=(1, 1, 1))
    page.insert_text((98, 230), 'Profit', fontsize=12, color=(1, 1, 1))
    page.insert_text((306, 231), 'Equality', fontsize=12, color=(1, 1, 1))
    second = doc.new_page(width=595, height=842)
    second.insert_text((60, 150), 'The comparisons use distinct economic priorities.', fontsize=12)
    source = doc.tobytes()
    doc.close()
    checks = [dict(page=1, bbox=[.164, .175, .492, .223], quote='Capitalism follow privatisation', verdict='correct', reason=''),
              dict(page=1, bbox=[.164, .245, .492, .283], quote='Profit', verdict='correct', reason=''),
              dict(page=1, bbox=[.515, .176, .874, .224], quote='Socialism follow social welfare', verdict='correct', reason=''),
              dict(page=1, bbox=[.515, .246, .874, .284], quote='Equality', verdict='correct', reason=''),
              dict(page=2, bbox=[.10, .16, .65, .19], quote='distinct economic priorities', verdict='correct', reason='')]
    evidence = copy.deepcopy(case['evidence'])
    evidence.update(original_source_sha256=digest(source), render_source_sha256=digest(source),
                    original_pages_reviewed=[1, 2], questions=[dict(qid='Q9', max_score=2, score=2,
                    attempted=True, objective=False, answer_pages=[1, 2], feedback='Both comparisons correct.', checks=checks)])
    report = GradeReport(total_score=2, max_total=2, evidence_contract=True, overall_remarks='Good comparisons.',
                questions=[QuestionMark(qid='Q9', max_score=2, score=2, page=2,
                      y_fraction=.175, anchor_line_id='faculty:q9:4', objective=False,
                      attempted=True, mark_style='single')])
    pdf_renderer.build_evaluated_pdf(source, 'table.pdf', [], report,
                    ocr_pages=[PageOCR(1, 1190, 1684, [])], reviewed_evidence=evidence)
    marks = [m for m in report.render_manifest if m['role'] in ('answer', 'faculty_check')]
    assert len(marks) == 5
    for index, first in enumerate(marks):
        for second in marks[index + 1:]:
            if first['page'] != second['page']:
                continue
            a, b = fitz.Rect(first['verdict_bbox']), fitz.Rect(second['verdict_bbox'])
            assert (a & b).get_area() <= .005 * min(a.get_area(), b.get_area())


def test_reviewed_gutter_refuses_to_silently_stack_excess_marks(case, monkeypatch):
    from teacher_layout import Line, TeacherLayout
    with fitz.open(stream=case['source'], filetype='pdf') as source:
        layout = TeacherLayout(source, case['report'], case['cache'], reviewed_evidence=case['evidence'])
        # Simulate dense source ink and a gutter already entirely occupied by
        # neighbouring verdicts. A renderer must reject instead of returning the
        # old unconditional W+3, dy=0 position over another mark.
        layout.placed[1] = [fitz.Rect(595, 0, 2000, 842)]
        monkeypatch.setattr(layout, '_ink_fraction', lambda *args: 1)
        monkeypatch.setattr(layout, '_disc_ink', lambda *args: 1)
        line = Line('faculty:q2:0', 1, fitz.Rect(60, 160, 300, 180), 'actual answer')
        with pytest.raises(ValueError, match='No collision-free reviewed mark position'):
            layout.place(line, line.rect, 595, layout.tick_w, 0, 0)


@pytest.mark.parametrize('mutation', [
    'source_hash', 'provenance_hash', 'incomplete_pages', 'unresolved', 'missing_questions',
    'duplicate_questions', 'mismatched_marks', 'missing_checks', 'out_of_page', 'boolean_page',
    'negative_box', 'inverted_box', 'nan_box', 'boolean_box', 'empty_quote', 'blank_quote',
    'missing_wrong_reason', 'blank_wrong_reason', 'objective_mismatch',
])
def test_review_validation_rejects_unbound_or_invalid_evidence(case, mutation):
    evidence = copy.deepcopy(case['evidence'])
    first = evidence['questions'][0]
    if mutation == 'source_hash': evidence['render_source_sha256'] = '0' * 64
    elif mutation == 'provenance_hash': evidence['official_sha256'] = 'not-a-hash'
    elif mutation == 'incomplete_pages': evidence['original_pages_reviewed'] = [1]
    elif mutation == 'unresolved': evidence['unresolved_issues'] = ['Unreadable answer']
    elif mutation == 'missing_questions': evidence['questions'].pop()
    elif mutation == 'duplicate_questions': evidence['questions'][2] = copy.deepcopy(first)
    elif mutation == 'mismatched_marks': first['score'] = 0
    elif mutation == 'missing_checks': first['checks'] = []
    elif mutation == 'out_of_page': first['checks'][0]['page'] = 3
    elif mutation == 'boolean_page': first['checks'][0]['page'] = True
    elif mutation == 'negative_box': first['checks'][0]['bbox'][0] = -.1
    elif mutation == 'inverted_box': first['checks'][0]['bbox'] = [.5, .1, .3, .2]
    elif mutation == 'nan_box': first['checks'][0]['bbox'][0] = float('nan')
    elif mutation == 'boolean_box': first['checks'][0]['bbox'][0] = False
    elif mutation == 'empty_quote': first['checks'][0]['quote'] = ''
    elif mutation == 'blank_quote': first['checks'][0]['quote'] = '  '
    elif mutation == 'missing_wrong_reason': evidence['questions'][1]['checks'][0]['reason'] = ''
    elif mutation == 'blank_wrong_reason': evidence['questions'][1]['checks'][0]['reason'] = '  '
    elif mutation == 'objective_mismatch': first['objective'] = False
    with pytest.raises(ValueError):
        pdf_renderer._validate_reviewed_evidence(case['source'], case['report'], evidence)


@pytest.mark.parametrize('mutation', ['duplicate_report_question', 'foreign_question_anchor', 'foreign_criterion_anchor', 'anchor_page_mismatch'])
def test_native_review_rejects_ambiguous_or_foreign_owned_anchors(case, mutation):
    report = case['report'].model_copy(deep=True)
    if mutation == 'duplicate_report_question':
        report.questions.append(report.questions[0].model_copy(deep=True))
    elif mutation == 'foreign_question_anchor':
        report.questions[0].anchor_line_id = 'faculty:q2:0'
    elif mutation == 'foreign_criterion_anchor':
        report.questions[1].criteria[0].step_line_id = 'faculty:q1:0'
    else:
        report.questions[0].page = 2
    with pytest.raises(ValueError):
        pdf_renderer._validate_reviewed_evidence(case['source'], report, case['evidence'])


def write_inputs(case):
    paths = {}
    raw = {'source': case['source'], 'official': case['official'],
           'report': case['report'].model_dump_json(indent=2).encode(),
           'ocr': json.dumps([pageocr_to_dict(p) for p in case['cache']]).encode(),
           'review': json.dumps(case['review'], indent=2).encode()}
    for key, data in raw.items():
        path = case['tmp'] / (key + ('.pdf' if key in ['source', 'official'] else '.json'))
        path.write_bytes(data)
        paths[key] = path
    return paths, raw


def test_adapter_preserves_originals_and_discloses_saved_correction(case):
    case['report'].verified_step_boxes = {'q2:0': {'line_id': 'P1L2', 'source': 'old-provider'}}
    case['report'].verified_step_anchors = {'q2:0': ['P1L2', 'old phrase']}
    case['report'].objective_visual_verified_qids = ['Q1']
    paths, raw = write_inputs(case)
    output = case['tmp'] / 'native-reviewed.pdf'
    audit = render_saved_review(paths['source'], paths['official'], paths['report'], paths['ocr'],
                                paths['review'], {'Q1': 1, 'Q2': 3, 'Q3': 2}, output,
                                overall_remarks='Revise GDP welfare.')
    assert all(paths[key].read_bytes() == value for key, value in raw.items())
    assert audit['original_report_sha256'] == digest(raw['report'])
    assert audit['original_ocr_sha256'] == digest(raw['ocr'])
    assert audit['review_sha256'] == digest(raw['review'])
    assert audit['pdf_sha256'] == digest(output.read_bytes())
    assert audit['score'] == 3 and audit['maximum'] == 6
    assert audit['fresh_provider_grading'] is False
    assert audit['automatic_release_authorized'] is False
    corrected = GradeReport.model_validate_json(output.with_suffix('.report.json').read_bytes())
    assert not corrected.verified_step_boxes and not corrected.verified_step_anchors
    assert not corrected.objective_visual_verified_qids
    assert any('no fresh provider recheck' in r for r in corrected.review_reasons)
    assert corrected.questions[0].is_correct and corrected.questions[0].chosen_option == 'B'
    assert corrected.questions[2].attempted is False


@pytest.mark.parametrize('selection', ['valid', 'wrong_verdict', 'negative', 'out_of_range', 'boolean', 'string'])
def test_adapter_total_anchor_override_must_be_an_own_eligible_reviewed_check(case, selection):
    with fitz.open(stream=case['source'], filetype='pdf') as source:
        source[1].insert_text((60, 250), 'Unpaid production is not counted in GDP.', fontsize=13)
        case['source'] = source.tobytes()
    case['review']['source_sha256'] = digest(case['source'])
    question = case['review']['questions'][1]
    question['checks'].append(dict(page=2, bbox=[.10, .28, .60, .31],
        quote='Unpaid production is not counted in GDP.', verdict='correct', reason=''))
    indices = {'valid': 1, 'wrong_verdict': 0, 'negative': -1, 'out_of_range': 3, 'boolean': True, 'string': '1'}
    question['native_total_check_index'] = indices[selection]
    paths, raw = write_inputs(case)
    output = case['tmp'] / 'chosen-native-anchor.pdf'
    def render():
        return render_saved_review(paths['source'], paths['official'], paths['report'], paths['ocr'],
                  paths['review'], {'Q1': 1, 'Q2': 3, 'Q3': 2}, output, overall_remarks='Revise GDP welfare.')
    if selection != 'valid':
        with pytest.raises(ValueError): render()
        assert not output.exists()
    else:
        audit = render()
        corrected = GradeReport.model_validate_json(output.with_suffix('.report.json').read_bytes())
        q = corrected.questions[1]
        assert q.anchor_line_id == 'faculty:q2:1', 'Explicit source-bound row supersedes the later default total anchor'
        assert q.criteria[0].step_line_id == 'faculty:q2:1'
        assert q.score == 2 and q.max_score == 3 and audit['score'] == 3
        assert all(paths[key].read_bytes() == value for key, value in raw.items())


@pytest.mark.parametrize('bound_file', ['source', 'official'])
def test_adapter_rejects_wrong_bound_inputs_without_creating_output(case, bound_file):
    paths, raw = write_inputs(case)
    paths[bound_file].write_bytes(raw[bound_file] + b'\nchanged')
    output = case['tmp'] / 'must-not-exist.pdf'
    with pytest.raises(ValueError):
        render_saved_review(paths['source'], paths['official'], paths['report'], paths['ocr'],
                            paths['review'], {'Q1': 1, 'Q2': 3, 'Q3': 2}, output,
                            overall_remarks='Revise GDP welfare.')
    assert not output.exists()
    assert paths['report'].read_bytes() == raw['report']
    assert paths['ocr'].read_bytes() == raw['ocr']


def test_rotation_transforms_cached_boxes_once_without_mutating_original():
    page = PageOCR(1, 100, 200, [LineBox('P1L1', 'actual answer', (10, 20, 40, 50), 1)],
                   [WordBox('actual', (10, 20, 25, 50))])
    moved = rotate_cache_page(page, 90)
    assert (moved.image_width, moved.image_height) == (200, 100)
    assert moved.lines[0].bbox == (20, 60, 50, 90)
    assert moved.words[0].bbox == (20, 75, 50, 90)
    assert page.lines[0].bbox == (10, 20, 40, 50)
    assert (page.image_width, page.image_height) == (100, 200)


def test_history_source_question_subparts_keep_explicit_official_allocation():
    review = dict(subject='History', questions=[dict(qid='Q12', score=3, max_score=4,
                  feedback='1 + 1 + 1', checks=[dict(page=10, quote='Al-Biruni'),
                  dict(page=10, quote='Religious practices'), dict(page=10, quote='Cultural background'),
                  dict(page=10, quote='Valuable information')])])
    parts = native_questions(review)
    assert [(q['qid'], q['score'], q['max_score']) for q in parts] == [
        ('Q12.1', 1, 1), ('Q12.2', 1, 1), ('Q12.3', 1, 2)]
    assert len(parts[2]['checks']) == 2
    review['questions'][0]['score'] = 2.5
    with pytest.raises(ValueError): native_questions(review)


def test_ordinary_renderer_does_not_require_or_claim_faculty_review(case):
    report = GradeReport(total_score=1, max_total=1, overall_remarks='Good work.',
                         questions=[QuestionMark(qid='Q1', score=1, max_score=1, page=1,
                             y_fraction=.14, anchor_line_id='P1L1', objective=True,
                             attempted=True, is_correct=True, chosen_option='B', correct_option='B')],
                         evidence_contract=True)
    pdf = pdf_renderer.build_evaluated_pdf(case['source'], 'student.pdf', [], report,
                                         ocr_pages=case['cache'])
    with fitz.open(stream=pdf, filetype='pdf') as output:
        assert len(output) == 2
        assert 'EVALUATED' in output[0].get_text()
    assert not any(m.get('location_source') == 'faculty_source_review' for m in report.render_manifest)
    assert any(m['role'] == 'answer' and m['qid'] == 'Q1' for m in report.render_manifest)
