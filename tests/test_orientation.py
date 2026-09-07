# -*- coding: utf-8 -*-
"""Straightening sideways pages must not wreck the booklet.

Students photograph pages in landscape constantly, and a rotated page returns no
OCR lines at all, so orientation correction is load-bearing. But the rebuild used
to re-encode each rotated page as PNG and hand it to PyMuPDF, which stored the
stream uncompressed: a 4 MB, 18-page CA booklet with four sideways pages came back
at 159 MB. That payload broke grading outright — every subjective question came
back 0 "Incorrect answer" because the model never saw those pages.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz
from PIL import Image

from orient import _REBUILD_MAX_EDGE, normalize_orientation

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def photo_booklet(n_pages: int = 6) -> bytes:
    """A booklet of big photo-like pages, the shape a phone actually produces."""
    doc = fitz.open()
    for p in range(n_pages):
        # Noise compresses badly, which is exactly what makes a real photo expensive.
        img = Image.effect_noise((1400, 1000), 48).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        page = doc.new_page(width=2000, height=1500)
        page.insert_image(page.rect, stream=buf.getvalue())
    return doc.tobytes(deflate=True, garbage=3)


src = photo_booklet()
print(f"== rebuilding rotated pages stays proportionate ({len(src)/1e6:.1f} MB in) ==")
rots = [0, 90, 0, 270, 0, 180]
out, applied, changed = normalize_orientation(src, "student.pdf", rotations=rots)
print(f"  rotated {sum(1 for r in rots if r)} of {len(rots)} pages -> {len(out)/1e6:.1f} MB")
check("the booklet was actually rebuilt", changed)
check("rotations are applied as given", applied == rots, f"{applied}")
check("output stays within 4x the input", len(out) <= len(src) * 4,
      f"{len(src)/1e6:.1f} MB -> {len(out)/1e6:.1f} MB")

print("== rotated pages are upright and unrotated pages are untouched ==")
doc = fitz.open(stream=out, filetype="pdf")
check("page count preserved", doc.page_count == len(rots), f"{doc.page_count}")
# A 90/270 rotation swaps the page's aspect; 0 and 180 keep it.
src_doc = fitz.open(stream=src, filetype="pdf")
for i, r in enumerate(rots):
    was_landscape = src_doc[i].rect.width > src_doc[i].rect.height
    is_landscape = doc[i].rect.width > doc[i].rect.height
    expected = (not was_landscape) if r in (90, 270) else was_landscape
    check(f"page {i+1} (rot {r}) has the right aspect", is_landscape == expected,
          f"{doc[i].rect}")

print("== rebuilt pages embed a capped, JPEG-compressed photo ==")
for i, r in enumerate(rots):
    if not r:
        continue
    imgs = doc[i].get_images(full=True)
    check(f"page {i+1} embeds exactly one image", len(imgs) == 1, f"{len(imgs)}")
    if not imgs:
        continue
    info = doc.extract_image(imgs[0][0])
    check(f"page {i+1} long edge is capped",
          max(info["width"], info["height"]) <= _REBUILD_MAX_EDGE,
          f'{info["width"]}x{info["height"]} (cap {_REBUILD_MAX_EDGE})')
    check(f"page {i+1} is stored as JPEG, not raw", info["ext"] in ("jpeg", "jpg"),
          f'stored as {info["ext"]}')

print("== an all-upright booklet is returned byte-identical ==")
same, rots0, changed0 = normalize_orientation(src, "student.pdf", rotations=[0] * len(rots))
check("nothing rebuilt when nothing is rotated", not changed0)
check("original bytes returned verbatim", same is src or same == src)


# A school student photographs the OPEN notebook, so one image holds two facing pages.
# The writing is upright, so the rotation pass waves it through — and OCR then orders
# lines by their y across the whole image, interleaving the left page with the right
# and handing the grader a scrambled answer. MEASURED on a Class 12 Chemistry sheet:
# two of its five images were spreads and 30 of 46 questions came back "not attempted"
# on a copy where the student had plainly answered Q19 through Q21.
print("== a photographed two-page spread is split into single pages ==")
import io as _sio

import fitz as _sfitz
from PIL import Image as _SImage, ImageDraw as _SDraw

from orient import _SPREAD_MIN_ASPECT, split_two_page_spreads


def _spread_pdf(width=2000, height=1400, gutter=1000):
    img = _SImage.new("RGB", (width, height), (250, 249, 245))
    d = _SDraw.Draw(img)
    # Writing runs almost the full width of each page, as it does in a real notebook,
    # leaving only a narrow margin either side of the fold.
    for x0, x1 in ((60, gutter - 60), (gutter + 60, width - 60)):
        for row in range(9):
            y = 160 + row * 120
            d.rectangle([x0, y, x1, y + 26], outline=(30, 30, 90))
            d.text((x0 + 10, y), "the student wrote a line here", fill=(30, 30, 90))
    d.rectangle([gutter - 6, 0, gutter + 6, height], fill=(90, 90, 95))   # the fold
    buf = _sio.BytesIO()
    img.save(buf, "JPEG", quality=88)
    doc = _sfitz.open()
    pg = doc.new_page(width=width, height=height)
    pg.insert_image(_sfitz.Rect(0, 0, width, height), stream=buf.getvalue())
    return doc.tobytes()


_out, _changed = split_two_page_spreads(_spread_pdf(), "student.pdf")
check("the spread is recognised", _changed)
_sd = _sfitz.open(stream=_out, filetype="pdf")
check("it becomes two pages", _sd.page_count == 2, f"{_sd.page_count} pages")
check("and both are portrait, like a notebook page",
      all(_sd[i].rect.width < _sd[i].rect.height for i in range(_sd.page_count)),
      [(round(_sd[i].rect.width), round(_sd[i].rect.height)) for i in range(_sd.page_count)])
check("the halves are about equal",
      abs(_sd[0].rect.width - _sd[1].rect.width) < _sd[0].rect.width * 0.25,
      f"{_sd[0].rect.width:.0f} vs {_sd[1].rect.width:.0f}")

# An ordinary upright page must be handed back untouched — byte for byte, so a normal
# upload pays nothing for a check that does not apply to it.
_plain = _sfitz.open()
_plain.new_page(width=600, height=850)
_plain_bytes = _plain.tobytes()
_same, _ch2 = split_two_page_spreads(_plain_bytes, "student.pdf")
check("a portrait page is left alone", not _ch2 and _same == _plain_bytes)
check("the aspect gate is above 1.0 so a square page is not split",
      _SPREAD_MIN_ASPECT > 1.0, _SPREAD_MIN_ASPECT)
check("a non-PDF upload is left alone",
      split_two_page_spreads(b"not a pdf", "photo.jpg") == (b"not a pdf", False))
check("corrupt bytes never lose the copy",
      split_two_page_spreads(b"%PDF-broken", "student.pdf") == (b"%PDF-broken", False))


print()
print("== one booklet is photographed one way, so its pages rotate one way ==")
# MEASURED on a real Class 10 Physics booklet (3 pages, all photographed sideways):
# the free heuristic returned 90, 0, 90. Page 2 alone would have been left sideways,
# and the rebuilt PDF would then hold two different page geometries - worse than
# leaving all three untouched, because the renderer and OCR both assume one.
# Page 2's own profile is decisive and wrong (banding +0.303 towards 0 degrees when
# 90 is upright), and cropping to the ink makes it MORE confident, not less. The
# reliable signal is not in the page, it is in the batch.
from orient import _agree_across_pages  # noqa: E402

check("the odd page out is brought into line", _agree_across_pages([90, 0, 90]) == [90, 90, 90],
      f"{_agree_across_pages([90, 0, 90])}")
check("a booklet already agreeing is untouched",
      _agree_across_pages([90, 90, 90]) == [90, 90, 90])
check("an upright booklet stays upright", _agree_across_pages([0, 0, 0]) == [0, 0, 0])

# Conservative on purpose: a booklet that genuinely disagrees is left alone rather
# than forced, because forcing it would rotate pages that were right to begin with.
check("no strict majority means no change", _agree_across_pages([90, 0, 0]) == [90, 0, 0],
      f"{_agree_across_pages([90, 0, 0])}")
check("an even split is left alone", _agree_across_pages([90, 90, 0, 0]) == [90, 90, 0, 0],
      f"{_agree_across_pages([90, 90, 0, 0])}")
check("two different rotations never merge",
      _agree_across_pages([90, 270, 90, 270]) == [90, 270, 90, 270],
      f"{_agree_across_pages([90, 270, 90, 270])}")
check("a single page is never second-guessed", _agree_across_pages([90]) == [90])
check("two pages are too few to vote", _agree_across_pages([90, 0]) == [90, 0],
      f"{_agree_across_pages([90, 0])}")

# End to end: three sideways pages must come back as three upright pages of ONE size.
sideways = fitz.open()
for i in range(3):
    pg = sideways.new_page(width=842, height=595)          # landscape
    for row in range(14):                                   # ruled lines + writing
        y = 60 + row * 34
        pg.draw_line(fitz.Point(40, y), fitz.Point(800, y), color=(0.7, 0.75, 0.9), width=0.8)
        pg.insert_text((60, y - 6), "R = R1 + R2 = 20 + 4 = 24 ohm", fontsize=13,
                       color=(0.1, 0.1, 0.4))
book = sideways.tobytes()
sideways.close()
out, rots, changed = normalize_orientation(book, "booklet.pdf")
sizes = {(round(p.rect.width), round(p.rect.height)) for p in fitz.open(stream=out, filetype="pdf")}
check("every page of a booklet ends up the same shape", len(sizes) == 1,
      f"rotations={rots}, page sizes={sizes}")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
