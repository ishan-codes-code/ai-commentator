"""Offline tests for the final casting script (--generate-script).

No live API calls: a fake text-only client stands in for the Baseten
OpenAI-compatible endpoint, and fixtures reuse the production builders.
Run inside Docker:
    docker compose run --rm -v "$PWD/tests:/app/tests:ro" \\
        commentator python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openai import APIError

import main
from main import (
    ObservationError,
    build_match_state,
    claim_id_of,
    detect_events,
    ensure_observation_timestamp,
    discover_observation_files,
    gate_commentary_claims,
    parse_script_segments,
    run_generate_script,
    script_text_valid,
    validate_events,
    validate_match_state,
    validate_observation,
    validate_script,
)
from test_commentary import obs_doc, obs_item, write_tree

ENV_A = "Arid desert mesas under bright light."
ENV_B = "Green containers near a rocky ledge."
HUD_HEALTH = "Bottom-center player status bar shows health 200/200 and one elimination."


class FakeScriptCompletions:
    """Mimics client.chat.completions.create for the script path."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(kwargs, len(self.calls) - 1)


class FakeScriptClient:
    def __init__(self, handler):
        self.chat = SimpleNamespace(completions=FakeScriptCompletions(handler))
        self.close_calls = 0

    @property
    def calls(self):
        return self.chat.completions.calls

    def close(self):
        self.close_calls += 1


def script_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def api_error(message):
    return APIError(message, request=object(), body=None)


def script_json(entries):
    return json.dumps({"segments": entries})


