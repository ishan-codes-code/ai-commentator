from pathlib import Path
import json
import os
import sys

from openai import OpenAI

from ..config import (
    COMMENTARY_GATE_RULES,
    BASETEN_BASE_URL,
    COMMENTARY_PATH,
    COMMENTARY_SCHEMA_VERSION,
    EVENTS_PATH,
    FRAMES_DIR,
    MATCH_STATE_PATH,
    OBSERVATIONS_DIR,
)
from ..errors import ObservationError, WriterAPIError
from ..events.detection import validate_events
from ..state.match_state import observation_sha256, validate_match_state
from ..storage import write_json_atomic
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import discover_observation_files, warn_about_legacy_observations
from ..vision.observations import parse_observation, validate_observation
from .claims import gate_commentary_claims
from .validation import validate_commentary
from .writer import (
    claim_id_of,
    build_writer_prompt,
    prioritize_writer_claims,
    render_commentary_claims,
    run_writer_path,
)


def load_prior_commentary(out_path: Path) -> tuple:
    """Read the previous commentary file; return (status, claim_ids, texts).

    A missing file means a first run; a malformed one is reported with a
    warning and treated as a first run. The history texts feed writer
    consistency; the claim ids feed repeat suppression.
    """
    if not out_path.is_file():
        return "absent", set(), []
    try:
        prior = validate_commentary(
            json.loads(out_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        print(
            f"Warning: ignoring malformed prior commentary {out_path}: "
            f"{exc}. Proceeding as a first run.",
            file=sys.stderr,
        )
        return "ignored-malformed", set(), []
    claim_ids = {line["claim_id"] for line in prior["lines"]}
    claim_ids.update(
        entry["subject"]
        for entry in prior["suppressed"]
        if entry["rule"] == "already-emitted"
    )
    texts = [line["text"] for line in prior["lines"]]
    return "ok", claim_ids, texts






def run_generate_commentary(
    observations_dir: Path | None = None,
    state_path: Path | None = None,
    events_path: Path | None = None,
    commentary_path: Path | None = None,
    frames_dir: Path | None = None,
    writer: str = "template",
    client=None,
) -> int:
    """Generate evidence-grounded commentary lines; return exit code.

    With ``writer="template"`` (default) the run is fully offline: reads
    only existing ``frame_<timestamp>.json`` files, the stored
    ``match_state.json`` snapshot, and the stored ``events.json`` file,
    never calls Baseten. With ``writer="llm"`` one text-only model call
    words the prioritized claims; every model line is validated against
    its gated claim and falls back to template wording on any failure,
    and a provider failure falls back to the full deterministic run.
    The snapshot and the events file must exactly cover the discovered
    observations, otherwise generation refuses to run on stale inputs. A
    repeat run suppresses unchanged claims against the previous commentary
    file (a missing file means a first run; a malformed one is ignored
    with a warning and the run proceeds). Writes
    ``output/commentary/commentary.json`` atomically. Returns 0 when
    inputs validate and the write succeeds — even with zero lines
    (deliberate silence); returns 1 on invalid inputs or write failure,
    leaving any previous valid file intact. A caller-injected ``client``
    is never closed; an internally created one is closed after its run.
    """
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    st_path = MATCH_STATE_PATH if state_path is None else Path(state_path)
    ev_path = EVENTS_PATH if events_path is None else Path(events_path)
    out_path = COMMENTARY_PATH if commentary_path is None else Path(commentary_path)
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
            f"Commentary failed: {len(files)} discovered, "
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
        print(f"Commentary gating failed: {exc}", file=sys.stderr)
        return 1
    prior_status, prior_claims, prior_texts = load_prior_commentary(out_path)
    if prior_claims:
        fresh_claims = []
        for claim in claims:
            claim_id = claim_id_of(claim)
            if claim_id in prior_claims:
                suppressed.append(
                    {
                        "rule": "already-emitted",
                        "subject": claim_id,
                        "reason": (
                            f"identical claim already emitted at "
                            f"{claim['timestamp']}s with unchanged evidence; "
                            f"no new development."
                        ),
                    }
                )
            else:
                fresh_claims.append(claim)
        claims = fresh_claims
    if writer == "llm" and claims:
        lines = run_writer_path(claims, prior_texts, client)
    else:
        try:
            lines = render_commentary_claims(claims)
        except ObservationError as exc:
            print(f"Commentary rendering failed: {exc}", file=sys.stderr)
            return 1
    document = {
        "schema_version": COMMENTARY_SCHEMA_VERSION,
        "generated_from": {
            "observations": accepted_names,
            "match_state": st_path.name,
            "events": ev_path.name,
        },
        "prior_commentary": {"file": out_path.name, "status": prior_status},
        "rules_evaluated": sorted(COMMENTARY_GATE_RULES),
        "lines": sorted(
            lines, key=lambda line: (line["timestamp"], line["seq"], line["id"])
        ),
        "suppressed": suppressed,
    }
    try:
        bundle = {
            "observations": {
                source: validated for _, source, validated in accepted
            },
            "state": state,
            "events": events,
        }
        validate_commentary(document, bundle)
    except ObservationError as exc:
        print(f"Commentary validation failed: {exc}", file=sys.stderr)
        return 1
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_path, document)
    except OSError as exc:
        print(
            f"Commentary failed: could not write {out_path}: {exc}. "
            f"Previous commentary file left intact.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Commentary finished: {len(files)} discovered, "
        f"{len(accepted)} accepted, {len(skipped)} skipped, "
        f"{len(rejected)} rejected, {len(document['lines'])} line(s), "
        f"{len(document['suppressed'])} suppressed."
    )
    for line in document["lines"]:
        print(f"  line: [{line['timestamp']}s] {line['text'][:100]}")
    print(f"Commentary snapshot: {out_path}")
    if not document["lines"]:
        print("Commentary status: success (deliberate silence).")
    else:
        print("Commentary status: success.")
    return 0


