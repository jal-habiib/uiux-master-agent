import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "generate_component.py"


def load_module():
    config_stub = types.ModuleType("config")
    config_stub.OUTPUT_DIR = Path("output")
    renderer_stub = types.ModuleType("renderers")
    renderer_stub.get_renderer = lambda target: (lambda spec: "<p>stub</p>")

    with patch.dict(sys.modules, {"config": config_stub, "renderers": renderer_stub}):
        spec = importlib.util.spec_from_file_location("generate_component_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


generate_component_module = load_module()


class GenerateComponentPathTests(unittest.TestCase):
    def test_valid_name_is_saved_inside_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            with (
                patch.object(generate_component_module, "OUTPUT_DIR", output_dir),
                patch.object(generate_component_module, "get_renderer", return_value=lambda spec: "<p>ok</p>"),
            ):
                result = generate_component_module.generate_component({"name": "pricing-card"})

            self.assertNotIn("error", result)
            self.assertEqual(Path(result["saved_to"]), output_dir / "pricing-card.html")
            self.assertEqual(
                (output_dir / "pricing-card.html").read_text(encoding="utf-8"),
                "<p>ok</p>",
            )

    def test_rejects_traversal_and_path_syntax(self):
        invalid_names = (
            "../outside",
            "..",
            "nested/component",
            r"nested\component",
            "name.html",
            "",
            " leading-space",
            "a" * 65,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            with (
                patch.object(generate_component_module, "OUTPUT_DIR", output_dir),
                patch.object(generate_component_module, "get_renderer", return_value=lambda spec: "<p>ok</p>"),
            ):
                for name in invalid_names:
                    with self.subTest(name=name):
                        result = generate_component_module.generate_component({"name": name})
                        self.assertIn("error", result)

            self.assertEqual(list(output_dir.iterdir()), [])

    def test_rejects_non_string_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()

            with (
                patch.object(generate_component_module, "OUTPUT_DIR", output_dir),
                patch.object(generate_component_module, "get_renderer", return_value=lambda spec: "<p>ok</p>"),
            ):
                result = generate_component_module.generate_component(
                    {"name": ["not", "a", "filename"]}
                )

            self.assertIn("error", result)
            self.assertEqual(list(output_dir.iterdir()), [])

    def test_rejects_target_symlink_that_resolves_outside_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "output"
            output_dir.mkdir()
            outside_file = root / "outside.html"
            outside_file.write_text("keep me", encoding="utf-8")
            link = output_dir / "safe-name.html"

            try:
                link.symlink_to(outside_file)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")

            with (
                patch.object(generate_component_module, "OUTPUT_DIR", output_dir),
                patch.object(generate_component_module, "get_renderer", return_value=lambda spec: "<p>changed</p>"),
            ):
                result = generate_component_module.generate_component({"name": "safe-name"})

            self.assertIn("error", result)
            self.assertEqual(outside_file.read_text(encoding="utf-8"), "keep me")

    def test_rejects_target_symlink_to_file_inside_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            existing = output_dir / "existing.html"
            existing.write_text("keep me", encoding="utf-8")
            link = output_dir / "safe-name.html"
            try:
                link.symlink_to(existing)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")

            with (
                patch.object(generate_component_module, "OUTPUT_DIR", output_dir),
                patch.object(generate_component_module, "get_renderer", return_value=lambda spec: "<p>changed</p>"),
            ):
                result = generate_component_module.generate_component({"name": "safe-name"})

            self.assertIn("error", result)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")


if __name__ == "__main__":
    unittest.main()
