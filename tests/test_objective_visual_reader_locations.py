"""Regressions for observed MCQ table headings and statement-value misreads."""
import json
from unittest.mock import patch

import pytest

import grader
from grader import ObjectiveChoiceObservation, map_objective_image_pages


def test_statement_list_remains_complete_answer_value():
    observed = ObjectiveChoiceObservation.model_validate({
        "qid": "Q4", "chosen_option": "(B)(C)(D)", "clear": True,
    })
    assert observed.chosen_option == ""
    assert observed.written_answer == "(B)(C)(D)"
    assert observed.answer_kind == "statement_combination"
    assert observed.clear


def test_statement_kind_does_not_promote_last_statement_to_option():
    observed = ObjectiveChoiceObservation.model_validate({
        "qid": "Q4", "chosen_option": "D", "written_answer": "(B)(C)(D)",
        "answer_kind": "statement_combination", "clear": True,
    })
    assert observed.chosen_option == ""
    assert observed.written_answer == "(B)(C)(D)"


@pytest.mark.parametrize("box", [
    [], [0.1, 0.2, 0.3], [0.2, 0.3, 0.1, 0.4],
    [-0.1, 0.2, 0.3, 0.4], [0.1, 0.2, float("nan"), 0.4],
    [False, 0.2, 0.3, 0.4], "0.1,0.2,0.3,0.4",
])
def test_bad_geometry_never_becomes_a_verified_location(box):
    observed = ObjectiveChoiceObservation.model_validate({
        "qid": "Q1", "chosen_option": "A", "clear": True,
        "answer_bbox": box, "location_clear": True,
    })
    assert observed.clear  # choice reading and location are separate contracts
    assert not observed.location_clear
    assert observed.answer_bbox == []


def test_subset_page_mapping_preserves_actual_box_and_original_read():
    original = ObjectiveChoiceObservation(
        qid="Q1", chosen_option="A", page=2, clear=True,
        answer_bbox=[0.1, 0.73, 0.24, 0.77], location_clear=True,
    )
    mapped = map_objective_image_pages([original], [3, 7])[0]
    assert mapped.page == 7
    assert mapped.answer_bbox == original.answer_bbox
    assert mapped.location_clear
    assert original.page == 2
    invalid = map_objective_image_pages([original], [3])[0]
    assert not invalid.clear and not invalid.location_clear
    assert invalid.answer_bbox == []


def test_reader_retains_two_actual_table_cells_without_invented_spacing():
    payload = {"items": [
        {"qid": "Q1", "chosen_option": "A", "clear": True, "page": 1,
         "answer_bbox": [0.04, 0.14, 0.25, 0.18], "location_clear": True},
        {"qid": "Q2", "chosen_option": "B", "clear": True, "page": 1,
         "answer_bbox": [0.04, 0.31, 0.25, 0.35], "location_clear": True},
    ]}
    response = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    with patch.object(grader, "_claude_call", return_value=response) as call:
        observed = grader.read_objective_choices(
            [b"png"], ["Q1", "Q2"], google_token="test", task_id="test",
            options={"Q1": {"A": "One", "B": "Two"}},
        )
    assert [item.answer_bbox for item in observed] == [
        [0.04, 0.14, 0.25, 0.18], [0.04, 0.31, 0.25, 0.35],
    ]
    assert call.call_count == 1
