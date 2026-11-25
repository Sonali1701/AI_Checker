# -*- coding: utf-8 -*-
"""Build 79, group "missed": real answers scored 0 as not attempted.

Parishram ISC Chemistry (20 Sept 2026), re-marked by faculty. The OCR lines below are
copied from the stored reports, in the order Mathpix returned them.

* A reaction scheme drawn a few pixels above its "(9)" label (Drisha Q9) was handed
  to Q8, and the two pages of a notebook photo (Akshra) were interleaved row by row:
  synthesize_diagram_regions re-sorted every page by height.
* Q12 drawn as a scheme (Aasmeen, Aditya, Saptarshi, Ayushi) came back "Not
  attempted" although the visual inventory or the OCR label showed writing; nothing
  read it again before the second examiner, who may not credit an unattempted row.
* Aasmeen's Q16 "(ii)" read as "(11)" became Q11's heading, and the tool marked Q11
  on another answer's writing although the inventory placed Q11 elsewhere.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader  # noqa: E402
import pipeline  # noqa: E402
from answer_inventory import (AnswerInventory, InventoryItem, InventoryResult,  # noqa: E402
                              merge_inventory, take_answer_inventory, verify_inventory)
from answer_map import build_answer_map  # noqa: E402
from budgeting import BudgetPolicy  # noqa: E402
from exam_package import build_exam_package  # noqa: E402
from grader import (Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark,  # noqa: E402
                    _answered_question_numbers)
from mathpix import (LineBox, PageOCR, build_transcript, order_spread_lines,  # noqa: E402
                     synthesize_diagram_regions)
from strict_grading import StrictGradeError  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def page(number, width, height, rows):
    return PageOCR(page=number, image_width=width, image_height=height, lines=[
        LineBox(line_id=lid, text=text, bbox=tuple(float(v) for v in box), page=number,
                confidence=conf, kind=kind)
        for lid, box, text, conf, kind in rows])


def chemistry_package():
    """The paper's 24 questions: Q1-Q5 1 mark, Q6-Q15 2 marks, Q16-Q24 5 marks."""
    marks = [1] * 5 + [2] * 10 + [5] * 9
    scheme = MarksScheme(total=sum(marks), items=[MarksItem(qid=f"Q{i}", max=m)
                                                  for i, m in enumerate(marks, start=1)])
    pkg = build_exam_package(exam_type="school", student_class="12", subject="Chemistry",
                             level="12", paper="", attempt="", applicable_law_date=None,
                             total_marks=scheme.total, question_paper=b"paper",
                             answer_key=b"key", key_source="upload", marks_scheme=scheme,
                             scheme_source="text")
    return pkg, scheme


# Drisha, page 1 (1240 x 1755): Q8's answer, then "(9)" with its two reactions read as one
# diagram block whose top sits 42 px ABOVE the label's.
DRISHA_P1 = [
    ('P1L20', [145, 813, 743, 911], 'gaseous form. \\( \\mathrm{SO}_{2} \\) and HCl are gas.', 0.99, 'text'),
    ('P1L21', [100, 911, 485, 1022], '(8) \\( \\mathrm{CH}_{3}-\\mathrm{CH}_{2}-\\underset{\\mathrm{CH}}{\\mathrm{CH}_{2}}-\\mathrm{Cl} \\)', 0.34, 'text'),
    ('P1L22', [511, 885, 927, 968], 'is more reactive than', 1.0, 'text'),
    ('P1D23', [146, 1022, 453, 1135], '[detected diagram]', None, 'diagram'),
    ('P1L24', [469, 978, 878, 1074], 'towards \\( S_{N_{1}} \\) reaction', 1.0, 'text'),
    ('P1L25', [151, 1088, 847, 1193], 'becouse \\( \\mathrm{SN}_{1} \\) is more stable for \\( 3^{\\circ}>2^{\\circ}>1^{\\circ} \\)', 0.55, 'text'),
    ('P1L26', [119, 1197, 179, 1260], '(9)', 0.96, 'text'),
    ('P1D27', [160, 1155, 1132, 1363], '[detected diagram]', None, 'diagram'),
    ('P1L28', [119, 1366, 218, 1436], '(10) i)', 1.0, 'text'),
    ('P1L29', [170, 1375, 1148, 1482], 'ii) R muchice is the mixture. Of when both the mirror', 0.05, 'text'),
    ('P1L30', [693, 1414, 1176, 1481], 'is is their and optically in active', 0.36, 'text'),
    ('P1L31', [136, 1474, 1140, 1567], '(11) Because in ortho and para pasition of p benzene their', 0.53, 'text'),
]

