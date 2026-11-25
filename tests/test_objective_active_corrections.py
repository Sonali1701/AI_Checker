"""Cancelled handwritten prefixes cannot certify the surviving MCQ answer."""
import json
from unittest.mock import patch

import pytest

import grader
from exam_package import build_exam_package
from grader import GradeReport, MarksItem, MarksScheme, ObjectiveChoiceObservation, QuestionMark
from mathpix import LineBox, PageOCR
from strict_grading import (_observed_letter, _objective_choice_from_line,
                            apply_official_objective_evidence, StrictGradeError)


OPTIONS = {
    'A': 'The tax protests',
    'B': "The campaign against 'sati'",
    'C': 'The campaign against untouchability',
    'D': 'The freedom campaign',
}
PAPER = """Q1. Identify the reform campaign.
(A) The tax protests
(B) The campaign against 'sati'
(C) The campaign against untouchability
(D) The freedom campaign
"""


def observation(**overrides):
    data = dict(qid='Q1', chosen_option='C', clear=True,
                written_answer='(b.) The campaigns against Sati',
                cancelled_answer='(c) The', selection_state='replacement',
                answer_kind='option_label', page=1)
    data.update(overrides)
    return ObjectiveChoiceObservation.model_validate(data)


def test_documented_cancelled_prefix_yields_the_separately_copied_live_answer():
    assert _observed_letter(observation(), OPTIONS) == 'B'
    assert _observed_letter(observation(chosen_option='B'), OPTIONS) == 'B'
    assert _observed_letter(observation(written_answer='The campaigns against Sati'), OPTIONS) == 'B'
    assert _observed_letter(observation(), {}) == 'B'  # explicit live label is visual evidence


def test_replacement_does_not_prefer_a_correct_option_or_one_correction_direction():
    opposite = observation(chosen_option='B', cancelled_answer='(B) The campaign against sati',
                           written_answer='(C) The campaign against untouchability')
    assert _observed_letter(opposite, OPTIONS) == 'C'
    rewritten = observation(chosen_option='C', cancelled_answer='(C) scratched words',
                           written_answer='(C) The campaign against untouchability')
    assert _observed_letter(rewritten, OPTIONS) == 'C'


@pytest.mark.parametrize('overrides', [
    {'selection_state': 'single_active', 'cancelled_answer': ''},
    {'selection_state': 'unclear', 'cancelled_answer': ''},
    {'cancelled_answer': '(A) unrelated old answer'},
    {'written_answer': '(C) The (B) The campaigns against Sati'},
    {'selection_state': 'multiple_active'},
    {'written_answer': ''},
    {'cancelled_answer': ''},
])
def test_unexplained_conflict_or_mixed_live_candidates_cannot_be_certified(overrides):
    assert _observed_letter(observation(**overrides), OPTIONS) == ''


def test_active_label_and_active_words_must_not_name_different_uncancelled_options():
    assert _observed_letter(observation(chosen_option='B', selection_state='single_active',
                                       cancelled_answer='',
                                       written_answer='(B) The campaign against untouchability'), OPTIONS) == ''
    consistent = observation(chosen_option='C', selection_state='single_active',
                             cancelled_answer='',
                             written_answer='(C) The campaign against untouchability')
    assert _observed_letter(consistent, OPTIONS) == 'C'


def test_assertion_reason_statement_references_are_not_competing_live_choices():
    observed = observation(chosen_option='D', selection_state='single_active',
                           cancelled_answer='', written_answer='(D) (A) is false but (R) true')
    assert _observed_letter(observed, {}) == 'D'


def test_reader_preserves_cancelled_and_live_transcriptions_in_one_existing_call():
    payload = {'items': [dict(qid='Q1', chosen_option='C', clear=True,
                             written_answer='(b.) The campaigns against Sati',
                             cancelled_answer='(c) The', selection_state='replacement',
                             answer_kind='option_label', page=1,
                             answer_bbox=[.36,.23,.91,.27], location_clear=True)]}
    response = {'content': [{'type': 'text', 'text': json.dumps(payload)}]}
    with patch.object(grader, '_claude_call', return_value=response) as call:
        observed = grader.read_objective_choices(
            [b'png'], ['Q1'], google_token='test', task_id='test', options={'Q1': OPTIONS})
    assert call.call_count == 1
    assert observed[0].cancelled_answer == '(c) The'
    assert observed[0].written_answer == '(b.) The campaigns against Sati'
    assert observed[0].chosen_option == 'B'  # downstream final-choice/location agreement
    assert _observed_letter(observed[0], OPTIONS) == 'B'
    assert observed[0].answer_bbox == [.36,.23,.91,.27]


