import html
import re


DEFAULT_PALETTE = {
    "bg": "#FFFFFF",
    "text": "#111111",
    "accent": "#2563EB",
    "muted": "#6B7280",
}
PALETTE_KEYS = tuple(DEFAULT_PALETTE)
COLOR_TOKEN = re.compile(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?")
SECTIONS = {"header", "price", "features", "cta", "body"}


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("Component content values must be strings.")
    return html.escape(value, quote=True)


def _palette(spec_palette: object) -> dict[str, str]:
    if spec_palette is None:
        return DEFAULT_PALETTE.copy()
    if not isinstance(spec_palette, dict):
        raise ValueError("Palette must be an object.")

    palette = DEFAULT_PALETTE.copy()
    for key in PALETTE_KEYS:
        value = spec_palette.get(key, palette[key])
        if not isinstance(value, str) or not COLOR_TOKEN.fullmatch(value):
            raise ValueError(f"Palette color '{key}' must be a 3- or 6-digit hex color.")
        palette[key] = value
    return palette


def _structure(value: object) -> list[str]:
    if value is None:
        return ["body"]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Structure must be a list of section names.")
    return [item for item in value if item in SECTIONS]


def render(spec: dict) -> str:
    if not isinstance(spec, dict):
        raise ValueError("Component specification must be an object.")

    name = _text(spec.get("name", "component"), "component")
    if not isinstance(spec.get("name", "component"), str):
        raise ValueError("Component name must be a string.")

    content = spec.get("content", {})
    if isinstance(content, str):
        content = {"body": content}
    if not isinstance(content, dict):
        raise ValueError("Content must be an object or string.")

    palette = _palette(spec.get("palette"))
    structure = _structure(spec.get("structure"))
    style_vars = "\n".join(
        f"      --{key}: {palette[key]};" for key in PALETTE_KEYS
    )

    def section_html(section: str) -> str:
        if section == "header":
            title = _text(content.get("title"), html.unescape(name).title())
            return f'<h2 class="text-xl font-semibold text-[var(--text)]">{title}</h2>'
        if section == "price":
            price = _text(content.get("price"), "UGX 0")
            return f'<div class="text-4xl font-bold text-[var(--accent)]">{price}</div>'
        if section == "features":
            items = content.get("features", ["Feature one", "Feature two", "Feature three"])
            if isinstance(items, str):
                items = [item.strip() for item in items.split(",")]
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise ValueError("Features must be a string or a list of strings.")
            rendered_items = "".join(
                f"<li class='flex items-start gap-2'><span class='text-[var(--accent)]'>"
                f"✓</span>{_text(item)}</li>"
                for item in items
            )
            return "<ul class='space-y-2 text-sm text-[var(--muted)]'>" + rendered_items + "</ul>"
        if section == "cta":
            cta = _text(content.get("cta"), "Get started")
            return (
                '<button class="w-full bg-[var(--accent)] text-white font-medium py-3 px-4 '
                'rounded-lg hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-offset-2 '
                f'focus:ring-[var(--accent)]" type="button">{cta}</button>'
            )
        return f'<p class="text-sm text-[var(--muted)]">{_text(content.get("body"))}</p>'

    inner = "\n      ".join(section_html(section) for section in structure)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-sm p-6 rounded-2xl shadow-md space-y-4"
         style="{style_vars} background: var(--bg); color: var(--text);">
          {inner}
    </div>
</body>
</html>
"""
