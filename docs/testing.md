# Testing

All runtime verification is native: a repository-root `.venv` for Python
and `npm test` in `electron/` for the integration layer. Never put real
secrets in commands; use environment-variable references (e.g.
`"$BASETEN_API_KEY"`) instead.

A successful build does **not** prove API connectivity. Report build,
extraction, and vision-test results separately.

## Python environment

```sh
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\Activate.ps1     # Windows PowerShell
pip install -r requirements.txt
```

## Default frame extraction

```sh
.venv/bin/python python/main.py
# Or: .venv/bin/python python/main.py --video /path/to/clip.mp4 --output-dir /tmp/run1
```

Reads the selected video (default `gameplay/match.mp4`), writes
`output/frames/frame_<timestamp>.jpg` (or under `--output-dir`).

## Single-frame vision test

```sh
BASETEN_API_KEY="$BASETEN_API_KEY" .venv/bin/python python/main.py --vision-test
```

Sends only `output/frames/frame_00000.jpg` to the configured model,
validates the response against `docs/contracts/observation-v1.md`, prints
the validated JSON, and saves it to `output/observations/frame_00000.json`
(git-ignored). Requires `BASETEN_API_KEY` to be set in your shell
or `.env` (never echo or commit its value). Invalid model output exits
non-zero with an `Invalid observation:` error and saves nothing.

## Unit tests (deterministic, no API calls)

```sh
.venv/bin/python -m unittest discover -s tests -v
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
downstream state/event identity, `--jobs` CLI rules, and client lifecycle
  (internally created clients closed on success/request/validation failure,
  injected clients never closed) — via fake clients
(see `tests/test_vision_jobs.py`); and CLI wiring of the engine move
(`--video`, `--output-dir`; see `tests/test_commentary_writer.py`); and
writer-mode wording, fallback,
and claim validation via fake writer clients
(see `tests/test_commentary_writer.py`); and final-script generation —
valid output, natural paraphrase, invented-identity/kill/causal rejection,
unconfirmed-echo handling, unknown/duplicate claim ids, malformed/empty
output, API failure without false success, deliberate silence without a
model call, history suppression, timestamp/provenance preservation,
contract validation, atomic-write safety, upstream preservation, and CLI
wiring — via fake script clients (see `tests/test_script.py`); and the
worker protocol — argument parsing, invalid video paths, pure-NDJSON
stdout, error handling, exit codes, and secret redaction — via fixture
videos and observations, no paid calls (see `tests/test_worker.py`).
There are deliberately
no wall-clock speed assertions in the suite; the synthetic
sequential-vs-concurrent timing comparison lives in
`tests/benchmark_jobs.py` (not collected by unittest discovery):

```sh
.venv/bin/python tests/benchmark_jobs.py
```

It submits 5 uncached mocked requests with a fixed per-request sleep and
reports elapsed time for `--jobs 1` versus `--jobs 4` plus an output-identity
check. Its numbers are synthetic (fake sleeps, no network) and must not be
quoted as a production speedup.

## Electron tests (no dependencies, no API calls)

```sh
cd electron && npm test
```

Covers incremental NDJSON parsing (split chunks, multiple messages per
chunk, invalid lines), IPC input validation, `.venv` interpreter and
worker-script resolution, and process launch/cancel/cleanup via
`node:test` (see `electron/test/`).

## Electron build check

```sh
cd electron && npm run package
```

Builds the main, preload, and renderer bundles and packages the app. A
successful package does not prove the worker launches — verify that
separately (next section).

## Worker check (offline, no API calls)

```sh
.venv/bin/python python/worker.py /path/to/clip.mp4 --output-dir /tmp/worker-check --skip-vision
```

Every stdout line must be a JSON protocol message ending with `result`;
exit code must be `0`. The equivalent Electron-driven check spawns the
same command through the main process (`process-video` with
`{skipVision: true}`) and observes the same message stream in the
renderer. Paid stages are never exercised by these checks.

## Multi-frame vision batch (offline verification, no API calls)

Selection and reporting without spending inference budget:

```sh
# Show what --limit 5 would select from the real extracted frames.
PYTHONPATH=python .venv/bin/python -c \
  "from main import discover_candidate_frames, select_spread_frames, FRAMES_DIR; \
   c = discover_candidate_frames(FRAMES_DIR); \
   print(len(c), [t for t, _ in select_spread_frames(c, 5)])"
