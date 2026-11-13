"""Streamlit-facing helpers for the persisted S6 intake job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config
import job_store
import orchestrator

APP_ACTOR = "streamlit-ui"
INTAKE_METADATA = {"integration": "s6", "source": "streamlit"}


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
    """Return only jobs that the S6 UI can safely resume."""
    if not _db_path(db_path).exists():
        return []
    return [
        job
        for job in orchestrator.list_recoverable_jobs(db_path)
        if job["state"] == "INTAKE"
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
