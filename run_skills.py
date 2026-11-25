"""EXE entry point for the PW Skills tool (PWSkillsChecker.exe), made for the PW Skills team.

The same local server as AICheckerClient.exe (run_client.py) in its PW Skills mode
(server.APP_MODE): the browser opens on /skills.html, the checker home and the copy
workflow lead there, and the server reports its own build id ("...-skills"), so it
neither joins nor is joined by the checker or workflow app. Its runs live next to the
EXE in pw_skills_data, and its saved jobs and caches in their own app-data folder.
"""
from __future__ import annotations

import multiprocessing

if __name__ == "__main__":
    # Folder mode's grading processes are this same EXE started again; this must run
    # before anything heavy is imported (see run_client.py).
    multiprocessing.freeze_support()

import os
from pathlib import Path

os.environ["AICHECKER_START_PAGE"] = "/skills.html"
os.environ["AICHECKER_APP_MODE"] = "skills"
if not os.environ.get("AICHECKER_DATA_DIR", "").strip():
    _base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.environ.get("TEMP") or "."
    os.environ["AICHECKER_DATA_DIR"] = str(Path(_base) / "AI Copy Checker - PW Skills")

import run_client  # noqa: E402  (reads AICHECKER_START_PAGE, and imports server, which reads the mode)

if __name__ == "__main__":
    run_client.main()
