"""Tests for the stdin control channel (M4.2-A transport).

Offline only: no Baseten calls. The engine tests inject fake vision
clients (reused from test_adaptive); the subprocess tests shadow the
`openai` package with a local fake so no network call is possible even
by accident. Run natively:
    .venv/Scripts/python -m unittest tests.test_control -v
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import worker
from main import (
    CONTROL_PROTOCOL_VERSION,
    CONTROL_TYPE_STOP,
    MAX_CONTROL_LINE_CHARS,
    ControlListener,
    StopToken,
    detach_control_listener,
    interpret_control_line,
    start_control_listener,
)

STOP_LINE = '{"type":"stop","version":1}'

# Reuse the adaptive fakes (fake vision client, fixture video writer,
# observation builders) rather than duplicating them.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_adaptive import (  # noqa: E402
    FakeChoice,
    FakeClient,
    FakeCompletion,
    FakeMessage,
    FakeRawResponse,
    adaptive_text,
    observation_doc,
    run_adaptive_vision,
    stamp_from_prompt,
    write_test_video,
)

WORKER_PATH = Path(worker.__file__)


class InterpretControlLineTests(unittest.TestCase):
    def test_valid_stop_command(self):
        self.assertEqual(interpret_control_line(STOP_LINE), "stop")
        self.assertEqual(CONTROL_PROTOCOL_VERSION, 1)
        self.assertEqual(CONTROL_TYPE_STOP, "stop")

    def test_valid_stop_with_surrounding_whitespace(self):
        self.assertEqual(interpret_control_line('  {"type":"stop","version":1}\n'), "stop")

    def test_valid_stop_with_extra_fields_is_accepted(self):
        # Forward compatibility: unknown fields on a stop are ignored.
        self.assertEqual(
            interpret_control_line('{"type":"stop","version":1,"note":"x"}'),
            "stop",
        )

    def test_blank_lines_ignored_silently(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(interpret_control_line(""))
            self.assertIsNone(interpret_control_line("   \n"))
        self.assertEqual(err.getvalue(), "")

    def test_malformed_json_rejected(self):
        for bad in ("{nope", "[1, 2", "just words", '{"type":'):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(interpret_control_line(bad), msg=bad)
            self.assertIn("malformed", err.getvalue())

    def test_non_dict_json_rejected(self):
        for bad in ("[1]", '"stop"', "42", "null", "true"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(interpret_control_line(bad), msg=bad)
            self.assertIn("malformed", err.getvalue())

    def test_unknown_command_rejected(self):
        for bad in (
            '{"type":"halt","version":1}',
            '{"type":"STOP","version":1}',
            '{"type":"","version":1}',
            '{"version":1}',
            '{}',
        ):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(interpret_control_line(bad), msg=bad)
            self.assertIn("unknown", err.getvalue())

    def test_unsupported_version_rejected(self):
        for bad in (
            '{"type":"stop","version":0}',
            '{"type":"stop","version":2}',
            '{"type":"stop","version":"1"}',
            '{"type":"stop","version":1.0}',
            '{"type":"stop","version":true}',
            '{"type":"stop","version":null}',
            '{"type":"stop"}',
        ):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(interpret_control_line(bad), msg=bad)
            self.assertIn("unsupported", err.getvalue())

    def test_non_string_input_never_raises(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for bad in (None, 42, b'{"type":"stop","version":1}', ["stop"]):
                self.assertIsNone(interpret_control_line(bad))

    def test_overlong_line_rejected_without_echo(self):
        payload = '{"type":"stop","version":1,"pad":"' + "x" * MAX_CONTROL_LINE_CHARS + '"}'
        self.assertGreater(len(payload), MAX_CONTROL_LINE_CHARS)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(interpret_control_line(payload))
        logged = err.getvalue()
        self.assertIn("over-long", logged)
        self.assertNotIn("x" * 32, logged)

    def test_rejections_never_echo_payloads(self):
        secrets = ["sk-test-secret-payload", "A" * 500]
        lines = [
            "{bad json " + secrets[0],
            '{"type":"' + secrets[0] + '","version":1}',
            '{"type":"stop","version":"' + secrets[1] + '"}',
        ]
        for line in lines:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(interpret_control_line(line))
            logged = err.getvalue()
            self.assertNotIn(secrets[0], logged)
            self.assertNotIn(secrets[1], logged)


class ControlListenerTests(unittest.TestCase):
    def pump(self, text):
        token = StopToken()
        listener = start_control_listener(token, stream=io.StringIO(text))
        listener.thread.join(timeout=10)
        self.assertFalse(listener.thread.is_alive())
        return token, listener

    def test_stop_line_sets_token(self):
        token, _ = self.pump(STOP_LINE + "\n")
        self.assertTrue(token.is_stopping())

    def test_multiple_commands_in_one_stream(self):
        token, _ = self.pump(
            '{"type":"halt","version":1}\n'
            + STOP_LINE
            + '\n{broken\n'
            + STOP_LINE
            + '\n'
        )
        self.assertTrue(token.is_stopping())

    def test_unknown_only_stream_leaves_token_clear(self):
        token, _ = self.pump('{"type":"halt","version":1}\n{"nope"\n')
        self.assertFalse(token.is_stopping())

    def test_empty_stream_ends_quietly(self):
        token, listener = self.pump("")
        self.assertFalse(token.is_stopping())
        self.assertIsNotNone(listener.thread)

    def test_repeated_stop_commands_are_idempotent(self):
        token, _ = self.pump((STOP_LINE + "\n") * 5)
        self.assertTrue(token.is_stopping())

    def test_split_chunks_reassembled(self):
        read_fd, write_fd = os.pipe()
        token = StopToken()
        with os.fdopen(read_fd, "r") as stream:
            listener = start_control_listener(token, stream=stream)
            half = len(STOP_LINE) // 2
            os.write(write_fd, STOP_LINE[:half].encode("utf-8"))
            time.sleep(0.2)
            self.assertFalse(token.is_stopping())
            os.write(write_fd, (STOP_LINE[half:] + "\n").encode("utf-8"))
            os.close(write_fd)
            listener.thread.join(timeout=10)
        self.assertFalse(listener.thread.is_alive())
        self.assertTrue(token.is_stopping())

    def test_detach_makes_late_commands_noops(self):
        read_fd, write_fd = os.pipe()
        token = StopToken()
        with os.fdopen(read_fd, "r") as stream:
            listener = start_control_listener(token, stream=stream)
            detach_control_listener(listener)
            # A command arriving after detach is read but affects nothing.
            os.write(write_fd, (STOP_LINE + "\n").encode("utf-8"))
            os.close(write_fd)
            listener.thread.join(timeout=10)
        self.assertFalse(listener.thread.is_alive())
        self.assertFalse(token.is_stopping())
        detach_control_listener(None)  # None-safe

    def test_broken_stream_does_not_raise(self):
        class BrokenStream:
            def __iter__(self):
                return self

            def __next__(self):
                raise OSError("stdin gone")

        token = StopToken()
        listener = start_control_listener(token, stream=BrokenStream())
        listener.thread.join(timeout=10)
        self.assertFalse(listener.thread.is_alive())
        self.assertFalse(token.is_stopping())

    def test_missing_stdin_returns_none(self):
        with mock.patch.object(sys, "stdin", None):
            self.assertIsNone(start_control_listener(StopToken()))
        detach_control_listener(None)  # None-safe

    def test_double_start_is_safe(self):
        token = StopToken()
        listener = start_control_listener(token, stream=io.StringIO(""))
        first = listener.thread
        self.assertIs(listener.start(), listener)
        self.assertIs(listener.thread, first)
        first.join(timeout=10)


class ControlLifecycleTests(unittest.TestCase):
    """Drive the real adaptive loop with stops arriving as stdin bytes."""

    def run_with_control_stream(self, stream, video_seconds=7, fake=None,
                                max_frames=10):
        """Replicate the worker wiring: listener + token + real loop."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4", seconds=video_seconds)
            obs_dir = root / "out" / "observations"
            token = StopToken()
            listener = start_control_listener(token, stream=stream)
            try:
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out):
                    with contextlib.redirect_stderr(err):
                        result = run_adaptive_vision(
                            video_path=video,
                            observations_dir=obs_dir,
                            max_frames=max_frames,
                            client=fake,
                            stop_token=token,
                        )
            finally:
                detach_control_listener(listener)
            frame_names = sorted(p.name for p in obs_dir.glob("frame_*.json"))
            return result, frame_names

    def echo_client(self, speeds=("NORMAL",)):
        def handle(kwargs, index):
            timestamp = stamp_from_prompt(kwargs)
            speed = speeds[index] if index < len(speeds) else speeds[-1]
            return FakeRawResponse(
                FakeCompletion(
                    [
                        FakeChoice(
                            FakeMessage(adaptive_text(timestamp, speed, "Test."))
                        )
                    ]
                )
            )

        return FakeClient(handle)

    def test_stop_before_first_frame_via_prebuffered_stdin(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, (STOP_LINE + "\n").encode("utf-8"))
        os.close(write_fd)
        with os.fdopen(read_fd, "r") as stream:
            result, frame_names = self.run_with_control_stream(
                stream, fake=self.echo_client(), max_frames=5
            )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["termination"], "stop-requested")
        self.assertEqual(result["observations"], 0)
        self.assertEqual(result["requests"], 0)
        self.assertEqual(result["requested_timestamps"], [])
        self.assertEqual(frame_names, [])

    def test_stop_during_in_flight_request_persists_response(self):
        read_fd, write_fd = os.pipe()
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def handle(kwargs, index):
            calls.append(index)
            timestamp = stamp_from_prompt(kwargs)
            entered.set()
            self.assertTrue(release.wait(timeout=30))
            return FakeRawResponse(
                FakeCompletion(
                    [
                        FakeChoice(
                            FakeMessage(adaptive_text(timestamp, "HIGH", "Test."))
                        )
                    ]
                )
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            obs_dir = root / "out" / "observations"
            token = StopToken()
            with os.fdopen(read_fd, "r") as stream:
                listener = start_control_listener(token, stream=stream)
                try:
                    result_holder = {}
                    thread = threading.Thread(
                        target=lambda: result_holder.update(
                            result=run_adaptive_vision(
                                video_path=video,
                                observations_dir=obs_dir,
                                max_frames=5,
                                client=FakeClient(handle),
                                stop_token=token,
                            )
                        )
                    )
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out):
                        with contextlib.redirect_stderr(err):
                            thread.start()
                            try:
                                self.assertTrue(entered.wait(timeout=30))
                                # Stop arrives as stdin bytes mid-request.
                                os.write(
                                    write_fd,
                                    (STOP_LINE + "\n").encode("utf-8"),
                                )
                                release.set()
                            finally:
                                try:
                                    os.close(write_fd)
                                except OSError:
                                    pass
                            thread.join(timeout=60)
                            # Read while the fixture tree still exists.
                            observed = json.loads(
                                (obs_dir / "frame_00000.json").read_text(
                                    encoding="utf-8"
                                )
                            )
                            manifest_status = json.loads(
                                (obs_dir / "adaptive_run.json").read_text(
                                    encoding="utf-8"
                                )
                            )["status"]
                finally:
                    detach_control_listener(listener)
        self.assertFalse(thread.is_alive())
        result = result_holder["result"]
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["termination"], "stop-requested")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["observations"], 1)
        # The in-flight response validated and persisted before halting.
        self.assertEqual(observed["timestamp"], 0)
        self.assertEqual(manifest_status, "stopped")

    def test_closed_channel_does_not_affect_run(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)  # Immediate EOF: channel closed, no commands.
        with os.fdopen(read_fd, "r") as stream:
            result, frame_names = self.run_with_control_stream(
                stream, video_seconds=7, fake=self.echo_client(), max_frames=10
            )
        self.assertEqual(len(frame_names), 3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["termination"], "video-end")
        self.assertEqual(result["requested_timestamps"], [0, 3, 6])
        self.assertEqual(result["requests"], 3)


