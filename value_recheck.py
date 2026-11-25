"""A second look at the value a deduction quotes from the student's page.

The grader reads the booklet once. When it misreads a number, the misreading
becomes both the deduction and the reason the student reads. MEASURED on the Radiant
Physics batch (2026-09-29): "29.5 days" read as 20.5 ("29.5 not 20.5"), "9.8 N" read
as 0.8 N, and each was released with its false remark.

For every remark that quotes a value as the student's ("..., not 20.5"), the OCR
line holding that value is cropped from the page image and read again by a small
vision model. If the fresh reading does not show the quoted value, the copy goes to a
faculty reviewer with both readings. The model reads; code only compares. Fails open:
any error leaves the copy as it was.
"""
from __future__ import annotations

import json
import re
from io import BytesIO

VALUE_RECHECK_MODEL = "claude-haiku-4-5"
MAX_CHECKS = 6
# "Should be 29.5, not 20.5", "29.5 days, not 20.5", "not 0.8 N". The quoted value
# must carry a digit: a word ("not maximum") is a judgement, not a reading.
_QUOTED_RE = re.compile(r"\bnot\s+([−\-]?\s*\d[\d.,/]*(?:\s*[a-zA-Z°%]{1,6})?)", re.IGNORECASE)


def _norm(value: str) -> str:
    value = value.replace("−", "-").replace("minus", "-")
    return re.sub(r"[^0-9.\-/]", "", value).strip(".")


def quoted_values(report) -> list[tuple[object, str]]:
    """(question, quoted student value) for each remark that quotes one."""
    out = []
    for q in list(getattr(report, "questions", None) or []):
        if bool(getattr(q, "objective", False)):
            continue
        if float(getattr(q, "score", 0) or 0) >= float(getattr(q, "max_score", 0) or 0):
            continue
        texts = [str(getattr(q, "remark", "") or "")]
        texts += [str(getattr(c, "evidence", "") or "") for c in getattr(q, "criteria", None) or []]
        for text in texts:
            for match in _QUOTED_RE.finditer(text.replace(" minus ", " -")):
                value = _norm(match.group(1))
                if value and any(ch.isdigit() for ch in value):
                    out.append((q, value))
    seen, unique = set(), []
    for q, value in out:
        if (q.qid, value) not in seen:
            seen.add((q.qid, value))
            unique.append((q, value))
    return unique[:MAX_CHECKS]


def _line_for(q, value, ocr_pages):
    """The OCR line of this question whose text shows the quoted value."""
    ids = [str(getattr(q, "anchor_line_id", "") or "")]
    ids += [str(getattr(c, "step_line_id", "") or "") for c in getattr(q, "criteria", None) or []]
    lines = {line.line_id: line for page in ocr_pages or [] for line in page.lines}
    candidates = [lines[i] for i in ids if i in lines]
    page = int(getattr(q, "page", 0) or 0)
    candidates += [line for p in ocr_pages or [] if p.page == page for line in p.lines]
    for line in candidates:
        if value in _norm(line.text.replace("\\", " ")):
            return line
    return None


def _crop(png: bytes, page, line) -> bytes | None:
    from PIL import Image
    img = Image.open(BytesIO(png))
    sx = img.width / max(float(getattr(page, "image_width", 0) or img.width), 1.0)
    sy = img.height / max(float(getattr(page, "image_height", 0) or img.height), 1.0)
    x0, y0, x1, y1 = [float(v) for v in line.bbox]
    pad_y = 0.4 * (y1 - y0)
    box = (max(0, int(x0 * sx - 20)), max(0, int((y0 - pad_y) * sy)),
           min(img.width, int(x1 * sx + 20)), min(img.height, int((y1 + pad_y) * sy)))
    if box[2] - box[0] < 20 or box[3] - box[1] < 10:
        return None
    buf = BytesIO()
    img.crop(box).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def recheck_quoted_values(report, sa_pngs, ocr_pages, *, google_token: str, task_id: str,
                          filename: str = "", usage_out: dict | None = None,
                          call=None) -> list[str]:
    """Review reasons for remarks whose quoted value the page does not show."""
    reasons: list[str] = []
    pages = {p.page: p for p in ocr_pages or []}
    for q, value in quoted_values(report):
        line = _line_for(q, value, ocr_pages)
        if line is None:
            continue           # nothing on the page reads that way; not ours to judge
        page = pages.get(line.page)
        if page is None or line.page - 1 >= len(sa_pngs or []):
            continue
        crop = _crop(sa_pngs[line.page - 1], page, line)
        if crop is None:
            continue
        try:
            reading = (call or _read_crop)(crop, google_token=google_token, task_id=task_id,
                                           filename=filename, usage_out=usage_out)
        except Exception as exc:  # noqa: BLE001 - fail open
            print(f"[value-recheck] {q.qid}: skipped ({type(exc).__name__}: {exc})", flush=True)
            continue
        numbers = {_norm(n) for n in re.findall(r"[−\-]?\d[\d.,/]*", str(reading or ""))}
        if numbers and value not in numbers:
            reasons.append(f"{q.qid}: the remark says the student wrote {value}, but the "
                           f"page reads \"{str(reading).strip()[:60]}\". Check the deduction.")
    return reasons


