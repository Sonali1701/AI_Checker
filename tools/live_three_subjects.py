"""Exercise the real HTTP grading flow for the three supplied school papers.

The Google credential is read only from PW_GOOGLE_TOKEN.  It is never written to
disk, placed in a URL, or printed.  Outputs are evidence artifacts for manual review.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests


DOWNLOADS = Path(r"C:\Users\vs510\Downloads")
OUT = Path(os.environ.get("AICHECKER_OUT_DIR")
           or Path(__file__).resolve().parents[1] / "e2e_live_20260903" / "results")
BASE_URL = os.environ.get("AICHECKER_BASE_URL", "http://127.0.0.1:8769").rstrip("/")

CASES = [
    {
        "slug": "economics",
        "subject": "Economics",
        "student_class": "Class 11 Commerce",
        "total": 25,
        "question": "Unit Test - 01_Economics_Uday PRO 2027 Class 11th Commerce_Priyanka Ma'am_Surya_Question.pdf",
        "key": "Unit Test - 01_Economics_Uday PRO 2027 Class 11th Commerce_Priyanka Ma'am_Surya_Sol.pdf",
        "student": "2e358124-18dd-4bf5-a496-7d79b0676d96.pdf",
    },
    {
        "slug": "business_studies",
        "subject": "Business Studies",
        "student_class": "Class 11 Commerce",
        "total": 25,
        "question": "Commerce_Unit Test - 01_BST_QP.pdf",
        "key": "Commerce_Unit Test - 01_BST_Solution.pdf",
        "student": "ee6a9b24-87bf-4c3f-aa74-fca37bbf5b5a.pdf",
    },
    {
        "slug": "core_maths",
        "subject": "Core Maths",
        "student_class": "Class 11 Commerce",
        "total": 25,
        "question": "MTS UDAY Pro CORE.pdf",
        "key": "MTS UDAY Pro CORE H&S solution.pdf",
        "student": "22a19b59-3055-4732-be25-5f673b28c4f4.pdf",
    },
]


def _request_json(method: str, path: str, *, headers: dict, timeout: int, **kwargs):
    response = requests.request(
        method, f"{BASE_URL}{path}", headers=headers, timeout=timeout, **kwargs
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text[:500]}
    if not response.ok:
        raise RuntimeError(f"{path} returned {response.status_code}: {payload}")
    return payload


def _common(case: dict) -> dict[str, str]:
    return {
        "provider": "gemini",
        "model": "",
        "student_class": case["student_class"],
        "exam_level": case["student_class"],
        "subject": case["subject"],
        "exam_type": "school",
        "total_marks": str(case["total"]),
    }


def run_case(case: dict, headers: dict[str, str]) -> None:
    case_dir = OUT / case["slug"]
    case_dir.mkdir(parents=True, exist_ok=True)
    question = DOWNLOADS / case["question"]
    key = DOWNLOADS / case["key"]
    student = DOWNLOADS / case["student"]
    for path in (question, key, student):
        if not path.is_file():
            raise FileNotFoundError(path)

    print(f"[{case['slug']}] detecting and independently verifying marks...", flush=True)
    with question.open("rb") as question_fh:
        detected = _request_json(
            "POST",
            "/api/detect-marks",
            headers=headers,
            timeout=480,
            data={**_common(case), "mode": "ai"},
            files={"question_paper": (question.name, question_fh, "application/pdf")},
        )
    (case_dir / "detect.json").write_text(
        json.dumps(detected, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    items = detected.get("items") or []
    if not items:
        raise RuntimeError(f"{case['slug']}: no question/maxima rows were detected")
    print(
        f"[{case['slug']}] marks={detected.get('method')} rows={len(items)} "
        f"row_sum={detected.get('total')} effective={detected.get('effective_total')}",
        flush=True,
    )

    print(f"[{case['slug']}] starting strict grading job...", flush=True)
    with question.open("rb") as question_fh, key.open("rb") as key_fh, student.open("rb") as student_fh:
        files = [
            ("question_paper", (question.name, question_fh, "application/pdf")),
            ("answer_key", (key.name, key_fh, "application/pdf")),
            ("answer_sheets", (student.name, student_fh, "application/pdf")),
        ]
        started = _request_json(
            "POST",
            "/api/evaluate",
            headers=headers,
            timeout=180,
            data={
                **_common(case),
                "key_source": "upload",
                "marks_items": json.dumps(items, separators=(",", ":")),
                "marks_source": str(detected.get("method") or "unknown"),
                "marks_attestation": str(detected.get("marks_attestation") or ""),
            },
            files=files,
        )

    job_id = started["job_id"]
    job_key = started["download_key"]
    deadline = time.monotonic() + 1500
    last_line = None
    while True:
        status = _request_json(
            "GET",
            f"/api/jobs/{job_id}",
            headers=headers,
            # A busy server can take well over 30s to answer a status poll, and this
            # client used to abandon a healthy job on that alone - the run was fine,
            # the poll just timed out. Wait longer than the server's slowest stage.
            timeout=120,
            params={"k": job_key},
        )
        line = (status.get("status"), status.get("state"), status.get("done"), status.get("current"))
        if line != last_line:
            print(f"[{case['slug']}] job={line}", flush=True)
            last_line = line
        if status.get("status") in {"done", "error"}:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{case['slug']}: grading did not finish in 25 minutes")
        time.sleep(2)

    (case_dir / "status.json").write_text(
        json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    results = status.get("results") or []
    if status.get("status") != "done" or len(results) != 1:
        raise RuntimeError(f"{case['slug']}: grading job failed: {status}")
    result = results[0]
    if result.get("release") is not True:
        raise RuntimeError(f"{case['slug']}: result held: {result.get('error')}")
    response = requests.get(
        f"{BASE_URL}/api/jobs/{job_id}/pdf/{result['index']}",
        params={"k": job_key},
        timeout=180,
    )
    if not response.ok or not response.content.startswith(b"%PDF-"):
        raise RuntimeError(
            f"{case['slug']}: evaluated PDF download failed ({response.status_code})"
        )
    output_pdf = case_dir / f"evaluated_{case['slug']}.pdf"
    output_pdf.write_bytes(response.content)
    cost = result.get("cost") or {}
    print(
        f"[{case['slug']}] RELEASED score={result.get('score')}/{result.get('max')} "
        f"cost={cost.get('total_inr')} PDF={output_pdf}",
        flush=True,
    )


def main() -> None:
    token = (os.environ.get("PW_GOOGLE_TOKEN") or "").strip()
    if not token:
        raise SystemExit("PW_GOOGLE_TOKEN is required")
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}"}
    auth = _request_json("GET", "/api/auth/status", headers=headers, timeout=60)
    if not auth.get("allowed"):
        raise SystemExit("The supplied PW identity is not allowed for AI Copy Checker")
    print("Authorization: allowed for AI Copy Checker", flush=True)
    selected = {
        item.strip() for item in os.environ.get("AICHECKER_CASES", "").split(",")
        if item.strip()
    }
    cases = [case for case in CASES if not selected or case["slug"] in selected]
    failures: list[str] = []
    for case in cases:
        try:
            run_case(case, headers)
        except Exception as exc:
            message = f"{case['slug']}: {exc}"
            failures.append(message)
            print(f"[{case['slug']}] FAILED: {exc}", flush=True)
    if failures:
        raise SystemExit("One or more live grading jobs failed: " + " | ".join(failures))
    print("SELECTED LIVE GRADING JOBS RELEASED", flush=True)


if __name__ == "__main__":
    main()
