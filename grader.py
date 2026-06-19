"""Claude-powered handwritten answer-sheet grader."""
from __future__ import annotations

import base64
import math
import re
from io import BytesIO
from typing import Literal

import fitz  # PyMuPDF
from PIL import Image
from pydantic import BaseModel, Field

from mathpix import PageOCR, overlay_anchors_on_png


def _anthropic_client(api_key: str | None):
    """Lazily construct the Anthropic client. Importing `anthropic` lazily (not at module
    top) keeps the desktop CLIENT — which uses this module only for the schema, regex
    marks detection, and PDF utils, never the Claude API — free of the heavy SDK, so the
    packaged EXE stays small and doesn't bundle SDK transitive deps it never calls."""
    import anthropic
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


# ---------- Output schema ----------

class Criterion(BaseModel):
    description: str = Field(
        description="Short label for one rubric step/criterion the student was assessed on (e.g. 'Correct formula', 'Mentioned curiosity', 'Stated importance of humanity', 'Correct arithmetic in step 3'). 3-8 words."
    )
    awarded: float = Field(description="Marks awarded for THIS criterion only.")
    max: float = Field(description="Maximum marks for THIS criterion only.")
    met: bool = Field(
        description="True if the student fully satisfied this criterion (awarded == max), false otherwise. Drives whether a ✓ or ✗ is drawn."
    )
    step_line_id: str = Field(
        default="",
        description=(
            "The anchor (e.g. 'P3L9' for a text line or 'P7D1' for a diagram region) where THIS rubric step is satisfied — a small ✓/✗ will be drawn next to it. "
            "Pick by LOOKING at the overlaid page image: every line is labelled with its [PnLk] in blue, every diagram region with [PnDk] in orange, every header line in red.\n\n"
            "STRICT RULES:\n"
            "1. NEVER use a header line (anything labelled RED — 'Q.27)', '(a)', '(i)', 'Section-B', etc.). Always pick a line containing actual student working.\n"
            "2. NEVER reuse a step_line_id across two criteria of the SAME question — each step must land on its own line so ticks don't pile up.\n"
            "3. Prefer the LAST line of evidence (the line where the student arrives at the result) over the first line of a multi-line step.\n"
            "4. Sub-parts (i)(ii)(iii)(iv)(v) and (a)(b)(c)(d) each get their own criterion with their own step_line_id pointing inside that sub-part.\n"
            "5. For steps satisfied by a diagram, figure, table, or hand-drawn sketch (no text), use the diagram-region anchor 'PnDk' (orange outline) — do NOT fall back to the nearest text line. This is the correct way to mark drawing-based steps.\n"
            "Leave empty only when there is genuinely no anchor on the visible page."
        ),
    )


class QuestionMark(BaseModel):
    qid: str = Field(description="Question identifier, e.g. 'Q1.A.1' or 'Q4.B.iii'.")
    score: float = Field(description="Marks awarded (must equal sum of criteria awarded values).")
    max_score: float = Field(description="Maximum possible marks for this sub-question (must equal sum of criteria max values).")
    page: int = Field(description="1-indexed page number of the student's answer sheet where this answer appears.")
    anchor_line_id: str = Field(
        default="",
        description="If an OCR transcript is provided, set this to the [line_id] (e.g. 'P3L7') of the FIRST line of this sub-question's answer on the student's page. Used for precise mark placement. Leave empty if no OCR transcript or no matching line.",
    )
    y_fraction: float = Field(
        description="Approximate vertical position (0.0=top, 1.0=bottom) on that page where the answer to this sub-question starts. Used as a fallback when anchor_line_id is unavailable."
    )
    remark: str = Field(
        default="",
        description=(
            "VERY SHORT handwritten-style reason marks were lost — this gets scribbled in red CURSIVE on the student's page next to the answer, like a real teacher's margin note. "
            "Hard cap: 8 words, ideally 3–6. Use a teacher's terse phrasing. "
            "GOOD examples: 'wrong antonym', 'tense error', 'missed key point', 'no reason given', 'irrelevant', 'check spelling', 'factual error', 'grammar — verb tense', 'incomplete answer', 'wrong modal'. "
            "BAD (too long): 'Lost 1 mark due to incorrect antonym for hostile.', 'The student missed the key idea about humour in the doctor's description.' "
            "Empty string if full marks were awarded."
        ),
    )
    criteria: list[Criterion] = Field(
        default_factory=list,
        description=(
            "Step-by-step breakdown of how the score was computed — like a teacher's ticks and crosses against the rubric. "
            "REQUIRED for every sub-question worth more than 1 mark: list each rubric criterion the student was checked against, "
            "with marks awarded vs max, and whether it was met. For a 1-mark question, you may return an empty list or a single criterion. "
            "Sum of `awarded` across criteria MUST equal `score`; sum of `max` MUST equal `max_score`."
        ),
    )
    mark_style: Literal["per_step", "single", "auto"] = Field(
        default="auto",
        description=(
            "How a teacher would physically mark THIS answer on the sheet:\n"
            "- 'per_step': put a tick + the marks next to EACH step/point individually. Use for "
            "(a) numerical / calculation answers and multi-step derivations, AND (b) ENUMERATED "
            "answers where each point sits on its own line — 'state four causes', 'list three "
            "differences', 'give two examples', fill-in-the-blanks, and LABELLED DIAGRAMS. For these, "
            "set each criterion's step_line_id to the line (or 'PnDk' diagram region) for that point.\n"
            "- 'single': ONE tick + the total in the margin, plus a short remark — use for FLOWING "
            "PROSE: essays, paragraph answers, descriptive English/Hindi/literature answers, and "
            "history/civics explanations where ticking every sentence would be wrong.\n"
            "- 'auto' (default): only set this if genuinely unsure; the renderer will infer from the "
            "content. Prefer to decide explicitly between 'per_step' and 'single'."
        ),
    )
    objective: bool = Field(
        default=False,
        description=(
            "True ONLY for genuinely objective items (MCQ, assertion-reason, true/false, "
            "match-the-following, one-word/one-value answers where the answer is either right "
            "or wrong). When True the SYSTEM scores the item in code from `is_correct` / "
            "`attempted` — set `criteria` empty and `mark_style` to 'single'. Leave False for "
            "every descriptive / short / long answer (those keep normal criteria-based grading)."
        ),
    )
    attempted: bool = Field(
        default=True,
        description=(
            "OBJECTIVE items only. False ONLY when the student left this item genuinely BLANK "
            "(wrote nothing). If they wrote ANYTHING for it — a value, a word, a letter, even a "
            "wrong one — this is True. NEVER mark an item that has writing as not-attempted just "
            "because you cannot read which option letter it maps to."
        ),
    )
    is_correct: bool = Field(
        default=False,
        description=(
            "OBJECTIVE items only. Your judgement of whether the student's written answer MATCHES "
            "the correct answer in the key — compared BY VALUE, not by letter. Read what the "
            "student actually wrote (e.g. '-10', '20', 'four decimal places', 'A is true but R is "
            "false') and decide if it equals the key's correct answer. This value comparison is "
            "what the system scores from, so it is the field that matters most — getting the "
            "option letter is NOT required."
        ),
    )
    chosen_option: str = Field(
        default="",
        description=(
            "OBJECTIVE items, OPTIONAL corroboration. The single option LETTER the student marked, "
            "read from the PAGE IMAGE — 'A'/'B'/'C'/'D' — IF you can clearly tell. Leave '' if the "
            "student wrote a value but no clear letter (this does NOT make it unattempted — set "
            "`attempted`/`is_correct` from the value). NEVER read this from the OCR transcript."
        ),
    )
    correct_option: str = Field(
        default="",
        description=(
            "OBJECTIVE items, OPTIONAL corroboration. The correct option LETTER from the ANSWER KEY "
            "('A'/'B'/'C'/'D') IF the key gives one. Used only to phrase the remark and as a "
            "cross-check; scoring is driven by `is_correct`. Leave '' for descriptive questions."
        ),
    )


