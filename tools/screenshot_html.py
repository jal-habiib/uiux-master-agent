from pathlib import Path
import re

from playwright.sync_api import sync_playwright

from config import OUTPUT_DIR


_HTML_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.html")


def _available_files() -> list[str]:
    try:
        return sorted(path.name for path in OUTPUT_DIR.iterdir() if path.is_file())
    except OSError:
        return []


def screenshot_html(filename: str) -> dict:
    """Capture full-page screenshots at mobile, tablet, and desktop sizes."""
    console_logs = []
    try:
        output_dir = OUTPUT_DIR.resolve()
        if not isinstance(filename, str) or not _HTML_FILENAME.fullmatch(filename):
            return {
                "error": "Filename must be a single safe .html filename",
                "available_files": _available_files(),
                "console_logs": console_logs,
            }
        source_path = output_dir / filename
        if source_path.is_symlink():
            return {
                "error": "Source file must not be a symlink",
                "available_files": _available_files(),
                "console_logs": console_logs,
            }
        html_path = source_path.resolve()

        if html_path.parent != output_dir:
            return {
                "error": "File must be inside the output folder",
                "available_files": _available_files(),
                "console_logs": console_logs,
            }
        if not html_path.is_file():
            return {
                "error": "File not found",
                "available_files": _available_files(),
                "console_logs": console_logs,
            }

        viewports = {
            "mobile": (375, 812),
            "tablet": (768, 1024),
            "desktop": (1440, 900),
        }
        screenshot_paths = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for device, (width, height) in viewports.items():
                    screenshot_path = OUTPUT_DIR / f"screenshot_{filename}_{device}.png"
                    if screenshot_path.is_symlink() or screenshot_path.resolve().parent != output_dir:
                        raise ValueError("Screenshot path must remain inside the output folder")
                    page = browser.new_page(viewport={"width": width, "height": height})
                    try:
                        page.on(
                            "console",
                            lambda msg: console_logs.append(msg.text)
                            if msg.type == "error"
                            else None,
                        )
                        page.goto(html_path.as_uri())
                        page.screenshot(path=str(screenshot_path), full_page=True)
                        screenshot_paths.append(str(screenshot_path))
                    finally:
                        page.close()
            finally:
                browser.close()

        return {"paths": screenshot_paths, "console_logs": console_logs}
    except Exception as exc:
        return {
            "error": f"Unable to screenshot '{filename}': {exc}",
            "available_files": _available_files(),
            "console_logs": console_logs,
        }
