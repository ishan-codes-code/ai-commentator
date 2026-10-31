# 0003 — Modular engine and Electron main process

- **Status:** accepted.
- **Supersedes:** the “split `main.py`” alternative recorded in ADR 0002;
  that alternative described the migration milestone, not the current
  architecture.

## Context

The Python engine and Electron main process had grown into large modules
with several domain responsibilities. Tests and the worker also import
engine functions from `python/main.py`, so a structural refactor needs to
preserve that public path and its patchable dependencies.

## Decision

- Organize Python domain logic under `python/ai_commentator/`, with shared
  configuration, errors, and storage below vision, state, events,
  commentary, and script domains. Keep CLI dispatch in
  `python/ai_commentator/cli.py` and preserve `python/main.py` as the explicit
  compatibility facade.
- Keep provider-specific requests within `vision/client.py`; preserve the
  worker as an orchestrator of the existing engine functions and retain the
  documented NDJSON protocol.
- Compose Electron from `main.js` and focused window, media protocol, and
  IPC modules. Keep project metadata, artifact, and media storage in their
  own modules, with the old `project-store.mjs` path retained for imports.
- Treat existing JSON schemas, worker messages, output paths, prompts,
  sampling, limits, and security boundaries as compatibility surfaces.

## Alternatives considered

- **Leave the engine and Electron bootstrap monolithic:** minimizes file
  movement but keeps unrelated responsibilities coupled and hard to review.
- **Remove the `main.py` facade and update every consumer:** would simplify
  package imports but breaks established tests and worker compatibility.
- **Split by file size alone:** risks modules with artificial boundaries;
  extraction follows domain ownership instead.

## Consequences

- Future changes should read and modify only the owning domain module and
  relevant contract, while compatibility imports remain available from
  `main.py`.
- Some domain modules remain several hundred lines because their validation
  or provenance rules are cohesive; further splits should follow a clear
  responsibility boundary.
- Renderer sections and feature components remain separate and visually
  unchanged; `WorkspaceSection` and `ArtifactBrowser` remain candidates for
  later focused extraction if their responsibilities continue to grow.
