# Architecture Decision Records

Short records of durable architectural decisions. Each ADR captures why a
decision was made so future agents do not re-litigate it without new evidence.

## Format

Create one file per decision: `docs/decisions/NNNN-short-title.md`, with:

- **Context** — the situation and constraints at decision time.
- **Decision** — what was chosen.
- **Alternatives considered** — what else was weighed, and why not.
- **Consequences** — what follows from the choice (benefits and costs).
- **Status** — `proposed`, `accepted`, or `superseded` (with a pointer).

Keep each ADR to one focused decision. Routine implementation choices do
not need an ADR; record choices that constrain future work.
