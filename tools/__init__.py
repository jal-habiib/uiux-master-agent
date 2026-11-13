from .analyze_image import analyze_image
from .check_contrast import check_contrast
from .lookup_pattern import lookup_pattern
from .generate_component import generate_component
from .save_file import save_file
from .edit_file import edit_file
from .read_notes import read_notes
from .read_brand import read_brand
from .screenshot_html import screenshot_html
from .semantic_memory import save_memory, search_memory
from .critic import critique_code
from .verify_html import verify_html

TOOLS = {
    "analyze_image": {
        "fn": analyze_image,
        "desc": (
            "Describe a user-uploaded PNG or JPEG screenshot in structured detail "
            "(layout, colors, components, issues). The file must be under temp_uploads "
            "and no larger than 15 MB."
        ),
        "params": {
            "image_path": {
                "type": "string",
                "desc": "User-uploaded PNG/JPEG path under temp_uploads, maximum 15 MB.",
            }
        },
        "required": ["image_path"],
    },
    "save_file": {
        "fn": save_file,
        "desc": "Save raw HTML/CSS code directly to a file in the output folder.",
        "params": {
            "filename": {"type": "string", "desc": "The name of the file (e.g., 'pricing-card.html')"},
            "content": {"type": "string", "desc": "The raw HTML code to save"},
        },
        "required": ["filename", "content"],
    },
    "edit_file": {
        "fn": edit_file,
        "desc": "Replace an exact string in an existing file in the output folder.",
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
    "check_contrast": {
        "fn": check_contrast,
        "desc": "Compute WCAG contrast ratio between two hex colors. Returns ratio and pass/fail for AA and AAA.",
        "params": {
            "fg": {"type": "string", "desc": "Foreground hex, e.g. #1A1A1A"},
            "bg": {"type": "string", "desc": "Background hex, e.g. #FDF6EC"},
        },
        "required": ["fg", "bg"],
    },
    "lookup_pattern": {
        "fn": lookup_pattern,
        "desc": "Fetch a known design pattern spec by name (e.g. 'pricing-card', 'empty-state', 'data-table').",
        "params": {"name": {"type": "string", "desc": "Pattern name in kebab-case"}},
        "required": ["name"],
    },
    "generate_component": {
        "fn": generate_component,
        "desc": "Render a component spec to code. target defaults to 'html-tailwind'.",
        "params": {
            "spec": {"type": "object", "desc": "Spec matching the schema in the system prompt"},
            "target": {"type": "string", "desc": "Renderer target, default 'html-tailwind'"},
        },
        "required": ["spec"],
    },
    "read_notes": {
        "fn": read_notes,
        "desc": "Read saved design notes and preferences to recall past decisions.",
        "params": {},
        "required": [],
    },
    "read_brand": {
        "fn": read_brand,
        "desc": "Read the brand kit from data/brand.json, including required colors and fonts.",
        "params": {},
        "required": [],
    },
    "screenshot_html": {
        "fn": screenshot_html,
        "desc": "Takes a screenshot of a generated HTML file so you can visually review your own work.",
        "params": {
            "filename": {
                "type": "string",
                "desc": "The HTML filename in the output folder (e.g., 'sacco-pricing.html')",
            }
        },
        "required": ["filename"],
    },
    "save_memory": {
        "fn": save_memory,
        "desc": "Save an important design decision, user preference, or completed project into long-term memory.",
        "params": {
            "text": {"type": "string", "desc": "The design memory to save"},
        },
        "required": ["text"],
    },
    "search_memory": {
        "fn": search_memory,
        "desc": "Search long-term memory to recall past design decisions, user color preferences, or past projects.",
        "params": {
            "query": {"type": "string", "desc": "The design memory search query"},
        },
        "required": ["query"],
    },
    "critique_code": {
        "fn": critique_code,
        "desc": (
            "Evaluate an HTML file with a static heuristic seven-dimension, "
            "100-point soft-score rubric. Hard gates are not verified by this tool."
        ),
        "params": {
            "html_path": {"type": "string", "desc": "Path to the HTML file to critique"},
        },
        "required": ["html_path"],
    },
    "verify_html": {
        "fn": verify_html,
        "desc": (
            "Run Playwright and local axe-core hard-gate verification at 375, 768, "
            "and 1440px, then combine the result with the static soft score. "
            "Unavailable checks remain NOT_VERIFIED."
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