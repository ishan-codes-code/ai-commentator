from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import contextlib
import json
import os
import sys

from openai import OpenAI

from ..config import (
    BASETEN_BASE_URL,
    DEFAULT_VISION_BATCH_JOBS,
    DEFAULT_VISION_BATCH_LIMIT,
    FRAMES_DIR,
    MAX_VISION_BATCH_JOBS,
    OBSERVATIONS_DIR,
    VISION_FRAME_PATH,
)
from ..errors import ObservationError, VisionAPIError
from ..storage import copy_verified, write_observation_json
from .client import analyze_frame, ensure_observation_timestamp
from .frames import (
    discover_candidate_frames,
    observation_path_for_frame,
    parse_frame_timestamp,
    select_spread_frames,
    warn_about_legacy_observations,
    find_legacy_observations,
    parse_observation_timestamp,
)
from .observations import parse_observation, validate_observation


def migrate_observations(
    frames_dir: Path | None = None, observations_dir: Path | None = None
) -> dict:
    """Move legacy observation JSONs out of the frames directory.

    Each ``frame_<timestamp>.json`` under the frames directory is validated
    (schema plus filename timestamp); valid files are byte-copied to the
    observations directory and the source is removed only after the
    destination verifies byte-identical. Byte-identical duplicates already
    stored are removed as verified copies. Conflicting destinations and
    invalid files are left in place and reported — never overwritten,
    deleted, or discarded. Idempotent: re-running a completed migration
    reports no remaining files.

    Returns a summary dict with ``moved``, ``removed_duplicates``,
    ``conflicts``, ``invalid``, and ``errors`` filename lists.
    """
    src_dir = FRAMES_DIR if frames_dir is None else Path(frames_dir)
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    summary = {
        "moved": [],
        "removed_duplicates": [],
        "conflicts": [],
        "invalid": [],
        "errors": [],
    }
    for legacy_path in find_legacy_observations(src_dir):
        timestamp = parse_observation_timestamp(legacy_path.name)
        if read_validated_observation(legacy_path, timestamp) is None:
            summary["invalid"].append(legacy_path.name)
            continue
        destination = obs_dir / legacy_path.name
        try:
            source_bytes = legacy_path.read_bytes()
        except OSError as exc:
            summary["errors"].append(f"{legacy_path.name}: {exc}")
            continue
        if destination.is_file():
            try:
                identical = destination.read_bytes() == source_bytes
            except OSError as exc:
                summary["errors"].append(f"{destination.name}: {exc}")
                continue
            if identical:
                try:
                    legacy_path.unlink()
                except OSError as exc:
                    summary["errors"].append(f"{legacy_path.name}: {exc}")
                    continue
                summary["removed_duplicates"].append(legacy_path.name)
            else:
                summary["conflicts"].append(legacy_path.name)
            continue
        try:
            copy_verified(legacy_path, destination)
            legacy_path.unlink()
        except OSError as exc:
            summary["errors"].append(f"{legacy_path.name}: {exc}")
            continue
        summary["moved"].append(legacy_path.name)
    return summary


def run_migration(
    frames_dir: Path | None = None, observations_dir: Path | None = None
) -> int:
    """Run the legacy-observation migration; return a process exit code.

    Exit 0 only when no legacy observation files remain; otherwise reports
    what still needs attention and exits 1. Safe to re-run.
    """
    summary = migrate_observations(frames_dir, observations_dir)
    src_dir = FRAMES_DIR if frames_dir is None else Path(frames_dir)
    for name in summary["moved"]:
        print(f"  moved: {name}")
    for name in summary["removed_duplicates"]:
        print(f"  removed verified duplicate: {name}")
    for name in summary["conflicts"]:
        print(
            f"  conflict (left in place): {name} — destination already holds "
            f"different content; resolve manually.",
            file=sys.stderr,
        )
    for name in summary["invalid"]:
        print(
            f"  invalid (left in place): {name} — does not validate; "
            f"inspect or remove manually.",
            file=sys.stderr,
        )
    for detail in summary["errors"]:
        print(f"  error: {detail}", file=sys.stderr)
    remaining = [path.name for path in find_legacy_observations(src_dir)]
    print(
        f"Migration finished: {len(summary['moved'])} moved, "
        f"{len(summary['removed_duplicates'])} verified duplicates removed, "
        f"{len(summary['conflicts'])} conflicts, "
        f"{len(summary['invalid'])} invalid, "
        f"{len(summary['errors'])} errors, "
        f"{len(remaining)} legacy file(s) remaining."
    )
    if remaining:
        print(
            "Migration incomplete: legacy observation files remain in the "
            "frames directory (listed above).",
            file=sys.stderr,
        )
        return 1
    print("Migration complete: no legacy observation files remain.")
    return 0


