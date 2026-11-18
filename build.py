"""Bounded Create-mode HTML Build stage.

Provider JSON is limited to 1 MiB per run, each generated HTML page and each
saved page to 256 KiB, and no more than 20 pages are accepted. These limits
bound both untrusted model output and local immutable artifact growth.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import app_jobs
import config
import design
import job_store
import orchestrator
import planning
import provider_gateway

gateway_call = provider_gateway.call

SCHEMA_VERSION = "1.0"
MAX_RESPONSE_BYTES = app_jobs.MAX_BUILD_TOTAL_BYTES
MAX_PAGE_BYTES = app_jobs.MAX_BUILD_PAGE_BYTES
MAX_STRING_CHARS = 2000
MAX_ARRAY_ITEMS = 20
BUILD_ACTOR = "html-builder"
BUILD_INTEGRATION = app_jobs.BUILD_INTEGRATION
BUILD_MANIFEST_NAME = app_jobs.BUILD_MANIFEST_NAME
_BUILD_FILENAME = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,98}[a-z0-9])?\.html\Z")
_FRAGMENT = re.compile(r"[A-Za-z0-9_.:-]+\Z")
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6}\s?|.)")
_CSS_UNSAFE = re.compile(
    r"(?:url\s*\(|image-set\s*\(|@import\b|expression\s*\(|-moz-binding\b|behavior\s*:|"
    r"javascript\s*:|data\s*:)",
    re.IGNORECASE,
)
_VOID_ELEMENTS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)
_FORBIDDEN_ELEMENTS = frozenset(
    {
        "script", "iframe", "object", "embed", "base", "applet", "portal",
        "animate", "animatemotion", "animatetransform", "set", "discard",
    }
)
_FORBIDDEN_ATTRIBUTES = frozenset({"ping", "attributionsrc"})
_FORBIDDEN_SVG_ANIMATION_ATTRIBUTES = frozenset(
    {
        "attributename", "attributetype", "from", "to", "by",
        "begin", "end", "dur", "repeatcount", "repeatdur", "restart",
        "calcmode", "keytimes", "keysplines", "keypoints", "additive",
        "accumulate",
    }
)
_URL_ATTRIBUTES = frozenset(
    {
        "href", "src", "action", "formaction", "poster", "data", "cite",
        "background", "manifest", "xlink:href",
    }
)
_PROMPT = (config.PROMPTS_DIR / "build_v1.md").read_text(encoding="utf-8")


class BuildError(job_store.ValidationError):
    """Raised when a Build result cannot be validated or persisted safely."""


def _object(value: Any, fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise BuildError(f"{name} must be an object with exactly the required fields")
    return value


def _string(value: Any, name: str, *, max_chars: int = MAX_STRING_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{name} must be a non-empty string")
    if len(value) > max_chars:
        raise BuildError(f"{name} exceeds {max_chars} characters")
    return value


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_ARRAY_ITEMS
        or (not value and not allow_empty)
    ):
        raise BuildError(f"{name} must contain at most {MAX_ARRAY_ITEMS} strings")
    return [_string(item, f"{name}[{index}]") for index, item in enumerate(value)]


def _safe_local_url(value: str, filenames: frozenset[str], name: str) -> None:
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise BuildError(f"{name} must be a safe local page or fragment reference")
    if "\\" in value or value.startswith("//") or "?" in value:
        raise BuildError(f"{name} must not use a remote or ambiguous URL")
    if value.startswith("#"):
        fragment = value[1:]
        if fragment and _FRAGMENT.fullmatch(fragment):
            return
        raise BuildError(f"{name} contains an unsafe fragment")
    page, separator, fragment = value.partition("#")
    if (
        page not in filenames
        or (separator and (not fragment or not _FRAGMENT.fullmatch(fragment)))
        or "/" in page
        or ":" in page
    ):
        raise BuildError(f"{name} must reference a generated local page or safe fragment")


def _decode_css_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escaped = match.group(1).strip()
        if not escaped:
            return ""
        if re.fullmatch(r"[0-9a-fA-F]{1,6}", escaped):
            try:
                return chr(int(escaped, 16))
            except (ValueError, OverflowError):
                return " "
        return escaped

    return _CSS_ESCAPE.sub(replace, value)


def _validate_css(value: str, name: str) -> None:
    without_comments = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    if _CSS_UNSAFE.search(_decode_css_escapes(without_comments)):
        raise BuildError(f"{name} contains an unsafe CSS construct")


class _SafeHTMLParser(HTMLParser):
    def __init__(self, filenames: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.filenames = filenames
        self.stack: list[str] = []
        self.counts: dict[str, int] = {}
        self.doctype_count = 0
        self.html_closed = False
        self.has_viewport = False
        self.title_text = ""
        self.style_chunks: list[str] | None = None

    def _fail(self, message: str) -> None:
        raise BuildError(message)

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() != "doctype html" or self.stack or self.doctype_count:
            self._fail("HTML must start with one HTML5 doctype")
        self.doctype_count += 1

    def unknown_decl(self, data: str) -> None:
        self._fail("HTML contains an unsupported declaration")

    def handle_comment(self, data: str) -> None:
        self._fail("HTML comments are not allowed in generated pages")

    def handle_pi(self, data: str) -> None:
        self._fail("HTML processing instructions are not allowed")

    def _check_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, str | None]:
        values: dict[str, str | None] = {}
        for name, value in attrs:
            lowered = name.lower()
            if lowered in values:
                self._fail(f"{tag} has a duplicate {lowered!r} attribute")
            if lowered.startswith("on"):
                self._fail("inline event-handler attributes are not allowed")
            if lowered in _FORBIDDEN_ATTRIBUTES:
                self._fail(f"{lowered} attributes can send external requests and are not allowed")
            if lowered in _FORBIDDEN_SVG_ANIMATION_ATTRIBUTES:
                self._fail(f"{lowered} SVG animation attributes are not allowed")
            if lowered in {"srcdoc", "srcset"}:
                self._fail(f"{lowered} attributes are not allowed")
            values[lowered] = value
        for name in _URL_ATTRIBUTES & values.keys():
            value = values[name]
            if value is None:
                self._fail(f"{name} URL attribute must have a value")
            _safe_local_url(value, self.filenames, f"{tag}.{name}")
        if "style" in values:
            _validate_css(values["style"] or "", f"{tag}.style")
        if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            self._fail("HTML refresh directives are not allowed")
        if tag == "html" and not (values.get("lang") or "").strip():
            self._fail("HTML root must declare a non-empty language")
        if tag == "meta" and (values.get("name") or "").lower() == "viewport":
            content = (values.get("content") or "").lower().replace(" ", "")
            if "width=device-width" not in content:
                self._fail("HTML viewport must declare device-width")
            self.has_viewport = True
        return values

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _FORBIDDEN_ELEMENTS:
            self._fail(f"<{tag}> elements are not allowed")
        values = self._check_attrs(tag, attrs)
        parent = self.stack[-1] if self.stack else None
        if tag == "html":
            if parent is not None or self.counts.get("html") or not self.doctype_count or self.html_closed:
                self._fail("HTML document must have one root element after its doctype")
        elif parent is None or self.html_closed:
            self._fail("HTML elements must be nested inside the document root")
        if tag in {"head", "body"} and parent != "html":
            self._fail(f"<{tag}> must be a direct child of <html>")
        if tag == "head" and self.counts.get("body"):
            self._fail("<head> must appear before <body>")
        if tag == "body" and not self.counts.get("head"):
            self._fail("<body> requires a preceding <head>")
        if tag == "title" and parent != "head":
            self._fail("<title> must be inside <head>")
        if tag == "main" and "body" not in self.stack:
            self._fail("<main> must be inside <body>")
        if tag == "link" and values.get("href") is not None:
            self._fail("external or non-page stylesheet links are not allowed")
        if tag == "style":
            if self.style_chunks is not None:
                self._fail("nested <style> elements are malformed")
            self.style_chunks = []
        self.counts[tag] = self.counts.get(tag, 0) + 1
        if tag not in _VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in _VOID_ELEMENTS:
            self._fail("non-void HTML elements cannot use self-closing syntax")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_ELEMENTS:
            self._fail(f"void element </{tag}> cannot have an end tag")
        if not self.stack or self.stack[-1] != tag:
            self._fail(f"HTML closing tag </{tag}> is malformed or out of order")
        if tag == "style" and self.style_chunks is not None:
            _validate_css("".join(self.style_chunks), "<style>")
            self.style_chunks = None
        self.stack.pop()
        if tag == "html":
            self.html_closed = True

    def handle_data(self, data: str) -> None:
        if (not self.stack or self.html_closed) and data.strip():
            self._fail("text must not appear outside the HTML document root")
        if self.stack == ["html"] and data.strip():
            self._fail("text must not appear outside <head> or <body>")
        if self.stack and self.stack[-1] == "title":
            self.title_text += data
        if self.style_chunks is not None:
            self.style_chunks.append(data)


def validate_html_document(html: Any, filenames: set[str] | frozenset[str]) -> str:
    """Validate a complete static document and reject executable/remote content."""
    if not isinstance(html, str) or not html.strip():
        raise BuildError("HTML page must be a non-empty string")
    try:
        size = len(html.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise BuildError("HTML page must be valid UTF-8 text") from exc
    if size > MAX_PAGE_BYTES:
        raise BuildError("HTML page exceeds 256 KB")
    available = frozenset(filenames)
    parser = _SafeHTMLParser(available)
    try:
        parser.feed(html)
        parser.close()
    except BuildError:
        raise
    except (ValueError, AssertionError) as exc:
        raise BuildError("HTML document is malformed") from exc
    if (
        parser.doctype_count != 1
        or parser.stack
        or not parser.html_closed
        or parser.counts.get("html") != 1
        or parser.counts.get("head") != 1
        or parser.counts.get("body") != 1
        or parser.counts.get("title") != 1
        or not parser.title_text.strip()
        or not parser.has_viewport
        or parser.counts.get("main", 0) < 1
    ):
        raise BuildError(
            "HTML must be a complete document with doctype, lang, title, viewport, and main"
        )
    return html


def validate_envelope(value: Any, page_specs: list[dict[str, Any]]) -> dict[str, Any]:
    envelope = _object(
        value,
        ("schema_version", "status", "pages", "clarification_questions"),
        "model response",
    )
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise BuildError("schema_version must be '1.0'")
    status = _string(envelope["status"], "status")
    if status not in {"completed", "needs_input"}:
        raise BuildError("status must be 'completed' or 'needs_input'")
    questions = _strings(
        envelope["clarification_questions"],
        "clarification_questions",
        allow_empty=status == "completed",
    )
    pages = envelope["pages"]
    if not isinstance(pages, list) or len(pages) > MAX_ARRAY_ITEMS:
        raise BuildError("pages must be a list of at most 20 items")
    if status == "needs_input":
        if pages:
            raise BuildError("needs_input responses must not contain pages")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "pages": [],
            "clarification_questions": questions,
        }
    if questions:
        raise BuildError("completed responses cannot contain clarification questions")
    if not pages:
        raise BuildError("completed responses must contain pages")
    expected_names = [_string(item.get("page"), "design page name") for item in page_specs]
    if not expected_names or len(expected_names) > MAX_ARRAY_ITEMS:
        raise BuildError("Design must contain between 1 and 20 pages")
    if len(set(expected_names)) != len(expected_names) or len(pages) != len(expected_names):
        raise BuildError("completed pages must match each Design page exactly once")

    normalized_pages: list[dict[str, str]] = []
    seen_pages: set[str] = set()
    seen_filenames: set[str] = set()
    aggregate_bytes = 0
    for index, item in enumerate(pages):
        page = _object(item, ("page", "filename", "html"), f"pages[{index}]")
        page_name = _string(page["page"], f"pages[{index}].page")
        filename = _string(page["filename"], f"pages[{index}].filename")
        html = page["html"]
        if not _BUILD_FILENAME.fullmatch(filename):
            raise BuildError(f"pages[{index}].filename must be a canonical single .html filename")
        if page_name not in expected_names or page_name in seen_pages:
            raise BuildError("completed pages must map to every Design page exactly once")
        if filename in seen_filenames:
            raise BuildError("page filenames must be unique")
        if not isinstance(html, str) or not html.strip():
            raise BuildError(f"pages[{index}].html must be a non-empty string")
        try:
            page_bytes = len(html.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise BuildError(f"pages[{index}].html must be valid UTF-8") from exc
        if page_bytes > MAX_PAGE_BYTES:
            raise BuildError("HTML page exceeds 256 KB")
        aggregate_bytes += page_bytes
        if aggregate_bytes > MAX_RESPONSE_BYTES:
            raise BuildError("HTML pages exceed the 1 MB aggregate build limit")
        seen_pages.add(page_name)
        seen_filenames.add(filename)
        normalized_pages.append({"page": page_name, "filename": filename, "html": html})
    if seen_pages != set(expected_names):
        raise BuildError("completed pages must map to every Design page exactly once")
    filenames = {page["filename"] for page in normalized_pages}
    for page in normalized_pages:
        validate_html_document(page["html"], filenames)
    page_by_name = {page["page"]: page for page in normalized_pages}
    ordered_pages = [page_by_name[name] for name in expected_names]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "pages": ordered_pages,
        "clarification_questions": [],
    }


def _response_json(response: Any, page_specs: list[dict[str, Any]]) -> dict[str, Any]:
    choices = getattr(response, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        raise BuildError("Build provider returned no JSON content")
    try:
        response_bytes = len(content.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise BuildError("Build provider response must be valid UTF-8") from exc
    if response_bytes > MAX_RESPONSE_BYTES:
        raise BuildError("Build provider response exceeds 1 MB")
    if not content.strip():
        raise BuildError("Build provider returned no JSON content")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(content, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise BuildError("Build provider returned malformed JSON") from exc
    return validate_envelope(value, page_specs)


def _source_artifacts(
    job_id: str,
    checkpoints: list[dict[str, Any]],
    data_root: str | Path | None,
) -> dict[str, Any]:
    try:
        brief, ux_plan, s7_refs = design._latest_s7_plan(job_id, checkpoints, data_root)
    except (design.DesignError, job_store.ValidationError) as exc:
        raise BuildError("Build requires valid, job-scoped S7 Product Brief and UX Plan artifacts") from exc

    design_checkpoint = next(
        (
            item for item in reversed(checkpoints)
            if item.get("metadata", {}).get("integration") == "s8-design"
        ),
        None,
    )
    if (
        not design_checkpoint
        or design_checkpoint.get("stage") != "PLANNING"
        or design_checkpoint.get("metadata", {}).get("status") != "completed"
    ):
        raise BuildError("Build requires the latest completed S8 Design checkpoint")
    design_refs = design_checkpoint.get("artifact_refs")
    metadata = design_checkpoint.get("metadata", {})
    if (
        not isinstance(metadata, dict)
        or not isinstance(design_refs, list)
        or len(design_refs) != 2
        or metadata.get("artifact_refs") != design_refs
        or metadata.get("source_artifact_refs") != s7_refs
        or {PurePosixPath(ref).name for ref in design_refs if isinstance(ref, str)}
        != app_jobs.DESIGN_ARTIFACT_NAMES
    ):
        raise BuildError("S8 Design checkpoint references are incomplete or inconsistent")
    try:
        loaded_design = app_jobs.saved_design_artifacts(
            {"id": job_id, "checkpoints": checkpoints}, data_root
        )
        if loaded_design is None or set(loaded_design) != app_jobs.DESIGN_ARTIFACT_NAMES:
            raise BuildError("S8 Design artifacts are missing")
        design_spec = design.validate_design_spec(loaded_design["design_spec.json"], ux_plan)
        design_tokens = design.validate_design_tokens(loaded_design["design_tokens.json"])
    except (job_store.ValidationError, design.DesignError) as exc:
        raise BuildError("S8 Design artifacts are invalid or not job-scoped") from exc

    return {
        "product_brief": brief,
        "ux_plan": ux_plan,
        "design_spec": design_spec,
        "design_tokens": design_tokens,
        "source_artifact_refs": [*s7_refs, *design_refs],
    }


def _canonical_job_uuid(job_id: str) -> str:
    try:
        return design._canonical_job_uuid(job_id)
    except job_store.ValidationError as exc:
        raise BuildError(str(exc)) from exc


def load_build_inputs(
    job_id: str,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load and validate this Create job's completed S7 and S8 inputs."""
    normalized_job_id = _canonical_job_uuid(job_id)
    job = orchestrator.get_job(normalized_job_id, db_path)
    if job["mode"] != "create":
        raise BuildError("Build is available only for Create jobs")
    if job["state"] != "DESIGN":
        raise BuildError("Build requires a job in DESIGN")
    checkpoints = orchestrator.list_checkpoints(normalized_job_id, db_path)
    prior_build = next(
        (
            item for item in reversed(checkpoints)
            if item.get("stage") == "DESIGN"
            and item.get("metadata", {}).get("integration") == BUILD_INTEGRATION
        ),
        None,
    )
    if prior_build and prior_build.get("metadata", {}).get("status") == "completed":
        raise BuildError("a completed Build already exists for this job")
    return {
        "job": job,
        "checkpoints": checkpoints,
        **_source_artifacts(normalized_job_id, checkpoints, artifact_root),
    }


