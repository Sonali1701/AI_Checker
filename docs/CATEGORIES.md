# Exam categories

Every paper is graded under one exam category. Each category's settings live in
its own file under `categories/`, so a change for one exam family cannot leak
into another.

| Category | File | What is different today |
|---|---|---|
| School board (CBSE, ICSE, ISC, state) | `categories/school.py` | Better answer of an OR counts. Rs.12 ceiling. Full size marks. |
| PW Vidyapeeth (Radiant, Parishram ...) | `categories/vidyapeeth.py` | Same as school for now. Never auto detected; the operator picks it. |
| Commerce, Class 11 and 12 | `categories/commerce.py` | Second examiner on every copy. Rs.18 ceiling. 0.8 size marks. Brief correct Economics/BST points keep their marks; invalid account formats earn nothing. Build 91 enforces first N theory points, original-image maths/accountancy checks, and holds incorrect or misplaced PDF awards. |
| CA / CMA / CS | `categories/ca.py` | ICAI strictness prompt. Only the first answers of an OR count. AI marks reading (no regex). Whole number totals. 0.6 size marks. Rs.18. Since build 85 (live check, 6 Oct 2026): every copy to faculty review; a provision credited without its section number is a warning, not a hold; case application rules; an overall remark with a presentation sentence on every copy. |
| Design and drawing | `categories/design.py` | Own pipeline (design_grading). Every copy to faculty review. |
| Teaching exams (KVS, NVS, EMRS, TET) | `categories/teaching.py` | Teacher recruitment mains papers with no answer key. With no key uploaded, the job writes a model answer key from the paper once (`teaching_key.py`, cached per paper), checks that every question is answered with steps adding up to its marks, freezes those steps for every copy, and saves `AI_MODEL_ANSWER_KEY.pdf` beside the results and in the zip. Rules for a model key, Hindi or English answers, letters, reports and pedagogy. Copies are released to students under the usual release checks. Never auto detected. |
| School of Innovation (tech and non-tech) | `categories/innovation.py` | PW Skills assignments typed into a document and uploaded as a PDF (full stack, data science, data analytics, non-tech courses). Typed documents are accepted (the typed-document refusal is off), text pages are read from the PDF text layer instead of OCR (`typed_sheets.py`), and "Each question carries N marks" with Question 1 to N is a free marks reading when N times the marks is the official total. A PDF that holds only a name and a GitHub or Drive link, or more than 40 pages (a spreadsheet export), goes to faculty review unread with the reason. Rules: the key is one model solution, code is paid per requirement it meets and only for the effect it has as written (a conflicting media query or a later overriding rule cancels the credit, mentor review 15 Oct 2026), a link in place of an answer earns nothing for that question. Every copy to faculty review until calibrated. Rs.18. Never auto detected. |
| The Gurukulam School (PW Gurukul) | `categories/gurukul.py` | Diagnostic tests (DT), Grades 1 to 8, 20 marks in 30 minutes, answered in ruled notebooks. Same settings as school; the rules find answers by the child's per-section numbering ("Sec-B 1."), mark one-mark items 1 or 0, need the exact word form on language items (no credit for "babys"), follow the key's own scheme (Hindi ½ splits), mark a question the key leaves open from the paper and tag it "[teacher to check: ...]", and write short, kind remarks for a child. The cover remark is written for a child and the parents. A model key is written when none is uploaded. Every copy to teacher review until calibrated. Never auto detected. |

## How a paper gets its category

`categories.resolve()` decides, in this order:

1. the category the operator picked in the "Exam category" select,
2. the design paper type,
3. the legacy exam type "professional" (old clients, old job records),
4. auto detection from the class and subject the operator typed: CA hints in
   the class ("CA Inter", "CMA", a bare "CA") give CA; a Class 11 or 12 commerce
   subject gives Commerce; everything else is School board.

Vidyapeeth, Design, Teaching, School of Innovation and Gurukul are never guessed. An unknown category is refused with a
400, never graded under a guess. The job record keeps `category` and
`category_source` ("explicit" or "auto").

BST, OCM and SP are recognized commerce subject abbreviations. Mathematics,
Core Mathematics and Applied Mathematics are auto-detected as Commerce only
when Class 11/12 explicitly names the Commerce stream. An explicit operator
category still takes precedence.

## Commerce feedback checks in build 91

