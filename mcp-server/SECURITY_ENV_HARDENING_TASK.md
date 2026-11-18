# MCP subprocess environment hardening

Work only in `mcp-server/`. Do not modify any files outside that directory. Do not stage, commit, push, or run destructive commands. Preserve all existing work, including the unfinished S7 changes elsewhere in the repository.

## Goal
Harden subprocess execution in `src/uiux_agent_mcp/server.py` against inherited environment-variable attacks without breaking normal Git authentication, Windows operation, or the existing command allowlist.

## Required implementation
1. Inspect the current subprocess implementation and tests before editing.
2. Resolve executable paths safely:
   - Use the current Python interpreter via `sys.executable` for allowed Python commands.
   - Resolve Git via `shutil.which("git")`; fail closed with a clear error if unavailable.
   - Do not accept executable paths or command fragments from user input.
3. Build a sanitized child environment for every subprocess, including Git metadata/preview commands and commit/push:
   - Remove inherited variables whose names start with `GIT_` or `PYTHON` (case-insensitive).
   - Preserve normal platform variables needed by Windows and process execution (for example SystemRoot, PATH, TEMP, TMP, USERPROFILE, HOME, and required authentication variables).
   - Set `GIT_CONFIG_NOSYSTEM=1` and `GIT_TERMINAL_PROMPT=0`.
   - Do not remove SSH-agent or credential-helper environment needed for legitimate authenticated pushes.
   - Avoid broad environment clearing that would break Windows or Git authentication.
4. For Git read-only commands, explicitly disable pagers, external diff, and text conversion where applicable (e.g. `--no-pager`, `--no-ext-diff`, `--no-textconv`). Keep existing command grammar and argument validation strict.
5. Apply the same executable and environment protections to internal Git commands used for status, staged-tree calculation, commit previews, commit revalidation, and push previews/revalidation.
6. Preserve commit hooks as currently documented; do not silently disable hooks.
7. Preserve timeout, output-limit, process-tree termination, and status reporting behavior.

## Tests
Add focused adversarial tests proving that:
- hostile inherited `GIT_*` variables cannot redirect Git config, repository, worktree, index, hooks, pager, or external diff behavior;
- hostile `PYTHON*` variables cannot alter allowed Python command execution;
- normal Windows-required environment variables are retained when present;
- authentication-related variables needed for Git push are not indiscriminately stripped;
- read-only Git commands do not invoke configured pagers/external diff/textconv;
- existing allowlist, timeout, output cap, commit confirmation, and push confirmation tests still pass.

Use temporary test repositories and subprocess environments; do not touch the real repository's Git config, index, remotes, or credentials. Tests must not print secret values.

## Verification
From `mcp-server/`, run:
- `python -m unittest discover -s tests -v`
- `python -m compileall -q src tests`

Also run `git diff --check` from the repository root, but do not stage or commit anything.

## Final report
Summarize files changed, security behavior added, tests and results, and any remaining limitations. If any requirement cannot be safely met, explain it rather than weakening validation.
