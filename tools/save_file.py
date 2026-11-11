from config import OUTPUT_DIR
from pathlib import Path


def _available_files() -> list[str]:
    try:
        return sorted(path.name for path in OUTPUT_DIR.iterdir() if path.is_file())
    except OSError:
        return []


def save_file(filename: str, content: str) -> dict:
    try:
        filename = Path(filename).name

        # Ensure it has a .html extension
        if not filename.endswith(".html"):
            filename += ".html"
            
        path = OUTPUT_DIR / filename
        if path.exists():
            stem = path.stem
            suffix = path.suffix
            version = 2
            while True:
                versioned_path = OUTPUT_DIR / f"{stem}_v{version}{suffix}"
                if not versioned_path.exists():
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