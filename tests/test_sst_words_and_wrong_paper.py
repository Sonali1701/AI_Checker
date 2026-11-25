# -*- coding: utf-8 -*-
"""The ICSE Class 9 SST batch of 2026-09-14: every one of six booklets failed.

The paper was "Subjective Test-06" (Radiant Infinity ICSE 2027, 30 marks, Q1-Q5 MCQs
keyed B, C, B, C, C). Reading the booklets page by page showed two different problems.

  ONLY TWO WERE TEST-06 SCRIPTS, and both were blocked by MCQ reading:
    849bbe95  "1) Indica (b)", "2) Tamil (c)" ... five correct answers written as
              WORDS with the letter after them. The key-blind reader returned the
              words as chosen_option ("Indica", "25 years"), the Literal["A".."D"]
              rejected them, and the whole copy died on a pydantic ValidationError.
    39b55d4d  "Q3. [3B] Fold Mountain" (a 3 overwritten with B). The grading pass
              named no option ("?"), the key-blind reader clearly saw B, and the
              two-reader rule had nothing to agree with, so the copy was held.
    (A third shape, from 2db58186: the reader returned lowercase "b", which the
    Literal also rejected - one lowercase letter killed a copy.)

  FOUR WERE SST TEST-08 SCRIPTS and one a MATHEMATICS Test-08 script, uploaded
  against the Test-06 key: "Ans-1 (b) Samundragupta", "Q.1 ... (Pragya Prashasti)?
  / option (A) Samudragupta", "Mathematics - Subjectivetest-08". Nothing noticed,
  so each was graded against the wrong paper and died on an unrelated gate
  ("claimed surplus alternative has no located answer label", "low-confidence
  OCR") after Rs.15-30 of calls. A model now checks, right after OCR, whether the
  booklet answers this paper (paper_match.py); code blocks the copy only when the
  student answers the model quotes as evidence are really on the booklet.

Every OCR line below is transcribed from the real page images.
"""
from __future__ import annotations

import json
import os
import re
import sys
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import grader  # noqa: E402
import pipeline  # noqa: E402
from costs import record_usage_event  # noqa: E402
from exam_package import build_exam_package  # noqa: E402
from grader import (GradeReport, MarksItem, MarksScheme,  # noqa: E402
                    ObjectiveChoiceObservation, ObjectiveChoiceSheet, QuestionMark)
from mathpix import LineBox, PageOCR  # noqa: E402
from paper_match import (PaperMatchVerdict, PaperMismatch,  # noqa: E402
                         build_paper_check_prompt, wrong_paper_reason)
from strict_grading import (StrictGradeError, _choice_letter,  # noqa: E402
                            apply_official_objective_evidence,
                            option_named_by_words, option_texts_from_paper,
                            reconcile_report_to_package)

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


# The paper's own text layer, Q1-Q5 as PyMuPDF extracts it (one option per line,
# each question label on a line of its own), plus the start of Q6.
PAPER = """Foundation
Subjective Test-06
Q1
SECTION-A
PART-I (10 Marks) [Compulsory]
Multiple Type Question (1-5)
Which ancient account written by Megasthenes
gives a detailed description of the administration
and society of the Mauryan Empire?
[1 M]
(A) Arthashastra
(B) Indika
(C) Mudrarakshasa
(D) Deepavamsa
Q2
The Sangam literature was primarily composed
and compiled in which language during the three
ancient assemblies of Tamil poets?
[1 M]
(A) Sanskrit
(B) Prakrit
(C) Tamil
(D) Pali
Q3
Which of the following landforms is primarily
formed due to endogenic forces (internal earth
movements)?
[1 M]
(A) Delta
(B) Fold Mountain
(C) Sand Dune
(D) V-shaped Valley
Q4
Block Mountains are formed due to faulting.
What is the uplifted block between two adjacent
faults called?
[1 M]
(A) Graben
(B) Rift Valley
(C) Horst
(D) Alluvial Plain
Q5
What is the minimum age requirement for a
candidate to contest elections to the Lok Sabha
in India?
[1 M]
(A) 18 years
(B) 21 years
(C) 25 years
(D) 30 years
Q6
Fill in the blanks:
"""
KEY = "Answer Key\nQ1\n(B)\nQ2\n(C)\nQ3\n(B)\nQ4\n(C)\nQ5\n(C)\n"


