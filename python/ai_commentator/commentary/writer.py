from pathlib import Path
import json
import os
import re
import sys

from openai import APIError

from ..config import (
    EVENT_NAME_RE,
    BASETEN_BASE_URL,
    BASETEN_MODEL,
    COMMENTARY_QUALIFIERS,
    COMMENTARY_SCHEMA_VERSION,
    WRITER_MAX_CLAIMS,
    WRITER_MAX_EVIDENCE_CHARS,
    WRITER_MAX_HISTORY_LINES,
    stylistic_prompt_suffix,
)
from ..errors import ObservationError, WriterAPIError
from ..events.detection import _feed_name_tokens
from ..vision.observations import _reject_extra, parse_observation
from .validation import commentary_claim_id, commentary_line_id


def render_claim_text(claim: dict) -> str:
    """Deterministic template wording for one gated claim.

    A fixed prefix plus verbatim validated strings. The single wording
    implementation shared by the template path and the writer fallback
    path, so both speak exactly what the gate approved.
    """
    description = claim["evidence"][0]
    timestamp = claim["timestamp"]
    if claim["template"] == "scene_visible":
        return f"At {timestamp}s, visible: {description}"
    if claim["template"] == "hud_reading":
        return f"HUD reads: {description}"
    if claim["template"] == "development_new":
        return f"New development: {description}"
    if claim["template"] == "development_update":
        return f"Update: {description}"
    if claim["template"] == "event_echo":
        quoted = " — ".join(claim["evidence"])
        return f"{quoted} — unconfirmed: {claim['qualification']}"
    raise ObservationError(
        f"Unknown commentary template '{claim['template']}'."
    )


def _render_claim_line(claim: dict, text: str, seq: int) -> dict:
    """Build one commentary-v1 line for a claim with the given wording.

    Identity, sources, evidence, confidence, and qualification always come
    from the gated claim; only ``text`` varies (template wording or
    validated writer wording).
    """
    timestamp = claim["timestamp"]
    claim_id = commentary_claim_id(
        claim["type"],
        claim["template"],
        claim["evidence"],
        claim["source_ids"],
        timestamp,
    )
    return {
        "schema_version": COMMENTARY_SCHEMA_VERSION,
        "id": commentary_line_id(claim_id, seq, timestamp),
        "claim_id": claim_id,
        "timestamp": timestamp,
        "seq": seq,
        "type": claim["type"],
        "template": claim["template"],
        "text": text,
        "sources": claim["sources"],
        "event_id": claim["event_id"],
        "evidence": claim["evidence"],
        "confidence": claim["confidence"],
        "qualification": claim["qualification"],
    }


def order_commentary_claims(claims: list) -> list:
    """Deterministic claim order shared by both renderers."""
    return sorted(
        claims,
        key=lambda claim: (
            claim["timestamp"],
            claim["template"],
            sorted(claim["source_ids"]),
        ),
    )


def render_commentary_claims(claims: list) -> list:
    """Render gated claims with their templates; return line dicts.

    Each template is a fixed prefix plus verbatim validated strings. The
    renderer takes no other inputs, so it cannot combine facts or add
    interpretations outside the claim's approved wording.
    """
    lines = []
    seq_by_timestamp: dict = {}
    for claim in order_commentary_claims(claims):
        timestamp = claim["timestamp"]
        seq = seq_by_timestamp.get(timestamp, 0)
        seq_by_timestamp[timestamp] = seq + 1
        lines.append(_render_claim_line(claim, render_claim_text(claim), seq))
    return lines


def prioritize_writer_claims(claims: list) -> tuple:
    """Split gated claims into model-worded and template-worded sets.

    Developments and event echoes carry the match narrative, so they go
    to the model first; scene lines follow. At most WRITER_MAX_CLAIMS go
    to the model; the rest keep deterministic template wording in the same
    run. Ordering is deterministic, so the split is stable.
    """
    ordered = sorted(
        claims,
        key=lambda claim: (
            0 if claim["type"] in ("development", "event_echo") else 1,
            claim["timestamp"],
            claim["template"],
            sorted(claim["source_ids"]),
        ),
    )
    return ordered[:WRITER_MAX_CLAIMS], ordered[WRITER_MAX_CLAIMS:]


