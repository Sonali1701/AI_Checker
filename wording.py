# -*- coding: utf-8 -*-
"""Plain teacher wording for everything a student reads on an evaluated copy.

The reviewer's rule (2026-09-16): no semicolons and no dashes anywhere on the copy,
because they read as machine output. A remark is short plain sentences, the way a
teacher writes in a margin: "Points are only named, not explained. Write one or two
lines on each point."

`plain_sentences` is applied to every question remark and to the overall remarks
before rendering, and `wording_problems` lets a test or a preflight check prove the
rule held.
"""
from __future__ import annotations

import re

_DASHES = ("‒", "–", "—", "―", "−")
# A dash used as punctuation, with space on at least one side ("named - not").
_DASH_PUNCT_RE = re.compile(r"\s+[-‒–—―−]+\s+|\s+[-‒–—―−]+(?=\S)|(?<=\S)[-‒–—―−]+\s+")
# A hyphen inside a word ("U-shaped", "flat-topped", "well-organised").
_HYPHEN_IN_WORD_RE = re.compile(r"(?<=\w)-(?=\w)")
# A numeric range "1-2 lines" -> "1 to 2 lines" (matched before the hyphen rule).
_NUM_RANGE_RE = re.compile(r"(?<=\d)\s*-\s*(?=\d)")
# A minus sign is maths, not punctuation. MEASURED 2026-09-29 (Core Maths Q13): the
# grader wrote "should be 1, not \u22121" and the dash rule turned it into "should be
# 1, not, 1", a remark that contradicts itself. U+2212 is always a minus; an ASCII
# hyphen is one when it stands before a number with no word in front of it.
_MINUS_SIGN_RE = re.compile("\u2212\\s*")
# A capital matrix or set counts too: "B transpose = -B" read "= , B" (MP Board Core
# Maths Q3, 22 Oct 2026).
_UNARY_MINUS_RE = re.compile(r"(?:(?<=^)|(?<=[\s(=,:]))-(?=\d|[a-zA-Z]\d*(?!\w))")
# A hyphen between two algebra terms is a minus too. KVS PRT Mock 01 (2026-10-10):
# "instead of 2d-c" reached the student as "instead of 2d c". Lower case terms only,
# so "Q1-Q3", "U-shaped" and "x-axis" keep their meaning.
_TERM = r"(?:\d+[a-z]{0,2}|[a-z]\d*)"
_TERM_MINUS_RE = re.compile(rf"(?<![\w.]){_TERM}(?:-{_TERM})+(?![\w])")


# A spreadsheet formula is code: its minus signs are maths. MEASURED 23 Oct 2026 (PW
# Skills, Data Cleaning Q8): the grader wrote "Use =A1-TODAY()" and the learner read
# "Use =A1 TODAY()". A token that starts with "=" and a cell, function or bracket is
# kept as written; trailing punctuation stays outside it.
_FORMULA_RE = re.compile(r"(?<!\S)=[A-Za-z$(][^\s]*?(?=[.,;:!?)]*(?:\s|$))")
# An em dash or a horizontal bar between words is punctuation even with no space round
# it ("feature engineering—those sections" read "feature engineering those sections").
_WORD_DASH_RE = re.compile("(?<=[^\\d\\s])\\s*[—―]\\s*(?=[^\\d\\s])")


def _algebra_minus(value: str) -> str:
    maths = bool(re.search(r"[+=×÷^<>]", value))

    def chain(match: "re.Match[str]") -> str:
        text = match.group(0)
        if maths or any(ch.isdigit() for ch in text):
            return text.replace("-", " minus ")
        return text
    return _TERM_MINUS_RE.sub(chain, value)
# Prompt-style labels that are not sentences ("Ans: C", "Key: Tamil").
_ANS_RE = re.compile(r"^\s*Ans(?:wer)?\s*[:.]?\s*\(?([A-Da-d])\)?\s*\.?\s*$")