def package():
    scheme = MarksScheme(total=5, items=[MarksItem(qid=f"Q{n}", max=1) for n in range(1, 6)])
    built = build_exam_package(
        exam_type="school", student_class="9", subject="SST", level="9", paper="",
        attempt="", applicable_law_date=None, total_marks=5, question_paper=b"paper",
        answer_key=b"key", key_source="upload", marks_scheme=scheme,
        scheme_source="text", question_paper_text=PAPER, answer_key_text=KEY)
    return built, scheme


def page_of(texts, confidence=None):
    return PageOCR(page=1, image_width=1000, image_height=1400, lines=[
        LineBox(f"P1L{i + 1}", text, (60, 40 + 40 * i, 900, 70 + 40 * i), 1,
                confidence=confidence)
        for i, text in enumerate(texts)])


JASKARAN_849BBE95 = [          # Test-06, all five correct, letters after the words
    "Name- Jaskaran singh sidhu", "6/9/2026 SST", "Section-A", "Part-I",
    "Multiple choice questions:-", "1) Indica (b)", "2) Tamil (c)",
    "3) Fold mountains (b)", "4) Horst (c)", "5) 25 years (c)", "6) Fill ups:-",
    "a) Dhanananda", "b) Sangam", "7)", "(Attempt any one)",
]
SHEET_39B55D4D = [             # Test-06, Q3's "3" overwritten with B
    "Section", "Q1. [B] Indikaa", "Q2 [C] Tamil", "Q3. [3B] Fold Mountain",
    "Q4. [C] Horst", "Q.5 [C] 25yrs", "Q.6", "a) Dhana Nanda",
]
FOREIGN = {                    # Test-08 scripts, and one Mathematics Test-08 script
    "2db58186": ["Name:- Vaibhav Sinha IX", "Ans-1 (b) Samundragupta",
                 "Ans-2 (b) Rajaraja I", "Ans-3 (b) Granite",
                 "Ans-4 (b) Dormant Volcano", "Ans-5 (b) Graben (Rift Valley)",
                 "Ans 6 a) Harishena", "b) Sabha"],
    "9e880c2b": ["Subjetive test-08", "SST Radiant infinity ICSE 2027",
                 "1 Chandragupta II Samudragupta (option b)", "2 Rajendra I (option c)",
                 "3 Granite (option b)", "4 Dormant (option b)",
                 "5 Graben (Rift valley) (option b)", "6a Kalidasa"],
    "ebdde34e": ["SECTION-A", "Part-I (10 Marks) [Compulsory]",
                 "Multiple Choice Questions (1-5)",
                 "Q.1 Which ........ inscription (Pragya Prashasti)?",
                 ": option (A) Samudragupta", "Q.2 The ---- Chola King?",
                 ": option (B) Rajaraja I", "Q.3 Which .... surface (Plutonic Rock)?",
                 ": option (A) Basalt", "Q.4 A volcano ---- as",
                 ": option (B) Darmant Volcano", "Q.5 Block ---- called?",
                 "= Option (B) Graben (Rift Valley).", "Q.6 Fill in the Blanks:"],
    "959781c9": ["Section-A", "Q1. (B) Samudragupta", "Q2. (B) Rajaraja I",
                 "Q3. (B) Granite", "Q4. (B) Dormant Volcano", "Q5. (B) Graben", "Q6.",
                 "a, Harishena"],
    "851add73": ["Mathematics - Subjectivetest-08", "Section-A",
                 "1 AD = DC (option A)", "2 All of them are true (option D)",
                 r"3 \( \triangle A B C \cong \triangle A E D \) (option C)",
                 r"4 \( \triangle A B C \cong \triangle P Q R \) (option D)",
                 r"5 \( \angle C A D=45^{\circ} \) (option D)"],
}


print("== the paper's options no longer swallow the next question's label ==")
options = option_texts_from_paper(PAPER)
check("option D of Q1 is 'Deepavamsa', not 'Deepavamsa\\nQ2'",
      options[1]["D"] == "Deepavamsa", repr(options[1].get("D")))
check("option D of Q2 is 'Pali'", options[2]["D"] == "Pali", repr(options[2].get("D")))
check("all five MCQs parse four options",
      all(len(options[n]) == 4 for n in range(1, 6)))

