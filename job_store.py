"""Durable SQLite persistence for orchestration jobs.

This module contains storage mechanics only.  Transition validity belongs in
``orchestrator.py``.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

SCHEMA_VERSION = 1
DEFAULT_BUSY_TIMEOUT_MS = 5000


class StoreError(Exception):
    """Base class for durable job-store errors."""


class SchemaVersionError(StoreError):
    """Raised when a database was created by a newer store implementation."""


class JobNotFoundError(StoreError):
    """Raised when a requested job does not exist."""


class ValidationError(StoreError, ValueError):
    """Raised when a value cannot be stored safely."""


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path is not None else config.DATA_DIR / "jobs.sqlite3"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any, name: str) -> str:
    def validate(item: Any) -> None:
        if item is None or isinstance(item, (bool, str, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValidationError(f"{name} must be valid JSON")
            return
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValidationError(f"{name} must be valid JSON")
                validate(child)
            return
        raise ValidationError(f"{name} must be valid JSON")

    validate(value)
    try:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{name} must be valid JSON") from exc
    return encoded


def validate_artifact_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValidationError("artifact_refs must be a list of non-empty strings")
    _json_value(value, "artifact_refs")
    return [item.strip() for item in value]


def validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("metadata must be a JSON object")
    _json_value(value, "metadata")
    return dict(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    return value.strip()


@contextmanager
def _connection(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_store(db_path: str | Path | None = None) -> Path:
    """Create or validate the current schema without changing existing jobs."""
    path = _db_path(db_path)
    with _connection(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_metadata "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is not None:
            try:
                stored_version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise SchemaVersionError("database schema version is malformed") from exc
            if stored_version > SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database schema version {row['value']} is newer than supported version "
                    f"{SCHEMA_VERSION}"
                )
            if stored_version < SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database schema version {row['value']} is older than supported version "
                    f"{SCHEMA_VERSION}; no migration is available"
                )
        connection.execute(
            "INSERT OR IGNORE INTO schema_metadata(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                resume_state TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS job_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                from_state TEXT,
                from_revision INTEGER NOT NULL,
                to_state TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                artifact_refs TEXT NOT NULL,
                metadata TEXT NOT NULL,
                idempotency_key TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS job_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                stage TEXT NOT NULL,
                revision INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                artifact_refs TEXT NOT NULL,
                metadata TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_transitions_job "
            "ON job_transitions(job_id, id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_checkpoints_job "
            "ON job_checkpoints(job_id, id)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_transitions_idempotency "
            "ON job_transitions(job_id, idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )
    return path


def _require_job(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobNotFoundError(f"job {job_id} was not found")
    return row


def _decode_transition(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["artifact_refs"] = json.loads(result.pop("artifact_refs"))
    result["metadata"] = json.loads(result.pop("metadata"))
    return result


def _decode_checkpoint(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["artifact_refs"] = json.loads(result.pop("artifact_refs"))
    result["metadata"] = json.loads(result.pop("metadata"))
    return result


def _job(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_job(mode: str, actor: str, reason: str, db_path: str | Path | None = None) -> dict[str, Any]:
    """Atomically create a job and its initial CREATED history record."""
    mode = _text(mode, "mode")
    actor = _text(actor, "actor")
    reason = _text(reason, "reason")
    path = initialize_store(db_path)
    job_id = str(uuid.uuid4())
    timestamp = _utc_now()
    with _connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO jobs(id, mode, state, revision, resume_state, created_at, updated_at) "
            "VALUES (?, ?, 'CREATED', 0, NULL, ?, ?)",
            (job_id, mode, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO job_transitions(
                job_id, from_state, from_revision, to_state, timestamp, actor, reason,
                artifact_refs, metadata, idempotency_key
            ) VALUES (?, NULL, 0, 'CREATED', ?, ?, ?, '[]', '{}', NULL)""",
            (job_id, timestamp, actor, reason),
        )
        return _job(_require_job(connection, job_id))


