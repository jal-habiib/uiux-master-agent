from pathlib import Path
import re

from config import OUTPUT_DIR


_HTML_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.html")


def _validated_output_path(filename: object) -> tuple[str, Path]:
    if not isinstance(filename, str) or not _HTML_FILENAME.fullmatch(filename):
        raise ValueError("Filename must be a single safe .html filename.")

    output_root = Path(OUTPUT_DIR).resolve()
    path = output_root / filename
    if path.is_symlink() or path.resolve().parent != output_root:
        raise ValueError("File must resolve inside the output directory.")
    return filename, path


def edit_file(filename: str, search_string: str, replace_string: str) -> dict:
    """Replace an exact string in an existing output file."""
    try:
        if not isinstance(search_string, str) or not isinstance(replace_string, str):
            raise ValueError("Search and replacement values must be strings.")
        safe_filename, path = _validated_output_path(filename)
        content = path.read_text(encoding="utf-8")
        if not search_string or search_string not in content:
            return {"error": "Search string not found. Please check the code."}

        path.write_text(
            content.replace(search_string, replace_string),
            encoding="utf-8",
        )
        return {"edited": True, "filename": safe_filename, "path": str(path)}
    except FileNotFoundError:
        return {"error": f"File not found: {filename}"}
    except OSError as exc:
        return {"error": f"Unable to edit file '{filename}': {exc}"}
    except ValueError as exc:
        return {"error": str(exc)}
