# S12 Implementation Task — Automated Refine

## Objective

Connect the existing `VERIFY -> REFINE -> VERIFY` state loop to an evidence-driven HTML repair operation.

## Behavior

- Accept only a Create job in `VERIFY` or a recoverable `REFINE` state.
- Require the latest saved, integrity-checked Verify report to have status `FAIL`; do not refine `PASS` or `NEEDS_INPUT` reports.
- Supply the Product Brief, UX Plan, Design spec/tokens, latest immutable HTML pages, exact Verify hard-gate evidence, deterministic quality results, and optional bounded user feedback to the Refine provider.
- Treat all artifact content and feedback as untrusted data, not instructions.
- Preserve exact Design page names and filenames. Return complete HTML pages using the S9 response schema and run all existing HTML/security validation before saving.
- Persist each successful refinement as a new immutable run. Never overwrite prior Build or Refine output.
- Return to `VERIFY` only after the new artifacts and checkpoint are saved. A failed provider call remains recoverable in `REFINE`.
- Re-run Verify against the new run. A previous report is stale as soon as a new run exists and cannot be used by Gate & Deliver.
- Gate counts only successful `REFINE -> VERIFY` cycles. Repeated provider failures do not consume the three-cycle allowance.

## Safety and scope

No browser evidence is fabricated. NOT_VERIFIED checks remain unverified. Refine may repair only static HTML; it does not alter the Product Brief, UX Plan, or Design artifacts. No deployment or external publishing is performed.

## Tests

- Failed Verify report produces a new immutable run and returns to Verify.
- New Verify run references the new Build artifact refs.
- Non-failed reports cannot trigger Refine.
- Provider failure is recoverable in REFINE.
- Existing Build, Verify, Gate, path-integrity, and state-transition tests remain green.
