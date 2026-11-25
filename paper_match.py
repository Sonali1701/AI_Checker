"""Does this answer booklet answer THIS question paper? A model decides; code checks.

MEASURED on the ICSE Class 9 SST batch of 2026-09-14: five of the seven booklets
uploaded against "Subjective Test-06" were other papers' scripts - four SST Test-08
and one Mathematics Test-08. Each was graded against the wrong key and died on an
unrelated structural gate after Rs.15-30 of calls. Had they got past those gates, a
student would have been handed near zero for work never marked against its own paper.

Recognising "this is a different test" is a judgement about meaning - Samudragupta is
not an answer about Megasthenes, Chinese travellers are not Mauryan archaeology, a
triangle proof is not Social Science - so a model makes it. A first version used
string rules (option-word matching, label regexes) and only ever worked on MCQ
answers written out in words; a booklet whose MCQs were letters only slipped through.

The model's verdict alone never blocks a copy, because a hallucinated "wrong paper"
would stop a student being marked at all. Code blocks only when the model says
different_paper with high confidence AND at least two of the student answers it
quotes as evidence are really on the booklet (checked against the OCR). Anything
less - unsure, low confidence, unverifiable quotes, or a failed call - grades the
copy as normal.

From 29 Oct 2026 (no faculty to review copies) a suspected copy is looked at again on
its page images by a second model (SheetLook, rejection_after_look). Two readings that
agree reject it; anything else grades it. The verified-evidence rules above decide
only when that look cannot be had.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from mathpix import readable_ocr_text

PAPER_CHECK_CLAUDE_MODEL = "claude-haiku-4-5"
PAPER_CHARS = 12000       # most school papers fit whole; a long CA paper is cut
BOOKLET_CHARS = 10000     # the first several answer pages are plenty to tell
MIN_PAPER_CHARS = 200     # a scanned paper with no text layer cannot be compared
# A paper whose text layer holds less than this per page prints its questions as
# pictures. MEASURED 2026-10-22 (Maharashtra HSC Book-keeping Unit Test-1): 207
# characters over two pages, the instructions note only. The check, shown no options,
# refused a copy whose answers were this paper's own options as "a different test".
MIN_PAPER_CHARS_PER_PAGE = 300
MIN_BOOKLET_CHARS = 80    # a near-blank booklet has nothing to compare
BLOCK_CONFIDENCE = 0.8
MIN_VERIFIED_MISMATCHES = 2
_QUOTE_MATCH = 0.85       # OCR and the model's copy may differ by a letter or two

PAPER_CHECK_SYSTEM_PROMPT = (
    "You check whether a student's answer booklet was written for a given question "
    "paper. You never grade, and you never judge whether an answer is correct."
)


class PaperMismatch(BaseModel):
    question: str = Field(
        default="",
        description="The question number on the uploaded paper, e.g. 'Q1'.")
    student_wrote: str = Field(
        default="",
        description="The student's answer, copied EXACTLY as it appears in the "
                    "booklet transcript.")
    paper_asks: str = Field(
        default="",
        description="What the uploaded paper asks or offers at that question, briefly.")


class PaperMatchVerdict(BaseModel):
    verdict: Literal["same_paper", "different_paper", "unsure"] = "unsure"
    booklet_kind: Literal["answer_sheet", "notes", "typed", "other", "unsure"] = Field(
        default="unsure",
        description="What the booklet is: answer_sheet (a student's answers to exam "
                    "questions), notes (study notes, copied text, definitions or a "
                    "diary with no answers to numbered questions), typed (printed or "
                    "typed text rather than handwriting), other (an unrelated document), "
                    "unsure.")
    kind_evidence: list[str] = Field(
        default_factory=list,
        description="Up to three booklet lines, copied EXACTLY from the transcript, "
                    "that show what the booklet is; empty for an answer sheet.")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Probability from 0 to 1 that the verdict is right.")
    sheet_heading: str = Field(
        default="",
        description="Any test name, number or subject written at the top of the "
                    "booklet, copied exactly; empty if none.")
    mismatches: list[PaperMismatch] = Field(default_factory=list)
    reason: str = Field(default="", description="One plain sentence for the teacher.")

    @model_validator(mode="before")
    @classmethod
    def _tolerate_drift(cls, data):
        """The proxy does not enforce response schemas; the parser is the contract."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw = re.sub(r"[^a-z]", "", str(data.get("verdict") or "").lower())
        data["verdict"] = ("different_paper" if raw.startswith(("different", "other", "wrong", "mismatch"))
                           else "same_paper" if raw.startswith(("same", "match"))
                           else "unsure")
        try:
            confidence = float(str(data.get("confidence") or 0).strip().rstrip("%"))
        except ValueError:
            confidence = 0.0
        if confidence > 1.0:          # "90" or "90%"
            confidence /= 100.0
        data["confidence"] = min(1.0, max(0.0, confidence))
        rows = data.get("mismatches")
        if not isinstance(rows, list):
            rows = []
        data["mismatches"] = [
            row if isinstance(row, (dict, PaperMismatch)) else {"student_wrote": str(row)}
            for row in rows if row
        ]
        for key in ("sheet_heading", "reason"):
            data[key] = str(data.get(key) or "")
        kind = re.sub(r"[^a-z]", "", str(data.get("booklet_kind") or "").lower())
        data["booklet_kind"] = ("answer_sheet" if kind.startswith(("answer", "sheet", "exam", "script"))
                                else "notes" if kind.startswith(("note", "study", "copied", "diary"))
                                else "typed" if kind.startswith(("typed", "print", "digital"))
                                else "other" if kind.startswith(("other", "unrelated", "irrelevant"))
                                else "unsure")
        quotes = data.get("kind_evidence")
        if not isinstance(quotes, list):
            quotes = [quotes] if quotes else []
        data["kind_evidence"] = [str(q) for q in quotes if q]
        return data


