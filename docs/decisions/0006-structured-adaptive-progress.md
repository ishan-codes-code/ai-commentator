# 0006 — Structured adaptive progress messages

- **Status:** accepted.

## Context

During adaptive runs the worker's stdout carried only stage-level
`progress` plus the terminal `result`/`stopped`: the UI could show
that sampling was running but never what it was deciding. The
per-iteration evidence existed in two wrong places for live display —
human-readable stderr lines and the `adaptive_run.json` manifest,
which the renderer cannot tail without polling. Parsing stderr as a
progress source would bake log formatting into a contract.

## Decision

- The engine accepts an optional `on_iteration` reporting hook in
  `run_adaptive_vision`, invoked once per validated, **persisted**
  iteration with `{timestamp, speed, reason, next_timestamp,
  observations, requests}`. The hook never triggers requests and its
  exceptions are contained so reporting can never break the loop.
- `python/worker.py` passes an emitter that forwards each summary as
  `{"type":"adaptive_sample","data":{...}}` on stdout. The message is
  additive and non-terminal; existing messages and non-adaptive modes
  are unchanged.
- The Electron side validates the shape strictly
  (`isValidAdaptiveSample`: finite non-negative timestamp, `speed` in
  `HIGH`/`NORMAL`/`LOW`, reason string within 280 chars, nullable
  next timestamp, non-negative integer counters) and rejects anything
  outside it as a protocol error, so unvalidated model text can never
  reach the renderer as progress.
- The renderer folds samples into `worker-view.js` state (bounded,
  reset on every `started` so samples cannot leak across runs) and
  renders the chronological decision history from that state; the full
  history stays inspectable in the manifest after the run.

## Alternatives considered

- **Parsing stderr progress lines:** rejected; log text is a
  diagnostic surface, not a versioned contract, and parsing it would
  couple the UI to wording.
- **Polling `adaptive_run.json` from the main process:** rejected;
  polling adds latency, races with atomic rewrites, and duplicates
  delivery the stdout pipe already provides.
- **Richer per-frame payloads (frame bytes, full observations):**
  rejected; the UI needs decisions and counts, and observations are
  already browsable as artifacts. The message stays small.

## Consequences

- `adaptive_sample` becomes part of the worker protocol
  (`docs/contracts/worker-protocol.md`): shape changes require a
  contract review with parser, mapper, and fixture tests updated
  together.
- Interval tuning stays in `ADAPTIVE_INTERVALS`; only the reporting
  of decisions is new, and the engine default `max_frames` (60) is
  untouched — the UI proposes its own conservative default (10).
