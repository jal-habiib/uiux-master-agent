import json
from renderers import get_renderer
from config import OUTPUT_DIR

def generate_component(spec: dict | str, target: str = "html-tailwind") -> dict:
    try:
        if isinstance(spec, str):
            spec = json.loads(spec)
            
        renderer = get_renderer(target)
        code = renderer(spec)
        
        name = spec.get("name", "component")
        path = OUTPUT_DIR / f"{name}.html"
        path.write_text(code)
        
        return {"target": target, "saved_to": str(path), "code": code}
    except Exception as e:
        return {"error": f"Failed to generate component: {e}"}