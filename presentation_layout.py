"""Evidence location and bounded layout policy, independent of PDF drawing.

OCR/visual verification locate the evidence. This module never changes grades.
All boxes returned by the evidence locator use ORIGINAL page coordinates.
"""
import math
import re
from mathpix import word_target_bbox


# The tallest box, as a share of the page height, that still names one written row.
TIGHT_ROW = .085
# Inside a table block, one entry is the unit a teacher ticks: a journal entry with
# its narration, a row of a comparison table. Uday Bihar Board Accountancy 3319af12
# (10 Oct 2026): the journal was one OCR table block, and the second examiner's box
# for each of its four entries (three or four rows of the hand, about 9 to 12% of the
# photo) was refused as "not a tight row", so no entry got its step mark and the copy
# was held. Two rows of the hand is the legacy renderer's own limit for a block.
BLOCK_ENTRY_ROW = 2 * TIGHT_ROW


def row_box_limit(line) -> float:
    """The tallest verifier box (share of the page height) naming one row of `line`."""
    if isinstance(line, dict):
        kind, lid = line.get("kind", ""), line.get("lid") or line.get("line_id") or ""
    else:
        kind = getattr(line, "kind", "")
        lid = getattr(line, "line_id", None) or getattr(line, "lid", None) or ""
    block = kind in ("table", "diagram") or re.fullmatch(r"P\d+D\d+", str(lid))
    return BLOCK_ENTRY_ROW if block else TIGHT_ROW


def row_box_fits(line_box, box, *, block: bool = False) -> bool:
    """Does a verifier's normalized `box` name one assessed row of the OCR line whose
    normalized box is `line_box`?

    Either the box sits inside the line, with a little slack (0.02 of the page up and
    down, 0.06 across, since an OCR box hugs the ink and a verifier's box takes in the
    written entry: on Uday Bihar Board Accountancy 3319af12 a box 0.008 wider than its
    maths block was refused), no taller than one row of a plain line or two rows of
    a block. Or it is drawn round the entry its cited row opens: a journal entry with
    its narration around "Ramesh A/c Dr", a worked example around its first row
    (0a6c8e91, 10 Oct 2026, where every such box was refused and Q11 to Q13 lost all
    their step marks). Never taller than two rows of the hand."""
    try:
        vals = [float(v) for v in box]
        lx0, ly0, lx1, ly1 = (float(v) for v in line_box)
    except (TypeError, ValueError):
        return False
    if (len(vals) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in vals)
            or vals[0] >= vals[2] or vals[1] >= vals[3]):
        return False
    height = vals[3] - vals[1]
    if height > BLOCK_ENTRY_ROW + 1e-9:
        return False
    tall_line = (ly1 - ly0) > TIGHT_ROW
    inside = (vals[0] >= lx0 - .06 and vals[2] <= lx1 + .06
              and vals[1] >= ly0 - .02 and vals[3] <= ly1 + .02)
    if inside and height <= (BLOCK_ENTRY_ROW if (block or tall_line) else TIGHT_ROW) + 1e-9:
        return True
    centre = (ly0 + ly1) / 2
    return (not tall_line and vals[1] - .01 <= centre <= vals[3] + .01
            and vals[0] <= lx1 and vals[2] >= lx0)


def tight_row(page, line):
    return (not line.cancelled and (line.confidence is None or line.confidence >= .8)
            and 0 < line.bbox[3] - line.bbox[1] <= .085 * page.image_height)


def ocr_evidence_box(page, line, quote, *, visually_verified=False):
    """Use measured, unique OCR geometry; never estimate character positions."""
    if line.cancelled or (not visually_verified and line.confidence is not None and line.confidence < .8):
        return None
    box = word_target_bbox(page, line, quote)
    if box and box[3] - box[1] <= .085 * page.image_height:
        return tuple(box), 'ocr_words'
    if tight_row(page, line):
        return tuple(line.bbox), 'ocr_line'
    if visually_verified and 0 < line.bbox[3] - line.bbox[1] <= .085 * page.image_height:
        return tuple(line.bbox), 'visually_confirmed_ocr_line'
    # Some OCR responses contain both a merged table and its constituent lines.
    # Only exact unique text inside the owned region may refine its geometry.
    normalize = lambda s: ''.join(s.split()).casefold()
    wanted = normalize(quote)
    candidates = [row for row in page.lines if wanted and row.line_id != line.line_id
                  and normalize(row.text) == wanted and tight_row(page, row)
                  and row.bbox[0] >= line.bbox[0] and row.bbox[1] >= line.bbox[1]
                  and row.bbox[2] <= line.bbox[2] and row.bbox[3] <= line.bbox[3]]
    if len(candidates) == 1:
        return tuple(candidates[0].bbox), 'ocr_embedded_line'
    return None


def normalized_box(page, box):
    return [v / (page.image_width if i % 2 == 0 else page.image_height)
            for i, v in enumerate(box)]


def valid_visual_box(page, line, box):
    if len(box) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
        return False
    return row_box_fits(normalized_box(page, line.bbox), box,
                        block=row_box_limit(line) == BLOCK_ENTRY_ROW)


MAX_HORIZONTAL_GAP = 64.0
MAX_VERTICAL_GAP = 10.0


def composite_criterion(criterion):
    """Conservative legacy guard; the visual verifier also declares section scope."""
    if criterion.evidence_kind == 'missing':
        return False
    # A prose point about "significant events or transactions" is one assessed
    # explanation. Only entry/recording language implies multiple ledger checks.
    return bool(re.search(r'^\s*(?:(?:cash|bank|cash and bank)\s+)?transactions\s*[.:]?\s*$|'
                          r'\b(?:\d+|all|multiple|several)\s+transactions\b|'
                          r'\btransaction\s+entries\b|\b(?:recording|posting)\s+(?:of\s+)?transactions\b|'
                          r'\b(working notes|opening balances|all expenses|'
                          r'account preparation|classification of assets)\b|\bM1 and M2\b',
                          criterion.description, re.I))