def _reject_link_components(path: Path) -> None:
    if ".." in path.parts:
        raise BuildError("artifact paths must not contain parent traversal")
    candidate = path.absolute()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in reversed((candidate, *candidate.parents)):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(details, "st_file_attributes", 0)
        if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
            raise BuildError("artifact paths must not contain symlink or reparse-point components")


def _write_atomic(path: Path, content: bytes, max_bytes: int, name: str) -> None:
    if len(content) > max_bytes:
        raise BuildError(f"{name} exceeds its configured artifact size limit")
    _reject_link_components(path)
    if path.exists():
        raise BuildError("Build artifact files are immutable and must not be overwritten")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    _reject_link_components(temporary)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    linked = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_link_components(path)
        if path.exists():
            raise BuildError("Build artifact files are immutable and must not be overwritten")
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


def _remove_run_directory(
    run_dir: Path, filenames: list[str], manifest_created: bool
) -> None:
    _reject_link_components(run_dir)
    if not run_dir.exists():
        return
    if not run_dir.is_dir():
        raise BuildError("failed Build run path is not a directory")
    resolved_dir = run_dir.resolve(strict=True)
    manifest = run_dir / BUILD_MANIFEST_NAME
    _reject_link_components(manifest)
    if manifest_created:
        if not manifest.exists():
            raise BuildError("current Build manifest disappeared before cleanup")
        if manifest.resolve(strict=True).parent != resolved_dir or not manifest.is_file():
            raise BuildError("failed Build run contains an unexpected manifest path")
        manifest.unlink()
    build_dir = run_dir / "build"
    _reject_link_components(build_dir)
    if build_dir.exists():
        if not build_dir.is_dir() or build_dir.resolve(strict=True).parent != resolved_dir:
            raise BuildError("failed Build run contains an unexpected output directory")
        for filename in filenames:
            child = build_dir / filename
            _reject_link_components(child)
            if child.exists():
                if child.resolve(strict=True).parent != build_dir.resolve(strict=True) or not child.is_file():
                    raise BuildError("failed Build run contains an unexpected page path")
                child.unlink()
        build_dir.rmdir()
    run_dir.rmdir()


