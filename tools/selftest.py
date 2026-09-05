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
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    suites = sorted(
        os.path.join(TESTS, name)
        for name in os.listdir(TESTS)
        if name.startswith("test_") and name.endswith(".py")
    )
    if not suites:
        print("No suites found in tests/.")
        return 1

    failed: list[str] = []
    for path in suites:
        name = os.path.basename(path)
        print(f"\n===== {name} " + "=" * max(0, 60 - len(name)))
        proc = subprocess.run([sys.executable, path], cwd=ROOT)
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
