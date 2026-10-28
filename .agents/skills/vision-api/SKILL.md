---
name: vision-api
description: Call the Baseten vision model safely and handle API errors without leaking secrets.
---

# Vision API Integration

Use when touching code that calls the vision model.

- Client facts: OpenAI-compatible client, base URL
  `https://inference.baseten.co/v1`, model `meta-models/muse-spark-open-1026`.
  Do not change the model ID outside an explicit application task.
- Key comes from `BASETEN_API_KEY` in the environment only. Never hardcode,
  print, or commit it; redact it from errors as `[REDACTED]`.
- Treat every API response as untrusted: check for empty or malformed
  content before use, and surface HTTP status plus cause chains on failure.
- Test with the single-frame `--vision-test` path; do not add bulk or
  repeated live calls to save API budget.