def _capitalise(sentence: str) -> str:
    sentence = sentence.strip()
    if not sentence:
        return sentence
    # Do not touch an identifier like "eV" or a quoted figure; only a lower-case
    # letter that starts a plain word gets a capital.
    if sentence[0].islower() and (len(sentence) == 1 or not sentence[1].isupper()):
        return sentence[0].upper() + sentence[1:]
    return sentence


def plain_sentences(text: str | None) -> str:
    """Rewrite a remark as plain sentences with no semicolons or dashes.

    * ";" ends the sentence: "named; not explained" -> "Named. Not explained."
    * a dash used as punctuation becomes a comma (mid clause) or a full stop
      (between two complete clauses is not detectable, so a comma is used):
      "Only two points - third missing" -> "Only two points, third missing."
    * "1-2" -> "1 to 2"; "U-shaped" -> "U shaped"
    * "Ans: C" -> "Correct answer is (C)."
    * every sentence starts with a capital and the remark ends with a full stop.
    """
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    m = _ANS_RE.match(value)
    if m:
        return f"Correct answer is ({m.group(1).upper()})."
    formulas: list[str] = []

    def hold(match: "re.Match[str]") -> str:
        formulas.append(match.group(0))
        return f"\ue000{len(formulas) - 1}\ue001"
    value = _FORMULA_RE.sub(hold, value)
    value = _WORD_DASH_RE.sub(", ", value)
    value = _TOOL_PHRASE_RE.sub("", value)
    value = _greek_words(value)
    value = _MINUS_SIGN_RE.sub(" minus ", value)
    # A range written with an en dash ("0–4 seconds") is a range too; dashes become
    # hyphens first, or the hyphen rule below left "0 4 seconds" (teaching review,
    # 22 Oct 2026, PGT Physics Q2).
    for d in _DASHES[:4]:
        value = value.replace(d, "-")
    value = _NUM_RANGE_RE.sub(" to ", value)
    value = _UNARY_MINUS_RE.sub("minus ", value)
    value = _algebra_minus(value)
    for d in _DASHES:
        value = value.replace(d, "-")
    value = _DASH_PUNCT_RE.sub(", ", value)
    value = _HYPHEN_IN_WORD_RE.sub(" ", value)
    value = value.replace("-", " ")
    value = value.replace(";", ".")
    value = re.sub(r"\s+([,.!?।])", r"\1", value)
    value = re.sub(r",\s*,", ",", value)
    value = re.sub(r"\.\s*\.", ".", value)
    # A Hindi sentence ends with a danda: "है।" was printed "है।." on every Hindi
    # remark (EMRS PGT Hindi cover, 22 Oct 2026).
    value = re.sub(r"([।॥])\s*\.", r"\1", value)
    value = " ".join(value.split())
    parts = [p for p in re.split(r"(?<=[.!?।॥])\s+", value) if p.strip()]
    parts = [_capitalise(p) for p in parts]
    out = " ".join(parts).strip()
    if out and out[-1] not in ".!?।॥":
        out += "."
    return re.sub("\ue000(\\d+)\ue001", lambda m: formulas[int(m.group(1))], out)


# Words the student cannot read as feedback: the grader's own view of its input.
# "No answer to Q8 appears on the supplied pages" (teaching review, 22 Oct 2026).
_TOOL_PHRASE_RE = re.compile(
    r"\s*\b(?:anywhere\s+)?(?:in|on)\s+(?:the\s+)?(?:supplied|provided|given)\s+(?:pages?|images?|booklet)\b",
    re.IGNORECASE)
# The copy's fonts have no Greek letters: "½Iω²" printed with a box for omega and
# "Z − σ" as "Z - s" (teaching review, 22 Oct 2026). A teacher writes the name.
_GREEK_WORDS = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "Δ": "delta", "ε": "epsilon",
    "η": "eta", "θ": "theta", "λ": "lambda", "μ": "mu", "ν": "nu", "π": "pi", "ρ": "rho",
    "σ": "sigma", "Σ": "sum", "τ": "tau", "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi",
    "ω": "omega", "Ω": "ohm",
}


