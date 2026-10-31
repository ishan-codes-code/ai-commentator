import sys
from pathlib import Path

from ..config import FRAMES_DIR, MATCH_STATE_PATH, OBSERVATIONS_DIR
from ..errors import ObservationError
from ..storage import write_match_state
from ..vision.client import ensure_observation_timestamp
from ..vision.frames import discover_observation_files, find_legacy_observations, warn_about_legacy_observations
from ..vision.observations import parse_observation, validate_observation
from .match_state import apply_observation, finalize_match_state, new_match_state_builder
def run_state_rebuild(
    observations_dir: Path | None = None,
    state_path: Path | None = None,
    frames_dir: Path | None = None,
) -> int:
    """Rebuild the rolling match state from stored observations; exit code.

    Offline: reads only existing ``frame_<timestamp>.json`` files, never
    calls Baseten. Reports discovered/accepted/skipped/rejected counts plus
    the state path. Returns 0 only when at least one observation was
    accepted and none was rejected; leaves any previous valid snapshot
    intact whenever nothing acceptable was built or the write fails.
    """
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    out_path = MATCH_STATE_PATH if state_path is None else Path(state_path)
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
    builder = new_match_state_builder()
    accepted: list[str] = []
    skipped: list[str] = []
    rejected: list[tuple[str, str]] = []
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
        outcome = apply_observation(builder, validated, path.name)
        if outcome == "duplicate":
            skipped.append(path.name)
        else:
            accepted.append(path.name)
    for name, reason in rejected:
        print(f"  rejected: {name}: {reason}")
    if not accepted:
        print(
            f"State rebuild failed: {len(files)} discovered, "
            f"{len(rejected)} rejected, none accepted.",
            file=sys.stderr,
        )
        return 1
    try:
        state = finalize_match_state(builder)
    except ObservationError as exc:
        print(f"State rebuild failed: invalid built state: {exc}", file=sys.stderr)
        return 1
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_match_state(out_path, state)
    except OSError as exc:
        print(
            f"State rebuild failed: could not write {out_path}: {exc}. "
            f"Previous snapshot left intact.",
            file=sys.stderr,
        )
        return 1
    print(
        f"State rebuild finished: {len(files)} discovered, "
        f"{len(accepted)} accepted, {len(skipped)} skipped, "
        f"{len(rejected)} rejected; {len(state['conflicts'])} conflict(s)."
    )
    for name, reason in rejected:
        print(f"  rejected: {name}: {reason}")
    print(f"State snapshot: {out_path}")
    if rejected:
        print("State rebuild status: partial failure.")
        return 1
    print("State rebuild status: success.")
    return 0


