import json
import re
from pathlib import Path

from renderers import get_renderer
from config import OUTPUT_DIR


_COMPONENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _component_output_path(name: object) -> Path:
    """Return a contained output path for a safe component filename."""
    if not isinstance(name, str) or not _COMPONENT_NAME.fullmatch(name):
        raise ValueError(
            "Component name must be 1-64 characters and contain only "
            "letters, numbers, underscores, or hyphens; it must start "
            "with a letter or number."
        )

    output_root = Path(OUTPUT_DIR).resolve()
    destination = output_root / f"{name}.html"

    if destination.is_symlink() or destination.resolve().parent != output_root:
        raise ValueError("Component output path must remain inside the output directory.")

    return destination


def generate_component(spec: dict | str, target: str = "html-tailwind") -> dict:
    try:
        if isinstance(spec, str):
            spec = json.loads(spec)

        if not isinstance(spec, dict):
            raise ValueError("Component specification must be a JSON object.")

        renderer = get_renderer(target)
        code = renderer(spec)

        name = spec.get("name", "component")
        path = _component_output_path(name)
        path.write_text(code, encoding="utf-8")

        return {"target": target, "saved_to": str(path), "code": code}
    except Exception as e:
        return {"error": f"Failed to generate component: {e}"}
