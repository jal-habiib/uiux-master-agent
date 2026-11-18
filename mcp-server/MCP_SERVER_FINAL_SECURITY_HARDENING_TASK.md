# MCP final security hardening follow-up

## Goal
Fix the remaining path-boundary issues found during independent review and make the credential-helper execution test mirror production helper discovery. Preserve the existing architecture and security model.

## Strict scope
- Production changes: mcp-server/src/uiux_agent_mcp/server.py only.
- Tests: mcp-server/tests/test_server.py only.
- Documentation: mcp-server/README.md only if a small, necessary correction is made to the Copilot Python interpreter example; do not guess a machine-specific Python path. Prefer documenting creation/use of a dedicated mcp-server virtual environment if the current example points at an environment that does not install this package.
- Do not change application/S7 files, skills, root README, other MCP files, dependencies, or project configuration.
- Do not read or print .env files or secrets.
- Do not commit or push.

## Required fixes
1. Project root validation:
   - Validate the originally supplied root path before resolving it.
   - Reject a root that is itself a symlink or Windows reparse point.
   - Also reject a root whose path traverses symlink/reparse-point components, including parent components where practical.
   - Keep the resolved, pinned root only after validation.
   - Fail closed with a clear ValueError or PermissionError.
   - Add tests for a final-component symlink and, where the platform permits, a symlink/junction in a parent component. Skip only when the OS genuinely does not permit creating the link.
2. Case-insensitive excluded-directory enforcement:
   - Normalize/casefold path components before checking EXCLUDED_DIRS in read/write path validation, directory listing filters, and staged-path validation.
   - Ensure variants such as .GIT, DATA, OUTPUT, TEMP_UPLOADS, NODE_MODULES, and .VENV cannot bypass the policy on Windows.
   - Add regression tests for mixed-case variants in read/list/write and staged-path checks where feasible.
3. Credential helper test realism:
   - In test_git_invokes_approved_fake_credential_helper_protocol, stop using Git's --exec-path override to locate the fake helper.
   - Keep the fake helper directory on the controlled child PATH, alongside the resolved Git executable directory.
   - Invoke Git credential fill with -c credential.helper=manager and verify Git discovers and runs the fake helper through PATH exactly as production would.
   - Retain the assertions that only action names are logged and dummy credentials are asserted in memory, never printed.
   - Keep the production argv assertion that credential.helper is injected only for push.

## Verification
From mcp-server run:
- python -m unittest discover -s tests -v
- python -m compileall -q src tests
- git diff --check
Report exact test count and outcomes. Do not commit or push. Do not modify files outside scope.