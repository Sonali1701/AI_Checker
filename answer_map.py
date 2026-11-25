"""Deterministic first-pass map from OCR labels to the official question graph.

This map is deliberately conservative: absence of a readable label means unresolved,
never blank. The visual grader may still locate the answer, and selective verification
must adjudicate any unresolved item that the primary grader calls unattempted.
"""
from __future__ import annotations

import re
from copy import copy
from dataclasses import asdict, dataclass

from exam_package import ExamPackage, normalize_qid
from grader import _student_label_num, _STUDENT_QLABEL_RE
from mathpix import readable_ocr_text


def _question_label_text(text: str) -> str:
    """Read an explicit ``Answer-(14)`` heading without changing bare numbers."""
    raw = readable_ocr_text(str(text or ""))
    raw = re.sub(
        r"^\W{0,3}sec(?:tion)?\s*[-–—:.]?\s*[A-Ea-e1-5]\s*[:.\-]?\s+"
        r"(?=(?:q(?:ues?(?:tion)?)?|ans(?:wer)?)\b)", "", raw,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"^(\W{0,3}(?:q(?:ues?(?:tion)?)?|answer|solution)"
        r"\s*(?:no\.?)?\s*[-–—.:)#]*\s*)\(\s*(\d{1,2})\s*\)",
        r"\g<1>\g<2>)", raw, flags=re.IGNORECASE,
    )


def _student_label_qid(text: str) -> str | None:
    """Return the canonical qid actually written in a conservative label.

    ``_student_label_num`` remains the chemistry-safe gate.  Once it confirms that
    the line really is an answer label, capture only immediately attached sub-parts;
    a bare ``Q1`` is deliberately not evidence that Q1(a), Q1(b), ... all exist.
    """
    raw = _question_label_text(text)
    number = _student_label_num(raw)
    if number is None:
        return None
    match = _STUDENT_QLABEL_RE.match(raw)
    if not match:
        return f"q{number}"
    suffix = raw[match.end():]
    parts: list[str] = []
    cursor = 0
    while cursor < len(suffix):
        part = re.match(
            r"\s*(?:(?:[.\-:→]\s*)?(?:\(([A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)\)|"
            r"([A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)(?:\)|(?=\.)))|"
            r"\.([A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)(?=\s|\.|$))",
            suffix[cursor:],
        )
        if not part:
            break
        value = (part.group(1) or part.group(2) or part.group(3) or "").lower()
        parts.append(value)
        cursor += part.end()
    return normalize_qid(".".join([f"q{number}", *parts]))


def _answer_map_page_order(page):
    """Keep late OCR table-column rows before the lower next-question heading.

    Do not globally sort a student's page: separate answers can share a row.
    Only recover text emitted after an explicit lower heading when it is fully
    above that heading, below the preceding explicit heading, and alongside
    already-seen left-column writing from the preceding answer.  IDs and OCR
    geometry stay unchanged; this is a local navigation view, not rewritten OCR.
    """
    ordered = []
    current_heading: int | None = None
    previous_heading: int | None = None
    width = float(getattr(page, "image_width", 0) or 0)
    for line in getattr(page, "lines", None) or []:
        raw = _question_label_text(getattr(line, "text", ""))
        match = _STUDENT_QLABEL_RE.match(raw)
        explicit = bool(match and match.group(1) and not getattr(line, "cancelled", False))
        if explicit:
            previous_heading = current_heading
            current_heading = len(ordered)
            ordered.append(line)
            continue
        if (current_heading is not None and previous_heading is not None
                and width > 0 and not getattr(line, "cancelled", False)):
            boundary = ordered[current_heading]
            prior = ordered[previous_heading]
            box = line.bbox
            alongside_left_text = any(
                candidate.bbox[2] <= box[0] + .02 * width
                and candidate.bbox[0] + .2 * width <= box[0]
                and candidate.bbox[1] < box[3] and box[1] < candidate.bbox[3]
                for candidate in ordered[previous_heading + 1:current_heading]
                if not getattr(candidate, "cancelled", False)
            )
            if (prior.bbox[1] <= box[1] and box[3] <= boundary.bbox[1]
                    and alongside_left_text):
                ordered.insert(current_heading, line)
                current_heading += 1
                continue
        ordered.append(line)
    view = copy(page)
    view.lines = ordered
    return view


