# Right-hand score strip and MCQ placement

The current placement policy is documented in [ON_SHEET_SCORES.md](ON_SHEET_SCORES.md).
Build 31 keeps score circles on the answer sheet and restores question scores,
breakdowns and feedback in the right strip. The build-29 behavior below is historical.

Build `2026.09.10.29-mcq-right-margin` removes the extra padding introduced in
build 28 and removes the MCQ placement fallback to the left of an answer.

Each original page retains its dimensions and receives only the existing score
strip directly on its right edge. Step circles and feedback use separate space
within that strip when an answer reaches the paper edge. MCQ verdicts stay right
of their own answer; tight columns use a nearby corner without covering the next
answer. Clearance accounts for raster rounding after adding the report header.

The 17-page MTS-04 Auditing copy was re-rendered from its previously released
report and cached OCR. Every question and score-bearing decision is identical;
the score remains 83.5/100. All 17 pages were visually checked, and mechanical
release checks found no warnings or blockers. No OCR, grading, or verification
provider calls were needed for the layout correction.

Tests cover mixed page sizes without extra padding, preserved evidence geometry,
readable margin scores, and MCQ marks on the right of their own answers.
