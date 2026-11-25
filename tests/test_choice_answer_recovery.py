"""Recorded C2 label and phantom C1 alternatives from the live Audit copy."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from answer_map import build_answer_map
from exam_package import normalize_qid, QuestionNode, _official_objective_answers
from grader import Criterion
from mathpix import LineBox, PageOCR
from pipeline import _unlocated_surplus_attempts
from test_nested_choices import make_package, make_report


class ChoiceAnswerRecoveryTests(unittest.TestCase):
    def test_selected_choice_report_is_checked_against_complete_printed_graph(self):
        from preflight import _check_scheme
        package = make_package()
        report = make_report(qids=[q.qid for q in package.questions
                                  if not q.qid.startswith(("Q4.", "Q6.c1."))])
        paper = "\n".join(f"({q.max_marks:g} Marks)" for q in package.questions
                           if not q.qid.startswith("Q1.") or not q.qid[3:].isdigit())
        self.assertTrue(_check_scheme(report, paper, 100))
        self.assertEqual(_check_scheme(report, paper, 100, package), [])
        report.questions[0].max_score += .5
        self.assertTrue(_check_scheme(report, paper, 100, package))

    def test_lettered_opinion_types_and_numbered_examples_remain_inside_answers(self):
        package = make_package()
        labels = ["Q2(a) Earlier answer", "Q2(b) Earlier answer", "Q2(c) Opinions",
                  "a) Qualified", "Supporting explanation", "b) Adverse", "Supporting explanation",
                  "c) Disclaimer", "Q5(b) Detection risk", "Examples of detection risk are.",
                  "1) No inventory inspection", "Continuation", "ii) Insufficient sampling",
                  "Reduce risk by widening testing"]
        pages = [PageOCR(page=1, image_width=1000, image_height=1400, lines=[
            LineBox(line_id=f"P1L{i}", text=t, bbox=(20, i*30, 800, i*30+20), page=1)
            for i, t in enumerate(labels, 1)])]
        mapped = {q.qid: q for q in build_answer_map(package, pages).locations}
        self.assertEqual(mapped["Q2.c"].line_ids, tuple(f"P1L{i}" for i in range(3, 9)))
        self.assertEqual(mapped["Q5.b"].line_ids, tuple(f"P1L{i}" for i in range(9, 15)))
        self.assertIn("P1L4", mapped["Q2.c"].uncertain_line_ids)
        self.assertIn("P1L11", mapped["Q5.b"].uncertain_line_ids)

    def test_part_a_numeric_key_and_part_b_restarted_labels_stay_separate(self):
        paper = "Part-A comprises 15 Multiple Choice Questions (MCQs).\n"
        paper += "\n".join(f"{i}.\nQuestion text" for i in range(1, 16))
        options = [2, 3, 1, 1, 2, 3, 2, 3, 2, 4, 2, 2, 3, 3, 2]
        key = "Answer Key\n" + "\n".join(f"{i}.\n({a})" for i, a in enumerate(options, 1))
        answers = _official_objective_answers(key, paper)
        self.assertEqual(answers, {i: "ABCD"[a-1] for i, a in enumerate(options, 1)})
        self.assertEqual(_official_objective_answers(key, "Numerical exercises"), {})
        package = make_package()
        package.questions = [QuestionNode(qid="Q1", max_marks=2, question_type="objective",
                                         objective_answer="B"),
                             QuestionNode(qid="Q1.a", max_marks=5)]
        texts = [[r"1) \( (3) \)"], ["Q1", "a) Audit procedures", "Explanation"]]
        pages = [PageOCR(page=p, image_width=1000, image_height=1400, lines=[
            LineBox(line_id=f"P{p}L{i}", text=t, bbox=(20, i*30, 800, i*30+20), page=p)
            for i, t in enumerate(lines, 1)]) for p, lines in enumerate(texts, 1)]
        mapped = {q.qid: q for q in build_answer_map(package, pages).locations}
        self.assertEqual(mapped["Q1"].line_ids, ("P1L1",))
        self.assertEqual(mapped["Q1.a"].line_ids, ("P2L2", "P2L3"))

    def test_c2_label_and_digit_between_roman_points_keep_the_whole_answer(self):
        package = make_package()
        labels = ["Q6", "C2) Explain NGO audit", "i) Knowledge", "ii) Board minutes",
                  "iii) Materiality", "iv) Procedures", "7) Relevant statutes", "vi) Accounting systems"]
        pages = [PageOCR(page=17, image_width=1000, image_height=1400, lines=[
            LineBox(line_id=f"P17L{i}", text=t, bbox=(20, i*30, 800, i*30+20), page=17)
            for i, t in enumerate(labels, 1)])]
        mapped = {q.qid: q for q in build_answer_map(package, pages).locations}
        self.assertEqual(mapped["Q6.c2"].status, "located")
        self.assertEqual(mapped["Q6.c2"].line_ids, tuple(f"P17L{i}" for i in range(2, 9)))
        self.assertIn("P17L7", mapped["Q6.c2"].uncertain_line_ids)
        self.assertEqual(mapped["Q6.c1.i"].status, "unresolved")

    def test_unlocated_surplus_claims_are_ordered_by_the_graders_positions_not_re_read(self):
        # An OR alternative the paper leaves unlabelled has no label to locate. The
        # grader's own page and y_fraction order the surplus attempts (2026-09-17:
        # the Accountancy copy bought a Rs.32 re-read of every alternative and was
        # then held). Nothing is assumed blank: every row stays attempted.
        package = make_package()
        report = make_report(qids=["Q6.c1.i", "Q6.c1.ii", "Q6.c2"])
        rows = {normalize_qid(q.qid): q for q in report.questions}
        regions = {normalize_qid("Q6.c2"): {"status": "located", "first_position": [17, 2]}}
        self.assertEqual(_unlocated_surplus_attempts(rows, package, regions), {})
        self.assertTrue(all(q.attempted for q in report.questions))
        # Rows without any usable position still need a fresh read.
        for q in report.questions[:2]:
            q.page = 0
        problems = _unlocated_surplus_attempts(rows, package, regions)
        self.assertEqual(set(problems), {normalize_qid("Q6.c1.i"), normalize_qid("Q6.c1.ii")})
        for q in report.questions[:2]:
            q.attempted = False
            q.score = 0
        self.assertEqual(_unlocated_surplus_attempts(rows, package, regions), {})

    def test_unlabelled_single_attempt_is_not_invented_as_a_surplus(self):
        package = make_package()
        report = make_report(qids=["Q6.c2"])
        rows = {normalize_qid(q.qid): q for q in report.questions}
        self.assertEqual(_unlocated_surplus_attempts(rows, package,
            {normalize_qid("Q6.c2"): {"status": "unresolved"}}), {})


if __name__ == "__main__":
    unittest.main()
