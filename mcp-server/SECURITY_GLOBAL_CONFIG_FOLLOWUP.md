# MCP follow-up: isolate Git global configuration

This is a narrow follow-up to SECURITY_ENV_HARDENING_TASK.md. Work only in mcp-server/. Do not modify files outside mcp-server/. Do not stage, commit, push, or run destructive commands. Preserve all existing project work.

## Review finding
The child environment removes inherited GIT_* variables and sets GIT_CONFIG_NOSYSTEM=1, but it does not disable Git's per-user global configuration file. Git can still read configuration from HOME/USERPROFILE/XDG locations. That means a user-level core.hooksPath, pager, diff, or other setting may affect operations despite the environment sanitization.

## Required fix
1. Inspect the current _child_environment implementation and Git subprocess paths.
2. Ensure Git subprocesses do not load user/global Git configuration, in a cross-platform way supported by the installed Git. Prefer Git's documented GIT_CONFIG_GLOBAL mechanism pointing to the platform null device (os.devnull), while retaining GIT_CONFIG_NOSYSTEM=1 and GIT_TERMINAL_PROMPT=0. Confirm behavior on Windows; do not assume /dev/null.
3. Keep authentication working: preserve SSH agent and credential-manager environment variables and do not disable credential helpers wholesale.
4. Do not disable repository-local commit hooks. Commit hooks must remain enabled as documented. This fix is only to prevent inherited user/global configuration from silently redirecting behavior.
5. Make sure all internal Git commands (metadata, preview, commit, push) receive the same sanitized environment.
6. Keep the exact public command allowlist and confirmation requirements unchanged.

## Tests
Add integration tests using temporary HOME/USERPROFILE/XDG config locations and temporary repositories to prove:
- a hostile global core.pager setting cannot run a custom executable;
- a hostile global diff.external or textconv setting cannot run a custom executable during read-only diff/show paths;
- a hostile global core.hooksPath setting cannot redirect commit hooks;
- authentication variables such as SSH_AUTH_SOCK and GCM_* remain available;
- existing repository-local hooks remain enabled if the current behavior supports them (document any platform-specific test limitation).
Do not read, print, or modify the real user's Git config, credentials, or repository hooks. Use temporary files and marker files inside the test temp directory.

## Verification
From mcp-server/ run:
- python -m unittest discover -s tests -v
- python -m compileall -q src tests

From repository root run git diff --check. Do not stage or commit anything.

## Report
List files changed, tests and results, and any remaining platform limitations. Do not claim complete isolation if any test is skipped or unsupported.
