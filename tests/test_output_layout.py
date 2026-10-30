"""Offline tests for the output layout split and legacy migration.

Frames stay in ``frames/``; generated JSON moves to ``observations/``.
No live API calls; fixture JPEGs are tiny generated images and the
extraction test uses a tiny synthetic MP4. Run natively from the repository root:
    .venv/bin/python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from unittest.mock import patch

import cv2
import numpy as np

import main as app
from main import (
    OBSERVATIONS_DIR,
    discover_observation_files,
    find_legacy_observations,
    main as run_extraction,
    migrate_observations,
    observation_path_for_frame,
    parse_observation_timestamp,
    run_migration,
    warn_about_legacy_observations,
)


def obs_text(timestamp, description="Visible HUD elements."):
    return json.dumps(
        {
            "schema_version": "observation-v1",
            "timestamp": timestamp,
            "observations": [
                {
                    "category": "hud",
                    "description": description,
                    "confidence": "high",
                    "evidence": "Test fixture frame.",
                }
            ],
            "uncertainties": [],
        }
    )


def write_jpeg(path):
    path = Path(path)
    frame = np.zeros((16, 12, 3), dtype=np.uint8)
    frame[:, :, 1] = 200
    if not cv2.imwrite(str(path), frame):
        raise AssertionError(f"could not write fixture {path}")
    return path


def write_test_video(path, seconds=5, fps=10):
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


def quiet_call(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)


def capture_call(func, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = func(*args, **kwargs)
    return result, out.getvalue(), err.getvalue()


class ParseObservationTimestampTests(unittest.TestCase):
    def test_valid_names(self):
        self.assertEqual(parse_observation_timestamp("frame_00000.json"), 0)
        self.assertEqual(parse_observation_timestamp("frame_00195.json"), 195)

    def test_invalid_names(self):
        for name in (
            "frame_00000.jpg",
            "match_state.json",
            "notes.json",
            "frame_abc.json",
            "frame_.json",
            "frame_12.png",
            "",
        ):
            self.assertIsNone(parse_observation_timestamp(name), msg=name)


class DiscoverObservationFilesTests(unittest.TestCase):
    def test_numeric_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ("frame_00010.json", "frame_00002.json", "frame_00003.json"):
                (directory / name).write_text(obs_text(0), encoding="utf-8")
            found = discover_observation_files(directory)
            self.assertEqual([ts for ts, _ in found], [2, 3, 10])

    def test_ignores_snapshot_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "frame_00003.json").write_text(
                obs_text(3), encoding="utf-8"
            )
            (directory / "match_state.json").write_text("{}", encoding="utf-8")
            (directory / "notes.txt").write_bytes(b"data")
            (directory / "frame_00003.jpg").write_bytes(b"data")
            found = discover_observation_files(directory)
            self.assertEqual([ts for ts, _ in found], [3])

    def test_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                discover_observation_files(Path(tmp) / "nope"), []
            )


class FindLegacyObservationsTests(unittest.TestCase):
    def test_finds_legacy_json_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "frame_00010.json").write_text("{}", encoding="utf-8")
            (directory / "frame_00002.json").write_text("{}", encoding="utf-8")
            (directory / "frame_00002.jpg").write_bytes(b"data")
            (directory / "notes.json").write_text("{}", encoding="utf-8")
            found = find_legacy_observations(directory)
            self.assertEqual(
                [path.name for path in found],
                ["frame_00002.json", "frame_00010.json"],
            )

    def test_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_legacy_observations(Path(tmp) / "nope"), [])


class LegacyWarningTests(unittest.TestCase):
    def test_silent_without_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, err = capture_call(
                warn_about_legacy_observations, Path(tmp)
            )
            self.assertEqual(err, "")

    def test_warns_with_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "frame_00002.json").write_text("{}", encoding="utf-8")
            _, _, err = capture_call(
                warn_about_legacy_observations, directory
            )
            self.assertIn("legacy observation", err)
            self.assertIn("migrate-observations", err)


class MigrationTests(unittest.TestCase):
    def test_moves_valid_file_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            obs = root / "observations"
            frames.mkdir()
            write_jpeg(frames / "frame_00006.jpg")
            body = obs_text(6).encode("utf-8")
            (frames / "frame_00006.json").write_bytes(body)
            code, _, _ = capture_call(
                run_migration, frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(code, 0)
            self.assertEqual((obs / "frame_00006.json").read_bytes(), body)
            self.assertFalse((frames / "frame_00006.json").exists())
            # The JPEG and unrelated files are preserved.
            self.assertTrue((frames / "frame_00006.jpg").is_file())

    def test_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            obs = root / "observations"
            frames.mkdir()
            (frames / "frame_00006.json").write_text(obs_text(6), encoding="utf-8")
            first = migrate_observations(
                frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(first["moved"], ["frame_00006.json"])
            second = migrate_observations(
                frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(
                second,
                {
                    "moved": [],
                    "removed_duplicates": [],
                    "conflicts": [],
                    "invalid": [],
                    "errors": [],
                },
            )
            code, _, _ = capture_call(
                run_migration, frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(code, 0)

    def test_identical_duplicate_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            obs = root / "observations"
            frames.mkdir()
            obs.mkdir()
            body = obs_text(6)
            (frames / "frame_00006.json").write_text(body, encoding="utf-8")
            (obs / "frame_00006.json").write_text(body, encoding="utf-8")
            summary = migrate_observations(
                frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(summary["removed_duplicates"], ["frame_00006.json"])
            self.assertFalse((frames / "frame_00006.json").exists())
            self.assertEqual(
                (obs / "frame_00006.json").read_text(encoding="utf-8"), body
            )

    def test_conflicting_destination_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            obs = root / "observations"
            frames.mkdir()
            obs.mkdir()
            legacy_body = obs_text(6, "Legacy reading.")
            stored_body = obs_text(6, "Stored reading.")
            (frames / "frame_00006.json").write_text(legacy_body, encoding="utf-8")
            (obs / "frame_00006.json").write_text(stored_body, encoding="utf-8")
            summary = migrate_observations(
                frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(summary["conflicts"], ["frame_00006.json"])
            # Neither side is touched.
            self.assertEqual(
                (frames / "frame_00006.json").read_text(encoding="utf-8"),
                legacy_body,
            )
            self.assertEqual(
                (obs / "frame_00006.json").read_text(encoding="utf-8"), stored_body
            )
            code, _, err = capture_call(
                run_migration, frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(code, 1)
            self.assertIn("conflict", err)

    def test_invalid_file_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            obs = root / "observations"
            frames.mkdir()
            (frames / "frame_00006.json").write_text("not json", encoding="utf-8")
            (frames / "frame_00009.json").write_text(
                obs_text(99), encoding="utf-8"
            )
            summary = migrate_observations(
                frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(
                sorted(summary["invalid"]),
                ["frame_00006.json", "frame_00009.json"],
            )
            self.assertTrue((frames / "frame_00006.json").is_file())
            self.assertTrue((frames / "frame_00009.json").is_file())
            self.assertFalse(obs.exists())
            code, _, _ = capture_call(
                run_migration, frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(code, 1)

    def test_unrelated_files_and_jpegs_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            obs = root / "observations"
            frames.mkdir()
            write_jpeg(frames / "frame_00006.jpg")
            (frames / "notes.json").write_text('{"keep": true}', encoding="utf-8")
            (frames / "frame_00006.json").write_text(obs_text(6), encoding="utf-8")
            code, _, _ = capture_call(
                run_migration, frames_dir=frames, observations_dir=obs
            )
            self.assertEqual(code, 0)
            self.assertTrue((frames / "frame_00006.jpg").is_file())
            self.assertEqual(
                (frames / "notes.json").read_text(encoding="utf-8"), '{"keep": true}'
            )
            self.assertEqual(
                sorted(path.name for path in frames.iterdir()),
                ["frame_00006.jpg", "notes.json"],
            )


class ExtractionRegressionTests(unittest.TestCase):
    def test_extraction_writes_jpegs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = write_test_video(root / "match.mp4", seconds=5, fps=10)
            frames = root / "frames"
            with patch.object(app, "VIDEO_PATH", video), patch.object(
                app, "FRAMES_DIR", frames
            ):
                quiet_call(run_extraction)
            names = sorted(path.name for path in frames.iterdir())
            self.assertEqual(names, ["frame_00000.jpg", "frame_00003.jpg"])
            for name in names:
                image = cv2.imread(str(frames / name), cv2.IMREAD_UNCHANGED)
                self.assertIsNotNone(image)
                self.assertTrue(image.size > 0)
            # Extraction never creates JSON or migrates anything.
            self.assertEqual(list(frames.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
