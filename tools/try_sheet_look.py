# -*- coding: utf-8 -*-
"""Ask the second look (grader.look_at_sheet) what each upload is, before a live run.

    python tools/try_sheet_look.py --token-file token.txt --paper question_paper.pdf COPY.pdf [COPY.pdf ...]

The look is the page-image reading that must agree with the text check before a copy
is rejected (paper_match.rejection_after_look, 29 Oct 2026). Run it on known junk (a
ticket, a form, typed notes) and on genuine copies, including a phone scanner app's
high-contrast scan and a Hindi-answered copy, and check every genuine one comes back
answers_to_this_paper. Prints one line per copy; costs about Rs 3 a copy. Never print
the token.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--paper", required=True, help="the test's question paper PDF")
    parser.add_argument("copies", nargs="+")
    args = parser.parse_args()

    import fitz
    import pw_access
    from grader import look_at_sheet, pdf_or_image_to_pngs
    from paper_match import LOOK_CONFIDENCE, MIN_PAPER_CHARS, paper_text_is_partial

    token = open(args.token_file, encoding="utf-8").read().strip()
    with fitz.open(args.paper) as paper:
        paper_text = "\n".join(page.get_text() for page in paper).strip()
        pages = paper.page_count
    paper_bytes = open(args.paper, "rb").read()
    usable = len(paper_text) >= MIN_PAPER_CHARS and not paper_text_is_partial(paper_text, pages)
    paper_pngs = [] if usable else pdf_or_image_to_pngs(paper_bytes, os.path.basename(args.paper))
    print(f"paper: {len(paper_text)} characters of text for {pages} page(s); "
          f"{'sent as text' if usable else 'sent as page images'}")
    for path in args.copies:
        name = os.path.basename(path)
        try:
            pngs = pdf_or_image_to_pngs(open(path, "rb").read(), name)
            look = look_at_sheet(pngs, paper_text if usable else "", paper_pngs,
                                 google_token=token, task_id=pw_access.new_task_id("sheetlook"),
                                 filename=name, usage_out={})
        except Exception as exc:  # noqa: BLE001 - one copy's failure is reported, the rest go on
            print(f"{name}: look failed ({type(exc).__name__}: {exc})")
            continue
        sure = "sure" if look.confidence >= LOOK_CONFIDENCE else "not sure enough to reject"
        print(f"{name}: {look.upload} at {look.confidence:.2f} ({sure}), "
              f"{'handwritten' if look.handwritten else 'not handwritten'}, {look.document}"
              + (f", other paper: {look.other_paper}" if look.other_paper else "")
              + (f" | {look.reason}" if look.reason else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
