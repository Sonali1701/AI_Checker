# PW App Kit v2 - Member Guide

Use this kit to connect an app to the shared PW proxy with zero provider API
keys in the app.

What the kit gives you:

- per-user, per-app allowlist checks
- provider calls through the shared proxy
- proxy-side raw usage logging to MongoDB
- sheet export to `Raw Usage Ledger Export`
- 7-day proxy session pass so long runs do not fail after Google's token expiry
- large Gemini/Claude request support through the proxy blob upload route

The app sends only the signed-in user's Google token. The proxy verifies the
user, checks the `Whitelisted` sheet, calls the provider with proxy-held keys,
and logs trusted usage.

## Files

| File | Purpose |
|---|---|
| `CONNECT-TO-PW-PROXY.md` | instruction file for the developer/AI assistant wiring the app |
| `pw_access.py` | Python client for local apps and Python backends |
| `pw_access.js` | JavaScript client for browser, Node, Vercel, and edge apps |
| `verify_onboarding.py` | Python onboarding self-check |
| `verify_onboarding.mjs` | JS onboarding self-check |
| `ONBOARD.md` | short version of this guide |

## Fixed Facts

| Thing | Value |
|---|---|
| Proxy URL | `https://pw-apps-proxy.vercel.app` |
| Control sheet | `https://docs.google.com/spreadsheets/d/1aaF3y0VsgyB_YcyfDK33VWcCagzwxBPOwjoe2TvfbHE` |
| Allowed account domain | `pw.live` |
| Raw export tab | `Raw Usage Ledger Export` |
| Providers | Gemini text, Gemini TTS, Gemini image, Claude, Mathpix, Sarvam TTS, ElevenLabs TTS |

## What Changed In v2

- Normal AI calls are logged by the proxy itself.
- Apps should not use client-side combined logging for provider calls.
- `log:false` is not part of the developer workflow.
- `UsageSession.flush()` is now a compatibility no-op.
- For multi-call tasks, apps should pass one `task_id` to every provider call.
- Combining is done later from raw rows using `Task ID + App Name + Email + Model`.

## Human Steps

1. Open the control sheet and go to `Whitelisted`.
2. Add the exact app name as a new header in row 1.
3. Add allowed `@pw.live` emails under that app column.
4. Give the app project and this kit to the developer/AI assistant.

Use this prompt:

```text
Onboard this app to the PW proxy following pw-app-kit/CONNECT-TO-PW-PROXY.md.
My APP_NAME is "PUT-YOUR-EXACT-APP-NAME-HERE".
Set APP_NAME in the client, check allowlist before every paid/main action,
route all provider calls through the pw_access helpers, remove all local
provider API keys, and for any task with more than one AI call create one
task_id and pass it to every call. Do not use client-side combined logging or
UsageSession.flush() for new code. Run the verification and show me the result.
```

## Task ID Pattern

For a task with multiple calls:

```python
task_id = pw_access.new_task_id("final-zip")

pw_access.gemini_generate(token, model="gemini-2.5-flash", request=req1, task_id=task_id)
pw_access.mathpix_ocr(token, request=req2, task_id=task_id)
pw_access.gemini_generate(token, model="gemini-2.5-flash", request=req3, task_id=task_id)
```

In the sheet, each raw call is a separate row. Your combined view can group by:

`Task ID + App Name + Email + Model`

## Verify

- Python app: `python pw-app-kit/verify_onboarding.py .env`
- JS app: `node pw-app-kit/verify_onboarding.mjs .env`
- Live test: sign in as a whitelisted user, make one AI call, then check
  `Raw Usage Ledger Export`.

## Rule

Never put Gemini, LiteLLM, Mathpix, Anthropic, Sarvam, ElevenLabs, OpenAI, or
any other provider key in the app, `.env`, desktop build, frontend bundle, or
shared repo.
