# -*- coding: utf-8 -*-
"""Release gate: everything about a marked copy that can be checked WITHOUT a model.

Of the eighteen defects found while reviewing real evaluated copies, fourteen were
mechanical — a wrong denominator, a question graded twice under one label, a tick on
a partially-earned step, a remark printed over the line above it, a page in the
middle of an answer with nothing on it. None of those need judgement, so none of
them should cost a model call. This module finds them for free.

The four that DO need judgement (is this mark right? is this reason true?) are out
of scope here by design — see `docs/reviewer_layer.md` for what an AI reviewer would
cost on top.

    issues = check_copy(report, pdf_bytes=pdf, total_marks=50)
    blocking = [i for i in issues if i.severity == "block"]

`block` means the copy is wrong and must not reach a student. `warn` means it is
worth a human glance but is not provably incorrect.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from anchors import validate_report_anchors
from grader import scheme_contradicts_paper
from pdf_renderer import disc_ink_fraction, rect_ink_fraction, student_ink_mask
from grader import _line_within_span
from remark_quality import fragment_problem, remark_quality_problem

MARGIN_REMARK_MAX_WORDS = 12

# Whole-paper shape checks stay quiet below this many questions — see _check_coverage.
_MIN_PAPER = 4

# Wording that should never reach a student — internal markers, tool internals, and
# the clause-joining punctuation a teacher does not use. Each of these appeared on a
# real copy that went out.
_BANNED_IN_REMARK = (
    ("not graded", "internal marker"),
    ("ocr", "tool internals"),
    ("verify", "hedging"),
    ("needs verification", "hedging"),
    ("appears to", "hedging"),
    ("the student", "third person"),
    ("[", "bracketed system notice"),
)


@dataclass(frozen=True)
class Issue:
    severity: str      # "block" | "warn" | "note"
    code: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.detail}"


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _check_marks(report, total_marks) -> list[Issue]:
    out: list[Issue] = []
    qs = list(getattr(report, "questions", None) or [])

    if total_marks is not None and abs(_f(report.max_total) - _f(total_marks)) > 0.25:
        out.append(Issue("block", "denominator",
                         f"copy is out of {_f(report.max_total):g} but the paper is "
                         f"{_f(total_marks):g}"))

    seen: dict[str, int] = {}
    for q in qs:
        seen[q.qid] = seen.get(q.qid, 0) + 1
    for qid, n in seen.items():
        if n > 1:
            out.append(Issue("block", "duplicate-qid",
                             f"{qid} appears {n} times — one answer is filed under a "
                             f"label already taken, so another question reads as "
                             f"never attempted"))

    summed = round(sum(_f(q.score) for q in qs), 2)
    if qs and abs(summed - _f(report.total_score)) > 0.25:
        out.append(Issue("block", "total-mismatch",
                         f"question scores add to {summed:g} but the copy shows "
                         f"{_f(report.total_score):g}"))

    for q in qs:
        if _f(q.score) > _f(q.max_score) + 1e-6:
            out.append(Issue("block", "over-award",
                             f"{q.qid} scored {_f(q.score):g} out of {_f(q.max_score):g}"))
        crits = list(getattr(q, "criteria", None) or [])
        if crits:
            cs = round(sum(_f(c.awarded) for c in crits), 2)
            if abs(cs - _f(q.score)) > 0.25:
                out.append(Issue("block", "criteria-mismatch",
                                 f"{q.qid} steps add to {cs:g} but the question shows "
                                 f"{_f(q.score):g}"))
            # The steps must account for the WHOLE question, not just the marks that
            # were given. A reviewer wrote "step marking is not proper" against a
            # Business Laws part whose strip read "1.5+1.5=3/4": two criteria, each
            # out of 1.5, on a 4-mark question. The missing mark belonged to a wrong
            # provision paragraph that carried no criterion at all, so it was never
            # drawn, never explained, and simply vanished. The awarded-sum check below
            # cannot see this — 1.5+1.5 reconciles perfectly with a score of 3.
            cm = round(sum(_f(c.max) for c in crits), 2)
            if abs(cm - _f(q.max_score)) > 0.25:
                out.append(Issue("block", "steps-do-not-cover-question",
                                 f"{q.qid} steps are worth {cm:g} in total but the "
                                 f"question is out of {_f(q.max_score):g} — "
                                 f"{abs(_f(q.max_score) - cm):g} mark(s) belong to no "
                                 f"step, so the student cannot see where they went"))
            for c in crits:
                description = str(getattr(c, "description", "") or "").strip()
                evidence = str(getattr(c, "evidence", "") or "").strip()
                if (description.lower() == "unallocated official rubric point"
                        or "primary breakdown omitted" in evidence.lower()):
                    out.append(Issue(
                        "block", "unresolved-rubric-point",
                        f"{q.qid} still contains a provisional rubric point; the "
                        "automatic verifier must resolve it into an actual requirement"))
                if _f(c.awarded) > _f(c.max) + 1e-6:
                    out.append(Issue("block", "step-over-award",
                                     f"{q.qid} step {c.description!r} scored "
                                     f"{_f(c.awarded):g} of {_f(c.max):g}"))
                # A tick is drawn for a fully-earned step, so `met` must agree with
                # the arithmetic or the sheet contradicts itself.
                earned = _f(c.max) > 0 and _f(c.awarded) >= _f(c.max) - 1e-9
                if bool(getattr(c, "met", earned)) != earned:
                    out.append(Issue("block", "met-mismatch",
                                     f"{q.qid} step {c.description!r} is marked "
                                     f"met={getattr(c, 'met')} but scored "
                                     f"{_f(c.awarded):g} of {_f(c.max):g}"))
    return out


def _check_remarks(report) -> list[Issue]:
    out: list[Issue] = []
    # The cover-page remarks were never checked, only the per-question ones — so a
    # "[TOTAL MISMATCH: ... review the maxima before trusting this score]" stamp rode
    # out at the top of a finished CA copy, still telling the reader not to trust a
    # total that had since been corrected to the paper's own 50.
    overall = (getattr(report, "overall_remarks", "") or "").strip()
    if "[" in overall:
        out.append(Issue("block", "overall-remark-wording",
                         f"the cover-page remarks carry a bracketed system notice: "
                         f"{overall[:110]!r}"))
    any_loss = any(_f(q.score) < _f(q.max_score) - 1e-9
                   for q in list(getattr(report, "questions", None) or []))
    if any_loss and not overall:
        out.append(Issue("block", "no-overall-remarks",
                         "marks were deducted but the student summary is blank"))
    for q in list(getattr(report, "questions", None) or []):
        remark = (getattr(q, "remark", "") or "").strip()
        lost = _f(q.score) < _f(q.max_score) - 1e-9
        attempted = bool(getattr(q, "attempted", True))
        if lost and attempted and not remark:
            out.append(Issue("block", "no-reason",
                             f"{q.qid} lost marks with no reason given"))
        elif lost and attempted:
            quality_problem = remark_quality_problem(remark)
            if quality_problem:
                out.append(Issue(
                    "block", "remark-not-actionable",
                    f"{q.qid} deduction feedback is {quality_problem}: {remark!r}",
                ))
        if not remark:
            continue
        low = remark.lower()
        for needle, why in _BANNED_IN_REMARK:
            if needle in low:
                out.append(Issue("block", "remark-wording",
                                 f"{q.qid} remark contains {needle!r} ({why}): {remark!r}"))
        # A note that names nothing ("Is vague. Misses.") is repaired during
        # finalisation. Warn if one still arrives - holding the copy would only mean
        # the student never gets it, since held copies are not reviewed.
        if fragment_problem(remark):
            out.append(Issue("warn", "remark-fragment",
                             f"{q.qid} remark is a fragment naming nothing: {remark!r}"))
        if ";" in remark:
            out.append(Issue("warn", "remark-punctuation",
                             f"{q.qid} remark stacks clauses with a semicolon: {remark!r}"))
        if re.search(r"\s[—–-]\s", remark):
            out.append(Issue("warn", "remark-punctuation",
                             f"{q.qid} remark uses a dash instead of a sentence: {remark!r}"))
        if len(remark.split()) > MARGIN_REMARK_MAX_WORDS:
            out.append(Issue("warn", "remark-length",
                             f"{q.qid} remark is {len(remark.split())} words "
                             f"(a teacher writes about {MARGIN_REMARK_MAX_WORDS}): "
                             f"{remark[:60]!r}"))
    return out


# A mark drawn over the student's own figures. The writing is a dark scan and every
# mark is drawn in red, so a mark sitting on a word shows up as dark NON-red pixels
# inside its circle — no OCR, no model, no original scan needed.
# Calibrated on real copies: marks confirmed clear score 0.000–0.024, a mark that
# made a word unreadable scored 0.063, and the worst (a circle through 7,61,024)
# scored 0.146. Two tiers, because 15% buries a figure and 4% only brushes it.
_MARK_INK_LIMIT = 0.035      # worth a look
_MARK_INK_BLOCK = 0.06       # the writing underneath is no longer readable
_MARK_RADIUS_PT = 11.0


_MARK_RE = re.compile(r"\d+(?:\.\d+)?")


def _rgb(color: int) -> tuple[int, int, int]:
    """PyMuPDF hands a span's colour back as a packed integer."""
    return (color >> 16) & 255, (color >> 8) & 255, color & 255


