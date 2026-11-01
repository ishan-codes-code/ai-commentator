# 0004 — Adaptive LLM-guided sampling engine

- **Status:** accepted.

## Context

Fixed-interval extraction (every 3 seconds) spends paid vision calls
uniformly: static scenes cost the same as rapidly changing ones. Milestone
M4.1 asks for a sequential feedback loop in which the vision model itself
chooses the next sampling density (HIGH / NORMAL / LOW) after each frame,
so paid calls concentrate where the visible evidence changes fastest.

## Decision

- Add a dedicated adaptive path under `python/ai_commentator/vision/`:
  `sampling.py` (speed validation, deterministic scheduler, wrapper
  parsing), `reader.py` (one open `VideoCapture` serving on-demand
  scheduled captures), `client.py` extensions (adaptive prompt plus
  previous-observation context), and `adaptive.py` (the sequential
  orchestrator with `StopToken` and a run manifest).
- The model returns one wrapper object with a separate `observation-v1`
  observation and a separate `sampling` decision; both are validated
  independently with the existing strict rules. A valid observation with
  a bad sampling decision falls back to NORMAL; a bad observation (or a
  malformed wrapper) fails the run without writing anything.
- Adaptive observations reuse the unchanged `observation-v1` contract and
  the `frame_<timestamp>.json` naming. Whole-second stamps keep the
  existing integer form; fractional stamps use the trimmed-decimal form
  (`frame_00000.5.json`), parsed as float. Adaptive metadata (speeds,
  reasons, run status) lives only in `adaptive_run.json`, never in
  observation documents.
- One sampled frame costs at most one paid request: no retries, no
  repair calls, a finite SDK timeout, and a configurable `max_frames`
  bound. The first failure terminates the run; partial observations and
  the manifest survive.
- Stop is cooperative at loop boundaries: an in-flight request always
  completes, validates, and persists before the run halts with terminal
  status `stopped`. Electron's existing SIGTERM cancel maps to the stop
  token in adaptive mode only; all other modes keep their behavior.

## Alternatives considered

- **Precomputed uniform list with per-frame intensity classifier:** keeps
  the batch architecture but needs a second (non-LLM) decision source;
  rejected because M4.1 names the vision LLM response as the decision
  source and optical-flow/computer-vision heuristics are explicitly out
  of scope.
- **Cache-and-skip (reuse stored observations):** would silently treat an
  old observation as proof a new adaptive request succeeded and would
  break the feedback loop, which needs a fresh sampling decision per
  step; rejected. Adaptive mode always re-requests.
- **Skip-and-continue on failure:** more resilient for long runs but
  spends paid calls after a failure and complicates the cost bound;
  rejected for M4.1. Failure terminates the run; a bounded
  skip-and-continue policy is deferred to a later milestone.

## Consequences

- `observation-v1`, the batch pipeline, and all downstream stages are
  unchanged and keep working on integer-timestamp observations;
  fractional adaptive observations flow through the same discovery and
  validation.
- Future sampling tuning edits one config dict
  (`ADAPTIVE_INTERVALS`); changing the wrapper or manifest shape is a
  contract change reviewed against `adaptive-sampling-v1.md`.
- Full UI-to-engine cooperative Stop (a dedicated stop button/IPC) is
  still future work: today Electron cancel in adaptive mode degrades to
  graceful stop via SIGTERM, then SIGKILL after the existing grace
  period. The renderer surfaces the `stopped` terminal message without
  new controls.
