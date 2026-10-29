"""Offline tests for bounded concurrent vision batch mode (--vision-batch --jobs).

No live API calls: fake clients stand in for the Baseten OpenAI-compatible
endpoint, and fixture frames are tiny generated JPEGs. No wall-clock speed
assertions here; the synthetic sequential-vs-concurrent timing comparison
lives in tests/benchmark_jobs.py. Run inside Docker:
    docker compose run --rm -v "$PWD/tests:/app/tests:ro" \\
        commentator python -m unittest discover -s tests -v
"""

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

import main
from main import (
    DEFAULT_VISION_BATCH_JOBS,
    MAX_VISION_BATCH_JOBS,
    build_match_state,
    detect_events,
    parse_jobs_argument,
    parse_observation_timestamp,
    run_vision_batch,
    validate_match_state,
    write_observation_json,
)

MAIN_PATH = Path(main.__file__)
PROMPT_TIMESTAMP_RE = re.compile(r"frame is at (\d+) seconds")


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


def observation_payload(timestamp):
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


def observation_text(timestamp):
    return json.dumps(observation_payload(timestamp))


def completion_for(text):
    return FakeRawResponse(FakeCompletion([FakeChoice(FakeMessage(text))]))


def prompt_timestamp(kwargs):
    """Recover the requested frame timestamp from the prompt text."""
    text = kwargs["messages"][0]["content"][0]["text"]
    match = PROMPT_TIMESTAMP_RE.search(text)
    return int(match.group(1))


def matching_handler(kwargs, index):
    """Serve the observation matching the requested frame timestamp.

    Request order is meaningless under concurrency, so the response is
    derived from the prompt instead of the call index.
    """
    return completion_for(observation_text(prompt_timestamp(kwargs)))


def write_jpeg(path):
    path = Path(path)
    frame = np.zeros((16, 12, 3), dtype=np.uint8)
    frame[:, :, 1] = 200
    if not cv2.imwrite(str(path), frame):
        raise AssertionError(f"could not write fixture {path}")
    return path


def make_layout(root, timestamps):
    root = Path(root)
    frames = root / "frames"
    obs = root / "observations"
    obs.mkdir(parents=True, exist_ok=True)
    frames.mkdir(parents=True, exist_ok=True)
    for ts in timestamps:
        write_jpeg(frames / f"frame_{ts:05d}.jpg")
    return frames, obs


