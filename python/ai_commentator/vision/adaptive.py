"""Adaptive vision orchestration: the M4.1 sequential feedback loop.

One sampled frame costs at most one vision request: capture the frame at
the scheduled timestamp, send it to the real Baseten vision model, strictly
validate the wrapper response, atomically persist the validated
``observation-v1`` document, and schedule the next timestamp from the
validated speed label. The loop ends at the video duration, at the
``max_frames`` bound, on the first failure, or on a cooperative stop.

Cooperative stop: a thread-safe :class:`StopToken` is checked before every
capture and before every paid request, and again after each persisted
observation. A stop requested during an in-flight request never cancels
that request: the response still completes, validates, and persists, and
the run then halts with terminal status ``stopped`` (never ``completed``).
"""

import contextlib
import os
import sys
import threading

from openai import OpenAI

from ..config import (
    ADAPTIVE_DEFAULT_MAX_FRAMES,
    ADAPTIVE_HARD_MAX_FRAMES,
    ADAPTIVE_REQUEST_TIMEOUT_S,
    ADAPTIVE_RUN_MANIFEST_FILENAME,
    ADAPTIVE_RUN_SCHEMA_VERSION,
    BASETEN_BASE_URL,
    OBSERVATIONS_DIR,
    VIDEO_PATH,
)
from ..errors import ObservationError, VisionAPIError
from ..storage import write_json_atomic
from .client import analyze_adaptive_frame
from .reader import ScheduledVideoReader
from .sampling import (
    AdaptiveScheduler,
    observation_path_for_timestamp,
    validate_intervals,
)


TERMINATION_VIDEO_END = "video-end"
TERMINATION_MAX_FRAMES = "max-frames"
TERMINATION_STOP_REQUESTED = "stop-requested"
TERMINATION_OPEN_FAILED = "open-failed"
TERMINATION_CAPTURE_FAILED = "capture-failed"
TERMINATION_REQUEST_FAILED = "request-failed"
TERMINATION_RESPONSE_INVALID = "response-invalid"
TERMINATION_WRITE_FAILED = "write-failed"
TERMINATION_MANIFEST_FAILED = "manifest-failed"


class StopToken:
    """Thread-safe cooperative stop flag for the adaptive loop.

    ``request_stop()`` is idempotent: repeated requests are harmless.
    The API call itself stays synchronous; the token is only observed at
    the loop boundaries around it, so an in-flight request always runs to
    completion.
    """

    def __init__(self):
        self._event = threading.Event()

    def request_stop(self):
        """Record a stop request; safe to call repeatedly."""
        self._event.set()

    def is_stopping(self):
        """True once a stop has been requested."""
        return self._event.is_set()


def parse_max_frames_argument(value):
    """Validate an ``--adaptive-max-frames`` value: 1..HARD_MAX int."""
    if isinstance(value, bool):
        raise ValueError(
            "--adaptive-max-frames must be between 1 and "
            f"{ADAPTIVE_HARD_MAX_FRAMES}, got {value!r}."
        )
    if isinstance(value, float):
        # int(2.5) would silently truncate: only whole floats are accepted.
        if not value.is_integer():
            raise ValueError(
                "--adaptive-max-frames must be a positive integer, "
                f"got {value!r}."
            )
        limit = int(value)
    else:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            raise ValueError(
                "--adaptive-max-frames must be a positive integer, "
                f"got {value!r}."
            ) from None
    if limit < 1 or limit > ADAPTIVE_HARD_MAX_FRAMES:
        raise ValueError(
            "--adaptive-max-frames must be between 1 and "
            f"{ADAPTIVE_HARD_MAX_FRAMES}, got {value!r}."
        )
    return limit


def new_run_manifest(video, obs_dir, intervals, max_frames):
    """Build the initial in-memory adaptive run manifest."""
    return {
        "schema_version": ADAPTIVE_RUN_SCHEMA_VERSION,
        "video": str(video),
        "output_observations": str(obs_dir),
        "intervals": dict(intervals),
        "max_frames": max_frames,
        "duration": None,
        "status": "running",
        "termination": None,
        "requested_timestamps": [],
        "iterations": [],
        "observations": 0,
        "requests": 0,
        "failed_requests": 0,
        "speed_counts": {"HIGH": 0, "NORMAL": 0, "LOW": 0},
        "error": None,
    }


