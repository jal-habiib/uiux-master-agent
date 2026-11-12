---
name: python-engineering
description: Implement, refactor, debug, and test Python code in the UI/UX Master Agent repository using safe, typed, maintainable patterns. Use for Python changes, bug fixes, tests, dependencies, or code review.
---

# Python engineering skill

## Before editing
- Inspect the target module, its callers, related tools, configuration, and tests.
- Confirm current behavior rather than relying on old review notes.
- Identify public interfaces, persisted data formats, side effects, and failure modes.
- Make the smallest change that solves the stated problem. Preserve working behavior.

## Implementation standards
- Prefer clear, small functions with explicit inputs and outputs.
- Add type hints to public functions and nontrivial data structures.
- Validate untrusted input at boundaries; do not rely on prompt instructions for validation.
- Raise or return actionable errors; do not swallow exceptions or disguise failure as success.
- Use standard logging with useful context, but never log API keys, tokens, credentials, or sensitive user content.
- Keep provider calls, orchestration, UI code, persistence, and side effects separated according to existing project boundaries.
- Avoid introducing dependencies for functionality already supported by the standard library or current stack.
- Pin or constrain dependencies deliberately and document any required external runtime setup.
- Do not add a framework or abstraction solely for hypothetical future use.

## Filesystem and data
- Resolve paths against the configured project workspace and verify the resolved path remains inside it.
- Consider traversal, absolute paths, symlinks, alternate separators, and platform-specific path behavior.
- Use atomic writes or temp-file-and-replace where practical for important artifacts.
- Preserve existing generated output, chat history, notes, vector stores, and user files unless the task explicitly targets them.
- Do not read or expose sensitive files. Keep secrets in environment/configuration stores and redact them from diagnostics.
- Make persistence changes backward-compatible or provide a tested migration.

## Testing
- Add focused unit tests for new behavior and regression tests for fixed defects.
- Test boundary and failure cases, not only the happy path.
- Mock external providers and network dependencies in unit tests.
- Keep browser/integration tests distinct from fast unit tests.
- Run the relevant test command and report exact results. If no test suite exists, say so and add the smallest useful test foundation when requested.
- Never claim lint, type checking, or tests passed unless the command actually ran successfully.

## Review checklist
Check correctness, security, path safety, error handling, timeouts, resource bounds, test coverage, backward compatibility, logging hygiene, and whether the change creates duplicate sources of truth.
