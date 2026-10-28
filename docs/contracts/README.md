# Contracts

Interfaces between pipeline stages (vision → observation → match state →
events → commentary → audio) must have explicit schemas.

- Define each stage's input/output shape before implementing the stage.
- Contract changes must be reviewed for compatibility with upstream
  producers and downstream consumers.
- Prefer strict validation at stage boundaries: reject or flag
  non-conforming data with a clear error rather than guessing.

Defined contracts:

- [observation-v1.md](observation-v1.md) — one validated vision-model
  observation of a single frame (roadmap milestone 2). Includes why
  server-side schema enforcement is unavailable for our model and why
  validation happens in code.