class Annotation(BaseModel):
    type: Literal["cross", "strikethrough", "circle"] = Field(
        description="'cross' draws a small red ✗ next to a wrong item (use for wrong MCQ choices, wrong single-word answers like a wrong antonym). 'strikethrough' draws a red line through a span of wrong text (use for grammar errors, wrong verb forms, misspelled words inside prose). 'circle' draws a hand-drawn red circle around a word or short phrase to highlight a factual error or wrong word choice the teacher wants the student to NOTICE (use for content mistakes like 'first overall' when it should be 'first twice', wrong character names, wrong dates). Prefer circle for content errors, strikethrough for grammar/wording errors, cross for short objective answers."
    )
    page: int = Field(description="1-indexed page number on the student's answer sheet.")
    target_line_id: str = Field(
        default="",
        description="If an OCR transcript is provided, set this to the [line_id] (e.g. 'P3L11') containing the wrong word/phrase. Used for precise placement. Leave empty if no OCR transcript or no matching line.",
    )
    target_word: str = Field(
        default="",
        description="The exact wrong word/phrase from the OCR transcript (e.g. 'pride', 'hostile'). Used to locate the word within target_line_id for tighter horizontal placement.",
    )
    x_fraction: float = Field(default=0.5, description="Fallback horizontal position (0.0-1.0) when no OCR target is available.")
    y_fraction: float = Field(default=0.5, description="Fallback vertical position (0.0-1.0) when no OCR target is available.")
    width_fraction: float = Field(
        default=0.08,
        description="Approximate width of the wrong text as a fraction of page width. Used for strikethrough sizing when no OCR target is available.",
    )
    label: str = Field(default="", description="The wrong word/phrase being marked (for reference).")


class GradeReport(BaseModel):
    total_score: float
    max_total: float
    overall_remarks: str = Field(
        description="2-3 sentence summary of overall performance, strengths, and what to improve."
    )
    section_totals: list[dict] = Field(
        default_factory=list,
        description="List of {'qid': 'Q1', 'score': 12.5, 'max_score': 16} for top-level questions.",
    )
    questions: list[QuestionMark]
    annotations: list[Annotation] = Field(
        default_factory=list,
        description="Inline marks on the student's pages. Add a 'cross' next to each wrong word in an objective answer (antonyms, synonyms, fill-in-the-blanks, MCQ choices). Add 'strikethrough' for clearly wrong phrases or whole-line errors. Do NOT annotate subjective prose answers — those are handled by the per-question remark.",
    )


# ---------- Marks scheme (the authoritative denominator) ----------

class MarksItem(BaseModel):
    """One gradable sub-part and its maximum marks, read off the question paper."""
    qid: str = Field(description="Sub-part id: top-level 'Q14'; lettered 'Q16.a'; roman 'Q18.iii'; nested 'Q6.ii.b'.")
    max: float = Field(description="Maximum marks PRINTED on the paper for THIS sub-part only.")
    description: str = Field(default="", description="3-6 word label so a teacher recognises the part.")


class MarksScheme(BaseModel):
    """The full per-part maximum-marks breakdown of a paper. Fixed for every student."""
    total: float = Field(description="Sum of every item's max — the paper's full marks.")
    items: list[MarksItem] = Field(default_factory=list)


def marks_scheme_from_items(items: list[dict]) -> "MarksScheme | None":
    """Build a MarksScheme from the editable table the UI sends (qid/max/part rows). Lives
    here (not pipeline) so the SDK-free desktop client can use it without importing the
    AI graders. pipeline re-exports it for backward compatibility."""
    m_items = []
    for r in items or []:
        qid = str(r.get("qid", "")).strip()
        if not qid:
            continue
        try:
            m_items.append(MarksItem(qid=qid, max=float(r.get("max", 0) or 0),
                                     description=str(r.get("part") or "")))
        except (TypeError, ValueError):
            continue
    if not m_items:
        return None
    return MarksScheme(total=sum(i.max for i in m_items), items=m_items)


def _norm_qid(qid: str | None) -> str:
    """Loose key for matching qids across formats: 'Q16.a'/'Q16(a)'/'16a' -> '16a'."""
    s = re.sub(r"[^a-z0-9]", "", (qid or "").lower())
    return re.sub(r"^q", "", s, count=1)


def _top_level_qid(qid: str | None) -> str:
    """'Q16.a' -> 'Q16', 'Q18.iii' -> 'Q18', 'Q1' -> 'Q1'."""
    m = re.match(r"\s*[Qq]?\s*(\d+)", qid or "")
    return f"Q{m.group(1)}" if m else (qid or "Q?")


def marks_scheme_block(marks_scheme: "MarksScheme | None") -> str:
    """Format the authoritative marks scheme for injection into the grading prompt."""
    if not marks_scheme or not marks_scheme.items:
        return ""
    lines = [
        "=== OFFICIAL MAXIMUM MARKS (AUTHORITATIVE — DO NOT CHANGE) ===",
        "These max marks are fixed by the question paper and are IDENTICAL for every student.",
        "Produce EXACTLY these sub-parts in `questions`, each with `max_score` equal to the value here,",
        "and grade each one SEPARATELY. An unattempted part scores 0 but KEEPS its max and must still",
        "appear. The grand total max is the sum below and never changes.",
        "",
    ]
    for it in marks_scheme.items:
        d = f" — {it.description}" if it.description else ""
        lines.append(f"  {it.qid}: max {it.max:g}{d}")
    total = sum(float(it.max) for it in marks_scheme.items)
    lines.append(f"  TOTAL: {total:g}")
    return "\n".join(lines)


def _norm_opt(s: str) -> str:
    """Normalise an option letter to a single uppercase A–D (or ''), so a hand-written
    '(b)' / 'B.' / 'option B' all compare equal. Used for deterministic MCQ scoring."""
    m = re.search(r"[A-Da-d]", str(s or ""))
    return m.group(0).upper() if m else ""


def _is_objective(q) -> bool:
    """An item is scored on the objective (all-or-nothing) path when the model flags it
    `objective`, OR — for backward compatibility with reports made before that flag — when
    it supplied a correct-option letter from the key."""
    return bool(getattr(q, "objective", False)) or bool(_norm_opt(getattr(q, "correct_option", "")))


def _snap_half(x: float) -> float:
    """Round to the nearest 0.5 — school marks come in half-mark steps, never 0.25/4.25."""
    try:
        return math.floor(float(x) * 2 + 0.5) / 2
    except (TypeError, ValueError):
        return 0.0


def _reconcile_report(report: GradeReport, marks_scheme: "MarksScheme | None" = None) -> GradeReport:
    """Make the report internally consistent and lock the denominator to the scheme.

    1. Parts define the whole: a question's score/max are the sums of its criteria.
    2. When a marks scheme is supplied it is authoritative — override each matched
       question's max_score, and append any sub-part the model dropped (as 0/max,
       flagged for the teacher) so the denominator can never come out short.
    3. Recompute every total (and section_totals) from the per-question source of
       truth, so the cover page, the app summary, and the page marks all agree.
    """
    for q in report.questions:
        # Objective questions are scored deterministically below (after the max is
        # finalised) — skip the criteria maths for them here.
        if _is_objective(q):
            continue
        if q.criteria:
            for c in q.criteria:                       # half-mark steps only
                c.awarded = _snap_half(c.awarded)
            q.score = round(sum(float(c.awarded) for c in q.criteria), 4)
            q.max_score = round(sum(float(c.max) for c in q.criteria), 4)
        else:
            q.score = _snap_half(q.score)

    if marks_scheme and marks_scheme.items:
        canon = {_norm_qid(it.qid): it for it in marks_scheme.items}
        seen: set[str] = set()
        for q in report.questions:
            n = _norm_qid(q.qid)
            if n in canon:
                q.max_score = float(canon[n].max)
                seen.add(n)
        for n, it in canon.items():
            if n not in seen:
                report.questions.append(QuestionMark(
                    qid=it.qid, score=0.0, max_score=float(it.max),
                    page=1, y_fraction=0.5, remark="[not graded — verify]",
                ))

    for q in report.questions:
        q.max_score = max(0.0, float(q.max_score))
        if _is_objective(q):
            q.criteria = []
            q.mark_style = "single"
            chosen = _norm_opt(getattr(q, "chosen_option", ""))
            correct = _norm_opt(getattr(q, "correct_option", ""))
            if chosen and correct:
                # CODE PATH — both option letters are available, so score it
                # deterministically by comparing the letters. This is the reliable case:
                # a correct option can't be marked wrong and every student grades alike.
                if chosen == correct:
                    q.score = q.max_score
                    q.remark = ""
                else:
                    q.score = 0.0
                    q.remark = f"Incorrect option (chose {chosen}, correct {correct})"
            else:
                # AI PATH — the option letters aren't both available (the student wrote
                # the VALUE, not a clear letter). Fall back to the model's value-equality
                # judgement instead of code. Never silently zero an item that has writing.
                attempted = (
                    bool(getattr(q, "attempted", True))
                    or bool(getattr(q, "is_correct", False))
                    or bool(chosen)
                )
                if not attempted:
                    q.score = 0.0
                    q.remark = "Unattempted"
                elif bool(getattr(q, "is_correct", False)):
                    q.score = q.max_score
                    q.remark = ""
                else:
                    q.score = 0.0
                    q.remark = "Incorrect answer"
        else:
            q.score = min(max(0.0, float(q.score)), q.max_score)

    report.total_score = round(sum(q.score for q in report.questions), 2)
    if marks_scheme and marks_scheme.items:
        report.max_total = round(sum(float(it.max) for it in marks_scheme.items), 2)
    else:
        report.max_total = round(sum(q.max_score for q in report.questions), 2)

    sec: dict[str, dict] = {}
    order: list[str] = []
    for q in report.questions:
        top = _top_level_qid(q.qid)
        if top not in sec:
            sec[top] = {"qid": top, "score": 0.0, "max_score": 0.0}
            order.append(top)
        sec[top]["score"] += q.score
        sec[top]["max_score"] += q.max_score
    report.section_totals = [
        {"qid": sec[t]["qid"], "score": round(sec[t]["score"], 2),
         "max_score": round(sec[t]["max_score"], 2)}
        for t in order
    ]
    return report


