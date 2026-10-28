---
name: structured-output
description: Define and validate explicit schemas at pipeline stage boundaries.
---

# Structured Output

Use when adding or changing data passed between pipeline stages.

- Define the schema explicitly before implementing the stage, and document
  it under `docs/contracts/`.
- Validate at the boundary: reject or flag non-conforming data with a
  clear error instead of guessing.
- Distinguish direct observations from uncertain interpretations in the
  schema itself; never let downstream stages consume unvalidated model text.
- Cover parsing and schema behavior with unit tests using fixtures, not
  live API calls.
