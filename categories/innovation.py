# -*- coding: utf-8 -*-
"""PW School of Innovation assignments (PW Skills): tech and non-tech courses.

The learner types the answers into a document and uploads it to the LMS as a PDF.
Every question carries the same marks ("Each question carries 20 marks", total 160
or 200), the assignment PDF is the question paper and a "Solutions" PDF of the same
code is the answer key. What is new for this category:

- the submissions are typed, so the typed-document refusal is off and text pages
  are read from the PDF text layer instead of OCR (typed_sheets.py);
- a PDF that holds only the learner's name and a GitHub or Drive link, or a data
  export of hundreds of pages, cannot be marked from the document and goes to a
  reviewer with the reason;
- code is marked on what it does against the question's requirements, the key being
  one model solution among many.

Samples (13 Oct 2026, sheet "Assignment Sample"): FSD-AG-003 HTML & CSS (8 x 20 =
160), DS-AG-001 Python Fundamentals and Data Structures (10 x 20 = 200) and an Excel
Basics and Functions assignment (DA). Of the 12 learner submissions, 4 are full typed
answers (8 or 9 pages), 1 is screenshots, 5 hold only a link, and 2 are 160 and 480
page spreadsheet exports.
"""
from __future__ import annotations

from dataclasses import replace

from .base import AnswerKeyPolicy, CostPolicy, DetectionRules, ReviewPolicy
from .school import SCHOOL