def build_paper_check_prompt(question_paper_text: str, booklet_transcript: str) -> str:
    """The question paper and the booklet transcript - never the answer key.

    Without the paper's text (its questions are printed as pictures) only what the
    booklet IS can be judged: the verdict stays unsure and booklet_kind still counts.
    """
    paper = str(question_paper_text or "").strip()
    booklet = str(booklet_transcript or "").strip()
    paper_cut = len(paper) > PAPER_CHARS
    booklet_cut = len(booklet) > BOOKLET_CHARS
    if not paper:
        return (
            "=== STUDENT ANSWER BOOKLET (handwriting OCR; spelling errors are OCR "
            "noise, judge meaning not spelling) ===\n"
            + booklet[:BOOKLET_CHARS]
            + ("\n[... later pages are not shown ...]" if booklet_cut else "")
            + "\n\nThe question paper's text is not available, so set verdict to unsure "
              "and confidence to your probability that booklet_kind is right.\n\n"
              "Say what the booklet IS, in booklet_kind: answer_sheet when it holds a "
              "student's answers to exam questions, even poor or partial ones; notes when "
              "it is study material, copied text, a list of definitions or a diary with no "
              "answers to numbered questions; typed when the transcript is printed or typed "
              "text rather than handwriting; other when it is an unrelated document (a form, "
              "ticket, letter, notice, timetable, advertisement, syllabus); unsure otherwise. "
              "For notes, typed or other, copy up to three booklet lines EXACTLY into "
              "kind_evidence. Copy any test name, number or subject written at the top of "
              "the booklet into sheet_heading."
        )
    return (
        "=== QUESTION PAPER (uploaded by the teacher) ===\n"
        + paper[:PAPER_CHARS]
        + ("\n[... the rest of the paper is not shown ...]" if paper_cut else "")
        + "\n\n=== STUDENT ANSWER BOOKLET (handwriting OCR; spelling errors are OCR "
          "noise, judge meaning not spelling) ===\n"
        + booklet[:BOOKLET_CHARS]
        + ("\n[... later pages are not shown ...]" if booklet_cut else "")
        + "\n\nDecide whether this booklet answers THIS question paper.\n\n"
        "same_paper: the answers address this paper's questions. Wrong, incomplete, "
        "out-of-order or partly attempted answers are normal and are NOT evidence of "
        "another paper: a student who picks a wrong option still picked one of THIS "
        "paper's options, and a weak answer to this paper's question is still an "
        "answer to it.\n"
        "different_paper: across several questions the answers clearly belong to a "
        "different paper - MCQ answers that are none of the options this paper prints "
        "for that question, written answers about topics this paper does not ask at "
        "all, a different subject altogether - often with a booklet headed by a "
        "different test name or number.\n"
        "unsure: too little is written, the transcript is unreadable, or the evidence "
        "is mixed.\n\n"
        "Say different_paper only when you can list at least two such answers. For "
        "each, copy the student's words EXACTLY as they appear in the booklet "
        "transcript, and say briefly what this paper asks at that question number. A "
        "heading alone is not enough; a heading together with mismatched answers is."
        + (" An answer to a question beyond the part of the paper shown above is not "
           "a mismatch." if paper_cut else "")
        + "\nCopy any test name, number or subject written at the top of the booklet "
          "into sheet_heading. confidence is your probability that the verdict is "
          "right.\n\n"
          "Also say what the booklet IS, in booklet_kind: answer_sheet when it holds a "
          "student's answers to exam questions, even poor or partial ones; notes when "
          "it is study material, copied text, a list of definitions or a diary with no "
          "answers to numbered questions; typed when the transcript is printed or typed "
          "text rather than handwriting; other when it is an unrelated document; unsure "
          "otherwise. For notes, typed or other, copy up to three booklet lines EXACTLY "
          "into kind_evidence."
    )


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", readable_ocr_text(str(text or "")).lower())


