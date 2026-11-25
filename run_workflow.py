"""EXE entry point for the integrated portal workflow (AICheckerWorkflow.exe).

Same local server and safeguards as AICheckerClient.exe (run_client.py); the only
difference is the page the browser opens on: /workflow.html instead of the checker
home. If the checker is already open, this just opens the workflow page on that
running copy instead of starting a second server.
"""
from __future__ import annotations

import multiprocessing

if __name__ == "__main__":
    # Folder mode's grading processes are this same EXE started again; this must run
    # before anything heavy is imported (see run_client.py).
    multiprocessing.freeze_support()

import os

os.environ["AICHECKER_START_PAGE"] = "/workflow.html"

import run_client  # noqa: E402  (reads AICHECKER_START_PAGE when it opens the browser)

if __name__ == "__main__":
    run_client.main()
