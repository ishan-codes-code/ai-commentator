"""Adaptive sampling decisions: validation, scheduling, response parsing.

M4.1 feedback loop: one captured frame -> one vision request -> one speed
label -> next scheduled timestamp. The model chooses only a speed label
(``HIGH`` / ``NORMAL`` / ``LOW``); this module maps the label to a
configured interval and computes the next timestamp deterministically.

Invalid adaptive output never controls the scheduler: a valid observation
with a missing or invalid sampling decision falls back to ``NORMAL`` with a
diagnostic, while a malformed response or an invalid observation is
rejected outright (no observation is written).
"""

import math

from ..config import (
    ADAPTIVE_DEFAULT_SPEED,
    ADAPTIVE_INTERVALS,
    ADAPTIVE_MAX_INTERVAL,
    ADAPTIVE_MAX_REASON_CHARS,
    ADAPTIVE_MIN_INTERVAL,
    ADAPTIVE_SPEEDS,
    ADAPTIVE_TIMESTAMP_PRECISION,
)
from ..errors import ObservationError
from .observations import parse_observation, validate_observation


SPEEDS = ADAPTIVE_SPEEDS
DEFAULT_SPEED = ADAPTIVE_DEFAULT_SPEED


def validate_intervals(mapping=None):
    """Validate an interval mapping; return normalized ``{speed: seconds}``.

    Requires exactly the ``HIGH`` / ``NORMAL`` / ``LOW`` keys with finite
    numeric values inside ``[ADAPTIVE_MIN_INTERVAL, ADAPTIVE_MAX_INTERVAL]``.
    Rejects missing or extra keys, booleans, non-numbers, NaN/infinity,
    and out-of-range values. ``None`` selects the configured defaults.
    """
    source = ADAPTIVE_INTERVALS if mapping is None else mapping
    if not isinstance(source, dict):
        raise ValueError("Adaptive intervals must be a mapping.")
    keys = set(source)
    expected = set(SPEEDS)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected: {', '.join(extra)}")
        raise ValueError(
            "Adaptive intervals must define exactly "
            f"{', '.join(SPEEDS)} ({'; '.join(parts)})."
        )
    normalized = {}
    for speed in SPEEDS:
        value = source[speed]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"Adaptive interval for {speed} must be a number, "
                f"got {value!r}."
            )
        if not math.isfinite(value):
            raise ValueError(
                f"Adaptive interval for {speed} must be finite, "
                f"got {value!r}."
            )
        interval = float(value)
        if interval < ADAPTIVE_MIN_INTERVAL or interval > ADAPTIVE_MAX_INTERVAL:
            raise ValueError(
                f"Adaptive interval for {speed} must be within "
                f"[{ADAPTIVE_MIN_INTERVAL}, {ADAPTIVE_MAX_INTERVAL}], "
                f"got {value!r}."
            )
        normalized[speed] = interval
    return normalized


def quantize_timestamp(value):
    """Round a scheduled timestamp to millisecond precision."""
    return round(float(value), ADAPTIVE_TIMESTAMP_PRECISION)


def format_adaptive_timestamp(timestamp):
    """Render a scheduled timestamp canonically for prompts and filenames.

    Whole seconds use the existing zero-padded integer form (``00003``);
    fractional seconds use the trimmed-decimal form (``00000.5``).
    """
    quantized = quantize_timestamp(timestamp)
    if quantized.is_integer():
        return f"{int(quantized):05d}"
    text = f"{quantized:.{ADAPTIVE_TIMESTAMP_PRECISION}f}".rstrip("0")
    whole, _, fraction = text.partition(".")
    return f"{int(whole):05d}.{fraction}"


def observation_path_for_timestamp(timestamp, observations_dir):
    """Return the observation JSON path for a scheduled timestamp."""
    from pathlib import Path

    obs_dir = Path(observations_dir)
    return obs_dir / f"frame_{format_adaptive_timestamp(timestamp)}.json"


