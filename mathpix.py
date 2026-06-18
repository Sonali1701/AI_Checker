"""Mathpix OCR client — returns per-page text with line bounding boxes for precise mark placement."""
from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass, field

import requests
from PIL import Image, ImageDraw, ImageFont

MATHPIX_TEXT_ENDPOINT = "https://api.mathpix.com/v3/text"


_HEADER_PREFIXES = ("q.", "q ", "q)")
_SECTION_PREFIX = "section"
_LONE_LABELS = {f"({r})" for r in (
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "a", "b", "c", "d", "e", "f",
)}


def is_header_line(text: str) -> bool:
    """True if the line is a question/section header, not actual student working."""
    s = text.strip().lower()
    if not s:
        return True
    if s.startswith(_HEADER_PREFIXES):
        return True
    if s.startswith(_SECTION_PREFIX) and len(s) <= 14:
        return True
    if s in _LONE_LABELS:
        return True
    if len(s) <= 5 and s.startswith("(") and s.endswith(")"):
        return True
    return False


@dataclass
class LineBox:
    line_id: str           # e.g. "P1L3" — stable ID we generate
    text: str              # OCR'd line text (may contain inline LaTeX)
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in IMAGE PIXEL coordinates
    page: int              # 1-indexed page on student answer sheet


@dataclass
class PageOCR:
    page: int                          # 1-indexed
    image_width: int                   # pixels of the PNG we sent to Mathpix
    image_height: int
    lines: list[LineBox] = field(default_factory=list)

    def transcript(self) -> str:
        """Format as a labelled transcript Claude can reference by [line_id]."""
        return "\n".join(f"[{l.line_id}] {l.text}" for l in self.lines)


def pageocr_to_dict(p: "PageOCR") -> dict:
    """JSON-safe dict for sending OCR results over the wire (proxy -> client), so the
    client can place mark anchors when it builds the evaluated PDF locally."""
    return {
        "page": p.page,
        "image_width": p.image_width,
        "image_height": p.image_height,
        "lines": [
            {"line_id": l.line_id, "text": l.text, "bbox": list(l.bbox), "page": l.page}
            for l in p.lines
        ],
    }


def pageocr_from_dict(d: dict) -> "PageOCR":
    """Inverse of pageocr_to_dict — rebuild a PageOCR on the client side."""
    return PageOCR(
        page=int(d["page"]),
        image_width=int(d["image_width"]),
        image_height=int(d["image_height"]),
        lines=[
            LineBox(line_id=l["line_id"], text=l["text"],
                    bbox=tuple(l["bbox"]), page=int(l["page"]))
            for l in d.get("lines", [])
        ],
    )


def _headers() -> dict[str, str]:
    key = os.environ.get("MATHPIX_APP_KEY")
    if not key:
        raise RuntimeError("Set MATHPIX_APP_KEY env var (and optionally MATHPIX_APP_ID).")
    h = {"app_key": key, "Content-Type": "application/json"}
    app_id = os.environ.get("MATHPIX_APP_ID")
    if app_id:
        h["app_id"] = app_id
    return h


def _bbox_from_cnt(cnt: list[list[float]]) -> tuple[float, float, float, float]:
    """Convert a Mathpix `cnt` polygon (list of [x, y] points) to (x0, y0, x1, y1)."""
    xs = [p[0] for p in cnt]
    ys = [p[1] for p in cnt]
    return (min(xs), min(ys), max(xs), max(ys))


# Mathpix application errors that mean "this page has no usable content" rather
# than "your setup is broken". These should degrade to an empty page, not abort
# the whole evaluation — a cover/form page or a faint scan legitimately OCRs to
# nothing, and grading + heuristic mark placement still work without it.
_SOFT_MATHPIX_ERROR_IDS = {
    "content_not_found", "image_no_content", "no_content",
    "image_decode_error", "image_download_error", "math_confidence",
}


def _is_auth_error(data: dict) -> bool:
    """True if a Mathpix error body indicates a credential/config problem we must surface."""
    info = data.get("error_info") or {}
    blob = f"{data.get('error', '')} {info.get('id', '')} {info.get('message', '')}".lower()
    return any(k in blob for k in ("unauthorized", "invalid", "credential", "app_key", "app_id", "forbidden"))


