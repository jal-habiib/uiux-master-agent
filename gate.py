"""Deterministic Gate & Deliver stage for completed Create jobs.

Delivery is a local, immutable ZIP package. A failed QA report is never
relabelled as passing; after three recorded REFINE -> VERIFY cycles it may be
packaged only as an explicitly not-passed delivery.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import app_jobs
import config
import job_store
import orchestrator
import verify

SCHEMA_VERSION = "1.0"
INTEGRATION = "s11-gate-deliver"
ACTOR = "delivery-gate"
REPORT_NAME = "gate_report.json"
DELIVERY_NAME = "delivery.zip"
DELIVERY_NOTE_NAME = "delivery_report.md"
MAX_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_FIX_CYCLES = 3
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class GateError(job_store.ValidationError):
    """Raised when Gate inputs, reports, or delivery artifacts are invalid."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")


def _check_path(path: Path) -> None:
    if ".." in path.parts:
        raise GateError("delivery paths cannot contain traversal")
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    candidate = path.absolute()
    for part in reversed((candidate, *candidate.parents)):
        try:
            details = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & flag:
            raise GateError("delivery paths cannot contain symlink or reparse-point components")


def _read_ref(reference: str, job_id: str, root: Path) -> bytes:
    return verify._read_ref(reference, job_id, root)


def _latest_gate_checkpoint(job: dict[str, Any]) -> dict[str, Any] | None:
    checkpoints = job.get("checkpoints")
    if not isinstance(checkpoints, list):
        checkpoints = orchestrator.list_checkpoints(job["id"])
    return next((item for item in reversed(checkpoints)
                 if item.get("metadata", {}).get("integration") == INTEGRATION), None)


