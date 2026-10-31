# Current state

## Commentary Studio UI foundation

The Electron renderer opens into a 900 × 650 windowed studio shell with a
toggleable navigation sidebar and live worker-status header. An open project
uses a bounded editor with independently toggleable processing/project
inspector, central Preview and Artifacts tabs, and a docked tabbed bottom
workspace. Audio is the only implemented bottom panel and shows honest empty
playback lanes at a fixed height. Processing actions/status and source details
remain project-scoped; artifact browsing stays project-scoped. Project source
preview uses the main-process `project://` protocol under both development
and packaged origins; it only serves the source filename recorded in project
metadata. The preview keeps native video controls and contains the source in a
dark, constrained stage. It reports available playback time, duration, source
dimensions, and playback status, with separate empty, loading, and playback
error states. Seeking beyond buffered video content can still fail; that media
protocol limitation remains unresolved. Prompt inspection and standalone audio
playback remain placeholders. The right inspector separates Processing,
Configure, and Project information into fixed-header tabs with independently
scrolling content; processing details remain driven by the worker view state.
The UI palette is monochromatic with semantic status accents. Window size and
lifecycle are unchanged. No Python engine or worker protocol behavior changed.

## Modularization status

Project storage is implemented under `electron/src/project-store/`: `metadata.mjs`
owns project lifecycle and metadata persistence, `artifacts.mjs` owns bounded
artifact discovery and JSON reads, and `media.mjs` owns the validated
project-media path resolver. `electron/src/project-store.mjs` and
`project-store/index.mjs` preserve the existing import surface.

The Python engine is organized under `python/ai_commentator/`: `config.py`,
`errors.py`, and `storage.py` provide shared foundations; `vision/` owns frame
handling, image/provider requests, observation validation, migration, and
batch execution; `state/match_state.py` owns state validation and
reconstruction, with `state/runner.py` rebuilding snapshots;
`events/detection.py` owns event rules and `events/runner.py` executes them;
`commentary/` owns claim validation/gating, writer wording, and generation;
`script/generation.py` owns script validation and `script/runner.py` executes
generation; and `cli.py` dispatches existing CLI
modes. `python/main.py` is the compatibility facade for tests and existing
imports. `python/worker.py` continues to import it as `main` and retains the
same NDJSON orchestration.

Electron `src/main.js` is now the composition point for app lifecycle and
dependency wiring. Window creation, the project media protocol, processing
IPC, and project/artifact IPC live in `src/main/window.js`,
`src/main/media-protocol.js`, and `src/main/ipc/`. The renderer remains divided
into app sections, layout, hooks, components, primitives, and libraries;
`WorkspaceSection` and `ArtifactBrowser` remain the largest feature components.
No documented data contract or pipeline behavior was intentionally changed.

Modularization verification: `.venv/Scripts/python.exe -m unittest discover
-s tests -v` passed 261 tests; `npm test` in `electron/` passed 52 tests;
Python CLI help, Electron main-module syntax checks, and an offline worker run
(12 valid NDJSON messages, exit 0) passed. `npm run package` built the main,
preload, and renderer Vite bundles, but Forge packaging could not finish
because its download request was blocked by the environment (`connect EACCES`).
No paid API calls were made.

Factual snapshot of the project as inspected in the repository.
Nothing here is a claim about future work; for the plan, see
[architecture.md](architecture.md) and [roadmap.md](roadmap.md).

Verification note: the engine lived in root `main.py` and ran under
Docker until the Electron/native migration (see
[decisions/0002-native-electron-python.md](decisions/0002-native-electron-python.md)).
Milestone 2–5 and 8b results below were verified before the migration
(241 unit tests passing on the host venv; Docker runs where noted). The
migration preserved the engine unchanged apart from path handling and
CLI wiring (verified by the suite passing after the move, plus 13 new
worker tests and 15 new Electron tests). No Baseten calls were made
during the migration.

## Existing files and responsibilities

