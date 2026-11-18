# S8 Implementation Task — Design Stage

## Objective

Implement the next end-to-end Create workflow stage for the UI/UX Master Agent:

`PLANNING -> DESIGN`

S7 already produces validated, immutable `product_brief.json` and `ux_plan.json` artifacts. S8 must consume those artifacts for the active job and produce a validated, immutable design specification and design-token system. It must not generate application HTML or run Build/Verify; those remain downstream stages.

## Repository and branch

- Repository: `jal-habiib/uiux-master-agent`
- Work on the existing branch: `chore/shared-agent-skills`
- Preserve the existing S7 and MCP server commits.
- Do not amend, reset, rebase, or force-push.
- Keep MCP server code and configuration out of scope.
- Do not commit or push unless the operator explicitly requests it.

## Required workflow

1. A Create job must be in `PLANNING`.
2. Load the Product Brief and UX Plan from the active job's latest valid S7 planning checkpoint. Reuse the existing job-scoped artifact validation approach. Never search for or load another job's artifacts.
3. Treat artifact contents as untrusted product data. Keep system instructions separate from user/product data; do not interpolate artifact content into the system prompt.
4. Call the existing provider gateway with explicit required capabilities (`text` and `json_output`), bounded by existing provider timeout/retry policy.
5. Require a strict JSON response matching the S8 schema below. Reject unknown/missing fields, wrong types, oversized strings/arrays, invalid enums, and invalid cross-field references.
6. If material design decisions cannot safely be made from the supplied brief/UX plan, return `needs_input` with clarification questions. Do not invent brand facts, content, product behaviour, or accessibility exceptions.
7. For a completed result, save immutable, versioned artifacts in a new job-specific run directory under the existing artifact root. Never overwrite an existing run.
8. Record an auditable checkpoint and transition the job from `PLANNING` to `DESIGN` using the current revision. Use the existing orchestration APIs and revision checks.
9. For `needs_input`, save the validated clarification response as a checkpoint on `PLANNING`, leave the job in `PLANNING`, and return questions to the UI.
10. Expose the design result and questions in the existing Streamlit Create job flow. On resume/reload, show only the active job's saved planning and design artifacts. Do not rerun a completed S8 stage automatically.
11. Do not allow Build to start as part of S8. No HTML generation, file-writing tools for generated pages, browser QA, Verify, Gate, or Delivery.

## S8 response schema

Use schema version `1.0`. Keep the top-level envelope exact:

- `schema_version`: string, exactly `"1.0"`
- `status`: `"completed"` or `"needs_input"`
- `design_spec`: object when completed; null when needs_input
- `design_tokens`: object when completed; null when needs_input
- `clarification_questions`: array of non-empty strings

### design_spec

Exact fields:

- `schema_version`: `"1.0"`
- `design_direction`: concise design rationale and visual direction
- `brand_attributes`: list of grounded attributes; distinguish assumptions from supplied facts
- `page_specs`: non-empty list of objects with exact fields:
  - `page`
  - `purpose`
  - `layout`
  - `sections` (non-empty list of strings)
  - `primary_action`
  - `responsive_behavior`
- `component_specs`: non-empty list of objects with exact fields:
  - `component`
  - `purpose`
  - `variants` (non-empty list)
  - `states` (non-empty list)
  - `accessibility`
- `interaction_specs`: non-empty list of objects with exact fields:
  - `interaction`
  - `trigger`
  - `feedback`
  - `error_behavior`
  - `keyboard_behavior`
- `content_guidance`: non-empty list of strings
- `accessibility_checks`: non-empty list of strings
- `responsive_rules`: non-empty list of strings
- `implementation_constraints`: non-empty list of strings
- `assumptions`: list of strings (may be empty)
- `open_questions`: list of strings (may be empty)

### design_tokens

Exact fields:

- `schema_version`: `"1.0"`
- `color`: object with exact keys `primary`, `secondary`, `accent`, `background`, `surface`, `text`, `muted`, `border`, `success`, `warning`, `danger`; each value must be a valid hex color in `#RRGGBB` form
- `typography`: object with exact keys `font_family`, `heading_scale`, `body_size`, `line_height`, `font_weights`; use bounded non-empty strings or a documented finite numeric scale as appropriate
- `spacing`: object with exact keys `unit`, `scale`; unit must be a positive integer number of pixels and scale a non-empty list of positive integer multiples
- `radius`: non-empty list of bounded strings with CSS-compatible radius values
- `shadows`: non-empty list of bounded strings
- `breakpoints`: object with exact keys `mobile`, `tablet`, `desktop`; positive integer pixel values and strictly increasing
- `container`: object with exact keys `max_width`, `page_gutter`; positive integer pixel values

Use conservative validation limits: maximum 64 KB provider response/artifact, maximum 20 items per array, maximum 2,000 characters per string, and reject non-finite numbers. Adjust limits only with a documented reason.

## Artifact and persistence requirements

- Store under `data/job_artifacts/<job-id>/<run-id>/`.
- Required completed artifacts: `design_spec.json`, `design_tokens.json`.
- Include stable artifact references in the S8 checkpoint and transition metadata.
- Use a unique UUID run ID and canonical UUID validation.
- Reuse or extract the existing S7 artifact path protections rather than creating a weaker parallel implementation.
- Reject symlinks/reparse points and traversal; enforce active-job subtree boundaries; do not overwrite existing immutable artifacts.
- If one artifact write fails, do not transition to DESIGN. Avoid leaving a misleading completed checkpoint. Clean up only files created by the current failed run where safe.
- Preserve prior run artifacts and history.

## UI requirements

- Integrate into the existing Create job experience; avoid a new unrelated screen or duplicate workflow.
- Provide a clear action to generate the design stage from a completed S7 plan.
- Display design direction, page specifications, component specifications, accessibility/responsive guidance, and design tokens in a readable way.
- Display clarification questions when status is `needs_input`; keep the job in `PLANNING`.
- Show a completed design stage and its artifact references after reload.
- Do not silently regenerate or overwrite a completed design.
- Do not expose raw provider prompts, credentials, environment variables, or unrelated jobs' artifacts.

## Testing requirements

Add focused unit/integration tests for:

- Exact schema acceptance and rejection of missing/extra fields.
- Invalid colors, breakpoint ordering, invalid numeric values, oversized content, wrong types, and invalid enum/status values.
- `needs_input` response and clarification persistence without state transition.
- Completed response saves both artifacts and transitions `PLANNING -> DESIGN`.
- Correct checkpoint/transition artifact references and metadata.
- Stale revision or transition failure does not mark the stage completed.
- Wrong job ID, wrong job mode/state, missing or malformed S7 artifacts, and cross-job artifact substitution are rejected.
- Symlink/reparse/traversal artifact protections and immutable artifact collision handling.
- Provider failure, malformed JSON, oversized provider response, and retry behaviour.
- Streamlit UI rendering, action flow, resume/reload, and no automatic regeneration of completed design.
- Existing S7 tests and all existing project tests remain green.

Run the full repository test suite using the project's configured Python environment, plus compile checks and `git diff --check`. Report the exact test count and any skips/failures. Do not claim live provider integration was tested unless a real provider call was explicitly performed.

## Engineering constraints

- Follow the existing shared project skills and code conventions.
- Keep prompt instructions in a dedicated versioned file, e.g. `prompts/design_v1.md`.
- Keep the implementation modular and independently testable, e.g. `design.py` plus a thin UI integration.
- Reuse `provider_gateway`, `orchestrator`, `job_store`, and existing app job artifact-loading patterns.
- Do not change the SQLite schema unless a concrete requirement proves it necessary; S8 should be implementable with current checkpoints/transitions.
- Do not weaken S7 validation, prompt-injection protections, artifact security, provider routing, or MCP security.
- Do not add dependencies without a clear need and explicit justification.
- Keep changes limited to S8 files and the minimal integration points required.

## Completion report

Before requesting review, provide:

1. Files created/changed and why.
2. S8 workflow and state-transition behaviour.
3. Test commands and exact results.
4. Security checks and known limitations.
5. Confirmation that no Build/Verify/Delivery stage was run.
6. Git status and diff summary.
7. Explicitly state that nothing was committed or pushed.
