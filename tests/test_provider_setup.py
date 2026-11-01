"""Provider setup boundary tests use a fake SDK client; no network calls."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "python" / "provider_setup.py"
spec = importlib.util.spec_from_file_location("provider_setup", MODULE_PATH)
provider_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider_setup)


class FakeModels:
    def __init__(self):
        self.calls = 0

    def list(self):
        self.calls += 1
        return SimpleNamespace(data=[SimpleNamespace(id="vision-candidate"), SimpleNamespace(id="text-only")])


class ProviderSetupTests(unittest.TestCase):
    def setUp(self):
        self.models = FakeModels()
        self.client = SimpleNamespace(models=self.models, chat=SimpleNamespace(completions=SimpleNamespace(create=mock.Mock(return_value=SimpleNamespace(choices=[object()])))), close=mock.Mock())

    def test_model_discovery_marks_every_model_unverified(self):
        with mock.patch.dict("os.environ", {"AI_COMMENTATOR_PROVIDER_API_KEY": "test-key"}), mock.patch.object(provider_setup, "OpenAI", return_value=self.client):
            result = provider_setup.run("models", "https://example.com/v1", "")
        self.assertEqual(result["models"], [{"id": "vision-candidate", "vision": "unverified"}, {"id": "text-only", "vision": "unverified"}])
        self.client.close.assert_called_once()

    def test_connection_tests_models_endpoint(self):
        with mock.patch.dict("os.environ", {"AI_COMMENTATOR_PROVIDER_API_KEY": "test-key"}), mock.patch.object(provider_setup, "OpenAI", return_value=self.client):
            self.assertEqual(provider_setup.run("connection", "https://example.com/v1", ""), {"ok": True, "connected": True})
        self.assertEqual(self.models.calls, 1)

    def test_vision_verification_sends_an_image(self):
        with mock.patch.dict("os.environ", {"AI_COMMENTATOR_PROVIDER_API_KEY": "test-key"}), mock.patch.object(provider_setup, "OpenAI", return_value=self.client):
            result = provider_setup.run("verify-vision", "https://example.com/v1", "vision-model")
        self.assertEqual(result, {"ok": True, "visionVerified": True})
        message = self.client.chat.completions.create.call_args.kwargs["messages"][0]
        self.assertEqual(message["content"][1]["type"], "image_url")
        self.client.close.assert_called_once()

    def test_missing_credential_fails_without_client_creation(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(provider_setup, "OpenAI") as client:
            with self.assertRaisesRegex(ValueError, "credential is missing"):
                provider_setup.run("connection", "https://example.com/v1", "")
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
