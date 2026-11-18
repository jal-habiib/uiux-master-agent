import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "design_kit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("design_kit_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


design_kit_mod = load_module()


class DesignKitPaletteTests(unittest.TestCase):
    def test_known_vibe(self):
        result = design_kit_mod.design_kit("palette", {"vibe": "warm"})
        self.assertIn("palette", result)
        self.assertEqual(result["palette"]["primary"], "#C2410C")

    def test_default_vibe_is_minimal(self):
        result = design_kit_mod.design_kit("palette")
        self.assertIn("palette", result)
        self.assertEqual(result["palette"]["primary"], "#18181B")

    def test_unknown_vibe(self):
        result = design_kit_mod.design_kit("palette", {"vibe": "nope"})
        self.assertIn("error", result)
        self.assertIn("available", result)

    def test_base_color_derivation(self):
        result = design_kit_mod.design_kit("palette", {"base": "#c2410c"})
        self.assertIn("palette", result)
        self.assertEqual(result["palette"]["primary"], "#C2410C")
        self.assertIn("tint", result["palette"])

    def test_invalid_base(self):
        result = design_kit_mod.design_kit("palette", {"base": "red"})
        self.assertIn("error", result)


class DesignKitFontTests(unittest.TestCase):
    def test_known_vibe(self):
        result = design_kit_mod.design_kit("font_pairing", {"vibe": "luxury"})
        self.assertEqual(result["heading"], "Cormorant Garamond")
        self.assertIn("google_fonts_url", result)

    def test_unknown_vibe(self):
        result = design_kit_mod.design_kit("font_pairing", {"vibe": "nope"})
        self.assertIn("error", result)


class DesignKitDirectionTests(unittest.TestCase):
    def test_matches_warm_keywords(self):
        result = design_kit_mod.design_kit("direction", {"brief": "A warm cozy local cafe landing page"})
        self.assertEqual(result["direction"], "warm")

    def test_empty_brief_defaults_minimal(self):
        result = design_kit_mod.design_kit("direction", {"brief": ""})
        self.assertEqual(result["direction"], "minimal")
        self.assertIn("palette", result)
        self.assertIn("fonts", result)
        self.assertIn("style_tokens", result)


class DesignKitSampleDataTests(unittest.TestCase):
    def test_pricing(self):
        result = design_kit_mod.design_kit("sample_data", {"kind": "pricing"})
        self.assertEqual(len(result["data"]), 3)

    def test_unknown_kind(self):
        result = design_kit_mod.design_kit("sample_data", {"kind": "nope"})
        self.assertIn("error", result)


class DesignKitExtractPaletteTests(unittest.TestCase):
    def test_dedup_and_normalize(self):
        result = design_kit_mod.design_kit("extract_palette", {"hexes": ["#C2410C", "#c2410c", "#FFFFFF"]})
        self.assertEqual(result["palette"], ["#C2410C", "#FFFFFF"])
        self.assertIn("roles", result)
        self.assertIn("contrasts", result)

    def test_no_valid_hexes(self):
        result = design_kit_mod.design_kit("extract_palette", {"hexes": ["red", "blue"]})
        self.assertIn("error", result)


class DesignKitDispatchTests(unittest.TestCase):
    def test_missing_action(self):
        result = design_kit_mod.design_kit("")
        self.assertIn("error", result)
        self.assertIn("actions", result)

    def test_unknown_action(self):
        result = design_kit_mod.design_kit("bogus")
        self.assertIn("error", result)

    def test_options_as_json_string(self):
        result = design_kit_mod.design_kit("palette", '{"vibe": "nature"}')
        self.assertEqual(result["palette"]["primary"], "#166534")


if __name__ == "__main__":
    unittest.main()
