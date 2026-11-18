"""Design kit: deterministic palette, font, direction, and sample-data generators.

Pure stdlib so the module loads standalone and is trivially unit-testable.
This is the "taste" layer the advisor asked for: it hands the agent concrete,
non-generic palette/font/direction choices instead of letting it drift to
purple-to-blue gradients and a single default font.
"""

import json
import re

# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _normalize_hex(value) -> str | None:
    if not isinstance(value, str):
        return None
    match = _HEX_RE.match(value.strip())
    if not match:
        return None
    return "#" + match.group(1).upper()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, c)) for c in rgb))


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _luminance(hex_color: str) -> float:
    r, g, b = (c / 255 for c in _hex_to_rgb(hex_color))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = _luminance(fg), _luminance(bg)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


# ---------------------------------------------------------------------------
# Curated data
# ---------------------------------------------------------------------------

_PALETTES = {
    "minimal": {"primary": "#18181B", "secondary": "#52525B", "accent": "#2563EB", "background": "#FFFFFF", "surface": "#FAFAFA", "text": "#18181B"},
    "warm": {"primary": "#C2410C", "secondary": "#F59E0B", "accent": "#EF4444", "background": "#FFF7ED", "surface": "#FFFFFF", "text": "#431407"},
    "cool": {"primary": "#1D4ED8", "secondary": "#0EA5E9", "accent": "#6366F1", "background": "#EFF6FF", "surface": "#FFFFFF", "text": "#0F172A"},
    "nature": {"primary": "#166534", "secondary": "#65A30D", "accent": "#D97706", "background": "#F0FDF4", "surface": "#FFFFFF", "text": "#14532D"},
    "corporate": {"primary": "#0F172A", "secondary": "#334155", "accent": "#0284C7", "background": "#F8FAFC", "surface": "#FFFFFF", "text": "#0F172A"},
    "playful": {"primary": "#DB2777", "secondary": "#8B5CF6", "accent": "#F59E0B", "background": "#FDF2F8", "surface": "#FFFFFF", "text": "#500724"},
    "luxury": {"primary": "#1C1917", "secondary": "#78350F", "accent": "#B45309", "background": "#FAFAF9", "surface": "#FFFFFF", "text": "#1C1917"},
}

_FONT_PAIRINGS = {
    "minimal": {"heading": "Space Grotesk", "body": "Inter"},
    "warm": {"heading": "Playfair Display", "body": "Inter"},
    "cool": {"heading": "Poppins", "body": "Inter"},
    "nature": {"heading": "Lora", "body": "Source Sans 3"},
    "corporate": {"heading": "Inter", "body": "Inter"},
    "playful": {"heading": "Bricolage Grotesque", "body": "Inter"},
    "luxury": {"heading": "Cormorant Garamond", "body": "Montserrat"},
}

_DIRECTIONS = {
    "minimal": {"keywords": ["minimal", "clean", "simple", "white", "airy", "sans", "spacious"]},
    "warm": {"keywords": ["warm", "cozy", "friendly", "local", "community", "cafe", "bakery", "artisan"]},
    "cool": {"keywords": ["cool", "tech", "modern", "startup", "data", "dashboard", "blue", "ai"]},
    "nature": {"keywords": ["nature", "organic", "green", "eco", "farm", "wellness", "health", "fresh"]},
    "corporate": {"keywords": ["corporate", "professional", "business", "finance", "saas", "b2b", "trust", "bank"]},
    "playful": {"keywords": ["playful", "fun", "bold", "colorful", "kids", "game", "vibrant", "energetic"]},
    "luxury": {"keywords": ["luxury", "elegant", "premium", "high-end", "classy", "gold", "boutique", "exclusive"]},
}

