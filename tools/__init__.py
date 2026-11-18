from .generate_component import generate_component
from .save_file import save_file
from .edit_file import edit_file
from .read_file import read_file
from .design_kit import design_kit
from .memory import memory
from .analyze_reference import analyze_reference
from .verify_html import verify_html

TOOLS = {
    "save_file": {
        "fn": save_file,
        "desc": (
            "Save raw HTML/CSS code directly to a file in the output folder. "
            "Returns an automatic review block (soft score, hard-gate status, "
            "failing gates, generic-lint findings, ranked fixes)."
        ),
        "params": {
            "filename": {"type": "string", "desc": "The name of the file (e.g., 'pricing-card.html')"},
            "content": {"type": "string", "desc": "The raw HTML code to save"},
        },
        "required": ["filename", "content"],
    },
    "edit_file": {
        "fn": edit_file,
        "desc": (
            "Replace an exact string in an existing file in the output folder. "
            "Returns an automatic review block like save_file."
        ),
        "params": {
            "filename": {
                "type": "string",
                "desc": "The existing filename in the output folder",
            },
            "search_string": {
                "type": "string",
                "desc": "The exact text to find",
            },
            "replace_string": {
                "type": "string",
                "desc": "The replacement text",
            },
        },
        "required": ["filename", "search_string", "replace_string"],
    },
    "read_file": {
        "fn": read_file,
        "desc": (
            "Read a file inside output/, data/, or prompts/, or list available "
            "files when called with no path (or path 'list')."
        ),
        "params": {
            "path": {
                "type": "string",
                "desc": "Optional file path relative to output/, data/, or prompts/. Omit or pass 'list' to list files.",
            }
        },
        "required": [],
    },
    "generate_component": {
        "fn": generate_component,
        "desc": (
            "Render a component spec to code. target defaults to 'html-tailwind'. "
            "Returns an automatic review block like save_file."
        ),
        "params": {
            "spec": {"type": "object", "desc": "Spec matching the schema in the system prompt"},
            "target": {"type": "string", "desc": "Renderer target, default 'html-tailwind'"},
        },
        "required": ["spec"],
    },
    "design_kit": {
        "fn": design_kit,
        "desc": (
            "Generate concrete, non-generic design choices. action is one of "
            "'palette', 'extract_palette', 'font_pairing', 'direction', or "
            "'sample_data'; options is an optional JSON object with the "
            "action's arguments (e.g. {\"vibe\": \"warm\"})."
        ),
        "params": {
            "action": {"type": "string", "desc": "One of: palette, extract_palette, font_pairing, direction, sample_data."},
            "options": {"type": "string", "desc": "Optional JSON object string with action-specific arguments."},
        },
        "required": ["action"],
    },
    "memory": {
        "fn": memory,
        "desc": (
            "Bundled memory access. action is 'save' (save text to memory), "
            "'search' (search memory), or 'notes' (read saved notes)."
        ),
        "params": {
            "action": {"type": "string", "desc": "One of: save, search, notes."},
            "text": {"type": "string", "desc": "Text to save (for 'save') or query (for 'search')."},
        },
        "required": ["action"],
    },
    "analyze_reference": {
        "fn": analyze_reference,
        "desc": (
            "Analyze an uploaded reference image into a design-system summary "
            "(vision description plus extracted hex palette)."
        ),
        "params": {
            "image_path": {"type": "string", "desc": "User-uploaded PNG/JPEG path under temp_uploads."},
        },
        "required": ["image_path"],
    },
    "verify_html": {
        "fn": verify_html,
        "desc": (
            "Run Playwright and local axe-core hard-gate verification at 375, 768, "
            "and 1440px, then combine the result with the static soft score. "
            "Unavailable checks remain NOT_VERIFIED. (Also runs automatically after save/edit.)"
        ),
        "params": {
            "filename": {
                "type": "string",
                "desc": "Safe single HTML filename in the output folder.",
            },
            "flows_json": {
                "type": "string",
                "desc": "Optional JSON specification of safe required user flows.",
            },
        },
        "required": ["filename"],
    },
}
