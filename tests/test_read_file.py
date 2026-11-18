import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "read_file.py"


def load_module():
    config_stub = types.ModuleType("config")
    config_stub.OUTPUT_DIR = Path("output")
    config_stub.DATA_DIR = Path("data")
    config_stub.PROMPTS_DIR = Path("prompts")

    with patch.dict(sys.modules, {"config": config_stub}):
        spec = importlib.util.spec_from_file_location("read_file_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


read_file_mod = load_module()


class ReadFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.output = root / "output"
        self.data = root / "data"
        self.prompts = root / "prompts"
        for directory in (self.output, self.data, self.prompts):
            directory.mkdir()

        for attr, value in (
            ("OUTPUT_DIR", self.output),
            ("DATA_DIR", self.data),
            ("PROMPTS_DIR", self.prompts),
        ):
            patcher = patch.object(read_file_mod, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_list_files(self):
        (self.output / "a.html").write_text("<p>a</p>", encoding="utf-8")
        (self.data / "b.json").write_text("{}", encoding="utf-8")

        result = read_file_mod.read_file("list")

        self.assertIn("files", result)
        self.assertEqual(result["files"]["output"], ["a.html"])
        self.assertEqual(result["files"]["data"], ["b.json"])
        self.assertEqual(result["files"]["prompts"], [])

    def test_no_path_lists_files(self):
        (self.output / "page.html").write_text("<h1>Hi</h1>", encoding="utf-8")

        result = read_file_mod.read_file()

        self.assertIn("files", result)
        self.assertEqual(result["files"]["output"], ["page.html"])

    def test_read_file_in_output(self):
        (self.output / "page.html").write_text("<h1>Hello</h1>", encoding="utf-8")

        result = read_file_mod.read_file("page.html")

        self.assertIn("content", result)
        self.assertIn("<h1>Hello</h1>", result["content"])

    def test_read_file_in_data(self):
        (self.data / "notes.json").write_text("[]", encoding="utf-8")

        result = read_file_mod.read_file("notes.json")

        self.assertIn("content", result)
        self.assertEqual(result["content"], "[]")

    def test_traversal_rejected(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        result = read_file_mod.read_file("../outside.txt")

        self.assertIn("error", result)

    def test_absolute_path_outside_roots_rejected(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        result = read_file_mod.read_file(str(outside))

        self.assertIn("error", result)

    def test_missing_file_returns_error_and_listing(self):
        result = read_file_mod.read_file("nope.html")

        self.assertIn("error", result)
        self.assertIn("available_files", result)

    def test_non_string_path_rejected(self):
        result = read_file_mod.read_file(["not", "a", "string"])

        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
