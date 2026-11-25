"""Evidence ownership, targeted anchor repair and drawn-mark coverage."""
from __future__ import annotations

import hashlib
import re

from anchors import resolve_steps, canonical_line_id, _quote_score
from exam_package import normalize_qid
from mathpix import is_header_line


def answer_mark_pages(report, question):
    """Exclude a spillover OCR table containing the next answer's heading.

    Whole-table OCR and individual rows can overlap. An otherwise unanchored
    table must not make the preceding question claim the next question's page.
    Explicit question/criterion evidence always takes precedence.
    """
    owned = report.answer_regions.get(normalize_qid(question.qid), {})
    pages = set(owned.get("pages", []))
    anchors = {question.anchor_line_id, *(c.step_line_id for c in question.criteria)}
    # A line the grader cited is on a page the answer is on, whatever the map says.
    for lid in anchors:
        match = re.match(r"P(\d+)[LD]", lid or "")
        if match:
            pages.add(int(match.group(1)))
    for pn in list(pages):
        lids = [lid for lid in owned.get("line_ids", []) if re.match(rf"P{pn}[LD]", lid)]
        if not lids or any(lid in anchors or not re.fullmatch(rf"P{pn}D\d+", lid) for lid in lids):
            continue
        foreign_heading = any(
            key != normalize_qid(question.qid)
            and any(re.match(rf"P{pn}L", lid) for lid in region.get("label_line_ids", []))
            for key, region in report.answer_regions.items())
        if foreign_heading:
            pages.discard(pn)
    return pages


def answer_line_ids(report, question) -> list[str]:
    """The lines a question's marks may sit on: its mapped region and the lines
    its grader cited.

    The map is a guess made before grading; the grader reads the page. When they
    disagree (2026-09-16, SST batch: "c. Intermountain" mapped to the next page,
    cited on its own line) the citation is where the answer is, and a mark drawn
    there must not be reported as outside the answer.
    """
    owned = list(report.answer_regions.get(normalize_qid(question.qid), {}).get("line_ids", []))
    seen = set(owned)
    for lid in [question.anchor_line_id, *(c.step_line_id for c in question.criteria)]:
        lid = (lid or "").strip()
        if lid and lid not in seen:
            owned.append(lid)
            seen.add(lid)
    return owned


def initialize_evidence(report, package, answer_map):
    report.evidence_contract = True
    report.answer_regions = {normalize_qid(item.qid): item.as_dict()
                             for item in answer_map.locations}
    report.placement_risk_qids = []
    report.coverage_recovered_qids = []
    report.regraded_qids = []
    report.verified_step_anchors = {}
    report.verified_step_boxes = {}
    report.render_manifest = []
    report.render_requirements = []
    report.render_manifest_pdf_sha256 = ""
    report.fixed_rubric_rules = {
        normalize_qid(q.qid): [c.model_dump(mode="json") for c in q.criteria]
        for q in package.questions if q.criteria
    }
    report.rubric_unfixed_qids = [q.qid for q in package.questions
                                 if q.question_type != "objective" and not q.criteria]


def propose_answer_regions(report, ocr_pages):
    """Split an OCR continuation at a visually proposed, unambiguous answer start.

    A missed heading otherwise gives every following line to the previous answer.
    The proposal cannot consume another answer's label or primary evidence. Both
    affected questions require independent visual ownership checks before release.
    """
    lines = [line for page in ocr_pages or [] for line in page.lines]
    positions = {line.line_id: i for i, line in enumerate(lines)}
    by_id = {line.line_id: line for line in lines}
    def set_lines(region, ids):
        region["line_ids"] = ids
        region["pages"] = sorted({by_id[lid].page for lid in ids})
        region["regions"] = []
        previous = None
        for lid in ids:
            line = by_id[lid]
            if previous is None or positions[lid] != previous + 1 or region["regions"][-1]["page"] != line.page:
                region["regions"].append({"page": line.page, "line_ids": [], "bbox": list(line.bbox)})
            run = region["regions"][-1]
            run["line_ids"].append(lid)
            box = run["bbox"]
            run["bbox"] = [min(box[0], line.bbox[0]), min(box[1], line.bbox[1]),
                           max(box[2], line.bbox[2]), max(box[3], line.bbox[3])]
            previous = positions[lid]
        region["uncertain_line_ids"] = [lid for lid in region.get("uncertain_line_ids", []) if lid in ids]
    references = {}
    labels = {}
    for qid, region in report.answer_regions.items():
        for lid in region.get("label_line_ids", []):
            labels.setdefault(lid, set()).add(qid)
    for q in report.questions:
        if not q.attempted:
            continue
        qid = normalize_qid(q.qid)
        for lid in [q.anchor_line_id] + [c.step_line_id for c in q.criteria]:
            lid = canonical_line_id(lid)
            if lid in by_id:
                references.setdefault(lid, set()).add(qid)
    for q in report.questions:
        qid = normalize_qid(q.qid)
        region = report.answer_regions.setdefault(qid, {})
        if not q.attempted or any(lid in by_id and not by_id[lid].cancelled
                and not is_header_line(by_id[lid].text) for lid in region.get("line_ids", [])):
            continue
        anchors = [lid for lid, owners in references.items() if owners == {qid}
                   and not by_id[lid].cancelled and labels.get(lid, {qid}) == {qid}]
        if not anchors:
            continue
        start = min(positions[lid] for lid in anchors)
        proposed = []
        for line in lines[start:]:
            lid = line.line_id
            if labels.get(lid, {qid}) != {qid} or references.get(lid, {qid}) != {qid}:
                break
            if not line.cancelled:
                proposed.append(lid)
        if not proposed or not set(anchors).issubset(proposed):
            continue  # Conflicting/interleaved answer references need a new visual read.
        displaced = []
        for other_qid, other in report.answer_regions.items():
            if other_qid == qid:
                continue
            old = list(other.get("line_ids", []))
            if set(old).intersection(proposed):
                set_lines(other, [lid for lid in old if lid not in proposed and lid in by_id])
                other["ownership_proposed"] = True
                displaced.append(other_qid)
        set_lines(region, proposed)
        region.update(status="inferred", ownership_proposed=True)
        print(f"[evidence] {q.qid}: proposed {len(proposed)} answer lines for visual ownership "
              f"verification; split OCR continuation from {', '.join(displaced) or '(unmapped)'}", flush=True)


def _cited_lines(citations, known) -> dict[str, set[str]]:
    """{qid: lines an examiner cited for it}, keeping only citations the page bears out.

    A citation is (qid, line id, quote). The line must exist and not be cancelled,
    and a quote must be found on that line. A quote of None marks a bare question
    anchor, which counts on its id alone; an empty quote proves nothing.
    """
    cited: dict[str, set[str]] = {}
    for qid, lid, quote in citations:
        lid = canonical_line_id(lid)
        line = known.get(lid)
        if not qid or line is None or getattr(line, "cancelled", False):
            continue
        if quote is not None and _quote_score(str(quote or ""), line.text) <= 0:
            continue
        cited.setdefault(qid, set()).add(lid)
    return cited


