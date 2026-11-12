import tempfile
import unittest
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


def load_module(filename, name):
    module_path = Path(__file__).resolve().parents[1] / "tools" / filename
    config_stub = types.ModuleType("config")
    config_stub.OUTPUT_DIR = Path("output")
    modules = {"config": config_stub}
    if filename == "screenshot_html.py":
        playwright_stub = types.ModuleType("playwright")
        sync_api_stub = types.ModuleType("playwright.sync_api")
        sync_api_stub.sync_playwright = None
        modules.update(
            {
                "playwright": playwright_stub,
                "playwright.sync_api": sync_api_stub,
            }
        )
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


save_file = load_module("save_file.py", "save_file_under_test")
screenshot_html = load_module("screenshot_html.py", "screenshot_html_under_test")


class SaveFileSecurityTests(unittest.TestCase):
    def test_rejects_broken_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "output"
            output_dir.mkdir()
            outside = root / "outside.html"
            link = output_dir / "card.html"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")
            with patch.object(save_file, "OUTPUT_DIR", output_dir):
                result = save_file.save_file("card", "should not escape")
            self.assertIn("error", result)
            self.assertFalse(outside.exists())


class ScreenshotHtmlSecurityTests(unittest.TestCase):
    def test_rejects_path_syntax_before_browser_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch.object(screenshot_html, "OUTPUT_DIR", output_dir):
                result = screenshot_html.screenshot_html("../card.html")
            self.assertIn("error", result)

    def test_rejects_source_symlink_inside_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            target = output_dir / "target.html"
            target.write_text("<html></html>", encoding="utf-8")
            link = output_dir / "card.html"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")
            with patch.object(screenshot_html, "OUTPUT_DIR", output_dir):
                result = screenshot_html.screenshot_html("card.html")
            self.assertIn("error", result)

    def test_rejects_screenshot_destination_symlink_before_page_screenshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "card.html").write_text("<html></html>", encoding="utf-8")
            outside = root / "outside.png"
            destination = output_dir / "screenshot_card.html_mobile.png"
            try:
                destination.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")

            class FakeBrowser:
                def new_page(self, **kwargs):
                    raise AssertionError("A page must not be created for a symlink destination.")

                def close(self):
                    pass

            class FakePlaywright:
                def __init__(self):
                    self.chromium = self

                def launch(self):
                    return FakeBrowser()

            class FakeContext:
                def __enter__(self):
                    return FakePlaywright()

                def __exit__(self, *args):
                    return False

            with (
                patch.object(screenshot_html, "OUTPUT_DIR", output_dir),
                patch.object(screenshot_html, "sync_playwright", return_value=FakeContext()),
            ):
                result = screenshot_html.screenshot_html("card.html")

            self.assertIn("error", result)
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