def _is_red(r: int, g: int, b: int) -> bool:
    return r > 110 and r > g + 55 and r > b + 55


def _ink_under_mark(mask, scale: float, bbox) -> float:
    """Fraction of the circle around this mark covered by the student's writing.

    Measured against the renderer's OWN idea of student ink, so a mark is judged by
    the same rule that placed it. Previously this counted every dark non-red pixel,
    which on ruled paper meant the printed ruling: one rule across the circle is
    ~5.5% of it and this blocks at 6%, so marks lying on blank ruled paper were
    reported as burying the student's writing.
    """
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return disc_ink_fraction(mask, scale, cx, cy, _MARK_RADIUS_PT)


# --- does the review agree with the marking? -------------------------------------
# The other checks look at marks, or at remarks, but never at the two together —
# which is the contradiction a reviewer spots first. On a real copy Q3.a carried a
# tick and a full 1.5 on the very line its margin remark called an error, and the
# statement the remark faulted was correct law.

_FAULT_RE = re.compile(
    r"\b(?:missing|missed|incorrect|wrong|incomplete|omitted|absent|lacks?|"
    r"failed|error|mistake|not\s+(?:stated|cited|mentioned|given|covered|"
    r"explained|shown|attempted|correct|valid|done)|should\s+(?:be|have))\b",
    re.IGNORECASE)