OPENAI_SHIM = '''
"""Process-boundary fake shadowing the openai package for stdin tests.

Implements only what the worker's adaptive path touches. Request flow
is synchronized with the test through files (no network, no timing
assumptions): each create() appends to SHIM_COUNT_FILE, touches
SHIM_STARTED_FILE on entry, and blocks until SHIM_RELEASE_FILE exists.
The response echoes the requested timestamp so validation passes.
"""
import json
import os
import re
import time


class APIError(Exception):
    def __init__(self, message="", request=None, body=None):
        super(APIError, self).__init__(message)
        self.message = message
        self.status_code = None
        self.request = request
        self.body = body


def _await_release():
    path = os.environ.get("SHIM_RELEASE_FILE")
    if not path:
        return
    deadline = time.time() + 120.0
    while not os.path.exists(path):
        if time.time() > deadline:
            raise TimeoutError("test release file never arrived")
        time.sleep(0.05)


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, message):
        self.message = message


class _Completion:
    def __init__(self, choices):
        self.choices = choices


class _RawResponse:
    status_code = 200

    def __init__(self, completion):
        self._completion = completion

    def parse(self):
        return self._completion


class _Completions:
    def __init__(self):
        self.with_raw_response = self

    def create(self, **kwargs):
        count_file = os.environ.get("SHIM_COUNT_FILE")
        if count_file:
            with open(count_file, "a", encoding="utf-8") as handle:
                handle.write("x\\n")
        started_file = os.environ.get("SHIM_STARTED_FILE")
        if started_file:
            open(started_file, "a", encoding="utf-8").close()
        _await_release()
        text = kwargs["messages"][0]["content"][0]["text"]
        match = re.search(r"This frame is at ([\\d.]+) seconds", text)
        stamp = float(match.group(1)) if match else 0.0
        timestamp = int(stamp) if float(stamp).is_integer() else float(stamp)
        document = {
            "observation": {
                "schema_version": "observation-v1",
                "timestamp": timestamp,
                "observations": [
                    {
                        "category": "environment",
                        "description": "Fixture backdrop.",
                        "confidence": "high",
                        "evidence": "Shim frame.",
                    }
                ],
                "uncertainties": [],
            },
            "sampling": {"next_sampling_speed": "NORMAL", "reason": "Shim."},
        }
        message = _Message(json.dumps(document))
        return _RawResponse(_Completion([_Choice(message)]))


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class OpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = _Chat()

    def close(self):
        pass
'''


