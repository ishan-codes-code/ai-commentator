import argparse
import base64
import contextlib
import hashlib
import json
import math
import mimetypes
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np
from openai import APIError, OpenAI

# Repository root (the directory containing electron/, python/, tests/).
# All default input/output paths resolve from here so the engine behaves
# the same regardless of the process working directory. Every runner also
# accepts explicit directory overrides for Electron-driven runs.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VIDEO_PATH = PROJECT_ROOT / "gameplay" / "match.mp4"
# Output layout: extracted JPEGs live under FRAMES_DIR; generated JSON
# (per-frame observations and the rolling match-state snapshot) lives under
# OBSERVATIONS_DIR. The two trees share frame-based basenames
# (frame_<timestamp>.jpg <-> frame_<timestamp>.json) but never mix.
FRAMES_DIR = PROJECT_ROOT / "output" / "frames"
OBSERVATIONS_DIR = PROJECT_ROOT / "output" / "observations"
MATCH_STATE_FILENAME = "match_state.json"
MATCH_STATE_PATH = OBSERVATIONS_DIR / MATCH_STATE_FILENAME
VISION_FRAME_PATH = FRAMES_DIR / "frame_00000.jpg"
INTERVAL = 3
BASETEN_BASE_URL = "https://inference.baseten.co/v1"
BASETEN_MODEL = "meta-models/muse-spark-open-1026"
OBSERVATION_SCHEMA_VERSION = "observation-v1"
OBSERVATION_CATEGORIES = ("environment", "character", "hud", "combat", "other")
OBSERVATION_CONFIDENCES = ("high", "medium", "low")
OBSERVATION_OUTPUT_PATH = OBSERVATIONS_DIR / "frame_00000.json"
FRAME_FILENAME_RE = re.compile(r"^frame_(\d+)\.jpg$")
OBSERVATION_FILENAME_RE = re.compile(r"^frame_(\d+)\.json$")
MATCH_STATE_SCHEMA_VERSION = "match-state-v1"
MATCH_STATE_STATUSES = ("current", "stale", "conflicted")
MATCH_STATE_CHANGE_KINDS = ("new_fact", "update")
EVENTS_DIR = PROJECT_ROOT / "output" / "events"
EVENTS_FILENAME = "events.json"
EVENTS_PATH = EVENTS_DIR / EVENTS_FILENAME
EVENT_SCHEMA_VERSION = "events-v1"
EVENT_STATUSES = ("candidate",)
EVENT_RULES = ("kill_feed_entry", "elim_count_increased")
EVENT_FEED_MENTIONS = ("kill feed", "killfeed", "elimination feed", "elim feed")
EVENT_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_]+")
EVENT_ELIM_RE = re.compile(r"Elim\s+(\d+)")
EVENT_TEAM_SCOPE_RE = re.compile(r"\bteam\s+([A-Za-z0-9_]+)", re.IGNORECASE)
EVENT_TRIGGER_CONFIDENCES = ("high", "medium")
COMMENTARY_DIR = PROJECT_ROOT / "output" / "commentary"
COMMENTARY_FILENAME = "commentary.json"
COMMENTARY_PATH = COMMENTARY_DIR / COMMENTARY_FILENAME
COMMENTARY_SCHEMA_VERSION = "commentary-v1"
COMMENTARY_TYPES = ("scene", "development", "event_echo")
COMMENTARY_PRIOR_STATUSES = ("absent", "ok", "ignored-malformed")
COMMENTARY_TEMPLATES = (
    "scene_visible",
    "hud_reading",
    "development_new",
    "development_update",
    "event_echo",
)
COMMENTARY_SCENE_CATEGORIES = ("environment", "character")
COMMENTARY_QUALIFIERS = {
    "kill_feed_entry": "killer and victim roles are not established",
    "elim_count_increased": (
        "who was eliminated, by whom, and by what means are not established"
    ),
}
COMMENTARY_GATE_RULES = (
    "valid-inputs",
    "no-conflicted-evidence",
    "confidence-floor",
    "uncertainty-scope",
    "category-scope",
    "no-elimination-from-absence",
    "already-emitted",
    "no-justified-wording",
    "stale-fact",
    "subsumed-by-development",
)
WRITER_MAX_CLAIMS = 6
WRITER_MAX_HISTORY_LINES = 4
WRITER_MAX_EVIDENCE_CHARS = 500
SCRIPT_DIR = PROJECT_ROOT / "output" / "script"
SCRIPT_FILENAME = "script.json"
SCRIPT_PATH = SCRIPT_DIR / SCRIPT_FILENAME
SCRIPT_SCHEMA_VERSION = "script-v1"
SCRIPT_STATUSES = ("success", "silence", "error")
SCRIPT_MIN_TEXT_CHARS = 8
SCRIPT_MAX_TEXT_CHARS = 600
SCRIPT_MAX_CLAIMS = WRITER_MAX_CLAIMS
SCRIPT_MAX_HISTORY_LINES = WRITER_MAX_HISTORY_LINES
SCRIPT_UNCERTAINTY_MARKERS = (
    "unconfirmed",
    "possible",
    "possibly",
    "may have",
    "might",
    "could be",
    "looks like",
    "appears",
    "appears to",
    "suspected",
    "reported",
    "signs of",
)
SCRIPT_CERTAINTY_MARKERS = ("confirmed", "definitely", "certainly")
SCRIPT_KILL_STEMS = (
    "kill", "killed", "killer", "elim", "knock", "knocked",
    "assist", "damage", "headshot", "downed",
)
SCRIPT_OUTCOME_STEMS = (
    "victor", " winner", " wins", "defeat", " champion",
    "placement", "first place", "top 1", "takes the match",
)
SCRIPT_ACTION_STEMS = (
    "pushing", "retreat", "healing", "fighting", "camping",
    "rotating", "rotation", " aiming", "flanking", "third-part",
)
SCRIPT_ZONE_STEMS = ("safe zone", "safe-zone", "map control", "circle is")
SCRIPT_CAUSAL_MARKERS = (
    "because", "therefore", "as a result", "led to", "caused by",
    "thanks to",
)
SCRIPT_INTENT_MARKERS = (
    "trying to", "planning to", "intends to", "wants to",
)
SCRIPT_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10,
}
DEFAULT_VISION_BATCH_LIMIT = 5
DEFAULT_VISION_BATCH_JOBS = 1
MAX_VISION_BATCH_JOBS = 4