_PRAISE_RE = re.compile(
    r"\b(?:full\s+marks|all\s+correct|correct(?:ly)?\b|well\s+(?:argued|done|"
    r"structured|explained)|good|nothing\s+to\s+fix|no\s+issue)\b",
    re.IGNORECASE)
# "Section 16(2) not cited by name", "Section 20 not explicitly cited",
# "Section 14(a) implied condition as to title not explicitly cited".
# The sentence opens on a section reference and closes on a citation complaint.
# Only citation verbs count: "Section 12 sufficient-evidence point missing" is a
# complaint about CONTENT and must stay a valid deduction.
# An accounting standard is the same complaint in CA clothing — "did not explicitly
# cite AS-12 by name" — and went uncaught while the identical wording about a Section
# was blocked. Seen on a CA Inter Advanced Accounting copy: the student applied AS-12
# correctly to all three sub-parts and lost 0.5 for not naming it; the human examiner
# gave full marks.
# A remark whose ONLY complaint is that a section number is missing. The student is
# told they were right and docked anyway, which is not a mark a teacher can defend.
#
# MEASURED on one Business Laws copy: the first version of this matched remarks
# ending "not cited" / "not cited by name" and caught 2 of the 7 such remarks on the
# sheet. The five it missed were the ordinary phrasings -- "Section 23(2) SOGA not
# cited BY NUMBER" and "Section NUMBERS (18 and 20 of SOGA) not cited" -- so the
# leading token may now be a plural, and the trailing qualifier may be by name, by
# number, by section, or absent.
_CITATION_ONLY_RE = re.compile(
    r"^\W*(?:(?:section|sec\.?|s\.)\s*(?:numbers?\s*)?[\d(][\w()]*|"
    r"(?:ind\s*as|as)[\s.-]*\d+[\w()]*)\b.*?"
    r"\bnot\s+(?:explicitly\s+|expressly\s+|specifically\s+)?"
    r"(?:cited|mentioned|quoted|named|stated|given)"
    r"(?:\s+by\s+(?:name|number|section))?(?:\s+in\s+[\w\s-]+)?\W*$",
    re.IGNORECASE)
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "was", "for", "on",
    "by", "with", "as", "at", "from", "that", "this", "it", "its", "be", "are",
    "not", "no", "has", "have", "had", "but", "you", "your", "his", "her",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _check_agreement(report) -> list[Issue]:
    """Every way a remark can contradict the mark printed beside it."""
    out: list[Issue] = []
    for q in list(getattr(report, "questions", None) or []):
        remark = (getattr(q, "remark", "") or "").strip()
        if not remark:
            continue
        crits = list(getattr(q, "criteria", None) or [])
        full = _f(q.max_score) > 0 and _f(q.score) >= _f(q.max_score) - 1e-9

        # 1. full marks, yet the margin names a fault
        if full and _FAULT_RE.search(remark) and not _PRAISE_RE.search(remark):
            out.append(Issue("block", "remark-on-full-marks",
                             f"{q.qid} scored full marks but the remark names a fault: "
                             f"{remark!r} — a student reading it cannot tell what was "
                             f"wrong, because nothing was"))

        # 2. the remark points at a STEP that was ticked. Q3.a's remark quoted the
        #    line carrying a tick and its full 1.5, so the sheet argued with itself.
        #    No fault word is required here: that remark read "C's claim from A and
        #    B in scenario (1) stated as Rs 2000 each" — a flat description with no
        #    fault verb in it, printed under "where marks were lost". Requiring a
        #    fault word is exactly how it went out.
        if not full:
            rw = _content_words(remark)
            unearned_match = any(
                len(rw & _content_words(
                    f"{getattr(c, 'evidence', '')} {getattr(c, 'description', '')}")) >= 2
                for c in crits if not bool(getattr(c, "met", False))
            )
            for c in crits:
                if not bool(getattr(c, "met", False)):
                    continue
                ev = _content_words(f"{getattr(c, 'evidence', '')} "
                                    f"{getattr(c, 'description', '')}")
                shared = rw & ev
                if len(shared) >= 2 and not unearned_match:
                    out.append(Issue(
                        "block", "remark-faults-earned-step",
                        f"{q.qid} remark faults {c.description!r}, a step given its "
                        f"full {_f(c.max):g} and a tick (shared wording: "
                        f"{', '.join(sorted(shared)[:4])})"))
                    break

        # 3. a deduction whose only stated ground is an uncited section number
        if not full and _sentences(remark) and all(
                _CITATION_ONLY_RE.match(s) for s in _sentences(remark)):
            out.append(Issue("block", "citation-only-deduction",
                             f"{q.qid} lost marks and the only reason given is a "
                             f"missing section number: {remark!r} — name the "
                             f"substantive gap or restore the marks"))
    out += _check_evidence_admits_correct(report)
    return out


