# Intended architecture

This document describes the intended end-state system. It is a plan, not a
claim about existing code. For what actually exists today, see
[current-state.md](current-state.md). Each component below is labeled with its
implementation status.

## Data flow (intended)

Recorded or live gameplay → frame sampling → vision provider adapter →
structured observation validation → rolling match context and event tracking
→ evidence-aware commentary generation → text-to-speech → local playback,
and eventually OBS audio integration.

## Components

### 1. Gameplay capture and frame sampling

- **Responsibility:** obtain gameplay images at a controlled rate and hand
  them to the vision stage.
- **Status:** partially implemented. The current input is a recorded MP4
  (`gameplay/match.mp4`, sampled every 3 seconds into `output/frames/`).
  Live capture is a future capability, not implemented.
- **Open questions:** sampling frequency and request-concurrency limits still
  require experimentation (see [roadmap.md](roadmap.md)).

### 2. Vision provider adapter

- **Responsibility:** send a frame to the vision model and return its
  response. Isolate provider-specific API logic (base URL, model ID, request
  shape, error mapping) behind a clear boundary so the provider can change
  without touching pipeline logic.
- **Status:** implemented for the single test frame and the multi-frame
  batch (structured `observation-v1` JSON via Baseten); live multi-frame
  use verified on 5 frames.

### 3. Structured observation validation

- **Responsibility:** parse the model response into an explicit schema and
  reject or flag anything that does not conform, before downstream stages
  may use it.
- **Status:** implemented for the single-frame case
  (`observation-v1` in [contracts/observation-v1.md](contracts/observation-v1.md),
  validated by `parse_observation` / `validate_observation`). Contract
  changes must be reviewed for compatibility
  (see [contracts/README.md](contracts/README.md)).

### 4. Rolling match context and event tracking

- **Responsibility:** maintain match state across frames (what is known,
  what changed) so commentary can reference developments over time.
- **Status:** match state implemented (`match-state-v1`, rebuilt offline
  via `python main.py --rebuild-state`); deterministic event detection
  implemented (`events-v1`, via `python main.py --detect-events`,
  candidate-only statuses). Richer event judgments remain future work.

### 5. Evidence-aware commentary generation

- **Responsibility:** produce concise commentary grounded only in validated
  observations. Must distinguish direct observations from uncertain
  interpretations and must not invent events, identities, or outcomes.
- **Status:** planned. Today's vision prompt already encodes this rule in
  prose; a structured, enforced version is future work.

### 6. Text-to-speech

- **Responsibility:** convert commentary text to speech audio.
- **Status:** planned.

### 7. Local application and eventual OBS audio integration

- **Responsibility:** play or route commentary audio locally first, then
  integrate with OBS output for live use.
- **Status:** planned.

## Boundaries

- Pipeline stages communicate through explicit schemas
  (see [contracts/README.md](contracts/README.md)).
- Provider-specific code stays behind the vision adapter boundary.
- Untrusted data (API responses, game text, model output) is validated at
  each boundary before application logic uses it.
