"""
verify_onboarding.py - PW App Kit v2 self-check.

Run from the app folder after copying pw_access.py:

    python verify_onboarding.py [path/to/.env] [optional_google_token]

Checks:
  1. pw_access imports and APP_NAME is set.
  2. APP_NAME is registered on the proxy.
  3. No provider API keys remain in the given .env.
  4. Warns if common old combined-logging patterns appear nearby.
  5. Optional live allowlist check when a Google token is supplied.
"""

import os
import sys


PROVIDER_KEY_HINTS = (
    "GEMINI",
    "MATHPIX",
    "SARVAM",
    "ELEVEN",
    "ANTHROPIC",
    "CLAUDE",
    "LITELLM",
    "OPENAI",
)

OLD_PATTERN_HINTS = (
    "log:false",
    '"log": false',
    "'log': false",
    "/api/usage-log",
    "UsageSession.flush",
    ".flush()",
)


def scan_env_for_keys(env_path):
    leaked = []
    if not os.path.exists(env_path):
        return leaked
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            name, _, value = s.partition("=")
            if value.strip() and any(k in name.upper() for k in PROVIDER_KEY_HINTS):
                leaked.append(name.strip())
    return leaked


def scan_old_patterns(root="."):
    hits = []
    skip_dirs = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv"}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".md")):
                continue
            path = os.path.join(base, name)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for hint in OLD_PATTERN_HINTS:
                if hint in text and "pw-app-kit" not in path.replace("\\", "/"):
                    hits.append(f"{path}: {hint}")
                    break
    return hits[:20]


def main():
    env_path = sys.argv[1] if len(sys.argv) > 1 else ".env"
    token = sys.argv[2] if len(sys.argv) > 2 else ""
    ok = True

    try:
        import pw_access
    except Exception as e:
        print("FAIL: cannot import pw_access:", e)
        sys.exit(1)

    print(f"APP_NAME = {pw_access.APP_NAME!r}")
    print(f"PROXY    = {pw_access.PROXY_BASE_URL}")

    if pw_access.APP_NAME == "SET-YOUR-APP-NAME":
        print("FAIL: APP_NAME is still the placeholder.")
        ok = False

    import requests

    try:
        r = requests.get(f"{pw_access.PROXY_BASE_URL}/api/apps", timeout=20)
        apps = r.json().get("apps", []) if r.status_code == 200 else []
        if pw_access.APP_NAME in apps:
            print(f"PASS: {pw_access.APP_NAME!r} is registered on the proxy")
        else:
            print(f"FAIL: {pw_access.APP_NAME!r} not in /api/apps. Add its exact column to Whitelisted.")
            ok = False
    except Exception as e:
        print("WARN: could not reach /api/apps:", e)
        ok = False

    leaked = scan_env_for_keys(env_path)
    if leaked:
        print(f"FAIL: provider keys still present in {env_path}: {', '.join(leaked)}")
        ok = False
    else:
        print(f"PASS: no provider keys in {env_path}")

    old_hits = scan_old_patterns(".")
    if old_hits:
        print("WARN: possible old combined-logging patterns found:")
        for hit in old_hits:
            print("  " + hit)
        print("      Remove these unless they are intentional legacy/manual audit code.")
    else:
        print("PASS: no obvious old combined-logging patterns found")

    if token:
        status = pw_access.check_allowed_status(token)
        print(f"allowlist check for supplied token: {status}")
        if status == "error":
            print("  token invalid/expired, proxy unreachable, or server error")
        ok = ok and status in ("allowed", "denied")

    print("\nRESULT:", "ALL GOOD" if ok else "ISSUES FOUND")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
