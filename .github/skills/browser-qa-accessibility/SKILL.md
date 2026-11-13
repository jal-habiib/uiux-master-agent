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
- A concrete defect observed by a completed check is FAIL. Missing dependencies, browser launch/navigation timeouts, unsupported operations, or incomplete evidence are NOT VERIFIED.

## Required viewport coverage
Test at 375px, 768px, and 1440px. At each viewport:
- Capture screenshots when rendering succeeds.
- Check horizontal overflow, clipping, overlap, and content visibility.
- Inspect browser console errors and failed network requests.
- Exercise required interactions and key responsive navigation.
- Do not report a viewport-dependent gate as PASS unless all three required viewports were actually checked.

## Accessibility hard gates
- axe-core: zero critical and serious violations.
- WCAG AA contrast: 4.5:1 for normal text; 3:1 for large text and relevant UI components.
- Treat axe incomplete contrast analysis as NOT VERIFIED unless a concrete contrast violation already proves FAIL.
- Images have meaningful alt text or intentional empty alt for decorative images.
- Form controls have programmatic labels and useful error associations.
- Interactive controls have accessible names; no empty links or buttons.
- Mobile touch targets are at least 44px where applicable; check visible, enabled controls.
- Keyboard focus order is logical, visible, and reaches all interactive controls. Positive tabindex is a failure; compare focused element identity/order, not tag names alone.
- Verify semantics, headings, landmarks, dialogs, and form behavior.

## Functional checks
Translate brief requirements into Playwright assertions for navigation, forms, dialogs, and other required flows. Test success, validation errors, disabled/loading states, and relevant edge cases. Do not infer functionality from screenshots alone.

The `verify_html(filename, flows_json)` tool checks a safe HTML filename inside the output directory. Required flows use a JSON object with a non-empty `flows` array; each flow has a non-empty `name` and non-empty `steps`. Supported actions are `click`, `check`, `uncheck`, `expect_visible`, and `expect_hidden` with exactly `action` and `selector`; `fill`, `select_option`, `press`, and `expect_text` additionally require a string `value`. Unknown fields/actions are rejected before any step executes. Missing flow specifications are NOT VERIFIED, not PASS. The tool runs the locally installed axe-core bundle; do not substitute a CDN.

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