def grade(observed, primary='B', line_text='1) (c) The (b) The campaigns against Sati',
          confidence=.44, allow_unconfirmed=False):
    scheme = MarksScheme(total=1, items=[MarksItem(qid='Q1', max=1)])
    package = build_exam_package(exam_type='school', student_class='12', subject='Sociology',
        level='12', paper='', attempt='', applicable_law_date=None, total_marks=1,
        question_paper=b'paper', answer_key=b'key', key_source='upload', marks_scheme=scheme,
        scheme_source='text', question_paper_text=PAPER, answer_key_text='Q1\n(B)\n')
    report = GradeReport(total_score=0, max_total=1, overall_remarks='', evidence_contract=True,
                        questions=[QuestionMark(qid='Q1', score=0, max_score=1, page=1,
                        y_fraction=.25, anchor_line_id='P1L1', chosen_option=primary,
                        objective=True)])
    page = PageOCR(page=1, image_width=1000, image_height=1400, lines=[
        LineBox('P1L1', line_text,
                (60,300,950,345),1,confidence=confidence)])
    apply_official_objective_evidence(report, package, [page],
                                     visual_choices={'Q1': observed} if observed is not None else None,
                                     question_paper_text=PAPER,allow_unconfirmed=allow_unconfirmed)
    return report


def test_final_score_uses_verified_live_answer_and_keeps_cancellation_disagreement_visible():
    correct = grade(observation())
    assert (correct.questions[0].chosen_option, correct.questions[0].score) == ('B',1)
    assert correct.objective_visual_verified_qids == ['Q1']
    opposite = observation(chosen_option='B', cancelled_answer='(B) The campaign against sati',
                           written_answer='(C) The campaign against untouchability')
    wrong = grade(opposite, primary='C',
                  line_text='1) (b) The (c) The campaign against untouchability')
    assert (wrong.questions[0].chosen_option,wrong.questions[0].score) == ('C',0)
    with pytest.raises(StrictGradeError):
        grade(observation(selection_state='single_active',cancelled_answer=''), primary='C')


def test_cancelled_correct_words_cannot_rescue_verified_wrong_label_only_replacement():
    wrong = observation(chosen_option='B', cancelled_answer='(B) The campaign against sati',
                        written_answer='(C)')
    report = grade(wrong,primary='C',line_text='1) (b) The campaign against sati (c)')
    assert (report.questions[0].chosen_option,report.questions[0].score) == ('C',0)
    assert report.objective_visual_verified_qids == ['Q1']


def test_clean_parsed_ocr_label_value_disagreement_requires_key_blind_read():
    # A clean glyph is not corroborated when the copied words name another option.
    with pytest.raises(StrictGradeError,match='OCR label and option words disagree'):
        grade(None,primary='A',line_text='1) (A) The campaign against sati',confidence=.99)
    unresolved = grade(None,primary='A',line_text='1) (A) The campaign against sati',
                       confidence=.99,allow_unconfirmed=True)
    assert unresolved.objective_unconfirmed_qids == ['Q1']
    assert unresolved.objective_visual_verified_qids == []
    assert unresolved.questions[0].chosen_option == 'A'  # no correct-key-word auto rescue
    live = observation(chosen_option='B',written_answer="(B) The campaign against sati",
                       cancelled_answer='',selection_state='single_active')
    confirmed = grade(live,primary='B',line_text='1) (A) The campaign against sati',confidence=.99)
    assert (confirmed.questions[0].chosen_option,confirmed.questions[0].score) == ('B',1)


@pytest.mark.parametrize('label', ['Answer. 1','Answer - 1','Answer-1','Ans 1','Q1','Question 1'])
def test_standalone_question_label_is_never_a_numeric_option_even_with_clean_ocr(label):
    assert _objective_choice_from_line(label,1) == ''
    with pytest.raises(StrictGradeError,match='no unambiguous anchored'):
        grade(None,primary='B',line_text=label,confidence=1)
    # Actual numeric choices and numbered answer rows keep their former meaning.
    assert _objective_choice_from_line('1) (2)',1) == 'B'
