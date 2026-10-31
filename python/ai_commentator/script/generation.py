from pathlib import Path
import json
import math
import os
import re
import sys

from openai import OpenAI

from ..commentary.claims import gate_commentary_claims
from ..commentary.generation import load_prior_commentary
from ..commentary.writer import claim_id_of, prioritize_writer_claims, request_writer_text
from ..config import (
    BASETEN_BASE_URL,
    COMMENTARY_PATH,
    EVENTS_PATH,
    FRAMES_DIR,
    MATCH_STATE_PATH,
    OBSERVATIONS_DIR,
    SCRIPT_PATH,
    SCRIPT_SCHEMA_VERSION,
    SCRIPT_ACTION_STEMS,
    SCRIPT_CAUSAL_MARKERS,
    EVENT_NAME_RE,
    SCRIPT_CERTAINTY_MARKERS,
    SCRIPT_INTENT_MARKERS,
    SCRIPT_KILL_STEMS,
    SCRIPT_MAX_CLAIMS,
    SCRIPT_MAX_HISTORY_LINES,
    SCRIPT_MAX_TEXT_CHARS,
    SCRIPT_MIN_TEXT_CHARS,
    SCRIPT_OUTCOME_STEMS,
    SCRIPT_STATUSES,
    SCRIPT_UNCERTAINTY_MARKERS,
    SCRIPT_WORD_NUMBERS,
    SCRIPT_ZONE_STEMS,
    WRITER_MAX_EVIDENCE_CHARS,
)
from ..errors import ObservationError, WriterAPIError
from ..events.detection import _feed_name_tokens, validate_events
from ..state.match_state import observation_sha256, validate_match_state
from ..storage import write_json_atomic
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import discover_observation_files, warn_about_legacy_observations
from ..vision.observations import (
    _reject_extra,
    _require_non_empty_string,
    parse_observation,
    validate_observation,
)


def script_segment_id(index: int) -> str:
    """Segment id in emission order (chronological): segment-001, ..."""
    return f"segment-{index + 1:03d}"


def build_script_prompt(claims: list, history_texts: list) -> str:
    """Build the bounded prompt for the final-script model call.

    The model sees only evidence-gated claims plus recent spoken lines.
    It never sees raw frames, raw observations, or full match history,
    and it must return JSON mapping source claim ids to spoken segments.
    """
    parts = [
        "You are a skilled live esports commentator covering Free Fire. ",
        "Write broadcast-ready spoken lines, one segment per moment below. ",
        "Use concise, energetic sentences that are comfortable to speak aloud. ",
        "Prefer active voice and natural contractions. Vary sentence openings ",
        "and transitions. Keep it exciting without melodrama. ",
        "Prioritize confirmed eliminations and major combat developments, ",
        "then significant status changes, then match-state developments, ",
        "then continuity. Skip visually obvious but unimportant HUD details. ",
        "Never speak just to fill silence: if an item cannot support an ",
        "interesting, useful line, omit it. ",
        "Strict evidence rules: use only the quoted evidence below. Never add ",
        "kills, knocks, assists, damage, identities, teams, positions, ",
        "movements, weapons, strategies, intentions, zone status, rotations, ",
        "map control, placement, victory, defeat, causes, or effects beyond ",
        "the quotes. Never say a player is pushing, retreating, healing, ",
        "fighting, camping, rotating, or aiming unless the quote says so. ",
        "A candidate event is NOT confirmed: echo segments must keep an ",
        "uncertainty marker such as 'unconfirmed' or 'possible'. Never ",
        "present a candidate as a confirmed kill, knock, or victory, and ",
        "never name a killer, victim, or outcome the quotes do not give. ",
        "Mention a player name only when the quote contains it. Do not read ",
        "JSON, field names, UI labels, or long detail lists aloud. No ",
        "markdown, headings, emojis, or excessive exclamation marks. ",
        "Respond with ONLY a single JSON object, no markdown fences, no ",
        "commentary, matching this shape: ",
        '{"segments": [{"source_claim_ids": [<one or more ids below>], ',
        '"text": <the spoken line>}]}. ',
        "Omit any item you cannot word safely. ",
    ]
    for claim in claims:
        evidence = " / ".join(claim["evidence"])[:WRITER_MAX_EVIDENCE_CHARS]
        parts.append(
            f"Claim {claim['claim_id']} (at {claim['timestamp']}s, "
            f"confidence {claim['confidence']}, "
            f"evidence: {evidence})."
        )
        if claim["qualification"] is not None:
            parts.append(f" Keep uncertainty: {claim['qualification']}.")
        parts.append(" ")
    if history_texts:
        parts.append("Already said (do not repeat this information): ")
        parts.append(" | ".join(history_texts[-SCRIPT_MAX_HISTORY_LINES:]))
    return "".join(parts)


