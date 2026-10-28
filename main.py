import argparse
import base64
import json
import math
import mimetypes
import os
import re
import sys
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


class ObservationError(ValueError):
    """Model output failed observation-v1 parsing or validation."""


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


def run_vision_test():
    api_key = os.environ.get("BASETEN_API_KEY")
    if not api_key:
        raise RuntimeError("BASETEN_API_KEY is not set in the container environment.")

    if not VISION_FRAME_PATH.is_file():
        raise FileNotFoundError(f"Frame not found: {VISION_FRAME_PATH}")

    image_bytes = VISION_FRAME_PATH.read_bytes()
    if not image_bytes:
        raise ValueError(f"Frame is empty: {VISION_FRAME_PATH}")

    image = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    if image is None or image.size == 0:
        raise ValueError(f"Frame is not a valid image: {VISION_FRAME_PATH}")

    mime_type, _ = mimetypes.guess_type(VISION_FRAME_PATH.name)
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(f"Could not determine image MIME type: {VISION_FRAME_PATH}")

    height, width = image.shape[:2]
    print(f"Validated frame: {VISION_FRAME_PATH} ({width}x{height}, {mime_type})")
    image_data_url = (
        f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    )

    client = OpenAI(api_key=api_key, base_url=BASETEN_BASE_URL)
    try:
        raw_response = client.chat.completions.with_raw_response.create(
            model=BASETEN_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": build_vision_prompt(vision_frame_timestamp()),
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
        detail = detail.replace(api_key, "[REDACTED]")
        print(f"Baseten API {status}: {detail}", file=sys.stderr)
        raise SystemExit(1) from None

    text = response.choices[0].message.content
    try:
        validated = validate_observation(parse_observation(text))
    except ObservationError as exc:
        print(f"Invalid observation: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    rendered = json.dumps(validated, indent=2, ensure_ascii=False)
    print(rendered)
    OBSERVATION_OUTPUT_PATH.write_text(rendered + "\n", encoding="utf-8")
    print(f"Saved validated observation: {OBSERVATION_OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vision-test",
        action="store_true",
        help="Request a validated structured-JSON observation of "
        "output/frames/frame_00000.jpg from Baseten's vision model.",
    )
    args = parser.parse_args()

    if args.vision_test:
        run_vision_test()
    else:
        main()
