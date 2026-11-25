# -*- coding: utf-8 -*-
"""Teaching copies reviewed page by page on 22 Oct 2026 (12 exams of the feedback sheet).

What the student saw and should not have: crosses and circled zeros on correct lines
another step was ticked for, continuation pages labelled "Not evaluated", "(1)" points
inside a later answer read as Q1, margin notes with semicolons and English on a Hindi
copy, a Hindi cover that ran out of its box, "है।." on every Hindi remark, "0 4
seconds" for "0–4 seconds", a box where omega should be, "the supplied pages", and a
paper printing one question twice with the repeat marked "not attempted".
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as N

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import answer_map  # noqa: E402
import categories  # noqa: E402
import pdf_renderer  # noqa: E402
import pipeline  # noqa: E402
import teaching_key  # noqa: E402
import wording  # noqa: E402


# --- what the student reads --------------------------------------------------

def test_a_hindi_sentence_ends_with_its_danda_alone():
    assert wording.plain_sentences("साधनावस्था का उल्लेख अनुपस्थित है।") == "साधनावस्था का उल्लेख अनुपस्थित है।"
    assert wording.plain_sentences("पदावली नहीं है।.") == "पदावली नहीं है।"


def test_an_en_dash_range_reads_as_a_range():
    assert wording.plain_sentences("Check v on 0–4 seconds") == "Check v on 0 to 4 seconds."


def test_greek_letters_are_written_out_and_subscripts_spaced():
    assert wording.plain_sentences("Z_eff = Z − σ") == "Z eff = Z minus sigma."
    assert wording.plain_sentences("Mg(L/2)=½Iω²") == "Mg(L/2)=½I omega²."


def test_the_graders_view_of_its_input_is_not_feedback():
    assert wording.plain_sentences("No answer to Q8 appears on the supplied pages.") == "No answer to Q8 appears."


def test_an_unattempted_question_says_not_attempted_in_the_copys_language():
    english = N(questions=[N(qid="Q8", remark="No Q8 response is present.", attempted=False,
                             score=0, max_score=4),
                           N(qid="Q1", remark="Explain each point.", attempted=True, score=3, max_score=4)],
                overall_remarks="")
    wording.plain_report(english)
    assert english.questions[0].remark == "Not attempted."
    hindi = N(questions=[N(qid="Q12", remark="No answer to Q12 appears on the supplied pages.",
                           attempted=False, score=0, max_score=4),
                         N(qid="Q1", remark="उदाहरण नहीं दिया।", attempted=True, score=3, max_score=4)],
              overall_remarks="")
    wording.plain_report(hindi)
    assert hindi.questions[0].remark == "उत्तर नहीं लिखा गया।"


def test_a_margin_note_is_plain_and_in_the_copys_language():
    assert pdf_renderer._margin_reason("Technology contrast is stated; labour missing", "") == \
        "Technology contrast is stated. Labour missing."
    hindi_remark = "खुसरो की भाषा की सरलता नहीं बताई।"
    assert pdf_renderer._margin_reason("Simplicity and concluding assessment are not explained",
                                       hindi_remark) == hindi_remark


def test_the_cover_measures_hindi_as_it_draws_it():
    text = "Q10: आदिकाल, वीरगाथा काल और शुक्ल का स्पष्ट परिचय नहीं है।" * 3
    width = 180.0
    lines = pdf_renderer._rs_wrap_count(text, width, 11.5)
    words, line, drawn = text.split(), "", 1
    for word in words:
        trial = (line + " " + word).strip()
        if pdf_renderer._text_width(trial, 11.5, lambda t: pdf_renderer.fitz.get_text_length(
                t, fontname="helv", fontsize=11.5)) > width:
            drawn += 1
            line = word
        else:
            line = trial
    assert lines == drawn


def test_a_missing_points_zero_goes_under_the_answer():
    end = {"page": 1, "lid": "P1L9", "x0": 40.0, "y0": 500.0, "x1": 400.0, "y1": 520.0}
    below = pdf_renderer._below_answer_end(end, 800.0)
    assert below["lid"] == "P1L9" and below["y0"] > end["y1"]
    assert pdf_renderer._below_answer_end({**end, "y0": 770.0, "y1": 790.0}, 800.0)["y0"] == 770.0


def test_a_page_with_no_mark_says_which_answer_it_continues():
    by_lid = {"P9L1": {"page": 9, "y1": 100.0}, "P9L2": {"page": 9, "y1": 700.0},
              "P10L1": {"page": 10, "y1": 100.0}}
    report = N(answer_regions={"q8": {"qid": "Q8", "status": "located", "line_ids": ["P9L1"], "pages": [9]},
                               "q9": {"qid": "Q9", "status": "located", "line_ids": ["P9L2"], "pages": [9]}},
               questions=[N(qid="Q8", attempted=True), N(qid="Q9", attempted=True)])
    assert pdf_renderer._continued_answer(report, by_lid, 10) == "Q9"
    report.answer_regions["q9"]["line_ids"].append("P10L1")
    report.answer_regions["q9"]["pages"].append(10)
    assert pdf_renderer._continued_answer(report, by_lid, 10) == "Q9"


# --- where an answer is --------------------------------------------------------

def test_numbered_points_restart_and_slip_without_becoming_questions():
    # Geography: four answers' points under one unread heading, and "(2)" for "(4)".
    assert answer_map._point_runs([1, 2, 2, 3, 1, 2, 3, 4, 1, 2]) == set(range(10))
    assert answer_map._point_runs([1, 2, 3, 2]) == {0, 1, 2, 3}
    assert answer_map._point_runs([5]) == set()


def test_a_hindi_margin_note_is_given_room_for_every_line():
    # KVS PGT Economics: a four line Hindi note was given room for one, and the notes
    # on a page printed over each other.
    note = "आपने पूरी प्रतियोगिता में फर्म के बंद होने का बिंदु सही बताया पर स्थिर लागत का उल्लेख कीजिए।"
    assert pdf_renderer._strip_wrap_lines(note, 120.0, 9.0) >= 4


def test_points_inside_a_later_answer_are_points_once_their_question_is_answered():
    # EMRS PGT History Q9: "1. अहिंसा ... 5. ब्रह्मचर्य", then "2. त्रिरत्न ... 5. समानता",
    # mid page, after "Q. 1" to "Q. 8" had each been answered with its own heading.
    from mathpix import LineBox, PageOCR
    texts = [f"Q. {n} answer {n} heading text" for n in range(1, 10)] + [
        "1. अहिंसा मन वचन कर्म से कष्ट न देना", "3. अस्तेय चोरी न करना सदा",
        "4. अपरिग्रह धन संचय न करना", "5. ब्रह्मचर्य इंद्रियों पर नियंत्रण रखना",
        "2. त्रिरत्न मोक्ष प्राप्ति के लिए", "5. समानता जाति व्यवस्था का विरोध"]
    page = PageOCR(1, 1000, 2000, [LineBox(f"P1L{i}", t, (50.0, 40.0 * i, 900.0, 40.0 * i + 30), 1)
                                   for i, t in enumerate(texts, start=1)])
    observations = [(1, i, line.line_id, answer_map._student_label_qid(line.text))
                    for i, line in enumerate(page.lines, start=1)
                    if answer_map._student_label_qid(line.text)]
    points = answer_map._points_under_headings([page], observations)
    assert {f"P1L{i}" for i in range(10, 16)} <= points


# --- the paper -----------------------------------------------------------------

def test_a_paper_that_prints_a_question_twice_holds_its_copies():
    question = lambda qid, text: N(qid=qid, prompt_text=text)  # noqa: E731
    repeat = "Discuss inductive effect, electromeric effect, and hyperconj"
    cfg = N(exam_package=N(questions=[question("Q8", repeat), question("Q15", repeat)]))
    reason = pipeline._repeated_question_hold(cfg)
    assert reason and "Q15 repeats Q8" in reason
    passage = "Rinjha lived with her family in a small village"
    parts = N(exam_package=N(questions=[question("Q1.a", passage), question("Q1.b", passage)]))
    assert pipeline._repeated_question_hold(parts) is None


# --- the marking ------------------------------------------------------------------

def test_the_whole_answer_is_read_and_facts_are_checked():
    rules = " ".join(categories.get("teaching").prompt.extra_rules.split())
    assert "Read the whole answer before calling a point missing" in rules
    assert "The key's examples are examples" in rules
    assert "Facts are checked, in the text and in the diagrams" in rules


def test_the_key_writes_no_steps_the_question_does_not_ask():
    prompt = " ".join(teaching_key.TEACHING_KEY_SYSTEM_PROMPT.split())
    assert "Every step is something the question asks for" in prompt
    assert teaching_key.TEACHING_KEY_VERSION == "2026-10-23.1"
