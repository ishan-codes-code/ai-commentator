# Contract: final casting script v1 (`script-v1`)

Versioned schema for the TTS-ready final commentary script (roadmap
milestone 9a: evidence-grounded LLM script generation). Produced by the
`--generate-script` path into `output/script/script.json` from validated
`observation-v1` files, their `match-state-v1` snapshot, and their
`events-v1` candidates. Consumed in the future by TTS; TTS itself is out
of scope and this contract carries no audio fields.

Unlike `commentary-v1`, the wording here is LLM-authored caster speech,
not deterministic templates. Traceability is preserved without verbatim
quotes: every segment cites the gated source claim ids that justify it,
timestamps are derived from that evidence (never invented by the model),
and validation enforces deterministic grounding checks
(see "Validation").

## Schema

Top-level value must be a JSON object with exactly these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string | yes, must equal `"script-v1"` | Contract version |
| `status` | `success`, `silence`, or `error` | yes | `success`: at least one validated segment; `silence`: valid inputs but nothing worth saying (no model call is made when no claims are eligible); `error`: the model request or validation failed — never a template fallback labeled as success |
| `generated_from` | object | yes | Replay provenance (see below) |
| `segments` | array of segment objects | yes (may be empty) | Spoken script in chronological order; empty for `silence`/`error` |
| `omitted` | array of omission objects | yes (may be empty) | Everything considered and skipped, with reasons (gating suppressions, repeat suppression, over-limit overflow, rejected model wording) |
| `error` | string or null | yes | Null for `success`/`silence`; non-empty diagnostic for `error` — never placed in speakable text |

`generated_from` has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `observations` | sorted array of non-empty strings | Every accepted observation filename |
| `match_state` | non-empty string | State snapshot filename (`match_state.json`) |
| `events` | non-empty string | Events filename (`events.json`) |

Each segment object has exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `segment_id` | non-empty string | Run-order id (`segment-001`, ... in chronological emission order) |
| `timestamp_seconds` | number | Latest cited evidence timestamp — never wall-clock time, never model-invented precision |
| `text` | non-empty string (8–600 chars) | TTS-ready spoken line: natural caster paraphrase, no JSON, field names, markdown, or diagnostics |
| `source_claim_ids` | non-empty array of non-empty strings | Stable gated claim ids (`clm-` + 12 hex chars, same identity as commentary claims); each id used at most once per document |

Each omission object has exactly these fields: `rule` (non-empty
string), `subject` (non-empty string), `reason` (non-empty string,
plain words).

## Validation

Shape checks always run. When the gated claim set is available
(`validate_script(data, claims_by_id)`), provenance checks also run:

- Every cited claim id is known; no id is used twice.
- Each segment timestamp equals the maximum timestamp of its cited claims.
- Segments are in chronological order.
- Each text passes `script_text_valid`: TTS shape, no invented
  `TAG.NAME` identities or numbers beyond the cited evidence (word
  numbers like "one" count as their digit), no kill/knock/damage,
  outcome, action, zone, causal, or intent claims the evidence does not
  contain, and preserved uncertainty (plus no certainty language) for
  candidate event echoes.
- Paraphrase is explicitly allowed: evidence quotes need not appear
  verbatim. Containment is not required; grounding is enforced by the
  checks above instead.

The checks are deterministic and need no second model call. A stronger
semantic judge was considered and rejected for this milestone: it would
add a paid request per segment with its own failure modes, while the
deterministic checks already reject the load-bearing risks (invented
identities, numbers, outcomes, actions, causes, and confirmed echoes).

## Compatibility

- `script-v1` is a new, distinct artifact. It does not change the
  meaning of `commentary-v1`, observations, match state, or events.
- Consumers must accept `silence` (empty segments, null error) as a
  valid outcome and must never speak the `error` or `omitted` fields.
- Field additions require a new schema version; readers must reject
  unknown top-level or segment fields rather than guessing.