# Akshra, page 1 (1275 x 972): a photo of two facing pages. Left page Q1 to Q10(i),
# right page Q10(ii) ("equal d and l, 50% each"), Q11 and the Q12 heading.
AKSHRA_P1 = [
    ('P1L7', [75, 65, 135, 102], '20/9/26', 1.0, 'text'),
    ('P1L8', [224, 94, 316, 128], 'Aleminty', 0.35, 'text'),
    ('P1L9', [150, 141, 251, 174], 'Sution A', 0.77, 'text'),
    ('P1L10', [69, 191, 347, 229], 'Q. 1 (C) 3-elebrobaptane', 0.52, 'text'),
    ('P1L11', [72, 245, 185, 279], 'Q. 2 (C)', 1.0, 'text'),
    ('P1L12', [73, 297, 374, 338], 'Q. 3 (C) Seopropyl veloride', 0.46, 'text'),
    ('P1L13', [72, 346, 400, 389], 'Q. 4 (A) Iredominantly but-2ye', 0.35, 'text'),
    ('P1L14', [66, 399, 464, 436], 'Q. 5 (C) 3-Brown-1-Meroblex-1-eve', 0.15, 'text'),
    ('P1L15', [179, 457, 268, 485], 'Section B', 0.99, 'text'),
    ('P1L16', [46, 504, 527, 548], 'Q. 6 Three Atrogenic rentes are possible', 0.47, 'text'),
    ('P1L17', [56, 557, 615, 605], 'Q. 7 Brawte by this nothod troo gases so, and', 0.13, 'text'),
    ('P1L18', [129, 588, 493, 627], 'HCl are foreed which chapes', 0.27, 'text'),
    ('P1L19', [56, 642, 368, 677], 'Q. \\( 8 \\mathrm{CH}_{3}-\\mathrm{CH}-\\mathrm{CH}_{2}-\\mathrm{Cl} \\) is', 1.0, 'text'),
    ('P1L20', [39, 687, 550, 734], 'Q.9. (i) \\( \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{OH} \\xrightarrow{\\mathrm{SOCl}_{2}} \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{Cl}+\\mathrm{SO}_{2} \\uparrow+\\mathrm{HCl} \\)', 1.0, 'text'),
    ('P1L21', [121, 742, 484, 789], '\\[\n\\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{Cl} \\xrightarrow{\\mathrm{KNN}} \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{CN}+\\mathrm{KM}\n\\]', 0.76, 'math'),
    ('P1L22', [57, 790, 612, 843], '(ii) \\( 2\\left(\\mathrm{CH}_{3}\\right)_{2} \\mathrm{CHBr}+\\mathrm{Na} \\xrightarrow[\\text { ether }]{\\text { dry }}\\left(\\mathrm{CH}_{3}\\right)_{2} \\mathrm{CH}-\\left(\\mathrm{CH}_{3}\\right)_{2} \\mathrm{CH}+\\mathrm{NaBl} \\)', 0.79, 'text'),
    ('P1L23', [23, 863, 240, 902], 'Q.1D) (i) Enautionars', 0.66, 'text'),
    ('P1L24', [121, 889, 628, 924], 'Unite nobenles which are non-xuperixiporable', 0.02, 'text'),
    ('P1L25', [116, 919, 585, 959], 'misror ineges of each folurs are celled', 0.08, 'text'),
    ('P1L26', [136, 946, 259, 967], 'mantionesel.', 0.55, 'text'),
    ('P1L28', [667, 117, 691, 141], '(ii)', 1.0, 'text'),
    ('P1L29', [713, 98, 1194, 127], 'A suinture in whim there is equal \\( d>l \\)', 0.53, 'text'),
    ('P1L30', [718, 123, 1189, 154], 'ellorater \\( 50 \\% \\) eace is a racemic visiture.', 0.2, 'text'),
    ('P1L31', [660, 187, 701, 218], 'Q. 11)', 1.0, 'text'),
    ('P1L32', [718, 169, 1180, 197], 'Hin is because the reconarce and the', 0.15, 'text'),
    ('P1L33', [712, 194, 1184, 231], 'inductive effect voiviteralance esses steel.', 0.11, 'text'),
    ('P1L34', [664, 260, 702, 290], '8.12)', 0.9, 'text'),
]

