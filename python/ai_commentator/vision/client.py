from pathlib import Path
import base64
import cv2
import mimetypes
import numpy as np

from openai import APIError

from ..config import (
    BASETEN_MODEL,
    OBSERVATION_CATEGORIES,
    OBSERVATION_CONFIDENCES,
    VISION_FRAME_PATH,
)
from ..errors import ObservationError, VisionAPIError
from .observations import parse_observation, validate_observation


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


