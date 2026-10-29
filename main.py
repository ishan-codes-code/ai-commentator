import argparse
import base64
import json
import math
import mimetypes
import os
import re
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from openai import APIError, OpenAI

VIDEO_PATH = Path("gameplay/match.mp4")
OUTPUT_DIR = Path("output/frames")
VISION_FRAME_PATH = OUTPUT_DIR / "frame_00000.jpg"
INTERVAL = 3
BASETEN_BASE_URL = "https://inference.baseten.co/v1"
BASETEN_MODEL = "meta-models/muse-spark-open-1026"
OBSERVATION_SCHEMA_VERSION = "observation-v1"
OBSERVATION_CATEGORIES = ("environment", "character", "hud", "combat", "other")
OBSERVATION_CONFIDENCES = ("high", "medium", "low")
OBSERVATION_OUTPUT_PATH = VISION_FRAME_PATH.with_suffix(".json")
FRAME_FILENAME_RE = re.compile(r"^frame_(\d+)\.jpg$")
DEFAULT_VISION_BATCH_LIMIT = 5


class ObservationError(ValueError):
    """Model output failed observation-v1 parsing or validation."""


class VisionAPIError(RuntimeError):
    """Baseten vision request failed; the message is already redacted."""


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


def write_observation_json(destination: Path, validated: dict) -> None:
    """Atomically write validated observation JSON (UTF-8, indented).

    The document is written to a temporary file in the destination directory
    and moved over the target only after the write succeeds, so a failure
    never leaves a partially written JSON file behind.
    """
    rendered = json.dumps(validated, indent=2, ensure_ascii=False) + "\n"
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
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


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


def main():
    if not VIDEO_PATH.is_file():
        raise FileNotFoundError(f"Video not found: {VIDEO_PATH.resolve()}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    video = cv2.VideoCapture(str(VIDEO_PATH))

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

            output_path = OUTPUT_DIR / f"frame_{timestamp:05d}.jpg"

            if not cv2.imwrite(str(output_path), frame):
                raise RuntimeError(f"Could not save {output_path}")

            output_path.chmod(0o644)
            print(f"Saved: {output_path}")
            saved_count += 1
            timestamp += INTERVAL

        print(f"Finished: {saved_count} frames saved.")
    finally:
        video.release()


def run_vision_test(client=None, frame_path: Path | None = None):
    """Analyse the first extracted frame (frame_00000.jpg) via Baseten.

    ``client`` and ``frame_path`` default to the live Baseten client and
    :data:`VISION_FRAME_PATH`; they exist so tests can inject a fake client
    and a fixture frame without network access.
    """
    frame = VISION_FRAME_PATH if frame_path is None else Path(frame_path)
    timestamp = parse_frame_timestamp(frame.name)
    if timestamp is None:
        raise RuntimeError(f"Cannot derive timestamp from frame name: {frame.name}")
    if client is None:
        api_key = os.environ.get("BASETEN_API_KEY")
        if not api_key:
            raise RuntimeError("BASETEN_API_KEY is not set in the container environment.")
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
    destination = frame.with_suffix(".json")
    write_observation_json(destination, validated)
    print(f"Saved validated observation: {destination}")


def run_vision_batch(
    limit: int = DEFAULT_VISION_BATCH_LIMIT,
    force: bool = False,
    client=None,
    directory: Path | None = None,
) -> int:
    """Analyse up to ``limit`` frames spread across the clip; return exit code.

    Each successfully analysed frame gets one validated observation-v1 JSON
    next to its JPEG. Frames that already have a validated observation are
    skipped unless ``force`` is set. Returns 0 only when every selected frame
    ends with a validated observation; any failure (or zero candidates)
    returns 1 after reporting the completed results.
    """
    frames_dir = OUTPUT_DIR if directory is None else Path(directory)
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
    planned = []
    for timestamp, frame_path in selected:
        destination = frame_path.with_suffix(".json")
        cached = not force and (
            read_validated_observation(destination, timestamp) is not None
        )
        planned.append((timestamp, frame_path, destination, cached))
    if client is None:
        if any(not cached for _, _, _, cached in planned):
            api_key = os.environ.get("BASETEN_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "BASETEN_API_KEY is not set in the container environment."
                )
            client = OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL)
        else:
            api_key = None
    else:
        api_key = None

    succeeded: list[Path] = []
    skipped: list[Path] = []
    failed: list[tuple[Path, str]] = []
    total = len(planned)
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
        print(f"[{index}/{total}] Analysing {frame_path} ...")
        try:
            validated = analyze_frame(client, frame_path, timestamp, api_key=api_key)
        except (FileNotFoundError, ValueError, VisionAPIError) as exc:
            print(f"Error [{frame_path.name}]: {exc}", file=sys.stderr)
            failed.append((frame_path, str(exc)))
            continue
        try:
            write_observation_json(destination, validated)
        except OSError as exc:
            print(
                f"Error [{frame_path.name}]: could not write {destination}: {exc}",
                file=sys.stderr,
            )
            failed.append((frame_path, str(exc)))
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
        "one validated observation-v1 JSON per frame next to its JPEG.",
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
    args = parser.parse_args()

    if args.limit is not None and not args.vision_batch:
        parser.error("--limit requires --vision-batch.")
    if args.force and not args.vision_batch:
        parser.error("--force requires --vision-batch.")

    if args.vision_test:
        run_vision_test()
    elif args.vision_batch:
        raise SystemExit(
            run_vision_batch(
                limit=args.limit or DEFAULT_VISION_BATCH_LIMIT,
                force=args.force,
            )
        )
    else:
        main()