# Aasmeen, pages 2 and 3. Q11's heading "Q11)" is read "(211)" and left blank; Q12 is a
# scheme read as one diagram under a lone "Q"; Q16's part "(ii)" is read "(11)".
AASMEEN_P2 = [
    ('P2L10', [128, 144, 993, 243], 'are non superimence companeds which', 0.25, 'text'),
    ('P2L11', [136, 188, 991, 320], 'each other', 0.43, 'text'),
    ('P2L12', [65, 237, 1029, 383], '(ii) 1:1 equimoler solution of dextro and lawo', 0.27, 'text'),
    ('P2L13', [154, 284, 1028, 381], 'form of enantiones on of dextro and laws', 0.43, 'text'),
    ('P2L14', [241, 343, 394, 412], 'minture', 1.0, 'text'),
    ('P2L15', [31, 420, 124, 506], '(211)', 0.99, 'text'),
    ('P2L16', [13, 568, 92, 644], 'Q', 0.39, 'text'),
    ('P2D17', [116, 565, 834, 1163], '[detected diagram]', None, 'diagram'),
    ('P2L18', [28, 1168, 991, 1248], 'Q13) Ambidentatrac Nucleophies are Nucleophes', 0.47, 'text'),
    ('P2L19', [156, 1227, 986, 1282], 'which have two or more donor sites', 0.86, 'text'),
    ('P2L20', [137, 1273, 988, 1351], 'but they wase only one donor site', 0.34, 'text'),
    ('P2L21', [176, 1313, 516, 1391], 'during reaction', 1.0, 'text'),
    ('P2L22', [222, 1397, 777, 1470], '\\[\n\\mathrm{CN}, \\mathrm{NC}^{-}, \\mathrm{NO}_{2}, \\mathrm{ONO}^{-}\n\\]', 1.0, 'math'),
    ('P2L23', [38, 1484, 802, 1557], 'Q 14) a) Alechol → 2-methylpropan - 2-ol', 0.14, 'text'),
    ('P2L24', [205, 1524, 966, 1616], 'Reggent \\( \\rightarrow \\) sucas Reagent, ( \\( \\mathrm{HCl} / \\mathrm{ZnCl} \\) )', 0.17, 'text'),
]
AASMEEN_P3 = [
    ('P3L7', [56, 121, 913, 236], 'b) Choromethane has hegher depose', 0.19, 'text'),
    ('P3L8', [147, 181, 998, 285], 'mement than flueronhethane because', 0.56, 'text'),
    ('P3L9', [81, 222, 1064, 331], 'it has higher bond legngth so qxd merages', 0.25, 'text'),
    ('P3L10', [16, 317, 945, 424], 'Q15 In case of aqueous K or Nucleose helic', 0.11, 'text'),
    ('P3L11', [138, 367, 948, 467], 'Substitution reactions takes place but', 0.28, 'text'),
    ('P3L12', [147, 414, 926, 498], 'in case of alcoholic KOH elemnation', 0.21, 'text'),
    ('P3L13', [83, 459, 977, 574], 'reaction takes peace ( \\( \\beta \\)-dehydrohalogination', 0.96, 'text'),
    ('P3L14', [133, 508, 324, 570], 'reaction)', 1.0, 'text'),
    ('P3L15', [9, 596, 960, 692], 'Q16 ajob and length in chlorobenzene is shorter', 0.42, 'text'),
    ('P3L16', [152, 645, 975, 749], 'than alky havels because down the group', 0.76, 'text'),
    ('P3L17', [61, 745, 261, 795], 'encreases', 0.89, 'text'),
    ('P3D18', [365, 787, 853, 973], '\\begin{tabular}[t]{|l|l|}\n\\hline F & \\\\\n\\hline Cl & Bond Length ↑ \\\\\n\\hline Br & \\multirow{2}{*}{Electronagativity ↓} \\\\\n\\hline T v & \\\\\n\\hline\n\\end{tabular}', 0.52, 'table'),
    ('P3L25', [37, 1010, 996, 1092], '(11) Reaction are accomponed by racemsation', 0.66, 'text'),
    ('P3L26', [177, 1066, 995, 1136], 'un optically act ve alky nalide through', 0.17, 'text'),
    ('P3L27', [153, 1107, 897, 1155], 'SN, merhansm because it is two', 0.91, 'text'),
    ('P3L28', [173, 1147, 838, 1207], 'step reaction and unemolecular', 0.68, 'text'),
    ('P3L29', [40, 1227, 1001, 1310], 'b) Checorofform Carbon tetrachiorise comas more', 0.04, 'text'),
    ('P3L30', [86, 1330, 247, 1373], 'more ce', 0.25, 'text'),
    ('P3L31', [27, 1409, 111, 1466], '(a17)', 0.87, 'text'),
    ('P3D32', [44, 1435, 1033, 1571], '[detected diagram]', None, 'diagram'),
]

# Saptarshi, page 2 (1275 x 857, left page of a spread): "Q12" with the whole
# aniline -> bromobenzene scheme read as one diagram block.
SAPTARSHI_P2 = [
    ('P2L7', [35, 63, 657, 181], 'is resonance effect \\& it has at circhite in tect finction', 0.0, 'text'),
    ('P2L8', [55, 105, 652, 159], 'got a tendency in - I effect (Indulti', 0.2, 'text'),
    ('P2L9', [59, 128, 180, 176], 'effect NH2', 0.41, 'text'),
    ('P2L10', [28, 191, 73, 218], 'Q12', 1.0, 'figure_label'),
    ('P2D11', [68, 159, 653, 454], '[detected diagram]', None, 'diagram'),
    ('P2L12', [37, 443, 547, 477], 'Q13. are', 0.99, 'text'),
    ('P2L13', [79, 475, 633, 515], 'that possess', 0.86, 'text'),
    ('P2L14', [61, 501, 630, 554], 'sites with unchared pair of electrons.', 0.85, 'text'),
]