def run_script_capture(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_generate_script(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


def read_script(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def two_claim_tree(root):
    return write_tree(
        root,
        {
            0: obs_doc(0, [obs_item("environment", ENV_A)]),
            3: obs_doc(
                3,
                [obs_item("environment", ENV_A), obs_item("environment", ENV_B)],
            ),
        },
    )


def echo_tree(root):
    return write_tree(
        root,
        {
            0: obs_doc(0, [obs_item("environment", ENV_A)]),
            3: obs_doc(
                3,
                [
                    obs_item("environment", ENV_A),
                    obs_item(
                        "hud",
                        "Kill feed shows a fresh entry.",
                        "high",
                        "RRQ.JEEK eliminated OG.IQOOPAWDI in the kill feed",
                    ),
                ],
            ),
        },
    )


def hud_tree(root):
    return write_tree(
        root,
        {
            0: obs_doc(0, [obs_item("hud", HUD_HEALTH)]),
        },
    )


def silence_tree(root):
    return write_tree(
        root,
        {
            0: obs_doc(
                0,
                [obs_item("hud", "Blurry counter digits.", "low")],
            ),
        },
    )


def gate_script_claims(obs_dir, state_path, events_path):
    accepted = []
    for timestamp, path in discover_observation_files(Path(obs_dir)):
        validated = ensure_observation_timestamp(
            validate_observation(
                json.loads(Path(path).read_text(encoding="utf-8"))
            ),
            timestamp,
        )
        accepted.append((timestamp, path.name, validated))
    state = validate_match_state(json.loads(Path(state_path).read_text()))
    events = validate_events(json.loads(Path(events_path).read_text()))
    claims, _ = gate_commentary_claims(list(accepted), state, events)
    return {claim_id_of(claim): claim for claim in claims}


def script_kwargs(obs_dir, state_path, events_path, out):
    out = Path(out)
    return dict(
        observations_dir=obs_dir,
        state_path=state_path,
        events_path=events_path,
        # A hermetic, missing commentary file: history comes only from the
        # script under test, never from the repo's real commentary.json.
        commentary_path=out.parent / "commentary.json",
        script_path=out,
    )


class ValidOutputTests(unittest.TestCase):
    def test_valid_response_produces_valid_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            self.assertTrue(by_id)
            texts = {
                claim_id: f"And here comes the play: {by_id[claim_id]['evidence'][0].lower()}"
                for claim_id in by_id
            }
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [claim_id], "text": text}
                    for claim_id, text in texts.items()
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            self.assertEqual(code, 0)
            doc = read_script(out)
        self.assertEqual(doc["schema_version"], "script-v1")
        self.assertEqual(doc["status"], "success")
        self.assertIsNone(doc["error"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0].get("model"), main.BASETEN_MODEL)
        self.assertNotIn("image_url", json.dumps(client.calls[0]))
        # Provenance preserved: every segment cites known claims exactly once.
        seen = []
        for segment in doc["segments"]:
            for claim_id in segment["source_claim_ids"]:
                self.assertIn(claim_id, by_id)
                self.assertNotIn(claim_id, seen)
                seen.append(claim_id)
        self.assertEqual(set(seen), set(by_id))
        # Full contract validation with provenance passes.
        validate_script(doc, by_id)
        # Chronological ordering holds.
        stamps = [s["timestamp_seconds"] for s in doc["segments"]]
        self.assertEqual(stamps, sorted(stamps))

    def test_paraphrase_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = hud_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            self.assertEqual(len(by_id), 1)
            claim_id = sorted(by_id)[0]
            text = "Full health on the board with one elimination to the name."
            self.assertTrue(script_text_valid(text, [by_id[claim_id]]))
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [claim_id], "text": text}
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            self.assertEqual(code, 0)
            doc = read_script(out)
        self.assertEqual(doc["status"], "success")
        self.assertEqual(doc["segments"][0]["text"], text)


class RejectionTests(unittest.TestCase):
    def run_single(self, tmp, text, tree=hud_tree):
        obs_dir, st, ev, _ = tree(tmp)
        out = Path(tmp) / "script.json"
        by_id = gate_script_claims(obs_dir, st, ev)
        claim_id = sorted(by_id)[0]
        client = FakeScriptClient(
            lambda kwargs, index: script_response(script_json([
                {"source_claim_ids": [claim_id], "text": text}
            ]))
        )
        code, _, _ = run_script_capture(
            **script_kwargs(obs_dir, st, ev, out), client=client
        )
        return code, read_script(out)

    def test_unsupported_identity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, doc = self.run_single(
                tmp, "RRQ.JEEK holds full health with one elimination."
            )
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["segments"], [])

    def test_unsupported_kill_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            claim_id = sorted(by_id)[0]
            text = "What a knock, he finishes the fight with heavy damage!"
            self.assertFalse(script_text_valid(text, [by_id[claim_id]]))
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [claim_id], "text": text}
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")

    def test_unconfirmed_event_cannot_be_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = echo_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            echo_ids = [
                cid for cid, claim in by_id.items()
                if claim["type"] == "event_echo"
            ]
            self.assertEqual(len(echo_ids), 1)
            echo = by_id[echo_ids[0]]
            bad = "RRQ.JEEK confirmed the elimination on OG.IQOOPAWDI."
            self.assertFalse(script_text_valid(bad, [echo]))
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [echo_ids[0]], "text": bad}
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")

    def test_uncertain_echo_with_qualifier_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = echo_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            echo_ids = [
                cid for cid, claim in by_id.items()
                if claim["type"] == "event_echo"
            ]
            echo = by_id[echo_ids[0]]
            good = (
                "There may have been movement in the feed, "
                "unconfirmed for now."
            )
            # Echo evidence names RRQ.JEEK; naming it is grounded here.
            good_named = (
                "Possible feed movement involving RRQ.JEEK, unconfirmed."
            )
            self.assertTrue(script_text_valid(good_named, [echo]))
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [echo_ids[0]], "text": good_named}
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            self.assertEqual(code, 0)
            self.assertEqual(read_script(out)["status"], "success")

    def test_causal_claim_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, doc = self.run_single(
                tmp,
                "Full health and one elimination because the zone forced the fight.",
            )
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")


