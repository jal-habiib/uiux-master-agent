from pathlib import Path

from config import OUTPUT_DIR


def edit_file(filename: str, search_string: str, replace_string: str) -> dict:
    """Replace an exact string in an existing output file."""
    safe_filename = Path(filename).name
    path = OUTPUT_DIR / safe_filename

    try:
        content = path.read_text(encoding="utf-8")
        if not search_string or search_string not in content:
            return {"error": "Search string not found. Please check the code."}

        path.write_text(
            content.replace(search_string, replace_string),
            encoding="utf-8",
        )
        return {"edited": True, "filename": safe_filename, "path": str(path)}
    except FileNotFoundError:
        return {"error": f"File not found: {safe_filename}"}
    except OSError as exc:
        return {"error": f"Unable to edit file '{safe_filename}': {exc}"}