def _load_inputs(job_id: str, db_path: str | Path | None, artifact_root: str | Path | None) -> dict[str, Any]:
    try:
        normalized = str(uuid.UUID(job_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise GateError("job id must be a canonical UUID") from exc
    if normalized != job_id:
        raise GateError("job id must use canonical UUID syntax")
    job = orchestrator.get_job(normalized, db_path)
    if job["mode"] != "create" or job["state"] != "VERIFY":
        raise GateError("Gate requires an active Create job in VERIFY")
    checkpoints = orchestrator.list_checkpoints(normalized, db_path)
    job = {**job, "checkpoints": checkpoints}
    report = verify.load_saved_verification(job, artifact_root)
    if not isinstance(report, dict):
        raise GateError("Gate requires a saved S10 Verify report")
    if report["status"] not in {"PASS", "FAIL", "NEEDS_INPUT"}:
        raise GateError("Verify report status is invalid")
    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    _check_path(root)

    refs = report["source_artifact_refs"]
    digests = report["source_sha256"]
    if not isinstance(refs, list) or not refs or len(refs) > 21 or set(refs) != set(digests):
        raise GateError("Verify source references are malformed")
    latest_build = next((item for item in reversed(checkpoints)
        if item.get("metadata", {}).get("integration") in {
            app_jobs.BUILD_INTEGRATION, app_jobs.REFINE_INTEGRATION
        }
        and item.get("metadata", {}).get("status") == "completed"), None)
    if (
        latest_build is None
        or not isinstance(latest_build.get("artifact_refs"), list)
        or set(latest_build["artifact_refs"]) != set(refs)
        or latest_build.get("metadata", {}).get("artifact_refs") != latest_build.get("artifact_refs")
    ):
        raise GateError("Verify report is stale; it does not cover the latest immutable Build/Refine run")
    source_bytes: dict[str, bytes] = {}
    for ref in refs:
        raw = _read_ref(ref, normalized, root)
        if hashlib.sha256(raw).hexdigest() != digests.get(ref):
            raise GateError("A source artifact changed after Verify")
        source_bytes[ref] = raw

    manifest_refs = [ref for ref in refs if ref.endswith("/" + app_jobs.BUILD_MANIFEST_NAME)]
    if len(manifest_refs) != 1:
        raise GateError("Verify report must reference exactly one S9 manifest")
    manifest_ref = manifest_refs[0]
    try:
        manifest = json.loads(source_bytes[manifest_ref].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError("S9 manifest is malformed") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0"
            or not isinstance(manifest.get("run_id"), str)
            or not isinstance(manifest.get("pages"), list)
            or not 1 <= len(manifest["pages"]) <= 20):
        raise GateError("S9 manifest does not match its contract")
    expected_page_refs = []
    page_files: dict[str, bytes] = {}
    filenames: set[str] = set()
    for page in manifest["pages"]:
        if (not isinstance(page, dict) or set(page) != {"page", "filename"}
                or not isinstance(page["page"], str) or not page["page"].strip()
                or not isinstance(page["filename"], str)
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]{0,98}[a-z0-9])?[.]html", page["filename"])
                or page["filename"] in filenames):
            raise GateError("S9 manifest page entry is invalid")
        filenames.add(page["filename"])
        ref = f"job_artifacts/{normalized}/{manifest['run_id']}/build/{page['filename']}"
        expected_page_refs.append(ref)
        raw = source_bytes.get(ref)
        if raw is None or len(raw) > 256 * 1024:
            raise GateError("A page is missing from the verified source set or exceeds its limit")
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateError("A generated page is not valid UTF-8") from exc
        import build
        build.validate_html_document(html, set(filenames) | {
            item["filename"] for item in manifest["pages"] if isinstance(item, dict) and isinstance(item.get("filename"), str)
        })
        page_files[page["filename"]] = raw
    if set(refs) != {manifest_ref, *expected_page_refs}:
        raise GateError("Verify report source refs do not exactly match the S9 manifest")

    source_refs = manifest.get("source_artifact_refs")
    expected_source_names = app_jobs.PLANNING_ARTIFACT_NAMES | app_jobs.DESIGN_ARTIFACT_NAMES
    if not isinstance(source_refs, list) or len(source_refs) != len(expected_source_names):
        raise GateError("S9 source artifact references are incomplete")
    source_files: dict[str, bytes] = {}
    for ref in source_refs:
        if not isinstance(ref, str) or chr(92) in ref:
            raise GateError("S9 source artifact reference is malformed")
        path = PurePosixPath(ref)
        if (path.is_absolute() or str(path) != ref or len(path.parts) != 4
                or path.parts[:2] != ("job_artifacts", normalized)
                or path.parts[3] not in expected_source_names):
            raise GateError("S9 source artifact is outside the active job")
        if path.parts[3] in source_files:
            raise GateError("S9 source artifacts contain duplicate names")
        raw = _read_ref(ref, normalized, root)
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GateError("A planning or design source artifact is malformed") from exc
        source_files[path.parts[3]] = raw
    if set(source_files) != expected_source_names:
        raise GateError("Delivery requires both planning and both design artifacts")

    verify_checkpoint = next((item for item in reversed(checkpoints)
        if item.get("metadata", {}).get("integration") == verify.VERIFY_INTEGRATION), None)
    if not verify_checkpoint or len(verify_checkpoint.get("artifact_refs", [])) != 1:
        raise GateError("The saved Verify checkpoint is missing")
    verify_ref = verify_checkpoint["artifact_refs"][0]
    verify_raw = _read_ref(verify_ref, normalized, root)
    if hashlib.sha256(verify_raw).hexdigest() != verify_checkpoint.get("metadata", {}).get("report_sha256"):
        raise GateError("Verify report failed its checkpoint integrity check")
    transitions = orchestrator.list_transitions(normalized, db_path)
    fix_cycles = sum(1 for item in transitions
                     if item.get("from_state") == "REFINE" and item.get("to_state") == "VERIFY")
    return {
        "job": job, "report": report, "root": root, "manifest": manifest,
        "manifest_ref": manifest_ref, "source_bytes": source_bytes,
        "source_files": source_files, "page_files": page_files,
        "verify_ref": verify_ref, "verify_bytes": verify_raw,
        "fix_cycles": fix_cycles,
    }


