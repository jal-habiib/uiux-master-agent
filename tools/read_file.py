from pathlib import Path

from config import OUTPUT_DIR, DATA_DIR, PROMPTS_DIR

_MAX_READ_CHARS = 20000

_LIST_MARKERS = {"", "list", "ls", ".", "files"}


def _allowed_roots() -> list[Path]:
    return [
        Path(OUTPUT_DIR).resolve(),
        Path(DATA_DIR).resolve(),
        Path(PROMPTS_DIR).resolve(),
    ]


def _list_files() -> dict:
    files: dict[str, list[str]] = {}
    for label, root in (
        ("output", OUTPUT_DIR),
        ("data", DATA_DIR),
        ("prompts", PROMPTS_DIR),
    ):
        try:
            files[label] = sorted(p.name for p in Path(root).iterdir() if p.is_file())
        except OSError:
            files[label] = []
    return {"files": files}


def _resolve_within_roots(path_text: str) -> tuple[Path | None, bool]:
    """Return (existing_file_or_None, was_inside_allowed_roots)."""
    candidate = Path(path_text)
    roots = _allowed_roots()
    if candidate.is_absolute():
        candidates = [candidate]
    else:
        candidates = [root / candidate for root in roots]

    inside = False
    for cand in candidates:
        try:
            resolved = cand.resolve(strict=False)
        except OSError:
            continue
        if any(resolved == root or root in resolved.parents for root in roots):
            inside = True
            if resolved.is_file():
                return resolved, True
    return None, inside


def read_file(path: str = "") -> dict:
    """Read a file inside output/, data/, or prompts/ — or list files."""
    if not isinstance(path, str):
        return {"error": "path must be a string."}

    if path.strip().lower() in _LIST_MARKERS:
        return _list_files()

    resolved, inside = _resolve_within_roots(path)
    if resolved is None:
        if inside:
            error = f"File not found: {path}"
        else:
            error = (
                f"Cannot read '{path}': it must resolve inside output/, data/, "
                "or prompts/."
            )
        return {"error": error, "available_files": _list_files()["files"]}

    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"Unable to read '{path}': {exc}"}

    truncated = len(text) > _MAX_READ_CHARS
    if truncated:
        text = text[:_MAX_READ_CHARS]

    result: dict = {"path": str(resolved), "content": text}
    if truncated:
        result["truncated"] = True
        result["note"] = f"Content truncated to {_MAX_READ_CHARS} characters."
    return result
