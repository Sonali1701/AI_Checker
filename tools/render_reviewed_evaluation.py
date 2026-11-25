"""Apply a complete source-bound review to a saved evaluation in its native format.

This is a local correction/rerender, not a new provider grading run. Original
provider reports and OCR caches are immutable. Explicit review locations stay
separate from provider verification and automatic-release decisions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from grader import Criterion, GradeReport, QuestionMark
from exam_package import normalize_qid
from mathpix import PageOCR, pageocr_from_dict
from pdf_renderer import build_evaluated_pdf
from pipeline import mark_scale_for
from tools.apply_faculty_review import canonical_qid, validate_review
from tools.rerender import apply_reviewed_rotations, reviewed_rotation_map


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rotate_cache_page(page, turn):
    """Transform a cache's explicitly reviewed coordinate frame, without OCR calls."""
    page=copy.deepcopy(page)
    if not turn:
        return page
    w,h=page.image_width,page.image_height
    def box(value):
        l,t,r,b=value
        corners=[(l,t),(l,b),(r,t),(r,b)]
        if turn==90:
            points=[(y,w-x) for x,y in corners]
        elif turn==180:
            points=[(w-x,h-y) for x,y in corners]
        elif turn==270:
            points=[(h-y,x) for x,y in corners]
        else:
            raise ValueError('Invalid explicitly reviewed cache rotation')
        return (min(x for x,y in points),min(y for x,y in points),
                max(x for x,y in points),max(y for x,y in points))
    for line in page.lines:
        line.bbox=box(line.bbox)
    for word in page.words:
        word.bbox=box(word.bbox)
    if turn in (90,270):
        page.image_width,page.image_height=h,w
    return page


def native_questions(review):
    """Retain the official History source-question subparts (1+1+2)."""
    questions=[]
    for question in review['questions']:
        if review['subject']=='History' and question['qid']=='Q12':
            if question['score'] not in (0,3):
                raise ValueError('History Q12 needs an explicit reviewed subpart allocation')
            checks=question.get('checks',[])
            selections=[checks[:1],checks[1:2],checks[2:]]
            for n,maximum in ((1,1),(2,1),(3,2)):
                item=copy.deepcopy(question)
                item.update(qid=f'Q12.{n}',max_score=maximum,
                            score=1 if question['score']==3 else 0,
                            checks=selections[n-1],
                            answer_pages=sorted({c['page'] for c in selections[n-1]}))
                if n==3 and question['score']==3:
                    item['feedback']='Cultural background can bias a source. Its usefulness does not replace critical scrutiny of that bias.'
                questions.append(item)
        else:
            questions.append(copy.deepcopy(question))
    return questions


