# Contracts

Interfaces between pipeline stages (vision → observation → match state →
events → commentary → audio) must have explicit schemas.

- Define each stage's input/output shape before implementing the stage.
- Contract changes must be reviewed for compatibility with upstream
  producers and downstream consumers.
- Prefer strict validation at stage boundaries: reject or flag
  non-conforming data with a clear error rather than guessing.

The vision JSON schema itself is **not** defined here. It belongs to the
next application milestone (roadmap milestone 2), where it will be added
under this directory alongside the decision that justifies it.