def run_batch_capture(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_vision_batch(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


def read_outputs(obs_dir):
    """Map frame timestamp -> parsed JSON for every observation file."""
    result = {}
    for path in sorted(Path(obs_dir).glob("frame_*.json")):
        result[parse_observation_timestamp(path.name)] = json.loads(
            path.read_text(encoding="utf-8")
        )
    return result


class ConcurrencyTracker:
    """Fake-client handler recording the peak in-flight request count."""

    def __init__(self, rendezvous=None, delay=0.01):
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_seen = 0
        self.calls = []
        self._rendezvous = rendezvous
        self._delay = delay

    def handle(self, kwargs, index):
        timestamp = prompt_timestamp(kwargs)
        with self._lock:
            self._in_flight += 1
            self.max_seen = max(self.max_seen, self._in_flight)
            self.calls.append(timestamp)
        try:
            if self._rendezvous is not None:
                self._rendezvous.wait()
            time.sleep(self._delay)
            return completion_for(observation_text(timestamp))
        finally:
            with self._lock:
                self._in_flight -= 1


class ParseJobsArgumentTests(unittest.TestCase):
    def test_valid_values(self):
        for value in ("1", "2", "4"):
            self.assertEqual(parse_jobs_argument(value), int(value))

    def test_defaults_and_max_constants(self):
        self.assertEqual(DEFAULT_VISION_BATCH_JOBS, 1)
        self.assertEqual(MAX_VISION_BATCH_JOBS, 4)

    def test_invalid_values(self):
        for value in ("0", "-1", "-100", "5", "100", "two", "", "2.5", None):
            with self.assertRaises(argparse.ArgumentTypeError, msg=repr(value)):
                parse_jobs_argument(value)


class JobsValidationTests(unittest.TestCase):
    def test_out_of_range_jobs_rejected_before_discovery(self):
        for jobs in (0, -2, 5, 100):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError, msg=f"jobs={jobs}"):
                    run_vision_batch(
                        jobs=jobs,
                        directory=Path(tmp),
                        observations_dir=Path(tmp),
                    )


class SequentialVsConcurrentTests(unittest.TestCase):
    def test_jobs1_and_jobs4_produce_identical_outputs(self):
        timestamps = [0, 3, 6, 9, 12]
        sequential, concurrent = {}, {}
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(Path(tmp) / "seq", timestamps)
            code, _, _ = run_batch_capture(
                limit=5, jobs=1, client=FakeClient(matching_handler),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 0)
            sequential = read_outputs(obs)
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(Path(tmp) / "con", timestamps)
            code, _, _ = run_batch_capture(
                limit=5, jobs=4, client=FakeClient(matching_handler),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 0)
            concurrent = read_outputs(obs)
        self.assertEqual(sorted(sequential), timestamps)
        self.assertEqual(sequential, concurrent)
        for timestamp, payload in concurrent.items():
            self.assertEqual(payload["timestamp"], timestamp)


class ConcurrencyBoundTests(unittest.TestCase):
    def test_max_in_flight_never_exceeds_jobs(self):
        # Frame count is a multiple of the barrier parties so every arrival
        # joins a complete rendezvous; a remainder would wait out the timeout.
        tracker = ConcurrencyTracker(
            rendezvous=threading.Barrier(4, timeout=30), delay=0.01
        )
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, [0, 3, 6, 9])
            code, _, _ = run_batch_capture(
                limit=4, jobs=4, client=FakeClient(tracker.handle),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 0)
            outputs = read_outputs(obs)
        self.assertEqual(tracker.max_seen, 4)
        self.assertEqual(len(outputs), 4)

    def test_two_jobs_overlap(self):
        tracker = ConcurrencyTracker(
            rendezvous=threading.Barrier(2, timeout=30), delay=0.01
        )
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, [0, 3, 6, 9])
            code, _, _ = run_batch_capture(
                limit=4, jobs=2, client=FakeClient(tracker.handle),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 0)
            outputs = read_outputs(obs)
        self.assertEqual(tracker.max_seen, 2)
        self.assertEqual(len(outputs), 4)

    def test_sequential_mode_never_overlaps(self):
        tracker = ConcurrencyTracker(delay=0.01)
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, [0, 3, 6])
            code, _, _ = run_batch_capture(
                limit=3, jobs=1, client=FakeClient(tracker.handle),
                directory=frames, observations_dir=obs,
            )
        self.assertEqual(code, 0)
        self.assertEqual(tracker.max_seen, 1)


class OutOfOrderCompletionTests(unittest.TestCase):
    def test_reverse_completion_order_still_maps_frames_correctly(self):
        timestamps = [0, 3, 6, 9, 12]
        top = max(timestamps)

        def reverse_handler(kwargs, index):
            timestamp = prompt_timestamp(kwargs)
            time.sleep((top - timestamp) * 0.02)
            return completion_for(observation_text(timestamp))

        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, timestamps)
            code, out, _ = run_batch_capture(
                limit=5, jobs=4, client=FakeClient(reverse_handler),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 0)
            outputs = read_outputs(obs)
        self.assertEqual(sorted(outputs), timestamps)
        for timestamp, payload in outputs.items():
            self.assertEqual(payload["timestamp"], timestamp)
        # Reporting stays in selected-frame order, not completion order.
        analysing = [
            line for line in out.splitlines() if line.startswith("[") and "Analysing" in line
        ]
        self.assertEqual(len(analysing), 5)
        saved = [line for line in out.splitlines() if line.startswith("Saved")]
        self.assertEqual(len(saved), 5)


