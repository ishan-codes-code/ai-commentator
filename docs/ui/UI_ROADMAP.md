# UI Roadmap

Staged frontend plan for the AI Commentator Electron renderer.
Foundation milestone first; no stage starts before its dependencies.
Anything not listed here is out of scope until a roadmap update says
otherwise.

## Stage 0 — Foundation and Commentary Studio shell (implemented)

- [x] Dependencies: `motion`, `lucide-react`, `@fontsource/inter`,
  `@fontsource/geist-mono`, shadcn runtime deps (`cn`, `radix-ui`,
  `class-variance-authority`).
- [x] shadcn manual setup for the Forge/Vite layout: `components.json`
  (`tsx: false`, Tailwind v4, `src/index.css`), `@/` alias (Vite +
  `jsconfig.json`), initial components
  (button, card, badge, separator, skeleton; alert + spinner added in
  Stage 1).
- [x] Monochrome semantic tokens, neutral action hierarchy, compact type
  and radius scales, and synchronized design-token documentation.
- [x] Windowed startup with normal operating-system controls and isolated
  preload configuration (current default: 900 × 650).
- [x] Studio shell with Editor, AI Prompts, AI Analysis, Audio, and Settings
  navigation; live worker status; flexible project preview; persistent
  honest audio tray; project and processing flows preserved.
- Verification: see `docs/current-state.md` and the task handoff for the
  actual test and packaging results from this environment.

## Stage 1 — Video selection + processing wiring (implemented, on `feature/video-import-processing`, uncommitted)

- Depends on: Stage 0.
- [x] Approved exception: `select-video` IPC (read-only native
  `dialog.showOpenDialog` + main-side validation/size; `selectVideo`
  in `preload.js`). No filesystem access reaches the renderer.
- [x] `VideoImport`: native picker, metadata (name/ext/size/path),
  `file://` preview with honest fallback, offline-safe
  `processVideo(path, { skipVision: true })` default, single-submit
  guard, Replace/Remove.
- [x] `RunPanel`: stage-level progress with skipped-stage honesty,
  cancel via `cancelProcessing` (confirmed by worker lifecycle),
  result summary from real `result` fields, human-readable errors.
  Pure `lib/worker-view.js` mapper with settled-gating + unit tests.
- Processing uses `window.api.processVideo`
  with main-process validation; cancel via `cancelProcessing`; progress
  and stage display through the existing worker-event subscription;
  result/error surfacing with artifacts from the `result` message.
- Acceptance: offline `--skip-vision`-equivalent run from the UI shows
  stage progress and completes; cancel stops the run; invalid paths show
  the main-process validation error.
- Excludes: paid stages from the UI default path; output browsing.
- Note: the Stage 1 import-first dashboard composition was superseded
  by the project-first dashboard below. `VideoImport` was removed;
  `RunPanel`, `worker-view`, and the offline default were reused.

## Stage 1.5 — Project-first dashboard + workspace (implemented, on `feature/video-import-processing`, uncommitted)

- Depends on: Stage 1.
- [x] Approved exception: project IPC in `main.js`/`preload.js`
  (`list-projects`, `create-project`, `open-project`,
  `update-project`, `delete-project`) plus `projectId`-bound
  `process-video` (main resolves source + output from metadata).
- [x] `project-store.mjs`: userData storage, allow-listed ids,
  containment checks, atomic writes, stage-then-rename creation,
  validated loads; covered by `electron/test/project-store.test.mjs`.
- [x] Dashboard: "Your Projects" + Add Project (native picker → name
  → isolated copy → open workspace); rows with name, source, size,
  last-opened, status; honest empty state; two-step delete.
- [x] Workspace: project header, gameplay preview + native playback,
  original-game-audio and AI-commentary lanes (honest not-implemented
  states), project-bound Process, reused `RunPanel`, per-project
  status persistence.
- [x] Hardening + artifacts: worker stage planning (`plan_stages`,
  extraction-only runs, honest counts), `interrupted` recovery
  (startup reconcile, exit backstop), artifact browser (frames,
  observations, state, events, commentary over `list-artifacts` /
  `read-artifact` + `project://` media).
- Acceptance: create/reopen/delete projects; per-project output
  isolation; offline run from workspace completes; cancel works.
- Excludes: artifact browsing, project rename, TTS, live capture.

## Stage 2 — Dashboard data

- Depends on: Stage 1.
- Read generated artifacts (`match_state.json`, `events.json`,
  `commentary.json`) from the run output directory and present counts,
  status, and recent lines. Read-only views; no invented data.
- Acceptance: after a real run, the dashboard shows observed counts;
  with no run yet, the empty state persists.
- Excludes: TTS/playback, live capture, OBS.

## Stage 3 — Polish and packaging confidence

- Depends on: Stage 2.
- Reduced-motion audit, keyboard-nav pass, empty/error-state copy
  review, packaged-build (`npm run package` + smoke) verification.
- Acceptance: packaged app renders the shell; worker lifecycle intact.

## Explicit exclusions (all stages above)

- No changes to the Python engine, `python/worker.py`, the NDJSON
  worker protocol, `main.js`, `preload.js`, `worker-protocol.mjs`,
  `python-worker.mjs`, Electron security settings, or the Muse model
  identifier.
- No Zustand until local state demonstrably fails; no router until
  section count or deep-linking needs it.
- No FastAPI, Docker, databases, or backend services.
- No TTS/audio playback, live capture, OBS integration, Free Fire
  knowledge pack (per `docs/roadmap.md` staging).
- No paid Baseten calls during verification.