For counted BST, Economics, OCM and SP theory answers, only the first N live
points are checked, including wrong points. Later correct points cannot replace
earlier wrong points. Official question counts and owned OCR list anchors are
checked deterministically; ambiguous order or missing decisions require review.
The best-answer rule for OR questions is unchanged.

Credit explicitly supported only by missing or cancelled work is removed before
verification. A live rewrite is located only inside its own answer. Maths checks
require the existing examiner to inspect original symbols, working, one-mark
allocations and complete corrections. Accountancy format and brief theory
deductions also require independent verification. Uncertified Commerce checks
and score changes after certification go to faculty review.
Margin shortening preserves an existing actionable examiner correction when the
first sentence is generic. Missing, generic or fragmentary final deduction
feedback requires review even when the marks were independently certified.

Commerce release checks compare printed PDF numerals with question and step
awards and enforce mark placement. Renderer repair is attempted before holding
an unresolved copy. Other categories retain their existing release policy.

Regression fixtures reproduce the reported defects offline. Comparison against
the spreadsheet's original student copies, additional BST samples, and fixing
the spreadsheet's mismatched English/Applied Maths links remain separate data
validation work; build 91 does not claim those samples have been regraded.

## Adding a rule for one category

- **A marking rule** (goes into the grading prompt of that category only): set
  `PromptPolicy.extra_rules` in the category's file. Write it short, general and
  numbered; the prompt is cached and shared by the whole batch.
