---
name: agent-orchestration
description: Design or modify the UI/UX Master Agent's staged workflow, job state machine, role handoffs, retries, checkpoints, budgets, and versioned stage contracts. Use for orchestrator or agent-loop work.
---

# Agent orchestration skill

## Core model
Implement a bounded, auditable staged workflow—not an unconstrained chat loop. The intended Create flow is Intake → Plan → Design → Build → Verify → Gate & Deliver. Review skips Plan, Design, and Build.

Specialist roles are modules within stages by default. Do not create separate agents, processes, or services unless measurements show a benefit.

## Stage contracts
- Each stage receives a typed, validated input and returns a versioned structured result.
- Record stage status, start/end times, actor/model, input/output artifact references, errors, retries, and relevant budget consumption.
- Keep reasoning outputs separate from tool side effects.
- Persist durable artifacts; do not rely on conversation history as the only record.
- Validate contract versions and required fields at boundaries.
- QA may produce findings and fix tasks. It must not silently mutate source files.

Expected Create artifacts include product_brief.json, ux_plan.json, design_brief.md, design_tokens.json, component_spec.json (recommended), source revision/diff, screenshots when rendering succeeds, qa_report.json, and delivery_report.md.

## Job state
Use explicit states equivalent to CREATED, INTAKE, PLANNING, DESIGN, BUILD, VERIFY, REFINE, WAITING_FOR_USER, BLOCKED, FAILED, DELIVERED, and CANCELLED. Adapt names only when the existing code has a sound established convention. Record each transition's timestamp, actor, reason, and artifact references. Reject invalid transitions.

## Budgets and stopping
- Define per-stage and per-job limits for iterations, elapsed time, tokens/cost, and tool calls where available.
- Derive iteration limits from the actual stage sequence plus a bounded number of fix cycles; do not use a limit smaller than the normal workflow.
- Use bounded retries with backoff for transient failures only.
- Stop on passed gate, negligible improvement, exhausted budget, cancellation, or required user input.
- After three fix cycles, do not imply success: deliver an explicit not-passed QA report with unresolved defects.

## Checkpoints and recovery
- Create checkpoints before source-changing stages.
- Keep diffs inspectable and associate them with a source revision.
- Make rollback available for generated workspace changes.
- Resume from durable state where safe; do not repeat costly or destructive actions blindly.
- Require user approval for destructive, external, credential-sensitive, or unusually costly actions.

## Testing
Test transition validity, retries, budget exhaustion, cancellation, resume/recovery, stage contract validation, and QA-to-fix handoffs. Include integration tests for a complete bounded Create and Review path with provider calls mocked.
