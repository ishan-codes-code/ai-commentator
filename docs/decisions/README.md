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

## Records

- [0001 — Docker-first development](0001-docker-first-development.md)
- [0002 — Native Electron and Python development](0002-native-electron-python.md)
- [0003 — Modular engine and Electron main process](0003-modular-engine-and-electron-process.md)
- [0004 — Adaptive LLM-guided sampling engine](0004-adaptive-sampling-engine.md)
- [0005 — Stdin cooperative-stop transport](0005-stdin-cooperative-stop-transport.md)
- [0006 — Structured adaptive progress messages](0006-structured-adaptive-progress.md)
