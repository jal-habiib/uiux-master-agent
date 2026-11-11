import re
from html.parser import HTMLParser
from pathlib import Path


class _HTMLInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = {}

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag.lower())
        self.attributes.setdefault(tag.lower(), []).append(dict(attrs))


RUBRIC = (
    "Hierarchy",
    "Spacing",
    "Typography",
    "Color",
    "Accessibility",
    "Mobile",
    "States",
    "Icons",
    "Images",
    "Consistency",
)


def _issue(issues: list[str], category: str, message: str) -> None:
    issues.append(f"{category}: {message}")


def critique_code(html_path: str) -> dict:
    path = Path(html_path)
    if not path.is_file():
        return {
            "score": 1,
            "issues": [f"File not found: {html_path}"],
            "passed": False,
        }

    try:
        html = path.read_text(encoding="utf-8", errors="replace")
        inspector = _HTMLInspector()
        inspector.feed(html)
    except Exception as exc:
        return {
            "score": 1,
            "issues": [f"Unable to read HTML file: {exc}"],
            "passed": False,
        }

    issues = []
    lower_html = html.lower()

    if not re.search(r"<h1\b", lower_html) or not re.search(r"<h[2-6]\b", lower_html):
        _issue(issues, "Hierarchy", "Include a clear h1 and supporting heading levels.")
    if not re.search(r"(gap|margin|padding)\s*:", lower_html):
        _issue(issues, "Spacing", "Define intentional spacing with gap, margin, or padding.")
    if not re.search(r"(font-family|font-size|font-weight|line-height)\s*:", lower_html):
        _issue(issues, "Typography", "Define typography beyond browser defaults.")
    if not re.search(r"(color|background(?:-color)?)\s*:", lower_html):
        _issue(issues, "Color", "Define an intentional text or background color palette.")

    interactive_tags = inspector.attributes.get("button", []) + inspector.attributes.get("a", [])
    if any("aria-label" not in attrs and not attrs.get("title") for attrs in interactive_tags):
        _issue(issues, "Accessibility", "Give icon-only or ambiguous controls accessible labels.")
    if not inspector.attributes.get("button") and not inspector.attributes.get("a"):
        _issue(issues, "Accessibility", "Include semantic interactive elements.")

    if "viewport" not in lower_html or not re.search(
        r"@media|(?:max|min)-width\s*:", lower_html
    ):
        _issue(issues, "Mobile", "Include a viewport declaration and responsive CSS.")

    if inspector.attributes.get("button") and not re.search(
        r":hover|:focus|:active|disabled", lower_html
    ):
        _issue(issues, "States", "Define hover, focus, active, or disabled states.")

    brand_icons = re.findall(r'data-lucide\s*=\s*["\'](instagram|twitter)["\']', lower_html)
    if brand_icons:
        names = ", ".join(sorted(set(brand_icons)))
        _issue(
            issues,
            "Icons",
            f"Lucide does not support brand icons ({names}); use a brand-icon library or SVG instead.",
        )

    for attrs in inspector.attributes.get("img", []):
        src = (attrs.get("src") or "").strip()
        if not src:
            _issue(issues, "Images", "Every img tag must have a valid src.")
        elif "placehold.co" in src.lower():
            _issue(issues, "Images", "Replace placehold.co placeholder images with real assets.")

    if not inspector.attributes.get("img") and not re.search(
        r"background(?:-image)?\s*:", lower_html
    ):
        _issue(issues, "Images", "Provide meaningful image content or an intentional image-free layout.")

    if not re.search(r"(class|id)\s*=", lower_html) or not re.search(
        r"(section|card|container|grid|flex)", lower_html
    ):
        _issue(issues, "Consistency", "Use a consistent component or layout structure.")

    failed_categories = {
        issue.split(":", 1)[0] for issue in issues if ":" in issue
    }
    score = max(1, len(RUBRIC) - len(failed_categories))
    return {"score": score, "issues": issues, "passed": score >= 8}
