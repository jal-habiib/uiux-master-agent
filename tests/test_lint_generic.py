import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "lint_generic.py"
spec = importlib.util.spec_from_file_location("lint_generic_under_test", MODULE_PATH)
lint_generic_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint_generic_mod)


def rule_names(html):
    return [finding["rule"] for finding in lint_generic_mod.lint_generic(html)]


class LintGenericTests(unittest.TestCase):
    def test_empty_input_returns_no_findings(self):
        self.assertEqual(lint_generic_mod.lint_generic(""), [])
        self.assertEqual(lint_generic_mod.lint_generic("   "), [])
        self.assertEqual(lint_generic_mod.lint_generic(None), [])

    def test_findings_have_expected_shape(self):
        html = "<div class='bg-gradient-to-r from-purple-600 to-blue-600'></div>"
        findings = lint_generic_mod.lint_generic(html)
        self.assertTrue(findings)
        for finding in findings:
            self.assertEqual(set(finding), {"rule", "severity", "message"})
            self.assertIn(finding["severity"], ("info", "warning"))

    def test_single_default_font(self):
        html = "<style>body { font-family: Inter; }</style>"
        self.assertIn("single-default-font", rule_names(html))

    def test_purple_blue_gradient_tailwind_classes(self):
        html = "<div class='bg-gradient-to-r from-purple-600 to-blue-600'></div>"
        self.assertIn("purple-blue-gradient", rule_names(html))

    def test_purple_blue_gradient_hex(self):
        html = "<div style='background: linear-gradient(#8b5cf6, #3b82f6)'></div>"
        self.assertIn("purple-blue-gradient", rule_names(html))

    def test_repeated_card_blocks(self):
        html = (
            "<div class='card p-4'>A</div>"
            "<div class='card p-4'>B</div>"
            "<div class='card p-4'>C</div>"
        )
        self.assertIn("repeated-card-blocks", rule_names(html))

    def test_flat_grayscale_palette_hex(self):
        html = "<div style='color:#000; background:#fff; border:#999'></div>"
        self.assertIn("flat-grayscale-palette", rule_names(html))

    def test_flat_grayscale_palette_tailwind(self):
        html = "<div class='bg-gray-100 text-gray-900 border-slate-200'></div>"
        self.assertIn("flat-grayscale-palette", rule_names(html))

    def test_single_border_radius(self):
        html = "<div class='rounded-lg'>A</div><div class='rounded-lg'>B</div>"
        self.assertIn("single-border-radius", rule_names(html))

    def test_emoji_as_icon(self):
        html = "<button>🚀 Launch</button>"
        self.assertIn("emoji-as-icon", rule_names(html))

    def test_placeholder_content(self):
        html = "<img src='https://placehold.co/600x400'><p>Lorem ipsum dolor</p>"
        self.assertIn("placeholder-content", rule_names(html))

    def test_over_centered_layout(self):
        html = (
            "<div class='text-center'>A</div>"
            "<div class='text-center'>B</div>"
            "<div class='text-center'>C</div>"
        )
        self.assertIn("over-centered-layout", rule_names(html))

    def test_well_designed_page_has_no_findings(self):
        html = """<!doctype html><html><head><style>
        body { font-family: 'Fraunces', Georgia, serif; color: #1d4ed8; }
        .card { border-radius: 4px; }
        .btn { border-radius: 9999px; }
        </style></head><body>
        <h1>Pricing</h1>
        <div class='card'>Plan A</div>
        <div class='card featured'>Plan B</div>
        <button class='btn bg-blue-600 text-white'>Get started</button>
        </body></html>"""
        self.assertEqual(rule_names(html), [])


if __name__ == "__main__":
    unittest.main()