INNOVATION_RULES = """\
1. This is a PW School of Innovation assignment (PW Skills): tech courses such as
   full stack development, data science and data analytics, and non-tech courses.
   The learner typed the answers into a document and uploaded it as a PDF, so the
   pages are printed text, pasted code and screenshots, not handwriting. Mark them
   the way an assignment reviewer does, question by question, against what each
   question asks.
2. Which question an answer belongs to: an answer starts at the heading the learner
   copied from the assignment ("Question 3 :", "Q3)", "Q.3") and runs to the next
   such heading. Numbered points, steps and sub-items inside an answer ("1.", "2)",
   "Step 3", the question's own "1. Customer Code") belong to that answer and are
   never a question number. When a heading is missing or carries the wrong number,
   decide by the question text the learner copied, compared with the assignment.
   PW SKILLS TEAM (21 Oct 2026): numbered points read as question numbers moved
   answers to the wrong question and left questions unevaluated.
3. The answer key is ONE model solution, not the only one. A different approach that
   does what the question asks (other tag, class, variable or function names, other
   library calls, formulas or wording) earns the same marks as the key's. Never cut
   marks because the learner's code or wording differs from the key's. A detail the
   key happens to have but the question does not ask for (an explicit height or width,
   a colour, a class name, an extra property) is never a requirement: when the
   learner's code reaches the result the question asks for another way, it is met.
   SME REVIEW (22 Oct 2026): an overlay answer lost marks for "container height
   missing" while the image gave the container its height and the overlay's height:
   100% covered it, as the question asked.
4. Code (HTML, CSS, JavaScript, Python, SQL, spreadsheet formulas): split the
   question's marks over the requirements the question states, and pay each
   requirement when the code as written meets it (a box "twice the width" needs
   flex: 2 or an equivalent; "demonstrate the error" needs the attempt and its
   result). Read the code for what it does, not for how long it is. A syntax or logic
   slip that breaks one requirement loses that requirement's share, not the whole
   question. Output, comments and explanations earn marks only where the question
   asks for them. Code shown in a screenshot is read like pasted code.
5. A requirement is met only when the code, run as written, produces it. Finding the
   construct the key uses is not enough: read everything else in the learner's
   answer that acts on the same result. In CSS that is the cascade: a second media
   query whose range overlaps and sets the same property, a later rule for the same
   selector, a more specific selector or !important that overrides it, or the base
   rule placed AFTER the media query. In a program it is a variable reassigned before
   use, a return or break before the line, wrong indentation, or an error raised on an
   earlier line. When anything in the answer cancels or contradicts the required
   behaviour, that requirement earns nothing, and the remark quotes the conflicting
   line and says how to fix it. A base rule placed BEFORE the media query is correct,
   and so is a breakpoint the key itself uses (max-width: 600px for "less than
   600px"). MENTOR REVIEW (15 Oct 2026): a media query answer was paid in full for
   having the required media query while a conflicting media query in the same answer
   changed the result. Code that would not run as written meets nothing it was meant
   to do: SQL with a missing keyword (an INSERT with no VALUES), a table or column name
   that differs from the one it created, or a misplaced * earns nothing for the task it
   serves, though a correct design inside it (the right columns and types) keeps its
   own share. REVIEW (23 Oct 2026): an INSERT with no VALUES keyword, into a table
   named differently from the one created, got 7 of 10.
6. Read the complete solution in context before you deduct. A fault the answer deals
   with elsewhere is not a fault: an import or definition the code uses that sits
   higher up or on the next page, a value checked before it is used, a line corrected
   further down, an edge case handled in a later step, a tag closed on the next page.
   A remark never asks for something the answer already does. Equally, an error
   anywhere in the answer that affects what the question asks for (rule 5) costs that
   requirement, however good the rest looks. PW SKILLS TEAM (21 Oct 2026): errors in
   code were missed, and issues already addressed in the answer were flagged.
7. Wrong content is not cancelled by right content. When an answer mixes correct and
   incorrect parts (a wrong statement beside right ones, a wrong output or value, a
   wrong claim about what the code does, one wrong formula among right ones), each
   incorrect part loses the share of the requirement it belongs to, and the remark
   names it. Full marks only when every requirement is met AND nothing in the answer
   is wrong. This overrides the school rules that pay a brief or partly right answer
   in full. A result the learner states (a query's output rows, a computed total or
   average, a printed output, a value read off a chart) is part of the answer: check
   it against the data the paper gives, and a wrong stated result loses the result's
   share even when the query or formula is right, with the remark naming the wrong
   value and the right one. PW SKILLS TEAM (21 Oct 2026): answers with correct and
   incorrect content were given full marks. REVIEW (23 Oct 2026): five SQL answers
   with correct queries listed the wrong employees (64,000 shown as above an average of
   64,800) and were paid in full.
8. Tables, charts and screenshots are answers. A "[detected table]" line (its rows
   follow, cells joined by "|"), a "[detected figure]" line and a DIAGRAM REGION stand
   for content in the answer: read a figure from the page image (a chart's type, axes,
   labels and values; a screenshot's code or output) and mark it like typed text, with
   its marks on that line. Never call such an answer missing or not attempted because
   the transcript has no words for it, and never ask for labels or annotations the
   question does not ask for. In a printed or exported sheet, a column of computed
   values (Total Price beside Quantity and Unit Price) answers a "calculate" or "add a
   column" task when its values are right, even though the page shows values and not
   formulas, unless the question asks to show the formula itself. PW SKILLS TEAM (21
   Oct 2026): solutions given as tables or charts were missed unless the learner
   annotated them. SME REVIEW (22 Oct 2026): a Q5 total already computed in the data
   sheet was marked as "no formula shown".
9. Theory and non-tech answers (definitions, comparisons, case studies, plans,
   reports): the key's points are a guide to what a complete answer covers. A point
   earns its share when it is correct and explained or illustrated with an example; a
   correct point only named earns half its share; a valid, relevant point the key does
   not list earns the same as a listed one. A restatement of the question, or text that
   does not address the question asked, earns nothing. In a tech course a "how would
   you solve, fix or implement" question is answered by the how: the property, rule,
   formula or code that does it (a media query with its breakpoint and the layout
   change it makes). Naming the technique without how it is applied is a point only
   named. SME REVIEW (22 Oct 2026): "I would use media queries and Flexbox" for a
   squeezed mobile menu got full marks where the SME asked for the CSS.
10. Pages headed "Linked work N of M" are the learner's own files, opened from the
   learner's link or upload and converted to text by the checker: a notebook with the
   outputs the learner saved, a workbook with its formulas, settings and values, a
   Power BI or Tableau file with its formulas, fields and visuals. Mark them as the
   learner's answer, matched to the questions by what they answer. The headings and
   grey notes on those pages are the checker's, not the learner's. A notebook cell
   with no saved output was not run: judge the code, never invent its output. "(no
   formula saved in the file)" means the learner's file has no formula there: that
   requirement is not met. A workbook sheet's filters, frozen panes, header
   formatting and formulas are written out as facts: check them against what the
   question asks. A link that could not be opened (its own page says why) and a link
   given in place of an answer with no linked work shown earn nothing, and the remark
   says to share the file publicly or paste the answer. Where the learner pastes an
   answer and also links work, mark both together as one answer. A link listed as "a
   video" was not watched: the reviewer marks it. Give the requirement the video
   answers (a one-minute project summary, a walkthrough) no marks, with the remark
   that the reviewer will mark the video, never guess what it shows, and mark every
   other question from the rest of the work as usual.
11. A question whose heading is printed but whose answer space is empty is not
   attempted. Marks are whole or half marks.
12. Question-wise marks and feedback agree. A question's marks are the sum of its
   criteria, one criterion per requirement, and its remark accounts for every mark it
   lost: name each requirement not met, what was wrong or missing in the learner's own
   answer, and how to fix it (the bug and where it is, the point to add), the way a
   mentor's review does. A question with full marks gets a remark that names nothing
   wrong; a question that lost marks never gets a remark that only praises. PW SKILLS
   TEAM (21 Oct 2026): feedback did not always match the marks and did not say why
   marks were lost.
13. Count what each question asks for. A question that asks for two or more things
   (explain X and why Y, calculate and interpret, define and give an example, write
   the query and show its output, build the chart and state the insight) has one
   requirement per ask, sharing the question's marks equally unless the paper says
   otherwise. An example, code, a diagram or an output the question asks for is an ask
   of its own. An ask that is missing loses its whole share: a two-part question with
   the second part missing earns at most half. A result stated without its working,
   where the question or the assignment's instructions ask to show the calculation or
   the steps, earns at most half of that ask. An ask answered only in part, or only
   named, earns half its share. When the question asks to explain, compare, name a
   concept or say when to use one thing over another, code alone does not answer that
   ask: it can earn only the share of an example or a demonstration the question asks
   for. A requirement the answer does not attempt earns nothing, never part marks (code
   that does not use the construct asked about earns nothing for that construct).
   Never take a token half or one mark for a missing ask, and never take marks for
   depth or extras the question does not ask for. SME REVIEW (22 Oct 2026): a missing
   second part cost 2 marks of 10 where the SME took 5, standard deviations stated
   without the calculation cost 1.5 marks in all where the SME took 10, and a for...in
   versus for...of question answered with two loops and no explanation got 20 of 20.
   A practical task (create, build, apply, insert, calculate or plot, in Excel, Power
   BI, Tableau, SQL or a notebook) is answered by its result: the table, chart, values
   or output the learner produced, shown as a screenshot, a printed result or the
   learner's own file. Steps that say how to do it, with no result of the learner's
   own, earn at most half of that task, and a generic recipe that would fit any
   dataset (no figure, field value or output from this one) at most a quarter. Code or
   CSS fragments where the question asks for a complete page, file or program earn only
   the parts they really cover. REVIEW (23 Oct 2026): a Pivot Table assignment answered
   with "Implementation Steps" alone, no table, chart or total anywhere, got 100 of 100.
14. An answer can sit away from its heading. When the learner points to it ("refer
   sheet 2", "see the dashboard below", "answer in the attached workbook", "see page
   6"), or a later page, sheet, screenshot or linked file plainly answers a question (a
   dashboard after the last answer, a sheet named after the question), mark that
   content as that question's answer, with its marks on that content. Never call such
   a question missing or not attempted while the content it points to is in the
   submission. SME REVIEW (22 Oct 2026): Q8 said "refer sheet 2" and the dashboard on
   sheet 2 of the PDF was not checked.
15. A project brief lists deliverables, not numbered questions, and each marked
   question is one of them (its description in the marks table names it: data
   collection, EDA, model, report, HLD, LLD, pipeline, deployment). Look for each
   deliverable in every page and linked file (a notebook, a report or design PDF, app
   code) and mark it from the file that provides it. A deliverable a file provides is
   attempted, whatever the answer map or the page inventory says, and its marks go on
   that file's pages. REVIEW (23 Oct 2026): a project's pipeline document and its
   Streamlit app were linked and shown, and both deliverables were marked not attempted.
16. A submission that answers a different assignment (its questions are not this
   paper's: random variables answered for a descriptive statistics paper) earns nothing
   for any question, and every remark says so ("This work answers a different
   assignment"), never "Not attempted" beside the learner's own answers. REVIEW (23 Oct
   2026): a learner linked another assignment's notebook, and each of its answers was
   marked "Not attempted".
"""