def swap_crossed_regions(report, cited, known=None) -> list[tuple[str, str]]:
    """Give two sibling answers that the map placed by position back to the parts
    the examiner cited them for.

    MEASURED 2026-10-03 (Parishram English, Jaskeerat Q11): page 3 holds (vii)
    unlabelled, the answer to (ix) unlabelled, then "viii)" (OCR "Vii)") and (x).
    The visual inventory filed the two middle answers as (viii) then (ix) by their
    order. The examiner read the page and cited (viii) on the Teasdale lines and
    (ix) on the Beethoven lines; the anchor check called both citations "outside
    the active answer", the repair forced them back, and (ix) lost its mark for an
    answer about the wrong poem. Maths 6 Sept (Akshay): "14)" was lost by the OCR,
    so the alpha working was filed as Q13 and only its last line as Q14.

    Which answer is which is the examiners' reading; the code only checks the
    shape. Both regions are parts of one question placed by a model rather than a
    label (every line still marked for a visual re-read), and either each part is
    cited only inside the other's region (exchanged), or one part is cited inside
    its own region and a sibling's that the examiner calls unattempted and nobody
    else cites (the sibling's lines join it). Anything else is left as it was.
    """
    regions = getattr(report, "answer_regions", None) or {}
    order = {lid: i for i, lid in enumerate(known or {})}

    def parent(qid):
        return qid.rsplit(".", 1)[0] if "." in qid else ""

    def placed_by_position(region):
        ids = set(region.get("line_ids") or [])
        return bool(ids) and ids <= set(region.get("uncertain_line_ids") or [])

    pairs = []
    for x, x_cited in cited.items():
        for y, y_cited in cited.items():
            if not x < y or parent(x) != parent(y):
                continue
            rx, ry = regions.get(x) or {}, regions.get(y) or {}
            if not (placed_by_position(rx) and placed_by_position(ry)):
                continue
            lx, ly = set(rx["line_ids"]), set(ry["line_ids"])
            if x_cited and y_cited and x_cited <= ly - lx and y_cited <= lx - ly:
                pairs.append((x, y))
    involved = [qid for pair in pairs for qid in pair]
    pairs = [(x, y) for x, y in pairs if involved.count(x) == 1 and involved.count(y) == 1]
    by_qid = {normalize_qid(q.qid): q for q in getattr(report, "questions", None) or []}
    moves = []
    for x, y in pairs:
        rx, ry = regions[x], regions[y]
        for key in (set(rx) | set(ry)) - {"qid"}:
            vx, vy = rx.pop(key, None), ry.pop(key, None)
            if vy is not None:
                rx[key] = vy
            if vx is not None:
                ry[key] = vx
        moves.append((x, y, "the examiner cited each on the other's answer; the two "
                            "positional answer regions were exchanged"))
    if order:
        for x, x_cited in cited.items():
            rx = regions.get(x) or {}
            if x in involved or not placed_by_position(rx):
                continue
            lx = set(rx["line_ids"])
            for y, ry in list(regions.items()):
                q = by_qid.get(y)
                if (y == x or parent(y) != parent(x) or q is None or q.attempted
                        or not placed_by_position(ry)):
                    continue
                ly = set(ry["line_ids"])
                if (not x_cited & ly or not x_cited <= lx | ly
                        or any(lines & ly for other, lines in cited.items() if other != x)
                        or any(lid not in order for lid in lx | ly)):
                    continue
                if min(order[lid] for lid in ly) < min(order[lid] for lid in lx):
                    for key in ("label_line_ids", "first_position"):
                        if key in ry:
                            rx[key] = ry[key]
                for key in ("line_ids", "evidence_line_ids", "uncertain_line_ids"):
                    rx[key] = sorted(set(rx.get(key) or []) | set(ry.get(key) or []),
                                     key=lambda lid: order.get(lid, len(order)))
                rx["pages"] = sorted({known[lid].page for lid in rx["line_ids"]})
                rx["regions"] = []  # rebuilt from authoritative line_ids by consumers
                ry.update(status="unresolved", line_ids=[], evidence_line_ids=[],
                          uncertain_line_ids=[], label_line_ids=[], pages=[], regions=[],
                          first_position=None)
                involved += [x, y]
                moves.append((x, y, "the examiner cited one answer across both and called "
                                    "the second unattempted; its positional lines joined the first"))
                break
    for x, y, what in moves:
        for qid in (x, y):
            regions[qid]["ownership_proposed"] = True
            q = by_qid.get(qid)
            ids = regions[qid].get("line_ids") or []
            if q is not None and ids and canonical_line_id(q.anchor_line_id) not in ids:
                q.anchor_line_id = ids[0]
                match = re.match(r"P(\d+)", ids[0])
                if match:
                    q.page = int(match.group(1))
        names = [by_qid[q].qid if q in by_qid else q for q in (x, y)]
        report.placement_risk_qids = sorted(set(getattr(report, "placement_risk_qids", None) or [])
                                            | set(names))
        print(f"[evidence] {names[0]} and {names[1]}: {what}", flush=True)
    return [(x, y) for x, y, _ in moves]


def prepare_evidence(report, ocr_pages):
    """Repair only from a unique student quote inside this answer's own region."""
    propose_answer_regions(report, ocr_pages)
    all_lines = [line for page in ocr_pages or [] for line in page.lines]
    by_id = {line.line_id: line for line in all_lines}
    swap_crossed_regions(report, _cited_lines(
        [(normalize_qid(q.qid), q.anchor_line_id, None) for q in report.questions if q.attempted]
        + [(normalize_qid(q.qid), c.step_line_id, c.anchor_quote)
           for q in report.questions if q.attempted for c in q.criteria], by_id), by_id)
    owners = {lid: qid for qid, region in report.answer_regions.items()
              for lid in region.get("line_ids", [])}
    starts = {}
    for question in report.questions:
        question.anchor_line_id = canonical_line_id(question.anchor_line_id)
        if question.attempted and question.anchor_line_id in by_id:
            starts.setdefault(question.anchor_line_id, set()).add(normalize_qid(question.qid))
    risks = []
    for q in report.questions:
        qid = normalize_qid(q.qid)
        region = report.answer_regions.setdefault(qid, {})
        if region.get("ownership_proposed"):
            risks.append(q.qid)
        ids = list(region.get("line_ids", []))
        if not ids and q.attempted:
            # An unlabeled answer can still be found visually. Limit its candidate
            # region to unowned writing, never a different question's known lines.
            start = next((i for i, line in enumerate(all_lines)
                          if line.line_id == q.anchor_line_id and line.line_id not in owners
                          and starts.get(line.line_id) == {qid}), None)
            if start is not None:
                for line in all_lines[start:]:
                    if line.line_id in owners or starts.get(line.line_id, {qid}) != {qid}:
                        break
                    ids.append(line.line_id)
                    owners[line.line_id] = qid
                region.update(line_ids=ids, status="inferred",
                              pages=sorted({by_id[lid].page for lid in ids}))
            risks.append(q.qid)
        live = [by_id[lid] for lid in ids if lid in by_id and not by_id[lid].cancelled]
        region["line_ids"] = [line.line_id for line in live]
        region["evidence_line_ids"] = [line.line_id for line in live if not is_header_line(line.text)]
        if q.attempted and live:
            if q.anchor_line_id not in region["line_ids"]:
                q.anchor_line_id = live[0].line_id
                q.page = live[0].page
            if region.get("uncertain_line_ids"):
                risks.append(q.qid)
        if not q.attempted:
            continue
        candidates = [{"lid": line.line_id, "text": line.text} for line in live]
        verified = []
        for index, criterion in enumerate(q.criteria):
            signature = [criterion.step_line_id, criterion.anchor_quote]
            if report.verified_step_anchors.get(f"{qid}:{index}") == signature:
                verified.append(index)
        def acceptable(lid, criterion):
            line = by_id.get(lid)
            return bool(line and not line.cancelled and not is_header_line(line.text))
        resolutions = resolve_steps(q.criteria, candidates, model_anchor_ok=acceptable,
                                    require_evidence=True, verified_indices=verified)
        for index, (criterion, resolution) in enumerate(zip(q.criteria, resolutions)):
            if resolution.placed:
                criterion.step_line_id = resolution.line_id
                line = by_id[resolution.line_id]
                page = next((p for p in ocr_pages if p.page == line.page), None)
                cert = report.verified_step_boxes.get(f'{qid}:{index}', {})
                from presentation_layout import composite_criterion
                if composite_criterion(criterion) and not cert.get('checking_points'):
                    risks.append(q.qid)
                from presentation_layout import ocr_evidence_box
                if (page and ocr_evidence_box(page, line, criterion.anchor_quote, visually_verified=index in verified) is None
                        and not (cert.get('line_id') == line.line_id
                                 and cert.get('quote') == criterion.anchor_quote and len(cert.get('bbox', [])) == 4)):
                    risks.append(q.qid)  # Buy row localization before rendering; never silently guess.
            else:
                risks.append(q.qid)
        if not live:
            risks.append(q.qid)
    # Keep an exclusively owned parent table available for visual localization.
    # OCR may return account names as child lines but omit their amount cells.
    # Without the parent ID the verifier cannot point to those actual figures.
    for page in ocr_pages or []:
        for parent in page.lines:
            if not re.match(r'P\d+D\d+$', parent.line_id) or parent.cancelled:
                continue
            contained = {}
            foreign_overlap = set()
            for qid, region in report.answer_regions.items():
                for lid in region.get('line_ids', []):
                    child = by_id.get(lid)
                    if not child or child.page != page.page or lid == parent.line_id:
                        continue
                    a, b = parent.bbox, child.bbox
                    if min(a[2],b[2]) <= max(a[0],b[0]) or min(a[3],b[3]) <= max(a[1],b[1]):
                        continue
                    foreign_overlap.add(qid)
                    if a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]:
                        contained.setdefault(qid,set()).add(lid)
            if len(foreign_overlap) == 1:
                owner = next(iter(foreign_overlap))
                region = report.answer_regions[owner]
                if len(contained.get(owner, [])) >= 2 and parent.line_id not in region['line_ids']:
                    previous = [(key, value) for key, value in report.answer_regions.items()
                                if parent.line_id in value.get('line_ids', [])]
                    # Never displace explicit evidence or a labelled answer start.
                    protected = any(parent.line_id in [q.anchor_line_id, *(c.step_line_id for c in q.criteria)]
                                    for q in report.questions if normalize_qid(q.qid) != owner)
                    protected |= any(parent.line_id in value.get('label_line_ids', []) for _, value in previous)
                    if protected:
                        continue
                    for key, value in previous:
                        for field in ('line_ids', 'evidence_line_ids', 'uncertain_line_ids'):
                            value[field] = [lid for lid in value.get(field, []) if lid != parent.line_id]
                        value['pages'] = sorted({by_id[lid].page for lid in value['line_ids'] if lid in by_id})
                        value['regions'] = []  # rebuilt from authoritative line_ids by consumers
                        value['ownership_proposed'] = True
                        risks.extend(q.qid for q in report.questions if normalize_qid(q.qid) == key)
                    region['line_ids'].append(parent.line_id)
                    region.setdefault('evidence_line_ids', []).append(parent.line_id)
                    region['pages'] = sorted(set(region.get('pages', [])) | {page.page})
                    region['ownership_proposed'] = True
                    risks.extend(q.qid for q in report.questions if normalize_qid(q.qid) == owner)
    report.placement_risk_qids = sorted(set(risks))


