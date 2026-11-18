"""Bounded Create-mode verification of immutable S9 Build artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import stat
import tempfile
import uuid
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import app_jobs
import build
import config
import job_store
import orchestrator
import planning
import provider_gateway
from tools.critic import critique_code
from tools.verify_html import _parse_flows

gateway_call = provider_gateway.call
log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
VERIFY_INTEGRATION = "s10-verify"
VERIFY_ACTOR = "html-verifier"
REPORT_NAME = "verify_report.json"
MAX_REPORT_BYTES = 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024
MAX_STRING_CHARS = 2000
MAX_EVIDENCE_CHARS = 700
MAX_ARRAY_ITEMS = 20
_BUILD_FILENAME = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,98}[a-z0-9])?\.html\Z")
_RESPONSIVE_SOURCE = re.compile(
    r"@media\b|(?:max|min)-width\s*:|(?:sm|md|lg|xl|2xl):",
    re.IGNORECASE,
)
_PROMPT = (config.PROMPTS_DIR / "verify_v1.md").read_text(encoding="utf-8")
_BROWSER_GATES = (
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
_STATIC_GATES = (
    "html_security",
    "html_structure",
    "semantic_accessibility_static",
    "responsive_source",
)
_REPORT_FIELDS = (
    "schema_version",
    "status",
    "verified",
    "job_id",
    "run_id",
    "source_artifact_refs",
    "source_sha256",
    "hard_gates",
    "soft_quality",
    "provider_review",
    "pages",
)
_PROVIDER_FINDING_FIELDS = ("page", "severity", "category", "finding")
_PROVIDER_SEVERITIES = frozenset({"critical", "serious", "moderate", "minor", "info"})


class VerifyError(job_store.ValidationError):
    """Raised when Verify inputs or artifacts cannot be trusted or persisted."""


def _object(value: Any, fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise VerifyError(f"{name} must contain exactly the required fields")
    return value


def _text(value: Any, name: str, *, max_chars: int = MAX_STRING_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
        raise VerifyError(f"{name} must be a non-empty string of at most {max_chars} characters")
    return value


class _SemanticInspector(HTMLParser):
    """Collect deterministic semantic checks without rendering or executing HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.active_names: list[dict[str, Any]] = []
        self.form_controls: list[dict[str, Any]] = []
        self.ids: set[str] = set()
        self.label_for: set[str] = set()
        self.issues: list[str] = []
        self.heading_levels: list[int] = []
        self.main_count = 0
        self.h1_count = 0
        self.image_count = 0
        self.label_depth = 0
        self.control_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        tag = tag.lower()
        element_id = values.get("id")
        if element_id:
            if element_id in self.ids:
                self.issues.append("duplicate id attributes")
            self.ids.add(element_id)
        if tag == "label":
            self.label_depth += 1
            if values.get("for"):
                self.label_for.add(values["for"] or "")
        if tag == "main":
            self.main_count += 1
        if tag == "img":
            self.image_count += 1
            if "alt" not in values:
                self.issues.append("image missing alt text")

        if re.fullmatch(r"h[1-6]", tag):
            level = int(tag[1])
            self.heading_levels.append(level)
            if level == 1:
                self.h1_count += 1
        if tag in {"a", "button"}:
            self.control_count += 1
            self.active_names.append({"tag": tag, "attrs": values, "text": ""})
        if tag in {"input", "select", "textarea"} and not (
            tag == "input" and (values.get("type") or "").lower() == "hidden"
        ):
            self.control_count += 1
            self.form_controls.append(
                {"attrs": values, "inside_label": self.label_depth > 0}
            )
        if tag not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
        }:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
        }:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for node in self.active_names:
            node["text"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "label":
            self.label_depth = max(0, self.label_depth - 1)
        if tag in {"a", "button"}:
            if self.active_names:
                node = self.active_names.pop()
                attrs = node["attrs"]
                if not any(
                    isinstance(attrs.get(name), str) and attrs[name].strip()
                    for name in ("aria-label", "aria-labelledby", "title")
                ) and not node["text"].strip():
                    if node["tag"] in {"a", "button"}:
                        self.issues.append(f"{node['tag']} missing accessible name")
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def results(self) -> list[str]:
        for node in self.form_controls:
            attrs = node["attrs"]
            if not node["inside_label"] and not any(
                isinstance(attrs.get(name), str) and attrs[name].strip()
                for name in ("aria-label", "aria-labelledby", "title")
            ):
                field_id = attrs.get("id")
                if not field_id or field_id not in self.label_for:
                    self.issues.append("form control missing a label")
        if self.main_count != 1:
            self.issues.append("document must contain exactly one main landmark")
        if self.h1_count != 1:
            self.issues.append("document must contain exactly one h1")
        if any(right > left + 1 for left, right in zip(self.heading_levels, self.heading_levels[1:])):
            self.issues.append("heading levels skip a rank")
        return list(dict.fromkeys(self.issues))


def _static_page_gates(html: str, filenames: set[str]) -> tuple[dict[str, dict], dict]:
    gates = {name: {"status": "PASS", "evidence": {}} for name in _STATIC_GATES}
    try:
        build.validate_html_document(html, filenames)
    except build.BuildError as exc:
        gates["html_security"] = {"status": "FAIL", "evidence": str(exc)[:MAX_STRING_CHARS]}
        gates["html_structure"] = {
            "status": "FAIL",
            "evidence": "Unsafe or malformed HTML document rejected by the S9 validator.",
        }
        gates["semantic_accessibility_static"] = {
            "status": "NOT_VERIFIED",
            "evidence": "Static semantic analysis was skipped for invalid HTML.",
        }
        gates["responsive_source"] = {
            "status": "NOT_VERIFIED",
            "evidence": "Responsive checks were skipped for invalid HTML.",
        }
        return gates, {"issues": []}

    inspector = _SemanticInspector()
    try:
        inspector.feed(html)
        inspector.close()
        semantic_issues = inspector.results()
    except (ValueError, AssertionError):
        semantic_issues = ["HTML semantic analysis could not parse the document"]
    if semantic_issues:
        gates["semantic_accessibility_static"] = {
            "status": "FAIL",
            "evidence": semantic_issues[:MAX_ARRAY_ITEMS],
        }
    else:
        gates["semantic_accessibility_static"] = {
            "status": "PASS",
            "evidence": {
                "main_landmarks": inspector.main_count,
                "h1_count": inspector.h1_count,
                "images": inspector.image_count,
                "named_controls": inspector.control_count,
            },
        }
    if not _RESPONSIVE_SOURCE.search(html):
        gates["responsive_source"] = {
            "status": "FAIL",
            "evidence": "No media query or Tailwind responsive breakpoint was found.",
        }
    else:
        gates["responsive_source"] = {
            "status": "PASS",
            "evidence": "Responsive CSS or breakpoint syntax is present; rendered overflow is checked separately.",
        }
    return gates, {"issues": semantic_issues}


def _canonical_job_id(job_id: str) -> str:
    try:
        normalized = str(uuid.UUID(job_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise VerifyError("job_id must use canonical UUID syntax") from exc
    if normalized != job_id:
        raise VerifyError("job_id must use canonical UUID syntax")
    return normalized


def _check_path_components(path: Path) -> None:
    if ".." in path.parts:
        raise VerifyError("Verify artifact paths cannot contain traversal")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in reversed((path.absolute(), *path.absolute().parents)):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(details, "st_file_attributes", 0)
        if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
            raise VerifyError("Verify artifact paths cannot contain symlink or reparse-point components")


def _read_ref(
    reference: str, job_id: str, data_root: Path, expected_bytes: bytes | None = None
) -> bytes:
    if not isinstance(reference, str) or "\\" in reference:
        raise VerifyError("S9 artifact reference is malformed")
    relative = PurePosixPath(reference)
    if str(relative) != reference or relative.is_absolute() or len(relative.parts) not in {4, 5}:
        raise VerifyError("S9 artifact reference is not canonical")
    if relative.parts[0] != "job_artifacts" or relative.parts[1] != job_id:
        raise VerifyError("S9 artifact reference is outside the active job")
    try:
        run_id = str(uuid.UUID(relative.parts[2]))
    except ValueError as exc:
        raise VerifyError("S9 artifact run id must be a UUID") from exc
    if run_id != relative.parts[2]:
        raise VerifyError("S9 artifact run id must be canonical")
    path = data_root.joinpath(*relative.parts)
    _check_path_components(path)
    try:
        active_job_root = (data_root / "job_artifacts" / job_id).resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(active_job_root)
    except ValueError:
        raise VerifyError("S9 artifact resolves outside the active job subtree") from None
    except FileNotFoundError as exc:
        raise VerifyError("S9 artifact file is missing") from exc
    if not resolved.is_file():
        raise VerifyError("S9 artifact is not a regular file")
    content = resolved.read_bytes()
    if expected_bytes is not None and content != expected_bytes:
        raise VerifyError("S9 artifact changed during verification")
    return content


def load_verify_inputs(
    job_id: str,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load only the active job's completed S9 manifest and page artifacts."""
    normalized_id = _canonical_job_id(job_id)
    job = orchestrator.get_job(normalized_id, db_path)
    if job["mode"] != "create":
        raise VerifyError("Verify is available only for Create jobs")
    if job["state"] not in {"BUILD", "VERIFY"}:
        raise VerifyError("Verify requires an active Create job in BUILD or VERIFY")
    checkpoints = orchestrator.list_checkpoints(normalized_id, db_path)
    build_checkpoint = next(
        (
            item for item in reversed(checkpoints)
            if item.get("metadata", {}).get("integration") in {
                app_jobs.BUILD_INTEGRATION, app_jobs.REFINE_INTEGRATION
            }
            and item.get("metadata", {}).get("status") == "completed"
        ),
        None,
    )
    if (
        not build_checkpoint
        or build_checkpoint.get("stage") not in {"DESIGN", "REFINE"}
        or build_checkpoint.get("metadata", {}).get("status") != "completed"
    ):
        raise VerifyError("Verify requires the active job's completed S9 Build checkpoint")
    references = build_checkpoint.get("artifact_refs")
    metadata = build_checkpoint.get("metadata")
    if (
        not isinstance(references, list)
        or not isinstance(metadata, dict)
        or metadata.get("artifact_refs") != references
    ):
        raise VerifyError("S9 Build checkpoint artifact references are inconsistent")
    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    _check_path_components(root)
    job_with_history = {**job, "checkpoints": checkpoints}
    artifacts = app_jobs.saved_build_artifacts(job_with_history, root)
    if not isinstance(artifacts, dict):
        raise VerifyError("S9 Build manifest or page artifacts are unavailable")
    manifest = artifacts.get(app_jobs.BUILD_MANIFEST_NAME)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "1.0"
        or manifest.get("run_id") != metadata.get("run_id")
        or manifest.get("source_artifact_refs") != metadata.get("source_artifact_refs")
    ):
        raise VerifyError("S9 Build manifest does not match the active job checkpoint")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages or len(pages) > build.MAX_ARRAY_ITEMS:
        raise VerifyError("S9 Build manifest pages are malformed")
    manifest_ref = (
        f"job_artifacts/{normalized_id}/{manifest['run_id']}/{app_jobs.BUILD_MANIFEST_NAME}"
    )
    expected_refs = [
        f"job_artifacts/{normalized_id}/{manifest['run_id']}/build/{page['filename']}"
        for page in pages
        if isinstance(page, dict) and isinstance(page.get("filename"), str)
    ] + [manifest_ref]
    if references != expected_refs:
        raise VerifyError("S9 checkpoint refs must exactly match its manifest and pages")
    filenames = {page["filename"] for page in pages}
    if (
        len(filenames) != len(pages)
        or set(artifacts) != {app_jobs.BUILD_MANIFEST_NAME, *filenames}
        or any(not _BUILD_FILENAME.fullmatch(name) for name in filenames)
    ):
        raise VerifyError("S9 Build manifest and page refs do not match")
    source_refs = manifest.get("source_artifact_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise VerifyError("S9 Build source refs are malformed")
    for source_ref in source_refs:
        relative_source = (
            PurePosixPath(source_ref)
            if isinstance(source_ref, str) and "\\" not in source_ref
            else None
        )
        if (
            relative_source is None
            or str(relative_source) != source_ref
            or relative_source.is_absolute()
            or len(relative_source.parts) != 4
            or relative_source.parts[:2] != ("job_artifacts", normalized_id)
            or relative_source.parts[3]
            not in app_jobs.PLANNING_ARTIFACT_NAMES | app_jobs.DESIGN_ARTIFACT_NAMES
        ):
            raise VerifyError("S9 Build source ref is outside the active job")
    manifest_bytes = _read_ref(manifest_ref, normalized_id, root)
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerifyError("S9 Build manifest is not valid UTF-8") from exc
    try:
        reread_manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise VerifyError("S9 Build manifest changed during verification") from exc
    if reread_manifest != manifest:
        raise VerifyError("S9 Build manifest changed during verification")

    source_sha256 = {manifest_ref: hashlib.sha256(manifest_bytes).hexdigest()}
    normalized_pages: list[dict[str, Any]] = []
    for page in pages:
        page_name = _text(page["page"], "S9 page name")
        filename = page["filename"]
        page_ref = f"job_artifacts/{normalized_id}/{manifest['run_id']}/build/{filename}"
        html = artifacts[filename]
        if not isinstance(html, str):
            raise VerifyError("S9 page must be valid UTF-8 HTML")
        page_bytes = _read_ref(page_ref, normalized_id, root, html.encode("utf-8"))
        if len(page_bytes) > build.MAX_PAGE_BYTES:
            raise VerifyError("S9 page exceeds the existing 256 KB limit")
        source_sha256[page_ref] = hashlib.sha256(page_bytes).hexdigest()
        normalized_pages.append(
            {"page": page_name, "filename": filename, "html": html, "artifact_ref": page_ref}
        )
    return {
        "job": job,
        "checkpoints": checkpoints,
        "build_checkpoint": build_checkpoint,
        "manifest": manifest,
        "manifest_ref": manifest_ref,
        "source_artifact_refs": list(references),
        "source_sha256": source_sha256,
        "pages": normalized_pages,
        "artifact_root": root,
    }


def _browser_gate_summary(
    result: Any, flows_supplied: bool
) -> tuple[dict[str, dict], dict]:
    if not isinstance(result, dict) or not isinstance(result.get("hard_gates"), dict):
        evidence = "Browser QA returned no structured evidence."
        return (
            {name: {"status": "NOT_VERIFIED", "evidence": evidence} for name in _BROWSER_GATES},
            {"status": "NOT_VERIFIED", "reason": evidence},
        )
    gates: dict[str, dict] = {}
    source_gates = result["hard_gates"]
    for name in _BROWSER_GATES:
        item = source_gates.get(name)
        status = item.get("status") if isinstance(item, dict) else None
        if status not in {"PASS", "FAIL", "NOT_VERIFIED"}:
            status = "NOT_VERIFIED"
        evidence = item.get("evidence", "No browser evidence.") if isinstance(item, dict) else "No browser evidence."
        encoded = json.dumps(evidence, ensure_ascii=True, default=str)
        if len(encoded) > MAX_EVIDENCE_CHARS:
            evidence = encoded[:MAX_EVIDENCE_CHARS]
        if name == "required_user_flows" and not flows_supplied:
            status = "NOT_VERIFIED"
            evidence = "No required user-flow specification was supplied."
        gates[name] = {"status": status, "evidence": evidence}
    score = result.get("score")
    score_gate_passed = result.get("score_gate_passed")
    quality = {
        "score": score if isinstance(score, int) and not isinstance(score, bool) else 0,
        "score_gate_passed": bool(score_gate_passed),
    }
    return gates, quality


def _browser_qa_for_job(
    inputs: dict[str, Any], flows_json: str | None
) -> dict[str, dict]:
    """Run the existing filename-scoped QA tool in a private copy of this build."""
    with tempfile.TemporaryDirectory(prefix="uiux-verify-") as directory:
        isolated_output = Path(directory)
        for page in inputs["pages"]:
            destination = isolated_output / page["filename"]
            content = page["html"].encode("utf-8")
            if len(content) > build.MAX_PAGE_BYTES:
                raise VerifyError("S9 page exceeds the existing 256 KB limit")
            with destination.open("xb") as stream:
                stream.write(content)
        module_path = Path(__file__).parent / "tools" / "verify_html.py"
        module_name = f"_verify_html_job_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise VerifyError("Existing browser QA module could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.OUTPUT_DIR = isolated_output
        results = {}
        for page in inputs["pages"]:
            results[page["filename"]] = module.verify_html(
                page["filename"], flows_json=flows_json
            )
        return results


def _provider_response(value: Any) -> dict[str, Any]:
    envelope = _object(
        value,
        ("schema_version", "summary", "findings"),
        "provider review",
    )
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise VerifyError("provider review schema_version must be '1.0'")
    summary = _text(envelope["summary"], "provider review summary")
    findings = envelope["findings"]
    if not isinstance(findings, list) or len(findings) > MAX_ARRAY_ITEMS:
        raise VerifyError("provider review findings must be a bounded list")
    normalized = []
    for index, finding in enumerate(findings):
        item = _object(finding, _PROVIDER_FINDING_FIELDS, f"provider findings[{index}]")
        normalized_item = {
            key: _text(item[key], f"provider findings[{index}].{key}")
            for key in _PROVIDER_FINDING_FIELDS
        }
        if normalized_item["severity"] not in _PROVIDER_SEVERITIES:
            raise VerifyError("provider finding severity is invalid")
        normalized.append(normalized_item)
    return {"status": "PASS", "summary": summary, "findings": normalized}


def _provider_review(
    pages: list[dict[str, Any]],
    call_fn: Callable[..., Any] | None,
    providers: list[dict] | None,
) -> dict[str, Any]:
    configured = config.PROVIDERS if providers is None else providers
    if not provider_gateway.eligible_providers(configured, {"text", "json_output"}):
        return {
            "status": "NOT_VERIFIED",
            "summary": "No configured text and JSON-output provider is available.",
            "findings": [],
        }
    data = [
        {"page": page["page"], "filename": page["filename"], "html": page["html"]}
        for page in pages
    ]
    try:
        result = (call_fn or gateway_call)(
            [
                {"role": "system", "content": _PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(data, ensure_ascii=True, separators=(",", ":")),
                },
            ],
            [],
            {"text", "json_output"},
            providers=configured,
            response_format={"type": "json_object"},
        )
        response = getattr(result, "response", None)
        choices = getattr(response, "choices", None)
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_PROVIDER_RESPONSE_BYTES:
            raise VerifyError("provider review response is missing or oversized")
        parsed = json.loads(content)
        return _provider_response(parsed)
    except Exception as exc:
        log.warning("Verify supplemental provider review unavailable (%s)", type(exc).__name__)
        return {
            "status": "NOT_VERIFIED",
            "summary": "Supplemental provider review did not complete.",
            "findings": [],
        }


def _status(gates: dict[str, dict], score_passed: bool) -> str:
    statuses = [gate["status"] for gate in gates.values()]
    if "FAIL" in statuses or not score_passed:
        return "FAIL"
    if "NOT_VERIFIED" in statuses:
        return "NEEDS_INPUT"
    return "PASS"


def _evaluate(
    inputs: dict[str, Any],
    flows_json: str | None,
    browser_qa_fn: Callable[..., Any] | None,
    provider_call_fn: Callable[..., Any] | None,
    providers: list[dict] | None,
) -> dict[str, Any]:
    try:
        flows = _parse_flows(flows_json)
    except ValueError as exc:
        raise VerifyError(f"required user-flow JSON is invalid: {exc}") from exc
    if flows_json is not None and len(flows_json.encode("utf-8")) > 64 * 1024:
        raise VerifyError("required user-flow JSON exceeds 64 KB")
    flow_data = json.dumps({"flows": flows}, ensure_ascii=True, separators=(",", ":")) if flows else None
    filename_set = {page["filename"] for page in inputs["pages"]}
    browser_results: dict[str, dict] = {}
    browser_error = False
    if browser_qa_fn is None and any(
        _static_page_gates(page["html"], filename_set)[0]["html_security"]["status"] == "PASS"
        for page in inputs["pages"]
    ):
        try:
            browser_results = _browser_qa_for_job(inputs, flow_data)
        except Exception as exc:
            log.warning("Verify browser QA unavailable (%s)", type(exc).__name__)
            browser_error = True
    page_results = []
    aggregate: dict[str, list[dict]] = {name: [] for name in (*_STATIC_GATES, *_BROWSER_GATES)}
    for page in inputs["pages"]:
        static_gates, static_detail = _static_page_gates(page["html"], filename_set)
        for name, result in static_gates.items():
            aggregate[name].append(result)
        path = inputs["artifact_root"].joinpath(
            *PurePosixPath(page["artifact_ref"]).parts
        )
        browser_gates: dict[str, dict]
        quality: dict[str, Any]
        if static_gates["html_security"]["status"] != "PASS":
            reason = "Browser QA was skipped because static HTML security validation failed."
            browser_gates = {
                name: {"status": "NOT_VERIFIED", "evidence": reason}
                for name in _BROWSER_GATES
            }
            quality = {"score": 0, "score_gate_passed": False}
        else:
            try:
                if browser_qa_fn is None:
                    if browser_error:
                        raise RuntimeError("Browser QA unavailable")
                    qa = browser_results[page["filename"]]
                else:
                    qa = browser_qa_fn(path, flows_json=flow_data)
                browser_gates, quality = _browser_gate_summary(qa, bool(flows))
            except Exception as exc:
                log.warning("Verify browser QA unavailable (%s)", type(exc).__name__)
                reason = "Browser QA did not complete."
                browser_gates = {
                    name: {"status": "NOT_VERIFIED", "evidence": reason}
                    for name in _BROWSER_GATES
                }
                quality = {"score": 0, "score_gate_passed": False}
        for name, result in browser_gates.items():
            aggregate[name].append(result)
        # Keep the existing deterministic soft score available even when browser
        # tooling is absent; the browser helper's copy is not treated as evidence.
        critic_result = critique_code(str(path))
        soft_score = critic_result.get("score", 0)
        soft_pass = critic_result.get("score_gate_passed") is True
        page_results.append(
            {
                "page": page["page"],
                "filename": page["filename"],
                "sha256": inputs["source_sha256"][page["artifact_ref"]],
                "hard_gates": {
                    **static_gates,
                    **browser_gates,
                },
                "static_semantics": static_detail,
                "soft_quality": {
                    "score": soft_score if isinstance(soft_score, int) else 0,
                    "score_gate_passed": soft_pass,
                    "weighted_score_breakdown": critic_result.get("weighted_score_breakdown", {}),
                },
            }
        )
    hard_gates = {}
    for name, results in aggregate.items():
        statuses = [result["status"] for result in results]
        status = "FAIL" if "FAIL" in statuses else (
            "NOT_VERIFIED" if "NOT_VERIFIED" in statuses else "PASS"
        )
        evidence = []
        for result in results:
            encoded = json.dumps(result["evidence"], ensure_ascii=True, default=str)
            evidence.append(
                result["evidence"]
                if len(encoded) <= MAX_EVIDENCE_CHARS
                else encoded[:MAX_EVIDENCE_CHARS]
            )
        hard_gates[name] = {"status": status, "evidence": evidence}
    scores = [item["soft_quality"]["score"] for item in page_results]
    score_passed = all(item["soft_quality"]["score_gate_passed"] for item in page_results)
    status = _status(hard_gates, score_passed)
    if all(item["hard_gates"]["html_security"]["status"] == "PASS" for item in page_results):
        provider_review = _provider_review(page_results_data(inputs), provider_call_fn, providers)
    else:
        provider_review = {
            "status": "NOT_VERIFIED",
            "summary": "Supplemental review was skipped because unsafe HTML failed static validation.",
            "findings": [],
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "verified": status == "PASS",
        "job_id": inputs["job"]["id"],
        "run_id": "",
        "source_artifact_refs": list(inputs["source_artifact_refs"]),
        "source_sha256": dict(inputs["source_sha256"]),
        "hard_gates": hard_gates,
        "soft_quality": {
            "score": round(sum(scores) / len(scores)) if scores else 0,
            "score_gate_passed": score_passed,
            "threshold": 80,
        },
        "provider_review": provider_review,
        "pages": page_results,
    }


def page_results_data(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"page": page["page"], "filename": page["filename"], "html": page["html"]}
        for page in inputs["pages"]
    ]


def _validate_report(report: Any, job_id: str) -> dict[str, Any]:
    value = _object(report, _REPORT_FIELDS, "verification report")
    if value["schema_version"] != SCHEMA_VERSION or value["job_id"] != job_id:
        raise VerifyError("verification report schema or job id is invalid")
    if value["status"] not in {"PASS", "FAIL", "NEEDS_INPUT"}:
        raise VerifyError("verification report status is invalid")
    if value["verified"] is not (value["status"] == "PASS"):
        raise VerifyError("verification status conflicts with the verified flag")
    if (
        not isinstance(value["source_artifact_refs"], list)
        or not value["source_artifact_refs"]
        or len(value["source_artifact_refs"]) > 21
        or any(
            not isinstance(reference, str)
            or "\\" in reference
            or not reference.startswith(f"job_artifacts/{job_id}/")
            for reference in value["source_artifact_refs"]
        )
    ):
        raise VerifyError("verification source refs are malformed")
    if (
        not isinstance(value["source_sha256"], dict)
        or set(value["source_sha256"]) != set(value["source_artifact_refs"])
        or any(
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in value["source_sha256"].values()
        )
    ):
        raise VerifyError("verification source digests are malformed")
    if (
        not isinstance(value["hard_gates"], dict)
        or set(value["hard_gates"]) != set((*_STATIC_GATES, *_BROWSER_GATES))
        or any(
            not isinstance(item, dict)
            or set(item) != {"status", "evidence"}
            or item.get("status") not in {"PASS", "FAIL", "NOT_VERIFIED"}
            for item in value["hard_gates"].values()
        )
    ):
        raise VerifyError("verification hard gates are malformed")
    if (
        not isinstance(value["soft_quality"], dict)
        or not isinstance(value["soft_quality"].get("score"), int)
        or isinstance(value["soft_quality"].get("score"), bool)
        or not isinstance(value["soft_quality"].get("score_gate_passed"), bool)
    ):
        raise VerifyError("verification soft quality details are malformed")
    expected_status = _status(
        value["hard_gates"], value["soft_quality"]["score_gate_passed"]
    )
    if value["status"] != expected_status:
        raise VerifyError("verification status contradicts its hard gates or soft score")
    if not isinstance(value["pages"], list) or not value["pages"] or len(value["pages"]) > 20:
        raise VerifyError("verification page reports are malformed")
    if not isinstance(value["provider_review"], dict) or value["provider_review"].get(
        "status"
    ) not in {"PASS", "NOT_VERIFIED"}:
        raise VerifyError("supplemental provider review is malformed")
    if any(not isinstance(page, dict) for page in value["pages"]):
        raise VerifyError("verification page reports are malformed")
    return value


def _reject_link_components(path: Path) -> None:
    _check_path_components(path)


def _write_atomic(path: Path, content: bytes) -> None:
    if len(content) > MAX_REPORT_BYTES:
        raise VerifyError("verification report exceeds 1 MiB")
    _reject_link_components(path)
    if path.exists():
        raise VerifyError("verification report artifacts are immutable")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    linked = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_link_components(path)
        if path.exists():
            raise VerifyError("verification report artifacts are immutable")
        os.link(temporary, path)
        linked = True
        temporary.unlink()
    except Exception:
        if linked:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _save_report(
    job_id: str, report: dict[str, Any], artifact_root: Path
) -> tuple[str, str, Path, bytes]:
    try:
        safe_root = planning._safe_base(artifact_root / "job_artifacts")
    except planning.PlanningError as exc:
        raise VerifyError(str(exc)) from exc
    job_dir = safe_root / job_id
    _reject_link_components(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    _reject_link_components(job_dir)
    if job_dir.resolve(strict=True).parent != safe_root.resolve(strict=True):
        raise VerifyError("Verify job artifact directory escapes the artifact root")
    run_id = str(uuid.uuid4())
    run_dir = job_dir / run_id
    run_dir.mkdir(exist_ok=False)
    _reject_link_components(run_dir)
    if run_dir.resolve(strict=True).parent != job_dir.resolve(strict=True):
        raise VerifyError("Verify run directory escapes the active job directory")
    report["run_id"] = run_id
    _validate_report(report, job_id)
    content = json.dumps(
        report, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    try:
        _write_atomic(run_dir / REPORT_NAME, content)
    except Exception:
        run_dir.rmdir()
        raise
    reference = f"job_artifacts/{job_id}/{run_id}/{REPORT_NAME}"
    return reference, run_id, run_dir, content


def _remove_current_run(run_dir: Path, expected_content: bytes) -> None:
    _reject_link_components(run_dir)
    if not run_dir.exists():
        return
    report_path = run_dir / REPORT_NAME
    _reject_link_components(report_path)
    if report_path.exists():
        if report_path.resolve(strict=True).parent != run_dir.resolve(strict=True):
            raise VerifyError("current Verify report escaped its run directory")
        if report_path.read_bytes() != expected_content:
            raise VerifyError("current Verify report changed before cleanup")
        report_path.unlink()
    run_dir.rmdir()


def verify_job(
    job_id: str,
    flows_json: str | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    browser_qa_fn: Callable[..., Any] | None = None,
    provider_call_fn: Callable[..., Any] | None = None,
    providers: list[dict] | None = None,
) -> dict[str, Any]:
    """Verify the latest immutable Build/Refine run and persist its evidence."""
    inputs = load_verify_inputs(job_id, db_path, artifact_root)
    report = _evaluate(inputs, flows_json, browser_qa_fn, provider_call_fn, providers)
    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    reference, run_id, run_dir, content = _save_report(inputs["job"]["id"], report, root)
    refs = [reference]
    metadata = {
        "integration": VERIFY_INTEGRATION,
        "schema_version": SCHEMA_VERSION,
        "status": report["status"],
        "verified": report["verified"],
        "run_id": run_id,
        "artifact_refs": refs,
        "source_artifact_refs": list(inputs["source_artifact_refs"]),
        "report_sha256": hashlib.sha256(content).hexdigest(),
        "hard_gates_status": (
            "FAIL" if any(item["status"] == "FAIL" for item in report["hard_gates"].values())
            else "NOT_VERIFIED"
            if any(item["status"] == "NOT_VERIFIED" for item in report["hard_gates"].values())
            else "PASS"
        ),
    }
    try:
        if report["status"] == "NEEDS_INPUT" or inputs["job"]["state"] == "VERIFY":
            orchestrator.record_checkpoint(
                inputs["job"]["id"],
                inputs["job"]["state"],
                VERIFY_ACTOR,
                "Verify evidence saved for the latest immutable Build run",
                refs,
                metadata,
                expected_revision=inputs["job"]["revision"],
                db_path=db_path,
            )
        else:
            orchestrator.transition_job(
                inputs["job"]["id"],
                "VERIFY",
                VERIFY_ACTOR,
                "Persist deterministic and browser Verify evidence",
                inputs["job"]["revision"],
                artifact_refs=refs,
                metadata=metadata,
                db_path=db_path,
                checkpoint={
                    "stage": "BUILD",
                    "actor": VERIFY_ACTOR,
                    "reason": "Save immutable Verify report",
                },
            )
    except Exception as exc:
        try:
            _remove_current_run(run_dir, content)
        except Exception as cleanup_error:
            raise VerifyError(
                f"Verify persistence failed and current-run cleanup failed: {cleanup_error}"
            ) from exc
        raise
    return {
        "report": report,
        "artifact_refs": refs,
        "run_id": run_id,
        "job": orchestrator.get_job(inputs["job"]["id"], db_path),
    }


def load_saved_verification(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load the active job's immutable Verify report without rerunning checks."""
    if job.get("mode") != "create" or job.get("state") not in {"BUILD", "VERIFY", "REFINE"}:
        raise VerifyError("saved Verify output requires a Create job in BUILD, VERIFY, or REFINE")
    values = app_jobs.saved_verify_artifacts(job, artifact_root)
    if values is None:
        return None
    report = _validate_report(values[REPORT_NAME], job["id"])
    checkpoint = next(
        (
            item for item in reversed(_job_checkpoints(job))
            if item.get("stage") == "BUILD"
            and item.get("metadata", {}).get("integration") == VERIFY_INTEGRATION
        ),
        None,
    )
    if (
        checkpoint is None
        or checkpoint.get("metadata", {}).get("run_id") != report["run_id"]
        or checkpoint.get("metadata", {}).get("status") != report["status"]
        or checkpoint.get("metadata", {}).get("source_artifact_refs")
        != report["source_artifact_refs"]
    ):
        raise VerifyError("saved Verify report does not match its active-job checkpoint")
    reference = checkpoint["artifact_refs"][0]
    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    report_bytes = _read_ref(reference, job["id"], root)
    if hashlib.sha256(report_bytes).hexdigest() != checkpoint.get("metadata", {}).get(
        "report_sha256"
    ):
        raise VerifyError("saved Verify report failed its checkpoint integrity digest")
    return report


def _job_checkpoints(job: dict[str, Any]) -> list[dict[str, Any]]:
    checkpoints = job.get("checkpoints")
    if isinstance(checkpoints, list):
        return [item for item in checkpoints if isinstance(item, dict)]
    latest = job.get("latest_checkpoint")
    return [latest] if isinstance(latest, dict) else []