# ---------- Helpers ----------

def pdf_or_image_to_pngs(data: bytes, filename: str, dpi: int = 150) -> list[bytes]:
    """Return a list of PNG bytes, one per page (PDF) or the image itself."""
    if not data:
        raise ValueError(
            f"'{filename or 'file'}' came through empty (0 bytes). The upload may not "
            "have finished or the file reference went stale — please re-upload it and try again."
        )
    lower = filename.lower()
    if lower.endswith(".pdf"):
        doc = fitz.open(stream=data, filetype="pdf")
        pages = []
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            pages.append(pix.tobytes("png"))
        doc.close()
        if not pages:
            raise ValueError(f"'{filename}' has no pages — it may be a corrupt PDF. Re-upload and retry.")
        return pages
    # treat as image
    img = Image.open(BytesIO(data)).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return [buf.getvalue()]


def exam_context_block(student_class: str | None, subject: str | None) -> str:
    """Build a short '=== EXAM CONTEXT ===' text block from class + subject.

    Returns '' when neither is supplied so callers can skip adding it.
    """
    bits = []
    if student_class and student_class.strip():
        bits.append(f"Class / Grade: {student_class.strip()}")
    if subject and subject.strip():
        bits.append(f"Subject: {subject.strip()}")
    if not bits:
        return ""
    return (
        "=== EXAM CONTEXT ===\n"
        + "\n".join(bits)
        + "\nGrade strictly to the standard expected at this class level, and apply the "
        "marking conventions normal for this subject. Calibrate difficulty, expected "
        "depth, and language expectations accordingly."
    )


def _image_block(png_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png_bytes).decode("utf-8"),
        },
    }


# Printed question-paper / answer-key images are downscaled before being sent to the model
# to cut input-image tokens (cost). Printed text stays legible at this size; student
# handwriting is NEVER downscaled, so grading accuracy is unaffected.
_QP_IMAGE_MAX_EDGE = 1500


def _downscale_for_model(png: bytes, max_edge: int = _QP_IMAGE_MAX_EDGE) -> bytes:
    """Return a smaller PNG (long edge ≤ max_edge) for printed pages; original if already
    small or on any error. Same PNG format — safe for both provider image payloads."""
    try:
        img = Image.open(BytesIO(png))
        w, h = img.size
        long_edge = max(w, h)
        if long_edge <= max_edge:
            return png
        scale = max_edge / float(long_edge)
        img = img.convert("RGB").resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return png


# ---------- Main grading call ----------

