from pathlib import Path
import argparse
import cv2
import re
import sys

from ..config import (
    DEFAULT_VISION_BATCH_JOBS,
    DEFAULT_VISION_BATCH_LIMIT,
    FRAME_FILENAME_RE,
    FRAMES_DIR,
    MAX_VISION_BATCH_JOBS,
    OBSERVATION_FILENAME_RE,
    OBSERVATIONS_DIR,
    INTERVAL,
    VIDEO_PATH,
)


def extract_frames(video_path: Path | None = None, frames_dir: Path | None = None):
    """Extract one JPEG every INTERVAL seconds from the gameplay video.

    Defaults to the project-root ``gameplay/match.mp4`` video and the
    project-root ``output/frames/`` directory; the Electron worker passes
    the user-selected video and output locations explicitly.
    """
    source = VIDEO_PATH if video_path is None else Path(video_path)
    out_dir = FRAMES_DIR if frames_dir is None else Path(frames_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Video not found: {source.resolve()}")

    out_dir.mkdir(parents=True, exist_ok=True)
    video = cv2.VideoCapture(str(source))

    if not video.isOpened():
        raise RuntimeError("Could not open the video.")

    try:
        fps = video.get(cv2.CAP_PROP_FPS)
        total_frames = video.get(cv2.CAP_PROP_FRAME_COUNT)

        if fps <= 0 or total_frames <= 0:
            raise RuntimeError("Invalid video metadata.")

        duration = total_frames / fps

        print(f"FPS: {fps:.2f}")
        print(f"Frames: {int(total_frames)}")
        print(f"Duration: {duration:.2f} seconds")

        timestamp = 0
        saved_count = 0

        while timestamp < duration:
            video.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = video.read()

            if not success:
                print(f"Could not read frame at {timestamp}s")
                timestamp += INTERVAL
                continue

            output_path = out_dir / f"frame_{timestamp:05d}.jpg"

            if not cv2.imwrite(str(output_path), frame):
                raise RuntimeError(f"Could not save {output_path}")

            output_path.chmod(0o644)
            print(f"Saved: {output_path}")
            saved_count += 1
            timestamp += INTERVAL

        print(f"Finished: {saved_count} frames saved.")
    finally:
        video.release()




def parse_frame_timestamp(name: str) -> int | None:
    """Return the numeric timestamp for a frame filename, else None.

    Matches only the extraction naming convention ``frame_<timestamp>.jpg``;
    anything else (wrong extension, non-numeric stamp, extra text) is not a
    candidate frame.
    """
    match = FRAME_FILENAME_RE.match(name)
    if match is None:
        return None
    return int(match.group(1))


def discover_candidate_frames(directory: Path) -> list[tuple[int, Path]]:
    """List ``(timestamp, path)`` for valid frame JPEGs, sorted by timestamp.

    Sorting is numeric, not lexicographic, so ``frame_00010.jpg`` sorts after
    ``frame_00002.jpg``. Unrelated files and subdirectories are ignored, and
    a missing directory yields no candidates.
    """
    if not directory.is_dir():
        return []
    candidates = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        timestamp = parse_frame_timestamp(entry.name)
        if timestamp is not None:
            candidates.append((timestamp, entry))
    candidates.sort(key=lambda item: item[0])
    return candidates


def select_spread_frames(
    candidates: list[tuple[int, Path]], limit: int
) -> list[tuple[int, Path]]:
    """Select up to ``limit`` candidates spread across the clip.

    ``candidates`` must already be sorted by timestamp (see
    :func:`discover_candidate_frames`). Both endpoints are always included:
    for a limit of two or more the selected indexes are evenly spaced over
    the candidate list, and a limit of one selects the first frame. A limit
    at or above the candidate count selects everything.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}.")
    if limit >= len(candidates):
        return list(candidates)
    if limit == 1:
        return [candidates[0]]
    last = len(candidates) - 1
    return [candidates[round(i * last / (limit - 1))] for i in range(limit)]


def observation_path_for_frame(
    frame_path, observations_dir: Path | None = None
) -> Path:
    """Return the observation JSON path for a frame JPEG.

    Maps ``frame_<timestamp>.jpg`` in the frames tree to
    ``frame_<timestamp>.json`` in the observations tree. ``frame_path`` may
    be a bare filename; ``observations_dir`` defaults to OBSERVATIONS_DIR
    and exists so tests can point at fixture directories.
    """
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    return obs_dir / (Path(frame_path).stem + ".json")


def parse_observation_timestamp(name: str) -> int | None:
    """Return the numeric timestamp for an observation filename, else None.

    Matches only the ``frame_<timestamp>.json`` naming; anything else
    (including the ``match_state.json`` snapshot) is not a stored
    observation.
    """
    match = OBSERVATION_FILENAME_RE.match(name)
    if match is None:
        return None
    return int(match.group(1))


def discover_observation_files(directory: Path) -> list[tuple[int, Path]]:
    """List ``(timestamp, path)`` for stored observation JSONs, sorted.

    Sorting is numeric by filename timestamp. The state snapshot and
    unrelated files never match the frame-based naming and are ignored; a
    missing directory yields no candidates.
    """
    if not directory.is_dir():
        return []
    candidates = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        timestamp = parse_observation_timestamp(entry.name)
        if timestamp is not None:
            candidates.append((timestamp, entry))
    candidates.sort(key=lambda item: item[0])
    return candidates


def find_legacy_observations(frames_dir: Path | None = None) -> list[Path]:
    """List observation JSONs still stored alongside frames (legacy layout).

    These are leftovers from before observations moved to OBSERVATIONS_DIR;
    see :func:`migrate_observations`. Sorted by numeric timestamp.
    """
    directory = FRAMES_DIR if frames_dir is None else Path(frames_dir)
    if not directory.is_dir():
        return []
    legacy = [
        entry
        for entry in directory.iterdir()
        if entry.is_file() and parse_observation_timestamp(entry.name) is not None
    ]
    legacy.sort(key=lambda path: parse_observation_timestamp(path.name))
    return legacy


def warn_about_legacy_observations(frames_dir: Path | None = None) -> None:
    """Print a non-blocking pointer to --migrate-observations when needed."""
    legacy = find_legacy_observations(frames_dir)
    if legacy:
        names = ", ".join(path.name for path in legacy[:5])
        extra = f" and {len(legacy) - 5} more" if len(legacy) > 5 else ""
        print(
            f"Warning: {len(legacy)} legacy observation file(s) in the frames "
            f"directory ({names}{extra}); run "
            f"'python main.py --migrate-observations' to move them to the "
            f"observations directory.",
            file=sys.stderr,
        )


def parse_limit_argument(value: str) -> int:
    """Argparse type for ``--limit``: a positive integer, else a CLI error."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--limit must be a positive integer, got {value!r}."
        ) from None
    if limit < 1:
        raise argparse.ArgumentTypeError(f"--limit must be at least 1, got {limit}.")
    return limit


def parse_jobs_argument(value: str) -> int:
    """Argparse type for ``--jobs``: an integer from 1 to MAX_VISION_BATCH_JOBS."""
    try:
        jobs = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--jobs must be an integer from 1 to {MAX_VISION_BATCH_JOBS}, "
            f"got {value!r}."
        ) from None
    if jobs < 1 or jobs > MAX_VISION_BATCH_JOBS:
        raise argparse.ArgumentTypeError(
            f"--jobs must be between 1 and {MAX_VISION_BATCH_JOBS}, got {jobs}."
        )
    return jobs