class ParseFailureTests(unittest.TestCase):
    def test_unknown_claim_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": ["clm-deadbeefcafe"], "text": "A story."}
                ]))
            )
            code, _, err = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["segments"], [])
        self.assertNotIn("clm-deadbeefcafe", json.dumps(doc["segments"]))

    def test_duplicate_claim_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = hud_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            claim_id = sorted(by_id)[0]
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [claim_id], "text": "First line here."},
                    {"source_claim_ids": [claim_id], "text": "Second line here."},
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")

    def test_malformed_json_handled_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            client = FakeScriptClient(
                lambda kwargs, index: script_response("{not json")
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["segments"], [])
        self.assertTrue(doc["error"])

    def test_empty_model_output_handled_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            client = FakeScriptClient(
                lambda kwargs, index: script_response("   ")
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["segments"], [])

    def test_model_empty_segments_means_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "silence")
        self.assertEqual(doc["segments"], [])

    def test_api_failure_is_not_false_success(self):
        def handle(kwargs, index):
            raise api_error("script backend exploded")

        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            client = FakeScriptClient(handle)
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["segments"], [])
        self.assertTrue(doc["error"])
        for segment in doc["segments"]:
            self.assertNotIn("exploded", segment["text"])


class SilenceAndHistoryTests(unittest.TestCase):
    def test_zero_eligible_claims_is_silence_without_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = silence_tree(tmp)
            out = Path(tmp) / "script.json"
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            doc = read_script(out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "silence")
        self.assertEqual(doc["segments"], [])
        self.assertIsNone(doc["error"])
        self.assertEqual(client.calls, [])

    def test_repeat_run_suppresses_without_new_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = hud_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            claim_id = sorted(by_id)[0]
            first = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {
                        "source_claim_ids": [claim_id],
                        "text": "Full health and one elimination on the board.",
                    }
                ]))
            )
            kwargs = script_kwargs(obs_dir, st, ev, out)
            code, _, _ = run_script_capture(client=first, **kwargs)
            self.assertEqual(code, 0)
            self.assertEqual(read_script(out)["status"], "success")
            second = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([]))
            )
            code, _, _ = run_script_capture(client=second, **kwargs)
            self.assertEqual(code, 0)
            doc = read_script(out)
            self.assertEqual(doc["status"], "silence")
            self.assertEqual(doc["segments"], [])
            self.assertEqual(second.calls, [])

    def test_history_prevents_repetition(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = hud_tree(tmp)
            script_out = Path(tmp) / "script.json"
            # A prior script already spoke this exact evidence (under an
            # unrelated claim id, so only the already-spoken text match
            # can suppress it — not the already-emitted id match).
            script_out.write_text(
                json.dumps({
                    "schema_version": "script-v1",
                    "status": "success",
                    "generated_from": {
                        "observations": ["frame_00000.json"],
                        "match_state": "match_state.json",
                        "events": "events.json",
                    },
                    "segments": [
                        {
                            "segment_id": "segment-001",
                            "timestamp_seconds": 0,
                            "text": HUD_HEALTH,
                            "source_claim_ids": ["clm-olderun0000"],
                        }
                    ],
                    "omitted": [],
                    "error": None,
                }) + "\n",
                encoding="utf-8",
            )
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, script_out), client=client
            )
            doc = read_script(script_out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "silence")
        self.assertEqual(doc["segments"], [])
        self.assertEqual(client.calls, [])
        self.assertTrue(
            any(entry["rule"] == "already-spoken" for entry in doc["omitted"])
        )