print("== a model's option letter is recovered in any spelling, words never pass ==")
for raw, want in [("B", "B"), ("b", "B"), ("(b)", "B"), ("[B]", "B"), ("B)", "B"),
                  ("option b", "B"), ("Option (B)", "B"), ("2", "B"),
                  ("B (STUDENT WROTE SAMUDRAGUPTA)", "B"), ("opt. D", "D"),
                  ("Indica", ""), ("25 years", ""), ("Deepavamsa", ""),
                  ("A and R both true", ""), ("B, C", ""), ("B/C", ""), ("?", "")]:
    check(f"_choice_letter({raw!r}) == {want!r}", _choice_letter(raw) == want,
          repr(_choice_letter(raw)))

print("== the reader's live replies no longer kill the sheet ==")
live_849 = {"items": [
    {"qid": "Q1", "chosen_option": "Indica", "page": 1, "y_fraction": 0.3, "clear": True},
    {"qid": "Q2", "chosen_option": "Tamil", "page": 1, "y_fraction": 0.32, "clear": True},
    {"qid": "Q3", "chosen_option": "Fold mountains", "page": 1, "y_fraction": 0.34, "clear": True},
    {"qid": "Q4", "chosen_option": "Horst", "page": 1, "y_fraction": 0.36, "clear": True},
    {"qid": "Q5", "chosen_option": "25 years", "page": 1, "y_fraction": 0.38, "clear": True},
]}
try:
    parsed = ObjectiveChoiceSheet.model_validate(live_849).items
    check("option WORDS as chosen_option validate (was a ValidationError)", True)
    check("the words move to written_answer and chosen_option is left empty",
          [(i.chosen_option, i.written_answer) for i in parsed][:2]
          == [("", "Indica"), ("", "Tamil")], [(i.chosen_option, i.written_answer) for i in parsed])
except Exception as exc:  # noqa: BLE001
    check("option WORDS as chosen_option validate (was a ValidationError)", False, repr(exc))
try:
    lower = ObjectiveChoiceSheet.model_validate({"items": [
        {"qid": f"Q{n}", "chosen_option": "b", "clear": True} for n in range(1, 6)]}).items
    check("lowercase 'b' becomes 'B' (was a ValidationError)",
          [i.chosen_option for i in lower] == ["B"] * 5)
except Exception as exc:  # noqa: BLE001
    check("lowercase 'b' becomes 'B' (was a ValidationError)", False, repr(exc)[:120])
try:
    odd = ObjectiveChoiceObservation.model_validate(
        {"qid": "Q1", "chosen_option": "(C)", "page": 0, "y_fraction": 1.7, "clear": "true"})
    check("out-of-range page/y and a string 'clear' are tolerated",
          (odd.chosen_option, odd.page, odd.y_fraction, odd.clear) == ("C", 1, 1.0, True))
except Exception as exc:  # noqa: BLE001
    check("out-of-range page/y and a string 'clear' are tolerated", False, repr(exc)[:120])

# The same live reply through the REAL Claude reader (only the network is stubbed).
calls = []


def fake_claude_read(token, *, task_id, model, request, filename, count):
    calls.append(request)
    return {"stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(live_849)}],
            "usage": {"input_tokens": 900, "output_tokens": 120}}


with patch.object(grader, "_claude_call", side_effect=fake_claude_read):
    try:
        read = grader.read_objective_choices(
            [b"png"], ["Q1", "Q2", "Q3", "Q4", "Q5"], google_token="t", task_id="t",
            options={f"Q{n}": options[n] for n in range(1, 6)})
    except Exception as exc:  # noqa: BLE001
        read = []
        print(f"  (reader raised {exc!r:.120})")
check("the Claude reader returns all five rows from the live reply",
      [i.written_answer for i in read] == ["Indica", "Tamil", "Fold mountains",
                                           "Horst", "25 years"])
check("the reader is called ONCE (a bad field used to trigger a paid retry too)",
      len(calls) == 1, len(calls))
prompt_text = json.dumps(calls[0]) if calls else ""
check("the reader is shown the printed options", "(B) Indika" in prompt_text)
check("the reader is never shown the answer key", "Answer Key" not in prompt_text)

print("== an answer written in words names its option ==")
for line, number, want in [
        ("1) Indica (b)", 1, "B"), ("2) Tamil (c)", 2, "C"),
        ("3) Fold mountains (b)", 3, "B"), ("4) Horst (c)", 4, "C"),
        ("5) 25 years (c)", 5, "C"), ("Q1. [B] Indikaa", 1, "B"),
        ("Q3. [3B] Fold Mountain", 3, "B"), ("Q.5 [C] 25yrs", 5, "C"),
        ("2) Tamil", 2, "C")]:
    check(f"{line!r} -> {want}", option_named_by_words(line, options[number])[0] == want,
          repr(option_named_by_words(line, options[number])))
