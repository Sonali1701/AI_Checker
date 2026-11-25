# -*- coding: utf-8 -*-
"""Build 79: an answer belongs to the question, part or OR alternative its CONTENT answers.

Faculty re-check of the Parishram 20 Sept (English, Chemistry) and 6 Sept (Maths) copies:
  * English, Jaskeerat Q16: labelled "b)" and answering 16(b), graded against 16(a) (8/10,
    about 4.5 against (b)). The page inventory filed it under Q16.alt1 by position and the
    grader was told Q16.alt2 was "NOT WRITTEN, never award it from another answer's lines".
  * English, Jaskeerat Q11: the unlabelled answer about Beethoven's deafness is (ix), written
    above "viii)". The inventory filed the two by their order, the examiner cited them the
    right way round, and the anchor check forced them back: (ix) lost its mark.
  * Maths 6 Sept, Akshay and Rishita: the "14)" heading was lost by the OCR, the inventory
    (which saw only question ids) filed the alpha working as Q13 and Q14 went "not attempted".
  * Chemistry Q17, one question id holding "What happens when (i)..(v)" OR "Identify A to E":
    answers to the OR set were marked against the first set.
The OCR lines below are copied from the stored reports.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402
from answer_inventory import (INVENTORY_SYSTEM_PROMPT, AnswerInventory,  # noqa: E402
                              InventoryItem, build_inventory_prompt, inventory_prompt_block,
                              take_answer_inventory)
from answer_map import AnswerLocation, AnswerMap, answer_map_prompt_block  # noqa: E402
from exam_package import ChoiceGroup, ChoiceOption, build_exam_package  # noqa: E402
from grader import AuditItem, Criterion, GradeReport, MarksItem, MarksScheme, QuestionMark  # noqa: E402
from grading_quality import prepare_evidence, resolve_verified_anchors  # noqa: E402
from mathpix import LineBox, PageOCR  # noqa: E402


def _package(items, groups=(), subject="English", total=None):
    scheme = MarksScheme(total=sum(m for _, m, _ in items), items=[
        MarksItem(qid=q, max=m, description=d,
                  question_type="objective" if d.startswith("MCQ") else "unknown",
                  correct_answer="B" if d.startswith("MCQ") else "")
        for q, m, d in items])
    return build_exam_package(
        exam_type="school", student_class="Class 12", subject=subject, level="Class 12",
        paper="", attempt="", applicable_law_date=None,
        total_marks=total or scheme.total, question_paper=b"%PDF-paper",
        answer_key=b"%PDF-key", key_source="upload", marks_scheme=scheme,
        scheme_source="ai_verified", choice_groups=list(groups))


def _or(qid, maximum):
    return ChoiceGroup(group_id=f"choice-{qid.lower()}", select_count=1,
                       selection_policy="first_attempted", contribution_marks=maximum,
                       options=[ChoiceOption(option_id="a", question_ids=[f"{qid}.alt1"]),
                                ChoiceOption(option_id="b", question_ids=[f"{qid}.alt2"])])


def _page(number, rows):
    return PageOCR(number, 1240, 1754, [
        LineBox(lid, text, tuple(box), number, confidence=0.9) for lid, text, box in rows])


# Rishita (Maths 6 Sept) page 5: "Q14." came back as "MQSA".
RISHITA_P5 = [
    ("P5L5", r"S12 \( \Delta|A|=4 \)", (245, 119, 423, 152)),
    ("P5L6", r"\[ |\operatorname{adj} A|=4^{2-1} \\ \left|A^{-1}\right|=\frac{1}{4} \]",
     (328, 165, 654, 355)),
    ("P5L7", "MQSA", (232, 396, 292, 420)),
    ("P5L8", r"\[ A^{2} =\left[\begin{array}{ccc} 5 & 5 \alpha & \alpha \\ 0 & \alpha & 5 \alpha"
             r" \end{array}\right] \]", (343, 370, 704, 603)),
    ("P5L9", r"\[ 25\left[\left(25 \alpha+5 \alpha^{2}\right) \alpha^{2}(30 \alpha+25)-0\right] \]",
     (367, 630, 785, 871)),
    ("P5L10", r"Q15. \( f(x)=\left\{\begin{array}{cc}x^{2}+3, & x \neq 0 \\ 1, & x=0\end{array}"
              r"\right. \)", (258, 913, 630, 997)),
]

# Jaskeerat (English 20 Sept) page 9: Q16, labelled and answered as (b).
JASKEERAT_P9 = [
    ("P9L5", "g160", (19, 178, 88, 240)),
    ("P9L6", 'b) In the short story. "There will seft rains", by', (49, 196, 1055, 309)),
    ("P9L10", "the house's inhabitants. But, this story is a warning", (116, 385, 1124, 472)),
    ("P9L18", "nuclear empowernment will 1 ead to demolishing,", (108, 774, 1168, 857)),
    ("P9L21", "humankind to be self-dependent and limit", (95, 922, 1119, 988)),
]

# Jaskeerat page 10: the Beethoven answer that follows.
JASKEERAT_P10 = [
    ("P10L6", 'In the "Beethoven" by Shane Roycan the', (170, 140, 1085, 263)),
    ("P10L5", "911. H3", (99, 151, 238, 237)),
    ("P10L7", "poet tries to make the andience deeply", (241, 215, 1105, 313)),
    ("P10L11", "Beethoven who was mentally and physically", (235, 396, 1196, 473)),
    ("P10L26", "Resillience and Regoice are key to transfors", (248, 1271, 1235, 1364)),
]

# Jaskeerat page 3: (vii) unlabelled, the (ix) answer unlabelled, "viii)" read as "Vii)", (x).
JASKEERAT_P3 = [
    ("P3L5", "The machines are shown as useful tools performing", (21, 208, 949, 290)),
    ("P3L6", "every task regardless of the presence of the", (43, 269, 854, 361)),
    ("P3L10", "demolich gradually.", (16, 484, 489, 561)),
    ("P3L11", "He is been physically and mentally being abused", (25, 560, 897, 655)),
    ("P3L12", "so his achievement is ext a precious", (21, 618, 875, 676)),
    ("P3L13", "git as he himself is suffering from dealness still", (20, 669, 878, 744)),
    ("P3L14", "his notes prodice sensational melodies.", (42, 718, 771, 781)),
    ("P3L15", "Vii) It signifies the nature as a nurtures and", (15, 813, 893, 886)),
    ("P3L16", "Sheeker who represents the liveliness because of", (48, 859, 891, 931)),
    ("P3L17", r"\( \sigma_{\text {its }} \) presence. \( \hbar \)", (52, 916, 225, 973)),
    ("P3L19", "* emphasizes * the readers. to a net just", (43, 1000, 874, 1072)),
    ("P3L20", "visis Beethoven's music but feel the sensation", (54, 1053, 960, 1119)),
    ("P3L21", "of his musical note and achievements", (47, 1101, 996, 1159)),
]


class InventoryReadsContentNotOrder(unittest.TestCase):
    """The cheap inventory pass saw only "Q13 (2 marks, written)", so it filed by order."""

    def test_written_questions_are_listed_with_what_they_ask(self):
        pkg = _package([("Q1", 1, "MCQ determinant of 3x3 [answer key prints (C)]"),
                        ("Q12", 2, "Determinant of inverse matrix"),
                        ("Q13", 2, "Rolle's theorem verification"),
                        ("Q14", 2, "Find alpha from |A squared| = 25"),
                        ("Q15", 2, "Continuity at x = 0")], subject="Mathematics")
        prompt = build_inventory_prompt(pkg, [_page(5, RISHITA_P5)])
        self.assertIn("- Q13 (2 marks, written): Rolle's theorem verification", prompt)
        self.assertIn("- Q14 (2 marks, written): Find alpha from |A squared| = 25", prompt)
        # An MCQ's text (which can carry a key note) never reaches the key-blind reader.
        self.assertIn("- Q1 (1 marks, MCQ)\n", prompt)
        self.assertNotIn("answer key prints", prompt)

    def test_the_inventory_is_told_content_decides(self):
        self.assertIn("never by its place in the booklet", " ".join(INVENTORY_SYSTEM_PROMPT.split()))
        self.assertIn("'b)' can sit over an answer to (a)", INVENTORY_SYSTEM_PROMPT)
        self.assertIn("Qn.alt1, Qn.alt2 are Qn's OR alternatives in the paper's printed order",
                      INVENTORY_SYSTEM_PROMPT)


class OneOrAnswerIsNotAnAbsentAlternative(unittest.TestCase):
    """Jaskeerat Q16: the grader was forbidden to file (b)'s answer under Q16.alt2."""

    def run_inventory(self, items):
        pkg = _package([("Q14", 5, "Three apparitions"),
                        ("Q16.alt1", 10, "Soft Rains: the automated house"),
                        ("Q16.alt2", 10, "Soft Rains as a warning about technology"),
                        ("Q17", 5, "Beethoven poem"), ("Q18", 5, "Macbeth's guilt"),
                        ("Q19", 5, "Lady Macbeth")],
                       groups=[_or("Q16", 10), ChoiceGroup(
                           group_id="any-two", select_count=2, selection_policy="first_attempted",
                           contribution_marks=10, options=[
                               ChoiceOption(option_id=q, question_ids=[q])
                               for q in ("Q17", "Q18", "Q19")])], total=30)
        pages = [_page(9, JASKEERAT_P9), _page(10, JASKEERAT_P10)]
        amap = AnswerMap(package_id=pkg.package_id, locations=tuple(
            AnswerLocation(qid=q.qid, status="unresolved") for q in pkg.questions))
        return take_answer_inventory(
            package=pkg, answer_map=amap, ocr_pages=pages, student_pngs=[b"png"],
            google_token="t", task_id="k", reader=lambda *a, **k: AnswerInventory(items=items))

    def test_the_other_alternative_is_left_to_the_content(self):
        result = self.run_inventory([
            InventoryItem(qid="Q16.alt1", written=True, pages=[9], first_line_id="P9L5",
                          last_line_id="P9L21", label_as_written="16 b)"),
            InventoryItem(qid="Q16.alt2", written=False),
            InventoryItem(qid="Q18", written=False)])
        self.assertIn("Q16.alt2", result.absent_qids)
        self.assertEqual(result.alternatives, {"Q16.alt2": ["Q16.alt1"]})
        block = inventory_prompt_block(result)
        not_written = next(line for line in block.splitlines() if line.startswith("NOT WRITTEN"))
        self.assertNotIn("Q16.alt2", not_written)
        # An "any two of Q17, Q18, Q19" question keeps the plain absent rule (CA phantoms).
        self.assertIn("Q18", not_written)
        self.assertIn("Q16.alt2 (the answer is filed under Q16.alt1)", block)
        self.assertIn("Decide from its CONTENT which alternative it answers", block)

    def test_both_alternatives_absent_is_still_not_written(self):
        result = self.run_inventory([InventoryItem(qid="Q16.alt1", written=False),
                                     InventoryItem(qid="Q16.alt2", written=False)])
        self.assertEqual(result.alternatives, {})
        self.assertIn("Q16.alt1, Q16.alt2", inventory_prompt_block(result))

    def test_the_grader_may_refile_an_answer_by_its_content(self):
        # Rishita's alpha working sat in the map under Q13; Q14 was "not written".
        block = inventory_prompt_block(self.run_inventory([
            InventoryItem(qid="Q14", written=False)]))
        self.assertIn("Never award them from writing that answers another question", block)
        self.assertNotIn("another answer's lines", block)
        amap = AnswerMap(package_id="p", locations=(AnswerLocation(
            qid="Q13", status="located", pages=(5,), label_line_ids=("P5L7",),
            line_ids=("P5L7", "P5L9"), uncertain_line_ids=("P5L7", "P5L9")),))
        self.assertIn("grade each answer under the question, part or OR alternative its content "
                      "answers", " ".join(answer_map_prompt_block(amap).split()))