# Exercise the skip path: frame 0 already has a validated observation.
.venv/bin/python python/main.py --vision-batch --limit 1
# Invalid limits fail fast with exit code 2 and no API call.
.venv/bin/python python/main.py --vision-batch --limit 0
# Invalid jobs values fail fast with exit code 2 and no API call.
.venv/bin/python python/main.py --vision-batch --jobs 0 --limit 1
.venv/bin/python python/main.py --vision-batch --jobs 5 --limit 1
# --jobs without --vision-batch is rejected with exit code 2.
.venv/bin/python python/main.py --jobs 2
```

## Multi-frame vision batch (live, paid)

```sh
.venv/bin/python python/main.py --vision-batch --limit 5
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
.venv/bin/python python/main.py --migrate-observations
```

## Match-state rebuild (offline, no API calls)

```sh
.venv/bin/python python/main.py --rebuild-state
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
.venv/bin/python python/main.py --detect-events
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

## Commentary generation (offline, no API calls)

```sh
.venv/bin/python python/main.py --generate-commentary
```

Reads `output/observations/frame_*.json` plus the stored
`output/observations/match_state.json` and `output/events/events.json`,
refuses to run when state or events do not exactly cover the discovered
observations (rebuild/detect first), and atomically writes
`output/commentary/commentary.json` (see
`docs/contracts/commentary-v1.md`). Reports discovered / accepted /
skipped / rejected / line / suppressed counts plus the output path.
Exits 0 when inputs validate and the write succeeds — even with zero
lines (deliberate silence, recorded with suppression reasons); exits
non-zero on invalid inputs or write failure, leaving any previous valid
file intact. Repeat runs suppress unchanged claims against the previous
file (missing = first run; malformed = warning + first run); only
`current` facts, confident readings, and candidate events with clean
sources can yield lines. Never makes network calls and never rewrites
sources, state, or events.

## Commentary writer (paid, opt-in)

```sh
BASETEN_API_KEY="$BASETEN_API_KEY" .venv/bin/python python/main.py --commentary-write
```

Words the gated claims with a single text-only model call (same endpoint
and model as vision, no images), then validates every line and falls
back per line — or fully, with a warning — to deterministic wording.
Missing `BASETEN_API_KEY` fails fast before any call. All writer tests
use fake clients (`tests/test_commentary_writer.py`); never run the
writer to test it without explicit authorization, and never default to
it in offline verification.

## Final casting script (paid, opt-in)

```sh
BASETEN_API_KEY="$BASETEN_API_KEY" .venv/bin/python python/main.py --generate-script
```

Reads the stored observations, `match_state.json`, and `events.json`
(refusing stale inputs exactly like commentary generation), gates claims
with the existing logic, suppresses repeats against the prior script and
recent commentary history, then words at most 6 prioritized claims with a
single text-only model call (same endpoint and model as vision, no
images). Each segment is validated with deterministic paraphrase checks
(no verbatim-quote requirement) and rejected on any unsupported wording;
there is no template fallback — provider, parse, or validation failures
write an explicit `error` status (exit 1), never a false success. Zero
eligible claims writes a `silence` status with no model call (exit 0).
Output is `output/script/script.json` (see `docs/contracts/script-v1.md`),
the input contract for a future TTS stage; TTS itself is not implemented.
Missing `BASETEN_API_KEY` fails fast before any call. All script tests
use fake clients (`tests/test_script.py`); never run this command to
test it without explicit authorization.

## Configuration and secret-handling checks

- `.env` exists locally and is git-ignored: `git check-ignore -v .env`
- `.env.example` documents the required names with no values
- No secret values in the tree: `git grep -i -E 'sk-|api[_-]?key\s*[:=]\s*\S' -- ':!docs'`
  (investigate any hit; the `BASETEN_API_KEY` *name* in code is expected)
- API key never appears in logs: vision errors print `[REDACTED]`, and
  the worker test suite asserts the key value never reaches stdout/stderr

## Future unit and integration testing conventions

- Unit tests target parsing, schemas, and event logic — the pure,
  deterministic parts. Prefer fixtures over live API calls.
- Integration checks run natively against recorded frames and fixture
  videos; live-model calls stay single-frame and opt-in.
- Name tests by stage (e.g. `test_observation_schema.py`,
  `test_event_detection.py`, `test_worker.py`) and keep them runnable
  with one command, documented here when the suite exists.
- Report results per `AGENTS.md`: what ran and passed, what failed, and
  what was not run. Inspection alone is never reported as a pass.