- `python/main.py` — compatibility facade for the engine's public functions
  and the existing CLI. Domain implementations live under
  `python/ai_commentator/`; `python/worker.py` continues to import the facade.
  The seven CLI modes preserve the existing behavior:
  - Default (`python python/main.py [--video PATH] [--output-dir DIR]`):
    extracts frames from the selected video (default
    `gameplay/match.mp4`) every 3 seconds (`INTERVAL = 3`) into
    `output/frames/` as `frame_<timestamp>.jpg`, printing FPS, frame
    count, and duration. Never writes JSON or migrates anything. All
    default paths resolve from the repository root regardless of working
    directory; `--video`/`--output-dir` override them.
  - Vision test (`--vision-test`): validates
    `output/frames/frame_00000.jpg` (non-empty, decodable image, image MIME
    type), requests an `observation-v1` JSON observation
    (see `docs/contracts/observation-v1.md`), strictly parses and validates
    it (`parse_observation` / `validate_observation`), prints the validated
    JSON, and saves it to `output/observations/frame_00000.json`
    (git-ignored). Invalid model output exits non-zero and saves nothing.
    API key is read from the `BASETEN_API_KEY` environment variable and
    redacted from error output. Server-side schema enforcement is
    unavailable for this model, so validation happens in code.
  - Vision batch (`--vision-batch [--limit N] [--force] [--jobs J]`):
    discovers existing `frame_<timestamp>.jpg` files (numeric-timestamp
    order), selects up to N spread across the clip (default 5, endpoints
    included; limit 1 selects the first frame), and runs each selected
    frame through the same shared vision pipeline as `--vision-test`
    (`analyze_frame`, via the `process_batch_frame` worker). Each success
    writes one validated `observation-v1` JSON into `output/observations/`
    via atomic temp-file replace; model timestamps must match the source
    frame or the response is rejected. Frames with an already-validated
    observation in the observations directory are skipped unless `--force`
    is given. `--jobs` (default 1, max 4) bounds how many vision requests
    run concurrently via `ThreadPoolExecutor`; reporting is reassembled in
    selected-frame order, never completion order. A live client is never
    shared across threads — each frame gets a fresh `OpenAI` client
    (installed SDK 3.40.0 states no thread-safety guarantee, so the design
    does not depend on one; construction performs no I/O). Each internally
    created client is closed after its request finishes, success or failure
    (context-managed factory); caller-injected clients are never closed.
    Per-frame
    failures, including unexpected client errors such as timeouts (real
    `APITimeoutError`s surface as `VisionAPIError` through the existing
    `APIError` mapping), are reported and the run continues; the final
    summary prints available/selected/succeeded/failed/skipped counts and
    the process exits non-zero unless every selected frame has a validated
    observation. No retries or combined output file. Legacy JSONs beside
    the frames only trigger a warning pointing at `--migrate-observations`.
    Extraction interval is unchanged (`INTERVAL = 3`); no sampling stride
    or intensity classifier was added.
  - Migration (`--migrate-observations`, offline): moves
    validated legacy `output/frames/frame_*.json` files to
    `output/observations/` with byte-copy + destination verification
    before source removal; byte-identical duplicates are removed, while
    conflicting destinations and invalid files are left in place and
    reported. Idempotent; exit 0 only when no legacy files remain.
  - State rebuild (`--rebuild-state`, offline, no Baseten
    calls): validates each `output/observations/frame_*.json` (schema plus
    filename timestamp), folds them into a `match-state-v1` document
    (`apply_observation` + `finalize_match_state`), validates the built
    state, and atomically writes `output/observations/match_state.json`.
    Exit 0 only when at least one observation was accepted and none was
    rejected. See `docs/contracts/match-state-v1.md`.
  - Event detection (`--detect-events`, offline, no Baseten
    calls): refuses to run on a stale snapshot (state timestamps and
    content hashes must exactly cover the discovered observations), then
    evaluates two conservative rules — `kill_feed_entry` (transcribed
    names in a mentioned feed) and `elim_count_increased` (same-team
    counter strictly increasing) — and atomically writes
    `output/events/events.json` (`validate_events`, `detect_events`).
    Everything fires as `candidate` with quoted evidence, source frame
    timestamps, and state fact ids; missing nameplates, ambiguous HUD
    numbers, timers, persistent banners, low-confidence triggers, and
    contested evidence are suppressed, never resolved. Exit 0 only when
    at least one observation was accepted and none was rejected. See
    `docs/contracts/events-v1.md`.
  - Commentary generation (`--generate-commentary`,
    offline, no Baseten calls): validates observations, `match_state.json`
    (timestamps + content hashes must cover inputs), and `events.json`
    (its `generated_from` must cover inputs), then gates claims
    deterministically (`gate_commentary_claims`: current facts only,
    confidence floor, uncertainty/conflict/category scope, candidate
    events with clean sources) and renders them with fixed templates only
    (`render_commentary_claims`: verbatim descriptions plus a hardcoded
    qualifier for `event_echo`; no confirmation path exists). Repeat runs
    suppress unchanged claims against the previous commentary file
    (missing = first run; malformed = warning + first run; emitted history
    spans prior lines and prior `already-emitted` records so silence
    converges). Validates the document with provenance
    (`validate_commentary` + bundle: refs resolve, evidence contained in
    text and sources, name-token closure, confidence ceiling, template
    preconditions) and atomically writes
    `output/commentary/commentary.json`. Exit 0 on valid inputs even with
    zero lines (deliberate silence); exit 1 on invalid inputs or write
    failure with the previous file intact. See
    `docs/contracts/commentary-v1.md`.
  - Writer mode (`--commentary-write`, paid opt-in): runs
    the same validate → gate → suppress pipeline, then words up to 6
    prioritized claims (developments/echoes first) with one text-only
    model call over gated claims plus the last 4 spoken lines — never raw
    observations or full history. Every model line is re-validated
    (evidence containment, token closure, verbatim qualifier) with
    per-line template fallback; provider/parse failure falls back to the
    full deterministic run with a warning, never a crash. Fresh client
    per run (closed after); injected clients never closed; missing key
    raises before any call. Silence makes no call. Same output contract,
    same exit codes; `template` names the gating template in both modes.
  - Script mode (`--generate-script`, paid opt-in): runs
    the same validate → gate pipeline plus repeat suppression against the
    prior script and recent commentary history, then words at most 6
    prioritized claims with one bounded text-only Muse Spark call (same
    endpoint/model, never frames or full history). Segments carry natural
    caster paraphrase validated deterministically (`script_text_valid`:
    TTS shape, name/number closure, forbidden kill/outcome/action/zone/
    causal/intent sets, echo uncertainty without certainty language) —
    no verbatim-quote requirement and no template fallback. Timestamps
    are derived from cited evidence, never invented. Provider, parse, or
    validation failure writes an explicit `error` status (exit 1), never
    a false success; zero eligible claims writes `silence` with no model
    call (exit 0). Atomically writes `output/script/script.json`
    (`script-v1`, see `docs/contracts/script-v1.md`); upstream
    observation, state, event, and commentary files are only read, never
    rewritten. Missing key fails fast before any call, writing nothing.
