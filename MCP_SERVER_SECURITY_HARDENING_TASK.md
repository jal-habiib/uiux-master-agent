# MCP Server Security Hardening Task

Work ONLY in `mcp-server/`. Do not modify the S7 implementation, root app, root requirements, data/, output/, temp_uploads/, .env files, or secrets. Do not commit or push.

## Objective
Harden the local UI/UX Agent MCP server before it is used as a trusted development bridge. Existing 9 tests are insufficient. Preserve the official MCP SDK, stdio + loopback-only Streamable HTTP, and existing tool names where practical.

## Required changes

1. Remove the unsafe generic Copilot execution path. `run_project_command` must NOT launch Copilot CLI at all. Do not allow arbitrary Copilot flags/prompts/task execution through this generic tool. If Copilot integration cannot be made narrowly safe, document that Copilot CLI must be launched separately by the human/operator and is not exposed as an MCP command.
2. Replace permissive Git argument parsing with exact allowlisted command grammars. Reject Git global options such as -c, --config-env, --git-dir, --work-tree, --exec-path, --upload-pack, --receive-pack, --no-pager, external diff, pager/config overrides, and unknown options. Allow only safe read-only forms needed for status, diff, log, branch, show, check-ignore. Validate all positional arguments and prevent option injection. Keep dedicated commit/push tools separate.
3. Restrict Python execution to exact safe forms only: --version/-V; `-m unittest discover -s tests -v`; `-m compileall -q src tests` (and optionally root-safe equivalent if needed). Reject arbitrary module names, test paths, script paths, extra args, `-c`, and interpreter flags. If this is too restrictive, remove Python from generic command execution and document manual test execution.
4. Bound subprocess output in memory, not merely after `communicate()`. Implement bounded capture that terminates the process once output exceeds the configured limit, and kill the process tree on timeout where the platform supports it (Windows process group/taskkill; POSIX process group). Return an explicit truncated/limit_exceeded/timed_out status. Never inherit shell execution. Handle launch failures safely.
5. Harden filesystem paths. Check Windows reparse points/junctions as well as symlinks. Do not create parent directories before validating all existing ancestors. Re-check before replace. Do not claim race-proof guarantees unless implemented with OS-level handle-relative operations. If fully safe portable atomic writes cannot be guaranteed, narrow writes to a single dedicated workspace directory and explicitly document the residual local race limitation. Reads must open safely and reject reparse/symlink components.
6. Expand sensitive path exclusions: .env variants, .npmrc, .pypirc, .netrc, .ssh, .aws, credentials, tokens, private keys, certificates, common cloud credential directories/files. Avoid overblocking ordinary source files.
7. Fix Git commit: require staged changes to exist (cached diff exit code exactly 1), reject all unstaged/untracked files, inspect staged paths and reject sensitive/excluded paths, don't stage anything, and use explicit exact confirmation. Consider hook execution risk and document it. Never commit if prechecks fail.
8. Fix Git push: require clean tree, non-detached branch, configured upstream, report exact branch/upstream/commit SHA and require confirmation bound to those exact values (e.g. a phrase including branch and SHA). Push explicit remote + HEAD:upstream-branch, never force. Do not use generic push args.
9. Add adversarial tests for Git global option injection, arbitrary Python modules/paths, all Copilot execution attempts, oversized real subprocess output, timeout/process cleanup, Windows reparse point handling (platform-conditional), nested symlink writes, empty staged commit, sensitive staged path, detached/no-upstream push, exact branch/SHA confirmation, and MCP tool registration.
10. Update README security claims to accurately describe implemented protections and residual limitations. Do not claim "never follows symlinks" unless the implementation really proves it.
11. Add a protocol smoke test using the official MCP Python SDK stdio client: launch server, list tools, call health_check, verify tool names, and shut down cleanly. Do not expose credentials or environment contents.
12. Ignore generated `*.egg-info/`, `__pycache__/`, bytecode, build/dist artifacts in mcp-server/.gitignore. Do not include generated artifacts.

## Verification
- Run all unittest tests from mcp-server.
- Run compileall.
- Run git diff --check at repository root.
- Run the stdio MCP protocol smoke test.
- Report exact test counts and limitations.
- No commit or push.

Be conservative. Security must be enforced in code, not prompts. Do not add dependencies beyond the existing MCP SDK and Python standard library.