# -*- coding: utf-8 -*-
"""Auth failures stay fail-closed but no longer collapse into one generic 403."""
import base64
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from fastapi import HTTPException

import pw_access
import server

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeResponse:
    def __init__(self, status_code, allowed=False):
        self.status_code = status_code
        self._allowed = allowed

    def json(self):
        return {"allowed": self._allowed}


def diagnostic_for(value):
    original = pw_access._post
    pw_access._post = lambda *_a, **_k: value if not isinstance(value, Exception) else (_ for _ in ()).throw(value)
    try:
        return pw_access.check_allowed_diagnostic("token")
    finally:
        pw_access._post = original


print("== proxy decisions retain a safe failure category ==")
check("allowed", diagnostic_for(FakeResponse(200, True)) == ("allowed", "allowed"))
check("sheet denial", diagnostic_for(FakeResponse(200, False)) == ("denied", "not_allowlisted"))
check("HTTP 403 denial", diagnostic_for(FakeResponse(403)) == ("denied", "not_allowlisted"))
check("HTTP 401 credential", diagnostic_for(FakeResponse(401)) == ("error", "credential_rejected"))
check("HTTP 503 service", diagnostic_for(FakeResponse(503)) == ("error", "proxy_unavailable"))
check(
    "network failure",
    diagnostic_for(requests.exceptions.ConnectionError("offline")) == ("error", "proxy_unreachable"),
)


def jwt(claims):
    encode = lambda value: base64.urlsafe_b64encode(
        json.dumps(value).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


print("\n== local expiry diagnostics never replace proxy authorization ==")
original = pw_access.check_allowed_diagnostic
called = []
pw_access.check_allowed_diagnostic = lambda _token: called.append(True) or ("allowed", "allowed")
try:
    try:
        server._require_allowed("Bearer " + jwt({"exp": time.time() - 1}))
        check("expired token is rejected", False, "no exception")
    except HTTPException as exc:
        check("expired token is rejected", exc.status_code == 401 and "expired" in exc.detail.lower())
    check("expired token never reaches proxy", not called)

    current = jwt({"exp": time.time() + 3600})
    check("current token still requires proxy", server._require_allowed("Bearer " + current) == current)
    check("proxy was consulted", called == [True])
finally:
    pw_access.check_allowed_diagnostic = original


print("\n== upstream categories remain fail-closed and actionable ==")
for reason, expected_status, phrase in (
    ("credential_rejected", 401, "rejected"),
    ("proxy_unreachable", 503, "unavailable"),
    ("proxy_http_400", 503, "proxy_http_400"),
):
    pw_access.check_allowed_diagnostic = lambda _token, r=reason: ("error", r)
    try:
        server._require_allowed("Bearer " + jwt({"exp": time.time() + 3600}))
        check(reason, False, "no exception")
    except HTTPException as exc:
        check(reason, exc.status_code == expected_status and phrase in exc.detail.lower(), exc.detail)
pw_access.check_allowed_diagnostic = original

if FAILS:
    print(f"\nFAILED: {', '.join(FAILS)}")
    raise SystemExit(1)
print("\nAll auth diagnostic checks passed.")
