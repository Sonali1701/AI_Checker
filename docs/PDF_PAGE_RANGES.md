# Combined question paper and answer key

The checker and copy workflow accept optional PDF page ranges. Page numbers start
at 1 and refer to the PDF viewer's page count, rather than numbers printed on the
paper. Examples: `1`, `2-5`, or `1-3,5`. Leave a range blank to use the whole file.

For a combined paper:

1. Upload the combined PDF as the official question paper.
2. Select **Question paper and answer key are in the same PDF**.
3. Enter the question pages and the answer pages. Include detailed solutions and
   diagrams in the answer range, as well as the short answer key.
4. Continue with the usual exam details, marks verification and checking.

For the supplied three-page Biology example, the question range is `1` and the
answer range is `2-3`.

Separate question-paper and answer-key uploads still work. Their page ranges are
optional. Student copies are processed in full.

This feature selects input pages before the existing checker runs. Grading,
marks verification, release checks and portal actions retain their existing logic.
Changing the selected pages requires marks verification again. Invalid, repeated
or out-of-bounds page numbers are rejected before grading starts.

## Build 90 validation

The new executable is `dist/AICheckerClient-build90.exe`. The 259 focused input,
grading-route and workflow tests passed, along with seven browser scenario groups
and the existing marks UI checks. The pre-build checks covered 108 script suites
and eight pytest suites; the test runner now invokes pytest files correctly.
All 37 existing application modules outside input handling retained their hashes.

The staged executable passed isolated startup, exact bundled source/interface,
optional range forms on seven API routes, public OAuth configuration and two
frozen grading-worker checks. No fresh provider grading or real portal operation
was performed for this feature. Validation and smoke reports sit beside the EXE.
