import json
import os
import sys
from pathlib import Path

from openai import OpenAI

from ..commentary.claims import gate_commentary_claims
from ..commentary.generation import load_prior_commentary
from ..commentary.writer import claim_id_of, prioritize_writer_claims, request_writer_text
from ..config import (
    BASETEN_BASE_URL,
    COMMENTARY_PATH,
    EVENTS_PATH,
    MATCH_STATE_PATH,
    OBSERVATIONS_DIR,
    SCRIPT_MAX_CLAIMS,
    SCRIPT_MAX_HISTORY_LINES,
    SCRIPT_PATH,
    SCRIPT_SCHEMA_VERSION,
)
from ..errors import ObservationError, WriterAPIError
from ..events.detection import validate_events
from ..state.match_state import observation_sha256, validate_match_state
from ..storage import write_json_atomic
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import discover_observation_files, warn_about_legacy_observations
from ..vision.observations import parse_observation, validate_observation
from .generation import (
    _script_norm,
    build_script_prompt,
    load_prior_script,
    parse_script_segments,
    script_segment_id,
    script_text_valid,
    validate_script,
)


def run_generate_script(
    observations_dir: Path | None = None,
    state_path: Path | None = None,
    events_path: Path | None = None,
    commentary_path: Path | None = None,
    script_path: Path | None = None,
    frames_dir: Path | None = None,
    client=None,
) -> int:
    """Generate the LLM-cast final script; return exit code.

    Offline until the single bounded text-only model call: reads stored
    observations, match state, and events (refusing stale inputs exactly
    like commentary), gates claims with the existing logic, suppresses
    repeats against the prior script plus already-spoken history, and
    words at most SCRIPT_MAX_CLAIMS prioritized claims with one Muse
    Spark request. No deterministic template text is ever presented as
    LLM output: invalid or unsupported segments are omitted, and a
    provider or validation failure writes an explicit ``error`` status
    (exit 1) instead of a false success. Zero eligible claims writes a
    ``silence`` status with no model call (exit 0). Upstream artifacts
    are only read, never rewritten.
    """
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    st_path = MATCH_STATE_PATH if state_path is None else Path(state_path)
    ev_path = EVENTS_PATH if events_path is None else Path(events_path)
    cmt_path = COMMENTARY_PATH if commentary_path is None else Path(commentary_path)
    out_path = SCRIPT_PATH if script_path is None else Path(script_path)
    warn_about_legacy_observations(frames_dir)
    files = discover_observation_files(obs_dir)
    if not files:
        print(f"Error: no frame_*.json observations in {obs_dir}.", file=sys.stderr)
        return 1
    try:
        state = validate_match_state(
            json.loads(st_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        print(
            f"Error: invalid match state {st_path}: {exc}. "
            f"Run 'python main.py --rebuild-state' first.",
            file=sys.stderr,
        )
        return 1
    try:
        events = validate_events(
            json.loads(ev_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        print(
            f"Error: invalid events file {ev_path}: {exc}. "
            f"Run 'python main.py --detect-events' first.",
            file=sys.stderr,
        )
        return 1
    accepted: list = []
    skipped: list[str] = []
    rejected: list[tuple[str, str]] = []
    seen_digests: dict = {}
    for timestamp, path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            rejected.append((path.name, str(exc)))
            continue
        try:
            validated = ensure_observation_timestamp(
                validate_observation(parse_observation(text)), timestamp
            )
        except ObservationError as exc:
            rejected.append((path.name, str(exc)))
            continue
        digest = observation_sha256(validated)
        if digest in seen_digests.setdefault(timestamp, set()):
            skipped.append(path.name)
            continue
        seen_digests[timestamp].add(digest)
        accepted.append((timestamp, path.name, validated))
    for name, reason in rejected:
        print(f"  rejected: {name}: {reason}")
    if not accepted:
        print(
            f"Script failed: {len(files)} discovered, "
            f"{len(rejected)} rejected, none accepted.",
            file=sys.stderr,
        )
        return 1
    accepted_names = sorted({source for _, source, _ in accepted})
    state_sources = {entry["timestamp"]: entry for entry in state["sources"]}
    if sorted(state_sources) != sorted(
        {timestamp for timestamp, _, _ in accepted}
    ) or any(
        state_sources[timestamp]["file"] != source_name
        or state_sources[timestamp]["sha256"] != observation_sha256(validated)
        for timestamp, source_name, validated in accepted
    ):
        print(
            f"Error: match state {st_path} does not cover the discovered "
            f"observations (timestamps or content differ). "
            f"Run 'python main.py --rebuild-state' first.",
            file=sys.stderr,
        )
        return 1
    if sorted(events["generated_from"]["observations"]) != accepted_names:
        print(
            f"Error: events file {ev_path} does not cover the discovered "
            f"observations. Run 'python main.py --detect-events' first.",
            file=sys.stderr,
        )
        return 1
    try:
        claims, suppressed = gate_commentary_claims(
            [(timestamp, source, validated) for timestamp, source, validated in accepted],
            state,
            events,
        )
    except ObservationError as exc:
        print(f"Script gating failed: {exc}", file=sys.stderr)
        return 1
    omitted = [
        {"rule": entry["rule"], "subject": entry["subject"], "reason": entry["reason"]}
        for entry in suppressed
    ]
    prior_claims, prior_texts = load_prior_script(out_path)
    _, _, prior_commentary_texts = load_prior_commentary(cmt_path)
    history_texts = (prior_commentary_texts + prior_texts)[-SCRIPT_MAX_HISTORY_LINES:]
    history_norm = [_script_norm(text) for text in history_texts if text.strip()]
    fresh_claims = []
    for claim in claims:
        claim_id = claim_id_of(claim)
        if claim_id in prior_claims:
            omitted.append(
                {
                    "rule": "already-emitted",
                    "subject": claim_id,
                    "reason": (
                        "identical claim already cast in a prior script; "
                        "no new development."
                    ),
                }
            )
            continue
        norm_evidence = [_script_norm(part) for part in claim["evidence"]]
        if any(
            part and any(part in spoken for spoken in history_norm)
            for part in norm_evidence
        ):
            omitted.append(
                {
                    "rule": "already-spoken",
                    "subject": claim_id,
                    "reason": "evidence already covered by recent commentary.",
                }
            )
            continue
        fresh_claims.append(dict(claim, claim_id=claim_id))
    claims_by_id = {claim["claim_id"]: claim for claim in fresh_claims}
    generated_from = {
        "observations": accepted_names,
        "match_state": st_path.name,
        "events": ev_path.name,
    }

    def write_document(document: dict) -> int:
        try:
            validate_script(document, claims_by_id)
        except ObservationError as exc:
            print(f"Script validation failed: {exc}", file=sys.stderr)
            return 1
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(out_path, document)
        except OSError as exc:
            print(
                f"Script failed: could not write {out_path}: {exc}. "
                f"Previous script file left intact.",
                file=sys.stderr,
            )
            return 1
        return 0

    if not fresh_claims:
        document = {
            "schema_version": SCRIPT_SCHEMA_VERSION,
            "status": "silence",
            "generated_from": generated_from,
            "segments": [],
            "omitted": omitted,
            "error": None,
        }
        code = write_document(document)
        if code == 0:
            print(
                f"Script finished: {len(files)} discovered, "
                f"{len(accepted)} accepted, 0 segment(s), "
                f"{len(omitted)} omitted."
            )
            print(f"Script snapshot: {out_path}")
            print("Script status: silence (deliberate silence).")
        return code
    model_claims, overflow = prioritize_writer_claims(fresh_claims)
    for claim in overflow:
        omitted.append(
            {
                "rule": "over-claim-limit",
                "subject": claim["claim_id"],
                "reason": (
                    f"claim beyond the bounded batch of {SCRIPT_MAX_CLAIMS}; "
                    f"not sent to the model in this run."
                ),
            }
        )
    prompt_claims = list(model_claims)
    prompt = build_script_prompt(prompt_claims, history_texts)
    prompt_by_id = {claim["claim_id"]: claim for claim in prompt_claims}
    try:
        if client is None:
            api_key = os.environ.get("BASETEN_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "BASETEN_API_KEY is not set in the environment."
                )
            with OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL) as live_client:
                raw = request_writer_text(live_client, prompt, api_key=api_key)
        else:
            raw = request_writer_text(client, prompt)
        parsed = parse_script_segments(raw, prompt_by_id)
    except (WriterAPIError, ObservationError) as exc:
        message = str(exc)
        document = {
            "schema_version": SCRIPT_SCHEMA_VERSION,
            "status": "error",
            "generated_from": generated_from,
            "segments": [],
            "omitted": omitted
            + [
                {
                    "rule": "model-output",
                    "subject": "script-request",
                    "reason": f"model request produced no usable output: {message}",
                }
            ],
            "error": f"script generation failed: {message}",
        }
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(out_path, document)
        except OSError as write_exc:
            print(
                f"Script failed: {exc}; could not write {out_path}: "
                f"{write_exc}. Previous script file left intact.",
                file=sys.stderr,
            )
            return 1
        print(f"Script failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        # Missing API key: fail fast before any paid call, writing nothing.
        print(f"Script failed: {exc}", file=sys.stderr)
        return 1
    built = []
    for ids, wording in parsed:
        cited = [prompt_by_id[claim_id] for claim_id in ids]
        if not script_text_valid(wording, cited):
            omitted.append(
                {
                    "rule": "unsupported-wording",
                    "subject": "+".join(ids),
                    "reason": "model wording not grounded in cited evidence.",
                }
            )
            continue
        timestamp = max(claim["timestamp"] for claim in cited)
        built.append((timestamp, sorted(ids), wording.strip()))
    built.sort(key=lambda item: (item[0], item[1]))
    segments = []
    for position, (timestamp, ids, wording) in enumerate(built):
        segments.append(
            {
                "segment_id": script_segment_id(position),
                "timestamp_seconds": timestamp,
                "text": wording,
                "source_claim_ids": ids,
            }
        )
    if not segments:
        if not parsed:
            document = {
                "schema_version": SCRIPT_SCHEMA_VERSION,
                "status": "silence",
                "generated_from": generated_from,
                "segments": [],
                "omitted": omitted,
                "error": None,
            }
            code = write_document(document)
            if code == 0:
                print(
                    f"Script finished: {len(files)} discovered, "
                    f"{len(accepted)} accepted, 0 segment(s), "
                    f"{len(omitted)} omitted."
                )
                print(f"Script snapshot: {out_path}")
                print("Script status: silence (deliberate silence).")
            return code
        document = {
            "schema_version": SCRIPT_SCHEMA_VERSION,
            "status": "error",
            "generated_from": generated_from,
            "segments": [],
            "omitted": omitted,
            "error": "script generation failed: model wording failed validation.",
        }
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(out_path, document)
        except OSError as write_exc:
            print(
                f"Script failed: model wording failed validation; could not "
                f"write {out_path}: {write_exc}. Previous script file left "
                f"intact.",
                file=sys.stderr,
            )
            return 1
        print(
            "Script failed: model wording failed validation.", file=sys.stderr
        )
        return 1
    document = {
        "schema_version": SCRIPT_SCHEMA_VERSION,
        "status": "success",
        "generated_from": generated_from,
        "segments": segments,
        "omitted": omitted,
        "error": None,
    }
    code = write_document(document)
    if code == 0:
        print(
            f"Script finished: {len(files)} discovered, "
            f"{len(accepted)} accepted, {len(segments)} segment(s), "
            f"{len(omitted)} omitted."
        )
        for segment in segments:
            print(
                f"  segment: [{segment['timestamp_seconds']}s] "
                f"{segment['text'][:100]}"
            )
        print(f"Script snapshot: {out_path}")
        print("Script status: success.")
    return code


