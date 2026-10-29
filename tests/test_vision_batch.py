"""Offline tests for multi-frame vision batch mode (--vision-batch).

No live API calls: a fake client stands in for the Baseten OpenAI-compatible
endpoint, and fixture frames are tiny generated JPEGs. Run inside Docker:
    docker compose run --rm -v "$PWD/tests:/app/tests:ro" \\
        commentator python -m unittest discover -s tests -v
"""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from openai import APIError

from main import (
    BASETEN_MODEL,
    DEFAULT_VISION_BATCH_LIMIT,
    OBSERVATION_SCHEMA_VERSION,
    ObservationError,
    VisionAPIError,
    analyze_frame,
    discover_candidate_frames,
    ensure_observation_timestamp,
    extract_response_text,
    load_frame_image,
    parse_frame_timestamp,
    parse_limit_argument,
    read_validated_observation,
    run_vision_batch,
    run_vision_test,
    select_spread_frames,
    write_observation_json,
)


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


class StatusAPIError(APIError):
    status_code = 503


def api_error(message):
    return APIError(message, request=object(), body=None)


def observation_text(timestamp):
    return json.dumps(
        {
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
    )


def sequential_handler(texts):
    """Serve prepared response texts in request order."""

    def handle(kwargs, index):
        return FakeRawResponse(
            FakeCompletion([FakeChoice(FakeMessage(texts[index]))])
        )

    return handle


def scripted_handler(steps):
    """Serve steps in order; each step is a response text or an exception."""

    def handle(kwargs, index):
        step = steps[index]
        if isinstance(step, Exception):
            raise step
        return FakeRawResponse(FakeCompletion([FakeChoice(FakeMessage(step))]))

    return handle


def write_jpeg(path):
    path = Path(path)
    frame = np.zeros((16, 12, 3), dtype=np.uint8)
    frame[:, :, 1] = 200
    if not cv2.imwrite(str(path), frame):
        raise AssertionError(f"could not write fixture {path}")
    return path


def make_frames(directory, timestamps):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [write_jpeg(directory / f"frame_{ts:05d}.jpg") for ts in timestamps]


def candidates_for(timestamps):
    return [(ts, Path(f"frame_{ts:05d}.jpg")) for ts in timestamps]


def quiet_call(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)


def run_batch_capture(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_vision_batch(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


class ParseFrameTimestampTests(unittest.TestCase):
    def test_valid_names(self):
        self.assertEqual(parse_frame_timestamp("frame_00000.jpg"), 0)
        self.assertEqual(parse_frame_timestamp("frame_00195.jpg"), 195)
        self.assertEqual(parse_frame_timestamp("frame_3.jpg"), 3)

    def test_invalid_names(self):
        for name in (
            "notes.txt",
            "frame_abc.jpg",
            "frame_.jpg",
            "frame_12.png",
            "frame_00005.jpeg",
            "FRAME_00003.jpg",
            "frame_00003.jpg.bak",
            "xframe_00003.jpg",
            "frame_00003jpg",
            "",
        ):
            self.assertIsNone(parse_frame_timestamp(name), msg=name)


class DiscoverTests(unittest.TestCase):
    def test_numeric_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ("frame_00010.jpg", "frame_00002.jpg", "frame_00003.jpg"):
                (directory / name).write_bytes(b"data")
            found = discover_candidate_frames(directory)
            self.assertEqual([ts for ts, _ in found], [2, 3, 10])

    def test_discovery_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0, 3, 6])
            first = discover_candidate_frames(directory)
            second = discover_candidate_frames(directory)
            self.assertEqual(first, second)

    def test_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "frame_00003.jpg").write_bytes(b"data")
            for name in (
                "notes.txt",
                "frame_abc.jpg",
                "frame_12.png",
                "frame_00003.json",
                "frame_00005.jpeg",
            ):
                (directory / name).write_bytes(b"data")
            (directory / "frame_00009.jpg").mkdir()
            found = discover_candidate_frames(directory)
            self.assertEqual([ts for ts, _ in found], [3])

    def test_missing_and_empty_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(discover_candidate_frames(root / "nope"), [])
            self.assertEqual(discover_candidate_frames(root), [])