def render_saved_review(source,official,saved_report,saved_ocr,review_path,maxima,
                        output,*,overall_remarks,short_notes=None,cache_rotations=None):
    source_bytes=source.read_bytes()
    official_bytes=official.read_bytes()
    original_report_bytes=saved_report.read_bytes()
    original_ocr_bytes=saved_ocr.read_bytes()
    review=json.loads(review_path.read_text(encoding='utf-8'))
    for q in review['questions']:
        q['qid']=canonical_qid(q['qid'])
    with fitz.open(stream=source_bytes,filetype='pdf') as original:
        count=len(original)
    total=validate_review(review,source_bytes,official_bytes,count,maxima)
    with fitz.open(stream=official_bytes,filetype='pdf') as key:
        if sorted(review.get('official_pages_reviewed',[]))!=list(range(1,len(key)+1)):
            raise ValueError('Every official paper/key page must be reviewed')
    rotations=reviewed_rotation_map({'page_rotations':review.get('page_rotations',{}),
                                    'source_sha256':review['source_sha256']},count,source_bytes)
    render_source=apply_reviewed_rotations(source_bytes,rotations)
    report=GradeReport.model_validate_json(original_report_bytes)
    old_questions={normalize_qid(q.qid):q for q in report.questions}
    corrected=native_questions(review)
    short_notes=short_notes or {}
    native_marks=[]
    for item in corrected:
        qid=item['qid']
        checks=item.get('checks',[])
        if checks:
            eligible=[(i,c) for i,c in enumerate(checks) if c['verdict']=='correct']
            selected=(eligible if item['score']>0 and eligible else list(enumerate(checks)))
            selected_index=item.get('native_total_check_index')
            if selected_index is not None:
                if (isinstance(selected_index,bool) or not isinstance(selected_index,int)
                        or selected_index not in {i for i,_ in selected}):
                    raise ValueError('Explicit total anchor must be an eligible reviewed check')
                index,anchor=selected_index,checks[selected_index]
            else:
                index,anchor=max(selected,key=lambda pair:(pair[1]['page'],pair[1]['bbox'][3]))
            lid=f'faculty:{qid.lower()}:{index}'
            page=anchor['page']
            y_fraction=sum(anchor['bbox'][1::2])/2
        else:
            index,anchor,lid=None,None,''
            page=(item.get('answer_pages') or [1])[0]
            y_fraction=.5
        old=old_questions.get(normalize_qid(qid))
        objective=item['objective']
        note=short_notes.get(qid)
        if note is None:
            note=re.split(r'(?<=[.!?])\s+',item['feedback'])[0]
        remark=note if item['score']<item['max_score'] else ''
        if not item['attempted']:
            remark='Not answered.'
        correct=re.search(r'(?:correct(?:\s+option)?\s*[:.]?\s*|correct answer(?:\s+is)?\s*[:.]?\s*)\(?([A-D])\b',
                          item['feedback'],re.IGNORECASE)
        correct_option=correct.group(1).upper() if correct else (old.correct_option if old and objective else '')
        chosen=re.search(r'(?:chosen|selected)\s+\(?([A-D])\b',item['feedback'],re.IGNORECASE)
        chosen_option=chosen.group(1).upper() if chosen else (correct_option if objective and item['score']==item['max_score'] else '')
        criteria=[]
        if not objective:
            criteria=[Criterion(description='Reviewed answer against official key',awarded=item['score'],
                max=item['max_score'],met=item['score']==item['max_score'],
                evidence=item['feedback'] if item['score']<item['max_score'] else '',
                anchor_quote=anchor['quote'] if anchor else '',step_line_id=lid,
                evidence_kind='written' if item['attempted'] else 'missing')]
        style='single'
        if review['subject']=='History' and qid=='Q13' and item['attempted']:
            if len(checks)!=5 or sum(c['verdict']=='correct' for c in checks)!=item['score']:
                raise ValueError('Map awards must match five genuinely reviewed 1-mark locations')
            style='per_step'
            criteria=[Criterion(description=f'Map location {i+1}',max=1,
                awarded=1 if c['verdict']=='correct' else 0,met=c['verdict']=='correct',
                evidence=c.get('reason',''),anchor_quote=c['quote'],
                step_line_id=f'faculty:{qid.lower()}:{i}',evidence_kind='diagram') for i,c in enumerate(checks)]
        native_marks.append(QuestionMark(qid=qid,score=item['score'],max_score=item['max_score'],
            page=page,y_fraction=y_fraction,anchor_line_id=lid,remark=remark,criteria=criteria,
            mark_style=style,objective=objective,attempted=item['attempted'],
            is_correct=item['score']==item['max_score'] and item['attempted'],
            correct_option=correct_option,chosen_option=chosen_option))
        item['native_anchor_line_id']=lid
        item['native_short_note']=remark
        for check in checks:
            # Corrections belong beside incorrect statements even when enough
            # other valid material earns the whole answer its marks. They add no
            # extra deduction and use the existing native margin-note format.
            check['show_reason']=check['verdict']=='wrong' and not objective
    report.questions=native_marks
    report.total_score=total
    report.max_total=sum(maxima.values())
    report.overall_remarks=overall_remarks
    # Verification belonged to the original provider report, not to these edited
    # awards/locations. Retain that original report, discard stale certificates in
    # the corrected copy, and explicitly keep automatic release unapproved.
    report.verified_step_boxes={}
    report.verified_step_anchors={}
    report.objective_visual_verified_qids=[]
    report.review_reasons=list(report.review_reasons)+['Saved evaluation corrected through faculty-style source review; no fresh provider recheck.']
    evidence={'reviewer':review['reviewer'],'review_sha256':digest(review_path.read_bytes()),
        'original_source_sha256':digest(source_bytes),'official_sha256':digest(official_bytes),
        'render_source_sha256':digest(render_source),'original_pages_reviewed':list(range(1,count+1)),
        'all_questions_reviewed':True,'unresolved_issues':[],'questions':corrected}
    cache=[pageocr_from_dict(p) for p in json.loads(original_ocr_bytes)]
    cache_rotations=reviewed_rotation_map({str(k):v for k,v in (cache_rotations or {}).items()},
                                        count,source_bytes)
    cache=[rotate_cache_page(page,cache_rotations.get(page.page,0)) for page in cache]
    for page in cache:
        page.lines=[line for line in page.lines if line.confidence is None or line.confidence>=.6]
    pdf=build_evaluated_pdf(render_source,source.name,[],report,ocr_pages=cache,
           mark_scale=mark_scale_for('school',review['subject'],'school'),reviewed_evidence=evidence)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(pdf)
    audit={'sample_id':review['sample_id'],'renderer':'pdf_renderer.build_evaluated_pdf / teacher_layout',
           'score':report.total_score,'maximum':report.max_total,'fresh_provider_grading':False,
           'automatic_release_authorized':False,'source_sha256':digest(source_bytes),
           'render_source_sha256':digest(render_source),'official_sha256':digest(official_bytes),
           'review_sha256':digest(review_path.read_bytes()),'original_report_sha256':digest(original_report_bytes),
           'original_ocr_sha256':digest(original_ocr_bytes),'pdf_sha256':digest(pdf),
           'original_page_count':count,'page_rotations':review.get('page_rotations',{}),
           'cache_frame_rotations':cache_rotations,'reviewed_evidence':evidence,
           'render_manifest':report.render_manifest,'render_page_layouts':report.render_page_layouts,
           'unanchored_step_qids':report.unanchored_step_qids}
    output.with_suffix('.audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    output.with_suffix('.report.json').write_text(report.model_dump_json(indent=2),encoding='utf-8')
    return audit


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','official','saved-report','saved-ocr','review','maxima','out'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--overall-remarks',required=True)
    args=parser.parse_args()
    audit=render_saved_review(args.source,args.official,args.saved_report,args.saved_ocr,args.review,
          json.loads(args.maxima.read_text()),args.out,overall_remarks=args.overall_remarks)
    print(json.dumps({k:audit[k] for k in ('sample_id','renderer','score','maximum','fresh_provider_grading','pdf_sha256')}))