SYSTEM_PROMPT = """You are an experienced school exam evaluator grading handwritten answer sheets.

You will be shown:
1. The QUESTION PAPER (images of the exam questions)
2. The ANSWER KEY / MARKING SCHEME (model answers and how marks should be awarded)
3. The STUDENT'S ANSWER SHEET (images of the student's handwritten answers, page by page)

# GRADING METHOD — TWO PASSES

Internally work in two passes before emitting the final GradeReport:

**Pass 1 — Extraction.** Walk the question paper and enumerate every sub-question and sub-part (Q1.i, Q1.ii, ..., Q6.ii.a, Q6.ii.b, Q6.ii.c, Q6.ii.d, ...). For each one, locate the student's answer on the answer sheet and transcribe (mentally) what they wrote. If a sub-part is unattempted, record that explicitly — do not silently fold it into a sibling.

**Pass 2 — Per-sub-part grading.** Grade each sub-part on its own against the rubric. Never holistically eyeball a multi-part question and pick a single number. Each (i)(ii)(iii) and each (a)(b)(c)(d) MUST appear as its OWN criterion in `criteria`, with its own `awarded`, `max`, and `met` — even when the parent question has a combined mark total. Sum at the end. This is the single biggest source of under-marking, so do not skip it.

# MAXIMUM MARKS COME FROM THE QUESTION PAPER — NEVER FROM THE STUDENT

The maximum marks for each question and sub-part are a property of the QUESTION PAPER, not of what the student wrote. Get them right or every total is wrong:

- Read the printed marks tags on the paper — "[1 Mark]", "[2 Marks]", "[3 Marks]", "[5 Marks]", etc. Those printed numbers ARE the max marks. Do not invent them or infer them from difficulty or from how much the student wrote.
- EVERY lettered part (a),(b) and EVERY roman part (i),(ii),(iii) carries its OWN printed marks. Emit each as its own entry and sum them for the parent. Example: if Q16(a) is printed "[3 Marks]" and Q16(b) is printed "[2 Marks]", then emit Q16.a (max 3) and Q16.b (max 2) — so Q16's max is 5, NOT 4 and NOT 3.
- The grand total (`max_total`) is the SUM of every printed mark on the paper and is IDENTICAL for every student of that paper. It does not shrink because a student skipped a part.
- An unattempted or blank sub-part still has its full max — it scores 0 AWARDED out of its max, and it MUST still appear in `questions`. NEVER drop a sub-part because the student left it blank, and never merge a sub-part's marks into a sibling.
- If an "=== OFFICIAL MAXIMUM MARKS ===" block is supplied below, it is AUTHORITATIVE: reproduce exactly those sub-parts with exactly those max values, and grade each one separately.
- Before emitting, add up every `max_score`. It MUST equal the paper's printed total. If it doesn't, you misread a marks tag or dropped a sub-part — find it and fix it.

# PARTIAL CREDIT RULES (apply to every criterion)

Default to partial credit, not zero, whenever the student demonstrates partial understanding. Use this scale per criterion:

- **Full marks (100%)** — answer matches the rubric in content and is acceptably expressed.
- **Most marks (~75%)** — core idea is correct but one supporting detail is missing or one minor factual slip.
- **Half marks (~50%)** — core idea is in the right direction but imprecise wording, partial coverage, or one of two required points missing. Example: "scared" in place of "had stopped / was absent" — the meaning is in the ballpark, so award 0.5, not 0.
- **Quarter marks (~25%)** — student is on the wrong track but mentions one relevant keyword or shows minimal recognition.
- **Zero (0%)** — wrong, unattempted, or unrelated.

Do NOT give 0 just because the wording differs from the model answer. Ask: does the answer convey the rubric's core idea? If yes, it earns at least 50%.

# CONTENT VS LANGUAGE (LANGUAGE / literature papers ONLY)

This split applies ONLY to language / literature papers (English, Hindi, Sanskrit, etc.). For content subjects (Social Science, Science, History, Geography, Civics, Economics, Math) DO NOT use it — grade on content/keywords only, with NO language/expression criterion and NO spelling/grammar deduction.

For descriptive answers worth 3+ marks on a LANGUAGE paper (paragraphs, diary entries, story completions, 40–50 word and 100–120 word answers in literature), split the marks into two SEPARATE criteria:

- **"Content / ideas"** — worth ~70% of the question's marks. Judge factual accuracy, coverage of required points, relevance to the prompt. Grammar errors do NOT reduce this score.
- **"Language / expression"** — worth ~30% of the question's marks. Judge grammar, sentence construction, vocabulary, spelling. Content gaps do NOT reduce this score.

Award each independently, then sum. This prevents grammar weakness from bleeding into content marks and vice versa. For short objective answers (MCQ, fill-in-the-blank, one-word, 1-mark items), do NOT split — use a single criterion.

# OBJECTIVE ANSWERS ARE ALL-OR-NOTHING

The graded partial-credit scale above applies to descriptive / multi-step answers ONLY. For OBJECTIVE items — MCQ choices, true/false, match-the-following, one-word answers, antonyms/synonyms, fill-in-the-blanks — there is no "close enough": the answer is either correct (full marks) or wrong (zero). Do NOT award 25–50% to a wrong antonym, a wrong MCQ option, or a wrong fill-in just because the student attempted it or was "in the ballpark". The ONLY tolerance is an obvious spelling slip where the intended correct word is unambiguous (e.g. "begining" for "beginning") — accept that as correct. Everything else objective is 0.

# READING MCQ / OBJECTIVE CHOICES — TRUST THE PAGE IMAGE, NOT THE OCR LETTER

All-or-nothing cuts both ways: do not mark a CORRECT MCQ wrong either. Decide what the student actually chose by LOOKING at the page image, using BOTH signals together:
- the option LETTER they circled or wrote — (A)/(B)/(C)/(D), and
- the option VALUE / expression they wrote next to it — e.g. "xy²", "−10", "four decimal places", "20".

Award FULL marks if EITHER the chosen letter OR the written value matches the correct option in the key. OCR very frequently misreads a hand-circled option letter (a circled "B" can come through as "R", "P", "8", "13", etc.), so NEVER mark an MCQ wrong solely because the transcribed letter looks off — verify against the image and the written value, and give the legible correct value precedence over a garbled letter. Only mark it wrong when the student's actual choice, as seen on the page, genuinely differs from the key. Grade the SAME MCQ identically no matter which student wrote it.

# OBJECTIVE QUESTIONS — JUDGE BY VALUE, THE SYSTEM SCORES IN CODE

For EVERY objective question (MCQ, assertion-reason, true/false, match-the-following, one-word / one-value answers) you must NOT decide the score yourself — the system scores it in code so a correct answer is never marked wrong and every student is graded identically. Students very often write the ANSWER VALUE (e.g. "−10", "20", "four decimal places", "A is true but R is false") instead of circling a letter, so do NOT depend on reading a letter. On each such QuestionMark set:
- `objective`: **true**.
- `attempted`: **true** if the student wrote ANYTHING for this item (a value, word, or letter — even a wrong one). Set **false** ONLY when the item is genuinely BLANK. This is critical: an item that has writing must NEVER be reported as not-attempted just because you cannot tell which letter it maps to.
- `is_correct`: **true** if what the student wrote MATCHES the key's correct answer compared BY VALUE; otherwise **false**.
- `chosen_option` / `correct_option`: the option LETTERS — fill BOTH only when you can read the student's choice AND the key's answer clearly. When BOTH are present the system scores by comparing them directly (deterministic); when either is missing the system falls back to your `is_correct` value judgement. So: give clear letters when you have them, and always set `is_correct` for the value-only case. Leaving the letters "" does NOT make the item unattempted.
- Leave `criteria` empty and set `mark_style` to "single".
Set `objective` true ONLY for genuinely objective items; leave it false for every descriptive / short / long answer (those keep normal criteria-based grading). Grade the SAME objective item identically no matter which student wrote it or how messy the handwriting is.

# SCORE ONLY WHAT THE RUBRIC MARKS — DO NOT INVENT DEDUCTIONS

Award and deduct marks ONLY for criteria defined in the answer key / marking scheme. Do not invent your own criteria.
- For NON-LANGUAGE subjects (Social Science, History, Geography, Civics, Economics, Science, Mathematics, etc.), spelling, grammar, handwriting, neatness and presentation carry NO marks — NEVER deduct for them. If the content is correct, award full marks even when the writing has spelling/grammar errors. You may note it in the remark, but the score MUST NOT drop for it.
- Apply the "Content vs Language" split (below) ONLY to LANGUAGE / literature papers (English, Hindi, Sanskrit, etc.), where expression genuinely carries marks. For all other subjects there is NO separate "Language / expression" criterion.
- Award marks in steps of 0.5 only (…, 1.0, 1.5, 2.0). NEVER produce quarter marks like 4.25 or 0.25 — round to the nearest 0.5.

# SUBJECT-SPECIFIC CONVENTIONS

If an "=== EXAM CONTEXT ===" block names the subject, grade with that subject's conventions. If it does not, infer the subject from the question paper.

- **Math / Physics / Chemistry (numerical problems):** Award METHOD marks step by step — correct formula, correct substitution, and each algebra/arithmetic step earn their own marks even when a later step is wrong. Apply *error-carried-forward*: if the student uses a wrong earlier value correctly in later steps, penalise it ONCE, not at every step. A correct final value carries the question only if working is shown (or the rubric allows answer-only). Units and significant figures matter only if the rubric demands them.
- **Science (Biology / theory):** Award marks per required keyword/concept the rubric lists, and for correctly drawn/labelled diagrams. A diagram or labelled figure can earn marks on its own — anchor those to the orange diagram region, not a text line.
- **English / languages / humanities (descriptive):** Apply the Content vs Language split above. Reward relevance to the prompt, coverage of required points, and structure; judge expression separately.

**Calibrate to the class/grade level given.** For lower grades, weight communication of the idea over grammatical perfection; for higher grades, hold expression and precision to a stricter standard. Expected depth and the bar for "full marks" rise with the class level.

# CALIBRATION — DO NOT OVER-PENALISE (especially language)

Real teachers reward effort and understanding. The most common failure of an AI grader is scoring noticeably BELOW what a fair human marker would give, by punishing weak English too hard. Apply these floors:

- **Language / expression is generous by default.** If the writing is UNDERSTANDABLE despite grammar, spelling, and sentence-structure errors, award MOST of the language marks (~70%+). Drop the language sub-score below half ONLY when the errors genuinely obscure the meaning — not merely because errors are frequent. For school-level and second-language writers, treat frequent-but-readable mistakes as normal, not as near-zero.
- **Content has a floor when the main idea is present.** If the student conveys the central idea and attempts the required points, award at least ~60% of the content marks even when details are thin or the wording is clumsy. Reserve low content scores for answers that are off-topic, factually wrong, or missing the key points.
- **No double jeopardy.** An error already counted against "Language" must NOT also pull down "Content", and vice-versa. Count each weakness once.
- **Read charitably.** Unclear handwriting, minor spelling, and awkward phrasing get the benefit of the doubt. Never mark a substantively correct answer as "incorrect" just because it is hard to read or poorly worded.
- **Sanity-check the overall level before emitting.** A sincere attempt that shows real understanding but has weak English should land roughly in the 60–75% range overall — NOT 45–55%. Totals below ~50% should be reserved for papers that are largely wrong, off-topic, or unattempted. If your running total is dragging well below what the content alone would justify, your language penalties are too harsh — revisit them. (This calibration applies to descriptive work; objective items remain all-or-nothing.)

# LITERATURE / PRESCRIBED-TEXT QUESTIONS (you may not have the source text)

Language papers (English, Hindi, etc.) often test prescribed NCERT/board chapters, poems, and stories that are NOT reproduced on the question paper — the question just names them ("Why did Albert Einstein leave his school?", "What shows that the poet loved his mother — 'Rain on the Roof'", "How did Santosh carve her own destiny?", "changes in the child once separated from his parents — 'The Lost Child'", "Who was Behrman?"). You cannot see the chapter, so decide what "correct" means in this strict priority order:

1. **Answer key first.** If the supplied ANSWER KEY / MARKING SCHEME covers the question, it is authoritative — grade against it and ignore your own recollection if they differ.
2. **Your own knowledge second.** If no key covers it, use your knowledge of the named text. You very likely know the common NCERT/CBSE prescribed chapters, poems, and stories — grade against the actual plot/characters/themes, and mark factual errors (wrong character, wrong event, wrong reason) accordingly.
3. **If genuinely unsure of the specific text, DO NOT bluff.** When you are not confident you know the exact chapter/extract being referenced, do NOT invent a "model answer" and do NOT penalise the student for content you cannot verify. Grade on what you CAN judge — relevance to the prompt, coherence, structure, effort, and language — give the benefit of the doubt on content, and append "[verify against text]" to the remark so a human can confirm. Never mark a content point wrong merely because it contradicts a guess about a text you are unsure of.

For **reference-to-context** questions where the extract IS printed on the paper (e.g. Q6, Q7 here), the passage is in front of you — grade those normally and strictly from the printed extract; this caveat does not apply to them.

# TICK / CROSS PLACEMENT — DO NOT SKIP THIS SECTION

The whole grading sheet is useless if ticks land on the wrong line. Every overlaid page shows:
- BLUE `[PnLk]` labels on every text line.
- ORANGE `[PnDk]` outlines on figures / tables / diagrams.
- RED labels on question / section headers (`Q.27)`, `(a)`, `(i)`, `Section-B`).

Pick `step_line_id` by LOOKING at the page image, not by guessing from the transcript. Hard rules:

1. **Never anchor on a RED header.** Always pick a line containing actual student working.
2. **One anchor per criterion within a question.** Never reuse the same `step_line_id` across two criteria of the same question — the ticks would pile up.
3. **Pick the LAST line of evidence** for that step (where the student lands the answer/result), not the first line of a multi-line answer.
4. **Each sub-part gets its own anchor.** (i)(ii)(iii)(iv)(v) and (a)(b)(c)(d) — never share.
5. **Diagrams use `PnDk` (orange).** For steps satisfied by a figure / table / hand-drawn sketch with no text, use the orange diagram-region anchor — that is what those anchors are for.
6. **For a "Language / expression" criterion on a descriptive answer**, anchor on the LAST line of that answer (the line where the answer ends). Do NOT pick a random line in the middle; do NOT reuse the "Content" criterion's anchor.
7. **For a "Content / ideas" criterion**, anchor on the line where the strongest content evidence appears (often the line containing the key fact / keyword the rubric asks for).

If you cannot identify a confident anchor, leave `step_line_id` empty — that is better than pointing to the wrong line.

# STRUCTURAL RULES

- Identify every sub-question that requires evaluation (e.g. Q1.A.1, Q1.A.2, Q2.B, Q4.B.iii ...).
- For EACH sub-question, read the student's handwritten answer carefully and award marks against the rubric.
- For every sub-question worth more than 1 mark, populate `criteria` with one entry PER rubric step / sub-part / content-vs-language split as described above. The sum of `awarded` MUST equal `score`; the sum of `max` MUST equal `max_score`. Never emit a multi-mark question with empty `criteria`.
- Set `mark_style` for every question to control how it is marked ON THE SHEET, the way a real teacher would:
  * `"per_step"` — a tick + the marks beside EACH step/point. Use for numerical/calculation work and multi-step derivations, AND for ENUMERATED answers where each point is on its own line ("state four causes", "list three differences", "give two examples", fill-in-the-blanks) and LABELLED DIAGRAMS. Give each criterion its own `step_line_id` (a text line or `PnDk` diagram region) so each point gets its own tick.
  * `"single"` — ONE tick + the total + a margin remark. Use for FLOWING PROSE: essays, paragraphs, descriptive English/Hindi/literature answers, history/civics explanations — where ticking every sentence would be wrong.
  Decide explicitly; only leave `"auto"` when genuinely unsure.

Worked examples of the per-sub-part + partial-credit + content/language rules:

  * **Multi-part literature extract (e.g. Q6.ii worth 5 marks with parts a/b/c/d):**
    - Criterion "(a) inference from surroundings" — 1/1 met
    - Criterion "(b) celestial entity MCQ" — 1/1 met
    - Criterion "(c) effectiveness of description, 40 words" — 1/2 (student named tone but missed the humour angle — half credit, not zero)
    - Criterion "(d) substitute for 'taken time off'" — 0.5/1 ("scared" is wrong direction but acknowledges absence — quarter to half credit)
    → score = 3.5 / 5

  * **Long-form literature answer (e.g. Q10 worth 6 marks, "How did Santosh carve her own destiny?"):**
    - Criterion "Content: defying tradition, hard work, Everest achievement" — 3/4 (covered most points but factual slip on "first overall" vs "first twice")
    - Criterion "Language / expression" — 1/2 (numerous grammar errors but meaning communicated)
    → score = 4 / 6

  * **Grammar set (Q3 — 10 items × 1 mark):** one criterion per item, met true/false. Count carefully and DO NOT mis-tally.

  * **Math:** "Correct formula" (1/1), "Correct substitution" (1/1), "Arithmetic in step 3" (0/1) → 2/3.

  * **Fill-in-the-blank set (4 items, 0.5 each):** one criterion per blank with met=true/false.

- For any sub-question where the student did NOT get full marks, write a short, specific remark explaining what was wrong or missing (e.g. "Lost 1 mark due to incorrect antonym for 'hostile'.").
- Identify the page number (1-indexed in the student's answer sheet) and an approximate vertical position (y_fraction between 0.0 and 1.0) where each answer starts, so the mark can be written in the left margin.
- Sum sub-question scores into section totals (Q1, Q2, ...) and an overall total.
- Write a brief overall_remarks paragraph (2-3 sentences) on strengths and areas to improve.
- Mark specific wrong words/phrases on the page so the student can SEE what cost them marks — like a real teacher's red pen. Use three annotation types:
  * `type: "cross"` — small red ✗ next to a wrong single-item answer (wrong MCQ choice, wrong antonym, wrong fill-in-the-blank).
  * `type: "strikethrough"` — red line through wrong wording: grammar errors (wrong tense, subject-verb disagreement), misspellings, wrong verb forms, redundant phrases. USE THIS INSIDE PROSE ANSWERS too — if a student writes "I am going" when context required "I was going", strike through "am going". This is exactly how teachers mark up paragraphs and diary entries.
  * `type: "circle"` — hand-drawn red circle around a FACTUAL error or wrong word choice in prose: e.g., circle "first overall" when the rubric required "first twice", circle a wrong character name, circle a wrong date. Reserve circle for content errors the student must NOTICE.
  Set `target_line_id` to the OCR line containing the wrong word and `target_word` to the exact text — that gives the tightest placement. For each prose answer that lost marks, add 1–4 inline annotations pointing at the actual offending words. Do NOT carpet-bomb the page with annotations — pick the most representative errors.

# KEEP THE OUTPUT COMPACT (this does NOT change how you grade)

Grade exactly as thoroughly as before — SAME number of criteria, SAME partial-credit judgement, SAME care. Only the TEXT you emit should be compact, so the response stays small:
- Criterion `description`: a 2–5 word LABEL, never a sentence (e.g. "correct formula", "substitution step", "(ii) deforestation point"). Do NOT write explanations or reasoning here.
- `remark`: ≤ 8 words, and ONLY for sub-questions that lost marks; empty string when full marks.
- OBJECTIVE questions (where you set `objective: true`): leave `criteria` EMPTY and `remark` EMPTY — the system scores and labels them in code, so any text here is wasted.
- `annotations`: only the 1–3 most important wrong words on an answer; never annotate every error.
- `overall_remarks`: 2–3 short sentences, no more.
NEVER reduce the NUMBER of criteria, drop a sub-part, or grade less carefully to save space — only shorten the wording. Granularity and accuracy come first; brevity applies only to the words.

# SELF-CHECK BEFORE EMITTING

Before you finalise the GradeReport, review each multi-part question once more and ask:
1. Does every (i)(ii)(iii) and (a)(b)(c)(d) appear as its own criterion?
2. For each criterion I scored 0, is the answer truly absent/unrelated — or does it deserve 25–50% partial credit because the core idea is in the ballpark?
3. For each long-form answer, did I split content from language, or did grammar errors silently pull the content score down?
4. Do the criterion sums match `score` and `max_score`?
5. Did I respect the CALIBRATION floors — is the language sub-score generous where the writing is still understandable, and does the overall total reflect a fair human marker rather than an over-harsh one?

Correct any issue you find before emitting.

Be fair, accurate, and consistent with the answer key. If handwriting is unclear, give the benefit of the doubt where reasonable but note illegibility in the remark. Award partial marks where the rubric allows — default to partial, not zero."""


