"""Benchmark production releases against independently marked, question-level truth.

Offline: python tools/benchmark_quality.py dataset.json --results benchmark_results
Live (billable): add --run --token-file token.txt --server http://127.0.0.1:8000
No legacy grading path and no automatic replay of a submitted grading job.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exam_package import normalize_qid


def compare_copy(truth, record):
    released = record.get("release") is True
    report = record.get("report") or {}
    result = {"id": truth["id"], "subject": truth["subject"], "released": released,
              "production_contract": bool(report.get("evidence_contract")),
              "errors": [], "deltas": [], "false_blanks": 0,
              "missed_questions": 0, "unmarked_answers": 0,
              "placement_checked": 0, "placement_errors": 0}
    if not released:
        return result  # Holds count in coverage, never disappear from the denominator.
    if not result["production_contract"]:
        result["errors"].append("Released report did not use the production evidence contract")
    expected = {normalize_qid(q["qid"]): q for q in truth["questions"]}
    actual_rows = report.get("questions", [])
    actual = {normalize_qid(q["qid"]): q for q in actual_rows}
    if len(actual) != len(actual_rows):
        result["errors"].append("Duplicate question IDs")
    if abs(float(report.get("max_total", 0)) - float(truth["total_marks"])) > 1e-6:
        result["errors"].append("Wrong official total")
    for qid, question in expected.items():
        ai = actual.get(qid)
        if ai is None:
            result["missed_questions"] += 1
            continue
        if abs(float(ai["max_score"]) - float(question["max_score"])) > 1e-6:
            result["errors"].append(f"{qid}: wrong maximum")
        result["deltas"].append(float(ai["score"]) - float(question["score"]))
        if question.get("attempted", True) and not ai.get("attempted", True):
            result["false_blanks"] += 1
        marks = [m for m in report.get("render_manifest", [])
                 if normalize_qid(m["qid"]) == qid and m["role"] in {"answer", "step"}]
        if question.get("attempted", True) and not marks:
            result["unmarked_answers"] += 1
        # Human regions are normalized to the ORIGINAL page, excluding added strips.
        # Match each expectation once; a single mark cannot satisfy several steps.
        used = set()
        for anchor in question.get("mark_regions", []):
            result["placement_checked"] += 1
            found = None
            for index, mark in enumerate(marks):
                if index in used or mark["page"] != anchor["page"]:
                    continue
                box = mark.get("mark_normalized")
                if not box:
                    continue
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                x0, y0, x1, y1 = anchor["bbox"]
                if x0 <= cx <= x1 and y0 <= cy <= y1:
                    found = index
                    break
            if found is None:
                result["placement_errors"] += 1
            else:
                used.add(found)
    unexpected = set(actual) - set(expected)
    if unexpected:
        result["errors"].append("Unexpected questions: " + ", ".join(sorted(unexpected)))
    result["total_delta"] = float(report.get("total_score", 0)) - sum(float(q["score"]) for q in expected.values())
    result["question_scores"] = {qid: float(q["score"]) for qid, q in actual.items()}
    return result


def summarize(rows):
    released = [row for row in rows if row["released"]]
    deltas = [delta for row in released for delta in row["deltas"]]
    repeats = defaultdict(list)
    for row in released:
        for qid, score in row.get("question_scores", {}).items():
            repeats[(row["id"], qid)].append(score)
    ranges = [max(values) - min(values) for values in repeats.values() if len(values) > 1]
    return {
        "copies": len(rows), "released": len(released),
        "withheld_rate": (len(rows) - len(released)) / len(rows) if rows else None,
        "question_mae": sum(abs(d) for d in deltas) / len(deltas) if deltas else None,
        "question_bias": sum(deltas) / len(deltas) if deltas else None,
        "question_exact_agreement": sum(abs(d) < 1e-6 for d in deltas) / len(deltas) if deltas else None,
        "max_repeat_question_range": max(ranges) if ranges else None,
        **{key: sum(row[key] for row in rows) for key in
           ("false_blanks", "missed_questions", "unmarked_answers", "placement_checked", "placement_errors")},
        "structural_errors": sum(len(row["errors"]) for row in rows),
    }


def run_copy(case, root, server, token):
    import requests
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    def request(method, path, **kwargs):
        response = session.request(method, server.rstrip("/") + path, timeout=480, **kwargs)
        if not response.ok:
            raise RuntimeError(f"{path}: HTTP {response.status_code}")
        return response.json()
    common = {"student_class": case["student_class"], "subject": case["subject"],
              "total_marks": str(case["total_marks"]), "provider": "claude", "model": ""}
    qp, key, student = [root / case[name] for name in ("question_paper", "answer_key", "student")]
    with qp.open("rb") as fh:
        detected = request("POST", "/api/detect-marks", data={**common, "mode": "auto"},
                           files={"question_paper": (qp.name, fh, "application/pdf")})
    with ExitStack() as stack:
        files = [(name, (path.name, stack.enter_context(path.open("rb")), "application/pdf"))
                 for name, path in (("question_paper", qp), ("answer_key", key), ("answer_sheets", student))]
        form = {**common, "key_source": "upload", "marks_items": json.dumps(detected.get("items", [])),
                "marks_source": detected.get("method", ""),
                "marks_attestation": detected.get("marks_attestation", "")}
        if case.get("step_rubric"):
            form["step_rubric"] = (root / case["step_rubric"]).read_text(encoding="utf-8")
        started = request("POST", "/api/evaluate", data=form, files=files)
    path = f"/api/jobs/{started['job_id']}"
    params = {"k": started["download_key"]}
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        status = request("GET", path, params=params)
        if status.get("status") in {"done", "error"}:
            break
        time.sleep(3)
    else:
        raise TimeoutError("Production job did not complete within 30 minutes; it was not resubmitted.")
    result = next(iter(status.get("results") or []), {})
    if result.get("release") is not True:
        return {"release": False, "state": status.get("state"), "held_reasons": result.get("held_reasons", [])}
    saved = request("GET", path + f"/report/{result['index']}", params=params)
    return {"release": True, "report": saved["report"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Submit billable jobs to the production API")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-question-mae", type=float, default=0.5)
    parser.add_argument("--max-withheld-rate", type=float, default=0.05)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if not dataset.get("copies"):
        parser.error("Dataset requires a non-empty copies array")
    if args.run and not args.token_file:
        parser.error("--run requires --token-file")
    token = args.token_file.read_text(encoding="utf-8").strip() if args.run else ""
    args.results.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in dataset["copies"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case["id"]):
            parser.error("Copy IDs may contain letters, numbers, underscores and hyphens only")
        for repeat in range(1, args.repeats + 1):
            artifact = args.results / f"{case['id']}.{repeat}.json"
            if args.run and not artifact.exists():
                record = run_copy(case, args.dataset.parent, args.server, token)
                artifact.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            if not artifact.exists():
                parser.error(f"Missing production result: {artifact}")
            rows.append(compare_copy(case, json.loads(artifact.read_text(encoding="utf-8"))))
    summary = summarize(rows)
    summary["by_subject"] = {subject: summarize([r for r in rows if r["subject"] == subject])
                             for subject in sorted({r["subject"] for r in rows})}
    (args.results / "summary.json").write_text(json.dumps({"summary": summary, "copies": rows}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    failed = (summary["question_mae"] is None or summary["question_mae"] > args.max_question_mae
              or summary["withheld_rate"] > args.max_withheld_rate
              or any(summary[key] for key in ("structural_errors", "false_blanks", "missed_questions",
                                               "unmarked_answers", "placement_errors")))
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