_OBJECTIVE_QID_RE = re.compile(r"^Q?(\d+)$", re.IGNORECASE)
_ANSWER_LINE_RE = re.compile(
    r"^\s*(?:q(?:uestion)?\s*)?(\d+)\s*[).:\-]{1,3}", re.IGNORECASE)


def _check_objective_anchor_ownership(report, ocr_pages, answer_map=None) -> list[Issue]:
    """An objective verdict must be anchored to the line for that exact question.

    This is intentionally deterministic: Q5 may never inherit a line beginning Q6,
    even if a visual model estimates that the two rows are close together.
    """
    if not ocr_pages:
        return []
    line_text = {
        str(getattr(line, "line_id", "") or "").strip():
        str(getattr(line, "text", "") or "").strip()
        for page in ocr_pages or [] for line in (getattr(page, "lines", None) or [])
    }
    out: list[Issue] = []
    owners: dict[str, str] = {}
    visually_verified = {
        str(qid or "").strip().lower()
        for qid in (getattr(report, "objective_visual_verified_qids", None) or [])
    }
    map_locations = (
        answer_map.get("locations", []) if isinstance(answer_map, dict)
        else list(getattr(answer_map, "locations", None) or [])
    )
    mapped: dict[str, set[str]] = {}
    for item in map_locations:
        field = item.get if isinstance(item, dict) else (
            lambda name, default=None: getattr(item, name, default))
        if field("status", "") != "located":
            continue
        mapped[str(field("qid", "") or "").strip().lower()] = {
            str(line_id or "")
            for line_id in (field("label_line_ids", ()) or ())
            if str(line_id or "")
        }
    for q in list(getattr(report, "questions", None) or []):
        objective = (bool(getattr(q, "objective", False))
                     or bool(str(getattr(q, "correct_option", "") or "").strip()))
        qmatch = _OBJECTIVE_QID_RE.match(str(getattr(q, "qid", "") or "").strip())
        if not objective or qmatch is None or not bool(getattr(q, "attempted", True)):
            continue
        expected = int(qmatch.group(1))
        if str(getattr(q, "qid", "") or "").strip().lower() in visually_verified:
            # Mathpix supplied no text anchor for this checkbox/diagram region;
            # two independent key-blind visual reads already agreed on the active
            # choice. Its verdict is QID-labelled in the dedicated margin lane.
            continue
        anchor = str(getattr(q, "anchor_line_id", "") or "").strip()
        text = line_text.get(anchor, "")
        actual_match = _ANSWER_LINE_RE.match(text)
        map_owned = anchor in mapped.get(
            str(getattr(q, "qid", "") or "").strip().lower(), set())
        if (not anchor or (not map_owned and (
                actual_match is None or int(actual_match.group(1)) != expected))):
            out.append(Issue(
                "block", "objective-anchor-mismatch",
                f"{q.qid} is anchored to {anchor or 'no line'} containing {text[:50]!r}; "
                f"its verdict must be bound to a line beginning with question {expected}"))
            continue
        prior = owners.get(anchor)
        if prior and prior != q.qid:
            out.append(Issue(
                "block", "duplicate-objective-anchor",
                f"{prior} and {q.qid} share {anchor}; their verdicts are not independently located"))
        owners[anchor] = q.qid
    return out


