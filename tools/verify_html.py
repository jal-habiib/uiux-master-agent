import json
import re
from pathlib import Path
from typing import Any

from config import OUTPUT_DIR, ROOT
from tools.critic import critique_code


VIEWPORTS = {
    "mobile": (375, 812),
    "tablet": (768, 1024),
    "desktop": (1440, 900),
}
SAFE_HTML_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.html\Z")
FLOW_ACTIONS = {
    "click",
    "fill",
    "check",
    "uncheck",
    "select_option",
    "press",
    "expect_visible",
    "expect_hidden",
    "expect_text",
}


def _gate(status: str, evidence: Any) -> dict[str, Any]:
    return {"status": status, "evidence": evidence}


def _report(filename: str, gates: dict[str, dict], critic: dict) -> dict:
    statuses = [gate["status"] for gate in gates.values()]
    hard_status = "FAIL" if "FAIL" in statuses else (
        "NOT_VERIFIED" if "NOT_VERIFIED" in statuses else "PASS"
    )
    reasons = [
        f"{name}: {gate['status']}"
        for name, gate in gates.items()
        if gate["status"] != "PASS"
    ]
    overall = hard_status == "PASS" and bool(critic.get("score_gate_passed"))
    if not overall and not reasons:
        reasons.append("Soft score did not meet the 80-point threshold.")
    if not overall and hard_status == "PASS" and not critic.get("score_gate_passed"):
        reasons.append("Static soft score is below 80.")
    return {
        "filename": filename,
        "assessment_type": "playwright_hard_gates",
        "viewport": [
            {"name": name, "width": width, "height": height}
            for name, (width, height) in VIEWPORTS.items()
        ],
        "hard_gates": gates,
        "hard_gates_status": hard_status,
        "score": critic.get("score", 0),
        "weighted_score_breakdown": critic.get("weighted_score_breakdown", {}),
        "score_gate_passed": bool(critic.get("score_gate_passed")),
        "overall_passed": overall,
        "overall_failure_reasons": reasons,
    }


def _empty_gates(reason: str) -> dict[str, dict]:
    return {
        name: _gate("NOT_VERIFIED", reason)
        for name in (
            "build_render",
            "console_errors",
            "failed_requests",
            "horizontal_overflow",
            "axe_accessibility",
            "wcag_aa_contrast",
            "semantic_accessibility",
            "mobile_touch_targets",
            "keyboard_focus_order",
            "required_user_flows",
        )
    }


def _validated_path(filename: object) -> Path:
    if not isinstance(filename, str) or not SAFE_HTML_FILENAME.fullmatch(filename):
        raise ValueError("Filename must be a single safe .html filename.")
    output_root = Path(OUTPUT_DIR).resolve()
    path = output_root / filename
    if path.is_symlink() or path.resolve().parent != output_root:
        raise ValueError("HTML file must be a non-symlink inside the output directory.")
    if not path.is_file():
        raise FileNotFoundError(f"HTML file not found: {filename}")
    return path


