"""Streamlit-facing helpers for the persisted S6 intake job."""

from __future__ import annotations

import json
import re
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
DESIGN_ARTIFACT_NAMES = frozenset({"design_spec.json", "design_tokens.json"})
BUILD_MANIFEST_NAME = "build_manifest.json"
BUILD_INTEGRATION = "s9-build"
REFINE_INTEGRATION = "s12-refine"
VERIFY_REPORT_NAME = "verify_report.json"
VERIFY_INTEGRATION = "s10-verify"
MAX_BUILD_PAGE_BYTES = 256 * 1024
MAX_BUILD_TOTAL_BYTES = 1024 * 1024
MAX_VERIFY_REPORT_BYTES = 1024 * 1024
_BUILD_FILENAME = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,98}[a-z0-9])?\.html\Z")


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
        if job["state"] in {"INTAKE", "PLANNING", "DESIGN", "BUILD", "VERIFY", "REFINE", "GATE_AND_DELIVER"}
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
    """Resolve a saved UI job in an enabled Create workflow stage."""
    if not _db_path(db_path).exists():
        raise job_store.JobNotFoundError(f"job {job_id!r} was not found")
    job = orchestrator.get_job(job_id, db_path)
    if job["state"] not in {"INTAKE", "PLANNING", "DESIGN", "BUILD", "VERIFY", "REFINE", "GATE_AND_DELIVER", "DELIVERED"}:
        raise job_store.ValidationError(
            f"job {job_id!r} cannot resume from state {job['state']!r}; "
            "expected an enabled Create stage or DELIVERED"
        )
    checkpoints = orchestrator.list_checkpoints(job_id, db_path)
    job["latest_checkpoint"] = checkpoints[-1] if checkpoints else None
    job["checkpoints"] = checkpoints
    return job


def _checkpoint_history(job: dict[str, Any]) -> list[dict[str, Any]]:
    checkpoints = job.get("checkpoints")
    if isinstance(checkpoints, list):
        return [item for item in checkpoints if isinstance(item, dict)]
    latest = job.get("latest_checkpoint")
    return [latest] if isinstance(latest, dict) else []