# The marker's own evidence conceding the student was right, in the same breath as
# taking marks off: "explains the concept correctly but does not cite Section 16(2)",
# "states the principle correctly but does not cite Section 27 SOGA by number".
_EVIDENCE_CONCEDES_RE = re.compile(
    r"\b(?:correct(?:ly)?|right|accurate(?:ly)?|valid)\b"
    r"[^.]{0,80}?\b(?:but|however|though|yet)\b"
    r"[^.]{0,80}?\b(?:not|no|without|omits?|fails?|does\s+n[o']t)\b"
    r"[^.]{0,60}?\b(?:cite[ds]?|citation|section|provision|number)\b",
    re.IGNORECASE)


def _check_evidence_admits_correct(report) -> list[Issue]:
    """A step docked marks whose own evidence says the student got it right.

    The remark-level check above reads only `q.remark`, so it never saw the place
    these admissions actually live. On one Business Laws copy the remark check
    caught 2 citation-only deductions; five more were sitting in criterion evidence
    reading "explains the concept correctly but does not cite Section 23(2) SOGA by
    number" — about 2.5 marks the student had earned and was docked for anyway.
    """
    out: list[Issue] = []
    for q in list(getattr(report, "questions", None) or []):
        for c in (getattr(q, "criteria", None) or []):
            if _f(c.awarded) >= _f(c.max) - 1e-9:
                continue                      # nothing was taken away
            ev = str(getattr(c, "evidence", "") or "").strip()
            if not ev or not _EVIDENCE_CONCEDES_RE.search(ev):
                continue
            out.append(Issue(
                "block", "citation-only-deduction",
                f"{q.qid} step {str(getattr(c, 'description', ''))[:40]!r} was docked "
                f"{_f(c.max) - _f(c.awarded):g} mark(s) but its own evidence says the "
                f"student was correct and only missed a citation: {ev[:110]!r}"))
    return out


def _check_rendered(pdf_bytes: bytes, report=None) -> list[Issue]:
    """Checks that need the finished PDF: overflow, unmarked pages, marks on writing."""
    out: list[Issue] = []
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        return [Issue("warn", "unreadable-pdf", f"could not open the copy: {exc}")]

    marked: list[int] = []
    for i in range(len(doc)):
        page = doc[i]
        # One raster per page, built lazily: pages with no marks never pay for it.
        ink: list = []

        def _ink_for(page=page, ink=ink):
            if not ink:
                ink.append(student_ink_mask(page))
            return ink[0]

        text = page.get_text().strip()
        # A page carrying only the margin rule has ~2 drawings; ticks are vector paths
        # and carry no text, so BOTH signals are needed to call a page unmarked.
        if text or len(page.get_drawings()) > 2:
            marked.append(i)
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for sp in l["spans"]:
                    if sp["bbox"][2] > page.rect.width - 1:
                        out.append(Issue("block", "text-overflow",
                                         f"page {i+1}: {sp['text'][:40]!r} runs off the "
                                         f"right edge"))
                    # A circled part-mark sitting on the student's own figures.
                    # Question totals are drawn larger than step marks, so the cap
                    # has to clear both. Cover and strip numerals pass this filter
                    # too, and score ~0: there is no student ink under them.
                    if (_MARK_RE.fullmatch(sp["text"].strip())
                            and sp["size"] < 20 and _is_red(*_rgb(sp["color"]))):
                        _mask, _scale = _ink_for()
                        frac = _ink_under_mark(_mask, _scale, sp["bbox"])
                        if frac >= _MARK_INK_BLOCK:
                            out.append(Issue(
                                "block", "mark-overlaps-writing",
                                f"page {i+1}: the mark {sp['text'].strip()!r} is drawn "
                                f"over the student's writing ({frac:.0%} of the circle "
                                f"is their ink) — what is under it cannot be read"))
                        elif frac >= _MARK_INK_LIMIT:
                            out.append(Issue(
                                "warn", "mark-touches-writing",
                                f"page {i+1}: the mark {sp['text'].strip()!r} clips the "
                                f"student's writing ({frac:.0%} of the circle is their "
                                f"ink)"))
    # Every question that was scored must be NAMED somewhere on the copy. The strip
    # stacks its entries top-down and reserves a slot even when none is left, so an
    # entry that ran past the foot of the page was drawn outside it and vanished —
    # two of five MCQ answers carried no visible mark, and nothing said so.
    shown = chr(10).join(doc[i].get_text() for i in range(len(doc)))
    for q in (list(getattr(report, "questions", None) or []) if report is not None else []):
        qid = str(getattr(q, "qid", "") or "").strip()
        # An UNATTEMPTED question has nothing on the page to mark, and the renderer
        # deliberately leaves it out of the strip — the cover's "Where marks were lost"
        # carries it instead. Requiring it here flagged 24 blank answers on a Chemistry
        # copy as defects, which buried the one real finding among them.
        if not bool(getattr(q, "attempted", True)):
            continue
        if qid and qid not in shown:
            out.append(Issue("block", "question-not-shown",
                             f"{qid} scored {_f(q.score):g}/{_f(q.max_score):g} but is "
                             f"named nowhere on the copy — the student cannot see it "
                             f"was marked"))

    # A page of an answer with no tick, cross or circled mark anywhere on the sheet.
    # The existing unmarked-page check cannot see this: page 1 carries the cover block,
    # so it counts as "marked" on text alone. On a CA copy the whole Balance Sheet —
    # the bulk of Q2 — went by without a single mark, because every criterion the
    # model wrote pointed at the notes on the pages after it.
    inked, has_sheet = [], []
    for i in range(len(doc)):
        page = doc[i]
        sheet = None
        for b in page.get_text("dict")["blocks"]:
            if b["type"] == 1:                       # the scanned answer sheet itself
                r = fitz.Rect(b["bbox"])
                sheet = r if sheet is None else (sheet | r)
        if sheet is None:
            continue
        has_sheet.append((i, sheet))
        for dr in page.get_drawings():
            col = dr.get("color") or dr.get("fill")
            if not col:
                continue
            if not _is_red(*[int(c * 255) for c in col[:3]]):
                continue
            mark_rect = dr["rect"]
            in_adjacent_margin = (
                mark_rect.x0 >= sheet.x1 - 2
                and mark_rect.x1 <= page.rect.width + 1
                and mark_rect.y1 >= sheet.y0
                and mark_rect.y0 <= sheet.y1
            )
            if sheet.intersects(mark_rect) or in_adjacent_margin:
                inked.append(i)
                break
    if inked:
        for i, sheet in has_sheet:
            if i in inked or i > inked[-1]:
                continue
            mask, scale = student_ink_mask(doc[i])
            # Only a page the student actually wrote on. A blank continuation page is
            # the unmarked-page check's business, not this one.
            if rect_ink_fraction(mask, scale, sheet.x0, sheet.y0,
                                 sheet.x1, sheet.y1) < 0.02:
                continue
            out.append(Issue("warn", "page-without-a-mark",
                             f"page {i + 1} carries the student's writing but no tick, "
                             f"cross or circled mark — every criterion for the answer "
                             f"on it was anchored to a different page"))

    if marked:
        blank = [i + 1 for i in range(marked[0], marked[-1] + 1) if i not in marked]
        if blank:
            out.append(Issue("warn", "unmarked-page",
                             f"page(s) {blank} carry no marks although they sit inside "
                             f"the marked range — a continuation page the marker missed"))
    doc.close()
    return out