def _greek_words(value: str) -> str:
    out = []
    for i, ch in enumerate(value):
        word = _GREEK_WORDS.get(ch)
        if word is None:
            out.append(ch)
            continue
        before = " " if i and value[i - 1].isalnum() else ""
        after = " " if i + 1 < len(value) and value[i + 1].isalpha() else ""
        out.append(f"{before}{word}{after}")
    # "Z_eff" is how a grader types a subscript; a student reads "Z eff".
    return re.sub(r"(?<=\w)_(?=\w)", " ", "".join(out))


def wording_problems(text: str | None) -> list[str]:
    """Why a student-facing text breaks the wording rule, or [] when it is clean."""
    value = _FORMULA_RE.sub("", str(text or ""))      # a formula's minus is maths
    problems = []
    if ";" in value:
        problems.append("semicolon")
    if "-" in value or any(d in value for d in _DASHES):
        problems.append("dash")
    return problems


# The marking rules and the checker's reasoning are for the examiner, never for the
# student. Bihar Board SMEs (21 Oct 2026) could not understand "One-line explanation
# receives two-thirds treatment under the Bihar Board rule" or "explicit answer text
# controls the conflicting letter".
_RULE_TALK_RE = re.compile(
    r"(?i)\b(?:bihar\s+board|board\s+rule|(?:one|two)[\s-]thirds?\s+treatment|treatment\s+under"
    r"|under\s+the\s+[\w\s]{0,30}?rules?\b|(?:commerce|marking|faculty)\s+rules?\b|rule\s+\d+"
    r"|explicit\s+(?:answer|written)|controls\s+the\s+conflicting|conflicting\s+letter"
    r"|second\s+examiner|rubric|criterion|criteria)")
_EXPLAIN_RE = re.compile(r"(?i)explain|explanation|one[\s-]line|bare|points?\b")


def strip_rule_talk(text: str | None) -> str:
    """The remark without its sentences about rules or the checker's reasoning; when
    nothing else is left, a plain instruction ("Explain each point in one or two
    lines.") or nothing."""
    text = str(text or "").strip()
    if not text or not _RULE_TALK_RE.search(text):
        return text
    parts = [p.strip() for p in re.split(r"(?<=[.!?;])\s+|;\s*", text) if p.strip()]
    kept = [p.rstrip(";").strip() for p in parts if not _RULE_TALK_RE.search(p)]
    if kept:
        return " ".join(k if k.endswith((".", "!", "?")) else k + "." for k in kept)
    return "Explain each point in one or two lines." if _EXPLAIN_RE.search(text) else ""


# What a grader writes for a question with no answer, in its own words: "No answer to
# Q8 appears on the supplied pages", "No Q15 response is present", "Question 2 has no
# answer". A teacher writes "Not attempted" (teaching review, 22 Oct 2026).
_ABSENT_RE = re.compile(
    r"(?i)^\s*(?:(?:no|not)\b.{0,80}\b(?:answer|attempt\w*|response|written)\b"
    r"|question\s+\d+\w*\s+has\s+no\s+answer|not\s+attempted)")
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def plain_report(report) -> None:
    """Apply the rule to every remark on a GradeReport, in place."""
    questions = list(getattr(report, "questions", None) or [])
    # The copy's language is the language of its answered questions' remarks.
    answered = [str(getattr(q, "remark", "") or "") for q in questions
                if bool(getattr(q, "attempted", True))]
    hindi = sum(bool(_DEVANAGARI_RE.search(r)) for r in answered)
    english = sum(bool(re.search(r"[A-Za-z]{3}", r) and not _DEVANAGARI_RE.search(r)) for r in answered)
    for q in questions:
        remark = getattr(q, "remark", "")
        if (remark and not bool(getattr(q, "attempted", True))
                and float(getattr(q, "max_score", 0) or 0) > 0
                and float(getattr(q, "score", 0) or 0) <= 0 and _ABSENT_RE.search(remark)):
            q.remark = "उत्तर नहीं लिखा गया।" if hindi > english else "Not attempted."
            continue
        if remark:
            q.remark = plain_sentences(strip_rule_talk(remark))
    overall = getattr(report, "overall_remarks", "")
    if overall:
        report.overall_remarks = plain_sentences(strip_rule_talk(overall))


