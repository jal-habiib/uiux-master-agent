"""Analyze a reference image into a design-system summary.

A thin wrapper over analyze_image (vision): it returns the vision description
plus the hex palette extracted deterministically from that description.
"""

import re

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def _hexes_from_text(text: str) -> list[str]:
    if not text:
        return []
    seen = []
    for match in _HEX_RE.findall(text):
        hex_value = match.upper()
        if hex_value not in seen:
            seen.append(hex_value)
    return seen


def analyze_reference(image_path: str) -> dict:
    try:
        from tools.analyze_image import analyze_image
    except Exception as exc:
        return {"error": f"Unable to analyze reference: {exc}"}

    try:
        result = analyze_image(image_path)
    except Exception as exc:
        return {"error": f"Unable to analyze reference: {exc}"}

    if not isinstance(result, dict):
        return {"error": f"Unexpected analyze_image result: {result!r}"}
    if "error" in result:
        return result

    text = result.get("analysis", "")
    return {"reference_analysis": text, "palette": _hexes_from_text(text)}