# A sub-part label at the head of a criterion description: "(iv) Alkene ...",
# "Q22(iv): Which ...", "(a)(ii) Racemisation". Lowercase only, so a formula such as
# "CH3C(Cl)" is never read as a label.
_PART_LABEL_RE = re.compile(r"\((?:[a-h]|i{1,3}|iv|vi{0,3}|ix|x)\)")


def _part_label(description) -> str:
    head = str(description or "")
    head = head.split(":", 1)[0] if ":" in head[:40] else head[:24]
    return "".join(_PART_LABEL_RE.findall(head))


def _quote_key(text) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def _describes_work(text: str) -> bool:
    """True for a note in words about the student's work ("Student defines ancient
    towns as those flourishing 2000 years ago"), False for working written out as a
    claim ("Δ = xy² - xz² - y"), which the page must contain."""
    if re.search(r"[=<>≤≥×÷√∫∑Δ^²³⁴]|\\[a-z]+", text):
        return False
    tokens = text.split()
    words = [t for t in tokens if re.fullmatch(r"[^\W\d_][\w'’-]*[,.;:)]?", t)]
    return len(tokens) >= 4 and len(words) >= 0.7 * len(tokens)


def find_unsupported_credit(report, ocr_pages) -> dict[str, str]:
    """Credited steps whose own evidence the page does not bear out, by "QID:index".

    MEASURED on the 20 Sept Parishram review (Maths 6 Sept, Chemistry): the first
    examiner paid 2/2 for a determinant expansion it quoted in `evidence` and that
    is nowhere on the page, paid a Chemistry part (iv) from the very line and words
    it also used for part (v), and paid (iv) of another copy from a line that holds
    other working. Every check here is mechanical, the meaning stays with the AI:
    a flagged step is sent to the second examiner with the reason, and its credit
    is removed only when that examiner, having looked at the page, still cannot
    certify it (see uncertified_credit_keys). Nothing is flagged when the copy has
    no OCR at all, because then the check itself cannot run.

    * the marker recorded the work as missing and still paid it in full;
    * the step cites no line, or a line that is not on this copy;
    * two credited steps of DIFFERENT sub-parts cite the same line with the same
      quote: one piece of writing answers one part;
    * a FULL award whose `evidence` (empty by contract for full marks, so any text is
      a claim about the page) matches neither its cited text line nor any line of
      the answer.
    """
    lines = {line.line_id: line for page in ocr_pages or [] for line in page.lines}
    if not lines:
        return {}
    # A page the OCR could not read (a bare label at most, yet not blank) cannot bear out or
    # refute what is written on it, so an answer on it is not checked here either.
    # MEASURED on Economics 67266cbc (10 Oct 2026): page 2 of a sideways photo read as
    # "(2)" only; the grader saw Q13's working on the image, cited the nearest lines of
    # page 3, and all 4 marks of a correct answer were removed as unsupported.
    unread = {page.page for page in ocr_pages or []
              if len(getattr(page, "lines", None) or []) <= 1
              and len(re.sub(r"\W", "", "".join(str(l.text or "") for l in page.lines))) <= 6
              and not getattr(page, "blank", False)}
    regions = getattr(report, "answer_regions", None) or {}
    out: dict[str, str] = {}
    for q in report.questions:
        if not getattr(q, "attempted", True):
            continue
        qid = normalize_qid(q.qid)
        if unread:
            region = regions.get(qid) or {}
            pages = {int(p) for p in region.get("pages") or []}
            pages |= {int(m.group(1)) for lid in region.get("label_line_ids") or []
                      if (m := re.match(r"P(\d+)", str(lid)))}
            pages.add(int(getattr(q, "page", 0) or 0))
            if pages & unread:
                continue
        criteria = list(getattr(q, "criteria", None) or [])
        own = [lines[lid] for lid in answer_line_ids(report, q) if lid in lines]
        for index, c in enumerate(criteria):
            if float(c.awarded or 0) <= 1e-9:
                continue
            key = f"{qid}:{index}"
            lid = canonical_line_id(c.step_line_id)
            full = float(c.awarded) >= float(c.max) - 1e-9
            if c.evidence_kind == "missing" and full:
                out[key] = "the first examiner recorded this work as missing but paid for it."
                continue
            if not lid or lid not in lines:
                out[key] = ("this credit cites no line of the student's writing"
                            + (f" ({lid} is not on this copy)." if lid else "."))
                continue
            quote, label = _quote_key(c.anchor_quote), _part_label(c.description)
            other = next((o for j, o in enumerate(criteria)
                          if j != index and float(o.awarded or 0) > 1e-9
                          and canonical_line_id(o.step_line_id) == lid
                          and len(quote) >= 4 and _quote_key(o.anchor_quote) == quote
                          and label and _part_label(o.description) not in ("", label)), None)
            if other is not None:
                out[key] = (f"part {label} is credited from the same line {lid} and the same "
                            f"words as part {_part_label(other.description)}.")
                continue
            evidence = str(c.evidence or "").strip()
            line = lines[lid]
            if (evidence and full and not re.fullmatch(r"P\d+D\d+", lid)
                    and "[detected diagram]" not in str(line.text or "")
                    and not any(_quote_score(evidence, str(l.text or "")) > 0
                                for l in [line, *own])):
                # A note that DESCRIBES the work ("Student defines ancient towns as
                # those flourishing 2000 years ago") is not a quote, and failing to
                # find it on the page proves nothing when the step's own quote IS
                # there. MEASURED on a Parishram Geography copy (8 Oct 2026): Q14's
                # ancient and medieval towns, quoted word for word from page 8, were
                # flagged, the second examiner could not certify them, and 2 marks the
                # faculty gave were removed. A note naming figures that are not on the
                # page is still a claim the page must bear out (the determinant case).
                # Figures are looked for on the whole page of the cited line: the
                # next row of the same point ("... during 2000 years ago") is often
                # outside the mapped region.
                answer_text = " ".join(str(l.text or "") for l in [line, *own]) + " " + " ".join(
                    str(l.text or "") for l in lines.values() if l.page == line.page)
                quoted = len(_quote_key(c.anchor_quote)) >= 4 and any(
                    _quote_score(str(c.anchor_quote or ""), str(l.text or "")) > 0
                    for l in [line, *own])
                figures = re.findall(r"\d+(?:\.\d+)?", evidence)
                if (quoted and _describes_work(evidence)
                        and all(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", answer_text)
                                for f in figures)):
                    continue
                out[key] = (f"its stated evidence {evidence[:60]!r} is not on line {lid} "
                            "or anywhere in this answer.")
    return out


