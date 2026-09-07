# Connect An App To The PW Shared Proxy

This is the implementation checklist for the developer or AI assistant wiring
an app to the proxy.

## Goal

The app must:

- sign in only `@pw.live` users,
- check the app allowlist before every paid/main action,
- call providers only through the PW proxy,
- ship zero provider API keys,
- send `task_id` for multi-call tasks,
- rely on proxy-side raw logging, not client-side combined logging.

## Fixed Values

| Thing | Value |
|---|---|
| Proxy base URL | `https://pw-apps-proxy.vercel.app` |
| Control sheet | `https://docs.google.com/spreadsheets/d/1aaF3y0VsgyB_YcyfDK33VWcCagzwxBPOwjoe2TvfbHE` |
| Allowlist tab | `Whitelisted` |
| Raw export tab | `Raw Usage Ledger Export` |
| Allowed domain | `pw.live` |

## 1. Add The Client

Use:

- `pw_access.py` for Python local apps or Python backends.
- `pw_access.js` for browser, Node, Vercel, or edge apps.

Set:

```python
APP_NAME = "Exact Sheet Header"
```

or in JS:

```js
export const APP_NAME = "Exact Sheet Header";
```

The app name must exactly match a row-1 header in `Whitelisted`.

## 2. Verify App Registration

Open:

```text
https://pw-apps-proxy.vercel.app/api/apps
```

The app name must appear. If it does not, stop and ask the owner to fix the
sheet header.

## 3. Use The User's Google Token

Use the signed-in user's Google access token or ID token. The proxy verifies
the token and requires an `@pw.live` email.

The kit automatically exchanges the Google token for a proxy-issued 7-day
session pass. Apps do not need to manage this themselves.

For long-running apps, pass a token provider function when available, not an
old captured token string.

## 4. Gate Every Paid/Main Action

Python:

```python
if not pw_access.check_allowed(google_token):
    raise PermissionError("Not authorized for this app.")
```

JS:

```js
if (!(await checkAllowed(googleToken))) {
  throw new Error("Not authorized for this app.");
}
```

Fail closed: if the proxy cannot decide, deny the action.

## 5. Route Provider Calls Through The Proxy

Do not call Gemini, LiteLLM, Mathpix, Anthropic, Sarvam, ElevenLabs, OpenAI, or
any provider directly from the app.

Use these helpers:

- Python: `gemini_generate`, `claude_generate`, `gemini_tts`, `gemini_image`,
  `mathpix_ocr`, `sarvam_tts`, `elevenlabs_tts`
- JS: `geminiGenerate`, `claudeGenerate`, `geminiTts`, `geminiImage`,
  `mathpixOcr`, `sarvamTts`, `elevenLabsTts`

For Gemini/Claude, keep sending the existing request body shape. Large requests
above about 3.5 MB are automatically uploaded through the proxy's blob route.

## 6. Add Task ID For Multi-Call Tasks

If one user task makes more than one AI call, create one `task_id` at the start
and pass it to every helper call.

Python:

```python
task_id = pw_access.new_task_id("ai-qc")

pw_access.gemini_generate(
    google_token,
    model="gemini-2.5-flash",
    request=generate_content_body,
    filename="chapter.pdf",
    input_unit="No. of pages",
    count=20,
    task_id=task_id,
)
```

JS:

```js
const task_id = newTaskId("ai-qc");

await geminiGenerate(googleToken, {
  model: "gemini-2.5-flash",
  request: generateContentBody,
  filename: "chapter.pdf",
  input_unit: "No. of pages",
  count: 20,
  task_id,
});
```

Each provider call still logs as its own raw row. Later reporting can combine
by `Task ID + App Name + Email + Model`.

## 7. Do Not Use Old Combined Logging

Do not add `log:false`.

Do not use `UsageSession.flush()` for new code. It remains only so older code
does not crash; in v2 it is a no-op and raw provider calls are already logged.

Do not call `/api/usage-log` for normal proxy-supported providers. The helper
`log_usage` / `logUsage` is legacy/manual audit only.

## 8. Remove Keys

Remove provider keys from:

- `.env`
- source files
- build scripts
- desktop packaged files
- frontend bundles

Search for at least:

`GEMINI`, `LITELLM`, `MATHPIX`, `ANTHROPIC`, `CLAUDE`, `SARVAM`, `ELEVEN`, `OPENAI`

## 9. Test

Run the verifier:

```bash
python pw-app-kit/verify_onboarding.py .env
```

or:

```bash
node pw-app-kit/verify_onboarding.mjs .env
```

Then sign in as a whitelisted `@pw.live` user and make one real AI call.
Confirm a new row appears in `Raw Usage Ledger Export`.

## Completion Checklist

- [ ] `APP_NAME` exactly matches `Whitelisted` sheet header
- [ ] `/api/apps` lists the app
- [ ] allowlist check runs before every paid/main action
- [ ] all provider calls use `pw_access`
- [ ] multi-call tasks pass one `task_id` to every call
- [ ] no provider keys remain in the app
- [ ] a live run appears in `Raw Usage Ledger Export`
