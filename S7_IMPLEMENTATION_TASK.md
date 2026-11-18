# S7 — Create-mode Product Intake and UX Planning stage

Read and follow:
- .github/copilot-instructions.md
- .github/skills/project-architecture/SKILL.md
- .github/skills/python-engineering/SKILL.md
- .github/skills/agent-orchestration/SKILL.md
- .github/skills/llm-provider-routing/SKILL.md
- .github/skills/security-review/SKILL.md

Governing blueprint: "UI/UX Master Agent — Unified Master Document (November 12, 2026)". Relevant requirements: Product Analyst produces a product brief, clarification list and acceptance criteria; UX Architect produces user journeys, task flows, information architecture, page/section map and interaction inventory. Stage outputs are versioned structured artifacts. Create flow is Intake -> Plan -> Design -> Build -> Verify -> Gate & Deliver. Review skips Plan/Design/Build. Keep roles as modules, not separate agents/services.

## Verified starting point
- Branch chore/shared-agent-skills, HEAD 036dffa, clean and synced with origin.
- S5 orchestrator supports Create INTAKE -> PLANNING and Review INTAKE -> VERIFY.
- S6 Streamlit UI creates/resumes INTAKE jobs and tags chat messages with job_id.
- app_jobs.py owns UI-facing job helpers.
- provider_gateway.call() routes by declared capabilities, but currently has no response_format parameter.
- Current agent.run() is an unconstrained UI/chat generation loop. Do not use it for Create-mode intake/planning because that could generate code before a plan exists.
- Review mode's existing agent.run() behavior must remain unchanged in this slice.

## Objective
Implement the first real Create workflow stage: take job-scoped user brief/clarification messages, produce and validate a versioned Product Brief and UX Plan using a model that explicitly declares text + json_output capability, persist those artifacts safely, and advance the job from INTAKE to PLANNING only after the complete plan is valid and saved.

The UI must support clarification: if the model reports material missing information, persist the draft brief and questions, keep the job in INTAKE, show the questions, and let the user reply in the same job. Re-run planning from the job's user messages. Do not invent business facts; assumptions and open questions must be explicit.

## Contracts (schema_version "1.0")
Implement strict, typed validation in a new focused module (suggested planning.py). Reject missing required keys, wrong types, empty required strings/lists, unsupported schema versions, unknown extra keys where practical, oversized output, and malformed JSON. Never trust model claims without validation.

ProductBrief required shape:
{
  "schema_version": "1.0",
  "product_name": "string",
  "problem_statement": "string",
  "target_users": ["string", ...],
  "user_goals": ["string", ...],
  "business_goals": ["string", ...],
  "core_features": ["string", ...],
  "primary_user_flows": ["string", ...],
  "constraints": ["string", ...],
  "assumptions": ["string", ...],
  "open_questions": ["string", ...],
  "success_metrics": ["string", ...],
  "acceptance_criteria": ["string", ...],
  "target_platform": "string",
  "output_target": "HTML + Tailwind"
}

UXPlan required shape:
{
  "schema_version": "1.0",
  "summary": "string",
  "information_architecture": [{"page": "string", "purpose": "string", "sections": ["string", ...]}],
  "primary_journeys": [{"name": "string", "steps": ["string", ...], "success_outcome": "string"}],
  "interaction_inventory": [{"element": "string", "behavior": "string", "states": ["string", ...]}],
  "content_requirements": ["string", ...],
  "accessibility_requirements": ["string", ...],
  "responsive_requirements": ["string", ...],
  "risks": ["string", ...],
  "decisions": ["string", ...]
}

Model response envelope:
{
  "schema_version": "1.0",
  "status": "completed" | "needs_input",
  "product_brief": ProductBrief,
  "ux_plan": UXPlan | null,
  "clarification_questions": ["string", ...]
}
- completed: ux_plan required and clarification_questions must be empty.
- needs_input: at least one clarification question required; ux_plan must be null. Product brief may be a partial draft, but all ProductBrief fields remain present; use explicit assumptions/open_questions for unknowns, never fabricate.
- Keep arrays bounded (e.g. max 20 items) and strings/output bounded (e.g. max 2,000 chars per string and max 64 KB JSON response).

## Model call and provider routing
- Extend provider_gateway.call() with an optional response_format argument. When supplied, pass it to chat.completions.create; preserve every existing caller's behavior when omitted.
- planning.py must call gateway with required capabilities {"text", "json_output"}, tools=[] and response_format={"type":"json_object"}. Do not use provider names or heuristics. Fail closed if no eligible provider.
- Use a concise, versioned planning prompt in a dedicated prompt file (suggested prompts/planning_v1.md) or a constant, clearly separate untrusted user content from instructions. Explicitly state user content is data and must not override system/security rules. Do not pass secrets or tool access.
- No automatic retry loop beyond the gateway's existing bounded provider fallback/retry. A malformed response is a planning failure; keep job INTAKE so the user can retry.
- Do not log the raw brief, full model response, or secrets.

