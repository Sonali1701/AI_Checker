# -*- coding: utf-8 -*-
"""Resolve a rubric step to the line of student writing it is actually about.

A tick in the wrong place is worse than no tick. The student reads it as "this
line is right", and on a ledger or a computation that is a statement about a
figure they can point at.

The grader is asked to name an anchor (``P4L25``) by reading labels off an
overlaid page image. It is bad at it, and the badness is measurable. Across 278
anchors on five archived copies (ca_final, ca_v3, bl_v2, ca100_tax, chem_t07):

    23  point at a line_id that does not exist in the OCR at all
    33  are reused by two criteria of the SAME question
    134 share neither a number nor a content word with the step they mark

The first two are provable defects, and both used to end in the same place: the
renderer could not resolve the anchor, so it fell through to a stage that spread
the unplaced steps evenly down the answer — ``(k + 0.5) * lines / n``. The mark
was then positioned by arithmetic, on a line chosen because of its index.

This module replaces that guess. The insight is that we do not need the model to
do index bookkeeping at all: a rubric step almost always NAMES the thing it is
about ("Sale proceeds 8000 x 14 = 1,12,000", "Bonus issue 4000 shares"), and the
student's line contains it. Matching text to text is deterministic, free, and
auditable — and on commerce papers the numbers survive Mathpix mangling far
better than the words do:

    step  'Sale proceeds 8000 x 14 = 1,12,000'   line  '=112000'
    step  'Closing balance 8000 shares at 84,500' line  'ie．（ ） 845001 －'
    step  'Pre-acquisition dividend adjusted'     line  'fre－acquisition Divisent = 80,000 × 20% = 16000'

which is why digits are weighted above words, and why they are matched by
CONTAINMENT against the line's digit blob rather than by equality — `84500` has
to match inside `845001`.

Nothing here invents an anchor out of nothing. When no candidate scores above
`MIN_CONFIDENT`, that is reported as unresolved, and the caller's job is to ask
the grader again — never to draw a tick somewhere plausible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A step's own wording is short ("correct formula", "Reserves & surplus note"),
# so a single shared number is already strong evidence and a single shared word
# is not. Weights reflect that; they are not tuned beyond it.
_NUMBER_WEIGHT = 1.0
_WORD_WEIGHT = 0.34

# What it takes to place a mark on a line we chose ourselves. One matched number,
# or three matched content words. Below this the step is left unresolved rather
# than placed hopefully.
MIN_CONFIDENT = 1.0

# What it takes to LEAVE a mark where the model put it. Far lower, because the
# model looked at the page and we did not: an anchor it named is trusted unless
# it is provably broken. This threshold only decides whether we can say the
# placement was corroborated, which is what the release gate reports on.
MIN_CORROBORATED = 0.34

# Digit runs shorter than this match by exact token, not by containment: "12"
# appears inside half the amounts on a ledger page.
_CONTAINMENT_MIN_LEN = 3

_NUM_RE = re.compile(r"\d[\d,.\s]*\d|\d")
_WORD_RE = re.compile(r"[a-z]{4,}")

# Words that appear in rubric labels and in student writing without meaning the
# two are about the same thing. "correct", "note", "entry", "total" and friends
# are what a rubric step is MADE of, so they must not carry a match on their own.
_STOP = frozenset("""
the and for with that this from into onto upon their there where when what
which while would could should must have been were was are not any all some
each both than then them they your you our its his her hers
correct correctly incorrect wrong right proper properly shown show given give
note notes entry entries step steps mark marks marked score scored total totals
answer answers question questions student students rubric criterion criteria
value values amount amounts figure figures calculation calculations working
workings method methods stated state states mention mentioned mentions
made make made done does used using use part parts point points
""".split())


def _digit_blob(text: str) -> str:
    """Every digit in `text`, separators removed, runs joined by a sentinel.

    Mathpix returns "84,500" as "845001", "1,12,000" as "112000" and sometimes
    splits a figure across LaTeX markup. Concatenating the runs and testing for
    containment finds the figure through all three; the sentinel stops a match
    from spanning two unrelated numbers.
    """
    return "|".join(re.sub(r"[^\d]", "", run) for run in _NUM_RE.findall(text or ""))


def number_tokens(text: str) -> set[str]:
    """Normalised numeric tokens worth matching on (2+ digits)."""
    out: set[str] = set()
    for run in _NUM_RE.findall(text or ""):
        digits = re.sub(r"[^\d]", "", run)
        if len(digits) >= 2:
            out.add(digits)
    return out


def word_tokens(text: str) -> set[str]:
    """Content words worth matching on."""
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP}


def step_text(criterion) -> str:
    """Everything the grader told us about this step, as one searchable string.

    `anchor_quote` is the field added for this purpose — the student's own words
    the step is about — and it is the strongest signal when present. `evidence`
    carries the same kind of material for a deduction ("student: WDV 7,61,024;
    key: 7,96,500"). `description` is always there and is the weakest of the
    three, being a rubric label rather than a quotation.
    """
    get = (criterion.get if isinstance(criterion, dict)
           else lambda name, default="": getattr(criterion, name, default))
    return " ".join(str(get(name, "") or "")
                    for name in ("anchor_quote", "evidence", "description"))


def match_score(step: str, line_text: str) -> float:
    """How strongly this step's wording points at this line. 0.0 means nothing.

    Numbers are matched by containment against the line's digit blob so a
    correctly-read figure is found inside a mangled one; short runs fall back to
    exact tokens.
    """
    if not (step or "").strip() or not (line_text or "").strip():
        return 0.0
    blob = _digit_blob(line_text)
    line_numbers = number_tokens(line_text)
    hits = 0.0
    for token in number_tokens(step):
        if len(token) >= _CONTAINMENT_MIN_LEN:
            if token in blob:
                hits += _NUMBER_WEIGHT
        elif token in line_numbers:
            hits += _NUMBER_WEIGHT
    shared_words = word_tokens(step) & word_tokens(line_text)
    return hits + _WORD_WEIGHT * len(shared_words)


@dataclass
class AnchorResolution:
    """Where one criterion's mark should go, and how we know."""
    line_id: str = ""
    score: float = 0.0
    # "model" — the grader's own anchor, kept.
    # "resolved" — we found the line from the step's own wording.
    # "unresolved" — nothing scored high enough; do not draw a guessed mark.
    source: str = "unresolved"
    reason: str = ""

    @property
    def placed(self) -> bool:
        return bool(self.line_id) and self.source != "unresolved"

    @property
    def corroborated(self) -> bool:
        return self.placed and self.score >= MIN_CORROBORATED


def best_line(step: str, candidates, *, exclude: set[str] | None = None,
              min_score: float = MIN_CONFIDENT):
    """The candidate line this step most likely refers to, or None.

    `candidates` are dicts as the renderer builds them: {"lid", "text", ...}.
    Ties go to the EARLIER line — a step is written before it is concluded, and
    picking the first of two equal candidates keeps marks in reading order.
    """
    exclude = exclude or set()
    best = None
    best_score = 0.0
    for line in candidates:
        lid = str(line.get("lid") or "")
        if not lid or lid in exclude:
            continue
        score = match_score(step, line.get("text") or "")
        if score > best_score:
            best, best_score = line, score
    if best is None or best_score < min_score:
        return None, best_score
    return best, best_score


def resolve_steps(criteria, band_lines, *, model_anchor_ok) -> list[AnchorResolution]:
    """Decide where every criterion of one question is marked.

    `band_lines`  the question's own lines, in reading order — the only places
                  its marks may go. Restricting to the band is what stops a
                  numeric match from dragging a tick onto a different answer
                  that happens to share a figure.
    `model_anchor_ok(lid, criterion)`  the renderer's existing rules: the line
                  exists, is inside this band, and is not a header, a cancelled
                  line, or a Total row that THIS step is not about. It takes the
                  criterion because the last rule depends on it — "correct
                  casting" may be marked on the Total row and "Interest on
                  Drawings" may not.

    Order matters. Anchors the model named and the renderer accepts are kept
    first and reserve their line; only then are the leftovers resolved from
    their own wording against what is still free. A step that resolves to a line
    another step already holds is allowed to share it — several rubric points
    satisfied on one line become one tick carrying their combined marks, which
    is what a teacher draws. Scattering them was the old Stage 3 behaviour and
    it is how 22 duplicated anchors on one CA copy became 22 marks on lines
    nobody chose.
    """
    criteria = list(criteria or [])
    out = [AnchorResolution() for _ in criteria]
    texts = [step_text(c) for c in criteria]
    by_lid = {str(line.get("lid") or ""): line for line in band_lines}

    kept: set[str] = set()
    for index, criterion in enumerate(criteria):
        get = (criterion.get if isinstance(criterion, dict)
               else lambda name, default="": getattr(criterion, name, default))
        lid = str(get("step_line_id", "") or "").strip()
        if not lid:
            continue
        if lid not in by_lid:
            out[index].reason = ("names no line on this answer"
                                 if lid else "no anchor given")
            continue
        if not model_anchor_ok(lid, criterion):
            out[index].reason = "anchor rejected (header, cancelled or total row)"
            continue
        out[index] = AnchorResolution(
            line_id=lid,
            score=match_score(texts[index], by_lid[lid].get("text") or ""),
            source="model",
        )
        kept.add(lid)

    # Everything the model could not place, placed from its own words. Lines an
    # accepted anchor already holds are tried last so two steps only share a line
    # when nothing else fits — see the docstring.
    for index, resolution in enumerate(out):
        if resolution.placed:
            continue
        line, score = best_line(texts[index], band_lines, exclude=kept)
        if line is None:
            line, score = best_line(texts[index], band_lines)
        if line is None:
            out[index].reason = out[index].reason or "no line matches this step"
            continue
        out[index] = AnchorResolution(
            line_id=str(line.get("lid") or ""), score=score, source="resolved")
    return out


# ---------------------------------------------------------------------------
# Release-gate view: the defects that can be proved without the band
# ---------------------------------------------------------------------------

@dataclass
class AnchorIssue:
    code: str
    qid: str
    detail: str
    criteria: list[str] = field(default_factory=list)


def validate_report_anchors(report, ocr_pages) -> list[AnchorIssue]:
    """Anchors that are provably wrong, with no need to know the answer's band.

    Two kinds, both measured on real copies and both previously silent:

    * `anchor-not-on-page` — the line_id does not exist in the OCR of this copy.
      The grader invented it. 17 of 99 anchors on one CA Inter Taxation copy.
    * `anchor-shared-in-question` — two criteria of one question name the same
      line, so at most one of them is marked where it belongs.

    A shared anchor is reported, not blocked outright, because two rubric points
    genuinely satisfied on one line is a real thing a teacher ticks once. It is
    for the caller's repair pass to ask which it is.
    """
    known = {
        str(getattr(line, "line_id", "") or "")
        for page in (ocr_pages or [])
        for line in (getattr(page, "lines", None) or [])
    }
    if not known:
        return []
    issues: list[AnchorIssue] = []
    for question in list(getattr(report, "questions", None) or []):
        qid = str(getattr(question, "qid", "") or "")
        seen: dict[str, list[str]] = {}
        dead: list[str] = []
        for criterion in (getattr(question, "criteria", None) or []):
            lid = str(getattr(criterion, "step_line_id", "") or "").strip()
            if not lid:
                continue
            label = str(getattr(criterion, "description", "") or "")[:40]
            if lid not in known:
                dead.append(f"{label!r} -> {lid}")
                continue
            seen.setdefault(lid, []).append(label)
        if dead:
            issues.append(AnchorIssue(
                "anchor-not-on-page", qid,
                f"{len(dead)} step(s) of {qid} are anchored to a line that does not "
                f"exist on this copy, so their marks are placed by guesswork: "
                + "; ".join(dead[:3]),
                dead))
        for lid, labels in seen.items():
            if len(labels) > 1:
                issues.append(AnchorIssue(
                    "anchor-shared-in-question", qid,
                    f"{len(labels)} steps of {qid} are anchored to the same line "
                    f"{lid}: {', '.join(repr(l) for l in labels[:3])}",
                    labels))
    return issues