check("digits decide between '21 years' and '25 years'",
      option_named_by_words("5) 21 yrs", options[5])[0] == "B")
check("words naming TWO options name neither",
      option_named_by_words("4) Graben (Rift Valley)", options[4])[0] == "")
check("words from another paper name nothing",
      option_named_by_words("Ans-1 (b) Samundragupta", options[1])[0] == "")

print("== 849bbe95: low-confidence OCR, letter and words agree -> scored, no reader ==")
pkg, _scheme = package()
report = GradeReport(total_score=0, max_total=5, overall_remarks="", questions=[
    QuestionMark(qid=f"Q{n}", score=0, max_score=1, page=1, y_fraction=0.2,
                 anchor_line_id=f"P1L{5 + n}") for n in range(1, 6)])
report.evidence_contract = True
report = reconcile_report_to_package(report, pkg)
try:
    apply_official_objective_evidence(report, pkg, [page_of(JASKARAN_849BBE95, 0.62)],
                                      question_paper_text=PAPER)
    check("all five scored from the page alone",
          [q.score for q in report.questions] == [1.0] * 5,
          [(q.qid, q.chosen_option, q.score) for q in report.questions])
except StrictGradeError as exc:
    check("all five scored from the page alone", False, str(exc))

print("== a word-only answer line with clean OCR is scored ==")
report = GradeReport(total_score=0, max_total=5, overall_remarks="", questions=[
    QuestionMark(qid=f"Q{n}", score=0, max_score=1, page=1, y_fraction=0.2,
                 anchor_line_id=f"P1L{n}") for n in range(1, 6)])
report = reconcile_report_to_package(report, pkg)
try:
    apply_official_objective_evidence(
        report, pkg, [page_of(["1) Indika", "2) Prakrit", "3) Fold Mountain", "4) Horst",
                               "5) 30 years"], 0.95)], question_paper_text=PAPER)
    check("words alone score right and wrong answers alike",
          [q.score for q in report.questions] == [1.0, 0.0, 1.0, 1.0, 0.0]
          and report.questions[1].remark == "Ans: C",
          [(q.chosen_option, q.score) for q in report.questions])
except StrictGradeError as exc:
    check("words alone score right and wrong answers alike", False, str(exc))

print("== 39b55d4d Q3: grading pass silent, key-blind reader + page OCR agree ==")


def q3_report(primary_q3):
    rows = []
    for n, choice in zip(range(1, 6), ["B", "C", primary_q3, "C", "C"]):
        rows.append(QuestionMark(qid=f"Q{n}", score=0, max_score=1, page=1, y_fraction=0.2,
                                 anchor_line_id=f"P1L{1 + n}", chosen_option=choice))
    built = GradeReport(total_score=0, max_total=5, overall_remarks="", questions=rows)
    built.evidence_contract = True
    return reconcile_report_to_package(built, pkg)


seen_b = {f"q{n}": ObjectiveChoiceObservation(qid=f"Q{n}", chosen_option=c, clear=True)
          for n, c in zip(range(1, 6), "BCBCC")}
report = q3_report("")
try:
    apply_official_objective_evidence(report, pkg, [page_of(SHEET_39B55D4D, 0.6)],
                                      visual_choices=seen_b, question_paper_text=PAPER)
    check("Q3 is scored B (correct) instead of holding the copy",
          report.questions[2].chosen_option == "B" and report.questions[2].score == 1.0)
    check("the whole copy scores 5/5", report.total_score == 5.0, report.total_score)
except StrictGradeError as exc:
    check("Q3 is scored B (correct) instead of holding the copy", False, str(exc))
seen_c = dict(seen_b, q3=ObjectiveChoiceObservation(qid="Q3", chosen_option="C", clear=True))
try:
    apply_official_objective_evidence(q3_report(""), pkg, [page_of(SHEET_39B55D4D, 0.6)],
                                      visual_choices=seen_c, question_paper_text=PAPER)
    check("a reader that DISAGREES with the page's own words still holds", False)
except StrictGradeError as exc:
    check("a reader that DISAGREES with the page's own words still holds",
          exc.code == "OBJECTIVE_ANSWER_UNRESOLVED", exc.code)
words_reader = dict(seen_b, q3=ObjectiveChoiceObservation.model_validate(
    {"qid": "Q3", "chosen_option": "Fold Mountain", "clear": True}))
