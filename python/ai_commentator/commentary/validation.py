import hashlib
import json
import math
import re

from ..config import (
    COMMENTARY_GATE_RULES,
    COMMENTARY_PRIOR_STATUSES,
    COMMENTARY_QUALIFIERS,
    COMMENTARY_SCENE_CATEGORIES,
    COMMENTARY_SCHEMA_VERSION,
    COMMENTARY_TEMPLATES,
    COMMENTARY_TYPES,
    EVENT_NAME_RE,
    EVENT_STATUSES,
    EVENT_TRIGGER_CONFIDENCES,
    OBSERVATION_CATEGORIES,
    OBSERVATION_CONFIDENCES,
)
from ..errors import ObservationError
from ..events.detection import _feed_name_tokens
from ..state.match_state import fact_key
from ..vision.observations import _reject_extra, _require_non_empty_string


def commentary_hash(payload: dict) -> str:
    """Hex digest of a commentary payload's canonical JSON form."""
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def commentary_claim_id(
    line_type: str,
    template: str,
    evidence: list,
    source_ids: list,
    timestamp,
) -> str:
    """Stable claim identity for repetition suppression.

    The latest evidence timestamp is part of the identity: identical
    evidence at the same timestamp is the same claim (suppressible), while
    a re-sighting at a new timestamp is a new claim (re-emitted).
    """
    return "clm-" + commentary_hash(
        {
            "type": line_type,
            "template": template,
            "evidence": evidence,
            "sources": sorted(source_ids),
            "timestamp": timestamp,
        }
    )[:12]


def commentary_line_id(claim_id: str, seq: int, timestamp) -> str:
    """Run-unique line id derived from the stable claim id."""
    return "cmt-" + commentary_hash(
        {"claim": claim_id, "seq": seq, "timestamp": timestamp}
    )[:12]


