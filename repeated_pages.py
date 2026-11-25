# -*- coding: utf-8 -*-
"""The same page photographed twice.

CA faculty review (18 Oct 2026), Business Laws 23a44745: pages 1 and 2 are two photos
of one page (Q1(a)). Both were marked, and the faculty asked for the second to be
"marked as repeated". Two photos of one page read back as nearly the same words in
the same order; two different pages, even of one answer, share only a few. Only
handwritten text counts: a diary's printed header and a scanner app's watermark sit
on every page. A pair is called a repeat only when both pages carry enough writing
and their words agree closely, so a doubtful pair is left alone (fails open).

Whole-page photo matching was tried first and could not tell pages apart: on
Business Laws f87b56cc every photo shows the same laptop and floor, and different
pages matched as well as the real repeat did.
"""
from __future__ import annotations

import difflib
import re

MIN_WORDS = 30        # words of writing a page needs before it is compared
MIN_RATIO = 0.6       # word-sequence agreement that makes two pages one page
_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(texts) -> list[str]:
    return [w for text in texts for w in _WORD_RE.findall(str(text or "").lower()) if len(w) > 2]


def repeated_pages(pages: dict[int, list[str]]) -> dict[int, int]:
    """{page: the earlier page it repeats}, from each page's handwritten line texts."""
    words = {number: _words(texts) for number, texts in pages.items()}
    order = sorted(words)
    out: dict[int, int] = {}
    for j, later in enumerate(order):
        b = words[later]
        if len(b) < MIN_WORDS:
            continue
        for earlier in order[:j]:
            a = words[earlier]
            if earlier in out or len(a) < MIN_WORDS or min(len(a), len(b)) < 0.6 * max(len(a), len(b)):
                continue
            if difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= MIN_RATIO:
                out[later] = earlier
                break
    return out


def ocr_repeated_pages(ocr_pages) -> dict[int, int]:
    """repeated_pages over Mathpix pages, handwritten text lines only."""
    return repeated_pages({
        int(page.page): [line.text for line in page.lines
                         if (getattr(line, "kind", "text") or "text") == "text"
                         and not getattr(line, "printed", False) and not getattr(line, "cancelled", False)]
        for page in ocr_pages or []})


def line_pages(ocr_pages) -> dict[str, int]:
    return {line.line_id: int(page.page) for page in ocr_pages or [] for line in page.lines}


def prompt_block(repeats: dict[int, int]) -> str:
    """What the grader is told. Empty when no page repeats another."""
    if not repeats:
        return ""
    pairs = ", ".join(f"page {later} repeats page {earlier}" for later, earlier in sorted(repeats.items()))
    return ("=== REPEATED PAGES ===\n"
            f"The same writing was photographed twice: {pairs}. Mark that writing once, on the "
            "earlier page's lines. Never cite a line on the repeated page and never pay its "
            "points a second time. The repeated page is noted on the copy as repeated.")


def paid_twice(report, ocr_pages) -> dict[str, str]:
    """{qid: why} for a question credited on a repeated page AND on the page it repeats."""
    from exam_package import normalize_qid
    repeats = ocr_repeated_pages(ocr_pages)
    if not repeats:
        return {}
    page_of = line_pages(ocr_pages)
    out: dict[str, str] = {}
    for q in list(getattr(report, "questions", None) or []):
        if not bool(getattr(q, "attempted", True)):
            continue
        pages = {page_of.get(str(getattr(c, "step_line_id", "") or ""))
                 for c in (getattr(q, "criteria", None) or []) if float(getattr(c, "awarded", 0) or 0) > 0}
        for later, earlier in repeats.items():
            if later in pages and earlier in pages:
                out[normalize_qid(str(q.qid))] = (
                    f"{q.qid} is paid on page {later}, which repeats page {earlier} (the same writing "
                    f"photographed twice). Mark it once, on page {earlier}'s lines only.")
                break
    return out