class GradingRulesNameAnswersByContent(unittest.TestCase):
    def test_or_inside_one_question_and_unlabelled_parts(self):
        block = " ".join(grader.SCHOOL_MARKING_POLICY_BLOCK.split())
        # Chemistry Q17: answers to "Identify A to E" were marked as (i) to (v).
        self.assertIn("The same holds for an OR inside one question id", block)
        self.assertIn("build the criteria from the set the student answered", block)
        self.assertIn('"b)" above an answer to (a) is (a)', block)
        # Jaskeerat Q11: position used to decide ("Parts follow their order on the page").
        self.assertNotIn("Parts follow their order on the page", block)
        self.assertIn('an unlabelled answer about (ix) is (ix) even above "viii)"', block)
        self.assertIn("is part (a)", block)
        prompt = grader.build_system_prompt("Chemistry", "Class 12")
        self.assertIn("OR inside one question", prompt)


def _q11_report(viii_lines, ix_lines, viii_anchor, ix_anchor):
    def part(qid, score, line, quote):
        return QuestionMark(qid=qid, score=score, max_score=1, page=3, y_fraction=0.5,
                            anchor_line_id=line, criteria=[Criterion(
                                description=f"{qid} reason", awarded=score, max=1,
                                met=score >= 1, anchor_quote=quote, step_line_id=line)])
    regions = {
        "q11.vii": ["P3L5", "P3L6", "P3L10"], "q11.viii": viii_lines,
        "q11.ix": ix_lines, "q11.x": ["P3L19", "P3L20", "P3L21"]}
    report = GradeReport(total_score=1, max_total=4, overall_remarks="", questions=[
        part("Q11.vii", 0.5, "P3L5", "The machines are shown as useful tools"),
        part("Q11.viii", 0, *viii_anchor),
        part("Q11.ix", 0, *ix_anchor),
        part("Q11.x", 0.5, "P3L20", "visis Beethoven's music")])
    report.evidence_contract = True
    # The inventory's spans: every line still marked for a visual re-read.
    report.answer_regions = {
        key: {"qid": key.upper().replace("Q11.", "Q11."), "status": "located",
              "label_line_ids": [lines[0]], "line_ids": list(lines),
              "uncertain_line_ids": list(lines), "evidence_line_ids": list(lines),
              "pages": [3]}
        for key, lines in regions.items()}
    return report