class ControlSubprocessTests(unittest.TestCase):
    """Real worker.py processes driven through stdin (fake provider)."""

    DUMMY_KEY = "test-dummy-key-never-a-secret"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shim_dir = self.root / "shim"
        shim_dir.mkdir()
        (shim_dir / "openai.py").write_text(OPENAI_SHIM, encoding="utf-8")
        self.video = write_test_video(self.root / "clip.mp4", seconds=7)
        self.out_dir = self.root / "out"
        self.started_file = self.root / "started"
        self.release_file = self.root / "release"
        self.count_file = self.root / "count"

    def worker_env(self):
        env = dict(os.environ)
        env["BASETEN_API_KEY"] = self.DUMMY_KEY
        shim = str(self.root / "shim")
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = shim + os.pathsep + existing if existing else shim
        env["SHIM_STARTED_FILE"] = str(self.started_file)
        env["SHIM_RELEASE_FILE"] = str(self.release_file)
        env["SHIM_COUNT_FILE"] = str(self.count_file)
        return env

    def spawn_worker(self, *extra_args):
        return subprocess.Popen(
            [
                sys.executable,
                str(WORKER_PATH),
                str(self.video),
                "--output-dir",
                str(self.out_dir),
                "--adaptive",
                "--adaptive-max-frames",
                "10",
                *extra_args,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.worker_env(),
        )

    def wait_for_file(self, path, timeout=90.0):
        deadline = time.time() + timeout
        while not path.exists():
            if time.time() > deadline:
                self.fail(f"timed out waiting for {path}")
            time.sleep(0.05)

    def finish(self, proc, timeout=180):
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            self.fail(f"worker did not exit; stdout={stdout!r} stderr={stderr!r}")
        return proc.returncode, stdout, stderr

    def assert_stdout_clean(self, stdout):
        lines = [line for line in stdout.splitlines() if line.strip()]
        self.assertTrue(lines, "worker printed nothing on stdout")
        messages = [json.loads(line) for line in lines]
        self.assertNotIn(self.DUMMY_KEY, stdout)
        return messages

    def request_count(self):
        if not self.count_file.exists():
            return 0
        return len(self.count_file.read_text(encoding="utf-8").splitlines())

    def test_stop_during_in_flight_request_end_to_end(self):
        proc = self.spawn_worker()
        try:
            # Deterministic gate: the first vision request has begun.
            self.wait_for_file(self.started_file)
            proc.stdin.write(STOP_LINE + "\n")
            proc.stdin.flush()
            self.release_file.touch()
            code, stdout, stderr = self.finish(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(code, 0)
        self.assertNotIn(self.DUMMY_KEY, stderr)
        messages = self.assert_stdout_clean(stdout)
        stopped = [m for m in messages if m.get("type") == "stopped"]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["data"]["status"], "stopped")
        self.assertFalse([m for m in messages if m.get("type") == "result"])
        self.assertFalse([m for m in messages if m.get("type") == "error"])
        # Exactly one paid-equivalent request; the response persisted.
        self.assertEqual(self.request_count(), 1)
        obs_dir = self.out_dir / "observations"
        doc = json.loads((obs_dir / "frame_00000.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["timestamp"], 0)
        self.assertEqual(doc["schema_version"], "observation-v1")
        manifest = json.loads(
            (obs_dir / "adaptive_run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "stopped")
        self.assertEqual(manifest["requested_timestamps"], [0])

    def test_closed_channel_completes_normally(self):
        proc = self.spawn_worker()
        try:
            self.release_file.touch()
            proc.stdin.close()  # Immediate EOF: no commands, no crash.
            code, stdout, stderr = self.finish(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(code, 0)
        self.assertNotIn(self.DUMMY_KEY, stderr)
        messages = self.assert_stdout_clean(stdout)
        results = [m for m in messages if m.get("type") == "result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(self.request_count(), 3)
        manifest = json.loads(
            (self.out_dir / "observations" / "adaptive_run.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["termination"], "video-end")

    def test_malformed_stdin_does_not_break_run(self):
        proc = self.spawn_worker()
        try:
            self.release_file.touch()
            proc.stdin.write("{broken json\n")
            proc.stdin.write('{"type":"self-destruct","version":1}\n')
            proc.stdin.write('{"type":"stop","version":999}\n')
            proc.stdin.flush()
            proc.stdin.close()
            code, stdout, stderr = self.finish(proc)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(code, 0)
        self.assertNotIn(self.DUMMY_KEY, stderr)
        self.assertIn("Control: ignoring", stderr)
        messages = self.assert_stdout_clean(stdout)
        self.assertEqual(len([m for m in messages if m.get("type") == "result"]), 1)
        manifest = json.loads(
            (self.out_dir / "observations" / "adaptive_run.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["status"], "completed")
