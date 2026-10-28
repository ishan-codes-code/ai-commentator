"""Deterministic fixtures for observation-v1 parsing and validation.

No live API calls. Run inside Docker:
    docker compose run --rm -v "$PWD/tests:/app/tests:ro" \\
        commentator python -m unittest discover -s tests -v
"""

import unittest

from main import (
    OBSERVATION_CATEGORIES,
    OBSERVATION_CONFIDENCES,
    OBSERVATION_SCHEMA_VERSION,
    ObservationError,
    build_vision_prompt,
    parse_observation,
    validate_observation,
    vision_frame_timestamp,
)

VALID = {
    "schema_version": "observation-v1",
    "timestamp": 0,
    "observations": [
        {
            "category": "hud",
            "description": "Kill counter in the top bar.",
            "confidence": "medium",
            "evidence": "Digits in the top-center bar, partly obscured.",
        },
        {
            "category": "environment",
            "description": "Grassy field with crates.",
            "confidence": "high",
            "evidence": "Green ground plane and wooden crates left of center.",
        },
    ],
    "uncertainties": ["Player name text is too small to read."],
}

EMPTY_VALID = {
    "schema_version": "observation-v1",
    "timestamp": 0,
    "observations": [],
    "uncertainties": ["Frame is black; nothing describable."],
}


def mutate(doc, **overrides):
    merged = dict(doc)
    merged.update(overrides)
    return merged


class ParseObservationTests(unittest.TestCase):
    def test_valid_json_object(self):
        self.assertEqual(
            parse_observation(
                '{"schema_version": "observation-v1", "timestamp": 0, '
                '"observations": [], "uncertainties": []}'
            )["schema_version"],
            "observation-v1",
        )

    def test_fenced_json_tolerated(self):
        self.assertEqual(
            validate_observation(
                parse_observation(
                    '```json\n{"schema_version": "observation-v1", '
                    '"timestamp": 0, "observations": [], '
                    '"uncertainties": []}\n```'
                )
            )["observations"],
            [],
        )

    def test_empty_output_rejected(self):
        for text in ("", "   ", None):
            with self.assertRaises(ObservationError, msg=repr(text)):
                parse_observation(text)

    def test_malformed_json_rejected(self):
        with self.assertRaises(ObservationError):
            parse_observation('{"schema_version": ')

    def test_non_object_top_level_rejected(self):
        for text in ("[1, 2]", '"just a string"', "42"):
            with self.assertRaises(ObservationError, msg=text):
                parse_observation(text)

    def test_prose_rejected(self):
        with self.assertRaises(ObservationError):
            parse_observation("The frame shows a grassy field.")


class ValidateObservationTests(unittest.TestCase):
    def test_valid_document_passes(self):
        self.assertEqual(validate_observation(VALID)["observations"][0]["category"], "hud")

    def test_empty_observations_allowed_with_reason(self):
        self.assertEqual(validate_observation(EMPTY_VALID)["uncertainties"], [
            "Frame is black; nothing describable."
        ])

    def test_missing_fields_rejected(self):
        for field in ("schema_version", "timestamp", "observations", "uncertainties"):
            doc = {k: v for k, v in VALID.items() if k != field}
            with self.assertRaises(ObservationError, msg=field):
                validate_observation(doc)

    def test_wrong_schema_version_rejected(self):
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, schema_version="observation-v2"))
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, schema_version=""))

    def test_bad_timestamp_rejected(self):
        for bad in ("0", True, None, float("nan"), [0], {"t": 0}):
            with self.assertRaises(ObservationError, msg=repr(bad)):
                validate_observation(mutate(VALID, timestamp=bad))

    def test_float_timestamp_accepted(self):
        self.assertEqual(
            validate_observation(mutate(VALID, timestamp=3.5))["timestamp"], 3.5
        )

    def test_unsupported_category_rejected(self):
        doc = dict(VALID)
        doc["observations"] = [dict(VALID["observations"][0], category="killfeed")]
        with self.assertRaises(ObservationError):
            validate_observation(doc)

    def test_invalid_confidence_rejected(self):
        doc = dict(VALID)
        doc["observations"] = [dict(VALID["observations"][0], confidence="certain")]
        with self.assertRaises(ObservationError):
            validate_observation(doc)

    def test_empty_description_or_evidence_rejected(self):
        for field in ("description", "evidence"):
            doc = dict(VALID)
            doc["observations"] = [dict(VALID["observations"][0], **{field: "  "})]
            with self.assertRaises(ObservationError, msg=field):
                validate_observation(doc)

    def test_non_object_observation_rejected(self):
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, observations=["hud"]))

    def test_extra_fields_rejected(self):
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, reasoning="extra top-level"))
        doc = dict(VALID)
        doc["observations"] = [dict(VALID["observations"][0], score=0.9)]
        with self.assertRaises(ObservationError):
            validate_observation(doc)

    def test_bad_uncertainties_rejected(self):
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, uncertainties="unsure"))
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, uncertainties=["  "]))
        with self.assertRaises(ObservationError):
            validate_observation(mutate(VALID, uncertainties=[42]))

    def test_non_dict_rejected(self):
        for bad in ([], "x", None, 42):
            with self.assertRaises(ObservationError, msg=repr(bad)):
                validate_observation(bad)


class PromptTests(unittest.TestCase):
    def test_prompt_names_schema_and_enums(self):
        prompt = build_vision_prompt(0)
        self.assertIn(OBSERVATION_SCHEMA_VERSION, prompt)
        for category in OBSERVATION_CATEGORIES:
            self.assertIn(category, prompt)
        for confidence in OBSERVATION_CONFIDENCES:
            self.assertIn(confidence, prompt)
        self.assertIn("0 seconds", prompt)

    def test_frame_timestamp_matches_single_frame(self):
        self.assertEqual(vision_frame_timestamp(), 0)


if __name__ == "__main__":
    unittest.main()