def _delivery_note(inputs: dict[str, Any], gate_status: str) -> str:
    report = inputs["report"]
    lines = [
        "# UI/UX Master Agent delivery",
        "",
        f"- Job: {inputs['job']['id']}",
        f"- Delivery status: {gate_status}",
        f"- Verify status: {report['status']}",
        f"- Fix cycles recorded: {inputs['fix_cycles']} of {MAX_FIX_CYCLES}",
        f"- Build run: {inputs['manifest']['run_id']}",
        "",
        "## Included",
        "- Product brief and UX plan",
        "- Design specification and design tokens",
        "- Immutable HTML pages and build manifest",
        "- Machine-readable Verify report and source checksums",
        "- This delivery note",
        "",
        "## Preview",
        "Extract the ZIP and open the first HTML page in a modern browser.",
        "Pages are standalone HTML files; browser-dependent checks and external assets",
        "are described in the Verify report and must not be assumed available offline.",
        "",
        "## Quality and limitations",
        "The Verify report is authoritative. NOT_VERIFIED checks are not passes.",
        "A NOT_PASSED delivery is explicitly not a production-readiness claim.",
        "No deployment or external publishing was performed.",
        "",
        "## Source trace",
        f"- S9 manifest: {inputs['manifest_ref']}",
        f"- Verify report: {inputs['verify_ref']}",
        "- SHA-256 checksums are recorded in gate_report.json beside this package.",
        "",
    ]
    return "\n".join(lines)