def validate_sampling(data):
    """Validate a sampling decision; never raise for bad model output.

    Returns ``(speed, reason, diagnostics)``. Missing, invalid, malformed,
    or unexpected values fall back to ``NORMAL`` with a diagnostic entry;
    the reason is bounded descriptive text (truncated when too long, dropped
    when not a string). The speed is only ever one of the permitted labels.
    """
    diagnostics = []
    if not isinstance(data, dict):
        return DEFAULT_SPEED, "", ["sampling-not-object"]
    allowed = {"next_sampling_speed", "reason"}
    extra = sorted(set(data) - allowed)
    if extra:
        # Unexpected sampling content is malformed: it must never steer the
        # scheduler, so the whole decision falls back to NORMAL.
        diagnostics.append(f"sampling-unexpected-fields: {', '.join(extra)}")
        return DEFAULT_SPEED, "", diagnostics
    speed = data.get("next_sampling_speed")
    if speed not in SPEEDS:
        diagnostics.append(f"sampling-invalid-speed: {speed!r}")
        speed = DEFAULT_SPEED
    reason = data.get("reason", "")
    if "reason" not in data:
        pass
    elif not isinstance(reason, str):
        diagnostics.append(f"sampling-invalid-reason-type: {type(reason).__name__}")
        reason = ""
    elif len(reason) > ADAPTIVE_MAX_REASON_CHARS:
        diagnostics.append(
            f"sampling-reason-truncated: {len(reason)}>"
            f"{ADAPTIVE_MAX_REASON_CHARS}"
        )
        reason = reason[:ADAPTIVE_MAX_REASON_CHARS]
    return speed, reason, diagnostics


def parse_adaptive_response(text, expected_timestamp):
    """Parse one adaptive wrapper response.

    Returns ``(observation, speed, reason, sampling_diagnostics)`` where
    ``observation`` passed the unchanged ``observation-v1`` validation and
    its timestamp matches the requested source timestamp. Raises
    ``ObservationError`` when the response is malformed, the observation is
    missing or invalid, extra fields appear, or the timestamp mismatches.
    A missing or invalid ``sampling`` section never raises: it falls back
    to ``NORMAL`` with diagnostics.
    """
    wrapper = parse_observation(text)
    if not isinstance(wrapper, dict):
        raise ObservationError("Adaptive response must be a JSON object.")
    allowed = {"observation", "sampling"}
    extra = sorted(set(wrapper) - allowed)
    if extra:
        raise ObservationError(
            "Adaptive response has unsupported fields: "
            f"{', '.join(extra)}."
        )
    if "observation" not in wrapper:
        raise ObservationError(
            "Adaptive response requires field 'observation'."
        )
    observation = wrapper["observation"]
    if not isinstance(observation, dict):
        raise ObservationError("Adaptive response field 'observation' must be an object.")
    validated = validate_observation(observation)
    actual = validated.get("timestamp")
    if actual != expected_timestamp:
        raise ObservationError(
            f"Model returned timestamp {actual!r}, expected "
            f"{expected_timestamp!r} for this frame."
        )
    if "sampling" not in wrapper:
        return validated, DEFAULT_SPEED, "", ["sampling-missing"]
    speed, reason, diagnostics = validate_sampling(wrapper["sampling"])
    return validated, speed, reason, diagnostics


class AdaptiveScheduler:
    """Map validated speed labels to configured intervals deterministically.

    ``next_timestamp`` returns the next scheduled timestamp, or ``None``
    when the next step reaches or passes the video duration. Never returns
    a repeated or non-increasing timestamp and never schedules an
    out-of-range value.
    """

    def __init__(self, intervals=None, duration=None):
        self.intervals = validate_intervals(intervals)
        if (
            duration is None
            or isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError(
                f"Adaptive scheduler requires a positive finite duration, "
                f"got {duration!r}."
            )
        self.duration = float(duration)
        self.minimum_step = min(self.intervals.values())

    def first_timestamp(self):
        """Sampling always starts at timestamp zero."""
        return 0

    def interval_for(self, speed):
        """Return the configured interval, falling back to NORMAL."""
        if speed not in SPEEDS:
            speed = DEFAULT_SPEED
        return self.intervals[speed]

    def next_timestamp(self, current, speed):
        """Compute the next timestamp after ``current`` for ``speed``.

        Returns ``None`` when the next step reaches or exceeds the video
        duration (clean termination). Guarantees a strictly increasing,
        in-range result otherwise.
        """
        current_value = quantize_timestamp(current)
        step = self.interval_for(speed)
        candidate = quantize_timestamp(current_value + step)
        if candidate <= current_value:
            # Defensive: quantized arithmetic must always make progress.
            candidate = quantize_timestamp(current_value + self.minimum_step)
        if candidate <= current_value:
            raise RuntimeError(
                "Adaptive scheduler cannot advance: "
                f"current={current_value!r}, step={step!r}."
            )
        if candidate >= self.duration:
            return None
        return candidate