def _student_subpart_label(text: str) -> str | None:
    """A standalone ``(i)``/``(a)`` label, without inventing its parent.

    The caller may use this only while an official multi-part parent is active.
    That context is what keeps an option letter or chemistry expression from being
    promoted into a question id by itself.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    # Mathpix commonly surrounds handwriting with inline-LaTeX delimiters.
    readable = readable_ocr_text(raw)
    match = re.match(
        r"^\W{0,3}\(?\s*([A-Za-z]\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+|\d+)\s*\)"
        r"(?=\s|[.\-:]|$)",
        readable,
    )
    return (match.group(1).lower() if match else None)


_OPTION_ANSWER_RE = re.compile(r"\(\s*[a-dA-D1-4]\s*\)|^\s*[a-dA-D1-4]\s*[.)]?\s*$")
# A section heading on its own line: "Section - B", "SECTION B", "Sec-A", "Section B:".
_SECTION_HEADING_RE = re.compile(r"\W*sec(?:tion)?\s*[-–—:.]?\s*[A-Ea-e1-5]\s*[:.\-]?\W*",
                                 re.IGNORECASE)


def _bare_point(text: str) -> tuple[int, str] | None:
    """(number, text after it) for a line opening with a BARE "3)" / "3." / "3->".

    "Q3" or "Ans 3" is a question heading and is never a list point.
    """
    readable = readable_ocr_text(str(text or ""))
    match = _STUDENT_QLABEL_RE.match(readable)
    if not match or not match.group(2):
        return None
    return int(match.group(2)), readable[match.end():].strip()


def _point_runs(numbers: list[int]) -> set[int]:
    """Positions in `numbers` that belong to a run of numbered points.

    A run starts at 1 or 2 and climbs by small steps; a new "1" starts a new run. The
    whole sequence used to have to be ONE run, so on a copy whose later headings the
    OCR could not read ("Any 12", "vs 13", "Anily expetors Tsumami" for Ans 12 to 14)
    the points of four answers fell under one heading as 1, 2, 2, 3, 1, 2, 3, 4, 1, 2
    and none was a point: "(1) There must be a Tsunami warning centre" became a Q1
    heading, and Q1's total was drawn inside Q14's answer (EMRS PGT Geography, 22 Oct
    2026). A step back of one is a slip ("(1) compact, (2) semi-compact, (3) hamlet,
    (2) dispersed" for (4)), and a step of three is a point the OCR did not read."""
    runs: list[list[int]] = []
    for position, number in enumerate(numbers):
        if not runs or number == 1:
            runs.append([position])
        else:
            runs[-1].append(position)
    found: set[int] = set()
    for run in runs:
        values = [numbers[p] for p in run]
        if (len(values) >= 2 and values[0] <= 2
                and all(-1 <= b - a <= 3 for a, b in zip(values, values[1:]))):
            found.update(run)
    return found


def _points_under_headings(pages, observations) -> set[str]:
    """Line ids of bare "3." / "(1)" labels that are list points, not questions.

    KVS PRT Mock 01 (2026-10-10): every answer opens with a written heading
    ("Answer-1", "उत्तर - 3", "उत्तर - 9"), and the Q8 and Q9 answers number their
    points 1. to 6. Each bare point was read as a question label, so Q1 to Q6 took
    over the rest of Q8 and Q9, and Q1's and Q2's totals were drawn inside Q9. In a
    booklet with written headings, a bare number is a point when its question has a
    heading of its own, or goes back from the heading it sits under, AND it is one of
    a run of points (1, 2, 3 ... under one heading) or cites an equation number
    ("(1) ≠ (2)"). An option answer ("1)(C)"), a short value ("1.) Nehru") and a lone
    out-of-order heading ("11.) ASEAN was set up in 1967") keep their meaning.
    """
    text = {line.line_id: line.text for page in pages for line in page.lines}
    headed: set[int] = set()
    for _, _, line_id, parsed in observations:
        match = re.match(r"q(\d+)", parsed)
        if match and _bare_point(text.get(line_id, "")) is None:
            headed.add(int(match.group(1)))
    if len(headed) < 2:
        return set()
    # Bare labels grouped under the written heading they follow.
    groups: list[tuple[int | None, list[tuple[str, int, str]]]] = [(None, [])]
    for _, _, line_id, parsed in observations:
        match = re.match(r"q(\d+)", parsed)
        if not match:
            continue
        bare = _bare_point(text.get(line_id, ""))
        if bare is None:
            groups.append((int(match.group(1)), []))
        else:
            groups[-1][1].append((line_id, int(match.group(1)), bare[1]))
    points: set[str] = set()
    # Questions whose own written heading the student has already passed. A bare "3."
    # inside the answer to Q9, when "Q. 3" was answered pages earlier, is a point of
    # Q9 even when its numbers make no clean run: EMRS PGT History (22 Oct 2026) Q9
    # listed the five vows inside its first point ("1. अहिंसा ... 5. ब्रह्मचर्य") and
    # then "2. त्रिरत्न ... 5. समानता", and every one was read as Q1 to Q5.
    # A bare number that opens a page is the student going back to that question.
    page_openers = {next((line.line_id for line in page.lines
                          if not getattr(line, "cancelled", False)), None) for page in pages}
    answered_before: set[int] = set()
    for heading, group in groups:
        numbers = [number for _, number, _ in group]
        in_run_at = _point_runs(numbers)
        for position, (line_id, number, rest) in enumerate(group):
            in_run = position in in_run_at
            # A one or two word value ("1.) Nehru") keeps its meaning; inside a run
            # of points two words are a point ("(2) Semi-compact settlement").
            if (re.match(r"\(?\s*[A-Da-d]\s*\)", rest)
                    or len(rest.split()) < (2 if in_run else 3)):
                continue
            cites = bool(re.search(r"\(\s*\d{1,2}\s*\)", rest))
            elsewhere = number in headed or (heading is not None and number < heading)
            passed = (heading is not None and number < heading and number in answered_before
                      and line_id not in page_openers)
            if (elsewhere and (in_run or cites)) or passed:
                points.add(line_id)
        if heading is not None:
            answered_before.add(heading)
    return points


def _restarted_point_lists(pages) -> set[str]:
    """Line ids of a numbered list INSIDE an answer, which must not open questions.

    CA Inter CMA MTS-02, 2026-09-19: under "Q2B) Inventory control" the student
    listed "1) ABC analysis, 2) VED analysis, 3) Re-order level system, 4) Economic
    order quantity", and the map read "3)" and "4)" as Part A Q3 and Q4. Their MCQ
    marks landed on those lines, and on other copies such phantom answers took
    "any four" slots from real ones. A run is a list, not questions, only when all
    of this holds: it restarts at 1 after the sheet has already shown a label, it
    has at least three points on nearby lines of one page, and every point is
    prose, not an option answer such as "2) (b)" or "3) Indica (c)".
    """
    noise: set[str] = set()
    seen_label = False
    for page in pages:
        lines = [l for l in (getattr(page, "lines", None) or [])
                 if not getattr(l, "cancelled", False)]
        points = []
        for index, line in enumerate(lines):
            point = _bare_point(getattr(line, "text", ""))
            if point is not None:
                points.append((index, point[0], point[1], line.line_id))
        runs: list[list[tuple]] = []
        for item in points:
            if (runs and item[1] == runs[-1][-1][1] + 1
                    and item[0] - runs[-1][-1][0] <= 3):
                runs[-1].append(item)
            else:
                runs.append([item])
        run_starts = {run[0][3]: run for run in runs}
        for index, line in enumerate(lines):
            run = run_starts.get(line.line_id)
            if run is not None and seen_label and run[0][1] == 1 and len(run) >= 3 and all(
                    re.search(r"[A-Za-z]{3,}", text) and not _OPTION_ANSWER_RE.search(text)
                    for _, _, text, _ in run):
                noise.update(line_id for *_, line_id in run)
            if _student_label_num(str(getattr(line, "text", "") or "")) is not None:
                seen_label = True
    return noise


def _embedded_number_labels(pages, official_qids: set[str],
                            objective_qids: set[str]) -> tuple[set[str], set[str]]:
    """Weak parenthesized numbers must not steal an explicitly headed answer.

    A Political Science copy's ``(ii)`` was OCR'd as ``(11)``, a cause under
    Answer15 began ``(1)``, and optionB under Answer13 became ``(8)``.  Each
    number also had a genuine explicit question heading elsewhere.  Promoting
    these weak cues opened false Q11/Q1/Q8 regions and certified marks against
    another answer's writing.  Keep them in their existing parent context.

    This does not relabel handwriting.  Explicit question headings, ordinary
    N.) answer sequences and a fresh page-opening numbered answer remain labels.
    A number that names an official child is left for the subpart parser.
    """
    explicit = set()
    for page in pages:
        for line in getattr(page, "lines", None) or []:
            if getattr(line, "cancelled", False):
                continue
            match = _STUDENT_QLABEL_RE.match(_question_label_text(line.text))
            if match and match.group(1):
                parsed = _student_label_qid(line.text)
                if parsed:
                    explicit.add(parsed)
                    root = parsed.split(".", 1)[0]
                    if root in official_qids and root in objective_qids:
                        # ``Q1 (B) value`` is an explicit objective answer,
                        # rather than proof that a separate Q1.b exists.
                        explicit.add(root)
    suppressed: set[str] = set()
    uncertain: set[str] = set()
    for page in pages:
        active_explicit: str | None = None
        lines = [line for line in (getattr(page, "lines", None) or [])
                 if not getattr(line, "cancelled", False)]
        nested_options: set[str] = set()
        # A passage can number its MCQ subparts 1)..4), even when the
        # package contains one aggregate theory row for that passage.  The
        # same numbers name separate objective questions elsewhere.  Require
        # a restarted run of at least three, a same-page explicit nonobjective
        # parent, and explicit headings for every colliding objective.  A
        # single option answer or a fresh section/page sequence stays a label.
        for index, line in enumerate(lines):
            raw = _question_label_text(line.text)
            match = _STUDENT_QLABEL_RE.match(raw)
            parsed = _student_label_qid(line.text)
            if _SECTION_HEADING_RE.fullmatch(raw):
                active_explicit = None
            elif match and match.group(1) and parsed:
                active_explicit = parsed
            elif match and parsed:
                point = _bare_point(line.text)
                run = []
                if (active_explicit and active_explicit not in objective_qids
                        and re.fullmatch(r"q\d+", active_explicit)
                        and (active_explicit in official_qids or any(
                            qid.startswith(active_explicit + ".") for qid in official_qids))
                        and point and point[0] == 1
                        and _OPTION_ANSWER_RE.search(point[1])):
                    for candidate_index, candidate in enumerate(lines[index:], start=index):
                        text = _question_label_text(candidate.text)
                        heading = _STUDENT_QLABEL_RE.match(text)
                        if _SECTION_HEADING_RE.fullmatch(text) or (heading and heading.group(1)):
                            break
                        if run and candidate_index - run[-1][0] > 3:
                            break
                        item = _bare_point(candidate.text)
                        if item is None:
                            continue
                        num, tail = item
                        target = f"q{num}"
                        repeated_ocr = bool(run and num == run[-1][1]
                                            and tail.casefold() == run[-1][2].casefold())
                        if (target not in objective_qids or target not in explicit
                                or not _OPTION_ANSWER_RE.search(tail)
                                or (num != (run[-1][1] + 1 if run else 1) and not repeated_ocr)):
                            break
                        run.append((candidate_index, num, tail, candidate.line_id))
                    if len({item[1] for item in run}) >= 3:
                        nested_options.update(item[3] for item in run)
                        continue
                if line.line_id not in nested_options:
                    active_explicit = None
        active_explicit = None
        for line in lines:
            if getattr(line, "cancelled", False):
                continue
            raw = _question_label_text(line.text)
            if _SECTION_HEADING_RE.fullmatch(raw):
                active_explicit = None
                continue
            match = _STUDENT_QLABEL_RE.match(raw)
            parsed = _student_label_qid(line.text)
            if not match or not parsed:
                continue
            if match.group(1):
                active_explicit = parsed
                continue
            # A long prose list point cannot establish a second MCQ attempt.
            # Recorded Q14 water-sharing bullet: "1. On the basis of Sindh ...".
            # Short option/value answers and bare descriptive headings keep
            # their existing meaning, including out-of-order "11.)" headings.
            tail = raw[match.end():].strip()
            weak_number = line.line_id in nested_options or bool(re.match(r"^\s*\(\s*\d", raw)) or (
                parsed in objective_qids
                and len(re.findall(r"[A-Za-z]+", tail)) >= 6
                and not _OPTION_ANSWER_RE.search(tail)
            )
            collision = parsed.split(".", 1)[0] if line.line_id in nested_options else parsed
            if (active_explicit and weak_number
                    and re.fullmatch(r"q\d+", collision)
                    and collision != active_explicit and collision in explicit):
                lid = str(getattr(line, "line_id", "") or "")
                suppressed.add(lid)
                subpart = _student_subpart_label(line.text)
                child = normalize_qid(f"{active_explicit}.{subpart}") if subpart else ""
                if child not in official_qids:
                    uncertain.add(lid)
                continue
            active_explicit = None
    return suppressed, uncertain


def _misread_subpart_labels(pages, repaired_by_position: dict, package,
                            enumeration_noise: set[str]) -> dict[str, str]:
    """{line id: the sub-part letter it really is} for a label OCR misread out of order.

    MEASURED 2026-10-01 (Radiant 2.0 Class 9 SST Test 02, Aradhya): under "Q7" the
    student wrote a) b) c) on page 1 and d) on page 2. Mathpix read the "a)" as "d)."
    ("d). Archeological remains..."), so the map filed Q7's first answer as Q7.d, left
    Q7.a unresolved, and dropped the real "d) - Harappans used to make ornament..." as
    a return to an answered sibling. The grader then marked Q7.d against Q7.a's
    sources (0/3, "Lists archaeological sources").

    Repaired only in the tightly proved case, like the duplicated-digit repair above:
    under one parent, a label is out of order with the label after it, the letter just
    before that next label is never seen anywhere under the parent, the label before
    this one (or the parent heading) leads straight into that missing letter, and the
    misread letter itself appears again LATER in its proper place.
    """
    children: dict[str, list[str]] = {}
    for question in package.questions:
        match = re.fullmatch(r"(q\d+)\.([a-z]|[ivx]+)", normalize_qid(question.qid))
        if match:
            children.setdefault(match.group(1), []).append(match.group(2))
    if not children:
        return {}
    sequences: dict[str, list[tuple[str, str, bool]]] = {}
    active_parent: str | None = None
    for page in pages:
        page_number = int(getattr(page, "page", 0) or 0)
        for line_index, line in enumerate(list(getattr(page, "lines", None) or []), start=1):
            line_id = str(getattr(line, "line_id", "") or "")
            if getattr(line, "cancelled", False):
                continue
            parsed = repaired_by_position.get((page_number, line_index, line_id))
            if parsed:
                match = re.fullmatch(r"(q\d+)(?:\.([a-z]|[ivx]+))?(?:\..*)?", parsed)
                active_parent = match.group(1) if match else None
                if (match and match.group(2) and active_parent in children
                        and match.group(2) in children[active_parent]):
                    # A heading that names its own part ("Q7.a)") is never relabelled.
                    sequences.setdefault(active_parent, []).append(
                        (line_id, match.group(2), True))
                continue
            if _SECTION_HEADING_RE.fullmatch(readable_ocr_text(str(getattr(line, "text", "") or ""))):
                active_parent = None
                continue
            if not active_parent or active_parent not in children or line_id in enumeration_noise:
                continue
            subpart = _student_subpart_label(str(getattr(line, "text", "") or ""))
            if subpart and subpart in children[active_parent]:
                sequences.setdefault(active_parent, []).append((line_id, subpart, False))
    fixes: dict[str, str] = {}
    for parent, sequence in sequences.items():
        order = children[parent]
        rank = {letter: i for i, letter in enumerate(order)}
        letters = [letter for _, letter, _ in sequence]
        for i, (line_id, letter, fixed) in enumerate(sequence):
            if fixed or i + 1 >= len(sequence):
                continue
            following = letters[i + 1]
            if rank[letter] <= rank[following] or rank[following] == 0:
                continue
            expected = order[rank[following] - 1]
            leads_in = (rank[expected] == 0 if i == 0
                        else rank[letters[i - 1]] == rank[expected] - 1)
            if (leads_in and expected not in letters
                    and letter in letters[i + 1:]):
                fixes[line_id] = expected
                letters[i] = expected
    return fixes


@dataclass(frozen=True)
class AnswerLocation:
    qid: str
    status: str                 # "located" | "unresolved"
    pages: tuple[int, ...] = ()
    label_line_ids: tuple[str, ...] = ()
    first_position: tuple[int, int] | None = None
    confidence: float = 0.0
    line_ids: tuple[str, ...] = ()
    regions: tuple[dict, ...] = ()
    uncertain_line_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        data = asdict(self)
        data["pages"] = list(self.pages)
        data["label_line_ids"] = list(self.label_line_ids)
        data["line_ids"] = list(self.line_ids)
        data["regions"] = list(self.regions)
        data["uncertain_line_ids"] = list(self.uncertain_line_ids)
        data["first_position"] = (list(self.first_position)
                                  if self.first_position is not None else None)
        return data


@dataclass(frozen=True)
class AnswerMap:
    package_id: str
    locations: tuple[AnswerLocation, ...]

    @property
    def unresolved_qids(self) -> list[str]:
        return [item.qid for item in self.locations if item.status == "unresolved"]

    def as_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "locations": [item.as_dict() for item in self.locations],
            "unresolved_qids": self.unresolved_qids,
        }


