# Contract: frame observation v1 (`observation-v1`)

Versioned schema for one validated vision-model observation of a single
gameplay frame. Produced by the `--vision-test` path, consumed in the future
by rolling match-state tracking (roadmap milestone 4). Strict validation:
anything not conforming is rejected, never guessed.

## Why not constrained decoding

Per [Baseten's structured-outputs documentation](https://docs.baseten.co/inference/structured-outputs),
Model APIs accept `json_schema` but **Muse Spark Open does not enforce the
schema**. Our configured model (`meta-models/muse-spark-open-1026`) is a
Muse Spark Open model, so server-side enforcement is unavailable to us.
We therefore request JSON via the prompt and validate strictly in code
(`parse_observation` / `validate_observation` in `main.py`). If a future
model enforces schemas, this contract is already written as JSON Schema
compatible input — but re-verify against that model before relying on it.

## Schema

Top-level value must be a JSON object with exactly these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string | yes, must equal `"observation-v1"` | Contract version |
| `timestamp` | number (int or float) | yes | Frame position in seconds within the source video, as stated in the prompt |
| `observations` | array of observation objects | yes (may be empty) | Directly visible evidence, one entry per distinct thing seen |
| `uncertainties` | array of non-empty strings | yes (may be empty) | What is unreadable, ambiguous, or uncertain — never invented detail |

Each observation object has exactly these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `category` | string, one of `environment`, `character`, `hud`, `combat`, `other` | yes | What kind of thing is observed |
| `description` | non-empty string | yes | What is visible |
| `confidence` | string, one of `high`, `medium`, `low` | yes | How certain the observation is |
| `evidence` | non-empty string | yes | Visible detail grounding the claim (e.g. quoted on-screen text, position, color) |

## Validation rules

Reject with a clear error (never silently accept or fabricate):

- Malformed JSON, empty model output, or a top-level value that is not an object.
- Missing required fields or wrong types (`timestamp` must be a true
  number — booleans are rejected; strings must be non-empty where required).
- `category` outside the permitted set; `confidence` outside
  `high`/`medium`/`low`.
- Unknown extra fields at any level (strict shape keeps later consumers safe).
- Optional markdown code fences around the JSON are tolerated and stripped;
  anything else around the JSON is rejected.

## Representing unknown or ambiguous information

- Do not invent a category, description, evidence, or confidence value.
- Anything unreadable or ambiguous goes into `uncertainties` as plain text.
- An observation with `confidence: "low"` is still a claim about something
  seen; pure guesses belong in `uncertainties`, not in `observations`.
- A frame with nothing describable is `observations: []` with the reason
  recorded in `uncertainties` — not fabricated content.

## Example

```json
{
  "schema_version": "observation-v1",
  "timestamp": 0,
  "observations": [
    {
      "category": "hud",
      "description": "Kill counter in the top bar.",
      "confidence": "medium",
      "evidence": "Digits in the top-center bar, partly obscured."
    }
  ],
  "uncertainties": ["Player name text is too small to read."]
}
```

## Compatibility

Changes to this contract require bumping `schema_version` and reviewing all
consumers. Additive relaxation (e.g. a new category) is a minor change but
still review-gated; tightening or renaming is breaking.