def validate_commentary(data: dict, bundle=None) -> dict:
    """Strictly validate a commentary-v1 document; reject anything else.

    Shape is always checked. When ``bundle`` (``{"observations": ...,
    "state": ..., "events": ...}``) is supplied, provenance is checked too:
    source refs must resolve, evidence must be contained in both cited
    observation text and line text, named tokens must close over the
    evidence, confidence must not exceed any cited source, and the template
    must be allowlisted with its qualification rule satisfied.
    """
    if not isinstance(data, dict):
        raise ObservationError("Commentary must be a JSON object.")
    _reject_extra(
        data,
        (
            "schema_version",
            "generated_from",
            "prior_commentary",
            "rules_evaluated",
            "lines",
            "suppressed",
        ),
        "Commentary",
    )
    if data.get("schema_version") != COMMENTARY_SCHEMA_VERSION:
        raise ObservationError(
            "Commentary requires schema_version "
            f"'{COMMENTARY_SCHEMA_VERSION}'."
        )
    generated = data.get("generated_from")
    if not isinstance(generated, dict):
        raise ObservationError(
            "Commentary requires field 'generated_from' as an object."
        )
    _reject_extra(
        generated, ("observations", "match_state", "events"), "Commentary generated_from"
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
            "Commentary generated_from requires 'observations' as a "
            "non-empty list of filenames."
        )
    _require_non_empty_string(generated, "match_state", "Commentary generated_from")
    _require_non_empty_string(generated, "events", "Commentary generated_from")
    prior = data.get("prior_commentary")
    if not isinstance(prior, dict):
        raise ObservationError(
            "Commentary requires field 'prior_commentary' as an object."
        )
    _reject_extra(prior, ("file", "status"), "Commentary prior_commentary")
    _require_non_empty_string(prior, "file", "Commentary prior_commentary")
    if prior.get("status") not in COMMENTARY_PRIOR_STATUSES:
        raise ObservationError(
            "Commentary prior_commentary has invalid status "
            f"'{prior.get('status')}'."
        )
    rules = data.get("rules_evaluated")
    if not isinstance(rules, list) or any(
        not isinstance(item, str) or not item.strip() for item in rules
    ):
        raise ObservationError(
            "Commentary requires field 'rules_evaluated' as a list of rule names."
        )
    if "lines" not in data or not isinstance(data["lines"], list):
        raise ObservationError("Commentary requires field 'lines' as a list.")
    seen_seqs: dict = {}
    for index, line in enumerate(data["lines"]):
        where = f"lines[{index}]"
        if not isinstance(line, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            line,
            (
                "schema_version",
                "id",
                "claim_id",
                "timestamp",
                "seq",
                "type",
                "template",
                "text",
                "sources",
                "event_id",
                "evidence",
                "confidence",
                "qualification",
            ),
            where,
        )
        if line.get("schema_version") != COMMENTARY_SCHEMA_VERSION:
            raise ObservationError(
                f"{where} requires schema_version '{COMMENTARY_SCHEMA_VERSION}'."
            )
        _require_non_empty_string(line, "id", where)
        _require_non_empty_string(line, "claim_id", where)
        line_ts = line.get("timestamp")
        if (
            isinstance(line_ts, bool)
            or not isinstance(line_ts, (int, float))
            or (isinstance(line_ts, float) and not math.isfinite(line_ts))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        seq = line.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ObservationError(
                f"{where} field 'seq' must be a non-negative integer."
            )
        key = (line_ts, seq)
        if key in seen_seqs:
            raise ObservationError(
                f"{where} duplicates (timestamp, seq) of {seen_seqs[key]}."
            )
        seen_seqs[key] = where
        if line.get("type") not in COMMENTARY_TYPES:
            raise ObservationError(
                f"{where} has invalid type '{line.get('type')}'."
            )
        if line.get("template") not in COMMENTARY_TEMPLATES:
            raise ObservationError(
                f"{where} has unknown template '{line.get('template')}'."
            )
        _require_non_empty_string(line, "text", where)
        sources = line.get("sources")
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
            _reject_extra(entry, ("timestamp", "file", "fact_id"), entry_where)
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
        event_id = line.get("event_id")
        if event_id is not None and (
            not isinstance(event_id, str) or not event_id.strip()
        ):
            raise ObservationError(
                f"{where} field 'event_id' must be a non-empty string or null."
            )
        evidence = line.get("evidence")
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
        if line.get("confidence") not in OBSERVATION_CONFIDENCES:
            raise ObservationError(
                f"{where} has invalid confidence '{line.get('confidence')}'."
            )
        qualification = line.get("qualification")
        if line["type"] == "event_echo":
            if not isinstance(qualification, str) or not qualification.strip():
                raise ObservationError(
                    f"{where} of type 'event_echo' requires a non-empty "
                    f"'qualification'."
                )
            if event_id is None:
                raise ObservationError(
                    f"{where} of type 'event_echo' requires an 'event_id'."
                )
        else:
            if qualification is not None:
                raise ObservationError(
                    f"{where} of type '{line['type']}' requires "
                    f"'qualification' to be null."
                )
            if event_id is not None:
                raise ObservationError(
                    f"{where} of type '{line['type']}' requires 'event_id' "
                    f"to be null."
                )
    if "suppressed" not in data or not isinstance(data["suppressed"], list):
        raise ObservationError("Commentary requires field 'suppressed' as a list.")
    for index, entry in enumerate(data["suppressed"]):
        entry_where = f"suppressed[{index}]"
        if not isinstance(entry, dict):
            raise ObservationError(f"{entry_where} must be an object.")
        _reject_extra(entry, ("rule", "subject", "reason"), entry_where)
        _require_non_empty_string(entry, "rule", entry_where)
        _require_non_empty_string(entry, "subject", entry_where)
        _require_non_empty_string(entry, "reason", entry_where)
    if bundle is not None:
        _verify_commentary_provenance(data, bundle)
    return data


def _verify_commentary_provenance(data: dict, bundle: dict) -> None:
    """Check every line against the input bundle; raise on mismatch.

    The bundle carries ``observations`` ({filename: validated}), ``state``
    (validated match-state-v1), and ``events`` (validated events-v1).
    Containment and token checks are necessary but not sufficient proof of
    support: they bind wording to quoted evidence, while the template
    precondition inputs (checked here per template) establish that the
    claim itself was gated.
    """
    observations = bundle["observations"]
    state = bundle["state"]
    events = bundle["events"]
    facts_by_id = {fact["id"]: fact for fact in state["facts"]}
    events_by_id = {event["id"]: event for event in events["events"]}
    for index, line in enumerate(data["lines"]):
        where = f"lines[{index}]"
        source_confidences = []
        cited_texts = []
        for entry in line["sources"]:
            validated = observations.get(entry["file"])
            if validated is None:
                raise ObservationError(
                    f"{where} cites unknown observation '{entry['file']}'."
                )
            if validated["timestamp"] != entry["timestamp"]:
                raise ObservationError(
                    f"{where} source timestamp does not match "
                    f"'{entry['file']}'."
                )
            fact = facts_by_id.get(entry["fact_id"])
            if fact is None:
                raise ObservationError(
                    f"{where} cites unknown fact '{entry['fact_id']}'."
                )
            if fact["status"] == "conflicted":
                raise ObservationError(
                    f"{where} cites conflicted fact '{entry['fact_id']}'."
                )
            source_confidences.append(fact["confidence"])
            for item in validated["observations"]:
                if fact_key(item["category"], item["description"]) == (
                    fact["category"],
                    fact["description"],
                ):
                    cited_texts.append(item["description"])
                    cited_texts.append(item["evidence"])
        for quote in line["evidence"]:
            if not any(quote in text for text in cited_texts):
                raise ObservationError(
                    f"{where} evidence is not quoted from its cited sources."
                )
            if quote not in line["text"]:
                raise ObservationError(
                    f"{where} evidence is not contained in the line text."
                )
        evidence_tokens = set()
        for quote in line["evidence"]:
            evidence_tokens.update(
                match.group(0) for match in EVENT_NAME_RE.finditer(quote)
            )
        for token in _feed_name_tokens(line["text"]):
            if token not in evidence_tokens:
                raise ObservationError(
                    f"{where} names '{token}' outside its cited evidence."
                )
        order = ("high", "medium", "low")
        if order.index(line["confidence"]) > min(
            order.index(confidence) for confidence in source_confidences
        ):
            raise ObservationError(
                f"{where} confidence exceeds a cited source confidence."
            )
        _verify_commentary_template(line, where, facts_by_id, events_by_id)


def _verify_commentary_template(line: dict, where: str, facts_by_id: dict, events_by_id: dict) -> None:
    """Check a line's template precondition inputs; raise on mismatch.

    Each template names the exact inputs its claim requires. The renderer
    can only invoke these templates, so passing this check means the line
    was gated — not merely that its text contains the right quotes.
    """
    template = line["template"]
    facts = [facts_by_id[entry["fact_id"]] for entry in line["sources"]]
    if template == "scene_visible":
        if line["type"] != "scene" or len(facts) != 1:
            raise ObservationError(
                f"{where} template 'scene_visible' requires type 'scene' "
                f"with exactly one fact."
            )
        fact = facts[0]
        if (
            fact["category"] not in COMMENTARY_SCENE_CATEGORIES
            or fact["status"] != "current"
            or fact["confidence"] not in EVENT_TRIGGER_CONFIDENCES
        ):
            raise ObservationError(
                f"{where} template 'scene_visible' requires a current "
                f"high/medium environment or character fact."
            )
    elif template == "hud_reading":
        if line["type"] != "scene" or len(facts) != 1:
            raise ObservationError(
                f"{where} template 'hud_reading' requires type 'scene' "
                f"with exactly one fact."
            )
        fact = facts[0]
        if (
            fact["category"] != "hud"
            or fact["status"] != "current"
            or fact["confidence"] not in EVENT_TRIGGER_CONFIDENCES
        ):
            raise ObservationError(
                f"{where} template 'hud_reading' requires a current "
                f"high/medium hud fact."
            )
    elif template in ("development_new", "development_update"):
        if line["type"] != "development" or len(facts) != 1:
            raise ObservationError(
                f"{where} template '{template}' requires type "
                f"'development' with exactly one fact."
            )
        fact = facts[0]
        if (
            fact["category"] not in COMMENTARY_SCENE_CATEGORIES + ("hud",)
            or fact["status"] != "current"
            or fact["confidence"] not in EVENT_TRIGGER_CONFIDENCES
        ):
            raise ObservationError(
                f"{where} template '{template}' requires a current "
                f"high/medium environment, character, or hud fact."
            )
    elif template == "event_echo":
        if line["type"] != "event_echo":
            raise ObservationError(
                f"{where} template 'event_echo' requires type 'event_echo'."
            )
        event = events_by_id.get(line["event_id"])
        if event is None or event["status"] != "candidate":
            raise ObservationError(
                f"{where} cites no candidate event '{line['event_id']}'."
            )
    else:  # Unreachable: the template enum is validated above.
        raise ObservationError(f"{where} has unknown template '{template}'.")


