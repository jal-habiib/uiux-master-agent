"""Create-mode design specification and token generation stage."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import app_jobs
import config
import job_store
import orchestrator
import planning
import provider_gateway

gateway_call = provider_gateway.call

SCHEMA_VERSION = "1.0"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_STRING_CHARS = 2000
MAX_ARRAY_ITEMS = 20
DESIGN_ACTOR = "ux-designer"
DESIGN_ARTIFACT_NAMES = frozenset({"design_spec.json", "design_tokens.json"})
ENVELOPE_FIELDS = (
    "schema_version", "status", "design_spec", "design_tokens",
    "clarification_questions",
)
DESIGN_SPEC_FIELDS = (
    "schema_version", "design_direction", "brand_attributes", "page_specs",
    "component_specs", "interaction_specs", "content_guidance",
    "accessibility_checks", "responsive_rules", "implementation_constraints",
    "assumptions", "open_questions",
)
TOKEN_FIELDS = ("schema_version", "color", "typography", "spacing", "radius",
                "shadows", "breakpoints", "container")
COLOR_FIELDS = (
    "primary", "secondary", "accent", "background", "surface", "text",
    "muted", "border", "success", "warning", "danger",
)
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_CSS_RADIUS = re.compile(r"(?:0|(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em|%))\Z")
_CSS_LENGTH = r"(?:0|-?(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em))"
_CSS_SHADOW = re.compile(
    rf"(?:none|{_CSS_LENGTH}\s+{_CSS_LENGTH}(?:\s+{_CSS_LENGTH})?"
    rf"(?:\s+{_CSS_LENGTH})?(?:\s+#[0-9a-fA-F]{{6,8}})?)\Z"
)
_PROMPT = (config.PROMPTS_DIR / "design_v1.md").read_text(encoding="utf-8")


class DesignError(job_store.ValidationError):
    """Raised when the Design stage cannot validate or persist a result."""


def _canonical_job_uuid(job_id: str) -> str:
    try:
        normalized = planning._job_uuid(job_id)
    except planning.PlanningError as exc:
        raise DesignError(str(exc)) from exc
    if normalized != job_id:
        raise DesignError("job_id must use canonical UUID syntax")
    return normalized


def _object(value: Any, fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DesignError(f"{name} must be an object")
    if set(value) != set(fields):
        missing = sorted(set(fields) - set(value))
        extra = sorted(set(value) - set(fields))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        raise DesignError(f"{name} has invalid fields ({'; '.join(detail)})")
    return value


def _string(value: Any, name: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignError(f"{name} must be a non-empty string")
    if len(value) > MAX_STRING_CHARS:
        raise DesignError(f"{name} exceeds {MAX_STRING_CHARS} characters")
    if exact is not None and value != exact:
        raise DesignError(f"{name} must be {exact!r}")
    return value


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ARRAY_ITEMS:
        raise DesignError(f"{name} must be a list of at most {MAX_ARRAY_ITEMS} items")
    if not value and not allow_empty:
        raise DesignError(f"{name} must not be empty")
    return [_string(item, f"{name}[{index}]") for index, item in enumerate(value)]


def _objects(
    value: Any, name: str, fields: tuple[str, ...], list_fields: frozenset[str]
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_ARRAY_ITEMS:
        raise DesignError(f"{name} must be a non-empty list of at most {MAX_ARRAY_ITEMS} items")
    result = []
    for index, item in enumerate(value):
        obj = _object(item, fields, f"{name}[{index}]")
        for field in fields:
            if field in list_fields:
                _strings(obj[field], f"{name}[{index}].{field}")
            else:
                _string(obj[field], f"{name}[{index}].{field}")
        result.append(dict(obj))
    return result


def _number(value: Any, name: str, *, minimum: float, maximum: float) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesignError(f"{name} must be a finite number")
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise DesignError(f"{name} must be finite and between {minimum} and {maximum}")
    return value


def _positive_integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DesignError(f"{name} must be a positive integer")
    return value


def validate_design_spec(value: Any, ux_plan: dict[str, Any]) -> dict[str, Any]:
    spec = _object(value, DESIGN_SPEC_FIELDS, "design_spec")
    _string(spec["schema_version"], "design_spec.schema_version", exact=SCHEMA_VERSION)
    _string(spec["design_direction"], "design_spec.design_direction")

    brand_attributes = _strings(
        spec["brand_attributes"], "design_spec.brand_attributes", allow_empty=True
    )
    if any(
        not item.startswith(("[supplied]", "[assumption]"))
        for item in brand_attributes
    ):
        raise DesignError(
            "brand_attributes must mark each item as [supplied] or [assumption]"
        )

    page_specs = _objects(
        spec["page_specs"],
        "design_spec.page_specs",
        ("page", "purpose", "layout", "sections", "primary_action", "responsive_behavior"),
        frozenset({"sections"}),
    )
    planned_pages = [
        _string(item["page"], "ux_plan.information_architecture.page")
        for item in ux_plan["information_architecture"]
    ]
    designed_pages = [item["page"] for item in page_specs]
    if (
        len(set(planned_pages)) != len(planned_pages)
        or len(set(designed_pages)) != len(designed_pages)
        or set(designed_pages) != set(planned_pages)
    ):
        raise DesignError("page_specs must reference every planned page exactly once")

    component_specs = _objects(
        spec["component_specs"],
        "design_spec.component_specs",
        ("component", "purpose", "variants", "states", "accessibility"),
        frozenset({"variants", "states"}),
    )
    components = [item["component"].casefold() for item in component_specs]
    if len(set(components)) != len(components):
        raise DesignError("component_specs component names must be unique")

    interaction_specs = _objects(
        spec["interaction_specs"],
        "design_spec.interaction_specs",
        ("interaction", "trigger", "feedback", "error_behavior", "keyboard_behavior"),
        frozenset(),
    )
    for field in (
        "content_guidance", "accessibility_checks", "responsive_rules",
        "implementation_constraints",
    ):
        _strings(spec[field], f"design_spec.{field}")
    for field in ("assumptions", "open_questions"):
        _strings(spec[field], f"design_spec.{field}", allow_empty=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "design_direction": spec["design_direction"],
        "brand_attributes": brand_attributes,
        "page_specs": page_specs,
        "component_specs": component_specs,
        "interaction_specs": interaction_specs,
        "content_guidance": list(spec["content_guidance"]),
        "accessibility_checks": list(spec["accessibility_checks"]),
        "responsive_rules": list(spec["responsive_rules"]),
        "implementation_constraints": list(spec["implementation_constraints"]),
        "assumptions": list(spec["assumptions"]),
        "open_questions": list(spec["open_questions"]),
    }


def validate_design_tokens(value: Any) -> dict[str, Any]:
    tokens = _object(value, TOKEN_FIELDS, "design_tokens")
    _string(tokens["schema_version"], "design_tokens.schema_version", exact=SCHEMA_VERSION)

    colors = _object(tokens["color"], COLOR_FIELDS, "design_tokens.color")
    for name in COLOR_FIELDS:
        color = _string(colors[name], f"design_tokens.color.{name}")
        if not _HEX_COLOR.fullmatch(color):
            raise DesignError(f"design_tokens.color.{name} must use #RRGGBB form")

    typography = _object(
        tokens["typography"],
        ("font_family", "heading_scale", "body_size", "line_height", "font_weights"),
        "design_tokens.typography",
    )
    _string(typography["font_family"], "design_tokens.typography.font_family")
    heading_scale = typography["heading_scale"]
    if not isinstance(heading_scale, list) or not heading_scale or len(heading_scale) > MAX_ARRAY_ITEMS:
        raise DesignError("heading_scale must be a non-empty finite numeric scale")
    heading_scale = [
        _number(item, f"heading_scale[{index}]", minimum=0.1, maximum=10)
        for index, item in enumerate(heading_scale)
    ]
    if any(left >= right for left, right in zip(heading_scale, heading_scale[1:])):
        raise DesignError("heading_scale must be strictly increasing")
    body_size = _number(typography["body_size"], "body_size", minimum=0.5, maximum=5)
    line_height = _number(
        typography["line_height"], "line_height", minimum=0.8, maximum=3
    )
    font_weights = typography["font_weights"]
    if not isinstance(font_weights, list) or not font_weights or len(font_weights) > MAX_ARRAY_ITEMS:
        raise DesignError("font_weights must be a non-empty list")
    for index, weight in enumerate(font_weights):
        if (
            not isinstance(weight, int)
            or isinstance(weight, bool)
            or weight < 100
            or weight > 900
            or weight % 100 != 0
        ):
            raise DesignError(f"font_weights[{index}] must be a multiple of 100 from 100 to 900")
    if len(set(font_weights)) != len(font_weights):
        raise DesignError("font_weights must not contain duplicates")

    spacing = _object(tokens["spacing"], ("unit", "scale"), "design_tokens.spacing")
    unit = _positive_integer(spacing["unit"], "design_tokens.spacing.unit")
    scale = spacing["scale"]
    if not isinstance(scale, list) or not scale or len(scale) > MAX_ARRAY_ITEMS:
        raise DesignError("design_tokens.spacing.scale must be a non-empty list")
    for index, item in enumerate(scale):
        multiple = _positive_integer(item, f"design_tokens.spacing.scale[{index}]")
        if multiple % unit:
            raise DesignError("spacing scale values must be integer multiples of unit")
    if len(set(scale)) != len(scale) or any(
        left >= right for left, right in zip(scale, scale[1:])
    ):
        raise DesignError("spacing scale must be strictly increasing without duplicates")

    radius = _strings(tokens["radius"], "design_tokens.radius")
    if any(not _CSS_RADIUS.fullmatch(item) for item in radius):
        raise DesignError("radius values must be CSS-compatible non-negative lengths")
    shadows = _strings(tokens["shadows"], "design_tokens.shadows")
    if any(not _CSS_SHADOW.fullmatch(item) for item in shadows):
        raise DesignError("shadow values must use supported CSS length and hex-color syntax")

    breakpoints = _object(
        tokens["breakpoints"], ("mobile", "tablet", "desktop"), "design_tokens.breakpoints"
    )
    breakpoint_values = [
        _positive_integer(breakpoints[name], f"design_tokens.breakpoints.{name}")
        for name in ("mobile", "tablet", "desktop")
    ]
    if not breakpoint_values[0] < breakpoint_values[1] < breakpoint_values[2]:
        raise DesignError("breakpoints must be strictly increasing")

    container = _object(
        tokens["container"], ("max_width", "page_gutter"), "design_tokens.container"
    )
    max_width = _positive_integer(container["max_width"], "design_tokens.container.max_width")
    page_gutter = _positive_integer(
        container["page_gutter"], "design_tokens.container.page_gutter"
    )
    if page_gutter * 2 >= max_width:
        raise DesignError("container page_gutter must be less than half of max_width")

    return {
        "schema_version": SCHEMA_VERSION,
        "color": dict(colors),
        "typography": {
            "font_family": typography["font_family"],
            "heading_scale": heading_scale,
            "body_size": body_size,
            "line_height": line_height,
            "font_weights": list(font_weights),
        },
        "spacing": {"unit": unit, "scale": list(scale)},
        "radius": radius,
        "shadows": shadows,
        "breakpoints": dict(breakpoints),
        "container": dict(container),
    }


def validate_envelope(value: Any, ux_plan: dict[str, Any]) -> dict[str, Any]:
    envelope = _object(value, ENVELOPE_FIELDS, "model response")
    _string(envelope["schema_version"], "schema_version", exact=SCHEMA_VERSION)
    status = _string(envelope["status"], "status")
    if status not in {"completed", "needs_input"}:
        raise DesignError("status must be 'completed' or 'needs_input'")
    questions = _strings(
        envelope["clarification_questions"],
        "clarification_questions",
        allow_empty=status == "completed",
    )
    if status == "completed":
        if questions:
            raise DesignError("completed responses cannot contain clarification questions")
        design_spec = validate_design_spec(envelope["design_spec"], ux_plan)
        tokens = validate_design_tokens(envelope["design_tokens"])
    else:
        if envelope["design_spec"] is not None or envelope["design_tokens"] is not None:
            raise DesignError("needs_input responses must not contain design artifacts")
        design_spec = None
        tokens = None
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "design_spec": design_spec,
        "design_tokens": tokens,
        "clarification_questions": questions,
    }


def _response_json(response: Any, ux_plan: dict[str, Any]) -> dict[str, Any]:
    choices = getattr(response, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise DesignError("design provider returned no JSON content")
    if len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise DesignError("design provider response exceeds 64 KB")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(content, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DesignError("design provider returned malformed JSON") from exc
    return validate_envelope(value, ux_plan)


def _latest_s7_plan(
    job_id: str,
    checkpoints: list[dict[str, Any]],
    data_root: str | Path | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    checkpoint = next(
        (
            item for item in reversed(checkpoints)
            if item.get("stage") == "INTAKE"
            and item.get("metadata", {}).get("schema_version") == SCHEMA_VERSION
            and item.get("metadata", {}).get("status") == "completed"
        ),
        None,
    )
    if checkpoint is None:
        raise DesignError("design requires a completed S7 planning checkpoint")
    references = checkpoint.get("artifact_refs")
    if not isinstance(references, list):
        raise DesignError("S7 planning artifact references are malformed")
    names = [PurePosixPath(ref).name for ref in references if isinstance(ref, str)]
    if len(references) != 2 or set(names) != app_jobs.PLANNING_ARTIFACT_NAMES:
        raise DesignError("S7 planning checkpoint must reference both required artifacts")

    loaded = app_jobs.saved_planning_artifacts(
        {"id": job_id, "latest_checkpoint": checkpoint},
        artifact_root=data_root,
    )
    if loaded is None or set(loaded) != app_jobs.PLANNING_ARTIFACT_NAMES:
        raise DesignError("S7 planning artifacts are missing or incomplete")
    try:
        brief = planning.validate_product_brief(loaded["product_brief.json"])
        ux_plan = planning.validate_ux_plan(loaded["ux_plan.json"])
    except planning.PlanningError as exc:
        raise DesignError("S7 planning artifacts do not match the validated S7 schema") from exc
    return brief, ux_plan, list(references)


def _reject_link_components(path: Path) -> None:
    if ".." in path.parts:
        raise DesignError("artifact paths must not contain parent traversal")
    candidate = path.absolute()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in reversed((candidate, *candidate.parents)):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(details, "st_file_attributes", 0)
        if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
            raise DesignError("artifact paths must not contain symlink or reparse-point components")


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(
        value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise DesignError("design artifact exceeds 64 KB")
    _reject_link_components(path)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _remove_run_directory(run_dir: Path, filenames: tuple[str, ...]) -> None:
    _reject_link_components(run_dir)
    if not run_dir.exists():
        return
    if not run_dir.is_dir():
        raise DesignError("failed Design run path is not a directory")
    resolved_dir = run_dir.resolve(strict=True)
    for filename in filenames:
        child = run_dir / filename
        _reject_link_components(child)
        if child.exists():
            resolved_child = child.resolve(strict=True)
            if resolved_child.parent != resolved_dir or not resolved_child.is_file():
                raise DesignError("failed Design run contains an unexpected artifact path")
            child.unlink()
    run_dir.rmdir()


def _save_artifacts(
    job_id: str,
    design_spec: dict[str, Any],
    design_tokens: dict[str, Any],
    data_root: str | Path | None,
) -> tuple[list[str], str, Path]:
    normalized_job_id = _canonical_job_uuid(job_id)
    configured_data_root = Path(data_root) if data_root is not None else config.DATA_DIR
    try:
        root = planning._safe_base(configured_data_root / "job_artifacts")
    except planning.PlanningError as exc:
        raise DesignError(str(exc)) from exc
    job_dir = root / normalized_job_id
    _reject_link_components(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    _reject_link_components(job_dir)
    if job_dir.resolve().parent != root.resolve():
        raise DesignError("job artifact directory escapes the artifact root")

    run_id = str(uuid.uuid4())
    run_dir = job_dir / run_id
    try:
        run_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise DesignError("Design run ID collision; existing artifacts were preserved") from exc
    _reject_link_components(run_dir)
    if run_dir.resolve().parent != job_dir.resolve():
        raise DesignError("Design run directory escapes the active job directory")

    names = ("design_spec.json", "design_tokens.json")
    values = (design_spec, design_tokens)
    try:
        for name, value in zip(names, values):
            _write_json_exclusive(run_dir / name, value)
    except Exception as exc:
        try:
            _remove_run_directory(run_dir, names)
        except Exception as cleanup_error:
            raise DesignError(
                f"failed to write Design artifacts and clean up the incomplete run: {cleanup_error}"
            ) from exc
        raise

    refs = [
        f"job_artifacts/{normalized_job_id}/{run_id}/{name}"
        for name in names
    ]
    return refs, run_id, run_dir


def _provider_audit(gateway_result: Any) -> dict[str, Any]:
    provider = getattr(gateway_result, "provider", None)
    result: dict[str, Any] = {"attempts": getattr(gateway_result, "attempts", 0)}
    if isinstance(provider, dict):
        for key in ("provider_id", "model_id", "model"):
            value = provider.get(key)
            if isinstance(value, str) and value:
                result[key] = value[:200]
    return result


def design_job(
    job_id: str,
    clarification: str | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    gateway_call_fn: Callable[..., provider_gateway.GatewayResult] | None = None,
    providers: list[dict] | None = None,
) -> dict[str, Any]:
    """Generate and persist a validated design result for a completed S7 plan."""
    normalized_job_id = _canonical_job_uuid(job_id)
    job = orchestrator.get_job(normalized_job_id, db_path)
    if job["mode"] != "create":
        raise DesignError("design is available only for create jobs")
    if job["state"] != "PLANNING":
        raise DesignError("design requires a job in PLANNING")
    if clarification is not None:
        clarification = _string(clarification, "clarification")

    checkpoints = orchestrator.list_checkpoints(normalized_job_id, db_path)
    last_design_checkpoint = next(
        (
            item for item in reversed(checkpoints)
            if item.get("stage") == "PLANNING"
            and item.get("metadata", {}).get("integration") == "s8-design"
        ),
        None,
    )
    if (
        last_design_checkpoint
        and last_design_checkpoint.get("metadata", {}).get("status") == "needs_input"
        and clarification is None
    ):
        raise DesignError("a response to the saved design clarification questions is required")

    brief, ux_plan, source_refs = _latest_s7_plan(
        normalized_job_id, checkpoints, artifact_root
    )
    user_data = {
        "product_brief": brief,
        "ux_plan": ux_plan,
        "user_clarifications": clarification,
    }
    call_fn = gateway_call_fn or gateway_call
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
        providers=providers if providers is not None else config.PROVIDERS,
        response_format={"type": "json_object"},
    )
    if getattr(gateway_result, "response", None) is None:
        raise DesignError("all eligible design providers failed")
    envelope = _response_json(gateway_result.response, ux_plan)
    provider_audit = _provider_audit(gateway_result)

    if envelope["status"] == "needs_input":
        metadata = {
            "integration": "s8-design",
            "schema_version": SCHEMA_VERSION,
            "status": "needs_input",
            "artifact_refs": [],
            "clarification_questions": envelope["clarification_questions"],
            "source_artifact_refs": source_refs,
            "provider": provider_audit,
        }
        orchestrator.record_checkpoint(
            normalized_job_id,
            "PLANNING",
            DESIGN_ACTOR,
            "Design requires user clarification",
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

    refs, run_id, run_dir = _save_artifacts(
        normalized_job_id,
        envelope["design_spec"],
        envelope["design_tokens"],
        artifact_root,
    )
    metadata = {
        "integration": "s8-design",
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
            "DESIGN",
            DESIGN_ACTOR,
            "Validated design specification and tokens saved",
            job["revision"],
            artifact_refs=refs,
            metadata=metadata,
            db_path=db_path,
            checkpoint={
                "stage": "PLANNING",
                "actor": DESIGN_ACTOR,
                "reason": "Save validated Design stage artifacts",
            },
        )
    except Exception as exc:
        try:
            _remove_run_directory(run_dir, ("design_spec.json", "design_tokens.json"))
        except Exception as cleanup_error:
            raise DesignError(
                f"Design transition failed and its new artifacts could not be cleaned up: {cleanup_error}"
            ) from exc
        raise

    return {
        **envelope,
        "artifact_refs": refs,
        "run_id": run_id,
        "job": orchestrator.get_job(normalized_job_id, db_path),
    }
