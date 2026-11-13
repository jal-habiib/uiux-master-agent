import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "analyze_image.py"


def load_module():
    config_stub = types.ModuleType("config")
    config_stub.API_KEY = "test-key"
    config_stub.BASE_URL = "https://example.invalid"
    config_stub.MODEL = "test-model"
    config_stub.ROOT = Path.cwd()
    config_stub.PROVIDERS = [{
        "provider_id": "test",
        "name": "Test",
        "api_key": "test-key",
        "base_url": "https://example.invalid",
        "model_id": "test-model",
        "capabilities": {"text", "vision"},
        "timeout": 30.0,
        "priority": 1,
        "enabled": True,
    }]

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = Mock()

    with patch.dict(sys.modules, {"config": config_stub, "openai": openai_stub}):
        spec = importlib.util.spec_from_file_location("analyze_image_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


analyze_image_module = load_module()


class AnalyzeImageSecurityTests(unittest.TestCase):
    def test_rejects_files_outside_upload_directory_without_provider_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = Path(temp_dir) / "private.png"
            outside.write_bytes(b"private data")
            with (
                patch.object(analyze_image_module, "UPLOADS_DIR", Path(temp_dir) / "uploads"),
                patch.object(analyze_image_module, "OpenAI") as provider,
            ):
                result = analyze_image_module.analyze_image(str(outside))

            self.assertIn("error", result)
            provider.assert_not_called()

    def test_rejects_unsupported_file_types(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            uploads = Path(temp_dir) / "uploads"
            uploads.mkdir()
            document = uploads / "notes.txt"
            document.write_text("not an image", encoding="utf-8")
            with (
                patch.object(analyze_image_module, "UPLOADS_DIR", uploads),
                patch.object(analyze_image_module, "OpenAI") as provider,
            ):
                result = analyze_image_module.analyze_image(str(document))

            self.assertIn("error", result)
            provider.assert_not_called()

    def test_rejects_symlink_to_file_outside_upload_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            uploads = root / "uploads"
            uploads.mkdir()
            outside = root / "private.png"
            outside.write_bytes(b"private data")
            link = uploads / "uploaded.png"

            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")

            with (
                patch.object(analyze_image_module, "UPLOADS_DIR", uploads),
                patch.object(analyze_image_module, "OpenAI") as provider,
            ):
                result = analyze_image_module.analyze_image(str(link))

            self.assertIn("error", result)
            provider.assert_not_called()

    def test_valid_uploaded_image_is_sent_to_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            uploads = Path(temp_dir) / "uploads"
            uploads.mkdir()
            image = uploads / "screen.png"
            image.write_bytes(b"png-data")

            response = Mock()
            response.choices = [Mock(message=Mock(content="analysis"))]
            client = Mock()
            client.chat.completions.create.return_value = response

            with (
                patch.object(analyze_image_module, "UPLOADS_DIR", uploads),
                patch.object(analyze_image_module, "OpenAI", return_value=client) as provider,
            ):
                result = analyze_image_module.analyze_image(str(image))

            self.assertEqual(result, {"analysis": "analysis"})
            provider.assert_called_once()
            client.chat.completions.create.assert_called_once()

    def test_shared_gateway_receives_vision_requirements_and_configured_providers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            uploads = Path(temp_dir) / "uploads"
            uploads.mkdir()
            image = uploads / "screen.png"
            image.write_bytes(b"png-data")
            gateway_result = types.SimpleNamespace(
                response=Mock(choices=[Mock(message=Mock(content="analysis"))]),
                failures=[],
            )
            with (
                patch.object(analyze_image_module, "UPLOADS_DIR", uploads),
                patch.object(analyze_image_module, "call", return_value=gateway_result) as gateway,
            ):
                result = analyze_image_module.analyze_image(str(image))

            self.assertEqual(result, {"analysis": "analysis"})
            self.assertEqual(gateway.call_args.kwargs["required_capabilities"], {"text", "vision"})
            self.assertIs(
                gateway.call_args.kwargs["providers"],
                analyze_image_module.config.PROVIDERS,
            )

    def test_no_eligible_provider_returns_actionable_diagnostic_without_call(self):
        original = analyze_image_module.config.PROVIDERS
        analyze_image_module.config.PROVIDERS = [
            {
                **original[0],
                "name": "TextOnly",
                "capabilities": {"text"},
                "capability_diagnostic": "missing vision capability",
            }
        ]
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                uploads = Path(temp_dir) / "uploads"
                uploads.mkdir()
                image = uploads / "screen.png"
                image.write_bytes(b"png-data")
                with (
                    patch.object(analyze_image_module, "UPLOADS_DIR", uploads),
                    patch.object(analyze_image_module, "OpenAI") as provider,
                ):
                    result = analyze_image_module.analyze_image(str(image))
        finally:
            analyze_image_module.config.PROVIDERS = original

        self.assertIn("required capabilities: text, vision", result["error"])
        self.assertIn("TextOnly", result["error"])
        self.assertIn("missing vision capability", result["error"])
        provider.assert_not_called()

    def test_gateway_falls_back_to_eligible_vision_provider(self):
        original = analyze_image_module.config.PROVIDERS
        analyze_image_module.config.PROVIDERS = [
            {**original[0], "name": "VisionDown", "api_key": "down", "capabilities": {"text", "vision"}, "priority": 1},
            {**original[0], "name": "TextOnly", "api_key": "text", "capabilities": {"text"}, "priority": 2},
            {**original[0], "name": "VisionBackup", "api_key": "backup", "capabilities": {"text", "vision"}, "priority": 3},
        ]
        clients = []

        def make_client(**kwargs):
            client = Mock()
            if kwargs["api_key"] == "down":
                client.chat.completions.create.side_effect = RuntimeError("down")
            else:
                client.chat.completions.create.return_value = Mock(
                    choices=[Mock(message=Mock(content="fallback analysis"))]
                )
            clients.append((kwargs["api_key"], client))
            return client

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                uploads = Path(temp_dir) / "uploads"
                uploads.mkdir()
                image = uploads / "screen.png"
                image.write_bytes(b"png-data")
                with (
                    patch.object(analyze_image_module, "UPLOADS_DIR", uploads),
                    patch.object(analyze_image_module, "OpenAI", side_effect=make_client),
                ):
                    result = analyze_image_module.analyze_image(str(image))
        finally:
            analyze_image_module.config.PROVIDERS = original

        self.assertEqual(result, {"analysis": "fallback analysis"})
        self.assertEqual([key for key, _ in clients], ["down", "backup"])
        self.assertEqual(clients[0][1].chat.completions.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