def _on_booklet(quote: str, lines: list[str]) -> bool:
    """True when the quoted words are really on the booklet (OCR lines, compacted)."""
    target = _compact(quote)
    if len(target) < 4:
        return False
    matcher = SequenceMatcher(None, "", target)     # target's index is built once
    size = len(target)
    for index, line in enumerate(lines):
        # A quote may run across one OCR line break.
        joined = line + (lines[index + 1] if index + 1 < len(lines) else "")
        if target in joined:
            return True
        if len(joined) < size - 1:
            continue
        for start in range(0, max(1, len(joined) - size + 1)):
            matcher.set_seq1(joined[start:start + size])
            if (matcher.quick_ratio() >= _QUOTE_MATCH
                    and matcher.ratio() >= _QUOTE_MATCH):
                return True
    return False


def _booklet_lines(ocr_pages) -> list[str]:
    return [_compact(getattr(line, "text", ""))
            for page in list(ocr_pages or [])
            for line in list(getattr(page, "lines", None) or [])]


def _answers_this_paper(row: PaperMismatch, question_paper_text: str) -> bool:
    """True when the quoted answer is one of THIS paper's printed options.

    MEASURED 2026-09-16: a booklet headed "Subjective Test - 08" but answered
    against Test-06 was refused as another paper's script, and the very evidence
    offered was "1 (B) Indika" and "(C) Tamid" - Test-06's own Q1 and Q2 answers,
    both correct. A heading is not evidence; an answer that names one of this
    paper's options is evidence AGAINST the model's verdict, so it never counts.
    """
    if not question_paper_text:
        return False
    from strict_grading import option_named_by_words, option_texts_from_paper

    number = re.search(r"(\d{1,3})", str(row.question or ""))
    options = option_texts_from_paper(question_paper_text)
    candidates = ([options[int(number.group(1))]]
                  if number and int(number.group(1)) in options
                  else list(options.values()))
    return any(option_named_by_words(row.student_wrote, choices)[0]
               for choices in candidates if choices)


_LATEX_WORD_RE = re.compile(r"\\[a-zA-Z]+")


def _math_compact(text: str) -> str:
    """Digits, letters and + - = / only, LaTeX commands removed: "\\frac{2+\\sqrt{5}}"
    and "2+ root 5" both keep "2+5"."""
    text = _LATEX_WORD_RE.sub("", str(text or "")).lower()
    return re.sub(r"[^0-9a-z+\-=/]", "", text)


def _longest_common(a: str, b: str) -> int:
    best = 0
    for i in range(len(a)):
        for j in range(i + best + 1, len(a) + 1):
            if a[i:j] in b:
                best = j - i
            else:
                break
    return best