report = q3_report("")
try:
    apply_official_objective_evidence(report, pkg, [page_of(SHEET_39B55D4D, 0.6)],
                                      visual_choices=words_reader, question_paper_text=PAPER)
    check("a reader that answers in WORDS is mapped through the printed options",
          report.questions[2].chosen_option == "B")
except StrictGradeError as exc:
    check("a reader that answers in WORDS is mapped through the printed options", False,
          str(exc))

print("== the second examiner may call a whole-question row 'criterion 0' ==")
# Build36, live, on the genuine Test-06 copy 39b55d4d: the MCQs now scored, then the
# second examiner's certificate was refused - "row 15 echoes criterion 0, expected
# -1" - for each 1-mark fill-in-the-blank, which the prompt prints as "#15 [Q6.a]
# whole question" with no criterion number at all.
from grader import AuditItem, Criterion, audit_completion_errors  # noqa: E402

echo_report = GradeReport(total_score=3, max_total=4, overall_remarks="", questions=[
    QuestionMark(qid="Q6.a", score=1, max_score=1, page=1, y_fraction=0.3),
    QuestionMark(qid="Q7.a", score=2, max_score=3, page=1, y_fraction=0.5, criteria=[
        Criterion(description="Ashokan edicts", awarded=1, max=1, met=True),
        Criterion(description="Sanchi Stupa", awarded=1, max=1, met=True),
        Criterion(description="Pillar capital", awarded=0, max=1, met=False)]),
])
rows_whole = [AuditItem(row=0, correct=1, qid="Q6.a", criterion=c) for c in (0, 1, -1)]
check("a whole-question row echoed as criterion 0, 1 or -1 is accepted",
      all(not any("echoes criterion" in e for e in audit_completion_errors(
          echo_report, [item], include_awards=True)) for item in rows_whole))
wrong_echo = AuditItem(row=1, correct=1, qid="Q7.a", criterion=2)
check("a criterion row echoing a DIFFERENT criterion is still refused",
      any("echoes criterion" in e for e in audit_completion_errors(
          echo_report, [wrong_echo], include_awards=True)))

print("== the paper check asks a model, with the paper and booklet only ==")
prompt = build_paper_check_prompt(PAPER, "[P1L1] Q1. (B) Samudragupta")
check("the prompt carries the question paper", "(B) Indika" in prompt)
check("the prompt carries the booklet transcript", "Samudragupta" in prompt)
check("wrong answers are explicitly NOT evidence of another paper",
      "NOT evidence of another paper" in prompt)
check("the model must quote the student's words exactly",
      "EXACTLY as they appear in the booklet" in prompt)
long_prompt = build_paper_check_prompt(PAPER * 60, "x " * 9000)
check("a long paper is cut, and the model is told so",
      "rest of the paper is not shown" in long_prompt
      and "beyond the part of the paper shown" in long_prompt)

print("== the verdict parser survives drift instead of killing the sheet ==")
for raw, want in [
        ({"verdict": "DIFFERENT", "confidence": "92%"}, ("different_paper", 0.92)),
        ({"verdict": "Different paper", "confidence": 90}, ("different_paper", 0.9)),
        ({"verdict": "same", "confidence": 0.7}, ("same_paper", 0.7)),
        ({"verdict": "no idea", "confidence": "high"}, ("unsure", 0.0)),
        ({}, ("unsure", 0.0))]:
    try:
        parsed = PaperMatchVerdict.model_validate(raw)
        check(f"{raw} -> {want}", (parsed.verdict, round(parsed.confidence, 2)) == want,
              (parsed.verdict, parsed.confidence))
    except Exception as exc:  # noqa: BLE001
        check(f"{raw} -> {want}", False, repr(exc)[:120])
loose = PaperMatchVerdict.model_validate(
    {"verdict": "different_paper", "confidence": 0.9, "mismatches": ["Samudragupta", None]})
check("a mismatch given as a bare string is kept as the quote",
      [row.student_wrote for row in loose.mismatches] == ["Samudragupta"])


def verdict(kind="different_paper", confidence=0.95, quotes=(), heading=""):
    return PaperMatchVerdict(
        verdict=kind, confidence=confidence, sheet_heading=heading,
        mismatches=[PaperMismatch(question=f"Q{i + 1}", student_wrote=quote,
                                  paper_asks="about the Mauryan Empire")
                    for i, quote in enumerate(quotes)])


