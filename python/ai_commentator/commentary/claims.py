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


def _commentary_uncertainty_tokens(accepted: list) -> dict:
    """Map name tokens named in uncertainties to their source filenames.

    A claim quoting a token that a source uncertainty also names is about
    something the model itself flagged as uncertain, so the gate
    suppresses it (rule ``uncertainty-scope``).
    """
    tokens: dict = {}
    for _, source_name, validated in accepted:
        for text in validated["uncertainties"]:
            for token in _feed_name_tokens(text):
                tokens.setdefault(token, set()).add(source_name)
    return tokens


def _commentary_fact_claim(
    fact: dict,
    source_name: str,
    uncertainty_tokens: dict,
    subject: str,
) -> tuple:
    """Gate one fact for scene wording; return (claim, suppression).

    Exactly one of the pair is non-None. The claim carries the template
    key and the verbatim description it may restate — nothing else — so
    the renderer cannot combine facts or add interpretations.
    """
    if fact["status"] == "conflicted":
        return None, ("no-conflicted-evidence", subject,
                      f"fact '{fact['id']}' is conflicted; nothing is asserted.")
    if fact["confidence"] not in EVENT_TRIGGER_CONFIDENCES:
        return None, ("confidence-floor", subject,
                      f"fact '{fact['id']}' confidence "
                      f"'{fact['confidence']}' is below the floor.")
    if fact["category"] in ("combat", "other"):
        return None, ("category-scope", subject,
                      f"fact '{fact['id']}' category '{fact['category']}' "
                      f"is never scene wording.")
    quoted = {token for token in _feed_name_tokens(fact["description"])}
    quoted.update(_feed_name_tokens(fact["evidence"]))
    clashing = sorted(
        token for token in quoted if token in uncertainty_tokens
    )
    if clashing:
        return None, ("uncertainty-scope", subject,
                      f"fact '{fact['id']}' quotes {', '.join(clashing)}, "
                      f"named as uncertain in its sources.")
    if fact["category"] == "hud":
        template = "hud_reading"
    else:
        template = "scene_visible"
    source = {
        "timestamp": fact["last_seen"],
        "file": source_name,
        "fact_id": fact["id"],
    }
    return (
        {
            "template": template,
            "type": "scene",
            "timestamp": fact["last_seen"],
            "sources": [source],
            "source_ids": [fact["id"]],
            "event_id": None,
            "evidence": [fact["description"]],
            "confidence": fact["confidence"],
            "qualification": None,
        },
        None,
    )


def gate_commentary_claims(accepted: list, state: dict, events: dict) -> tuple:
    """Evaluate gating rules over validated inputs; return (claims, suppressed).

    ``accepted`` is ``[(timestamp, source, validated)]`` with validated
    observations. Every candidate is checked in rule order; the first
    failure emits one suppression record. Scene wording considers only
    ``current`` facts; developments additionally require a latest-timestamp
    change; event echoes require a candidate event with clean sources.
    """
    claims = []
    suppressed = []
    seen_claims: set = set()
    uncertainty_tokens = _commentary_uncertainty_tokens(accepted)
    facts_by_id = {fact["id"]: fact for fact in state["facts"]}
    latest = state["latest_timestamp"]
    contested = {
        conflict["timestamp"] for conflict in state.get("conflicts", [])
    }

    def emit(claim):
        key = (
            claim["template"],
            claim["timestamp"],
            tuple(sorted(claim["source_ids"])),
        )
        if key in seen_claims:
            return
        seen_claims.add(key)
        claims.append(claim)

    def suppress(rule, subject, reason):
        suppressed.append({"rule": rule, "subject": subject, "reason": reason})

    for fact in state["facts"]:
        subject = f"fact '{fact['id']}'"
        if fact["first_seen"] in contested or fact["last_seen"] in contested:
            suppress("no-conflicted-evidence", subject,
                     f"fact '{fact['id']}' touches a contested timestamp.")
            continue
        if fact["status"] != "current":
            suppress("stale-fact", subject,
                     f"fact '{fact['id']}' is '{fact['status']}'; "
                     f"only current facts yield scene wording.")
            continue
        claim, suppression = _commentary_fact_claim(
            fact, fact["last_source"], uncertainty_tokens, subject
        )
        if suppression is not None:
            suppress(*suppression)
        else:
            emit(claim)
    latest_changes = [
        change for change in state.get("changes", [])
        if change["timestamp"] == latest
    ]
    for change in latest_changes:
        fact = facts_by_id.get(change["fact_id"])
        subject = f"change at {change['timestamp']}s ({change['kind']})"
        if fact is None:
            suppress("valid-inputs", subject,
                     f"change cites unknown fact '{change['fact_id']}'.")
            continue
        if fact["status"] != "current":
            suppress("category-scope", subject,
                     f"changed fact '{fact['id']}' is '{fact['status']}'.")
            continue
        claim, suppression = _commentary_fact_claim(
            fact, fact["last_source"], uncertainty_tokens, subject
        )
        if suppression is not None:
            suppress(*suppression)
            continue
        template = (
            "development_new" if change["kind"] == "new_fact"
            else "development_update"
        )
        claim = dict(claim)
        claim["template"] = template
        claim["type"] = "development"
        emit(claim)
    for event in events.get("events", []):
        subject = f"event '{event['id']}'"
        if event["status"] != "candidate":
            suppress("valid-inputs", subject,
                     f"event '{event['id']}' status "
                     f"'{event['status']}' is not a candidate.")
            continue
        rule_base = event["rule"].split("/")[0]
        qualifier = COMMENTARY_QUALIFIERS.get(rule_base)
        if qualifier is None:
            suppress("no-justified-wording", subject,
                     f"no approved wording for rule '{event['rule']}'.")
            continue
        dirty = [
            source for source in event["sources"]
            if facts_by_id.get(source["fact_id"], {}).get("status") == "conflicted"
            or source["timestamp"] in contested
        ]
        if dirty:
            suppress("no-conflicted-evidence", subject,
                     f"event '{event['id']}' rests on conflicted evidence.")
            continue
        if event["confidence"] not in EVENT_TRIGGER_CONFIDENCES:
            suppress("confidence-floor", subject,
                     f"event '{event['id']}' confidence "
                     f"'{event['confidence']}' is below the floor.")
            continue
        emit(
            {
                "template": "event_echo",
                "type": "event_echo",
                "timestamp": event["timestamp"],
                "sources": list(event["sources"]),
                "source_ids": [source["fact_id"] for source in event["sources"]],
                "event_id": event["id"],
                "evidence": list(event["evidence"]),
                "confidence": event["confidence"],
                "qualification": qualifier,
            }
        )
    developed = {
        (claim["timestamp"], source_id)
        for claim in claims
        if claim["type"] == "development"
        for source_id in claim["source_ids"]
    }
    if developed:
        kept = []
        for claim in claims:
            if claim["type"] == "scene" and any(
                (claim["timestamp"], source_id) in developed
                for source_id in claim["source_ids"]
            ):
                suppress(
                    "subsumed-by-development",
                    f"fact '{claim['source_ids'][0]}'",
                    "scene wording subsumed by the development line for "
                    "the same new evidence.",
                )
            else:
                kept.append(claim)
        claims = kept
    return claims, suppressed
