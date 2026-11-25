"""Parishram Humanities Class 12, Infinity Test 10 (20 Sept 2026), graded live on
8 Oct 2026 against the faculty's own marks.

- Political Science: "Answer. 1" on its own line over "(B) Jawaharlal Nehru" was read
  as option 1 = A through the answer map's label line (read with no question number),
  so Q1 to Q3 lost three correct answers.
- Geography: "(D) Roca - Western Europe" was OCR'd "(1)", the two readers split D and
  A, and the student's correct answer, written out in the option's own words, got 0.
- History, Political Science, Sociology and English: plain correct long answers were
  paid in full where the faculty paid 50 to 70 percent (Vidyapeeth category rule).
"""
import pytest  # noqa: F401 - tools/selftest.py runs a file under pytest when it imports pytest

import categories
import grader
from strict_grading import _objective_choice_from_line
from test_objective_active_corrections import grade, observation


def test_a_bare_answer_heading_is_never_an_option_without_its_number():
    for label in ("Answer. 1", "Answer - 2", "Answer-3", "Answer -4", "Ans 1", "Q 3"):
        assert _objective_choice_from_line(label, None) == ""
    # Written options still read, with or without the question number.
    assert _objective_choice_from_line("(B) Jawaharlal Nehru", None) == "B"
    assert _objective_choice_from_line("6) (3) is negative", None) == "C"


def test_the_options_own_words_settle_a_letter_the_readers_split_on():
    split = observation(chosen_option="A", written_answer="(A)", cancelled_answer="",
                        selection_state="single_active")
    report = grade(split, primary="B", line_text="1) (1) The campaign against sati",
                   confidence=.6, allow_unconfirmed=True)
    q = report.questions[0]
    assert (q.chosen_option, q.score) == ("B", 1)
    # Still named for a faculty check: the letter itself was never confirmed.
    assert report.objective_unconfirmed_qids == ["Q1"]


def test_words_that_match_neither_reader_settle_nothing():
    split = observation(chosen_option="D", written_answer="(D)", cancelled_answer="",
                        selection_state="single_active")
    report = grade(split, primary="C", line_text="1) (1) The campaign against sati",
                   confidence=.6, allow_unconfirmed=True)
    q = report.questions[0]
    assert (q.chosen_option, q.score) == ("A", 0)
    assert report.objective_unconfirmed_qids == ["Q1"]


HISTORY_PAPER = """Q.12. Why did historians critically examine travellers' accounts?
                            SECTION - E
Map-Based Question
On the given outline political map of India, locate
and label the following five places:
"""
GEOGRAPHY_PAPER = """6. Question 15 is map based carrying 5 marks. Write solution on Indian map with 5 points.
Q14. Elaborate on the historical classification of Indian towns.
Section - D
Map Based Questions (Q15)
Q15.  On the given outline world map, five features
are indicated by letters (A, B, C, D, E).
"""


def test_map_questions_are_found_with_or_without_a_number():
    from preflight import map_question_numbers
    assert map_question_numbers(HISTORY_PAPER) == {13}
    assert map_question_numbers(GEOGRAPHY_PAPER) == {15}
    assert map_question_numbers("Q3. Explain the importance of mapping.") == set()
    # The old patterns still hold.
    assert map_question_numbers("20. On an outline map of India, locate") == {20}


def test_a_map_answer_credited_in_full_goes_to_a_teacher():
    from types import SimpleNamespace as NS
    from preflight import _check_map_answers
    report = NS(questions=[NS(qid="Q13", attempted=True, objective=False, score=5),
                           NS(qid="Q6", attempted=True, objective=True, score=1)])
    issues = _check_map_answers(report, HISTORY_PAPER)
    assert [i.code for i in issues] == ["map-needs-teacher"]
    assert "Q13" in issues[0].detail and "Q6" not in issues[0].detail


def test_an_orientation_reply_that_reasons_first_still_names_every_page():
    from grader import parse_orientation_reply
    assert parse_orientation_reply("[90, 270, 0]", 3) == [90, 270, 0]
    talkative = ("I'll analyze each image by examining the geometry of the handwriting.\n"
                 "**Image 1:** letter tops point left, so 270.\n"
                 "**Image 2:** tops point right -> 90 degrees\n"
                 "**Image 3:** upright, 0\n**Image 4:** 270\n**Image 5:** rotate 90")
    assert parse_orientation_reply(talkative, 5) == [270, 90, 0, 270, 90]
    # A reply cut off before naming every page still names none (the caller ignores it).
    assert len(parse_orientation_reply("I'll analyze each image. Image 1: 270.", 5)) != 5


def test_parishram_long_answers_are_marked_as_the_faculty_mark_them():
    rules = categories.get("vidyapeeth").prompt.extra_rules
    assert "introduction" in rules and "conclusion" in rules
    assert "Economics or Geography" in rules
    # Fault-free copies go to students (user's decision, 12 Oct 2026).
    assert categories.get("vidyapeeth").review.auto_release
    history = grader.build_system_prompt("History", "Class 12", "school", category="vidyapeeth")
    assert "Parishram faculty" in history
    school = grader.build_system_prompt("History", "Class 12", "school", category="school")
    assert "Parishram faculty" not in school


# --- 9 Oct 2026: what was still wrong after the first round -------------------

def _page(texts, conf):
    from mathpix import LineBox, PageOCR
    return PageOCR(6, 1241, 1754, [LineBox(f"P6L{i}", t, (100, 100 + 60 * i, 1100, 150 + 60 * i), 6,
                                           confidence=conf) for i, t in enumerate(texts, 1)])