- **A release hold** (a check that must send this category's copies to faculty):
  add its issue code to `ReviewPolicy.extra_hold_codes`. To let a shared hold
  through as a warning for one category, use `ReviewPolicy.warn_only_codes`.
  `ReviewPolicy(auto_release=False, review_reason=...)` sends every copy of the
  category to faculty review, marked and with its PDF (design papers, and CA since
  build 85).
- The category's `extra_rules` reach the grader AND the second examiner, so the
  examiner cannot raise what a category rule takes away.
- **Money, mark size, second examiner, OR rule**: the `cost`, `render`,
  `examiner` and `choice` fields.

Then re-record the fingerprint and say in the commit what moved:

```
python tools/category_golden.py           # shows what changed
python tools/category_golden.py --write   # records it (deliberate changes only)
python tools/selftest.py
```

`tests/test_category_golden.py` pins the prompt bytes, mark size, budget,
second examiner, department and OR rule for 48 class/subject/exam-type
combinations, so a refactor that changes any of them fails loudly.

## Still following the subject inside a category

A school paper in a commerce subject (Class 9 Economics) still gets the commerce
mark size, budget and second examiner, as before categories existed
(`categories.mark_scale`, `budget_inr`, `always_moderate`). The subject axis
(`categories/subjects.py`) also still picks the subject prompt blocks: a
commerce stream English paper is marked as English. Changing either is a
deliberate, announced change.

## PW Vidyapeeth intake and results sheet (team requirements, 10 Oct 2026)

- **Many students in one PDF.** With the Vidyapeeth category, a PDF holding several
  booklets is split at each printed cover page (`vidyapeeth_intake.py`, a local image
  match against two templates in `assets/`, one per scanner framing measured; covers
  score 0.84 or more, answer pages at most 0.47, threshold 0.65). A page between 0.42
  and 0.65 may be a cover: its booklet goes to a person. Booklets are graded as
  `<file> #01.pdf`, `#02` ... Pages before the first cover and a cover with no answer
  pages go to faculty review without being graded. An upload may be one stack of up
  to 300 MB; each booklet in it must be 60 MB or less.
- **Release (12 Oct 2026).** Copies the release gate finds no fault on go to the
  student; the rest go to faculty review with the reason.
- **Cover details.** Each booklet's cover is read from a 220 dpi crop of its top half
  (student name, roll number, subject, class, batch, centre, phone, date), and the
  roll and phone boxes (8 and 10) are read a second time by another model. A digit
  the two readings disagree on becomes "?". A roll number that is empty, uncertain or
  not 8 digits sends the copy to faculty review to be matched by hand.
- **Results sheet.** `student_results.csv` (row by row) and `student_results.xlsx`
  (at the end of a folder run, and in an upload's zip): Center Name, Course, Class,
  Board, Subject, Test Date, Registration Number, Student Name, Phone Number,
  Subject-wise Score, Total Marks, Percentage, Review Status, Report Link, plus the
  source file and notes (duplicate registration numbers are named). Centre, course
  and test date typed once in the form are used for every student of the upload;
  left empty, each cover's reading is used. The class is the one the paper was
  graded for, dates are DD/MM/YYYY, names in capitals, and a phone number that is not
  a certain 10 digits is noted.
- **File names (13 Oct 2026).** Each checked copy is named by the student's name and
  roll number from the cover (`evaluated_TANYA_SINGH_23217614.pdf`,
  `REVIEW_..._reasons.txt`), in an upload's zip, its single-copy download and
  folder mode alike; a roll digit not read for certain is X, a second copy with the
  same name gets `_2`, and a copy whose cover gave neither keeps its file's name.
  Folder mode records each name in its ledger, so a resumed run keeps them.
- **Answer language and step marking** (any category, `marking_settings.py`): the
  operator picks the answer language and Standard / Strict / Lenient steps; both reach
  the grader and the second examiner. Languages other than English and Hindi send
  copies to faculty review until sample copies in that language have been checked.

## School of Innovation (13 Oct 2026)

- **Typed submissions.** `IntakePolicy.typed_submissions` turns off the
  typed-document refusal for the category and reads every text page from the PDF
  text layer (`typed_sheets.text_layer_pages`): exact text for code, no OCR cost,
  line boxes in the rendered page's pixels so marks anchor as before. Pages that
  are pictures (screenshots) or rotated still go to Mathpix. A link annotation's
  target is written after the words that carry it, so "Github Link : CODE" reads
  as the URL.
- **Held unread, with the reason** (faculty review, not rejected): a submission
  whose text, links removed, is under 600 characters and names a link
  (`typed_sheets.link_only_reason`), and an upload over `IntakePolicy.max_pages`
  (40) pages (`too_many_pages_reason`). Of the 12 sample submissions, 5 are
  link-only and 2 are 160 and 480 page spreadsheet exports.
- **No class field (14 Oct 2026).** An assignment belongs to a course, not a class,
  so `Category.level_label` ("School of Innovation") marks the category as having no
  class level: the form hides the field (`body.no-level-mode`), the checklist does not
  ask for it, and the server stands the label in wherever a level is shown or
  required (`server._effective_level`).
- **Marks reading.** `grader._uniform_marks_scheme` prices "Each question carries
  20 marks" over Question 1..N only when N x 20 equals the official total; it is
  reached only when no section header matched, so it changes no paper the section
  reader already prices.
- **Mentor review (15 Oct 2026).** Both checked samples were right except one
  media query answer, paid in full for having the media query the key uses while a
  conflicting media query in the same answer changed the result. Rule 5 of
  `INNOVATION_RULES` (rule 4 until 21 Oct) now pays a requirement only for what the
  code produces as written (CSS cascade, later or more specific rules, reassigned
  variables, early returns), names the correct patterns so a right answer is not
  cut, and asks the remark to quote the conflicting line. It reaches the grader and
  the second examiner, which re-checks awarded marks.
- **PW Skills team's test (21 Oct 2026).** Five observations, five changes:
  1. *Question numbers*: numbered points inside an answer were read as questions.
     `answer_map.build_answer_map(..., typed=True)` (passed for a category with
     `typed_submissions`) drops every bare "1." / "2)" label once two questions
     carry a "Question N" / "QN" heading (`_typed_list_points`); rule 2 says the
     same to the grader. Measured case: a lone "4." point in Q3 opened Q4 and took
     Q3's answer.
  2. *Partial marking*: rule 7, wrong content is not cancelled by right content,
     overriding the school rules that pay a brief or partly right answer in full.
  3. *Tables and charts*: typed pages now read a ruled table as one line per row
     (cells joined by " | ") under a `[detected table]` anchor, and a picture
     covering 3 percent of the page or more as a `[detected figure]` anchor
     (`typed_sheets._page_lines`, P<n>D<k> lines); rule 8 says to read them from
     the page image and never call them not attempted. A document with a figure or
     table is not link-only.
  4. *Marks and feedback*: rule 12, one criterion per requirement and a remark that
     accounts for every mark lost. The marked copy's band already shows
     question-wise totals, and `preflight` already holds a full-marks remark that
     names a fault.
  5. *Context*: rule 6, read the whole solution before deducting; a fault handled
     elsewhere in the answer is not a fault, an error anywhere that affects the
     result is.
- **Linked work and other formats (built 21 Oct 2026, after the team's samples).**
  `IntakePolicy.linked_work` (innovation only). In `pipeline.grade_sheet`, before the
  photo gate, `linked_work.prepare_submission` turns the upload into ONE PDF:
  - a PDF with GitHub / Drive / Docs / Sheets / Colab / OneDrive links keeps its pages
    and gains each linked file after them; links that cannot be opened get a page
    naming why. A cover of two pages or fewer is `label_free_pages` for the answer
    map (a cover listed "Q2 ... Q14" as a table of contents);
  - a notebook, workbook, CSV, Power BI (.pbix/.pbit), Tableau (.twb/.twbx), Word
    or code file uploaded directly, or a .txt/.url of links, becomes the converted PDF.
  `work_document.py` converts: notebooks with saved outputs and plots (nothing is
  run; Plotly charts summarised), workbooks as per-sheet facts (frozen panes,
  filters and criteria, header formatting, widths, conditional formatting, tables),
  formula patterns per column with counts, first rows of values, pivot tables and
  charts (a sheet named after a question is headed "Qn: sheet ..."), Power BI data
  models (calculated columns, measures and calculated tables with DAX, Power Query,
  relationships; Power BI's auto date tables left out; an empty formula shown as
  "(no formula saved in the file)") read from the XPress9 DataModel with the
  `xpress9` wheel (MIT) and stdlib sqlite3, plus report pages and visual fields,
  Tableau data sources, calculated fields, worksheets and dashboards, and Word text,
  tables and pictures. Only those hosts are contacted, over HTTPS, every redirect hop
  checked, 40 MB a file, 80 MB a submission, 12 files from a repository or folder,
  6 links. A link-only cover whose links cannot be opened goes to faculty review
  with the reason (`unopened_reason`). Uploads: `server._sheet_extensions(category)`,
  `bulk.list_sheets(..., extensions)` and the form's picker take the extra types for
  this category only. Rule 10 tells the grader how to read the converted pages.
  On the 13 samples of the "Other samples" tab: every link opened, 12 documents
  produced (notebooks, the 16-sheet workbook as 15 pages, both Power BI models, a
  Drive PDF), typed PDFs unchanged. Tests: tests/test_innovation_linked_work.py.
- **Other submission formats (21 Oct 2026, assessed first).** All seven links
  in the six link-only samples were publicly downloadable: two Drive PDFs with the
  full answers, three Jupyter notebooks on GitHub (one with no saved outputs), a
  Drive folder holding a PDF and a notebook, and two Excel workbooks (one sheet per
  question, 6,000+ formulas, autofilters, pivot tables). Following links and
  converting notebooks and workbooks would make half the samples markable. Power BI
  (.pbix) and Tableau (.twb/.twbx) expose their structure (pages, visuals, fields,
  calculated fields) but not rendered visuals; no samples yet.
- **Samples:** sheet "Assignment Sample" (FSD-AG-003 HTML & CSS 160, DS-AG-001
  Python 200, DA Excel Basics). Not yet run live (needs a token); the harness is
  `tools/live_innovation_samples.py`.
- **PW Skills feasibility report and SME review (22 Oct 2026).** Sheet "Assignment
  Sample Data for AI Tool": tab "Main Report" (risks and action plan), "PDF samples"
  (AI and SME marks with the SME's analysis), "All format sample" (one submission per
  format). Run on build 126 with `tools/live_innovation_feedback.py` (a cases file of
  papers, keys and copies, AI beside SME marks). Changes:
  1. *Formats.* A Colab notebook downloads from Drive under its title alone ("Excel
     Basic"): `linked_work._named` adds the extension its content shows (notebook,
     PDF, workbook, Word, Power BI, Tableau, images). One notebook linked from Drive
     and GitHub is read once, the copy with more saved output (`_work_key`).
  2. *Videos* (the DM one-minute summary): a link to YouTube, Loom or Vimeo, a video
     file name, a `video/` or `audio/` download, or a video in a Drive folder is
     listed as "a video ... the reviewer marks it" (`VideoLink`) and never
     downloaded. A cover of screenshots is not link-only (`_has_pictures`): the DM
     copy of seven screenshot pages had been held unread because its 40 MB video
     could not be opened.
  3. *SME review flag.* `linked_work.review_notes` names each video not watched and
     each link not opened as a review reason on a copy that was still marked.
  4. *Marking.* Rule 13 counts what each question asks for: a missing ask loses its
     whole share (a missing second part is half), a result without the working the
     question asks for earns at most half that ask, code alone does not answer an
     "explain / compare / when to use" ask, a requirement not attempted earns
     nothing. Rule 14: an answer placed elsewhere ("refer sheet 2") is marked there.
     Rule 8: a computed column in a printed sheet answers a "calculate" task. Rule
     10: a video gets no marks and a remark that the reviewer marks it. Measured on
     build 126: a for...in versus for...of question answered with two loops and no
     explanation got 20 of 20 (SME: missing), a computed property names question
     answered with a static key got 9 of 20.
  5. *Cover remark.* `_compose_overall_remarks(..., assignment=True)` for this
     category: a full-mark copy gets two sentences of appreciation and one optional
     next step (the team: full marks came with no feedback at all).
  6. *Marks.* The DA papers print no marks at all, and the AI reader called every
     question unresolved, so no DA copy could be graded. `MarksReadingPolicy.
     unprinted_convention` tells the reader this exam's rule: an equal share of the
     entered total for each question, printed marks always winning.
- **"All format sample" tab with papers (23 Oct 2026).** The team added the question
  paper and solutions for each format row. Every submission converts (Google Sheet
  link, Drive folder of workbooks, Power BI and Tableau files, Colab notebook with a
  theory PDF, GitHub notebooks and a project repository, SQL files, pasted
  screenshots; the DM videos are listed for the reviewer). Two gaps found:
  1. *No solutions file* for 6 of the 14 papers, and the form refused a paper with
     no key. `AnswerKeyPolicy.may_generate` is now on for this category: with no key
     uploaded, `teaching_key.write_answer_key` writes the model key once per paper,
     told the exam is a PW Skills assignment (`AnswerKeyPolicy.examination`,
     `key_notes`: code by requirement and behaviour, tool tasks by what the file must
     show); teaching's prompt and cache are unchanged (`teaching_key.for_exam`). The
     form offers "No answer key" for any category with `generates_key`
     (`ui_choices`, `key-optional-mode`).
  2. *Equal shares by code.* A 35-question Data Toolkit paper of 100 marks came back
     from the reader totalling 90 and was refused. `pipeline.equal_shares` gives each
     question its share in half marks (the earlier questions take the odd halves) when
     the category has `unprinted_convention`, the paper prints no tags or choice, and
     the reading's own maxima are near equal; a reading of printed, uneven marks is
     left alone.
- **Review of the 14 marked copies (23 Oct 2026, build 131).** Each copy was marked
  again by hand against its paper and solutions: 5 fit to send, 9 not (overpaid
  practical tasks, false remarks, converter gaps). Changes for build 133:
  1. *Rules.* 5: code that would not run (an INSERT with no VALUES) earns nothing for
     its task. 7: a stated result (query output rows, a total, an average) is checked
     against the paper's data, and a wrong one loses its share. 13: a practical task is
     answered by its result; steps alone earn at most half, a generic recipe at most a
     quarter, fragments only what they cover. 15: a project brief's deliverables are
     found in every linked file (a pipeline PDF and an app.py were marked not
     attempted). 16: work for a different assignment earns nothing and the remarks say
     so, never "Not attempted".
  2. *Tableau* (`work_document.tableau_html`): fields named as an analyst names them
     (SUM(Treatment_Cost)), filter ranges and values, Marks card fields, mark colour and
     labels, sorts, titles, dashboard controls, story points (a date filter, a colour
     and the one story point were in the .twb, and three remarks called them missing).
  3. *Notebooks* (`work_document._html_output`): a Plotly chart saved as HTML becomes
     the chart's summary, read from `Plotly.newPlot`, instead of a page of JavaScript.
  4. *Wording* (`wording.plain_sentences`): a formula token ("=A1-TODAY()") keeps its
     minus sign (it reached a learner as "Use =A1 TODAY()"), and an em dash between
     words becomes a comma instead of vanishing.
  5. *Cover remark* (`grader._assignment_cover_line`): the cover call sees every
     question's marks, the steps it earned and the remark for what it lost, and may
     praise only what those lines show (a cover praised filters a dashboard did not
     have, and two learners got the same text).
  6. *Wrong file* (`pipeline._wrong_work_note`): a typed copy with 300+ words of work
     that earns 10 percent or less is flagged for the reviewer as possibly another
     assignment or the wrong file.
  Still open: marks drift on typed and converted pages (ticks beside the next line or
  over the learner's text, margin labels where an answer ends), a renderer matter.

## The Gurukulam School, PW Gurukul (20 Nov 2026)

The Gurukul team sent sample copies of five diagnostic tests (English Grade 5 Nouns,
Number; Hindi Grade 6; Science Grade 7 Measurement of Time and Motion; Social Science
Grade 8 The Parliamentary System; Mathematics Grade 6 Integers). A diagnostic test is
a 20 mark, 30 minute chapter test. The teacher's Word file "DT & AK" holds the test
and then its answer key. The children answer in ruled notebooks, mostly without
copying the questions, and each child's pages are scanned into one PDF.

- **Uploading.** Convert the DT & AK file to PDF and either upload it twice (question
  paper and answer key) or once with "Question paper and answer key are in the same
  PDF" and the pages of each. Answer keys often start halfway down a page, so splitting
  the Word file at the key heading gives the cleanest two PDFs.
- **Rules** (`GURUKUL_RULES`):
  1. the context, a diagnostic test marked the way the class teacher marks it;
  2. answers are found by the child's numbering, which starts again at 1 in every
     section, and by content when a heading is missing or the paper repeats a section
     name;
  3. one-mark items are 1 or 0, never half;
  4. a language item that tests a word form needs that exact form, while capitals,
     full stops and untested words cost nothing, and the word is read again on the
     page before a look-alike letter costs a mark (pilot E01: "sheep" read as "sheen");
  5. the key's own scheme (Note:, अंक-विभाजन, the Hindi ½ per word) is followed;
  6. short answers are marked on ideas, every thing the question asks for ("Give
     examples") is a point of its own, and a "Is this correct? Explain" part has two
     halves;
  7. a question the key leaves without an answer, or calls undeterminable, is marked
     from the paper when sure, otherwise any attempt earns the mark, and a mark lost
     there is tagged "[teacher to check: the key gives no answer]" (a full-mark
     question keeps no remark, so paper-level gaps are listed for the teacher);
  8. a key whose letter and text disagree, or whose answer contradicts the question,
     is marked against the correct answer and tagged "[teacher to check: the key says
     ...]";
  9. remarks are one short, simple, kind sentence in the paper's language, giving the
     right answer in words for a wrong one-mark item.
- **Cover remark.** `grader._compose_overall_remarks(diagnostic=True)` writes the
  overall remark for a young child and the parents, in Hindi for a Hindi paper and in
  English for every other subject (decided in code: in the pilot the English and
  Science covers came back in Hindi), without the score, and with no presentation
  sentence about headings. It is written from every question's marks and the steps
  each earned (as for an assignment), so it praises only what the copy shows (pilot
  C01: told only the lost marks, the cover of a Motion and Time test praised "what
  living things need to grow"), and a full-mark copy gets two sentences of praise.
  It names topics and words and never counts questions: in the 23 Nov samples six
  of 33 covers miscounted ("the first eight questions correctly" over a wrong Q6,
  "all three plurals" in a five-word question), and "plurals that end in -o" was
  printed as "end in minus o" by the shared wording step.
- **Steps from the key** (`key_rubric.key_rubric_applies`, 23 Nov 2026). Every
  Gurukul paper's written questions are marked on steps set once from the teacher's
  key (`_gurukul_system`: the school key-rubric prompt with the Gurukul paper named,
  one step per one-mark item, and one step per thing the question asks for) and
  frozen for every copy. With steps set per copy, "What is meant by rest and motion?
  Give examples" was split into the two meanings only, twice, and an answer without
  an example got 2/2.
- **A section of options is a list of items** (`exam_package._question_nodes`, 23 Nov
  2026). A row worth more than 4 marks is never one multiple-choice question, whatever
  the marks reading typed it: English Section B (five "circle the correct plural"
  items) was typed objective with answer (b), and every copy that wrote the words
  scored 0/5 "Correct answer is (B).". It is now marked on one step per item.
- **Struck-out words** (rule 10): the word written to replace a struck-out one is the
  answer, and a struck-out word is never quoted as the child's answer.
- **The 23 Nov 2026 review.** Of 33 sample copies none was fit to send as the tool
  drew it (ticks on the wrong lines, wrong marks above, untrue cover sentences); all
  were corrected by hand with `ca_eval/gurukul/correct/` before delivery.
- **Release.** Every copy goes to teacher review (`auto_release=False`) until the AI
  marks have been compared with the teachers' own marks on sample copies.
- **Sample paper problems found on 20 Nov 2026.** The Hindi copies answer a different
  test from the "Grade6-Hindi-DT & AK-Sept26" paper that came with them. The Maths key
  has no answer for Q3, and the Maths paper prints "Section A" twice. The Science key
  calls Q7 "cannot be determined" (rule 7 covers it). Tests:
  tests/test_gurukul_category.py.
