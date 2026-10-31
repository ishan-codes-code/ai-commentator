from pathlib import Path
import hashlib
import json
import math
import sys

from ..config import (
    FRAMES_DIR,
    MATCH_STATE_PATH,
    OBSERVATIONS_DIR,
    MATCH_STATE_CHANGE_KINDS,
    MATCH_STATE_SCHEMA_VERSION,
    MATCH_STATE_STATUSES,
    OBSERVATION_CATEGORIES,
    OBSERVATION_CONFIDENCES,
)
from ..errors import ObservationError
from ..storage import write_match_state
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import (
    discover_observation_files,
    find_legacy_observations,
    warn_about_legacy_observations,
)
from ..vision.observations import (
    _reject_extra,
    _require_non_empty_string,
    parse_observation,
    validate_observation,
)


def observation_sha256(validated: dict) -> str:
    """Hex digest of a validated observation's canonical JSON form."""
    canonical = json.dumps(
        validated, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fact_key(category: str, description: str) -> tuple[str, str]:
    """Identity of a match-state fact shared by the tracker and detector.

    The exact ``(category, description)`` pair after stripping surrounding
    whitespace — no fuzzy matching, no name parsing, no cross-category
    merging. A reworded claim is a different fact.
    """
    return (category, description.strip())


def new_match_state_builder() -> dict:
    """Return an empty working builder for match-state construction.

    The builder carries private bookkeeping (``_seen``, ``_contested``) that
    :func:`finalize_match_state` strips; only the finalized document
    conforms to ``match-state-v1``.
    """
    return {
        "schema_version": MATCH_STATE_SCHEMA_VERSION,
        "latest_timestamp": None,
        "sources": [],
        "facts": {},
        "changes": [],
        "conflicts": [],
        "uncertainties": {},
        "_seen": {},
        "_contested": set(),
    }


def validate_match_state(data: dict) -> dict:
    """Strictly validate a match-state-v1 document; reject anything else."""
    if not isinstance(data, dict):
        raise ObservationError("Match state must be a JSON object.")
    _reject_extra(
        data,
        (
            "schema_version",
            "latest_timestamp",
            "sources",
            "facts",
            "changes",
            "conflicts",
            "uncertainties",
        ),
        "Match state",
    )
    if data.get("schema_version") != MATCH_STATE_SCHEMA_VERSION:
        raise ObservationError(
            "Match state requires schema_version "
            f"'{MATCH_STATE_SCHEMA_VERSION}'."
        )
    latest = data.get("latest_timestamp")
    if latest is not None and (
        isinstance(latest, bool)
        or not isinstance(latest, (int, float))
        or (isinstance(latest, float) and not math.isfinite(latest))
    ):
        raise ObservationError("Match state field 'latest_timestamp' must be "
                               "a number or null.")
    for field in ("sources", "facts", "changes", "conflicts", "uncertainties"):
        if field not in data or not isinstance(data[field], list):
            raise ObservationError(
                f"Match state requires field '{field}' as a list."
            )
    for index, source in enumerate(data["sources"]):
        where = f"sources[{index}]"
        if not isinstance(source, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            source,
            ("timestamp", "file", "observations", "uncertainties", "sha256"),
            where,
        )
        timestamp = source.get("timestamp")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or (isinstance(timestamp, float) and not math.isfinite(timestamp))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        _require_non_empty_string(source, "file", where)
        for count_field in ("observations", "uncertainties"):
            count = source.get(count_field)
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise ObservationError(
                    f"{where} field '{count_field}' must be a non-negative integer."
                )
        _require_non_empty_string(source, "sha256", where)
    for index, fact in enumerate(data["facts"]):
        where = f"facts[{index}]"
        if not isinstance(fact, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            fact,
            (
                "id",
                "category",
                "description",
                "confidence",
                "evidence",
                "first_seen",
                "last_seen",
                "last_source",
                "status",
                "history",
            ),
            where,
        )
        _require_non_empty_string(fact, "id", where)
        if fact.get("category") not in OBSERVATION_CATEGORIES:
            raise ObservationError(
                f"{where} has unsupported category '{fact.get('category')}'."
            )
        _require_non_empty_string(fact, "description", where)
        if fact.get("confidence") not in OBSERVATION_CONFIDENCES:
            raise ObservationError(
                f"{where} has invalid confidence '{fact.get('confidence')}'."
            )
        _require_non_empty_string(fact, "evidence", where)
        for seen_field in ("first_seen", "last_seen"):
            seen = fact.get(seen_field)
            if (
                isinstance(seen, bool)
                or not isinstance(seen, (int, float))
                or (isinstance(seen, float) and not math.isfinite(seen))
            ):
                raise ObservationError(f"{where} field '{seen_field}' must be a number.")
        if fact["first_seen"] > fact["last_seen"]:
            raise ObservationError(
                f"{where} has first_seen after last_seen."
            )
        _require_non_empty_string(fact, "last_source", where)
        if fact.get("status") not in MATCH_STATE_STATUSES:
            raise ObservationError(
                f"{where} has invalid status '{fact.get('status')}'."
            )
        if "history" not in fact or not isinstance(fact["history"], list):
            raise ObservationError(f"{where} requires field 'history' as a list.")
        for entry_index, entry in enumerate(fact["history"]):
            entry_where = f"{where}.history[{entry_index}]"
            if not isinstance(entry, dict):
                raise ObservationError(f"{entry_where} must be an object.")
            _reject_extra(
                entry, ("timestamp", "source", "confidence", "evidence"), entry_where
            )
            entry_ts = entry.get("timestamp")
            if (
                isinstance(entry_ts, bool)
                or not isinstance(entry_ts, (int, float))
                or (isinstance(entry_ts, float) and not math.isfinite(entry_ts))
            ):
                raise ObservationError(
                    f"{entry_where} field 'timestamp' must be a number."
                )
            _require_non_empty_string(entry, "source", entry_where)
            if entry.get("confidence") not in OBSERVATION_CONFIDENCES:
                raise ObservationError(
                    f"{entry_where} has invalid confidence "
                    f"'{entry.get('confidence')}'."
                )
            _require_non_empty_string(entry, "evidence", entry_where)
    for index, change in enumerate(data["changes"]):
        where = f"changes[{index}]"
        if not isinstance(change, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            change, ("timestamp", "kind", "fact_id", "detail", "source"), where
        )
        change_ts = change.get("timestamp")
        if (
            isinstance(change_ts, bool)
            or not isinstance(change_ts, (int, float))
            or (isinstance(change_ts, float) and not math.isfinite(change_ts))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        if change.get("kind") not in MATCH_STATE_CHANGE_KINDS:
            raise ObservationError(
                f"{where} has invalid kind '{change.get('kind')}'."
            )
        _require_non_empty_string(change, "fact_id", where)
        _require_non_empty_string(change, "detail", where)
        _require_non_empty_string(change, "source", where)
    for index, conflict in enumerate(data["conflicts"]):
        where = f"conflicts[{index}]"
        if not isinstance(conflict, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(conflict, ("timestamp", "sources", "detail"), where)
        conflict_ts = conflict.get("timestamp")
        if (
            isinstance(conflict_ts, bool)
            or not isinstance(conflict_ts, (int, float))
            or (isinstance(conflict_ts, float) and not math.isfinite(conflict_ts))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        sources = conflict.get("sources")
        if (
            not isinstance(sources, list)
            or len(sources) < 2
            or any(not isinstance(item, str) or not item.strip() for item in sources)
        ):
            raise ObservationError(
                f"{where} requires 'sources' as a list of at least two "
                f"non-empty strings."
            )
        _require_non_empty_string(conflict, "detail", where)
    for index, uncertainty in enumerate(data["uncertainties"]):
        where = f"uncertainties[{index}]"
        if not isinstance(uncertainty, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            uncertainty,
            ("text", "first_seen", "last_seen", "sources", "resolved"),
            where,
        )
        _require_non_empty_string(uncertainty, "text", where)
        for seen_field in ("first_seen", "last_seen"):
            seen = uncertainty.get(seen_field)
            if (
                isinstance(seen, bool)
                or not isinstance(seen, (int, float))
                or (isinstance(seen, float) and not math.isfinite(seen))
            ):
                raise ObservationError(f"{where} field '{seen_field}' must be a number.")
        if uncertainty["first_seen"] > uncertainty["last_seen"]:
            raise ObservationError(
                f"{where} has first_seen after last_seen."
            )
        sources = uncertainty.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(item, str) or not item.strip() for item in sources)
        ):
            raise ObservationError(
                f"{where} requires 'sources' as a non-empty list of strings."
            )
        if not isinstance(uncertainty.get("resolved"), bool):
            raise ObservationError(f"{where} field 'resolved' must be a boolean.")
    return data


def apply_observation(builder: dict, validated: dict, source_name: str) -> str:
    """Fold one validated observation into a working match-state builder.

    Fact identity is the exact ``(category, description)`` pair — no fuzzy
    matching, no name parsing, no cross-category merging. Returns
    ``"applied"``, ``"duplicate"`` (same timestamp and content already
    processed — a no-op), or ``"conflict"`` (same timestamp, different
    content — both provenances preserved, neither silently chosen).
    """
    timestamp = validated["timestamp"]
    digest = observation_sha256(validated)
    seen = builder["_seen"].setdefault(timestamp, {"digests": set(), "sources": []})
    if digest in seen["digests"]:
        return "duplicate"
    # A conflict needs two distinct provenances: the same source restating
    # changed content is applied as an ordinary update, not a conflict.
    contested = bool(seen["digests"]) and len(set(seen["sources"]) | {source_name}) >= 2
    if contested:
        builder["_contested"].add(timestamp)
        existing = next(
            (
                entry
                for entry in builder["conflicts"]
                if entry["timestamp"] == timestamp
            ),
            None,
        )
        merged = sorted(set(seen["sources"]) | {source_name})
        if existing is None:
            builder["conflicts"].append(
                {
                    "timestamp": timestamp,
                    "sources": merged,
                    "detail": (
                        f"Divergent observations for timestamp {timestamp!r}: "
                        f"{', '.join(merged)}."
                    ),
                }
            )
        else:
            existing["sources"] = sorted(set(existing["sources"]) | {source_name})
            existing["detail"] = (
                f"Divergent observations for timestamp {timestamp!r}: "
                f"{', '.join(existing['sources'])}."
            )
    seen["digests"].add(digest)
    seen["sources"].append(source_name)
    builder["sources"].append(
        {
            "timestamp": timestamp,
            "file": source_name,
            "observations": len(validated["observations"]),
            "uncertainties": len(validated["uncertainties"]),
            "sha256": digest,
        }
    )
    if builder["latest_timestamp"] is None or timestamp > builder["latest_timestamp"]:
        builder["latest_timestamp"] = timestamp
    for item in validated["observations"]:
        key = fact_key(item["category"], item["description"])
        fact = builder["facts"].get(key)
        if fact is None:
            fact = {
                "category": item["category"],
                "description": item["description"].strip(),
                "sightings": [],
            }
            builder["facts"][key] = fact
            builder["changes"].append(
                {
                    "timestamp": timestamp,
                    "kind": "new_fact",
                    "key": key,
                    "detail": f"First observed ({item['category']}): "
                    f"{item['description'].strip()[:120]}",
                    "source": source_name,
                }
            )
        else:
            latest_sighting = max(
                fact["sightings"], key=lambda entry: (entry["timestamp"], entry["source"])
            )
            if (
                latest_sighting["confidence"] != item["confidence"]
                or latest_sighting["evidence"] != item["evidence"]
            ):
                builder["changes"].append(
                    {
                        "timestamp": timestamp,
                        "kind": "update",
                        "key": key,
                        "detail": (
                            f"Re-observed ({item['category']}) with different "
                            f"confidence/evidence."
                        ),
                        "source": source_name,
                    }
                )
        fact["sightings"].append(
            {
                "timestamp": timestamp,
                "source": source_name,
                "confidence": item["confidence"],
                "evidence": item["evidence"],
            }
        )
    for text in validated["uncertainties"]:
        entry = builder["uncertainties"].get(text)
        if entry is None:
            builder["uncertainties"][text] = {
                "first_seen": timestamp,
                "last_seen": timestamp,
                "sources": [source_name],
            }
        else:
            entry["first_seen"] = min(entry["first_seen"], timestamp)
            entry["last_seen"] = max(entry["last_seen"], timestamp)
            if source_name not in entry["sources"]:
                entry["sources"].append(source_name)
    return "conflict" if contested else "applied"


def finalize_match_state(builder: dict) -> dict:
    """Convert a working builder into a validated match-state-v1 document.

    Ids assign in first-seen order and every list is emitted sorted, so the
    same source observations always produce byte-identical state regardless
    of the order they were applied in. Raises on internal inconsistency;
    never returns an invalid document.
    """
    latest = builder["latest_timestamp"]
    contested = set(builder["_contested"])
    ordered_keys = sorted(
        builder["facts"],
        key=lambda key: (
            min(entry["timestamp"] for entry in builder["facts"][key]["sightings"]),
            key[0],
            key[1],
        ),
    )
    key_to_id = {key: f"f{i:04d}" for i, key in enumerate(ordered_keys, start=1)}
    facts = []
    for key in ordered_keys:
        working = builder["facts"][key]
        sightings = sorted(
            working["sightings"], key=lambda entry: (entry["timestamp"], entry["source"])
        )
        first_seen = sightings[0]["timestamp"]
        latest_sighting = sightings[-1]
        if first_seen in contested:
            status = "conflicted"
        elif latest is not None and latest_sighting["timestamp"] == latest:
            status = "current"
        else:
            status = "stale"
        facts.append(
            {
                "id": key_to_id[key],
                "category": working["category"],
                "description": working["description"],
                "confidence": latest_sighting["confidence"],
                "evidence": latest_sighting["evidence"],
                "first_seen": first_seen,
                "last_seen": latest_sighting["timestamp"],
                "last_source": latest_sighting["source"],
                "status": status,
                "history": [
                    {
                        "timestamp": entry["timestamp"],
                        "source": entry["source"],
                        "confidence": entry["confidence"],
                        "evidence": entry["evidence"],
                    }
                    for entry in sightings
                ],
            }
        )
    changes = sorted(
        (
            {
                "timestamp": entry["timestamp"],
                "kind": entry["kind"],
                "fact_id": key_to_id[entry["key"]],
                "detail": entry["detail"],
                "source": entry["source"],
            }
            for entry in builder["changes"]
        ),
        key=lambda entry: (entry["timestamp"], entry["kind"], entry["fact_id"]),
    )
    state = {
        "schema_version": MATCH_STATE_SCHEMA_VERSION,
        "latest_timestamp": latest,
        "sources": sorted(
            builder["sources"], key=lambda entry: (entry["timestamp"], entry["file"])
        ),
        "facts": facts,
        "changes": changes,
        "conflicts": sorted(builder["conflicts"], key=lambda entry: entry["timestamp"]),
        "uncertainties": [
            {
                "text": text,
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                "sources": sorted(entry["sources"]),
                "resolved": False,
            }
            for text, entry in sorted(builder["uncertainties"].items())
        ],
    }
    return validate_match_state(state)


def build_match_state(observations: list) -> dict:
    """Build a match-state-v1 document from ``(timestamp, source, validated)``.

    Input order does not matter: observations are applied sorted by
    (timestamp, source), so repeated builds over the same files are
    byte-identical.
    """
    builder = new_match_state_builder()
    for timestamp, source_name, validated in sorted(
        observations, key=lambda item: (item[0], item[1])
    ):
        if validated["timestamp"] != timestamp:
            raise ObservationError(
                f"Source {source_name} timestamp {validated['timestamp']!r} does "
                f"not match expected {timestamp}."
            )
        apply_observation(builder, validated, source_name)
    return finalize_match_state(builder)