class ObservationError(ValueError):
    """Model output failed observation-v1 parsing or validation."""


class VisionAPIError(RuntimeError):
    """Baseten vision request failed; the message is already redacted."""


class WriterAPIError(RuntimeError):
    """Baseten commentary-writer request failed; the message is already redacted."""


def vision_frame_timestamp():
    try:
        return int(VISION_FRAME_PATH.stem.split("_")[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot derive timestamp from frame name: {VISION_FRAME_PATH.name}"
        ) from exc


def build_vision_prompt(timestamp):
    categories = ", ".join(f'"{c}"' for c in OBSERVATION_CATEGORIES)
    confidences = ", ".join(f'"{c}"' for c in OBSERVATION_CONFIDENCES)
    return (
        "Describe only the visible evidence in this Free Fire gameplay frame. "
        f"This frame is at {timestamp} seconds in the video. "
        "Cover the environment, characters, visible HUD elements, and apparent "
        "combat activity. Clearly distinguish direct observations from uncertain "
        "interpretations. Do not invent events, actions, identities, or outcomes "
        "that are not visible in this single frame. "
        "Respond with ONLY a single JSON object, no markdown fences, no commentary, "
        "matching this shape: "
        '{"schema_version": "observation-v1", '
        '"timestamp": <number, the frame timestamp stated above>, '
        '"observations": [{"category": <one of: ' + categories + ">, "
        '"description": <non-empty string of what is visible>, '
        '"confidence": <one of: ' + confidences + ">, "
        '"evidence": <non-empty string grounding the claim>}], '
        '"uncertainties": [<strings describing what is unreadable or ambiguous>]}. '
        "Put anything unreadable or ambiguous in uncertainties; never invent detail."
    )


def parse_observation(text):
    if not isinstance(text, str) or not text.strip():
        raise ObservationError("Baseten returned no text content.")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", cleaned, re.DOTALL)
        if match is None:
            raise ObservationError("Model output is not a single JSON value.")
        cleaned = match.group(1).strip()
        if not cleaned:
            raise ObservationError("Baseten returned no text content.")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ObservationError(f"Model output is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ObservationError("Model output must be a JSON object.")
    return data


def _reject_extra(mapping, allowed, where):
    extra = sorted(set(mapping) - set(allowed))
    if extra:
        raise ObservationError(f"{where} has unsupported fields: {', '.join(extra)}.")


def _require_non_empty_string(mapping, field, where):
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ObservationError(f"{where} requires a non-empty string field '{field}'.")
    return value


def validate_observation(data):
    if not isinstance(data, dict):
        raise ObservationError("Observation must be a JSON object.")
    _reject_extra(
        data,
        ("schema_version", "timestamp", "observations", "uncertainties"),
        "Observation",
    )
    if data.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise ObservationError(
            "Observation requires schema_version "
            f"'{OBSERVATION_SCHEMA_VERSION}'."
        )
    if "timestamp" not in data:
        raise ObservationError("Observation requires field 'timestamp'.")
    timestamp = data["timestamp"]
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or (isinstance(timestamp, float) and not math.isfinite(timestamp))
    ):
        raise ObservationError("Observation field 'timestamp' must be a number.")
    if "observations" not in data or not isinstance(data["observations"], list):
        raise ObservationError("Observation requires field 'observations' as a list.")
    for index, item in enumerate(data["observations"]):
        where = f"observations[{index}]"
        if not isinstance(item, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            item, ("category", "description", "confidence", "evidence"), where
        )
        if item.get("category") not in OBSERVATION_CATEGORIES:
            raise ObservationError(
                f"{where} has unsupported category "
                f"'{item.get('category')}'; expected one of: "
                f"{', '.join(OBSERVATION_CATEGORIES)}."
            )
        _require_non_empty_string(item, "description", where)
        if item.get("confidence") not in OBSERVATION_CONFIDENCES:
            raise ObservationError(
                f"{where} has invalid confidence '{item.get('confidence')}'; "
                f"expected one of: {', '.join(OBSERVATION_CONFIDENCES)}."
            )
        _require_non_empty_string(item, "evidence", where)
    if "uncertainties" not in data or not isinstance(data["uncertainties"], list):
        raise ObservationError("Observation requires field 'uncertainties' as a list.")
    for index, entry in enumerate(data["uncertainties"]):
        if not isinstance(entry, str) or not entry.strip():
            raise ObservationError(
                f"uncertainties[{index}] must be a non-empty string."
            )
    return data


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


def load_frame_image(frame_path: Path) -> tuple[bytes, str, int, int]:
    """Validate a frame file; return ``(bytes, MIME type, width, height)``.

    Raises FileNotFoundError for a missing file and ValueError for an empty,
    undecodable, or non-image file.
    """
    if not frame_path.is_file():
        raise FileNotFoundError(f"Frame not found: {frame_path}")
    image_bytes = frame_path.read_bytes()
    if not image_bytes:
        raise ValueError(f"Frame is empty: {frame_path}")
    image = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    if image is None or image.size == 0:
        raise ValueError(f"Frame is not a valid image: {frame_path}")
    mime_type, _ = mimetypes.guess_type(frame_path.name)
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"Could not determine image MIME type: {frame_path}")
    height, width = image.shape[:2]
    return image_bytes, mime_type, width, height


def build_image_data_url(image_bytes: bytes, mime_type: str) -> str:
    """Encode validated image bytes as a data URL for the vision request."""
    return f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def extract_response_text(response) -> str:
    """Return the text content of a chat completion, rejecting empty ones.

    Raises ObservationError when the provider returns no choices or no text
    content, so callers never see an IndexError or a None dereference.
    """
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    text = getattr(message, "content", None)
    if not isinstance(text, str) or not text.strip():
        raise ObservationError("Baseten returned no text content.")
    return text


