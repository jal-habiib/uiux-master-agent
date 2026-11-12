---
name: ui-ux-design
description: Translate product briefs and screenshots into evidence-informed UX plans, design systems, component specifications, and responsive HTML/Tailwind interfaces for the UI/UX Master Agent.
---

# UI/UX design skill

## Start with the product
Before designing, identify:
- User groups, primary user goal, and key tasks.
- Product context, business constraints, content, and required interactions.
- Information hierarchy and navigation model.
- Critical user flows and states.
- Assumptions, missing information, and risks.

Do not invent domain rules or silently fill important product gaps. Make assumptions explicit and ask when a missing answer materially changes the design.

## UX planning
Produce a concise UX plan with user goals, information architecture, primary journeys, screen inventory, interaction requirements, empty/loading/error/success states, and responsive considerations. Prioritize task completion and clarity over decoration.

## Design system
Create a coherent token set for color, typography, spacing, radius, elevation, and breakpoints. Use semantic token names and validate token references. Prefer a consistent spacing/type scale and reusable components over one-off styling.

## Interface design
- Use semantic HTML and accessible native controls where possible.
- Establish a clear visual hierarchy, readable typography, intentional whitespace, and predictable alignment.
- Use responsive CSS/Tailwind layouts that adapt fluidly; do not merely shrink desktop layouts.
- Design interaction states: default, hover, focus-visible, active, disabled, loading, empty, error, and success as applicable.
- Avoid empty links/buttons, placeholder-only form labels, decorative controls that look functional, and unlabeled icon buttons.
- Use real product content when available. Do not force a stock placeholder image source or a specific placeholder service into every design.
- Ensure mobile layouts are intentionally composed at 375px, tablet at 768px, and desktop at 1440px.

## Evidence and iteration
Visual taste is not a substitute for verification. Inspect rendered screenshots and DOM behavior. Use deterministic checks for accessibility, contrast, overflow, and functionality; use vision judgment only for subjective visual qualities. Tie each refinement to a specific observed defect and re-run the relevant checks after changing source.
