# Current state

Factual snapshot of the project as inspected in the repository.
Nothing here is a claim about future work; for the plan, see
[architecture.md](architecture.md) and [roadmap.md](roadmap.md).

Verification note: milestone 2 results were verified by execution on the
host venv. Milestone 3 (batch), milestone 4 (layout split + state tracker),
and milestone 5 (event detection) results below were verified inside Docker
via an escalated shell (the sandboxed shell user lacks daemon access); see
"Latest verified test results". No live Baseten calls were made in the
milestone 3, 4, or 5 sessions; the 5 stored observations are from the
earlier authorized live batch run.

## Existing files and responsibilities

- `main.py` — the whole application. Six modes (still one module; the
  tracker, migration, and detector were kept in `main.py` as the smallest
  complete changes rather than a speculative split):
  - Default (`python main.py`): extracts frames from `gameplay/match.mp4`
    every 3 seconds (`INTERVAL = 3`) into `output/frames/` as
    `frame_<timestamp>.jpg`, printing FPS, frame count, and duration.
    Never writes JSON or migrates anything.
  - Vision test (`python main.py --vision-test`): validates
    `output/frames/frame_00000.jpg` (non-empty, decodable image, image MIME
    type), requests an `observation-v1` JSON observation
    (see `docs/contracts/observation-v1.md`), strictly parses and validates
    it (`parse_observation` / `validate_observation`), prints the validated
    JSON, and saves it to `output/observations/frame_00000.json`
    (git-ignored). Invalid model output exits non-zero and saves nothing.
    API key is read from the `BASETEN_API_KEY` environment variable and
    redacted from error output. Server-side schema enforcement is
    unavailable for this model, so validation happens in code.
  - Vision batch (`python main.py --vision-batch [--limit N] [--force]`):
    discovers existing `frame_<timestamp>.jpg` files (numeric-timestamp
    order), selects up to N spread across the clip (default 5, endpoints
    included; limit 1 selects the first frame), and runs each selected
    frame through the same shared vision pipeline as `--vision-test`
    (`analyze_frame`). Each success writes one validated `observation-v1`
    JSON into `output/observations/` via atomic temp-file replace; model
    timestamps must match the source frame or the response is rejected.
    Frames with an already-validated observation in the observations
    directory are skipped unless `--force` is given. Per-frame failures
    are reported and the run continues; the final summary prints
    available/selected/succeeded/failed/skipped counts and the process
    exits non-zero unless every selected frame has a validated observation.
    No concurrency, retries, or combined output file. Legacy JSONs beside
    the frames only trigger a warning pointing at `--migrate-observations`.
  - Migration (`python main.py --migrate-observations`, offline): moves
    validated legacy `output/frames/frame_*.json` files to
    `output/observations/` with byte-copy + destination verification
    before source removal; byte-identical duplicates are removed, while
    conflicting destinations and invalid files are left in place and
    reported. Idempotent; exit 0 only when no legacy files remain.
  - State rebuild (`python main.py --rebuild-state`, offline, no Baseten
    calls): validates each `output/observations/frame_*.json` (schema plus
    filename timestamp), folds them into a `match-state-v1` document
    (`apply_observation` + `finalize_match_state`), validates the built
    state, and atomically writes `output/observations/match_state.json`.
    Exit 0 only when at least one observation was accepted and none was
    rejected. See `docs/contracts/match-state-v1.md`.
  - Event detection (`python main.py --detect-events`, offline, no Baseten
    calls): refuses to run on a stale snapshot (state timestamps and
    content hashes must exactly cover the discovered observations), then
    evaluates two conservative rules — `kill_feed_entry` (transcribed
    names in a mentioned feed) and `elim_count_increased` (same-team
    counter strictly increasing) — and atomically writes
    `output/events/events.json` (`validate_events`, `detect_events`).
    Everything fires as `candidate` with quoted evidence, source frame
    timestamps, and state fact ids; missing nameplates, ambiguous HUD
    numbers, timers, persistent banners, low-confidence triggers, and
    contested evidence are suppressed, never resolved. Exit 0 only when at
    least one observation was accepted and none was rejected. See
    `docs/contracts/events-v1.md`.
- `Dockerfile` — `python:3.12-slim`, installs `requirements.txt`, copies
  only `main.py`, creates `/app/output/frames`, creates non-root user
  `appuser` (uid 10001), runs as `appuser`, default command
  `python main.py`.
- `compose.yaml` — service `commentator`: builds `.`, overrides the
  container user with `${HOST_UID:-1000}:${HOST_GID:-1000}`, passes
  `BASETEN_API_KEY` through, mounts `./gameplay` read-only and `./output`
  read-write, uses `network_mode: bridge`, `restart: "no"`.
- `requirements.txt` — `opencv-python-headless`, `openai`.
- `.gitignore` — excludes `.env`, `.venv/`, caches, `gameplay/`, `output/`,
  `.vscode/`.
- `.dockerignore` — excludes `.venv`, caches, `.git`, `.env`, `output`,
  `gameplay` from the build context.
- `README.md` — title only; real instructions live in `docs/`.
- `.vscode/settings.json` — terminal uses the env file.

## How to build the Docker image

See [testing.md](testing.md) for the exact commands. In short:
`docker compose build` (or `docker build -t commentator .`).

## How to run frame extraction

`docker compose run --rm commentator`
(default command; reads `./gameplay/match.mp4`, writes `./output/frames/`).