# The one result figure a step's description ends with: "Kp = PD/P0 = 8/45 = 17.78%",
# "Market price per share = EPS x PE = 43.75 x 8 = 350", "... = EPS 43.75". A number
# followed by more working ("= 8/45", "= 43.75 x 8") is part of the working.
_STEP_RESULT_RE = re.compile(
    r"=\s*(?:[A-Za-z][A-Za-z.]*\s+)?([−\-]?\d[\d,]*(?:\.\d+)?)(?=\s*(?:%|times)?\s*(?:[,;]\s|[,;.]?\s*$))")
_NUMBER_RE = re.compile(r"[−\-]?\d[\d.,/]*")


def step_result(description: str) -> str:
    """The figure a numeric step's description says it reaches, or "" when it names no
    single one ("EBT = 7,81,250, ..., bank loan = 21,09,375" names two)."""
    results = list(_STEP_RESULT_RE.finditer(str(description or "").strip()))
    return _norm(results[0].group(1)) if len(results) == 1 else ""


def _numbers(text: str) -> set[str]:
    return {n for n in (_norm(x) for x in _NUMBER_RE.findall(str(text or "").replace(",", ""))) if n}


def _same_figure(expected: str, numbers: set[str]) -> bool:
    if expected in numbers:
        return True
    try:
        want = float(expected)
    except ValueError:
        return False
    for n in numbers:
        try:
            if "." in expected and abs(float(n) - want) <= 0.011:
                return True
        except ValueError:
            continue
    return False


