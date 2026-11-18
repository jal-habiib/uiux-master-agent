"""Policy and public API for the durable S5 orchestration state machine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import job_store

MODES = frozenset({"create", "review"})
STATES = (
    "CREATED",
    "INTAKE",
    "PLANNING",
    "DESIGN",
    "BUILD",
    "VERIFY",
    "REFINE",
    "GATE_AND_DELIVER",
    "WAITING_FOR_USER",
    "BLOCKED",
    "FAILED",
    "DELIVERED",
    "CANCELLED",
)
CONTROL_STATES = frozenset({"WAITING_FOR_USER", "BLOCKED", "FAILED"})
TERMINAL_STATES = frozenset({"DELIVERED", "CANCELLED"})
CREATE_EDGES = {
    "CREATED": {"INTAKE"},
    "INTAKE": {"PLANNING"},
    "PLANNING": {"DESIGN"},
    "DESIGN": {"BUILD"},
    "BUILD": {"VERIFY"},
    "VERIFY": {"REFINE", "GATE_AND_DELIVER"},
    "REFINE": {"VERIFY"},
    "GATE_AND_DELIVER": {"DELIVERED"},
}
REVIEW_EDGES = {
    "CREATED": {"INTAKE"},
    "INTAKE": {"VERIFY"},
    "VERIFY": {"REFINE", "GATE_AND_DELIVER"},
    "REFINE": {"VERIFY"},
    "GATE_AND_DELIVER": {"DELIVERED"},
}


def _mode(mode: str) -> str:
    if mode not in MODES:
        raise job_store.ValidationError(f"unsupported mode: {mode!r}")
    return mode


def initialize_store(db_path: str | Path | None = None) -> Path:
    return job_store.initialize_store(db_path)


def create_job(mode: str, actor: str, reason: str, db_path: str | Path | None = None) -> dict[str, Any]:
    return job_store.create_job(_mode(mode), actor, reason, db_path)


def get_job(job_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    return job_store.get_job(job_id, db_path)


def _validate_target(job: dict[str, Any], to_state: str, metadata: dict[str, Any]) -> str | None:
    if to_state not in STATES:
        raise job_store.ValidationError(f"unsupported state: {to_state!r}")
    current = job["state"]
    if current in TERMINAL_STATES:
        raise job_store.ValidationError(f"terminal job cannot transition from {current}")
    if to_state == "CANCELLED":
        return None
    if to_state == "DELIVERED":
        passed = metadata.get("gate_status") == "PASS"
        not_passed_after_limit = (
            metadata.get("gate_status") == "NOT_PASSED_AFTER_3_CYCLES"
            and metadata.get("status") == "NOT_PASSED"
            and isinstance(metadata.get("fix_cycles"), int)
            and not isinstance(metadata.get("fix_cycles"), bool)
            and metadata["fix_cycles"] >= 3
        )
        if current != "GATE_AND_DELIVER" or not (passed or not_passed_after_limit):
            raise job_store.ValidationError(
                "DELIVERED requires GATE_AND_DELIVER and a passing gate or an explicit not-passed report after three fix cycles"
            )
    if current in CONTROL_STATES:
        if to_state != job["resume_state"]:
            raise job_store.ValidationError(
                f"{current} may resume only to its persisted stage {job['resume_state']!r}"
            )
        return None
    if to_state in CONTROL_STATES:
        if current == "CREATED":
            raise job_store.ValidationError("CREATED cannot enter a control state")
        return current
    if current == "CREATED" and to_state != "INTAKE":
        raise job_store.ValidationError("CREATED may transition only to INTAKE or CANCELLED")
    edges = CREATE_EDGES if job["mode"] == "create" else REVIEW_EDGES
    if to_state not in edges.get(current, set()):
        raise job_store.ValidationError(f"invalid transition {current} -> {to_state}")
    return None


def _matches_idempotent_request(
    prior: dict[str, Any],
    expected_revision: int,
    to_state: str,
    actor: str,
    reason: str,
    artifact_refs: list[str],
    metadata: dict[str, Any],
) -> bool:
    return (
        prior["from_revision"] == expected_revision
        and prior["to_state"] == to_state
        and prior["actor"] == actor
        and prior["reason"] == reason
        and prior["artifact_refs"] == artifact_refs
        and prior["metadata"] == metadata
    )


def transition_job(
    job_id: str,
    to_state: str,
    actor: str,
    reason: str,
    expected_revision: int,
    artifact_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    db_path: str | Path | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise job_store.ValidationError("expected_revision must be a non-negative integer")
    artifacts = job_store.validate_artifact_refs(artifact_refs)
    metadata_value = job_store.validate_metadata(metadata)
    checkpoint_value = None
    if checkpoint is not None:
        if not isinstance(checkpoint, dict) or set(checkpoint) != {
            "stage", "actor", "reason"
        }:
            raise job_store.ValidationError(
                "checkpoint must contain stage, actor, and reason"
            )
        for field in ("stage", "actor", "reason"):
            if not isinstance(checkpoint[field], str) or not checkpoint[field].strip():
                raise job_store.ValidationError(
                    f"checkpoint.{field} must be a non-empty string"
                )
        checkpoint_value = {
            "stage": checkpoint["stage"],
            "actor": checkpoint["actor"],
            "reason": checkpoint["reason"],
            "artifact_refs": artifacts,
            "metadata": metadata_value,
        }
    if idempotency_key is not None:
        prior = job_store.find_idempotent_transition(job_id, idempotency_key, db_path)
        if prior is not None:
            if not _matches_idempotent_request(
                prior, expected_revision, to_state, actor, reason, artifacts, metadata_value
            ):
                raise job_store.ValidationError("idempotency_key conflicts with the original request")
            return prior
    job = get_job(job_id, db_path)
    if job["revision"] != expected_revision:
        if idempotency_key is not None:
            prior = job_store.find_idempotent_transition(job_id, idempotency_key, db_path)
            if prior is not None:
                if not _matches_idempotent_request(
                    prior, expected_revision, to_state, actor, reason, artifacts, metadata_value
                ):
                    raise job_store.ValidationError(
                        "idempotency_key conflicts with the original request"
                    )
                return prior
        raise job_store.ValidationError(
            f"stale revision: expected {expected_revision}, current {job['revision']}"
        )
    if checkpoint_value is not None:
        expected_stage = (
            job["resume_state"] if job["state"] in CONTROL_STATES else job["state"]
        )
        if checkpoint_value["stage"] != expected_stage:
            raise job_store.ValidationError(
                f"checkpoint stage must match the current stage {expected_stage!r}"
            )
    resume_state = _validate_target(job, to_state, metadata_value)
    return job_store.apply_transition(
        job_id,
        job["state"],
        to_state,
        actor,
        reason,
        artifacts,
        metadata_value,
        expected_revision,
        resume_state,
        idempotency_key,
        db_path,
        checkpoint_value,
    )


def cancel_job(
    job_id: str,
    actor: str,
    reason: str,
    expected_revision: int,
    idempotency_key: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    return transition_job(
        job_id,
        "CANCELLED",
        actor,
        reason,
        expected_revision,
        idempotency_key=idempotency_key,
        db_path=db_path,
    )


def record_checkpoint(
    job_id: str,
    stage: str,
    actor: str,
    reason: str,
    artifact_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    if stage not in STATES:
        raise job_store.ValidationError(f"unsupported checkpoint stage: {stage!r}")
    job = get_job(job_id, db_path)
    if job["state"] in TERMINAL_STATES:
        raise job_store.ValidationError("terminal jobs cannot receive checkpoints")
    if job["state"] in CONTROL_STATES:
        expected_stage = job["resume_state"]
    else:
        expected_stage = job["state"]
    if stage != expected_stage:
        raise job_store.ValidationError(
            f"checkpoint stage must match the current stage {expected_stage!r}"
        )
    effective_expected_revision = (
        job["revision"] if expected_revision is None else expected_revision
    )
    return job_store.record_checkpoint(
        job_id,
        stage,
        actor,
        reason,
        job_store.validate_artifact_refs(artifact_refs),
        job_store.validate_metadata(metadata),
        effective_expected_revision,
        db_path,
    )


def list_transitions(job_id: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    return job_store.list_transitions(job_id, db_path)


def list_checkpoints(job_id: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    return job_store.list_checkpoints(job_id, db_path)


def list_recoverable_jobs(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    return job_store.list_recoverable_jobs(db_path)
