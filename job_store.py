"""Small durable repository for job snapshots and released/held PDF artifacts.

The execution engine still owns live Python objects. This repository stores the
user-visible state after every transition, so completed results survive an EXE restart.
A process that dies while a provider request is in flight is marked failed on recovery;
it is never replayed blindly because the original request may already have been billed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path


def default_data_dir(app_name: str = "AI Copy Checker") -> Path:
    configured = (os.environ.get("AICHECKER_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.environ.get("TEMP") or ".")
    return Path(base) / app_name


class JobRepository:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else default_data_dir() / "jobs.sqlite3"
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        if not self._ready:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT NOT NULL,
                    sheet_index INTEGER NOT NULL,
                    pdf BLOB NOT NULL,
                    PRIMARY KEY (job_id, sheet_index),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                """
            )
            connection.commit()
            self._ready = True
        return connection

    @staticmethod
    def _snapshot(job: dict) -> dict:
        return {
            key: value for key, value in job.items()
            if key != "pdfs" and key not in {"_runtime", "payload"}
        }

    def save(self, job_id: str, job: dict) -> None:
        snapshot = self._snapshot(job)
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False)
        pdfs = dict(job.get("pdfs") or {})
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "INSERT INTO jobs(job_id,snapshot_json,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(job_id) DO UPDATE SET snapshot_json=excluded.snapshot_json, "
                        "updated_at=excluded.updated_at",
                        (job_id, encoded, time.time()),
                    )
                    for index, pdf in pdfs.items():
                        if isinstance(pdf, (bytes, bytearray)):
                            connection.execute(
                                "INSERT INTO artifacts(job_id,sheet_index,pdf) VALUES(?,?,?) "
                                "ON CONFLICT(job_id,sheet_index) DO NOTHING",
                                (job_id, int(index), sqlite3.Binary(bytes(pdf))),
                            )
            finally:
                connection.close()

    def load(self, *, max_age_seconds: float = 3600) -> dict[str, dict]:
        cutoff = time.time() - max(0.0, float(max_age_seconds))
        if not self.path.exists():
            return {}
        recovered: dict[str, dict] = {}
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT job_id,snapshot_json FROM jobs WHERE updated_at>=?",
                    (cutoff,),
                ).fetchall()
                for job_id, encoded in rows:
                    try:
                        job = json.loads(encoded)
                    except (TypeError, ValueError):
                        continue
                    pdfs = {
                        int(index): bytes(pdf) for index, pdf in connection.execute(
                            "SELECT sheet_index,pdf FROM artifacts WHERE job_id=?", (job_id,)
                        ).fetchall()
                    }
                    job["pdfs"] = pdfs
                    if job.get("status") == "running":
                        job["status"] = "error"
                        job["state"] = "failed"
                        job["current"] = ""
                        job["error"] = (
                            "The app restarted during a provider operation. The request was "
                            "not replayed because its billing outcome is ambiguous."
                        )
                    recovered[str(job_id)] = job
            finally:
                connection.close()
        return recovered

    def delete(self, job_id: str) -> None:
        if not self.path.exists():
            return
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute("DELETE FROM jobs WHERE job_id=?", (str(job_id),))
            finally:
                connection.close()
