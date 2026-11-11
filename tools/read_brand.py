import json

from config import DATA_DIR


def read_brand() -> dict:
    """Read the brand kit from data/brand.json."""
    return json.loads((DATA_DIR / "brand.json").read_text())
