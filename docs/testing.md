# Testing

All runtime verification uses Docker. Never put real secrets in commands;
use environment-variable references (e.g. `"$BASETEN_API_KEY"`) instead.

A successful image build does **not** prove API connectivity. Report build,
extraction, and vision-test results separately.

## Image build

```sh
docker compose build
# or: docker build -t commentator .
```

## Default frame extraction

```sh
docker compose run --rm commentator
```

Reads `./gameplay/match.mp4`, writes `./output/frames/frame_<timestamp>.jpg`.

## Single-frame vision test

```sh
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --vision-test
```

Sends only `output/frames/frame_00000.jpg` to the configured model,
validates the response against `docs/contracts/observation-v1.md`, prints
the validated JSON, and saves it to `output/frames/frame_00000.json`
(git-ignored). Requires `BASETEN_API_KEY` to be set in your shell
or `.env` (never echo or commit its value). Invalid model output exits
non-zero with an `Invalid observation:` error and saves nothing.

## Unit tests (deterministic, no API calls)

```sh
docker compose run --rm -v "$PWD/tests:/app/tests:ro" \
  commentator python -m unittest discover -s tests -v
```

Covers `parse_observation` / `validate_observation` with fixtures for valid
and invalid responses (see `tests/test_observation_schema.py`). Tests mount
read-only into the container; no image change is needed to run them.

## Configuration and secret-handling checks

- `.env` exists locally and is git-ignored: `git check-ignore -v .env`
- `.env` is excluded from the image: check `.dockerignore` contains `.env`
- No secret values in the tree: `git grep -i -E 'sk-|api[_-]?key\s*[:=]\s*\S' -- ':!docs'`
  (investigate any hit; the `BASETEN_API_KEY` *name* in code/compose is expected)
- API key never appears in logs: vision errors print `[REDACTED]`

## Future unit and integration testing conventions

- Unit tests target parsing, schemas, and event logic — the pure,
  deterministic parts. Prefer fixtures over live API calls.
- Integration tests run in Docker (`docker compose run`) against recorded
  frames; live-model tests stay single-frame and opt-in.
- Name tests by stage (e.g. `test_observation_schema.py`,
  `test_event_detection.py`) and keep them runnable with one command,
  documented here when the suite exists.
- Report results per `AGENTS.md`: what ran and passed, what failed, and
  what was not run. Inspection alone is never reported as a pass.