def uncertified_credit_keys(report, verdicts, unverified_rows, *, include_awards,
                            selected_qids) -> set[str]:
    """Flagged credit the second examiner was asked about and could not certify.

    Only rows the examiner actually ruled on with evidence_status other than
    'verified' count. A row with no verdict, or with two, is a check that did not
    run, and its credit stays the first examiner's.
    """
    flagged = dict(getattr(report, "unsupported_credit", None) or {})
    if not flagged or not unverified_rows:
        return set()
    from grader import _audit_rows
    rows = _audit_rows(report, include_awards, selected_qids)
    ruled: dict[int, list] = {}
    for verdict in verdicts or []:
        ruled.setdefault(int(verdict.row), []).append(verdict)
    keys = set()
    for row in unverified_rows:
        entries = ruled.get(row, [])
        if (len(entries) != 1 or entries[0].evidence_status == "verified"
                or not 0 <= row < len(rows)):
            continue
        q, index, _maximum = rows[row]
        key = f"{normalize_qid(q.qid)}:{index}"
        if index >= 0 and key in flagged and float(q.criteria[index].awarded) > 1e-9:
            keys.add(key)
    return keys


def remove_unsupported_credit(report, keys) -> list[str]:
    """Zero flagged credit that neither the page nor the second examiner supports."""
    notes = []
    for q in report.questions:
        for index, c in enumerate(getattr(q, "criteria", None) or []):
            key = f"{normalize_qid(q.qid)}:{index}"
            if key not in keys or float(c.awarded) <= 1e-9:
                continue
            label = " ".join(str(c.description or "this part").split()).rstrip(".")
            notes.append(f"{q.qid}: {float(c.awarded):g} of {float(c.max):g} for "
                         f"{label!r} removed, because "
                         f"{(getattr(report, 'unsupported_credit', None) or {}).get(key, 'no evidence was found')} "
                         "The second examiner could not confirm it from the page.")
            c.awarded = 0.0
            c.met = False
            c.evidence = f"{label[:1].upper()}{label[1:]} is not shown in your working."
            if not str(q.remark or "").strip() or float(q.score) >= float(q.max_score) - 1e-9:
                q.remark = c.evidence
    if notes:
        from grader import _recompute_scores
        _recompute_scores(report)
    return notes


def propose_verifier_regions(report, verdicts, ocr_pages, *, include_awards, selected_qids):
    """Use fresh examiner references to propose a previously unlocated answer.

    Primary references may overlap even when the examiner found distinct answers.
    Reuse the conservative boundary splitter on a copy; never apply its grades.
    Every affected decision must be requested again against the proposed regions.
    """
    from collections import Counter
    from grader import _audit_rows
    candidate = report.model_copy(deep=True)
    rows = _audit_rows(candidate, include_awards, selected_qids)
    known = {line.line_id: line for page in ocr_pages or [] for line in page.lines}
    selected = {normalize_qid(q.qid) for q, _, _ in rows}
    unresolved = {qid for qid in selected if not any(
        lid in known and not known[lid].cancelled and not is_header_line(known[lid].text)
        for lid in report.answer_regions.get(qid, {}).get('line_ids', []))}
    if not unresolved:
        return {}
    counts = Counter(item.row for item in verdicts)
    references = {}
    for item in verdicts:
        if (counts[item.row] != 1 or not 0 <= item.row < len(rows)
                or item.evidence_status != 'verified'):
            continue
        q, index, _ = rows[item.row]
        qid = normalize_qid(q.qid)
        lid = canonical_line_id(item.step_line_id)
        if (not q.attempted or not item.qid or normalize_qid(item.qid) != qid
                or lid not in known or known[lid].cancelled
                or is_header_line(known[lid].text)):
            continue
        if index >= 0:
            q.criteria[index].step_line_id = lid
            q.criteria[index].anchor_quote = item.anchor_quote
        references.setdefault(qid, []).append(lid)
    if not unresolved.intersection(references):
        return {}
    positions = {lid: i for i, lid in enumerate(known)}
    for q in candidate.questions:
        lids = references.get(normalize_qid(q.qid), [])
        if lids:
            q.anchor_line_id = min(lids, key=positions.get)
    propose_answer_regions(candidate, ocr_pages)
    changed = {qid for qid, region in candidate.answer_regions.items()
               if region.get('line_ids', []) != report.answer_regions.get(qid, {}).get('line_ids', [])}
    if not changed or not changed.issubset(selected):
        return {}  # An unrequested answer cannot lose evidence in a focused audit.
    if not (unresolved & changed).issubset(references):
        return {}  # No newly proposed answer may rely only on stale primary anchors.
    if not any(candidate.answer_regions.get(qid, {}).get('line_ids') for qid in unresolved & changed):
        return {}
    report.answer_regions = candidate.answer_regions
    for qid in changed:
        region = report.answer_regions[qid]
        region['ownership_proposed'] = True
        region['evidence_line_ids'] = [lid for lid in region.get('line_ids', [])
            if lid in known and not known[lid].cancelled and not is_header_line(known[lid].text)]
    report.placement_risk_qids = sorted(set(report.placement_risk_qids) | {
        q.qid for q in report.questions if normalize_qid(q.qid) in changed})
    return {row: f'{q.qid}: answer ownership was newly proposed from examiner references. '
            'Read the original images and confirm this decision using its updated allowed evidence IDs; '
            'return uncertain if the written answer belongs elsewhere.'
            for row, (q, _, _) in enumerate(rows) if normalize_qid(q.qid) in changed}


def _owned_verifier_line_id(original, quote, box, live):
    lid = canonical_line_id(original)
    scored = [(line, _quote_score(quote, line.text)) for line in live]
    scored = [(line, score) for line, score in scored if score > 0]
    strongest = max((score for _, score in scored), default=0)
    matches = [line for line, score in scored
               if strongest < 1.5 or score == strongest]
    if len(matches) == 1 and not (box and lid in {line.line_id for line in live}):
        lid = matches[0].line_id
    return lid


def resolve_verified_anchors(report, verdicts, ocr_pages, *, include_awards, selected_qids):
    """Repair verifier bookkeeping from unique evidence inside the owned answer.

    Scores, question ownership and quotes are unchanged. Unresolved references are
    returned as repair gaps, so the existing bounded verification repair can handle
    them before applying any marks. No global nearest-line search is permitted.
    """
    from grader import _audit_rows
    rows = _audit_rows(report, include_awards, selected_qids)
    known = {line.line_id: line for page in ocr_pages or [] for line in page.lines}
    # The examiner's reading of which unlabelled part is which wins over the map's
    # order, when the move has an exact shape (swap_crossed_regions).
    swap_crossed_regions(report, _cited_lines(
        [(normalize_qid(rows[v.row][0].qid), v.step_line_id, v.anchor_quote or "")
         for v in verdicts if 0 <= v.row < len(rows) and v.evidence_status == "verified"
         and rows[v.row][0].attempted
         and (not v.qid or normalize_qid(v.qid) == normalize_qid(rows[v.row][0].qid))], known), known)
    gaps = {}
    for verdict in verdicts:
        if not 0 <= verdict.row < len(rows) or verdict.evidence_status != "verified":
            continue  # Structural/uncertainty checks belong to audit_completion_gaps.
        q, index, _maximum = rows[verdict.row]
        if not q.attempted:
            continue
        qid = normalize_qid(q.qid)
        if verdict.qid and normalize_qid(verdict.qid) != qid:
            continue
        allowed = report.answer_regions.get(qid, {}).get("line_ids", [])
        live = [known[lid] for lid in allowed if lid in known and not known[lid].cancelled
                and (index < 0 or not is_header_line(known[lid].text))]
        live_ids = {line.line_id for line in live}
        original = verdict.step_line_id
        # A quote may fix a misread ID even when the ID happens to exist elsewhere.
        # It cannot move the boundary or certify cancelled / other-question writing.
        # A valid owned visual target wins over OCR text. Repeated amounts may
        # be omitted from this table's transcript but recognized on a later page.
        # Its tight box is validated below; do not move it to that later entry.
        lid = _owned_verifier_line_id(original, verdict.anchor_quote, verdict.anchor_bbox, live)
        if lid not in live_ids:
            detail = (f"{q.qid}: evidence anchor {original!r} is outside the active answer; "
                      f"choose from {', '.join(sorted(live_ids)) or '(no located evidence)'}. "
                      "Copy a quote from the relevant line; do not reuse another question's anchor.")
            gaps[verdict.row] = detail
            continue
        verdict.step_line_id = lid
        line = known[lid]
        page = next((p for p in ocr_pages if p.page == line.page), None)
        from presentation_layout import ocr_evidence_box
        if (page and ocr_evidence_box(page, line, verdict.anchor_quote, visually_verified=True) is None
                and not verdict.anchor_bbox):
            gaps[verdict.row] = (
                f'{q.qid}: OCR anchor {lid} does not reliably locate one assessed row. Return anchor_bbox '
                'around this criterion\'s actual row, normalized [left,top,right,bottom] '
                'in the original student page image. A table midpoint is not a valid step location.')
        if lid != original:
            print(f"[evidence] {q.qid} row {verdict.row}: resolved {original!r} to {lid}", flush=True)
        if index >= 0:
            from presentation_layout import checking_plan_problem
            # Checking/award targets use the same original-ID contract as the
            # primary anchor, including harmless wrappers and unique owned quotes.
            for point in [*verdict.checking_points, *([verdict.award_anchor] if verdict.award_anchor else [])]:
                point.line_id = _owned_verifier_line_id(point.line_id, point.quote, point.bbox, live)
            checked_criterion = q.criteria[index].model_copy(update={'evidence_kind': verdict.evidence_kind})
            problem = checking_plan_problem(checked_criterion, verdict.award_scope,
                [p.model_dump() for p in verdict.checking_points],
                verdict.award_anchor.model_dump() if verdict.award_anchor else None,
                {l.line_id: (p, l) for p in ocr_pages for l in p.lines}, set(allowed))
            if problem:
                gaps[verdict.row] = f'{q.qid}: {problem}'
    return gaps


