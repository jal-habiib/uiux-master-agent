import re
from html.parser import HTMLParser
from pathlib import Path


RUBRIC = (
    ("Visual hierarchy", 20),
    ("Spacing and rhythm", 15),
    ("Responsive behavior", 15),
    ("Design-system consistency", 15),
    ("State coverage", 15),
    ("Typography", 10),
    ("Code quality and performance", 10),
)


class _HTMLInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: dict[str, list[dict[str, str | None]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        self.tags.append(normalized_tag)
        self.attributes.setdefault(normalized_tag, []).append(dict(attrs))


def _issue(issues: list[dict[str, str]], category: str, message: str) -> None:
    issues.append({"category": category, "message": message})


def _assessment(
    score: int,
    breakdown: dict[str, dict[str, int]],
    issues: list[dict[str, str]],
    *,
    error: str | None = None,
) -> dict:
    score = max(0, min(100, score))
    result = {
        "score": score,
        "score_gate_passed": score >= 80,
        "passed": score >= 80,
        "passed_semantics": "soft-score threshold only; not overall delivery acceptance",
        "overall_passed": False,
        "weighted_score_breakdown": breakdown,
        "issues": issues,
        "assessment_type": "static_heuristic",
        "hard_gates_status": "NOT_VERIFIED",
    }
    if error:
        result["error"] = error
    return result


def _empty_breakdown() -> dict[str, dict[str, int]]:
    return {
        category: {"weight": weight, "score": 0, "weighted_score": 0}
        for category, weight in RUBRIC
    }


def critique_code(html_path: str) -> dict:
    breakdown = _empty_breakdown()
    path = Path(html_path)
    if not path.is_file():
        return _assessment(
            0,
            breakdown,
            [{"category": "Code quality and performance", "message": "HTML file was not found."}],
            error=f"File not found: {html_path}",
        )

    try:
        html = path.read_text(encoding="utf-8", errors="replace")
        inspector = _HTMLInspector()
        inspector.feed(html)
    except Exception as exc:
        return _assessment(
            0,
            breakdown,
            [{"category": "Code quality and performance", "message": "HTML could not be read."}],
            error=f"Unable to read HTML file: {exc}",
        )

    lower_html = html.lower()
    deductions = {category: 0 for category, _ in RUBRIC}
    issues: list[dict[str, str]] = []

    def deduct(category: str, points: int, message: str) -> None:
        deductions[category] += points
        _issue(issues, category, message)

    if not re.search(r"<h1\b", lower_html):
        deduct("Visual hierarchy", 8, "Add one clear h1 that names the page or primary task.")
    if not re.search(r"<h[2-6]\b", lower_html):
        deduct("Visual hierarchy", 5, "Add supporting heading levels for distinct content groups.")

    if not re.search(r"(?:gap-|space-|p[trblxy]?-[0-9]|m[trblxy]?-[0-9])|"
                     r"(?:gap|margin|padding)\s*:", lower_html):
        deduct("Spacing and rhythm", 7, "Define a repeatable spacing scale with gap, margin, or padding.")

    if not re.search(r'<meta\b[^>]*name\s*=\s*["\']viewport["\']', lower_html):
        deduct("Responsive behavior", 4, "Add a viewport meta declaration for mobile rendering.")
    if not re.search(
        r"@media|(?:max|min)-width\s*:|(?:sm|md|lg|xl|2xl):",
        lower_html,
    ):
        deduct(
            "Responsive behavior",
            4,
            "Add responsive CSS or Tailwind breakpoint rules for layout changes.",
        )

    if not re.search(r"(?:--[a-z0-9-]+:|class\s*=|id\s*=)", lower_html):
        deduct("Design-system consistency", 6, "Use shared classes or design tokens for repeated styling.")
    if len(re.findall(r"<(?:section|article|div|ul|ol)\b", lower_html)) < 2:
        deduct("Design-system consistency", 4, "Group content into explicit, reusable layout regions.")

    interactive_tags = ("button", "a", "input", "select", "textarea")
    interactive = [
        attrs
        for tag in interactive_tags
        for attrs in inspector.attributes.get(tag, [])
    ]
    if interactive and not re.search(r":hover|:focus|:active|disabled|hover:|focus:", lower_html):
        deduct("State coverage", 7, "Define visible hover, focus, active, or disabled states for controls.")

    if not re.search(r"(?:font-family|font-size|font-weight|line-height)\s*:", lower_html) and not re.search(
        r"text-(?:xs|sm|base|lg|xl|[2-9]xl)|font-(?:normal|medium|semibold|bold)",
        lower_html,
    ):
        deduct("Typography", 5, "Define a type scale, weight, or line-height beyond browser defaults.")

    if "<!doctype html>" not in lower_html:
        deduct("Code quality and performance", 4, "Include a standards-mode HTML doctype.")
    if "<html" not in lower_html or "<meta" not in lower_html:
        deduct("Code quality and performance", 3, "Use a complete document structure with metadata.")
    if len(re.findall(r"<script\b", lower_html)) > 3:
        deduct("Code quality and performance", 3, "Limit third-party scripts and keep the page lightweight.")

    for category, weight in RUBRIC:
        score = max(0, weight - deductions[category])
        breakdown[category] = {
            "weight": weight,
            "score": score,
            "weighted_score": score,
        }

    return _assessment(
        sum(item["weighted_score"] for item in breakdown.values()),
        breakdown,
        issues,
    )
