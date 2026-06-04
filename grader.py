"""Claude-powered handwritten answer-sheet grader."""
from __future__ import annotations

import base64
from io import BytesIO
from typing import Literal

import anthropic
import fitz  # PyMuPDF
from PIL import Image
from pydantic import BaseModel, Field

from mathpix import PageOCR, overlay_anchors_on_png


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

# PARTIAL CREDIT RULES (apply to every criterion)

Default to partial credit, not zero, whenever the student demonstrates partial understanding. Use this scale per criterion:

- **Full marks (100%)** — answer matches the rubric in content and is acceptably expressed.
- **Most marks (~75%)** — core idea is correct but one supporting detail is missing or one minor factual slip.
- **Half marks (~50%)** — core idea is in the right direction but imprecise wording, partial coverage, or one of two required points missing. Example: "scared" in place of "had stopped / was absent" — the meaning is in the ballpark, so award 0.5, not 0.
- **Quarter marks (~25%)** — student is on the wrong track but mentions one relevant keyword or shows minimal recognition.
- **Zero (0%)** — wrong, unattempted, or unrelated.

Do NOT give 0 just because the wording differs from the model answer. Ask: does the answer convey the rubric's core idea? If yes, it earns at least 50%.

# CONTENT VS LANGUAGE (descriptive / long-form answers only)

For descriptive answers worth 3+ marks (paragraphs, diary entries, story completions, 40–50 word and 100–120 word answers in literature), split the marks into two SEPARATE criteria:

- **"Content / ideas"** — worth ~70% of the question's marks. Judge factual accuracy, coverage of required points, relevance to the prompt. Grammar errors do NOT reduce this score.
- **"Language / expression"** — worth ~30% of the question's marks. Judge grammar, sentence construction, vocabulary, spelling. Content gaps do NOT reduce this score.

Award each independently, then sum. This prevents grammar weakness from bleeding into content marks and vice versa. For short objective answers (MCQ, fill-in-the-blank, one-word, 1-mark items), do NOT split — use a single criterion.

# OBJECTIVE ANSWERS ARE ALL-OR-NOTHING

The graded partial-credit scale above applies to descriptive / multi-step answers ONLY. For OBJECTIVE items — MCQ choices, true/false, match-the-following, one-word answers, antonyms/synonyms, fill-in-the-blanks — there is no "close enough": the answer is either correct (full marks) or wrong (zero). Do NOT award 25–50% to a wrong antonym, a wrong MCQ option, or a wrong fill-in just because the student attempted it or was "in the ballpark". The ONLY tolerance is an obvious spelling slip where the intended correct word is unambiguous (e.g. "begining" for "beginning") — accept that as correct. Everything else objective is 0.

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


def generate_rubric(
    question_paper_pngs: list[bytes],
    model: str = "claude-opus-4-7",
    api_key: str | None = None,
    student_class: str | None = None,
    subject: str | None = None,
) -> str:
    """Generate an editable Markdown rubric from the question paper alone."""
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

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
) -> GradeReport:
    """Run the full grading call and return a structured GradeReport.

    Provide EITHER answer_key_pngs (uploaded file) OR answer_key_text (AI-generated/edited rubric).
    """
    if not answer_key_pngs and not answer_key_text:
        raise ValueError("Provide either answer_key_pngs or answer_key_text.")

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    content: list[dict] = []
    ctx = exam_context_block(student_class, subject)
    if ctx:
        content.append({"type": "text", "text": ctx})
    content.append({"type": "text", "text": "=== QUESTION PAPER ==="})
    for png in question_paper_pngs:
        content.append(_image_block(png))

    content.append({"type": "text", "text": "=== ANSWER KEY / MARKING SCHEME ==="})
    if answer_key_text:
        content.append({"type": "text", "text": answer_key_text})
    for png in (answer_key_pngs or []):
        content.append(_image_block(png))

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
    parsed = getattr(final, "parsed_output", None)
    if parsed is None:
        text = next((b.text for b in final.content if b.type == "text"), "")
        parsed = GradeReport.model_validate_json(text)
    return parsed