def _script_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _script_numbers(text: str) -> list:
    numbers = []
    for match in re.finditer(r"\d+(?:\.\d+)?", text):
        token = match.group(0)
        numbers.append(float(token) if "." in token else int(token))
    for word, value in SCRIPT_WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b", text.lower()):
            numbers.append(value)
    return numbers


def _stem_present(lowered: str, stems: tuple) -> bool:
    return any(stem in lowered for stem in stems)


def script_text_valid(text: str, cited_claims: list) -> bool:
    """Deterministic paraphrase check for one script segment.

    Paraphrase is allowed: evidence quotes need not appear verbatim.
    What is enforced instead: TTS-ready shape, no invented names or
    numbers, no forbidden semantic additions beyond the cited evidence,
    and preserved uncertainty for candidate events. Returns True only
    when the wording passes every check.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    stripped = text.strip()
    if not (SCRIPT_MIN_TEXT_CHARS <= len(stripped) <= SCRIPT_MAX_TEXT_CHARS):
        return False
    if "```" in text or text.lstrip().startswith("#"):
        return False
    if "**" in text or "claim_id" in text or "source_claim_ids" in text:
        return False
    if text.count("!") > 2:
        return False
    evidence_texts = []
    for claim in cited_claims:
        evidence_texts.extend(claim["evidence"])
    evidence_joined = " / ".join(evidence_texts)
    evidence_lower = evidence_joined.lower()
    text_lower = text.lower()
    evidence_names = set()
    for quote in evidence_texts:
        evidence_names.update(m.group(0) for m in EVENT_NAME_RE.finditer(quote))
    for token in _feed_name_tokens(text):
        if token not in evidence_names:
            return False
    evidence_numbers = set(_script_numbers(evidence_joined))
    cited_timestamps = {claim["timestamp"] for claim in cited_claims}
    for number in _script_numbers(text):
        if number not in evidence_numbers and number not in cited_timestamps:
            return False
    if not _stem_present(evidence_lower, SCRIPT_KILL_STEMS):
        if _stem_present(text_lower, SCRIPT_KILL_STEMS):
            return False
    if not _stem_present(evidence_lower, SCRIPT_OUTCOME_STEMS):
        if _stem_present(text_lower, SCRIPT_OUTCOME_STEMS):
            return False
    if not _stem_present(evidence_lower, SCRIPT_ACTION_STEMS):
        if _stem_present(text_lower, SCRIPT_ACTION_STEMS):
            return False
    if not _stem_present(evidence_lower, SCRIPT_ZONE_STEMS):
        if _stem_present(text_lower, SCRIPT_ZONE_STEMS):
            return False
    for marker in SCRIPT_CAUSAL_MARKERS:
        if marker in text_lower and marker not in evidence_lower:
            return False
    for marker in SCRIPT_INTENT_MARKERS:
        if marker in text_lower and marker not in evidence_lower:
            return False
    if any(claim.get("type") == "event_echo" for claim in cited_claims):
        if not any(m in text_lower for m in SCRIPT_UNCERTAINTY_MARKERS):
            return False
        # Word boundaries matter: "unconfirmed" must not trip "confirmed".
        if any(
            re.search(rf"\b{re.escape(marker)}\b", text_lower)
            for marker in SCRIPT_CERTAINTY_MARKERS
        ):
            return False
    return True


def parse_script_segments(text: str, claims_by_id: dict) -> list:
    """Parse model JSON into [(source_claim_ids, text)].

    Raises ObservationError on malformed JSON, unknown or duplicate claim
    ids, or empty wordings. Omissions (claims the model skipped) are fine
    and simply absent from the result.
    """
    data = parse_observation(text)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ObservationError(
            "Script output must be a JSON object with a 'segments' list."
        )
    _reject_extra(data, ("segments",), "Script output")
    parsed = []
    seen: set = set()
    for index, entry in enumerate(data["segments"]):
        where = f"script segments[{index}]"
        if not isinstance(entry, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(entry, ("source_claim_ids", "text"), where)
        ids = entry.get("source_claim_ids")
        wording = entry.get("text")
        if (
            not isinstance(ids, list) or not ids
            or any(not isinstance(i, str) or not i.strip() for i in ids)
        ):
            raise ObservationError(
                f"{where} requires 'source_claim_ids' as a non-empty list."
            )
        if not isinstance(wording, str) or not wording.strip():
            raise ObservationError(f"{where} requires a non-empty 'text'.")
        for claim_id in ids:
            if claim_id not in claims_by_id:
                raise ObservationError(
                    f"{where} cites unknown claim '{claim_id}'."
                )
            if claim_id in seen:
                raise ObservationError(
                    f"{where} duplicates claim '{claim_id}'."
                )
        for claim_id in ids:
            seen.add(claim_id)
        parsed.append((list(ids), wording))
    return parsed


def validate_script(data: dict, claims_by_id: dict | None = None) -> dict:
    """Strictly validate a script-v1 document; reject anything else.

    Shape is always checked. When ``claims_by_id`` (gated claims keyed by
    stable claim id) is supplied, provenance is checked too: every cited
    claim id must be known and used at most once, timestamps must equal
    the latest cited evidence timestamp, segments must be chronological,
    and each text must pass :func:`script_text_valid`. Diagnostics live
    in ``omitted``/``error``, never in speakable ``text``.
    """
    if not isinstance(data, dict):
        raise ObservationError("Script must be a JSON object.")
    _reject_extra(
        data,
        (
            "schema_version",
            "status",
            "generated_from",
            "segments",
            "omitted",
            "error",
        ),
        "Script",
    )
    if data.get("schema_version") != SCRIPT_SCHEMA_VERSION:
        raise ObservationError(
            f"Script requires schema_version '{SCRIPT_SCHEMA_VERSION}'."
        )
    if data.get("status") not in SCRIPT_STATUSES:
        raise ObservationError(
            f"Script has invalid status '{data.get('status')}'."
        )
    generated = data.get("generated_from")
    if not isinstance(generated, dict):
        raise ObservationError("Script requires field 'generated_from' as an object.")
    _reject_extra(
        generated, ("observations", "match_state", "events"), "Script generated_from"
    )
    generated_observations = generated.get("observations")
    if (
        not isinstance(generated_observations, list)
        or not generated_observations
        or any(
            not isinstance(item, str) or not item.strip()
            for item in generated_observations
        )
    ):
        raise ObservationError(
            "Script generated_from requires 'observations' as a "
            "non-empty list of filenames."
        )
    _require_non_empty_string(generated, "match_state", "Script generated_from")
    _require_non_empty_string(generated, "events", "Script generated_from")
    if "segments" not in data or not isinstance(data["segments"], list):
        raise ObservationError("Script requires field 'segments' as a list.")
    seen_ids: set = set()
    last_timestamp = None
    for index, segment in enumerate(data["segments"]):
        where = f"segments[{index}]"
        if not isinstance(segment, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            segment,
            ("segment_id", "timestamp_seconds", "text", "source_claim_ids"),
            where,
        )
        _require_non_empty_string(segment, "segment_id", where)
        if segment["segment_id"] in seen_ids:
            raise ObservationError(f"{where} duplicates segment id.")
        seen_ids.add(segment["segment_id"])
        timestamp = segment.get("timestamp_seconds")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or (isinstance(timestamp, float) and not math.isfinite(timestamp))
        ):
            raise ObservationError(f"{where} field 'timestamp_seconds' must be a number.")
        if last_timestamp is not None and timestamp < last_timestamp:
            raise ObservationError(f"{where} breaks chronological ordering.")
        last_timestamp = timestamp
        _require_non_empty_string(segment, "text", where)
        stripped = segment["text"].strip()
        if not (SCRIPT_MIN_TEXT_CHARS <= len(stripped) <= SCRIPT_MAX_TEXT_CHARS):
            raise ObservationError(f"{where} text has an invalid length.")
        if "```" in segment["text"] or "claim_id" in segment["text"]:
            raise ObservationError(f"{where} text is not TTS-ready.")
        ids = segment.get("source_claim_ids")
        if (
            not isinstance(ids, list) or not ids
            or any(not isinstance(i, str) or not i.strip() for i in ids)
        ):
            raise ObservationError(
                f"{where} requires 'source_claim_ids' as a non-empty list."
            )
    if "omitted" not in data or not isinstance(data["omitted"], list):
        raise ObservationError("Script requires field 'omitted' as a list.")
    for index, entry in enumerate(data["omitted"]):
        entry_where = f"omitted[{index}]"
        if not isinstance(entry, dict):
            raise ObservationError(f"{entry_where} must be an object.")
        _reject_extra(entry, ("rule", "subject", "reason"), entry_where)
        _require_non_empty_string(entry, "rule", entry_where)
        _require_non_empty_string(entry, "subject", entry_where)
        _require_non_empty_string(entry, "reason", entry_where)
    error = data.get("error")
    if error is not None and (not isinstance(error, str) or not error.strip()):
        raise ObservationError("Script field 'error' must be a string or null.")
    status = data.get("status")
    if status == "success" and not data["segments"]:
        raise ObservationError("Script status 'success' requires segments.")
    if status == "silence" and data["segments"]:
        raise ObservationError("Script status 'silence' requires no segments.")
    if status == "silence" and error is not None:
        raise ObservationError("Script status 'silence' requires a null error.")
    if status == "success" and error is not None:
        raise ObservationError("Script status 'success' requires a null error.")
    if status == "error" and error is None:
        raise ObservationError("Script status 'error' requires an error message.")
    if status == "error" and data["segments"]:
        raise ObservationError("Script status 'error' requires no segments.")
    if claims_by_id is not None:
        used: set = set()
        for index, segment in enumerate(data["segments"]):
            where = f"segments[{index}]"
            cited = []
            for claim_id in segment["source_claim_ids"]:
                if claim_id not in claims_by_id:
                    raise ObservationError(
                        f"{where} cites unknown claim '{claim_id}'."
                    )
                if claim_id in used:
                    raise ObservationError(
                        f"{where} duplicates claim '{claim_id}'."
                    )
                used.add(claim_id)
                cited.append(claims_by_id[claim_id])
            expected = max(claim["timestamp"] for claim in cited)
            if segment["timestamp_seconds"] != expected:
                raise ObservationError(
                    f"{where} timestamp does not match its cited evidence."
                )
            if not script_text_valid(segment["text"], cited):
                raise ObservationError(
                    f"{where} text is not grounded in its cited evidence."
                )
    return data


def load_prior_script(script_path: Path) -> tuple:
    """Read the previous script file; return (claim_ids, texts).

    A missing file means a first run; a malformed one is reported with a
    warning and treated as a first run. Claim ids feed repeat suppression;
    texts feed writer continuity.
    """
    if not script_path.is_file():
        return set(), []
    try:
        prior = validate_script(
            json.loads(script_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        print(
            f"Warning: ignoring malformed prior script {script_path}: "
            f"{exc}. Proceeding as a first run.",
            file=sys.stderr,
        )
        return set(), []
    claim_ids = set()
    for segment in prior["segments"]:
        claim_ids.update(segment["source_claim_ids"])
    claim_ids.update(
        entry["subject"]
        for entry in prior["omitted"]
        if entry["rule"] == "already-emitted"
    )
    return claim_ids, [segment["text"] for segment in prior["segments"]]
