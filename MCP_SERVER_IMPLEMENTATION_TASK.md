# Build Task: Dedicated UI/UX Agent MCP Server

## Objective
Create an independently testable Python MCP server in the new `mcp-server/` directory. It is development infrastructure for the existing repository, not part of the UI/UX Agent runtime. Preserve every existing file and all current uncommitted S7 work.

## Repository constraints
- Repository root: C:\\Users\\DELL\\Desktop\\uiux-agent
- Do not modify or delete existing S7 files or unrelated project files.
- Do not read, print, copy, or expose .env files, API keys, credentials, tokens, or private keys.
- Do not modify contents of data/, output/, or temp_uploads/.
- Do not commit or push anything.
- Python 3.12 is available on Windows.
- Keep MCP implementation isolated in mcp-server/.

## Architecture
- Use the official Python MCP SDK package (PyPI package: mcp) and FastMCP.
- Support both stdio transport (for local MCP clients such as Copilot CLI) and Streamable HTTP transport (for a future authenticated host/bridge).
- HTTP must bind to 127.0.0.1 by default, never 0.0.0.0. No public tunnel or unauthenticated remote exposure.
- Make project root configurable only through a CLI argument or environment variable, defaulting to the repository root (parent of mcp-server). Resolve and pin it at startup.
- Python 3.12 compatible. Include pyproject.toml with bounded dependency range mcp>=1.9,<2, README, .gitignore if needed, and tests using stdlib unittest (avoid adding pytest dependency).
- No changes to root requirements.txt.

## Tools to implement
1. health_check: return server version, transport-independent status, resolved project root, and supported tool names. Never return environment variables or secrets.
2. list_project_files(path="."): list entries under root, bounded result count, skip .git, .venv, venv, node_modules, __pycache__, data, output, temp_uploads, and sensitive filenames. Do not follow symlinks.
3. read_project_file(path): UTF-8 only, regular files only, maximum 1 MiB, reject sensitive names and excluded directories, enforce containment and reject symlink components.
4. write_project_file(path, content): only within a dedicated `mcp-workspace/` directory under project root (create it if needed); UTF-8, max 1 MiB, atomic replace, reject symlink components. Do not permit writes elsewhere in repository.
5. run_project_command(command, args=[]): allow only explicit executable names python, git, copilot. Use subprocess with an argv list, never shell=True. Enforce working directory inside project root. Bound timeout (1..120 seconds) and output (max 20,000 chars each stream). Never permit command strings, shell operators, pipes, redirection, or arbitrary executables. Validate args and block obvious secret-reading commands/paths and destructive operations (git reset --hard, clean, checkout, push, credential helpers, etc.). For git, allow only safe read commands initially (status, diff, log, branch, show, check-ignore). For python, allow test/compile/version commands only; no arbitrary -c execution. For copilot, permit only CLI version/help and task execution scoped to repository; task execution must use non-interactive prompt mode and avoid --allow-all-paths, --allow-all-urls, --yolo. Require explicit caller-provided prompt and run with cwd=root. Do not pass env secrets in tool outputs.
6. git_status: safe wrapper around git status --short --branch.
7. git_diff: safe wrapper around git diff -- (optional staged mode) with output cap.
8. git_commit(message, confirmation): only if confirmation exactly equals `COMMIT VERIFIED CHANGES`; require non-empty message; refuse if any unstaged or untracked files exist; only commit staged files. Never stage automatically.
9. git_push(confirmation): only if confirmation exactly equals `PUSH VERIFIED COMMIT`; refuse if working tree dirty; push current branch to its configured upstream. Do not force push.

Important: no tool may access paths outside root, follow symlinks, expose secrets, or use shell=True. Keep errors concise and non-sensitive. Use typed input schemas/descriptions for MCP tools.

## Testing
- Unit tests for path traversal, absolute paths outside root, symlink escapes, excluded directories, sensitive files, size limits, write-only workspace restriction, atomic writes, executable allowlist, command validation, timeout/output bounds, and commit/push confirmation and safety checks.
- Tests must use temporary directories and mocks; never mutate the real repository, invoke Copilot, or perform real Git commits/pushes.
- Add a smoke test or documented commands for starting stdio and local Streamable HTTP.
- Run tests with python -m unittest discover -s tests -v from mcp-server.
- Run compileall and git diff --check on new files.
- If SDK is unavailable, report dependency installation command; do not silently skip all tests.

## Deliverables
- mcp-server/pyproject.toml
- mcp-server/src/uiux_agent_mcp/ package
- mcp-server/tests/
- mcp-server/README.md
- A concise security model and exact client connection examples for Copilot CLI. Do not claim ChatGPT can connect directly until its host/transport path is confirmed.
- At completion, report files changed, tests run/results, and known limitations.

## Work protocol
Inspect existing repository conventions only as needed. Implement only within mcp-server/. Do not alter S7. Do not commit or push.
