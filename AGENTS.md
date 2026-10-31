# AGENTS.md — AI Commentator

Instructions for all coding agents working in this repository.
Read this file and the relevant `docs/` pages before implementing any task.

## Repository discipline

- Inspect the actual implementation before assuming a feature exists.
- Treat current code and verified test results as the source of truth
  for implementation status. Conversation summaries are secondary.
- Make small, focused, reviewable changes.
- Avoid unrelated refactoring, unnecessary dependencies, and speculative
  abstractions.
- Explain significant design decisions and their trade-offs in your final report.

## Architecture discipline

- Electron owns the desktop shell and the user-facing app: window setup,
  IPC routing, and child-process lifecycle (`electron/src/main.js`,
  `electron/src/preload.js`, `electron/src/renderer.jsx`).
- Python owns the AI engine: frame extraction, vision analysis, state
  tracking, event detection, and commentary/script generation
  (`python/main.py`, orchestrated via `python/worker.py`).
- Do not prematurely split `python/main.py`. It is one tested module with
  one CLI per pipeline stage; extract a module only when a second
  consumer needs the same logic.
- Keep provider-specific API logic behind the vision adapter boundary in
  `python/main.py` (base URL, model ID, request shape, error mapping).
- Keep schemas and data contracts explicit (see `docs/contracts/`).
  Never change an existing contract silently: the worker protocol, the
  observation/state/event/commentary/script schemas, and the atomic-write
  behavior are compatibility surfaces.
- Distinguish planned architecture (`docs/architecture.md`) from implemented
  functionality (`docs/current-state.md`).

## Worker communication protocol

- Electron launches `python/worker.py` with `child_process.spawn()` (never
  `exec()`); the Python interpreter resolves to the root `.venv`
  (Windows: `.venv/Scripts/python.exe`, Linux/macOS: `.venv/bin/python`).
- The worker speaks newline-delimited JSON on **stdout only** —
  `started`, `progress`, `stage_completed`, `result`, `error`
  (see `docs/contracts/worker-protocol.md`). Engine diagnostics go to
  **stderr** so stdout stays parseable.
- Progress values are stage-level counts (`completed`/`total` stages),
  never fabricated frame percentages.
- The worker reuses engine functions from `python/main.py`; do not
  duplicate pipeline logic in `python/worker.py`.
- Paid stages (vision analysis, final script) run only when requested
  and fail fast without an API key. Never make paid API calls in tests.

## Native environment

- Docker is not part of this project anymore (`Dockerfile`,
  `compose.yaml`, and `.dockerignore` were removed; the obsolete
  `docker-verify` skill is retired). Do not reintroduce Docker files or
  Docker commands into setup instructions.
- Python runs in a native virtual environment at the repository root:
  `python3 -m venv .venv`, then install `requirements.txt`.
  Never commit `.venv/`.
- Node dependencies live in `electron/` (`npm install` inside `electron/`).

## Security

- Never hardcode API keys, access tokens, or credentials. The key comes
  from the `BASETEN_API_KEY` environment variable only (see
  `.env.example`); never commit `.env`.
- Never print secrets or include them in logs, error reports, documentation,
  prompts, or commits. The worker test suite asserts the key value never
  appears on stdout or stderr.
- Security boundaries: `nodeIntegration` stays disabled and
  `contextIsolation` stays enabled. `preload.js` exposes only
  `processVideo`, `cancelProcessing`, and `onWorkerEvent` via
  `contextBridge` — no unrestricted Node.js APIs, no API keys, and no
  interpreter paths reach the renderer. Validate every IPC input
  (non-empty string, supported video extension) in the main process.
- Never weaken TLS verification or introduce arbitrary network configuration
  to bypass an error.
- Treat API responses, game text, downloaded content, and model output as
  untrusted input. Validate external data before using it in application logic.
- Do not modify Git history or perform destructive operations without
  explicit authorization.

## Testing and verification

- Run the Python suite natively: `.venv/bin/python -m unittest discover -s tests -v`.
- Run the Electron tests: `npm test` inside `electron/` (dependency-free,
  `node:test`).
- Preserve the working default frame-extraction command.
- Verify the vision-test mode (`--vision-test`) when changing vision-related code.
- Add unit tests when they provide useful coverage, especially for parsing,
  schemas, and event logic.