_MCQ_LETTER_RE = re.compile(r"^\s*(?:Ans(?:wer)?\s*[:.]?\s*|Correct answer is\s*)\(?([A-Da-d])\)?\s*[.:]?\s*(.*)$", re.IGNORECASE)


_LETTER_TO_DIGIT = {"A": "1", "B": "2", "C": "3", "D": "4"}


def paper_numbers_options(paper_text: str | None) -> bool:
    """True when the paper labels MCQ options (1) (2) (3) (4) rather than (A)-(D)."""
    text = str(paper_text or "")
    numbered = len(re.findall(r"\(\s*[1-4]\s*\)", text))
    lettered = len(re.findall(r"\(\s*[A-Da-d]\s*\)", text))
    return numbered >= 8 and numbered > 2 * lettered


def mcq_remark(letter: str, options: dict | None, numbered: bool = False) -> str:
    """'Correct answer is (C) 25 years.' when the paper's option text is known.

    On a paper that numbers its options the label is the number the student sees:
    "(3) 28 m", never "(C) 28 m" (Applied Maths Q5, 2026-09-29).
    """
    letter = (letter or "").strip().upper()[:1]
    label = _LETTER_TO_DIGIT.get(letter, letter) if numbered else letter
    text = ""
    if options:
        text = str(options.get(letter) or options.get(letter.lower()) or "").strip()
    text = " ".join(text.split()).rstrip(" .")
    if len(text) > 60:
        # A long option cut to fit read "situated in Ind." once the plain-wording
        # pass turned "..." into a full stop (CA Corporate Law, 2026-09-19). The
        # letter alone is what a teacher writes.
        text = ""
    return f"Correct answer is ({label}) {text}." if text else f"Correct answer is ({label})."


def finalise_wording(report, paper_options: dict | None = None, numbered: bool = False):
    """Last pass over every remark the student reads.

    Wrong MCQs get "Correct answer is (C) 25 years." from the paper's own printed
    options (keyed by question number, as strict_grading.option_texts_from_paper
    returns them), and everything is put into plain sentences.
    """
    paper_options = paper_options or {}
    for q in list(getattr(report, "questions", None) or []):
        if not bool(getattr(q, "objective", False)):
            continue
        if float(getattr(q, "score", 0) or 0) > 0:
            continue
        correct = str(getattr(q, "correct_option", "") or "").strip().upper()[:1]
        remark = str(getattr(q, "remark", "") or "")
        m = _MCQ_LETTER_RE.match(remark)
        if not correct and m:
            correct = m.group(1).upper()
        if not correct:
            continue
        number = re.sub(r"\D", "", str(q.qid).split(".")[0])
        options = None
        # A part ("Q19.ii", "Q1.4") has no options of its own in this table: the
        # number's options are its first part's. Vidyapeeth Class 8 Maths (14 Oct
        # 2026) told a student "Q19(ii): Correct answer is (B) 7", Q19(i)'s option.
        if number and not re.search(r"[.(]", str(q.qid)):
            options = paper_options.get(int(number)) or paper_options.get(number)
        q.remark = mcq_remark(correct, options, numbered)
    plain_report(report)
    return report


