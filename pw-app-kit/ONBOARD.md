# PW App Kit v2 - Short Onboarding

## Human Step

In the control sheet `Whitelisted` tab, add the exact app name as a row-1
header and list allowed `@pw.live` emails below it.

## Developer Step

Copy the correct client:

- Python backend/local app: `pw_access.py`
- Browser/Node/Vercel/edge app: `pw_access.js`

Set `APP_NAME` to the exact sheet header.

Before every paid/main action:

```python
if not pw_access.check_allowed(google_token):
    raise PermissionError("Not authorized for this app.")
```

Route all provider calls through the kit helpers. Do not ship provider keys.

For multi-call tasks:

```python
task_id = pw_access.new_task_id("my-app")
pw_access.gemini_generate(token, model="gemini-2.5-flash", request=req, task_id=task_id)
```

Use the same `task_id` for every AI call in that task.

## Logging Rule

The proxy logs raw usage automatically. Do not use `log:false`,
`UsageSession.flush()`, or `/api/usage-log` for normal provider calls.

Rows appear in `Raw Usage Ledger Export`. Combine later by:

`Task ID + App Name + Email + Model`

## Verify

```bash
python pw-app-kit/verify_onboarding.py .env
```

or:

```bash
node pw-app-kit/verify_onboarding.mjs .env
```
