"""Offline tests for evidence-grounded event detection (events-v1).

No live API calls and no fixture credentials: observations are small
hand-built observation-v1 documents, and match state is built locally with
build_match_state. Run inside Docker:
    docker compose run --rm -v "$PWD/tests:/app/tests:ro" \\
        commentator python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from main import (
    EVENT_RULES,
    EVENT_SCHEMA_VERSION,
    MATCH_STATE_FILENAME,
    ObservationError,
    build_match_state,
    detect_events,
    run_detect_events,
    run_state_rebuild,
    validate_events,
    validate_match_state,
    validate_observation,
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


KILL_FEED = (
    "hud",
    "Top-right kill feed lines show recent eliminations.",
    "high",
    "Feed lines read 'RRQ.JEEK eliminated OG.IQOOPAWDI' and "
    "'SBUL.BUNNY eliminated TAG.VIPER'.",
)
KILL_FEED_UNREADABLE = (
    "hud",
    "Top-right kill feed lines are visible but too small to transcribe.",
    "high",
    "Tiny feed rows at the screen edge; no entry is legible.",
)
TIMER_742 = (
    "hud",
    "Top bar shows a timer reading 07:42.",
    "high",
    "Timer text 07:42 with clock icon.",
)
TIMER_1057 = (
    "hud",
    "Top bar shows a timer reading 10:57.",
    "high",
    "Timer text 10:57 with clock icon.",
)
COUNTERS_9_36 = (
    "hud",
    "Top bar shows counters 9 and 36 of unknown meaning.",
    "high",
    "Numbers 9 and 36 next to flag and silhouette icons.",
)
COUNTERS_9_31 = (
    "hud",
    "Top bar shows counters 9 and 31 of unknown meaning.",
    "high",
    "Numbers 9 and 31 next to flag and silhouette icons.",
)
NAMEPLATE = (
    "character",
    "Nameplate CTZ.KLAUS06 visible over the terrain.",
    "medium",
    "Yellow text label CTZ.KLAUS06 with a small marker.",
)
BANNER = (
    "hud",
    "Red banner stating Safe Zone is now shrinking.",
    "high",
    "Red banner text at the screen edge.",
)
ELIM_AG_4 = (
    "hud",
    "Bottom-left team panel for team AG showing members AG.BAROS and "
    "AG.SAIFA, plus header 'Elim 4'.",
    "high",
    "Team AG panel header Elim 4.",
)
ELIM_AG_5 = (
    "hud",
    "Bottom-left team panel for team AG showing members AG.BAROS and "
    "AG.NAMELESS, plus header 'Elim 5'.",
    "high",
    "Team AG panel header Elim 5.",
)
ELIM_SBUL_5 = (
    "hud",
    "Bottom-left team panel for team SBUL showing member SBUL.BUNNY, "
    "plus header 'Elim 5'.",
    "high",
    "Team SBUL panel header Elim 5.",
)
ELIM_PERSONAL = (
    "hud",
    "Bottom-center status shows player names 'BOMBER' and 'AG.BAROS Elim 1'.",
    "high",
    "Status bar text AG.BAROS Elim 1.",
)


def triples(*docs):
    return [
        (doc["timestamp"], f"frame_{doc['timestamp']:05d}.json", doc)
        for doc in docs
    ]


def build_state(*docs):
    return build_match_state(triples(*docs))


def detect(*docs):
    state = build_state(*docs)
    document, _ = detect_events(triples(*docs), state)
    return document


def capture_detect(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_detect_events(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


def write_obs(directory, doc):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"frame_{doc['timestamp']:05d}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class ValidateEventsTests(unittest.TestCase):
    def test_valid_document_passes(self):
        document = detect(obs_doc(48, [TIMER_742]))
        self.assertEqual(
            validate_events(document)["schema_version"], EVENT_SCHEMA_VERSION
        )

    def test_rejections(self):
        good = detect(obs_doc(48, [TIMER_742]))
        event = good["events"][0] if good["events"] else None
        _ = event
        base = {
            "schema_version": "events-v1",
            "generated_from": {
                "observations": ["frame_00048.json"],
                "match_state": MATCH_STATE_FILENAME,
            },
            "rules_evaluated": ["kill_feed_entry"],
            "contested_timestamps": [],
            "events": [],
        }
        full_event = {
            "schema_version": "events-v1",
            "id": "evt-0123456789ab",
            "type": "kill_feed_entry",
            "timestamp": 48,
            "status": "candidate",
            "rule": "kill_feed_entry/v1",
            "sources": [
                {"timestamp": 48, "file": "frame_00048.json", "fact_id": "f0001"}
            ],
            "evidence": ["feed mention", "transcribed line"],
            "confidence": "high",
            "detail": "Why the rule fired.",
        }
        cases = [
            ("bad version", {**base, "schema_version": "events-v0"}),
            ("extra field", {**base, "reasoning": "x"}),
            ("missing field", {k: v for k, v in base.items() if k != "events"}),
            ("empty observations", {**base, "generated_from": {
                "observations": [], "match_state": "match_state.json"}}),
            ("bad status", {**base, "events": [
                {**full_event, "status": "confirmed"}]}),
            ("missing evidence", {**base, "events": [
                {k: v for k, v in full_event.items() if k != "evidence"}]}),
            ("empty evidence", {**base, "events": [
                {**full_event, "evidence": []}]}),
            ("bad confidence", {**base, "events": [
                {**full_event, "confidence": "certain"}]}),
            ("missing fact_id", {**base, "events": [{**full_event, "sources": [
                {"timestamp": 48, "file": "frame_00048.json"}]}]}),
            ("empty sources", {**base, "events": [
                {**full_event, "sources": []}]}),
            ("non-object", ["not", "a", "dict"]),
            ("non-object event", {**base, "events": ["kill"]}),
        ]
        for label, doc in cases:
            with self.assertRaises(ObservationError, msg=label):
                validate_events(doc)


class KillFeedRuleTests(unittest.TestCase):
    def test_supported_feed_entry_emitted(self):
        document = detect(obs_doc(48, [KILL_FEED]))
        self.assertEqual(len(document["events"]), 1)
        event = document["events"][0]
        self.assertTrue(event["id"].startswith("evt-"))
        self.assertEqual(event["type"], "kill_feed_entry")
        self.assertEqual(event["timestamp"], 48)
        self.assertEqual(event["status"], "candidate")
        self.assertEqual(event["rule"], "kill_feed_entry/v1")
        self.assertEqual(
            event["sources"],
            [{"timestamp": 48, "file": "frame_00048.json", "fact_id": "f0001"}],
        )
        self.assertEqual(
            event["evidence"], [KILL_FEED[1], KILL_FEED[3]]
        )
        self.assertEqual(event["confidence"], "high")
        self.assertIn("not established", event["detail"])
        # Deterministic id: same inputs, same id.
        again = detect(obs_doc(48, [KILL_FEED]))
        self.assertEqual(again["events"][0]["id"], event["id"])

    def test_untranscribed_feed_suppressed(self):
        document = detect(obs_doc(48, [KILL_FEED_UNREADABLE]))
        self.assertEqual(document["events"], [])

    def test_low_confidence_feed_suppressed(self):
        weak = ("hud", KILL_FEED[1], "low", KILL_FEED[3])
        document = detect(obs_doc(48, [weak]))
        self.assertEqual(document["events"], [])

    def test_non_hud_feed_mention_suppressed(self):
        combat = ("combat", "No kill feed activity is visible.", "high", "Clear sky.")
        document = detect(obs_doc(48, [combat]))
        self.assertEqual(document["events"], [])

    def test_names_without_feed_mention_suppressed(self):
        plain = (
            "hud",
            "Top bar shows counters and nameplates.",
            "high",
            "Labels RRQ.JEEK and OG.IQOOPAWDI are legible.",
        )
        document = detect(obs_doc(48, [plain]))
        self.assertEqual(document["events"], [])

    def test_repeated_claim_emits_once(self):
        document = detect(obs_doc(48, [KILL_FEED, KILL_FEED]))
        self.assertEqual(len(document["events"]), 1)


class ElimCountRuleTests(unittest.TestCase):
    def test_supported_increase_emitted(self):
        document = detect(obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_AG_5]))
        self.assertEqual(len(document["events"]), 1)
        event = document["events"][0]
        self.assertEqual(event["type"], "elim_count_increased")
        self.assertEqual(event["timestamp"], 48)
        self.assertEqual(event["status"], "candidate")
        self.assertEqual(
            [entry["file"] for entry in event["sources"]],
            ["frame_00000.json", "frame_00048.json"],
        )
        self.assertEqual(len(event["evidence"]), 2)
        self.assertIn("4->5", event["detail"])
        self.assertIn("AG", event["detail"])
        # Provenance resolves to real state facts.
        state = build_state(obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_AG_5]))
        fact_ids = {fact["id"] for fact in state["facts"]}
        for entry in event["sources"]:
            self.assertIn(entry["fact_id"], fact_ids)

    def test_equal_counts_suppressed(self):
        document = detect(obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_AG_4]))
        self.assertEqual(document["events"], [])

    def test_decrease_suppressed(self):
        lower = (
            "hud",
            "Bottom-left team panel for team AG showing members AG.BAROS, "
            "plus header 'Elim 3'.",
            "high",
            "Team AG panel header Elim 3.",
        )
        document = detect(obs_doc(0, [ELIM_AG_5]), obs_doc(48, [lower]))
        self.assertEqual(document["events"], [])

    def test_disjoint_scopes_suppressed(self):
        document = detect(obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_SBUL_5]))
        self.assertEqual(document["events"], [])

    def test_personal_counter_not_linked_to_team_counter(self):
        document = detect(obs_doc(0, [ELIM_PERSONAL]), obs_doc(48, [ELIM_AG_5]))
        self.assertEqual(document["events"], [])

    def test_mixed_sentence_attributes_to_nearest_scope(self):
        mixed = (
            "hud",
            "Panel mentions team AG and team SBUL with header 'Elim 4'.",
            "high",
            "Shared panel Elim 4.",
        )
        # The counter resolves to the nearest team label (SBUL), which has
        # no counterpart reading, so no event links it to team AG.
        document = detect(obs_doc(0, [ELIM_AG_4]), obs_doc(48, [mixed]))
        self.assertEqual(document["events"], [])

    def test_low_confidence_reading_suppressed(self):
        weak = (ELIM_AG_5[0], ELIM_AG_5[1], "low", ELIM_AG_5[3])
        document = detect(obs_doc(0, [ELIM_AG_4]), obs_doc(48, [weak]))
        self.assertEqual(document["events"], [])

    def test_chained_increases_emit_per_step(self):
        higher = (
            "hud",
            "Bottom-left team panel for team AG showing members AG.BAROS, "
            "plus header 'Elim 6'.",
            "high",
            "Team AG panel header Elim 6.",
        )
        document = detect(
            obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_AG_5]), obs_doc(96, [higher])
        )
        self.assertEqual(len(document["events"]), 2)
        self.assertEqual(
            [event["timestamp"] for event in document["events"]], [48, 96]
        )


class NoInferenceTests(unittest.TestCase):
    def test_missing_nameplate_is_not_elimination(self):
        document = detect(obs_doc(0, [NAMEPLATE]), obs_doc(48, [TIMER_742]))
        self.assertEqual(document["events"], [])

    def test_timer_change_is_not_an_event(self):
        document = detect(obs_doc(0, [TIMER_742]), obs_doc(48, [TIMER_1057]))
        self.assertEqual(document["events"], [])

    def test_ambiguous_hud_numbers_are_not_events(self):
        document = detect(obs_doc(0, [COUNTERS_9_36]), obs_doc(48, [COUNTERS_9_31]))
        self.assertEqual(document["events"], [])

    def test_persistent_banner_is_not_repeated(self):
        document = detect(
            obs_doc(0, [BANNER]), obs_doc(48, [BANNER]), obs_doc(96, [BANNER])
        )
        self.assertEqual(document["events"], [])

    def test_no_event_is_ever_confirmed(self):
        document = detect(
            obs_doc(0, [ELIM_AG_4, KILL_FEED]),
            obs_doc(48, [ELIM_AG_5, KILL_FEED]),
        )
        self.assertTrue(document["events"])
        for event in document["events"]:
            self.assertEqual(event["status"], "candidate")


class DuplicateAndConflictTests(unittest.TestCase):
    def test_duplicate_input_skipped(self):
        doc = obs_doc(48, [KILL_FEED])
        state = build_state(doc)
        single, _ = detect_events(triples(doc), state)
        doubled, counts = detect_events(triples(doc, doc), state)
        self.assertEqual(doubled, single)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["accepted"], 1)

    def test_out_of_order_input_converges(self):
        docs = [obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_AG_5])]
        state = build_state(*docs)
        forward, _ = detect_events(triples(*docs), state)
        backward, _ = detect_events(triples(*reversed(docs)), state)
        self.assertEqual(backward, forward)
        self.assertEqual(len(forward["events"]), 1)

    def divergent_pair(self):
        first = obs_doc(48, [TIMER_742])
        second = obs_doc(48, [TIMER_1057])
        # Same timestamp from two distinct provenances: a structural conflict.
        named = [
            (48, "frame_00048.json", first),
            (48, "frame_00048b.json", second),
        ]
        return named, build_match_state(named)

    def test_contested_timestamp_skipped(self):
        named, state = self.divergent_pair()
        conflicted = [
            fact["id"] for fact in state["facts"] if fact["status"] == "conflicted"
        ]
        self.assertTrue(conflicted)
        document, counts = detect_events(named, state)
        self.assertEqual(document["events"], [])
        self.assertEqual(counts["contested"], [48])
        self.assertEqual(counts["accepted"], 0)
        # The replay provenance still records the considered inputs.
        self.assertEqual(
            sorted(document["generated_from"]["observations"]),
            ["frame_00048.json", "frame_00048b.json"],
        )

    def test_contested_timestamp_does_not_block_others(self):
        named, _ = self.divergent_pair()
        kill = obs_doc(96, [KILL_FEED])
        named = named + [(96, "frame_00096.json", kill)]
        state = build_match_state(named)
        document, counts = detect_events(named, state)
        self.assertEqual(len(document["events"]), 1)
        self.assertEqual(document["events"][0]["timestamp"], 96)
        self.assertEqual(counts["contested"], [48])


class MalformedInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.obs = self.root / "observations"
        self.frames = self.root / "frames"
        self.frames.mkdir()
        self.state_path = self.root / "match_state.json"
        self.events_path = self.root / "events.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_malformed_observation_rejected(self):
        self.obs.mkdir()
        good = self.obs / "frame_00000.json"
        good.write_text(json.dumps(obs_doc(0, [TIMER_742])), encoding="utf-8")
        bad = self.obs / "frame_00048.json"
        bad.write_text("not json", encoding="utf-8")
        state_doc = build_match_state(
            [(0, "frame_00000.json", obs_doc(0, [TIMER_742]))]
        )
        self.state_path.write_text(json.dumps(state_doc), encoding="utf-8")
        code, out, _ = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=self.events_path,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 1)
        self.assertIn("rejected", out)
        self.assertIn("partial failure", out)
        self.assertTrue(self.events_path.is_file())

    def test_timestamp_mismatch_rejected(self):
        self.obs.mkdir()
        path = self.obs / "frame_00048.json"
        path.write_text(json.dumps(obs_doc(0, [TIMER_742])), encoding="utf-8")
        state_doc = build_match_state(
            [(0, "frame_00000.json", obs_doc(0, [TIMER_742]))]
        )
        self.state_path.write_text(json.dumps(state_doc), encoding="utf-8")
        code, out, _ = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=self.events_path,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 1)
        self.assertIn("rejected", out)
        self.assertFalse(self.events_path.exists())

    def test_invalid_state_rejected(self):
        self.obs.mkdir()
        path = self.obs / "frame_00000.json"
        path.write_text(json.dumps(obs_doc(0, [TIMER_742])), encoding="utf-8")
        self.state_path.write_text("not json", encoding="utf-8")
        code, _, err = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=self.events_path,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 1)
        self.assertIn("rebuild-state", err)
        self.assertFalse(self.events_path.exists())

    def test_stale_state_refused(self):
        self.obs.mkdir()
        for ts in (0, 48):
            path = self.obs / f"frame_{ts:05d}.json"
            path.write_text(json.dumps(obs_doc(ts, [TIMER_742])), encoding="utf-8")
        state_doc = build_match_state(
            [(0, "frame_00000.json", obs_doc(0, [TIMER_742]))]
        )
        self.state_path.write_text(json.dumps(state_doc), encoding="utf-8")
        code, _, err = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=self.events_path,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 1)
        self.assertIn("rebuild-state", err)
        self.assertFalse(self.events_path.exists())

    def test_no_observations_is_an_error(self):
        self.obs.mkdir()
        code, _, err = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=self.events_path,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 1)
        self.assertIn("no frame_", err)
        self.assertFalse(self.events_path.exists())

    def test_write_failure_preserves_previous_events(self):
        self.obs.mkdir()
        path = self.obs / "frame_00000.json"
        path.write_text(json.dumps(obs_doc(0, [TIMER_742])), encoding="utf-8")
        state_doc = build_match_state(
            [(0, "frame_00000.json", obs_doc(0, [TIMER_742]))]
        )
        self.state_path.write_text(json.dumps(state_doc), encoding="utf-8")
        code, _, _ = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=self.events_path,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 0)
        before = self.events_path.read_bytes()
        blocker = self.root / "blocker"
        blocker.mkdir()
        code, _, err = capture_detect(
            observations_dir=self.obs,
            state_path=self.state_path,
            events_path=blocker,
            frames_dir=self.frames,
        )
        self.assertEqual(code, 1)
        self.assertIn("left intact", err)
        self.assertEqual(self.events_path.read_bytes(), before)


class ProvenanceTests(unittest.TestCase):
    def test_events_reference_accepted_sources(self):
        docs = [obs_doc(0, [ELIM_AG_4]), obs_doc(48, [ELIM_AG_5, KILL_FEED])]
        state = build_state(*docs)
        document, counts = detect_events(triples(*docs), state)
        accepted = {f"frame_{doc['timestamp']:05d}.json" for doc in docs}
        self.assertEqual(
            set(document["generated_from"]["observations"]), accepted
        )
        self.assertEqual(document["generated_from"]["match_state"], "match_state.json")
        self.assertEqual(document["rules_evaluated"], sorted(EVENT_RULES))
        fact_ids = {fact["id"] for fact in state["facts"]}
        for event in document["events"]:
            for entry in event["sources"]:
                self.assertIn(entry["file"], accepted)
                self.assertIn(entry["fact_id"], fact_ids)
                self.assertEqual(
                    entry["timestamp"],
                    int(entry["file"].split("_")[1].split(".")[0]),
                )


class DeterminismTests(unittest.TestCase):
    def test_repeated_runs_identical(self):
        document = detect(
            obs_doc(0, [ELIM_AG_4, KILL_FEED]), obs_doc(48, [ELIM_AG_5])
        )
        again = detect(
            obs_doc(0, [ELIM_AG_4, KILL_FEED]), obs_doc(48, [ELIM_AG_5])
        )
        self.assertEqual(
            json.dumps(document, indent=2, sort_keys=True),
            json.dumps(again, indent=2, sort_keys=True),
        )


class PreservationTests(unittest.TestCase):
    def test_detection_does_not_touch_sources_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "observations"
            frames = root / "frames"
            frames.mkdir()
            for ts in (0, 48):
                path = obs / f"frame_{ts:05d}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(obs_doc(ts, [TIMER_742])), encoding="utf-8"
                )
            from main import run_state_rebuild

            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = run_state_rebuild(
                    observations_dir=obs,
                    state_path=root / "match_state.json",
                    frames_dir=frames,
                )
            self.assertEqual(code, 0)
            before = {
                path.name: path.read_bytes()
                for path in list(obs.iterdir()) + [root / "match_state.json"]
            }
            code, _, _ = capture_detect(
                observations_dir=obs,
                state_path=root / "match_state.json",
                events_path=root / "events" / "events.json",
                frames_dir=frames,
            )
            self.assertEqual(code, 0)
            after = {
                path.name: path.read_bytes()
                for path in list(obs.iterdir()) + [root / "match_state.json"]
            }
            self.assertEqual(before, after)
            self.assertEqual(
                sorted(path.name for path in (root / "events").iterdir()),
                ["events.json"],
            )


if __name__ == "__main__":
    unittest.main()
