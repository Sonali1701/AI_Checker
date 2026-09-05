# -*- coding: utf-8 -*-
"""A 529 from the model must not cost the teacher a whole copy.

REPORTED 2026-08-27, mid-batch:

    fb322ec8-...pdf: claude proxy error 529: {"error":"claude error","detail":
    {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"},...}}

529 is Anthropic's "overloaded_error": the upstream model is at capacity. It was not in
_RETRY_STATUSES, so the sheet failed outright — throwing away every OCR page already
paid for and the minutes already spent — over a condition that clears by itself.

Four faults behind one error message:
  * 529 was not retried at all.
  * The schedule was far too short for an overload anyway: 3 attempts at 1.5s and 3.0s
    is ~4.5s of patience for something that takes minutes to clear.
  * No jitter, while OCR fires 6 pages at once — every page in a throttled wave backed
    off for the identical interval and collided again on the retry.
  * Retry-After was ignored, so the server's own answer to "when should I come back"
    was thrown away.

And what the teacher saw was a raw JSON body, which reads like the tool is broken when
nothing here is broken.

The one thing that must NOT change: a TIMEOUT is still never retried. The request may
have been processed, and re-sending a grading call would bill the copy twice.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import pw_access as pw

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Resp:
    def __init__(self, code, headers=None, text="{}"):
        self.status_code = code
        self.headers = headers or {}
        self.text = text


print("== 529 is retried ==")
check("529 is in the retry set", 529 in pw._RETRY_STATUSES)
check("and is treated as a capacity problem", 529 in pw._CAPACITY_STATUSES)
check("429 still is too", 429 in pw._CAPACITY_STATUSES)
for code in (500, 502, 503, 504):
    check(f"{code} is not replayed after ambiguous dispatch", code not in pw._RETRY_STATUSES)
check("a refusal still fails fast", 403 not in pw._RETRY_STATUSES)
check("a bad request still fails fast", 400 not in pw._RETRY_STATUSES)
check("so does an auth failure", 401 not in pw._RETRY_STATUSES)

print("\n== an overload gets more patience than a bad hop ==")
check("more than three attempts", pw._RETRY_ATTEMPTS >= 4, pw._RETRY_ATTEMPTS)
cap = sum(pw._backoff_seconds(Resp(529), a) for a in range(pw._RETRY_ATTEMPTS - 1))
check("an overload is waited out for at least a minute", cap >= 60, f"{cap:.0f}s")
check("no single wait is unbounded",
      all(pw._backoff_seconds(Resp(529), a) <= pw._MAX_BACKOFF_SECONDS
          for a in range(12)))
check("waits grow rather than repeat",
      pw._backoff_seconds(Resp(529), 3) > pw._backoff_seconds(Resp(529), 0))

print("\n== jitter, so a throttled wave does not re-collide ==")
draws = {round(pw._backoff_seconds(Resp(429), 1), 4) for _ in range(30)}
check("two pages do not back off in lockstep", len(draws) > 25,
      f"{len(draws)} distinct waits in 30 draws")
check("but they stay near the intended interval",
      all(0.5 * pw._CAPACITY_BACKOFF_SECONDS * 2 <= d <= 1.5 * pw._CAPACITY_BACKOFF_SECONDS * 2
          for d in draws), sorted(draws)[:3])

print("\n== the server's own Retry-After wins ==")
check("honoured when sent", pw._backoff_seconds(Resp(529, {"Retry-After": "12"}), 0) == 12.0)
check("capped so it cannot hang the app",
      pw._backoff_seconds(Resp(529, {"Retry-After": "9999"}), 0) == pw._MAX_BACKOFF_SECONDS)
check("a nonsense value is ignored rather than crashing",
      pw._backoff_seconds(Resp(529, {"Retry-After": "soon"}), 0) > 0)
check("a negative value cannot go backwards",
      pw._backoff_seconds(Resp(529, {"Retry-After": "-5"}), 0) == 0.0)
check("no header, no problem", pw._backoff_seconds(Resp(529), 0) > 0)

print("\n== the retry loop actually recovers the copy ==")
calls = {"n": 0}
slept = []
_real_once, _real_sleep = pw._post_once, pw.time.sleep
pw.time.sleep = lambda s: slept.append(s)
try:
    def flaky(path, token, payload, timeout, force_new=False):
        calls["n"] += 1
        return Resp(200) if calls["n"] >= 3 else Resp(529)

    pw._post_once = flaky
    r = pw._post("/api/claude", "tok", {}, 5)
    check("a copy survives two overloads", r.status_code == 200, r.status_code)
    check("it took three attempts", calls["n"] == 3, calls["n"])
    check("and it waited between them", len(slept) == 2, slept)
    check("waiting meaningfully, not spinning", all(s > 1 for s in slept), slept)

    calls["n"] = 0
    slept.clear()
    pw._post_once = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), Resp(529))[1]
    r = pw._post("/api/claude", "tok", {}, 5)
    check("a persistent overload gives up eventually",
          calls["n"] == pw._RETRY_ATTEMPTS, calls["n"])
    check("and returns the last response rather than hanging", r.status_code == 529)

    calls["n"] = 0
    pw._post_once = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), Resp(400))[1]
    pw._post("/api/claude", "tok", {}, 5)
    check("a 400 is not retried at all", calls["n"] == 1, calls["n"])

    print("\n== a timeout is still never retried ==")
    calls["n"] = 0

    def times_out(*a, **k):
        calls["n"] += 1
        raise requests.exceptions.Timeout("too slow")

    pw._post_once = times_out
    try:
        pw._post("/api/claude", "tok", {}, 5)
        check("the timeout propagates", False, "it was swallowed")
    except requests.exceptions.Timeout:
        check("the timeout propagates", True)
    check("and it was tried exactly once", calls["n"] == 1,
          f"{calls['n']} attempts — re-sending a grading call would bill the copy twice")
finally:
    pw._post_once, pw.time.sleep = _real_once, _real_sleep

print("\n== what the teacher is told ==")


def message_for(code):
    try:
        pw._raise_proxy_error("claude", Resp(code, text='{"error":"claude error"}'))
    except pw.PWAccessError as exc:
        return str(exc)
    return ""


msg = message_for(529)
check("the overload message says the model is busy", "is busy right now" in msg, msg)
check("it says the copy itself is fine", "Nothing is wrong with this copy" in msg, msg)
check("it says what to do", "re-run it in a few minutes" in msg, msg)
check("no raw JSON is shown", "{" not in msg, msg)
check("429 gets its own wording", "rate limiting us" in message_for(429), message_for(429))
check("other errors keep their detail for diagnosis",
      "proxy error 500" in message_for(500), message_for(500))
check("a 200 raises nothing", message_for(200) == "")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
