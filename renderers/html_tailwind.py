def render(spec: dict) -> str:
    name = spec.get("name", "component")
    palette = spec.get("palette", {"bg": "#FFFFFF", "text": "#111111", "accent": "#2563EB", "muted": "#6B7280"})
    content = spec.get("content", {})
    if isinstance(content, str):
        content = {"body": content}
    structure = spec.get("structure", ["body"])

    style_vars = f"""
      --bg: {palette.get('bg')};
      --text: {palette.get('text')};
      --accent: {palette.get('accent')};
      --muted: {palette.get('muted')};
    """

    def section_html(section):
        if section == "header":
            return f'<h2 class="text-xl font-semibold text-[var(--text)]">{content.get("title", name.title())}</h2>'
        if section == "price":
            return f'<div class="text-4xl font-bold text-[var(--accent)]">{content.get("price", "UGX 0")}</div>'
        if section == "features":
            items = content.get("features", ["Feature one", "Feature two", "Feature three"])
            if isinstance(items, str):
                items = [i.strip() for i in items.split(",")]
            return "<ul class='space-y-2 text-sm text-[var(--muted)]'>" + "".join(
                f"<li class='flex items-start gap-2'><span class='text-[var(--accent)]'>✓</span>{i}</li>"
                for i in items
            ) + "</ul>"
        if section == "cta":
            return f'<button class="w-full bg-[var(--accent)] text-white font-medium py-3 px-4 rounded-lg hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--accent)]" type="button">{content.get("cta", "Get started")}</button>'
        if section == "body":
            return f'<p class="text-sm text-[var(--muted)]">{content.get("body", "")}</p>'
        return f"<!-- unknown section: {section} -->"

    inner = "\n      ".join(section_html(s) for s in structure)

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
         style="{style_vars.strip()} background: var(--bg); color: var(--text);">
          {inner}
    </div>
</body>
</html>
"""