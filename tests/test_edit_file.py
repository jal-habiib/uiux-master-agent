import tempfile
import unittest
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "edit_file.py"
config_stub = types.ModuleType("config")
config_stub.OUTPUT_DIR = Path("output")
with patch.dict(sys.modules, {"config": config_stub}):
    spec = importlib.util.spec_from_file_location("edit_file_under_test", MODULE_PATH)
    edit_file = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edit_file)


class EditFileSecurityTests(unittest.TestCase):
    def test_edits_valid_html_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "card.html").write_text("old", encoding="utf-8")
            with patch.object(edit_file, "OUTPUT_DIR", output_dir):
                result = edit_file.edit_file("card.html", "old", "new")
            self.assertEqual(result["edited"], True)
            self.assertEqual((output_dir / "card.html").read_text(encoding="utf-8"), "new")

    def test_rejects_unsafe_names_before_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            outside = Path(temp_dir).parent / "outside.html"
            with patch.object(edit_file, "OUTPUT_DIR", output_dir):
                for name in ("../outside.html", r"..\outside.html", "C:\\outside.html", "nested/card.html"):
                    with self.subTest(name=name):
                        result = edit_file.edit_file(name, "old", "new")
                        self.assertIn("error", result)
            self.assertFalse(outside.exists())

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "output"
            output_dir.mkdir()
            outside = root / "outside.html"
            outside.write_text("keep", encoding="utf-8")
            link = output_dir / "card.html"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")
            with patch.object(edit_file, "OUTPUT_DIR", output_dir):
                result = edit_file.edit_file("card.html", "keep", "changed")
            self.assertIn("error", result)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_rejects_symlink_to_file_inside_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            existing = output_dir / "existing.html"
            existing.write_text("keep", encoding="utf-8")
            link = output_dir / "card.html"
            try:
                link.symlink_to(existing)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")
            with patch.object(edit_file, "OUTPUT_DIR", output_dir):
                result = edit_file.edit_file("card.html", "keep", "changed")
            self.assertIn("error", result)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_rejects_non_string_search_and_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "card.html").write_text("keep", encoding="utf-8")
            with patch.object(edit_file, "OUTPUT_DIR", output_dir):
                for search, replacement in ((None, "new"), ("keep", None), ([], "new")):
                    with self.subTest(search=search, replacement=replacement):
                        result = edit_file.edit_file("card.html", search, replacement)
                        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
