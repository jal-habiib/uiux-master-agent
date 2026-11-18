"""Best-effort automatic post-save review.

Run by ``save_file`` / ``edit_file`` / ``generate_component`` after a successful
write so the model never has to remember to call the checks itself. Returns a
compact, ranked report suitable for a truncated tool result.

Every project import here is lazy and defensive: this module has no top-level
``tools.*`` or ``config`` import, so importing it never triggers
``tools/__init__.py``, and any failure inside a step degrades to a partial
report instead of raising.
"""

from __future__ import annotations

from pathlib import Path


def review(filename: str) -> dict:
    """Return a compact post-save report for *filename* (never raises)."""
    report: dict = {"filename": filename, "available": False}

    path = _resolve_path(filename)
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report["error"] = f"review could not read file: {exc}"
        return report

    report["available"] = True
    report["generic_lint"] = _run_lint(html)

    critic = _run_critic(path)
    if critic is not None:
        report["soft_score"] = critic.get("score")
        report["soft_passed"] = bool(critic.get("score_gate_passed"))
        report["top_issues"] = _top_issues(critic.get("issues", []))

    verify = _run_verify(filename)
    if verify is not None:
        report["hard_gates_status"] = verify.get("hard_gates_status")
        report["failing_gates"] = [
            name
            for name, gate in verify.get("hard_gates", {}).items()
            if gate.get("status") == "FAIL"
        ]
        report["overall_passed"] = bool(verify.get("overall_passed"))

    return report


def _resolve_path(filename: str) -> Path:
    try:
        from config import OUTPUT_DIR
        return Path(OUTPUT_DIR) / filename
    except Exception:
        return Path(filename)


def _run_lint(html: str) -> list[dict]:
    try:
        from tools.lint_generic import lint_generic
        return lint_generic(html)
    except Exception as exc:
        return [{
            "rule": "lint_generic",
            "severity": "error",
            "message": f"generic lint unavailable: {exc}",
        }]


def _run_critic(path: Path) -> dict | None:
    try:
        from tools.critic import critique_code
        result = critique_code(str(path))
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _run_verify(filename: str) -> dict | None:
    try:
        from tools.verify_html import verify_html
        result = verify_html(filename)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _top_issues(issues: list[dict], limit: int = 5) -> list[dict]:
    return [
        {"category": issue.get("category"), "message": issue.get("message")}
        for issue in issues[:limit]
        if isinstance(issue, dict)
    ]
