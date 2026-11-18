"""Bundled memory tool: save / search / read notes through one entry point."""

import json

from config import DATA_DIR

_ACTIONS = ("save", "search", "notes")


def _read_notes() -> dict:
    try:
        path = DATA_DIR / "notes.json"
        notes = json.loads(path.read_text()) if path.exists() else []
        return {"notes": notes}
    except Exception:
        return {"notes": []}


def memory(action: str, text: str = "") -> dict:
    """save -> save_memory(text); search -> search_memory(text); notes -> read notes."""
    if not isinstance(action, str) or not action.strip():
        return {"error": "action is required.", "actions": list(_ACTIONS)}

    action = action.strip().lower()

    if action == "notes":
        return _read_notes()

    if action in ("save", "search"):
        if not isinstance(text, str) or not text.strip():
            return {"error": f"text is required for action '{action}'."}
        try:
            from tools.semantic_memory import save_memory, search_memory
        except Exception as exc:
            return {"error": f"Memory backend unavailable: {exc}"}
        return save_memory(text) if action == "save" else search_memory(text)

    return {"error": f"Unknown action '{action}'.", "actions": list(_ACTIONS)}