_STYLE_TOKENS = {
    "minimal": {"radius": "0.5rem", "shadow": "subtle", "spacing": "generous", "density": "low"},
    "warm": {"radius": "0.75rem", "shadow": "soft", "spacing": "comfortable", "density": "medium"},
    "cool": {"radius": "0.5rem", "shadow": "soft", "spacing": "compact", "density": "medium"},
    "nature": {"radius": "1rem", "shadow": "soft", "spacing": "comfortable", "density": "medium"},
    "corporate": {"radius": "0.375rem", "shadow": "crisp", "spacing": "compact", "density": "high"},
    "playful": {"radius": "1rem", "shadow": "bold", "spacing": "playful", "density": "low"},
    "luxury": {"radius": "0.25rem", "shadow": "subtle", "spacing": "generous", "density": "low"},
}

_SAMPLE_DATA = {
    "pricing": [
        {"tier": "Starter", "price": "$9", "period": "/mo", "features": ["1 project", "Community support", "Basic analytics"], "cta": "Start free"},
        {"tier": "Pro", "price": "$29", "period": "/mo", "features": ["10 projects", "Priority support", "Advanced analytics", "Custom domains"], "cta": "Get Pro"},
        {"tier": "Enterprise", "price": "Custom", "period": "", "features": ["Unlimited projects", "Dedicated manager", "SSO & audit logs"], "cta": "Contact sales"},
    ],
    "testimonials": [
        {"name": "Amina Wanjiru", "role": "Product Manager", "quote": "Cut our design time in half without sacrificing quality."},
        {"name": "Daniel Ochieng", "role": "Founder", "quote": "The result feels like a real studio shipped it."},
        {"name": "Grace Muthoni", "role": "Design Lead", "quote": "Consistent, accessible, and on-brand every time."},
    ],
    "nav": [
        {"label": "Product", "href": "#product"},
        {"label": "Pricing", "href": "#pricing"},
        {"label": "About", "href": "#about"},
        {"label": "Contact", "href": "#contact"},
    ],
    "stats": [
        {"label": "Active users", "value": "12,480", "delta": "+18%"},
        {"label": "Uptime", "value": "99.98%", "delta": "+0.2%"},
        {"label": "Avg. response", "value": "140ms", "delta": "-12%"},
        {"label": "NPS", "value": "68", "delta": "+5"},
    ],
    "features": [
        {"title": "Fast by default", "description": "Sub-second loads on every view."},
        {"title": "Accessible", "description": "WCAG AA out of the box."},
        {"title": "Secure", "description": "End-to-end encryption."},
    ],
}

_SAMPLE_DATA_KINDS = sorted(_SAMPLE_DATA)


def _google_fonts_url(heading: str, body: str) -> str:
    def slug(name: str) -> str:
        return name.replace(" ", "+")

    return "https://fonts.googleapis.com/css2?" + "&".join(
        [f"family={slug(heading)}", f"family={slug(body)}"]
    ) + "&display=swap"


def _palette_from_base(base_hex: str) -> dict:
    base = _normalize_hex(base_hex)
    rgb = _hex_to_rgb(base)
    lum = _luminance(base)
    tint = _rgb_to_hex(_mix(rgb, (255, 255, 255), 0.88))
    shade = _rgb_to_hex(_mix(rgb, (0, 0, 0), 0.55))
    accent = _rgb_to_hex(tuple(255 - c for c in rgb))
    dark = lum <= 0.5
    return {
        "primary": base,
        "secondary": shade,
        "accent": accent,
        "background": "#0B0B0F" if dark else "#FFFFFF",
        "surface": "#1A1A1F" if dark else "#FFFFFF",
        "text": "#F8FAFC" if dark else "#0F172A",
        "tint": tint,
    }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _make_palette(options: dict) -> dict:
    base = options.get("base")
    if base:
        normalized = _normalize_hex(base)
        if normalized is None:
            return {"error": f"Invalid base color '{base}'. Use a 6-digit hex like #C2410C."}
        return {"palette": _palette_from_base(normalized), "source": "base", "note": "Derived from the provided base color."}

    vibe = str(options.get("vibe", "minimal")).strip().lower()
    if vibe not in _PALETTES:
        return {"error": f"Unknown vibe '{vibe}'.", "available": sorted(_PALETTES)}
    return {"palette": dict(_PALETTES[vibe]), "vibe": vibe}