VIII_BY_ORDER = ["P3L11", "P3L12", "P3L13", "P3L14"]
IX_BY_ORDER = ["P3L15", "P3L16", "P3L17"]
TEASDALE = ("P3L15", "It signifies the nature as a nurtures and")
BEETHOVEN = ("P3L11", "He is been physically and mentally being abused")


class ExaminersDecideWhichUnlabelledPartIsWhich(unittest.TestCase):
    pages = [_page(3, JASKEERAT_P3)]

    def verdicts(self):
        # Rows: vii (0), viii (1), ix (2), x (3). The examiner read the page.
        return [AuditItem(row=1, qid="Q11.viii", evidence_status="verified", correct=0,
                          step_line_id=TEASDALE[0], anchor_quote=TEASDALE[1],
                          reason="The poem shows nature going on after mankind."),
                AuditItem(row=2, qid="Q11.ix", evidence_status="verified", correct=1,
                          step_line_id=BEETHOVEN[0], anchor_quote=BEETHOVEN[1])]

    def resolve(self, report):
        return resolve_verified_anchors(report, self.verdicts(), self.pages,
                                        include_awards=True, selected_qids=None)

    def test_the_examiners_crossed_citations_exchange_the_positional_regions(self):
        report = _q11_report(VIII_BY_ORDER, IX_BY_ORDER, BEETHOVEN, TEASDALE)
        gaps = self.resolve(report)
        self.assertFalse([g for g in gaps.values() if "outside the active answer" in g], gaps)
        self.assertEqual(report.answer_regions["q11.viii"]["line_ids"], IX_BY_ORDER)
        self.assertEqual(report.answer_regions["q11.ix"]["line_ids"], VIII_BY_ORDER)
        self.assertEqual(report.answer_regions["q11.vii"]["line_ids"], ["P3L5", "P3L6", "P3L10"])
        self.assertTrue(report.answer_regions["q11.ix"]["ownership_proposed"])
        self.assertIn("Q11.ix", report.placement_risk_qids)

    def test_only_an_exact_exchange_of_two_positional_regions_moves(self):
        # A region with a label of its own is never exchanged.
        report = _q11_report(VIII_BY_ORDER, IX_BY_ORDER, BEETHOVEN, TEASDALE)
        report.answer_regions["q11.ix"]["uncertain_line_ids"] = []
        gaps = self.resolve(report)
        self.assertTrue([g for g in gaps.values() if "outside the active answer" in g])
        self.assertEqual(report.answer_regions["q11.viii"]["line_ids"], VIII_BY_ORDER)
        # A one-sided citation moves nothing.
        report = _q11_report(VIII_BY_ORDER, IX_BY_ORDER, BEETHOVEN, TEASDALE)
        verdicts = self.verdicts()
        verdicts[1] = verdicts[1].model_copy(update={
            "step_line_id": "P3L16", "anchor_quote": "Sheeker who represents the liveliness"})
        resolve_verified_anchors(report, verdicts, self.pages, include_awards=True,
                                 selected_qids=None)
        self.assertEqual(report.answer_regions["q11.viii"]["line_ids"], VIII_BY_ORDER)
        # A quote that is not on its line proves nothing.
        report = _q11_report(VIII_BY_ORDER, IX_BY_ORDER, BEETHOVEN, TEASDALE)
        verdicts = self.verdicts()
        verdicts[0] = verdicts[0].model_copy(update={"anchor_quote": "Beethoven was deaf"})
        resolve_verified_anchors(report, verdicts, self.pages, include_awards=True,
                                 selected_qids=None)
        self.assertEqual(report.answer_regions["q11.viii"]["line_ids"], VIII_BY_ORDER)
        # The exact exchange does move.
        report = _q11_report(VIII_BY_ORDER, IX_BY_ORDER, BEETHOVEN, TEASDALE)
        self.resolve(report)
        self.assertEqual(report.answer_regions["q11.viii"]["line_ids"], IX_BY_ORDER)

    def test_the_first_examiners_crossed_reading_is_kept_before_verification(self):
        # The grader read (viii) on the Teasdale lines and (ix) on the Beethoven lines.
        report = _q11_report(VIII_BY_ORDER, IX_BY_ORDER, TEASDALE, BEETHOVEN)
        prepare_evidence(report, self.pages)
        self.assertEqual(report.answer_regions["q11.viii"]["line_ids"], IX_BY_ORDER)
        self.assertEqual(report.answer_regions["q11.ix"]["line_ids"], VIII_BY_ORDER)
        ix = next(q for q in report.questions if q.qid == "Q11.ix")
        self.assertEqual((ix.anchor_line_id, ix.criteria[0].step_line_id), ("P3L11", "P3L11"))