print("== code blocks a copy only on evidence that is really on the booklet ==")
booklet_959 = [page_of(FOREIGN["959781c9"])]
real = verdict(quotes=["Samudragupta", "Rajaraja I", "Dormant Volcano"])
reason = wrong_paper_reason(real, booklet_959)
check("a confident verdict with quotes found on the booklet blocks the copy",
      reason.startswith("This answer sheet appears to be for a different test"), reason)
check("the message quotes the student", "the student wrote 'Samudragupta'" in reason, reason)
check("the message says what to do and that no marks were given",
      "Upload that test's question paper" in reason and reason.endswith("No marks were given."))
check("OCR-level spelling differences still verify (Samundragupta vs Samudragupta)",
      wrong_paper_reason(verdict(quotes=["Samudragupta", "Rajaraja I"]),
                         [page_of(FOREIGN["2db58186"])]) != "")
check("quotes the model INVENTED never block a copy",
      wrong_paper_reason(verdict(quotes=["Ashoka the Great", "Harappan seals"]),
                         booklet_959) == "")
check("one verified quote is not enough",
      wrong_paper_reason(verdict(quotes=["Samudragupta", "Harappan seals"]),
                         booklet_959) == "")
check("the same answer quoted twice counts once",
      wrong_paper_reason(verdict(quotes=["Samudragupta", "samudragupta"]),
                         booklet_959) == "")
check("low confidence never blocks",
      wrong_paper_reason(verdict(confidence=0.6, quotes=["Samudragupta", "Rajaraja I"]),
                         booklet_959) == "")
check("same_paper and unsure never block",
      wrong_paper_reason(verdict("same_paper", quotes=["Samudragupta", "Rajaraja I"]),
                         booklet_959) == ""
      and wrong_paper_reason(verdict("unsure", quotes=["Samudragupta", "Rajaraja I"]),
                             booklet_959) == "")
headed = [page_of(FOREIGN["9e880c2b"])]
reason = wrong_paper_reason(verdict(quotes=["Rajendra I", "Granite"],
                                    heading="Subjetive test-08"), headed)
check("a heading that is really on the sheet is repeated",
      "The sheet is headed 'Subjetive test-08'" in reason, reason)
reason = wrong_paper_reason(verdict(quotes=["Rajendra I", "Granite"],
                                    heading="Unit Test 3 Physics"), headed)
check("a heading the model invented is left out", "headed" not in reason, reason)

# LIVE 2026-09-16: a Test-06 booklet headed "Subjective Test - 08" was refused as
# another paper's script, and the evidence offered was "1 (B) Indika" and "(C) Tamid"
# - this paper's own Q1 and Q2 answers, both correct. A heading is not evidence.
headed_wrong = verdict(quotes=["1) Indica (b)", "2) Tamil (c)"],
                       heading="Subjective Test - 08")
own_answers = [page_of(["Subjective Test - 08"] + JASKARAN_849BBE95[5:7])]
check("an answer that names THIS paper's option is never wrong-paper evidence",
      wrong_paper_reason(headed_wrong, own_answers, PAPER) == "",
      wrong_paper_reason(headed_wrong, own_answers, PAPER)[:160])
check("...while a foreign answer still blocks",
      wrong_paper_reason(verdict(quotes=["Samudragupta", "Rajaraja I"]),
                         [page_of(FOREIGN["959781c9"])], PAPER) != "")

print("== both providers' calls parse a real-shaped reply ==")
reply = {"verdict": "different_paper", "confidence": 0.93,
         "sheet_heading": "", "reason": "Answers are about the Guptas and Cholas.",
         "mismatches": [{"question": "Q1", "student_wrote": "Samudragupta",
                         "paper_asks": "Megasthenes' account: Indika etc."}]}
sent = []


def fake_claude(token, *, task_id, model, request, filename, count):
    sent.append(request)
    return {"content": [{"type": "text", "text": "Here you go:\n" + json.dumps(reply)}],
            "usage": {"input_tokens": 3000, "output_tokens": 150}}


usage = {}
with patch.object(grader, "_claude_call", side_effect=fake_claude):
    got = grader.check_paper_match(PAPER, "[P1L1] Q1. (B) Samudragupta", google_token="t",
                                   task_id="t", usage_out=usage)
check("the Claude call returns the verdict (prose around the JSON tolerated)",
      got.verdict == "different_paper" and got.mismatches[0].student_wrote == "Samudragupta")