def request_observation_text(
    client,
    timestamp: int,
    image_data_url: str,
    api_key: str | None = None,
) -> str:
    """Request one observation-v1 JSON document; return the raw model text.

    Provider failures raise VisionAPIError with the secret redacted; empty
    completions raise ObservationError.
    """
    try:
        raw_response = client.chat.completions.with_raw_response.create(
            model=BASETEN_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": build_vision_prompt(timestamp),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                }
            ],
        )
        print(f"Baseten API response: HTTP {raw_response.status_code}")
        response = raw_response.parse()
    except APIError as exc:
        status_code = getattr(exc, "status_code", None)
        status = f"HTTP {status_code}" if status_code else "request error"
        detail = getattr(exc, "message", None) or str(exc)
        if exc.__cause__:
            detail = f"{detail} Cause: {exc.__cause__}"
        if api_key:
            detail = detail.replace(api_key, "[REDACTED]")
        raise VisionAPIError(f"Baseten API {status}: {detail}") from None
    return extract_response_text(response)


def ensure_observation_timestamp(validated: dict, expected: int) -> dict:
    """Reject a validated observation whose timestamp is not the source frame's.

    The model timestamp is never silently overwritten: a mismatch means the
    response does not describe the requested frame.
    """
    actual = validated.get("timestamp")
    if actual != expected:
        raise ObservationError(
            f"Model returned timestamp {actual!r}, expected {expected} for this frame."
        )
    return validated


def analyze_frame(
    client,
    frame_path: Path,
    timestamp: int,
    api_key: str | None = None,
) -> dict:
    """Run the full vision pipeline for one frame; return the validated observation."""
    image_bytes, mime_type, width, height = load_frame_image(frame_path)
    print(f"Validated frame: {frame_path} ({width}x{height}, {mime_type})")
    text = request_observation_text(
        client,
        timestamp,
        build_image_data_url(image_bytes, mime_type),
        api_key=api_key,
    )
    return ensure_observation_timestamp(
        validate_observation(parse_observation(text)), timestamp
    )


def write_json_atomic(destination: Path, data: dict) -> None:
    """Atomically write a JSON document (UTF-8, indented, trailing newline).

    The document is written to a temporary file in the destination directory
    and moved over the target only after the write succeeds, so a failure
    never leaves a partially written JSON file behind.
    """
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(rendered)
        os.replace(tmp_path, destination)
        tmp_path = None
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def write_observation_json(destination: Path, validated: dict) -> None:
    """Atomically write a validated observation JSON document."""
    write_json_atomic(destination, validated)


def write_match_state(destination: Path, state: dict) -> None:
    """Atomically write a validated match-state snapshot."""
    write_json_atomic(destination, state)


def copy_verified(source: Path, destination: Path) -> None:
    """Byte-copy a file via temp+replace, verifying the destination bytes.

    The source is never modified. Raises on any failure without leaving a
    partial or corrupt destination file behind.
    """
    data = source.read_bytes()
    tmp_path = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
        os.replace(tmp_path, destination)
        tmp_path = None
        if destination.read_bytes() != data:
            destination.unlink()
            raise OSError(f"verification read mismatch for {destination}")
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


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


