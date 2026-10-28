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

## 4. Develop a rolling match-state tracker

- **Goal:** maintain match state across frames (what is known, what changed).
- **Dependencies:** milestone 3.
- **Acceptance:** state updates deterministically from validated
  observations; state shape is documented in `docs/contracts/`.
- **Out of scope:** event judgments, commentary wording.

## 5. Detect meaningful changes, prevent unsupported event claims

- **Goal:** emit events only when validated observations support them.
- **Dependencies:** milestone 4.
- **Acceptance:** tests show supported events fire and unsupported claims
  are suppressed on fixture observations.
- **Out of scope:** commentary phrasing, audio.

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