- `python/worker.py` — the Electron worker. Parses a video path plus
  `--output-dir`/`--limit`/`--force`/`--jobs`/`--skip-vision`/`--with-script`,
  reuses the engine runners above (never duplicates pipeline logic), and
  reports NDJSON on stdout (`started`, `progress`, `stage_completed`,
  `result`, `error`; see `docs/contracts/worker-protocol.md`). Engine
  prints are redirected to stderr; secrets never printed; exit 0 on
  success, 1 on stage failure, 2 on validation errors. Testable without
  Electron and without paid calls (`--skip-vision`, no key needed).
- `electron/` — the desktop shell: `src/main.js` (app composition and lifecycle),
  `src/main/window.js` (window setup),
  `src/main/ipc/processing.js` (worker lifecycle and selection IPC),
  `src/main/ipc/projects.js` (project/artifact IPC),
  `src/main/media-protocol.js` (project media scheme),
  `src/preload.js` (audited `contextBridge`: `processVideo`,
  `cancelProcessing`, `onWorkerEvent`), `src/worker-protocol.mjs`
  (incremental NDJSON parser, input validation, `.venv`/worker
  resolution), `src/python-worker.mjs` (spawn manager with cancel and
  cleanup), `src/renderer.jsx` + `src/components/` (React entry, minimal
  worker-status view), `test/` (`node:test` suites, no dependencies).
- `requirements.txt` — `opencv-python-headless`, `openai` (root, unchanged).
- `.env.example` — documents `BASETEN_API_KEY` with no values.
  A local `.env` holds the real key (git-ignored).