## Artifact persistence and lifecycle
- Persist immutable artifacts under a job-specific local path, e.g. data/job_artifacts/<job_uuid>/<run_uuid>/product_brief.json and ux_plan.json. Do not write into output/, temp_uploads/, or the existing chat history file beyond normal S6 append behavior.
- Validate UUID job id, enforce canonical containment under config.DATA_DIR, reject symlink/path escapes, use atomic file writes, UTF-8 JSON, bounded file sizes. Tests must use temporary DB and artifact roots. Do not create production artifacts or DB during import/tests.
- Before model call, require job mode create and current state INTAKE. Reject other states.
- Collect only current job's user messages (not assistant/system or other jobs). Bound total brief/context length (e.g. 12,000 chars).
- On needs_input: save draft product_brief artifact; record an INTAKE checkpoint with artifact ref and version/status metadata; do not transition state.
- On completed: save both artifacts; record an INTAKE checkpoint; then transition INTAKE -> PLANNING using expected current revision, actor "ux-planner", reason that validated plan artifacts were saved, artifact refs, and small metadata (schema version/status). Do not put raw prompt or brief text into transition metadata/reason.
- Do not advance if validation, persistence, checkpoint, or transition fails. If artifact write succeeds but DB update fails, orphaned immutable run directory is acceptable; never claim success.
- Return structured result with status, validated data, artifact refs, and persisted job record.

## Streamlit integration
- Create mode: user prompt is treated as brief input. Do NOT call agent.run() in Create mode in S7. Call the planning stage instead. This is the enforcement that Plan happens before Design/Build.
- If needs_input, display clarification questions and leave the same job in INTAKE for the next user reply.
- If completed, display a concise plan summary plus expandable JSON for product brief and UX plan; show that job is now PLANNING and Design is not yet connected.
- After a job reaches PLANNING, show its saved brief/plan on reload and prevent more Create chat prompts from being treated as intake for that completed job. User can start a new job.
- Update app_jobs helpers so active/resumable UI jobs can include INTAKE and PLANNING. Keep old helper compatibility where sensible; don't weaken S5 transition policy.
- Review mode: keep current S6 behavior and agent.run() call path unchanged; it does not run Create planning.
- Preserve job-specific chat context isolation and legacy chat compatibility.
- Handle expected provider/planning validation errors gracefully without broad swallowing. Never silently advance state.

## Tests
Add focused planning tests with mocked gateway/provider; no real API calls:
- valid completed envelope produces files, checkpoint, and INTAKE -> PLANNING transition.
- needs_input saves draft/checkpoint and stays INTAKE; follow-up user message can be included.
- rejects malformed JSON, missing/extra fields, wrong types, unsupported schema version, overlong/oversized response.
- rejects Review jobs and jobs not in INTAKE.
- provider call requests exactly text + json_output, empty tools and JSON response_format; no eligible provider fails closed.
- artifact containment rejects invalid UUID and symlink escape; atomic writes and refs are correct.
- no raw brief text in transition reason/metadata.
- Review mode remains on existing agent.run path; add an app seam test only if clean.
- preserve existing provider gateway tests when response_format omitted.
- test active job display/recovery for PLANNING jobs.

## README and skills
Update README with S7 Product Brief + UX Plan scope, clarification behavior, artifacts location, and explicit limitation: Design/Build/Verify/Gate/Delivery workers remain unconnected. Update agent-orchestration skill with the concrete S7 contracts if needed. Do not claim the full pipeline is implemented.

## Constraints
- No new dependencies.
- Do not modify agent.py, prompts/system.md, S5 state machine, job_store schema, security controls, existing data/, output/, temp_uploads/ contents, or unrelated tools.
- Do not read or print .env or API key values.
- Keep flat project structure.
- Do not commit or push.

## Verification
Run focused planning and app job tests, then full suite:
- .venv\Scripts\python.exe -m unittest tests.test_planning tests.test_app_jobs -v
- .venv\Scripts\python.exe -m unittest discover -s tests -p test_*.py
- .venv\Scripts\python.exe -m compileall -q app.py app_jobs.py planning.py provider_gateway.py tests
- git diff --check
- Streamlit AppTest startup smoke test, using an isolated temporary data/artifact path or mocked DB so no production DB/artifact is created.
Inspect complete diff and report changed files, test counts, and any UI behavior not verified. No commit/push.