def _restates_this_paper(row: PaperMismatch, question_paper_text: str) -> bool:
    """True when the student's quoted words repeat what THIS paper asks.

    MEASURED 2026-09-30: Kartik's Class 9 Maths copy was refused as another test
    with the evidence "Q9: the student wrote \\frac{2+\\sqrt{5}}{2-\\sqrt{5}} x ...,
    this paper asks Rationalize the denominator of \\frac{2+\\sqrt{5}}{2-\\sqrt{5}}":
    the student had copied this paper's own question. Six or more characters of
    formula in common, with a digit among them, is the same question.
    """
    said = _math_compact(row.student_wrote)[:40]
    if len(said) < 6:
        return False
    for asks in (row.paper_asks, question_paper_text):
        common = _longest_common(said, _math_compact(asks))
        if common >= 6 and any(ch.isdigit() for ch in said[:common + 6]):
            return True
    return False


def verified_mismatches(verdict: PaperMatchVerdict, ocr_pages,
                        question_paper_text: str = "") -> list[PaperMismatch]:
    """The model's evidence rows whose quoted student words are on the booklet.

    A row is dropped when the words are not really there (a hallucinated quote) or
    when they name an option THIS paper prints (the model misread the situation).
    """
    lines = _booklet_lines(ocr_pages)
    kept: list[PaperMismatch] = []
    seen: set[str] = set()
    for row in verdict.mismatches:
        key = _compact(row.student_wrote)
        if (key and key not in seen and _on_booklet(row.student_wrote, lines)
                and not _answers_this_paper(row, question_paper_text)
                and not _restates_this_paper(row, question_paper_text)):
            seen.add(key)
            kept.append(row)
    return kept


def paper_text_is_partial(question_paper_text: str, paper_pages: int) -> bool:
    """Whether the paper's text layer misses most of what its pages print."""
    return bool(paper_pages) and (
        len((question_paper_text or "").strip()) < MIN_PAPER_CHARS_PER_PAGE * int(paper_pages))


def booklet_copies_paper(ocr_pages, question_paper_text: str) -> bool:
    """Whether most of the booklet's lines are the question paper's own text: the
    paper itself, or a printout of it, uploaded as the copy."""
    paper = _compact(question_paper_text)
    if len(paper) < MIN_PAPER_CHARS:
        return False
    lines = [_compact(getattr(line, "text", ""))
             for page in list(ocr_pages or [])
             for line in list(getattr(page, "lines", None) or [])
             if len(str(getattr(line, "text", "") or "").split()) >= 3]
    lines = [line for line in lines if len(line) >= 12]
    return bool(lines) and sum(line in paper for line in lines) >= 0.5 * len(lines)


_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def booklet_script_mismatch(question_paper_text: str, booklet_text: str) -> str:
    """Why the booklet cannot be answers to this paper by its script alone, or "".

    A paper printed only in English (no Devanagari letter anywhere) answered in a
    booklet whose letters are mostly Devanagari is another subject's booklet: a
    bilingual paper prints Hindi too and never trips this.
    """
    paper, booklet = question_paper_text or "", booklet_text or ""
    if len(paper.strip()) < MIN_PAPER_CHARS or _DEVANAGARI_RE.search(paper):
        return ""
    hindi, latin = len(_DEVANAGARI_RE.findall(booklet)), len(_LATIN_RE.findall(booklet))
    if hindi < 200 or hindi < 2 * latin:
        return ""
    return ("This booklet is written in Hindi, but this question paper is printed only in "
            "English, so it may belong to another subject's test. A reviewer should check "
            "it before any marks are given.")


def typed_document_reason(ocr_pages) -> str:
    """The message a printed or typed upload is refused with, or "" for handwriting."""
    from mathpix import booklet_is_typed
    typed, printed, handwritten = booklet_is_typed(ocr_pages)
    if not typed:
        return ""
    return (f"This upload is a printed or typed document ({printed} printed lines, "
            f"{handwritten} handwritten), not a handwritten answer sheet. No marks were given.")


