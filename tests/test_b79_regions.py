# -*- coding: utf-8 -*-
"""Build 79: question regions that spill and marks bound to the wrong line.

Every fixture is the real Mathpix OCR of a copy the faculty re-marked by hand
(Parishram 20 Sept Chemistry and English, 6 Sept Maths). Each case names the copy,
what the tool did and what the reviewer recorded as the right line.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from answer_inventory import VerifiedSpan, merge_inventory  # noqa: E402
from answer_map import build_answer_map  # noqa: E402
from evidence_repair import repair_report_evidence  # noqa: E402
from exam_package import build_exam_package  # noqa: E402
from grader import Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402
from mathpix import LineBox, PageOCR, build_transcript, is_header_line  # noqa: E402
import pdf_renderer  # noqa: E402
import teacher_layout  # noqa: E402


def _page(number, width, height, rows):
    return PageOCR(number, width, height, [
        LineBox(lid, text, tuple(float(v) for v in box), number, kind=kind)
        for lid, text, box, kind in rows])


def L(lid, text, box, kind="text"):
    return (lid, text, box, kind)


def _doc(sizes, ocr=(), ink=None):
    """Blank pages of these sizes, with ink where `ocr` has lines (the middle of each
    box, or the OCR-pixel rows given in `ink` by line id)."""
    doc = fitz.open()
    for width, height in sizes:
        doc.new_page(width=width, height=height)
    for pg in ocr:
        page = doc[pg.page - 1]
        sx, sy = page.rect.width / pg.image_width, page.rect.height / pg.image_height
        for line in pg.lines:
            x0, y0, x1, y1 = line.bbox
            y0, y1 = (ink or {}).get(line.line_id, (y0 + .3 * (y1 - y0), y1 - .3 * (y1 - y0)))
            page.draw_rect(fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy),
                           color=None, fill=(0.15, 0.15, 0.4))
    for page in doc:
        page.insert_text((5, 12), ".", fontsize=4, color=(.9, .9, .9))
    return doc


def _package(items, total):
    return build_exam_package(
        exam_type="school", student_class="Class 12", subject="Test", level="12",
        paper="Test", attempt="", applicable_law_date=None, total_marks=total,
        question_paper=b"paper", answer_key=b"key", key_source="upload",
        marks_scheme=MarksScheme(total=total, items=items), scheme_source="text")


ENGLISH = _package(
    [MarksItem(qid=f"Q{i}", max=1, question_type="objective", correct_answer="B") for i in range(1, 11)]
    + [MarksItem(qid=f"Q11.{r}", max=1, question_type="theory")
       for r in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")]
    + [MarksItem(qid=q, max=5, question_type="theory") for q in ("Q12.a", "Q12.b")], 30)

CHEMISTRY = _package(
    [MarksItem(qid=f"Q{i}", max=1, question_type="objective", correct_answer="C") for i in range(1, 6)]
    + [MarksItem(qid=f"Q{i}", max=2, question_type="theory") for i in range(6, 16)]
    + [MarksItem(qid=f"Q{i}", max=5, question_type="theory") for i in range(16, 25)], 70)


def _span(qid, *lids):
    return VerifiedSpan(qid=qid, line_ids=list(lids), pages=sorted({int(l[1]) for l in lids}))


def _located(result, qid):
    return next(item for item in result.answer_map.locations if item.qid == qid)


# ---------------------------------------------------------------- Shweta (English)
# Q12(a) is on page 2. Page 3 holds Q11 (iv) to (x); its labels "v)", "(vi)" ... do not
# parse under the Q12 heading the map is still running, so the map gave all of page 3
# to Q12(a), and the Q12(a) total 3.5 was drawn beside Q11(x) on page 3.
SHWETA_P2 = _page(2, 1241, 1754, [
    L('P2L4', 'Section - B', (365, 124, 676, 226)),
    L('P2L5', 'Q-12. (a) In ACt III the Macbeth was foolish about', (45, 231, 1051, 335)),
    L('P2L6', 'crown the he wants his fruitless brown his own', (133, 288, 1144, 381)),
    L('P2L7', 'no one can take back his crown but', (107, 335, 1025, 402)),
    L('P2L30', 'the feance reen off . Then varbeth started', (165, 1388, 1219, 1536)),
    L('P2L31', 'feel insure, an mater mater he santed', (101, 1483, 1196, 1669)),
    L('P2L32', 'doing frusterated and ofraid.', (157, 1560, 1009, 1687)),
])
SHWETA_P3 = _page(3, 1241, 1754, [
    L('P3L9', 'v) he wants to know who was the person is', (63, 227, 1167, 357)),
    L('P3L10', 'going to trouble him that he will take', (188, 287, 1198, 411)),
    L('P3L11', 'further step.', (177, 347, 469, 429)),
    L('P3L12', '(v) They warn him to beware of the person who', (47, 446, 1171, 580)),
    L('P3L13', 'was born from his mother.', (164, 509, 865, 618)),
    L('P3L14', '(vi) they warn him to macduff.', (30, 611, 868, 745)),
    L('P3L15', '(vii) There is no person, they died in nuclear', (5, 725, 1116, 850)),
    L('P3L16', 'bomb explosion.', (157, 786, 502, 869)),
    L('P3L17', '(viii)', (12, 891, 126, 947)),
    L('P3L18', '(ix) he started to make notes from his mind', (8, 948, 1131, 1075)),
    L('P3L19', 'which come to their symphonies.', (149, 1007, 937, 1121)),
    L('P3L20', '(x) his distining power is weak, day by day he', (8, 1112, 1154, 1241)),
    L('P3L21', 'deaf it ivas use as starkly.', (131, 1175, 938, 1283)),
])
SHWETA_P4 = _page(4, 1241, 1754, [
    L('P4L7', 'Section - A', (163, 189, 343, 249)),
    L('P4L8', '1. (B) Played most foully to obtain the crown.', (14, 288, 994, 415)),
    L('P4L9', "2 (C) the witches have prophesied that Banque's descendent", (5, 352, 1200, 482)),
    L('P4L10', 'will be kings.', (150, 415, 462, 508)),
    L('P4L11', "3.(B) Macheth's guilt and psychological instability.", (9, 468, 1120, 590)),
    L('P4L12', '4. (C) have dealt with Macbeth without consulting her.', (8, 526, 1193, 652)),
    L('P4L13', '5 (B) Macduff.', (12, 586, 362, 683)),
    L('P4L14', '6.(C) despair and rage.', (9, 647, 526, 746)),
])


class RegionStopsWhereAnAddedAnswerBegins(unittest.TestCase):
    def test_shweta_q12a_region_keeps_to_page_2(self):
        pages = [SHWETA_P2, SHWETA_P3]
        amap = build_answer_map(ENGLISH, pages)
        before = next(item for item in amap.locations if item.qid == "Q12.a")
        self.assertIn("P3L21", before.line_ids)       # the map alone runs on to page 3
        spans = {"Q11.iv": _span("Q11.iv", "P3L9", "P3L10", "P3L11"),
                 "Q11.v": _span("Q11.v", "P3L12", "P3L13"),
                 "Q11.vi": _span("Q11.vi", "P3L14"),
                 "Q11.vii": _span("Q11.vii", "P3L15", "P3L16"),
                 "Q11.ix": _span("Q11.ix", "P3L18", "P3L19"),
                 "Q11.x": _span("Q11.x", "P3L20", "P3L21")}
        result = merge_inventory(amap, spans, {"Q11.viii"}, pages)
        q12a = _located(result, "Q12.a")
        self.assertEqual(q12a.pages, (2,))
        self.assertEqual(q12a.label_line_ids, ("P2L5",))
        self.assertTrue(all(lid.startswith("P2") for lid in q12a.line_ids), q12a.line_ids)
        self.assertEqual([r["page"] for r in q12a.regions], [2])
        self.assertEqual(_located(result, "Q11.x").line_ids, ("P3L20", "P3L21"))

    def test_shweta_mcq_regions_end_at_the_next_mcq(self):
        # "1. (B) ..." ran on over "2 (C) ..." and "3.(B) ..." (both added by the
        # inventory, the map cannot parse "2 (C)" with its long option text).
        pages = [SHWETA_P4]
        amap = build_answer_map(ENGLISH, pages)
        spans = {"Q2": _span("Q2", "P4L9", "P4L10"), "Q3": _span("Q3", "P4L11"),
                 "Q5": _span("Q5", "P4L13"), "Q6": _span("Q6", "P4L14")}
        result = merge_inventory(amap, spans, set(), pages)
        self.assertEqual(_located(result, "Q1").line_ids, ("P4L8",))
        self.assertEqual(_located(result, "Q4").line_ids, ("P4L12",))

    def test_anushka_q11_ix_does_not_run_into_x(self):
        # "2 x) it helps reader..." (Q11.x) was read as no label at all, so Q11.ix's
        # region held it, and the (ix) mark sat on the (x) answer.
        pages = [_page(2, 3125, 4167, [
            L('P2L12', '11 vii) the house keeps cooking and clearing', (214, 1885, 2487, 2091)),
            L('P2L13', 'perfectly for a human family that', (638, 2017, 2512, 2188)),
            L('P2L14', 'is abready dead.', (663, 2129, 1612, 2280)),
            L('P2L15', '11 wii) it shous that the nature does not care', (170, 2316, 2726, 2500)),
            L('P2L16', 'if human disappear, warning us', (680, 2443, 2430, 2637)),
            L('P2L17', 'about the danger of destroying ourselves.', (678, 2566, 2847, 2773)),
            L('P2L18', "11. ix) he created some of the worldest's gretest", (121, 2769, 2803, 3004)),
            L('P2L19', 'music even though he could not', (661, 2919, 2633, 3082)),
            L('P2L20', 'hear anything.', (574, 3010, 1460, 3207)),
            L('P2L21', '2 ×) it helps reader to focus on deep, beautiful', (121, 3269, 2951, 3470)),
            L('P2L22', 'music that Becthoven reated.', (595, 3381, 2373, 3563)),
        ])]
        amap = build_answer_map(ENGLISH, pages)
        self.assertIn("P2L22", next(i for i in amap.locations if i.qid == "Q11.ix").line_ids)
        spans = {"Q11.viii": _span("Q11.viii", "P2L15", "P2L16", "P2L17"),
                 "Q11.x": _span("Q11.x", "P2L21", "P2L22")}
        result = merge_inventory(amap, spans, set(), pages)
        self.assertEqual(_located(result, "Q11.ix").line_ids, ("P2L18", "P2L19", "P2L20"))

    def test_dimpal_q1_heading_that_opens_q19_takes_the_inventory_lines(self):
        # "Ans 1 g (ii) CH3CH2CH2CH2Cl + NaI" is Q19 (ii). The map read Q1 there, the
        # region ran through Q19 and Q20, and Q1's MCQ mark was drawn on a Q20 line.
        pages = [
            _page(1, 1241, 1754, [
                L('P1L5', 'Section - A', (615, 272, 759, 303)),
                L('P1D8', '[detected diagram]', (458, 508, 832, 604), 'diagram'),
                L('P1L9', '3- Claoroheptane', (527, 611, 792, 660)),
                L('P1L10', 'Ans-2 Both \\( A \\) and \\( R \\) are true and \\( R \\) is the correc',
                  (219, 689, 1114, 750)),
                L('P1L11', 'explanation of \\( A \\).', (323, 731, 607, 788)),
                L('P1L12', 'Ans-3 Esopropyl chloride', (201, 796, 632, 864)),
            ]),
            _page(3, 1241, 1754, [
                L('P3L17', 'Ans 1 g (ii) \\( \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{CH}_{2} '
                           '\\mathrm{CH}_{2} \\mathrm{Cl}+\\mathrm{NaI} \\)', (150, 1242, 1091, 1335)),
                L('P3L18', '(iii) \\( \\mathrm{C}_{6} \\mathrm{H}_{5} \\mathrm{Cl}+2 \\mathrm{Na} \\)',
                  (201, 1316, 1068, 1411)),
                L('P3L19', '(iv) Treat with alc. KOH removes the Br and a \\( \\beta-H \\) to',
                  (188, 1398, 1063, 1488)),
            ]),
            _page(4, 1241, 1754, [
                L('P4L6', '(v) 1-Bromo1-methyl Pent 3-ena', (193, 334, 661, 421)),
                L('P4L8', 'Ans occ) 2-Chloro 3-methyl butane', (171, 405, 682, 484)),
                L('P4L9', '1-1 odo -2,2 - dincethyl butane', (275, 451, 725, 524)),
            ]),
        ]
        amap = build_answer_map(CHEMISTRY, pages)
        before = next(i for i in amap.locations if i.qid == "Q1")
        self.assertEqual(before.label_line_ids, ("P3L17",))
        spans = {"Q1": _span("Q1", "P1D8", "P1L9"),
                 "Q19": _span("Q19", "P3L17", "P3L18", "P3L19", "P4L6"),
                 "Q20": _span("Q20", "P4L8", "P4L9")}
        result = merge_inventory(amap, spans, set(), pages)
        q1 = _located(result, "Q1")
        self.assertEqual(q1.line_ids, ("P1D8", "P1L9"))
        self.assertEqual(q1.pages, (1,))

    def test_rishita_q30_stops_at_the_q28_heading(self):
        # "g28." is Q28's heading, unreadable to the map, so Q30's one line answer
        # "Q30. (0). 1. (Ans)" ran on through Q28's working and onto page 11, where
        # the grader then cited "k = 8/z" for Q30.
        maths = _package(
            [MarksItem(qid=f"Q{i}", max=1, question_type="objective", correct_answer="A") for i in range(1, 6)]
            + [MarksItem(qid=f"Q{i}", max=5, question_type="numeric") for i in range(6, 31)], 130)
        pages = [
            _page(10, 1240, 1755, [
                L('P10L28', r'LHD \( \neq \) RHD [Not differentiable]', (295, 1027, 726, 1090)),
                L('P10L29', 'Q30. (0). 1. (Ans)', (152, 1109, 529, 1162)),
                L('P10D31', r'\begin{tabular}[t]{|l|l|l|l|} \hline 1+x & & 1 & \\ \hline \end{tabular}',
                  (247, 1173, 686, 1328), 'table'),
                L('P10L30', 'g28.', (124, 1193, 195, 1236)),
                L('P10D41', '[detected diagram]', (244, 1369, 785, 1531), 'diagram'),
                L('P10L42', r'\[ (x-z)(y-x)(z-y)=0 . \]', (209, 1546, 633, 1661), 'math'),
                L('P10L44', 'Scanned with OKEN Scanner', (889, 1699, 1220, 1726)),
            ]),
            _page(11, 1240, 1755, [
                L('P11L2', r'\[ k=\frac{8}{z} \text { (Ans) } \]', (238, 95, 633, 515), 'math'),
            ]),
        ]
        amap = build_answer_map(maths, pages)
        self.assertIn("P11L2", next(i for i in amap.locations if i.qid == "Q30").line_ids)
        result = merge_inventory(amap, {"Q28": _span("Q28", "P10L30", "P10D41", "P10L42")}, set(), pages)
        q30 = _located(result, "Q30")
        self.assertEqual(q30.line_ids[0], "P10L29")
        self.assertNotIn("P10L42", q30.line_ids)
        self.assertNotIn("P11L2", q30.line_ids)
        self.assertEqual(q30.pages, (10,))

    def test_a_region_with_no_added_answer_inside_is_unchanged(self):
        pages = [SHWETA_P2, SHWETA_P3]
        amap = build_answer_map(ENGLISH, pages)
        result = merge_inventory(amap, {}, set(), pages)
        self.assertEqual(_located(result, "Q12.a"), next(i for i in amap.locations if i.qid == "Q12.a"))


# ---------------------------------------------------------------- evidence repair
def _mcq(qid, anchor, page, chosen="C", score=1.0):
    return QuestionMark(qid=qid, score=score, max_score=1, page=page, y_fraction=.5, objective=True,
                        chosen_option=chosen, anchor_line_id=anchor)


class McqBindsToItsOwnLine(unittest.TestCase):
    def test_hamza_q2_leaves_a_q21_point_on_page_8_for_its_own_line(self):
        # The tool drew Q2's tick on "2. Formula of (1)" (Q21, page 8); the reviewer's
        # line is P1L4 "2.) C) Both A and R are true ...", among Q1, Q3, Q4, Q5.
        ocr = [
            _page(1, 942, 1650, [
                L('P1L2', 'Section - A', (25, 72, 196, 120)),
                L('P1L3', '1) C) 3 - chlor oheptane', (6, 118, 341, 194)),
                L('P1L4', '2.) C) Both \\( A \\) and \\( R \\) are true \\( R \\) is the correctexplanalion',
                  (5, 180, 929, 251)),
                L('P1L5', 'of \\( A \\)', (85, 232, 160, 280)),
                L('P1L6', '3) C) Isopropy L Chloride', (7, 287, 389, 362)),
                L('P1L7', '4) C) Prodomim Predominantly But -7 -cene', (6, 356, 683, 430)),
                L('P1L8', '5) C) 3 bromo - 1-chloro cy (lo hex-1-ene', (9, 428, 767, 507)),
                L('P1L9', 'Section B', (17, 541, 175, 592)),
            ]),
            _page(8, 937, 1650, [
                L('P8L1', 'Q21) \\( \\ln 8 \\) alivany', (7, 3, 707, 175)),
                L('P8L3', '2. Formula of (1)', (61, 180, 348, 285)),
                L('P8L6', 'Thrompound \\( A \\) is 1 - Bromo -2 -methy propare (Isobaty \\( C \\)',
                  (15, 459, 834, 593)),
            ]),
        ]
        questions = [_mcq("Q1", "P1L3", 1), _mcq("Q2", "P8L3", 8), _mcq("Q3", "P1L6", 1),
                     _mcq("Q4", "P1L7", 1), _mcq("Q5", "P1L8", 1),
                     QuestionMark(qid="Q21", score=3, max_score=5, page=8, y_fraction=.2,
                                  anchor_line_id="P8L6", criteria=[
                                      Criterion(description="A", awarded=1, max=1, met=True,
                                                step_line_id="P8L6", anchor_quote="1 - Bromo -2 -methy")])]
        report = GradeReport(total_score=8, max_total=10, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions={
                                 "q2": {"qid": "Q2", "status": "located", "pages": [1, 8],
                                        "line_ids": ["P1L4", "P1L5", "P8L3"],
                                        "label_line_ids": ["P1L4", "P8L3"]}})
        doc = _doc([(942, 1650)] * 7 + [(937, 1650)])
        repair_report_evidence(report, ocr, doc)
        self.assertEqual(questions[1].anchor_line_id, "P1L4")
        self.assertEqual(questions[1].page, 1)
        self.assertEqual(report.answer_regions["q2"]["line_ids"], ["P1L4"])
        self.assertEqual([q.anchor_line_id for q in questions[2:5]], ["P1L6", "P1L7", "P1L8"])

    def test_an_answered_option_line_on_another_page_stays(self):
        # A student who answered Q2 at the end of the booklet ("2) (C) ...") keeps it.
        ocr = [
            _page(1, 942, 1650, [
                L('P1L3', '1) C) 3 - chlor oheptane', (6, 118, 341, 194)),
                L('P1L4', '2) A) Both A and R', (5, 180, 400, 251)),
                L('P1L6', '3) C) Isopropy L Chloride', (7, 287, 389, 362)),
                L('P1L7', '4) C) Predominantly But 2 ene', (6, 356, 683, 430)),
            ]),
            _page(2, 942, 1650, [L('P2L3', '2) (C) Both A and R are true', (61, 180, 548, 285))]),
        ]
        questions = [_mcq("Q1", "P1L3", 1), _mcq("Q2", "P2L3", 2), _mcq("Q3", "P1L6", 1),
                     _mcq("Q4", "P1L7", 1)]
        report = GradeReport(total_score=4, max_total=4, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions={})
        repair_report_evidence(report, ocr, _doc([(942, 1650)] * 2))
        self.assertEqual(questions[1].anchor_line_id, "P2L3")

    def test_shweta_mcq_row_takes_its_anchor_page(self):
        # The report said Q3, Q5 and Q6 were on page 1; their anchors are page 4 lines.
        questions = [_mcq("Q1", "P4L8", 4, "B"), _mcq("Q2", "P4L9", 4), _mcq("Q3", "P4L11", 1, "B"),
                     _mcq("Q4", "P4L12", 4), _mcq("Q5", "P4L13", 1, "B"), _mcq("Q6", "P4L14", 1)]
        regions = {f"q{i}": {"qid": f"Q{i}", "status": "located", "pages": [4],
                             "line_ids": [lid], "label_line_ids": [lid]}
                   for i, lid in ((1, "P4L8"), (2, "P4L9"), (3, "P4L11"), (4, "P4L12"),
                                  (5, "P4L13"), (6, "P4L14"))}
        report = GradeReport(total_score=6, max_total=6, overall_remarks="", questions=questions,
                             evidence_contract=True, answer_regions=regions)
        repair_report_evidence(report, [SHWETA_P4], _doc([(1241, 1754)] * 4))
        self.assertEqual({q.qid: q.page for q in questions},
                         {f"Q{i}": 4 for i in range(1, 7)})
        self.assertEqual([q.anchor_line_id for q in questions],
                         ["P4L8", "P4L9", "P4L11", "P4L12", "P4L13", "P4L14"])


# ---------------------------------------------------------------- scanner footer
RISHITA_P5 = _page(5, 1240, 1755, [
    L('P5L10', 'Q15. \\( f(x)=\\left\\{\\begin{array}{cc}x^{2}+3, & x \\neq 0 \\\\ 1, & x=0\\end{array}\\right. \\)',
      (258, 913, 630, 997)),
    L('P5D11', '\\begin{tabular}[t]{|l|l|l|} \\hline LHL & RHL & f(0)=.1 \\\\ \\hline \\end{tabular}',
      (325, 993, 903, 1160), 'table'),
    L('P5L17', '\\[ \\lim _{x \\rightarrow 0^{+}} x^{2}+3 \\]', (550, 1051, 709, 1103)),
    L('P5L20', '\\( \\therefore \\quad L H L=R H L \\neq f(0) \\quad \\) [Hence, not continueius at \\( x=0 \\)',
      (397, 1165, 1011, 1230)),
    L('P5D21', '[detected diagram]', (229, 1240, 1011, 1683), 'diagram'),
    L('P5L23', 'Scanned with OKEN Scanner', (889, 1699, 1220, 1726)),
])


class ScannerFooterIsNotWriting(unittest.TestCase):
    def test_footer_reads_as_a_header(self):
        self.assertTrue(is_header_line("Scanned with OKEN Scanner"))
        self.assertTrue(pdf_renderer._is_header("Scanned with OKEN Scanner"))
        self.assertTrue(pdf_renderer._is_header("Scanned with CamScanner"))
        self.assertFalse(is_header_line("The area was scanned by radar in 1990 for minerals"))
        self.assertIn("[P5L23] Scanned with OKEN Scanner (HEADER", build_transcript([RISHITA_P5]))

    def test_rishita_q15_mark_is_not_drawn_on_the_footer(self):
        # The tool put Q15's 2/2 on "Scanned with OKEN Scanner" under the photo; the
        # reviewer's line is P5L20 "LHL = RHL != f(0) [Hence, not continuous]".
        q = QuestionMark(qid="Q15", score=2, max_score=2, page=5, y_fraction=.5, anchor_line_id="P5L20",
                         criteria=[Criterion(description="Continuity at 0", awarded=2, max=2, met=True,
                                             step_line_id="P5L20", anchor_quote="LHL=RHL")])
        report = GradeReport(total_score=2, max_total=2, overall_remarks="Good work.", questions=[q],
                             evidence_contract=True, answer_regions={
                                 "q15": {"qid": "Q15", "status": "located", "pages": [5],
                                         "label_line_ids": ["P5L10"],
                                         "line_ids": ["P5L10", "P5D11", "P5L17", "P5L20", "P5D21", "P5L23"]}})
        doc = _doc([(1240, 1755)] * 5, [RISHITA_P5])
        layout = teacher_layout.TeacherLayout(doc, report, [RISHITA_P5])
        self.assertNotIn("P5L23", [line.lid for line in layout._region_lines(q)])
        teacher_layout.build_teacher_pdf(doc, report, [RISHITA_P5])
        rows = [row for row in report.render_manifest if row.get("qid") == "Q15"
                and row.get("role") == "answer"]
        self.assertTrue(rows)
        self.assertNotIn("P5L23", {row.get("end_line_id") for row in rows})
        self.assertNotIn("P5L23", {row.get("line_id") for row in rows})


# ---------------------------------------------------------------- two page spreads
# Ayushi's page 1 is a photo of an open notebook (fold near x 1578 of 3125). Rows are
# walked in height order, which alternates between the facing pages: the walk from
# "Ans-6- 2" (left page) ended on "well as from N side (isocynide)" (Q13, right page),
# and Q6's mark was drawn there. Q9's ended on the right page's "Reagent: Conc. HCl".
AYUSHI_P1 = _page(1, 3125, 4167, [
    L('P1L12', 'Section A', (638, 1229, 847, 1313)),
    L('P1L31', 'Ans-10s is Enantienters:- Non-superimposable mirror inage', (1593, 1288, 2731, 1400)),
    L('P1L13', 'Q-1- C) 3-chloroheptane', (81, 1350, 640, 1438)),
    L('P1L32', 'stereoisomers.', (2017, 1368, 2280, 1424)),
    L('P1L33', 'ii) Racomic mixture:- An equimolar mixture of two enantiomers', (1674, 1386, 3027, 1521)),
    L('P1L14', 'Q-2 → c) Both \\( A \\) and \\( R \\) are true but \\( R \\) is carrect explaination of \\( A \\).', (78, 1419, 1292, 1510)),
    L('P1L34', 'that are optically inactive.', (1962, 1462, 2553, 1563)),
    L('P1L15', 'Q-3→ c) 9sopropyl chloride', (74, 1483, 642, 1570)),
    L('P1L16', 'Q-4→ c) Prodominantly But-2-one', (87, 1544, 788, 1640)),
    L('P1L35', 'Ans-11) Although Cl withdraws electron by the - I effect,', (1595, 1557, 2970, 1699)),
    L('P1L17', 'Q-5→ B) 6-Bremo-2-chlorocyclahux-1-ene', (97, 1608, 939, 1697)),
    L('P1L36', 'its lone pair donates electrons by the \\( +R \\)', (1777, 1627, 2906, 1737)),
    L('P1L37', 'effect, increasing electron density at ortho', (1786, 1680, 2906, 1813)),
    L('P1L18', 'Section-B', (572, 1726, 788, 1805)),
    L('P1L38', 'and para positions.', (1833, 1762, 2324, 1864)),
    L('P1L19', 'Ans-7: thionyl chloride \\( \\left(\\mathrm{SOCl}_{2}\\right) \\) is considered best for', (23, 1835, 1388, 1951)),
    L('P1L20', 'preparing chlarocthone from ethonol because', (231, 1904, 1421, 2013)),
    L('P1L39', 'Anstine to Monobromoberzene', (1746, 1917, 2377, 2000)),
    L('P1L21', 'the gases formed as product evolve leaving pure habalkon.', (231, 1960, 1551, 2082)),
    L('P1D40', '[detected diagram]', (1744, 1976, 2445, 2146), 'diagram'),
    L('P1L22', 'with high yield\n\\( \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{OH}+\\mathrm{SOCl}_{2} \\longrightarrow \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{Cl}+\\underset{(\\mathrm{gas})}{\\mathrm{SO}_{2} \\uparrow+\\mathrm{HCl} \\uparrow} \\)', (265, 2044, 1422, 2207)),
    L('P1L41', 'Antitive', (1743, 2101, 1858, 2150)),
    L('P1L42', 'Ans-13- Ambident nucleophites:- Nucleaphiles that con attack', (1597, 2148, 2942, 2286)),
    L('P1L43', 'from two different atoms.', (2104, 2220, 2712, 2322)),
    L('P1L44', 'Ex:- \\( C N \\) cm cm attack from C side (Nitrile) as', (1657, 2271, 2917, 2400)),
    L('P1L23', 'Ans-6- 2', (27, 2305, 258, 2371)),
    L('P1L45', 'well as from \\( N \\) side (isocynide)', (1782, 2354, 2476, 2457)),
    L('P1L24', 'Ans-8 → \\( \\mathrm{CH}_{3}-\\mathrm{CH}_{2}-\\mathrm{CH}^{-} \\mathrm{Cl} \\) is more reactive towards SN reaction', (13, 2373, 1534, 2502)),
    L('P1L46', 'Ans-14-6) \\( \\mathrm{CH}_{3} \\mathrm{Ce} \\) has a higher dipole moment because its', (1608, 2445, 2998, 2593)),
    L('P1L25', 'as compared to \\( \\mathrm{CH}_{3}-\\mathrm{CH}^{-}-\\mathrm{CH}_{2}-\\mathrm{Cl} \\) because the priority order', (184, 2449, 1472, 2584)),
    L('P1L26', 'of reactivity \\( \\mathrm{CH}_{3} \\) in \\( \\mathrm{SN}_{1} \\) reaction is', (201, 2512, 1404, 2659)),
    L('P1L47', 'bond polarity and bond length produce a larger', (1765, 2521, 2917, 2644)),
    L('P1L27', '\\( 3^{\\circ}>2^{\\circ}>1^{\\circ} \\) respectively.', (273, 2602, 786, 2709)),
    L('P1L48', 'net dipole than \\( \\mathrm{CH}_{3} \\mathrm{~F} \\).', (1782, 2606, 2322, 2693)),
    L('P1L49', 'a) Alechal: tert-Butyl cilcohol (2 methylpropan-2-ol)', (1691, 2697, 2888, 2841)),
    L('P1L28', 'Ans-9→i) \\( \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{OH} \\xrightarrow{\\mathrm{SOCl}_{2}} \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{Cl} \\xrightarrow{\\mathrm{KCN}} \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{CN}+\\mathrm{KCl} \\)', (9, 2699, 1337, 2854)),
    L('P1L50', 'Reagent: Conc. HCl anhydrous \\( 2 \\mathrm{nCl}_{2}\\left(\\mathrm{OrSOCl}_{2}\\right) \\)', (1756, 2758, 2993, 2900)),
    L('P1L29', 'ii) \\( 2\\left(\\mathrm{CH}_{3}\\right)_{2} \\mathrm{CHRr}+2 \\mathrm{Na} \\) का ether \\( \\mathrm{C}_{5} \\mathrm{H}_{12}+2 \\mathrm{NaBr} \\)', (129, 2830, 1271, 2987)),
])


class PointStaysOnItsPageOfTheSpread(unittest.TestCase):
    def _layout(self):
        doc = _doc([(1500, 2000)], [])
        report = GradeReport(total_score=0, max_total=0, overall_remarks="", questions=[],
                             evidence_contract=True, answer_regions={})
        return teacher_layout.TeacherLayout(doc, report, [AYUSHI_P1])

    def test_the_fold_is_found(self):
        layout = self._layout()
        self.assertIn(1, layout.folds)
        self.assertTrue(700 < layout.folds[1] < 800, layout.folds)

    def test_point_end_never_crosses_the_fold(self):
        layout = self._layout()
        lines = layout.lines
        stops = {"P1L24", "P1L42", "P1L44", "P1L46", "P1L28", "P1L49"}
        self.assertEqual(layout.point_end(lines["P1L23"], stops).lid, "P1L23")
        end = layout.point_end(lines["P1L28"], stops - {"P1L28"})
        self.assertLess(end.rect.x1, layout.folds[1], end.lid)
        # A point that really continues on its own page still runs on.
        self.assertEqual(layout.point_end(lines["P1L44"], stops - {"P1L44"}).lid, "P1L45")

    def test_rows_above_stay_on_the_same_page(self):
        layout = self._layout()
        lines = layout.lines
        above = layout.rows_above_in_point(lines["P1L45"], lines["P1L45"], set())
        self.assertTrue(all(o.rect.x0 > layout.folds[1] for o in above), [o.lid for o in above])


# ---------------------------------------------------------------- loose OCR boxes
# Jaskeerat's "Q1. (B) played most foully..." box is 138 px tall and runs over the top
# of "Quo (C) the witches...". Its "last row" was Q2's, so Q1's tick sat on the Q2
# line and Q2's went to the margin. The reviewer drew Q1 on rows 290 to 370.
JASKEERAT_P1 = _page(1, 1235, 1650, [
    L('P1L3', 'Section A -', (130, 228, 285, 287)),
    L('P1L4', 'Q1. (B) played most foully to obtain theorown.', (61, 268, 1065, 406)),
    L('P1L5', "Quo (C) the witches have prophesised that Banquo's descendants", (53, 365, 1188, 495)),
    L('P1L6', 'will be kings.', (170, 461, 366, 519)),
    L('P1L7', "Q3. (B) Macbeth's quilt and psychological instability.", (66, 518, 1063, 636)),
    L('P1L8', 'Q4. (C) have dealt with Macketh without consulting her.', (57, 624, 1083, 718)),
    L('P1L9', '95. (B) Macduff', (58, 742, 312, 808)),
    L('P1L10', 'Q0. (c) despair and rage.', (58, 834, 460, 904)),
    L('P1L11', 'Q7. (B) If It is unaware, in any herman sense, that', (49, 920, 1071, 1002)),
    L('P1L12', 'its inhabitants are dead.', (118, 978, 624, 1030)),
])


class LooseBoxKeepsItsOwnRow(unittest.TestCase):
    def test_jaskeerat_q1_row_is_above_q2(self):
        ink = {"P1L4": (300, 365), "P1L5": (405, 470), "P1L6": (470, 510)}
        doc = _doc([(592.6, 792.0)], [JASKEERAT_P1], ink)
        report = GradeReport(total_score=0, max_total=0, overall_remarks="", questions=[],
                             evidence_contract=True, answer_regions={})
        layout = teacher_layout.TeacherLayout(doc, report, [JASKEERAT_P1])
        layout.pitch = 24.5      # the booklet's row pitch, measured over all ten pages
        q1 = layout.mark_row(layout.lines["P1L4"], None)
        q2_top = layout.lines["P1L5"].rect.y0
        self.assertLessEqual(q1.y1, q2_top + 1, (q1, q2_top))
        self.assertTrue(300 * .48 - 2 <= (q1.y0 + q1.y1) / 2 <= 365 * .48 + 2, q1)
        # A row whose box overlaps nothing below keeps its geometry.
        q4 = layout.mark_row(layout.lines["P1L9"], None)
        self.assertTrue(742 * .48 <= (q4.y0 + q4.y1) / 2 <= 808 * .48, q4)


# ---------------------------------------------------------------- struck-out work
class StruckOutWorkRules(unittest.TestCase):
    """No OCR signal exists for these strikes: Mathpix returns no strike field or markup,
    and the line detector cannot see a faint wavy line (Vantika Q11(x) first attempt),
    a diagonal slash across a block (Akshay Q10), a struck drawn structure (Saptarshi
    Q19(iv)) or one struck word beside the live answer (Rishita Q30 "zero (0) 1 (Ans)").
    So both examiners are told what such strikes look like and which attempt counts."""

    def test_grader_is_told_the_final_unstruck_attempt_is_the_answer(self):
        from grader import build_system_prompt
        for subject in ("Mathematics", "English", "Chemistry"):
            prompt = build_system_prompt(subject, "Class 12", "school")
            self.assertIn("the FINAL unstruck one is the answer", prompt)
            self.assertIn("diagonal", prompt)
            self.assertIn("zero (0) 1 (Ans)", prompt)
            self.assertIn("even when it is correct", prompt)

    def test_inventory_locates_the_rewritten_attempt(self):
        # Vantika's Q11(x) span began on the struck "x] it wants us to focus on the".
        from answer_inventory import INVENTORY_SYSTEM_PROMPT
        self.assertIn("first line of the final, unstruck attempt", INVENTORY_SYSTEM_PROMPT)

    def test_moderator_removes_marks_given_for_struck_work(self):
        from grader import AUDIT_SYSTEM_PROMPT
        self.assertIn("remove marks the first examiner gave", AUDIT_SYSTEM_PROMPT)
        self.assertIn("final unstruck attempt", AUDIT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