# Appended to the system prompt ONLY for reasoning subjects (Physics / Chemistry /
# Mathematics, and the numerical/reasoning parts of a combined Science paper). In
# these subjects the reasoning IS part of the answer, so a right answer reached
# through wrong reasoning, an imprecise statement of a law, or wrong terminology
# must cost marks — unlike language/humanities, where wording is judged separately
# and generously. Self-gating text inside the block handles the unknown-subject case.
REASONING_RIGOR_BLOCK = """# REASONING RIGOR (Physics / Chemistry / Mathematics)

These rules apply ONLY when the subject is Physics, Chemistry, or Mathematics — or to the Physics/Chemistry/numerical-reasoning answers within a combined Science paper. If the paper is a language, literature, humanities, or biology-recall paper, IGNORE this entire section and grade as before.

In these subjects the *reasoning* is part of the answer, not just the final result. Grade the stated method, law, and terminology — not only the conclusion. This is a MODERATE strictness pass: it exists to stop conceptual and terminology errors from being silently ignored, NOT to mark harshly. Shown working is still rewarded generously.

1. **A correct final answer reached through a wrong principle/method does NOT earn full marks.** If the student lands the right number or option but the stated reasoning, formula, or law is wrong, award only the steps they genuinely earned and deduct for the flawed reasoning. Example: defining a couple's moment as "force × perpendicular distance from the pivot" is a conceptual error (a couple's moment is force × the distance BETWEEN the two forces, independent of any pivot) even if the arithmetic gives the right value — cap it at partial credit.

2. **Statements of laws, definitions, and principles must be precise and complete.** A loosely or incompletely stated law is not full marks. Example: "all the forces on a body must be zero" for static equilibrium is imprecise — the condition is that the *vector sum* of the forces is zero (and the vector sum of moments is zero); award ~50–75%, not full. Reserve full marks for a correctly and completely stated law.

3. **Penalise wrong terminology and wrong physical reasoning even when the gist is close.** Examples: explaining a stone flying off a broken string by "centrifugal force pulling it away" is the wrong reason — it is inertia (Newton's first law) once the centripetal force stops; swapped clockwise/anticlockwise labels; "speed" used for "velocity"; wrong sign conventions; wrong units quoted as part of a definition. Each costs marks proportional to the error.

4. **But keep rewarding shown method — error-carried-forward still applies.** Correct formula, correct substitution, and each correct step earn their marks individually; a single propagated arithmetic slip is penalised once, not at every later step. Do not let this rigor push a genuine, well-worked solution into harsh territory — the aim is accuracy about reasoning, not severity.

5. **Name the specific reasoning error in the remark** (e.g. "a couple's moment is independent of the pivot", "equilibrium needs the vector SUM of forces = 0, not each force = 0", "flies off due to inertia, not centrifugal force") so the student learns what was wrong, not merely that marks were lost."""