def not_answer_sheet_reason(verdict: PaperMatchVerdict, ocr_pages, labels_found: int,
                            typed_allowed: bool = False) -> str:
    """The message a booklet that is not an answer sheet is refused with, or "".

    The model's kind is acted on only with evidence: it must be sure, at least one
    of the lines it quotes must really be on the booklet, and the student must not
    have labelled answers with this paper's question numbers (two or more labels
    say it is an answer sheet, whatever the model thinks of its content). For a
    category whose submissions are typed documents (School of Innovation
    assignments), "typed" is what the booklet is meant to be, not a reason.
    """
    if verdict.booklet_kind not in ("notes", "other", "typed"):
        return ""
    if typed_allowed and verdict.booklet_kind == "typed":
        return ""
    # A model that also says the booklet answers THIS paper has contradicted itself;
    # and two located answers make it an answer sheet. MEASURED on 24 real copies
    # (2026-09-17): the weakest had two answers located, every other one three or more.
    if verdict.verdict == "same_paper":
        return ""
    if verdict.confidence < BLOCK_CONFIDENCE or labels_found >= 2:
        return ""
    lines = _booklet_lines(ocr_pages)
    quoted = [q for q in verdict.kind_evidence if _on_booklet(q, lines)]
    if not quoted:
        return ""
    what = {"notes": "study notes or copied text", "typed": "a typed or printed document",
            "other": "not an answer sheet"}[verdict.booklet_kind]
    shown = "; ".join(f"'{' '.join(q.split())[:60]}'" for q in quoted[:2])
    return (f"This upload looks like {what}, not answers to this paper (it says {shown}). "
            "Upload the student's handwritten answer sheet for this test. No marks were given.")


_SUBJECT_WORDS = {
    "physics": ("physics",), "chemistry": ("chemistry",), "biology": ("biology", "bio"),
    "maths": ("maths", "mathematics", "math"), "english": ("english",),
    "economics": ("economics",), "accountancy": ("accountancy", "accounts"),
    "history": ("history",), "geography": ("geography",), "hindi": ("hindi",),
    "computer": ("computer",), "business": ("business studies",),
}


def _subjects_named(text: str) -> set[str]:
    words = " ".join(str(text or "").lower().split())
    return {subject for subject, names in _SUBJECT_WORDS.items()
            if any(re.search(rf"\b{re.escape(n)}\b", words) for n in names)}


def other_subject_heading(verdict: PaperMatchVerdict, ocr_pages, subject: str) -> str:
    """The booklet's own heading when it names another subject than this test's.

    MEASURED 2026-10-03 (Parishram ISC): Khushmeet's Physics booklet, headed "Board
    Subjective test 05 (Physics)", was uploaded to the Chemistry test. The check said
    "different_paper at 0.98, the booklet is headed Physics" but only 1 of its 5 quoted
    answers was found on the page, so the copy was graded as Chemistry: 0/70 and held.
    The heading the model quotes is checked against the booklet's first lines, and the
    subjects are compared by name; nothing is refused on the model's word alone.
    """
    heading = " ".join(str(getattr(verdict, "sheet_heading", "") or "").split())
    ours = _subjects_named(subject)
    if not heading or not ours:
        return ""
    lines = _booklet_lines(ocr_pages)[:12]
    named = _subjects_named(heading)
    if named and not (named & ours) and _on_booklet(heading, lines):
        return heading
    # The model may quote the heading loosely; a subject word written in the
    # booklet's own first lines is the same evidence.
    first = " ".join(lines)
    named = _subjects_named(first)
    if named and not (named & ours) and _subjects_named(heading) & named:
        return heading
    return ""


def wrong_paper_reason(verdict: PaperMatchVerdict, ocr_pages,
                       question_paper_text: str = "", subject: str = "") -> str:
    """The message a copy is refused with, or "" to grade it as normal."""
    if verdict.verdict != "different_paper" or verdict.confidence < BLOCK_CONFIDENCE:
        return ""
    heading = other_subject_heading(verdict, ocr_pages, subject)
    if heading:
        return (f"This answer sheet is for another subject: it is headed '{heading[:60]}', "
                f"and this test is {subject}. Upload it under its own subject's test. "
                "No marks were given.")
    evidence = verified_mismatches(verdict, ocr_pages, question_paper_text)
    if len(evidence) < MIN_VERIFIED_MISMATCHES:
        return ""
    shown = "; ".join(
        (f"{row.question.strip()}: " if row.question.strip() else "")
        + f"the student wrote '{' '.join(row.student_wrote.split())[:50]}'"
        + (f", this paper asks {' '.join(row.paper_asks.split())[:80]}"
           if row.paper_asks.strip() else "")
        for row in evidence[:3])
    message = f"This answer sheet appears to be for a different test ({shown})."
    heading = " ".join(verdict.sheet_heading.split())
    # Repeat the heading only if it is really written near the top of the booklet.
    if heading and _on_booklet(heading, _booklet_lines(ocr_pages)[:12]):
        message += f" The sheet is headed '{heading[:60]}'."
    return message + (" Upload that test's question paper and answer key, then grade "
                      "this sheet again. No marks were given.")