class SelectTests(unittest.TestCase):
    def test_spread_66_frames_to_5(self):
        candidates = candidates_for(range(0, 196, 3))
        self.assertEqual(len(candidates), 66)
        stamps = [ts for ts, _ in select_spread_frames(candidates, 5)]
        self.assertEqual(stamps, [0, 48, 96, 147, 195])
        self.assertEqual(len(set(stamps)), 5)
        self.assertEqual(stamps, sorted(stamps))

    def test_endpoints_always_included(self):
        candidates = candidates_for([0, 3, 100])
        stamps = [ts for ts, _ in select_spread_frames(candidates, 2)]
        self.assertEqual(stamps, [0, 100])

    def test_non_contiguous_spread(self):
        candidates = candidates_for([0, 1, 100, 101, 102, 103])
        stamps = [ts for ts, _ in select_spread_frames(candidates, 3)]
        self.assertEqual(stamps, [0, 100, 103])

    def test_limit_one_selects_first(self):
        candidates = candidates_for([3, 6, 9])
        self.assertEqual(select_spread_frames(candidates, 1), [candidates[0]])

    def test_limit_at_or_above_count_selects_all(self):
        candidates = candidates_for([0, 3])
        self.assertEqual(select_spread_frames(candidates, 10), candidates)
        self.assertEqual(select_spread_frames(candidates, 2), candidates)

    def test_single_candidate(self):
        candidates = candidates_for([42])
        self.assertEqual(select_spread_frames(candidates, 5), candidates)
        self.assertEqual(select_spread_frames(candidates, 1), candidates)

    def test_invalid_limit_rejected(self):
        candidates = candidates_for([0, 3])
        for bad in (0, -1, -100):
            with self.assertRaises(ValueError, msg=bad):
                select_spread_frames(candidates, bad)


class LimitArgumentTests(unittest.TestCase):
    def test_valid_limits(self):
        self.assertEqual(parse_limit_argument("1"), 1)
        self.assertEqual(parse_limit_argument("5"), 5)

    def test_invalid_limits_rejected(self):
        for bad in ("0", "-1", "x", "2.5", ""):
            with self.assertRaises(argparse.ArgumentTypeError, msg=repr(bad)):
                parse_limit_argument(bad)

    def test_default_batch_limit_is_five(self):
        self.assertEqual(DEFAULT_VISION_BATCH_LIMIT, 5)


class LoadImageTests(unittest.TestCase):
    def test_missing_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_frame_image(Path(tmp) / "frame_00000.jpg")

    def test_empty_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame_00000.jpg"
            path.write_bytes(b"")
            with self.assertRaises(ValueError):
                load_frame_image(path)

    def test_corrupt_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame_00000.jpg"
            path.write_bytes(b"not image data" * 64)
            with self.assertRaises(ValueError):
                load_frame_image(path)

    def test_valid_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_jpeg(Path(tmp) / "frame_00000.jpg")
            data, mime, width, height = load_frame_image(path)
            self.assertTrue(data)
            self.assertEqual(mime, "image/jpeg")
            self.assertEqual((width, height), (12, 16))


class TimestampCheckTests(unittest.TestCase):
    def test_match_accepted(self):
        doc = {"timestamp": 6}
        self.assertIs(ensure_observation_timestamp(doc, 6), doc)

    def test_float_equal_accepted(self):
        self.assertEqual(
            ensure_observation_timestamp({"timestamp": 6.0}, 6)["timestamp"], 6.0
        )

    def test_mismatch_rejected(self):
        with self.assertRaises(ObservationError):
            ensure_observation_timestamp({"timestamp": 9}, 6)


