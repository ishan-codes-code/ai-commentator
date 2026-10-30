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
- **Status:** superseded. The engine behavior verified here is preserved,
  but the Docker runtime was replaced by the native desktop platform in
  milestone 11.

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
  validation, and failure reporting is implemented (219 unit tests pass in
  Docker, including the milestone-6 concurrency and client-lifecycle
  suites, the milestone-7 commentary suite, and the milestone-8 writer
  suite). The 5 stored live
  observations (timestamps 0, 48, 96, 147, 195)
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
- **Status:** complete. Sampling kept at the existing 3-second extraction
  interval (no stride or intensity classifier added — recorded video needs
  no live-stream queue). `--jobs` (default 1, hard max 4) bounds concurrent
  vision requests on the batch path; 219 unit tests pass in Docker,
  including barrier-rendezvous in-flight caps and reverse-completion-order
  identity, and a synthetic mocked benchmark (jobs=1: 1.51s vs jobs=4:
  0.61s for 5 fake requests, identical outputs) documents the bound choice.
  Live-rate measurements are explicitly out of scope: no Baseten calls were
  made, so provider-side rate tolerance remains unverified.

## 7. Generate concise, evidence-grounded commentary

- **Goal:** short commentary text grounded only in validated observations.
- **Dependencies:** milestones 2–5.
- **Acceptance:** sample outputs cite supporting observations; uncertain
  content is labeled; no invented events on fixtures.
- **Out of scope:** audio output.
- **Status:** complete. `--generate-commentary` emits `commentary-v1`
  lines from deterministic templates over gated claims (current facts,
  confident readings, candidate events with clean sources; qualifier
  mandatory on echoes, confidence never upgraded); 26 fixture tests show
  eligible lines emitted and unsupported claims suppressed with reasons,
  including silence-as-success, repeat convergence, and stale-input
  refusal. Real-tree run: 9 development lines, then converging silence —
  documented as the honest result for the current evidence.

## 8. Natural, evidence-grounded AI commentary

- **Goal:** natural caster-style wording over the gated M7 claims, without
  weakening evidence requirements.
- **Dependencies:** milestones 2–7.
- **Acceptance:** model-worded lines keep identity/provenance and pass
  claim-level validation; unsupported lines fall back; provider failure
  degrades to the deterministic run; no invented events on fixtures;
  no live calls in tests.
- **Out of scope:** audio output.
- **Status:** complete. `--commentary-write` words prioritized claims
  with one text-only model call (same endpoint/model, bounded prompt);
  every line is re-validated (evidence containment, token closure,
  verbatim qualifier) with per-line template fallback, and provider
  trouble falls back to the full deterministic run. 21 fixture tests
  cover valid output, malformed/unknown/unsupported lines, qualifier
  enforcement, repetition, silence, API errors, missing keys, and CLI
  wiring — all offline with fakes. Live writer runs remain opt-in and
  unverified.

## 8b. Evidence-grounded LLM casting script (final deliverable before TTS)

- **Goal:** a genuine TTS-ready esports casting script over the gated
  milestone-7 claims, with paraphrase allowed but evidence safety kept.
- **Dependencies:** milestones 2–8.
- **Acceptance:** `output/script/script.json` validates against
  `script-v1`; paraphrased facts pass, invented identities/numbers/
  outcomes/actions/causes and confirmed-echo wordings are rejected;
  provider or validation failure reports an explicit error, never a
  false success; silence needs no model call; no live calls in tests.
- **Out of scope:** audio output (TTS consumes `script-v1` in milestone 9).
- **Status:** complete. `--generate-script` reuses the Baseten/Muse Spark
  endpoint with one bounded text-only request per run (at most 6
  prioritized claims plus recent history, never frames or full history),
  validates every segment deterministically (names, numbers, forbidden
  semantic sets, echo uncertainty), derives timestamps from evidence,
  and fails safe with no template fallback. 22 fixture tests cover the
  acceptance cases — all offline with fakes. Live script runs remain
  opt-in and unverified.

## 9. Integrate TTS and audio playback

- **Goal:** speak commentary locally.
- **Dependencies:** milestones 7–8b (TTS consumes `script-v1` segments).
- **Acceptance:** each script segment's text produces playable audio locally.
- **Out of scope:** live capture, OBS.

## 10. Integrate live capture and OBS output

- **Goal:** move from recorded MP4 to live input with OBS audio routing.
- **Dependencies:** milestones 6–9.
- **Acceptance:** live session produces routed commentary audio end to end.
- **Out of scope:** (end of current plan).

## 11. Native desktop application (Electron + Python)

- **Goal:** run the preserved AI engine as a native desktop app instead
  of containers, with a defined Electron/Python interface.
- **Dependencies:** milestones 2–8b (engine behavior preserved, not rebuilt).
- **Acceptance:** engine moved to `python/main.py` with configurable
  video/output paths; `python/worker.py` orchestrates the pipeline over
  NDJSON stdout; Electron launches it via `spawn()`, streams progress
  over IPC, and supports cancel/cleanup; Python suite and Electron
  `node:test` suites pass with no paid calls; Docker files removed;
  production interpreter packaging explicitly out of scope.
- **Out of scope:** video-selection UI and dashboard, TTS, live capture,
  OBS, database, FastAPI.
- **Status:** complete. Next: renderer video-selection UI on the existing
  `processVideo`/`cancelProcessing`/`onWorkerEvent` API, then milestone 9.