def read_validated_observation(path: Path, expected_timestamp: int) -> dict | None:
    """Return the validated observation at ``path``, or None if unusable.

    An existing file is only trusted after it parses, validates, and matches
    the expected frame timestamp; anything else is treated as absent so the
    frame is (re-)analysed.
    """
    if not path.is_file():
        return None
    try:
        return ensure_observation_timestamp(
            validate_observation(
                parse_observation(path.read_text(encoding="utf-8"))
            ),
            expected_timestamp,
        )
    except (OSError, ValueError):
        return None






def run_vision_test(
    client=None,
    frame_path: Path | None = None,
    observations_dir: Path | None = None,
):
    """Analyse the first extracted frame (frame_00000.jpg) via Baseten.

    The validated observation is saved to the observations directory
    (``output/observations/frame_00000.json``), not next to the JPEG.
    ``client``, ``frame_path``, and ``observations_dir`` default to the live
    Baseten client and standard paths; they exist so tests can inject a fake
    client and fixture paths without network access.
    """
    frame = VISION_FRAME_PATH if frame_path is None else Path(frame_path)
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    warn_about_legacy_observations(frame.parent)
    timestamp = parse_frame_timestamp(frame.name)
    if timestamp is None:
        raise RuntimeError(f"Cannot derive timestamp from frame name: {frame.name}")
    if client is None:
        api_key = os.environ.get("BASETEN_API_KEY")
        if not api_key:
            raise RuntimeError("BASETEN_API_KEY is not set in the environment.")
        client = OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL)
    else:
        api_key = None
    try:
        validated = analyze_frame(client, frame, timestamp, api_key=api_key)
    except VisionAPIError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except ObservationError as exc:
        print(f"Invalid observation: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    rendered = json.dumps(validated, indent=2, ensure_ascii=False)
    print(rendered)
    obs_dir.mkdir(parents=True, exist_ok=True)
    destination = observation_path_for_frame(frame, obs_dir)
    write_observation_json(destination, validated)
    print(f"Saved validated observation: {destination}")


def process_batch_frame(make_client, timestamp: int, frame_path: Path,
                         destination: Path, api_key: str | None = None) -> tuple[str, str]:
    """Analyse one planned batch frame and atomically write its observation.

    Returns ``("succeeded", destination)`` or ``("failed", reason)``. Every
    per-frame failure — a missing, empty, or corrupt image, a provider
    error, an unexpected client error (including timeouts), or an
    unwritable output — is captured as a failure result, so one frame can
    never abort or corrupt its siblings. ``make_client`` is a zero-argument
    context manager called inside the worker, so a live client is never
    shared across threads and an internally created client is always
    closed, whether the request succeeds or fails. A caller-injected
    client is yielded without transferring ownership and is never closed.
    Only ``BaseException``s that must propagate (such as
    ``KeyboardInterrupt``) escape this boundary.
    """
    try:
        with make_client() as vision_client:
            validated = analyze_frame(
                vision_client, frame_path, timestamp, api_key=api_key
            )
    except Exception as exc:
        return ("failed", str(exc))
    try:
        write_observation_json(destination, validated)
    except OSError as exc:
        return ("failed", f"could not write {destination}: {exc}")
    return ("succeeded", str(destination))


def run_vision_batch(
    limit: int = DEFAULT_VISION_BATCH_LIMIT,
    force: bool = False,
    jobs: int = DEFAULT_VISION_BATCH_JOBS,
    client=None,
    directory: Path | None = None,
    observations_dir: Path | None = None,
) -> int:
    """Analyse up to ``limit`` frames spread across the clip; return exit code.

    Each successfully analysed frame gets one validated observation-v1 JSON
    in the observations directory. Frames that already have a validated
    observation there are skipped unless ``force`` is set. Returns 0 only
    when every selected frame ends with a validated observation; any failure
    (or zero candidates) returns 1 after reporting the completed results.
    ``jobs`` bounds how many vision requests run concurrently (1 means
    sequential); results are always reported in selected-frame order, never
    in request-completion order.
    """
    if jobs < 1 or jobs > MAX_VISION_BATCH_JOBS:
        raise ValueError(
            f"jobs must be between 1 and {MAX_VISION_BATCH_JOBS}, got {jobs}."
        )
    frames_dir = FRAMES_DIR if directory is None else Path(directory)
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    warn_about_legacy_observations(frames_dir)
    candidates = discover_candidate_frames(frames_dir)
    if not candidates:
        print(f"Error: no frame_*.jpg candidates in {frames_dir}.", file=sys.stderr)
        return 1
    selected = select_spread_frames(candidates, limit)
    stamps = ", ".join(str(timestamp) for timestamp, _ in selected)
    print(
        f"Discovered {len(candidates)} candidate frames in {frames_dir}; "
        f"selected {len(selected)}: {stamps}"
    )
    obs_dir.mkdir(parents=True, exist_ok=True)
    planned = []
    for timestamp, frame_path in selected:
        destination = observation_path_for_frame(frame_path, obs_dir)
        cached = not force and (
            read_validated_observation(destination, timestamp) is not None
        )
        planned.append((timestamp, frame_path, destination, cached))
    if client is None:
        if any(not cached for _, _, _, cached in planned):
            api_key = os.environ.get("BASETEN_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "BASETEN_API_KEY is not set in the environment."
                )
        else:
            api_key = None

        @contextlib.contextmanager
        def make_client():
            # Fresh client per frame: a live client is never shared across
            # worker threads, and construction performs no I/O. The context
            # manager closes the client after its request finishes, whether
            # the request, response processing, or validation succeeds or
            # raises.
            with OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL) as live_client:
                yield live_client
    else:
        api_key = None

        @contextlib.contextmanager
        def make_client():
            # Caller-owned client: yielded without taking ownership, so the
            # worker must never close it.
            yield client

    succeeded: list[Path] = []
    skipped: list[Path] = []
    failed: list[tuple[Path, str]] = []
    total = len(planned)
    work_items = [
        (position, timestamp, frame_path, destination)
        for position, (timestamp, frame_path, destination, cached) in enumerate(planned)
        if not cached
    ]
    for position, _, frame_path, _ in work_items:
        print(f"[{position + 1}/{total}] Analysing {frame_path} ...")
    outcomes: dict[int, tuple[str, str]] = {}
    if jobs == 1:
        for position, timestamp, frame_path, destination in work_items:
            outcomes[position] = process_batch_frame(
                make_client, timestamp, frame_path, destination, api_key=api_key
            )
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            pending = {
                pool.submit(
                    process_batch_frame,
                    make_client,
                    timestamp,
                    frame_path,
                    destination,
                    api_key,
                ): position
                for position, timestamp, frame_path, destination in work_items
            }
            for future in as_completed(pending):
                outcomes[pending[future]] = future.result()
    for index, (timestamp, frame_path, destination, cached) in enumerate(
        planned, start=1
    ):
        if cached:
            print(
                f"[{index}/{total}] Skipped {frame_path.name}: validated "
                f"observation already exists at {destination.name} "
                "(use --force to re-analyse)."
            )
            skipped.append(destination)
            continue
        status, detail = outcomes[index - 1]
        if status == "failed":
            print(f"Error [{frame_path.name}]: {detail}", file=sys.stderr)
            failed.append((frame_path, detail))
            continue
        print(f"Saved validated observation: {destination}")
        succeeded.append(destination)

    print(
        f"Vision batch finished: {len(candidates)} available, "
        f"{len(selected)} selected, {len(succeeded)} succeeded, "
        f"{len(failed)} failed, {len(skipped)} skipped."
    )
    for path in succeeded:
        print(f"  output: {path}")
    for frame_path, reason in failed:
        print(f"  failure: {frame_path.name}: {reason}")
    if failed and (succeeded or skipped):
        print("Vision batch status: partial failure.")
        return 1
    if failed:
        print("Vision batch status: failure.")
        return 1
    print("Vision batch status: success.")
    return 0


