# -*- coding: utf-8 -*-
"""Typed submissions (School of Innovation assignments): PDFs made of text pages.

A handwritten answer sheet is photographed and read by Mathpix. A typed assignment
carries its own exact text, so its text pages are read from the PDF text layer into
the same PageOCR shape (lines with pixel boxes, words, printed=True): nothing is
paid for OCR, code is read character for character, and every mark still has a
line to anchor to. A page that is a picture (a screenshot of code, a scanned page)
has no text layer and is read by Mathpix as before; a rotated page likewise.

Also here: the typed-page test the photo gate shares (is_text_layer_page), the
reason a PDF that holds only a name and a GitHub or Drive link goes to a reviewer
instead of being graded as blank, and the page limit for a category that takes
typed documents (a spreadsheet export of hundreds of pages is not an answer
document).

Imports nothing from pipeline, grader or server.
"""
from __future__ import annotations

import io
import re

from mathpix import LineBox, PageOCR, WordBox

# A page whose own text layer carries at least this much text, and whose images cover
# less than half of it, is a typed or printed page: its images are a logo or a stamp.
TEXT_PAGE_CHARS = 200
TEXT_PAGE_IMAGE_SHARE = 0.5
# Reading a typed submission, a page that carries pictures is read from the text
# layer only when it also has this much visible text outside them; with less it is
# a page of screenshots and Mathpix reads it. (A cover page with a name and a link
# has about 120 characters.)
READ_PAGE_CHARS = 20
# A submission whose text, links removed, is shorter than this and that names a
# link holds the learner's details and the link, not answers.
LINK_ONLY_MAX_CHARS = 600

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_LINK_LABEL_RE = re.compile(
    r"\b(?:github|git|drive|google\s*drive|colab|kaggle|notebook|repo(?:sitory)?|"
    r"project|excel|sheet|assignment|code|file)\s*link\b|\blink\s*[:\-]|\bgithub\.com\b|"
    r"\bdrive\.google\.com\b|\bcolab\.research\b|\bdocs\.google\.com\b",
    re.IGNORECASE)


def _pictures(page) -> list:
    """The page's pictures of any size that matters, clipped to the page."""
    import fitz
    out = []
    for info in page.get_image_info():
        if max(int(info.get("width") or 0), int(info.get("height") or 0)) < 32:
            continue                         # 2 px decoration tiles on a typed page
        box = fitz.Rect(info["bbox"]) & page.rect
        if not box.is_empty:
            out.append(box)
    return out


def _visible_chars(page, pictures) -> int:
    """Characters of VISIBLE text not lying on a picture: a scanner app's invisible
    OCR layer over a photo is not print."""
    import fitz
    visible = 0
    for span in page.get_texttrace():
        if span.get("type") == 3 or float(span.get("opacity", 1.0) or 0.0) <= 0.0:
            continue
        box = fitz.Rect(span["bbox"])
        centre = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        if any(centre in picture for picture in pictures):
            continue
        visible += sum(1 for char in span.get("chars") or () if chr(char[0]).strip())
    return visible


def is_text_layer_page(page, min_chars: int = TEXT_PAGE_CHARS) -> bool:
    """A printed page (circular, syllabus, receipt, typed assignment), not a
    photographed answer sheet.

    MEASURED 2026-10-01 (Radiant 2.0 Class 9 batch): Krishna's 8-page school
    syllabus (789-1554 characters of text a page, one 176 px logo), Ritik's syllabus
    (136 px icons), Samriddhi's CISCE circular (640 px emblem) and Aachal's project
    guidelines (225 px logo) were sent to FACULTY REVIEW as "PHOTO_TOO_SMALL", while
    the same kind of upload without a logo was correctly REJECTED as a printed
    document. A logo on a typed page is not a low-resolution photo of handwriting.
    A photographed page is never mistaken for one: its photo covers the page, and a
    scanner's OCR layer over it is invisible text.
    """
    try:
        area = max(1.0, float(page.rect.width * page.rect.height))
        pictures = _pictures(page)
        if sum(box.get_area() for box in pictures) / area >= TEXT_PAGE_IMAGE_SHARE:
            return False
        return _visible_chars(page, pictures) >= min_chars
    except Exception:  # noqa: BLE001 - unknown means "treat it as a photo page"
        return False