# Subjects whose answers hinge on reasoning/derivation, where REASONING_RIGOR_BLOCK applies.
_REASONING_SUBJECT_KEYWORDS = ("physics", "chemistry", "math", "science", "phy ", "chem")
# Hints that a subject is descriptive/language/humanities (rigor block omitted),
# UNLESS the name also explicitly contains physics/chemistry/math.
_NON_REASONING_HINTS = (
    "social", "english", "hindi", "sanskrit", "urdu", "language", "literature",
    "history", "geograph", "civic", "political", "economic", "moral", "art",
)


def _is_reasoning_subject(subject: str | None) -> bool | None:
    """True for reasoning subjects (Physics/Chem/Maths/Science), False for clearly
    language/humanities subjects, None when the subject is unknown/blank (let the
    self-gating prompt text decide based on the question paper)."""
    if not subject or not subject.strip():
        return None
    s = subject.strip().lower()
    has_hard = any(k in s for k in ("physics", "chemistry", "math"))
    if any(h in s for h in _NON_REASONING_HINTS) and not has_hard:
        return False
    if any(k in s for k in _REASONING_SUBJECT_KEYWORDS):
        return True
    return None


def build_system_prompt(subject: str | None) -> str:
    """Grading system prompt, with the reasoning-rigor block appended for reasoning
    subjects (or unknown subjects, where the block self-gates). Omitted entirely for
    language/humanities papers so their generous wording-tolerant calibration stands."""
    if _is_reasoning_subject(subject) is False:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + "\n\n" + REASONING_RIGOR_BLOCK


RUBRIC_GEN_PROMPT = """You are an expert exam-paper analyst. Given a question paper, produce a detailed marking rubric that a grader can apply mechanically.

For EVERY sub-question and EVERY sub-part (Q1.i, Q1.ii, ..., Q6.ii.a, Q6.ii.b, Q6.ii.c, Q6.ii.d, ...):
- State the sub-question / sub-part identifier and its maximum marks. Do NOT collapse multi-part questions (e.g. Q6.ii worth 5 marks with parts a/b/c/d) into a single line — each part gets its own bullet with its own mark budget that sums to the parent total.
- Briefly describe what the question asks.
- List the model answer or the specific key points/criteria a student must include to earn full marks. Use concrete keywords/phrases the grader should look for.
- Describe how partial credit is awarded on a graded scale, not all-or-nothing. Default to a scale like:
  * Full marks — answer matches in content and is acceptably expressed.
  * ~75% — core idea correct, one supporting detail missing OR one minor factual slip.
  * ~50% — core idea in the right direction but imprecise wording or only half the required points.
  * ~25% — wrong track but one relevant keyword mentioned.
  * 0 — wrong, unattempted, or unrelated.
  Note explicitly where "right idea, wrong wording" should still earn ~50%.
- For descriptive / long-form answers worth 3+ marks (paragraphs, diary entries, story completions, 40–50 word and 100–120 word literature answers), split the marks into:
  * "Content / ideas" — ~70% of the marks (factual accuracy, coverage of required points, relevance).
  * "Language / expression" — ~30% of the marks (grammar, sentence construction, vocabulary, spelling).
  State both budgets explicitly so grammar errors don't bleed into content scoring. Do NOT split for short objective answers (MCQ, fill-in-the-blank, one-word, 1-mark items).
- For "answer ANY N of M" style questions, note that and clarify how the grader should pick which attempted answers to score.
- **For literature / prescribed-text questions whose source is NOT reproduced on the paper** (questions that merely name a chapter, poem, or story — e.g. "Why did Einstein leave school?", "Who was Behrman?", "'Rain on the Roof'", "'The Lost Child'"): write the expected model answer / key points from your knowledge of that named NCERT/board text so the grader has something concrete to mark against. If you are NOT confident you know the specific text, do not fabricate — instead write the bullet as "[NEEDS ANSWER KEY — source text not on paper; teacher to supply key points]" so the teacher fills it in before grading. (Reference-to-context questions whose extract IS printed on the paper need no such note — derive their key points from the printed extract.)

Format the rubric as clean Markdown with one section per top-level question (## Q1, ## Q2, ...) and bullet points per sub-question / sub-part. Be specific and concrete — a teacher should be able to grade from this rubric alone, applying the partial-credit and content/language rules above without further interpretation.

At the top, include a one-line total: "**Total: X marks**" reflecting the sum of all sub-question maximums."""


# ---------- Deterministic marks-scheme extraction (regex on the printed tags) ----------
# The printed "[N Mark(s)]" tag is data, not something to reason about — read it with a
# regex off the PDF text layer (free, instant, can't hallucinate). The LLM extractor below
# is only a fallback for scanned papers / images that have no text layer.

# A marks tag: "[1 Mark]", "[2 Marks]", "(3 marks)", "[5 mark]", "[2 M]" — bracket/paren,
# with "M" / "Mark" / "Marks" (the \b stops it matching "[2 metres]").
_MARKS_TAG_RE = re.compile(r"[\[(]\s*(\d+(?:\.\d+)?)\s*m(?:ark)?s?\b\s*[\])]", re.IGNORECASE)
# The paper's printed TOTAL: "Max. Marks: 40", "Maximum Marks - 40", "M.M. 40", "Total Marks: 40".
_TOTAL_MARKS_RE = re.compile(
    r"(?:max(?:imum)?\.?\s*marks?|m\.?\s*m\.?|total\s*marks?)\s*[:\-]?\s*(\d{1,3})",
    re.IGNORECASE)
