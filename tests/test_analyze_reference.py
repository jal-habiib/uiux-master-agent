import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "analyze_reference.py"


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_reference_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ar_mod = load_module()


class HexExtractionTests(unittest.TestCase):
    def test_extracts_and_dedups(self):
        text = "Background #FDF6EC and primary #C2410C, plus #fdf6ec again."
        self.assertEqual(ar_mod._hexes_from_text(text), ["#FDF6EC", "#C2410C"])

    def test_empty_and_colorless(self):
        self.assertEqual(ar_mod._hexes_from_text(""), [])
        self.assertEqual(ar_mod._hexes_from_text("no colors here"), [])


class AnalyzeReferenceTests(unittest.TestCase):
    def _stub_analyze_image(self, return_value):
        fake_tools = types.ModuleType("tools")
        fake_ai = types.ModuleType("tools.analyze_image")
        fake_ai.analyze_image = lambda image_path: return_value
        fake_tools.analyze_image = fake_ai
        return fake_tools, fake_ai

    def test_wraps_vision_and_extracts_palette(self):
        fake_tools, fake_ai = self._stub_analyze_image({"analysis": "Uses #FDF6EC bg and #C2410C primary."})
        with patch.dict(sys.modules, {"tools": fake_tools, "tools.analyze_image": fake_ai}):
            result = ar_mod.analyze_reference("img.png")
        self.assertEqual(result["palette"], ["#FDF6EC", "#C2410C"])
        self.assertIn("reference_analysis", result)

    def test_propagates_error(self):
        fake_tools, fake_ai = self._stub_analyze_image({"error": "bad image"})
        with patch.dict(sys.modules, {"tools": fake_tools, "tools.analyze_image": fake_ai}):
            result = ar_mod.analyze_reference("img.png")
        self.assertIn("error", result)

    def test_backend_unavailable_returns_error(self):
        with patch.dict(sys.modules, {"tools": None}):
            result = ar_mod.analyze_reference("img.png")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