def observation_sha256(validated: dict) -> str:
    """Hex digest of a validated observation's canonical JSON form."""
    canonical = json.dumps(
        validated, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fact_key(category: str, description: str) -> tuple[str, str]:
    """Identity of a match-state fact shared by the tracker and detector.

    The exact ``(category, description)`` pair after stripping surrounding
    whitespace — no fuzzy matching, no name parsing, no cross-category
    merging. A reworded claim is a different fact.
    """
    return (category, description.strip())


def new_match_state_builder() -> dict:
    """Return an empty working builder for match-state construction.

    The builder carries private bookkeeping (``_seen``, ``_contested``) that
    :func:`finalize_match_state` strips; only the finalized document
    conforms to ``match-state-v1``.
    """
    return {
        "schema_version": MATCH_STATE_SCHEMA_VERSION,
        "latest_timestamp": None,
        "sources": [],
        "facts": {},
        "changes": [],
        "conflicts": [],
        "uncertainties": {},
        "_seen": {},
        "_contested": set(),
    }


def validate_match_state(data: dict) -> dict:
    """Strictly validate a match-state-v1 document; reject anything else."""
    if not isinstance(data, dict):
        raise ObservationError("Match state must be a JSON object.")
    _reject_extra(
        data,
        (
            "schema_version",
            "latest_timestamp",
            "sources",
            "facts",
            "changes",
            "conflicts",
            "uncertainties",
        ),
        "Match state",
    )
    if data.get("schema_version") != MATCH_STATE_SCHEMA_VERSION:
        raise ObservationError(
            "Match state requires schema_version "
            f"'{MATCH_STATE_SCHEMA_VERSION}'."
        )
    latest = data.get("latest_timestamp")
    if latest is not None and (
        isinstance(latest, bool)
        or not isinstance(latest, (int, float))
        or (isinstance(latest, float) and not math.isfinite(latest))
    ):
        raise ObservationError("Match state field 'latest_timestamp' must be "
                               "a number or null.")
    for field in ("sources", "facts", "changes", "conflicts", "uncertainties"):
        if field not in data or not isinstance(data[field], list):
            raise ObservationError(
                f"Match state requires field '{field}' as a list."
            )
    for index, source in enumerate(data["sources"]):
        where = f"sources[{index}]"
        if not isinstance(source, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            source,
            ("timestamp", "file", "observations", "uncertainties", "sha256"),
            where,
        )
        timestamp = source.get("timestamp")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or (isinstance(timestamp, float) and not math.isfinite(timestamp))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        _require_non_empty_string(source, "file", where)
        for count_field in ("observations", "uncertainties"):
            count = source.get(count_field)
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise ObservationError(
                    f"{where} field '{count_field}' must be a non-negative integer."
                )
        _require_non_empty_string(source, "sha256", where)
    for index, fact in enumerate(data["facts"]):
        where = f"facts[{index}]"
        if not isinstance(fact, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            fact,
            (
                "id",
                "category",
                "description",
                "confidence",
                "evidence",
                "first_seen",
                "last_seen",
                "last_source",
                "status",
                "history",
            ),
            where,
        )
        _require_non_empty_string(fact, "id", where)
        if fact.get("category") not in OBSERVATION_CATEGORIES:
            raise ObservationError(
                f"{where} has unsupported category '{fact.get('category')}'."
            )
        _require_non_empty_string(fact, "description", where)
        if fact.get("confidence") not in OBSERVATION_CONFIDENCES:
            raise ObservationError(
                f"{where} has invalid confidence '{fact.get('confidence')}'."
            )
        _require_non_empty_string(fact, "evidence", where)
        for seen_field in ("first_seen", "last_seen"):
            seen = fact.get(seen_field)
            if (
                isinstance(seen, bool)
                or not isinstance(seen, (int, float))
                or (isinstance(seen, float) and not math.isfinite(seen))
            ):
                raise ObservationError(f"{where} field '{seen_field}' must be a number.")
        if fact["first_seen"] > fact["last_seen"]:
            raise ObservationError(
                f"{where} has first_seen after last_seen."
            )
        _require_non_empty_string(fact, "last_source", where)
        if fact.get("status") not in MATCH_STATE_STATUSES:
            raise ObservationError(
                f"{where} has invalid status '{fact.get('status')}'."
            )
        if "history" not in fact or not isinstance(fact["history"], list):
            raise ObservationError(f"{where} requires field 'history' as a list.")
        for entry_index, entry in enumerate(fact["history"]):
            entry_where = f"{where}.history[{entry_index}]"
            if not isinstance(entry, dict):
                raise ObservationError(f"{entry_where} must be an object.")
            _reject_extra(
                entry, ("timestamp", "source", "confidence", "evidence"), entry_where
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
            _require_non_empty_string(entry, "source", entry_where)
            if entry.get("confidence") not in OBSERVATION_CONFIDENCES:
                raise ObservationError(
                    f"{entry_where} has invalid confidence "
                    f"'{entry.get('confidence')}'."
                )
            _require_non_empty_string(entry, "evidence", entry_where)
    for index, change in enumerate(data["changes"]):
        where = f"changes[{index}]"
        if not isinstance(change, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            change, ("timestamp", "kind", "fact_id", "detail", "source"), where
        )
        change_ts = change.get("timestamp")
        if (
            isinstance(change_ts, bool)
            or not isinstance(change_ts, (int, float))
            or (isinstance(change_ts, float) and not math.isfinite(change_ts))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        if change.get("kind") not in MATCH_STATE_CHANGE_KINDS:
            raise ObservationError(
                f"{where} has invalid kind '{change.get('kind')}'."
            )
        _require_non_empty_string(change, "fact_id", where)
        _require_non_empty_string(change, "detail", where)
        _require_non_empty_string(change, "source", where)
    for index, conflict in enumerate(data["conflicts"]):
        where = f"conflicts[{index}]"
        if not isinstance(conflict, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(conflict, ("timestamp", "sources", "detail"), where)
        conflict_ts = conflict.get("timestamp")
        if (
            isinstance(conflict_ts, bool)
            or not isinstance(conflict_ts, (int, float))
            or (isinstance(conflict_ts, float) and not math.isfinite(conflict_ts))
        ):
            raise ObservationError(f"{where} field 'timestamp' must be a number.")
        sources = conflict.get("sources")
        if (
            not isinstance(sources, list)
            or len(sources) < 2
            or any(not isinstance(item, str) or not item.strip() for item in sources)
        ):
            raise ObservationError(
                f"{where} requires 'sources' as a list of at least two "
                f"non-empty strings."
            )
        _require_non_empty_string(conflict, "detail", where)
    for index, uncertainty in enumerate(data["uncertainties"]):
        where = f"uncertainties[{index}]"
        if not isinstance(uncertainty, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            uncertainty,
            ("text", "first_seen", "last_seen", "sources", "resolved"),
            where,
        )
        _require_non_empty_string(uncertainty, "text", where)
        for seen_field in ("first_seen", "last_seen"):
            seen = uncertainty.get(seen_field)
            if (
                isinstance(seen, bool)
                or not isinstance(seen, (int, float))
                or (isinstance(seen, float) and not math.isfinite(seen))
            ):
                raise ObservationError(f"{where} field '{seen_field}' must be a number.")
        if uncertainty["first_seen"] > uncertainty["last_seen"]:
            raise ObservationError(
                f"{where} has first_seen after last_seen."
            )
        sources = uncertainty.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(item, str) or not item.strip() for item in sources)
        ):
            raise ObservationError(
                f"{where} requires 'sources' as a non-empty list of strings."
            )
        if not isinstance(uncertainty.get("resolved"), bool):
            raise ObservationError(f"{where} field 'resolved' must be a boolean.")
    return data


def apply_observation(builder: dict, validated: dict, source_name: str) -> str:
    """Fold one validated observation into a working match-state builder.

    Fact identity is the exact ``(category, description)`` pair — no fuzzy
    matching, no name parsing, no cross-category merging. Returns
    ``"applied"``, ``"duplicate"`` (same timestamp and content already
    processed — a no-op), or ``"conflict"`` (same timestamp, different
    content — both provenances preserved, neither silently chosen).
    """
    timestamp = validated["timestamp"]
    digest = observation_sha256(validated)
    seen = builder["_seen"].setdefault(timestamp, {"digests": set(), "sources": []})
    if digest in seen["digests"]:
        return "duplicate"
    # A conflict needs two distinct provenances: the same source restating
    # changed content is applied as an ordinary update, not a conflict.
    contested = bool(seen["digests"]) and len(set(seen["sources"]) | {source_name}) >= 2
    if contested:
        builder["_contested"].add(timestamp)
        existing = next(
            (
                entry
                for entry in builder["conflicts"]
                if entry["timestamp"] == timestamp
            ),
            None,
        )
        merged = sorted(set(seen["sources"]) | {source_name})
        if existing is None:
            builder["conflicts"].append(
                {
                    "timestamp": timestamp,
                    "sources": merged,
                    "detail": (
                        f"Divergent observations for timestamp {timestamp!r}: "
                        f"{', '.join(merged)}."
                    ),
                }
            )
        else:
            existing["sources"] = sorted(set(existing["sources"]) | {source_name})
            existing["detail"] = (
                f"Divergent observations for timestamp {timestamp!r}: "
                f"{', '.join(existing['sources'])}."
            )
    seen["digests"].add(digest)
    seen["sources"].append(source_name)
    builder["sources"].append(
        {
            "timestamp": timestamp,
            "file": source_name,
            "observations": len(validated["observations"]),
            "uncertainties": len(validated["uncertainties"]),
            "sha256": digest,
        }
    )
    if builder["latest_timestamp"] is None or timestamp > builder["latest_timestamp"]:
        builder["latest_timestamp"] = timestamp
    for item in validated["observations"]:
        key = fact_key(item["category"], item["description"])
        fact = builder["facts"].get(key)
        if fact is None:
            fact = {
                "category": item["category"],
                "description": item["description"].strip(),
                "sightings": [],
            }
            builder["facts"][key] = fact
            builder["changes"].append(
                {
                    "timestamp": timestamp,
                    "kind": "new_fact",
                    "key": key,
                    "detail": f"First observed ({item['category']}): "
                    f"{item['description'].strip()[:120]}",
                    "source": source_name,
                }
            )
        else:
            latest_sighting = max(
                fact["sightings"], key=lambda entry: (entry["timestamp"], entry["source"])
            )
            if (
                latest_sighting["confidence"] != item["confidence"]
                or latest_sighting["evidence"] != item["evidence"]
            ):
                builder["changes"].append(
                    {
                        "timestamp": timestamp,
                        "kind": "update",
                        "key": key,
                        "detail": (
                            f"Re-observed ({item['category']}) with different "
                            f"confidence/evidence."
                        ),
                        "source": source_name,
                    }
                )
        fact["sightings"].append(
            {
                "timestamp": timestamp,
                "source": source_name,
                "confidence": item["confidence"],
                "evidence": item["evidence"],
            }
        )
    for text in validated["uncertainties"]:
        entry = builder["uncertainties"].get(text)
        if entry is None:
            builder["uncertainties"][text] = {
                "first_seen": timestamp,
                "last_seen": timestamp,
                "sources": [source_name],
            }
        else:
            entry["first_seen"] = min(entry["first_seen"], timestamp)
            entry["last_seen"] = max(entry["last_seen"], timestamp)
            if source_name not in entry["sources"]:
                entry["sources"].append(source_name)
    return "conflict" if contested else "applied"


def finalize_match_state(builder: dict) -> dict:
    """Convert a working builder into a validated match-state-v1 document.

    Ids assign in first-seen order and every list is emitted sorted, so the
    same source observations always produce byte-identical state regardless
    of the order they were applied in. Raises on internal inconsistency;
    never returns an invalid document.
    """
    latest = builder["latest_timestamp"]
    contested = set(builder["_contested"])
    ordered_keys = sorted(
        builder["facts"],
        key=lambda key: (
            min(entry["timestamp"] for entry in builder["facts"][key]["sightings"]),
            key[0],
            key[1],
        ),
    )
    key_to_id = {key: f"f{i:04d}" for i, key in enumerate(ordered_keys, start=1)}
    facts = []
    for key in ordered_keys:
        working = builder["facts"][key]
        sightings = sorted(
            working["sightings"], key=lambda entry: (entry["timestamp"], entry["source"])
        )
        first_seen = sightings[0]["timestamp"]
        latest_sighting = sightings[-1]
        if first_seen in contested:
            status = "conflicted"
        elif latest is not None and latest_sighting["timestamp"] == latest:
            status = "current"
        else:
            status = "stale"
        facts.append(
            {
                "id": key_to_id[key],
                "category": working["category"],
                "description": working["description"],
                "confidence": latest_sighting["confidence"],
                "evidence": latest_sighting["evidence"],
                "first_seen": first_seen,
                "last_seen": latest_sighting["timestamp"],
                "last_source": latest_sighting["source"],
                "status": status,
                "history": [
                    {
                        "timestamp": entry["timestamp"],
                        "source": entry["source"],
                        "confidence": entry["confidence"],
                        "evidence": entry["evidence"],
                    }
                    for entry in sightings
                ],
            }
        )
    changes = sorted(
        (
            {
                "timestamp": entry["timestamp"],
                "kind": entry["kind"],
                "fact_id": key_to_id[entry["key"]],
                "detail": entry["detail"],
                "source": entry["source"],
            }
            for entry in builder["changes"]
        ),
        key=lambda entry: (entry["timestamp"], entry["kind"], entry["fact_id"]),
    )
    state = {
        "schema_version": MATCH_STATE_SCHEMA_VERSION,
        "latest_timestamp": latest,
        "sources": sorted(
            builder["sources"], key=lambda entry: (entry["timestamp"], entry["file"])
        ),
        "facts": facts,
        "changes": changes,
        "conflicts": sorted(builder["conflicts"], key=lambda entry: entry["timestamp"]),
        "uncertainties": [
            {
                "text": text,
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                "sources": sorted(entry["sources"]),
                "resolved": False,
            }
            for text, entry in sorted(builder["uncertainties"].items())
        ],
    }
    return validate_match_state(state)


def build_match_state(observations: list) -> dict:
    """Build a match-state-v1 document from ``(timestamp, source, validated)``.

    Input order does not matter: observations are applied sorted by
    (timestamp, source), so repeated builds over the same files are
    byte-identical.
    """
    builder = new_match_state_builder()
    for timestamp, source_name, validated in sorted(
        observations, key=lambda item: (item[0], item[1])
    ):
        if validated["timestamp"] != timestamp:
            raise ObservationError(
                f"Source {source_name} timestamp {validated['timestamp']!r} does "
                f"not match expected {timestamp}."
            )
        apply_observation(builder, validated, source_name)
    return finalize_match_state(builder)


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
    return "".join(parts)


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


def script_segment_id(index: int) -> str:
    """Segment id in emission order (chronological): segment-001, ..."""
    return f"segment-{index + 1:03d}"


def build_script_prompt(claims: list, history_texts: list) -> str:
    """Build the bounded prompt for the final-script model call.

    The model sees only evidence-gated claims plus recent spoken lines.
    It never sees raw frames, raw observations, or full match history,
    and it must return JSON mapping source claim ids to spoken segments.
    """
    parts = [
        "You are a skilled live esports commentator covering Free Fire. ",
        "Write broadcast-ready spoken lines, one segment per moment below. ",
        "Use concise, energetic sentences that are comfortable to speak aloud. ",
        "Prefer active voice and natural contractions. Vary sentence openings ",
        "and transitions. Keep it exciting without melodrama. ",
        "Prioritize confirmed eliminations and major combat developments, ",
        "then significant status changes, then match-state developments, ",
        "then continuity. Skip visually obvious but unimportant HUD details. ",
        "Never speak just to fill silence: if an item cannot support an ",
        "interesting, useful line, omit it. ",
        "Strict evidence rules: use only the quoted evidence below. Never add ",
        "kills, knocks, assists, damage, identities, teams, positions, ",
        "movements, weapons, strategies, intentions, zone status, rotations, ",
        "map control, placement, victory, defeat, causes, or effects beyond ",
        "the quotes. Never say a player is pushing, retreating, healing, ",
        "fighting, camping, rotating, or aiming unless the quote says so. ",
        "A candidate event is NOT confirmed: echo segments must keep an ",
        "uncertainty marker such as 'unconfirmed' or 'possible'. Never ",
        "present a candidate as a confirmed kill, knock, or victory, and ",
        "never name a killer, victim, or outcome the quotes do not give. ",
        "Mention a player name only when the quote contains it. Do not read ",
        "JSON, field names, UI labels, or long detail lists aloud. No ",
        "markdown, headings, emojis, or excessive exclamation marks. ",
        "Respond with ONLY a single JSON object, no markdown fences, no ",
        "commentary, matching this shape: ",
        '{"segments": [{"source_claim_ids": [<one or more ids below>], ',
        '"text": <the spoken line>}]}. ',
        "Omit any item you cannot word safely. ",
    ]
    for claim in claims:
        evidence = " / ".join(claim["evidence"])[:WRITER_MAX_EVIDENCE_CHARS]
        parts.append(
            f"Claim {claim['claim_id']} (at {claim['timestamp']}s, "
            f"confidence {claim['confidence']}, "
            f"evidence: {evidence})."
        )
        if claim["qualification"] is not None:
            parts.append(f" Keep uncertainty: {claim['qualification']}.")
        parts.append(" ")
    if history_texts:
        parts.append("Already said (do not repeat this information): ")
        parts.append(" | ".join(history_texts[-SCRIPT_MAX_HISTORY_LINES:]))
    return "".join(parts)


def _script_norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _script_numbers(text: str) -> list:
    numbers = []
    for match in re.finditer(r"\d+(?:\.\d+)?", text):
        token = match.group(0)
        numbers.append(float(token) if "." in token else int(token))
    for word, value in SCRIPT_WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b", text.lower()):
            numbers.append(value)
    return numbers


def _stem_present(lowered: str, stems: tuple) -> bool:
    return any(stem in lowered for stem in stems)


def script_text_valid(text: str, cited_claims: list) -> bool:
    """Deterministic paraphrase check for one script segment.

    Paraphrase is allowed: evidence quotes need not appear verbatim.
    What is enforced instead: TTS-ready shape, no invented names or
    numbers, no forbidden semantic additions beyond the cited evidence,
    and preserved uncertainty for candidate events. Returns True only
    when the wording passes every check.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    stripped = text.strip()
    if not (SCRIPT_MIN_TEXT_CHARS <= len(stripped) <= SCRIPT_MAX_TEXT_CHARS):
        return False
    if "```" in text or text.lstrip().startswith("#"):
        return False
    if "**" in text or "claim_id" in text or "source_claim_ids" in text:
        return False
    if text.count("!") > 2:
        return False
    evidence_texts = []
    for claim in cited_claims:
        evidence_texts.extend(claim["evidence"])
    evidence_joined = " / ".join(evidence_texts)
    evidence_lower = evidence_joined.lower()
    text_lower = text.lower()
    evidence_names = set()
    for quote in evidence_texts:
        evidence_names.update(m.group(0) for m in EVENT_NAME_RE.finditer(quote))
    for token in _feed_name_tokens(text):
        if token not in evidence_names:
            return False
    evidence_numbers = set(_script_numbers(evidence_joined))
    cited_timestamps = {claim["timestamp"] for claim in cited_claims}
    for number in _script_numbers(text):
        if number not in evidence_numbers and number not in cited_timestamps:
            return False
    if not _stem_present(evidence_lower, SCRIPT_KILL_STEMS):
        if _stem_present(text_lower, SCRIPT_KILL_STEMS):
            return False
    if not _stem_present(evidence_lower, SCRIPT_OUTCOME_STEMS):
        if _stem_present(text_lower, SCRIPT_OUTCOME_STEMS):
            return False
    if not _stem_present(evidence_lower, SCRIPT_ACTION_STEMS):
        if _stem_present(text_lower, SCRIPT_ACTION_STEMS):
            return False
    if not _stem_present(evidence_lower, SCRIPT_ZONE_STEMS):
        if _stem_present(text_lower, SCRIPT_ZONE_STEMS):
            return False
    for marker in SCRIPT_CAUSAL_MARKERS:
        if marker in text_lower and marker not in evidence_lower:
            return False
    for marker in SCRIPT_INTENT_MARKERS:
        if marker in text_lower and marker not in evidence_lower:
            return False
    if any(claim.get("type") == "event_echo" for claim in cited_claims):
        if not any(m in text_lower for m in SCRIPT_UNCERTAINTY_MARKERS):
            return False
        # Word boundaries matter: "unconfirmed" must not trip "confirmed".
        if any(
            re.search(rf"\b{re.escape(marker)}\b", text_lower)
            for marker in SCRIPT_CERTAINTY_MARKERS
        ):
            return False
    return True


def parse_script_segments(text: str, claims_by_id: dict) -> list:
    """Parse model JSON into [(source_claim_ids, text)].

    Raises ObservationError on malformed JSON, unknown or duplicate claim
    ids, or empty wordings. Omissions (claims the model skipped) are fine
    and simply absent from the result.
    """
    data = parse_observation(text)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ObservationError(
            "Script output must be a JSON object with a 'segments' list."
        )
    _reject_extra(data, ("segments",), "Script output")
    parsed = []
    seen: set = set()
    for index, entry in enumerate(data["segments"]):
        where = f"script segments[{index}]"
        if not isinstance(entry, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(entry, ("source_claim_ids", "text"), where)
        ids = entry.get("source_claim_ids")
        wording = entry.get("text")
        if (
            not isinstance(ids, list) or not ids
            or any(not isinstance(i, str) or not i.strip() for i in ids)
        ):
            raise ObservationError(
                f"{where} requires 'source_claim_ids' as a non-empty list."
            )
        if not isinstance(wording, str) or not wording.strip():
            raise ObservationError(f"{where} requires a non-empty 'text'.")
        for claim_id in ids:
            if claim_id not in claims_by_id:
                raise ObservationError(
                    f"{where} cites unknown claim '{claim_id}'."
                )
            if claim_id in seen:
                raise ObservationError(
                    f"{where} duplicates claim '{claim_id}'."
                )
        for claim_id in ids:
            seen.add(claim_id)
        parsed.append((list(ids), wording))
    return parsed


def validate_script(data: dict, claims_by_id: dict | None = None) -> dict:
    """Strictly validate a script-v1 document; reject anything else.

    Shape is always checked. When ``claims_by_id`` (gated claims keyed by
    stable claim id) is supplied, provenance is checked too: every cited
    claim id must be known and used at most once, timestamps must equal
    the latest cited evidence timestamp, segments must be chronological,
    and each text must pass :func:`script_text_valid`. Diagnostics live
    in ``omitted``/``error``, never in speakable ``text``.
    """
    if not isinstance(data, dict):
        raise ObservationError("Script must be a JSON object.")
    _reject_extra(
        data,
        (
            "schema_version",
            "status",
            "generated_from",
            "segments",
            "omitted",
            "error",
        ),
        "Script",
    )
    if data.get("schema_version") != SCRIPT_SCHEMA_VERSION:
        raise ObservationError(
            f"Script requires schema_version '{SCRIPT_SCHEMA_VERSION}'."
        )
    if data.get("status") not in SCRIPT_STATUSES:
        raise ObservationError(
            f"Script has invalid status '{data.get('status')}'."
        )
    generated = data.get("generated_from")
    if not isinstance(generated, dict):
        raise ObservationError("Script requires field 'generated_from' as an object.")
    _reject_extra(
        generated, ("observations", "match_state", "events"), "Script generated_from"
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
            "Script generated_from requires 'observations' as a "
            "non-empty list of filenames."
        )
    _require_non_empty_string(generated, "match_state", "Script generated_from")
    _require_non_empty_string(generated, "events", "Script generated_from")
    if "segments" not in data or not isinstance(data["segments"], list):
        raise ObservationError("Script requires field 'segments' as a list.")
    seen_ids: set = set()
    last_timestamp = None
    for index, segment in enumerate(data["segments"]):
        where = f"segments[{index}]"
        if not isinstance(segment, dict):
            raise ObservationError(f"{where} must be an object.")
        _reject_extra(
            segment,
            ("segment_id", "timestamp_seconds", "text", "source_claim_ids"),
            where,
        )
        _require_non_empty_string(segment, "segment_id", where)
        if segment["segment_id"] in seen_ids:
            raise ObservationError(f"{where} duplicates segment id.")
        seen_ids.add(segment["segment_id"])
        timestamp = segment.get("timestamp_seconds")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or (isinstance(timestamp, float) and not math.isfinite(timestamp))
        ):
            raise ObservationError(f"{where} field 'timestamp_seconds' must be a number.")
        if last_timestamp is not None and timestamp < last_timestamp:
            raise ObservationError(f"{where} breaks chronological ordering.")
        last_timestamp = timestamp
        _require_non_empty_string(segment, "text", where)
        stripped = segment["text"].strip()
        if not (SCRIPT_MIN_TEXT_CHARS <= len(stripped) <= SCRIPT_MAX_TEXT_CHARS):
            raise ObservationError(f"{where} text has an invalid length.")
        if "```" in segment["text"] or "claim_id" in segment["text"]:
            raise ObservationError(f"{where} text is not TTS-ready.")
        ids = segment.get("source_claim_ids")
        if (
            not isinstance(ids, list) or not ids
            or any(not isinstance(i, str) or not i.strip() for i in ids)
        ):
            raise ObservationError(
                f"{where} requires 'source_claim_ids' as a non-empty list."
            )
    if "omitted" not in data or not isinstance(data["omitted"], list):
        raise ObservationError("Script requires field 'omitted' as a list.")
    for index, entry in enumerate(data["omitted"]):
        entry_where = f"omitted[{index}]"
        if not isinstance(entry, dict):
            raise ObservationError(f"{entry_where} must be an object.")
        _reject_extra(entry, ("rule", "subject", "reason"), entry_where)
        _require_non_empty_string(entry, "rule", entry_where)
        _require_non_empty_string(entry, "subject", entry_where)
        _require_non_empty_string(entry, "reason", entry_where)
    error = data.get("error")
    if error is not None and (not isinstance(error, str) or not error.strip()):
        raise ObservationError("Script field 'error' must be a string or null.")
    status = data.get("status")
    if status == "success" and not data["segments"]:
        raise ObservationError("Script status 'success' requires segments.")
    if status == "silence" and data["segments"]:
        raise ObservationError("Script status 'silence' requires no segments.")
    if status == "silence" and error is not None:
        raise ObservationError("Script status 'silence' requires a null error.")
    if status == "success" and error is not None:
        raise ObservationError("Script status 'success' requires a null error.")
    if status == "error" and error is None:
        raise ObservationError("Script status 'error' requires an error message.")
    if status == "error" and data["segments"]:
        raise ObservationError("Script status 'error' requires no segments.")
    if claims_by_id is not None:
        used: set = set()
        for index, segment in enumerate(data["segments"]):
            where = f"segments[{index}]"
            cited = []
            for claim_id in segment["source_claim_ids"]:
                if claim_id not in claims_by_id:
                    raise ObservationError(
                        f"{where} cites unknown claim '{claim_id}'."
                    )
                if claim_id in used:
                    raise ObservationError(
                        f"{where} duplicates claim '{claim_id}'."
                    )
                used.add(claim_id)
                cited.append(claims_by_id[claim_id])
            expected = max(claim["timestamp"] for claim in cited)
            if segment["timestamp_seconds"] != expected:
                raise ObservationError(
                    f"{where} timestamp does not match its cited evidence."
                )
            if not script_text_valid(segment["text"], cited):
                raise ObservationError(
                    f"{where} text is not grounded in its cited evidence."
                )
    return data


def load_prior_script(script_path: Path) -> tuple:
    """Read the previous script file; return (claim_ids, texts).

    A missing file means a first run; a malformed one is reported with a
    warning and treated as a first run. Claim ids feed repeat suppression;
    texts feed writer continuity.
    """
    if not script_path.is_file():
        return set(), []
    try:
        prior = validate_script(
            json.loads(script_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        print(
            f"Warning: ignoring malformed prior script {script_path}: "
            f"{exc}. Proceeding as a first run.",
            file=sys.stderr,
        )
        return set(), []
    claim_ids = set()
    for segment in prior["segments"]:
        claim_ids.update(segment["source_claim_ids"])
    claim_ids.update(
        entry["subject"]
        for entry in prior["omitted"]
        if entry["rule"] == "already-emitted"
    )
    return claim_ids, [segment["text"] for segment in prior["segments"]]


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


def main(video_path: Path | None = None, frames_dir: Path | None = None):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--vision-test",
        action="store_true",
        help="Request a validated structured-JSON observation of "
        "output/frames/frame_00000.jpg from Baseten's vision model.",
    )
    mode.add_argument(
        "--vision-batch",
        action="store_true",
        help="Analyse up to --limit frames spread across the clip, saving "
        "one validated observation-v1 JSON per frame in output/observations/.",
    )
    mode.add_argument(
        "--migrate-observations",
        action="store_true",
        help="Move validated legacy observation JSONs from output/frames/ "
        "to output/observations/ (verified, idempotent, offline).",
    )
    mode.add_argument(
        "--rebuild-state",
        action="store_true",
        help="Rebuild output/observations/match_state.json from the stored "
        "observation-v1 JSON files (offline, no API calls).",
    )
    mode.add_argument(
        "--detect-events",
        action="store_true",
        help="Detect evidence-grounded events from stored observations and "
        "match_state.json into output/events/events.json (offline).",
    )
    mode.add_argument(
        "--generate-commentary",
        action="store_true",
        help="Generate evidence-grounded commentary lines from stored "
        "observations, match_state.json, and events.json into "
        "output/commentary/commentary.json (offline).",
    )
    mode.add_argument(
        "--commentary-write",
        action="store_true",
        help="Word gated commentary claims with one text-only model call "
        "(paid, opt-in), validating every line and falling back to "
        "deterministic wording on any failure. Writes "
        "output/commentary/commentary.json like --generate-commentary.",
    )
    mode.add_argument(
        "--generate-script",
        action="store_true",
        help="Generate the TTS-ready LLM casting script from stored "
        "observations, match_state.json, and events.json into "
        "output/script/script.json (one bounded text-only model call; "
        "no template fallback).",
    )
    parser.add_argument(
        "--limit",
        type=parse_limit_argument,
        default=None,
        metavar="N",
        help="Maximum frames for --vision-batch (positive integer, "
        f"default {DEFAULT_VISION_BATCH_LIMIT}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyse frames that already have a validated observation JSON.",
    )
    parser.add_argument(
        "--jobs",
        type=parse_jobs_argument,
        default=None,
        metavar="J",
        help="Maximum concurrent vision requests for --vision-batch "
        f"(integer 1-{MAX_VISION_BATCH_JOBS}, default {DEFAULT_VISION_BATCH_JOBS}).",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=None,
        metavar="PATH",
        help="Gameplay video file for default frame extraction "
        f"(default {VIDEO_PATH}). Only applies to the default mode.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Base output directory holding frames/, observations/, "
        "events/, commentary/, and script/ "
        f"(default {PROJECT_ROOT / 'output'}).",
    )
    args = parser.parse_args()

    if args.limit is not None and not args.vision_batch:
        parser.error("--limit requires --vision-batch.")
    if args.force and not args.vision_batch:
        parser.error("--force requires --vision-batch.")
    if args.jobs is not None and not args.vision_batch:
        parser.error("--jobs requires --vision-batch.")
    offline_mode = (
        args.vision_test
        or args.vision_batch
        or args.migrate_observations
        or args.rebuild_state
        or args.detect_events
        or args.generate_commentary
        or args.commentary_write
        or args.generate_script
    )
    if args.video is not None and offline_mode:
        parser.error("--video only applies to default frame extraction.")

    # Resolve the output tree. None means "use the project-root defaults";
    # the runners below treat None as their standard locations.
    frames_dir = obs_dir = state_path = None
    events_path = commentary_path = script_path = None
    if args.output_dir is not None:
        base = Path(args.output_dir)
        frames_dir = base / "frames"
        obs_dir = base / "observations"
        state_path = obs_dir / MATCH_STATE_FILENAME
        events_path = base / "events" / EVENTS_FILENAME
        commentary_path = base / "commentary" / COMMENTARY_FILENAME
        script_path = base / "script" / SCRIPT_FILENAME

    if args.vision_test:
        run_vision_test(
            frame_path=frames_dir / "frame_00000.jpg"
            if frames_dir is not None
            else None,
            observations_dir=obs_dir,
        )
    elif args.vision_batch:
        raise SystemExit(
            run_vision_batch(
                limit=args.limit or DEFAULT_VISION_BATCH_LIMIT,
                force=args.force,
                jobs=args.jobs
                if args.jobs is not None
                else DEFAULT_VISION_BATCH_JOBS,
                directory=frames_dir,
                observations_dir=obs_dir,
            )
        )
    elif args.migrate_observations:
        raise SystemExit(
            run_migration(
                frames_dir=frames_dir, observations_dir=obs_dir
            )
        )
    elif args.rebuild_state:
        raise SystemExit(
            run_state_rebuild(
                observations_dir=obs_dir,
                state_path=state_path,
                frames_dir=frames_dir,
            )
        )
    elif args.detect_events:
        raise SystemExit(
            run_detect_events(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                frames_dir=frames_dir,
            )
        )
    elif args.generate_commentary:
        raise SystemExit(
            run_generate_commentary(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                frames_dir=frames_dir,
            )
        )
    elif args.commentary_write:
        raise SystemExit(
            run_generate_commentary(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                frames_dir=frames_dir,
                writer="llm",
            )
        )
    elif args.generate_script:
        raise SystemExit(
            run_generate_script(
                observations_dir=obs_dir,
                state_path=state_path,
                events_path=events_path,
                commentary_path=commentary_path,
                script_path=script_path,
                frames_dir=frames_dir,
            )
        )
    else:
        main(video_path=args.video, frames_dir=frames_dir)
