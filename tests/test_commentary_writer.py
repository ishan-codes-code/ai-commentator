"""Offline tests for the LLM commentary writer (--commentary-write).

No live API calls: a fake writer client stands in for the Baseten
OpenAI-compatible endpoint, and fixtures reuse the deterministic builders.
Run natively from the repository root:
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from types import SimpleNamespace
from unittest.mock import patch

from openai import APIError

import main
from main import (
    BASETEN_MODEL,
    ObservationError,
    WRITER_MAX_CLAIMS,
    WRITER_MAX_HISTORY_LINES,
    WriterAPIError,
    build_match_state,
    build_writer_prompt,
    claim_id_of,
    detect_events,
    discover_observation_files,
    ensure_observation_timestamp,
    gate_commentary_claims,
    parse_observation,
    parse_writer_lines,
    prioritize_writer_claims,
    request_writer_text,
    run_generate_commentary,
    validate_events,
    validate_match_state,
    validate_observation,
    writer_text_valid,
)
from test_commentary import obs_doc, obs_item, read_commentary, write_tree

MAIN_PATH = Path(main.__file__)
ENV_A = "Arid desert mesas under bright light."
ENV_B = "Green containers near a rocky ledge."


class FakeWriterCompletions:
    """Mimics client.chat.completions.create for the writer path."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(kwargs, len(self.calls) - 1)


class FakeWriterClient:
    def __init__(self, handler):
        self.chat = SimpleNamespace(completions=FakeWriterCompletions(handler))
        self.close_calls = 0

    @property
    def calls(self):
        return self.chat.completions.calls

    def close(self):
        self.close_calls += 1


def writer_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def api_error(message):
    return APIError(message, request=object(), body=None)


def load_inputs(obs_dir, state_path, events_path):
    accepted = []
    for timestamp, path in discover_observation_files(Path(obs_dir)):
        validated = ensure_observation_timestamp(
            validate_observation(parse_observation(path.read_text(encoding="utf-8"))),
            timestamp,
        )
        accepted.append((timestamp, path.name, validated))
    state = validate_match_state(json.loads(Path(state_path).read_text(encoding="utf-8")))
    events = validate_events(json.loads(Path(events_path).read_text(encoding="utf-8")))
    return accepted, state, events


def gate_ids_and_claims(obs_dir, state_path, events_path):
    accepted, state, events = load_inputs(obs_dir, state_path, events_path)
    claims, _ = gate_commentary_claims(list(accepted), state, events)
    return {claim_id_of(claim): claim for claim in claims}


def wording_json(entries):
    return json.dumps({"lines": entries})


