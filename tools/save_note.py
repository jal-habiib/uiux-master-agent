import json
from datetime import datetime
from config import DATA_DIR

def save_design_note(text: str) -> dict:
    path = DATA_DIR / "notes.json"
    notes = json.loads(path.read_text()) if path.exists() else []
    notes.append({"ts": datetime.utcnow().isoformat(), "text": text})
    path.write_text(json.dumps(notes, indent=2))
    return {"saved": True, "total": len(notes)}