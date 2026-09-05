"""Regression checks for Claude's compact grading output grammar."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grader import (
    _CODE_OWNED_GRADE_REPORT_FIELDS,
    _grade_report_output_schema,
    parse_grade_report,
)


schema = _grade_report_output_schema()

# Anthropic rejected the old ~16 KB / 12-field grammar as "Schema is too
# complex".  Keep the provider boundary deliberately small.
encoded = json.dumps(schema, separators=(",", ":"))
assert len(encoded) < 4000, len(encoded)
assert set(schema["properties"]) == {
    "total_score", "max_total", "overall_remarks", "questions", "annotations"
}
assert not (_CODE_OWNED_GRADE_REPORT_FIELDS & set(schema["properties"]))
assert not (_CODE_OWNED_GRADE_REPORT_FIELDS & set(schema["required"]))

# Metadata called "description" is removed, but the Criterion field with that
# exact name must survive.  A naive recursive key filter silently deleted it.
criterion = schema["$defs"]["Criterion"]
assert "description" in criterion["properties"]
assert "description" in criterion["required"]

# Anthropic rejects maxItems/maxLength and rejects a 16-property nested map as too
# complex. Strict grading keeps the array compact and restricts qid to the official
# immutable set; reconciliation rejects duplicate or missing rows.
bounded = _grade_report_output_schema(question_qids=["Q1", "Q2", "Q16"])
question_array = bounded["properties"]["questions"]
assert question_array["type"] == "array"
assert bounded["$defs"]["QuestionMark"]["properties"]["qid"]["enum"] == [
    "Q1", "Q2", "Q16",
]
assert "annotations" not in bounded["properties"]
assert set(bounded["properties"]) == {"questions"}
assert "Annotation" not in bounded["$defs"]
assert set(bounded["$defs"]["QuestionMark"]["properties"]) == {
    "qid", "score", "page", "anchor_line_id", "remark", "criteria",
    "attempted", "is_correct", "chosen_option",
}
assert "chosen_option" in bounded["$defs"]["QuestionMark"]["required"]
assert set(bounded["$defs"]["Criterion"]["properties"]) == {
    "description", "awarded", "max", "evidence", "anchor_quote", "step_line_id",
}
# anchor_quote must be REQUIRED, not optional: it is what places the tick, and a
# model that may omit it will. See anchors.py for the measurement that forced it.
assert "anchor_quote" in bounded["$defs"]["Criterion"]["required"]
assert "maxItems" not in json.dumps(bounded)
assert "maxLength" not in json.dumps(bounded)


def metadata_keys(node, *, inside_name_map=False):
    found = []
    if isinstance(node, list):
        for value in node:
            found.extend(metadata_keys(value))
    elif isinstance(node, dict):
        for key, value in node.items():
            if not inside_name_map and key in {"title", "description"}:
                found.append(key)
            found.extend(metadata_keys(
                value, inside_name_map=key in {"properties", "$defs"}
            ))
    return found


assert metadata_keys(schema) == []

# Omitted code-owned fields are populated by Pydantic defaults and are then
# rebuilt by normal reconciliation.
report = parse_grade_report(json.dumps({
    "total_score": 1,
    "max_total": 1,
    "overall_remarks": "Good start.",
    "questions": [{
        "qid": "Q1", "score": 1, "max_score": 1, "page": 1,
        "y_fraction": 0.25, "criteria": [],
    }],
    "annotations": [],
}))
assert report.section_totals == []
assert report.ungraded_count == 0
assert report.false_blank_qids == []
assert report.maxima_rescaled is False

# Provider-only keyed question maps are converted back to the normal list model.
mapped = parse_grade_report(json.dumps({
    "total_score": 1,
    "max_total": 2,
    "overall_remarks": "",
    "questions": {
        "Q1": {"qid": "Q1", "score": 1, "max_score": 1, "page": 1,
               "y_fraction": 0.2, "criteria": []},
        "Q2": {"qid": "Q2", "score": 0, "max_score": 1, "page": 1,
               "y_fraction": 0.4, "criteria": []},
    },
}))
assert [q.qid for q in mapped.questions] == ["Q1", "Q2"]

print("PASS: Claude grading schema is compact and preserves provider fields")
