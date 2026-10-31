from pathlib import Path
import hashlib
import json
import math
import re
import sys

from ..config import (
    EVENTS_PATH,
    FRAMES_DIR,
    MATCH_STATE_PATH,
    OBSERVATIONS_DIR,
    MATCH_STATE_FILENAME,
    OBSERVATION_CONFIDENCES,
    EVENT_ELIM_RE,
    EVENT_FEED_MENTIONS,
    EVENT_NAME_RE,
    EVENT_RULES,
    EVENT_SCHEMA_VERSION,
    EVENT_STATUSES,
    EVENT_TEAM_SCOPE_RE,
    EVENT_TRIGGER_CONFIDENCES,
)
from ..errors import ObservationError
from ..state.match_state import fact_key, observation_sha256, validate_match_state
from ..storage import write_json_atomic
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import (
    discover_observation_files,
    find_legacy_observations,
    warn_about_legacy_observations,
)
from ..vision.observations import (
    _reject_extra,
    _require_non_empty_string,
    validate_observation,
    parse_observation,
)


def validate_events(data: dict) -> dict:
    """Strictly validate an events-v1 document; reject anything else."""
    if not isinstance(data, dict):
        raise ObservationError("Events document must be a JSON object.")
    _reject_extra(
        data,
        (
            "schema_version",
            "generated_from",
            "rules_evaluated",
            "contested_timestamps",
            "events",
        ),
        "Events",
    )
    if data.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise ObservationError(
            "Events requires schema_version "
            f"'{EVENT_SCHEMA_VERSION}'."
        )
    generated = data.get("generated_from")
    if not isinstance(generated, dict):
        raise ObservationError(
            "Events requires field 'generated_from' as an object."
        )
    _reject_extra(generated, ("observations", "match_state"), "Events generated_from")
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
            "Events generated_from requires 'observations' as a non-empty "
            "list of filenames."
        )
    _require_non_empty_string(generated, "match_state", "Events generated_from")
    rules = data.get("rules_evaluated")
    if not isinstance(rules, list) or any(
        not isinstance(item, str) or not item.strip() for item in rules
    ):
        raise ObservationError(
            "Events requires field 'rules_evaluated' as a list of rule names."
        )
    contested = data.get("contested_timestamps")
    if not isinstance(contested, list) or any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or (isinstance(item, float) and not math.isfinite(item))
        for item in contested
    ):
        raise ObservationError(
            "Events requires field 'contested_timestamps' as a list of numbers."
        )
    if "events" not in data or not isinstance(data["events"], list):
        raise ObservationError("Events requires field 'events' as a list.")
    for index, event in enumerate(data["events"]):
        where = f"events[{index}]"
        if not isinstance(event, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            event,
            (
                "schema_version",
                "id",
                "type",
                "timestamp",
                "status",
                "rule",
                "sources",
                "evidence",
                "confidence",
                "detail",
            ),
            where,
        )
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise ObservationError(
                f"{where} requires schema_version '{EVENT_SCHEMA_VERSION}'."
            )
        _require_non_empty_string(event, "id", where)
        _require_non_empty_string(event, "type", where)
        event_ts = event.get("timestamp")
        if (
            isinstance(event_ts, bool)
            or not isinstance(event_ts, (int, float))
            or (isinstance(event_ts, float) and not math.isfinite(event_ts))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        if event.get("status") not in EVENT_STATUSES:
            raise ObservationError(
                f"{where} has invalid status '{event.get('status')}'."
            )
        _require_non_empty_string(event, "rule", where)
        sources = event.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(item, dict) for item in sources)
        ):
            raise ObservationError(
                f"{where} requires 'sources' as a non-empty list of objects."
            )
        for entry_index, entry in enumerate(sources):
            entry_where = f"{where}.sources[{entry_index}]"
            _reject_extra(
                entry, ("timestamp", "file", "fact_id"), entry_where
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
            _require_non_empty_string(entry, "file", entry_where)
            _require_non_empty_string(entry, "fact_id", entry_where)
        evidence = event.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(
                not isinstance(item, str) or not item.strip() for item in evidence
            )
        ):
            raise ObservationError(
                f"{where} requires 'evidence' as a non-empty list of strings."
            )
        if event.get("confidence") not in OBSERVATION_CONFIDENCES:
            raise ObservationError(
                f"{where} has invalid confidence '{event.get('confidence')}'."
            )
        _require_non_empty_string(event, "detail", where)
    return data


