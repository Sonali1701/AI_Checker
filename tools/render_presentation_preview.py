"""Replay saved evidence through the production renderer without grading calls.

No coordinate overrides or sample-specific placement rules are applied here.
Existing evidence provenance (including prior manual review) is preserved.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from grader import GradeReport
from mathpix import pageocr_from_dict, synthesize_diagram_regions
from pdf_renderer import build_evaluated_pdf
from grading_quality import check_render_manifest
from preflight import _check_rendered
from strict_grading import report_decision_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--job', required=True, type=Path)
    parser.add_argument('--index', default='0')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    report = GradeReport.model_validate_json(args.report.read_text(encoding='utf-8'))
    saved = json.loads(args.job.read_text(encoding='utf-8'))['reports'][args.index]
    original = GradeReport.model_validate(saved['report'])
    assert report_decision_digest(report) == report_decision_digest(original), 'Saved grading decisions differ'
    questions = report.model_dump()['questions']
    pages = [synthesize_diagram_regions(pageocr_from_dict(p)) for p in saved['ocr']]
    pdf = build_evaluated_pdf(args.source.read_bytes(), args.source.name, [], report, pages, mark_scale=.6)
    assert questions == report.model_dump()['questions'], 'Presentation changed grading decisions'
    issues = check_render_manifest(report, pdf) + _check_rendered(pdf, report)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'preview.pdf').write_bytes(pdf)
    (args.out / 'report.json').write_text(report.model_dump_json(indent=2), encoding='utf-8')
    result = {'issues': [i.__dict__ for i in issues], 'score': report.total_score,
              'decisions_unchanged': True, 'fresh_ai_verification': False,
              'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
              'pdf_sha256': hashlib.sha256(pdf).hexdigest()}
    (args.out / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        for i, page in enumerate(doc):
            page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False).save(args.out / f'page-{i+1:02}.png')
    print(json.dumps(result, indent=2))
    return bool(issues)


if __name__ == '__main__':
    raise SystemExit(main())
