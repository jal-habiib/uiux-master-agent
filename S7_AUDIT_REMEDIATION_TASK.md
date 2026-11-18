# S7 Audit Remediation — Artifact Safety, Prompt Isolation, and Coverage

## Goal

Address the S7 audit findings before connecting Design. Preserve the current S7 behavior and S5 lifecycle policy. Do not broaden scope into Design/Build/Verify.

Read `S7_IMPLEMENTATION_TASK.md` and the shared engineering/security skills before editing. Treat the original S7 task as the governing contract.

## Audit findings to fix

### 1. Artifact-root symlink validation is ineffective

In `planning.py`, `_safe_base()` resolves `artifact_root` before calling `root.is_symlink()`. A resolved path no longer reports that its input was a symlink, so the current check does not prove the supplied root was not a symlink. Also review all artifact path ancestors and Windows reparse points, not only the final path component.

Required:
- Inspect the original, unresolved path and each existing ancestor for symlink/reparse-point components before resolving/creating directories.
- Keep the default production artifact root canonically contained under `config.DATA_DIR/job_artifacts`.
- Keep temporary artifact-root injection available for isolated tests, but reject symlink/reparse-point roots and escapes from the explicitly selected test root.
- Validate the job UUID before making a provider call, not only when saving artifacts.
- Preserve immutable per-run directories, atomic file replacement and the 64 KB artifact bound.
- Add tests for invalid UUIDs, symlink/reparse-point artifact roots, job-directory escape and intermediate symlink components. Skip platform-specific symlink tests only when the OS genuinely cannot create the link, and report the skip.

### 2. Saved-artifact loader must be bound to the active job

In `app_jobs.saved_planning_artifacts()`, references are currently resolved and checked against the whole `DATA_DIR`, rather than specifically against `DATA_DIR/job_artifacts/<active-job-id>`. The loader should not accept another job's artifact reference or a traversal path that resolves elsewhere inside `DATA_DIR`.

Required:
- Accept only relative references with the exact structure `job_artifacts/<active-job-uuid>/<run-uuid>/<filename>`.
- Require the embedded job UUID to equal `job['id']`.
- Allow only `product_brief.json` and `ux_plan.json` filenames.
- Reject traversal, absolute paths, symlink/reparse-point components and any resolved path outside the active job's artifact subtree.
- Bound loaded file size to the same 64 KB limit before parsing JSON.
- Add tests for cross-job references, `..` traversal, symlink references, unexpected filenames and oversized artifact files.

### 3. Untrusted brief content must not be interpolated into the system prompt

`planning.plan_job()` currently inserts the user's brief into `_PROMPT` with `.replace('{user_content}', context)` and sends the combined text as a single system message. Delimiter text inside a user brief can imitate the prompt's boundary markers. The prompt file itself already labels user data as untrusted, but role separation should enforce the boundary more clearly.

Required:
- Keep the versioned planning instructions static in the system message.
- Send the accumulated brief/follow-up content as a separate `user` role message.
- Remove the user-content placeholder and interpolated data section from the system prompt file.
- Keep `tools=[]`, required capabilities exactly `{'text', 'json_output'}`, and `response_format={'type': 'json_object'}`.
- Add a test proving adversarial content containing the existing delimiter and fake instructions remains in the user message and never becomes part of the system message.
- Do not add secrets, tools or extra retries.

### 4. Enforce job message ownership in the planning module

`_user_context()` currently accepts untagged messages (`job_id` missing/None) for any requested job. The Streamlit caller filters messages first, but the planning module should enforce its own S7 contract rather than rely on every caller to pre-filter.

Required:
- Include only messages with `role == 'user'` AND `message['job_id'] == job_id`.
- Exclude untagged legacy messages, assistant/system messages and messages belonging to other jobs.
- Update tests/fixtures to tag current-job messages explicitly.
- Add a context isolation test that includes untagged legacy messages and another job's messages.

## Test coverage required

Expand `tests/test_planning.py` and `tests/test_app_jobs.py` to cover the requirements above and the following existing contract gaps:
- Missing required keys, unexpected keys, wrong types, unsupported schema versions, empty required strings/lists, overlong strings, arrays above 20 items, malformed JSON and response bodies above 64 KB.
- `needs_input` requires at least one question and `ux_plan=None`; `completed` requires a valid UX plan and no questions.
- No eligible provider fails closed without a live API call.
- Gateway request uses exactly the declared text + json_output capabilities, no tools, and JSON response_format.
- `provider_gateway.call()` omits response_format entirely when it is not supplied, preserving old callers.
- PLANNING jobs can be recovered and their saved brief/plan are displayed from their own artifact references.
- A PLANNING Create job rejects another intake prompt without calling the planner or `agent.run()`.
- Review mode remains on its existing `agent.run()` path and never invokes Create planning.
- Add a Streamlit AppTest startup smoke test with isolated temporary data/artifact paths or mocks; it must not create or alter production DB/artifact files.

Use mocks for provider calls. No real API requests.

## Verification

From the repository root:
1. `.venv\Scripts\python.exe -m unittest tests.test_planning tests.test_app_jobs tests.test_provider_gateway -v`
2. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py`
3. `.venv\Scripts\python.exe -m compileall -q app.py app_jobs.py planning.py provider_gateway.py tests`
4. `git diff --check`
5. Run the Streamlit AppTest startup smoke using only temporary/mocked storage.

Inspect the full diff. Report test counts, exact changed files, skipped platform-specific tests and any UI behavior not verified.

## Strict boundaries

- Do not modify `agent.py`, the S5 state machine or SQLite schema.
- Do not modify `.env`, read or print secrets, or make real provider calls.
- Do not modify the contents of `data/`, `output/` or `temp_uploads/`.
- Do not modify the MCP server or its Copilot configuration.
- Do not stage, commit or push.
- Preserve all unrelated S7 and MCP work. Make only changes needed for this remediation.