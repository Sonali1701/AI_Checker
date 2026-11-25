"""Exercise the real HTTP grading flow for the six supplied papers.

Three Class 11 Commerce unit tests and three CA Intermediate master tests.

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
           or Path(__file__).resolve().parents[1] / "live_six" / "results")
BASE_URL = os.environ.get("AICHECKER_BASE_URL", "http://127.0.0.1:8769").rstrip("/")

CASES = [
    {
        "slug": "economics",
        "subject": "Economics",
        "student_class": "Class 11 Commerce",
        "exam_type": "school",
        "total": 25,
        "question": "Unit Test - 01_Economics_Uday PRO 2027 Class 11th Commerce_Priyanka Ma'am_Surya_Question.pdf",
        "key": "Unit Test - 01_Economics_Uday PRO 2027 Class 11th Commerce_Priyanka Ma'am_Surya_Sol.pdf",
        "student": "2e358124-18dd-4bf5-a496-7d79b0676d96.pdf",
    },
    {
        "slug": "business_studies",
        "subject": "Business Studies",
        "student_class": "Class 11 Commerce",
        "exam_type": "school",
        "total": 25,
        "question": "Commerce_Unit Test - 01_BST_QP.pdf",
        "key": "Commerce_Unit Test - 01_BST_Solution.pdf",
        "student": "ee6a9b24-87bf-4c3f-aa74-fca37bbf5b5a.pdf",
    },
    {
        "slug": "core_maths",
        "subject": "Core Maths",
        "student_class": "Class 11 Commerce",
        "exam_type": "school",
        "total": 25,
        "question": "MTS UDAY Pro CORE.pdf",
        "key": "MTS UDAY Pro CORE H&S solution.pdf",
        "student": "22a19b59-3055-4732-be25-5f673b28c4f4.pdf",
    },
    {
        "slug": "ca_financial_management",
        "subject": "Financial Management",
        "student_class": "CA Intermediate",
        "exam_type": "professional",
        "total": 50,
        "question": "Master Test-04_FM SM_CA Inter Udesh Regular Sep 2026 Group-02_26-06-2026_(Questions)_Namrata Madam_Sohel.pdf",
        "key": "Master Test-04_FM SM_CA Inter Udesh Regular Sep 2026 Group-02_26-06-2026_(Solutions)_Namrata Madam_Sohel.pdf",
        "student": "830d07ac-a037-4cb7-9c80-cb1f0cd6758d.pdf",
    },
    {
        "slug": "ca_corporate_laws",
        "subject": "Corporate and Other Laws",
        "student_class": "CA Intermediate",
        "exam_type": "professional",
        "total": 50,
        "question": "Master Test_01_Corporate and Others Laws_CA Intermediate Udesh Regular Sep 2026_Prasad Puranik Sir_Surya Yadav _Question (updaed).pdf",
        "key": "Master Test_01_Corporate and Others Laws_CA Intermediate Udesh Regular Sep 2026_Prasad Puranik Sir_Surya Yadav _Solution.pdf",
        "student": "bf057097-89fd-40ff-9eca-7e3958783253.pdf",
    },
    {
        "slug": "ca_taxation",
        "subject": "Taxation",
        "student_class": "CA Intermediate",
        "exam_type": "professional",
        "total": 100,
        "question": "MTS_-5_Taxation_of_CA_intermediate_UDESH_Regular_SEPT_2026_(Group-1)_Rashika_Ma'am_Rohit_MCQ+Subje_Question .pdf",
        "key": "MTS_-5_Taxation _of_CA_intermediate_UDESH_Regular_SEPT_2026_(Group-1)_Rashika_Ma'am_Rohit_MCQ+Subje_Solution 111 (3).pdf",
        "student": "ea25a99b-9eea-4c1e-a422-a18eb5fc6c5d.pdf",
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
        "provider": "claude",
        "model": "",
        "student_class": case["student_class"],
        "exam_level": case["student_class"],
        "subject": case["subject"],
        "exam_type": case["exam_type"],
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

    # Two providers have to agree on the question/maxima graph before /api/evaluate
    # will start, and on a paper with optional questions ("answer any 2 of the
    # remaining 3") they disagree often enough that one attempt is not enough. The
    # server says so itself on the 409: "Click Detect max marks once more."
    attempts = int(os.environ.get("AICHECKER_DETECT_ATTEMPTS", "4"))
    detected = None
    for attempt in range(1, attempts + 1):
        print(f"[{case['slug']}] detecting and independently verifying marks "
              f"(attempt {attempt}/{attempts})...", flush=True)
        with question.open("rb") as question_fh:
            detected = _request_json(
                "POST",
                "/api/detect-marks",
                headers=headers,
                timeout=480,
                data={**_common(case), "mode": "ai"},
                files={"question_paper": (question.name, question_fh, "application/pdf")},
            )
        method = str(detected.get("method") or "")
        # The attestation token IS the proof: /api/evaluate accepts an AI-derived
        # graph only when the server cached a dual-provider-verified one, and that
        # is exactly when detect-marks hands back a token. "ai_unverified" carries
        # a plausible graph and no proof, so it fails the same 409 as a mismatch.
        if detected.get("items") and str(detected.get("marks_attestation") or ""):
            print(f"[{case['slug']}] verified graph (method={method!r})", flush=True)
            break
        print(f"[{case['slug']}] not independently verified (method={method!r}): "
              f"{detected.get('verification_detail') or 'no detail'}; retrying",
              flush=True)
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
            timeout=900,
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
            timeout=300,
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

    # Save what the copy was drawn FROM, not just the drawing. A renderer fix that
    # lands after a batch is worthless if verifying it means paying to grade the
    # sheet again - with these two files tools/rerender.py redraws it for nothing.
    saved = requests.get(
        f"{BASE_URL}/api/jobs/{job_id}/report/{result['index']}",
        params={"k": job_key},
        timeout=180,
    )
    if saved.ok:
        body = saved.json()
        (case_dir / "report.json").write_text(
            json.dumps(body.get("report") or {}, indent=1, ensure_ascii=False),
            encoding="utf-8")
        (case_dir / "ocr.json").write_text(
            json.dumps(body.get("ocr") or [], ensure_ascii=False), encoding="utf-8")
    else:
        # Not fatal - the evaluated copy is already on disk and is what the batch is
        # for. Say so plainly, because it means the next renderer fix costs money.
        print(f"[{case['slug']}] WARNING: report not saved "
              f"({saved.status_code}); a re-render will need a paid re-grade",
              flush=True)
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