def apply_verified_anchors(report, verdicts, ocr_pages, *, include_awards, selected_qids):
    from grader import _audit_rows
    from strict_grading import StrictGradeError
    gaps = resolve_verified_anchors(report, verdicts, ocr_pages,
        include_awards=include_awards, selected_qids=selected_qids)
    if gaps:
        raise StrictGradeError("VERIFIED_ANCHOR_OUTSIDE_ANSWER", next(iter(gaps.values())))
    rows = _audit_rows(report, include_awards, selected_qids)
    known = {line.line_id: line for page in ocr_pages or [] for line in page.lines}
    # Validate every row before mutating any certificate or report anchor.
    seen = set()
    for verdict in verdicts:
        if (not 0 <= verdict.row < len(rows) or verdict.evidence_status != "verified"
                or verdict.row in seen):
            raise StrictGradeError("VERIFIED_ANCHOR_OUTSIDE_ANSWER",
                                   "An unverified or unknown row cannot certify an evidence anchor.")
        q = rows[verdict.row][0]
        if verdict.qid and normalize_qid(verdict.qid) != normalize_qid(q.qid):
            # The row number is what the examiner was asked for; the echoed id is a
            # courtesy it may spell its own way ("Q10.alt2" for "Q10.OR"). It is
            # noted, never a reason to hold the copy (operator's rule, 2026-09-17).
            print(f"[moderation] row {verdict.row} echoed {verdict.qid!r} for {q.qid!r}; "
                  "the row number decides", flush=True)
        seen.add(verdict.row)
        if verdict.anchor_verdict == 'missing' and verdict.evidence_kind != 'missing':
            raise StrictGradeError('INVALID_PRESENTATION_ANCHOR',
                                   f'{q.qid}: a missing-work symbol must be supported by missing-work evidence.')
        if verdict.anchor_bbox:
            import math
            from presentation_layout import BLOCK_ENTRY_ROW, row_box_fits, row_box_limit
            box = verdict.anchor_bbox
            page = next(p for p in ocr_pages if p.page == known[verdict.step_line_id].page)
            line = known[verdict.step_line_id]
            outer = [line.bbox[0] / page.image_width, line.bbox[1] / page.image_height,
                     line.bbox[2] / page.image_width, line.bbox[3] / page.image_height]
            if (len(box) != 4 or not all(math.isfinite(v) for v in box)
                    or not row_box_fits(outer, box, block=row_box_limit(line) == BLOCK_ENTRY_ROW)):
                raise StrictGradeError('INVALID_PRESENTATION_ANCHOR',
                                       f'{q.qid}: the visual row box must identify a tight row inside its verified OCR region '
                                       f'(box {[round(float(v), 3) for v in box]}, region {[round(v, 3) for v in outer]}).')
    for verdict in verdicts:
        q, index, _maximum = rows[verdict.row]
        if not q.attempted:
            continue
        qid = normalize_qid(q.qid)
        lid = verdict.step_line_id
        line = known[lid]
        if index >= 0:
            q.criteria[index].step_line_id = lid
            q.criteria[index].anchor_quote = verdict.anchor_quote
            q.criteria[index].evidence_kind = verdict.evidence_kind
            report.verified_step_anchors[f"{qid}:{index}"] = [lid, verdict.anchor_quote]
            report.verified_step_boxes.pop(f'{qid}:{index}', None)
            if verdict.anchor_bbox or verdict.anchor_verdict or verdict.checking_points or verdict.award_anchor:
                report.verified_step_boxes[f'{qid}:{index}'] = {
                    'line_id': lid, 'quote': verdict.anchor_quote,
                    'bbox': list(verdict.anchor_bbox), 'verdict': verdict.anchor_verdict,
                    'source': 'visual_verifier', 'award_scope': verdict.award_scope,
                    'deduction_reason': verdict.reason if verdict.correct < _maximum else '',
                    'checking_points': [p.model_dump() for p in verdict.checking_points],
                    'award_anchor': verdict.award_anchor.model_dump() if verdict.award_anchor else None}
        else:
            q.anchor_line_id = lid
            q.page = line.page


