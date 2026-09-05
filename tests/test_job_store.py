# -*- coding: utf-8 -*-
"""Durable job snapshots survive restart without replaying ambiguous calls."""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from job_store import JobRepository

FAILS = []


def check(name, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" +
          (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILS.append(name)


with tempfile.TemporaryDirectory() as directory:
    path = os.path.join(directory, "jobs.sqlite3")
    repo = JobRepository(path)
    running = {
        "status": "running", "state": "primary_grading", "current": "a.pdf",
        "created": 1, "key": "secret", "results": [], "pdfs": {0: b"%PDF fake"},
    }
    repo.save("job-running", running)
    recovered = JobRepository(path).load(max_age_seconds=3600)
    job = recovered["job-running"]
    check("artifact survives a repository reopen", job["pdfs"][0].startswith(b"%PDF"))
    check("download key survives a repository reopen", job["key"] == "secret")
    check("in-flight provider work is not replayed after restart",
          job["status"] == "error" and job["state"] == "failed", job)
    check("ambiguous billing is named", "not replayed" in job["error"], job["error"])

    done = dict(running, status="done", state="released", current="",
                results=[{"index": 0, "release": True}])
    repo.save("job-done", done)
    check("completed state survives", JobRepository(path).load()["job-done"]["state"]
          == "released")
    repo.delete("job-done")
    check("eviction deletes durable state", "job-done" not in JobRepository(path).load())

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
