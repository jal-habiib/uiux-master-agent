# Fix Windows credential-helper test isolation

## Finding
The revised test that relies on PATH discovery fails on this Windows host because Git resolves its bundled Credential Manager before the temporary fake helper. This is a platform helper-resolution behavior, not evidence that production code invokes the fake helper incorrectly. Do not weaken the production credential-helper policy to make the test pass.

## Scope
- Modify only mcp-server/tests/test_server.py.
- Do not change production code, docs, dependencies, or any other files.
- No real credentials, network, remote, push, commit, or repository state changes.
- Do not commit or push.

## Required change
1. Make the fake-helper protocol test deterministic by using Git's command-line --exec-path=<temporary-helper-directory> override for that test subprocess only. The test must still use the explicit -c credential.helper=manager setting and run the real git credential fill plumbing.
2. Keep the fake helper implementation and assert that Git actually invokes its get action, returns the deterministic dummy username/password to the captured subprocess output, and logs only the action name. Never print or include dummy credential values in assertion messages or logs.
3. Keep the test isolated in a temporary directory and clean up all files.
4. Add a short code comment explaining why --exec-path is used only in this test: Windows Git may prioritize its bundled helper over PATH, so the override makes the fake helper deterministic. Do not claim this tests production PATH resolution.
5. Retain the separate production assertion that ProjectSecurity injects credential.helper=manager for push only and not for read-only or commit commands.
6. Keep all newly added project-root symlink and case-insensitive excluded-directory tests unchanged.

## Verification
From mcp-server run the focused test first:
- python -m unittest test_server.ProjectSecurityTests.test_git_invokes_approved_fake_credential_helper_protocol -v
Then run:
- python -m unittest discover -s tests -v
- python -m compileall -q src tests
- git diff --check
Report exact test count and outcomes. Do not commit or push.