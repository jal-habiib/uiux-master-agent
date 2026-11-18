# S7 Final UI Acceptance Tests

## Scope
Add UI integration coverage for the two Create-mode intake outcomes. This is a test-only task.

Read `S7_IMPLEMENTATION_TASK.md`, `app.py`, `app_jobs.py`, `planning.py`, and `tests/test_app_jobs.py`.

Modify only `tests/test_app_jobs.py`.

## Requirements

Use Streamlit `AppTest`, temporary folders, patched `config.DATA_DIR`, and the existing `_run_app()` helper that mocks `chromadb.PersistentClient`. Do not create or modify production `data/` files. Do not make real provider calls.

Add these tests:

### 1. Create-mode completed plan
- Start a Create job in isolated storage.
- Render the app with that job selected.
- Submit a new brief through `chat_input`.
- Mock `planning.plan_job` with a side effect that records an INTAKE checkpoint and performs the valid INTAKE -> PLANNING transition using the existing orchestrator API, then returns a completed result with a test UX plan summary and the persisted job record.
- Assert `planning.plan_job` was called exactly once with the active job id and the current job's user message.
- Assert `agent.run` was not called.
- Assert the UI renders the completed-plan message and the supplied UX plan summary.
- Assert the persisted job state is PLANNING.

### 2. Create-mode needs-input and same-job follow-up
- Start a Create job in isolated storage and render the app with that job selected.
- Mock `planning.plan_job` with a deterministic side effect: first call returns `needs_input` with at least one clarification question and leaves the job in INTAKE; second call returns `completed` and performs the valid INTAKE -> PLANNING transition.
- Submit the initial brief. Assert the clarification question is displayed, the job remains INTAKE, and `agent.run` is not called.
- Submit a follow-up through the same UI session. Assert the planner is called a second time with the same job id and both user messages; assistant messages must not be treated as brief context.
- Assert the completed result advances that same job to PLANNING.

## Test implementation notes
- Follow the existing AppTest conventions and `_run_app()` helper; every AppTest run uses its 20-second timeout and mocked ChromaDB client.
- Prefer assertions against rendered Streamlit markdown/text and the actual persisted SQLite job state.
- Use small fake Product Brief/UX Plan objects; do not add fixture dependencies.
- Ensure mocks are restored with context managers.
- Do not add sleeps or time-based polling.

## Verification

Run:
1. `.venv\Scripts\python.exe -m unittest tests.test_app_jobs -v`
2. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py`
3. `.venv\Scripts\python.exe -m compileall -q app.py app_jobs.py planning.py provider_gateway.py tests`
4. `git diff --check`

Report the new UI test names and actual results.

## Boundaries

- Do not change application code, planning logic, provider gateway, MCP server, or Copilot MCP configuration.
- Do not stage, commit, or push.
- Preserve all existing user changes.