def _saved_artifacts(
    job: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    allowed_names: frozenset[str],
    label: str,
    artifact_root: str | Path | None,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> dict[str, Any] | None:
    if not checkpoint:
        return None
    job_id = job.get("id")
    try:
        normalized_job_id = str(uuid.UUID(job_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise job_store.ValidationError(f"job id must be a UUID to load {label} artifacts") from exc
    if normalized_job_id != job_id:
        raise job_store.ValidationError("job id must use canonical UUID syntax")

    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    references = checkpoint.get("artifact_refs", [])
    if not isinstance(references, list):
        raise job_store.ValidationError(f"{label} artifact references must be a list")
    if not references:
        return None

    def reject_link_components(path: Path) -> None:
        if ".." in path.parts:
            raise job_store.ValidationError(f"{label} artifact paths cannot contain traversal")
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
                    f"{label} artifact paths cannot contain symlink or reparse-point components"
                )

    reject_link_components(root)
    active_job_root = root / "job_artifacts" / normalized_job_id
    reject_link_components(active_job_root)
    values: dict[str, Any] = {}
    seen_names: set[str] = set()
    artifact_run_id: str | None = None
    for reference in references:
        if not isinstance(reference, str) or "\\" in reference:
            raise job_store.ValidationError(f"{label} artifact reference is malformed")
        path = PurePosixPath(reference)
        if (
            path.is_absolute()
            or str(path) != reference
            or len(path.parts) != 4
            or path.parts[0] != "job_artifacts"
            or path.parts[1] != normalized_job_id
            or path.parts[3] not in allowed_names
        ):
            raise job_store.ValidationError(
                f"{label} artifact reference is outside the active job's allowed structure"
            )
        try:
            run_id = str(uuid.UUID(path.parts[2]))
        except ValueError as exc:
            raise job_store.ValidationError(f"{label} artifact run id must be a UUID") from exc
        if run_id != path.parts[2]:
            raise job_store.ValidationError(
                f"{label} artifact run id must use canonical UUID syntax"
            )
        if artifact_run_id is not None and run_id != artifact_run_id:
            raise job_store.ValidationError(
                f"{label} artifacts must come from the same immutable run"
            )
        artifact_run_id = run_id
        name = path.parts[3]
        if name in seen_names:
            raise job_store.ValidationError(f"duplicate {label} artifact filename")
        seen_names.add(name)
        candidate = root.joinpath(*path.parts)
        reject_link_components(candidate)
        try:
            resolved_root = active_job_root.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
        except ValueError:
            raise job_store.ValidationError(
                f"{label} artifact resolves outside the active job's artifact subtree"
            ) from None
        except FileNotFoundError as exc:
            raise job_store.ValidationError(f"{label} artifact file does not exist") from exc
        if not resolved_candidate.is_file():
            raise job_store.ValidationError(f"{label} artifact must be a regular file")
        if resolved_candidate.stat().st_size > max_bytes:
            raise job_store.ValidationError(f"{label} artifact exceeds its size limit")
        try:
            with resolved_candidate.open("r", encoding="utf-8") as stream:
                values[name] = json.load(stream)
        except json.JSONDecodeError as exc:
            raise job_store.ValidationError(f"{label} artifact contains malformed JSON") from exc
    if not values:
        return None
    return values


def saved_verify_artifacts(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load this job's latest S10 report from its recorded checkpoint only."""
    checkpoint = next(
        (
            item for item in reversed(_checkpoint_history(job))
            if item.get("stage") == "BUILD"
            and item.get("metadata", {}).get("integration") == VERIFY_INTEGRATION
        ),
        None,
    )
    if checkpoint is None:
        return None
    metadata = checkpoint.get("metadata", {})
    references = checkpoint.get("artifact_refs")
    if (
        not isinstance(references, list)
        or len(references) != 1
        or metadata.get("artifact_refs") != references
        or metadata.get("status") not in {"PASS", "FAIL", "NEEDS_INPUT"}
    ):
        raise job_store.ValidationError("Verify checkpoint references are malformed")
    values = _saved_artifacts(
        job,
        checkpoint,
        frozenset({VERIFY_REPORT_NAME}),
        "Verify",
        artifact_root,
        max_bytes=MAX_VERIFY_REPORT_BYTES,
    )
    if values is not None and set(values) != {VERIFY_REPORT_NAME}:
        raise job_store.ValidationError("Verify checkpoint must reference one report")
    return values


def saved_planning_artifacts(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load S7 artifacts only from this job's latest intake checkpoint."""
    checkpoint = next(
        (item for item in reversed(_checkpoint_history(job)) if item.get("stage") == "INTAKE"),
        None,
    )
    return _saved_artifacts(
        job, checkpoint, PLANNING_ARTIFACT_NAMES, "planning", artifact_root
    )


def saved_design_artifacts(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load completed S8 artifacts only from this job's saved Design checkpoint."""
    checkpoint = next(
        (
            item for item in reversed(_checkpoint_history(job))
            if item.get("metadata", {}).get("integration") == "s8-design"
            and item.get("metadata", {}).get("status") == "completed"
        ),
        None,
    )
    values = _saved_artifacts(
        job, checkpoint, DESIGN_ARTIFACT_NAMES, "design", artifact_root
    )
    if values is not None and set(values) != DESIGN_ARTIFACT_NAMES:
        raise job_store.ValidationError("completed Design checkpoint must reference both artifacts")
    return values


def saved_design_clarification_questions(job: dict[str, Any]) -> list[str] | None:
    """Return persisted clarification questions for the active job's Design stage."""
    checkpoint = next(
        (
            item for item in reversed(_checkpoint_history(job))
            if item.get("metadata", {}).get("integration") == "s8-design"
            and item.get("metadata", {}).get("status") == "needs_input"
        ),
        None,
    )
    if checkpoint is None:
        return None
    questions = checkpoint.get("metadata", {}).get("clarification_questions")
    if (
        not isinstance(questions, list)
        or not questions
        or len(questions) > 20
        or any(not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in questions)
    ):
        raise job_store.ValidationError("saved design clarification questions are malformed")
    return list(questions)


def saved_build_clarification_questions(job: dict[str, Any]) -> list[str] | None:
    """Return persisted clarification questions for this job's Build stage."""
    checkpoint = next(
        (
            item for item in reversed(_checkpoint_history(job))
            if item.get("stage") == "DESIGN"
            and item.get("metadata", {}).get("integration") == BUILD_INTEGRATION
            and item.get("metadata", {}).get("status") == "needs_input"
        ),
        None,
    )
    if checkpoint is None:
        return None
    questions = checkpoint.get("metadata", {}).get("clarification_questions")
    if (
        not isinstance(questions, list)
        or not questions
        or len(questions) > 20
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 2000
            for item in questions
        )
    ):
        raise job_store.ValidationError("saved Build clarification questions are malformed")
    return list(questions)


def saved_build_artifacts(
    job: dict[str, Any], artifact_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load a completed Build manifest and pages only from this job's checkpoint."""
    if job.get("mode") != "create" or job.get("state") not in {"BUILD", "VERIFY", "REFINE"}:
        raise job_store.ValidationError("saved Build artifacts require a Create job in BUILD, VERIFY, or REFINE")
    checkpoint = next(
        (
            item for item in reversed(_checkpoint_history(job))
            if item.get("metadata", {}).get("integration") in {BUILD_INTEGRATION, REFINE_INTEGRATION}
            and item.get("metadata", {}).get("status") == "completed"
            and item.get("stage") in {"DESIGN", "REFINE"}
        ),
        None,
    )
    if checkpoint is None:
        return None

    job_id = job.get("id")
    try:
        normalized_job_id = str(uuid.UUID(job_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise job_store.ValidationError("job id must be a UUID to load Build artifacts") from exc
    if normalized_job_id != job_id:
        raise job_store.ValidationError("job id must use canonical UUID syntax")

    references = checkpoint.get("artifact_refs")
    metadata = checkpoint.get("metadata")
    if (
        not isinstance(references, list)
        or not isinstance(metadata, dict)
        or metadata.get("artifact_refs") != references
        or len(references) < 2
        or len(references) > 21
        or any(not isinstance(reference, str) or "\\" in reference for reference in references)
    ):
        raise job_store.ValidationError("Build artifact references are malformed")

    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    active_job_root = root / "job_artifacts" / normalized_job_id
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reject_link_components(path: Path) -> None:
        if ".." in path.parts:
            raise job_store.ValidationError("Build artifact paths cannot contain traversal")
        candidate = path.absolute()
        for component in reversed((candidate, *candidate.parents)):
            try:
                details = component.lstat()
            except FileNotFoundError:
                continue
            attributes = getattr(details, "st_file_attributes", 0)
            if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
                raise job_store.ValidationError(
                    "Build artifact paths cannot contain symlink or reparse-point components"
                )

    reject_link_components(root)
    reject_link_components(active_job_root)
    parsed_refs: dict[str, tuple[PurePosixPath, Path]] = {}
    run_id: str | None = None
    manifest_path: Path | None = None
    for reference in references:
        path = PurePosixPath(reference)
        if path.is_absolute() or str(path) != reference:
            raise job_store.ValidationError("Build artifact reference is not canonical")
        if (
            len(path.parts) not in {4, 5}
            or path.parts[0] != "job_artifacts"
            or path.parts[1] != normalized_job_id
        ):
            raise job_store.ValidationError("Build artifact is outside the active job structure")
        try:
            reference_run_id = str(uuid.UUID(path.parts[2]))
        except ValueError as exc:
            raise job_store.ValidationError("Build artifact run id must be a UUID") from exc
        if reference_run_id != path.parts[2]:
            raise job_store.ValidationError("Build artifact run id must be canonical")
        if run_id is not None and run_id != reference_run_id:
            raise job_store.ValidationError("Build artifacts must share one immutable run")
        run_id = reference_run_id
        if len(path.parts) == 4 and path.parts[3] == BUILD_MANIFEST_NAME:
            key = BUILD_MANIFEST_NAME
            if manifest_path is not None:
                raise job_store.ValidationError("Build checkpoint contains duplicate manifests")
            manifest_path = root.joinpath(*path.parts)
        elif (
            len(path.parts) == 5
            and path.parts[3] == "build"
            and _BUILD_FILENAME.fullmatch(path.parts[4])
        ):
            key = path.parts[4]
        else:
            raise job_store.ValidationError("Build artifact reference has an unsupported path")
        if key in parsed_refs:
            raise job_store.ValidationError("Build artifact filenames must be unique")
        candidate = root.joinpath(*path.parts)
        reject_link_components(candidate)
        try:
            resolved_root = active_job_root.resolve(strict=True)
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(resolved_root)
        except ValueError:
            raise job_store.ValidationError(
                "Build artifact resolves outside the active job's artifact subtree"
            ) from None
        except FileNotFoundError as exc:
            raise job_store.ValidationError("Build artifact file does not exist") from exc
        if not resolved_candidate.is_file():
            raise job_store.ValidationError("Build artifact must be a regular file")
        parsed_refs[key] = (path, resolved_candidate)

    if manifest_path is None:
        raise job_store.ValidationError("Build checkpoint must reference a manifest")
    manifest_file = parsed_refs[BUILD_MANIFEST_NAME][1]
    try:
        manifest_size = manifest_file.stat().st_size
        if manifest_size > 64 * 1024:
            raise job_store.ValidationError("Build manifest exceeds 64 KB")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise job_store.ValidationError("Build manifest is missing or malformed") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "run_id", "pages", "source_artifact_refs"}
        or manifest.get("schema_version") != "1.0"
        or manifest.get("run_id") != run_id
    ):
        raise job_store.ValidationError("Build manifest has an invalid schema or run id")

    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages or len(pages) > 20:
        raise job_store.ValidationError("Build manifest pages are malformed")
    filenames: set[str] = set()
    page_names: set[str] = set()
    html_values: dict[str, str] = {}
    total_bytes = manifest_size
    for page in pages:
        if (
            not isinstance(page, dict)
            or set(page) != {"page", "filename"}
            or not isinstance(page["page"], str)
            or not page["page"].strip()
            or len(page["page"]) > 2000
            or not isinstance(page["filename"], str)
            or not _BUILD_FILENAME.fullmatch(page["filename"])
            or page["filename"] in filenames
            or page["page"] in page_names
        ):
            raise job_store.ValidationError("Build manifest page entries are malformed")
        filename = page["filename"]
        if filename not in parsed_refs:
            raise job_store.ValidationError("Build manifest page is not referenced by checkpoint")
        html_path = parsed_refs[filename][1]
        size = html_path.stat().st_size
        total_bytes += size
        if size > MAX_BUILD_PAGE_BYTES:
            raise job_store.ValidationError("Build HTML page exceeds 256 KB")
        if total_bytes > MAX_BUILD_TOTAL_BYTES:
            raise job_store.ValidationError("Build artifacts exceed 1 MB in aggregate")
        try:
            html_values[filename] = html_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise job_store.ValidationError("Build HTML page is not valid UTF-8") from exc
        filenames.add(filename)
        page_names.add(page["page"])
    if set(parsed_refs) != {BUILD_MANIFEST_NAME, *filenames}:
        raise job_store.ValidationError("Build checkpoint contains unlisted page artifacts")
    source_refs = manifest.get("source_artifact_refs")
    if (
        not isinstance(source_refs, list)
        or not source_refs
        or any(not isinstance(item, str) or not item.strip() for item in source_refs)
        or metadata.get("source_artifact_refs") != source_refs
    ):
        raise job_store.ValidationError("Build manifest source references are malformed")
    return {BUILD_MANIFEST_NAME: manifest, **html_values}
