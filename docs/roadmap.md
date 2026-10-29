# Roadmap (proposed)

Incremental milestones. This is a proposed plan, not a claim that any
future work is complete. Each milestone lists its goal, dependencies,
acceptance criteria, and what is explicitly out of scope.

## 1. Stabilize and checkpoint the existing Docker MVP

- **Goal:** reproducible build, extraction, and one-frame vision test.
- **Dependencies:** none.
- **Acceptance:** image builds; default command extracts frames from the
  recorded MP4; `--vision-test` returns a description of `frame_00000.jpg`.
- **Out of scope:** new features, schema work, networking changes.

## 2. Standardize vision output as validated structured JSON

- **Goal:** a documented schema for one frame observation, validated before use.
- **Dependencies:** milestone 1.
- **Acceptance:** vision response for the single test frame parses against
  an explicit schema; invalid output is rejected with a clear error.
- **Out of scope:** multi-frame runs, state tracking, commentary text.

## 3. Test observations across selected frames

- **Goal:** run the validated observation step over a small set of existing
  frames and inspect quality/coverage.
- **Dependencies:** milestone 2.
- **Acceptance:** observations for N selected frames validate; failure modes
  documented.
- **Out of scope:** tracking state across frames, commentary.
- **Status:** complete. `--vision-batch` with spread selection, per-frame
  validation, and failure reporting is implemented (118 unit tests pass in
  Docker). The 5 stored live observations (timestamps 0, 48, 96, 147, 195)
  validate against `observation-v1` and were content-surveyed; failure modes
  are documented in `docs/current-state.md` and `docs/testing.md`.

## 4. Develop a rolling match-state tracker

- **Goal:** maintain match state across frames (what is known, what changed).
- **Dependencies:** milestone 3.
- **Acceptance:** state updates deterministically from validated
  observations; state shape is documented in `docs/contracts/`.
- **Out of scope:** event judgments, commentary wording.
- **Status:** complete. `--rebuild-state` builds a validated
  `match-state-v1` snapshot offline from stored observations (deterministic
  rebuilds verified byte-identical on fixtures and on the real 5
  observations); the shape and update semantics are documented in
  `docs/contracts/match-state-v1.md`.

## 5. Detect meaningful changes, prevent unsupported event claims

- **Goal:** emit events only when validated observations support them.
- **Dependencies:** milestone 4.
- **Acceptance:** tests show supported events fire and unsupported claims
  are suppressed on fixture observations.
- **Out of scope:** commentary phrasing, audio.
- **Status:** complete. `--detect-events` emits `events-v1` candidates from
  two conservative rules (transcribed kill feed, team-scoped elim-counter
  increase) with full provenance; 34 fixture tests show supported events
  firing and unsupported claims (missing nameplates, ambiguous HUD,
  timers, persistent banners, low confidence, contested evidence)
  suppressed. Real-tree run: 5 accepted, 0 emitted — documented as the
  honest result for the current evidence.

## 6. Add controlled sampling and bounded request concurrency

- **Goal:** choose sampling frequency and concurrency limits by experiment.
- **Dependencies:** milestones 3–5.
- **Acceptance:** documented experiments justify the chosen rate and bounds;
  resource usage stays within limits.
- **Out of scope:** commentary, TTS.

## 7. Generate concise, evidence-grounded commentary

- **Goal:** short commentary text grounded only in validated observations.
- **Dependencies:** milestones 2–5.
- **Acceptance:** sample outputs cite supporting observations; uncertain
  content is labeled; no invented events on fixtures.
- **Out of scope:** audio output.

## 8. Integrate TTS and audio playback

- **Goal:** speak commentary locally.
- **Dependencies:** milestone 7.
- **Acceptance:** text produces playable audio locally.
- **Out of scope:** live capture, OBS.

## 9. Integrate live capture and OBS output

- **Goal:** move from recorded MP4 to live input with OBS audio routing.
- **Dependencies:** milestones 6–8.
- **Acceptance:** live session produces routed commentary audio end to end.
- **Out of scope:** (end of current plan).
