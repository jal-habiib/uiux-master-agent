"""Deterministic "generic design" linter for generated HTML.

Pure standard library (no project imports) so it is safe to import and call
from any context — including unit tests and the automatic post-save review.

`lint_generic(html)` returns a list of findings, each::

    {"rule": "<kebab-name>", "severity": "info" | "warning", "message": "<actionable fix>"}

The rules encode the specific "looks like a generic AI template" tells the
design advisor called out: a single default font, the purple-to-blue gradient,
three or more identical card blocks, a flat #000/#fff grayscale palette, a
single border radius everywhere, emoji used in place of icons, placeholder
content, and a centered-everything layout.
"""

from __future__ import annotations

import re
from collections import Counter

# Emoji ranges most likely to appear as stand-in icons.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF]"
)

_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")

_CLASS_RE = re.compile(r"""class\s*=\s*["']([^"']+)["']""")

_DEFAULT_FONTS = {
    "inter",
    "arial",
    "helvetica",
    "roboto",
    "system-ui",
    "ui-sans-serif",
    "sans-serif",
    "segoe ui",
    "verdana",
    "tahoma",
}

# Purple/violet/indigo/fuchsia and blue/sky/cyan hexes (Tailwind palette +
# common hand-written values).
_PURPLE_HEX = {
    "#6366f1", "#8b5cf6", "#a855f7", "#7c3aed", "#9333ea",
    "#6d28d9", "#7e22ce", "#d946ef", "#c026d3", "#a21caf",
}
_BLUE_HEX = {
    "#3b82f6", "#2563eb", "#1d4ed8", "#60a5fa", "#93c5fd",
    "#0ea5e9", "#0284c7", "#1e40af", "#38bdf8",
}

_PURPLE_CLASS = r"(?:purple|violet|fuchsia|indigo)"
_BLUE_CLASS = r"(?:blue|sky|cyan)"

_NEUTRAL_CLASS = r"(?:gray|slate|zinc|neutral|stone)"


def _extract_font_families(html: str) -> set[str]:
    families: set[str] = set()
    for match in re.finditer(r"font-family\s*:\s*([^;}\"']+)", html, re.IGNORECASE):
        for part in match.group(1).split(","):
            name = part.strip().strip("\"' ").lower()
            if name:
                families.add(name)
    return families


def _distinct_hex_colors(html: str) -> set[str]:
    return {match.group(0).lower() for match in _HEX_COLOR_RE.finditer(html)}


def _is_gray(hex_color: str) -> bool:
    value = hex_color.lstrip("#")
    if len(value) == 3:
        r, g, b = (int(ch * 2, 16) for ch in value)
    else:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    return r == g == b


def _normalized_class_strings(html: str) -> list[str]:
    return [" ".join(raw.split()) for raw in _CLASS_RE.findall(html)]


def _has_purple_blue_gradient(html: str) -> bool:
    lowered = html.lower()

    # Tailwind class form: from-<purple-ish> to-<blue-ish> (either direction).
    from_purple = re.search(rf"from-{_PURPLE_CLASS}\b", lowered) is not None
    to_blue = re.search(rf"to-{_BLUE_CLASS}\b", lowered) is not None
    from_blue = re.search(rf"from-{_BLUE_CLASS}\b", lowered) is not None
    to_purple = re.search(rf"to-{_PURPLE_CLASS}\b", lowered) is not None
    if (from_purple and to_blue) or (from_blue and to_purple):
        return True

    # Hex form inside a gradient declaration.
    for segment in re.finditer(r"(?:linear|radial|conic)-gradient\([^)]*\)", lowered):
        body = segment.group(0)
        if any(c in body for c in _PURPLE_HEX) and any(c in body for c in _BLUE_HEX):
            return True
    return False