def _check_scheme(report, paper_text: str, total_marks: float | None) -> list[Issue]:
    """Does the report's denominator agree with the marks the paper itself prints?

    Every other check here reads the report alone, so all of them are satisfied by a
    self-consistent report built on an invented marking scheme — and that is the
    single largest measured error in the tool. Nine runs of one CA Inter Taxation
    paper, the question paper supplied every time, produced nine different schemes
    and scores from 10/100 to 67.5/100 against a human's 42. Seven gave objective
    questions 1.5 marks where the paper prints "(2 Marks)"; each still summed to
    exactly 100, because force_official_total rescales whatever the failed repair
    pass leaves behind.

    This is the one check with a source of truth outside the report.
    """
    out: list[Issue] = []
    if getattr(report, "maxima_rescaled", False):
        # BLOCKING, not a warning. A rescaled report prints a denominator beside every
        # question that the paper never had, and the grand total being exact is what
        # makes it convincing. MEASURED on the fan-out A/B: a Chemistry copy came back
        # with 17 of ~45 sub-parts totalling 30 marks, was smeared 30 -> 70, and every
        # 1-mark MCQ went out as "/2.5" against a plausible-looking 42/70. As a warning
        # this passed the gate; there is no reading of "every printed maximum is
        # invented" that a student should receive.
        out.append(Issue("block", "maxima-rescaled",
                         "per-question maxima were rescaled to force the official total "
                         "— the total is exact but every denominator under it is "
                         "approximate, so the marks printed beside each question are "
                         "not the paper's"))
    if not paper_text:
        return out
    conflict = scheme_contradicts_paper(report, paper_text, total_marks)
    if conflict:
        out.append(Issue("block", "scheme-contradicts-paper", conflict))
    return out