# A top-level question number at line start: "12." or "12)".
_QNUM_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]")
# Lettered sub-part "(a)".."(h)" — lowercase only, so MCQ options "(A)".."(D)" are ignored.
_SUBLETTER_RE = re.compile(r"^\s*\(?([a-h])\)")
# Roman sub-part "(i)".."(x)" — lowercase only.
_SUBROMAN_RE = re.compile(r"^\s*\(?(i{1,3}|iv|v|vi{0,3}|ix|x)\)")
# Lenient tag for a MALFORMED bracket — "[2 Marks" with the closing "]" missing (a real
# typo seen on papers). Requires an opening bracket + the word "mark", so it can't match
# stray prose. Used only when the strict tag found nothing on the line.
_MARKS_TAG_OPEN_RE = re.compile(r"[\[(]\s*(\d+(?:\.\d+)?)\s*marks?\b", re.IGNORECASE)
# A "N Mark(s) Each" heading that sets the per-question marks for a whole block (e.g.
# "Multiple Choice Questions: [1 Mark Each]") instead of tagging each question.
_MARKS_EACH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*marks?\s+each", re.IGNORECASE)
# Section headings that END a "… Each" block (the next section is marked differently).
_SECTION_HDR_RE = re.compile(
    r"(answer\s+type|very\s+short|short\s+answer|long\s+answer|case\s+study|section\b)",
    re.IGNORECASE)


def total_marks_from_text(pages_text: list[str]) -> float | None:
    """The paper's printed maximum total ('Max. Marks: 40'), read deterministically."""
    for page in pages_text:
        m = _TOTAL_MARKS_RE.search(page)
        if m:
            return float(m.group(1))
    return None


def _marks_from_right_column(doc: "fitz.Document") -> list[MarksItem]:
    """Read per-question marks printed in a right-hand column of bare numbers (the common
    Indian-board layout: '1', '2', '3' aligned to each question's row). Associates each
    right-column number with the question number on the same row. Top-level only."""
    items: list[MarksItem] = []
    seen: set[str] = set()
    for page in doc:
        words = page.get_text("words")  # (x0,y0,x1,y1, text, block, line, wordno)
        if not words:
            continue
        W = page.rect.width
        qnums: list[tuple[float, int]] = []   # (y_center, qnum) at far left
        marks: list[tuple[float, int]] = []   # (y_center, value) at far right
        for w in words:
            x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], (w[4] or "").strip()
            yc = (y0 + y1) / 2.0
            mqn = re.fullmatch(r"(\d{1,2})[.)]?", txt)
            if mqn and x0 < W * 0.18:
                qnums.append((yc, int(mqn.group(1))))
            elif re.fullmatch(r"\d{1,2}", txt) and x0 > W * 0.82:
                v = int(txt)
                if 1 <= v <= 20:
                    marks.append((yc, v))
        if not qnums or not marks:
            continue
        qnums.sort()
        for ymark, val in sorted(marks):
            above = [q for q in qnums if q[0] <= ymark + 8]  # question at/above this row
            if not above:
                continue
            qn = above[-1][1]
            qid = f"Q{qn}"
            if qid in seen:
                continue
            seen.add(qid)
            items.append(MarksItem(qid=qid, max=float(val), description=""))
    return items


def marks_scheme_from_text(pages_text: list[str]) -> MarksScheme:
    """Parse the per-part maximum marks from question-paper TEXT (no LLM, no cost).

    Walks the lines tracking the current question number and sub-part, and attaches each
    printed "[N Marks]" tag to that context. Pure function over text so it is unit-testable
    without a PDF. Returns a MarksScheme (possibly empty if no tags were found).
    """
    items: list[MarksItem] = []
    seen: set[str] = set()
    cur_q: int | None = None
    cur_sub: str | None = None
    qtext: dict[int, str] = {}        # top-level qnum -> the question's text (for the "what it is" column)

    def _sub_on(s: str) -> str | None:
        m = _SUBROMAN_RE.match(s) or _SUBLETTER_RE.match(s)
        return m.group(1).lower() if m else None

    def _clean(s: str) -> str:
        return _MARKS_TAG_OPEN_RE.sub("", _MARKS_TAG_RE.sub("", s)).strip(" .:-)")[:60]

    # Pass 1: explicit per-question "[N Marks]" tags (strict, then lenient for an unclosed
    # bracket). "… Each" heading lines are skipped here — they're handled in pass 2. Also
    # remember each question's TEXT (off its number line) for the editable table's label.
    for page in pages_text:
        for raw in page.splitlines():
            line = raw.strip()
            if not line or _MARKS_EACH_RE.search(line):
                continue
            mq = _QNUM_RE.match(line)
            if mq:
                cur_q = int(mq.group(1))
                cur_sub = _sub_on(line[mq.end():].lstrip())   # e.g. "16. (a) Prove ..."
                rem = _clean(line[mq.end():])
                if rem and cur_q not in qtext:
                    qtext[cur_q] = rem
            else:
                sub = _sub_on(line)
                if sub is not None:
                    cur_sub = sub
            matches = list(_MARKS_TAG_RE.finditer(line)) or list(_MARKS_TAG_OPEN_RE.finditer(line))
            for mm in matches:
                if cur_q is None:
                    continue
                qid = f"Q{cur_q}" + (f".{cur_sub}" if cur_sub else "")
                if qid in seen:
                    continue
                seen.add(qid)
                desc = qtext.get(cur_q) or _clean(line)        # prefer the question text
                items.append(MarksItem(qid=qid, max=float(mm.group(1)), description=desc))

    # Pass 2: "N Mark(s) Each" headings — apply N to every top-level question in that block
    # (e.g. the MCQs under "Multiple Choice Questions: [1 Mark Each]") that wasn't tagged
    # explicitly. The block ends at the next "Each" heading or section heading.
    cur_each: float | None = None
    for page in pages_text:
        for raw in page.splitlines():
            line = raw.strip()
            if not line:
                continue
            me = _MARKS_EACH_RE.search(line)
            if me:
                v = float(me.group(1))
                cur_each = v if 0 < v <= 10 else None
                continue
            if _SECTION_HDR_RE.search(line):
                cur_each = None
                continue
            mq = _QNUM_RE.match(line)
            if mq and cur_each is not None:
                qn = int(mq.group(1))
                qid = f"Q{qn}"
                if qid not in seen:
                    seen.add(qid)
                    items.append(MarksItem(qid=qid, max=cur_each, description=qtext.get(qn, "")))

    return MarksScheme(total=round(sum(it.max for it in items), 3), items=items)


def _columnar_text(page: "fitz.Page") -> str:
    """Extract a page's text in column-aware reading order (left column, then right)."""
    blocks = [b for b in page.get_text("blocks")
              if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()]
    if not blocks:
        return ""
    mid = page.rect.width / 2.0
    left = [b for b in blocks if (b[0] + b[2]) / 2.0 < mid]
    right = [b for b in blocks if (b[0] + b[2]) / 2.0 >= mid]

    def _ordered(bs: list) -> str:
        return "\n".join(b[4] for b in sorted(bs, key=lambda b: (round(b[1], 1), b[0])))

    if left and right:                       # genuine two-column layout
        return _ordered(left) + "\n" + _ordered(right)
    return _ordered(blocks)                    # single column → plain top-to-bottom


def marks_scheme_from_pdf(data: bytes, filename: str) -> MarksScheme | None:
    """Deterministic marks scheme from a PDF's text layer — no LLM. Combines three printed
    sources, in order of authority:
      1. inline "[N Mark(s)]" / "[N M]" tags (incl. sub-parts a/b, i/ii),
      2. a right-hand column of bare per-question numbers (common board layout),
      3. the header total ("Max. Marks: 40") — used as the authoritative grand total.
    Returns None only when nothing usable is found (e.g. scanned/no text layer)."""
    if not (filename or "").lower().endswith(".pdf") or not data:
        return None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages_text = [_columnar_text(p) for p in doc]
        raw_text = [p.get_text() for p in doc]           # plain reading order keeps "(a) ... [2 M]" intact
        header_total = total_marks_from_text(pages_text) or total_marks_from_text(raw_text)
        col_items = _marks_from_right_column(doc)
        doc.close()
    except Exception:
        return None

    # Inline "[N Mark(s)]" tags from BOTH extractions (columnar handles two-column MCQ
    # pages; raw reading order handles case-study sub-parts) — first qid wins.
    tag_by_qid: dict[str, MarksItem] = {}
    for src in (pages_text, raw_text):
        for it in marks_scheme_from_text(src).items:
            tag_by_qid.setdefault(it.qid, it)
    items = list(tag_by_qid.values())
    covered = {_top_level_qid(it.qid) for it in items}   # e.g. {"Q19", "Q20"}
    for it in col_items:                                  # add column marks only where tags didn't cover
        if _top_level_qid(it.qid) not in covered:
            items.append(it)
            covered.add(_top_level_qid(it.qid))

    if not items and header_total is None:
        return None
    items.sort(key=lambda it: (int(re.search(r"\d+", it.qid).group()), it.qid))
    total = header_total if header_total is not None else round(sum(i.max for i in items), 3)
    return MarksScheme(total=round(total, 3), items=items)


