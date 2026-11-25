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

Always pass a task_id:

```python
task_id = pw_access.new_task_id("my-app")
pw_access.gemini_generate(token, model="gemini-2.5-flash", request=req,
                          input_unit="No. of pages", count=1, task_id=task_id)
```

Use the same `task_id` for every AI call in that task. Without one, the job's
volume cannot be totalled and shows blank on the dashboard.

`count` = what THIS call processed (one call doing 3 pages sends 3). Keep
`input_unit` the same across the app.

## Logging Rule

The proxy logs raw usage automatically. Do not use `log:false`,
`UsageSession.flush()`, or `/api/usage-log` for normal provider calls.

Rows appear in the `Usage Cost 2.0` tab, already grouped per task by the
proxy. You do not combine anything yourself. The old
`Raw Usage Ledger Export` tab is retired.

## Verify

```bash
python pw-app-kit/verify_onboarding.py .env
```

or:

```bash
node pw-app-kit/verify_onboarding.mjs .env
```
