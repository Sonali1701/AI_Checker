import copy
import importlib.util
import json
from pathlib import Path

import fitz
import pytest

SPEC = importlib.util.spec_from_file_location('faculty_delivery',
    Path(__file__).resolve().parents[1]/'tools'/'apply_faculty_review.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def pdf_bytes(text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((100,100),text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def case():
    source,official = pdf_bytes('Student chose B'),pdf_bytes('Correct option B')
    review = {'sample_id':'sample-01','subject':'Review test','reviewer':'Test reviewer',
        'source_sha256':module.digest(source),'official_sha256':module.digest(official),
        'all_questions_reviewed':True,'unresolved_issues':[],
        'original_pages_reviewed':[1],'official_pages_reviewed':[1],
        'questions':[{'qid':'Q1','max_score':1,'score':1,'attempted':True,
          'objective':True,'feedback':'Correct option B.','answer_pages':[1],
          'checks':[{'page':1,'bbox':[.16,.10,.38,.13],'verdict':'correct',
                     'quote':'Student chose B','reason':''}]}]}
    return source,official,review,{'Q1':1}


@pytest.mark.parametrize('which', ['source','official'])
def test_review_cannot_be_applied_to_other_pdf(case,which):
    source,official,review,maxima = case
    if which=='source':
        source=pdf_bytes('Different student')
    else:
        official=pdf_bytes('Different examination')
    with pytest.raises(ValueError,match='bind'):
        module.validate_review(review,source,official,1,maxima)


@pytest.mark.parametrize('change,message',[
    ('missing_question','complete official maxima'),
    ('wrong_maximum','Invalid marks'),
    ('partial_objective','all or nothing'),
    ('unresolved','Unresolved'),
    ('unreviewed_page','Every original page'),
    ('wrong_page','reviewed answer page'),
    ('out_of_bounds','evidence box'),
    ('unattempted_credit','Unattempted'),
    ('wrong_without_reason','correction'),
])
def test_incomplete_or_unsupported_corrections_fail_closed(case,change,message):
    source,official,review,maxima = case
    q=review['questions'][0]
    if change=='missing_question':
        review['questions']=[]
    elif change=='wrong_maximum':
        q['max_score']=2
    elif change=='partial_objective':
        q['score']=.5
    elif change=='unresolved':
        review['unresolved_issues']=['Ambiguous source answer']
    elif change=='unreviewed_page':
        review['original_pages_reviewed']=[]
    elif change=='wrong_page':
        q['checks'][0]['page']=2
    elif change=='out_of_bounds':
        q['checks'][0]['bbox'][0]=-.1
    elif change=='unattempted_credit':
        q['attempted']=False
    elif change=='wrong_without_reason':
        q['checks'][0]['verdict']='wrong'
    with pytest.raises(ValueError,match=message):
        module.validate_review(review,source,official,1,maxima)


def test_complete_delivery_has_original_image_feedback_and_bound_audit(case,tmp_path):
    source,official,review,maxima=case
    paths=[tmp_path/name for name in ('source.pdf','official.pdf','review.json','checked.pdf')]
    paths[0].write_bytes(source)
    paths[1].write_bytes(official)
    # Reviewers may use plain numeric IDs; the canonical ID must be on the output.
    review['questions'][0]['qid']='1'
    paths[2].write_text(json.dumps(review),encoding='utf-8')
    audit=module.apply_review(*paths[:3],maxima,paths[3])
    assert audit['source_sha256']==module.digest(paths[0].read_bytes())
    assert audit['pdf_sha256']==module.digest(paths[3].read_bytes())
    assert audit['fresh_provider_grading'] is False
    assert audit['score']==1 and audit['maximum']==1
    with fitz.open(paths[3]) as output:
        assert len(output)==2
        assert len(output[0].get_images())==1
        text='\n'.join(page.get_text() for page in output)
        assert 'Q1' in text and 'Correct option B.' in text
        assert 'Total 1 / 1' in text
        box=fitz.Rect(audit['geometry'][0]['rendered_evidence_bbox'])
        assert output[0].rect.contains(box)
        assert fitz.Rect(audit['geometry'][0]['mark_bbox']).x0>box.x1
    assert paths[0].read_bytes()==source


def test_summary_keeps_every_question_when_page_feedback_fills(case):
    source,official,review,_=case
    base=review['questions'][0]
    review['questions']=[]
    for number in range(1,25):
        q=copy.deepcopy(base)
        q['qid']=f'Q{number}'
        q['feedback']=f'Unique feedback for question {number}. '+'Explain the relevant point clearly. '*12
        q['checks']=[]
        q['attempted']=False
        q['score']=0
        review['questions'].append(q)
    maxima={q['qid']:1 for q in review['questions']}
    module.validate_review(review,source,official,1,maxima)
    pdf,_=module.render_review(source,review)
    with fitz.open(stream=pdf,filetype='pdf') as output:
        summary='\n'.join(p.get_text() for p in list(output)[1:])
        for number in range(1,25):
            assert f'Unique feedback for question {number}.' in summary
        for page in output:
            for block in page.get_text('blocks'):
                assert block[0]>=0 and block[1]>=0
                assert block[2]<=page.rect.width and block[3]<=page.rect.height


def test_missing_official_page_review_prevents_delivery(case,tmp_path):
    source,official,review,maxima=case
    review['official_pages_reviewed']=[]
    source_path=tmp_path/'source.pdf'
    official_path=tmp_path/'official.pdf'
    review_path=tmp_path/'review.json'
    source_path.write_bytes(source)
    official_path.write_bytes(official)
    review_path.write_text(json.dumps(review))
    output=tmp_path/'checked.pdf'
    with pytest.raises(ValueError,match='Every official'):
        module.apply_review(source_path,official_path,review_path,maxima,output)
    assert not output.exists()


def test_feedback_typographic_apostrophe_remains_readable(case):
    source,_,review,_=case
    review['questions'][0]['feedback']='An individual\u2019s answer is correct.'
    pdf,_=module.render_review(source,review)
    with fitz.open(stream=pdf,filetype='pdf') as output:
        text='\n'.join(page.get_text() for page in output)
        assert "individual's answer" in text
        assert 'individual\u00b7s' not in text