# Aditya, page 2 (1580 x 2084): Q12, Q13, Q14 and Q17 merged into ONE table block.
ADITYA_P2 = [
    ('P2L6', [90, 83, 1179, 132], '- Puye', 0.47, 'text'),
    ('P2L7', [243, 178, 474, 268], 'negative.', 1.0, 'text'),
    ('P2D8', [88, 254, 1394, 1606], '\\begin{tabular}[t]{|l|l|}\n\\hline 012 & \\\\\n\\hline 013 & atoms having more than one donar site present: \\( E x=C N, N C \\) \\\\\n\\hline O14 & Groug attly HCl at room temp. \\\\\n\\hline ii & \\( \\mathrm{CH}_{3} \\mathrm{Cl} \\rightarrow \\mathrm{CH}_{3} \\mathrm{~F} \\) \\\\\n\\hline\n\\end{tabular}', 0.0, 'table'),
    ('P2D11', [318, 267, 688, 468], '[detected diagram]', None, 'diagram'),
    ('P2L15', [259, 620, 1317, 764], 'atoms having more than one donar site', 0.73, 'text'),
    ('P2L16', [246, 683, 983, 800], 'present: \\( E x=C N, N C \\)', 0.92, 'text'),
]


# Saptarshi, page 1 (1275 x 885): a photo of two facing pages. Mathpix returned the 27
# lines of the LEFT page only; the right page (the rest of Q8, then Q9 to Q11) had none.
SAPTARSHI_P1 = [
    ('P1L1', [40, 136, 261, 178], 'Q1 (A) But ane', 1.0, 'text'),
    ('P1L2', [438, 2, 652, 78], '\\( \\mathrm{CH}_{3}=\\mathrm{CH}_{2}-\\mathrm{CH}_{2}-\\mathrm{CH}_{3}+\\mathrm{H}_{3} \\)\nTest', 0.48, 'text'),
    ('P1L3', [23, 24, 329, 85], '\\( \\mathrm{CH}_{3}-\\mathrm{CH}_{2}-\\mathrm{CH}_{3}^{2} \\mathrm{H}-\\stackrel{\\mathrm{C}}{\\mathrm{CH}}-\\mathrm{BO} \\) and Su', 0.01, 'text'),
    ('P1L4', [523, 58, 580, 89], 'Datel', 0.63, 'text'),
    ('P1L5', [264, 86, 462, 119], 'SECTION-A', 1.0, 'text'),
    ('P1D6', [522, 81, 646, 167], '[detected diagram]', None, 'diagram'),
    ('P1L7', [42, 188, 487, 235], 'Q2 (B) A is talse but R is true', 0.53, 'text'),
    ('P1D8', [511, 192, 643, 271], '[detected diagram]', None, 'diagram'),
    ('P1L9', [38, 243, 425, 295], 'Q3 (C) Jso propyl chloride', 1.0, 'text'),
    ('P1L10', [35, 297, 480, 352], 'Q4 (C) Prodominantly But - 2-ene', 0.99, 'text'),
    ('P1L11', [37, 350, 570, 413], 'Q5(C) 3-Bromo-1-Chlorocyclo héx-1-ene \\( { }^{\\text {a }} \\)', 0.43, 'text'),
    ('P1L12', [239, 417, 471, 457], '\\[\nS E C T I O N-B\n\\]', 0.79, 'math'),
    ('P1L13', [36, 463, 181, 502], '96. 6.', 1.0, 'text'),
    ('P1L14', [37, 517, 639, 578], 'QT \\( \\mathrm{C}_{2} \\mathrm{H}_{5} \\mathrm{OH}+\\mathrm{SOCl}_{2} \\xrightarrow{\\text { Pridine }} \\mathrm{CH}_{3} \\mathrm{CH}_{2} \\mathrm{Cl}_{2} \\mathrm{SO}_{4}+ \\)', 0.28, 'text'),
    ('P1L15', [45, 569, 633, 614], 'Thionyl chloride is comsident th HAT', 0.49, 'text'),
    ('P1L16', [47, 596, 620, 638], 'method for pree is consi dered the best', 0.54, 'text'),
    ('P1L17', [42, 620, 639, 687], 'ethanol because it is a strong oxidising agent', 0.99, 'text'),
    ('P1L18', [44, 655, 632, 712], 'and two gases are also liberated', 0.66, 'text'),
    ('P1L19', [51, 681, 628, 743], 'along with chloroethane and the two gases', 0.49, 'text'),
    ('P1L20', [53, 708, 468, 761], 'exase in the form of vapours.', 0.97, 'text'),
    ('P1L21', [36, 758, 80, 793], 'Q8', 1.0, 'text'),
    ('P1L22', [91, 748, 403, 851], '\\[\n\\begin{array}{l}\n\\mathrm{CH} 3-\\mathrm{Cn}-\\mathrm{CH}_{2}-\\mathrm{Cl} \\\\\n\\mathrm{CH}_{3}\n\\end{array}\n\\]', 0.25, 'math'),
    ('P1L23', [85, 835, 422, 884], '1- chlow-2-methyl provers', 0.34, 'text'),
    ('P1D24', [476, 764, 641, 872], '[detected diagram]', None, 'diagram'),
    ('P1L25', [476, 765, 646, 841], '\\( \\mathrm{CH}_{3}-\\stackrel{2}{\\mathrm{CH}_{2}}-\\stackrel{3}{-} \\mathrm{CH}-\\mathrm{Cl} \\)', 0.27, 'text'),
    ('P1L26', [475, 857, 646, 884], '3-chlorosurare', 0.27, 'text'),
    ('P1L27', [477, 859, 645, 885], '3-chlorosulare', 0.2, 'text'),
]
# The right page as the faculty reviewer transcribed it, in the CROPPED page's frame
# (x - 652): what a second OCR read of that page returns.
SAPTARSHI_P1_RIGHT = [
    ('P1L1', [8, 85, 618, 150], '3-Chlorobutane is more reactive towards SN1 reaction because SN1 reaction', 0.8, 'text'),
    ('P1L2', [8, 150, 523, 195], 'follows the order of 3 > 2 > 1.', 0.8, 'text'),
    ('P1L3', [0, 195, 618, 340], 'Q9 (i) CH3CH2OH SOCl2 -> CH3CH2Cl KCN -> CH3CH2C=N.', 0.8, 'text'),
    ('P1L4', [8, 540, 618, 620], 'Q10. (i) Enantiomers - These are pair of stereoisomers that are non superimposable mirror images.', 0.8, 'text'),
    ('P1L5', [108, 745, 618, 805], 'Q11. Although chlorine is an electron withdrawing group, yet it is', 0.8, 'text'),
]