# Akshay (Maths 6 Sept) page 4: the "14)" heading is not in the OCR of P4L4.
AKSHAY_P4 = [
    ("P4L3", "\[\n\text { 12) } \int_{A^{-1}}|A|=4\n\]", (206, 715, 507, 865)),
    ("P4L4", "\[\n\begin{array}{l}\n{\left[\begin{array}{lll}\n5 & 5 \alpha & \alpha \\\n"
             "0 & \alpha & 5 \alpha \\\n0 & 0 & 5\n\end{array}\right]} \\\n"
             "{\left[\begin{array}{ccc}\n25 & 25 \alpha+5 \alpha^{2} & 5 \alpha+25 "
             "\alpha^{2}+5 \alpha \\\n0 & 0 & 25\n\end{array}\right]=21}\n\end{array}\n\]",
     (241, 912, 1049, 1421)),
    ("P4L5", "\[\n\begin{aligned}\n25 \alpha^{2} & =25 \\\n\alpha^{2} & =2521 \\\n"
             "\alpha^{2} & =1\n\end{aligned}\n\]", (334, 1427, 608, 1615)),
    ("P4L6", "\[\n\text { a. } 1+2=+1\n\]", (384, 1611, 717, 1660)),
]


class AnAnswerSplitByPositionRejoinsItsQuestion(unittest.TestCase):
    """Akshay: the alpha working was filed as Q13 and only its last line as Q14."""

    def report(self, q13_attempted=False):
        def region(lines, uncertain=True):
            return {"status": "located", "label_line_ids": [lines[0]], "line_ids": list(lines),
                    "uncertain_line_ids": list(lines) if uncertain else [],
                    "evidence_line_ids": list(lines), "pages": [4]}
        q12 = QuestionMark(qid="Q12", score=0.5, max_score=2, page=4, y_fraction=0.4,
                           anchor_line_id="P4L3", criteria=[Criterion(
                               description="det of inverse", awarded=0.5, max=2, met=False,
                               anchor_quote="|A|=4", step_line_id="P4L3")])
        q13 = QuestionMark(qid="Q13", score=0, max_score=2, page=4, y_fraction=0.5,
                           anchor_line_id="", attempted=q13_attempted, remark="Not attempted")
        # The grader read the alpha working as Q14, by what it works out.
        q14 = QuestionMark(qid="Q14", score=1, max_score=2, page=4, y_fraction=0.6,
                           anchor_line_id="P4L4", criteria=[
                               Criterion(description="A squared", awarded=1, max=1, met=True,
                                         anchor_quote="25 & 25 \alpha+5 \alpha^{2}",
                                         step_line_id="P4L4"),
                               Criterion(description="alpha from |A squared|", awarded=0, max=1,
                                         met=False, anchor_quote="25 \alpha^{2} & =25",
                                         step_line_id="P4L5")])
        report = GradeReport(total_score=1.5, max_total=6, overall_remarks="",
                             questions=[q12, q13, q14])
        report.answer_regions = {"q12": region(["P4L3"], uncertain=False),
                                 "q13": region(["P4L4", "P4L5"]), "q14": region(["P4L6"])}
        return report

    def test_the_unattempted_siblings_positional_lines_join_the_cited_answer(self):
        report = self.report()
        prepare_evidence(report, [_page(4, AKSHAY_P4)])
        self.assertEqual(report.answer_regions["q14"]["line_ids"], ["P4L4", "P4L5", "P4L6"])
        self.assertEqual(report.answer_regions["q14"]["label_line_ids"], ["P4L4"])
        self.assertEqual(report.answer_regions["q13"]["line_ids"], [])
        q14 = report.questions[2]
        self.assertEqual([c.step_line_id for c in q14.criteria], ["P4L4", "P4L5"])
        self.assertEqual(q14.anchor_line_id, "P4L4")
        self.assertIn("Q14", report.placement_risk_qids)
        # When the grader still calls Q13 attempted, the lines stay where they are.
        report = self.report(q13_attempted=True)
        prepare_evidence(report, [_page(4, AKSHAY_P4)])
        self.assertEqual(report.answer_regions["q14"]["line_ids"], ["P4L6"])


if __name__ == "__main__":
    unittest.main()
