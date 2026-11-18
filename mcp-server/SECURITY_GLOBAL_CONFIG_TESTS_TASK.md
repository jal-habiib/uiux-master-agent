# MCP security test correction: prove real global config is ignored

Work only in mcp-server/. Do not modify files outside mcp-server/. Do not stage, commit, or push. Preserve all existing work.

## Why this follow-up is needed
The current global-hooks test sets a repository-local core.hooksPath. Repository-local config takes precedence over global config, so that test does not prove the global hooksPath is ignored. The current global-config tests also set GIT_CONFIG_GLOBAL explicitly to a temporary file; add coverage for the normal user config locations selected through HOME/USERPROFILE/XDG_CONFIG_HOME.

## Required test changes
1. Update tests in tests/test_server.py. Keep production code unchanged unless a test demonstrates an actual defect.
2. Add an integration test that creates hostile config in normal user config locations:
   - HOME/.gitconfig
   - USERPROFILE/.gitconfig (where applicable)
   - XDG_CONFIG_HOME/git/config
   Do not set GIT_CONFIG_GLOBAL in this test. Invoke the MCP server's own git status/diff/commit paths under the temporary environment. Prove the hostile config is not loaded. Do not touch the real user's configuration.
3. Correct the global hooks test:
   - Do NOT configure repository-local core.hooksPath.
   - Put a benign marker-writing pre-commit hook in the repository's default .git/hooks directory.
   - Put a hostile marker-writing pre-commit hook in a temporary global hooks directory, selected only by temporary HOME/USERPROFILE global config.
   - Run git_commit_preview and git_commit through ProjectSecurity.
   - Prove the local repository hook executes, the global hook does not, and the commit succeeds.
   - Keep platform-aware behavior and explain any genuine platform limitation; do not silently skip a substantive assertion after it has already run.
4. Strengthen the global textconv test:
   - Create a temporary executable/script that writes a marker.
   - Add and commit a .gitattributes entry selecting a custom diff driver for the test file.
   - Put the matching diff.<driver>.textconv setting in the normal temporary global config.
   - Change the tracked test file and run ProjectSecurity.git_diff().
   - Prove the marker is not created.
5. Keep or improve external diff coverage using a hostile global diff.external setting and verify no marker is created.
6. For core.pager, retain an explicit assertion that generated read-only Git argv includes --no-pager. A non-TTY captured stdout may not invoke a pager naturally, so do not claim a pager integration test proves more than it does.
7. Avoid assertions that pass merely because Git didn't need to invoke a tool; tests must establish the relevant configuration was present in the temporary global config and that the security runner overrode it.
8. Preserve all existing security behavior and other tests.

## Verification
From mcp-server/:
- python -m unittest discover -s tests -v
- python -m compileall -q src tests

From repository root:
- git diff --check

Do not stage, commit, or push.

## Report
List updated tests, results, skips, and limitations. Make clear if the tests verify configuration isolation through normal HOME/USERPROFILE/XDG lookup.