def _package(inputs: dict[str, Any], note: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("README_DELIVERY.md", note)
        for name, raw in sorted(inputs["source_files"].items()):
            archive.writestr(f"artifacts/{name}", raw)
        archive.writestr(f"build/{app_jobs.BUILD_MANIFEST_NAME}", _json_bytes(inputs["manifest"]))
        for name, raw in sorted(inputs["page_files"].items()):
            archive.writestr(f"build/{name}", raw)
        archive.writestr("qa/verify_report.json", inputs["verify_bytes"])
    value = buffer.getvalue()
    if len(value) > MAX_PACKAGE_BYTES:
        raise GateError("delivery package exceeds the 10 MiB limit")
    return value


def _write_atomic(path: Path, raw: bytes) -> None:
    _check_path(path)
    if path.exists():
        raise GateError("delivery artifacts are immutable; refusing to overwrite")
    temp = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    linked = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _check_path(path)
        if path.exists():
            raise GateError("delivery artifacts are immutable; refusing to overwrite")
        os.link(temp, path)
        linked = True
        temp.unlink()
    except Exception:
        if linked:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def _save_run(job_id: str, root: Path, report: dict[str, Any], note: str,
              package: bytes | None) -> tuple[str, list[str], Path, dict[str, bytes]]:
    base = root / "job_artifacts"
    _check_path(base)
    base.mkdir(parents=True, exist_ok=True)
    job_dir = base / job_id
    _check_path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    _check_path(job_dir)
    if job_dir.resolve(strict=True).parent != base.resolve(strict=True):
        raise GateError("delivery job directory escaped the artifact root")
    run_id = str(uuid.uuid4())
    run_dir = job_dir / run_id
    run_dir.mkdir(exist_ok=False)
    _check_path(run_dir)
    report["run_id"] = run_id
    report_ref = f"job_artifacts/{job_id}/{run_id}/{REPORT_NAME}"
    note_ref = f"job_artifacts/{job_id}/{run_id}/{DELIVERY_NOTE_NAME}"
    report["artifact_refs"] = [report_ref, note_ref] + (
        [f"job_artifacts/{job_id}/{run_id}/{DELIVERY_NAME}"] if package is not None else []
    )
    report_raw = _json_bytes(report)
    if len(report_raw) > MAX_REPORT_BYTES:
        raise GateError("Gate report exceeds its size limit")
    files = {REPORT_NAME: report_raw, DELIVERY_NOTE_NAME: note.encode("utf-8")}
    if package is not None:
        files[DELIVERY_NAME] = package
    try:
        for name, raw in files.items():
            _write_atomic(run_dir / name, raw)
    except Exception:
        for name, raw in files.items():
            target = run_dir / name
            try:
                if target.is_file() and target.read_bytes() == raw:
                    target.unlink()
            except OSError:
                pass
        try:
            run_dir.rmdir()
        except OSError:
            pass
        raise
    return run_id, report["artifact_refs"], run_dir, files


def gate_job(job_id: str, db_path: str | Path | None = None,
             artifact_root: str | Path | None = None) -> dict[str, Any]:
    """Evaluate the code-enforced gate and create a local delivery package when allowed."""
    inputs = _load_inputs(job_id, db_path, artifact_root)
    qa = inputs["report"]
    failed = [name for name, item in qa["hard_gates"].items() if item["status"] == "FAIL"]
    unverified = [name for name, item in qa["hard_gates"].items() if item["status"] == "NOT_VERIFIED"]
    score = qa["soft_quality"]["score"]
    score_passed = qa["soft_quality"]["score_gate_passed"] and score >= 80
    if qa["status"] == "NEEDS_INPUT" or unverified:
        status = "BLOCKED"
        reason = "Verification evidence is incomplete; missing checks cannot be treated as passing."
        package = None
        gate_status = "BLOCKED"
    elif failed or not score_passed:
        if inputs["fix_cycles"] < MAX_FIX_CYCLES:
            status = "BLOCKED"
            reason = "Quality gate failed; refinement is required before delivery."
            package = None
            gate_status = "BLOCKED"
        else:
            status = "NOT_PASSED"
            reason = "Three fix cycles were completed; packaging is allowed only with an explicit not-passed report."
            gate_status = "NOT_PASSED_AFTER_3_CYCLES"
            package = None
    else:
        status = "PASS"
        reason = "All hard gates and the soft quality threshold passed."
        gate_status = "PASS"
        package = None

    note = _delivery_note(inputs, gate_status)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "gate_status": gate_status,
        "job_id": job_id,
        "run_id": "",
        "verify_status": qa["status"],
        "fix_cycles": inputs["fix_cycles"],
        "max_fix_cycles": MAX_FIX_CYCLES,
        "failed_gates": failed,
        "not_verified_gates": unverified,
        "soft_quality": {"score": score, "threshold": 80, "passed": score_passed},
        "source_artifact_refs": list(qa["source_artifact_refs"]),
        "source_sha256": dict(qa["source_sha256"]),
        "verify_report_ref": inputs["verify_ref"],
        "verify_report_sha256": hashlib.sha256(inputs["verify_bytes"]).hexdigest(),
        "package_sha256": None,
        "reason": reason,
        "artifact_refs": [],
    }
    if status in {"PASS", "NOT_PASSED"}:
        package = _package(inputs, note)
    root = inputs["root"]
    run_id = str(uuid.uuid4())
    # Package digest is computed before report persistence; the report itself is
    # stored beside (not inside) the ZIP, avoiding a circular archive checksum.
    report["run_id"] = run_id
    if package is not None:
        report["package_sha256"] = hashlib.sha256(package).hexdigest()
    # Use the same run id in all immutable artifact references.
    run_id, refs, run_dir, files = _save_run(job_id, root, report, note, package)
    metadata = {
        "integration": INTEGRATION, "schema_version": SCHEMA_VERSION,
        "run_id": run_id, "status": status, "gate_status": gate_status, "fix_cycles": inputs["fix_cycles"],
        "artifact_refs": refs, "package_sha256": report["package_sha256"],
        "verify_report_ref": inputs["verify_ref"],
        "verify_report_sha256": report["verify_report_sha256"],
    }
    try:
        if status == "BLOCKED":
            orchestrator.record_checkpoint(
                job_id, "VERIFY", ACTOR, reason, refs, metadata,
                expected_revision=inputs["job"]["revision"], db_path=db_path,
            )
            next_job = orchestrator.get_job(job_id, db_path)
        else:
            first = orchestrator.transition_job(
                job_id, "GATE_AND_DELIVER", ACTOR, reason, inputs["job"]["revision"],
                artifact_refs=refs, metadata=metadata, db_path=db_path,
                checkpoint={"stage": "VERIFY", "actor": ACTOR, "reason": "Persist Gate report and delivery package"},
            )
            next_job = finalize_delivery(job_id, db_path=db_path, artifact_root=root)
            if next_job.get("job"):
                next_job = next_job["job"]
    except Exception:
        # If the state transition already persisted, its immutable artifacts are
        # now the recovery source and must not be deleted.
        current = orchestrator.get_job(job_id, db_path)
        if current["state"] == "VERIFY":
            for name, raw in files.items():
                target = run_dir / name
                try:
                    if target.is_file() and target.read_bytes() == raw:
                        target.unlink()
                except OSError:
                    pass
            try:
                run_dir.rmdir()
            except OSError:
                pass
        raise
    return {
        "report": report, "artifact_refs": refs, "package_bytes": package,
        "job": next_job, "status": status,
    }