- `.gitignore` — excludes `.env`, `.venv/`, caches, `gameplay/`, `output/`,
  Electron `out/` and `.vite/`, `.vscode/`.
- `README.md` — native setup, engine commands, worker usage, Electron
  development commands, testing; details live in `docs/`.

## How to build and run (native)

See [testing.md](testing.md) for the exact commands. In short:

```sh
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
.venv/bin/python python/main.py                          # extract frames
.venv/bin/python python/main.py --vision-test            # one paid request
.venv/bin/python python/main.py --vision-batch --limit 5 # paid, opt-in
.venv/bin/python python/worker.py gameplay/match.mp4 --output-dir /tmp/run --skip-vision  # offline
cd electron && npm install && npm test && npm run package && npm start
```

## Output directory layout

- `output/frames/` — extracted JPEGs only (`frame_<timestamp>.jpg`).
- `output/observations/` — generated JSON only: `frame_<timestamp>.json`
  per analysed frame plus the rolling snapshot `match_state.json`.
- `output/events/` — generated event output only (`events.json`).
- `output/commentary/` — generated commentary only (`commentary.json`).
- `output/script/` — generated final casting script only (`script.json`,
  the TTS input contract; TTS itself is not implemented).
- Everything under `output/` is git-ignored. `--output-dir` re-bases the
  whole tree; defaults resolve from the repository root.

## Required environment variables (names only, no values)

- `BASETEN_API_KEY` — required for paid stages; empty otherwise.

A `.env` file is present in the working tree (git-ignored) and the host
environment defines `BASETEN_API_KEY`. Secret values are never recorded here.

## Actual model configuration

- Base URL: `https://inference.baseten.co/v1` (`BASETEN_BASE_URL`).
- Model identifier: `meta-models/muse-spark-open-1026` (`BASETEN_MODEL`).
- Do not change the model ID outside an explicit application task.

## Electron/Python integration status

- Worker protocol defined ([contracts/worker-protocol.md](contracts/worker-protocol.md));
  `python/worker.py` verified independently (offline run: NDJSON-only
  stdout ending in `result`, exit 0; validation/key failures emit `error`
  with exit 2/1; no secrets on either stream).
- Electron main launches the worker via `spawn()`, parses stdout
  incrementally, forwards progress/results to the renderer, refuses a
  second concurrent job, supports cancel, and terminates the child on
  app exit. Verified by `node:test` suites and by driving the real worker
  from Node (offline, no paid calls); the earlier native migration's
  package run built cleanly. The latest modularization package attempt
  built all Vite targets but was blocked during Forge's network download;
  see the modularization verification note above.
- Production interpreter packaging is explicitly out of scope: the
  integration target is local development with a native `.venv`.

## Latest verified test results

- Pre-migration baseline (root `main.py`, host venv,
  `python -m unittest discover -s tests`): **241 tests, all passed**.
- Post-migration: **254 Python tests, all passed**
  (`.venv/bin/python -m unittest discover -s tests`: the 241 migrated
  engine tests plus 13 new `tests/test_worker.py` protocol tests) and
  **15 Electron tests, all passed** (`npm test` in `electron/`);
  the one migration-caused failure (a CLI test that relied on
  cwd-relative paths) was fixed by passing `--output-dir` at the call
  site, which also covers the new flag. `npm run package` builds
  cleanly; the worker was driven from Node end-to-end (offline,
  exit 0, no paid calls).