def located(answer_map, qid):
    return next(item for item in answer_map.locations if item.qid == qid)


class ReadingOrderIsKept(unittest.TestCase):
    """mathpix.synthesize_diagram_regions must not re-sort a page by height."""

    def test_a_scheme_drawn_above_its_label_stays_with_its_question(self):
        pkg, _ = chemistry_package()
        drisha = synthesize_diagram_regions(page(1, 1240, 1755, DRISHA_P1))
        amap = build_answer_map(pkg, [drisha])
        self.assertIn("P1D27", located(amap, "Q9").line_ids)
        self.assertNotIn("P1D27", located(amap, "Q8").line_ids)

    def test_two_facing_pages_keep_the_left_page_first(self):
        akshra = page(1, 1275, 972, AKSHRA_P1)
        akshra.lines = order_spread_lines(akshra.lines, 1275, 1)
        synthesize_diagram_regions(akshra)
        order = [line.line_id for line in akshra.lines]
        self.assertLess(order.index("P1L26"), order.index("P1L28"))
        self.assertEqual(order[order.index("P1L26") + 1:order.index("P1L26") + 4],
                         ["P1L28", "P1L29", "P1L30"])
        # Q11's two lines on the right page are under its own heading again.
        self.assertGreaterEqual(_answered_question_numbers([akshra]).get(11, 0), 2)
        transcript = build_transcript([akshra])
        self.assertLess(transcript.index("mantionesel."), transcript.index("[P1L28] (ii)"))
        self.assertLess(transcript.index("[P1L30]"), transcript.index("[P1L31] Q. 11)"))

    def test_a_new_region_goes_in_before_the_line_under_its_gap(self):
        left = [('P1L1', [40, 100, 500, 140], 'Q1 first line', 0.9, 'text'),
                ('P1L2', [40, 600, 500, 640], 'Q2 after a drawing', 0.9, 'text')]
        right = [('P1L3', [700, 120, 1150, 160], 'Q3 on the facing page', 0.9, 'text'),
                 ('P1L4', [700, 170, 1150, 210], 'still Q3', 0.9, 'text')]
        spread = synthesize_diagram_regions(page(1, 1200, 800, left + right))
        ids = [line.line_id for line in spread.lines]
        synthetic = [line.line_id for line in spread.lines if line.kind == "synthetic_region"]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(ids, ["P1L1", synthetic[0], "P1L2", "P1L3", "P1L4"])


def blank_png(width, height):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