# A criterion that pays for CITING a provision, against a line where the student
# cited none. MEASURED on the CA Foundation Business Laws copy the faculty rejected:
# Q1(a)(i) earned "Correct provision cited (S.10 ICA)" in full, while the lines it was
# anchored to say only "under the provisions of The Indian Contract Act, 1872" — the
# Act, no section, and a rule the student had stated wrongly. The grading rules already
# say a position given without the section citation loses that portion; the marker
# simply invented the citation, and the sub-part came back 1.5/2 for the second time
# against a reviewer who had ruled it 0.5/2.
_CRITERION_CITES_RE = re.compile(
    r"\b(?:provision|section|cited|citation)\b", re.IGNORECASE)
_SECTION_IN_ANSWER_RE = re.compile(
    r"(?:\b(?:section|sec\.?|u/s)\s*\d+)|(?:\bs\.\s*\d+)", re.IGNORECASE)


def _check_citation_credit(report, ocr_pages) -> list[Issue]:
    lines = {l.line_id: (l.text or "")
             for p in (ocr_pages or []) for l in getattr(p, "lines", [])}
    if not lines:
        return []
    out: list[Issue] = []
    for q in list(getattr(report, "questions", None) or []):
        for c in (getattr(q, "criteria", None) or []):
            desc = str(getattr(c, "description", "") or "")
            if not _CRITERION_CITES_RE.search(desc):
                continue
            if _f(c.awarded) < _f(c.max) - 1e-9 or _f(c.max) <= 0:
                continue                      # only a step paid IN FULL is suspect
            lid = (getattr(c, "step_line_id", "") or "").strip()
            text = lines.get(lid)
            if text is None:
                continue
            # Judge the WHOLE answer, not the one line this step happens to point at.
            # enforce_evidence_against_awards already zeroes an uncited provision, and
            # it reads the whole span for exactly the reason written beside it: "a
            # student who cites the section one line above an anchor has still cited
            # it". This gate read a single line, so it went on blocking copies whose
            # citation the repair had just found and correctly kept - a hold with no
            # repair that can clear it, which is the one thing a release gate must
            # never be. Same evidence, same verdict.
            anchored = [(getattr(other, "step_line_id", "") or "").strip()
                        for other in (getattr(q, "criteria", None) or [])]
            anchored.append((getattr(q, "anchor_line_id", "") or "").strip())
            anchored = [a for a in anchored if a]
            span = " ".join(lines.get(a, "") for a in anchored)
            span += " " + " ".join(text for line_id, text in lines.items()
                                   if _line_within_span(line_id, anchored))
            if _SECTION_IN_ANSWER_RE.search(span):
                continue                      # the student did cite a section
            out.append(Issue("block", "citation-credited-not-given",
                             f"{q.qid} step {desc[:44]!r} was paid "
                             f"{_f(c.awarded):g}/{_f(c.max):g} for citing a provision, "
                             f"but the line it points at names no section: "
                             f"{text.strip()[:60]!r}"))
    return out


def _check_anchors(report, ocr_pages) -> list[Issue]:
    """Marks whose position on the page is not backed by anything.

    Three separate faults, all previously silent because the renderer had a
    fallback for each: an anchor naming a line that is not on this copy, two
    steps of one question claiming the same line, and a step the renderer could
    not place at all. MEASURED over 278 anchors on five archived copies: 23 dead
    and 33 shared, so a fifth of all step marks were positioned by arithmetic.

    Severity is never `block`, and deliberately so. These are re-marking triggers:
    the answer is to ask the grader again for that question, not to hold a copy
    nobody is going to look at. Promote to `block` only once the repair pass that
    acts on them exists.

    Sharing a line and failing to place a step are both `note` rather than `warn`,
    because strict mode holds a copy on any warning. Neither is necessarily a fault:
    two rubric points genuinely satisfied on one line is a real thing a teacher ticks
    once, and an unplaced step costs that step its tick while the question's own
    total - written at the head of the answer - still carries the score. On the strict
    path a warning here refused whole copies over a mark that was either positioned
    exactly where it belonged or accounted for a line above. A DEAD anchor stays a
    warning: it really does put a mark somewhere the page cannot account for.
    """
    out: list[Issue] = []
    for issue in validate_report_anchors(report, ocr_pages):
        severity = "note" if issue.code == "anchor-shared-in-question" else "warn"
        out.append(Issue(severity, issue.code, issue.detail))
    stranded = list(getattr(report, "unanchored_step_qids", None) or [])
    if stranded:
        shown = ", ".join(stranded[:8])
        more = f" and {len(stranded) - 8} more" if len(stranded) > 8 else ""
        # Informational, not actionable. "The page cannot account for the score" was
        # not true: the step branch writes the question's own total ("Q14 2.5/3") at
        # the head of the answer whenever it marks at all, and the strip prints the
        # per-step chain ONLY when the marks actually drawn reconcile to the score.
        # So an unplaced step costs a tick, never the accounting. Raised as a warn it
        # was fatal — strict mode upgrades every warn to a hold, and a held copy is a
        # copy no student ever gets back, because nobody reviews them.
        out.append(Issue(
            "note", "step-not-placed",
            f"{len(stranded)} question(s) carry a rubric step that found no line on "
            f"the student's writing, so that step went unticked; the question total "
            f"still carries the score: {shown}{more}"))
    return out


