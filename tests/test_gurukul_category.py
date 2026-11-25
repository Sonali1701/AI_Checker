# -*- coding: utf-8 -*-
"""The Gurukulam School (PW Gurukul) diagnostic tests, Grades 1 to 8 (20 Nov 2026)."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import categories  # noqa: E402
import grader  # noqa: E402


def test_the_operator_picks_gurukul_and_it_is_never_guessed():
    for typed in ("gurukul", "Gurukulam", "The Gurukulam School", "PW Gurukul",
                  "The Gurukulam School (PW Gurukul)"):
        assert categories.get(typed).id == "gurukul"
    assert categories.resolve(category="gurukul", student_class="Grade 5",
                              subject="English").source == "explicit"
    # The same class and subject typed without the category stay School board.
    assert categories.resolve(student_class="Grade 5", subject="English").id == "school"
    assert categories.get("gurukul").detection.auto_detectable is False


def test_gurukul_is_a_school_category_with_its_own_rules():
    g, s = categories.get("gurukul"), categories.get("school")
    assert (g.department, g.legacy_exam_type, g.pipeline) == ("school", "school", "written")
    assert g.choice == s.choice and g.render == s.render and g.cost == s.cost
    assert g.intake == s.intake and g.examiner == s.examiner
    assert "Gurukulam" in g.prompt.extra_rules


def test_the_rules_reach_the_grader_and_no_other_category():
    gurukul = grader.build_system_prompt("English", "Grade 5", "school", category="gurukul")
    school = grader.build_system_prompt("English", "Grade 5", "school", category="school")
    for rule in ("starts again at 1 in every section", '"babys", "leafs", "tomatos"',
                 "[teacher to check: the key", "प्रत्येक सही शब्द के लिए ½ अंक",
                 # Pilot 21 Nov 2026: "sheep" read as "sheen" lost its mark, and
                 # "Give examples" went unmarked on a 2/2 answer with no example.
                 '"sheep" reads as "sheen"', "Give examples",
                 "never only a letter",
                 # 23 Nov 2026 samples: E09 lost A2 for a struck-out "baby" before
                 # "babies", and E05's note quoted a struck-out word as "mano".
                 "struck out"):
        assert rule in gurukul
        assert rule not in school
    assert school == grader.build_system_prompt("English", "Grade 5", "school")


def test_copies_go_to_teacher_review_and_a_missing_key_is_written():
    g = categories.get("gurukul")
    assert g.review.auto_release is False and "teacher review" in g.review.review_reason
    assert g.answer_key.may_generate and "Gurukulam" in g.answer_key.examination


def test_the_form_offers_gurukul():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "static", "index.html"), encoding="utf-8") as fh:
        assert '<option value="gurukul">' in fh.read()
    assert "gurukul" in [c["id"] for c in categories.ui_choices()]


def _report():
    q = NS(qid="Q1", score=0.0, max_score=1.0, remark="The plural of leaf is leaves.",
           objective=True, criteria=[])
    return NS(questions=[q], total_score=15.0, max_total=20.0)


def _cover_request(subject):
    sent = {}

    def call(token, *, task_id, model, request, filename, count):
        sent["text"] = request["messages"][0]["content"][0]["text"]
        return {}

    with patch.object(grader, "_claude_call", side_effect=call), \
            patch.object(grader, "_claude_text", return_value="Well done."), \
            patch.object(grader, "_claude_usage", return_value={}), \
            patch.object(grader, "record_usage_event"):
        grader._compose_overall_remarks(_report(), model="m", google_token="t", task_id="k",
                                        filename="f.pdf", subject=subject, usage={},
                                        diagnostic=True)
    return sent["text"]


def test_the_cover_remark_is_written_for_a_young_child():
    text = _cover_request("English")
    assert "Grades 1 to 8" in text and "headings" not in text
    assert "Do not write the marks or the score" in text


def test_the_cover_names_topics_instead_of_counting():
    # 23 Nov 2026 samples: "the first eight questions correctly" over a wrong Q6,
    # and "plurals that end in -o" printed as "end in minus o".
    text = _cover_request("English")
    assert "Never count questions or answers" in text
    assert "never with a hyphen such as -o or -es" in text


def test_a_five_mark_section_of_options_is_marked_item_by_item():
    # 23 Nov 2026 samples: English Section B (five 1-mark "circle the correct plural"
    # items) was typed one objective question with answer (b); every copy that wrote
    # the words scored 0/5 "Correct answer is (B).".
    from exam_package import _question_nodes
    row = NS(qid="QB", max=5.0, description="Circle the correct plural form.",
             question_type="objective", correct_answer="B")
    one = NS(qid="Q1", max=1.0, description="", question_type="objective", correct_answer="C")
    nodes = {n.qid: n for n in _question_nodes(NS(items=[row, one]))}
    assert nodes["QB"].question_type != "objective" and not nodes["QB"].objective_answer
    assert nodes["Q1"].question_type == "objective" and nodes["Q1"].objective_answer == "C"


def test_the_cover_prints_no_markdown_and_no_doubled_full_stop():
    # 23 Nov 2026 samples: "**motion and rest**" and 'the tricky ones like "feet.".'
    assert "**" not in grader.humanise_remark("Practise **motion and rest** with examples.")
    cleaned = grader.humanise_remark('Practise plurals. The tricky ones are "boxes" and "feet."')
    assert not cleaned.endswith('.".') and cleaned.endswith('feet."')
    assert grader.humanise_remark("Plural is deer. Well done!").endswith("done!")


def test_the_cover_language_follows_the_subject():
    # Pilot 21 Nov 2026: English and Science covers came back in Hindi.
    for subject in ("English", "Science", "Social Science", "Mathematics"):
        text = _cover_request(subject)
        assert "simple English only" in text and "Hindi" not in text, subject
    for subject in ("Hindi", "हिंदी"):
        assert "simple Hindi in Devanagari script only" in _cover_request(subject), subject


def test_every_gurukul_paper_is_marked_on_steps_set_from_its_key():
    # Pilot C01 (21 and 23 Nov 2026): "Give examples" was left out of the per-copy
    # steps twice and an answer with no example got 2/2.
    import key_rubric
    for subject in ("Science", "English", "Hindi", "Social Science", "Mathematics"):
        assert key_rubric.key_rubric_applies(NS(exam_type="school", category="gurukul"), subject)
    assert not key_rubric.key_rubric_applies(NS(exam_type="school", category="school"), "Science")
    assert key_rubric.rubric_flavor(NS(exam_type="school", category="gurukul")) == "gurukul"


def test_the_gurukul_scheme_prompt_names_its_own_paper_and_leaves_vidyapeeth_alone():
    import key_rubric
    school = key_rubric.SCHOOL_KEY_RUBRIC_SYSTEM_PROMPT
    gurukul = key_rubric._gurukul_system(school)
    assert key_rubric._SCHOOL_PAPER in school          # the replace still has a target
    assert "Vidyapeeth" not in gurukul and "Gurukulam" in gurukul
    assert "an example of rest" in gurukul and "one step of 1 mark per item" in gurukul
    assert key_rubric.SCHOOL_KEY_RUBRIC_SYSTEM_PROMPT == school

    sent = {}

    def call(token, *, task_id, model, filename, count, request):
        sent["system"] = request["system"]
        raise RuntimeError("stop here")

    from exam_package import ExamPackage  # noqa: F401 - the package type the reader takes
    package = NS(subject="Science", questions=[
        NS(qid="Q11", max_marks=2.0, prompt_text="What is meant by rest and motion?",
           scorable=True, question_type="short", criteria=[])])
    for flavor, wanted in (("gurukul", "Gurukulam"), ("vidyapeeth", "Vidyapeeth")):
        with patch.object(grader, "_claude_call", side_effect=call), \
                patch.object(key_rubric, "_key_pngs", return_value=[]):
            try:
                key_rubric.read_key_rubric(package, b"%PDF", "key text", google_token="t",
                                           task_id="k", flavor=flavor, category_rules="r")
            except RuntimeError:
                pass
        assert wanted in sent["system"], flavor


def _full_marks_report():
    q = NS(qid="Q11", score=2.0, max_score=2.0, remark="", objective=False,
           criteria=[NS(description="Meaning of rest", awarded=1.0, max=1.0),
                     NS(description="An example of motion", awarded=1.0, max=1.0)])
    return NS(questions=[q], total_score=20.0, max_total=20.0)


def test_the_cover_praises_only_what_the_questions_show():
    # Pilot C01 (23 Nov 2026): told only the lost marks, the cover of a Motion and
    # Time test praised "what living things need to grow".
    sent = {}

    def call(token, *, task_id, model, request, filename, count):
        sent["text"] = request["messages"][0]["content"][0]["text"]
        return {}

    with patch.object(grader, "_claude_call", side_effect=call), \
            patch.object(grader, "_claude_text", return_value="Well done."), \
            patch.object(grader, "_claude_usage", return_value={}), \
            patch.object(grader, "record_usage_event"):
        cover = grader._compose_overall_remarks(
            _full_marks_report(), model="m", google_token="t", task_id="k",
            filename="f.pdf", subject="Science", usage={}, diagnostic=True)
    # A 20/20 child gets a cover too, written from every question's earned steps.
    assert cover.startswith("Well done")
    assert "earned: Meaning of rest; An example of motion" in sent["text"]
    assert "Every mark was earned" in sent["text"]


def test_a_full_mark_cover_falls_back_to_praise_in_the_papers_language():
    with patch.object(grader, "_claude_call", side_effect=RuntimeError("offline")):
        english = grader._compose_overall_remarks(
            _full_marks_report(), model="m", google_token="t", task_id="k",
            filename="f.pdf", subject="Science", usage={}, diagnostic=True)
        hindi = grader._compose_overall_remarks(
            _full_marks_report(), model="m", google_token="t", task_id="k",
            filename="f.pdf", subject="Hindi", usage={}, diagnostic=True)
    assert english.startswith("Well done") and "सही" in hindi
    # Other school papers still get no cover when nothing was lost.
    assert grader._compose_overall_remarks(
        _full_marks_report(), model="m", google_token="t", task_id="k",
        filename="f.pdf", subject="Science", usage={}) == ""