def ocr_page(png_bytes: bytes, page_num: int) -> PageOCR:
    """OCR a single page image. Returns text lines with bounding boxes in pixel coords.

    If Mathpix reports a content-level error (e.g. "Content not found" on a
    sparse cover/form page), this returns an *empty* PageOCR for that page so the
    rest of the submission still gets processed. Auth/credential errors are
    re-raised so a misconfigured key isn't silently swallowed.
    """
    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    payload = {
        "src": "data:image/png;base64," + base64.standard_b64encode(png_bytes).decode("utf-8"),
        "formats": ["text"],
        "include_line_data": True,
        # leave math_inline_delimiters / math_display_delimiters at defaults
    }
    resp = requests.post(MATHPIX_TEXT_ENDPOINT, json=payload, headers=_headers(), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        info = data.get("error_info") or {}
        err_id = (info.get("id") or "").lower()
        if _is_auth_error(data):
            raise RuntimeError(f"Mathpix error on page {page_num}: {data['error']}")
        if err_id in _SOFT_MATHPIX_ERROR_IDS or not err_id:
            # No usable content on this page — degrade gracefully.
            return PageOCR(page=page_num, image_width=width, image_height=height, lines=[])
        raise RuntimeError(f"Mathpix error on page {page_num}: {data['error']}")

    lines: list[LineBox] = []
    for idx, ld in enumerate(data.get("line_data", []), start=1):
        if ld.get("type") not in ("text", "math"):
            continue
        text = (ld.get("text") or "").strip()
        if not text:
            continue
        cnt = ld.get("cnt")
        if not cnt:
            continue
        bbox = _bbox_from_cnt(cnt)
        lines.append(LineBox(
            line_id=f"P{page_num}L{idx}",
            text=text,
            bbox=bbox,
            page=page_num,
        ))

    return PageOCR(page=page_num, image_width=width, image_height=height, lines=lines)


def ocr_all_pages(png_pages: list[bytes]) -> list[PageOCR]:
    return [ocr_page(png, i) for i, png in enumerate(png_pages, start=1)]


def build_transcript(pages: list[PageOCR]) -> str:
    """Build a combined OCR transcript for the full student submission, with page headers.

    Lines that are question/section headers are tagged `(HEADER)` so the model
    knows not to anchor step ticks there. Diagram regions are tagged
    `(DIAGRAM REGION)`.
    """
    parts: list[str] = []
    for p in pages:
        parts.append(f"=== Page {p.page} ===")
        for ln in p.lines:
            tag = ""
            if "D" in ln.line_id and "L" not in ln.line_id:
                tag = " (DIAGRAM REGION — use this anchor for steps whose evidence is a drawing/figure/table)"
            elif is_header_line(ln.text):
                tag = " (HEADER — never use as step_line_id)"
            parts.append(f"[{ln.line_id}] {ln.text}{tag}")
    return "\n".join(parts)


def synthesize_diagram_regions(page: PageOCR, min_gap_px: int = 180) -> PageOCR:
    """Add synthetic diagram-region anchors for big vertical gaps between OCR
    lines. OCR can't see hand-drawn diagrams, but a wide blank band between
    text lines is almost always a figure — give it an anchor like 'P7D1' so
    the grader can target it.
    """
    text_lines = [l for l in page.lines if not l.line_id.startswith(f"P{page.page}D")]
    if not text_lines:
        return page
    sorted_lines = sorted(text_lines, key=lambda l: l.bbox[1])
    W = page.image_width
    prev_bottom = sorted_lines[0].bbox[3]
    diag_idx = 1
    margin_x = max(40, W // 30)
    new_lines: list[LineBox] = []
    for line in sorted_lines[1:]:
        y0 = line.bbox[1]
        if y0 - prev_bottom >= min_gap_px:
            new_lines.append(LineBox(
                line_id=f"P{page.page}D{diag_idx}",
                text="[diagram region — likely a figure/diagram/table the OCR could not transcribe]",
                bbox=(margin_x, prev_bottom + 8, W - margin_x, y0 - 8),
                page=page.page,
            ))
            diag_idx += 1
        prev_bottom = max(prev_bottom, line.bbox[3])
    page.lines.extend(new_lines)
    page.lines.sort(key=lambda l: l.bbox[1])
    return page


def overlay_anchors_on_png(png_bytes: bytes, page_ocr: PageOCR) -> bytes:
    """Draw each line_id label directly on the page image, so the model can
    map visual position to line_id at a glance instead of having to infer it
    from the textual transcript alone. This is the biggest accuracy win for
    step-mark placement.

    - Text lines: small blue label `[PnLk]` in the right margin of each line.
    - Diagram regions: light-orange outlined box + label so the model can pick
      a diagram anchor for figure/table-based steps.
    - Headers: red `[PnLk]` label so the model never anchors a step there.
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    W, H = img.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(16, W // 90)
    font = None
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    def label_size(t):
        b = draw.textbbox((0, 0), t, font=font)
        return b[2] - b[0], b[3] - b[1]

    for ln in page_ocr.lines:
        x0, y0, x1, y1 = ln.bbox
        is_diagram = "D" in ln.line_id and "L" not in ln.line_id
        is_header = (not is_diagram) and is_header_line(ln.text)

        text = ln.line_id
        tw, th = label_size(text)
        # Default placement: just past the right end of the line
        lx = x1 + 10
        ly = (y0 + y1) / 2 - th / 2
        # Wrap to left margin if it would fall off the page
        if lx + tw + 6 > W - 2:
            lx = max(4, x0 - tw - 12)

        if is_diagram:
            bg = (255, 235, 200, 230)
            fg = (200, 90, 0)
            # outline the diagram region itself
            draw.rectangle([x0, y0, x1, y1], outline=fg + (200,), width=2)
            # Place the label inside the top-left of the region
            lx = x0 + 6
            ly = y0 + 6
        elif is_header:
            bg = (255, 220, 220, 220)
            fg = (180, 30, 30)
        else:
            bg = (220, 235, 255, 215)
            fg = (10, 50, 160)

        draw.rectangle(
            [lx - 3, ly - 2, lx + tw + 4, ly + th + 3],
            fill=bg,
            outline=fg + (220,),
            width=1,
        )
        draw.text((lx, ly), text, fill=fg, font=font)

    combined = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    combined.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