class ExtractResponseTextTests(unittest.TestCase):
    def test_valid_content_returned(self):
        response = FakeCompletion([FakeChoice(FakeMessage('{"a": 1}'))])
        self.assertEqual(extract_response_text(response), '{"a": 1}')

    def test_empty_choices_rejected(self):
        for response in (
            FakeCompletion([]),
            SimpleNamespace(choices=None),
            SimpleNamespace(),
        ):
            with self.assertRaises(ObservationError):
                extract_response_text(response)

    def test_missing_or_blank_content_rejected(self):
        for content in (None, "", "   ", 42):
            response = FakeCompletion([FakeChoice(FakeMessage(content))])
            with self.assertRaises(ObservationError, msg=repr(content)):
                extract_response_text(response)


class AnalyzeFrameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.frame = write_jpeg(Path(self.tmp.name) / "frame_00006.jpg")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_observation_accepted(self):
        client = FakeClient(sequential_handler([observation_text(6)]))
        validated = quiet_call(analyze_frame, client, self.frame, 6)
        self.assertEqual(validated["timestamp"], 6)
        self.assertEqual(validated["schema_version"], OBSERVATION_SCHEMA_VERSION)
        kwargs = client.calls[0]
        self.assertEqual(kwargs["model"], BASETEN_MODEL)
        parts = kwargs["messages"][0]["content"]
        self.assertEqual(parts[0]["type"], "text")
        self.assertIn("6 seconds", parts[0]["text"])
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertTrue(
            parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )

    def test_mismatched_timestamp_rejected(self):
        client = FakeClient(sequential_handler([observation_text(9)]))
        with self.assertRaises(ObservationError):
            quiet_call(analyze_frame, client, self.frame, 6)

    def test_malformed_and_schema_invalid_rejected(self):
        for bad in ('{"schema_version": ', "The frame shows things."):
            client = FakeClient(sequential_handler([bad]))
            with self.assertRaises(ObservationError, msg=bad):
                quiet_call(analyze_frame, client, self.frame, 6)
        invalid = json.loads(observation_text(6))
        invalid["observations"][0]["category"] = "killfeed"
        client = FakeClient(sequential_handler([json.dumps(invalid)]))
        with self.assertRaises(ObservationError):
            quiet_call(analyze_frame, client, self.frame, 6)

    def test_empty_choices_and_missing_content_rejected(self):
        raws = [
            FakeRawResponse(FakeCompletion([])),
            FakeRawResponse(FakeCompletion([FakeChoice(FakeMessage(None))])),
        ]
        for raw in raws:
            client = FakeClient(lambda kwargs, index, bound=raw: bound)
            with self.assertRaises(ObservationError):
                quiet_call(analyze_frame, client, self.frame, 6)

    def test_api_error_redacts_key(self):
        secret = "SECRET-KEY-123"
        client = FakeClient(scripted_handler([api_error(f"auth {secret} denied")]))
        with self.assertRaises(VisionAPIError) as ctx:
            quiet_call(analyze_frame, client, self.frame, 6, api_key=secret)
        self.assertIn("[REDACTED]", str(ctx.exception))
        self.assertNotIn(secret, str(ctx.exception))

    def test_api_error_status_reported(self):
        err = StatusAPIError("overloaded", request=object(), body=None)
        client = FakeClient(scripted_handler([err]))
        with self.assertRaises(VisionAPIError) as ctx:
            quiet_call(analyze_frame, client, self.frame, 6)
        self.assertIn("HTTP 503", str(ctx.exception))


class WriteObservationTests(unittest.TestCase):
    def test_writes_indented_utf8_with_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "frame_00006.json"
            doc = json.loads(observation_text(6))
            write_observation_json(dest, doc)
            text = dest.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertIn('\n  "timestamp": 6,\n', text)
            self.assertEqual(json.loads(text), doc)

    def test_no_partial_file_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "missing-dir" / "frame_00006.json"
            with self.assertRaises(OSError):
                write_observation_json(dest, json.loads(observation_text(6)))
            self.assertFalse(dest.exists())
            self.assertEqual(list(root.rglob("*")), [])


