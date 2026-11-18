# S10 Implementation Task — Verify Stage

## Objective and scope

Implement the Create-mode Verify stage after S9:

`BUILD -> VERIFY`

Consume only the active Create job's completed, immutable S9 build manifest and
its referenced pages. Verify their integrity and run deterministic static and
available browser checks. Save a bounded immutable report under the same job's
artifact subtree and transition atomically with the job's current revision.
Do not implement Refine, Gate & Deliver, or delivery behavior.

The repository has S7, S8, and S9 implementation task documents and an S9
status in README; no separate tracked roadmap or November 12, 2026 unified
master document is present in the checkout. This task follows the stage order
and guardrails in `.github/copilot-instructions.md`, the shared skills, and the
live S7-S9 code. No broader architecture change is proposed.

## Required behavior

1. Accept only a canonical UUID for an active `create` job in `BUILD`.
   Reject Review jobs, other states, missing jobs, absent/malformed checkpoints,
   cross-job refs, and inconsistent manifests without changing job state.
2. Load only the job's latest completed S9 checkpoint and the exact immutable
   manifest/pages named by its artifact refs. Revalidate path containment,
   UUIDs, symlink/reparse components, file types, sizes, UTF-8, manifest/page
   correspondence, source refs, and content hashes while loading.
3. Treat manifest, page, and user-supplied browser-flow content as untrusted
   data. Never interpolate them into a system prompt or execute page code
   outside the existing isolated browser QA mechanism. Do not edit Build files.
4. Run deterministic HTML security/structure, semantic accessibility,
   responsive-source, and existing static quality checks. When the existing
   Playwright/axe-core QA capability is installed, run it against the exact
   validated S9 page files. Browser checks that cannot run are `NOT_VERIFIED`,
   never `PASS`. Required flows may be provided as strict JSON; no flow
   evidence is `NOT_VERIFIED`.
5. Use the existing provider gateway only for optional supplemental source
   review when a configured model explicitly declares `text` and `json_output`.
   Keep model findings informational, validate its bounded JSON response, and
   never let model opinion override deterministic or browser evidence. Provider
   absence/failure is explicitly `NOT_VERIFIED` for the supplemental review,
   not a fabricated success or a replacement for hard gates.
6. Report `PASS`, `FAIL`, or `NEEDS_INPUT`. `PASS` requires every hard gate to
   be `PASS` and the existing soft quality threshold to pass. Any failed hard
   gate yields `FAIL` and `verified: false`; any unverified hard gate without a
   failure yields `NEEDS_INPUT` and `verified: false`.
7. Persist a versioned report (maximum 1 MiB) at
   `data/job_artifacts/<job-uuid>/<verify-run-uuid>/verify_report.json`.
   Writes must be bounded, atomic, immutable, job-scoped, and reject
   symlink/reparse escapes and collisions. Record S9 source refs and SHA-256
   digests for the manifest and each page.
8. For `NEEDS_INPUT`, atomically persist a BUILD checkpoint with report refs
   and leave state/revision unchanged so the user can retry Verify without
   regenerating Build. For `PASS` or `FAIL`, atomically checkpoint and transition
   `BUILD -> VERIFY` using the captured revision. On stale revision, any
   persistence failure, or transition failure, do not claim success and clean
   up only files created by that Verify run.
9. Resume/reload reads and revalidates the saved report; it must never
   regenerate pages or rerun a completed Verify stage. Expose a Verify action
   only for an active Create job in BUILD and show saved verification after
   reload. Keep Gate & Deliver unavailable.

## Contracts and bounds

- Report schema version: `"1.0"`. Exact report fields are
  `schema_version`, `status`, `verified`, `job_id`, `run_id`,
  `source_artifact_refs`, `source_sha256`, `hard_gates`,
  `soft_quality`, `provider_review`, and `pages`.
- Gate statuses are `PASS`, `FAIL`, and `NOT_VERIFIED`. Aggregate status uses
  the precedence described above. A failed gate must never be reported as
  verified.
- Flow input accepts only a bounded JSON object with `flows`; each flow has a
  non-empty `name` and non-empty `steps`; every step uses an existing supported
  browser action and its exact `selector`/optional `value` fields. Do not add
  script/eval actions or model-generated flows.
- Limit the report to 1 MiB, each source page to the existing S9 256 KiB cap,
  at most 20 pages, and text values to 2,000 characters. These reuse existing
  S9 output limits while bounding derived evidence.
- The optional provider review has its own strict versioned response contract
  and does not participate in hard-gate decisions. Do not store credentials,
  raw prompts, or provider error payloads.

## Files and compatibility

- Add a focused `verify.py` module and `prompts/verify_v1.md`.
- Add only the small integration needed in `app.py`, `app_jobs.py`, and the
  existing browser QA helper to verify a caller-validated immutable artifact
  path. Keep its existing output-directory tool contract unchanged.
- Add `tests/test_verify.py` and focused UI/recovery coverage. Update README
  only to describe S10 Verify and state that Gate & Deliver remain unconnected.
- Do not alter S5 policy/schema, S7/S8 contracts, S9 generation behavior,
  generated artifacts, data, output, temp uploads, MCP server files, or
  unrelated tools. No dependency changes, commits, or pushes.
- Artifact format is additive and uses the existing checkpoint/transition
  schema; no database migration is required. Rollback is limited to removing
  the new Verify integration/module/prompt/tests and restoring its UI/help
  references. Never delete existing S7-S9 artifacts.

## Tests and verification

Cover state/mode/job rejection, latest-manifest mismatch, malformed or
cross-job refs, tampered/unsafe HTML, static quality and accessibility,
responsive checks, optional provider failure, absent browser/axe, real report
status precedence, stale revision, atomic checkpoint/transition and cleanup,
immutable collisions, resume/reload integrity, and cross-job isolation.
Mock provider and browser dependencies; do not claim live provider or browser
execution from mocked tests.

Run the full test suite with the project virtual environment, compile affected
modules, and check whitespace:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py
.venv\Scripts\python.exe -m compileall -q app.py app_jobs.py verify.py tools\verify_html.py tests
git diff --check
```

Inspect the final diff and Git status. Do not commit or push.
