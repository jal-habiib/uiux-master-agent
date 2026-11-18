"""S12 bounded, evidence-driven HTML refinement."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import app_jobs
import build
import config
import job_store
import orchestrator
import provider_gateway
import verify

REFINE_ACTOR = "s12-refine"
REFINE_INTEGRATION = app_jobs.REFINE_INTEGRATION
_PROMPT = Path(__file__).parent.joinpath("prompts", "refine_v1.md").read_text(encoding="utf-8")


class RefineError(job_store.ValidationError):
    pass


def _latest_verify(job: dict[str, Any], db_path=None, artifact_root=None) -> dict[str, Any]:
    checkpoints = orchestrator.list_checkpoints(job["id"], db_path)
    checkpoint = next((item for item in reversed(checkpoints)
        if item.get("metadata", {}).get("integration") == verify.VERIFY_INTEGRATION), None)
    if checkpoint is None or len(checkpoint.get("artifact_refs", [])) != 1:
        raise RefineError("Refine requires a saved Verify report")
    saved_job = {**job, "checkpoints": checkpoints}
    saved = verify.load_saved_verification(saved_job, artifact_root)
    if not saved:
        raise RefineError("Refine requires a valid saved Verify report")
    if saved["status"] != "FAIL":
        raise RefineError("Only a failed Verify report can trigger automated refinement")
    return {"report": saved, "checkpoint": checkpoint, "checkpoints": checkpoints}


def refine_job(
    job_id: str,
    user_feedback: str | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    gateway_call_fn: Callable[..., Any] | None = None,
    providers: list[dict] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Use saved Verify failures to produce a new immutable Build run."""
    try:
        job = orchestrator.get_job(job_id, db_path)
    except job_store.ValidationError as exc:
        raise RefineError(str(exc)) from exc
    if job.get("mode") != "create" or job.get("state") not in {"VERIFY", "REFINE"}:
        raise RefineError("Refine requires a Create job in VERIFY or REFINE")
    if user_feedback is not None:
        if not isinstance(user_feedback, str) or len(user_feedback) > build.MAX_STRING_CHARS:
            raise RefineError("Refinement feedback must be a bounded text value")
        user_feedback = user_feedback.strip() or None

    saved_verify = _latest_verify(job, db_path, artifact_root)
    report = saved_verify["report"]
    if not report.get("hard_gates") or not report.get("pages"):
        raise RefineError("Verify report does not contain actionable page and gate evidence")
    if job["state"] == "VERIFY":
        orchestrator.transition_job(
            job_id, "REFINE", REFINE_ACTOR,
            "Begin bounded automated refinement from saved Verify findings",
            job["revision"],
            metadata={"integration": REFINE_INTEGRATION, "source_verify_run_id": saved_verify["checkpoint"]["metadata"].get("run_id")},
            db_path=db_path,
        )
        job = orchestrator.get_job(job_id, db_path)

    checkpoints = orchestrator.list_checkpoints(job_id, db_path)
    current = app_jobs.saved_build_artifacts({**job, "checkpoints": checkpoints}, artifact_root)
    if not isinstance(current, dict):
        raise RefineError("The latest immutable Build artifacts are unavailable")
    manifest = current[app_jobs.BUILD_MANIFEST_NAME]
    source = build._source_artifacts(job_id, checkpoints, artifact_root)
    page_specs = source["design_spec"]["page_specs"]
    pages = []
    for item in manifest["pages"]:
        pages.append({"page": item["page"], "filename": item["filename"], "html": current[item["filename"]]})

    payload = {
        "product_brief": source["product_brief"],
        "ux_plan": source["ux_plan"],
        "design_spec": source["design_spec"],
        "design_tokens": source["design_tokens"],
        "previous_pages": pages,
        "verify_report": report,
        "user_feedback": user_feedback,
        "constraints": {
            "preserve_page_names_and_filenames": True,
            "fix_only_evidenced_issues_without_regressing_passed_checks": True,
            "no_external_assets_or_scripts": True,
            "return_complete_pages_not_diffs": True,
        },
    }
    call_fn = gateway_call_fn or gateway_call
    options: dict[str, Any] = {
        "providers": providers if providers is not None else config.PROVIDERS,
        "response_format": {"type": "json_object"},
    }
    if status_callback is not None:
        options["status_callback"] = status_callback
    result = call_fn(
        [{"role": "system", "content": _PROMPT},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=True, separators=(",", ":"))}],
        [], {"text", "json_output"}, **options,
    )
    if getattr(result, "response", None) is None:
        raise RefineError("all eligible Refine providers failed; job remains recoverable in REFINE")
    envelope = build._response_json(result.response, page_specs)
    if envelope["status"] != "completed":
        raise RefineError("Refine requires clarification; no replacement pages were saved")
    old_names = [(item["page"], item["filename"]) for item in manifest["pages"]]
    new_names = [(item["page"], item["filename"]) for item in envelope["pages"]]
    if old_names != new_names:
        raise RefineError("Refine must preserve the exact page names and filenames")

    refs, run_id, run_dir, new_manifest = build._save_artifacts(
        job_id, envelope["pages"], source["source_artifact_refs"], artifact_root,
    )
    provider_audit = build._provider_audit(result)
    metadata = {
        "integration": REFINE_INTEGRATION,
        "schema_version": build.SCHEMA_VERSION,
        "status": "completed",
        "run_id": run_id,
        "artifact_refs": refs,
        "source_artifact_refs": source["source_artifact_refs"],
        "parent_build_run_id": manifest["run_id"],
        "source_verify_run_id": saved_verify["checkpoint"]["metadata"].get("run_id"),
        "provider": provider_audit,
    }
    try:
        transitioned = orchestrator.transition_job(
            job_id, "VERIFY", REFINE_ACTOR,
            "Save refined HTML as a new immutable run and return to Verify",
            job["revision"], artifact_refs=refs, metadata=metadata,
            db_path=db_path,
            checkpoint={"stage": "REFINE", "actor": REFINE_ACTOR,
                        "reason": "Persist evidence-driven refined Build artifacts"},
        )
    except Exception as exc:
        try:
            build._remove_run_directory(run_dir, [page["filename"] for page in envelope["pages"]], manifest_created=True)
        except Exception as cleanup_error:
            raise RefineError(f"Refine persistence failed and run cleanup failed: {cleanup_error}") from exc
        raise
    return {"status": "completed", "run_id": run_id, "manifest": new_manifest,
            "artifact_refs": refs, "job": orchestrator.get_job(job_id, db_path)}


def gateway_call(messages, images, capabilities, **kwargs):
    return provider_gateway.call(messages, images, capabilities, **kwargs)