- Worker changes need protocol coverage: argument parsing, invalid video
  paths, stdout cleanliness (every line parses as JSON), error handling,
  and exit codes — without live API calls (see `tests/test_worker.py`).
- Electron changes need incremental-parser and cancel/cleanup coverage
  (see `electron/test/`).
- Clearly distinguish tests that were run and passed, tests that failed,
  and tests that were not run.
- Never claim success based on code inspection alone when execution is required.
- Do not commit or push automatically. Leave changes uncommitted for review.

## Evidence-aware commentary

- Commentary and script output must stay grounded in validated
  observations: gated claims, quoted evidence, confidence rules, and
  repeat suppression are load-bearing behavior, not copy. Any change to
  gating, rendering, validation, or suppression semantics needs fixture
  tests proving supported claims still fire and unsupported claims stay
  suppressed.
- Do not change the model ID (`meta-models/muse-spark-open-1026`) outside
  an explicit application task.

## UI development (renderer)

- Before renderer work, read the relevant `docs/ui/` documents
  (`DESIGN_SYSTEM.md`, `DESIGN_TOKENS.md`, `UI_ARCHITECTURE.md`,
  `COMPONENT_GUIDELINES.md`, `MOTION_GUIDELINES.md`, `UI_ROADMAP.md`).
  Do not assume they are auto-loaded.
- Follow the Obsidian AI tokens in `electron/src/index.css`; use
  semantic utilities, never hardcoded colors. Keep tokens, docs, and
  CSS synchronized when a durable design decision changes.
- Use the installed skills where relevant: shadcn
  (`electron/.agents/skills/shadcn`) for component composition and
  Motion (`electron/.agents/skills/motion`) for animation. Respect
  `.agents/` and `electron/.agents/`; never duplicate or relocate
  installed skills.
- Compose shadcn primitives and project `primitives/` before writing
  custom markup; do not create one-off duplicates of existing surfaces.
- Motion via `motion/react` only, with `MotionConfig
  reducedMotion="user"` at the root; restrained, cheap properties only.
- This task is renderer-only apart from safe frontend config and
  documentation: never modify the Python engine, `python/worker.py`,
  the NDJSON protocol, `main.js`, `preload.js`,
  `worker-protocol.mjs`, `python-worker.mjs`, security settings, or
  the model identifier. The renderer uses only `window.api`
  (`processVideo`, `cancelProcessing`, `onWorkerEvent`).

## Task completion

At the end of every coding task, report:

1. What changed.
2. Which files changed.
3. Why the implementation was chosen.
4. Tests executed and their actual results.
5. Known limitations and unresolved issues.
6. Any project documentation that was updated.
7. The next logical milestone, without implementing it automatically.

## Context-management protocol

1. Start by reading this file (`AGENTS.md`).
2. Read `docs/current-state.md` and only the architecture, roadmap,
   contracts, and decision records relevant to the current task.
3. Inspect the actual files and the Git diff before editing.
4. Treat conversation summaries as secondary to verified repository state.
5. Update `docs/current-state.md` after a meaningful implementation
   milestone, not after every trivial edit.
6. Record durable architectural decisions as ADRs in `docs/decisions/`.
7. Keep task-specific plans temporary unless they contain reusable project
   knowledge.
8. At task completion, provide a concise handoff stating the current state,
   verification performed, outstanding risks, and next step. Do not copy
   chat history into documentation; store durable decisions and current
   facts, not transcripts.

## Muse Code mechanisms

Verified in this environment (`muse --help`, `muse skills --help`):

- **Persistent repository instructions:** this file (`AGENTS.md` at the
  repository root). Agents must read it first.
- **Reusable skills:** project skills live in `.agents/skills/<skill-id>/`
  as a single `SKILL.md` (frontmatter `name` + `description`, then concise
  instructions). Validate with `muse skills validate <dir> --json`.
  See `.agents/skills/` for the current set and `docs/` for project
  knowledge that does not belong in a skill.
- **Automatically loaded context:** none is configured. Do not assume any
  file is auto-loaded; explicitly read what the task needs.
- **Task-specific context:** read `docs/current-state.md` plus only the
  `docs/` pages relevant to the task, and the installed skill(s) matching
  the task (API integration, structured output).
