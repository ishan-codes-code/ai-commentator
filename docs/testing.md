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
the validated JSON, and saves it to `output/observations/frame_00000.json`
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
API client (see `tests/test_vision_batch.py`); output-layout separation,
legacy migration, and extraction (see `tests/test_output_layout.py`); and
match-state validation, update semantics, determinism, and no-inference
rules (see `tests/test_match_state.py`); event validation, firing
rules, suppression cases, deduplication, determinism, and source
preservation via fixtures (see `tests/test_event_detection.py`); and
bounded concurrency — argument/bound validation, sequential/concurrent
output identity, in-flight caps via barrier rendezvous, reverse completion
order, error/timeout isolation, cache skip, `--force`, schema enforcement,
downstream state/event identity, and `--jobs` CLI rules — via fake clients
(see `tests/test_vision_jobs.py`). Tests mount read-only into the
container; no image change is needed to run them. There are deliberately
no wall-clock speed assertions in the suite; the synthetic
sequential-vs-concurrent timing comparison lives in
`tests/benchmark_jobs.py` (not collected by unittest discovery):

```sh
docker compose run --rm -v "$PWD/tests:/app/tests:ro" \
  commentator python /app/tests/benchmark_jobs.py
```

It submits 5 uncached mocked requests with a fixed per-request sleep and
reports elapsed time for `--jobs 1` versus `--jobs 4` plus an output-identity
check. Its numbers are synthetic (fake sleeps, no network) and must not be
quoted as a production speedup.

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
# Invalid jobs values fail fast with exit code 2 and no API call.
docker compose run --rm commentator python main.py --vision-batch --jobs 0 --limit 1
docker compose run --rm commentator python main.py --vision-batch --jobs 5 --limit 1
# --jobs without --vision-batch is rejected with exit code 2.
docker compose run --rm commentator python main.py --jobs 2
```

## Multi-frame vision batch (live, paid)

```sh
docker compose run --rm -e BASETEN_API_KEY="$BASETEN_API_KEY" \
  commentator python main.py --vision-batch --limit 5
```

Analyses up to 5 frames spread across the clip (default when `--limit` is
omitted), saving one validated `observation-v1` JSON per frame in
`output/observations/`. Frames with an already-validated observation are
skipped unless `--force` is given. Append `--jobs J` (1–4, default 1) to
bound concurrent vision requests; reporting stays in selected-frame order
regardless of completion order. Any per-frame failure still reports the
completed results but exits non-zero. This makes one paid request per
analysed frame; run it only with explicit authorization, and never default
to the full clip.

## Output layout and legacy migration (offline)

JPEG frames live in `output/frames/`; generated JSON lives in
`output/observations/` (`frame_<timestamp>.json` per analysed frame, plus
`match_state.json`); generated event output lives in `output/events/`
(`events.json`). Observations created before the split that still sit
in `output/frames/` are detected by vision commands (non-blocking warning)
and moved by an explicit verified migration:

```sh
# Move validated legacy JSONs to output/observations/ (idempotent).
# Verifies destination bytes before removing any source; conflicts and
# invalid files are left in place and reported, exit 1 until resolved.
docker compose run --rm commentator python main.py --migrate-observations
```

## Match-state rebuild (offline, no API calls)

```sh
docker compose run --rm commentator python main.py --rebuild-state
```

Reads `output/observations/frame_*.json`, validates each file (schema plus
filename timestamp), and atomically writes
`output/observations/match_state.json` (see
`docs/contracts/match-state-v1.md`). Reports discovered / accepted /
skipped / rejected counts plus the state path; exits non-zero when nothing
was accepted or anything was rejected. Re-running over unchanged files
produces a byte-identical snapshot.

## Event detection (offline, no API calls)

```sh
docker compose run --rm commentator python main.py --detect-events
```

Reads `output/observations/frame_*.json` plus the stored
`output/observations/match_state.json`, refuses to run on a stale snapshot
(state timestamps and content hashes must exactly cover the discovered
observations), and atomically writes `output/events/events.json` (see
`docs/contracts/events-v1.md`). Reports discovered / accepted / skipped /
rejected / emitted counts plus contested timestamps and the output path;
exits non-zero when nothing was accepted or anything was rejected.
Re-running over unchanged inputs produces a byte-identical file. Supported
events (transcribed kill-feed entry, team-scoped elim-counter increase)
emit as `candidate`s with full provenance; everything else — missing
nameplates, ambiguous HUD numbers, timers, persistent banners,
low-confidence triggers, contested evidence — is suppressed. Never makes
network calls and never rewrites sources or state.

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
