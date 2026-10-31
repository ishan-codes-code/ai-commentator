import json
import math
import re

from ..config import OBSERVATION_CATEGORIES, OBSERVATION_CONFIDENCES, OBSERVATION_SCHEMA_VERSION
from ..errors import ObservationError


def parse_observation(text):
    if not isinstance(text, str) or not text.strip():
        raise ObservationError("Baseten returned no text content.")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", cleaned, re.DOTALL)
        if match is None:
            raise ObservationError("Model output is not a single JSON value.")
        cleaned = match.group(1).strip()
        if not cleaned:
            raise ObservationError("Baseten returned no text content.")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ObservationError(f"Model output is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ObservationError("Model output must be a JSON object.")
    return data


def _reject_extra(mapping, allowed, where):
    extra = sorted(set(mapping) - set(allowed))
    if extra:
        raise ObservationError(f"{where} has unsupported fields: {', '.join(extra)}.")


def _require_non_empty_string(mapping, field, where):
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ObservationError(f"{where} requires a non-empty string field '{field}'.")
    return value


def validate_observation(data):
    if not isinstance(data, dict):
        raise ObservationError("Observation must be a JSON object.")
    _reject_extra(
        data,
        ("schema_version", "timestamp", "observations", "uncertainties"),
        "Observation",
    )
    if data.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise ObservationError(
            "Observation requires schema_version "
            f"'{OBSERVATION_SCHEMA_VERSION}'."
        )
    if "timestamp" not in data:
        raise ObservationError("Observation requires field 'timestamp'.")
    timestamp = data["timestamp"]
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or (isinstance(timestamp, float) and not math.isfinite(timestamp))
    ):
        raise ObservationError("Observation field 'timestamp' must be a number.")
    if "observations" not in data or not isinstance(data["observations"], list):
        raise ObservationError("Observation requires field 'observations' as a list.")
    for index, item in enumerate(data["observations"]):
        where = f"observations[{index}]"
        if not isinstance(item, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            item, ("category", "description", "confidence", "evidence"), where
        )
        if item.get("category") not in OBSERVATION_CATEGORIES:
            raise ObservationError(
                f"{where} has unsupported category "
                f"'{item.get('category')}'; expected one of: "
                f"{', '.join(OBSERVATION_CATEGORIES)}."
            )
        _require_non_empty_string(item, "description", where)
        if item.get("confidence") not in OBSERVATION_CONFIDENCES:
            raise ObservationError(
                f"{where} has invalid confidence '{item.get('confidence')}'; "
                f"expected one of: {', '.join(OBSERVATION_CONFIDENCES)}."
            )
        _require_non_empty_string(item, "evidence", where)
    if "uncertainties" not in data or not isinstance(data["uncertainties"], list):
        raise ObservationError("Observation requires field 'uncertainties' as a list.")
    for index, entry in enumerate(data["uncertainties"]):
        if not isinstance(entry, str) or not entry.strip():
            raise ObservationError(
                f"uncertainties[{index}] must be a non-empty string."
            )
    return data