INNOVATION = replace(
    SCHOOL,
    id="innovation",
    display_name="School of Innovation (tech and non-tech)",
    ui_hint="PW School of Innovation assignments, typed and uploaded as PDFs, for tech "
            "courses (full stack, data science, data analytics) and non-tech courses. "
            "Upload the assignment PDF as the question paper and its solutions PDF as "
            "the answer key. Learners' GitHub, Drive, Docs, Sheets, Colab and OneDrive "
            "links are opened, and notebooks, Excel, CSV, Power BI, Tableau and Word "
            "files can be uploaded as answer sheets. A link that cannot be opened, or a "
            "data export of many pages, goes to faculty review with the reason. "
            "Every copy goes to faculty review until the marking is calibrated.",
    # The class and subject fields ("FSD", "HTML & CSS") do not say School of
    # Innovation, so the operator picks this category.
    detection=DetectionRules(auto_detectable=False),
    prompt=replace(SCHOOL.prompt, extra_rules=INNOVATION_RULES),
    # Learners submit notebooks, workbooks, Power BI and Tableau files, or a cover PDF
    # with a GitHub or Drive link (PW Skills team's samples, 21 Oct 2026): the linked
    # work is opened and read (linked_work.py).
    intake=replace(SCHOOL.intake, typed_submissions=True, max_pages=40, linked_work=True),
    # The data analytics assignments print no marks at all (Descriptive Statistics,
    # Excel Basics, Pivot Table & Charts, DAX in Power BI): the mentors mark every
    # question out of an equal share of the total (10 x 10 = 100). MEASURED 22 Oct 2026:
    # the marks reader called each such question "unresolved" and no DA copy could be
    # graded at all.
    marks=replace(SCHOOL.marks, unprinted_convention=(
        "PW Skills course assignments. When the paper prints no marks for its questions "
        "(no marks tag, no 'each question carries N marks', no section marks), every "
        "top-level question carries an equal share of the official total: give each the "
        "same maximum, in whole or half marks as near equal as the total allows, adding "
        "up to the official total exactly. That IS this exam's allocation, so do not "
        "report it in allocation_issues. Count each numbered question or listed task "
        "once (Q1, Question 1, 1., a bulleted task the paper lists among its questions). "
        "Marks the paper prints anywhere always win over this rule.")),
    # Many assignments have no solutions file ("ANSWER SHEET NOT FOUND" for 6 of the 14
    # papers of the team's "All format sample" tab, 23 Oct 2026), and the form refused
    # them. With no key uploaded, a model answer key is written from the paper once
    # (teaching_key.py), checked by a second examiner, and saved with the results; an
    # uploaded solutions file still wins.
    answer_key=AnswerKeyPolicy(
        may_generate=True,
        examination=("a PW Skills course assignment (full stack development, data science, "
                     "data analytics or a non-tech course) that mentors review"),
        key_notes=(
            "For this assignment: code questions (HTML, CSS, JavaScript, Python, SQL, DAX, "
            "spreadsheet formulas) get working code in the model answer, with the output "
            "where the question asks for it. Give one step per requirement the question "
            "states, described by the behaviour or result to check (the three boxes sit "
            "side by side with the first twice as wide), never by one solution's class, "
            "variable or table names, and put other correct approaches in "
            "accepted_answers. Tool tasks (Excel, Power BI, Tableau, Jupyter) get one step "
            "per task the question lists, naming what the learner's file must show (the "
            "formula column, the chart type and its fields, the filter, the measure). An "
            "explain, compare or 'when would you use' question has its explanation as a "
            "step of its own, apart from any example it asks for.")),
    # A new kind of input (typed documents) with no faculty marks to calibrate
    # against yet: the AI marks are a first draft for the reviewer. Flip auto_release
    # once sample copies have been compared with the mentors' marks.
    review=ReviewPolicy(
        auto_release=False,
        review_reason="School of Innovation copies go to faculty review: the AI marks "
                      "are a first draft until they match the mentors' marks on sample "
                      "copies."),
    # 8 to 10 questions of 20 marks over up to 40 typed pages: the commerce tier.
    cost=CostPolicy(budget_inr=18.0),
    # An assignment belongs to a course (FSD, data science), not a class, so the
    # form has no class field for this category (user's decision, 14 Oct 2026).
    level_label="School of Innovation",
)
