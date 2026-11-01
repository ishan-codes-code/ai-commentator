# Contract: adaptive sampling v1 (`adaptive-sampling-v1`)

LLM-guided sequential sampling for recorded gameplay video (milestone
M4.1). One captured frame costs at most one vision request: the model
returns a validated `observation-v1` observation **plus** a separate
sampling decision, and the scheduler maps that decision to the next
capture timestamp. No optical-flow or frame-difference heuristics, no
multi-frame batching, no concurrent requests.

## Model response wrapper

The model must return one JSON object with exactly these fields:

```json
{
  "observation": {
    "schema_version": "observation-v1",
    "timestamp": 12,
    "observations": [],
    "uncertainties": []
  },
  "sampling": {
    "next_sampling_speed": "HIGH",
    "reason": "Intense combat activity is visible."
  }
}
```

### Observation half

- Must pass the unchanged `parse_observation` / `validate_observation`
  rules (`observation-v1`): strict shape, no extra fields at any level,
  markdown fences tolerated exactly as the existing parser allows.
- Its `timestamp` must equal the requested scheduled source-video
  timestamp (a number; the model value is compared by value, never
  reformatted). A mismatch rejects the whole response.
- Only this object is persisted, via the existing atomic observation
  write to `output/observations/frame_<timestamp>.json`. No adaptive
  metadata is ever added to it.

### Sampling half

Validated separately by `validate_sampling`, which never raises:

- `next_sampling_speed` must be exactly one of `HIGH`, `NORMAL`, `LOW`
  (case-sensitive; anything else is not guessed at). Missing, invalid,
  malformed, non-object, or unexpected content (including extra fields)
  falls back to `NORMAL` with a recorded diagnostic.
- `reason` is optional, untrusted descriptive text: non-strings become
  `""`, longer text is truncated to `ADAPTIVE_MAX_REASON_CHARS` (280)
  with a diagnostic. It is never executed and never schedules anything.
- A valid observation with a bad sampling decision is still persisted;
  the scheduler uses the `NORMAL` fallback. Sampling output can never
  discard a validated observation.

If the wrapper is malformed or the observation half is invalid, nothing
is written and the run terminates as failed (no retries, no repair
calls).

## Scheduler

- Speeds map to configured intervals (`ADAPTIVE_INTERVALS` in
  `python/ai_commentator/config.py`): `HIGH` 0.5s, `NORMAL` 3.0s,
  `LOW` 6.0s. These are initial engineering values; tune by editing
  that dict. Every value must stay within
  `[ADAPTIVE_MIN_INTERVAL, ADAPTIVE_MAX_INTERVAL]` (0.1–60.0s).
- The first sample is always timestamp 0. Each next timestamp is
  `quantize(current + interval)` at millisecond precision, strictly
  increasing, and strictly below the video duration; reaching or passing
  the duration ends the run cleanly. No repeated, non-increasing, or
  out-of-range timestamps are ever scheduled.
- Only the validated speed label drives the scheduler. Model text and
  floating-point values from the model never set a timestamp.

## Observation filenames

Whole-second stamps keep the existing integer form
(`frame_00003.json`, parsed as `3`). Fractional stamps use the
trimmed-decimal form (`frame_00000.5.json`, parsed as `0.5`).
Discovery, state rebuild, and downstream stages accept both; the strict
integer contract for the fixed-interval path is unchanged.

## Prompt context

Each request carries at most the immediately previous validated
observation (timestamp plus bounded entry/field/uncertainty caps, 2000
characters total) as supplementary background. It is labeled as
background, never as current fact; the prompt forbids repeating it as a
fresh claim, inventing identities/kills/causes/intentions, and
suggesting unseen events.

## Run manifest (`adaptive_run.json`)

Stored beside the observations
(`output/observations/adaptive_run.json`), written atomically after
every iteration so partial progress survives a stop. Schema version
`adaptive-run-v1`. Records: source video reference, sampling
configuration, duration, every requested timestamp, per-iteration speed
/ bounded reason / sampling diagnostics / observation filename, request
and failure counts, speed counts, termination reason (`video-end`,
`max-frames`, `stop-requested`, or a `*-failed` phase), final status
(`completed`, `stopped`, `failed`), and the failure detail when present.
Never contains secrets or image data.

## Stop semantics

A stop request is honored at loop boundaries only. An in-flight request
runs to completion, validates, and persists; the run then halts with
status `stopped` (never `completed`). If the in-flight request fails,
the failure is recorded while `stopped` is preserved as the terminal
status. No capture or paid request starts after a stop is observed;
repeated stops are idempotent.

## Cost discipline

At most one vision request per sampled frame; at most `max_frames`
frames per run (default 60, hard cap 10000); one finite SDK timeout per
request; zero retries. Tests use mocks and never touch the paid API.

## Compatibility

Changes to the wrapper shape, the filename extension, or the manifest
schema require a version bump and review of the scheduler, the worker
protocol, and downstream consumers. Interval value changes are tuning,
not contract changes, but stay inside the validated bounds.
