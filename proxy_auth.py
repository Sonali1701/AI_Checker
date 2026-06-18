"""Per-user bearer-token auth for the AI proxy.

Tokens live in the PROXY_TOKENS env var as comma-separated ``name:token`` pairs, e.g.

    PROXY_TOKENS="alice:tok_ab12cd34,bob:tok_ef56gh78"

The server never ships these to clients — each user is handed only their own token.
Revoke a user by removing their pair (and redeploying); the lookup re-reads the env on
every request so there's no stale cache. Put a rate-limiter keyed on the returned name
in front of this if you need per-user throttling.

Tokens are compared in constant time so a timing side-channel can't leak them.
"""
from __future__ import annotations

import hmac
import os


def _load_tokens() -> dict[str, str]:
    """Parse PROXY_TOKENS into {token: user_name}. Malformed pairs are skipped."""
    out: dict[str, str] = {}
    for pair in (os.environ.get("PROXY_TOKENS", "") or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        name, tok = pair.split(":", 1)
        name, tok = name.strip(), tok.strip()
        if name and tok:
            out[tok] = name
    return out


def user_for_token(token: str) -> str | None:
    """Return the user name a bearer token belongs to, or None if it matches none.

    Iterates ALL configured tokens with a constant-time compare each, so neither a
    miss nor a near-miss is distinguishable by timing."""
    if not token:
        return None
    match: str | None = None
    for tok, name in _load_tokens().items():
        if hmac.compare_digest(token, tok):
            match = name
    return match


def user_from_authorization(authorization: str | None) -> str | None:
    """Extract and validate a 'Bearer <token>' header value. None if absent/invalid."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return user_for_token(parts[1].strip())