MARKS_SCHEME_PROMPT = """You are an exam-paper analyst. Read the QUESTION PAPER images and extract the MAXIMUM MARKS for every question and sub-part, EXACTLY as printed.

Rules:
- The marks are printed on the paper as "[1 Mark]", "[2 Marks]", "[3 Marks]", "[5 Marks]", etc. Use those printed values verbatim. Do NOT guess and do NOT infer from difficulty.
- Enumerate EVERY sub-part separately. Lettered parts (a),(b) and roman parts (i),(ii),(iii) each carry their OWN marks. Example: if Q16 has (a) "[3 Marks]" and (b) "[2 Marks]", output two items — Q16.a = 3 and Q16.b = 2 — NOT a single Q16 = 4 or Q16 = 5.
- If a question has no sub-parts, output one item for it (e.g. Q14 = 3).
- qid format: top-level "Q14"; lettered "Q16.a"; roman "Q18.iii"; nested "Q6.ii.b".
- Give a 3-6 word `description` of each part so a teacher can recognise it.
- `total` = the exact sum of all item maxes. This is the paper's full marks.

Be precise and complete — a single missed sub-part makes the whole denominator wrong."""


def extract_marks_scheme(
    question_paper_pngs: list[bytes],
    model: str = "claude-opus-4-7",
    api_key: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
) -> MarksScheme:
    """Read the authoritative per-part maximum-marks scheme off the question paper."""
    client = _anthropic_client(api_key)

    content: list[dict] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        content.append({"type": "text", "text": ctx})
    content.append({"type": "text", "text": "Question paper:"})
    for png in question_paper_pngs:
        content.append(_image_block(png))
    content.append({"type": "text", "text": "Extract the official maximum-marks scheme as described."})

    with client.messages.stream(
        model=model,
        max_tokens=8000,
        temperature=0,
        system=MARKS_SCHEME_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=MarksScheme,
    ) as stream:
        final = stream.get_final_message()
    parsed = getattr(final, "parsed_output", None)
    if parsed is None:
        text = next((b.text for b in final.content if b.type == "text"), "")
        parsed = MarksScheme.model_validate_json(text)
    return parsed


def generate_rubric(
    question_paper_pngs: list[bytes],
    model: str = "claude-opus-4-7",
    api_key: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
) -> str:
    """Generate an editable Markdown rubric from the question paper alone."""
    client = _anthropic_client(api_key)

    content: list[dict] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        content.append({"type": "text", "text": ctx})
    content.append({"type": "text", "text": "Question paper:"})
    for png in question_paper_pngs:
        content.append(_image_block(png))
    content.append({
        "type": "text",
        "text": "Produce the marking rubric for this paper as described in the system prompt.",
    })

    response = client.messages.create(
        model=model,
        max_tokens=8000,
        temperature=0,
        system=RUBRIC_GEN_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return next(b.text for b in response.content if b.type == "text")


def grade_answer_sheet(
    question_paper_pngs: list[bytes],
    student_pngs: list[bytes],
    answer_key_pngs: list[bytes] | None = None,
    answer_key_text: str | None = None,
    ocr_transcript: str | None = None,
    ocr_pages: list[PageOCR] | None = None,
    model: str = "claude-opus-4-7",
    api_key: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
    marks_scheme: MarksScheme | None = None,
    usage_out: dict | None = None,
) -> GradeReport:
    """Run the full grading call and return a structured GradeReport.

    Provide EITHER answer_key_pngs (uploaded file) OR answer_key_text (AI-generated/edited rubric).
    Pass marks_scheme to lock the maximum marks to the question paper (consistent denominator).
    Pass usage_out (a dict) to receive the call's token usage for cost accounting.
    """
    if not answer_key_pngs and not answer_key_text:
        raise ValueError("Provide either answer_key_pngs or answer_key_text.")

    client = _anthropic_client(api_key)

    content: list[dict] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        content.append({"type": "text", "text": ctx})
    content.append({"type": "text", "text": "=== QUESTION PAPER ==="})
    for png in question_paper_pngs:
        content.append(_image_block(_downscale_for_model(png)))

    content.append({"type": "text", "text": "=== ANSWER KEY / MARKING SCHEME ==="})
    if answer_key_text:
        content.append({"type": "text", "text": answer_key_text})
    for png in (answer_key_pngs or []):
        content.append(_image_block(_downscale_for_model(png)))

    msb = marks_scheme_block(marks_scheme)
    if msb:
        content.append({"type": "text", "text": msb})

    # If we have OCR pages, overlay the line_id labels directly on each page
    # image. This is the biggest win: the model sees the visual location of
    # each [PnLk] label, so it can pick the right anchor by eye instead of
    # inferring from the textual transcript alone.
    overlaid_pngs: list[bytes] = []
    if ocr_pages:
        ocr_by_page = {p.page: p for p in ocr_pages}
        for i, png in enumerate(student_pngs, start=1):
            po = ocr_by_page.get(i)
            overlaid_pngs.append(overlay_anchors_on_png(png, po) if po else png)
    else:
        overlaid_pngs = list(student_pngs)

    content.append({"type": "text", "text": f"=== STUDENT'S ANSWER SHEET ({len(overlaid_pngs)} pages, 1-indexed) ==="})
    if ocr_pages:
        content.append({"type": "text", "text": (
            "Each line on these page images is overlaid with its OCR line_id (e.g. [P3L7]) in BLUE. "
            "Question/section header lines are labelled in RED — never anchor a step there. "
            "Diagram regions are outlined in ORANGE with an anchor like [P7D1] — use those when the "
            "step's evidence is a figure, table, or hand-drawn diagram (no text). "
            "Pick step_line_id by LOOKING at the page — match each rubric step to the visible line "
            "(or diagram region) where the student actually demonstrated that step."
        )})
    for i, png in enumerate(overlaid_pngs, start=1):
        content.append({"type": "text", "text": f"--- Page {i} ---"})
        content.append(_image_block(png))

    if ocr_transcript:
        content.append({
            "type": "text",
            "text": (
                "=== OCR TRANSCRIPT OF STUDENT'S ANSWER SHEET (lines labelled [PnLk], diagrams [PnDk]) ===\n"
                "This transcript mirrors the labels overlaid on the page images. Lines tagged (HEADER) "
                "are question/section labels — never anchor step ticks to them. Lines tagged "
                "(DIAGRAM REGION) are blank bands where a figure/table lives — use them when the step "
                "is satisfied by a drawing.\n\n"
                + ocr_transcript
            ),
        })

    content.append({
        "type": "text",
        "text": (
            "Now grade the student's answers against the answer key. Return a complete GradeReport: every sub-question must appear in `questions`, every top-level question in `section_totals`, and total_score must equal the sum of all sub-question scores."
            + (" Populate anchor_line_id and target_line_id from the OCR transcript wherever possible for precise mark placement." if ocr_transcript else "")
        ),
    })

    # Use streaming to safely allow a high max_tokens — step-by-step criteria
    # blow past 16k easily on a 17-page sheet.
    with client.messages.stream(
        model=model,
        max_tokens=64000,
        temperature=0,
        system=build_system_prompt(subject),
        messages=[{"role": "user", "content": content}],
        output_format=GradeReport,
    ) as stream:
        final = stream.get_final_message()

    if final.stop_reason == "max_tokens":
        raise RuntimeError(
            "Claude hit the output token limit before finishing. "
            "Try gemini-2.5-pro for higher headroom, or simplify the rubric."
        )
    if usage_out is not None:
        u = getattr(final, "usage", None)
        usage_out.update({
            "provider": "claude",
            "model": model,
            # Claude's input_tokens already EXCLUDES cache reads — keep them separate.
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0) if u else 0,
            "cached_input_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0,
            "cache_write_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0) if u else 0,
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0) if u else 0,
        })

    parsed = getattr(final, "parsed_output", None)
    if parsed is None:
        text = next((b.text for b in final.content if b.type == "text"), "")
        parsed = GradeReport.model_validate_json(text)
    return _reconcile_report(parsed, marks_scheme)
