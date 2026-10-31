"""Tests for the Electron worker protocol (python/worker.py).

Offline only: no Baseten calls. Subprocess runs strip BASETEN_API_KEY
from the environment so a paid call is impossible even by accident.
Run natively:
    .venv/bin/python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import cv2
import numpy as np

import worker
from worker import emit, validate_video

WORKER_PATH = Path(worker.__file__)


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


def seed_observations(obs_dir, timestamps=(0, 3, 6)):
    obs_dir = Path(obs_dir)
    obs_dir.mkdir(parents=True, exist_ok=True)
    for timestamp in timestamps:
        doc = {
            "schema_version": "observation-v1",
            "timestamp": timestamp,
            "observations": [
                {
                    "category": "environment",
                    "description": "Arid desert mesas under bright light.",
                    "confidence": "high",
                    "evidence": "Fixture evidence.",
                }
            ],
            "uncertainties": [],
        }
        (obs_dir / f"frame_{timestamp:05d}.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )
    return obs_dir


def offline_env():
    env = dict(os.environ)
    env.pop("BASETEN_API_KEY", None)
    return env


def run_worker(*argv, env=None):
    return subprocess.run(
        [sys.executable, str(WORKER_PATH), *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        text=True,
        env=offline_env() if env is None else env,
    )


def parse_stdout_lines(proc):
    lines = proc.stdout.splitlines()
    assert lines, "worker printed nothing on stdout"
    return [json.loads(line) for line in lines]


class ValidateVideoTests(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            problem = validate_video(Path(tmp) / "nope.mp4")
        self.assertIsNotNone(problem)
        self.assertIn("not found", problem)

    def test_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            problem = validate_video(Path(tmp))
        self.assertIsNotNone(problem)

    def test_unsupported_extension_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "clip.txt"
            candidate.write_text("not a video", encoding="utf-8")
            problem = validate_video(candidate)
        self.assertIsNotNone(problem)
        self.assertIn(".txt", problem)

    def test_supported_extensions_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for suffix in worker.SUPPORTED_VIDEO_EXTENSIONS:
                candidate = Path(tmp) / f"clip{suffix}"
                candidate.write_bytes(b"\x00")
                self.assertIsNone(
                    validate_video(candidate), msg=f"extension {suffix}"
                )


class EmitTests(unittest.TestCase):
    def test_emit_is_single_json_line(self):
        out, saved = io.StringIO(), worker.REAL_STDOUT
        worker.REAL_STDOUT = out
        try:
            emit({"type": "progress", "stage": "frame_extraction"})
        finally:
            worker.REAL_STDOUT = saved
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertEqual(
            json.loads(out.getvalue()),
            {"type": "progress", "stage": "frame_extraction"},
        )


class WorkerCliTests(unittest.TestCase):
    def test_missing_video_argument_exits_2(self):
        with self.assertRaises(SystemExit) as raised:
            worker.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_missing_file_reports_error_json_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_worker(str(Path(tmp) / "nope.mp4"))
        self.assertEqual(proc.returncode, 2)
        messages = parse_stdout_lines(proc)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "error")
        self.assertEqual(messages[0]["stage"], "validation")

    def test_unsupported_extension_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "clip.txt"
            candidate.write_text("not a video", encoding="utf-8")
            proc = run_worker(str(candidate))
        self.assertEqual(proc.returncode, 2)
        messages = parse_stdout_lines(proc)
        self.assertEqual(messages[0]["type"], "error")

    def test_missing_key_fails_fast_without_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            proc = run_worker(str(video), "--output-dir", str(out))
        self.assertEqual(proc.returncode, 1)
        messages = parse_stdout_lines(proc)
        self.assertEqual(messages[0]["type"], "error")
        self.assertEqual(messages[0]["stage"], "vision_analysis")
        self.assertIn("BASETEN_API_KEY", messages[0]["message"])
        # Fail-fast: no frames extracted, no output tree created.
        self.assertFalse((out / "frames").exists())

    def test_missing_key_blocks_opt_in_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            seed_observations(root / "out" / "observations")
            proc = run_worker(
                str(video),
                "--output-dir",
                str(root / "out"),
                "--skip-vision",
                "--with-script",
            )
        self.assertEqual(proc.returncode, 1)
        messages = parse_stdout_lines(proc)
        self.assertEqual(messages[0]["type"], "error")
        self.assertEqual(messages[0]["stage"], "script")

    def test_never_prints_secret_values(self):
        sentinel = "sk-test-sentinel-that-must-never-appear"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            seed_observations(root / "out" / "observations")
            env = offline_env()
            env["BASETEN_API_KEY"] = sentinel
            proc = run_worker(
                str(video),
                "--output-dir",
                str(root / "out"),
                "--skip-vision",
                env=env,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(sentinel, proc.stdout)
        self.assertNotIn(sentinel, proc.stderr)


class WorkerOfflineRunTests(unittest.TestCase):
    def test_offline_run_reports_pure_ndjson_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            seed_observations(out / "observations")
            proc = run_worker(
                str(video), "--output-dir", str(out), "--skip-vision"
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
            messages = parse_stdout_lines(proc)
            # Every stdout line is a protocol message with a type.
            for message in messages:
                self.assertIn("type", message)
            self.assertEqual(messages[0]["type"], "started")
            self.assertEqual(messages[-1]["type"], "result")
            data = messages[-1]["data"]
            for message in messages:
                if message["type"] in ("progress", "stage_completed"):
                    self.assertEqual(message["total"], 5)
                    self.assertLessEqual(message["completed"], 5)
            self.assertEqual(data["video"], str(video))
            self.assertEqual(data["output_dir"], str(out))
            self.assertEqual(data["frames"], 3)
            self.assertEqual(data["observations"], 3)
            self.assertEqual(data["script"], "skipped")
            self.assertEqual(
                data["stages"],
                [
                    "frame_extraction",
                    "state_rebuild",
                    "event_detection",
                    "commentary",
                ],
            )
            self.assertEqual(data["skipped"], ["vision_analysis"])
            self.assertEqual(data["mode"], "full")
            completed = [
                message
                for message in messages
                if message["type"] == "stage_completed"
            ]
            # Every planned stage reports exactly once; completed and
            # skipped partition the stream with no overlap.
            self.assertEqual(
                sorted(message["stage"] for message in completed),
                sorted(data["stages"] + data["skipped"]),
            )
            self.assertEqual(
                set(data["stages"]).intersection(data["skipped"]), set()
            )
            skipped_details = {
                message["stage"]: message["detail"]
                for message in completed
                if message["detail"].get("skipped")
            }
            self.assertEqual(
                skipped_details,
                {
                    "vision_analysis": {
                        "skipped": True,
                        "reason": "option-skipped",
                    }
                },
            )
            # Artifacts referenced in the result actually exist.
            for key in ("state", "events", "commentary"):
                self.assertTrue(
                    Path(data["artifacts"][key]).is_file(), msg=key
                )

    def test_empty_tree_completes_extraction_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            proc = run_worker(
                str(video),
                "--output-dir",
                str(root / "out"),
                "--skip-vision",
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        messages = parse_stdout_lines(proc)
        self.assertEqual(messages[0]["type"], "started")
        self.assertEqual(messages[-1]["type"], "result")
        data = messages[-1]["data"]
        self.assertEqual(data["mode"], "extraction-only")
        self.assertEqual(data["stages"], ["frame_extraction"])
        self.assertEqual(
            data["skipped"],
            [
                "vision_analysis",
                "state_rebuild",
                "event_detection",
                "commentary",
            ],
        )
        self.assertEqual(data["frames"], 3)
        self.assertEqual(data["observations"], 0)
        # No fake artifacts: absent files are null, not paths.
        self.assertIsNone(data["artifacts"]["state"])
        self.assertIsNone(data["artifacts"]["events"])
        self.assertIsNone(data["artifacts"]["commentary"])
        # Monotonic counts that never exceed the plan total.
        seen = 0
        for message in messages:
            if message["type"] in ("progress", "stage_completed"):
                self.assertEqual(message["total"], 5)
                self.assertLessEqual(message["completed"], 5)
                self.assertGreaterEqual(message["completed"], seen)
                seen = message["completed"]
        # Exactly one terminal event, and it is the success.
        terminals = [
            message
            for message in messages
            if message["type"] in ("result", "error")
        ]
        self.assertEqual(len(terminals), 1)

    def test_corrupt_observations_still_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            obs = out / "observations"
            obs.mkdir(parents=True)
            (obs / "frame_00000.json").write_text(
                "{invalid json", encoding="utf-8"
            )
            proc = run_worker(
                str(video), "--output-dir", str(out), "--skip-vision"
            )
        self.assertEqual(proc.returncode, 1)
        messages = parse_stdout_lines(proc)
        errors = [
            message for message in messages if message["type"] == "error"
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["stage"], "state_rebuild")
        self.assertFalse(
            [message for message in messages if message["type"] == "result"]
        )

    def test_plan_stages_is_pure_and_modular(self):
        import types

        def flags(skip_vision, with_script=False):
            return types.SimpleNamespace(
                skip_vision=skip_vision, with_script=with_script
            )

        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            seeded = Path(tmp) / "seeded"
            seed_observations(seeded)
            full = worker.plan_stages(flags(False), seeded)
            self.assertEqual(
                [stage for stage, _ in full],
                [
                    "frame_extraction",
                    "vision_analysis",
                    "state_rebuild",
                    "event_detection",
                    "commentary",
                ],
            )
            self.assertTrue(all(mode == "run" for _, mode in full))
            self.assertEqual(worker.run_mode(full), "full")
            planned = dict(worker.plan_stages(flags(True), seeded))
            self.assertEqual(planned["vision_analysis"], "skip")
            self.assertEqual(planned["state_rebuild"], "run")
            fresh = worker.plan_stages(flags(True), empty)
            self.assertEqual(worker.run_mode(fresh), "extraction-only")
            self.assertEqual(
                [stage for stage, mode in fresh if mode == "skip"],
                [
                    "vision_analysis",
                    "state_rebuild",
                    "event_detection",
                    "commentary",
                ],
            )
            scripted = worker.plan_stages(flags(True, True), seeded)
            self.assertEqual(scripted[-1], ("script", "run"))




class VisionModeTests(unittest.TestCase):
    """Vision-enabled runs: limit plumbing, honest failure, cache reuse.

    Paid API calls are never made here: ``run_vision_batch`` is mocked
    and subprocess runs use the key-stripped offline environment.
    """

    def run_pipeline_quiet(self, args):
        import io
        import json

        buffer = io.StringIO()
        real = worker.REAL_STDOUT
        worker.REAL_STDOUT = buffer
        try:
            code, data = worker.run_pipeline(args)
        finally:
            worker.REAL_STDOUT = real
        messages = [json.loads(line) for line in buffer.getvalue().splitlines()]
        return code, data, messages

    def vision_args(self, video, out, **overrides):
        import types

        kwargs = {
            "video": video,
            "output_dir": out,
            "limit": 10,
            "force": False,
            "jobs": None,
            "skip_vision": False,
            "with_script": False,
        }
        kwargs.update(overrides)
        return types.SimpleNamespace(**kwargs)

    def test_limit_ten_reaches_vision_batch_and_completes_full(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"

            def fake_batch(limit, force, jobs, directory, observations_dir):
                seed_observations(Path(observations_dir))
                return 0

            with mock.patch.object(
                worker.engine, "run_vision_batch", side_effect=fake_batch
            ) as batch:
                code, data, messages = self.run_pipeline_quiet(
                    self.vision_args(str(video), str(out))
                )
        self.assertEqual(code, 0)
        batch.assert_called_once()
        _, kwargs = batch.call_args
        self.assertEqual(kwargs["limit"], 10)
        self.assertFalse(kwargs["force"])
        self.assertEqual(kwargs["jobs"], worker.engine.DEFAULT_VISION_BATCH_JOBS)
        self.assertEqual(data["mode"], "full")
        self.assertEqual(
            data["stages"],
            [
                "frame_extraction",
                "vision_analysis",
                "state_rebuild",
                "event_detection",
                "commentary",
            ],
        )
        self.assertEqual(data["skipped"], [])
        self.assertGreater(data["observations"], 0)
        self.assertIsNotNone(data["artifacts"]["state"])
        self.assertIsNotNone(data["artifacts"]["events"])
        self.assertIsNotNone(data["artifacts"]["commentary"])
        totals = {
            message["total"]
            for message in messages
            if message["type"] in ("progress", "stage_completed")
        }
        self.assertEqual(totals, {5})
        # run_pipeline returns (code, data); the terminal `result`
        # message is emitted by main(). Code 0 plus the full stage
        # list above is this path's success signal.

    def test_vision_failure_fails_run_without_downstream_results(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            with mock.patch.object(
                worker.engine, "run_vision_batch", return_value=1
            ):
                code, data, messages = self.run_pipeline_quiet(
                    self.vision_args(str(video), str(out))
                )
        self.assertEqual(code, 1)
        self.assertEqual(data, {})
        errors = [m for m in messages if m["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["stage"], "vision_analysis")
        self.assertFalse([m for m in messages if m["type"] == "result"])

    def test_seeded_observations_reused_for_full_offline_run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            seed_observations(out / "observations")
            proc = run_worker(
                str(video),
                "--output-dir",
                str(out),
                "--skip-vision",
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        messages = parse_stdout_lines(proc)
        data = messages[-1]["data"]
        self.assertEqual(data["mode"], "full")
        self.assertIn("state_rebuild", data["stages"])
        self.assertIn("event_detection", data["stages"])
        self.assertIn("commentary", data["stages"])
        self.assertEqual(data["skipped"], ["vision_analysis"])

    def test_vision_mode_without_key_fails_fast_before_extraction(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "clip.mp4")
            out = root / "out"
            proc = run_worker(
                str(video),
                "--output-dir",
                str(out),
                "--limit",
                "10",
                env=offline_env(),
            )
        self.assertEqual(proc.returncode, 1)
        messages = parse_stdout_lines(proc)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "error")
        self.assertEqual(messages[0]["stage"], "vision_analysis")
        self.assertIn("BASETEN_API_KEY", messages[0]["message"])
        self.assertFalse((root / "out").exists())

    def test_limit_argument_parses_explicit_ten(self):
        args = worker.build_parser().parse_args(["clip.mp4", "--limit", "10"])
        self.assertEqual(args.limit, 10)
        self.assertFalse(args.skip_vision)

if __name__ == "__main__":
    unittest.main()
