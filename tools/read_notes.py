import json

from config import DATA_DIR


def read_notes() -> dict:
    try:
        path = DATA_DIR / "notes.json"
        notes = json.loads(path.read_text()) if path.exists() else []
        return {"notes": notes}
    except Exception:
        return {"notes": []}
