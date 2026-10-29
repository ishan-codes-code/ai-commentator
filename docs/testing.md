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
and invalid responses (see `tests/test_observation_schema.py`), plus frame
discovery, spread selection, the shared per-frame vision pipeline, atomic
output writing, batch reporting, and single-frame compatibility via a fake
API client (see `tests/test_vision_batch.py`). Tests mount read-only into
the container; no image change is needed to run them.

## Multi-frame vision batch (offline verification, no API calls)

Selection and reporting without spending inference budget:

```sh
# Show what --limit 5 would select from the real extracted frames.
docker compose run --rm commentator python -c \
  "from main import discover_candidate_frames, select_spread_frames, OUTPUT_DIR; \
   c = discover_candidate_frames(OUTPUT_DIR); \
   print(len(c), [t for t, _ in select_spread_frames(c, 5)])"
# Exercise the skip path: frame 0 already has a validated observation.
docker compose run --rm commentator python main.py --vision-batch --limit 1
# Invalid limits fail fast with exit code 2 and no API call.
docker compose run --rm commentator python main.py --vision-batch --limit 0
```

## Multi-frame vision batch (live, paid)

```sh
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --vision-batch --limit 5
```

Analyses up to 5 frames spread across the clip (default when `--limit` is
omitted), saving one validated `observation-v1` JSON per frame next to its
JPEG. Frames with an already-validated observation are skipped unless
`--force` is given. Any per-frame failure still reports the completed
results but exits non-zero. This makes one paid request per analysed frame;
run it only with explicit authorization, and never default to the full clip.

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