- Historical detail (pre-migration runs, kept as evidence for the engine
  behavior the migration preserved):
  - Synthetic concurrency benchmark (`tests/benchmark_jobs.py`, fake
    sleeping client, zero network): 5 uncached mocked requests complete in
    **1.51s with `--jobs 1` vs 0.61s with `--jobs 4`**, with byte-identical
    outputs. These numbers measure lock-step fake sleeps, not model latency,
    and are not a production speedup claim.
  - Commentary generation on the real tree: **passed** — first run 5
    discovered, 5 accepted, 0 skipped, 0 rejected, **9 lines** (all
    `development` at 195s), 41 suppressed, exit 0; second run 0 lines, 50
    suppressed (deliberate silence), exit 0; third and fourth runs
    byte-identical to the second (convergence verified by hash).
  - Event detection on the real tree: **passed** — 5 discovered, 5 accepted,
    0 skipped, 0 rejected, **0 emitted**, 0 contested, exit 0. A rerun is
    byte-identical. Zero events is the honest result: the kill feed is
    mentioned but never transcribed, and no team-scoped elim counter
    repeats across frames.
  - State rebuild on the real tree: **passed** — 5 discovered, 5 accepted,
    0 skipped, 0 rejected, 0 conflicts, exit 0 (39 facts, 39 changes,
    30 uncertainties, latest 195; 28 stale / 11 current). A second rebuild
    is byte-identical.
  - Offline batch check post-migration: `--vision-batch --limit 1` skipped
    frame 0 from the new observations directory, exit 0, no key, no network.
  - Live single-frame vision test (milestone 2, prior session): **passed** —
    HTTP 200, model returned pure JSON conforming to `observation-v1`.
  - Live multi-frame observations (milestone 3 evidence, authorized run by
    the user before this session): 5 stored JSONs (timestamps 0, 48, 96,
    147, 195) all validate against `observation-v1`.
  - Frame extraction logic unchanged apart from path handling (verified via
    `git diff`) and covered by the offline synthetic-MP4 test.
- No Baseten calls were made in the migration session.

## Not implemented yet

TTS/audio playback, live capture, OBS integration,
Free Fire knowledge pack, and dashboard artifact browsing.
(Stage 1 video selection + run wiring is implemented, see below. Multi-frame
runs, output separation, match-state tracking, conservative event
detection, bounded batch concurrency, evidence-grounded commentary, the
LLM writer path, the final LLM casting script, the Electron worker
protocol, and the spawn/IPC integration are implemented. The writer and
script paths are verified on fixtures with fakes only; no live writer or
script call has been made. The production Python-packaging story is out
of scope. Cosmetic note: worker-thread
progress lines may interleave on stderr under `--jobs 2+`; the saved-file
record and final summary always print deterministically from the main
thread.)

## Renderer UI foundation (Stage 0, on `feature/ui-setup`, uncommitted)

- Obsidian AI design system documented in `docs/ui/` (identity, tokens,
  architecture, component/motion guidelines, roadmap).
- Renderer stack: React 19 + Vite 5 + Tailwind v4 (CSS-first `@theme`
  tokens in `electron/src/index.css`), shadcn manual setup
  (`components.json`, `tsx: false`, `@/` alias via renderer Vite config
  + `jsconfig.json`; generated `button`, `card`, `badge`, `separator`,
  `skeleton`), `motion` (via `motion/react`), `lucide-react`,
  `@fontsource` Inter + Geist Mono.
- App shell: `AppLayout` + `Sidebar` (Dashboard/Matches/Commentary/
  Settings, active `layoutId` indicator), `PageHeader`, `GlassPanel` /
  `GlassCard` / `StatusIndicator` primitives, honest empty dashboard +
  stubs; `WorkerStatus` bridge logic unchanged (restyled).
- Verified: `npm test` 15/15; renderer `vite build` clean with token
  utilities present; dev app boots with renderer connected and no app
  console errors. Forbidden areas untouched (Python, worker protocol,
  main/preload, security settings, model ID).

## Stage 1 — video import and processing (on `feature/video-import-processing`, uncommitted)

- Approved exception to the renderer-only rule: `select-video` IPC
  (`dialog.showOpenDialog` + `ipcMain.handle` in `main.js`, `selectVideo`
  in `preload.js`). Read-only native dialog, main-side validation,
  `sizeBytes` via `fs.statSync`; no filesystem access reaches the
  renderer. `nodeIntegration`/`contextIsolation` unchanged.
- `VideoImport` (selection, `file://` preview with honest fallback,
  metadata) + `RunPanel` (stage-level progress, skipped-stage honesty,
  cancel, result summary from real `result` fields, human-readable
  errors) in the Dashboard; `WorkerStatus` preserved.
- Pure `lib/worker-view.js` mapper (settled-gating so a new run starts
  only after the previous worker exits) + `lib/format.js` helpers, both
  covered by `electron/test/worker-view.test.mjs`.
