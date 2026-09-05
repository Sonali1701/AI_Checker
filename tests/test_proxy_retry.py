# -*- coding: utf-8 -*-
"""Billing-ambiguous failures are never replayed; explicit rejections may retry."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import pw_access

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeResponse:
    def __init__(self, status_code, text="body"):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"ok": True}


def run(statuses):
    """Drive _post against a scripted sequence; returns (response, calls made)."""
    seq = list(statuses)
    calls = []

    def fake_post(path, google_token, payload, timeout, force_new=False):
        calls.append(force_new)
        nxt = seq.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return FakeResponse(nxt)

    saved_post, saved_sleep, saved_auth = (
        pw_access._post_once, pw_access.time.sleep, pw_access._auth_token)
    pw_access._post_once = fake_post
    pw_access.time.sleep = lambda _s: None          # no real backoff in tests
    pw_access._auth_token = lambda *a, **k: "tok"
    try:
        return pw_access._post("/api/x", "gt", {}, 5), calls
    finally:
        (pw_access._post_once, pw_access.time.sleep,
         pw_access._auth_token) = saved_post, saved_sleep, saved_auth


print("== a 5xx is surfaced once because provider billing may already exist ==")
for status in (500, 502, 503, 504):
    r, calls = run([status])
    check(f"HTTP {status} is not replayed", r.status_code == status and len(calls) == 1,
          f"status={r.status_code}, calls={len(calls)}")

print("== deterministic failures are NOT retried ==")
for status in (400, 403, 404, 422):
    r, calls = run([status])
    check(f"HTTP {status} returned on the first attempt", len(calls) == 1,
          f"{len(calls)} attempts")

print("== a TIMEOUT is never retried (it could double-bill the copy) ==")
try:
    run([requests.exceptions.Timeout("too slow"), 200])
    check("timeout propagates instead of retrying", False, "no exception raised")
except requests.exceptions.Timeout:
    check("timeout propagates instead of retrying", True)

print("== a connection error is not replayed (a reset can happen after dispatch) ==")
try:
    run([requests.exceptions.ConnectionError("reset after send"), 200])
    check("connection error propagates instead of retrying", False, "no exception raised")
except requests.exceptions.ConnectionError:
    check("connection error propagates instead of retrying", True)

print("== the 401 session re-mint still happens, once, per attempt ==")
r, calls = run([401, 200])
check("401 triggers a forced re-mint", calls == [False, True], f"{calls}")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