# --------------------------------------------------------------------------
# Question labels as the student's paper prints them
# --------------------------------------------------------------------------
# The grader's question ids are internal keys: "QB2.B", "FM.PB.Q1.A", "GST-OBJ-Q3",
# "Q1.a.i". SME review of the 2026-09-19 CA batch (2026-09-23): "Question number marked
# for feedback is not correct, should be same as what is mentioned in question paper,
# addition B before every question number is creating confusion." A copy shows
# "Q2(b)", "FM Q1(A)", "GST MCQ 3". The part letter comes back only when two
# questions would otherwise read the same ("Part B Q1" beside Part A's "Q1").
_PART_WORD_RE = re.compile(r"^P(?:ART)?([A-Z])$", re.IGNORECASE)
_QUESTION_TOKEN_RE = re.compile(r"^Q(?:UESTION)?([A-Z])?(\d+)([A-Za-z]*)$", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)$", re.IGNORECASE)
# "alt1", "alt2", "or", "OR2": an internal-choice alternative of the question before it.
_ALTERNATIVE_RE = re.compile(r"^(alt|or)(\d*)$", re.IGNORECASE)


def _label_parts(qid: str, objective: bool = False):
    """(label without part, part letter) for a question id, or None to leave it as is."""
    raw = str(qid or "").strip()
    tokens = [t for t in re.split(r"[.\-\s_/]+", raw) if t]
    paper: list[str] = []
    part, number, subs, mcq = "", "", [], False
    for index, token in enumerate(tokens):
        found = _QUESTION_TOKEN_RE.match(token)
        if found:
            part = (found.group(1) or part).upper()
            number = found.group(2)
            if found.group(3):
                subs.append(found.group(3))
            subs += tokens[index + 1:]
            break
        if token.isdigit():
            number = token
            subs += tokens[index + 1:]
            break
        upper = token.upper()
        if upper in {"OBJ", "MCQ", "OBJECTIVE"}:
            mcq = True
        elif upper in {"SUB", "DESC", "DESCRIPTIVE", "THEORY"}:
            continue
        elif _PART_WORD_RE.match(upper):
            part = _PART_WORD_RE.match(upper).group(1).upper()
        elif token.isalpha():
            paper.append(upper)
        else:
            return None
    if not number:
        return None
    shown, alternative = [], 0
    for sub in subs:
        if not sub.isalnum():
            return None
        # An OR alternative is printed "OR" on the paper; the id's "alt2" is ours.
        # MEASURED 2026-10-22: MP Board Accountancy covers read "Q9(alt2)" and
        # "Q11(alt1): ... not attempted"; teaching covers "Q7(alt2)(c)".
        found = _ALTERNATIVE_RE.match(sub)
        if found:
            alternative = int(found.group(2) or (1 if found.group(1).lower() == "alt" else 2))
            continue
        shown.append(sub.lower() if _ROMAN_RE.match(sub) else sub)
    head = " ".join(paper)
    if mcq and not shown:
        body = f"MCQ {number}"
    elif objective and len(shown) == 1 and shown[0].isdigit():
        body = f"MCQ {shown[0]}"
    else:
        body = f"Q{number}" + "".join(f"({sub})" for sub in shown)
    if alternative > 1:
        body += " (OR)" if alternative == 2 else f" (OR {alternative - 1})"
    return (f"{head} {body}".strip(), part)


def question_label(qid: str, objective: bool = False) -> str:
    """One question id as the paper prints it ("QB2.B" -> "Q2(B)")."""
    parts = _label_parts(qid, objective)
    return parts[0] if parts else str(qid or "")


def uncounted_note(extra: dict | None) -> str:
    """The margin note beside an "answer any N" attempt the choice rule left out,
    in the words of the rule that left it out (strict_grading records it)."""
    if str((extra or {}).get("rule") or "") == "first_attempted":
        return "Only the answers attempted first in this section were counted."
    return "The best answers in this section were counted."


