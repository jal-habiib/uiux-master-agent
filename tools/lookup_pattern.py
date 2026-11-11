import json
from config import DATA_DIR

def lookup_pattern(name: str) -> dict:
    data = json.loads((DATA_DIR / "patterns.json").read_text())
    if name in data:
        return {"name": name, **data[name]}
    return {"error": f"Pattern '{name}' not found", "available": list(data.keys())}