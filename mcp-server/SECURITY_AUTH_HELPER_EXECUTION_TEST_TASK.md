# Security follow-up: prove the approved Git credential helper is actually invoked

## Objective
Add a focused regression test proving that Git can discover and invoke the explicitly approved fake credential helper (manager) through Git's real credential-helper protocol. The existing test only checks generated argv/PATH; it does not prove helper execution.

## Scope
- Modify only mcp-server/tests/test_server.py (and, only if genuinely needed, add a small test fixture under mcp-server/tests/).
- Do not modify production server code, README, project configuration, application code, or any existing task files.
- Do not read, print, copy, or use real credentials.
- Do not make network requests, contact a remote, run git push, or change repository state.
- Do not commit or push.

## Required test behavior
1. Create a temporary directory and an isolated temporary Git repository/configuration for the test.
2. Provide a fake executable discoverable by Git as git-credential-manager (the helper selected by credential.helper=manager). Make the fixture work on the project's Windows environment and remain portable where practical. If platform-specific launchers are required, keep them inside the temporary directory and clean them up.
3. The fake helper must implement the Git credential-helper protocol:
   - On get, record that it was invoked (record only the action name; do not log credential material), then return deterministic dummy username/password fields followed by a blank line.
   - If store or erase is invoked, record only the action name and exit safely.
   - Reject or safely ignore unexpected input; never echo received credential fields into logs or test output.
4. Invoke Git's actual credential plumbing in a subprocess, using an explicit -c credential.helper=manager setting and a controlled environment whose PATH includes the fake helper directory. Use no user/global/system Git configuration. Do not invoke push or any network operation.
5. Capture stdout/stderr and helper log internally. Assert:
   - Git exits successfully.
   - The fake helper's get action was actually invoked.
   - Git received the deterministic dummy credential values from the helper (assert in memory only; never include the values in assertion messages, logs, or output).
   - The helper log contains only expected action names and no credential data.
6. Also retain/assert the production security behavior: the server's approved-helper setting adds -c credential.helper=manager to a push argv only; it does not add the helper to read-only or commit argv. Reuse existing tests/helpers where possible rather than duplicating fragile setup.
7. Ensure the test is deterministic, self-contained, cleans temporary files, and does not depend on an installed real Git Credential Manager. If the host Git cannot run the fake helper on a platform, make the test fail with a clear platform/setup reason rather than silently passing without executing the helper.

## Verification
From mcp-server, run:
- python -m unittest discover -s tests -v
- python -m compileall -q src tests

Report the exact test count and results. Do not commit or push. Do not modify files outside the stated scope.