def question_labels(report) -> dict[str, str]:
    """Every question and section id of a report mapped to the label a student reads.
    Two different ids never share a label: where dropping a part letter would make
    them read the same, the part comes back in words ("Part B Q1")."""
    items: list[tuple[str, bool]] = []
    for q in list(getattr(report, "questions", None) or []):
        items.append((str(q.qid), bool(getattr(q, "objective", False))))
    for sec in list(getattr(report, "section_totals", None) or []):
        if isinstance(sec, dict) and sec.get("qid"):
            items.append((str(sec["qid"]), bool(sec.get("objective"))))
    parsed = {qid: _label_parts(qid, objective) for qid, objective in items}
    plain = {qid: (p[0] if p else qid) for qid, p in parsed.items()}
    owners: dict[str, set[str]] = {}
    for qid, label in plain.items():
        owners.setdefault(label, set()).add(qid)
    # A part whose labels clash anywhere names itself everywhere, so one copy never
    # shows "Part B Q1" beside a plain "Q2" from the same part.
    clashing = {parsed[qid][1] for label, ids in owners.items() if len(ids) > 1
                for qid in ids if parsed[qid] and parsed[qid][1]}
    out = {}
    for qid, label in plain.items():
        p = parsed[qid]
        out[qid] = f"Part {p[1]} {label}" if p and p[1] in clashing else label
    return out


_LAST_SUB_RE = re.compile(r"^(.+?)[.\-\s_/]+([^.\-\s_/]+)$")


def part_totals(items) -> dict[str, str]:
    """The total of a lettered part that the paper splits further, for its first sub-part.

    `items` is (qid, label, score, max, counted) in reading order. Q4(A)(i) 1.25/1.5
    and Q4(A)(ii) 1/1 give {"QB4.A.i": "Q4(A)  2.25/2.5"}: the margin writes the part
    total above the first sub-part. SME ruling on the CA Auditing copies (2026-10-09):
    "In Q 4a marking should also be mentioned", Q4(B) and Q4(C) had their totals and
    Q4(A) only had (i) and (ii). A bare question number (Q1 with parts a, b) is not a
    part and gets nothing, and a part the report marks on its own row needs nothing.
    """
    from pdf_renderer import _fmt_mark  # one number format for every mark on the copy
    rows = [(str(qid), str(label or ""), score, mx, counted)
            for qid, label, score, mx, counted in items]
    own = {qid for qid, *_ in rows}
    groups: dict[str, list] = {}
    for qid, label, score, mx, counted in rows:
        found = _LAST_SUB_RE.match(qid)
        if not found or found.group(1) in own:
            continue
        parent_label = question_label(found.group(1))
        if "(" not in parent_label:          # a whole question, not a lettered part
            continue
        groups.setdefault(found.group(1), []).append((qid, label, score, mx, counted))
    out: dict[str, str] = {}
    for parent, members in groups.items():
        counted = [m for m in members if m[4]]
        if len(members) < 2 or not counted:
            continue
        first_qid, first_label = members[0][0], members[0][1]
        cut = first_label.rfind("(")
        shown = first_label[:cut] if cut > 0 and first_label.endswith(")") else question_label(parent)
        score = sum(float(m[2] or 0) for m in counted)
        mx = sum(float(m[3] or 0) for m in counted)
        out[first_qid] = f"{shown}  {_fmt_mark(score)}/{_fmt_mark(mx)}"
    return out


def report_part_totals(report) -> dict[str, str]:
    """part_totals for a GradeReport, with the labels the copy prints."""
    labels = question_labels(report)
    return part_totals(
        (q.qid, labels.get(str(q.qid), question_label(q.qid)), q.score, q.max_score,
         not getattr(q, "uncounted", False))
        for q in list(getattr(report, "questions", None) or []))


def whole_mark_total(total: float) -> float:
    """ICAI awards a paper's total in whole marks: half up (77.5 -> 78, 20.25 -> 20)."""
    import math
    return float(math.floor(float(total) + 0.5 + 1e-9))
