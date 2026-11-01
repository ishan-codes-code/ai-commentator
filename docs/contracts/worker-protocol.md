# Worker protocol (Electron ↔ Python)

How the Electron main process drives the Python engine. The worker is
`python/worker.py`, launched with `child_process.spawn()` (never `exec()`).
It reuses the pipeline runners in `python/main.py`; the protocol below is
the only Electron/Python interface. Changing a message shape is a contract
change: update this document, the parser (`electron/src/worker-protocol.mjs`),
and the tests together.

## Transport

- Command: `<repo>/.venv/bin/python python/worker.py VIDEO [--output-dir DIR]
  [--limit N] [--force] [--jobs J] [--skip-vision] [--with-script]
  [--adaptive [--adaptive-max-frames N]]`
  (Windows interpreter: `.venv/Scripts/python.exe`).
- The video path is a positional argument; every flag after it is optional.
- **stdout carries only protocol messages**: one JSON object per line,
  newline-delimited (NDJSON). Engine diagnostics and legacy `print` output
  are redirected to **stderr** so stdout stays parseable even though the
  engine itself prints freely.
- The worker never prints secrets. `BASETEN_API_KEY` is read from the
  environment and its value never appears in any message.

## Messages

```json
{"type": "started", "message": "Processing video clip.mp4"}
{"type": "progress", "stage": "frame_extraction", "completed": 0, "total": 5, "progress": 0}
{"type": "stage_completed", "stage": "frame_extraction", "completed": 1, "total": 5, "detail": {"frames": 3}}
{"type": "stage_completed", "stage": "vision_analysis", "completed": 1, "total": 5, "detail": {"skipped": true, "reason": "option-skipped"}}
{"type": "result", "data": {"video": "/v/clip.mp4", "output_dir": "/o", "stages": ["frame_extraction", "state_rebuild", "event_detection", "commentary"], "skipped": ["vision_analysis"], "mode": "full", "frames": 3, "observations": 3, "events": 0, "commentary_lines": 1, "script": "skipped", "artifacts": {"state": "/o/observations/match_state.json", "events": "/o/events/events.json", "commentary": "/o/commentary/commentary.json", "script": null}}}
{"type": "stopped", "data": {"status": "stopped", "termination": "stop-requested", "mode": "adaptive", "observations": 2, "requests": 2}}
{"type": "error", "stage": "state_rebuild", "message": "State rebuild failed; see worker stderr."}
```

Message types:

- `started` — first message of a run; `message` names the video file.
- `progress` — emitted before each stage runs. `completed`/`total` are
  **stage counts** (stages genuinely finished / stages planned).
  Skipped stages never increment `completed`; `completed` never exceeds
  `total`. `progress` is
  `round(100 * completed / total)`. These are stage-level, never
  fabricated frame percentages. Skipped stages still emit `progress`
  (visiting the stage) followed by `stage_completed` with
  `{"skipped": true, "reason": ...}` (`option-skipped` or `no-observations`).
- `stage_completed` — `stage` plus a small `detail` object with real,
  observed values only (frame/observation/event/line counts from files
  the stage actually wrote, or `{"skipped": true, "reason": ...}`).
- `stopped` — terminal message of a gracefully stopped adaptive run
  (`--adaptive` only). `data` mirrors the `result` shape plus `status`
  (`"stopped"`), `termination` (`"stop-requested"`), request counts, and a
  `manifest` artifact path. Partial observations and the manifest survive.
  No `result` follows it; the process exits `0`.
- `result` — final message on success. `data` reports the video path,
  output directory, completed stage names in order, skipped stage names
  in order, run `mode` (`full` or `extraction-only`), observed counts,
  the script status (`success`, `silence`, or `skipped` when the
  `--with-script` stage did not run), and artifact paths (each `null`
  when the file was not produced). `script` is
  `null` unless `--with-script` ran.
- `error` — terminal failure. `stage` names the failing phase
  (`validation`, a pipeline stage name, `spawn`, `protocol`, or `worker`);
  `message` is human-readable and never contains secrets. At most one
  `error` is emitted per run, and no `result` follows it.