def _parse_flows(flows_json: str | None) -> list[dict]:
    if flows_json is None:
        return []
    if not isinstance(flows_json, str):
        raise ValueError("flows_json must be a JSON string.")
    if not flows_json.strip():
        return []
    try:
        payload = json.loads(flows_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"flows_json must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("flows"), list):
        raise ValueError("flows_json must contain a flows list.")
    flows = payload["flows"]
    if not flows:
        raise ValueError("flows_json must contain at least one required flow.")
    for flow in flows:
        if (
            not isinstance(flow, dict)
            or not isinstance(flow.get("name"), str)
            or not isinstance(flow.get("steps"), list)
        ):
            raise ValueError("Each flow needs a string name and a steps list.")
        if not flow["name"].strip() or not flow["steps"]:
            raise ValueError("Each flow needs a non-empty name and at least one step.")
        for step in flow["steps"]:
            if not isinstance(step, dict) or step.get("action") not in FLOW_ACTIONS:
                raise ValueError("Each flow step must use a supported browser action.")
            action = step["action"]
            expected = {"action", "selector"} | (
                {"value"} if action in {"fill", "select_option", "press", "expect_text"} else set()
            )
            if set(step) != expected:
                raise ValueError(f"{action} steps require exactly: {', '.join(sorted(expected))}.")
            if not isinstance(step["selector"], str) or not step["selector"].strip():
                raise ValueError("Each flow step needs a non-empty selector.")
            if "value" in step and not isinstance(step["value"], str):
                raise ValueError(f"{action} steps require a string value.")
    return flows


def _selector(locator: Any) -> str:
    try:
        return locator.evaluate(
            """(element) => {
                if (element.id) return '#' + element.id;
                const parts = [];
                let current = element;
                while (current && current.nodeType === 1) {
                    let part = current.tagName.toLowerCase();
                    let sibling = current;
                    let position = 1;
                    while ((sibling = sibling.previousElementSibling)) {
                        if (sibling.tagName === current.tagName) position += 1;
                    }
                    part += ':nth-of-type(' + position + ')';
                    parts.unshift(part);
                    current = current.parentElement;
                }
                return parts.join(' > ');
            }"""
        )
    except Exception:
        return "unknown"


def _run_flow(page: Any, flow: dict, timeout: int = 3000) -> list[dict]:
    results = []
    page.set_default_timeout(timeout)
    for step in flow["steps"]:
        action = step["action"]
        locator = page.locator(step["selector"])
        if action == "click":
            locator.click()
        elif action == "fill":
            locator.fill(step["value"])
        elif action == "check":
            locator.check()
        elif action == "uncheck":
            locator.uncheck()
        elif action == "select_option":
            locator.select_option(step["value"])
        elif action == "press":
            locator.press(step["value"])
        elif action == "expect_visible":
            if not locator.is_visible():
                raise AssertionError(f"Expected visible: {step['selector']}")
        elif action == "expect_hidden":
            if locator.is_visible():
                raise AssertionError(f"Expected hidden: {step['selector']}")
        elif action == "expect_text":
            actual = locator.inner_text()
            if step["value"] not in actual:
                raise AssertionError(f"Expected text {step['value']!r}, got {actual!r}")
        results.append({"action": action, "selector": step["selector"], "status": "PASS"})
    return results


def _is_unverified_exception(exc: BaseException) -> bool:
    """Classify unavailable or timed-out browser operations as unverified."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        isinstance(exc, (TimeoutError, NotImplementedError))
        or name in {"timeouterror", "notimplementederror"}
        or "timed out" in message
        or "timeout" in message
        or "not implemented" in message
        or "unsupported" in message
    )


def _keyboard_focus_status(rendered_viewports: set[str], focus_evidence: list[dict]) -> str:
    if "mobile" not in rendered_viewports:
        return "NOT_VERIFIED"
    tab_probes = [item for item in focus_evidence if "tab_order" in item]
    if not tab_probes or any(not item.get("probe_completed") for item in tab_probes):
        return "NOT_VERIFIED"
    return (
        "FAIL"
        if any(
            item.get("positive_tabindex")
            or item.get("matches_dom_order") is False
            for item in focus_evidence
        )
        else "PASS"
    )


def verify_html(filename: str, flows_json: str | None = None) -> dict:
    try:
        path = _validated_path(filename)
    except (ValueError, FileNotFoundError, OSError) as exc:
        critic = {"score": 0, "score_gate_passed": False, "weighted_score_breakdown": {}}
        gates = _empty_gates(str(exc))
        gates["build_render"] = _gate("FAIL", str(exc))
        gates["required_user_flows"] = _gate("NOT_VERIFIED", "Flow specification was not executed.")
        return _report(str(filename), gates, critic)
    try:
        flows = _parse_flows(flows_json)
    except ValueError as exc:
        critic = critique_code(str(path))
        gates = _empty_gates("Malformed required flow specification was not executed.")
        gates["required_user_flows"] = _gate("NOT_VERIFIED", str(exc))
        return _report(filename, gates, critic)

    critic = critique_code(str(path))
    gates = _empty_gates("Playwright verification has not run.")
    flow_evidence: list[dict] = []
    if not flows:
        gates["required_user_flows"] = _gate("NOT_VERIFIED", "No required flows were specified.")

    axe_path = Path(ROOT) / "node_modules" / "axe-core" / "axe.min.js"
    axe_available = axe_path.is_file()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        reason = f"Playwright unavailable: {exc}"
        gates = _empty_gates(reason)
        gates["build_render"] = _gate("NOT_VERIFIED", reason)
        gates["required_user_flows"] = _gate(
            "NOT_VERIFIED",
            "Required flows cannot run without Playwright.",
        )
        return _report(filename, gates, critic)

    console_errors: list[dict] = []
    page_errors: list[dict] = []
    failed_requests: list[dict] = []
    overflow: list[dict] = []
    semantic_failures: list[dict] = []
    touch_failures: list[dict] = []
    focus_evidence: list[dict] = []
    axe_results: list[dict] = []
    axe_errors: list[dict] = []
    rendered_viewports: set[str] = set()
    render_evidence: list[dict] = []
    render_failures: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for viewport_name, (width, height) in VIEWPORTS.items():
                    page = browser.new_page(viewport={"width": width, "height": height})
                    current_console: list[dict] = []
                    current_failed: list[dict] = []
                    page.on(
                        "console",
                        lambda message, name=viewport_name, bucket=current_console: (
                            bucket.append({"viewport": name, "text": message.text})
                            if message.type == "error"
                            else None
                        ),
                    )
                    page.on(
                        "pageerror",
                        lambda error, name=viewport_name: page_errors.append(
                            {"viewport": name, "message": str(error)}
                        ),
                    )
                    page.on(
                        "requestfailed",
                        lambda request, name=viewport_name, bucket=current_failed: bucket.append(
                            {"viewport": name, "url": request.url, "reason": request.failure}
                        ),
                    )
                    page.on(
                        "response",
                        lambda response, name=viewport_name, bucket=current_failed: (
                            bucket.append(
                                {"viewport": name, "url": response.url, "status": response.status}
                            )
                            if response.status >= 400
                            else None
                        ),
                    )
                    try:
                        page.goto(path.as_uri(), wait_until="load", timeout=10000)
                        page.wait_for_timeout(100)
                        dom = page.evaluate(
                            """() => {
                              const visible = (el) => {
                                const s = getComputedStyle(el), r = el.getBoundingClientRect();
                                return s.display !== 'none' && s.visibility !== 'hidden' &&
                                  !el.disabled && r.width > 0 && r.height > 0;
                              };
                              const selector = (el) => {
                                if (el.id) return '#' + el.id;
                                const parts = [];
                                let current = el;
                                while (current && current.nodeType === 1) {
                                  let part = current.tagName.toLowerCase();
                                  let sibling = current;
                                  let position = 1;
                                  while ((sibling = sibling.previousElementSibling)) {
                                    if (sibling.tagName === current.tagName) position += 1;
                                  }
                                  part += ':nth-of-type(' + position + ')';
                                  parts.unshift(part);
                                  current = current.parentElement;
                                }
                                return parts.join(' > ');
                              };
                              const accessibleName = (el) => {
                                const explicit = el.getAttribute('aria-label') ||
                                  el.getAttribute('title');
                                if (explicit && explicit.trim()) return explicit.trim();
                                const imageAlt = [...el.querySelectorAll('img[alt]')]
                                  .map(i => i.getAttribute('alt').trim()).filter(Boolean).join(' ');
                                if (imageAlt) return imageAlt;
                                const svgTitle = [...el.querySelectorAll('svg title')]
                                  .map(t => t.textContent.trim()).filter(Boolean).join(' ');
                                if (svgTitle) return svgTitle;
                                return el.textContent.trim();
                              };
                              const controls = [...document.querySelectorAll(
                                'a,button,input,select,textarea,[role="button"],[role="link"]')];
                              const focusable = controls.filter(e => visible(e) && e.tabIndex >= 0);
                              return {
                                scrollWidth: document.documentElement.scrollWidth,
                                bodyScrollWidth: document.body ? document.body.scrollWidth : 0,
                                innerWidth: innerWidth,
                                semantic: [
                                  ...[...document.images].filter(i => !i.hasAttribute('alt')).map(() => 'image missing alt'),
                                  ...[...document.querySelectorAll('a,button,[role="button"],[role="link"]')]
                                    .filter(e => visible(e) && !accessibleName(e))
                                    .map(() => 'control missing accessible name'),
                                  ...[...document.querySelectorAll('input,select,textarea')].filter(e => {
                                    if (e.type === 'hidden') return false;
                                    return !(e.labels && e.labels.length) && !e.getAttribute('aria-label') &&
                                      !e.getAttribute('aria-labelledby');
                                  }).map(() => 'form control missing label')
                                ],
                                targets: focusable.map(e => ({
                                  selector: selector(e), width: e.getBoundingClientRect().width,
                                  height: e.getBoundingClientRect().height
                                })),
                                positiveTabindex: [...document.querySelectorAll('[tabindex]')]
                                  .filter(e => Number(e.getAttribute('tabindex')) > 0)
                                  .map((e, index) => ({index, selector: selector(e)})),
                                focusableElements: focusable.map((e, index) => ({
                                  index, selector: selector(e)
                                }))
                              };
                            }"""
                        )
                        rendered_viewports.add(viewport_name)
                        render_evidence.append({"viewport": viewport_name, "loaded": True})
                        if dom["scrollWidth"] > width or dom["bodyScrollWidth"] > width:
                            overflow.append({"viewport": viewport_name, **dom})
                        semantic_failures.extend(
                            {"viewport": viewport_name, "message": item}
                            for item in dom["semantic"]
                        )
                        if viewport_name == "mobile":
                            touch_failures.extend(
                                {"viewport": viewport_name, **target}
                                for target in dom["targets"]
                                if target["width"] < 44 or target["height"] < 44
                            )
                        if dom["positiveTabindex"]:
                            focus_evidence.append(
                                {"viewport": viewport_name, "positive_tabindex": dom["positiveTabindex"]}
                            )
                        if viewport_name == "mobile":
                            order = []
                            for _ in dom["focusableElements"]:
                                page.keyboard.press("Tab")
                                order.append(
                                    page.evaluate(
                                        """() => {
                                          const selector = (el) => {
                                            if (el.id) return '#' + el.id;
                                            const parts = [];
                                            let current = el;
                                            while (current && current.nodeType === 1) {
                                              let part = current.tagName.toLowerCase();
                                              let sibling = current;
                                              let position = 1;
                                              while ((sibling = sibling.previousElementSibling)) {
                                                if (sibling.tagName === current.tagName) position += 1;
                                              }
                                              part += ':nth-of-type(' + position + ')';
                                              parts.unshift(part);
                                              current = current.parentElement;
                                            }
                                            return parts.join(' > ');
                                          };
                                          const controls = [...document.querySelectorAll(
                                            'a,button,input,select,textarea,[role="button"],[role="link"]')]
                                            .filter(e => {
                                              const s = getComputedStyle(e), r = e.getBoundingClientRect();
                                              return s.display !== 'none' && s.visibility !== 'hidden' &&
                                                !e.disabled && r.width > 0 && r.height > 0 && e.tabIndex >= 0;
                                            });
                                          const index = controls.indexOf(document.activeElement);
                                          return {
                                            index,
                                            selector: document.activeElement
                                              ? selector(document.activeElement)
                                              : 'body'
                                          };
                                        }"""
                                    )
                                )
                            probe_valid = all(
                                isinstance(item, dict)
                                and isinstance(item.get("index"), int)
                                and isinstance(item.get("selector"), str)
                                and item["index"] >= 0
                                for item in order
                            )
                            focus_evidence.append(
                                {
                                    "viewport": viewport_name,
                                    "expected_tab_order": dom["focusableElements"],
                                    "tab_order": order,
                                    "probe_completed": probe_valid,
                                    "observed_indices": [
                                        item.get("index") for item in order
                                    ],
                                    "observed_selectors": [
                                        item.get("selector") for item in order
                                    ],
                                    "matches_dom_order": probe_valid and [
                                        item["index"] for item in order
                                    ] == list(range(len(dom["focusableElements"]))) and [
                                        item["selector"] for item in order
                                    ] == [
                                        item["selector"] for item in dom["focusableElements"]
                                    ],
                                }
                            )
                        if axe_available:
                            try:
                                page.add_script_tag(path=str(axe_path))
                                axe_results.append(
                                    {
                                        "viewport": viewport_name,
                                        "result": page.evaluate(
                                            """async () => {
                                              const result = await axe.run();
                                              return {
                                                violations: result.violations.map(v => ({
                                                  id: v.id, impact: v.impact, description: v.description,
                                                  help: v.help, helpUrl: v.helpUrl,
                                                  nodes: v.nodes.map(n => ({target: n.target, html: n.html}))
                                                })),
                                                incomplete: result.incomplete.map(v => ({id: v.id, impact: v.impact}))
                                              };
                                            }"""
                                        ),
                                    }
                                )
                            except Exception as exc:
                                axe_errors.append({"viewport": viewport_name, "error": str(exc)})
                    except Exception as exc:
                        render_failures.append(f"{viewport_name}: {exc}")
                        render_evidence.append(
                            {"viewport": viewport_name, "loaded": False, "error": str(exc)}
                        )
                    finally:
                        console_errors.extend(current_console)
                        failed_requests.extend(current_failed)
                        page.close()
            finally:
                browser.close()
    except Exception as exc:
        reason = f"Playwright browser unavailable: {exc}"
        gates = _empty_gates(reason)
        gates["build_render"] = _gate("NOT_VERIFIED", reason)
        return _report(filename, gates, critic)

    gates["build_render"] = _gate(
        "NOT_VERIFIED" if render_failures or len(rendered_viewports) != len(VIEWPORTS) else "PASS",
        {"viewports": render_evidence, "errors": render_failures},
    )
    gates["console_errors"] = _gate(
        "FAIL" if console_errors or page_errors else (
            "PASS" if len(rendered_viewports) == len(VIEWPORTS) else "NOT_VERIFIED"
        ),
        {
            "console_errors": console_errors,
            "page_errors": page_errors,
            "rendered_viewports": sorted(rendered_viewports),
        },
    )
    gates["failed_requests"] = _gate(
        "FAIL" if failed_requests else (
            "PASS" if len(rendered_viewports) == len(VIEWPORTS) else "NOT_VERIFIED"
        ),
        {"requests": failed_requests, "rendered_viewports": sorted(rendered_viewports)},
    )
    gates["horizontal_overflow"] = _gate(
        "FAIL" if overflow else (
            "PASS" if len(rendered_viewports) == len(VIEWPORTS) else "NOT_VERIFIED"
        ),
        overflow or {"checked_viewports": sorted(rendered_viewports)},
    )
    if not axe_available:
        gates["axe_accessibility"] = _gate("NOT_VERIFIED", f"Missing local axe bundle: {axe_path}")
        gates["wcag_aa_contrast"] = _gate("NOT_VERIFIED", "Contrast requires the local axe bundle.")
    else:
        violations = [
            {"viewport": item["viewport"], **violation}
            for item in axe_results
            for violation in item["result"]["violations"]
        ]
        critical = [v for v in violations if v["impact"] in {"critical", "serious"}]
        gates["axe_accessibility"] = _gate(
            "FAIL" if critical else (
                "NOT_VERIFIED"
                if axe_errors or len(axe_results) != len(VIEWPORTS)
                else "PASS"
            ),
            critical or axe_errors or (
                {"reason": "axe did not run at every required viewport"}
                if len(axe_results) != len(VIEWPORTS)
                else {"violations": violations}
            ),
        )
        contrast = [v for v in violations if v["id"] == "color-contrast"]
        incomplete_contrast = [
            item for item in axe_results
            if any(v["id"] == "color-contrast" for v in item["result"]["incomplete"])
        ]
        gates["wcag_aa_contrast"] = _gate(
            "FAIL" if contrast else ("NOT_VERIFIED" if incomplete_contrast else "PASS"),
            contrast or incomplete_contrast or axe_errors or (
                {"reason": "axe-core did not complete at every required viewport"}
                if len(axe_results) != len(VIEWPORTS)
                else {"rule": "color-contrast"}
            ),
        )
    gates["semantic_accessibility"] = _gate(
        "FAIL" if semantic_failures else (
            "PASS" if len(rendered_viewports) == len(VIEWPORTS) else "NOT_VERIFIED"
        ),
        semantic_failures or {"method": "deterministic DOM checks"},
    )
    gates["mobile_touch_targets"] = _gate(
        "FAIL" if touch_failures else (
            "PASS" if "mobile" in rendered_viewports else "NOT_VERIFIED"
        ),
        touch_failures or {"viewport": "mobile", "minimum": 44},
    )
    focus_status = _keyboard_focus_status(rendered_viewports, focus_evidence)
    gates["keyboard_focus_order"] = _gate(focus_status, focus_evidence)

    if flows:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for flow in flows:
                        context = browser.new_context(viewport={"width": 375, "height": 812})
                        page = context.new_page()
                        try:
                            page.goto(path.as_uri(), wait_until="load", timeout=10000)
                            steps = _run_flow(page, flow)
                            flow_evidence.append({"name": flow["name"], "status": "PASS", "steps": steps})
                        except Exception as exc:
                            flow_evidence.append(
                                {
                                    "name": flow["name"],
                                    "status": "NOT_VERIFIED" if _is_unverified_exception(exc) else "FAIL",
                                    "error": str(exc),
                                }
                            )
                        finally:
                            page.close()
                            context.close()
                finally:
                    browser.close()
            flow_status = (
                "FAIL"
                if any(item["status"] == "FAIL" for item in flow_evidence)
                else (
                    "NOT_VERIFIED"
                    if any(item["status"] == "NOT_VERIFIED" for item in flow_evidence)
                    else "PASS"
                )
            )
        except Exception as exc:
            flow_status = "NOT_VERIFIED"
            flow_evidence.append({"status": "NOT_VERIFIED", "error": str(exc)})
        gates["required_user_flows"] = _gate(flow_status, flow_evidence)
    return _report(filename, gates, critic)
