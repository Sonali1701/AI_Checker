# -*- coding: utf-8 -*-
"""Re-draw an evaluated PDF from a saved report + cached OCR.

Placement bugs are renderer bugs, so verifying a fix must not mean paying to
grade the sheet again. The OCR is fetched once and cached next to the report;
after that every re-render is free.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import fitz

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mathpix
import pw_access
from grader import GradeReport, pdf_or_image_to_pngs
from pdf_renderer import build_evaluated_pdf
from pipeline import mark_scale_for


def reviewed_rotation_map(metadata, page_count, source_bytes, ocr_bytes=None):
    """Validate explicit CCW rotations and any supplied source/cache bindings.

    The JSON may be a page map (``{"1": 90}``) or a bound envelope containing
    ``page_rotations``, ``source_sha256`` and ``ocr_sha256``. Geometry must never
    be guessed from aspect ratio: an upright landscape notebook is legitimate.
    """
    if not isinstance(metadata, dict):
        raise ValueError("Page rotations must be a JSON object")
    wrapped = "page_rotations" in metadata
    rotations = metadata.get("page_rotations") if wrapped else metadata
    if not isinstance(rotations, dict):
        raise ValueError("page_rotations must be a JSON page map")
    if wrapped:
        for name, data in (("source_sha256", source_bytes), ("ocr_sha256", ocr_bytes)):
            if name not in metadata:
                continue
            expected = metadata[name]
            if (not isinstance(expected, str) or len(expected) != 64
                    or any(ch not in "0123456789abcdefABCDEF" for ch in expected)):
                raise ValueError(f"Invalid {name} binding")
            if data is None:
                raise ValueError("An OCR hash binding requires the existing OCR cache")
            if hashlib.sha256(data).hexdigest() != expected.lower():
                raise ValueError(f"Page rotations do not bind this {name.removesuffix('_sha256')}")
    validated = {}
    for key, turn in rotations.items():
        if (not isinstance(key, str) or not key.isdecimal()
                or str(int(key)) != key or not 1 <= int(key) <= page_count):
            raise ValueError("Page rotations contain an invalid original page number")
        if isinstance(turn, bool) or not isinstance(turn, int) or turn not in (0, 90, 180, 270):
            raise ValueError("Page rotations must be explicit CCW 0, 90, 180 or 270 degrees")
        validated[int(key)] = turn
    return validated


def apply_reviewed_rotations(source_bytes, rotations):
    """Rotate an in-memory PDF copy without re-encoding its source photographs.

    PIL/review angles are counterclockwise; PDF page rotation is clockwise.
    Existing PDF display rotations remain part of the source frame. With no
    requested turn the exact input bytes are retained, including metadata.
    """
    with fitz.open(stream=source_bytes, filetype="pdf") as document:
        validated = reviewed_rotation_map(
            {str(page): turn for page, turn in rotations.items()}, len(document), source_bytes)
        if not any(validated.values()):
            return source_bytes
        for page, turn in validated.items():
            if turn:
                original = document[page - 1]
                original.set_rotation((original.rotation - turn) % 360)
        return document.tobytes(garbage=4, deflate=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="run dir holding report.json")
    ap.add_argument("--student", required=True)
    ap.add_argument("--out", default="", help="output pdf (default <dir>/ai_rerender.pdf)")
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--exam-type", default="professional")
    ap.add_argument("--category", default="",
                    help="exam category the copy was graded under (ca, commerce, school, vidyapeeth)")
    ap.add_argument("--subject", default="")
    ap.add_argument("--page-rotations", default="",
                    help="JSON file of explicit reviewed CCW page rotations; optional source_sha256/ocr_sha256 bindings")
    args = ap.parse_args()

    report = GradeReport.model_validate_json(
        Path(args.dir, "report.json").read_text(encoding="utf-8"))
    sa_bytes = Path(args.student).read_bytes()
    ocr_path = os.path.join(args.dir, "ocr.json")
    if args.page_rotations:
        metadata = json.loads(Path(args.page_rotations).read_text(encoding="utf-8"))
        ocr_bytes = Path(ocr_path).read_bytes() if os.path.exists(ocr_path) else None
        with fitz.open(stream=sa_bytes, filetype="pdf") as original:
            rotations = reviewed_rotation_map(metadata, len(original), sa_bytes, ocr_bytes)
        sa_bytes = apply_reviewed_rotations(sa_bytes, rotations)
        print("Explicit reviewed CCW page rotations: "
              + json.dumps({str(page): turn for page, turn in rotations.items()}, sort_keys=True))
    sa_pngs = pdf_or_image_to_pngs(sa_bytes, "student.pdf")

    if os.path.exists(ocr_path):
        ocr_pages = [mathpix.pageocr_from_dict(d)
                     for d in json.loads(Path(ocr_path).read_text(encoding="utf-8"))]
        print(f"OCR from cache: {len(ocr_pages)} pages, "
              f"{sum(len(p.lines) for p in ocr_pages)} lines")
    else:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
        if not token or not pw_access.check_allowed(token):
            sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")
        ocr_pages = mathpix.ocr_all_pages(sa_pngs, google_token=token,
                                          task_id=pw_access.new_task_id("rerender"),
                                          filename="student.pdf")
        Path(ocr_path).write_text(json.dumps([mathpix.pageocr_to_dict(p) for p in ocr_pages]),
                                  encoding="utf-8")
        print(f"OCR fetched and cached: {len(ocr_pages)} pages, "
              f"{sum(len(p.lines) for p in ocr_pages)} lines "
              f"(Rs.{len(sa_pngs)*0.002*94:.1f})")

    pdf = build_evaluated_pdf(
        student_pdf_bytes=sa_bytes, student_filename="student.pdf",
        student_pngs=sa_pngs, report=report, ocr_pages=ocr_pages,
        mark_scale=mark_scale_for(args.exam_type, args.subject, args.category),
    )
    out = args.out or os.path.join(args.dir, "ai_rerender.pdf")
    with open(out, "wb") as fh:
        fh.write(pdf)
    print("wrote", out)


if __name__ == "__main__":
    main()
