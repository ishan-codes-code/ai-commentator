# 0002 — Native Electron + Python development

- **Status:** accepted.
- **Supersedes:** [0001-docker-first-development.md](0001-docker-first-development.md).

## Context

The project is becoming a native desktop application (Electron + React +
Vite + Python) instead of a Docker-first Python application. The Docker
runtime added container startup latency and daemon dependence without
serving the desktop target, while the engine itself is fully testable in
a native virtual environment (241 tests passing pre-migration).

## Decision

- The application executes natively: Python in a repository-root `.venv`,
  the desktop shell via Electron Forge (`electron/`).
- The engine moves to `python/main.py` with repository-root-anchored
  default paths plus `--video`/`--output-dir` overrides; the algorithms
  are not rewritten.
- Electron launches `python/worker.py` with `child_process.spawn()` and
  communicates over newline-delimited JSON on stdout
  (see `docs/contracts/worker-protocol.md`). Python stays a local
  processing engine, never a network server: no FastAPI, no database.
- All runtime verification is native: `unittest discover` under `.venv`,
  `node:test` in `electron/`, `electron-forge package`, and offline
  worker runs. Paid Baseten stages stay opt-in and are never exercised
  by automated tests.
- `Dockerfile`, `compose.yaml`, and `.dockerignore` are removed; the
  `docker-verify` skill is retired. Historical Docker verification notes
  remain in this log as evidence, not as instructions.

## Alternatives considered

- **Keep Docker for the engine, add Electron beside it:** preserves the
  old runtime but forces every contributor to run a daemon for a local
  desktop app and complicates video-file handoff; rejected.
- **FastAPI sidecar between Electron and Python:** adds a network server
  the MVP does not need (local orchestration over stdio suffices) and a
  new failure surface; rejected per the fixed technology choices.
- **Split `main.py` into a package now:** the worker reuses the existing
  runners without a split; a speculative split adds review surface with
  no second consumer; rejected until extraction is justified.

## Consequences

- Contributors need a local `.venv` and `npm install` in `electron/`;
  no Docker daemon required.
- Video selection is a real file path handed to the worker, not a
  container mount — the MVP works with any user-selected video.
- Production interpreter packaging/distribution is explicitly unsolved
  and out of scope; the integration target is local development.
- The old `network_mode: bridge` history from 0001 is moot: there is no
  container network anymore.
