import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "memory.py"


def load_module():
    config_stub = types.ModuleType("config")
    config_stub.DATA_DIR = Path("data")

    with patch.dict(sys.modules, {"config": config_stub}):
        spec = importlib.util.spec_from_file_location("memory_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


memory_mod = load_module()


class MemoryNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.patcher = patch.object(memory_mod, "DATA_DIR", self.data)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_notes_empty(self):
        self.assertEqual(memory_mod.memory("notes"), {"notes": []})

    def test_notes_reads_file(self):
        (self.data / "notes.json").write_text('["note one", "note two"]', encoding="utf-8")
        result = memory_mod.memory("notes")
        self.assertEqual(result["notes"], ["note one", "note two"])


class MemoryDispatchTests(unittest.TestCase):
    def _stub_backend(self):
        fake_tools = types.ModuleType("tools")
        fake_sm = types.ModuleType("tools.semantic_memory")
        calls = {"save": [], "search": []}
        fake_sm.save_memory = lambda text: calls["save"].append(text) or {"saved": True}
        fake_sm.search_memory = lambda query: calls["search"].append(query) or {"memories": ["m1"]}
        fake_tools.semantic_memory = fake_sm
        return fake_tools, fake_sm, calls

    def test_save_delegates(self):
        fake_tools, fake_sm, calls = self._stub_backend()
        with patch.dict(sys.modules, {"tools": fake_tools, "tools.semantic_memory": fake_sm}):
            result = memory_mod.memory("save", "a design decision")
        self.assertEqual(result, {"saved": True})
        self.assertEqual(calls["save"], ["a design decision"])

    def test_search_delegates(self):
        fake_tools, fake_sm, calls = self._stub_backend()
        with patch.dict(sys.modules, {"tools": fake_tools, "tools.semantic_memory": fake_sm}):
            result = memory_mod.memory("search", "query")
        self.assertEqual(result, {"memories": ["m1"]})
        self.assertEqual(calls["search"], ["query"])

    def test_save_requires_text(self):
        result = memory_mod.memory("save", "")
        self.assertIn("error", result)

    def test_missing_action(self):
        result = memory_mod.memory("")
        self.assertIn("error", result)

    def test_unknown_action(self):
        result = memory_mod.memory("bogus")
        self.assertIn("error", result)

    def test_backend_unavailable_returns_error(self):
        with patch.dict(sys.modules, {"tools": None, "tools.semantic_memory": None}):
            result = memory_mod.memory("save", "x")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
