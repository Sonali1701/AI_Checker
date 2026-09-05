# -*- coding: utf-8 -*-
"""Re-draw an evaluated PDF from a saved report + cached OCR.

Placement bugs are renderer bugs, so verifying a fix must not mean paying to
grade the sheet again. The OCR is fetched once and cached next to the report;
after that every re-render is free.
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mathpix
import pw_access
from grader import GradeReport, pdf_or_image_to_pngs
from pdf_renderer import build_evaluated_pdf
from pipeline import mark_scale_for


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="run dir holding report.json")
    ap.add_argument("--student", required=True)
    ap.add_argument("--out", default="", help="output pdf (default <dir>/ai_rerender.pdf)")
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--exam-type", default="professional")
    ap.add_argument("--subject", default="")
    args = ap.parse_args()

    report = GradeReport.model_validate_json(
        open(os.path.join(args.dir, "report.json"), encoding="utf-8").read())
    sa_bytes = open(args.student, "rb").read()
    sa_pngs = pdf_or_image_to_pngs(sa_bytes, "student.pdf")

    ocr_path = os.path.join(args.dir, "ocr.json")
    if os.path.exists(ocr_path):
        ocr_pages = [mathpix.pageocr_from_dict(d)
                     for d in json.load(open(ocr_path, encoding="utf-8"))]
        print(f"OCR from cache: {len(ocr_pages)} pages, "
              f"{sum(len(p.lines) for p in ocr_pages)} lines")
    else:
        token = open(args.token_file, encoding="utf-8").read().strip()
        if not token or not pw_access.check_allowed(token):
            sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")
        ocr_pages = mathpix.ocr_all_pages(sa_pngs, google_token=token,
                                          task_id=pw_access.new_task_id("rerender"),
                                          filename="student.pdf")
        json.dump([mathpix.pageocr_to_dict(p) for p in ocr_pages],
                  open(ocr_path, "w", encoding="utf-8"))
        print(f"OCR fetched and cached: {len(ocr_pages)} pages, "
              f"{sum(len(p.lines) for p in ocr_pages)} lines "
              f"(Rs.{len(sa_pngs)*0.002*94:.1f})")

    pdf = build_evaluated_pdf(
        student_pdf_bytes=sa_bytes, student_filename="student.pdf",
        student_pngs=sa_pngs, report=report, ocr_pages=ocr_pages,
        mark_scale=mark_scale_for(args.exam_type, args.subject),
    )
    out = args.out or os.path.join(args.dir, "ai_rerender.pdf")
    with open(out, "wb") as fh:
        fh.write(pdf)
    print("wrote", out)


if __name__ == "__main__":
    main()
