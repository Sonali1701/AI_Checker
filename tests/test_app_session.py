"""Sign in once a week: the app's session token outlives Google's hourly token."""
import base64
import hashlib
import json
import time

import pytest
from fastapi import HTTPException

import integrated_workflow
import pw_access
import server


def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def google_token(sub="1234567890", email="teacher@pw.live", exp=None):
    claims = {"iss": "https://accounts.google.com", "sub": sub, "email": email, "exp": int(exp or time.time() + 3600)}
    return f"{encode({'alg': 'RS256'})}.{encode(claims)}.signature"


@pytest.fixture
def proxy(monkeypatch):
    """The PW proxy as fixtures: every account allowed, a 7-day pass minted per Google token."""
    monkeypatch.setattr(pw_access, "check_allowed_diagnostic", lambda token, app=None: ("allowed", "allowed"))
    def mint(token, force_new=False):
        g = pw_access._resolve_token(token)
        key = hashlib.sha256(g.encode()).hexdigest()
        pw_access._sessions[key] = {"token": "pass-" + key[:8], "expiry": time.time() + 7 * 86400}
        return pw_access._sessions[key]["token"]
    monkeypatch.setattr(pw_access, "_auth_token", mint)
    monkeypatch.setattr(pw_access, "_sessions", {})
    monkeypatch.setattr(server, "_APP_SESSIONS", server.AppSessions())
    return monkeypatch


def test_sign_in_issues_a_session_that_lasts_as_long_as_the_proxy_pass(proxy):
    answer = server.auth_status("Bearer " + google_token())
    assert answer["allowed"] and answer["email"] == "teacher@pw.live"
    claims = server._unverified_google_claims(answer["session_token"])
    assert claims["iss"] == server.APP_SESSION_ISSUER and claims["sub"] == "1234567890" and claims["email"] == "teacher@pw.live"
    assert abs(claims["exp"] - (time.time() + 7 * 86400)) < 120 and abs(answer["expires_at"] - claims["exp"]) < 2
    # Presenting the session token again confirms it without a new sign-in.
    again = server.auth_status("Bearer " + answer["session_token"])
    assert again["session_token"] == answer["session_token"] and again["expires_at"] == answer["expires_at"]


def test_the_session_outlives_the_google_token(proxy):
    token = google_token()
    session = server.auth_status("Bearer " + token)["session_token"]
    later = time.time() + 2 * 3600
    proxy.setattr(server.time, "time", lambda: later)   # two hours later
    with pytest.raises(HTTPException) as refused:
        server._require_allowed("Bearer " + token)
    assert refused.value.status_code == 401 and "expired" in refused.value.detail
    assert server._require_allowed("Bearer " + session) == token   # the proxy kit gets the Google token for its cached pass


def test_the_session_keeps_the_google_account_as_run_owner(proxy):
    token = google_token()
    session = server.auth_status("Bearer " + token)["session_token"]
    owner = integrated_workflow.owner_for(token)
    assert integrated_workflow.owner_for(server._require_allowed("Bearer " + session)) == owner
    assert integrated_workflow.owner_for(session) == owner


def test_tampered_expired_and_restart_sessions_are_refused(proxy):
    session = server.auth_status("Bearer " + google_token())["session_token"]
    head, body, signature = session.split(".")
    forged = f"{head}.{body}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    with pytest.raises(HTTPException) as refused:
        server._require_allowed("Bearer " + forged)
    assert refused.value.status_code == 401 and "not valid" in refused.value.detail
    proxy.setattr(server, "_APP_SESSIONS", server.AppSessions())   # the app was restarted
    with pytest.raises(HTTPException) as restarted:
        server._require_allowed("Bearer " + session)
    assert restarted.value.status_code == 401 and "restarted" in restarted.value.detail
    sessions = server.AppSessions(clock=lambda: 1000.0)
    issued = sessions.issue("g", {"sub": "s", "email": "e@pw.live"}, 1000.0 + 60)
    sessions.clock = lambda: 1000.0 + 61
    with pytest.raises(HTTPException):
        sessions.resolve(issued["session_token"])


def test_sign_out_revokes_the_session(proxy):
    session = server.auth_status("Bearer " + google_token())["session_token"]
    assert server.auth_signout("Bearer " + session) == {"signed_out": True}
    assert server.auth_signout("Bearer " + session) == {"signed_out": False}
    with pytest.raises(HTTPException):
        server._require_allowed("Bearer " + session)


def test_without_a_proxy_pass_the_google_token_stays_the_session(proxy):
    proxy.setattr(pw_access, "_auth_token", lambda token, force_new=False: pw_access._resolve_token(token))
    answer = server.auth_status("Bearer " + google_token())
    assert answer["allowed"] and "session_token" not in answer


def test_google_tokens_still_work_unchanged(proxy):
    token = google_token()
    assert server._require_allowed("Bearer " + token) == token
    assert server._APP_SESSIONS.resolve(token) is None


@pytest.mark.parametrize("bearer", ["pass-0123456789abcdef", encode({"alg": "HS256"}) + "." + encode({"iss": "pw-proxy", "sub": "script"}) + ".sig"])
def test_a_bare_proxy_pass_still_authenticates_scripts(proxy, bearer):
    """Calibration scripts send a raw 7-day proxy pass as the Bearer: not an app session, so the old path."""
    seen = []
    proxy.setattr(pw_access, "check_allowed_diagnostic", lambda token, app=None: seen.append(token) or ("allowed", "allowed"))
    assert server._APP_SESSIONS.resolve(bearer) is None
    assert server._require_allowed("Bearer " + bearer) == bearer and seen == [bearer]
