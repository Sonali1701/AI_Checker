# MTS-04 Auditing and Ethics: step-mark layout

The reported failure rejected Q1.a, Q2.b and Q3.a after automatic regrading returned
breakdowns totalling 4.5, 4 and 4.5 marks, respectively. The question paper assigns
5 marks to each.

Visual inspection of the supplied step-marking solution confirms:

| Question | Solution PDF page | Printed allocation |
| --- | --- | --- |
| Q1(a) | 5 | Five steps at 1 mark each, including the case application |
| Q2(b) | 6 | Five listed limitations, labelled “1 marks each point” |
| Q3(a) | 7 | Three paragraphs carrying 1, 2 and 2 marks |

The red allocations are separate PDF text objects. Column-aware extraction improves
their reading order, but loses their horizontal relationship to paragraphs and
point groups. The previous payload optimization dropped all 11 solution images
because the text layer was dense (37,072 characters). Regrading reused that same
text-only key.

Build `2026.09.10.18-step-key-layout` retains images when the key contains separate
mark labels or group allocations. Primary grading, question recovery, independent
verification and verification repair receive those images and instructions for
reading the labels. Cost reservations include the retained key images. Plain prose
keys still use the text optimization. Strict total and fixed-rubric validation remain
in place.

Offline checks cover an annotated digital PDF, both provider payloads, full grading
and recovery flows, and rejection when independent verification remains unresolved.
The actual supplied PDF was also checked to retain all 11 images.

The first live run then exposed a repeated-reader failure: Q1(a) still omitted the
case application and Q5(c), an “any four” question, returned only three maxima.
Build `2026.09.10.27-step-key-recovery` permits one stronger-model recheck of
only the still-invalid questions when key images are available. Haiku recovery
escalates to Claude Sonnet; a Sonnet primary escalates to Opus. Gemini Pro was
tested during the live run but exhausted its output budget before completing its
two-question response. The stronger Claude recovery avoids that reasoning-budget
failure mode. Successful
first repairs are retained provisionally. The actual alternate model and output
allowance are reserved before dispatch, and all repairs must pass the original
mark validation together before any are committed. Independent verification and
the final release gate still run afterward. Transport errors and truncated
responses do not trigger this extra pass.

The live Sonnet recheck exposed a second prompt conflict in Q6(c2): marking every
surplus attempt with a positive criterion maximum inflated an “any four” question
to six marks. Both grading providers now receive an explicit scoring-slot rule at
the end of their request. Incorrect surplus attempts outside the required slots
have zero maximum and zero award; they cannot enlarge the denominator or reduce
marks already earned. The four-point regression covers four correct scoring rows
and two wrong surplus rows.

The same copy also exposed a choice-label problem. `C2)` now maps to the official
alphanumeric alternative, and an OCR digit bracketed by successive roman list
items remains within the answer as uncertain text. Claimed surplus alternatives
without a located label receive a fresh stronger-model read before choice ordering;
absence of an OCR label alone never becomes an assumed blank.

Verification now compresses student and retained key images using the primary
grader's existing image encoding. The live 17-page booklet plus 11-page key
otherwise exceeded the proxy's 32 MB request limit before verification began.
A noisy scanned-booklet regression checks the encoded request size and preserved
page count for both providers.

Part-A's explicit MCQ count and numbered question labels now recover the numeric
official key. The former parser recognized only a Section that "contains" MCQs
with Q-prefixed labels, leaving these objective rows untyped. In mixed papers,
an objective response such as `1) (3)` remains separate from the later descriptive
`Q1` parent heading. The uncapped workflow also honors the configured stronger
verifier instead of applying the historical budget fallback, and its system
instructions require a specific reason for unchanged partial-credit awards.

Already answered sibling labels appearing within an active answer remain uncertain
list content until visual verification, and an example list beginning with OCR
`1)` followed by `ii)` retains its current question. These corrections preserve
Q2(c)'s opinion types and Q5(b)'s detection-risk examples inside their answer regions.

For a final report with choices, the paper-allocation check now compares printed
tags against the complete official graph, while checking every selected maximum
against that graph. Omitted optional questions cannot look like missing printed
allocations. MCQ score placement now rejects the warning-level ink overlap that
would hold strict release, rather than waiting for the higher blocking threshold.
Placement searches a wider vertical range within the same answer line when the
nearest position is occupied. The report header groups objective and descriptive
questions separately when both parts restart at Q1, and reserves room for both
section headings.

The live copy `25d904d1-d533-42a3-a7f5-ee995b48c101.pdf` completed independent
verification and passed the final release gate at **83.5/100** (objective 18/30,
descriptive 65.5/70). Its 17-page evaluated PDF has no release warnings or blockers.
Q1(a), Q2(b) and Q3(a) each retain the paper's five-mark denominator and a matching
five-mark step breakdown. Completed source/OCR/model results were checkpointed
during diagnosis; final rendering and mechanical rechecks made no provider calls.