def _check_coverage(report) -> list[Issue]:
    """Answers the marker never read, and marks placed by guesswork.

    grader.find_unmarked_answers has already compared the report against the OCR
    and left its verdict on the report; this turns that into a release decision.
    """
    out: list[Issue] = []

    missed = list(getattr(report, "false_blank_qids", None) or [])
    if missed:
        shown = ", ".join(missed[:8])
        more = f" and {len(missed) - 8} more" if len(missed) > 8 else ""
        lost = sum(_f(q.max_score) for q in (getattr(report, "questions", None) or [])
                   if q.qid in set(missed))
        out.append(Issue(
            "block", "answer-not-read",
            f"{len(missed)} question(s) scored 0 as unattempted have writing on the "
            f"sheet — {lost:g} mark(s) the student may have earned were never read: "
            f"{shown}{more}"))

    # A report with no criteria anywhere is not a marked paper, it is a total. Every
    # remark-vs-mark check in this file iterates over criteria, so a copy like that
    # sails through the whole gate untouched — which is exactly what happened on all
    # five archived Chemistry runs, and why "Where marks were lost" printed nothing
    # for fourteen questions.
    questions = list(getattr(report, "questions", None) or [])
    answered = [q for q in questions if bool(getattr(q, "attempted", True))]
    lost_marks = [q for q in answered
                  if _f(q.score) < _f(q.max_score) - 1e-9]
    # _MIN_PAPER guards the two checks below. Both reason about the SHAPE of a whole
    # marked paper, and a report of two or three questions is a fixture or a quiz,
    # not a shape worth judging — firing there would only teach people to ignore the
    # gate. The copies these were written for had 11 and 45 questions.
    if len(lost_marks) >= _MIN_PAPER and not any(
            (getattr(q, "criteria", None) or []) for q in questions):
        out.append(Issue(
            "block", "no-criteria-anywhere",
            f"{len(lost_marks)} question(s) lost marks and the report carries no "
            f"criteria at all — there is no per-step evidence, so nothing tells the "
            f"student where the marks went and no other check here can run"))

    # Marks the renderer will have to place by a model-estimated y_fraction because
    # no OCR line was named. On the Chemistry copy every anchor was empty, so every
    # mark on the page was positioned by guesswork: ticks landed on the wrong
    # question's text and a circled 2 floated in blank space.
    if len(answered) >= _MIN_PAPER:
        unanchored = [q for q in answered
                      if not (getattr(q, "anchor_line_id", "") or "").strip()]
        if len(unanchored) == len(answered):
            out.append(Issue(
                "block", "no-anchors-at-all",
                f"not one of the {len(answered)} answered questions named an OCR line "
                f"— every mark on this copy is placed by an estimated page fraction, "
                f"not by the student's writing"))
        elif len(unanchored) > len(answered) // 2:
            out.append(Issue(
                "warn", "weak-anchoring",
                f"{len(unanchored)} of {len(answered)} answered questions named no OCR "
                f"line; their marks are placed by estimate"))
    return out


def check_copy(report, *, pdf_bytes: bytes | None = None,
               total_marks: float | None = None,
               paper_text: str = "", ocr_pages=None, answer_map=None) -> list[Issue]:
    """Every mechanical fault we know how to find, most severe first. Costs nothing."""
    issues = (_check_marks(report, total_marks) + _check_remarks(report)
              + _check_agreement(report)
              + _check_coverage(report)
              + _check_anchors(report, ocr_pages)
              + _check_scheme(report, paper_text, total_marks)
              + _check_citation_credit(report, ocr_pages)
              + _check_objective_anchor_ownership(
                  report, ocr_pages, answer_map=answer_map))
    if pdf_bytes:
        issues += _check_rendered(pdf_bytes, report)
    order = {"block": 0, "warn": 1, "note": 2}
    return sorted(issues, key=lambda i: (order.get(i.severity, 9), i.code))


def blocking(issues: list[Issue]) -> list[Issue]:
    """The issues that must stop a copy reaching a student."""
    return [i for i in (issues or []) if getattr(i, "severity", "") == "block"]


def summarise(issues: list[Issue]) -> str:
    if not issues:
        return "pre-flight clean"
    n_block = len(blocking(issues))
    return (f"pre-flight found {len(issues)} issue(s), {n_block} blocking:\n  "
            + "\n  ".join(str(i) for i in issues))
