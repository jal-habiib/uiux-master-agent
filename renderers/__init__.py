from . import html_tailwind

RENDERERS = {
    "html-tailwind": html_tailwind.render,
}

def get_renderer(target: str):
    if target not in RENDERERS:
        raise ValueError(f"Unknown target '{target}'. Available: {list(RENDERERS)}")
    return RENDERERS[target]