## How to run the one-frame vision test

`docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" commentator python main.py --vision-test`
(sends only `output/frames/frame_00000.jpg` to the model).

## How to run the multi-frame vision batch

Live (paid, one request per analysed frame — run only with authorization):

`docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" commentator python main.py --vision-batch --limit 5`

Offline checks (no API calls, see [testing.md](testing.md)): selection
preview over the real frames, `--vision-batch --limit 1` skip-path run,
and `--limit 0` CLI-error check.

## How to migrate legacy observations, rebuild match state, detect events

All offline (see [testing.md](testing.md)):

`docker compose run --rm commentator python main.py --migrate-observations`

`docker compose run --rm commentator python main.py --rebuild-state`

`docker compose run --rm commentator python main.py --detect-events`

## Output directory layout

- `output/frames/` — extracted JPEGs only (`frame_<timestamp>.jpg`).
- `output/observations/` — generated JSON only: `frame_<timestamp>.json`
  per analysed frame plus the rolling snapshot `match_state.json`.
- `output/events/` — generated event output only (`events.json`).
- Everything under `output/` is git-ignored and Docker-mounted.

## Required environment variables (names only, no values)

- `BASETEN_API_KEY` — required for `--vision-test`; empty otherwise.
- `HOST_UID` / `HOST_GID` — optional; default to 1000 in `compose.yaml`.

A `.env` file is present in the working tree (git-ignored) and the host
environment defines `BASETEN_API_KEY`. Secret values are never recorded here.

## Actual model configuration

- Base URL: `https://inference.baseten.co/v1` (`BASETEN_BASE_URL`).
- Model identifier: `meta-models/muse-spark-open-1026` (`BASETEN_MODEL`).
- Do not change the model ID outside an explicit application task.

## Networking configuration and known limitations

- `compose.yaml` sets `network_mode: bridge`. This is a current
  implementation detail with a known history of connectivity workarounds,
  not a universal recommendation. Any future networking change must be
  based on diagnosis and documented evidence (see
  [decisions/0001-docker-first-development.md](decisions/0001-docker-first-development.md)).
- Limitation: API connectivity from inside the container has not been
  verified in this documentation task. A successful image build does not
  prove API connectivity.

## Latest verified test results

- Unit suite (`tests/test_observation_schema.py` +
  `tests/test_vision_batch.py` + `tests/test_output_layout.py` +
  `tests/test_match_state.py` + new `tests/test_event_detection.py`):
  **152 tests, all passed**, run inside Docker via the canonical
  `docker compose run ... python -m unittest discover -s tests` command
  (escalated shell; sandbox user lacks daemon access). First-run failures
  were test- or fixture-side only (uncreated fixture directory, reporting
  split, over-strict scope matching, abbreviation dots splitting
  sentences); product fixes were limited to unifying rejected-file
  reporting, nearest-scope attribution, and sentence masking.
- Docker build: **passed** (`docker compose build`, exit 0).
- Event detection on the real tree: **passed** — 5 discovered, 5 accepted,
  0 skipped, 0 rejected, **0 emitted**, 0 contested, exit 0. A rerun is
  byte-identical; sources and state verified unchanged afterwards. Zero
  events is the honest result: the kill feed is mentioned but never
  transcribed, and no team-scoped elim counter repeats across frames.
- Synthetic positive check (offline, fixtures in container `/tmp`, repo
  untouched): a team AG `Elim 4` → `Elim 5` pair emits one
  `elim_count_increased` candidate with both provenances quoted.
- Legacy migration on the real tree: **passed** — 5 files moved, 0
  conflicts, 0 invalid, exit 0. SHA-256 hashes of all 5 files are identical
  before and after; 66 JPEGs untouched; 0 JSON files remain in
  `output/frames/`.
- State rebuild on the real tree: **passed** — 5 discovered, 5 accepted,
  0 skipped, 0 rejected, 0 conflicts, exit 0. Snapshot validated against
  `match-state-v1` (39 facts, 39 changes, 30 uncertainties, latest 195;
  28 stale / 11 current). A second rebuild is byte-identical.
- Offline batch check post-migration: `--vision-batch --limit 1` skipped
  frame 0 from the new observations directory, exit 0, no key, no network.
- Live single-frame vision test (milestone 2, prior session): **passed** —
  HTTP 200, model returned pure JSON conforming to `observation-v1`.
- Live multi-frame observations (milestone 3 evidence, authorized run by
  the user before this session): 5 stored JSONs (timestamps 0, 48, 96,
  147, 195) all validate against `observation-v1` (proven by the 5/5
  rebuild acceptance) and were content-surveyed — grounded visible
  descriptions with evidence, appropriately negative combat claims, no
  invented events observed.
- Frame extraction was not re-run on the real video; `main()` extraction
  logic is unchanged (verified via `git diff`) and extraction is covered
  by a new offline test using a synthetic MP4.
- No Baseten calls were made in this session.

## Not implemented yet

Commentary generation, TTS/audio playback, live capture, OBS integration,
controlled sampling/concurrency, Free Fire knowledge pack. (Multi-frame
runs, output separation, match-state tracking, and conservative event
detection are implemented and verified.)

## Recommended next milestone

Roadmap milestone 6: choose sampling frequency and concurrency limits by
documented experiment. See [roadmap.md](roadmap.md).