def same_written_entry(a, b):
    """Sharing a table row is not sharing an entry (e.g. debit versus credit)."""
    x = min(a['x1'], b['x1']) - max(a['x0'], b['x0'])
    y = min(a['y1'], b['y1']) - max(a['y0'], b['y0'])
    return (a['lid'] == b['lid'] and a['page'] == b['page']
            and x >= .8 * min(a['x1']-a['x0'], b['x1']-b['x0'])
            and y >= .8 * min(a['y1']-a['y0'], b['y1']-b['y0']))


def checking_plan_problem(criterion, scope, points, award, sources, allowed):
    """Validate all visual locations before accepting a moderation certificate."""
    section = scope == 'section' or composite_criterion(criterion)
    if criterion.evidence_kind == 'missing':
        return 'Absent work must not acquire checking strokes.' if points else ''
    if section and len(points) < 2:
        return 'Multi-entry criterion needs every assessed entry in checking_points, not one representative row.'
    expected = re.search(r'\b(\d+)\s+transactions\b', criterion.description, re.I)
    if expected and len(points) < int(expected[1]):
        return f'Criterion covers {expected[1]} transactions; locate each transaction in checking_points.'
    seen = set()
    located = []
    for target in points + ([award] if award else []):
        lid, quote, box = target.get('line_id'), target.get('quote', ''), target.get('bbox', [])
        if lid not in allowed or lid not in sources or not quote.strip():
            return 'Every checking/award target needs a quote and an owned original OCR line ID.'
        page, line = sources[lid]
        if line.cancelled:
            return 'Checking/award target points at cancelled work.'
        if box:
            if not valid_visual_box(page, line, box):
                return ('Checking/award box must tightly identify actual writing inside its owned OCR region '
                        f'(box {[round(float(v), 3) for v in box]} for {lid}).')
        else:
            measured = ocr_evidence_box(page, line, quote, visually_verified=True)
            if not measured:
                return 'Merged or ambiguous checking/award target requires a tight visual bbox.'
            box = normalized_box(page, measured[0])
        if target is not award:
            signature = (lid, tuple(box))
            if signature in seen:
                return 'Duplicate checking locations cannot stand in for different assessed entries.'
            seen.add(signature)
            located.append((page.page, box[3]))
            if target.get('verdict') == 'correct' and target.get('reason'):
                return 'A deduction reason cannot be attached to a correct checking point.'
        elif section and located and (page.page, box[3] + .01) < max(located):
            return 'Section award anchor must follow all its checking points, at the section ending.'
    return ''


def horizontal_reach(anchor):
    """How far from its evidence a mark may sit and still read as that row's mark.

    64pt is three line-heights on an A4 scan; on a phone photo whose handwriting
    is 60pt tall it is one, and a circle that size has nowhere to go.
    """
    return max(MAX_HORIZONTAL_GAP, 3.0 * (anchor[3] - anchor[1]))


def vertical_reach(anchor):
    """How far above or below its row a mark may sit and still read as that row's.

    Ten points on an A4 scan; on a phone photo with 30pt handwriting a mark
    written just under the end of a full-width row sits a whole row below it.
    """
    return max(MAX_VERTICAL_GAP, 1.2 * (anchor[3] - anchor[1]))


def near_evidence(box, anchor):
    """A blank position elsewhere on the sheet is not a valid annotation slot."""
    gap = max(anchor[0] - box[2], box[0] - anchor[2], 0)
    cy = (box[1] + box[3]) / 2
    reach = vertical_reach(anchor)
    return gap <= horizontal_reach(anchor) and anchor[1] - reach <= cy <= anchor[3] + reach


def score_candidates(anchor, width, height, *, at_end=False):
    """Ordered local candidates; no whole-page search or guessed table midpoints.

    Order matters, and it is a teacher's order: on the row, to the RIGHT of the
    writing, first; below the end of a multi-line answer next; back over the
    row's padding after that; and to the LEFT of the writing last of all. The
    old order interleaved right and left at every gap, so a circle went to the
    left of the words whenever the first two points to their right were taken -
    the reviewer's "the awarded marks are not on right side".
    """
    x0, y0, x1, y1 = anchor
    mid = (y0 + y1) / 2
    reach = int(horizontal_reach(anchor))
    row_offsets = (0, -3, 3, -6, 6, -8, 8)
    right = [x1 + gap for gap in range(2, reach + 1, 2)]
    # OCR contours often include a few points of padding around handwriting.
    # Insets are still subject to the actual-ink and text-core checks.
    insets = [x1 - inset for inset in range(0, 9)] + [x1 - width - inset for inset in range(0, 9, 2)]
    left = [x0 - width - gap for gap in range(2, reach + 1, 2)] + [x0]

    def boxes(starts, center, offsets):
        for dy in offsets:
            for x in starts:
                box = (x, center + dy - height / 2, x + width, center + dy + height / 2)
                if near_evidence(box, anchor):
                    yield box

    yield from boxes(right, mid, row_offsets)
    if at_end:
        # The answer's lower-right corner, under the end of its last line.
        corner = [x1 - width - inset for inset in range(2, 15, 2)]
        yield from boxes(corner + right, y1 + 9, (0, -4, -8, -12))
    yield from boxes(insets, mid, row_offsets)
    yield from boxes(left, mid, row_offsets)
