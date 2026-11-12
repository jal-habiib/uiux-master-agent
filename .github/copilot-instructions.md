# UI/UX Master Agent — Shared Project Instructions

These instructions are the repository-wide baseline for every coding assistant working in this project. Read the relevant skill in `.github/skills/` before substantial work. Keep this file concise; specialist procedures belong in skills.

## Source of truth
- Follow the **UI/UX Master Agent — Unified Master Document (November 12, 2026)** as the product and architecture authority.
- If a decision is not covered there, state the assumption and ask before making an irreversible architectural choice.
- Preserve existing working code, generated outputs, user data, and project history. Inspect before changing.
- Do not silently replace the agreed architecture with a generic agent framework or rewrite the application.

## Product contract
- Build a local developer tool that turns a product brief or screenshot into a verified, responsive, accessible HTML/Tailwind interface.
- Initial modes: Create and Review. Improve follows only after workspace isolation, diffs, and rollback are reliable. Explore is later.
- Streamlit remains the UI for now; SQLite is the local job/state store; use a custom staged state machine unless evidence justifies another framework.
- Specialist roles are modules with versioned contracts, not separate services by default.
- Prioritize a measurable vertical slice and benchmark evidence over adding features or premature modularization.

## Engineering invariants
1. Enforce critical requirements in code, not prompts alone.
2. Evidence outranks model opinion. A check that did not run is **NOT VERIFIED**, never PASS.
3. Keep creation separate from evaluation. QA may request a fix but must not silently edit source.
4. Use one source of truth per concern; avoid duplicate memory or competing rubrics.
5. Route by declared model capability (text, vision, tools, structured output), not provider name.
6. Make stages small, typed, versioned, observable, and bounded.
7. Make file operations safe, workspace-scoped, checkpointed, reviewable, and reversible.
8. Require human approval for destructive, external, credential-sensitive, or costly actions.

## Working protocol
- Before editing: inspect relevant files, Git status, current behavior, and tests; identify the smallest safe change.
- Work on a feature branch when possible. Never reset, force-push, bulk-delete, or overwrite user artifacts without explicit approval.
- Implement one vertical slice at a time. Avoid unrelated cleanup.
- Add or update tests with behavior changes. Run the narrowest relevant checks, then broader checks when feasible.
- Report exact files changed, commands run, test results, unverified items, and remaining risks. Never claim a test passed unless it ran and passed.
- Do not expose, read, copy, or log secrets. Keep sensitive files protected.
- Do not add unrestricted shell permissions to skills. Skills must not pre-approve shell tools.

## Quality gates
Hard gates cannot be compensated for by a visual score. Required checks include valid build/render, no fatal runtime or console/network errors, no horizontal overflow at 375/768/1440px, zero critical/serious axe-core findings, WCAG AA contrast, valid image alt text and form labels, no empty interactive controls, 44px minimum mobile touch targets, sensible keyboard focus, and required user flows passing in Playwright. If a check is not implemented or cannot run, report NOT VERIFIED.

The soft score is 100 points: visual hierarchy 20, spacing/rhythm 15, responsive layout 15, design-system consistency 15, state coverage 15, typography 10, code quality/performance 10. Delivery requires all hard gates and at least 80/100; after three fix cycles, deliver only with an explicit not-passed QA report.

## Relevant skills
- `project-architecture`: product scope, architecture, phases, contracts, sequencing.
- `python-engineering`: Python implementation and tests.
- `agent-orchestration`: stage machine, budgets, checkpoints, job state.
- `llm-provider-routing`: provider registry, capabilities, fallback and resilience.
- `ui-ux-design`: product/UX planning, design systems and interface implementation.
- `browser-qa-accessibility`: Playwright, axe-core, responsive and evidence-based QA.
- `security-review`: workspace safety, approvals, prompt injection and secrets.