def record_iteration(manifest, timestamp, destination, speed, reason, diagnostics):
    """Append one validated iteration to the manifest (in memory)."""
    manifest["requested_timestamps"].append(timestamp)
    manifest["iterations"].append(
        {
            "timestamp": timestamp,
            "observation_file": destination.name,
            "speed": speed,
            "reason": reason,
            "sampling_diagnostics": list(diagnostics),
            "status": "ok" if not diagnostics else "sampling-fallback",
        }
    )
    manifest["observations"] += 1
    manifest["requests"] += 1
    manifest["speed_counts"][speed] += 1


def run_adaptive_vision(
    video_path=None,
    observations_dir=None,
    max_frames=ADAPTIVE_DEFAULT_MAX_FRAMES,
    intervals=None,
    client=None,
    stop_token=None,
    on_iteration=None,
):
    """Run the adaptive sampling loop; return a result dict (never None).

    ``video_path`` defaults to the project gameplay video; observations
    land in ``observations_dir`` as ``frame_<timestamp>.json``. At most
    ``max_frames`` paid requests are made; the first failure terminates the
    run (no retries, no repair calls). ``client`` and ``stop_token`` exist
    for tests and the worker; a live client is created (and closed) only
    when none is injected. ``on_iteration`` is an optional reporting hook
    called once per validated, persisted iteration with a plain dict
    (``timestamp``, ``speed``, ``reason``, ``next_timestamp`` or None at
    video end, ``observations``, ``requests``); it never triggers another
    request and its own exceptions are contained so reporting can never
    break the loop. Result ``status`` is ``completed``, ``stopped``,
    or ``failed`` with a ``termination`` reason and an optional ``error``.
    """
    from pathlib import Path

    video = VIDEO_PATH if video_path is None else Path(video_path)
    obs_dir = OBSERVATIONS_DIR if observations_dir is None else Path(observations_dir)
    limit = parse_max_frames_argument(max_frames)
    validated_intervals = validate_intervals(intervals)
    token = stop_token if stop_token is not None else StopToken()

    manifest_path = obs_dir / ADAPTIVE_RUN_MANIFEST_FILENAME
    manifest = new_run_manifest(video, obs_dir, validated_intervals, limit)

    def save_manifest():
        write_json_atomic(manifest_path, manifest)

    def finish(status, termination, error=None):
        manifest["status"] = status
        manifest["termination"] = termination
        manifest["error"] = error
        try:
            save_manifest()
        except OSError as exc:
            print(
                f"Warning: could not write adaptive manifest: {exc}",
                file=sys.stderr,
            )
        return {
            "status": status,
            "termination": termination,
            "video": str(video),
            "observations_dir": str(obs_dir),
            "duration": manifest["duration"],
            "requested_timestamps": list(manifest["requested_timestamps"]),
            "observations": manifest["observations"],
            "requests": manifest["requests"],
            "failed_requests": manifest["failed_requests"],
            "speed_counts": dict(manifest["speed_counts"]),
            "manifest": str(manifest_path),
            "error": error,
        }

    def fail(termination, message):
        error = {"phase": termination, "message": message}
        print(f"Adaptive run failed ({termination}): {message}", file=sys.stderr)
        return finish("failed", termination, error)

    if client is None:
        api_key = os.environ.get("BASETEN_API_KEY")
        if not api_key:
            raise RuntimeError("BASETEN_API_KEY is not set in the environment.")
        owned_client = OpenAI(
            api_key=api_key,
            base_url=BASETEN_BASE_URL,
            timeout=ADAPTIVE_REQUEST_TIMEOUT_S,
        )
        vision_client = owned_client
    else:
        api_key = None
        owned_client = None
        vision_client = client

    try:
        obs_dir.mkdir(parents=True, exist_ok=True)
        try:
            reader = ScheduledVideoReader(video)
            reader.__enter__()
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            return fail(TERMINATION_OPEN_FAILED, str(exc))
        try:
            manifest["duration"] = reader.duration
            scheduler = AdaptiveScheduler(validated_intervals, reader.duration)
            try:
                save_manifest()
            except OSError as exc:
                return fail(TERMINATION_MANIFEST_FAILED, str(exc))
            print(
                f"Adaptive sampling {video.name}: duration "
                f"{reader.duration:.2f}s, intervals "
                + ", ".join(
                    f"{speed}={validated_intervals[speed]}s"
                    for speed in ("HIGH", "NORMAL", "LOW")
                )
                + f", max {limit} frames."
            )
            timestamp = scheduler.first_timestamp()
            previous = None
            while True:
                if token.is_stopping():
                    print("Adaptive run stopping before next capture.")
                    return finish("stopped", TERMINATION_STOP_REQUESTED)
                try:
                    frame = reader.capture_at(timestamp)
                except (ValueError, RuntimeError) as exc:
                    return fail(TERMINATION_CAPTURE_FAILED, str(exc))
                if token.is_stopping():
                    print("Adaptive run stopping before vision request.")
                    return finish("stopped", TERMINATION_STOP_REQUESTED)
                try:
                    validated, speed, reason, sampling_diagnostics = (
                        analyze_adaptive_frame(
                            vision_client,
                            frame,
                            timestamp,
                            previous,
                            api_key=api_key,
                        )
                    )
                except VisionAPIError as exc:
                    manifest["requests"] += 1
                    manifest["failed_requests"] += 1
                    if token.is_stopping():
                        print(
                            "Adaptive run stopping after failed request; "
                            "recording the failure."
                        )
                        return finish(
                            "stopped",
                            TERMINATION_STOP_REQUESTED,
                            {"phase": TERMINATION_REQUEST_FAILED, "message": str(exc)},
                        )
                    return fail(TERMINATION_REQUEST_FAILED, str(exc))
                except ObservationError as exc:
                    manifest["requests"] += 1
                    manifest["failed_requests"] += 1
                    if token.is_stopping():
                        return finish(
                            "stopped",
                            TERMINATION_STOP_REQUESTED,
                            {"phase": TERMINATION_RESPONSE_INVALID, "message": str(exc)},
                        )
                    return fail(TERMINATION_RESPONSE_INVALID, str(exc))
                except ValueError as exc:
                    # Local frame-encoding failure: no request was sent.
                    return fail(TERMINATION_CAPTURE_FAILED, str(exc))
                destination = observation_path_for_timestamp(timestamp, obs_dir)
                try:
                    write_json_atomic(destination, validated)
                except OSError as exc:
                    # The request itself succeeded; persistence failed.
                    manifest["requests"] += 1
                    return fail(
                        TERMINATION_WRITE_FAILED,
                        f"could not write {destination}: {exc}",
                    )
                record_iteration(
                    manifest,
                    timestamp,
                    destination,
                    speed,
                    reason,
                    sampling_diagnostics,
                )
                previous = validated
                try:
                    save_manifest()
                except OSError as exc:
                    return fail(TERMINATION_MANIFEST_FAILED, str(exc))
                if sampling_diagnostics:
                    print(
                        f"[{manifest['observations']}/{limit}] Sampled "
                        f"{timestamp}s -> {speed} "
                        f"({'; '.join(sampling_diagnostics)})."
                    )
                else:
                    print(
                        f"[{manifest['observations']}/{limit}] Sampled "
                        f"{timestamp}s -> {speed}."
                    )
                next_timestamp = scheduler.next_timestamp(timestamp, speed)
                if on_iteration is not None:
                    try:
                        on_iteration(
                            {
                                "timestamp": timestamp,
                                "speed": speed,
                                "reason": reason,
                                "next_timestamp": next_timestamp,
                                "observations": manifest["observations"],
                                "requests": manifest["requests"],
                            }
                        )
                    except Exception as exc:
                        print(
                            "Adaptive iteration hook failed; "
                            f"continuing without it: {exc}",
                            file=sys.stderr,
                        )
                if token.is_stopping():
                    print("Adaptive run stopping after persisted observation.")
                    return finish("stopped", TERMINATION_STOP_REQUESTED)
                if next_timestamp is None:
                    print(
                        "Adaptive run finished: reached video end "
                        f"({reader.duration:.2f}s)."
                    )
                    return finish("completed", TERMINATION_VIDEO_END)
                if len(manifest["requested_timestamps"]) >= limit:
                    print(
                        f"Adaptive run finished: sampled {limit} frame(s) "
                        "(max-frames bound)."
                    )
                    return finish("completed", TERMINATION_MAX_FRAMES)
                timestamp = next_timestamp
        finally:
            reader.close()
    finally:
        if owned_client is not None:
            with contextlib.suppress(Exception):
                owned_client.close()