check("the Claude call uses the cheap model and a JSON schema",
      sent and sent[0]["output_config"]["format"]["type"] == "json_schema")
check("the Claude call is billed to the paper_check stage",
      [event["stage"] for event in usage.get("_events", [])] == ["paper_check"])
check("the Claude call never sees the answer key", "Answer Key" not in json.dumps(sent))

print("== the pipeline asks BEFORE any grading call, and fails open ==")


class ReachedGrading(Exception):
    """The copy went on to be graded."""


def strict_cfg(paper_check=False):
    built, scheme = package()
    return built, scheme, pipeline.ProviderConfig(
        provider="claude", model="claude-haiku-4-5", google_token="test",
        task_id="sst-task", exam_type="school", grading_contract="exam_package_v2",
        exam_package=built, paper_check=paper_check)


def sheet_pdf(texts):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i, text in enumerate(texts):
        page.insert_text((60, 60 + 24 * i), text.encode("ascii", "replace").decode())
    data = doc.tobytes()
    doc.close()
    return data


def run_paper_check(texts, check_result, *, paper_check=True):
    """(error code or 'graded', grading calls, paper-check calls) for one booklet."""
    _, scheme, cfg = strict_cfg(paper_check)
    asked = []

    def ask(paper, booklet, **kwargs):
        asked.append((paper, booklet, kwargs["model"]))
        record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                                                 "input_tokens": 3000, "output_tokens": 150},
                           stage="paper_check")
        if isinstance(check_result, Exception):
            raise check_result
        return check_result

    with patch.object(pipeline, "ocr_all_pages", return_value=[page_of(texts)]), \
         patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
         patch.object(pipeline, "claude_check_paper", side_effect=ask), \
         patch.object(pipeline, "claude_grade", side_effect=ReachedGrading()) as grade:
        try:
            pipeline.grade_sheet(
                qp_pngs=[], sa_bytes=sheet_pdf(texts), filename="copy.pdf", ak_pngs=[],
                ak_text=KEY, marks_scheme=scheme, cfg=cfg, student_class="9",
                subject="SST", total_marks=5, use_mathpix=True, qp_text=PAPER)
            outcome = "finished"
        except ReachedGrading:
            outcome = "graded"
        except Exception as exc:  # noqa: BLE001
            outcome = getattr(exc, "code", type(exc).__name__)
        return outcome, grade.call_count, asked


outcome, graded, asked = run_paper_check(FOREIGN["959781c9"], real)
check("a proven foreign booklet is refused as WRONG_QUESTION_PAPER",
      outcome == "WRONG_QUESTION_PAPER", outcome)
check("...before any grading call (no money spent past OCR and the check)", graded == 0)
check("...after exactly one text-only check on the cheap model",
      len(asked) == 1 and asked[0][2] == "claude-haiku-4-5", asked[:1])
check("the check is given the paper and the booklet transcript",
      asked and "(B) Indika" in asked[0][0] and "Samudragupta" in asked[0][1])
outcome, graded, _ = run_paper_check(
    FOREIGN["959781c9"], verdict(quotes=["Ashoka the Great", "Harappan seals"]))
check("invented evidence: the copy is graded as normal",
      outcome == "graded" and graded == 1, outcome)
outcome, graded, _ = run_paper_check(JASKARAN_849BBE95, verdict("same_paper"))
check("same_paper: the copy is graded as normal", outcome == "graded", outcome)
outcome, graded, _ = run_paper_check(JASKARAN_849BBE95, RuntimeError("proxy 502"))
check("a failed check never costs the student their marks: graded as normal",
      outcome == "graded", outcome)
outcome, graded, asked = run_paper_check(FOREIGN["959781c9"], real, paper_check=False)
check("a hand-built config (tests, tools) never calls the model",
      outcome == "graded" and asked == [], (outcome, asked))
server_source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "server.py"), encoding="utf-8").read()
check("production turns the check on together with the strict contract",
      bool(re.search(r'grading_contract="exam_package_v2",(?:\s*#[^\n]*\n)*\s*paper_check=True',
                     server_source)))

print("== the pipeline settles a row the grading pass left blank ==")


