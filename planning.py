"""Create-mode product intake and UX planning stage."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

import config
import job_store
import orchestrator
import provider_gateway

gateway_call = provider_gateway.call

SCHEMA_VERSION = "1.0"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_STRING_CHARS = 2000
MAX_ARRAY_ITEMS = 20
MAX_CONTEXT_CHARS = 12000
PLANNING_ACTOR = "ux-planner"
_PROMPT = (config.PROMPTS_DIR / "planning_v1.md").read_text(encoding="utf-8")

PRODUCT_FIELDS = (
    "schema_version", "product_name", "problem_statement", "target_users",
    "user_goals", "business_goals", "core_features", "primary_user_flows",
    "constraints", "assumptions", "open_questions", "success_metrics",
    "acceptance_criteria", "target_platform", "output_target",
)
UX_FIELDS = (
    "schema_version", "summary", "information_architecture",
    "primary_journeys", "interaction_inventory", "content_requirements",
    "accessibility_requirements", "responsive_requirements", "risks",
    "decisions",
)
ENVELOPE_FIELDS = (
    "schema_version", "status", "product_brief", "ux_plan",
    "clarification_questions",
)


class PlanningError(job_store.ValidationError):
    """Raised when planning cannot produce a persisted valid result."""


def _object(value: Any, fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanningError(f"{name} must be an object")
    if set(value) != set(fields):
        missing = sorted(set(fields) - set(value))
        extra = sorted(set(value) - set(fields))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        raise PlanningError(f"{name} has invalid fields ({'; '.join(detail)})")
    return value


def _string(value: Any, name: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningError(f"{name} must be a non-empty string")
    if len(value) > MAX_STRING_CHARS:
        raise PlanningError(f"{name} exceeds {MAX_STRING_CHARS} characters")
    if exact is not None and value != exact:
        raise PlanningError(f"{name} must be {exact!r}")
    return value


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ARRAY_ITEMS:
        raise PlanningError(f"{name} must be a list of at most {MAX_ARRAY_ITEMS} items")
    if not value and not allow_empty:
        raise PlanningError(f"{name} must not be empty")
    return [_string(item, f"{name}[{index}]") for index, item in enumerate(value)]


def validate_product_brief(value: Any) -> dict[str, Any]:
    brief = _object(value, PRODUCT_FIELDS, "product_brief")
    _string(brief["schema_version"], "product_brief.schema_version", exact=SCHEMA_VERSION)
    for field in PRODUCT_FIELDS[1:]:
        if field in {"target_users", "user_goals", "business_goals", "core_features",
                     "primary_user_flows", "constraints", "assumptions", "open_questions",
                     "success_metrics", "acceptance_criteria"}:
            _strings(brief[field], f"product_brief.{field}")
        else:
            _string(brief[field], f"product_brief.{field}")
    _string(brief["output_target"], "product_brief.output_target", exact="HTML + Tailwind")
    return dict(brief)


def _objects(value: Any, name: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_ARRAY_ITEMS:
        raise PlanningError(f"{name} must be a non-empty list of at most {MAX_ARRAY_ITEMS} items")
    result = []
    for index, item in enumerate(value):
        obj = _object(item, fields, f"{name}[{index}]")
        for field in fields:
            if field == "sections" or field == "steps" or field == "states":
                _strings(obj[field], f"{name}[{index}].{field}")
            else:
                _string(obj[field], f"{name}[{index}].{field}")
        result.append(dict(obj))
    return result


def validate_ux_plan(value: Any) -> dict[str, Any]:
    plan = _object(value, UX_FIELDS, "ux_plan")
    _string(plan["schema_version"], "ux_plan.schema_version", exact=SCHEMA_VERSION)
    _string(plan["summary"], "ux_plan.summary")
    _objects(plan["information_architecture"], "information_architecture",
             ("page", "purpose", "sections"))
    _objects(plan["primary_journeys"], "primary_journeys",
             ("name", "steps", "success_outcome"))
    _objects(plan["interaction_inventory"], "interaction_inventory",
             ("element", "behavior", "states"))
    for field in UX_FIELDS[5:]:
        _strings(plan[field], f"ux_plan.{field}")
    return dict(plan)


def validate_envelope(value: Any) -> dict[str, Any]:
    envelope = _object(value, ENVELOPE_FIELDS, "model response")
    _string(envelope["schema_version"], "schema_version", exact=SCHEMA_VERSION)
    status = _string(envelope["status"], "status")
    if status not in {"completed", "needs_input"}:
        raise PlanningError("status must be 'completed' or 'needs_input'")
    brief = validate_product_brief(envelope["product_brief"])
    questions = _strings(
        envelope["clarification_questions"],
        "clarification_questions",
        allow_empty=status == "completed",
    )
    if status == "completed":
        if questions:
            raise PlanningError("completed responses cannot contain clarification questions")
        plan = validate_ux_plan(envelope["ux_plan"])
    else:
        if not questions:
            raise PlanningError("needs_input requires clarification questions")
        if envelope["ux_plan"] is not None:
            raise PlanningError("needs_input responses must not contain a UX plan")
        plan = None
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "product_brief": brief,
        "ux_plan": plan,
        "clarification_questions": questions,
    }


def _response_json(response: Any) -> dict[str, Any]:
    content = getattr(getattr(response, "choices", [None])[0], "message", None)
    content = getattr(content, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise PlanningError("planning provider returned no JSON content")
    if len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise PlanningError("planning provider response exceeds 64 KB")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise PlanningError("planning provider returned malformed JSON") from exc
    return validate_envelope(value)


def _user_context(messages: list[dict[str, Any]], job_id: str) -> str:
    parts = []
    for message in messages:
        if message.get("role") != "user" or message.get("job_id") != job_id:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    context = "\n\n--- FOLLOW-UP USER MESSAGE ---\n\n".join(parts)
    if not context:
        raise PlanningError("at least one user brief message is required")
    if len(context) > MAX_CONTEXT_CHARS:
        raise PlanningError("job brief context exceeds 12,000 characters")
    return context


def _reject_link_components(path: Path) -> None:
    if ".." in path.parts:
        raise PlanningError("artifact paths must not contain parent traversal")
    candidate = path.absolute()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in reversed((candidate, *candidate.parents)):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(details, "st_file_attributes", 0)
        if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
            raise PlanningError("artifact paths must not contain symlink or reparse-point components")


def _safe_base(artifact_root: str | Path | None) -> Path:
    if artifact_root is None:
        requested_root = config.DATA_DIR / "job_artifacts"
        _reject_link_components(config.DATA_DIR)
        _reject_link_components(requested_root)
        data_root = config.DATA_DIR.resolve()
        root = requested_root.resolve()
        if root != data_root / "job_artifacts":
            raise PlanningError("default artifact root is outside configured data directory")
    else:
        requested_root = Path(artifact_root)
        _reject_link_components(requested_root)
        root = requested_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    _reject_link_components(requested_root)
    if requested_root.resolve() != root:
        raise PlanningError("artifact root changed while being prepared")
    return root


def _job_uuid(job_id: str) -> str:
    try:
        parsed = uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise PlanningError("job_id must be a UUID") from exc
    if str(parsed) != job_id.lower():
        raise PlanningError("job_id must use canonical UUID syntax")
    return str(parsed)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise PlanningError("artifact exceeds 64 KB")
    _reject_link_components(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(path)
    if path.exists():
        raise PlanningError("artifact files are immutable and must not be overwritten")
    fd, temporary = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_artifacts(
    job_id: str, envelope: dict[str, Any], artifact_root: str | Path | None
) -> tuple[list[str], str]:
    root = _safe_base(artifact_root)
    normalized_job_id = _job_uuid(job_id)
    job_dir = root / normalized_job_id
    _reject_link_components(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    _reject_link_components(job_dir)
    if not job_dir.resolve().is_relative_to(root.resolve()):
        raise PlanningError("job artifact directory escapes the artifact root")
    run_id = str(uuid.uuid4())
    run_dir = job_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _reject_link_components(run_dir)
    if run_dir.resolve().parent != job_dir.resolve():
        raise PlanningError("run artifact directory escapes the job directory")
    _write_json(run_dir / "product_brief.json", envelope["product_brief"])
    refs = [f"job_artifacts/{normalized_job_id}/{run_id}/product_brief.json"]
    if envelope["ux_plan"] is not None:
        _write_json(run_dir / "ux_plan.json", envelope["ux_plan"])
        refs.append(f"job_artifacts/{normalized_job_id}/{run_id}/ux_plan.json")
    return refs, run_id


def plan_job(
    job_id: str,
    messages: list[dict[str, Any]],
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    gateway_call_fn: Callable[..., provider_gateway.GatewayResult] | None = None,
    providers: list[dict] | None = None,
) -> dict[str, Any]:
    """Run the bounded Create intake stage and persist its validated artifacts."""
    normalized_job_id = _job_uuid(job_id)
    job = orchestrator.get_job(job_id, db_path)
    if job["mode"] != "create":
        raise PlanningError("planning is available only for create jobs")
    if job["state"] != "INTAKE":
        raise PlanningError("planning requires a job in INTAKE")
    context = _user_context(messages, job_id)
    safe_artifact_root = _safe_base(artifact_root)
    call_fn = gateway_call_fn or gateway_call
    gateway_result = call_fn(
        [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": context},
        ],
        [],
        {"text", "json_output"},
        providers=providers if providers is not None else config.PROVIDERS,
        response_format={"type": "json_object"},
    )
    if gateway_result.response is None:
        raise PlanningError("all eligible planning providers failed")
    envelope = _response_json(gateway_result.response)
    refs, run_id = _save_artifacts(normalized_job_id, envelope, safe_artifact_root)
    metadata = {"schema_version": SCHEMA_VERSION, "status": envelope["status"]}
    orchestrator.record_checkpoint(
        job_id, "INTAKE", PLANNING_ACTOR, "Saved validated intake artifacts",
        refs, metadata, expected_revision=job["revision"], db_path=db_path,
    )
    if envelope["status"] == "completed":
        orchestrator.transition_job(
            job_id, "PLANNING", PLANNING_ACTOR,
            "Validated product brief and UX plan artifacts saved",
            job["revision"], artifact_refs=refs, metadata=metadata,
            db_path=db_path,
        )
        persisted = orchestrator.get_job(job_id, db_path)
    else:
        persisted = orchestrator.get_job(job_id, db_path)
    return {
        "status": envelope["status"],
        "product_brief": envelope["product_brief"],
        "ux_plan": envelope["ux_plan"],
        "clarification_questions": envelope["clarification_questions"],
        "artifact_refs": refs,
        "run_id": run_id,
        "job": persisted,
    }