def get_job(job_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    with _connection(initialize_store(db_path)) as connection:
        return _job(_require_job(connection, _text(job_id, "job_id")))


def find_idempotent_transition(
    job_id: str, idempotency_key: str, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    """Return a prior transition for a key, allowing retries before policy checks."""
    with _connection(initialize_store(db_path)) as connection:
        _require_job(connection, _text(job_id, "job_id"))
        key = _text(idempotency_key, "idempotency_key")
        row = connection.execute(
            "SELECT * FROM job_transitions WHERE job_id = ? AND idempotency_key = ?",
            (job_id, key),
        ).fetchone()
        return _decode_transition(row) if row is not None else None


def apply_transition(
    job_id: str,
    from_state: str,
    to_state: str,
    actor: str,
    reason: str,
    artifact_refs: list[str],
    metadata: dict[str, Any],
    expected_revision: int,
    resume_state: str | None,
    idempotency_key: str | None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist a validated transition under an immediate transaction."""
    job_id = _text(job_id, "job_id")
    actor = _text(actor, "actor")
    reason = _text(reason, "reason")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
        raise ValidationError("expected_revision must be a non-negative integer")
    if idempotency_key is not None:
        idempotency_key = _text(idempotency_key, "idempotency_key")
    artifact_json = _json_value(validate_artifact_refs(artifact_refs), "artifact_refs")
    metadata_json = _json_value(validate_metadata(metadata), "metadata")
    path = initialize_store(db_path)
    with _connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = _require_job(connection, job_id)
        if idempotency_key is not None:
            prior = connection.execute(
                "SELECT * FROM job_transitions WHERE job_id = ? AND idempotency_key = ?",
                (job_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                same_request = (
                    prior["from_state"] == from_state
                    and prior["from_revision"] == expected_revision
                    and prior["to_state"] == to_state
                    and prior["actor"] == actor
                    and prior["reason"] == reason
                    and prior["artifact_refs"] == artifact_json
                    and prior["metadata"] == metadata_json
                )
                if not same_request:
                    raise ValidationError("idempotency_key conflicts with the original request")
                return _decode_transition(prior)
        if job["revision"] != expected_revision:
            raise ValidationError(
                f"stale revision: expected {expected_revision}, current {job['revision']}"
            )
        timestamp = _utc_now()
        cursor = connection.execute(
            """INSERT INTO job_transitions(
                job_id, from_state, from_revision, to_state, timestamp, actor, reason,
                artifact_refs, metadata, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                from_state,
                expected_revision,
                to_state,
                timestamp,
                actor,
                reason,
                artifact_json,
                metadata_json,
                idempotency_key,
            ),
        )
        connection.execute(
            "UPDATE jobs SET state = ?, revision = revision + 1, resume_state = ?, updated_at = ? "
            "WHERE id = ?",
            (to_state, resume_state, timestamp, job_id),
        )
        row = connection.execute(
            "SELECT * FROM job_transitions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _decode_transition(row)


def record_checkpoint(
    job_id: str,
    stage: str,
    actor: str,
    reason: str,
    artifact_refs: list[str],
    metadata: dict[str, Any],
    expected_revision: int | None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    stage = _text(stage, "stage")
    actor = _text(actor, "actor")
    reason = _text(reason, "reason")
    artifacts = _json_value(validate_artifact_refs(artifact_refs), "artifact_refs")
    metadata_json = _json_value(validate_metadata(metadata), "metadata")
    if expected_revision is not None and (
        not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0
    ):
        raise ValidationError("expected_revision must be a non-negative integer")
    path = initialize_store(db_path)
    with _connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = _require_job(connection, _text(job_id, "job_id"))
        if expected_revision is not None and job["revision"] != expected_revision:
            raise ValidationError(
                f"stale revision: expected {expected_revision}, current {job['revision']}"
            )
        timestamp = _utc_now()
        cursor = connection.execute(
            """INSERT INTO job_checkpoints(
                job_id, stage, revision, timestamp, actor, reason, artifact_refs, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job["id"],
                stage,
                job["revision"],
                timestamp,
                actor,
                reason,
                artifacts,
                metadata_json,
            ),
        )
        row = connection.execute(
            "SELECT * FROM job_checkpoints WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _decode_checkpoint(row)


def list_transitions(job_id: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with _connection(initialize_store(db_path)) as connection:
        _require_job(connection, _text(job_id, "job_id"))
        rows = connection.execute(
            "SELECT * FROM job_transitions WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [_decode_transition(row) for row in rows]


def list_checkpoints(job_id: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with _connection(initialize_store(db_path)) as connection:
        _require_job(connection, _text(job_id, "job_id"))
        rows = connection.execute(
            "SELECT * FROM job_checkpoints WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [_decode_checkpoint(row) for row in rows]


def list_recoverable_jobs(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with _connection(initialize_store(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM jobs WHERE state NOT IN ('DELIVERED', 'CANCELLED') ORDER BY created_at"
        ).fetchall()
        result = []
        for row in rows:
            latest_transition = connection.execute(
                "SELECT * FROM job_transitions WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            latest_checkpoint = connection.execute(
                "SELECT * FROM job_checkpoints WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            item = _job(row)
            item["latest_transition"] = (
                _decode_transition(latest_transition) if latest_transition else None
            )
            item["latest_checkpoint"] = (
                _decode_checkpoint(latest_checkpoint) if latest_checkpoint else None
            )
            result.append(item)
        return result