def run_writer_capture(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_generate_commentary(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


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


class ValidOutputTests(unittest.TestCase):
    def test_natural_lines_keep_identity_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            self.assertTrue(by_id)
            # Keep every evidence quote verbatim so validation passes.
            texts = {
                claim_id: f"Caster says: {by_id[claim_id]['evidence'][0]}"
                for claim_id in by_id
            }
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([
                    {"claim_id": claim_id, "text": text}
                    for claim_id, text in texts.items()
                ]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
            self.assertEqual(code, 0)
            doc = read_commentary(out)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            client.calls[0].get("model"), BASETEN_MODEL,
            msg="writer must use the configured model",
        )
        self.assertNotIn(
            "image_url", json.dumps(client.calls[0]),
            msg="writer requests must be text-only",
        )
        got = {line["claim_id"]: line["text"] for line in doc["lines"]}
        self.assertEqual(got, texts)
        # Identity and provenance match the deterministic run exactly.
        template_ids = set(by_id)
        self.assertEqual(set(got), template_ids)

    def test_request_uses_configured_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            seen = {}

            def handle(kwargs, index):
                seen.update(kwargs)
                return writer_response(wording_json([]))

            client = FakeWriterClient(handle)
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen.get("model"), BASETEN_MODEL)


class FallbackTests(unittest.TestCase):
    def run_against(self, tmp, handler, **kwargs):
        obs_dir, st, ev, out = two_claim_tree(tmp)
        by_id = gate_ids_and_claims(obs_dir, st, ev)
        client = FakeWriterClient(handler)
        code, _, err = run_writer_capture(
            observations_dir=obs_dir,
            state_path=st,
            events_path=ev,
            commentary_path=out,
            writer="llm",
            client=client,
            **kwargs,
        )
        return code, err, read_commentary(out), by_id

    def template_texts(self):
        # Fresh directory: reusing the writer run's output dir would
        # suppress everything as already-emitted.
        with tempfile.TemporaryDirectory() as fresh:
            obs_dir, st, ev, out = two_claim_tree(fresh)
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
            )
            self.assertEqual(code, 0)
            doc = read_commentary(out)
        return {line["claim_id"]: line["text"] for line in doc["lines"]}

    def test_malformed_output_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, err, doc, _ = self.run_against(
                tmp, lambda kwargs, index: writer_response("{not json")
            )
            expected = self.template_texts()
        self.assertEqual(code, 0)
        self.assertIn("deterministic fallback", err)
        self.assertEqual(
            {line["claim_id"]: line["text"] for line in doc["lines"]},
            expected,
        )

    def test_unknown_claim_id_falls_back(self):
        def handle(kwargs, index):
            return writer_response(wording_json([
                {"claim_id": "clm-deadbeefcafe", "text": "A brand new story."}
            ]))

        with tempfile.TemporaryDirectory() as tmp:
            code, err, doc, _ = self.run_against(tmp, handle)
            expected = self.template_texts()
        self.assertEqual(code, 0)
        self.assertIn("deterministic fallback", err)
        self.assertEqual(
            {line["claim_id"]: line["text"] for line in doc["lines"]},
            expected,
        )

    def test_dropped_quote_falls_back_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            ids = sorted(by_id)
            good_id, bad_id = ids[0], ids[1]
            good_text = f"Nice: {by_id[good_id]['evidence'][0]}"
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([
                    {"claim_id": good_id, "text": good_text},
                    {"claim_id": bad_id, "text": "Vague words without the quote."},
                ]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
            self.assertEqual(code, 0)
            doc = read_commentary(out)
        got = {line["claim_id"]: line["text"] for line in doc["lines"]}
        self.assertEqual(got[good_id], good_text)
        # The unsupported line falls back to template wording.
        self.assertIn(by_id[bad_id]["evidence"][0], got[bad_id])

    def test_api_error_falls_back_without_crash(self):
        def handle(kwargs, index):
            raise api_error("writer backend exploded")

        with tempfile.TemporaryDirectory() as tmp:
            code, err, doc, _ = self.run_against(tmp, handle)
            expected = self.template_texts()
        self.assertEqual(code, 0)
        self.assertIn("deterministic fallback", err)
        self.assertEqual(
            {line["claim_id"]: line["text"] for line in doc["lines"]},
            expected,
        )

    def test_empty_response_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, err, doc, _ = self.run_against(
                tmp, lambda kwargs, index: SimpleNamespace(choices=[])
            )
            expected = self.template_texts()
        self.assertEqual(code, 0)
        self.assertIn("deterministic fallback", err)
        self.assertEqual(
            {line["claim_id"]: line["text"] for line in doc["lines"]},
            expected,
        )


class EventEchoWriterTests(unittest.TestCase):
    def test_qualified_echo_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = echo_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            echo_ids = [
                claim_id for claim_id, claim in by_id.items()
                if claim["type"] == "event_echo"
            ]
            self.assertEqual(len(echo_ids), 1)
            echo = by_id[echo_ids[0]]
            quoted = " — ".join(echo["evidence"])
            text = (
                f"Big moment: {quoted} — "
                f"unconfirmed: {echo['qualification']}"
            )
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([
                    {"claim_id": echo_ids[0], "text": text}
                ]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
            self.assertEqual(code, 0)
            doc = read_commentary(out)
        got = {line["claim_id"]: line["text"] for line in doc["lines"]}
        self.assertEqual(got[echo_ids[0]], text)

    def test_unqualified_confirmation_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = echo_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            echo_ids = [
                claim_id for claim_id, claim in by_id.items()
                if claim["type"] == "event_echo"
            ]
            echo = by_id[echo_ids[0]]
            # Keeps every quote but drops the qualifier and claims certainty.
            quoted = " — ".join(echo["evidence"])
            text = (
                f"{quoted} — Team RRQ definitely confirmed the kill."
            )
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([
                    {"claim_id": echo_ids[0], "text": text}
                ]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
            self.assertEqual(code, 0)
            doc = read_commentary(out)
        got = {line["claim_id"]: line["text"] for line in doc["lines"]}
        self.assertIn("unconfirmed", got[echo_ids[0]])
        self.assertNotIn("definitely", got[echo_ids[0]])

    def test_invented_identity_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            target = sorted(by_id)[0]
            quote = by_id[target]["evidence"][0]
            text = f"{quote} as RRQ.JEEK rotates in from nowhere."
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([
                    {"claim_id": target, "text": text}
                ]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
            self.assertEqual(code, 0)
            doc = read_commentary(out)
        got = {line["claim_id"]: line["text"] for line in doc["lines"]}
        self.assertNotIn("RRQ.JEEK", got[target])


class BehaviorPreservationTests(unittest.TestCase):
    def test_repeat_run_stays_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            texts = {
                claim_id: f"Says: {by_id[claim_id]['evidence'][0]}"
                for claim_id in by_id
            }
            kwargs = dict(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
            )
            first = FakeWriterClient(
                lambda kw, index: writer_response(wording_json([
                    {"claim_id": claim_id, "text": text}
                    for claim_id, text in texts.items()
                ]))
            )
            code, _, _ = run_writer_capture(client=first, **kwargs)
            self.assertEqual(code, 0)
            self.assertTrue(read_commentary(out)["lines"])
            second = FakeWriterClient(
                lambda kw, index: writer_response(wording_json([]))
            )
            code, _, _ = run_writer_capture(client=second, **kwargs)
            self.assertEqual(code, 0)
            doc = read_commentary(out)
            self.assertEqual(doc["lines"], [])
            # Nothing new to word, so the writer is not even called.
            self.assertEqual(second.calls, [])

    def test_silence_makes_no_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = write_tree(
                tmp,
                {
                    0: obs_doc(
                        0,
                        [obs_item("hud", "Blurry counter digits.", "low")],
                    ),
                },
            )
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
        self.assertEqual(code, 0)
        self.assertEqual(client.calls, [])

    def test_missing_key_raises_before_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            with patch.dict(os.environ):
                os.environ.pop("BASETEN_API_KEY", None)
                with self.assertRaises(RuntimeError) as raised:
                    run_generate_commentary(
                        observations_dir=obs_dir,
                        state_path=st,
                        events_path=ev,
                        commentary_path=out,
                        writer="llm",
                    )
        self.assertIn("BASETEN_API_KEY", str(raised.exception))
        self.assertFalse(Path(out).is_file())

    def test_injected_client_not_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_dir, st, ev, out = two_claim_tree(tmp)
            by_id = gate_ids_and_claims(obs_dir, st, ev)
            texts = {
                claim_id: f"Says: {by_id[claim_id]['evidence'][0]}"
                for claim_id in by_id
            }
            client = FakeWriterClient(
                lambda kwargs, index: writer_response(wording_json([
                    {"claim_id": claim_id, "text": text}
                    for claim_id, text in texts.items()
                ]))
            )
            code, _, _ = run_writer_capture(
                observations_dir=obs_dir,
                state_path=st,
                events_path=ev,
                commentary_path=out,
                writer="llm",
                client=client,
            )
        self.assertEqual(code, 0)
        self.assertEqual(client.close_calls, 0)


class PromptBoundsTests(unittest.TestCase):
    def test_history_truncated_to_bound(self):
        history = [f"Prior line {index}" for index in range(10)]
        prompt = main.build_writer_prompt([], history)
        for kept in history[-WRITER_MAX_HISTORY_LINES:]:
            self.assertIn(kept, prompt)
        self.assertNotIn(history[0], prompt)

    def test_evidence_truncated_per_claim(self):
        claim = {
            "claim_id": "clm-0123456789ab",
            "type": "scene",
            "template": "scene_visible",
            "timestamp": 3,
            "sources": [],
            "source_ids": ["f0001"],
            "event_id": None,
            "evidence": ["x" * 2000],
            "confidence": "high",
            "qualification": None,
        }
        prompt = main.build_writer_prompt([claim], [])
        self.assertIn("x" * 500, prompt)
        self.assertNotIn("x" * 2000, prompt)

    def test_prioritization_prefers_developments(self):
        def claim(kind, template, timestamp, source):
            return {
                "type": kind,
                "template": template,
                "timestamp": timestamp,
                "sources": [],
                "source_ids": [source],
                "event_id": None,
                "evidence": [f"Evidence {source}."],
                "confidence": "high",
                "qualification": None,
            }

        claims = [
            claim("scene", "scene_visible", 0, f"f{i:04d}") for i in range(8)
        ]
        claims.append(claim("development", "development_new", 3, "f0009"))
        model_claims, overflow = prioritize_writer_claims(claims)
        self.assertEqual(len(model_claims), WRITER_MAX_CLAIMS)
        self.assertEqual(len(overflow), 3)
        self.assertIn("f0009", [c["source_ids"][0] for c in model_claims])


class WriterValidationUnitTests(unittest.TestCase):
    def test_parse_rejects_unknown_and_duplicate_ids(self):
        from main import claim_id_of

        claim = {
            "type": "scene",
            "template": "scene_visible",
            "timestamp": 3,
            "sources": [],
            "source_ids": ["f0001"],
            "event_id": None,
            "evidence": [ENV_A],
            "confidence": "high",
            "qualification": None,
        }
        known = claim_id_of(claim)
        with self.assertRaises(ObservationError):
            parse_writer_lines(
                wording_json([{"claim_id": "clm-unknown", "text": "Hi."}]),
                [claim],
            )
        with self.assertRaises(ObservationError):
            parse_writer_lines(
                wording_json([
                    {"claim_id": known, "text": "One."},
                    {"claim_id": known, "text": "Two."},
                ]),
                [claim],
            )
        parsed = parse_writer_lines(
            wording_json([{"claim_id": known, "text": "Fine."}]), [claim]
        )
        self.assertEqual(parsed, {known: "Fine."})

    def test_writer_text_valid_cases(self):
        from main import writer_text_valid as valid

        claim = {
            "type": "scene",
            "template": "scene_visible",
            "timestamp": 3,
            "sources": [],
            "source_ids": ["f0001"],
            "event_id": None,
            "evidence": [ENV_A],
            "confidence": "high",
            "qualification": None,
        }
        self.assertTrue(valid(f"Caster: {ENV_A}", claim))
        self.assertFalse(valid("Vague words.", claim))
        echo = dict(
            claim,
            type="event_echo",
            template="event_echo",
            evidence=["RRQ.JEEK eliminated OG.IQOOPAWDI in the kill feed"],
            qualification="killer and victim roles are not established",
        )
        good = (
            "RRQ.JEEK eliminated OG.IQOOPAWDI in the kill feed — "
            "unconfirmed: killer and victim roles are not established"
        )
        self.assertTrue(valid(good, echo))
        self.assertFalse(valid(echo["evidence"][0], echo))

    def test_request_errors_redacted(self):
        def handle(kwargs, index):
            raise api_error("boom with sk-secret-inside")

        client = FakeWriterClient(handle)
        with self.assertRaises(WriterAPIError) as raised:
            request_writer_text(client, "prompt", api_key="sk-secret-inside")
        self.assertNotIn("sk-secret-inside", str(raised.exception))


class CliWriterTests(unittest.TestCase):
    def test_commentary_write_requires_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs_dir = root / "output" / "observations"
            obs_dir.mkdir(parents=True, exist_ok=True)
            doc = obs_doc(0, [obs_item("environment", ENV_A)])
            (obs_dir / "frame_00000.json").write_text(
                json.dumps(doc), encoding="utf-8"
            )
            accepted = [(0, "frame_00000.json", doc)]
            state = build_match_state(list(accepted))
            (obs_dir / "match_state.json").write_text(
                json.dumps(state, indent=2) + "\n",
                encoding="utf-8",
            )
            events, _ = detect_events(list(accepted), state)
            (root / "output" / "events").mkdir(parents=True, exist_ok=True)
            (root / "output" / "events" / "events.json").write_text(
                json.dumps(events), encoding="utf-8"
            )
            env = dict(os.environ)
            env.pop("BASETEN_API_KEY", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(MAIN_PATH),
                    "--commentary-write",
                    "--output-dir",
                    str(root / "output"),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                env=env,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("BASETEN_API_KEY", proc.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
