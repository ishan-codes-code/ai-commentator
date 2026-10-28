# Current state

Factual snapshot of the project as inspected in the repository.
Nothing here is a claim about future work; for the plan, see
[architecture.md](architecture.md) and [roadmap.md](roadmap.md).

Verification note: milestone 2 results below were verified by execution
(see "Latest verified test results"). The Docker build and in-container
runs were **not** possible in that session (daemon socket permission
denied, no sudo), so tests ran on the host project venv instead; the
Docker commands in [testing.md](testing.md) remain the canonical workflow
and must be re-run where Docker is available.

## Existing files and responsibilities

- `main.py` — the whole application. Two modes:
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

- Unit suite `tests/test_observation_schema.py`: **21 tests, all passed**
  (run on the host project venv; Docker daemon was unreachable in this
  session, so the canonical in-container command in [testing.md](testing.md)
  still needs a run where Docker is available).
- Live single-frame vision test: **passed** — HTTP 200, model returned pure
  JSON conforming to `observation-v1` (4 observations, 4 uncertainties),
  which validated, printed, and saved to
  `output/frames/frame_00000.json`. Run on the host venv (Docker
  unavailable); one request only.
- Frame extraction was not re-run; `main()` is byte-identical to the
  previous commit (verified via `git diff`). Docker build not verified in
  this session (daemon unreachable); re-run `docker compose build` where
  Docker is available.

## Not implemented yet

Multi-frame observation tests, rolling match-state tracking, event
detection, controlled sampling/concurrency, commentary generation,
TTS/audio playback, live capture, OBS integration.

## Recommended next milestone

Roadmap milestone 3: run the validated observation step over a small set
of existing frames and inspect quality/coverage — without changing the
model ID, networking, or extraction behavior. See [roadmap.md](roadmap.md).
