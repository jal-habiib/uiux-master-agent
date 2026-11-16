"""Streamlit-facing helpers for the persisted S6 intake job."""

from __future__ import annotations

import json
import stat
import uuid
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import config
import job_store
import orchestrator

APP_ACTOR = "streamlit-ui"
INTAKE_METADATA = {"integration": "s6", "source": "streamlit"}
MAX_ARTIFACT_BYTES = 64 * 1024
PLANNING_ARTIFACT_NAMES = frozenset({"product_brief.json", "ux_plan.json"})


def messages_for_job(
    messages: list[dict[str, Any]], job_id: str | None
) -> list[dict[str, Any]]:
    """Return messages belonging to a job without changing the source list."""
    if job_id is None:
        return [message for message in messages if message.get("job_id") is None]
    return [message for message in messages if message.get("job_id") == job_id]


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path is not None else config.DATA_DIR / "jobs.sqlite3"


def start_intake_job(
    mode: str, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Create a job and persist its initial CREATED -> INTAKE transition."""
    job = orchestrator.create_job(
        mode,
        APP_ACTOR,
        f"Start {mode} job from Streamlit UI",
        db_path,
    )
    orchestrator.transition_job(
        job["id"],
        "INTAKE",
        APP_ACTOR,
        "Initialize intake stage from Streamlit UI",
        job["revision"],
        metadata=INTAKE_METADATA,
        db_path=db_path,
    )
    return orchestrator.get_job(job["id"], db_path)


def list_recoverable_intake_jobs(
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return legacy intake listings, now including saved S7 planning jobs."""
    if not _db_path(db_path).exists():
        return []
    return [
        job
        for job in orchestrator.list_recoverable_jobs(db_path)
        if job["state"] in {"INTAKE", "PLANNING"}
    ]


def list_recoverable_ui_jobs(
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return Create jobs the UI can resume before downstream work is wired."""
    if not _db_path(db_path).exists():
        return []
    return [
        job
        for job in orchestrator.list_recoverable_jobs(db_path)
        if job["state"] in {"INTAKE", "PLANNING"}
    ]


def resume_intake_job(
    job_id: str, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Resolve a selected job, rejecting missing or non-INTAKE jobs."""
    if not _db_path(db_path).exists():
        raise job_store.JobNotFoundError(f"job {job_id!r} was not found")
    job = orchestrator.get_job(job_id, db_path)
    if job["state"] != "INTAKE":
        raise job_store.ValidationError(
            f"job {job_id!r} cannot resume from state {job['state']!r}; expected 'INTAKE'"
        )
    return job


def resume_ui_job(
    job_id: str, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Resolve a saved UI job in either the S7 intake or planning stage."""
    if not _db_path(db_path).exists():
        raise job_store.JobNotFoundError(f"job {job_id!r} was not found")
    job = orchestrator.get_job(job_id, db_path)
    if job["state"] not in {"INTAKE", "PLANNING"}:
        raise job_store.ValidationError(
            f"job {job_id!r} cannot resume from state {job['state']!r}; "
            "expected 'INTAKE' or 'PLANNING'"
        )
    checkpoints = orchestrator.list_checkpoints(job_id, db_path)
    job["latest_checkpoint"] = checkpoints[-1] if checkpoints else None
    return job


def saved_planning_artifacts(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load S7 artifacts only from the active job's immutable artifact subtree."""
    checkpoint = job.get("latest_checkpoint")
    if not checkpoint or checkpoint.get("stage") != "INTAKE":
        return None
    job_id = job.get("id")
    try:
        normalized_job_id = str(uuid.UUID(job_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise job_store.ValidationError("job id must be a UUID to load planning artifacts") from exc
    if normalized_job_id != job_id:
        raise job_store.ValidationError("job id must use canonical UUID syntax")

    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    references = checkpoint.get("artifact_refs", [])
    if not isinstance(references, list):
        raise job_store.ValidationError("planning artifact references must be a list")
    if not references:
        return None

    def reject_link_components(path: Path) -> None:
        if ".." in path.parts:
            raise job_store.ValidationError("planning artifact paths cannot contain traversal")
        candidate = path.absolute()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        for component in reversed((candidate, *candidate.parents)):
            try:
                details = component.lstat()
            except FileNotFoundError:
                continue
            attributes = getattr(details, "st_file_attributes", 0)
            if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
                raise job_store.ValidationError(
                    "planning artifact paths cannot contain symlink or reparse-point components"
                )

    reject_link_components(root)
    active_job_root = root / "job_artifacts" / normalized_job_id
    reject_link_components(active_job_root)
    values: dict[str, Any] = {}
    seen_names: set[str] = set()
    artifact_run_id: str | None = None
    for reference in references:
        if not isinstance(reference, str) or "\\" in reference:
            raise job_store.ValidationError("planning artifact reference is malformed")
        path = PurePosixPath(reference)
        if (
            path.is_absolute()
            or str(path) != reference
            or len(path.parts) != 4
            or path.parts[0] != "job_artifacts"
            or path.parts[1] != normalized_job_id
            or path.parts[3] not in PLANNING_ARTIFACT_NAMES
        ):
            raise job_store.ValidationError(
                "planning artifact reference is outside the active job's allowed structure"
            )
        try:
            run_id = str(uuid.UUID(path.parts[2]))
        except ValueError as exc:
            raise job_store.ValidationError("planning artifact run id must be a UUID") from exc
        if run_id != path.parts[2]:
            raise job_store.ValidationError("planning artifact run id must use canonical UUID syntax")
        if artifact_run_id is not None and run_id != artifact_run_id:
            raise job_store.ValidationError("planning artifacts must come from the same immutable run")
        artifact_run_id = run_id
        name = path.parts[3]
        if name in seen_names:
            raise job_store.ValidationError("duplicate planning artifact filename")
        seen_names.add(name)
        candidate = root.joinpath(*path.parts)
        reject_link_components(candidate)
        try:
            resolved_root = active_job_root.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
        except ValueError:
            raise job_store.ValidationError(
                "planning artifact resolves outside the active job's artifact subtree"
            ) from None
        except FileNotFoundError as exc:
            raise job_store.ValidationError("planning artifact file does not exist") from exc
        if not resolved_candidate.is_file():
            raise job_store.ValidationError("planning artifact must be a regular file")
        if resolved_candidate.stat().st_size > MAX_ARTIFACT_BYTES:
            raise job_store.ValidationError("planning artifact exceeds 64 KB")
        try:
            with resolved_candidate.open("r", encoding="utf-8") as stream:
                values[name] = json.load(stream)
        except json.JSONDecodeError as exc:
            raise job_store.ValidationError("planning artifact contains malformed JSON") from exc
    if not values:
        return None
    return values
