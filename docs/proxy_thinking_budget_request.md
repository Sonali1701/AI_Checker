# Request: pass `thinkingConfig` through to Gemini

**To:** PW apps-proxy team
**From:** AI Copy Checker (academics/innovation)
**Ask:** forward `generationConfig.thinkingConfig` unchanged to the Gemini API.

## What we're seeing

`thinkingConfig.thinkingBudget` has no effect on calls made through
`pw-apps-proxy.vercel.app`. Thinking tokens are billed as output tokens, so this is
currently the single largest cost line in our app.

Three probes, same proxy, same day:

| Call | `thinkingBudget` sent | thinking tokens returned |
|---|---|---|
| `gemini-2.5-flash`, short prompt | **0** | 689 |
| `gemini-2.5-flash`, same prompt | 2048 | 689 |
| `gemini-2.5-pro`, grading call | 4096 | 9,078 |
| `gemini-2.5-pro`, same grading call | 512 | 8,506 |

The first two rows are the clearest signal: on `gemini-2.5-flash`, `thinkingBudget: 0`
is documented to disable thinking entirely. We still get 689 thinking tokens, and the
number is identical to the 2048 run. The value is not reaching the model.

We send it in the standard place:

```json
{
  "contents": [...],
  "generationConfig": {
    "temperature": 0,
    "maxOutputTokens": 64000,
    "thinkingConfig": { "thinkingBudget": 4096 }
  }
}
```

## Why it matters to us

We grade handwritten answer sheets. One 7-page CA booklet on `gemini-2.5-pro`:

- visible response (the marks JSON we actually use): **~1,553 tokens**
- billed output tokens: **25,665**
- so roughly **24,000 tokens — 94% of our output spend — is thinking we cannot cap**

That is **₹24 of a ₹29.5 copy**. Our per-copy budget is ₹15. Even with zero
question-paper input and no follow-up calls, the floor on 2.5-pro is ₹25.3.

We tested the obvious workaround — grading on a flash model — across 20 human-marked
booklets. It fits the budget (₹5–13) but is ~18 marks off the human evaluator on
average, so it is not usable. Capping thinking at ~4k on 2.5-pro would put us at
roughly ₹10/copy **at full accuracy**.

## The ask

Pass `generationConfig.thinkingConfig` through to the Gemini endpoint as sent, rather
than dropping or overriding it. If it is being stripped deliberately (schema
allow-list, config normalisation), an allow-list entry for `thinkingConfig` would be
enough.

If a per-app default is preferred instead, a cap of ~4096 for AI Copy Checker works
for us.

## How to reproduce

Send the same short prompt twice to `gemini-2.5-flash` via
`POST /api/gemini/generate`, once with `thinkingConfig.thinkingBudget: 0` and once with
`2048`, and compare `usageMetadata.thoughtsTokenCount` in the two responses. They
should differ (the first should be 0); today they are identical.

Happy to share the exact request/response pairs and our token-level cost breakdown.
