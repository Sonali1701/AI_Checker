# Circled scores on the answer sheet

Build `2026.09.11.31-restored-margin-scores` restores the margin scores removed
in build 30. The sheet circles and the right-margin summary are both required.

Production PDFs now place one question-labelled circled total on the original
answer sheet for every attempted question, including full-credit, partial-credit
and zero-credit answers. Step awards also stay on the sheet. Criteria sharing
one verified OCR anchor remain grouped; a group representing the entire question
uses a single labelled total, avoiding duplicate circles with the same score.

The right-hand strip contains a question-labelled awarded/maximum score for every
attempted question, including full-credit answers with no feedback. A reconciled
step breakdown follows when available, then the deduction remark. Fractions such
as `9.5/10` and `8.5/10` stay in the short heading; arithmetic wraps separately at
spaces between terms. The report header retains awarded/maximum totals. Feedback follows scoring evidence rather
than an isolated heading on the previous page. Actual continuation pages receive
a small `Qn continued` label on the sheet instead of an unlabelled bracket in the
strip. An unanchored whole-table OCR region containing another answer's heading
does not create a false continuation; explicit criterion evidence is preserved.

Placement searches clear paper on the evidence row, checks both the circle and
the numeral against student ink, and reserves each annotation against collisions.
Digital pages retain their full paper width. Thick scanner borders are not
discarded as notebook ruling. If a required mark cannot fit, release validation
holds the copy instead of allowing it to move into the feedback strip.

The supplied copy was rendered from its saved report, original PDF and cached
OCR. Its question records and academic decision digest are unchanged at 43.5/50.
The regenerated artifact passes rendered-copy and evidence-manifest checks.
These checks cover geometry and score visibility; they do not establish that
coarse table anchors communicate individual criterion awards adequately. The
earlier review identified remaining issues with grouped table marks and removed
correctness ticks. This release specifically restores the missing margin scores.
No grading, OCR or verification provider calls were made for this correction.

`tests/test_on_sheet_scores.py` covers circled full/partial/zero totals, dense
rows, off-sheet marks, missing circles, false table continuations and readable
continuation labels. Existing MCQ and evidence tests now enforce the updated
placement policy.