def _typed_list_points(pages, observations) -> set[str]:
    """Line ids of bare "1." / "2)" labels in a TYPED submission that heads its answers
    "Question N" or "QN": there every bare number is a point inside an answer.

    PW Skills team, 21 Oct 2026: typed assignments copy each question with its
    "Question N :" heading, then number their points, steps and the question's own
    sub-items 1., 2., 3. Read as question labels, those numbers moved answers to the
    wrong question and left questions unevaluated. Handwriting mixes "Q1" and "2)"
    headings, so this applies only to a category whose submissions are typed, and
    only once two or more questions carry a prefixed heading."""
    text = {line.line_id: line.text for page in pages for line in page.lines}
    headed = {match.group(1) for _, _, line_id, parsed in observations
              for match in [re.match(r"q(\d+)", parsed)]
              if match and _bare_point(text.get(line_id, "")) is None}
    if len(headed) < 2:
        return set()
    return {line_id for _, _, line_id, _ in observations
            if _bare_point(text.get(line_id, "")) is not None}


def build_answer_map(package: ExamPackage, ocr_pages, *, typed: bool = False,
                     label_free_pages=frozenset()) -> AnswerMap:
    """Where each official question's answer is on the sheet.

    `typed`: the category's submissions are typed documents (School of Innovation),
    so bare numbers are list points once the answers carry "Question N" headings
    (_typed_list_points).
    `label_free_pages`: pages whose question numbers are not answer headings: a
    learner's short cover page in front of the linked work it points to, which lists
    "Q2 ... Q14" as a table of contents (linked_work.py)."""
    pages = [_answer_map_page_order(page) for page in (ocr_pages or [])]
    official_qids = {normalize_qid(question.qid) for question in package.questions}
    children_by_top: dict[str, set[str]] = {}
    objective_qids = {normalize_qid(q.qid) for q in package.questions
                      if q.question_type == "objective"}
    for qid in official_qids:
        top_match = re.match(r"q\d+", qid)
        if top_match:
            children_by_top.setdefault(top_match.group(0), set()).add(qid)
    observations: list[tuple[int, int, str, str]] = []
    enumeration_noise = set()
    roman = {value: i for i, value in enumerate(
        ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"), 1)}
    for page in pages:
        page_lines = list(page.lines)
        for i, line in enumerate(page_lines):
            if (i and _student_subpart_label(line.text) == "1"
                    and re.search(r"\bexamples?\b", page_lines[i-1].text, re.IGNORECASE)):
                following = next((_student_subpart_label(l.text) for l in page_lines[i+1:]
                                  if _student_subpart_label(l.text)), None)
                if following == "ii":
                    enumeration_noise.add(line.line_id)
        labels = [(line.line_id, _student_subpart_label(line.text)) for line in page.lines
                  if not getattr(line, "cancelled", False)]
        labels = [(lid, label) for lid, label in labels if label]
        for previous, current, following in zip(labels, labels[1:], labels[2:]):
            if (current[1].isdigit() and previous[1] in roman and following[1] in roman
                    and roman[following[1]] == roman[previous[1]] + 2):
                # Recorded Audit OCR: (iv), handwritten (v) read as 7), (vi).
                # A list item between successive roman items is not a new Q7.
                enumeration_noise.add(current[0])
    enumeration_noise |= _restarted_point_lists(pages)
    embedded_labels, embedded_uncertain = _embedded_number_labels(
        pages, official_qids, objective_qids)
    enumeration_noise |= embedded_uncertain
    non_question_labels = enumeration_noise | embedded_labels
    for page in pages:
        if int(getattr(page, "page", 0) or 0) in label_free_pages:
            continue
        for line_index, line in enumerate(list(getattr(page, "lines", None) or []), start=1):
            if getattr(line, "cancelled", False):
                continue
            if getattr(line, "line_id", "") in non_question_labels:
                continue
            parsed = _student_label_qid(str(getattr(line, "text", "") or ""))
            if not parsed:
                continue
            # On an aggregate/MCQ row, a following (A)/(b) is not an official
            # sub-question. Canonicalize before the sequence-based OCR digit
            # repair, so "1.) (A)" still participates as Q1 in 6,1,8 -> 6,7,8.
            top_match = re.match(r"q\d+", parsed)
            top = top_match.group(0) if top_match else None
            if top in objective_qids and re.fullmatch(
                    r"\s*(?:Q\s*)?\d+\s*[.)]\s*\(?\s*[1-4A-Da-d]\s*\)?\s*",
                    readable_ocr_text(line.text), re.IGNORECASE):
                parsed = top
            if (parsed not in official_qids and top in official_qids
                    and len(children_by_top.get(top, set())) == 1):
                parsed = top
            observations.append((
                int(getattr(page, "page", 0) or 0),
                line_index,
                str(getattr(line, "line_id", "") or ""),
                parsed,
            ))

    if typed:
        typed_points = _typed_list_points(pages, observations)
        if typed_points:
            # Only their question ownership is dropped: a numbered official sub-part
            # ("1." under a question with parts 1, 2, 3) is still read as one below.
            observations = [item for item in observations if item[2] not in typed_points]
    headed_points = _points_under_headings(pages, observations)
    if headed_points:
        observations = [item for item in observations if item[2] not in headed_points]
        enumeration_noise |= headed_points

    # Handwritten digits are occasionally confused even when the surrounding label
    # is clear (the live Economics copy reads 1,2,...,6,1,8 because Mathpix mistook
    # the student's 7 for 1).  Repair only that tightly proved case: three adjacent
    # top-level labels on one page, outer numbers differ by two, the middle reading
    # duplicates an earlier label, and the missing number is official.  This cannot
    # turn ordinary answer text into a label because every observation already passed
    # the conservative chemistry-safe label parser.
    seen_before: set[str] = set()
    repaired = list(observations)
    present = {parsed for _, _, _, parsed in observations}
    for index, observation in enumerate(observations):
        page, line_index, line_id, parsed = observation
        if 0 < index < len(observations) - 1:
            prev = observations[index - 1]
            nxt = observations[index + 1]
            prev_match = re.fullmatch(r"q(\d+)", prev[3])
            cur_match = re.fullmatch(r"q(\d+)", parsed)
            next_match = re.fullmatch(r"q(\d+)", nxt[3])
            if (prev_match and cur_match and next_match
                    and prev[0] == page == nxt[0]
                    and parsed in seen_before):
                prev_num = int(prev_match.group(1))
                next_num = int(next_match.group(1))
                expected = f"q{prev_num + 1}"
                if (next_num == prev_num + 2
                        and expected in official_qids
                        and expected not in present):
                    repaired[index] = (page, line_index, line_id, expected)
                    present.add(expected)
        # A leading 1 is the digit most often lost beside the notebook margin:
        # this sheet's visible ``15.)`` was OCR'd as a second ``5.)`` immediately
        # after Q14.  Repair only a repeated one-digit label following an official
        # teen predecessor, with the exact next official qid still absent.
        if index > 0 and parsed in seen_before:
            prev_match = re.fullmatch(r"q(\d+)", observations[index - 1][3])
            cur_match = re.fullmatch(r"q(\d)", parsed)
            if prev_match and cur_match:
                expected_num = int(prev_match.group(1)) + 1
                expected = f"q{expected_num}"
                if (10 <= expected_num <= 19
                        and int(cur_match.group(1)) == expected_num % 10
                        and expected in official_qids
                        and expected not in present):
                    repaired[index] = (page, line_index, line_id, expected)
                    present.add(expected)
            # The same lost-leading-1 error occurs when a new photographed page
            # starts with Q16: the live Economics sheet's visible ``16)`` came back
            # from Mathpix as ``(6.)``.  It cannot use the Q14->Q15 adjacency proof
            # above because the student answered Q16 before returning to Q14/Q15.
            # Repair only at the FIRST observed label on a later page, after a
            # contiguous run of teen questions has already proved that the script is
            # in the teens, and only when the corresponding teen qid is official and
            # otherwise absent.  A repeated Q6 on the same page stays Q6.
            if cur_match and observations[index - 1][0] != page:
                digit = int(cur_match.group(1))
                teen_num = 10 + digit
                teen = f"q{teen_num}"
                seen_numbers = {
                    int(match.group(1))
                    for value in seen_before
                    if (match := re.fullmatch(r"q(\d+)", value))
                }
                teen_seen = sorted(
                    number for number in seen_numbers if 10 <= number < teen_num
                )
                contiguous_teens = bool(teen_seen) and teen_seen == list(
                    range(teen_seen[0], teen_seen[-1] + 1)
                )
                if (teen in official_qids and teen not in present
                        and contiguous_teens and teen_seen[-1] >= teen_num - 3):
                    repaired[index] = (page, line_index, line_id, teen)
                    present.add(teen)
        seen_before.add(parsed)

    repaired_by_position = {
        (page, line_index, line_id): parsed
        for page, line_index, line_id, parsed in repaired
    }
    subpart_fixes = _misread_subpart_labels(pages, repaired_by_position, package,
                                            enumeration_noise)
    found: dict[str, list[tuple[int, int, str]]] = {}
    content: dict[str, list] = {}
    active_parent: str | None = None
    active_prefix: str | None = None
    active_target: str | None = None
    for page in pages:
        page_number = int(getattr(page, "page", 0) or 0)
        for line_index, line in enumerate(
                list(getattr(page, "lines", None) or []), start=1):
            line_id = str(getattr(line, "line_id", "") or "")
            if getattr(line, "cancelled", False):
                continue
            parsed = repaired_by_position.get((page_number, line_index, line_id))
            if parsed:
                top_match = re.match(r"q\d+", parsed)
                top = top_match.group(0) if top_match else None
                active_parent = top
                active_prefix = parsed
                target = parsed if parsed in official_qids else None
                if (target in objective_qids and len(children_by_top.get(top, set())) > 1
                        and not re.fullmatch(
                            r"\s*(?:Q\s*)?\d+\s*[.)]\s*\(?\s*[1-4A-Da-d]\s*\)?\s*",
                            readable_ocr_text(line.text), re.IGNORECASE)):
                    # A later Part-B Q1 heading is a parent of Q1.a, not another
                    # attempt at Part-A objective Q1. Keep its child context only.
                    target = None
                if (target is None and top in official_qids
                        and len(children_by_top.get(top, set())) == 1):
                    target = top
                if target:
                    found.setdefault(target, []).append(
                        (page_number, line_index, line_id))
                    content.setdefault(target, []).append(line)
                active_target = target
                continue
            if _SECTION_HEADING_RE.fullmatch(readable_ocr_text(str(getattr(line, "text", "") or ""))):
                # "Section - B" ends whatever answer was running: Aradhya's Q7(d) ran on
                # through the whole of Section B, whose "Q9a" labels OCR cannot parse.
                active_parent = active_prefix = active_target = None
                continue
            subpart = (None if line_id in enumeration_noise else _student_subpart_label(
                str(getattr(line, "text", "") or "")))
            subpart = subpart_fixes.get(line_id, subpart) if subpart else subpart
            if active_parent and subpart:
                possible = [normalize_qid(f"{active_prefix}.{subpart}"),
                            normalize_qid(f"{active_parent}.{subpart}")]
                target = next((qid for qid in possible if qid in official_qids), "")
                if (target and target != active_target and target in found
                        and active_target and active_target.startswith(f"{active_parent}.")):
                    # Returning to an already answered sibling without a question
                    # heading is ambiguous. Preserve the current answer's list
                    # (e.g. Q2(c)'s a/b/c opinion types) for visual verification.
                    enumeration_noise.add(line_id)
                    target = ""
                    subpart = ""
                if target in official_qids:
                    found.setdefault(target, []).append(
                        (page_number, line_index, line_id))
                    active_target = target
                elif subpart and any(qid.startswith(f"{active_parent}.{subpart}.")
                         for qid in official_qids):
                    active_prefix = f"{active_parent}.{subpart}"
                    active_target = None
            if active_target:
                content.setdefault(active_target, []).append(line)

    locations: list[AnswerLocation] = []
    for question in package.questions:
        hits = found.get(normalize_qid(question.qid), [])
        if hits:
            ordered = sorted(hits, key=lambda item: (item[0], item[1]))
            owned = content.get(normalize_qid(question.qid), [])
            uncertain = [line.line_id for line in owned
                         if getattr(line, "confidence", None) is not None
                         and line.confidence < 0.8]
            # enumeration_noise grows while the pages are mapped (a lettered list
            # under Q2(c) is found there); non_question_labels is the copy taken
            # before, so both are read, or those lines lose their visual check.
            uncertain.extend(line.line_id for line in owned
                             if line.line_id in non_question_labels
                             or line.line_id in enumeration_noise)
            repaired_labels = {new[2] for old, new in zip(observations, repaired)
                               if old[3] != new[3]}
            uncertain.extend(lid for _, _, lid in ordered if lid in repaired_labels)
            confidences = [line.confidence for line in owned
                           if getattr(line, "confidence", None) is not None]
            confidence = min(confidences) if confidences else 0.0
            if any(lid in repaired_labels for _, _, lid in ordered):
                confidence = min(confidence, 0.65)
            regions = []
            # Keep disjoint runs separate (a student may return to an earlier answer).
            all_lines = [line for page in pages for line in page.lines]
            positions = {line.line_id: index for index, line in enumerate(all_lines)}
            previous = None
            for line in owned:
                pos = positions[line.line_id]
                if (previous is None or pos != previous + 1
                        or regions[-1]["page"] != line.page):
                    regions.append({"page": line.page, "line_ids": [],
                                    "bbox": list(line.bbox)})
                region = regions[-1]
                region["line_ids"].append(line.line_id)
                box = region["bbox"]
                region["bbox"] = [min(box[0], line.bbox[0]), min(box[1], line.bbox[1]),
                                  max(box[2], line.bbox[2]), max(box[3], line.bbox[3])]
                previous = pos
            locations.append(AnswerLocation(
                qid=question.qid,
                status="located",
                pages=tuple(sorted({line.page for line in owned if line.page > 0})),
                label_line_ids=tuple(line_id for _, _, line_id in ordered if line_id),
                first_position=(ordered[0][0], ordered[0][1]),
                confidence=confidence,
                line_ids=tuple(line.line_id for line in owned),
                regions=tuple(regions),
                uncertain_line_ids=tuple(dict.fromkeys(uncertain)),
            ))
        else:
            locations.append(AnswerLocation(qid=question.qid, status="unresolved"))
    return AnswerMap(package_id=package.package_id, locations=tuple(locations))


def answer_map_prompt_block(answer_map: AnswerMap) -> str:
    located = [item for item in answer_map.locations if item.status == "located"]
    unresolved = answer_map.unresolved_qids
    lines = [
        "=== DETERMINISTIC OCR ANSWER MAP ===",
        "This is a navigation aid, not a grading decision. 'Unresolved' never means blank; "
        "search the page images yourself before setting attempted=false. It files answers "
        "by their labels and position: grade each answer under the question, part or OR "
        "alternative its content answers, even where that differs from this map.",
    ]
    lines.extend(
        f"- {item.qid}: page(s) {', '.join(map(str, item.pages))}; "
        f"label(s) {', '.join(item.label_line_ids) or '(page label only)'}; "
        f"answer lines: {', '.join(item.line_ids)}"
        + (f"; visually re-read {', '.join(item.uncertain_line_ids)}"
           if item.uncertain_line_ids else "")
        for item in located
    )
    if unresolved:
        lines.append("- Unresolved labels: " + ", ".join(unresolved))
    return "\n".join(lines)
