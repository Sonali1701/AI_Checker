"""Select upload pages before they enter the existing checker.

An empty selection preserves the original bytes. Non-empty selections use physical
PDF page numbers, starting at 1, and produce deterministic bytes so the paper's
existing marks attestation remains valid between setup and evaluation.
"""
from __future__ import annotations

import os
import re

import fitz


class PdfPageRangeError(ValueError):
    pass


def parse_page_range(page_range: str, page_count: int, label: str = "Document") -> list[int]:
    """Return zero-based pages, in the requested order, without repeated pages."""
    value = str(page_range or "").strip()
    if not value:
        return list(range(page_count))
    if len(value) > 4096:
        raise PdfPageRangeError(f"{label} page range is too long.")
    selected: list[int] = []
    seen: set[int] = set()
    for item in value.split(","):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", item)
        if match is None:
            raise PdfPageRangeError(
                f"{label} page range must use PDF page numbers, for example 1-3,5.")
        start = int(match[1])
        end = int(match[2]) if match[2] else start
        if start < 1 or end < start:
            raise PdfPageRangeError(
                f"{label} page range must start at 1 and use ascending ranges.")
        if end > page_count:
            raise PdfPageRangeError(
                f"{label} page range exceeds this PDF's {page_count} pages.")
        for number in range(start - 1, end):
            if number in seen:
                raise PdfPageRangeError(f"{label} page range repeats page {number + 1}.")
            seen.add(number)
            selected.append(number)
    return selected


def select_pdf_pages(data: bytes, filename: str | None, page_range: str = "",
                     label: str = "Document") -> bytes:
    """Extract an optional page selection without changing unselected uploads."""
    if not str(page_range or "").strip():
        return data
    if os.path.splitext(str(filename or "").lower())[1] != ".pdf":
        raise PdfPageRangeError(f"{label} page ranges are available for PDF files only.")
    try:
        with fitz.open(stream=data, filetype="pdf") as source:
            if not source.is_pdf or source.page_count == 0 or source.needs_pass:
                raise PdfPageRangeError(f"{label} must be a readable PDF to select its pages.")
            pages = parse_page_range(page_range, source.page_count, label)
            with fitz.open() as selected:
                for number in pages:
                    selected.insert_pdf(source, from_page=number, to_page=number)
                # A random PDF ID would bind two identical selections to different
                # hashes when marks are verified and evaluation starts later.
                return selected.tobytes(garbage=4, deflate=True, no_new_id=True)
    except PdfPageRangeError:
        raise
    except Exception as exc:
        raise PdfPageRangeError(
            f"{label} must be a readable PDF to select its pages.") from exc