class UnreadFacingPage(unittest.TestCase):
    """pipeline._read_unread_facing_pages: the OCR read one page of a two-page photo."""

    def cfg(self):
        from types import SimpleNamespace
        return SimpleNamespace(google_token="t", task_id="k")

    def test_saptarshi_right_page_is_read_and_joins_after_the_left_page(self):
        from PIL import Image
        from io import BytesIO
        pages = [page(1, 1275, 885, SAPTARSHI_P1)]
        crops = []

        def read(png, number, **kwargs):
            crops.append(Image.open(BytesIO(png)).size)
            return page(number, *crops[-1], SAPTARSHI_P1_RIGHT)

        with patch.object(pipeline, "ocr_page", side_effect=read):
            pipeline._read_unread_facing_pages([blank_png(1275, 885)], pages, cfg=self.cfg(),
                                               filename="s.pdf")
        self.assertEqual(crops, [(1275 - 652, 885)])
        ids = [line.line_id for line in pages[0].lines]
        self.assertEqual(ids[:27], [row[0] for row in SAPTARSHI_P1])
        self.assertEqual(ids[27:], ["P1L28", "P1L29", "P1L30", "P1L31", "P1L32"])
        q9 = next(line for line in pages[0].lines if line.line_id == "P1L30")
        self.assertEqual(q9.bbox[0], 652)
        # The rest of Q8 now sits under Q8, and Q9 to Q11 have headings of their own.
        pkg, _ = chemistry_package()
        amap = build_answer_map(pkg, pages)
        self.assertIn("P1L28", located(amap, "Q8").line_ids)
        for qid, line in (("Q9", "P1L30"), ("Q10", "P1L31"), ("Q11", "P1L32")):
            self.assertEqual(located(amap, qid).label_line_ids, (line,))

    def test_no_extra_read_when_both_pages_or_a_single_page_were_read(self):
        akshra = [page(1, 1275, 972, AKSHRA_P1)]
        drisha = [page(1, 1240, 1755, DRISHA_P1)]
        with patch.object(pipeline, "ocr_page") as read:
            pipeline._read_unread_facing_pages([blank_png(1275, 972)], akshra, cfg=self.cfg(),
                                               filename="a.pdf")
            pipeline._read_unread_facing_pages([blank_png(1240, 1755)], drisha, cfg=self.cfg(),
                                               filename="d.pdf")
        read.assert_not_called()

    def test_a_failed_read_changes_nothing(self):
        pages = [page(1, 1275, 885, SAPTARSHI_P1)]
        with patch.object(pipeline, "ocr_page", side_effect=RuntimeError("proxy 502")):
            pipeline._read_unread_facing_pages([blank_png(1275, 885)], pages, cfg=self.cfg(),
                                               filename="s.pdf")
        self.assertEqual([line.line_id for line in pages[0].lines],
                         [row[0] for row in SAPTARSHI_P1])

    def test_it_runs_inside_the_ocr_rereads(self):
        src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
        body = src[src.index("def _reread_sideways_pages("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("_read_unread_facing_pages(", body)


class WrittenBlankIsReadAgain(unittest.TestCase):
    """pipeline._recheck_written_blanks: a 'not attempted' on a written answer."""

    def setUp(self):
        self.pkg, self.scheme = chemistry_package()
        self.cfg = pipeline.ProviderConfig(
            provider="claude", model="claude-haiku-4-5", google_token="test",
            task_id="b79-missed", exam_type="school", grading_contract="exam_package_v2",
            exam_package=self.pkg)
        self.cfg.enforce_budget = False

    def common(self, ocr_pages):
        return dict(student_pngs=[b"page"] * 3, marks_scheme=self.scheme, usage_out={},
                    model=self.cfg.model, task_id=self.cfg.task_id, google_token="test",
                    question_paper_pngs=[], answer_key_pngs=[], answer_key_text="Official key",
                    qp_text="Official paper", ocr_transcript=build_transcript(ocr_pages),
                    subject="Chemistry", student_class="12", filename="copy.pdf")

    def recheck(self, report, ocr_pages, inventory):
        return pipeline._recheck_written_blanks(
            report, inventory=inventory, ocr_pages=ocr_pages, cfg=self.cfg,
            common=self.common(ocr_pages),
            policy=BudgetPolicy.for_copy(total_marks=70, exam_type="school"),
            hard_cap=math.inf, usd_to_inr=85, use_mathpix=True, stage=lambda *a: None)

    @staticmethod
    def blank(qid, page_number, anchor):
        # Aasmeen's stored Q12 row.
        return QuestionMark(qid=qid, score=0, max_score=2, page=page_number, y_fraction=0.5,
                            anchor_line_id=anchor, remark="Not attempted.", attempted=False,
                            mark_style="single", criteria=[Criterion(
                                description="Monobromobenzene from aniline", awarded=0, max=2,
                                met=False, evidence_kind="missing")])

    @staticmethod
    def answered(qid, page_number, line):
        return QuestionMark(qid=qid, score=2, max_score=2, page=page_number, y_fraction=0.5,
                            anchor_line_id=line, remark="", attempted=True, mark_style="single",
                            criteria=[Criterion(description="Answer", awarded=2, max=2,
                                                met=True, step_line_id=line)])

    @staticmethod
    def scheme_read(qid, page_number, line):
        return QuestionMark(qid=qid, score=2, max_score=2, page=page_number, y_fraction=0.5,
                            anchor_line_id=line, remark="", attempted=True, mark_style="per_step",
                            criteria=[Criterion(description="Diazotisation", awarded=1, max=1,
                                                met=True, evidence_kind="diagram", step_line_id=line),
                                      Criterion(description="CuBr / HBr gives bromobenzene",
                                                awarded=1, max=1, met=True,
                                                evidence_kind="diagram", step_line_id=line)])

    def report(self, rows):
        return GradeReport(questions=rows, total_score=sum(q.score for q in rows),
                           max_total=70, overall_remarks="")

    def test_aasmeen_q12_seen_by_the_inventory_is_read_again_and_credited(self):
        pages = [page(2, 1077, 1650, AASMEEN_P2), page(3, 1098, 1650, AASMEEN_P3)]
        rows = [self.answered("Q10", 2, "P2L12"), self.blank("Q12", 2, "P2L16"),
                self.answered("Q13", 2, "P2L19")]
        inventory = InventoryResult(answer_map=build_answer_map(self.pkg, pages),
                                    written_qids=["Q10", "Q12", "Q13"])

        def grade(**kwargs):
            return self.report([self.scheme_read(q, 2, "P2D17") for q in kwargs["question_qids"]])

        with patch.object(pipeline, "claude_grade", side_effect=grade) as call:
            result = self.recheck(self.report(rows), pages, inventory)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["question_qids"], ["Q12"])
        self.assertIn("RECHECK: ANSWERS CALLED BLANK", call.call_args.kwargs["qp_text"])
        self.assertIn("DIAGRAM REGION", call.call_args.kwargs["qp_text"])
        q12 = next(q for q in result.questions if q.qid == "Q12")
        self.assertTrue(q12.attempted)
        self.assertEqual(q12.score, 2)
        self.assertIn("Q12", result.regraded_qids)
        self.assertEqual([q.qid for q in result.questions], ["Q10", "Q12", "Q13"])
        self.assertEqual(next(q for q in result.questions if q.qid == "Q13").score, 2)

    def test_saptarshi_q12_label_with_a_drawing_under_it_is_read_again_without_inventory(self):
        pages = [page(2, 1275, 857, SAPTARSHI_P2)]
        rows = [self.blank("Q12", 2, "P2L10"), self.answered("Q13", 2, "P2L13")]

        def grade(**kwargs):
            return self.report([self.scheme_read(q, 2, "P2D11") for q in kwargs["question_qids"]])

        with patch.object(pipeline, "claude_grade", side_effect=grade) as call:
            result = self.recheck(self.report(rows), pages, None)
        self.assertEqual(call.call_args.kwargs["question_qids"], ["Q12"])
        self.assertTrue(next(q for q in result.questions if q.qid == "Q12").attempted)

    def test_a_reread_that_still_finds_nothing_keeps_the_first_decision(self):
        pages = [page(2, 1275, 857, SAPTARSHI_P2)]
        first = self.blank("Q12", 2, "P2L10")
        rows = [first, self.answered("Q13", 2, "P2L13")]
        with patch.object(pipeline, "claude_grade",
                          side_effect=lambda **k: self.report([self.blank("Q12", 2, "")])):
            result = self.recheck(self.report(rows), pages, None)
        self.assertIs(next(q for q in result.questions if q.qid == "Q12"), first)
        self.assertNotIn("Q12", result.regraded_qids)

    def test_a_reread_that_cannot_run_fails_open(self):
        pages = [page(2, 1275, 857, SAPTARSHI_P2)]
        report = self.report([self.blank("Q12", 2, "P2L10"), self.answered("Q13", 2, "P2L13")])
        before = report.model_dump()
        with patch.object(pipeline, "claude_grade",
                          side_effect=StrictGradeError("COVERAGE_RECOVERY_BUDGET_DENIED", "no money")):
            result = self.recheck(report, pages, None)
        self.assertEqual(result.model_dump(), before)

    def test_no_writing_signal_means_no_extra_call(self):
        # Aasmeen's Q11 heading "(211)" is unparsed and blank, and without the inventory
        # nothing on the page says Q12 was written either: no paid re-read.
        pages = [page(2, 1077, 1650, AASMEEN_P2)]
        rows = [self.blank("Q11", 2, "P2L15"), self.blank("Q12", 2, "P2L16"),
                self.answered("Q13", 2, "P2L19")]
        with patch.object(pipeline, "claude_grade") as call:
            self.recheck(self.report(rows), pages, InventoryResult(
                answer_map=build_answer_map(self.pkg, pages), written_qids=["Q13"]))
        call.assert_not_called()

    def test_the_recheck_runs_before_the_breakdown_regrade_and_verification(self):
        src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
        body = src[src.index("def grade_prepared("):]
        self.assertLess(body.index("_complete_missing_questions("),
                        body.index("_recheck_written_blanks("))
        self.assertLess(body.index("_recheck_written_blanks("),
                        body.index("_regrade_invalid_questions("))
        self.assertLess(body.index("_recheck_written_blanks("),
                        body.index("verification_candidates(report)"))


class InventoryWrittenClaims(unittest.TestCase):
    def test_written_claims_are_kept_even_when_their_lines_cannot_be_verified(self):
        # Aditya: Q14 lives inside the merged table block, so the inventory cannot cite
        # a line of its own; it still saw the answer, which is what the recheck needs.
        pkg, _ = chemistry_package()
        pages = [page(2, 1580, 2084, ADITYA_P2)]
        reply = AnswerInventory(items=[
            InventoryItem(qid="Q11", written=True, pages=[2], first_line_id="P2L6",
                          last_line_id="P2L7"),
            InventoryItem(qid="Q12", written=True, pages=[2], first_line_id="P2D11",
                          last_line_id="P2D11"),
            InventoryItem(qid="Q14", written=True, pages=[2]),
            InventoryItem(qid="Q15", written=False),
        ])
        result = take_answer_inventory(
            package=pkg, answer_map=build_answer_map(pkg, pages), ocr_pages=pages,
            student_pngs=[b"p1", b"p2"], google_token="t", task_id="t",
            reader=lambda *a, **k: reply)
        self.assertEqual(result.written_qids, ["Q11", "Q12", "Q14"])
        self.assertIn("Q15", result.absent_qids)


class PhantomHeadingInsideAnotherAnswer(unittest.TestCase):
    def test_aasmeen_q11_moves_to_where_the_inventory_saw_it(self):
        pkg, _ = chemistry_package()
        pages = [page(2, 1077, 1650, AASMEEN_P2), page(3, 1098, 1650, AASMEEN_P3)]
        amap = build_answer_map(pkg, pages)
        self.assertEqual(located(amap, "Q11").label_line_ids, ("P3L25",))  # the phantom
        reply = AnswerInventory(items=[
            InventoryItem(qid="Q11", written=True, pages=[2], first_line_id="P2L15",
                          last_line_id="P2L15", label_as_written="Q11)"),
            InventoryItem(qid="Q12", written=True, pages=[2], first_line_id="P2L16",
                          last_line_id="P2D17"),
            InventoryItem(qid="Q13", written=True, pages=[2], first_line_id="P2L18",
                          last_line_id="P2L22"),
            InventoryItem(qid="Q14", written=True, pages=[2, 3], first_line_id="P2L23",
                          last_line_id="P3L9"),
            InventoryItem(qid="Q15", written=True, pages=[3], first_line_id="P3L10",
                          last_line_id="P3L14"),
            InventoryItem(qid="Q16", written=True, pages=[3], first_line_id="P3L15",
                          last_line_id="P3L30"),
        ])
        spans, absent, _ = verify_inventory(reply, pages, pkg)
        merged = merge_inventory(amap, spans, absent, pages)
        q11 = located(merged.answer_map, "Q11")
        self.assertEqual(q11.line_ids, ("P2L15",))
        self.assertNotIn("P3L25", q11.line_ids)
        self.assertIn("Q11", merged.disputed_qids)
        self.assertEqual(located(merged.answer_map, "Q16").label_line_ids, ("P3L15",))
        # The drawn Q12 now carries its diagram block.
        self.assertEqual(located(merged.answer_map, "Q12").line_ids, ("P2L16", "P2D17"))

    def test_a_map_heading_of_its_own_is_kept_when_the_inventory_disagrees(self):
        pkg, _ = chemistry_package()
        pages = [page(2, 1077, 1650, AASMEEN_P2), page(3, 1098, 1650, AASMEEN_P3)]
        amap = build_answer_map(pkg, pages)
        reply = AnswerInventory(items=[
            InventoryItem(qid="Q15", written=True, pages=[3], first_line_id="P3L11",
                          last_line_id="P3L14"),
        ])
        spans, absent, _ = verify_inventory(reply, pages, pkg)
        merged = merge_inventory(amap, spans, absent, pages)
        self.assertEqual(located(merged.answer_map, "Q15").label_line_ids, ("P3L10",))


class DrawnAnswersAreNotBlank(unittest.TestCase):
    def test_the_prompt_says_a_diagram_region_is_read_from_the_image(self):
        prompt = grader.build_system_prompt("Chemistry", "12", "school")
        self.assertIn("never call it blank", prompt)
        src = open(os.path.join(ROOT, "grader.py"), encoding="utf-8").read()
        self.assertNotIn("are blank bands where a figure/table lives", src)
        self.assertIn("(it is not blank)", src)


if __name__ == "__main__":
    unittest.main()
