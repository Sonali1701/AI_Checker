"""Rebuild a checked copy from explicit, source-bound faculty-style corrections.

This is an offline review operation, not a provider run or automatic release.
The original source, review provenance, geometry and rendered PDF hashes remain
in the audit file. Every annotated location must be supplied by the reviewer.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
from pathlib import Path

import fitz
from PIL import Image

RED = (0.75, 0.08, 0.12)
INK = (0.10, 0.17, 0.25)
MUTED = (0.30, 0.37, 0.45)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_qid(value):
    value = str(value).strip()
    match = re.fullmatch(r'[Qq]?(\d+)(\.[a-zA-Z])?',value)
    if not match:
        raise ValueError('Invalid reviewed question ID')
    return 'Q'+str(int(match.group(1)))+(match.group(2).lower() if match.group(2) else '')


def validate_review(review, source_bytes, official_bytes, page_count, expected_maxima):
    if not expected_maxima:
        raise ValueError('A complete official marks table is required')
    if review.get('source_sha256') != digest(source_bytes):
        raise ValueError('Review does not bind this original student PDF')
    if review.get('official_sha256') != digest(official_bytes):
        raise ValueError('Review does not bind the official paper/key')
    if not review.get('reviewer') or review.get('all_questions_reviewed') is not True:
        raise ValueError('Explicit complete review provenance is required')
    if review.get('unresolved_issues'):
        raise ValueError('Unresolved review issues prevent delivery')
    if sorted(review.get('original_pages_reviewed', [])) != list(range(1, page_count + 1)):
        raise ValueError('Every original page must be reviewed')
    questions = review.get('questions', [])
    qids = [q.get('qid') for q in questions]
    if len(qids) != len(set(qids)) or set(qids) != set(expected_maxima):
        raise ValueError('Reviewed questions do not match the complete official maxima')
    for key, turn in review.get('page_rotations', {}).items():
        if not str(key).isdigit() or not 1 <= int(key) <= page_count or turn not in (0, 90, 180, 270):
            raise ValueError('Invalid reviewed page rotation')
    for q in questions:
        if not isinstance(q.get('attempted'), bool) or not isinstance(q.get('objective'), bool):
            raise ValueError('Attempt and objective decisions must be explicit')
        maximum, score = q.get('max_score'), q.get('score')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (maximum, score)):
            raise ValueError('Marks must be finite numbers')
        if maximum != expected_maxima[q['qid']] or not 0 <= score <= maximum or score * 2 != round(score * 2):
            raise ValueError('Invalid marks or official maximum')
        if not q.get('attempted') and score:
            raise ValueError('Unattempted work cannot earn credit')
        if q.get('objective') and score not in (0, maximum):
            raise ValueError('Objective marks must be all or nothing')
        if not isinstance(q.get('feedback'), str) or not q['feedback'].strip():
            raise ValueError('Every question needs reviewed feedback')
        if q.get('attempted') and not q.get('checks'):
            raise ValueError('Attempted answers require genuine reviewed locations')
        for page in q.get('answer_pages', []):
            if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= page_count:
                raise ValueError('Invalid answer page')
        for check in q.get('checks', []):
            page, box = check.get('page'), check.get('bbox')
            if page not in q.get('answer_pages', []) or not isinstance(page, int) or not 1 <= page <= page_count:
                raise ValueError('Check does not belong to a reviewed answer page')
            if (not isinstance(box, list) or len(box) != 4
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) or not 0 <= v <= 1 for v in box)
                    or not box[0] < box[2] or not box[1] < box[3]):
                raise ValueError('Invalid actual-source evidence box')
            if check.get('verdict') not in ('correct', 'wrong') or not str(check.get('quote', '')).strip():
                raise ValueError('A check needs a verdict and copied source evidence')
            if check['verdict'] == 'wrong' and not str(check.get('reason', '')).strip():
                raise ValueError('Wrong writing needs a correction')
    return sum(q['score'] for q in questions)


def _lines(text, font, size, width):
    # Built-in Helvetica does not encode typographic punctuation reliably.
    # Preserve the wording while rendering readable equivalents.
    text = str(text).translate(str.maketrans({
        '\u2018':"'", '\u2019':"'", '\u201c':'"', '\u201d':'"',
        '\u2013':'-', '\u2014':'-', '\u2026':'...', '\u00a0':' ',
    }))
    words, lines, row = text.split(), [], ''
    for word in words:
        candidate = (row + ' ' + word).strip()
        if row and font.text_length(candidate, fontsize=size) > width:
            lines.append(row)
            row = word
        else:
            row = candidate
    if row:
        lines.append(row)
    return lines or ['']


def _text(page, x, y, text, *, size=10, color=INK, bold=False, width=280):
    name = 'hebo' if bold else 'helv'
    font = fitz.Font(name)
    for line in _lines(text, font, size, width):
        page.insert_text((x, y), line, fontsize=size, fontname=name, color=color)
        y += size * 1.38
    return y


def _photo(page, turn):
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
    if turn:
        image = image.rotate(turn, expand=True)
    return image


def _trim_white(image):
    import numpy as np
    values = np.asarray(image.convert('L'))
    ys, xs = np.where(values < 248)
    if not len(xs):
        return (0, 0, image.width, image.height)
    pad = max(4, round(min(image.size) * .015))
    box = (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
           min(image.width, int(xs.max()) + pad + 1), min(image.height, int(ys.max()) + pad + 1))
    share = (box[2] - box[0]) * (box[3] - box[1]) / (image.width * image.height)
    return box if .12 <= share <= .85 else (0, 0, image.width, image.height)


def _draw_check(page, box, verdict, tag, gutter_y):
    # Location is explicit reviewed evidence. Place the symbol in the review
    # gutter, aligned to its actual row; never paint over the student's words.
    source_y = (box.y0 + box.y1) / 2
    y = gutter_y
    x = 629
    if verdict == 'correct':
        page.draw_polyline([(x, y), (x+4, y+4), (x+12, y-6)], color=RED, width=1.5)
    else:
        page.draw_line((x+1, y-5), (x+10, y+4), color=RED, width=1.5)
        page.draw_line((x+1, y+4), (x+10, y-5), color=RED, width=1.5)
    page.insert_text((x+16, y+3), tag, fontsize=7, fontname='helv', color=RED)
    # Keep connectors outside the source image; a light outline identifies the
    # actual reviewed passage without a line crossing another column's words.
    page.draw_rect(box + (-1, -1, 1, 1), color=(.64, .68, .72), width=.35)
    page.draw_polyline([(620,source_y),(625,source_y),(627,y)], color=(.66,.68,.70), width=.35)
    return [x, y-7, x+55, y+7]


def render_review(source_bytes, review):
    original = fitz.open(stream=source_bytes, filetype='pdf')
    out = fitz.open()
    questions = review['questions']
    total = sum(q['score'] for q in questions)
    maximum = sum(q['max_score'] for q in questions)
    subject = review['subject']
    manifest = []
    for number, source_page in enumerate(original, 1):
        image = _photo(source_page, review.get('page_rotations', {}).get(str(number), 0))
        crop = _trim_white(image)
        cropped = image.crop(crop)
        scale = min(595 / cropped.width, 775 / cropped.height)
        image_rect = fitz.Rect(20, 64, 20+cropped.width*scale, 64+cropped.height*scale)
        page = out.new_page(width=990, height=880)
        page.draw_rect(page.rect, fill=(1, 1, 1), color=None)
        _text(page, 22, 25, f'{subject} | Reviewed evaluation', size=14, bold=True, width=750)
        _text(page, 22, 43, f'{review["sample_id"]}  |  {total:g} / {maximum:g}  |  Original page {number}/{len(original)}',
              size=9, color=MUTED, width=750)
        buf = io.BytesIO()
        cropped.save(buf, format='JPEG', quality=95)
        page.insert_image(image_rect, stream=buf.getvalue())
        page.draw_line((693, 64), (693, 838), color=(.84, .86, .89), width=.6)
        _text(page, 709, 80, 'Marks and feedback', size=12, bold=True, width=255)
        y = 102
        on_page = [q for q in questions if number in q.get('answer_pages', [])]
        for q in on_page:
            text = q['feedback'] if q['answer_pages'][0] == number else 'Answer continued. See the full question feedback in the summary.'
            needed = 28 + len(_lines(text, fitz.Font('helv'), 9, 255)) * 12.42
            if y + needed > 820:
                _text(page, 709, y, 'Remaining feedback is in the complete question summary.', size=9, color=MUTED, width=250)
                break
            y = _text(page, 709, y, f'{q["qid"]}   {q["score"]:g} / {q["max_score"]:g}', size=10, bold=True, width=255)
            y = _text(page, 709, y+3, text, size=9, width=255) + 13
        located_checks = []
        for q in on_page:
            for i, check in enumerate(q.get('checks', [])):
                if check['page'] != number:
                    continue
                l, t, r, b = check['bbox']
                box = fitz.Rect(20+(l*image.width-crop[0])*scale,
                                64+(t*image.height-crop[1])*scale,
                                20+(r*image.width-crop[0])*scale,
                                64+(b*image.height-crop[1])*scale)
                if not image_rect.contains(box + (.5, .5, -.5, -.5)):
                    raise ValueError(f'{q["qid"]} reviewed evidence falls outside the displayed source')
                located_checks.append((q, i, check, box))
        occupied = []
        for q, i, check, box in sorted(located_checks, key=lambda entry:(entry[3].y0,entry[3].x0)):
                ideal = (box.y0+box.y1)/2
                y = max(72,min(826,ideal))
                for distance in range(0,800,13):
                    choices = [y+distance, y-distance] if distance else [y]
                    match = next((v for v in choices if 72 <= v <= 826 and all(abs(v-old)>=12 for old in occupied)),None)
                    if match is not None:
                        y = match
                        break
                else:
                    raise ValueError('Too many checked regions for a readable review gutter')
                occupied.append(y)
                mark = _draw_check(page, box, check['verdict'], q['qid'], y)
                manifest.append({'qid':q['qid'], 'original_page':number, 'check_index':i,
                    'verdict':check['verdict'], 'source_bbox':check['bbox'], 'rendered_evidence_bbox':list(box),
                    'mark_bbox':mark, 'quote':check['quote'], 'reason':check.get('reason', ''),
                    'location_source':'explicit_faculty_style_review', 'page_crop_pixels':list(crop)})
        _text(page, 22, 861, 'Ticks and crosses identify reviewed passages. Use the question marks and reasons in the complete feedback summary.',
              size=8, color=MUTED, width=950)
    # A complete, paginated summary is authoritative when an answer spans pages.
    page, y = None, 900
    for q in questions:
        font = fitz.Font('helv')
        needed = 28 + len(_lines(q['feedback'], font, 10, 530)) * 13.8
        if y + needed > 794:
            page = out.new_page(width=595, height=842)
            _text(page, 30, 35, f'{subject} | Question feedback', size=15, bold=True, width=535)
            _text(page, 30, 55, f'{review["sample_id"]}  |  Total {total:g} / {maximum:g}', size=10, width=535)
            y = 82
        y = _text(page, 30, y, f'{q["qid"]}    {q["score"]:g} / {q["max_score"]:g}', size=11, bold=True, width=530)
        y = _text(page, 30, y+3, q['feedback'], size=10, width=530) + 12
    out.set_metadata({'title':f'{subject} reviewed copy {review["sample_id"]}',
                      'author':'AI Checker - faculty-style review',
                      'subject':'Saved evaluation corrected through independent source review'})
    pdf = out.tobytes(garbage=4, deflate=True)
    out.close()
    original.close()
    return pdf, manifest


def apply_review(source, official, review_path, maxima, output):
    source_bytes, official_bytes = source.read_bytes(), official.read_bytes()
    review = json.loads(review_path.read_text(encoding='utf-8'))
    for question in review.get('questions',[]):
        question['qid'] = canonical_qid(question.get('qid'))
    with fitz.open(stream=source_bytes, filetype='pdf') as doc:
        score = validate_review(review, source_bytes, official_bytes, len(doc), maxima)
    with fitz.open(stream=official_bytes, filetype='pdf') as official_doc:
        if sorted(review.get('official_pages_reviewed', [])) != list(range(1,len(official_doc)+1)):
            raise ValueError('Every official question/key page must be reviewed')
    pdf, geometry = render_review(source_bytes, review)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pdf)
    audit = {'sample_id':review['sample_id'], 'score':score,'maximum':sum(maxima.values()),
        'reviewer':review['reviewer'], 'review_mode':'independent_faculty_style_correction',
        'fresh_provider_grading':False, 'source_sha256':digest(source_bytes),
        'official_sha256':digest(official_bytes), 'review_sha256':digest(review_path.read_bytes()),
        'pdf_sha256':digest(pdf), 'original_page_count':len(review['original_pages_reviewed']),
        'geometry':geometry, 'correction_notes':review.get('correction_notes', [])}
    output.with_suffix('.audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--official', required=True, type=Path)
    parser.add_argument('--review', required=True, type=Path)
    parser.add_argument('--maxima', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = apply_review(args.source, args.official, args.review,
                          json.loads(args.maxima.read_text(encoding='utf-8')), args.out)
    print(json.dumps({k:v for k,v in result.items() if k not in ('geometry','correction_notes')}))