class FailureIsolationTests(unittest.TestCase):
    def test_error_and_timeout_fail_only_their_frames(self):
        def flaky_handler(kwargs, index):
            timestamp = prompt_timestamp(kwargs)
            if timestamp == 6:
                raise RuntimeError("boom-6")
            if timestamp == 9:
                raise TimeoutError("timed out after 30s")
            time.sleep(0.01)
            return completion_for(observation_text(timestamp))

        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, [0, 3, 6, 9, 12])
            code, _, err = run_batch_capture(
                limit=5, jobs=4, client=FakeClient(flaky_handler),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 1)
            outputs = read_outputs(obs)
        self.assertEqual(sorted(outputs), [0, 3, 12])
        for timestamp, payload in outputs.items():
            self.assertEqual(payload["timestamp"], timestamp)
        # No partial JSON is left behind for the failed frames.
        self.assertFalse((obs / "frame_00006.json").exists())
        self.assertFalse((obs / "frame_00009.json").exists())
        self.assertIn("frame_00006", err)
        self.assertIn("frame_00009", err)


class CacheTests(unittest.TestCase):
    def test_cached_observations_make_no_requests(self):
        timestamps = [0, 3, 6, 9, 12]
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, timestamps)
            for timestamp in timestamps:
                write_observation_json(
                    obs / f"frame_{timestamp:05d}.json",
                    observation_payload(timestamp),
                )
            client = FakeClient(matching_handler)
            code, out, _ = run_batch_capture(
                limit=5, jobs=4, client=client,
                directory=frames, observations_dir=obs,
            )
        self.assertEqual(code, 0)
        self.assertEqual(client.calls, [])
        self.assertIn("5 skipped", out)

    def test_force_reanalyses_everything_concurrently(self):
        timestamps = [0, 3, 6]
        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, timestamps)
            for timestamp in timestamps:
                write_observation_json(
                    obs / f"frame_{timestamp:05d}.json",
                    observation_payload(timestamp),
                )
            client = FakeClient(matching_handler)
            code, _, _ = run_batch_capture(
                limit=3, jobs=3, force=True, client=client,
                directory=frames, observations_dir=obs,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(client.calls), 3)


class SchemaEnforcementTests(unittest.TestCase):
    def test_mismatched_timestamp_rejected_under_concurrency(self):
        def wrong_stamp_handler(kwargs, index):
            timestamp = prompt_timestamp(kwargs)
            return completion_for(observation_text(timestamp + 1000))

        with tempfile.TemporaryDirectory() as tmp:
            frames, obs = make_layout(tmp, [0, 3, 6])
            code, _, _ = run_batch_capture(
                limit=3, jobs=3, client=FakeClient(wrong_stamp_handler),
                directory=frames, observations_dir=obs,
            )
            self.assertEqual(code, 1)
            self.assertEqual(read_outputs(obs), {})


class DownstreamCompatibilityTests(unittest.TestCase):
    def _detect_from_outputs(self, obs_dir):
        accepted = []
        for path in sorted(Path(obs_dir).glob("frame_*.json")):
            timestamp = parse_observation_timestamp(path.name)
            validated = json.loads(path.read_text(encoding="utf-8"))
            accepted.append((timestamp, path.name, validated))
        state = build_match_state(list(accepted))
        validate_match_state(state)
        return detect_events(list(accepted), state)

    def test_concurrent_outputs_feed_state_and_events_identically(self):
        timestamps = [0, 3, 6]
        documents = []
        for jobs in (1, 4):
            with tempfile.TemporaryDirectory() as tmp:
                frames, obs = make_layout(tmp, timestamps)
                code, _, _ = run_batch_capture(
                    limit=3, jobs=jobs, client=FakeClient(matching_handler),
                    directory=frames, observations_dir=obs,
                )
                self.assertEqual(code, 0)
                document, _ = self._detect_from_outputs(obs)
                documents.append(document)
        self.assertEqual(documents[0], documents[1])


class CliJobsTests(unittest.TestCase):
    def _run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(MAIN_PATH), *argv],
            cwd=MAIN_PATH.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def test_jobs_requires_vision_batch(self):
        proc = self._run_cli("--jobs", "2")
        self.assertEqual(proc.returncode, 2)

    def test_jobs_out_of_range_rejected(self):
        for value in ("0", "5"):
            proc = self._run_cli("--vision-batch", "--jobs", value)
            self.assertEqual(proc.returncode, 2, msg=f"--jobs {value}")


if __name__ == "__main__":
    unittest.main()
