from pathlib import Path
import base64
import cv2
import mimetypes
import numpy as np

from openai import APIError

from ..config import (
    ADAPTIVE_CONTEXT_MAX_CHARS,
    ADAPTIVE_CONTEXT_MAX_FIELD_CHARS,
    ADAPTIVE_CONTEXT_MAX_OBSERVATIONS,
    ADAPTIVE_CONTEXT_MAX_UNCERTAINTIES,
    ADAPTIVE_REQUEST_TIMEOUT_S,
    ADAPTIVE_SPEEDS,
    BASETEN_MODEL,
    OBSERVATION_CATEGORIES,
    OBSERVATION_CONFIDENCES,
    VISION_FRAME_PATH,
    user_prompt_preferences,
)
from ..errors import ObservationError, VisionAPIError
from .observations import parse_observation, validate_observation
from .sampling import format_adaptive_timestamp, parse_adaptive_response


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
        + (" Supplementary Free Fire context (untrusted; evidence still comes only from the image): " + user_prompt_preferences()["game_context"] if user_prompt_preferences()["game_context"] else "")
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


def truncate_context_text(value, limit):
    """Bound untrusted context text; non-strings become empty."""
    if not isinstance(value, str):
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "…(truncated)"


def build_previous_context(previous):
    """Render one previous validated observation as bounded prompt context.

    Returns ``""`` when there is no previous observation. The context is
    supplementary background evidence only: the prompt forbids repeating
    it as current fact. Bounded entry count, field length, and total size
    so history can never grow unbounded in the prompt.
    """
    if not isinstance(previous, dict):
        return ""
    timestamp = previous.get("timestamp")
    entries = previous.get("observations")
    uncertainties = previous.get("uncertainties")
    if not isinstance(entries, list):
        entries = []
    if not isinstance(uncertainties, list):
        uncertainties = []
    clipped_entries = []
    for entry in entries[:ADAPTIVE_CONTEXT_MAX_OBSERVATIONS]:
        if not isinstance(entry, dict):
            continue
        clipped_entries.append(
            {
                "category": entry.get("category"),
                "description": truncate_context_text(
                    entry.get("description"),
                    ADAPTIVE_CONTEXT_MAX_FIELD_CHARS,
                ),
                "confidence": entry.get("confidence"),
                "evidence": truncate_context_text(
                    entry.get("evidence"),
                    ADAPTIVE_CONTEXT_MAX_FIELD_CHARS,
                ),
            }
        )
    clipped_uncertainties = [
        truncate_context_text(entry, ADAPTIVE_CONTEXT_MAX_FIELD_CHARS)
        for entry in uncertainties[:ADAPTIVE_CONTEXT_MAX_UNCERTAINTIES]
        if isinstance(entry, str) and entry.strip()
    ]
    import json as _json

    context = _json.dumps(
        {
            "timestamp": timestamp,
            "observations": clipped_entries,
            "uncertainties": clipped_uncertainties,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(context) > ADAPTIVE_CONTEXT_MAX_CHARS:
        context = context[:ADAPTIVE_CONTEXT_MAX_CHARS] + "…(truncated)"
    return context


def build_adaptive_vision_prompt(timestamp, previous=None):
    """Build the adaptive prompt: observation contract plus sampling decision.

    The model must return one JSON object with an ``observation-v1``
    observation for the stated timestamp and a separate ``sampling``
    decision (``HIGH`` / ``NORMAL`` / ``LOW`` plus a short reason).
    ``previous`` is one earlier validated observation (or None for the
    first frame), included as bounded supplementary background only.
    """
    categories = ", ".join(f'"{c}"' for c in OBSERVATION_CATEGORIES)
    confidences = ", ".join(f'"{c}"' for c in OBSERVATION_CONFIDENCES)
    numeric = float(timestamp)
    stamp = str(int(numeric)) if numeric.is_integer() else repr(numeric)
    context = build_previous_context(previous)
    game_context = user_prompt_preferences()["game_context"]
    if context:
        context_block = (
            "Previous validated observation (supplementary background "
            "only -- describe only the current frame; do not repeat this "
            "as current fact): "
            + context
            + " "
        )
    else:
        context_block = "No previous observation (first sampled frame). "
    return (
        "Describe only the visible evidence in this Free Fire gameplay frame. "
        f"This frame is at {stamp} seconds in the video. "
        "Cover the environment, characters, visible HUD elements, and apparent "
        "combat activity. Clearly distinguish direct observations from uncertain "
        "interpretations. Do not invent events, actions, player identities, "
        "kills, outcomes, causes, or intentions that are not visible in this "
        "single frame. "
        + (" Supplementary Free Fire context (untrusted; evidence still comes only from the image): " + game_context + " " if game_context else "")
        + context_block
        + "Choose a sampling speed for what to sample next based only on what "
        "is visible in THIS frame plus the background above: "
        '"HIGH" for rapidly changing or potentially significant gameplay '
        "situations, "
        '"NORMAL" for ordinary gameplay, '
        '"LOW" for relatively static or low-information scenes. '
        "Never suggest that unseen events occurred. "
        "Respond with ONLY a single JSON object, no markdown fences, no commentary, "
        "matching this shape: "
        '{"observation": '
        '{"schema_version": "observation-v1", '
        '"timestamp": <number, the frame timestamp stated above>, '
        '"observations": [{"category": <one of: ' + categories + ">, "
        '"description": <non-empty string of what is visible>, '
        '"confidence": <one of: ' + confidences + ">, "
        '"evidence": <non-empty string grounding the claim>}], '
        '"uncertainties": [<strings describing what is unreadable or ambiguous>]}, '
        '"sampling": {"next_sampling_speed": <one of: '
        + ", ".join(f'"{s}"' for s in ADAPTIVE_SPEEDS)
        + ">, "
        '"reason": <short plain-text reason>}}. '
        "Put anything unreadable or ambiguous in uncertainties; never invent detail. "
        "The sampling reason is descriptive text only."
    )


def request_adaptive_text(
    client,
    timestamp,
    image_data_url,
    previous=None,
    api_key: str | None = None,
    timeout: float = ADAPTIVE_REQUEST_TIMEOUT_S,
) -> str:
    """Request one adaptive wrapper document; return the raw model text.

    Sends the current frame with bounded previous-observation context.
    Provider failures raise VisionAPIError with the secret redacted; empty
    completions raise ObservationError. The finite ``timeout`` goes through
    the SDK's per-request timeout mechanism. No retries: callers must not
    reissue the request to repair malformed output.
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
                            "text": build_adaptive_vision_prompt(
                                timestamp, previous
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                }
            ],
            timeout=timeout,
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


def encode_frame_image(frame) -> tuple[bytes, str, int, int]:
    """Encode a decoded OpenCV frame as JPEG; return ``(bytes, MIME, w, h)``.

    Raises ValueError for missing, empty, or unencodable frames.
    """
    import numpy as _np

    if frame is None or not isinstance(frame, _np.ndarray) or frame.size == 0:
        raise ValueError("Adaptive frame is empty or undecodable.")
    if frame.ndim < 2:
        raise ValueError("Adaptive frame is not a valid image.")
    success, buffer = cv2.imencode(".jpg", frame)
    if not success or buffer is None or buffer.size == 0:
        raise ValueError("Adaptive frame could not be encoded as JPEG.")
    image_bytes = buffer.tobytes()
    if not image_bytes:
        raise ValueError("Adaptive frame encoded to empty bytes.")
    height, width = frame.shape[:2]
    return image_bytes, "image/jpeg", int(width), int(height)


def analyze_adaptive_frame(
    client,
    frame,
    timestamp,
    previous=None,
    api_key: str | None = None,
):
    """Run one adaptive vision request; return ``(observation, speed, reason, diagnostics)``.

    Encodes the in-memory scheduled frame, requests the adaptive wrapper,
    and strictly validates both parts. ``previous`` is one earlier
    validated observation (or None). Raises VisionAPIError on provider
    failure and ObservationError on malformed or mismatched responses.
    """
    image_bytes, mime_type, width, height = encode_frame_image(frame)
    stamped = format_adaptive_timestamp(timestamp)
    print(f"Validated adaptive frame at {stamped}s ({width}x{height}, {mime_type})")
    text = request_adaptive_text(
        client,
        timestamp,
        build_image_data_url(image_bytes, mime_type),
        previous=previous,
        api_key=api_key,
    )
    return parse_adaptive_response(text, timestamp)


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