def build_writer_prompt(claims: list, history_texts: list) -> str:
    """Build the bounded prompt for the commentary-writer model.

    The model sees only gated claims (with their verbatim evidence,
    confidence labels, and echo qualifiers) plus recent already-spoken
    lines for consistency. It never sees raw observations, match state,
    or full history. It must answer with JSON mapping claim ids to
    spoken lines, and may omit claims it cannot word safely.
    """
    parts = [
        "You are a concise live esports commentator for Free Fire gameplay. ",
        "Write one short spoken line per evidence item below (one or two ",
        "sentences, suitable for text-to-speech). Prefer the newest ",
        "developments; vary phrasing naturally; never repeat what was ",
        "already said. ",
        "Strict rules: each line must stay within its quoted evidence — ",
        "quote or closely restate the evidence, never add kills, knocks, ",
        "damage, identities, positions, outcomes, or causal explanations ",
        "beyond the quotes. A candidate event is NOT confirmed: lines for ",
        "event_echo items must keep their qualifier wording (unconfirmed). ",
        "If an item cannot be worded safely, omit it. ",
        "Respond with ONLY a single JSON object, no markdown fences, no ",
        "commentary, matching this shape: ",
        '{"lines": [{"claim_id": <one of the ids below>, ',
        '"text": <the spoken line>}]}. ',
    ]
    for claim in claims:
        evidence = " / ".join(claim["evidence"])[:WRITER_MAX_EVIDENCE_CHARS]
        parts.append(
            f"Claim {claim['claim_id']} (type {claim['type']}, "
            f"at {claim['timestamp']}s, confidence {claim['confidence']}, "
            f"evidence: {evidence})."
        )
        if claim["qualification"] is not None:
            parts.append(f" Required qualifier: {claim['qualification']}.")
        parts.append(" ")
    if history_texts:
        parts.append("Already said (do not repeat this information): ")
        parts.append(" | ".join(history_texts[-WRITER_MAX_HISTORY_LINES:]))
    return "".join(parts) + stylistic_prompt_suffix()


