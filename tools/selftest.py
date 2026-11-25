# -*- coding: utf-8 -*-
"""Run every offline regression suite. No token, no network, no sample files.

    python tools/selftest.py

Each suite reproduces a real defect found on a real answer sheet, so a green run
means those defects cannot come back silently. Run this before every EXE build.
For accuracy against human evaluators (which does need a token and the ground-truth
Excel), use tools/eval_ca.py --spread --jobs 4.
"""
from __future__ import annotations

import os
import ast
import argparse
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suites", nargs="*", help="Optional test filenames to rerun")
    requested = parser.parse_args().suites
    suites = sorted(
        os.path.join(TESTS, name)
        for name in os.listdir(TESTS)
        if name.startswith("test_") and name.endswith(".py")
    )
    if requested:
        unknown = set(requested) - {os.path.basename(path) for path in suites}
        if unknown:
            parser.error("Unknown suites: " + ", ".join(sorted(unknown)))
        suites = [path for path in suites if os.path.basename(path) in requested]
    if not suites:
        print("No suites found in tests/.")
        return 1

    failed: list[str] = []
    for path in suites:
        name = os.path.basename(path)
        print(f"\n===== {name} " + "=" * max(0, 60 - len(name)))
        # Workflow and page-range tests use pytest fixtures. Running those files
        # as scripts either skips their tests or cannot import the application.
        with open(path, encoding="utf-8-sig") as handle:
            tree = ast.parse(handle.read(), filename=path)
        uses_pytest = any(
            (isinstance(node, ast.Import) and any(alias.name == "pytest" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "pytest")
            for node in ast.walk(tree)
        )
        command = [sys.executable, "-m", "pytest", path, "-q"] if uses_pytest else [sys.executable, path]
        proc = subprocess.run(command, cwd=ROOT)
        if proc.returncode != 0:
            failed.append(name)

    print("\n" + "=" * 68)
    if failed:
        print(f"FAILED suites ({len(failed)}/{len(suites)}): {', '.join(failed)}")
        return 1
    print(f"All {len(suites)} suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
