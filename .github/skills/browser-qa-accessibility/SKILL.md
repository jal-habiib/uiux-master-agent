---
name: browser-qa-accessibility
description: Verify generated interfaces with Playwright, screenshots, browser console/network evidence, responsive checks, axe-core, WCAG contrast, keyboard interaction, and required user-flow tests.
---

# Browser QA and accessibility skill

## Evidence rules
- A check that did not run is **NOT VERIFIED**, never PASS.
- Every QA result must identify the exact source revision tested, browser/environment, viewport, check, result, and evidence location.
- Separate deterministic pass/fail checks from subjective visual review.
- QA reports findings and fix tasks; it does not directly edit the source under test.

## Required viewport coverage
Test at 375px, 768px, and 1440px. At each viewport:
- Capture screenshots when rendering succeeds.
- Check horizontal overflow, clipping, overlap, and content visibility.
- Inspect browser console errors and failed network requests.
- Exercise required interactions and key responsive navigation.

## Accessibility hard gates
- axe-core: zero critical and serious violations.
- WCAG AA contrast: 4.5:1 for normal text; 3:1 for large text and relevant UI components.
- Images have meaningful alt text or intentional empty alt for decorative images.
- Form controls have programmatic labels and useful error associations.
- Interactive controls have accessible names; no empty links or buttons.
- Mobile touch targets are at least 44px where applicable.
- Keyboard focus order is logical, visible, and reaches all interactive controls.
- Verify semantics, headings, landmarks, dialogs, and form behavior.

## Functional checks
Translate brief requirements into Playwright assertions for navigation, forms, dialogs, and other required flows. Test success, validation errors, disabled/loading states, and relevant edge cases. Do not infer functionality from screenshots alone.

## Gate and score
All hard gates must pass. The seven-part soft score totals 100:
- Visual hierarchy: 20
- Spacing/rhythm: 15
- Responsive layout: 15
- Design-system consistency: 15
- State coverage: 15
- Typography: 10
- Code quality/performance: 10

A delivery passes only when every hard gate passes and the soft score is at least 80. A high visual score cannot override a hard-gate failure. If a required tool/dependency is unavailable, report the affected check as NOT VERIFIED and explain why. After three fix cycles, produce a truthful not-passed report rather than claiming success.

## Test hygiene
Use isolated test fixtures and deterministic test data. Do not run untrusted generated code outside the intended browser/test isolation. Preserve screenshots and reports as evidence, linked to the tested source revision.