def request_writer_text(client, prompt: str, api_key: str | None = None) -> str:
    """Request one JSON wording document; return the raw model text.

    A single text-only completion (no images). Provider failures raise
    WriterAPIError with the secret redacted; empty completions raise
    ObservationError. Both drive deterministic fallback, never a crash.
    """
    try:
        response = client.chat.completions.create(
            model=BASETEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIError as exc:
        status_code = getattr(exc, "status_code", None)
        status = f"HTTP {status_code}" if status_code else "request error"
        detail = getattr(exc, "message", None) or str(exc)
        if exc.__cause__:
            detail = f"{detail} Cause: {exc.__cause__}"
        if api_key:
            detail = detail.replace(api_key, "[REDACTED]")
        raise WriterAPIError(f"Baseten API {status}: {detail}") from None
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    text = getattr(message, "content", None)
    if not isinstance(text, str) or not text.strip():
        raise ObservationError("Writer returned no text content.")
    return text


def parse_writer_lines(text: str, claims: list) -> dict:
    """Parse model wording into a {claim_id: text} map; reject bad output.

    Unknown or duplicate claim ids are rejected: the model may only word
    the gated claims it was given. Omissions are allowed — omitted claims
    keep deterministic template wording.
    """
    data = parse_observation(text)
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ObservationError("Writer output must be a JSON object with a 'lines' list.")
    known = {
        commentary_claim_id(
            claim["type"],
            claim["template"],
            claim["evidence"],
            claim["source_ids"],
            claim["timestamp"],
        )
        for claim in claims
    }
    texts: dict = {}
    for index, entry in enumerate(data["lines"]):
        where = f"writer lines[{index}]"
        if not isinstance(entry, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(entry, ("claim_id", "text"), where)
        claim_id = entry.get("claim_id")
        wording = entry.get("text")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ObservationError(f"{where} requires a non-empty 'claim_id'.")
        if not isinstance(wording, str) or not wording.strip():
            raise ObservationError(f"{where} requires a non-empty 'text'.")
        if claim_id not in known:
            raise ObservationError(
                f"{where} cites unknown claim '{claim_id}'."
            )
        if claim_id in texts:
            raise ObservationError(
                f"{where} duplicates claim '{claim_id}'."
            )
        texts[claim_id] = wording
    return texts


def writer_text_valid(text: str, claim: dict) -> bool:
    """Check one model-worded line against its gated claim.

    Mechanical checks only: every evidence quote must appear verbatim,
    named tokens must close over the evidence, and event echoes must
    carry their qualifier verbatim. Anything else fails closed to the
    deterministic template wording. This cannot prove semantic
    faithfulness — misleading arrangements of true quotes remain a known
    residual risk (see the contract).
    """
    for quote in claim["evidence"]:
        if quote not in text:
            return False
    evidence_tokens = set()
    for quote in claim["evidence"]:
        evidence_tokens.update(
            match.group(0) for match in EVENT_NAME_RE.finditer(quote)
        )
    for token in _feed_name_tokens(text):
        if token not in evidence_tokens:
            return False
    if claim["type"] == "event_echo":
        if claim["qualification"] not in text:
            return False
    return True


def render_writer_claims(claims: list, texts: dict) -> list:
    """Render claims with validated model wording; return line dicts.

    Claims are ordered and sequenced exactly like the template path, and
    identity/sources/evidence/confidence always come from the gated
    claim. A claim uses its model wording only when that wording passes
    per-claim validation; otherwise it falls back to deterministic
    template wording. Unknown model texts are never trusted.
    """
    lines = []
    seq_by_timestamp: dict = {}
    for claim in order_commentary_claims(claims):
        timestamp = claim["timestamp"]
        seq = seq_by_timestamp.get(timestamp, 0)
        seq_by_timestamp[timestamp] = seq + 1
        claim_id = commentary_claim_id(
            claim["type"],
            claim["template"],
            claim["evidence"],
            claim["source_ids"],
            timestamp,
        )
        wording = texts.get(claim_id)
        if wording is None or not writer_text_valid(wording, claim):
            wording = render_claim_text(claim)
        lines.append(_render_claim_line(claim, wording, seq))
    return lines


def claim_id_of(claim: dict) -> str:
    """Stable claim identity shared by prompting, parsing, and rendering."""
    return commentary_claim_id(
        claim["type"],
        claim["template"],
        claim["evidence"],
        claim["source_ids"],
        claim["timestamp"],
    )


def run_writer_path(claims: list, history_texts: list, client) -> list:
    """Word gated claims with the model, falling back safely; return lines.

    Prioritized claims go to one text-only model call; the rest keep
    template wording in the same run. Unknown, duplicate, or malformed
    model output falls back to the full deterministic run, and any single
    line that fails per-claim validation falls back to its template
    wording — provider trouble never crashes the pipeline. A missing API
    key raises RuntimeError before any call is attempted. A
    caller-injected client is never closed; an internally created one is
    closed after its single request.
    """
    model_claims, _overflow = prioritize_writer_claims(claims)
    prompt_claims = [dict(claim, claim_id=claim_id_of(claim)) for claim in model_claims]
    prompt = build_writer_prompt(prompt_claims, history_texts)
    texts: dict = {}
    if client is None:
        api_key = os.environ.get("BASETEN_API_KEY")
        if not api_key:
            raise RuntimeError(
                "BASETEN_API_KEY is not set in the environment."
            )
        try:
            with OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL) as live_client:
                raw = request_writer_text(live_client, prompt, api_key=api_key)
                texts = parse_writer_lines(raw, prompt_claims)
        except (WriterAPIError, ObservationError) as exc:
            print(
                f"Warning: commentary writer unavailable ({exc}); "
                f"using deterministic fallback.",
                file=sys.stderr,
            )
    else:
        try:
            raw = request_writer_text(client, prompt)
            texts = parse_writer_lines(raw, prompt_claims)
        except (WriterAPIError, ObservationError) as exc:
            print(
                f"Warning: commentary writer unavailable ({exc}); "
                f"using deterministic fallback.",
                file=sys.stderr,
            )
    try:
        return render_writer_claims(claims, texts)
    except ObservationError as exc:
        print(
            f"Warning: writer rendering failed ({exc}); "
            f"using deterministic fallback.",
            file=sys.stderr,
        )
        return render_commentary_claims(claims)