def _png_size(png: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as img:
        return img.size


# A picture on a typed page this large is part of the answer (a chart, a screenshot
# of code or output, a table pasted as an image); smaller ones are logos and icons
# (the PW Skills logo on the template's first page covers about 1 percent).
FIGURE_MIN_SHARE = 0.03
# Line kinds that stand for a picture or a table (the OCR's own and this module's).
# The OCR's synthetic gap regions are not among them: a gap is not evidence of work.
VISUAL_KINDS = frozenset({"diagram", "chart", "table"})
FIGURE_TEXT = ("[detected figure: a picture in the answer, such as a chart, a screenshot "
               "of code or output, or a table image. Read it from the page image]")


def _tables(page) -> list:
    """Ruled tables of at least two rows and two columns. A one-cell box is the
    template's answer box, not a table."""
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 - no table reading: the lines are read as text
        return []
    return [table for table in getattr(found, "tables", []) or []
            if int(table.row_count or 0) >= 2 and int(table.col_count or 0) >= 2]


def _cell_text(value) -> str:
    return " ".join(str(value or "").split())


def _page_lines(page, number: int, sx: float, sy: float, pictures=()) -> list[LineBox]:
    """The page's lines in reading order, boxed in the rendered image's pixels.

    - Text lines, with the target of a link annotation written after the words that
      carry it ("Github Link : CODE (https://github.com/...)").
    - A ruled table as one line per row, its cells joined by " | ", under a
      "[detected table]" anchor. Read line by line, its cells came apart and a
      solution given as a table was not recognised (PW Skills team, 21 Oct 2026).
    - A picture large enough to be part of the answer (a chart, a screenshot) as a
      "[detected figure]" anchor, so the grader reads it from the page image and has
      a line to put its marks on. Without one, a chart left no trace in the
      transcript and was marked as missing unless the learner annotated it.
    Figure and table anchors are P<n>D<k> lines, like the OCR's own figure lines.
    """
    import fitz
    links = [(fitz.Rect(link["from"]), str(link.get("uri") or "").strip())
             for link in page.get_links() if link.get("uri")]

    def scaled(box) -> tuple:
        return (box.x0 * sx, box.y0 * sy, box.x1 * sx, box.y1 * sy)

    tables = _tables(page)
    table_boxes = [fitz.Rect(table.bbox) for table in tables]
    area = max(1.0, float(page.rect.width * page.rect.height))
    figures = [box for box in pictures
               if box.get_area() / area >= FIGURE_MIN_SHARE
               and not any((box & table).get_area() > 0.5 * box.get_area()
                           for table in table_boxes)]
    # (top, left, kind, text, box): text lines, table rows and figure anchors.
    items: list[tuple] = []
    # Ligatures are spelled out ("fi", not the single glyph "ﬁ"): the grader quotes
    # the text, and a quote is matched against these lines letter for letter.
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_LIGATURES
    for block in page.get_text("dict", sort=True, flags=flags).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(str(span.get("text") or "") for span in line.get("spans", []))
            text = " ".join(text.split())
            if not text:
                continue
            box = fitz.Rect(line["bbox"])
            centre = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
            if any(centre in table for table in table_boxes):
                continue                     # read below, a row at a time
            for where, uri in links:
                if uri not in text and not (where & box).is_empty:
                    text += f" ({uri})"
            items.append((box.y0, box.x0, "text", text, box))
    for table, box in zip(tables, table_boxes):
        rows = list(table.extract() or [])
        items.append((box.y0, box.x0 - 1, "table",
                      f"[detected table: {len(rows)} rows x {int(table.col_count)} columns]", box))
        for index, (cells, row) in enumerate(zip(rows, table.rows)):
            text = " | ".join(_cell_text(cell) for cell in cells)
            if text.strip(" |"):
                row_box = fitz.Rect(row.bbox)
                items.append((row_box.y0 + 0.01 * (index + 1), box.x0, "text", text, row_box))
    for box in figures:
        items.append((box.y0, box.x0, "diagram", FIGURE_TEXT, box))
    items.sort(key=lambda item: (round(item[0], 1), item[1]))
    lines: list[LineBox] = []
    visual = 0
    for _, _, kind, text, box in items:
        if kind == "text":
            line_id = f"P{number}L{sum(1 for line in lines if line.kind == 'text') + 1}"
        else:
            visual += 1
            line_id = f"P{number}D{visual}"
        lines.append(LineBox(line_id=line_id, text=text, bbox=scaled(box), page=number,
                             confidence=1.0, kind=kind, printed=True))
    return lines


def text_layer_pages(data: bytes, filename: str, pngs: list[bytes]) -> dict[int, PageOCR]:
    """OCR-shaped readings of a typed PDF's text pages, keyed by page number (1-based).

    Only pages that are text (visible text outside pictures, pictures covering less
    than half the page, no rotation) are returned; a page of screenshots, a scanned
    page or an image upload is left for Mathpix. A page with no text and no pictures
    is returned empty (nothing to read, nothing to pay for). `pngs` are the pages as
    rendered for grading: the boxes are scaled to those pixels.
    """
    if not str(filename or "").lower().endswith(".pdf") or not data:
        return {}
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001 - not a PDF the reader can open: Mathpix decides
        return {}
    out: dict[int, PageOCR] = {}
    try:
        for number, page in enumerate(doc, start=1):
            if number > len(pngs):
                break
            try:
                if int(page.rotation or 0) % 360:
                    continue
                pictures = _pictures(page)
                area = max(1.0, float(page.rect.width * page.rect.height))
                if sum(box.get_area() for box in pictures) / area >= TEXT_PAGE_IMAGE_SHARE:
                    continue
                visible = _visible_chars(page, pictures)
                # A page with pictures and almost no text is a page of screenshots
                # (with a caption at most): Mathpix reads it. A page without pictures
                # has nothing else to read, however short its text.
                if pictures and visible < READ_PAGE_CHARS:
                    continue
                width, height = _png_size(pngs[number - 1])
                sx = width / max(1.0, float(page.rect.width))
                sy = height / max(1.0, float(page.rect.height))
                lines = _page_lines(page, number, sx, sy, pictures) if visible else []
                word_flags = fitz.TEXTFLAGS_WORDS & ~fitz.TEXT_PRESERVE_LIGATURES
                words = ([WordBox(text=str(w[4]), confidence=1.0,
                                  bbox=(w[0] * sx, w[1] * sy, w[2] * sx, w[3] * sy))
                          for w in page.get_text("words", flags=word_flags)]
                         if visible else [])
                out[number] = PageOCR(page=number, image_width=width, image_height=height,
                                      lines=lines, words=words)
            except Exception as exc:  # noqa: BLE001 - this page goes to Mathpix
                print(f"[typed] page {number} of {filename}: text layer not read ({exc})",
                      flush=True)
    finally:
        doc.close()
    return out


def submission_links(ocr_pages) -> list[str]:
    """The links a submission names, as written (URLs first, then labelled lines)."""
    urls: list[str] = []
    labelled: list[str] = []
    for page in list(ocr_pages or []):
        for line in list(getattr(page, "lines", None) or []):
            text = " ".join(str(getattr(line, "text", "") or "").split())
            found = [u.rstrip(".,;") for u in _URL_RE.findall(text)]
            if found:
                urls.extend(u for u in found if u not in urls)
            elif _LINK_LABEL_RE.search(text) and text not in labelled:
                labelled.append(text)
    return urls + labelled


def link_only_reason(ocr_pages) -> str:
    """Why a submission that holds only the learner's details and a link to the work
    goes to a reviewer, or "" when the document carries answers of its own.

    Five of the twelve sample submissions (13 Oct 2026) were one page: a name, an
    email and "Github Link : CODE" or a Drive link. The tool marks what is in the
    document; a reviewer opens the link.
    """
    links = submission_links(ocr_pages)
    if not links:
        return ""
    lines = [line for page in list(ocr_pages or [])
             for line in list(getattr(page, "lines", None) or [])]
    # A chart, a screenshot or a table in the document is work to mark, link or no link.
    if any(str(getattr(line, "kind", "text") or "text") in VISUAL_KINDS for line in lines):
        return ""
    text = " ".join(" ".join(str(getattr(line, "text", "") or "").split())
                    for line in lines
                    if str(getattr(line, "kind", "text") or "text") in ("text", "math"))
    rest = _URL_RE.sub(" ", text)
    rest = re.sub(r"\(\s*\)", " ", rest)
    if len(" ".join(rest.split())) > LINK_ONLY_MAX_CHARS:
        return ""
    shown = "; ".join(f"'{link[:80]}'" for link in links[:2])
    return (f"This submission holds the learner's details and a link to the work "
            f"({shown}), not the answers themselves. The tool marks what is in the "
            "document. Open the link and mark the work by hand. No marks were given.")


def pdf_page_count(data: bytes, filename: str) -> int:
    """Pages in the upload (1 for an image), 0 when it cannot be opened."""
    if not str(filename or "").lower().endswith(".pdf"):
        return 1 if data else 0
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            return int(doc.page_count)
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return 0


def too_many_pages_reason(data: bytes, filename: str, limit: int) -> str:
    """Why an upload longer than the category's page limit goes to a reviewer unread.

    Two of the sample DA submissions (13 Oct 2026) were the learner's spreadsheet
    printed to PDF: 160 and 480 pages of data rows, where the formulas and the
    formatting the questions ask for cannot be seen."""
    if not limit:
        return ""
    count = pdf_page_count(data, filename)
    if count <= int(limit):
        return ""
    return (f"This upload has {count} pages, more than the {int(limit)} a typed "
            "assignment runs to. It looks like a data or spreadsheet export rather "
            "than the written answers, so it was not read. A reviewer should open "
            "the learner's file and mark the work by hand.")
