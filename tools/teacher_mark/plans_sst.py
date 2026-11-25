# -*- coding: utf-8 -*-
"""Hand-verified marking plans for three SST Subjective Test-06 copies.

Each mark is (kind, anchor[, value]). kind is tick / half / cross. The anchor is an
OCR line id or ('xy', page, x0, y0, x1, y1) in OCR pixels for a line Mathpix did not
return. The value is what gets circled beside the tick (default 1 for a tick, 0.5 for
a half). `at` is the row the margin entry "Q7.c 2.5/3" sits beside. `reason` is the
note under it and in the top strip's "Where marks were lost". `remarks` is the
teacher's overall remark, split into bullets by the strip.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from teacher_mark import Sheet, render_previews  # noqa: E402

WORK = Path(__file__).resolve().parent
DOWNLOADS = Path(r"C:/Users/vs510/Downloads")
OUT = DOWNLOADS / "SST_evaluated_corrected"

PLANS = {
    # ------------------------------------------------------------------ copy A
    "139e179a-05a5-4b58-9314-f2ac315fe2cf": {
        "remarks": ("Very good attempt. Your MCQs, fill in the blanks and Q7(a) to Q7(c) are "
                    "complete and accurate. In explain type questions write one or two lines "
                    "under each point instead of only the heading. That alone would have given "
                    "you full marks in Q7(d) and Q10(c)."),
        "questions": [
            {"qid": "Q1", "max": 1, "score": 1, "marks": [("tick", "P1L13")]},
            {"qid": "Q2", "max": 1, "score": 1, "marks": [("tick", "P1L14")]},
            {"qid": "Q3", "max": 1, "score": 1, "marks": [("tick", "P1L15")]},
            {"qid": "Q4", "max": 1, "score": 1, "marks": [("tick", "P1L16")]},
            {"qid": "Q5", "max": 1, "score": 1, "marks": [("tick", "P1L17")]},
            {"qid": "Q6.a", "max": 1, "score": 1, "marks": [("tick", "P1L18")]},
            {"qid": "Q6.b", "max": 1, "score": 1, "marks": [("tick", "P1L20")]},
            {"qid": "Q6.c", "max": 1, "score": 1, "marks": [("tick", "P1L21")]},
            {"qid": "Q6.d", "max": 1, "score": 1, "marks": [("tick", "P1L22")]},
            {"qid": "Q6.e", "max": 1, "score": 1, "marks": [("tick", "P1L23")]},
            {"qid": "Q7.a", "max": 2, "score": 2, "at": "P1L25",
             "marks": [("tick", "P1L29"), ("tick", "P1L35")]},
            {"qid": "Q7.b", "max": 2, "score": 2, "at": "P1L36",
             # 'ii) Manimekalai' is the first line of page 2. Mathpix returned no line for it.
             "marks": [("tick", "P1L36"), ("tick", ("xy", 2, 250, 190, 600, 250))]},
            {"qid": "Q7.c", "max": 3, "score": 3, "at": "P2L2",
             "marks": [("tick", "P2L6"), ("tick", "P2L11"), ("tick", "P2L17")]},
            {"qid": "Q7.d", "max": 3, "score": 1.5, "at": "P2L18",
             "marks": [("half", "P2L18"), ("half", "P2L19"), ("half", "P2L20")],
             "reason": "Points are only named, not explained. Write one or two lines on each point."},
            {"qid": "Q10.a", "max": 2, "score": 2, "at": "P3L2",
             "marks": [("tick", "P3L4"), ("tick", "P3L5")]},
            {"qid": "Q10.b", "max": 2, "score": 2, "at": "P3L6",
             "marks": [("tick", "P3L7"), ("tick", "P3L10")]},
            {"qid": "Q10.c", "max": 3, "score": 1.5, "at": "P3L11",
             "marks": [("half", "P3L11"), ("half", "P3L12"), ("half", "P3L14")],
             "reason": "Types named with examples, but how each plain is formed is not explained."},
            {"qid": "Q10.d", "max": 3, "score": 3, "at": "P3L15",
             "marks": [("tick", "P3L16"), ("tick", "P3L19"), ("tick", "P3L21")]},
        ],
    },
    # ------------------------------------------------------------------ copy B
    "ad7bc5c9-3a8a-47d7-96ea-24a786c57fbb": {
        "remarks": ("Neat and well organised answers. Every descriptive question you attempted "
                    "scored full marks. You lost marks only on Q5, where the minimum age to "
                    "contest a Lok Sabha election is 25 years, and by leaving Q7(d) unattempted. "
                    "Always attempt every part, even briefly. Check the spelling of Commission."),
        "questions": [
            {"qid": "Q1", "max": 1, "score": 1, "marks": [("tick", "P1L8")]},
            {"qid": "Q2", "max": 1, "score": 1, "marks": [("tick", "P1L9")]},
            {"qid": "Q3", "max": 1, "score": 1, "marks": [("tick", "P1L10")]},
            {"qid": "Q4", "max": 1, "score": 1, "marks": [("tick", "P1L11")]},
            {"qid": "Q5", "max": 1, "score": 0, "marks": [("cross", "P1L12", 0)],
             "reason": "Correct answer is (C) 25 years."},
            {"qid": "Q6.a", "max": 1, "score": 1, "marks": [("tick", "P1L13")]},
            {"qid": "Q6.b", "max": 1, "score": 1, "marks": [("tick", "P1L14")]},
            {"qid": "Q6.c", "max": 1, "score": 1, "marks": [("tick", "P1L15")]},
            {"qid": "Q6.d", "max": 1, "score": 1, "marks": [("tick", "P1L16")]},
            {"qid": "Q6.e", "max": 1, "score": 1, "marks": [("tick", "P1L17")]},
            {"qid": "Q7.a", "max": 2, "score": 2, "at": "P1L19",
             "marks": [("tick", "P1L21"), ("tick", "P1L22")]},
            {"qid": "Q7.b", "max": 2, "score": 2, "at": "P1L23",
             "marks": [("tick", "P1L23"), ("tick", "P1L24")]},
            {"qid": "Q7.c", "max": 3, "score": 3, "at": "P1L25",
             "marks": [("tick", "P1L26"), ("tick", "P2L6"), ("tick", "P2L8")]},
            {"qid": "Q7.d", "max": 3, "score": 0, "at": "P2L9", "marks": [],
             "reason": "Not attempted."},
            {"qid": "Q10.a", "max": 2, "score": 2, "at": "P2L10",
             "marks": [("tick", "P2L13"), ("tick", "P2L14")]},
            {"qid": "Q10.b", "max": 2, "score": 2, "at": "P2L15",
             "marks": [("tick", "P2L15"), ("tick", "P2L16")]},
            {"qid": "Q10.c", "max": 3, "score": 3, "at": "P2L17",
             "marks": [("tick", "P2L18"), ("tick", "P2L20"), ("tick", "P2L23")]},
            {"qid": "Q10.d", "max": 3, "score": 3, "at": "P3L5",
             "marks": [("tick", "P3L6"), ("tick", "P3L8"), ("tick", "P3L11")]},
        ],
    },
    # ------------------------------------------------------------------ copy C
    "49a65640-9440-43d0-b06c-6ae13bbf4f0c": {
        "remarks": ("Good and well structured copy, Yashi. The objective section, Q7(a), Q7(b), "
                    "Q9(a) and Q9(b) are fully correct. In explain type questions each point "
                    "needs a line of explanation, not just a heading, as in Q7(c). Check that "
                    "every characteristic you list really belongs to the landform asked about, "
                    "as in Q9(c)."),
        "questions": [
            {"qid": "Q1", "max": 1, "score": 1, "marks": [("tick", "P1L3")]},
            {"qid": "Q2", "max": 1, "score": 1, "marks": [("tick", "P1L4")]},
            {"qid": "Q3", "max": 1, "score": 1, "marks": [("tick", "P1L5")]},
            {"qid": "Q4", "max": 1, "score": 1, "marks": [("tick", "P1L6")]},
            {"qid": "Q5", "max": 1, "score": 1, "marks": [("tick", "P1L7")]},
            {"qid": "Q6.a", "max": 1, "score": 1, "marks": [("tick", "P1L8")]},
            {"qid": "Q6.b", "max": 1, "score": 1, "marks": [("tick", "P1L9")]},
            {"qid": "Q6.c", "max": 1, "score": 1, "marks": [("tick", "P1L10")]},
            {"qid": "Q6.d", "max": 1, "score": 1, "marks": [("tick", "P1L11")]},
            {"qid": "Q6.e", "max": 1, "score": 1, "marks": [("tick", "P1L12")]},
            {"qid": "Q7.a", "max": 2, "score": 2, "at": "P1L13",
             "marks": [("tick", "P1L19"), ("tick", "P1L20")]},
            {"qid": "Q7.b", "max": 2, "score": 2, "at": "P1L21",
             "marks": [("tick", "P1L21", 2)]},
            {"qid": "Q7.c", "max": 3, "score": 1.5, "at": "P1L22",
             "marks": [("half", "P1L23"), ("half", "P1L24"), ("half", "P1L25")],
             "reason": "Features are only listed, not explained. Write one or two lines on each."},
            {"qid": "Q7.d", "max": 3, "score": 2.5, "at": "P2L1",
             # The photo is skewed, so the end of line 2 shares its y with line 3's words.
             # The half mark for point 1 goes after 'stone' on line 1. 'Mauryan History.'
             # (end of point 2) has no OCR line of its own.
             "marks": [("half", ("xy", 2, 1000, 355, 1126, 415)),
                       ("tick", ("xy", 2, 230, 505, 560, 560)),
                       ("tick", "P2L6")],
             "reason": "First point only describes the edicts. Explain their significance, for example that they show the extent of Ashoka's empire."},
            {"qid": "Q9.a", "max": 2, "score": 2, "at": "P2L8",
             "marks": [("tick", "P2L9"), ("tick", "P2L10")]},
            {"qid": "Q9.b", "max": 2, "score": 2, "at": "P2L17",
             "marks": [("tick", "P2L17"), ("tick", "P2L20")]},   # one per column heading
            {"qid": "Q9.c", "max": 3, "score": 2, "at": "P2L30",
             "marks": [("cross", "P2L31"), ("tick", "P2L32"), ("tick", "P2L33")],
             "reason": "U shaped valleys are carved by glaciers. They are not a feature of young fold mountains."},
            {"qid": "Q9.d", "max": 3, "score": 2.5, "at": "P2L34",
             "marks": [("tick", "P2L34"), ("half", "P2L35"), ("tick", "P2L36")],
             "reason": "Characteristics are too brief. Mention the rugged uneven surface cut into deep valleys and flat topped hills."},
        ],
    },
}


def main(only: set[str] | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png_dir = WORK / "png"
    png_dir.mkdir(exist_ok=True)
    for uuid, plan in PLANS.items():
        stem = uuid[:8]
        if only and stem not in only:
            continue
        ocr = json.loads((WORK / f"ocr_{stem}.json").read_text(encoding="utf-8"))
        raw = DOWNLOADS / f"{uuid}.pdf"
        sheet = Sheet(str(raw), ocr, plan)
        out_pdf = OUT / f"evaluated_{uuid}.pdf"
        sheet.build(str(out_pdf))
        total = sum(q["score"] for q in plan["questions"])
        print(f"{stem}: pitch={sheet.pitch:.1f}pt tick={sheet.tick_h:.1f}pt "
              f"circle_d={2*sheet.circle_r:.1f}pt total={total}/30 -> {out_pdf}")
        render_previews(str(out_pdf), str(png_dir), stem)


if __name__ == "__main__":
    main(set(sys.argv[1:]) or None)