class ReadValidatedObservationTests(unittest.TestCase):
    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame_00006.json"
            self.assertIsNone(read_validated_observation(path, 6))

    def test_valid_matching_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame_00006.json"
            path.write_text(observation_text(6), encoding="utf-8")
            self.assertEqual(read_validated_observation(path, 6)["timestamp"], 6)

    def test_unusable_returns_none(self):
        bodies = [
            observation_text(9),
            '{"schema_version": ',
            json.dumps({"schema_version": "observation-v1"}),
            "",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, body in enumerate(bodies):
                path = Path(tmp) / f"frame_{index:05d}.json"
                path.write_text(body, encoding="utf-8")
                self.assertIsNone(
                    read_validated_observation(path, index), msg=body[:30]
                )


class VisionBatchTests(unittest.TestCase):
    def test_success_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0, 3, 6, 9, 12, 15])
            before_jpegs = sorted(path.name for path in directory.glob("*.jpg"))
            selected = select_spread_frames(
                discover_candidate_frames(directory), 3
            )
            self.assertEqual([ts for ts, _ in selected], [0, 6, 15])
            client = FakeClient(
                sequential_handler([observation_text(ts) for ts, _ in selected])
            )
            code, out, err = run_batch_capture(
                limit=3, client=client, directory=directory
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertEqual(len(client.calls), 3)
            for ts in (0, 6, 15):
                doc = json.loads(
                    (directory / f"frame_{ts:05d}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(doc["timestamp"], ts)
                self.assertEqual(doc["schema_version"], OBSERVATION_SCHEMA_VERSION)
            for ts in (3, 9, 12):
                self.assertFalse((directory / f"frame_{ts:05d}.json").exists())
            self.assertEqual(
                sorted(path.name for path in directory.glob("*.jpg")), before_jpegs
            )
            self.assertIn(
                "6 available, 3 selected, 3 succeeded, 0 failed, 0 skipped", out
            )
            self.assertIn("Vision batch status: success.", out)

    def test_partial_failure_reporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0, 3, 6])
            steps = [observation_text(0), api_error("boom"), observation_text(6)]
            client = FakeClient(scripted_handler(steps))
            code, out, err = run_batch_capture(
                limit=3, client=client, directory=directory
            )
            self.assertEqual(code, 1)
            self.assertEqual(len(client.calls), 3)
            self.assertTrue((directory / "frame_00000.json").exists())
            self.assertFalse((directory / "frame_00003.json").exists())
            self.assertTrue((directory / "frame_00006.json").exists())
            self.assertEqual(list(directory.glob("*.tmp")), [])
            self.assertIn("3 selected, 2 succeeded, 1 failed, 0 skipped", out)
            self.assertIn("output: ", out)
            self.assertIn("Vision batch status: partial failure.", out)
            self.assertIn("Error [frame_00003.jpg]", err)

    def test_total_failure_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0, 3])
            client = FakeClient(
                scripted_handler([api_error("down"), api_error("down")])
            )
            code, out, _ = run_batch_capture(
                limit=2, client=client, directory=directory
            )
            self.assertEqual(code, 1)
            self.assertIn("Vision batch status: failure.", out)

    def test_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(sequential_handler([]))
            code, _, err = run_batch_capture(
                limit=5, client=client, directory=Path(tmp)
            )
            self.assertEqual(code, 1)
            self.assertIn("no frame_", err)

    def test_unrelated_outputs_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0, 3])
            stray = directory / "notes.json"
            stray.write_text('{"keep": true}', encoding="utf-8")
            other = directory / "frame_00003.json"
            other.write_text("garbage-not-json", encoding="utf-8")
            client = FakeClient(sequential_handler([observation_text(0)]))
            code, _, _ = run_batch_capture(
                limit=1, client=client, directory=directory
            )
            self.assertEqual(code, 0)
            self.assertEqual(stray.read_text(encoding="utf-8"), '{"keep": true}')
            self.assertEqual(other.read_text(encoding="utf-8"), "garbage-not-json")

    def test_skips_validated_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0, 3, 6])
            (directory / "frame_00003.json").write_text(
                observation_text(3), encoding="utf-8"
            )
            client = FakeClient(
                sequential_handler([observation_text(0), observation_text(6)])
            )
            code, out, _ = run_batch_capture(
                limit=3, client=client, directory=directory
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(client.calls), 2)
            self.assertIn("1 skipped", out)
            self.assertIn("Vision batch status: success.", out)

    def test_invalid_existing_is_reanalysed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0])
            (directory / "frame_00000.json").write_text(
                observation_text(99), encoding="utf-8"
            )
            client = FakeClient(sequential_handler([observation_text(0)]))
            code, out, _ = run_batch_capture(
                limit=1, client=client, directory=directory
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(client.calls), 1)
            doc = json.loads(
                (directory / "frame_00000.json").read_text(encoding="utf-8")
            )
            self.assertEqual(doc["timestamp"], 0)

    def test_force_reanalyses_valid_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0])
            (directory / "frame_00000.json").write_text(
                observation_text(0), encoding="utf-8"
            )
            client = FakeClient(sequential_handler([observation_text(0)]))
            code, out, _ = run_batch_capture(
                limit=1, force=True, client=client, directory=directory
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(client.calls), 1)
            self.assertIn("0 skipped", out)

    def test_missing_api_key_without_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0])
            with patch.dict(os.environ, {"BASETEN_API_KEY": ""}):
                with self.assertRaises(RuntimeError):
                    quiet_call(run_vision_batch, limit=1, directory=directory)

    def test_all_skipped_needs_no_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0])
            (directory / "frame_00000.json").write_text(
                observation_text(0), encoding="utf-8"
            )
            with patch.dict(os.environ, {"BASETEN_API_KEY": ""}):
                code, out, _ = run_batch_capture(limit=1, directory=directory)
            self.assertEqual(code, 0)
            self.assertIn("1 skipped", out)

    def test_corrupt_frame_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            make_frames(directory, [0])
            (directory / "frame_00003.jpg").write_bytes(b"junk" * 100)
            client = FakeClient(sequential_handler([observation_text(0)]))
            code, out, err = run_batch_capture(
                limit=5, client=client, directory=directory
            )
            self.assertEqual(code, 1)
            self.assertEqual(len(client.calls), 1)
            self.assertFalse((directory / "frame_00003.json").exists())
            self.assertIn("Error [frame_00003.jpg]", err)
            self.assertIn("partial failure", out)


