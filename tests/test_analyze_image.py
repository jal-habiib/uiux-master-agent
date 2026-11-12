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


if __name__ == "__main__":
    unittest.main()
