# -*- coding: utf-8 -*-
"""Work the pipeline does that buys nothing, and the retry that makes cutting it safe.

MEASURED per copy, 2026-08-27, from the archived run logs (elapsed printed by
tools/eval_one.py) on the CA Inter Taxation booklet:

    ca100_haiku_noaudit   out 3602 tokens   151s   moderation OFF
    ca100_haiku_opusaudit out 3345 tokens   202s   moderation ON
    ca100_haiku_upright   out 7696 tokens   185s
    ca100_haiku_bidir     out10733 tokens   217s
    ca100_sonnet_fixed    out10144 tokens   256s

Two things come out of that. Runtime tracks OUTPUT tokens, and the moderation pass
costs ~50s on top on otherwise comparable output.

A third is not in the timings but is plain in the inputs: the orientation pass
rasterised the 14-page question paper and the 21-page answer key at 150 dpi and sent
all 35 pages to a model, on EVERY copy — to straighten digital PDFs that were never
crooked and that the grader then reads as text, not images.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

import mathpix
import pw_access
from server import _is_digital_pdf

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def made_pdf(pages, text_per_page):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        if text_per_page:
            page.insert_textbox(fitz.Rect(40, 40, 555, 800), text_per_page, fontsize=8)
    out = doc.tobytes()
    doc.close()
    return out


print("== a digital paper does not need a model to tell it which way up it is ==")
digital = made_pdf(3, "The quick brown fox jumps over the lazy dog. " * 60)
scanned = made_pdf(3, "")
check("a PDF with a real text layer is recognised as generated",
      _is_digital_pdf(digital, "qp.pdf"))
check("a page-image PDF is not", not _is_digital_pdf(scanned, "student.pdf"))
check("a photo is not, whatever its size", not _is_digital_pdf(digital, "sheet.jpg"))
check("and neither is an empty or broken file",
      not _is_digital_pdf(b"", "qp.pdf") and not _is_digital_pdf(b"not a pdf", "qp.pdf"))

print("\n== the orienter only pays for the probe where it can help ==")
import server

calls = []


class Cfg:
    google_token = "t"
    task_id = "id"
    provider = "claude"
    model = "claude-haiku-4-5"
    exam_type = "professional"


_real = sys.modules.get("grader_gemini")
import grader_gemini

_orig = grader_gemini.orientation_probe
grader_gemini.orientation_probe = lambda pages, **kw: (calls.append(kw.get("filename")),
                                                       [0] * len(pages))[1]
try:
    orient = server._make_orienter(Cfg())
    orient(digital, "qp.pdf")
    check("no model call for the question paper", calls == [], calls)
    orient(digital, "key.pdf")
    check("nor for the answer key", calls == [], calls)
    orient(scanned, "student.pdf")
    check("but the student's photographed sheet still gets one", calls == ["student.pdf"], calls)
    check("and the bytes come back usable either way",
          isinstance(orient(digital, "qp.pdf"), bytes))
finally:
    grader_gemini.orientation_probe = _orig

print("\n== throttling us for our own concurrency is not a page-losing error ==")
check("429 retries", 429 in pw_access._RETRY_STATUSES)
for code in (500, 502, 503, 504):
    check(f"{code} is not replayed after ambiguous dispatch",
          code not in pw_access._RETRY_STATUSES)
check("a real refusal still fails fast", 403 not in pw_access._RETRY_STATUSES)
check("and so does a bad request", 400 not in pw_access._RETRY_STATUSES)
check("retries back off rather than hammering",
      pw_access._RETRY_ATTEMPTS >= 3 and pw_access._RETRY_BACKOFF_SECONDS >= 1.0,
      f"{pw_access._RETRY_ATTEMPTS} attempts, {pw_access._RETRY_BACKOFF_SECONDS}s base")

print("\n== OCR pages go out several at a time ==")
check("concurrency was raised", mathpix._OCR_CONCURRENCY >= 6, mathpix._OCR_CONCURRENCY)
check("but not so far it invites throttling", mathpix._OCR_CONCURRENCY <= 8,
      mathpix._OCR_CONCURRENCY)
check("an 18-page booklet takes 3 waves, not 5",
      -(-18 // mathpix._OCR_CONCURRENCY) == 3,
      f"{-(-18 // mathpix._OCR_CONCURRENCY)} waves")
check("raising it is only safe because 429 retries",
      429 in pw_access._RETRY_STATUSES,
      "without this, more concurrency means more silently lost pages")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
