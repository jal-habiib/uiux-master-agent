from config import OUTPUT_DIR
from pathlib import Path
import re


_HTML_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}(?:\.html)?")


def _available_files() -> list[str]:
    try:
        return sorted(path.name for path in OUTPUT_DIR.iterdir() if path.is_file())
    except OSError:
        return []


def save_file(filename: str, content: str) -> dict:
    try:
        if not isinstance(filename, str) or not _HTML_FILENAME.fullmatch(filename):
            raise ValueError("Filename must be a single safe HTML filename.")

        # Ensure it has a .html extension
        if not filename.endswith(".html"):
            filename += ".html"
            
        output_root = OUTPUT_DIR.resolve()
        path = output_root / filename
        if path.is_symlink() or path.resolve().parent != output_root:
            raise ValueError("File must resolve inside the output directory.")
        if path.exists():
            stem = path.stem
            suffix = path.suffix
            version = 2
            while True:
                versioned_path = OUTPUT_DIR / f"{stem}_v{version}{suffix}"
                if (
                    not versioned_path.exists()
                    and not versioned_path.is_symlink()
                    and versioned_path.resolve().parent == output_root
                ):
                    path = versioned_path
                    filename = path.name
                    break
                version += 1

        path.write_text(content, encoding="utf-8")
        return {"saved": True, "filename": filename, "path": str(path)}
    except Exception as e:
        return {
            "error": f"Unable to save file '{filename}': {e}",
            "available_files": _available_files(),
        }