Stages, in order: `frame_extraction` (offline), `vision_analysis` (paid
unless `--skip-vision`; reports skipped with reason `option-skipped`
when skipped),
`state_rebuild` (offline), `event_detection` (offline), `commentary`
(offline, deterministic wording), `script` (paid, only with
`--with-script`). The plan is computed from flags plus observation
presence (`plan_stages`): with `--skip-vision` and no observations, the
downstream analysis stages are skipped with reason `no-observations`
and the run completes as `extraction-only`. Corrupt observations still
fail `state_rebuild`; nothing invalid is ever silently accepted.
With `--adaptive`, the plan is `[adaptive_vision, state_rebuild,
event_detection, commentary]` (plus `script` with `--with-script`):
`frame_extraction` and batch `vision_analysis` never run and emit
nothing, and `--limit`/`--jobs`/`--force`/`--skip-vision` are rejected
as conflicts (exit `2`). A completed adaptive stage reports
`observations`/`requests`/`termination` detail; a stopped stage emits
`stopped` and halts before downstream work; a failed stage emits
`error`. See `docs/contracts/adaptive-sampling-v1.md`.

Electron also synthesizes two renderer-only messages that never appear
on the worker's stdout: `{type: "worker-log", text}` for stderr output
and `{type: "worker-exit", code, signal, cancelled}` when the child ends.

## Validation and exit codes

- Exit `0`: the planned stages all ran or were explicitly skipped;
  stdout ends with `result` (including `extraction-only` runs) or with
  `stopped` (a gracefully stopped adaptive run, still exit `0`).
- Exit `1`: a pipeline stage failed; stdout ends with `error`
  (missing API key for a requested paid stage, rejected observations,
  stale snapshot, write failure, unexpected exception).
- Exit `2`: argument or input validation failed. Missing/unsupported
  video files are reported as
  `{"type": "error", "stage": "validation", "message": ...}` on stdout
  (including `--adaptive` conflicts: with `--skip-vision`,
  batch-only `--limit`/`--jobs`/`--force`, or `--adaptive-max-frames`
  without `--adaptive`)
  before exiting; `argparse` usage errors go to stderr (standard
  behavior, no protocol message).
- Missing API keys are detected before any paid call or extraction work
  (fail fast); no output tree is created in that case.
- In adaptive mode only, SIGTERM is caught and becomes a graceful stop
  (`stopped`, exit `0`): the in-flight request completes, validates, and
  persists before halting. Electron's cancel therefore degrades to
  graceful stop within its SIGKILL grace period on POSIX; on Windows the
  plain kill still terminates immediately. Other modes keep cancel
  behavior below.
- `KeyboardInterrupt` (e.g. Electron cancel via SIGTERM/SIGKILL) emits
  `{"type": "error", "stage": "worker", "message": "Processing was cancelled."}`
  and exits `130`.

## Electron run modes

- Offline extraction: `--skip-vision` (no `--limit` passed). With no
  observations, downstream stages skip with `no-observations` and the
  run completes as `extraction-only`.
- AI analysis: vision enabled with an explicit `--limit 10` from
  Electron (`buildWorkerArgv` rejects non-positive limits before
  spawn). The main process refuses AI runs without `BASETEN_API_KEY`
  and the worker fails fast before any extraction work; the key value
  never appears in any message. `vision-status` reports only whether
  a key is configured.
- Adaptive sampling: `--adaptive [--adaptive-max-frames N]` (paid).
  One captured frame, one vision request, one validated sampling decision
  per step; the next timestamp follows the decision (HIGH 0.5s, NORMAL
  3s, LOW 6s). No extracted JPEG set is written; stages are
  `adaptive_vision` plus the offline downstream; the run `mode` is
  `adaptive`. The first failure terminates the run; a stop request emits
  `stopped` instead.
- One attempt per frame: the engine performs no automatic retries,
  so a run spends at most one vision call per selected frame; cached
  validated observations are reused unless `--force` is passed.

Malformed stdout lines are never fatal to the parser: the Electron
`createLineParser` counts them, forwards a `worker-log` note, and keeps
parsing the rest of the stream. A clean worker ends stdout with a
trailing newline; bytes after the last newline at process end are
dispatched as a final message.

## Testing

- `tests/test_worker.py` covers argument parsing, invalid video paths,
  pure-NDJSON stdout, error handling, exit codes, and secret redaction,
  and `tests/test_adaptive.py` covers the adaptive stage, the `stopped`
  terminal, flag conflicts, the adaptive protocol, and the engine stop boundaries —
  all offline with fixture videos and observations, never paid calls.
- `electron/test/` covers incremental parsing (split chunks, multiple
  messages per chunk, invalid lines), input validation, interpreter
  resolution, and process cancel/cleanup — with `node:test`, no
  dependencies.