# --- The second look: the pages themselves (29 Oct 2026) ---------------------------
#
# MEASURED on aichecker.log of 29 Oct 2026 (819 school copies, ten portal accounts):
# the text check above called 276 uploads another paper, notes, typed or an unrelated
# document, at 0.95 to 0.99, and 196 of them were graded all the same, because the
# code could not verify its evidence: an IRCTC ticket, a residence certificate form, a
# school notice, timetables, a syllabus and typed study guides have no "answers" to
# quote (0 of 0 found), and their printed numbering counted as this paper's question
# labels (18 on the form). 269 copies Mathpix read as printed were graded for the same
# labels; the text check called 177 of them something other than this paper's answers.
# With no faculty to review copies (user, 29 Oct 2026), every copy is now decided: a
# copy any reading suspects is looked at again, ON ITS PAGE IMAGES, by a different
# model that is not told what the first reading said, and it is rejected only when
# both agree. Disagreement, an unsure or failed look grades the copy as normal.
SHEET_LOOK_MODEL = "claude-sonnet-4-6"
LOOK_CONFIDENCE = 0.9
LOOK_PAGES = 4             # the first pages show what an upload is
LOOK_EDGE = 1400           # px, long edge of each student page sent
LOOK_PAPER_PAGES = 2       # paper page images, only when its text layer is missing
LOOK_PAPER_CHARS = 6000

SHEET_LOOK_SYSTEM_PROMPT = (
    "You look at an upload a student submitted for a test and say what it is. You never "
    "grade and you never judge whether an answer is correct."
)


class SheetLook(BaseModel):
    upload: Literal["answers_to_this_paper", "answers_to_another_paper",
                    "not_an_answer_sheet", "unsure"] = "unsure"
    handwritten: bool = Field(
        default=True,
        description="True when the student's answers are written by hand, on paper or on "
                    "a printed answer template.")
    document: str = Field(
        default="",
        description="What the upload is, in a few plain words, e.g. 'a railway ticket', "
                    "'a typed study guide', 'handwritten Biology answers'.")
    other_paper: str = Field(
        default="",
        description="For answers_to_another_paper: the subject or test those answers are "
                    "for, as the pages show it; empty otherwise.")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Probability from 0 to 1 that upload is right.")
    reason: str = Field(default="", description="One plain sentence.")

    @model_validator(mode="before")
    @classmethod
    def _tolerate_drift(cls, data):
        """The proxy does not enforce response schemas; the parser is the contract."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw = re.sub(r"[^a-z]", "", str(data.get("upload") or "").lower())
        data["upload"] = ("answers_to_another_paper" if "another" in raw or "other" in raw
                          else "not_an_answer_sheet" if raw.startswith("not") or "notan" in raw
                          else "answers_to_this_paper" if "this" in raw or raw.startswith("answers")
                          else "unsure")
        try:
            confidence = float(str(data.get("confidence") or 0).strip().rstrip("%"))
        except ValueError:
            confidence = 0.0
        if confidence > 1.0:
            confidence /= 100.0
        data["confidence"] = min(1.0, max(0.0, confidence))
        hand = data.get("handwritten", True)
        data["handwritten"] = (hand if isinstance(hand, bool)
                               else str(hand).strip().lower() not in ("false", "no", "0"))
        for key in ("document", "other_paper", "reason"):
            data[key] = str(data.get(key) or "")
        return data


def build_sheet_look_prompt(question_paper_text: str, *, paper_as_images: bool,
                            pages_shown: int, pages_total: int) -> str:
    """What the look is asked. The first reading's verdict is never shown to it."""
    paper = str(question_paper_text or "").strip()
    if paper_as_images:
        shown = "The question paper's first pages are the images after the student's pages."
    elif paper:
        shown = ("=== QUESTION PAPER (text) ===\n" + paper[:LOOK_PAPER_CHARS]
                 + ("\n[... the rest of the paper is not shown ...]" if len(paper) > LOOK_PAPER_CHARS else ""))
    else:
        shown = "The question paper is not available: judge only what the upload is."
    return (
        f"The first {pages_shown} image(s) are pages of the upload"
        + (f" (it has {pages_total} pages)" if pages_total > pages_shown else "")
        + ".\n\n" + shown + "\n\n"
        "Say what the upload is, in upload:\n"
        "answers_to_this_paper: a student's own answers to THIS paper's questions, written "
        "by hand on paper or on a printed answer template. Wrong, weak, partial, untidy, "
        "crossed out, photographed at an angle, dim, or answered in another language the "
        "paper allows are all normal. A student who copied out a question before answering "
        "it is still answering it.\n"
        "answers_to_another_paper: a student's handwritten answers to a different test: "
        "another subject, or questions of the same subject that this paper does not ask.\n"
        "not_an_answer_sheet: anything that is not a student's answers to an exam: a typed "
        "or printed document (study guide, worksheet, sample paper, marking scheme, "
        "solutions, this question paper itself), a form, ticket, certificate, letter, "
        "notice, timetable, advertisement or syllabus, a blank template, study notes or "
        "copied text with no answers to numbered questions, a photo of something else.\n"
        "unsure: the pages do not show it plainly.\n\n"
        "answers_to_another_paper and not_an_answer_sheet stop a student being marked, so "
        "say them only when the pages plainly show it. Set handwritten to whether the "
        "student's answers are written by hand. Say what the upload is in document, and "
        "for another paper the subject or test it answers in other_paper. confidence is "
        "your probability that upload is right."
    )


