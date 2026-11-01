"""Offline tests for adaptive vision sampling (M4.1).

No live API calls: a fake client stands in for the Baseten OpenAI-compatible
endpoint, synthetic MP4s stand in for gameplay video, and OpenCV behavior is
faked only for failure modes. Run natively from the repository root:
    .venv/Scripts/python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import cv2
import numpy as np
from openai import APIError

import worker
from main import (
    ADAPTIVE_DEFAULT_MAX_FRAMES,
    ADAPTIVE_INTERVALS,
    ADAPTIVE_MAX_REASON_CHARS,
    ADAPTIVE_SPEEDS,
    AdaptiveScheduler,
    ObservationError,
    ScheduledVideoReader,
    StopToken,
    analyze_adaptive_frame,
    build_adaptive_vision_prompt,
    build_previous_context,
    encode_frame_image,
    format_adaptive_timestamp,
    observation_path_for_timestamp,
    parse_adaptive_response,
    parse_max_frames_argument,
    parse_observation_timestamp,
    run_adaptive_vision,
    validate_intervals,
    validate_sampling,
)


def write_test_video(path, seconds=7, fps=10):
    path = Path(path)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48)
    )
    if not writer.isOpened():
        raise AssertionError("test video writer did not open (mp4v unavailable?)")
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    for _ in range(seconds * fps):
        writer.write(frame)
    writer.release()
    return path


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeCompletion:
    def __init__(self, choices):
        self.choices = choices


class FakeRawResponse:
    def __init__(self, completion, status_code=200):
        self._completion = completion
        self.status_code = status_code

    def parse(self):
        return self._completion


class FakeCompletions:
    """Mimics client.chat.completions with with_raw_response.create."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    @property
    def with_raw_response(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(kwargs, len(self.calls) - 1)


class FakeClient:
    def __init__(self, handler):
        self.chat = SimpleNamespace(completions=FakeCompletions(handler))

    @property
    def calls(self):
        return self.chat.completions.calls


def observation_doc(timestamp):
    return {
        "schema_version": "observation-v1",
        "timestamp": timestamp,
        "observations": [
            {
                "category": "hud",
                "description": f"Visible HUD elements at {timestamp}s.",
                "confidence": "high",
                "evidence": "Test fixture frame.",
            }
        ],
        "uncertainties": [],
    }


def adaptive_text(timestamp, speed="NORMAL", reason="Steady scene."):
    return json.dumps(
        {
            "observation": observation_doc(timestamp),
            "sampling": {"next_sampling_speed": speed, "reason": reason},
        }
    )


def stamp_from_prompt(kwargs):
    """Read the scheduled timestamp back out of the adaptive prompt."""
    text = kwargs["messages"][0]["content"][0]["text"]
    match = re.search(r"This frame is at ([\d.]+) seconds", text)
    assert match is not None, "prompt must state the frame timestamp"
    raw = match.group(1)
    return int(raw) if "." not in raw else float(raw)


def echo_handler(speeds=("NORMAL",), reasons=None):
    """Serve wrapper texts echoing each request's prompted timestamp."""

    def handle(kwargs, index):
        timestamp = stamp_from_prompt(kwargs)
        speed = speeds[index] if index < len(speeds) else speeds[-1]
        reason = (reasons or ["Steady scene."])[min(index, len(reasons or ["x"]) - 1)]
        return FakeRawResponse(
            FakeCompletion([FakeChoice(FakeMessage(adaptive_text(timestamp, speed, reason)))])
        )

    return handle


class SchedulerTests(unittest.TestCase):
    def test_first_timestamp_is_zero(self):
        scheduler = AdaptiveScheduler(None, 7.0)
        self.assertEqual(scheduler.first_timestamp(), 0)

    def test_speeds_map_to_configured_intervals(self):
        scheduler = AdaptiveScheduler(None, 100.0)
        self.assertEqual(scheduler.interval_for("HIGH"), 0.5)
        self.assertEqual(scheduler.interval_for("NORMAL"), 3.0)
        self.assertEqual(scheduler.interval_for("LOW"), 6.0)
        self.assertEqual(scheduler.next_timestamp(0, "HIGH"), 0.5)
        self.assertEqual(scheduler.next_timestamp(0, "NORMAL"), 3.0)
        self.assertEqual(scheduler.next_timestamp(0, "LOW"), 6.0)

    def test_invalid_speed_falls_back_to_normal(self):
        scheduler = AdaptiveScheduler(None, 100.0)
        self.assertEqual(scheduler.interval_for("TURBO"), 3.0)
        self.assertEqual(scheduler.interval_for(None), 3.0)
        self.assertEqual(scheduler.next_timestamp(10, "bogus"), 13.0)

    def test_end_of_video_terminates(self):
        scheduler = AdaptiveScheduler(None, 7.0)
        self.assertIsNone(scheduler.next_timestamp(6.5, "NORMAL"))
        self.assertIsNone(scheduler.next_timestamp(6.9, "HIGH"))
        self.assertIsNone(scheduler.next_timestamp(6.5, "LOW"))
        short = AdaptiveScheduler(None, 0.3)
        self.assertEqual(short.first_timestamp(), 0)
        self.assertIsNone(short.next_timestamp(0, "HIGH"))

    def test_float_boundaries_are_exact(self):
        scheduler = AdaptiveScheduler(None, 7.0)
        # Exact binary fractions accumulate without drift.
        self.assertEqual(scheduler.next_timestamp(0, "HIGH"), 0.5)
        self.assertEqual(scheduler.next_timestamp(0.5, "HIGH"), 1.0)
        # A step landing exactly on the duration ends the run, never
        # schedules an out-of-range timestamp.
        exact = AdaptiveScheduler(None, 6.0)
        self.assertIsNone(exact.next_timestamp(5.5, "HIGH"))
        self.assertIsNone(exact.next_timestamp(0, "LOW"))

    def test_timestamps_always_increase(self):
        scheduler = AdaptiveScheduler({"HIGH": 0.1, "NORMAL": 0.1, "LOW": 0.1}, 10.0)
        current = 0
        for _ in range(50):
            nxt = scheduler.next_timestamp(current, "HIGH")
            self.assertIsNotNone(nxt)
            self.assertGreater(nxt, current)
            current = nxt

    def test_custom_intervals_are_used(self):
        scheduler = AdaptiveScheduler({"HIGH": 1.0, "NORMAL": 2.0, "LOW": 4.0}, 100.0)
        self.assertEqual(scheduler.next_timestamp(0, "HIGH"), 1.0)
        self.assertEqual(scheduler.next_timestamp(0, "LOW"), 4.0)

    def test_invalid_interval_config_rejected(self):
        for bad in (
            {"HIGH": 0.5, "NORMAL": 3.0},  # missing key
            {"HIGH": 0.5, "NORMAL": 3.0, "LOW": 6.0, "TURBO": 1.0},  # extra
            {"HIGH": 0, "NORMAL": 3.0, "LOW": 6.0},  # below minimum
            {"HIGH": -1.0, "NORMAL": 3.0, "LOW": 6.0},
            {"HIGH": 0.5, "NORMAL": 3.0, "LOW": 1000.0},  # above maximum
            {"HIGH": float("nan"), "NORMAL": 3.0, "LOW": 6.0},
            {"HIGH": float("inf"), "NORMAL": 3.0, "LOW": 6.0},
            {"HIGH": True, "NORMAL": 3.0, "LOW": 6.0},  # bool is not a number
            {"HIGH": "fast", "NORMAL": 3.0, "LOW": 6.0},
            ["HIGH"],
            "not-a-dict",
        ):
            with self.assertRaises(ValueError, msg=f"intervals={bad!r}"):
                validate_intervals(bad)

    def test_invalid_duration_rejected(self):
        for bad in (0, -3.0, float("nan"), float("inf"), "7", True, None):
            with self.assertRaises(ValueError, msg=f"duration={bad!r}"):
                AdaptiveScheduler(None, bad)

    def test_format_and_path_helpers(self):
        self.assertEqual(format_adaptive_timestamp(0), "00000")
        self.assertEqual(format_adaptive_timestamp(3), "00003")
        self.assertEqual(format_adaptive_timestamp(3.0), "00003")
        self.assertEqual(format_adaptive_timestamp(0.5), "00000.5")
        self.assertEqual(format_adaptive_timestamp(1.25), "00001.25")
        out = Path("/tmp/obs")
        self.assertEqual(
            observation_path_for_timestamp(3, out), out / "frame_00003.json"
        )
        self.assertEqual(
            observation_path_for_timestamp(0.5, out), out / "frame_00000.5.json"
        )
        self.assertEqual(parse_observation_timestamp("frame_00003.json"), 3)
        self.assertEqual(parse_observation_timestamp("frame_00000.5.json"), 0.5)
        self.assertIsNone(parse_observation_timestamp("match_state.json"))

    def test_parse_max_frames_argument(self):
        self.assertEqual(parse_max_frames_argument(1), 1)
        self.assertEqual(parse_max_frames_argument("60"), 60)
        for bad in (0, -1, 10001, True, "many", 2.5, None):
            with self.assertRaises(ValueError, msg=f"max_frames={bad!r}"):
                parse_max_frames_argument(bad)


class ResponseValidationTests(unittest.TestCase):
    def test_valid_combined_response(self):
        validated, speed, reason, diagnostics = parse_adaptive_response(
            adaptive_text(12, "HIGH", "Combat visible."), 12
        )
        self.assertEqual(validated["timestamp"], 12)
        self.assertEqual(validated["schema_version"], "observation-v1")
        self.assertEqual(speed, "HIGH")
        self.assertEqual(reason, "Combat visible.")
        self.assertEqual(diagnostics, [])

    def test_missing_sampling_uses_normal_fallback(self):
        text = json.dumps({"observation": observation_doc(3)})
        validated, speed, reason, diagnostics = parse_adaptive_response(text, 3)
        self.assertEqual(validated["timestamp"], 3)
        self.assertEqual(speed, "NORMAL")
        self.assertEqual(reason, "")
        self.assertEqual(diagnostics, ["sampling-missing"])

    def test_invalid_sampling_label_falls_back_keeping_observation(self):
        for bad_sampling in (
            {"next_sampling_speed": "TURBO", "reason": "Fast."},
            {"next_sampling_speed": "high", "reason": "Fast."},
            {"next_sampling_speed": 3, "reason": "Fast."},
            {"next_sampling_speed": None},
            {"reason": "No speed given."},
            {},
            ["HIGH"],
            "HIGH",
            None,
        ):
            text = json.dumps(
                {"observation": observation_doc(0), "sampling": bad_sampling}
            )
            validated, speed, reason, diagnostics = parse_adaptive_response(
                text, 0
            )
            self.assertEqual(validated["timestamp"], 0, msg=f"{bad_sampling!r}")
            self.assertEqual(speed, "NORMAL", msg=f"{bad_sampling!r}")
            self.assertTrue(diagnostics, msg=f"{bad_sampling!r}")

    def test_extra_sampling_fields_do_not_control_scheduler(self):
        text = json.dumps(
            {
                "observation": observation_doc(0),
                "sampling": {
                    "next_sampling_speed": "LOW",
                    "reason": "Calm.",
                    "next_timestamp": 99,
                },
            }
        )
        validated, speed, reason, diagnostics = parse_adaptive_response(text, 0)
        self.assertEqual(validated["timestamp"], 0)
        self.assertEqual(speed, "NORMAL")
        self.assertTrue(any("unexpected" in entry for entry in diagnostics))

    def test_invalid_reason_type_and_length_handled_safely(self):
        text = json.dumps(
            {
                "observation": observation_doc(0),
                "sampling": {"next_sampling_speed": "HIGH", "reason": 42},
            }
        )
        _, speed, reason, diagnostics = parse_adaptive_response(text, 0)
        self.assertEqual((speed, reason), ("HIGH", ""))
        self.assertTrue(diagnostics)
        long_reason = "x" * (ADAPTIVE_MAX_REASON_CHARS + 50)
        text = json.dumps(
            {
                "observation": observation_doc(0),
                "sampling": {"next_sampling_speed": "LOW", "reason": long_reason},
            }
        )
        _, speed, reason, diagnostics = parse_adaptive_response(text, 0)
        self.assertEqual(speed, "LOW")
        self.assertEqual(len(reason), ADAPTIVE_MAX_REASON_CHARS)
        self.assertTrue(any("truncated" in entry for entry in diagnostics))

    def test_invalid_observation_rejected(self):
        broken = observation_doc(0)
        broken["observations"][0]["category"] = "spaceship"
        text = json.dumps({"observation": broken, "sampling": {"next_sampling_speed": "HIGH"}})
        with self.assertRaises(ObservationError):
            parse_adaptive_response(text, 0)

    def test_wrong_observation_timestamp_rejected(self):
        with self.assertRaises(ObservationError):
            parse_adaptive_response(adaptive_text(3, "HIGH"), 0)

    def test_extra_observation_fields_rejected(self):
        broken = observation_doc(0)
        broken["adaptive"] = True
        text = json.dumps({"observation": broken, "sampling": {"next_sampling_speed": "HIGH"}})
        with self.assertRaises(ObservationError):
            parse_adaptive_response(text, 0)
        broken_entry = observation_doc(0)
        broken_entry["observations"][0]["extra"] = 1
        text = json.dumps(
            {"observation": broken_entry, "sampling": {"next_sampling_speed": "HIGH"}}
        )
        with self.assertRaises(ObservationError):
            parse_adaptive_response(text, 0)

    def test_extra_wrapper_fields_rejected(self):
        text = json.dumps(
            {
                "observation": observation_doc(0),
                "sampling": {"next_sampling_speed": "HIGH"},
                "manifest": {},
            }
        )
        with self.assertRaises(ObservationError):
            parse_adaptive_response(text, 0)

    def test_missing_observation_rejected(self):
        text = json.dumps({"sampling": {"next_sampling_speed": "HIGH"}})
        with self.assertRaises(ObservationError):
            parse_adaptive_response(text, 0)

    def test_malformed_json_rejected(self):
        for bad in ("", "   ", "{invalid json", "[1, 2]", '"just a string"', "null"):
            with self.assertRaises(ObservationError, msg=f"text={bad!r}"):
                parse_adaptive_response(bad, 0)

    def test_markdown_fences_behave_like_observation_parser(self):
        fenced = "```json\n" + adaptive_text(5, "LOW", "Calm.") + "\n```"
        validated, speed, _, _ = parse_adaptive_response(fenced, 5)
        self.assertEqual(validated["timestamp"], 5)
        self.assertEqual(speed, "LOW")
        with self.assertRaises(ObservationError):
            parse_adaptive_response("```\nnot json\n```", 0)


class PromptAndEncodingTests(unittest.TestCase):
    def test_prompt_states_timestamp_and_speed_choices(self):
        prompt = build_adaptive_vision_prompt(0.5)
        self.assertIn("0.5", prompt)
        self.assertIn('"HIGH"', prompt)
        self.assertIn('"NORMAL"', prompt)
        self.assertIn('"LOW"', prompt)
        self.assertIn("observation-v1", prompt)
        self.assertIn("No previous observation", prompt)

    def test_prompt_includes_bounded_previous_context(self):
        previous = observation_doc(3)
        prompt = build_adaptive_vision_prompt(6, previous)
        self.assertIn("6", prompt)
        self.assertIn("supplementary", prompt)
        self.assertIn("3", prompt)

    def test_prompt_preserves_evidence_rules(self):
        prompt = build_adaptive_vision_prompt(0, observation_doc(0))
        for phrase in (
            "Do not invent",
            "uncertaint",
            "Never suggest that unseen events occurred",
        ):
            self.assertIn(phrase, prompt)

    def test_previous_context_bounds(self):
        self.assertEqual(build_previous_context(None), "")
        self.assertEqual(build_previous_context("nope"), "")
        huge = observation_doc(0)
        huge["observations"] = [
            {
                "category": "hud",
                "description": "d" * 5000,
                "confidence": "high",
                "evidence": "e" * 5000,
            }
            for _ in range(30)
        ]
        huge["uncertainties"] = ["u" * 5000 for _ in range(30)]
        context = build_previous_context(huge)
        self.assertLessEqual(len(context), 2000 + len("…(truncated)"))
        self.assertIn("…(truncated)", context)

    def test_encode_frame_image(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        image_bytes, mime_type, width, height = encode_frame_image(frame)
        self.assertTrue(image_bytes)
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual((width, height), (64, 48))
        for bad in (None, np.array([]), "not-an-array"):
            with self.assertRaises(ValueError, msg=f"frame={type(bad)}"):
                encode_frame_image(bad)

    def test_analyze_adaptive_frame_round_trip(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        client = FakeClient(echo_handler(("HIGH",)))
        validated, speed, reason, diagnostics = analyze_adaptive_frame(
            client, frame, 0, None
        )
        self.assertEqual(validated["timestamp"], 0)
        self.assertEqual(speed, "HIGH")
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(client.calls), 1)
        # The request used the configured model and a finite timeout.
        create_kwargs = client.calls[0]
        self.assertIn("timeout", create_kwargs)
        self.assertTrue(create_kwargs["timeout"] > 0)


class VideoReaderTests(unittest.TestCase):
    def test_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = ScheduledVideoReader(Path(tmp) / "nope.mp4")
            with self.assertRaises(FileNotFoundError):
                reader.__enter__()
            self.assertFalse(reader.is_open)

    def test_unopenable_video_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "clip.mp4"
            candidate.write_bytes(b"not a video")
            reader = ScheduledVideoReader(candidate)
            with self.assertRaises(RuntimeError):
                reader.__enter__()
            self.assertFalse(reader.is_open)

    def test_invalid_metadata_rejected(self):
        class DeadCapture:
            def isOpened(self):
                return True

            def get(self, _prop):
                return 0

            def release(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            candidate = write_test_video(Path(tmp) / "clip.mp4")
            with mock.patch(
                "ai_commentator.vision.reader.cv2.VideoCapture",
                return_value=DeadCapture(),
            ):
                with self.assertRaises(RuntimeError):
                    with ScheduledVideoReader(candidate):
                        pass

    def test_successful_scheduled_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = write_test_video(Path(tmp) / "clip.mp4")
            with ScheduledVideoReader(candidate) as reader:
                self.assertTrue(reader.is_open)
                self.assertGreater(reader.duration, 0)
                self.assertGreater(reader.fps, 0)
                frame = reader.capture_at(0)
                self.assertIsNotNone(frame)
                self.assertGreater(frame.size, 0)
                later = reader.capture_at(3.5)
                self.assertGreater(later.size, 0)
            self.assertFalse(reader.is_open)

    def test_out_of_range_timestamp_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = write_test_video(Path(tmp) / "clip.mp4", seconds=2)
            with ScheduledVideoReader(candidate) as reader:
                with self.assertRaises(ValueError):
                    reader.capture_at(-1)
                with self.assertRaises(ValueError):
                    reader.capture_at(reader.duration)
                with self.assertRaises(ValueError):
                    reader.capture_at(reader.duration + 10)

    def test_read_failure_raises(self):
        class FailingCapture:
            def __init__(self, real):
                self._real = real

            def isOpened(self):
                return True

            def get(self, prop):
                return self._real.get(prop)

            def set(self, _prop, _value):
                return True

            def read(self):
                return False, None

            def release(self):
                self._real.release()

        with tempfile.TemporaryDirectory() as tmp:
            candidate = write_test_video(Path(tmp) / "clip.mp4")
            real = cv2.VideoCapture(str(candidate))
            try:
                with mock.patch(
                    "ai_commentator.vision.reader.cv2.VideoCapture",
                    return_value=FailingCapture(real),
                ):
                    with ScheduledVideoReader(candidate) as reader:
                        with self.assertRaises(RuntimeError):
                            reader.capture_at(0)
            finally:
                real.release()

    def test_release_on_all_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = write_test_video(Path(tmp) / "clip.mp4")
            reader = ScheduledVideoReader(candidate)
            reader.__enter__()
            self.assertTrue(reader.is_open)
            reader.close()
            self.assertFalse(reader.is_open)
            reader.close()  # idempotent
            with ScheduledVideoReader(candidate) as entered:
                self.assertTrue(entered.is_open)
            self.assertFalse(entered.is_open)
            # A capture failure still releases via the context manager.
            with mock.patch.object(
                ScheduledVideoReader, "capture_at", side_effect=RuntimeError("boom")
            ):
                with self.assertRaises(RuntimeError):
                    with ScheduledVideoReader(candidate) as failing:
                        failing.capture_at(0)
                self.assertFalse(failing.is_open)


class OrchestrationTests(unittest.TestCase):
    def run_loop(self, video_seconds=7, speeds=("NORMAL",), max_frames=10, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        video = write_test_video(root / "clip.mp4", seconds=video_seconds)
        obs_dir = root / "out" / "observations"
        client = FakeClient(echo_handler(speeds))
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                result = run_adaptive_vision(
                    video_path=video,
                    observations_dir=obs_dir,
                    max_frames=max_frames,
                    client=client,
                    **kwargs,
                )
        return result, client, obs_dir

    def test_one_request_per_captured_frame(self):
        result, client, _ = self.run_loop(
            speeds=("HIGH", "HIGH", "NORMAL"), max_frames=3
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["termination"], "max-frames")
        self.assertEqual(result["requests"], 3)
        self.assertEqual(result["observations"], 3)
        self.assertEqual(len(client.calls), 3)

    def test_next_timestamp_uses_previous_validated_decision(self):
        result, _, _ = self.run_loop(speeds=("HIGH", "LOW"), max_frames=2)
        self.assertEqual(result["status"], "completed")
        # HIGH after 0 -> 0.5; LOW after 0.5 -> 6.5 (video is 7s).
        self.assertEqual(result["requested_timestamps"], [0, 0.5])
        self.assertEqual(result["speed_counts"], {"HIGH": 1, "NORMAL": 0, "LOW": 1})

    def test_run_terminates_at_video_end(self):
        result, client, _ = self.run_loop(speeds=("NORMAL",), max_frames=100)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["termination"], "video-end")
        # 7s video at NORMAL/3s: 0, 3, 6 then 9 >= 7 stops.
        self.assertEqual(result["requested_timestamps"], [0, 3, 6])
        self.assertEqual(len(client.calls), 3)

    def test_observations_written_and_manifest_recorded(self):
        result, _, obs_dir = self.run_loop(speeds=("HIGH",), max_frames=2)
        for timestamp in (0, 0.5):
            name = f"frame_{format_adaptive_timestamp(timestamp)}.json"
            doc = json.loads((obs_dir / name).read_text(encoding="utf-8"))
            self.assertEqual(doc["timestamp"], timestamp)
            self.assertEqual(doc["schema_version"], "observation-v1")
        manifest = json.loads(
            (obs_dir / "adaptive_run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], "adaptive-run-v1")
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["termination"], "max-frames")
        self.assertEqual(manifest["requested_timestamps"], [0, 0.5])
        self.assertEqual(len(manifest["iterations"]), 2)
        self.assertEqual(manifest["observations"], 2)
        self.assertEqual(manifest["requests"], 2)
        iteration = manifest["iterations"][0]
        self.assertEqual(iteration["speed"], "HIGH")
        self.assertIn("observation_file", iteration)
        self.assertNotIn("BASETEN_API_KEY", json.dumps(manifest))

    def test_previous_context_is_bounded_and_chronological(self):
        seen_prompts = []

        def handle(kwargs, index):
            seen_prompts.append(kwargs["messages"][0]["content"][0]["text"])
            timestamp = stamp_from_prompt(kwargs)
            return FakeRawResponse(
                FakeCompletion([FakeChoice(FakeMessage(adaptive_text(timestamp, "NORMAL")))])
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            obs_dir = root / "out" / "observations"
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    run_adaptive_vision(
                        video_path=video,
                        observations_dir=obs_dir,
                        max_frames=3,
                        client=FakeClient(handle),
                    )
        self.assertEqual(len(seen_prompts), 3)
        self.assertIn("No previous observation", seen_prompts[0])
        # Later prompts carry exactly one previous observation each.
        self.assertIn('"timestamp":0', seen_prompts[1].replace(" ", ""))
        self.assertIn('"timestamp":3', seen_prompts[2].replace(" ", ""))
        for prompt in seen_prompts:
            self.assertLess(len(prompt), 6000)

    def test_invalid_sampling_does_not_destroy_valid_observation(self):
        def handle(kwargs, index):
            timestamp = stamp_from_prompt(kwargs)
            return FakeRawResponse(
                FakeCompletion(
                    [FakeChoice(FakeMessage(
                        json.dumps(
                                {
                                    "observation": observation_doc(timestamp),
                                    "sampling": {
                                        "next_sampling_speed": "TURBO",
                                        "reason": "Fast!",
                                    },
                                }
                            )
                        ))
                    ]
                )
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            obs_dir = root / "out" / "observations"
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = run_adaptive_vision(
                        video_path=video,
                        observations_dir=obs_dir,
                        max_frames=1,
                        client=FakeClient(handle),
                    )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["observations"], 1)
            manifest = json.loads(
                (obs_dir / "adaptive_run.json").read_text(encoding="utf-8")
            )
        self.assertEqual(manifest["iterations"][0]["status"], "sampling-fallback")
        self.assertEqual(manifest["iterations"][0]["speed"], "NORMAL")
        # NORMAL fallback from 0 schedules 3 next (capped by max-frames here).
        self.assertEqual(result["requested_timestamps"], [0])

    def test_api_failure_creates_no_observation_and_no_retry(self):
        def handle(kwargs, index):
            raise APIError("boom", request=None, body=None)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            obs_dir = root / "out" / "observations"
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(err):
                    result = run_adaptive_vision(
                        video_path=video,
                        observations_dir=obs_dir,
                        max_frames=5,
                        client=FakeClient(handle),
                    )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["termination"], "request-failed")
            self.assertEqual(result["observations"], 0)
            self.assertEqual(result["failed_requests"], 1)
            self.assertEqual(result["error"]["phase"], "request-failed")
            self.assertEqual(
                [name.name for name in obs_dir.glob("frame_*.json")], []
            )

    def test_invalid_response_terminates_without_retry(self):
        calls = []

        def handle(kwargs, index):
            calls.append(index)
            return FakeRawResponse(FakeCompletion([FakeChoice(FakeMessage("{bad json"))]))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            obs_dir = root / "out" / "observations"
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = run_adaptive_vision(
                        video_path=video,
                        observations_dir=obs_dir,
                        max_frames=5,
                        client=FakeClient(handle),
                    )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["termination"], "response-invalid")
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                [name.name for name in obs_dir.glob("frame_*.json")], []
            )

    def test_write_failure_preserves_prior_observations(self):
        import ai_commentator.vision.adaptive as adaptive_module

        real_write = adaptive_module.write_json_atomic
        calls = {"count": 0}

        def flaky_write(destination, data):
            calls["count"] += 1
            if destination.name == "adaptive_run.json":
                return real_write(destination, data)
            if calls["count"] > 2:
                raise OSError("disk full")
            return real_write(destination, data)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            obs_dir = root / "out" / "observations"
            with mock.patch.object(
                adaptive_module, "write_json_atomic", side_effect=flaky_write
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    with contextlib.redirect_stderr(io.StringIO()):
                        result = run_adaptive_vision(
                            video_path=video,
                            observations_dir=obs_dir,
                            max_frames=5,
                            client=FakeClient(echo_handler(("NORMAL",))),
                        )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["termination"], "write-failed")
            # The first observation survived; the failed write left no file.
            first = obs_dir / "frame_00000.json"
            self.assertTrue(first.is_file())
            doc = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(doc["timestamp"], 0)

    def test_missing_key_without_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            with mock.patch.dict(os.environ, {"BASETEN_API_KEY": ""}):
                with self.assertRaises(RuntimeError):
                    run_adaptive_vision(
                        video_path=video,
                        observations_dir=root / "out" / "observations",
                    )

    def test_missing_video_fails_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = run_adaptive_vision(
                        video_path=root / "nope.mp4",
                        observations_dir=root / "out" / "observations",
                        client=FakeClient(echo_handler()),
                    )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["termination"], "open-failed")

    def test_secret_redaction_on_provider_error(self):
        from main import VisionAPIError, request_adaptive_text

        sentinel = "sk-test-sentinel-key"

        def handle(kwargs, index):
            raise APIError(f"bad key {sentinel} rejected", request=None, body=None)

        with self.assertRaises(VisionAPIError) as raised:
            request_adaptive_text(
                FakeClient(handle),
                0,
                "data:image/jpeg;base64,AAA=",
                api_key=sentinel,
            )
        self.assertNotIn(sentinel, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))