class ProvenanceTests(unittest.TestCase):
    def test_timestamps_derive_from_evidence_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            ids = sorted(by_id)
            # Return segments newest-first; the stage must reorder them.
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {
                        "source_claim_ids": [ids[1]],
                        "text": f"Later moment: {by_id[ids[1]]['evidence'][0].lower()}",
                    },
                    {
                        "source_claim_ids": [ids[0]],
                        "text": f"Earlier moment: {by_id[ids[0]]['evidence'][0].lower()}",
                    },
                ]))
            )
            code, _, _ = run_script_capture(
                **script_kwargs(obs_dir, st, ev, out), client=client
            )
            self.assertEqual(code, 0)
            doc = read_script(out)
        stamps = [s["timestamp_seconds"] for s in doc["segments"]]
        self.assertEqual(stamps, sorted(stamps))
        for segment in doc["segments"]:
            expected = max(
                by_id[cid]["timestamp"] for cid in segment["source_claim_ids"]
            )
            self.assertEqual(segment["timestamp_seconds"], expected)
        self.assertEqual(
            [s["segment_id"] for s in doc["segments"]],
            ["segment-001", "segment-002"][: len(doc["segments"])],
        )

    def test_contract_rejects_malformed_script(self):
        base = {
            "schema_version": "script-v1",
            "status": "success",
            "generated_from": {
                "observations": ["frame_00000.json"],
                "match_state": "match_state.json",
                "events": "events.json",
            },
            "segments": [
                {
                    "segment_id": "segment-001",
                    "timestamp_seconds": 0,
                    "text": "A genuinely spoken line about the scene.",
                    "source_claim_ids": ["clm-abc"],
                }
            ],
            "omitted": [],
            "error": None,
        }
        with self.assertRaises(ObservationError):
            validate_script(dict(base, status="bogus"))
        bad = dict(base)
        bad["segments"] = [dict(bad["segments"][0], text="")]
        with self.assertRaises(ObservationError):
            validate_script(bad)
        bad = dict(base, error="boom")
        with self.assertRaises(ObservationError):
            validate_script(bad)
        bad = dict(base)
        bad["segments"] = []
        with self.assertRaises(ObservationError):
            validate_script(bad)

    def test_write_failure_leaves_prior_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = hud_tree(tmp)
            out = Path(tmp) / "script.json"
            by_id = gate_script_claims(obs_dir, st, ev)
            claim_id = sorted(by_id)[0]
            prior = {
                "schema_version": "script-v1",
                "status": "silence",
                "generated_from": {
                    "observations": ["frame_00000.json"],
                    "match_state": "match_state.json",
                    "events": "events.json",
                },
                "segments": [],
                "omitted": [],
                "error": None,
            }
            out.write_text(json.dumps(prior, indent=2) + "\n", encoding="utf-8")
            before = out.read_bytes()
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {"source_claim_ids": [claim_id], "text": "x" * 6000}
                ]))
            )
            # Force the atomic write to fail after validation of inputs.
            with patch.object(main, "write_json_atomic",
                              side_effect=OSError("disk full")):
                code, _, _ = run_script_capture(
                    **script_kwargs(obs_dir, st, ev, out), client=client
                )
            self.assertEqual(code, 1)
            self.assertEqual(out.read_bytes(), before)

    def test_upstream_artifacts_unmutated(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, _ = two_claim_tree(tmp)
            out = Path(tmp) / "script.json"
            targets = []
            for timestamp, path in discover_observation_files(Path(obs_dir)):
                targets.append(path)
            targets += [Path(st), Path(ev)]
            before = {str(p): Path(p).read_bytes() for p in targets}
            by_id = gate_script_claims(obs_dir, st, ev)
            ids = sorted(by_id)
            client = FakeScriptClient(
                lambda kwargs, index: script_response(script_json([
                    {
                        "source_claim_ids": [claim_id],
                        "text": f"Line here: {by_id[claim_id]['evidence'][0].lower()}",
                    }
                    for claim_id in ids
                ]))
            )
            run_script_capture(**script_kwargs(obs_dir, st, ev, out), client=client)
            for path in targets:
                self.assertEqual(Path(path).read_bytes(), before[str(path)])

    def test_no_tts_or_audio_dependency(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        lowered = source.lower()
        for banned in ("pyttsx", "gtts", "elevenlabs", "openai.audio",
                       "pygame.mixer", "playsound", "pyaudio", "boto3.polly"):
            self.assertNotIn(banned, lowered)
        self.assertTrue(callable(run_generate_script))
        self.assertEqual(main.SCRIPT_SCHEMA_VERSION, "script-v1")

    def test_cli_wires_generate_script(self):
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, str(Path(main.__file__).parent / "main.py"),
             "--help"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--generate-script", proc.stdout)


if __name__ == "__main__":
    unittest.main()