def first_read_flags(verdict: PaperMatchVerdict | None) -> bool:
    """Whether the text check suspects this copy: another paper, or not an answer sheet."""
    if verdict is None or verdict.verdict == "same_paper" or verdict.confidence < BLOCK_CONFIDENCE:
        return False
    return verdict.verdict == "different_paper" or verdict.booklet_kind in ("notes", "typed", "other")


def _plain(text: str, limit: int = 80) -> str:
    """A model's words fit for the comment a student reads: no semicolons or dashes."""
    words = re.sub(r"\s*[;–—]\s*|\s+-\s+", ", ", " ".join(str(text or "").split()))
    words = re.sub(r"(?<=\w)-(?=\w)", " ", words).strip(" .,'\"")
    return words[:limit].rsplit(" ", 1)[0] if len(words) > limit else words


def rejection_after_look(look: SheetLook | None, *, verdict: PaperMatchVerdict | None,
                         typed_reason: str = "", script: str = "") -> tuple[str, str] | None:
    """(code, message) when two independent readings agree that this copy must not be
    marked against this paper, or None to grade it.

    The first reading is the text check (or Mathpix's printed flags, or the booklet's
    script); the second is the look at the pages. One alone never rejects a copy.
    """
    if look is None or look.upload == "unsure" or look.confidence < LOOK_CONFIDENCE:
        return None
    first_other = (verdict is not None and verdict.verdict == "different_paper"
                   and verdict.confidence >= BLOCK_CONFIDENCE)
    first_not_sheet = first_read_flags(verdict) and verdict.booklet_kind in ("notes", "typed", "other")
    document = _plain(look.document)
    if look.upload == "not_an_answer_sheet" and (typed_reason or first_other or first_not_sheet):
        what = f" It looks like {document}." if document else ""
        return ("NOT_AN_ANSWER_SHEET",
                f"This upload is not an answer sheet for this test.{what} Upload the student's "
                "handwritten answer sheet for this test. No marks were given.")
    if look.upload == "answers_to_another_paper" and (first_other or first_not_sheet or script):
        other = _plain(look.other_paper, 60)
        return ("WRONG_QUESTION_PAPER",
                "This answer sheet appears to be for a different test"
                + (f" ({other})" if other else "") + ", not the questions of this paper. "
                "Upload it under its own test. No marks were given.")
    if typed_reason and not look.handwritten:
        # Mathpix's printed flags and the look agree the answers are typed or printed:
        # only a handwritten sheet is checked (operator's rule, 2026-09-17).
        return ("NOT_AN_ANSWER_SHEET",
                "This upload is a typed or printed document, not a handwritten answer sheet. "
                "Upload the student's handwritten answer sheet for this test. No marks were given.")
    return None
