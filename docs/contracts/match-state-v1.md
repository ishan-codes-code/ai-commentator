# Contract: rolling match state v1 (`match-state-v1`)

Versioned schema for the rolling match-state snapshot built from validated
`observation-v1` JSON files (roadmap milestone 4). Produced offline by the
`--rebuild-state` path from `output/observations/frame_*.json`; consumed in
the future by event detection (milestone 5). The tracker performs no vision
calls, no text interpretation, and no Free Fire reasoning: it only files,
orders, and links claims the observations already make.

## Schema

Top-level value must be a JSON object with exactly these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string | yes, must equal `"match-state-v1"` | Contract version |
| `latest_timestamp` | number or null | yes | Highest source timestamp applied; null only when no observations were applied |
| `sources` | array of source objects | yes (may be empty) | One entry per applied observation file, sorted by timestamp |
| `facts` | array of fact objects | yes (may be empty) | Distinct claims, sorted by fact id |
| `changes` | array of change objects | yes (may be empty) | New facts and supported updates, sorted by (timestamp, kind, fact id) |
| `conflicts` | array of conflict objects | yes (may be empty) | Same-timestamp divergent observations, sorted by timestamp |
| `uncertainties` | array of uncertainty objects | yes (may be empty) | Unresolved unknowns, sorted by text |

Each source object has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | number | Source observation timestamp |
| `file` | non-empty string | Observation filename (e.g. `frame_00048.json`) |
| `observations` | integer >= 0 | Number of observation entries in that file |
| `uncertainties` | integer >= 0 | Number of uncertainty strings in that file |
| `sha256` | non-empty string | Hex digest of the validated source document (canonical JSON), used for duplicate detection |

Each fact object has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | non-empty string | Deterministic id (`f0001`, …) assigned in first-seen order |
| `category` | one of the `observation-v1` categories | Copied verbatim from the observation |
| `description` | non-empty string | Copied verbatim from the latest sighting |
| `confidence` | one of `high`/`medium`/`low` | The model's label from the latest sighting — a label, not proof |
| `evidence` | non-empty string | Copied verbatim from the latest sighting |
| `first_seen` | number | Earliest timestamp this fact key was observed |
| `last_seen` | number | Latest timestamp this fact key was observed |
| `last_source` | non-empty string | Filename of the latest sighting |
| `status` | one of `current`/`stale`/`conflicted` | Derived (see "Statuses" below) |
| `history` | array of sighting objects, sorted by (timestamp, source) | Every sighting: `{timestamp, source, confidence, evidence}` with the same field rules |

Each change object has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | number | When the change was observed |
| `kind` | `new_fact` or `update` | First sighting of a fact key, or a re-sighting with different confidence/evidence |
| `fact_id` | non-empty string | The affected fact |
| `detail` | non-empty string | Short human-readable note (e.g. what changed) |
| `source` | non-empty string | Observation filename that caused the change |

Each conflict object has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | number | The contested timestamp |
| `sources` | array of two or more filenames, sorted | Every divergent source preserved — none is discarded |
| `detail` | non-empty string | Short note naming the divergent sources |

Each uncertainty object has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `text` | non-empty string | The uncertainty string, verbatim |
| `first_seen` | number | Earliest timestamp carrying this text |
| `last_seen` | number | Latest timestamp carrying this text |
| `sources` | sorted array of filenames | Every observation carrying this text |
| `resolved` | boolean, always `false` in v1 | No rule resolves uncertainties yet (see below) |

## Update semantics

- **Input validation:** every file must parse as JSON, pass
  `observation-v1` validation, and carry the timestamp its filename states.
  Anything else is rejected before it can touch the state.
- **Processing order:** observations apply in numeric timestamp order
  (filename order breaks ties). The builder also accepts out-of-order input
  and converges to the same state (see "Determinism").
- **Fact identity:** a fact key is the exact `(category, description)` pair
  after stripping surrounding whitespace. No fuzzy matching, no name
  parsing, no cross-category merging. A reworded claim is a different fact.
- **Sightings:** each sighting appends to the fact's history and advances
  `last_seen`/`last_source`; top-level description/confidence/evidence always
  reflect the latest sighting. A re-sighting with identical confidence and
  evidence advances silently; one with different confidence or evidence emits
  an `update` change.
- **Statuses:** `current` means last seen at the latest processed timestamp;
  `stale` means last seen earlier — i.e. not re-observed, **not** disproven.
  `conflicted` means the fact's first sighting belongs to a contested
  timestamp (see "Conflicts"). Statuses are recomputed from sightings; they
  are never stored guesses.
- **Conflicts:** only structural conflicts are detected — two or more
  divergent observation documents for the same timestamp. Both are applied in
  filename order and both provenances are preserved in `conflicts`; nothing
  is silently chosen. Textual contradictions across different timestamps are
  **not** inferred (see "Not inferred").
- **Duplicates:** re-applying an identical document (same timestamp and
  content hash) is a no-op. Rebuilds are idempotent.
- **Uncertainties:** carried verbatim with provenance. Nothing in v1 marks an
  uncertainty resolved; `resolved` stays `false` so consumers never mistake
  carried-over text for settled knowledge.
- **Unknown vs disproven:** a fact absent from the state was never observed.
  A `stale` fact was observed before but not lately. Nothing in v1 records a
  fact as false — absence of evidence is never evidence of absence.

## Determinism

The same set of source observations always produces byte-identical state:
inputs sort by (timestamp, filename); fact ids assign in first-seen order;
facts, history, changes, conflicts, uncertainties, and source lists are all
emitted sorted; the document contains no wall-clock time, no random ids, and
no Arrival-order artifacts. Late/out-of-order input converges because
`first_seen`/`last_seen` combine with min/max and ordering is re-derived at
the end, never from arrival sequence.

## Persistence and recovery

- The snapshot lives at `output/observations/match_state.json`.
- It is written atomically (temporary file + replace) only after the built
  state passes `match-state-v1` validation, so a failure never leaves a
  partial or corrupt snapshot; the previous valid snapshot stays intact.
- Source observation files are read-only inputs and are never rewritten.
- Recovery is a rebuild: delete nothing — re-running `--rebuild-state`
  deterministically regenerates the snapshot from the observation files.
- The snapshot intentionally stores no processing history beyond `sources`:
  the observation files are the source of truth, the snapshot is derived.

## What the tracker explicitly does not infer

- Eliminations, deaths, knocks, or team wipes — a missing nameplate only
  leaves an older fact `stale`.
- The meaning of HUD numbers or timers — readings are filed verbatim.
- Combat events from timers, banners, or effects alone.
- Movement, intent, or match outcomes.
- Entity identity — similar names are never merged; each distinct claim
  stands alone.
- Correctness — a `high` model confidence is filed as a label, never
  treated as proof.
- Contradictions between different timestamps — only same-timestamp
  divergent documents are flagged, structurally.

## Compatibility

Changes to this contract require bumping `schema_version` and reviewing all
consumers. Additive relaxation is review-gated; tightening or renaming is
breaking. Consumers must tolerate unknown `kind`/`status` values
gracefully (treat as opaque) rather than crashing, so minor additions do
not break readers.