def event_id(
    event_type: str,
    timestamp,
    status: str,
    rule: str,
    sources: list,
    evidence: list,
    detail: str,
    confidence: str,
) -> str:
    """Deterministic event id from the event's canonical content."""
    canonical = json.dumps(
        {
            "type": event_type,
            "timestamp": timestamp,
            "status": status,
            "rule": rule,
            "sources": sources,
            "evidence": evidence,
            "detail": detail,
            "confidence": confidence,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "evt-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _feed_name_tokens(text: str) -> list[str]:
    """Distinct TAG.NAME tokens in encounter order (e.g. RRQ.JEEK)."""
    tokens = []
    for match in EVENT_NAME_RE.finditer(text):
        token = match.group(0)
        if token not in tokens:
            tokens.append(token)
    return tokens


def _mentions_feed(text: str) -> bool:
    """Whether the description explicitly references a kill/elim feed."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in EVENT_FEED_MENTIONS)


def _elim_readings(description: str) -> list:
    """Team-scoped Elim counter readings quoted in one description.

    A reading needs the counter and a team label in the same sentence; the
    counter is attributed to the nearest team label (the preceding one wins
    ties), which is how such HUD captions read ("team AG ... Elim 4").
    Dots inside TAG.NAME tokens (e.g. AG.BAROS) do not end sentences.
    Sentences without a team label yield nothing. Returns
    ``(scope, count, sentence)`` triples using the first counter per
    sentence.
    """
    masked = EVENT_NAME_RE.sub(
        lambda match: match.group(0).replace(".", "\x00"), description
    )
    readings = []
    parts = re.split(r"([.!?]+)", masked)
    for index in range(0, len(parts), 2):
        core = parts[index]
        terminator = parts[index + 1] if index + 1 < len(parts) else ""
        sentence = (core + terminator).replace("\x00", ".").strip()
        if not sentence:
            continue
        counter = EVENT_ELIM_RE.search(core)
        if counter is None:
            continue
        scopes = [
            (match.start(), match.group(1).upper())
            for match in EVENT_TEAM_SCOPE_RE.finditer(core)
        ]
        if not scopes:
            continue
        pos = counter.start()
        before = [(pos - start, name) for start, name in scopes if start < pos]
        if before:
            scope = min(before)[1]
        else:
            scope = min([(start - pos, name) for start, name in scopes])[1]
        readings.append((scope, int(counter.group(1)), sentence))
    return readings


def detect_events(observations: list, state: dict) -> tuple[dict, dict]:
    """Detect evidence-grounded events from validated observations + state.

    Inputs sort by (timestamp, source); identical documents are skipped as
    duplicates and timestamps with divergent documents are skipped as
    contested — conflicting evidence is never resolved in favor of one
    side. Only non-conflicted state facts can trigger rules, and each
    distinct trigger emits once (deduplicated by deterministic id), so the
    same inputs always yield the same document.

    Returns ``(events_document, counts)``. Raises ObservationError on
    invalid input; callers validate file-level input first and report
    per-file rejections.
    """
    for timestamp, source_name, validated in observations:
        validate_observation(validated)
        if validated["timestamp"] != timestamp:
            raise ObservationError(
                f"Source {source_name} timestamp {validated['timestamp']!r} does "
                f"not match expected {timestamp}."
            )
    validate_match_state(state)
    facts_by_key = {
        (fact["category"], fact["description"]): fact for fact in state["facts"]
    }
    ordered = sorted(observations, key=lambda item: (item[0], item[1]))
    digests_by_timestamp: dict = {}
    unique = []
    skipped = 0
    for timestamp, source_name, validated in ordered:
        digest = observation_sha256(validated)
        digests = digests_by_timestamp.setdefault(timestamp, set())
        if digest in digests:
            skipped += 1
            continue
        digests.add(digest)
        unique.append((timestamp, source_name, validated))
    by_timestamp: dict = {}
    for timestamp, source_name, validated in unique:
        by_timestamp.setdefault(timestamp, []).append((source_name, validated))
    contested = sorted(
        timestamp
        for timestamp, docs in by_timestamp.items()
        if len({observation_sha256(validated) for _, validated in docs}) > 1
    )
    clean = [
        (timestamp, source_name, validated)
        for timestamp, source_name, validated in unique
        if timestamp not in contested
    ]
    skipped += sum(len(by_timestamp[timestamp]) for timestamp in contested)
    events: dict = {}

    def emit(event_type, timestamp, rule, sources, evidence, confidence, detail):
        ordered_sources = sorted(
            sources, key=lambda entry: (entry["timestamp"], entry["file"])
        )
        eid = event_id(
            event_type,
            timestamp,
            "candidate",
            rule,
            ordered_sources,
            evidence,
            detail,
            confidence,
        )
        events.setdefault(
            eid,
            {
                "schema_version": EVENT_SCHEMA_VERSION,
                "id": eid,
                "type": event_type,
                "timestamp": timestamp,
                "status": "candidate",
                "rule": rule,
                "sources": ordered_sources,
                "evidence": evidence,
                "detail": detail,
                "confidence": confidence,
            },
        )

    def state_fact(category, description, source_name):
        fact = facts_by_key.get(fact_key(category, description))
        if fact is None:
            raise ObservationError(
                f"Observation {source_name} has no matching state fact; "
                f"rebuild match state first."
            )
        if fact["status"] == "conflicted":
            return None
        return fact

    for timestamp, source_name, validated in clean:
        for item in validated["observations"]:
            if (
                item["category"] != "hud"
                or item["confidence"] not in EVENT_TRIGGER_CONFIDENCES
            ):
                continue
            if not _mentions_feed(item["description"]):
                continue
            names = _feed_name_tokens(item["evidence"])
            if len(names) < 2:
                continue
            fact = state_fact(item["category"], item["description"], source_name)
            if fact is None:
                continue
            emit(
                "kill_feed_entry",
                timestamp,
                "kill_feed_entry/v1",
                [
                    {
                        "timestamp": timestamp,
                        "file": source_name,
                        "fact_id": fact["id"],
                    }
                ],
                [item["description"], item["evidence"]],
                item["confidence"],
                f"HUD observation at {timestamp}s references a kill feed and "
                f"transcribes names {', '.join(names)}; killer/victim roles "
                f"are not established.",
            )
    readings = []
    for timestamp, source_name, validated in clean:
        for item in validated["observations"]:
            if (
                item["category"] != "hud"
                or item["confidence"] not in EVENT_TRIGGER_CONFIDENCES
            ):
                continue
            for scope, count, sentence in _elim_readings(item["description"]):
                fact = state_fact(item["category"], item["description"], source_name)
                if fact is None:
                    continue
                readings.append(
                    {
                        "timestamp": timestamp,
                        "source": source_name,
                        "fact_id": fact["id"],
                        "scope": scope,
                        "count": count,
                        "sentence": sentence,
                        "confidence": item["confidence"],
                    }
                )
    readings.sort(key=lambda entry: (entry["timestamp"], entry["source"]))
    for index in range(1, len(readings)):
        earlier = readings[index - 1]
        later = readings[index]
        if later["timestamp"] <= earlier["timestamp"]:
            continue
        if later["scope"] != earlier["scope"]:
            continue
        if later["count"] <= earlier["count"]:
            continue
        emit(
            "elim_count_increased",
            later["timestamp"],
            "elim_count_increased/v1",
            [
                {
                    "timestamp": earlier["timestamp"],
                    "file": earlier["source"],
                    "fact_id": earlier["fact_id"],
                },
                {
                    "timestamp": later["timestamp"],
                    "file": later["source"],
                    "fact_id": later["fact_id"],
                },
            ],
            [earlier["sentence"], later["sentence"]],
            later["confidence"],
            f"Team {later['scope']} elim counter increased "
            f"{earlier['count']}->{later['count']} between "
            f"{earlier['timestamp']}s and {later['timestamp']}s; both readings "
            f"explicitly scope the counter to the same team. Who was "
            f"eliminated, by whom, and by what means are not established.",
        )
    document = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "generated_from": {
            # Every validated input, including contested-skipped files whose
            # exclusion is itself part of the replay provenance.
            "observations": sorted({source for _, source, _ in unique}),
            "match_state": MATCH_STATE_FILENAME,
        },
        "rules_evaluated": sorted(EVENT_RULES),
        "contested_timestamps": contested,
        "events": sorted(
            events.values(),
            key=lambda event: (event["timestamp"], event["type"], event["id"]),
        ),
    }
    counts = {
        "accepted": len(clean),
        "skipped": skipped,
        "contested": contested,
    }
    return validate_events(document), counts
