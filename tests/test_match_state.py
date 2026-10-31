"""Offline tests for the rolling match-state tracker (match-state-v1).

No live API calls and no fixture credentials: observations are small
hand-built observation-v1 documents. Run natively from the repository root:
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

from main import (
    MATCH_STATE_SCHEMA_VERSION,
    MATCH_STATE_STATUSES,
    ObservationError,
    apply_observation,
    build_match_state,
    finalize_match_state,
    new_match_state_builder,
    run_state_rebuild,
    validate_match_state,
)


def obs_doc(timestamp, observations, uncertainties=()):
    return {
        "schema_version": "observation-v1",
        "timestamp": timestamp,
        "observations": [
            {
                "category": category,
                "description": description,
                "confidence": confidence,
                "evidence": evidence,
            }
            for category, description, confidence, evidence in observations
        ],
        "uncertainties": list(uncertainties),
    }


HUD_742 = (
    "hud",
    "Top bar shows a timer reading 07:42 next to icons 9 and 36.",
    "high",
    "Timer text 07:42 with clock icon in the top bar.",
)
HUD_615 = (
    "hud",
    "Top bar shows a timer reading 06:15 next to icons 7 and 30.",
    "high",
    "Timer text 06:15 with clock icon in the top bar.",
)
NAMEPLATE = (
    "character",
    "Nameplate CTZ.KLAUS06 visible over the terrain at center.",
    "medium",
    "Yellow text label CTZ.KLAUS06 with a small marker.",
)


def build(*docs):
    """Build state from (timestamp, source, doc) triples."""
    return build_match_state(
        [(doc["timestamp"], f"frame_{doc['timestamp']:05d}.json", doc) for doc in docs]
    )


def capture_rebuild(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_state_rebuild(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


class ValidateMatchStateTests(unittest.TestCase):
    def test_empty_state_valid(self):
        state = {
            "schema_version": "match-state-v1",
            "latest_timestamp": None,
            "sources": [],
            "facts": [],
            "changes": [],
            "conflicts": [],
            "uncertainties": [],
        }
        self.assertEqual(validate_match_state(state)["latest_timestamp"], None)

    def test_built_state_valid(self):
        state = build(
            obs_doc(0, [HUD_742, NAMEPLATE], ["Minimap is unreadable."]),
            obs_doc(48, [HUD_615], ["Minimap is unreadable."]),
        )
        self.assertEqual(validate_match_state(state)["schema_version"], "match-state-v1")
        self.assertEqual(validate_match_state(state)["latest_timestamp"], 48)

    def test_rejections(self):
        good = build(obs_doc(0, [HUD_742]))
        cases = []
        cases.append(("bad version", {**good, "schema_version": "match-state-v0"}))
        cases.append(("extra field", {**good, "reasoning": "x"}))
        cases.append(("missing field", {k: v for k, v in good.items() if k != "facts"}))
        cases.append(("bool latest", {**good, "latest_timestamp": True}))
        cases.append(("bad latest", {**good, "latest_timestamp": "now"}))
        bad_source = dict(good["sources"][0], observations=-1)
        cases.append(("bad source", {**good, "sources": [bad_source]}))
        bad_fact = dict(good["facts"][0], status="proven")
        cases.append(("bad status", {**good, "facts": [bad_fact]}))
        bad_fact = {k: v for k, v in good["facts"][0].items() if k != "history"}
        cases.append(("missing history", {**good, "facts": [bad_fact]}))
        bad_change = dict(good["changes"][0], kind="event")
        cases.append(("bad kind", {**good, "changes": [bad_change]}))
        bad_change = {k: v for k, v in good["changes"][0].items() if k != "fact_id"}
        cases.append(("missing fact_id", {**good, "changes": [bad_change]}))
        cases.append(
            (
                "weak conflict",
                {
                    **good,
                    "conflicts": [
                        {"timestamp": 0, "sources": ["a.json"], "detail": "x"}
                    ],
                },
            )
        )
        cases.append(
            (
                "resolved non-bool",
                {
                    **good,
                    "uncertainties": [
                        {
                            "text": "unsure",
                            "first_seen": 0,
                            "last_seen": 0,
                            "sources": ["a.json"],
                            "resolved": "no",
                        }
                    ],
                },
            )
        )
        cases.append(("non-object", ["not", "a", "dict"]))
        cases.append(("non-dict fact", {**good, "facts": ["hud"]}))
        for label, doc in cases:
            with self.assertRaises(ObservationError, msg=label):
                validate_match_state(doc)


class ApplyObservationTests(unittest.TestCase):
    def test_first_sighting_creates_current_fact(self):
        builder = new_match_state_builder()
        outcome = apply_observation(
            builder, obs_doc(0, [HUD_742], ["Blurry corner."]), "frame_00000.json"
        )
        self.assertEqual(outcome, "applied")
        state = finalize_match_state(builder)
        self.assertEqual(state["latest_timestamp"], 0)
        self.assertEqual(len(state["sources"]), 1)
        self.assertEqual(state["sources"][0]["file"], "frame_00000.json")
        self.assertEqual(len(state["facts"]), 1)
        fact = state["facts"][0]
        self.assertEqual(fact["id"], "f0001")
        self.assertEqual(fact["status"], "current")
        self.assertEqual(fact["first_seen"], 0)
        self.assertEqual(fact["last_seen"], 0)
        self.assertEqual(len(fact["history"]), 1)
        self.assertEqual(
            [change["kind"] for change in state["changes"]], ["new_fact"]
        )
        self.assertEqual(len(state["uncertainties"]), 1)
        uncertainty = state["uncertainties"][0]
        self.assertEqual(uncertainty["text"], "Blurry corner.")
        self.assertFalse(uncertainty["resolved"])

    def test_identical_reapply_is_duplicate(self):
        builder = new_match_state_builder()
        doc = obs_doc(0, [HUD_742])
        self.assertEqual(apply_observation(builder, doc, "frame_00000.json"), "applied")
        self.assertEqual(
            apply_observation(builder, doc, "frame_00000.json"), "duplicate"
        )
        first = finalize_match_state(builder)
        second_builder = new_match_state_builder()
        apply_observation(second_builder, doc, "frame_00000.json")
        apply_observation(second_builder, doc, "frame_00000.json")
        self.assertEqual(finalize_match_state(second_builder), first)

    def test_resighting_advances_and_updates(self):
        builder = new_match_state_builder()
        apply_observation(builder, obs_doc(0, [HUD_742]), "frame_00000.json")
        # Identical re-sighting: history grows, no change entry.
        apply_observation(builder, obs_doc(48, [HUD_742]), "frame_00048.json")
        state = finalize_match_state(builder)
        fact = state["facts"][0]
        self.assertEqual(fact["status"], "current")
        self.assertEqual((fact["first_seen"], fact["last_seen"]), (0, 48))
        self.assertEqual(len(fact["history"]), 2)
        self.assertEqual(
            [change["kind"] for change in state["changes"]], ["new_fact"]
        )
        # Different confidence: a supported update is recorded.
        changed = (
            HUD_742[0],
            HUD_742[1],
            "medium",
            HUD_742[3],
        )
        apply_observation(builder, obs_doc(96, [changed]), "frame_00096.json")
        state = finalize_match_state(builder)
        fact = state["facts"][0]
        self.assertEqual(fact["confidence"], "medium")
        self.assertEqual(fact["last_seen"], 96)
        self.assertEqual(
            [change["kind"] for change in state["changes"]],
            ["new_fact", "update"],
        )

    def test_reworded_claim_is_a_new_fact(self):
        state = build(obs_doc(0, [HUD_742]), obs_doc(48, [HUD_615]))
        self.assertEqual(len(state["facts"]), 2)
        old = next(f for f in state["facts"] if f["first_seen"] == 0)
        new = next(f for f in state["facts"] if f["first_seen"] == 48)
        self.assertEqual(old["status"], "stale")
        self.assertEqual(new["status"], "current")
        self.assertEqual(
            [change["kind"] for change in state["changes"]],
            ["new_fact", "new_fact"],
        )

    def test_divergent_same_timestamp_is_conflict(self):
        builder = new_match_state_builder()
        first = obs_doc(0, [HUD_742])
        second = obs_doc(0, [HUD_615])
        self.assertEqual(
            apply_observation(builder, first, "frame_00000.json"), "applied"
        )
        self.assertEqual(
            apply_observation(builder, second, "frame_00000b.json"), "conflict"
        )
        state = finalize_match_state(builder)
        self.assertEqual(len(state["conflicts"]), 1)
        conflict = state["conflicts"][0]
        self.assertEqual(conflict["timestamp"], 0)
        self.assertEqual(
            conflict["sources"], ["frame_00000.json", "frame_00000b.json"]
        )
        self.assertEqual(
            {fact["status"] for fact in state["facts"]}, {"conflicted"}
        )
        # The conflict is preserved, not silently resolved.
        self.assertEqual(len(state["facts"]), 2)

    def test_same_source_restatement_is_not_a_conflict(self):
        builder = new_match_state_builder()
        self.assertEqual(
            apply_observation(
                builder, obs_doc(0, [HUD_742]), "frame_00000.json"
            ),
            "applied",
        )
        # Same file, changed content: applied as an ordinary update.
        changed = (HUD_742[0], HUD_742[1], "low", HUD_742[3])
        self.assertEqual(
            apply_observation(
                builder, obs_doc(0, [changed]), "frame_00000.json"
            ),
            "applied",
        )
        state = finalize_match_state(builder)
        self.assertEqual(state["conflicts"], [])
        self.assertEqual(
            [change["kind"] for change in state["changes"]],
            ["new_fact", "update"],
        )

    def test_out_of_order_converges(self):
        ordered = build(obs_doc(0, [HUD_742]), obs_doc(48, [HUD_615]))
        builder = new_match_state_builder()
        apply_observation(builder, obs_doc(48, [HUD_615]), "frame_00048.json")
        apply_observation(builder, obs_doc(0, [HUD_742]), "frame_00000.json")
        self.assertEqual(finalize_match_state(builder), ordered)

    def test_deterministic_rebuild(self):
        docs = [
            obs_doc(0, [HUD_742, NAMEPLATE], ["Blurry corner."]),
            obs_doc(48, [HUD_615], ["Blurry corner.", "Names overlap."]),
        ]
        first = json.dumps(build(*docs), indent=2, sort_keys=True)
        second = json.dumps(build(*docs), indent=2, sort_keys=True)
        self.assertEqual(first, second)

    def test_fact_top_level_reflects_latest_sighting(self):
        state = build(obs_doc(0, [HUD_742]), obs_doc(48, [HUD_742]))
        fact = state["facts"][0]
        self.assertEqual(fact["last_source"], "frame_00048.json")
        self.assertEqual(fact["last_seen"], 48)

    def test_unknown_fact_is_absent_not_false(self):
        state = build(obs_doc(0, [HUD_742]))
        descriptions = [fact["description"] for fact in state["facts"]]
        self.assertNotIn("Timer shows 06:15.", descriptions)
        # And a stale fact is still present, marked stale rather than removed.
        state = build(obs_doc(0, [HUD_742]), obs_doc(48, [HUD_615]))
        old = next(f for f in state["facts"] if "07:42" in f["description"])
        self.assertEqual(old["status"], "stale")


class NoInferenceTests(unittest.TestCase):
    """The tracker files claims; it never invents events or meanings."""

    BANNED_WORDS = (
        "eliminat",
        "knock",
        "knockdown",
        "death",
        "wipe",
        "kill",
        "score",
        "movement",
        "intent",
        "outcome",
        "event",
        "proven",
        "confirmed",
    )

    def assert_no_inference(self, state):
        blob = json.dumps(state).lower()
        for word in self.BANNED_WORDS:
            self.assertNotIn(word, blob, msg=word)
        self.assertTrue(
            {change["kind"] for change in state["changes"]}
            <= {"new_fact", "update"}
        )
        self.assertEqual(state["conflicts"], [])
        for fact in state["facts"]:
            self.assertIn(fact["status"], MATCH_STATE_STATUSES)

    def test_missing_nameplate_is_not_elimination(self):
        state = build(
            obs_doc(0, [NAMEPLATE, HUD_742]),
            obs_doc(48, [HUD_615]),
        )
        old = next(f for f in state["facts"] if "CTZ.KLAUS06" in f["description"])
        self.assertEqual(old["status"], "stale")
        self.assert_no_inference(state)

    def test_hud_numbers_gain_no_meaning(self):
        state = build(obs_doc(0, [HUD_742]), obs_doc(48, [HUD_615]))
        hud_facts = [f for f in state["facts"] if f["category"] == "hud"]
        self.assertEqual(len(hud_facts), 2)
        for fact in hud_facts:
            self.assertEqual(set(fact), {
                "id", "category", "description", "confidence", "evidence",
                "first_seen", "last_seen", "last_source", "status", "history",
            })
        self.assert_no_inference(state)

    def test_timer_alone_proves_no_combat(self):
        state = build(obs_doc(0, [HUD_742]), obs_doc(48, [HUD_615]))
        self.assertEqual(
            [f for f in state["facts"] if f["category"] == "combat"], []
        )
        self.assert_no_inference(state)

    def test_confidence_is_a_label_not_proof(self):
        state = build(obs_doc(0, [HUD_742]))
        fact = state["facts"][0]
        self.assertEqual(fact["confidence"], "high")
        self.assertEqual(fact["status"], "current")
        self.assert_no_inference(state)

    def test_similar_names_are_not_merged(self):
        left = (
            "character",
            "Nameplate CTZ.KLAUS06 visible at left.",
            "medium",
            "Yellow label at left.",
        )
        right = (
            "character",
            "Nameplate CTZ.KLAUS07 visible at left.",
            "medium",
            "Yellow label at left.",
        )
        state = build(obs_doc(0, [left, right]))
        self.assertEqual(len(state["facts"]), 2)
        self.assert_no_inference(state)


class StateRebuildRunnerTests(unittest.TestCase):
    def write_obs(self, directory, timestamp):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"frame_{timestamp:05d}.json"
        path.write_text(
            json.dumps(
                obs_doc(
                    timestamp,
                    [HUD_742],
                    ["Blurry corner."] if timestamp == 0 else [],
                )
            ),
            encoding="utf-8",
        )
        return path

    def test_rebuild_writes_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            self.write_obs(obs, 0)
            self.write_obs(obs, 48)
            state_path = root / "state" / "match_state.json"
            code, out, err = capture_rebuild(
                observations_dir=obs,
                state_path=state_path,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 0)
            self.assertEqual(err, "")
            self.assertIn("2 discovered, 2 accepted, 0 skipped, 0 rejected", out)
            self.assertIn(str(state_path), out)
            self.assertIn("State rebuild status: success.", out)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_match_state(state)["latest_timestamp"], 48)
            self.assertEqual(len(state["facts"]), 1)
            self.assertEqual(state["facts"][0]["status"], "current")

    def test_rebuild_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            self.write_obs(obs, 0)
            self.write_obs(obs, 48)
            first_path = root / "first.json"
            second_path = root / "second.json"
            code, _, _ = capture_rebuild(
                observations_dir=obs,
                state_path=first_path,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 0)
            code, _, _ = capture_rebuild(
                observations_dir=obs,
                state_path=second_path,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 0)
            self.assertEqual(
                first_path.read_bytes(), second_path.read_bytes()
            )

    def test_malformed_file_rejected_with_partial_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            self.write_obs(obs, 0)
            (obs / "frame_00048.json").write_text("not json", encoding="utf-8")
            state_path = root / "match_state.json"
            code, out, _ = capture_rebuild(
                observations_dir=obs,
                state_path=state_path,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 1)
            self.assertIn("rejected", out)
            self.assertIn("State rebuild status: partial failure.", out)
            # The valid observation still produced a usable snapshot.
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["latest_timestamp"], 0)

    def test_no_observations_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            obs.mkdir()
            state_path = root / "match_state.json"
            code, _, err = capture_rebuild(
                observations_dir=obs,
                state_path=state_path,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 1)
            self.assertIn("no frame_", err)
            self.assertFalse(state_path.exists())

    def test_empty_dir_suggests_migration_when_legacy_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            obs.mkdir()
            frames = root / "frames"
            frames.mkdir()
            (frames / "frame_00000.json").write_text(
                json.dumps(obs_doc(0, [HUD_742])), encoding="utf-8"
            )
            code, _, err = capture_rebuild(
                observations_dir=obs,
                state_path=root / "match_state.json",
                frames_dir=frames,
            )
            self.assertEqual(code, 1)
            self.assertIn("migrate-observations", err)

    def test_source_files_are_never_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            first = self.write_obs(obs, 0)
            second = self.write_obs(obs, 48)
            before = {
                path.name: path.read_bytes() for path in (first, second)
            }
            code, _, _ = capture_rebuild(
                observations_dir=obs,
                state_path=root / "match_state.json",
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 0)
            after = {path.name: path.read_bytes() for path in (first, second)}
            self.assertEqual(before, after)

    def test_write_failure_leaves_previous_snapshot_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            self.write_obs(obs, 0)
            state_path = root / "match_state.json"
            code, _, _ = capture_rebuild(
                observations_dir=obs,
                state_path=state_path,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 0)
            before = state_path.read_bytes()
            # Point the snapshot at an existing directory: the atomic write
            # must fail without touching the previous valid snapshot.
            blocker = root / "blocker"
            blocker.mkdir()
            code, _, err = capture_rebuild(
                observations_dir=obs,
                state_path=blocker,
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 1)
            self.assertIn("Previous snapshot left intact", err)
            self.assertEqual(state_path.read_bytes(), before)

    def test_timestamp_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            obs.mkdir()
            (obs / "frame_00048.json").write_text(
                json.dumps(obs_doc(0, [HUD_742])), encoding="utf-8"
            )
            code, out, _ = capture_rebuild(
                observations_dir=obs,
                state_path=root / "match_state.json",
                frames_dir=root / "frames",
            )
            self.assertEqual(code, 1)
            self.assertIn("rejected", out)
            self.assertFalse((root / "match_state.json").exists())


if __name__ == "__main__":
    unittest.main()