- Default run is offline-safe: `processVideo(path, { skipVision: true })`,
  no API key needed, no paid calls. `withScript` never enabled by default.
- Docs synced: `UI_ARCHITECTURE.md` (current structure, 4-function
  bridge), `COMPONENT_GUIDELINES.md` (`PageHeader` under `layout/`),
  `AGENTS.md` bridge lists, `UI_ROADMAP.md` Stage 1 acceptance.

## Stage 1.5 step 1 — Obsidian Studio overhaul (uncommitted)

- Near-black token system (`studio-950` `#030304` → `studio-800`
  `#121218`, solid `#1C1C24` hairlines, `#9670FF` accent); glass
  demoted to overlays only; 13px UI body, 15px section titles.
- Flat shell: `AppLayout` (sidebar + `AppHeader` bar + content),
  restyled `Sidebar`, shared `hooks/useWorkerStatus` (header pill +
  `WorkerStatus` card); removed unused `GlassPanel`/`PageHeader`;
  opacity-only section transitions.
- Dashboard recomposed (import → worker → marked-planned Projects
  area); control-state/a11y pass (labels, live regions, focus rings).
- Verified: Electron suite, renderer build, dev boot (see task report).

## Stage 1.5 — project-first dashboard + workspace (on `feature/video-import-processing`, uncommitted)

- Approved project IPC in `main.js`/`preload.js` (`list-projects`,
  `create-project`, `open-project`, `update-project`, `delete-project`)
  plus `projectId`-bound `process-video` (main resolves source + output
  from stored metadata). New `electron/src/project-store.mjs` (userData
  storage, allow-listed ids, containment checks, atomic writes,
  stage-then-rename creation) covered by
  `electron/test/project-store.test.mjs` (6 tests).
- Dashboard is project-first ("Your Projects" + Add Project flow with
  naming step; rows with status; honest empty state; two-step delete).
  New `WorkspaceSection` (preview + native playback, audio-lane
  placeholders, project-bound processing via reused `RunPanel`,
  per-project status persistence). `VideoImport` removed.
- Verified: 34/34 Electron tests, 254/254 Python tests, renderer build
  clean, dev boot with no app console errors, real offline run through
  the mapper; per-project output isolation confirmed.

## Hardening + artifact browsing (on `feature/video-import-processing`, uncommitted)

- Worker stage planning (`plan_stages` pure of flags + observation
  presence): fresh + `--skip-vision` completes as `extraction-only`
  (exit 0, honest counts, null artifacts); corrupt observations still
  fail `state_rebuild`. `completed` excludes skipped; totals equal the
  plan. Contract updated (additive: `skipped`, `mode`, skip reasons).
- `interrupted` recovery: store backstop (`settleProjectRun`,
  `reconcileInterruptedRuns`), startup reconcile, main exit tracking
  with quit-awareness; renderer surfaces the notice and re-runs.
- Artifact browser in the workspace (Tabs: frames with `project://`
  thumbnails, observations, match state, events, commentary) over
  `list-artifacts` / `read-artifact` plus the privileged `project://`
  media scheme (validated scopes/names/types, bare 404s).
- Verified: 256/256 Python, 41/41 Electron (see task report).

## Vision-enabled processing (Milestone 7, on
`feature/video-import-processing`, uncommitted)

- Workspace processing-mode selector: AI analysis (up to 10 frames,
  default when a key is configured) vs offline extraction.
- Explicit `--limit 10` via `buildWorkerArgv`; main-process key gate
  before spawn plus `vision-status` IPC (boolean only); `limit`
  validated before spawn, engine validates again.
- RunPanel shows real stage detail counts and skip reasons; full runs
  are labelled AI analysis, extraction-only runs keep their notice.
  ArtifactBrowser already refetches on run-status change.
- Verified: 261/261 Python (incl. 5 new vision-mode tests),
  52/52 Electron (incl. 11 new), renderer build clean. Real
  `--limit 10` run on `gameplay/match.mp4`: 66 frames, 10/10
  observations, state rebuilt, 0 events (honest), 8 commentary lines.

## Recommended next milestone

Roadmap milestone 9 (TTS and audio playback), then adaptive frame
sampling (explicit follow-up; not started).
See [roadmap.md](roadmap.md).
