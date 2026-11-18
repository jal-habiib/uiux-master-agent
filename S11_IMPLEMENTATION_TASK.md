# S11 Implementation Task — Gate & Deliver

## Objective

Complete the Create workflow after S10 Verify:

`VERIFY -> GATE_AND_DELIVER -> DELIVERED`

Gate must evaluate only the active Create job's saved, immutable S10 report and its exact S9/S7/S8 source artifacts. It must never infer missing evidence, re-run Build or Verify, or relabel a failed check as passing.

## Required behavior

1. Accept only a canonical UUID for a Create job in `VERIFY`; require a saved, integrity-checked S10 report.
2. Re-read every referenced artifact from the active job's artifact tree. Verify SHA-256 digests, manifest/page correspondence, source references, UTF-8, size bounds, and path containment. Reject traversal, symlink/reparse escapes, malformed references, and tampering.
3. Gate status is deterministic:
   - `PASS`: every hard gate passed and the soft quality score is at least 80.
   - `BLOCKED`: Verify evidence is incomplete, any hard gate is not verified, or a failed quality gate still has fewer than three recorded `REFINE -> VERIFY` cycles.
   - `NOT_PASSED`: a failed build has completed at least three recorded fix cycles. It may be packaged only with the explicit `NOT_PASSED_AFTER_3_CYCLES` status; it is never represented as passing.
4. Create a bounded local ZIP only for `PASS` or the explicit three-cycle `NOT_PASSED` outcome. Include the Product Brief, UX Plan, Design spec/tokens, Build manifest/pages, Verify report, and a delivery note. Do not deploy or publish externally.
5. Persist immutable, job-scoped Gate report, delivery note, and optional ZIP. Use UUID run IDs, atomic writes, collision protection, path checks, and cleanup limited to the current failed run.
6. Persist the Gate checkpoint before final delivery. `GATE_AND_DELIVER` is a recoverable intermediate state: finalization must resume from saved artifacts without regenerating or repackaging. `DELIVERED` requires a passing Gate or an explicit not-passed report after at least three fix cycles.
7. UI must show the saved Gate result after reload and offer a ZIP download only when a package exists. Blocked jobs must display failed and unverified gates. Refine remains a manual handoff; deployment is out of scope.

## Bounds and integrity

- Gate report maximum: 256 KiB.
- Delivery ZIP maximum: 10 MiB.
- Source HTML pages maximum: 256 KiB each; maximum 20 pages.
- Verify source SHA-256 values must match before packaging.
- The Gate checkpoint stores the Verify report digest and run identity so recovery can validate the saved report against the checkpoint.

## Tests

`tests/test_gate.py` covers passing package creation, failed-gate blocking, incomplete Verify rejection, tamper rejection, wrong-state rejection, and recovery from an interrupted finalization. `tests/test_orchestrator.py` covers the state transition policy, including the explicit three-cycle not-passed exception.

Run the full suite with:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py
.venv\Scripts\python.exe -m compileall -q app.py app_jobs.py orchestrator.py planning.py build.py verify.py gate.py tools tests
git diff --check
```

No commit, push, external publishing, or deployment is implied by this task document.
