---
name: project-architecture
description: Apply the UI/UX Master Agent's agreed product scope, architecture, phased roadmap, stage contracts, and sequencing. Use before planning features, changing architecture, restructuring modules, or deciding what to build next.
---

# Project architecture skill

## Authority and scope
Treat the **UI/UX Master Agent — Unified Master Document (November 12, 2026)** as the governing blueprint. Repository-wide constraints are summarized in `.github/copilot-instructions.md`. If the full blueprint is not available in the current assistant context, do not invent missing decisions; identify the gap and ask for the document or decision.

The product is a local developer tool that converts a product brief or screenshot into a verified HTML/Tailwind interface. The promise is usable, responsive, accessible, maintainable output backed by evidence—not merely attractive mockups.

## Product boundaries
- Build Create and Review first.
- Add Improve only after workspace isolation, diffs, and rollback are reliable.
- Explore is a later mode.
- Do not build a Figma replacement, unattended production deployment, arbitrary-framework generator, multi-tenant SaaS billing, or a claim of guaranteed design judgment.
- Initial target is the author's local-market application work; do not generalize the product prematurely.

## Agreed architecture
- Use a custom staged state machine; consider LangGraph only if real branching complexity warrants it.
- Keep specialist roles as modules inside stages. Separate worker processes/services require benchmark evidence.
- Keep Streamlit as the application UI for now.
- Use SQLite for local jobs/state; PostgreSQL is a later multi-user option.
- Target HTML + Tailwind output.
- Keep the existing flat structure during early phases. Do not prematurely reorganize into the eventual package architecture.
- Separate responsibilities: agents reason; tools perform controlled side effects; orchestrator owns sequencing/state/budgets; model gateway owns providers/capabilities; quality owns deterministic checks/evidence; security owns permissions and path policy; schemas own contracts; prompts contain role instructions.

## Workflow model
The intended Create pipeline is Intake → Plan → Design → Build → Verify → Gate & Deliver. Review skips Plan/Design/Build. Stages exchange versioned structured results and persist artifacts. QA identifies defects and creates fix tasks; it does not directly edit generated source.

Job states must be explicit and auditable. Preserve transition timestamp, actor, reason, and artifact references. Active jobs can be cancelled. Waiting for user input and blocked/failed/delivered outcomes must be distinguishable.

## Sequencing
- Revalidate the live repository before acting on historical assessments.
- Complete the first-sprint corrections before modularization: clean prompts/rubric; capability-aware vision routing; code-enforced gate; adequate bounded iteration; Playwright/axe-core dependencies and setup; foundational tests; complete README and license decision.
- Park unused tools until a real stage adopts them.
- Run the 30-brief benchmark (single-shot baseline versus full loop) before expanding capabilities or modularizing.
- Phase 3–5 work proceeds only after the stated benchmark gate is met.

## Change proposal checklist
Before an architectural change, state:
1. Which documented requirement it serves.
2. The smallest change that satisfies it.
3. Existing files and behavior affected.
4. Data/artifact migration or compatibility impact.
5. Tests and evidence required.
6. Rollback path.

Prefer a narrow, reversible vertical slice. Record unresolved decisions instead of silently deciding them.
