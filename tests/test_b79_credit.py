# -*- coding: utf-8 -*-
"""Build 79: marks given for work that is not on the page, and lenient marking.

Faculty re-marking of the Parishram 20 Sept (Chemistry, English) and 6 Sept (Maths)
copies found credit with no evidence behind it:

* Maths, Akshay Q6: 2/2 for "Δ = xy² - xz² - y + yz² + z - y²z + x²", an expansion
  that is nowhere on the page (he multiplied C3 by C2 and got 0). The quote was never
  compared with the cited line, and when the second examiner could not certify the
  row the first examiner's 2/2 simply stood.
* Chemistry, Namrata Q19: part (iv) paid from the very line and words that answer
  part (v). Mohammed Hamza Q21(iv): paid for an anti-Markovnikov step the page does
  not show; the second examiner could not certify it and the credit stood.

and calibration faults the prompt rules now name (a conclusion resting on a wrong
step, 1-mark reasons, long literature answers, chemistry structures and reagents).
The OCR lines below are copied from the stored reports of those copies.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitz  # noqa: E402
import grader  # noqa: E402
from grader import (AuditItem, Criterion, GradeReport, MarksItem, MarksScheme,  # noqa: E402
                    QuestionMark, build_audit_prompt)
from grading_quality import (find_unsupported_credit, remove_unsupported_credit,  # noqa: E402
                             uncertified_credit_keys)
from mathpix import LineBox, PageOCR  # noqa: E402
from selective_verification import verification_candidates  # noqa: E402

# Akshay (Maths 6 Sept) page 2, P2L9: a sideways photo, the OCR of the Q6 working.
AKSHAY_P2L9 = ("\\[\n\\left|\\begin{array}{ccc}\nz f x & z & 1 \\\\\nz f x & f & 1 \\\\\n"
               "z f x & x & 1\n\\end{array}\\right|=\\left|\\begin{array}{lll}\nf_{x} & z & 1 "
               "\\\\\nx z & h & 1 \\\\\nz h & x & 1\n\\end{array}\\right|<g\n\\]")
AKSHAY_EVIDENCE = "Δ = xy² - xz² - y + yz² + z - y²z + x²"

# Namrata (Chemistry) page 5: Q19 "iii)" drawing, "v)" (read as nu), its drawing and name.
NAMRATA_P5 = [("P5L1", "g19:- in", (34, 150, 155, 216)),
              ("P5D2", "[detected diagram]", (114, 116, 902, 422)),
              ("P5L3", "\\( \\nu \\)", (116, 478, 157, 548)),
              ("P5D4", "[detected diagram]", (220, 430, 583, 584)),
              ("P5L5", "IUPAC ⇒ 4-bromo-4-methyl pent -2-ene", (129, 623, 801, 702))]

# Mohammed Hamza (Chemistry) page 8: the working out of A to D, then (i) to (iii).
HAMZA_P8 = [
    ("P8L1", "Q21) \\( \\ln 8\\left(\\left(\\mathrm{H}_{3}\\right)_{2}\\left(\\mathrm{H}_{2} "
             "\\mathrm{H}_{1} \\mathrm{~B}^{2}\\right)\\right. \\) alivany \\( \\left(\\mathrm{H}"
             "_{2}\\right), \\mathrm{C} \\) )", (7, 3, 707, 175)),
    ("P8L3", "2. Formula of (1)", (61, 180, 348, 285)),
    ("P8L4", "\\[\n(\\mathrm{CH})_{1} \\mathrm{C}=\\mathrm{CH}_{2} \\xrightarrow{\\mathrm{HBr}}"
             "(2,8+\\mathrm{COH} \\text { but }-2 \\ldots \\text { Ahulpupon })\n\\]",
     (29, 203, 774, 359)),
    ("P8L6", "Thrompound \\( A \\) is 1 - Bromo -2 -methy propare (Isobaty \\( C \\)",
     (15, 459, 834, 593)),
    ("P8L8", "ii) Ars) Position isomersorm", (9, 607, 333, 704)),
    ("P8L9", "iii) Ans 2,5 Dinelty y the xane)", (6, 658, 435, 776)),
]


def _page(page, rows, width=1240, height=1755):
    return PageOCR(page, width, height,
                   [LineBox(lid, text, box, page, kind="diagram" if "D" in lid[2:] else "text")
                    for lid, text, box in rows])


def _crit(desc, awarded, maximum, lid, quote="", evidence="", kind="written"):
    return Criterion(description=desc, awarded=awarded, max=maximum, met=awarded >= maximum,
                     step_line_id=lid, anchor_quote=quote, evidence=evidence, evidence_kind=kind)


def _report(*questions):
    for q in questions:
        q.score = sum(c.awarded for c in q.criteria)
        q.max_score = sum(c.max for c in q.criteria)
    return GradeReport(total_score=sum(q.score for q in questions),
                       max_total=sum(q.max_score for q in questions), overall_remarks="",
                       evidence_contract=True, questions=list(questions))


def _q(qid, page, criteria):
    return QuestionMark(qid=qid, score=0, max_score=0, page=page, y_fraction=.2,
                        anchor_line_id=criteria[0].step_line_id, mark_style="per_step",
                        criteria=criteria)


def akshay():
    report = _report(_q("Q6", 2, [_crit("Determinant expansion and simplification", 2, 2,
                                        "P2L9", "z f x & z & 1", AKSHAY_EVIDENCE)]))
    return report, [_page(2, [("P2L9", AKSHAY_P2L9, (763, 203, 1005, 878))])]


def namrata():
    report = _report(_q("Q19", 5, [
        _crit("(i) Finkelstein reaction: 1-Cl to 1-I", 0, 1, "P5L1", "Q19:- iii)",
              "No Finkelstein preparation equation is present", "missing"),
        _crit("(iii) Chlorobenzene + CH₃Cl + Na/ether product", 1, 1, "P5D2", kind="diagram"),
        _crit("(iv) Alkene from 1-bromo-1-methylcyclohexane", 1, 1, "P5L5",
              "IUPAC ⇒ 4-bromo-4-methyl pent-2-ene"),
        _crit("(v) IUPAC name (incomplete question)", 1, 1, "P5L5",
              "IUPAC ⇒ 4-bromo-4-methylpent-2-ene")]))
    return report, [_page(5, NAMRATA_P5, 1077, 1650)]


def hamza():
    report = _report(_q("Q21", 8, [
        _crit("Name of compound (A): 1-bromo-2-methylpropane", 1, 1, "P8L6",
              "Thrompound A is 1 - Bromo -2 -methy propare (Isobaty C brumide"),
        _crit("Type of isomerism between (A) and (C): position isomerism", 1, 1, "P8L8",
              "Position isomersorm"),
        _crit("IUPAC name of compound (D): 2,5-dimethylhexane", 1, 1, "P8L9",
              "2,5 Dinelty y the xane"),
        _crit("Reaction of (C) with alc. KOH: dehydrohalogenation", 1, 1, "P8L4", "(CH)₁C=CH₂"),
        _crit("Addition of HBr in presence of peroxide: anti-Markovnikov", 1, 1, "P8L4",
              "(CH)₁C=CH₂ → HBr but-2...Ahulpupon",
              "Student correctly applies anti-Markovnikov's rule for HBr addition with "
              "peroxide, forming 1-bromo-2-methylpropane")]))
    return report, [_page(8, HAMZA_P8, 937, 1650)]


class UnsupportedCreditIsFlagged(unittest.TestCase):
    def test_quoted_working_that_is_not_on_the_page(self):
        report, pages = akshay()
        flags = find_unsupported_credit(report, pages)
        self.assertEqual(list(flags), ["q6:0"])
        self.assertIn("is not on line P2L9", flags["q6:0"])

    def test_one_line_cannot_answer_two_parts(self):
        report, pages = namrata()
        flags = find_unsupported_credit(report, pages)
        self.assertEqual(sorted(flags), ["q19:2", "q19:3"])
        self.assertIn("part (iv)", flags["q19:2"])
        # The drawing credited for (iii) and the uncredited (i) are left alone.
        self.assertNotIn("q19:1", flags)
        self.assertNotIn("q19:0", flags)

    def test_full_award_claiming_work_the_page_does_not_show(self):
        report, pages = hamza()
        self.assertEqual(list(find_unsupported_credit(report, pages)), ["q21:4"])

    def test_credit_with_no_line_or_recorded_missing(self):
        report = _report(_q("Q18", 2, [
            _crit("(i) structure", 1, 1, "P2L1", "CH3-CH(Cl)"),
            _crit("(iv) 1,4-Dibromobut-2-ene structure", 1, 1, "", kind="missing"),
            _crit("(v) structure", 1, 1, "P9L40", "Br")]))
        pages = [_page(2, [("P2L1", "i) CH3-CH(Cl)-CH3", (10, 10, 400, 60))])]
        flags = find_unsupported_credit(report, pages)
        self.assertEqual(sorted(flags), ["q18:1", "q18:2"])
        self.assertIn("P9L40 is not on this copy", flags["q18:2"])

    def test_partial_credit_reasons_and_missing_ocr_are_not_flagged(self):
        report = _report(_q("Q15", 3, [
            _crit("Alcoholic KOH: elimination, alkene product", 0.5, 1, "P3L22",
                  "elimination", "Elimination stated, but alkene product is omitted", "missing")]))
        pages = [_page(3, [("P3L22", "alc KOH gives elimination", (10, 10, 400, 60))])]
        self.assertEqual(find_unsupported_credit(report, pages), {})
        report, _ = akshay()
        self.assertEqual(find_unsupported_credit(report, []), {})


class FlaggedCreditIsRechecked(unittest.TestCase):
    def test_flag_makes_the_question_a_mandatory_verification(self):
        report, pages = akshay()
        report.unsupported_credit = find_unsupported_credit(report, pages)
        candidate = next(c for c in verification_candidates(report) if c.qid == "Q6")
        self.assertTrue(candidate.mandatory)
        self.assertIn("1-credit(s)-without-page-evidence", candidate.reasons)

    def test_second_examiner_is_told_why_the_row_is_flagged(self):
        report, pages = namrata()
        report.unsupported_credit = find_unsupported_credit(report, pages)
        prompt = build_audit_prompt(report, answer_key_text="key", ocr_transcript="t",
                                    include_awards=True)
        self.assertEqual(prompt.count("CODE CHECK:"), 2)
        self.assertIn("CODE CHECK: part (iv) is credited from the same line P5L5", prompt)
        self.assertIn("unconfirmed flagged credit is removed", prompt)
        unflagged, _ = hamza()
        self.assertNotIn("CODE CHECK", build_audit_prompt(
            _report(unflagged.questions[0].model_copy(update={"criteria": unflagged.questions[0].criteria[:3]})),
            answer_key_text="key", ocr_transcript="t", include_awards=True))


class UnconfirmedFlaggedCreditIsRemoved(unittest.TestCase):
    def test_uncertain_examiner_removes_only_flagged_credit(self):
        report, pages = hamza()
        report.unsupported_credit = find_unsupported_credit(report, pages)
        verdicts = [AuditItem(row=i, correct=1, evidence_status="verified", step_line_id=lid,
                              anchor_quote="x") for i, lid in enumerate(["P8L6", "P8L8", "P8L9"])]
        # Row 3 (unflagged) and row 4 (flagged) both come back uncertain.
        verdicts += [AuditItem(row=3, correct=1, evidence_status="uncertain"),
                     AuditItem(row=4, correct=1, evidence_status="uncertain")]
        _, unverified = grader.audit_applicable_verdicts(report, verdicts, include_awards=True)
        self.assertEqual(sorted(unverified), [3, 4])
        keys = uncertified_credit_keys(report, verdicts, unverified, include_awards=True,
                                       selected_qids=None)
        self.assertEqual(keys, {"q21:4"})
        notes = remove_unsupported_credit(report, keys)
        q = report.questions[0]
        self.assertEqual([c.awarded for c in q.criteria], [1, 1, 1, 1, 0])
        self.assertEqual(q.score, 4)
        self.assertEqual(report.total_score, 4)
        self.assertTrue(q.remark.endswith("is not shown in your working."))
        self.assertNotIn(";", q.remark)
        self.assertIn("could not confirm it", notes[0])

    def test_certified_or_unruled_flagged_credit_stays(self):
        report, pages = akshay()
        report.unsupported_credit = find_unsupported_credit(report, pages)
        certified = [AuditItem(row=0, correct=2, evidence_status="verified",
                               step_line_id="P2L9", anchor_quote="z f x & z & 1")]
        _, unverified = grader.audit_applicable_verdicts(report, certified, include_awards=True)
        self.assertEqual(uncertified_credit_keys(report, certified, unverified,
                                                 include_awards=True, selected_qids=None), set())
        # No verdict at all: the check did not run, so the credit is not touched.
        _, unverified = grader.audit_applicable_verdicts(report, [], include_awards=True)
        self.assertEqual(uncertified_credit_keys(report, [], unverified,
                                                 include_awards=True, selected_qids=None), set())


def _maths_package():
    from exam_package import build_exam_package
    scheme = MarksScheme(total=2, items=[MarksItem(qid="Q6", max=2)])
    return scheme, build_exam_package(
        exam_type="school", student_class="12", subject="Maths", level="12", paper="",
        attempt="", applicable_law_date=None, total_marks=2, question_paper=b"paper",
        answer_key=b"key", key_source="upload", marks_scheme=scheme, scheme_source="text",
        answer_key_text="")


class PipelineRemovesUnconfirmedCredit(unittest.TestCase):
    """Akshay Q6 end to end: the fabricated expansion, an uncertain second examiner."""

    def _run(self, status):
        import pipeline
        from costs import record_usage_event
        doc = fitz.open()
        page = doc.new_page(width=1240, height=1755)
        page.insert_text((80, 120), "Q6")
        page.insert_text((80, 300), "|1 x yz| = |1 x xyz| C3 = C2.C3 = xyz.0 = 0")
        source = doc.tobytes()
        doc.close()
        ocr = [PageOCR(1, 1240, 1755, [
            LineBox("P1L1", "Q6", (70, 100, 160, 130), 1, confidence=.99),
            LineBox("P1L2", AKSHAY_P2L9, (70, 200, 1000, 700), 1, confidence=.16)])]
        primary = _report(_q("Q6", 1, [_crit("Determinant expansion and simplification", 2, 2,
                                              "P1L2", "z f x & z & 1", AKSHAY_EVIDENCE)]))
        primary.evidence_contract = False
        scheme, pkg = _maths_package()
        cfg = pipeline.ProviderConfig(provider="claude", model="claude-haiku-4-5",
                                      google_token="test", task_id="test", exam_type="school",
                                      grading_contract="exam_package_v2", exam_package=pkg)
        seen = {}

        def grade(**kwargs):
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                               "input_tokens": 100, "output_tokens": 100}, stage="grading")
            return primary.model_copy(deep=True)

        def audit(report, **kwargs):
            seen["flags"] = dict(report.unsupported_credit)
            record_usage_event(kwargs["usage_out"], {"provider": "claude", "model": kwargs["model"],
                               "input_tokens": 100, "output_tokens": 100}, stage="verification")
            return [AuditItem(row=0, correct=2, evidence_status=status, step_line_id="P1L2",
                              anchor_quote="z f x & z & 1")]

        # No OCR re-read: the stub page is sparse and a second read is a network call.
        # Any request that still tried to leave the machine is refused and counted.
        with patch("pw_access._http_post", side_effect=ConnectionError("offline")) as post, \
                patch("pw_access._http_put", side_effect=ConnectionError("offline")), \
                patch.object(pipeline, "ocr_all_pages", return_value=ocr), \
                patch.object(pipeline, "_reread_sideways_pages", return_value={}), \
                patch.object(pipeline, "claude_grade", side_effect=grade), \
                patch.object(pipeline, "claude_audit", side_effect=audit):
            result = pipeline.grade_sheet(
                qp_pngs=[], sa_bytes=source, filename="akshay.pdf", ak_pngs=[],
                ak_text="Q6: xy² - xz² - x²y + yz² + x²z - y²z = (x-y)(y-z)(z-x). 2 marks.",
                marks_scheme=scheme, cfg=cfg, student_class="12", subject="Maths",
                total_marks=2, use_mathpix=True,
                qp_text="Maximum Marks: 2\nQ6. Expand the determinant. (2 Marks)")
        self.assertEqual(post.call_count, 0)
        return result, seen

    def test_uncertain_examiner_removes_the_unsupported_credit(self):
        result, seen = self._run("uncertain")
        self.assertIn("q6:0", seen["flags"])
        q6 = result["report"].questions[0]
        self.assertEqual(q6.score, 0)
        self.assertEqual(result["report"].total_score, 0)
        self.assertTrue(any(issue.code == "not-certified" and "could not confirm it" in issue.detail
                            for issue in result["issues"]))

    def test_certifying_examiner_keeps_the_credit(self):
        result, seen = self._run("verified")
        self.assertIn("q6:0", seen["flags"])
        self.assertEqual(result["report"].questions[0].score, 2)


class CalibrationRules(unittest.TestCase):
    """The reviewers' calibration, as general rules in the prompt of the right subject."""

    RIGOR = "6. **A conclusion that rests on a wrong step earns nothing for the conclusion"
    REASON = "11. **One mark reasons are judged on the reason the key gives.**"
    LONG = "12. **Long literature answers are marked against what the QUESTION asks.**"

    def test_maths_and_chemistry_mark_a_verdict_on_a_wrong_step_at_zero(self):
        # Maths 6 Sept: Rishita Q25 3/3 with a wrong RHD, Akshay Q15 1.5 after RHL = 1,
        # x^x differentiated by the power rule paid 3/3 on one copy and 1.5 on another.
        for subject in ("Mathematics", "Chemistry"):
            for recovery in (False, True):
                prompt = grader.build_system_prompt(subject, "Class 12", "school",
                                                    recovery=recovery)
                self.assertIn(self.RIGOR, prompt)
                self.assertIn("never one lump criterion", prompt)
                self.assertNotIn(self.LONG, prompt)
        chemistry = grader.build_system_prompt("Chemistry", "Class 12", "school")
        # Aulakh Q12: Cu2Cl2/HCl gives chlorobenzene, not bromobenzene. Aasmeen Q18.
        self.assertIn("a different real reaction (another halogen, another reagent) scores 0",
                      chemistry)
        self.assertIn("a group on the wrong carbon or the wrong branch is a wrong structure, "
                      "worth half at most", chemistry)

    def test_english_marks_reasons_and_long_answers_on_what_the_question_asks(self):
        prompt = grader.build_system_prompt("English", "Class 12", "school")
        self.assertNotIn(self.RIGOR, prompt)
        self.assertIn(self.REASON, prompt)
        # "It is the title of the story" earned 0.5; a correct short reason was cut to 0.5.
        self.assertIn("only restates the question or names the title", prompt)
        self.assertIn("Never halve a correct reason", prompt)
        self.assertIn(self.LONG, prompt)
        self.assertIn("four points (1, 1, 1 and 2", prompt)
        self.assertIn("five points of 2, each a relevant idea with its textual reference",
                      prompt)
        self.assertIn("naming without explaining", prompt)
        # Vantika Q13: about 75 words without the key events landed on the 60% floor.
        self.assertIn("The floor never lifts an answer that leaves out the points the "
                      "question asks for", prompt)

    def test_second_examiner_applies_the_same_rules(self):
        report, _ = hamza()
        prompt = build_audit_prompt(report, answer_key_text="key", ocr_transcript="t",
                                    include_awards=True)
        self.assertIn("LOWER also when a verdict rests on a wrong step", prompt)
        self.assertIn("one piece of writing is paid as the answer to two parts", prompt)
        self.assertIn("the full mark when the key's reason is there in any words", prompt)


if __name__ == "__main__":
    unittest.main()
