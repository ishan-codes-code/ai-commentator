# Current state

Factual snapshot of the project as inspected in the repository.
Nothing here is a claim about future work; for the plan, see
[architecture.md](architecture.md) and [roadmap.md](roadmap.md).

Verification note: milestone 2 results were verified by execution on the
host venv (Docker daemon unreachable in that session). Milestone 3 results
below were verified inside Docker via an escalated shell (the sandboxed
shell user lacks daemon access); see "Latest verified test results". No
live Baseten calls were made in the milestone 3 session.

## Existing files and responsibilities

- `main.py` — the whole application. Three modes:
  - Default (`python main.py`): extracts frames from `gameplay/match.mp4`
    every 3 seconds (`INTERVAL = 3`) into `output/frames/` as
    `frame_<timestamp>.jpg`, printing FPS, frame count, and duration.
  - Vision test (`python main.py --vision-test`): validates
    `output/frames/frame_00000.jpg` (non-empty, decodable image, image MIME
    type), requests an `observation-v1` JSON observation
    (see `docs/contracts/observation-v1.md`), strictly parses and validates
    it (`parse_observation` / `validate_observation`), prints the validated
    JSON, and saves it to `output/frames/frame_00000.json` (git-ignored).
    Invalid model output exits non-zero and saves nothing. API key is read
    from the `BASETEN_API_KEY` environment variable and redacted from error
    output. Server-side schema enforcement is unavailable for this model,
    so validation happens in code.
  - Vision batch (`python main.py --vision-batch [--limit N] [--force]`):
    discovers existing `frame_<timestamp>.jpg` files (numeric-timestamp
    order), selects up to N spread across the clip (default 5, endpoints
    included; limit 1 selects the first frame), and runs each selected
    frame through the same shared vision pipeline as `--vision-test`
    (`analyze_frame`). Each success writes one validated `observation-v1`
    JSON next to its JPEG via atomic temp-file replace; model timestamps
    must match the source frame or the response is rejected. Frames with
    an already-validated observation are skipped unless `--force` is given.
    Per-frame failures are reported and the run continues; the final
    summary prints available/selected/succeeded/failed/skipped counts and
    the process exits non-zero unless every selected frame has a validated
    observation. No concurrency, retries, or combined output file.
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

- Unit suite (`tests/test_observation_schema.py` + new
  `tests/test_vision_batch.py`): **74 tests, all passed**, run inside
  Docker via the canonical `docker compose run ... python -m unittest
  discover -s tests` command (escalated shell; sandbox user lacks daemon
  access). One initial failure was a wrong test expectation (missing comma
  in an indent assertion), fixed in the test — no product change needed.
- Docker build: **passed** (`docker compose build`, exit 0).
- Offline batch smoke tests on the real 66 extracted frames (no API calls):
  selection preview returned `[0, 48, 96, 147, 195]` for limit 5;
  `--vision-batch --limit 1` skipped frame 0 (validated JSON already
  present) and exited 0; `--vision-batch --limit 5` without a key failed
  before any request with a clear `BASETEN_API_KEY` error (exit 1);
  `--limit 0` and lone `--limit` exited 2 with clear CLI errors.
  `output/frames/` was byte-identical before and after.
- Live single-frame vision test (milestone 2, prior session): **passed** —
  HTTP 200, model returned pure JSON conforming to `observation-v1`
  (4 observations, 4 uncertainties), which validated, printed, and saved to
  `output/frames/frame_00000.json`.
- Live multi-frame batch: **not run** — no Baseten calls were made in this
  session. The documented live `--limit 5` command still needs an
  authorized run.
- Frame extraction was not re-run; `main()` is byte-identical to the
  previous commit (verified via `git diff`).

## Not implemented yet

Rolling match-state tracking, event detection, controlled
sampling/concurrency, commentary generation, TTS/audio playback, live
capture, OBS integration. (Multi-frame observation runs are implemented;
only the authorized live 5-frame verification remains.)

## Recommended next milestone

Finish roadmap milestone 3 with one authorized live `--vision-batch
--limit 5` run and inspect observation quality/coverage — without changing
the model ID, networking, or extraction behavior. Then milestone 4: rolling
match-state tracker. See [roadmap.md](roadmap.md).