class SingleFrameCompatibilityTests(unittest.TestCase):
    def test_success_writes_frame_00000_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = write_jpeg(Path(tmp) / "frame_00000.jpg")
            client = FakeClient(sequential_handler([observation_text(0)]))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                run_vision_test(client=client, frame_path=frame)
            self.assertEqual(err.getvalue(), "")
            self.assertIn('"schema_version": "observation-v1"', out.getvalue())
            self.assertIn("Saved validated observation: ", out.getvalue())
            doc = json.loads(
                (Path(tmp) / "frame_00000.json").read_text(encoding="utf-8")
            )
            self.assertEqual(doc["timestamp"], 0)

    def test_api_error_exits_1_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = write_jpeg(Path(tmp) / "frame_00000.jpg")
            client = FakeClient(scripted_handler([api_error("boom")]))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    run_vision_test(client=client, frame_path=frame)
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("Baseten API", err.getvalue())
            self.assertFalse((Path(tmp) / "frame_00000.json").exists())

    def test_invalid_observation_exits_1_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = write_jpeg(Path(tmp) / "frame_00000.jpg")
            client = FakeClient(sequential_handler(["not json"]))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    run_vision_test(client=client, frame_path=frame)
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("Invalid observation", err.getvalue())
            self.assertFalse((Path(tmp) / "frame_00000.json").exists())

    def test_missing_key_without_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = write_jpeg(Path(tmp) / "frame_00000.jpg")
            with patch.dict(os.environ, {"BASETEN_API_KEY": ""}):
                with self.assertRaises(RuntimeError):
                    quiet_call(run_vision_test, frame_path=frame)

    def test_unparseable_frame_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = write_jpeg(Path(tmp) / "frame_xyz.jpg")
            client = FakeClient(sequential_handler([observation_text(0)]))
            with self.assertRaises(RuntimeError):
                quiet_call(run_vision_test, client=client, frame_path=frame)


if __name__ == "__main__":
    unittest.main()
