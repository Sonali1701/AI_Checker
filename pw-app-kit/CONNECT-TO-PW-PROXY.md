# Connect An App To The PW Shared Proxy

This is the implementation checklist for the developer or AI assistant wiring
an app to the proxy.

## Goal

The app must:

- sign in only `@pw.live` users,
- check the app allowlist before every paid/main action,
- call providers only through the PW proxy,
- ship zero provider API keys,
- send a `task_id` on every AI call,
- send a truthful `input_unit` + `count`,
- rely on proxy-side logging, not client-side combined logging.

## Fixed Values

| Thing | Value |
|---|---|
| Proxy base URL | `https://pw-apps-proxy.vercel.app` |
| Control sheet | `https://docs.google.com/spreadsheets/d/1aaF3y0VsgyB_YcyfDK33VWcCagzwxBPOwjoe2TvfbHE` |
| Allowlist tab | `Whitelisted` |
| Cost sheet tab | `Usage Cost 2.0` |
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

## 6. Task ID, Input Unit, And Count

### Always send a task_id

Create one `task_id` at the start of a user task and pass it to every helper
call in that task - including tasks that currently make only one call, since
they rarely stay that way.

This is not optional bookkeeping. The proxy groups a job's calls by `task_id`.
Without one, every call for that app/user/model falls into a single
day-long bucket that cannot be split back into jobs, and the tool's volume
shows as blank on the dashboard. Two live apps have this problem today.

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

### What `count` must mean

`count` is the only field the proxy cannot interpret on its own, so state it
plainly in your app and stay consistent.

**Send what THIS call processed.** One call that OCRs 3 pages sends `count=3`.
Ten calls, one page each, send `count=1` ten times. The proxy adds them up.

```python
for page in pages:                       # 20 calls
    pw_access.mathpix_ocr(token, request=req(page),
                          input_unit="No. of pages", count=1,
                          task_id=task_id)
# -> 20 pages
```

The other convention also works - repeating the job total on every call
(`count=20` on all 20 calls) - and the proxy detects it. But it can only detect
it when an app is CONSISTENT. Mixing both inside one app and unit makes the
volume unrecoverable.

Two more rules that matter:

- **Keep `input_unit` stable per app.** A tool reporting slides on some calls
  and characters on others gets one headline unit; the rest are recorded but
  shown separately. They are never added together.
- **`count` must not be a running total.** Sending 1, 2, 3, ... 20 as you go
  reads as per-call quantities and sums to 210.

The proxy rolls the calls up server-side into one row per
(task, app, user, file, model). Nothing downstream needs to combine raw rows.

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

Confirm a row appears in the `Usage Cost 2.0` tab of the control sheet, with
your app name, the right `Input Unit`, and a sensible `Volume`.

That tab syncs every 15 minutes, so allow one cycle - or ask the sheet owner to
run `Usage Cost 2.0 -> Sync now`. The old `Raw Usage Ledger Export` tab is
retired and is no longer written to; do not verify against it.

## Completion Checklist

- [ ] `APP_NAME` exactly matches `Whitelisted` sheet header
- [ ] `/api/apps` lists the app
- [ ] allowlist check runs before every paid/main action
- [ ] all provider calls use `pw_access`
- [ ] every AI call passes a `task_id` (one per user task)
- [ ] `input_unit` is stable for the app, and `count` is what each call did
- [ ] no provider keys remain in the app
- [ ] a live run appears in `Usage Cost 2.0` with a sensible Volume