def run_objective_pipeline(primary, first_read, second_read, texts, confidence=0.6):
    """Drive grade_prepared up to and through the objective stage."""
    built, scheme, cfg = strict_cfg()
    pages = [page_of(texts, confidence)]
    readers = {"first": 0, "second": 0}

    def grade(**kwargs):
        record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": cfg.model,
                                                 "input_tokens": 9000, "output_tokens": 900},
                           stage="primary")
        rows = []
        for n, choice in zip(range(1, 6), primary):
            line = next((line.line_id for line in pages[0].lines
                         if line.text.lstrip("QAns.-").startswith(str(n))), "")
            rows.append(QuestionMark(qid=f"Q{n}", score=0, max_score=1, page=1,
                                     y_fraction=0.1 * n, anchor_line_id=line,
                                     chosen_option=choice, objective=True))
        return GradeReport(total_score=0, max_total=5, overall_remarks="", questions=rows)

    def reader(key, letters):
        def read(pngs, qids, **kwargs):
            readers[key] += 1
            record_usage_event(kwargs["usage_out"], {
                "provider": "claude",
                "model": kwargs["model"], "input_tokens": 1500, "output_tokens": 200},
                stage="objective_verify")
            return [ObjectiveChoiceObservation.model_validate(
                {"qid": qid, "chosen_option": letter, "clear": bool(letter)})
                for qid, letter in zip(qids, letters)]
        return read

    # Both key-blind readers are Claude now; they are told apart by model.
    first_model, second_model = pipeline._objective_reader_models(cfg.model)
    assert len({cfg.model, first_model}) == 2 and first_model != second_model
    read_first, read_second = reader("first", first_read), reader("second", second_read)

    def read_by_model(pngs, qids, **kwargs):
        return (read_first if kwargs["model"] == first_model else read_second)(pngs, qids, **kwargs)

    with patch.object(pipeline, "ocr_all_pages", return_value=pages), \
         patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
         patch.object(pipeline, "claude_grade", side_effect=grade), \
         patch.object(pipeline, "claude_read_objectives", side_effect=read_by_model):
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        png = doc[0].get_pixmap().tobytes("png")
        doc.close()
        try:
            out = pipeline.grade_prepared(
                qp_pngs=[], sa_pngs=[png], ak_pngs=[], ak_text=KEY, marks_scheme=scheme,
                cfg=cfg, filename="sst.pdf", student_class="9", subject="SST",
                use_mathpix=True, total_marks=5, qp_text=PAPER)
            return out["report"], readers, None
        except StrictGradeError as exc:
            return None, readers, exc


# 39b55d4d exactly: primary says "?" for Q3, the first reader says B.
graded, readers, error = run_objective_pipeline(
    ["B", "C", "", "C", "C"], ["B", "C", "B", "C", "C"], ["B", "C", "B", "C", "C"],
    SHEET_39B55D4D)
check("39b55d4d grades 5/5 instead of holding on Q3",
      error is None and graded is not None and graded.total_score == 5.0,
      f"{error.code}: {error}" if error else (graded.total_score if graded else None))
check("...on the first key-blind read alone (the page OCR agreed)",
      readers == {"first": 1, "second": 0}, readers)

# Nothing on the page (a tick-box sheet), grading pass silent on Q2: both key-blind
# reads agree -> settled; one read alone would not be enough.
blank_page = ["Section A"]
graded, readers, error = run_objective_pipeline(
    ["B", "", "B", "C", "C"], ["B", "C", "B", "C", "C"], ["B", "C", "B", "C", "C"], blank_page)
check("two agreeing key-blind reads settle a row the grading pass left blank",
      error is None and graded is not None
      and graded.questions[1].chosen_option == "C" and graded.total_score == 5.0,
      f"{error.code}: {error}" if error else [(q.qid, q.chosen_option) for q in graded.questions])
check("...and it took the second read to do it", readers == {"first": 1, "second": 1}, readers)
graded, readers, error = run_objective_pipeline(
    ["B", "", "B", "C", "C"], ["B", "C", "B", "C", "C"], ["B", "A", "B", "C", "C"], blank_page)
# POLICY 2026-09-16: an unreadable box is a note on the copy, not the loss of every
# other mark on it. Nothing on this page could be read and the readers disagree, so
# the row falls back to the examiners' own reading and is flagged for the teacher.
check("readers that DISAGREE no longer destroy the copy",
      error is None and graded is not None,
      f"{error.code}: {error}" if error else "")
check("...the unconfirmed row is named for the teacher",
      graded is not None and graded.objective_unconfirmed_qids == ["Q2"],
      graded.objective_unconfirmed_qids if graded else None)
check("...and the rest of the copy keeps its marks",
      graded is not None and graded.total_score == 5.0,
      graded.total_score if graded else None)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all checks passed")