def load_saved_gate(job: dict[str, Any], artifact_root: str | Path | None = None) -> dict[str, Any] | None:
    if job.get("mode") != "create" or job.get("state") not in {"VERIFY", "GATE_AND_DELIVER", "DELIVERED"}:
        raise GateError("saved Gate output requires a Create job in a Gate-related state")
    checkpoints = job.get("checkpoints")
    if not isinstance(checkpoints, list):
        checkpoints = orchestrator.list_checkpoints(job["id"])
    checkpoint = next((item for item in reversed(checkpoints)
        if item.get("metadata", {}).get("integration") == INTEGRATION), None)
    if checkpoint is None:
        return None
    refs = checkpoint.get("artifact_refs")
    metadata = checkpoint.get("metadata", {})
    if (not isinstance(refs, list) or len(refs) not in {2, 3}
            or metadata.get("artifact_refs") != refs):
        raise GateError("Gate checkpoint references are malformed")
    root = Path(artifact_root) if artifact_root is not None else config.DATA_DIR
    report_ref = refs[0]
    report_raw = _read_ref(report_ref, job["id"], root)
    report = json.loads(report_raw.decode("utf-8"))
    if (not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION
            or report.get("job_id") != job["id"] or report.get("run_id") != checkpoint.get("metadata", {}).get("run_id")
            or report.get("artifact_refs") != refs):
        raise GateError("Saved Gate report does not match its checkpoint")
    if metadata.get("status") != report.get("status") or metadata.get("gate_status") != report.get("gate_status"):
        raise GateError("Saved Gate report status does not match its checkpoint")
    if report.get("verify_report_sha256") != metadata.get("verify_report_sha256"):
        raise GateError("Saved Gate Verify digest does not match its checkpoint")
    result = {"report": report, "artifact_refs": refs, "package_bytes": None}
    if len(refs) == 3:
        package_raw = _read_ref(refs[2], job["id"], root)
        if hashlib.sha256(package_raw).hexdigest() != report.get("package_sha256"):
            raise GateError("Saved delivery package failed its integrity check")
        if len(package_raw) > MAX_PACKAGE_BYTES:
            raise GateError("Saved delivery package exceeds its size limit")
        result["package_bytes"] = package_raw
    return result


def finalize_delivery(job_id: str, db_path: str | Path | None = None,
                      artifact_root: str | Path | None = None) -> dict[str, Any]:
    """Finish the second transition after a persisted Gate package, safely resumable."""
    job = orchestrator.get_job(job_id, db_path)
    if job["mode"] != "create" or job["state"] != "GATE_AND_DELIVER":
        raise GateError("delivery finalization requires a Create job in GATE_AND_DELIVER")
    checkpoints = orchestrator.list_checkpoints(job_id, db_path)
    job = {**job, "checkpoints": checkpoints}
    saved = load_saved_gate(job, artifact_root)
    if not saved or saved["report"]["status"] not in {"PASS", "NOT_PASSED"}:
        raise GateError("only a completed passing or three-cycle not-passed Gate can be delivered")
    report = saved["report"]
    metadata = {
        "gate_status": report["gate_status"],
        "status": report["status"],
        "run_id": report["run_id"],
        "fix_cycles": report["fix_cycles"],
        "integration": INTEGRATION,
        "artifact_refs": saved["artifact_refs"],
        "package_sha256": report["package_sha256"],
        "verify_report_sha256": report["verify_report_sha256"],
    }
    result = orchestrator.transition_job(
        job_id, "DELIVERED", ACTOR, report["reason"], job["revision"],
        artifact_refs=saved["artifact_refs"], metadata=metadata, db_path=db_path,
        checkpoint={"stage": "GATE_AND_DELIVER", "actor": ACTOR, "reason": "Finalize local delivery"},
    )
    return {"job": orchestrator.get_job(job_id, db_path), "report": report,
            "artifact_refs": saved["artifact_refs"], "package_bytes": saved["package_bytes"]}