def check_render_manifest(report, pdf_bytes):
    """Every required answer/step must have a mark tied to its actual answer line."""
    from preflight import Issue
    if not report.evidence_contract:
        return []
    problems = []
    if report.render_manifest_pdf_sha256 != hashlib.sha256(pdf_bytes).hexdigest():
        return [Issue("block", "render-certificate-mismatch", "The drawing record does not match this PDF.")]
    rows = report.render_manifest
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        from pdf_renderer import student_ink_mask, _is_objective_mark
        from preflight import _ink_under_mark, _MARK_INK_BLOCK
        layouts = report.render_page_layouts
        if any(abs(float(v["margin_bbox"][0]) - float(v["content_bbox"][2])) > .01
               for v in layouts.values() if v.get("margin_bbox") and v.get("content_bbox")):
            problems.append(Issue("block", "inconsistent-margin-layout",
                                  "The score strip is separated from the answer sheet by an extra gap."))
        # Two margin notes written over each other are unreadable (Uday Bihar Board
        # Accountancy 3319af12, released 10 Oct 2026 with Q12 and Q13 overlapping).
        notes = [row for row in rows if row.get("role") == "feedback" and row.get("bbox")]
        for i, first in enumerate(notes):
            a = fitz.Rect(first["bbox"])
            for second in notes[i + 1:]:
                if second.get("page") != first.get("page"):
                    continue
                b = fitz.Rect(second["bbox"])
                overlap = (a & b)
                if not overlap.is_empty and overlap.get_area() > 0.15 * min(a.get_area(), b.get_area()):
                    problems.append(Issue("block", "feedback-overlap",
                                          f"{first['qid']} and {second['qid']}: their margin notes "
                                          "are written over each other."))
        masks = {}
        for row in rows:
            pno = row.get("page", 0)
            box = fitz.Rect(row.get("bbox", (0, 0, 0, 0)))
            if not 1 <= pno <= len(doc) or box.is_empty:
                problems.append(Issue("block", "invisible-mark", f"{row['qid']}: invalid drawing geometry."))
            elif not doc[pno - 1].rect.contains(box):
                problems.append(Issue("block", "invisible-mark", f"{row['qid']}: drawing extends beyond the page."))
            elif row.get("numeral_bbox"):
                if pno not in masks:
                    # Erase the ruling at the tilt the renderer found, so this raster
                    # and the renderer's agree on what is ink.
                    content = layouts.get(str(pno), {}).get("content_bbox")
                    masks[pno] = student_ink_mask(
                        doc[pno - 1], skew=layouts.get(str(pno), {}).get("ruling_skew"),
                        content_w=(content[2] - content[0]) if content else None,
                        content_top=float(content[1]) if content else 0.0)
                mask, scale = masks[pno]
                if _ink_under_mark(mask, scale, row["numeral_bbox"]) >= _MARK_INK_BLOCK:
                    problems.append(Issue("block", "mark-overlaps-writing", f"{row['qid']}: a score numeral covers student writing."))
            if row.get("role") in {"answer", "step", "total", "continuation", "verdict"}:
                layout = layouts.get(str(pno), {})
                content = fitz.Rect(layout.get("content_bbox", (0, 0, 0, 0)))
                # The teacher layout may put a mark just past the divider, beside its
                # own row, when the answer runs to the edge of the page; it records
                # that gutter in the page layout. The legacy layout has no gutter:
                # its marks stay on the sheet.
                allowed = fitz.Rect(content)
                if layout.get("gutter_bbox"):
                    allowed |= fitz.Rect(layout["gutter_bbox"])
                if not allowed.contains(box):
                    problems.append(Issue("block", "mark-outside-sheet",
                                          f"{row['qid']}: its checking mark is outside the original answer sheet."))
                if 1 <= pno <= len(doc):
                    text_blocks = doc[pno-1].get_text('dict', clip=box + (-1,-1,1,1)).get('blocks', [])
                    for block in text_blocks:
                        for line in block.get('lines', []):
                            for span in line.get('spans', []):
                                color = span.get('color', 0)
                                if ((color >> 16) > 150 and ((color >> 8) & 255) < 80 and (color & 255) < 80
                                        and span['text'].strip() and not re.fullmatch(r'\d+(?:\.\d+)?', span['text'].strip())):
                                    problems.append(Issue('block', 'forbidden-sheet-label',
                                                          f"{row['qid']}: answer-sheet annotations may contain only circled numbers and checking strokes."))
        for i, row in enumerate(rows):
            if not row.get("numeral_bbox"):
                continue
            box = fitz.Rect(row["numeral_bbox"])
            for other in rows[i + 1:]:
                if other["page"] != row["page"] or other["role"] == "continuation":
                    continue
                other_box = fitz.Rect(other.get("numeral_bbox") or other["bbox"])
                overlap = box & other_box
                if overlap.get_area() > .25 * min(box.get_area(), other_box.get_area()):
                    problems.append(Issue("block", "mark-collision", f"{row['qid']}: score overlaps another annotation."))
        # Read back the rendered artifact, not just a claim that drawing was requested.
        drawings = {i + 1: [d for d in page.get_drawings()
                           if (d.get("color") or d.get("fill") or (0,))[0] > .6]
                    for i, page in enumerate(doc)}
        # A visible but wrong numeral is a score contradiction, not a missing
        # circle. Commerce SME review: a step awarded 1 was printed as 2. Read the
        # PDF itself so a correct report/manifest cannot certify the wrong ink.
        for row in rows:
            if (row.get("role") not in {"answer", "step", "total"}
                    or row.get("score") is None or not row.get("numeral_bbox")):
                continue
            pno = row.get("page", 0)
            box = fitz.Rect(row["numeral_bbox"])
            if not 1 <= pno <= len(doc) or box.is_empty:
                continue
            printed = "".join(doc[pno - 1].get_textbox(box + (-1, -1, 1, 1)).split())
            expected = f"{float(row['score']):g}"
            if printed and printed != expected:
                problems.append(Issue(
                    "block", "printed-award-mismatch",
                    f"{row['qid']}: the circled mark reads {printed!r}, "
                    f"but this award is {expected}."))
        for row in rows:
            if row["role"] in {"answer", "step", "total"} and not any(
                    d["rect"].intersects(fitz.Rect(row["bbox"]))
                    for d in drawings.get(row["page"], [])):
                problems.append(Issue("block", "invisible-mark", f"{row['qid']}: no mark is visible at its recorded position."))
        visible_verdicts = []
        for row in rows:
            if row.get('verdict') not in {'correct', 'partial', 'wrong'}:
                continue
            box = fitz.Rect(row.get('verdict_bbox') or row['bbox'])
            # A circle alone must not satisfy the tick/cross requirement. Checking
            # strokes use tapered red paths, whereas circles have four cubic arcs.
            # The recorded box is the stroke's PATH; the pen adds a fringe of up
            # to 0.12 x the tick size outside it. A stroke belongs to this box when
            # most of it lies inside, so a large tick is found and a neighbouring
            # mark that merely grazes the box is not.
            def _mostly_inside(rect, box=box):
                area = rect.get_area()
                if area <= 1e-6:
                    # A bare line (the page divider, the header rule) has no area
                    # to be "mostly" inside; it counts only when it lies within.
                    return box.contains(rect) and rect.width < box.width and rect.height < box.height
                return (box & rect).get_area() >= .6 * area
            strokes = [d for d in drawings.get(row['page'], [])
                       if _mostly_inside(d['rect'])
                       and (d.get('color') or d.get('fill') or (0, 0, 0))[1] < .4
                       and d.get('items') and all(i[0] == 'l' for i in d['items'])]
            extent = fitz.Rect()
            for stroke in strokes:
                extent |= stroke['rect'] + (-.01, -.01, .01, .01)
            if len(strokes) >= 3 and extent.width > 3 and extent.height > 3:
                visible_verdicts.append(row)
            else:
                problems.append(Issue('block', 'verdict-not-visible', f"{row['qid']}: its tick or cross is missing."))
        for q in report.questions:
            if q.attempted and not any(r['qid'] == q.qid for r in visible_verdicts):
                problems.append(Issue('block', 'verdict-not-visible', f'{q.qid}: no tick or cross is visible on its answer.'))
            if not q.attempted:
                continue
            for ci, criterion in enumerate(q.criteria):
                from presentation_layout import composite_criterion
                cert = report.verified_step_boxes.get(f'{normalize_qid(q.qid)}:{ci}', {})
                if cert and (cert.get('line_id') != criterion.step_line_id or cert.get('quote') != criterion.anchor_quote):
                    problems.append(Issue('block', 'stale-checking-plan', f'{q.qid}: checking plan no longer matches its criterion.'))
                    cert = {}
                points = cert.get('checking_points', [])
                if composite_criterion(criterion) and not points:
                    problems.append(Issue('block', 'incomplete-checking-coverage',
                                          f'{q.qid}: a multi-entry criterion has no individual checking plan.'))
                for pi, point in enumerate(points):
                    point_id = f'{ci}:{pi}'
                    matches = [r for r in visible_verdicts if r['qid'] == q.qid
                               and point_id in r.get('checking_points', []) and ci in r.get('criteria', [])
                               and r.get('line_id') == point['line_id'] and r.get('verdict') == point['verdict']]
                    if not matches:
                        problems.append(Issue('block', 'checking-point-missing',
                                              f'{q.qid}: checking point {point_id} has no matching visible verdict.'))
                if cert.get('award_scope') == 'section' and not cert.get('award_anchor') and any(
                        r['qid'] == q.qid and r['role'] == 'step' and ci in r.get('criteria', []) for r in rows):
                    problems.append(Issue('block', 'section-score-on-entry',
                                          f'{q.qid}: a section score was attached to an isolated entry.'))
        for requirement in report.render_requirements:
            index = requirement.get('criterion')
            if requirement.get('role') not in {'step', 'verdict'} or index is None:
                continue
            q = next((q for q in report.questions if q.qid == requirement['qid']), None)
            if q and 0 <= index < len(q.criteria) and q.criteria[index].evidence_kind != 'missing':
                if not any(r['qid'] == q.qid and index in r.get('criteria', []) for r in visible_verdicts):
                    problems.append(Issue('block', 'verdict-not-visible', f'{q.qid}: a step has no visible checking stroke.'))
        for q in report.questions:
            if not q.attempted or not _is_objective_mark(q):
                continue
            if q.score <= 1e-6:
                # Wrong answers need the visible cross already checked above. A
                # circled 0 beside the cross is how the approved copies read; any
                # other numeral beside a cross contradicts it.
                if any(row.get("qid") == q.qid and row.get("role") == "answer"
                       and row.get("numeral_bbox")
                       and row.get("score_text") not in ("0", "0.0") for row in rows):
                    problems.append(Issue("block", "zero-beside-cross",
                                          f"{q.qid}: a zero numeral was drawn beside its cross."))
                continue
            expected = f"{q.score:g}"
            visible = False
            for row in rows:
                if (row.get("qid") != q.qid or row.get("role") != "answer"
                        or not row.get("numeral_bbox") or row.get("score_text") != expected
                        or row.get("score_style") != "circled_award"):
                    continue
                pno = row.get("page", 0)
                box = fitz.Rect(row["numeral_bbox"])
                if (not 1 <= pno <= len(doc) or box.is_empty
                        or not (fitz.Rect(row["bbox"]) + (-.01, -.01, .01, .01)).contains(box)
                        or not doc[pno - 1].rect.contains(box)):
                    continue
                # A recorded score alone is insufficient: read back the actual
                # numeral next to the verdict, excluding the distant summary.
                printed = doc[pno - 1].get_textbox(box + (-1, -1, 1, 1))
                circled = any(len(d.get("items", [])) == 4
                              and all(item[0] == "c" for item in d["items"])
                              and all(abs(a - b) < .2 for a, b in zip(d["rect"], box))
                              for d in drawings.get(pno, []))
                if "".join(printed.split()) == expected and circled:
                    visible = True
                    break
            if not visible:
                problems.append(Issue("block", "mcq-score-not-marked",
                                      f"{q.qid}: its circled awarded mark is missing beside the MCQ answer."))
        for row in rows:
            if row.get("role") != "feedback":
                continue
            if row.get('reason_page') is not None and row['reason_page'] != row['page']:
                problems.append(Issue('block','reason-on-wrong-page',
                                      f"{row['qid']}: deduction reason is not on the error's page."))
            layout = report.render_page_layouts.get(str(row["page"]), {})
            margin = fitz.Rect(layout.get("margin_bbox", (0, 0, 0, 0)))
            content = fitz.Rect(layout.get("content_bbox", (0, 0, 0, 0)))
            if (layout.get("margin_side") != "right" or margin.x0 < content.x1 - .01
                    or not margin.contains(fitz.Rect(row["bbox"]))):
                problems.append(Issue("block", "feedback-outside-right-margin",
                                      f"{row['qid']}: its feedback is outside the right margin strip."))
                continue
            question = next((q for q in report.questions if q.qid == row["qid"]), None)
            if question is not None and 1 <= row["page"] <= len(doc):
                owned_pages = report.answer_regions.get(normalize_qid(question.qid), {}).get("pages", [])
                if _is_objective_mark(question) and owned_pages and row["page"] not in owned_pages:
                    problems.append(Issue("block", "mcq-total-on-wrong-page",
                                          f"{question.qid}: its margin score is not on the answer's page."))
                printed = doc[row["page"] - 1].get_textbox(fitz.Rect(row["bbox"]) + (-1, -1, 1, 1))
                if row.get('reason_continuation'):
                    # A note on another page of the answer that names its own question
                    # needs no second score: the score is written once (CA faculty
                    # review, 17 Oct 2026, read a repeated score as marks given twice).
                    from pdf_renderer import _label as _printed_label
                    shown_label = _printed_label(question.qid, report)
                    flat = " ".join(printed.split())
                    labelled = (row.get('label_only') and (question.qid in flat or shown_label in flat)
                                and any(r.get('margin_score') and r.get('qid') == row['qid']
                                        and r.get('role') == 'feedback' for r in rows))
                    if labelled:
                        continue
                    if not printed.strip() or not any(r.get('margin_score') and r.get('qid') == row['qid']
                            and r.get('page') == row['page'] and r.get('role') == 'feedback' for r in rows):
                        problems.append(Issue('block','feedback-question-missing',
                                              f"{row['qid']}: continued feedback needs a visible same-page score heading."))
                    continue  # Its shared heading is independently checked below/above.
                if question.qid not in printed:
                    problems.append(Issue("block", "feedback-question-missing",
                                          f"{row['qid']}: its feedback is missing its question label."))
                expected = (f"{question.score:g}/{question.max_score:g}"
                            if question.max_score else f"{question.score:g}")
                # Check the rendered lines, so a fraction split as '9.5/1' and
                # '0' cannot pass merely because concatenated text reads 9.5/10.
                rendered_lines = doc[row['page'] - 1].get_text('dict', clip=fitz.Rect(row['bbox']))
                if not any(expected in ''.join(span['text'] for span in line['spans']).replace(' ', '')
                           for block in rendered_lines['blocks'] for line in block.get('lines', [])):
                    problems.append(Issue('block', 'margin-score-not-marked',
                                          f'{question.qid}: its awarded/maximum score is missing or split in the right margin.'))
        for q in report.questions:
            if not q.attempted:
                continue
            step_mode = any(r['qid'] == q.qid and r['role'] == 'step' for r in report.render_requirements)
            if step_mode:
                awards = [r for r in rows if r['qid'] == q.qid and r['role'] == 'step']
                if abs(sum(r.get('score') or 0 for r in awards) - q.score) > .001:
                    problems.append(Issue('block', 'step-awards-do-not-reconcile', f'{q.qid}: sheet awards do not equal its margin total.'))
                if any(r['qid'] == q.qid and r.get('question_total') for r in rows):
                    problems.append(Issue('block', 'duplicate-sheet-total', f'{q.qid}: step awards must not have another on-sheet total.'))
                for row in awards:
                    indices = row.get('criteria', [])
                    if indices and all(isinstance(i, int) and 0 <= i < len(q.criteria)
                                       for i in indices):
                        expected_award = sum(float(q.criteria[i].awarded) for i in indices)
                        if abs(float(row.get('score') or 0) - expected_award) > .001:
                            problems.append(Issue(
                                'block', 'step-award-mismatch',
                                f'{q.qid}: this step shows {float(row.get("score") or 0):g} '
                                f'but its criteria award {expected_award:g}.'))
                    if not row.get('numeral_bbox'):
                        if (row.get('score') or 0) > 0:
                            problems.append(Issue('block', 'award-circle-missing', f'{q.qid}: an awarded step has no circle.'))
                        continue
                    circle = fitz.Rect(row['numeral_bbox'])
                    printed = doc[row['page']-1].get_textbox(circle + (-1,-1,1,1))
                    if ''.join(printed.split()) != f"{row['score']:g}" or not any(
                            len(d.get('items', [])) == 4 and all(i[0] == 'c' for i in d['items'])
                            and all(abs(a-b)<.2 for a,b in zip(d['rect'],circle)) for d in drawings.get(row['page'], [])):
                        problems.append(Issue('block', 'award-circle-missing', f'{q.qid}: its circled step award is not visible.'))
                continue
            totals = [row for row in rows if row.get("qid") == q.qid and row.get("question_total")]
            crowded = any(r['qid'] == q.qid and r.get('presentation_mode') in {'crowded_question_total', 'section_question_total'}
                          for r in report.render_requirements)
            if crowded:
                if len(totals) != 1 or any(r['qid'] == q.qid and r['role'] == 'step' for r in rows):
                    problems.append(Issue('block', 'duplicate-sheet-total',
                                          f'{q.qid}: crowded layout needs exactly one total and no step awards.'))
                for i, c in enumerate(q.criteria):
                    if c.max > 0 and c.evidence_kind != 'missing' and not any(
                            r['qid'] == q.qid and i in r.get('criteria', []) for r in visible_verdicts):
                        problems.append(Issue('block', 'verdict-not-visible',
                                              f'{q.qid}: crowded layout lost a step checking stroke.'))
            visible = False
            for row in totals:
                box = fitz.Rect(row.get("numeral_bbox") or (0, 0, 0, 0))
                if box.is_empty or not 1 <= row.get("page", 0) <= len(doc):
                    continue
                printed = doc[row["page"] - 1].get_textbox(box + (-1, -1, 1, 1))
                circled = any(len(d.get("items", [])) == 4
                              and all(item[0] == "c" for item in d["items"])
                              and all(abs(a - b) < .2 for a, b in zip(d["rect"], box))
                              for d in drawings.get(row["page"], []))
                if "".join(printed.split()) == f"{q.score:g}" and circled:
                    visible = True
            if not visible:
                problems.append(Issue("block", "question-total-not-marked",
                                      f"{q.qid}: its circled total is missing on the answer sheet."))
    for requirement in report.render_requirements:
        qid = requirement["qid"]
        matches = [row for row in rows if row.get("qid") == qid
                   and (row.get("role") == requirement["role"]
                        or requirement['role'] == 'verdict' and bool(row.get('verdict'))
                        or requirement["role"] == "total" and row.get("question_total"))
                   and (requirement.get("criterion") is None
                        or requirement["criterion"] in row.get("criteria", []))
                   and (not requirement.get('checking_point')
                        or requirement['checking_point'] in row.get('checking_points', []))]
        if not matches:
            problems.append(Issue("block", "required-mark-missing",
                                  f"{qid}: {requirement['role']} mark is missing beside the answer."))
    for row in rows:
        if row.get("role") not in {"answer", "step", "total", "verdict"}:
            continue
        question = next((q for q in report.questions if q.qid == row["qid"]), None)
        owned = answer_line_ids(report, question) if question is not None else []
        # A row of a merged MCQ block that the student numbered with THIS question's
        # number is that question's answer, whichever question the map gave the block to.
        numbered = (row.get("array_row_label") is not None and question is not None
                    and re.match(r"\s*[Qq]?\s*(\d+)", question.qid or "")
                    and int(re.match(r"\s*[Qq]?\s*(\d+)", question.qid).group(1)) == row["array_row_label"])
        if not row.get("line_id") or (row["line_id"] not in owned and not numbered):
            problems.append(Issue("block", "mark-outside-answer", f"{row['qid']}: mark is not anchored to its answer."))
        anchor_box = row.get("anchor_bbox")
        box = row.get("bbox")
        if anchor_box and box:
            from presentation_layout import near_evidence
            layout = report.render_page_layouts.get(str(row.get("page")), {})
            gutter = layout.get("gutter_bbox") if layout.get("layout") == "teacher" else None
            # The teacher layout's gutter placement: past the divider, level with
            # its own row. That is beside the evidence, as the reviewer approved.
            in_gutter = bool(gutter) and fitz.Rect(gutter).contains(fitz.Rect(box)) and (
                min(box[3], anchor_box[3]) - max(box[1], anchor_box[1]) > 0)
            if not in_gutter and not near_evidence(box, anchor_box):
                problems.append(Issue("block", "mark-away-from-evidence", f"{row['qid']}: mark drifted away from its evidence line."))
    # An empty/partial requirements list must never waive the per-question check.
    for q in report.questions:
        if not q.attempted:
            continue
        if not any(row.get('qid') == q.qid and row.get('margin_score')
                   and row.get('role') == 'feedback' for row in rows):
            problems.append(Issue('block', 'margin-score-not-marked',
                                  f'{q.qid}: its score is missing from the right margin.'))
        step_mode = any(r['qid'] == q.qid and r['role'] == 'step' for r in report.render_requirements)
        if not step_mode and not any(row.get("qid") == q.qid and row.get("question_total")
                   and row.get("score") is not None and abs(row["score"] - q.score) < 1e-6 for row in rows):
            problems.append(Issue("block", "question-total-not-marked", f"{q.qid}: its awarded total is not recorded on the copy."))
        if not any(row.get("qid") == q.qid and row.get("role") in {"answer", "step", "verdict"} for row in rows):
            problems.append(Issue("block", "answer-not-marked", f"{q.qid}: no visible answer mark was recorded."))
        # Empty heading pages must not acquire invented correctness marks.
        # Step coverage is enforced by the per-criterion requirements above.
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            problems += objective_marks_off_their_rows(report, doc)
    except Exception:  # noqa: BLE001 - a raster that cannot be built proves nothing
        pass
    problems += objective_answers_sharing_a_row(report)
    return problems