def _save_artifacts(
    job_id: str,
    pages: list[dict[str, str]],
    source_artifact_refs: list[str],
    data_root: str | Path | None,
) -> tuple[list[str], str, Path, dict[str, Any]]:
    try:
        safe_root = planning._safe_base(
            (Path(data_root) if data_root is not None else config.DATA_DIR) / "job_artifacts"
        )
    except planning.PlanningError as exc:
        raise BuildError(str(exc)) from exc
    normalized_job_id = _canonical_job_uuid(job_id)
    job_dir = safe_root / normalized_job_id
    _reject_link_components(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    _reject_link_components(job_dir)
    if job_dir.resolve(strict=True).parent != safe_root.resolve(strict=True):
        raise BuildError("job artifact directory escapes the artifact root")

    run_id = str(uuid.uuid4())
    run_dir = job_dir / run_id
    try:
        run_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise BuildError("Build run ID collision; existing artifacts were preserved") from exc
    _reject_link_components(run_dir)
    if run_dir.resolve(strict=True).parent != job_dir.resolve(strict=True):
        raise BuildError("Build run directory escapes the active job directory")
    build_dir = run_dir / "build"
    written_filenames: list[str] = []
    manifest_created = False
    try:
        build_dir.mkdir()
        _reject_link_components(build_dir)
        if build_dir.resolve(strict=True).parent != run_dir.resolve(strict=True):
            raise BuildError("Build output directory escapes the current run")
        total_bytes = 0
        for page in pages:
            content = page["html"].encode("utf-8")
            total_bytes += len(content)
            if total_bytes > MAX_RESPONSE_BYTES:
                raise BuildError("Build artifacts exceed 1 MB in aggregate")
            _write_atomic(build_dir / page["filename"], content, MAX_PAGE_BYTES, "HTML page")
            written_filenames.append(page["filename"])
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "pages": [
                {"page": page["page"], "filename": page["filename"]}
                for page in pages
            ],
            "source_artifact_refs": list(source_artifact_refs),
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        if total_bytes + len(manifest_bytes) > MAX_RESPONSE_BYTES:
            raise BuildError("Build artifacts exceed 1 MB in aggregate")
        _write_atomic(run_dir / BUILD_MANIFEST_NAME, manifest_bytes, 64 * 1024, "Build manifest")
        manifest_created = True
    except Exception as exc:
        try:
            _remove_run_directory(run_dir, written_filenames, manifest_created)
        except Exception as cleanup_error:
            raise BuildError(
                f"failed to write Build artifacts and clean up the current run: {cleanup_error}"
            ) from exc
        raise

    refs = [
        f"job_artifacts/{normalized_job_id}/{run_id}/build/{page['filename']}"
        for page in pages
    ]
    refs.append(f"job_artifacts/{normalized_job_id}/{run_id}/{BUILD_MANIFEST_NAME}")
    return refs, run_id, run_dir, manifest


def _provider_audit(gateway_result: Any) -> dict[str, Any]:
    provider = getattr(gateway_result, "provider", None)
    result: dict[str, Any] = {"attempts": getattr(gateway_result, "attempts", 0)}
    if isinstance(provider, dict):
        for key in ("provider_id", "model_id", "model"):
            value = provider.get(key)
            if isinstance(value, str) and value:
                result[key] = value[:200]
    return result


def build_job(
    job_id: str,
    clarification: str | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    gateway_call_fn: Callable[..., provider_gateway.GatewayResult] | None = None,
    providers: list[dict] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Generate, validate, and persist HTML pages for a completed S8 Design."""
    inputs = load_build_inputs(job_id, db_path, artifact_root)
    job = inputs["job"]
    normalized_job_id = job["id"]
    if clarification is not None:
        clarification = _string(clarification, "clarification")
    last_build_checkpoint = next(
        (
            item for item in reversed(inputs["checkpoints"])
            if item.get("stage") == "DESIGN"
            and item.get("metadata", {}).get("integration") == BUILD_INTEGRATION
        ),
        None,
    )
    if (
        last_build_checkpoint
        and last_build_checkpoint.get("metadata", {}).get("status") == "needs_input"
        and clarification is None
    ):
        raise BuildError("a response to the saved Build clarification questions is required")

    user_data = {
        "product_brief": inputs["product_brief"],
        "ux_plan": inputs["ux_plan"],
        "design_spec": inputs["design_spec"],
        "design_tokens": inputs["design_tokens"],
        "user_clarifications": clarification,
    }
    call_fn = gateway_call_fn or gateway_call
    call_options: dict[str, Any] = {
        "providers": providers if providers is not None else config.PROVIDERS,
        "response_format": {"type": "json_object"},
    }
    if status_callback is not None:
        call_options["status_callback"] = status_callback
    gateway_result = call_fn(
        [
            {"role": "system", "content": _PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_data, ensure_ascii=True, separators=(",", ":")),
            },
        ],
        [],
        {"text", "json_output"},
        **call_options,
    )
    if getattr(gateway_result, "response", None) is None:
        raise BuildError("all eligible Build providers failed")
    envelope = _response_json(
        gateway_result.response, inputs["design_spec"]["page_specs"]
    )
    provider_audit = _provider_audit(gateway_result)
    source_refs = inputs["source_artifact_refs"]

    if envelope["status"] == "needs_input":
        metadata = {
            "integration": BUILD_INTEGRATION,
            "schema_version": SCHEMA_VERSION,
            "status": "needs_input",
            "artifact_refs": [],
            "source_artifact_refs": source_refs,
            "clarification_questions": envelope["clarification_questions"],
            "provider": provider_audit,
        }
        orchestrator.record_checkpoint(
            normalized_job_id,
            "DESIGN",
            BUILD_ACTOR,
            "Build requires user clarification",
            [],
            metadata,
            expected_revision=job["revision"],
            db_path=db_path,
        )
        return {
            **envelope,
            "artifact_refs": [],
            "run_id": None,
            "job": orchestrator.get_job(normalized_job_id, db_path),
        }

    refs, run_id, run_dir, manifest = _save_artifacts(
        normalized_job_id,
        envelope["pages"],
        source_refs,
        artifact_root,
    )
    metadata = {
        "integration": BUILD_INTEGRATION,
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "run_id": run_id,
        "artifact_refs": refs,
        "source_artifact_refs": source_refs,
        "provider": provider_audit,
    }
    try:
        orchestrator.transition_job(
            normalized_job_id,
            "BUILD",
            BUILD_ACTOR,
            "Validated HTML pages and Build manifest saved",
            job["revision"],
            artifact_refs=refs,
            metadata=metadata,
            db_path=db_path,
            checkpoint={
                "stage": "DESIGN",
                "actor": BUILD_ACTOR,
                "reason": "Save completed Build artifacts",
            },
        )
    except Exception as exc:
        try:
            _remove_run_directory(
                run_dir,
                [page["filename"] for page in envelope["pages"]],
                manifest_created=True,
            )
        except Exception as cleanup_error:
            raise BuildError(
                f"Build transition failed and current-run cleanup failed: {cleanup_error}"
            ) from exc
        raise
    return {
        **envelope,
        "manifest": manifest,
        "artifact_refs": refs,
        "run_id": run_id,
        "job": orchestrator.get_job(normalized_job_id, db_path),
    }


def load_completed_build(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Reload and revalidate a saved Build without invoking a provider."""
    if job.get("mode") != "create" or job.get("state") not in {"BUILD", "VERIFY", "REFINE"}:
        raise BuildError("saved Build output requires a Create job in BUILD, VERIFY, or REFINE")
    values = app_jobs.saved_build_artifacts(job, artifact_root)
    if values is None:
        return None
    checkpoints = _job_checkpoints(job)
    sources = _source_artifacts(job["id"], checkpoints, artifact_root)
    manifest = values[BUILD_MANIFEST_NAME]
    if manifest["source_artifact_refs"] != sources["source_artifact_refs"]:
        raise BuildError("Build manifest references do not match the saved S7/S8 inputs")
    expected_pages = [item["page"] for item in sources["design_spec"]["page_specs"]]
    manifest_pages = [item["page"] for item in manifest["pages"]]
    if manifest_pages != expected_pages:
        raise BuildError("Build manifest pages do not match the saved S8 Design")
    filenames = {item["filename"] for item in manifest["pages"]}
    pages = []
    for item in manifest["pages"]:
        html = values[item["filename"]]
        validate_html_document(html, filenames)
        pages.append({**item, "html": html})
    return {"manifest": manifest, "pages": pages}


def _job_checkpoints(job: dict[str, Any]) -> list[dict[str, Any]]:
    checkpoints = job.get("checkpoints")
    if isinstance(checkpoints, list):
        return [item for item in checkpoints if isinstance(item, dict)]
    latest = job.get("latest_checkpoint")
    return [latest] if isinstance(latest, dict) else []