# Pages 6 to 11 of the English copy were photographed upside down. This is how the
# upright read of page 6 came back (confidence 0.12, Latin, Devanagari and Cyrillic).
UPSIDE_DOWN_READ = ["कि probocif puro turad we brown wonarys", "का करका का ग्यत 4+09 पम्लाकान",
                    "Arrung word", "गैजक लाग प्रय सम व्यंज्यम्य वानीयन मित्री",
                    "womposo, sub abory fra lorms lowna", "флалогогияно флитаю"]
TURNED_READ = ["12. The only one reason of Tiger King to kill one hundred",
               "tigers was to protect himself and prove the astrology",
               "prediction wrong of him being killed by tiger. The decision",
               "reveals his stubborn nature as well as boastful behaviour"]


def test_an_upside_down_page_is_turned_when_its_text_proves_it():
    from unittest.mock import patch
    import pipeline
    upright, turned = _page(UPSIDE_DOWN_READ, .12), _page(TURNED_READ, .7)
    assert pipeline._script_share(upright) < .8 and pipeline._script_share(turned) == 1.0
    assert pipeline._text_says_upside_down(upright, turned)
    cfg = pipeline.ProviderConfig()
    with patch("grader.orientation_probe", return_value=[0]):     # the probe said upright
        assert pipeline._confirmed_upside_down(b"png", 5, cfg, "copy.pdf", {},
                                               upright=upright, turned=turned)
        # Without the two reads the probe still decides, as before.
        assert not pipeline._confirmed_upside_down(b"png", 5, cfg, "copy.pdf", {})


def test_noise_on_the_turned_side_never_turns_a_page():
    import pipeline
    # The mat4 page of 2026-10-01: a short upright answer, gibberish when turned.
    upright = _page(["Q16 x = 6", "so n = 3"], .9)
    gibberish = _page(["1=6+n", "τ(ε+c)", "です", "k(ε) ν"], .98)
    assert not pipeline._text_says_upside_down(upright, gibberish)
    # A clean upright page is never turned, whatever the turned read says.
    assert not pipeline._text_says_upside_down(_page(TURNED_READ, .7), _page(TURNED_READ, .9))


def test_a_note_describing_quoted_work_is_not_unsupported_credit():
    from types import SimpleNamespace as NS
    from mathpix import LineBox, PageOCR
    from grading_quality import find_unsupported_credit
    from grader import Criterion, GradeReport, QuestionMark
    page = PageOCR(8, 1241, 1754, [
        LineBox("P8L8", "(1) Amient Toums - These are those tourns that", (60, 100, 900, 150), 8),
        LineBox("P8L9", "Mounished during 2000 years ago", (60, 160, 900, 210), 8)])
    def report(evidence):
        c = Criterion(description="Ancient towns", awarded=1, max=1, met=True, step_line_id="P8L8",
                      anchor_quote="Ancient Towns - These are those towns that flourished",
                      evidence=evidence)
        return GradeReport(total_score=1, max_total=1, overall_remarks="", evidence_contract=True,
                           questions=[QuestionMark(qid="Q14", score=1, max_score=1, page=8,
                                                   y_fraction=.1, anchor_line_id="P8L8",
                                                   criteria=[c])])
    described = "Student defines ancient towns as those flourishing 2000 years ago, with examples."
    assert find_unsupported_credit(report(described), [page]) == {}
    # A figure the page does not have, or written working, is still a claim to check.
    assert find_unsupported_credit(report("Student says they flourished 3500 years ago."), [page])
    assert find_unsupported_credit(report("Δ = xy² - xz² - y + yz²"), [page])


def test_an_arrow_written_as_latex_matches_the_students_arrow():
    from anchors import _quote_score
    assert _quote_score("B → Praiwes", r"\( B \rightarrow \) Praiwes") > 0


def test_english_long_answers_are_calibrated_for_both_examiners():
    from grader import GradeReport, QuestionMark
    rules = categories.get("vidyapeeth").prompt.extra_rules
    for anchor in ("plain English literature answer: 2.5", "plain English answer 3.5 of 5",
                   "no hard historical facts: 2 to", "without comparing them"):
        assert anchor in " ".join(rules.split()), anchor
    english = grader.build_system_prompt("English", "Class 12", "school", category="vidyapeeth")
    assert "plain English answer 3.5 of 5" in " ".join(english.split())
    report = GradeReport(total_score=2.5, max_total=4, overall_remarks="", questions=[
        QuestionMark(qid="Q10", score=2.5, max_score=4, page=1, y_fraction=.1)])
    audit = grader.build_audit_prompt(report, "key", "transcript", category_rules=rules)
    assert "raises a mark under these rules only for a point that IS on" in " ".join(audit.split())


def test_a_vidyapeeth_copy_gets_a_written_remark_with_a_presentation_sentence():
    from unittest.mock import patch
    from grader import GradeReport, QuestionMark
    report = GradeReport(total_score=6, max_total=10, overall_remarks="", questions=[
        QuestionMark(qid="Q13", score=3, max_score=6, page=1, y_fraction=.1,
                     remark="Explain each consequence and add a conclusion.")])
    sent = {}

    def fake_call(token, **kwargs):
        sent.update(kwargs)
        return {}
    with patch.object(grader, "_claude_call", fake_call), \
            patch.object(grader, "_claude_text", lambda response: "You know the causes well."), \
            patch.object(grader, "_claude_usage", lambda response, model: {}), \
            patch.object(grader, "record_usage_event", lambda *a, **k: None):
        text = grader._compose_overall_remarks(report, model="m", google_token="", task_id="t",
                                               filename="f", subject="Sociology", usage={},
                                               presentation=True)
    message = sent["request"]["messages"][0]["content"][0]["text"]
    assert "introduction and a conclusion" in message and "CA, CMA or CS" not in message
    assert text.startswith("You know the causes well")
