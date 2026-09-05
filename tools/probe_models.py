# -*- coding: utf-8 -*-
"""Which Claude models does the PW proxy actually have deployments for?

    python tools/probe_models.py --token-file token.txt

The proxy is a DEPLOYMENT gateway, not the Anthropic API: a model id only works if
someone has provisioned a deployment with that exact name. A model that exists at
Anthropic and prices correctly in costs.py can still 404 here — and it 404s at RUN
time, after OCR has been paid for and part of a booklet already graded.

That is not hypothetical. Switching the small-bracket grader to claude-sonnet-5 on
2026-08-31 looked right on every other axis (a third cheaper, newer model, rates
already in costs.py) and would have killed every copy under 50 marks in production.
Run this before changing any model constant in pipeline.py, and keep
pipeline.PROXY_CLAUDE_MODELS in step with what it prints.

Each probe is a 4-token request — the whole sweep costs a fraction of a rupee.
"""
from __future__ import annotations

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_access
from pipeline import PROXY_CLAUDE_MODELS

# Everything worth asking about: what we route to today, plus the newer ids we would
# move to the moment PW provisions them.
CANDIDATES = (
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-fable-5",
)


def probe(token: str, model: str, task_id: str) -> tuple[bool, str]:
    request = {"max_tokens": 4, "temperature": 0,
               "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    try:
        pw_access.claude_generate(token, model=model, request=request,
                                  filename="probe",
                                  task_id=task_id)
        return True, "available"
    except Exception as exc:
        message = str(exc)
        if "does not exist" in message or "DeploymentNotFound" in message:
            return False, "404 no deployment"
        return False, message.replace("\n", " ")[:88]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", default="token.txt")
    args = ap.parse_args()
    with io.open(args.token_file, encoding="utf-8") as fh:
        token = fh.read().strip()
    if not token:
        sys.exit(f"{args.token_file} is empty — sign in and paste a fresh token.")

    if not pw_access.check_allowed(token):
        sys.exit("Not authorized for AI Copy Checker (or the PW proxy is unavailable).")

    live: set[str] = set()
    task_id = pw_access.new_task_id("probe-models")
    print()
    for model in CANDIDATES:
        ok, detail = probe(token, model, task_id)
        if ok:
            live.add(model)
        print(f"  {model:<20} {'OK ' if ok else '-- '} {detail}")

    listed = set(PROXY_CLAUDE_MODELS)
    missing = sorted(live - listed)
    stale = sorted(listed - live)
    print()
    if missing:
        print(f"  NEW: deployed but not in PROXY_CLAUDE_MODELS: {', '.join(missing)}")
    if stale:
        print(f"  GONE: in PROXY_CLAUDE_MODELS but not deployed: {', '.join(stale)}")
    if not missing and not stale:
        print("  pipeline.PROXY_CLAUDE_MODELS matches the proxy.")
    print()


if __name__ == "__main__":
    main()