def lint_generic(html: str) -> list[dict]:
    """Return deterministic generic-design findings for *html* source text."""
    findings: list[dict] = []

    if not isinstance(html, str) or not html.strip():
        return findings

    lowered = html.lower()

    # 1. Single default font.
    families = _extract_font_families(html)
    if families and len(families) == 1 and (families & _DEFAULT_FONTS):
        family = next(iter(families))
        findings.append({
            "rule": "single-default-font",
            "severity": "info",
            "message": (
                f"Uses a single default font ('{family}') with no pairing. "
                "Pair a distinctive display font with a readable body font."
            ),
        })

    # 2. Purple-to-blue gradient.
    if _has_purple_blue_gradient(html):
        findings.append({
            "rule": "purple-blue-gradient",
            "severity": "warning",
            "message": (
                "Uses the purple-to-blue gradient, a common generic tell. "
                "Choose one hue family or a brand-specific color transition instead."
            ),
        })

    # 3. Three or more identical card blocks (same class string repeated).
    class_counts = Counter(_normalized_class_strings(html))
    for class_name, count in class_counts.items():
        if class_name and count >= 3 and re.search(r"(?:card|item|feature|grid|col|tile)", class_name):
            findings.append({
                "rule": "repeated-card-blocks",
                "severity": "warning",
                "message": (
                    f"Class '{class_name[:40]}' repeats {count} times — likely three or "
                    "more identical card blocks. Vary at least one block (featured, size, "
                    "or visual weight) to break the generic grid."
                ),
            })
            break

    # 4. Flat grayscale palette (no hue beyond black/white/grays).
    hex_colors = _distinct_hex_colors(html)
    if hex_colors and all(_is_gray(c) for c in hex_colors):
        findings.append({
            "rule": "flat-grayscale-palette",
            "severity": "warning",
            "message": (
                "Palette is grayscale only (black/white/gray) with no brand tint. "
                "Introduce one accent hue for emphasis and interactive states."
            ),
        })
    else:
        # Tailwind neutral-palette-only tell (bg-/text-/border- gray|slate|zinc|neutral|stone).
        color_classes = re.findall(
            rf"(?:bg|text|border|from|to|via|ring|fill|stroke)-[a-z]+-[0-9]+",
            lowered,
        )
        if color_classes and all(
            re.match(rf"(?:bg|text|border|from|to|via|ring|fill|stroke)-{_NEUTRAL_CLASS}-", c)
            for c in color_classes
        ):
            findings.append({
                "rule": "flat-grayscale-palette",
                "severity": "warning",
                "message": (
                    "Uses only neutral Tailwind shades (gray/slate/zinc/neutral/stone) "
                    "with no accent hue. Add a brand tint for buttons and highlights."
                ),
            })

    # 5. Single border radius everywhere.
    radii = {r.strip().lower() for r in re.findall(r"border-radius\s*:\s*([^;}\"']+)", lowered)}
    rounded_classes = set(re.findall(r"\brounded(?:-[a-z0-9]+)?\b", lowered))
    distinct_radii = len(radii | rounded_classes)
    if distinct_radii == 1:
        value = next(iter(radii | rounded_classes))
        findings.append({
            "rule": "single-border-radius",
            "severity": "info",
            "message": (
                f"Every corner uses the same radius ('{value}'). Use two radii — a small "
                "one for controls and a larger one for cards — to add hierarchy."
            ),
        })

    # 6. Emoji used as icons.
    emojis = list(dict.fromkeys(_EMOJI_RE.findall(html)))[:3]
    if emojis:
        findings.append({
            "rule": "emoji-as-icon",
            "severity": "info",
            "message": (
                f"Emoji detected ({' '.join(emojis)}) — if these stand in for icons, "
                "replace them with a consistent icon set (e.g. Lucide/Heroicons) for a "
                "cohesive, professional look."
            ),
        })

    # 7. Placeholder content.
    placeholders = [
        token
        for token, pattern in (
            ("placehold.co", r"placehold\.co"),
            ("picsum.photos", r"picsum\.photos"),
            ("via.placeholder.com", r"via\.placeholder\.com"),
            ("lorem ipsum", r"lorem\s+ipsum"),
        )
        if re.search(pattern, lowered)
    ]
    if placeholders:
        findings.append({
            "rule": "placeholder-content",
            "severity": "warning",
            "message": (
                f"Placeholder content found ({', '.join(placeholders)}). Replace with "
                "real, meaningful copy and imagery before delivery."
            ),
        })

    # 8. Centered-everything layout.
    center_count = len(re.findall(r"text-center", lowered))
    center_count += len(re.findall(r"text-align\s*:\s*center", lowered))
    if center_count >= 3:
        findings.append({
            "rule": "over-centered-layout",
            "severity": "info",
            "message": (
                f"{center_count} centered blocks detected. Reserve centering for hero "
                "and CTAs; left-align body content for readability and scanability."
            ),
        })

    return findings
