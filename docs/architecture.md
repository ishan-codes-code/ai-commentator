# Intended architecture

This document describes the intended end-state system. It is a plan, not a
claim about existing code. For what actually exists today, see
[current-state.md](current-state.md). Each component below is labeled with its
implementation status.

## System shape (native desktop)

The application is a native desktop app with two halves:

- **Electron shell** (`electron/`): React + Vite + Tailwind renderer,
  a minimal `preload.js` bridge (`processVideo`, `cancelProcessing`,
  `onWorkerEvent` over `contextBridge`), and a main process that owns
  the window, IPC routing, and the Python child-process lifecycle.
  `nodeIntegration` stays disabled; `contextIsolation` stays enabled.
- **Python engine** (`python/`): the AI pipeline lives in the cohesive
  `ai_commentator/` package; `main.py` preserves the existing CLI and public
  imports, while `worker.py` orchestrates the stages for Electron and reports
  NDJSON progress on stdout
  (see [contracts/worker-protocol.md](contracts/worker-protocol.md)).

Communication is `child_process.spawn()` of
`<repo>/.venv/bin/python python/worker.py VIDEO`
(Windows: `.venv/Scripts/python.exe`). Python is a local processing
engine, not a network server: no Docker, no FastAPI, no database.
Packaging the interpreter for production builds is explicitly out of
scope; the target is local development with a native `.venv`.

## Current module boundaries

These are implemented boundaries, distinct from the future capabilities
described later in this document:

- **Python shared foundations:** `ai_commentator/config.py` owns paths,
  versions, limits, and constants; `errors.py` owns shared errors;
  `storage.py` owns atomic JSON writes and verified copies.
- **Vision:** `vision/frames.py` owns frame extraction, discovery, spread
  selection, and frame-related argument parsing. `vision/client.py` owns
  image validation/encoding and the provider request adapter.
  `vision/observations.py` owns observation parsing and validation.
  `vision/batch.py` owns migration, single-frame execution, and batch
  orchestration.
- **Pipeline domains:** `state/match_state.py` owns state validation,
  application, and finalization; `state/runner.py` rebuilds snapshots.
  `events/detection.py` owns event validation, rules, and provenance;
  `events/runner.py` executes detection. `commentary/validation.py` validates
  claim documents and provenance, `claims.py` gates evidence, `writer.py`
  owns writer wording, and `generation.py` runs commentary. In `script/`,
  `generation.py` owns prompt construction, parsing, and validation;
  `runner.py` executes the stage.
- **Entry points and compatibility:** `ai_commentator/cli.py` dispatches the
  existing commands. `python/main.py` explicitly re-exports the established
  engine API and keeps legacy patch points; `python/worker.py` imports that
  facade and retains the worker protocol.
- **Electron:** `src/main.js` composes app lifecycle and dependencies.
  `src/main/window.js`, `src/main/media-protocol.js`, and
  `src/main/ipc/{processing,projects}.js` own window creation, media serving,
  and their respective IPC handlers. Project storage is split into metadata,
  artifacts, and media modules, with `src/project-store.mjs` preserving the
  former import path. `preload.js` remains the narrow renderer bridge.
- **Renderer:** app sections, layout, hooks, components, primitives, and
  libraries remain separate. `WorkspaceSection` and `ArtifactBrowser` are
  still the largest feature components and retain their current presentation.

Dependency direction proceeds from shared foundations to vision/state/events,
then commentary/script and CLI. Pipeline modules communicate through the
existing documented contracts. Electron IPC modules depend on the worker and
project-store boundaries; the renderer continues to use only `window.api`.
`docs/current-state.md` records the observed implementation state.

## Data flow (intended)

Recorded gameplay → frame sampling → vision provider adapter →
structured observation validation → rolling match context and event
tracking → evidence-aware commentary generation → final casting script
→ text-to-speech → local playback, and eventually OBS audio integration.

The first six stages run through the worker today (vision and script
stages are paid and conditional); TTS, playback, and OBS remain planned.

## Components

### 1. Gameplay capture and frame sampling

- **Responsibility:** obtain gameplay images at a controlled rate and hand
  them to the vision stage.
- **Status:** partially implemented. The current input is a recorded video
  file selected by the caller (`python/main.py --video PATH`, or the
  worker's positional video argument; default remains
  `gameplay/match.mp4`), sampled every 3 seconds into `output/frames/`
  (or `--output-dir`). Output directories resolve from the repository
  root regardless of working directory. Live capture is a future
  capability, not implemented.
- **Open questions:** sampling frequency and request-concurrency limits still
  require experimentation (see [roadmap.md](roadmap.md)).

### 2. Vision provider adapter

- **Responsibility:** send a frame to the vision model and return its
  response. Isolate provider-specific API logic (base URL, model ID, request
  shape, error mapping) behind a clear boundary so the provider can change
  without touching pipeline logic.
- **Status:** implemented for the single test frame and the multi-frame
  batch (structured `observation-v1` JSON via Baseten); live multi-frame
  use verified on 5 frames. Reused unchanged by the worker.

### 3. Structured observation validation

- **Responsibility:** parse the model response into an explicit schema and
  reject or flag anything that does not conform, before downstream stages
  may use it.
- **Status:** implemented for the single-frame case
  (`observation-v1` in [contracts/observation-v1.md](contracts/observation-v1.md),
  validated by `parse_observation` / `validate_observation`). Contract
  changes must be reviewed for compatibility
  (see [contracts/README.md](contracts/README.md)).

### 4. Rolling match context and event tracking

- **Status:** match state implemented (`match-state-v1`, rebuilt offline
  via `python/main.py --rebuild-state` or the worker's `state_rebuild`
  stage); deterministic event detection implemented (`events-v1`, via
  `--detect-events` or the worker's `event_detection` stage,
  candidate-only statuses). Richer event judgments remain future work.

### 5. Evidence-aware commentary generation

- **Status:** implemented (`commentary-v1`, generated offline via
  `--generate-commentary` or the worker's `commentary` stage from validated
  observations, match state, and event candidates through deterministic
  templates; candidate echoes carry mandatory qualifiers, silence is
  explicit). Natural wording via `--commentary-write` (one text-only
  model call over gated claims, every line re-validated with template
  fallback) is implemented and opt-in; no live writer call has been made.
- **Status (final casting script):** implemented (`script-v1`, via
  `--generate-script` or the worker's opt-in `script` stage through one
  bounded text-only model call). Verified on fixtures with fakes only; no
  live script call has been made.

### 6. Text-to-speech

- **Responsibility:** convert commentary text to speech audio.
- **Status:** planned. Its input contract is `script-v1`
  (see [contracts/script-v1.md](contracts/script-v1.md)); each segment's
  `text` is directly consumable with no further rewriting stage.

### 7. Local application and eventual OBS audio integration

- **Responsibility:** play or route commentary audio locally first, then
  integrate with OBS output for live use.
- **Status:** planned. The Electron shell, the worker protocol, and a
  minimal worker-status view exist; the video-selection UI and dashboard
  do not.

## Boundaries

- Pipeline stages communicate through explicit schemas
  (see [contracts/README.md](contracts/README.md)); Electron and Python
  communicate only through the worker protocol
  (see [contracts/worker-protocol.md](contracts/worker-protocol.md)).
- Provider-specific code stays behind the vision adapter boundary.
- Untrusted data (API responses, game text, model output) is validated at
  each boundary before application logic uses it.
- Electron security boundary: the renderer reaches the main process only
  through the three audited `preload.js` functions; API keys and
  interpreter paths never cross into the renderer.
