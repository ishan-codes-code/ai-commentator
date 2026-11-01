# 0005 — Stdin cooperative-stop control channel

- **Status:** accepted.

## Context

M4.1 gave the adaptive engine a `StopToken` with exact loop-boundary
semantics, but the only Electron-to-worker signal was process
termination (SIGTERM/SIGKILL via `cancel()`). Signals are platform
asymmetric (Windows has no POSIX SIGTERM delivery to console-less
children) and conflated forceful cancellation with graceful stop.
The worker protocol needed a deliberate, cross-platform way to ask a
live run to stop without killing it.

## Decision

- The child keeps `stdio: ['pipe', 'pipe', 'pipe']` with fixed
  ownership: **stdout** carries only NDJSON protocol messages,
  **stderr** carries diagnostics, **stdin** carries only documented
  control messages. The process manager owns stdin writes; the
  renderer never touches the child.
- One versioned control contract, `{"type":"stop","version":1}`
  (`WORKER_CONTROL_VERSION`), parsed by
  `python/ai_commentator/vision/control.py` on a daemon reader thread
  so the adaptive loop never blocks. Blank lines are ignored;
  malformed JSON, unknown commands, and unsupported versions are
  rejected with a short stderr note that never echoes the payload;
  EOF ends the reader quietly. The channel carries no paths, shell,
  code, or secrets.
- A valid stop routes to the existing adaptive `StopToken` — no second
  mechanism. The in-flight request completes, validates, and persists;
  the run halts before the next capture and the worker emits terminal
  `stopped` with exit `0`. Repeated stops are idempotent.
- `requestStop()` reports only whether the line was sent
  (`no-active-job`, `stdin-unavailable`, `already-requested`,
  `stop-requested`, `write-failed`); completion is observed through
  `stopped` and the child exit. Cooperative stop never marks the
  record cancelled and never arms the SIGTERM/SIGKILL escalation.
  `cancel()` stays as the forceful fallback and shutdown path.

## Alternatives considered

- **POSIX signals as the stop signal:** rejected as the primary path
  because Windows delivery differs and signals cannot carry a
  versioned contract; the SIGTERM-to-token mapping stays only as a
  POSIX fallback inside adaptive runs.
- **A general-purpose RPC channel over stdin:** rejected; one small
  versioned command avoids building and securing a command surface
  the product does not need.
- **Polling the manifest or a sentinel file:** rejected; polling adds
  latency and filesystem coupling where a pipe already exists.

## Consequences

- Cooperative stop works identically on Windows and POSIX with only
  the standard library on both sides.
- A stop request is scoped to the active child record; stdin writes
  never cross run boundaries, and the renderer cannot address the
  process directly (only `request-stop` IPC).
- Future control commands, if ever needed, bump `version` and extend
  the same parser and tests; until then the channel stays one command.