def restore_misread_steps(report, sa_pngs, ocr_pages, *, google_token: str, task_id: str,
                          filename: str = "", usage_out: dict | None = None, call=None) -> list[str]:
    """CA papers: put back the marks of a step docked for a figure the page does not show.

    Review of the CA FM MT01 copies (22 Oct 2026): three right answers were docked for
    OCR misreadings the grader quoted as the student's figure. "Kp = 8/45 = 17.78%",
    boxed and repeated in the student's own WACC table, was read "8/45 + 12.781"; "MPS =
    8 x 43.75 = 350" was read "8 x 43.35 = 350" and crossed. Neither remark said "not X",
    so the value recheck above never looked. Here a lost step whose description ends in
    one result figure, and whose evidence quotes one other figure, has the OCR line that
    shows that figure read again from the page image. When the fresh reading shows the
    step's own result (to rounding) and not the quoted figure, the deduction rested on a
    misreading: the step gets its marks back and the faculty is told. Anything else
    changes nothing. Returns the review notes."""
    notes: list[str] = []
    pages = {p.page: p for p in ocr_pages or []}
    checks = 0
    changed = False
    for q in list(getattr(report, "questions", None) or []):
        if bool(getattr(q, "objective", False)) or not bool(getattr(q, "attempted", True)):
            continue
        for index, c in enumerate(list(getattr(q, "criteria", None) or [])):
            if checks >= MAX_CHECKS:
                break
            if float(getattr(c, "awarded", 0) or 0) >= float(getattr(c, "max", 0) or 0):
                continue
            description = str(getattr(c, "description", "") or "")
            result = step_result(description)
            if not result:
                continue
            quoted = _numbers(getattr(c, "evidence", "")) - _numbers(description) - {result}
            if len(quoted) != 1:
                continue
            wrong = quoted.pop()
            line = _line_for(q, wrong, ocr_pages)
            page = pages.get(getattr(line, "page", 0)) if line is not None else None
            if page is None or line.page - 1 >= len(sa_pngs or []):
                continue
            crop = _crop(sa_pngs[line.page - 1], page, line)
            if crop is None:
                continue
            checks += 1
            try:
                reading = (call or _read_crop)(crop, google_token=google_token, task_id=task_id,
                                               filename=filename, usage_out=usage_out)
            except Exception as exc:  # noqa: BLE001 - fail open
                print(f"[value-recheck] {q.qid}: skipped ({type(exc).__name__}: {exc})", flush=True)
                continue
            seen = _numbers(reading)
            if not seen or wrong in seen or not _same_figure(result, seen):
                continue
            c.awarded = float(c.max)
            c.met = True
            c.evidence = ""
            # The step's certificate is what the renderer draws: left at the second
            # examiner's "wrong", the restored 2 marks went out beside a cross (CA FM
            # 79fb40f0, 22 Oct 2026).
            from exam_package import normalize_qid
            box = (getattr(report, "verified_step_boxes", None) or {}).get(f"{normalize_qid(str(q.qid))}:{index}")
            if isinstance(box, dict):
                box["verdict"] = "correct"
                box["deduction_reason"] = ""
                for point in box.get("checking_points") or []:
                    if isinstance(point, dict):
                        point["verdict"] = "correct"
                        point["reason"] = ""
            q.score = round(min(float(q.max_score), sum(float(x.awarded or 0) for x in q.criteria)), 2)
            kept = [s for s in re.split(r"(?<=[.!?])\s+", str(getattr(q, "remark", "") or "").strip())
                    if s and wrong not in _numbers(s)]
            remark = " ".join(kept)
            if not remark and q.score < float(q.max_score):
                remark = " ".join(str(x.evidence).strip() for x in q.criteria
                                  if float(x.awarded or 0) < float(x.max or 0) and str(x.evidence or "").strip())
            q.remark = remark
            changed = True
            notes.append(f"{q.qid}: \"{wrong}\" was read again from the page as {result}, the step's "
                         f"own result, so its {float(c.max):g} mark(s) were restored. Check the step.")
    if changed:
        report.total_score = round(sum(float(x.score or 0) for x in report.questions), 2)
        try:
            from grader import _rebuild_section_totals
            _rebuild_section_totals(report)
        except Exception:  # noqa: BLE001 - totals are rebuilt again at render time
            pass
    return notes


def _read_crop(png: bytes, *, google_token: str, task_id: str, filename: str = "",
               usage_out: dict | None = None) -> str:
    from grader import (_claude_call, _claude_text, _claude_usage, _image_block,
                        record_usage_event)
    response = _claude_call(
        google_token, task_id=task_id, model=VALUE_RECHECK_MODEL,
        request={"max_tokens": 400, "temperature": 0, "messages": [{"role": "user", "content": [
            {"type": "text", "text": (
                "This is a crop of a student's handwritten answer. It may hold more than "
                "one line, for example a worked expression over three rows. Transcribe "
                "exactly the numbers and units written on EVERY line, top to bottom and left "
                "to right, as plain text. Read a looped 9 as 9, a crossed 7 as 7. Reply with "
                "ONLY JSON: {\"text\": \"...\"}.")},
            _image_block(png)]}]},
        filename=filename, count=1)
    record_usage_event(usage_out, _claude_usage(response, VALUE_RECHECK_MODEL), stage="verify")
    raw = _claude_text(response)
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        # A reply cut off before its JSON closed is unread. Uday Bihar Board
        # Accountancy 0a6c8e91 (10 Oct 2026): a journal crop ran past the token
        # limit, its fragment "```json {"text": "Date Particulars ..." was compared
        # as the page's reading, and Q13 was held over a 1800 the student wrote.
        return ""
    try:
        return str(json.loads(match.group(0)).get("text", ""))
    except ValueError:
        return ""