def objective_answers_sharing_a_row(report) -> list:
    """Two objective questions whose marks cite the same single answer line.

    MEASURED 2026-10-21/22 (UP and MP Board Class 11 Commerce, copies released straight
    to students): Economics MCQ 2 and MCQ 3 both cited the "Ans 3rd (D)" line, so MCQ
    2's cross and "Correct answer is (D)" sat on the student's right answer D and the
    "Ans 2nd" line had no mark at all; on two Business Studies copies Q1 and Q2 both
    cited Q2's line, which showed a tick, a cross and two marks. Each mark sat on the
    row it cited, so objective_marks_off_their_rows passed them. Answers in two columns
    of one line cite different boxes, and a box taller than one answer line (a block
    cited for several MCQs) is left to that check."""
    from preflight import Issue
    from pdf_renderer import _is_objective_mark
    objective = {q.qid for q in report.questions
                 if _is_objective_mark(q) and getattr(q, "attempted", True)}
    cited: dict = {}
    for row in report.render_manifest or []:
        if (row.get("qid") not in objective or row.get("role") not in {"answer", "verdict"}
                or not row.get("anchor_bbox")):
            continue
        key, box = (row.get("page"), row["qid"]), [float(v) for v in row["anchor_bbox"]]
        if key not in cited or box[3] - box[1] < cited[key][3] - cited[key][1]:
            cited[key] = box
    pages: dict = {}
    for (page, qid), box in cited.items():
        pages.setdefault(page, []).append((qid, box))
    problems, reported = [], set()
    for rows in pages.values():
        heights = sorted(box[3] - box[1] for _, box in rows)
        line = heights[len(heights) // 2]
        for i, (first, a) in enumerate(rows):
            for second, b in rows[i + 1:]:
                tall = min(a[3], b[3]) - max(a[1], b[1])
                wide = min(a[2], b[2]) - max(a[0], b[0])
                if (tall >= 0.8 * min(a[3] - a[1], b[3] - b[1])
                        and wide >= 0.8 * min(a[2] - a[0], b[2] - b[0])
                        and max(a[3] - a[1], b[3] - b[1]) <= 1.6 * line
                        and not {first, second} & reported):
                    reported.update({first, second})
                    problems.append(Issue(
                        "block", "objective-answers-share-a-row",
                        f"{first} and {second}: both are marked on the same answer line, so "
                        "one of them is marked on the wrong answer and another answer has "
                        "no mark."))
    return problems


def objective_marks_off_their_rows(report, doc) -> list:
    """An MCQ's mark drawn beside another answer's row.

    MEASURED 2026-10-14 (Vidyapeeth Class 8 Maths, seven copies released straight to
    students): on five a tick, cross or circled 0 read as the next answer's (rows
    20pt apart, marks 25pt tall, free to slide 8pt), and one MCQ part was marked on
    blank paper below its case study. Every other check passed them, because each
    mark was "near" its evidence. A mark of an MCQ belongs on its own row: its centre
    within that row, unless it sits in the labelled margin lane."""
    from preflight import Issue
    from pdf_renderer import _is_objective_mark
    objective = {q.qid for q in report.questions
                 if _is_objective_mark(q) and getattr(q, "attempted", True)}
    layouts = report.render_page_layouts or {}
    marks = []
    for row in report.render_manifest:
        if (row.get("qid") not in objective or row.get("role") not in {"answer", "verdict", "total"}
                or not row.get("anchor_bbox") or not row.get("bbox")):
            continue
        content = layouts.get(str(row.get("page")), {}).get("content_bbox")
        if content and float(row["bbox"][0]) >= float(content[2]) - 1:
            continue                                   # the QID-labelled margin lane
        marks.append(row)
    # A question's own row is the tightest row any of its marks cites: a mark may cite
    # a whole merged MCQ block, its other marks that block's row for this question.
    bands = {}
    for row in marks:
        key, band = (row["page"], row["qid"]), [float(v) for v in row["anchor_bbox"]]
        if key not in bands or band[3] - band[1] < bands[key][3] - bands[key][1]:
            bands[key] = band
    problems, reported = [], set()
    for row in marks:
        box, own = [float(v) for v in row["bbox"]], bands[(row["page"], row["qid"])]
        centre = (box[1] + box[3]) / 2
        if own[1] - 3 <= centre <= own[3] + 3 or row["qid"] in reported:
            continue
        beside = [qid for (page, qid), band in bands.items()
                  if page == row["page"] and qid != row["qid"] and band[1] + 2 <= centre <= band[3] - 2]
        # Just under or over its own row, where no other answer is, is the corner a
        # two-column MCQ row leaves for its mark; it reads as this answer's.
        away = max(own[1] - centre, centre - own[3])
        if not beside and away <= max(12.0, own[3] - own[1]):
            continue
        reported.add(row["qid"])
        problems.append(Issue("block", "mark-on-another-row",
                              f"{row['qid']}: its mark is drawn "
                              + (f"beside {beside[0]}'s answer" if beside else "off its own answer's row")
                              + ", where the student would read it as that row's."))
    return problems