class StopTokenTests(unittest.TestCase):
    def test_stop_token_lifecycle(self):
        token = StopToken()
        self.assertFalse(token.is_stopping())
        token.request_stop()
        self.assertTrue(token.is_stopping())
        token.request_stop()  # repeated requests are idempotent
        self.assertTrue(token.is_stopping())


class GracefulStopTests(unittest.TestCase):
    def make_run(self, video_seconds=7):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        video = write_test_video(root / "clip.mp4", seconds=video_seconds)
        return root, video, root / "out" / "observations"

    def quiet(self, func, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                return func(*args, **kwargs)

    def test_stop_before_first_capture(self):
        _, video, obs_dir = self.make_run()
        token = StopToken()
        token.request_stop()
        client = FakeClient(echo_handler(("HIGH",)))
        result = self.quiet(
            run_adaptive_vision,
            video_path=video,
            observations_dir=obs_dir,
            max_frames=5,
            client=client,
            stop_token=token,
        )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["termination"], "stop-requested")
        self.assertEqual(result["observations"], 0)
        self.assertEqual(result["requests"], 0)
        self.assertEqual(len(client.calls), 0)
        manifest = json.loads(
            (obs_dir / "adaptive_run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "stopped")
        self.assertEqual(manifest["requested_timestamps"], [])

    def test_stop_between_iterations(self):
        _, video, obs_dir = self.make_run()
        token = StopToken()

        def handle(kwargs, index):
            timestamp = stamp_from_prompt(kwargs)
            if index >= 1:
                self.fail("no second API call may happen after stop")
            token.request_stop()  # stop arrives during the first request
            return FakeRawResponse(
                FakeCompletion([FakeChoice(FakeMessage(adaptive_text(timestamp, "HIGH")))])
            )

        client = FakeClient(handle)
        result = self.quiet(
            run_adaptive_vision,
            video_path=video,
            observations_dir=obs_dir,
            max_frames=5,
            client=client,
            stop_token=token,
        )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["termination"], "stop-requested")
        self.assertEqual(result["observations"], 1)
        self.assertEqual(result["requests"], 1)
        self.assertEqual(len(client.calls), 1)
        # The in-flight response still validated and persisted.
        doc = json.loads((obs_dir / "frame_00000.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["timestamp"], 0)

    def test_stop_during_in_flight_request(self):
        _, video, obs_dir = self.make_run()
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def handle(kwargs, index):
            calls.append(index)
            timestamp = stamp_from_prompt(kwargs)
            entered.set()
            self.assertTrue(release.wait(timeout=30), "test harness deadlock")
            return FakeRawResponse(
                FakeCompletion([FakeChoice(FakeMessage(adaptive_text(timestamp, "HIGH")))])
            )

        token = StopToken()
        client = FakeClient(handle)
        result_holder = {}

        def target():
            result_holder["result"] = run_adaptive_vision(
                video_path=video,
                observations_dir=obs_dir,
                max_frames=5,
                client=client,
                stop_token=token,
            )

        thread = threading.Thread(target=target)
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                thread.start()
                try:
                    self.assertTrue(entered.wait(timeout=30))
                    # Request Stop while the fake request is blocked: the
                    # request itself is never cancelled.
                    token.request_stop()
                    token.request_stop()  # idempotent
                    release.set()
                finally:
                    release.set()
                thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "adaptive loop thread did not finish")
        result = result_holder["result"]
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["termination"], "stop-requested")
        self.assertEqual(len(calls), 1, "in-flight request ran to completion once")
        self.assertEqual(result["observations"], 1)
        # The valid in-flight response was persisted before halting.
        doc = json.loads((obs_dir / "frame_00000.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["timestamp"], 0)
        manifest = json.loads(
            (obs_dir / "adaptive_run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "stopped")
        self.assertEqual(manifest["requested_timestamps"], [0])

    def test_stop_during_failing_request_reports_failure_as_stopped(self):
        _, video, obs_dir = self.make_run()
        entered = threading.Event()
        release = threading.Event()

        def handle(kwargs, index):
            entered.set()
            self.assertTrue(release.wait(timeout=30), "test harness deadlock")
            raise APIError("provider exploded", request=None, body=None)

        token = StopToken()
        client = FakeClient(handle)
        result_holder = {}

        def target():
            result_holder["result"] = run_adaptive_vision(
                video_path=video,
                observations_dir=obs_dir,
                max_frames=5,
                client=client,
                stop_token=token,
            )

        thread = threading.Thread(target=target)
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                thread.start()
                try:
                    self.assertTrue(entered.wait(timeout=30))
                    token.request_stop()
                    release.set()
                finally:
                    release.set()
                thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "adaptive loop thread did not finish")
        result = result_holder["result"]
        # Stop intent is preserved while the request failure is recorded.
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["termination"], "stop-requested")
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["error"]["phase"], "request-failed")
        self.assertEqual(result["observations"], 0)
        self.assertEqual(
            [path.name for path in obs_dir.glob("frame_*.json")], []
        )


class WorkerAdaptiveTests(unittest.TestCase):
    def run_pipeline_quiet(self, args):
        buffer = io.StringIO()
        real = worker.REAL_STDOUT
        worker.REAL_STDOUT = buffer
        try:
            code, data = worker.run_pipeline(args)
        finally:
            worker.REAL_STDOUT = real
        messages = [json.loads(line) for line in buffer.getvalue().splitlines()]
        return code, data, messages

    def adaptive_args(self, video, out, **overrides):
        kwargs = {
            "video": video,
            "output_dir": out,
            "limit": None,
            "force": False,
            "jobs": None,
            "skip_vision": False,
            "with_script": False,
            "adaptive": True,
            "adaptive_max_frames": 2,
        }
        kwargs.update(overrides)
        return SimpleNamespace(**kwargs)

    def completed_result(self, video, obs_dir):
        return {
            "status": "completed",
            "termination": "video-end",
            "video": str(video),
            "observations_dir": str(obs_dir),
            "duration": 7.0,
            "requested_timestamps": [0, 3],
            "observations": 2,
            "requests": 2,
            "failed_requests": 0,
            "speed_counts": {"HIGH": 0, "NORMAL": 2, "LOW": 0},
            "manifest": str(Path(obs_dir) / "adaptive_run.json"),
            "error": None,
        }

    def test_plan_and_mode_cover_adaptive(self):
        args = worker.build_parser().parse_args(
            ["clip.mp4", "--adaptive", "--adaptive-max-frames", "5"]
        )
        self.assertTrue(args.adaptive)
        self.assertEqual(args.adaptive_max_frames, 5)
        with tempfile.TemporaryDirectory() as tmp:
            plan = worker.plan_stages(args, Path(tmp))
        self.assertEqual(
            [stage for stage, _ in plan],
            [
                "adaptive_vision",
                "state_rebuild",
                "event_detection",
                "commentary",
            ],
        )
        self.assertTrue(all(mode == "run" for _, mode in plan))
        self.assertEqual(worker.run_mode(plan), "adaptive")
        scripted = worker.build_parser().parse_args(
            ["clip.mp4", "--adaptive", "--with-script"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            scripted_plan = worker.plan_stages(scripted, Path(tmp))
        self.assertEqual(scripted_plan[-1], ("script", "run"))

    def test_conflicting_flags_rejected(self):
        def run_main(argv):
            out, saved = io.StringIO(), worker.REAL_STDOUT
            worker.REAL_STDOUT = out
            try:
                code = worker.main(argv)
            finally:
                worker.REAL_STDOUT = saved
            return code, out.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            code, stdout = run_main(
                [str(video), "--output-dir", str(out), "--adaptive", "--skip-vision"],
            )
            self.assertEqual(code, 2)
            message = json.loads(stdout.strip().splitlines()[-1])
            self.assertEqual(message["type"], "error")
            self.assertEqual(message["stage"], "validation")
            code, stdout = run_main(
                [str(video), "--output-dir", str(out), "--adaptive", "--limit", "3"],
            )
            self.assertEqual(code, 2)
            code, stdout = run_main(
                [
                    str(video),
                    "--output-dir",
                    str(out),
                    "--adaptive-max-frames",
                    "3",
                ],
            )
            self.assertEqual(code, 2)

    def test_completed_adaptive_run_reports_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            obs_dir = out / "observations"
            obs_dir.mkdir(parents=True)
            (obs_dir / "frame_00000.json").write_text(
                json.dumps(observation_doc(0)), encoding="utf-8"
            )
            (obs_dir / "frame_00003.json").write_text(
                json.dumps(observation_doc(3)), encoding="utf-8"
            )
            expected = self.completed_result(video, obs_dir)
            with mock.patch.object(
                worker.engine, "run_adaptive_vision", return_value=expected
            ):
                code, data, messages = self.run_pipeline_quiet(
                    self.adaptive_args(str(video), str(out))
                )
        self.assertEqual(code, 0)
        stages = [
            message
            for message in messages
            if message["type"] == "stage_completed"
        ]
        self.assertEqual(stages[0]["stage"], "adaptive_vision")
        self.assertEqual(stages[0]["detail"]["observations"], 2)
        self.assertEqual(stages[0]["detail"]["requests"], 2)
        self.assertEqual(data["mode"], "adaptive")
        self.assertEqual(
            data["stages"],
            ["adaptive_vision", "state_rebuild", "event_detection", "commentary"],
        )
        totals = {
            message["total"]
            for message in messages
            if message["type"] in ("progress", "stage_completed")
        }
        self.assertEqual(totals, {4})
        for message in messages:
            if message["type"] in ("progress", "stage_completed"):
                self.assertNotIn(
                    message["stage"], ("frame_extraction", "vision_analysis")
                )

    def test_stopped_adaptive_run_emits_stopped_and_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            obs_dir = out / "observations"
            obs_dir.mkdir(parents=True)
            (obs_dir / "frame_00000.json").write_text(
                json.dumps(observation_doc(0)), encoding="utf-8"
            )
            stopped = dict(self.completed_result(video, obs_dir))
            stopped.update(
                status="stopped",
                termination="stop-requested",
                observations=1,
                requests=1,
                requested_timestamps=[0],
            )
            with mock.patch.object(
                worker.engine, "run_adaptive_vision", return_value=stopped
            ):
                code, data, messages = self.run_pipeline_quiet(
                    self.adaptive_args(str(video), str(out))
                )
        self.assertEqual(code, 0)
        stopped_messages = [
            message for message in messages if message["type"] == "stopped"
        ]
        self.assertEqual(len(stopped_messages), 1)
        self.assertEqual(stopped_messages[0]["data"]["status"], "stopped")
        self.assertEqual(
            stopped_messages[0]["data"]["termination"], "stop-requested"
        )
        self.assertEqual(data["status"], "stopped")
        # Downstream stages never ran: no message references them, and no
        # result message follows the stopped terminal.
        downstream = {"state_rebuild", "event_detection", "commentary"}
        self.assertFalse(
            [m for m in messages if m.get("stage") in downstream]
        )
        self.assertFalse([m for m in messages if m["type"] == "result"])
        # Every stdout line is a protocol message.
        for message in messages:
            self.assertIn(
                message["type"],
                ("started", "progress", "stage_completed", "result", "stopped", "error"),
            )

    def test_failed_adaptive_run_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            failed = dict(self.completed_result(video, out / "observations"))
            failed.update(
                status="failed",
                termination="request-failed",
                observations=0,
                requests=1,
                failed_requests=1,
                requested_timestamps=[],
                error={"phase": "request-failed", "message": "boom"},
            )
            with mock.patch.object(
                worker.engine, "run_adaptive_vision", return_value=failed
            ):
                code, data, messages = self.run_pipeline_quiet(
                    self.adaptive_args(str(video), str(out))
                )
        self.assertEqual(code, 1)
        self.assertEqual(data, {})
        errors = [m for m in messages if m["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["stage"], "adaptive_vision")
        self.assertFalse([m for m in messages if m["type"] == "result"])
        self.assertFalse([m for m in messages if m["type"] == "stopped"])

    def test_stop_handler_install_and_restore(self):
        token = StopToken()
        before = signal.getsignal(signal.SIGTERM)
        previous = worker.install_stop_handler(token)
        try:
            self.assertFalse(token.is_stopping())
            handler = signal.getsignal(signal.SIGTERM)
            self.assertNotEqual(handler, before)
            handler(signal.SIGTERM, None)
            self.assertTrue(token.is_stopping())
        finally:
            worker.restore_stop_handler(previous)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)


class CliAdaptiveTests(unittest.TestCase):
    def test_adaptive_dispatch_reports_completed(self):
        from ai_commentator import cli as cli_module

        result = {
            "status": "completed",
            "termination": "video-end",
            "observations": 2,
            "requests": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                worker.engine, "run_adaptive_vision", return_value=result
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        cli_module.run_cli(
                            worker.engine,
                            ["--adaptive", "--output-dir", tmp],
                        )
        self.assertEqual(raised.exception.code, 0)

    def test_adaptive_failure_exits_nonzero(self):
        from ai_commentator import cli as cli_module

        result = {
            "status": "failed",
            "termination": "request-failed",
            "observations": 0,
            "requests": 1,
            "error": {"phase": "request-failed", "message": "boom"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                worker.engine, "run_adaptive_vision", return_value=result
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        cli_module.run_cli(
                            worker.engine,
                            ["--adaptive", "--output-dir", tmp],
                        )
        self.assertEqual(raised.exception.code, 1)

    def test_adaptive_max_frames_requires_adaptive(self):
        from ai_commentator import cli as cli_module

        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stderr(io.StringIO()):
                cli_module.run_cli(
                    worker.engine, ["--adaptive-max-frames", "3"]
                )
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
