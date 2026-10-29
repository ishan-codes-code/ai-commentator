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
  observation of a single frame (roadmap milestones 2–3). Includes why
  server-side schema enforcement is unavailable for our model and why
  validation happens in code.
- [match-state-v1.md](match-state-v1.md) — the rolling match-state snapshot
  rebuilt from validated observations (roadmap milestone 4): fact identity,
  provenance, statuses, update semantics, determinism, and what the
  tracker explicitly does not infer.
- [events-v1.md](events-v1.md) — evidence-grounded gameplay events detected
  offline from stored observations and match state (roadmap milestone 5):
  event identity, candidate-only statuses, the two firing rules with their
  evidence requirements, suppression rules, determinism, and replay
  provenance.
