import json
import sys
from pathlib import Path

from ..config import EVENTS_PATH, FRAMES_DIR, MATCH_STATE_PATH, OBSERVATIONS_DIR
from ..errors import ObservationError
from ..state.match_state import observation_sha256, validate_match_state
from ..storage import write_json_atomic
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import discover_observation_files, find_legacy_observations, warn_about_legacy_observations
from ..vision.observations import parse_observation, validate_observation
from .detection import detect_events
def run_detect_events(
    observations_dir: Path | None = None,
    state_path: Path | None = None,
    events_path: Path | None = None,
    frames_dir: Path | None = None,
) -> int:
    """Detect evidence-grounded events from stored data; exit code.

    Offline: reads only existing ``frame_<timestamp>.json`` files and the
    stored ``match_state.json`` snapshot, never calls Baseten. The snapshot
    must exactly cover the discovered observations (timestamps and content
    hashes), otherwise detection refuses to run on stale state. Reports
    discovered/accepted/skipped/rejected/emitted counts plus the output
    path. Returns 0 only when at least one observation was accepted and
    none was rejected; leaves any previous valid events file intact
    whenever nothing acceptable was built or the write fails.
    """
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    st_path = MATCH_STATE_PATH if state_path is None else Path(state_path)
    out_path = EVENTS_PATH if events_path is None else Path(events_path)
    warn_about_legacy_observations(frames_dir)
    files = discover_observation_files(obs_dir)
    if not files:
        print(f"Error: no frame_*.json observations in {obs_dir}.", file=sys.stderr)
        if find_legacy_observations(frames_dir):
            print(
                "Hint: legacy observation files exist in the frames directory; "
                "run 'python main.py --migrate-observations' first.",
                file=sys.stderr,
            )
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
            f"Event detection failed: {len(files)} discovered, "
            f"{len(rejected)} rejected, none accepted.",
            file=sys.stderr,
        )
        return 1
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
    try:
        document, counts = detect_events(
            [(timestamp, source, validated) for timestamp, source, validated in accepted],
            state,
        )
    except ObservationError as exc:
        print(f"Event detection failed: {exc}", file=sys.stderr)
        return 1
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(out_path, document)
    except OSError as exc:
        print(
            f"Event detection failed: could not write {out_path}: {exc}. "
            f"Previous events file left intact.",
            file=sys.stderr,
        )
        return 1
    skipped_total = len(skipped) + counts["skipped"]
    print(
        f"Event detection finished: {len(files)} discovered, "
        f"{len(accepted)} accepted, {skipped_total} skipped, "
        f"{len(rejected)} rejected, {len(document['events'])} emitted; "
        f"{len(counts['contested'])} contested timestamp(s)."
    )
    for name in counts["contested"]:
        print(f"  contested: {name}")
    for event in document["events"]:
        print(f"  emitted: {event['id']} {event['type']} at {event['timestamp']}s")
    print(f"Events snapshot: {out_path}")
    if rejected:
        print("Event detection status: partial failure.")
        return 1
    print("Event detection status: success.")
    return 0


