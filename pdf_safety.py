"""Learners' PDFs that MuPDF refuses, made usable before a paid check is spent on them.

From the Autopilot of 9 Nov 2026, where each of these failed three attempts and was skipped:
  * blank pages among the written ones: PyMuPDF's show_pdf_page refuses a page with no
    content, so the copy failed at its very last step, after the check had been paid for;
  * pages whose /Parent is the page itself ("cycle in resources");
  * an image whose transparency mask has lost its data ("object is not a stream");
  * a file whose object numbers break copying a page ("source object number out of range");
  * a page 84 x 112 inches, 211 million pixels at 150 dpi.

A healthy file is never rewritten: repair() runs only after a page has been refused.
"""
from __future__ import annotations

import re

import fitz

# 150 dpi on A4 is 2 million pixels, so this only ever touches a page drawn very large.
# PIL refuses 179 million pixels outright.
MAX_PAGE_PIXELS = 40_000_000
_XREF = re.compile(r"(\d+) 0 R")


def capped_dpi(page, dpi):
    """The resolution to draw this page at: as asked, unless the image would be enormous."""
    pixels = page.rect.width * page.rect.height * (dpi / 72) ** 2
    return dpi if pixels <= MAX_PAGE_PIXELS else max(36, int(dpi * (MAX_PAGE_PIXELS / pixels) ** .5))


def show_source_page(target, rect, source, number, **options):
    """show_pdf_page, leaving the area white when the source page has nothing on it."""
    if not source[number].get_contents():
        return False
    target.show_pdf_page(rect, source, number, **options)
    return True


def every_page_copies(data):
    """True when each page of this PDF can be copied onto another page, as the checked
    copy is built. Tried before the paid check, not found out after it."""
    scratch = fitz.open()
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            for number in range(document.page_count):
                page = scratch.new_page()
                show_source_page(page, page.rect, document, number)
    except Exception:  # noqa: BLE001 - any refusal means the same thing here
        return False
    finally:
        scratch.close()
    return True


def _pages_node(document):
    found = _XREF.fullmatch(document.xref_get_key(document.pdf_catalog(), "Pages")[1])
    return int(found.group(1)) if found else 0


def _fix_page_parent(document, xref, root):
    kind, value = document.xref_get_key(xref, "Parent")
    if kind == "xref" and root:
        parent = int(_XREF.fullmatch(value).group(1))
        if parent == xref or document.xref_get_key(parent, "Type")[1] != "/Pages":
            document.xref_set_key(xref, "Parent", f"{root} 0 R")


def _fix_image_masks(document):
    """An image whose mask is not a stream stops its whole page. The image stays, unmasked."""
    for xref in range(1, document.xref_length()):
        if document.xref_get_key(xref, "Subtype")[1] != "/Image":
            continue
        for key in ("SMask", "Mask"):
            kind, value = document.xref_get_key(xref, key)
            if kind == "xref" and not document.xref_is_stream(int(_XREF.fullmatch(value).group(1))):
                document.xref_set_key(xref, key, "null")


def repair(data):
    """The PDF with the damage above mended, the learner's own content kept. Saving also
    renumbers the objects. A file this cannot mend comes back as it would be read anyway,
    and the caller's next attempt fails as before: a copy is never checked with a page
    silently missing."""
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            return data
        root = _pages_node(document)
        for number in range(document.page_count):
            _fix_page_parent(document, document.page_xref(number), root)
        _fix_image_masks(document)
        return document.tobytes(garbage=3)