def _font_pairing(options: dict) -> dict:
    vibe = str(options.get("vibe", "minimal")).strip().lower()
    if vibe not in _FONT_PAIRINGS:
        return {"error": f"Unknown vibe '{vibe}'.", "available": sorted(_FONT_PAIRINGS)}
    heading = _FONT_PAIRINGS[vibe]["heading"]
    body = _FONT_PAIRINGS[vibe]["body"]
    return {"vibe": vibe, "heading": heading, "body": body, "google_fonts_url": _google_fonts_url(heading, body)}


def _pick_direction(options: dict) -> dict:
    brief = str(options.get("brief", "")).strip().lower()
    scores = {name: sum(1 for kw in spec["keywords"] if kw in brief) for name, spec in _DIRECTIONS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        best = "minimal"
        rationale = "No strong signals in the brief; defaulting to a clean, safe minimal direction."
    else:
        matched = [kw for kw in _DIRECTIONS[best]["keywords"] if kw in brief]
        rationale = f"Brief matched {best} signals: {', '.join(matched)}."
    return {
        "direction": best,
        "rationale": rationale,
        "palette": dict(_PALETTES[best]),
        "fonts": dict(_FONT_PAIRINGS[best]),
        "style_tokens": dict(_STYLE_TOKENS[best]),
    }


def _sample_data(options: dict) -> dict:
    kind = str(options.get("kind", "")).strip().lower()
    if kind not in _SAMPLE_DATA:
        return {"error": f"Unknown kind '{kind}'.", "available": _SAMPLE_DATA_KINDS}
    return {"kind": kind, "data": _SAMPLE_DATA[kind]}


def _extract_palette(options: dict) -> dict:
    raw = options.get("hexes", options.get("colors", []))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return {"error": "Provide 'hexes' as a list of hex colors."}

    normalized = []
    for item in raw:
        h = _normalize_hex(item)
        if h and h not in normalized:
            normalized.append(h)
    if not normalized:
        return {"error": "No valid hex colors found."}

    roles = {}
    if normalized:
        roles["primary"] = normalized[0]
    if len(normalized) > 1:
        roles["secondary"] = normalized[1]
    if len(normalized) > 2:
        primary = normalized[0]
        roles["accent"] = max(normalized[1:], key=lambda c: _contrast_ratio(primary, c))

    contrasts = [
        {
            "hex": h,
            "contrast_on_white": round(_contrast_ratio(h, "#FFFFFF"), 2),
            "contrast_on_black": round(_contrast_ratio(h, "#000000"), 2),
        }
        for h in normalized[:6]
    ]
    return {"palette": normalized, "roles": roles, "contrasts": contrasts}


_ACTIONS = ("palette", "extract_palette", "font_pairing", "direction", "sample_data")


def _parse_options(options) -> dict:
    if options is None or options == "":
        return {}
    if isinstance(options, dict):
        return options
    if isinstance(options, str):
        try:
            parsed = json.loads(options)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def design_kit(action: str, options=None) -> dict:
    """Dispatch a design-kit action. options is an optional JSON object string or dict."""
    if not isinstance(action, str) or not action.strip():
        return {"error": "action is required.", "actions": list(_ACTIONS)}

    action = action.strip().lower()
    opts = _parse_options(options)

    if action == "palette":
        return _make_palette(opts)
    if action == "extract_palette":
        return _extract_palette(opts)
    if action == "font_pairing":
        return _font_pairing(opts)
    if action == "direction":
        return _pick_direction(opts)
    if action == "sample_data":
        return _sample_data(opts)
    return {"error": f"Unknown action '{action}'.", "actions": list(_ACTIONS)}
