# MCP Git Status Stdin Isolation Fix

## Context and confirmed diagnosis

The MCP server is launched by GitHub Copilot using stdio transport. In this transport, the server process's stdin is the MCP protocol input stream.

`ProjectSecurity._run_argv` currently starts child processes with stdout/stderr pipes but does not set stdin. The child therefore inherits the server's MCP input stream.

This was reproduced through the official Python MCP stdio client:
- Calling `git_status` on the normal server stalls until the internal 30-second timeout.
- Running the same MCP server with child `subprocess.Popen` instances defaulting stdin to `subprocess.DEVNULL` returns `git_status` successfully in approximately 0.09 seconds.
- The Git command itself and `ProjectSecurity.git_status()` both complete quickly when called directly.

This is the confirmed fix direction. Do not change Git settings, credential configuration, command grammar, timeout, or Copilot permissions.

## Required changes

Modify only:
1. `mcp-server/src/uiux_agent_mcp/server.py`
2. `mcp-server/tests/test_protocol_smoke.py`

### Production change

In `ProjectSecurity._run_argv`, add this to the `subprocess.Popen` keyword arguments:

`"stdin": subprocess.DEVNULL,`

Keep `shell=False`, existing pinned `cwd`, sanitized child environment, stdout/stderr capture and bounds, timeout and process-tree termination logic, and all existing Git command restrictions and credential-helper isolation.

The purpose is to ensure child processes cannot consume the parent's MCP stdio protocol input.

Do not alter `_terminate` or other subprocess invocations unless tests demonstrate a concrete need.

### Regression test

Extend the existing stdio protocol smoke test to call the `git_status` MCP tool in the same initialized `ClientSession`, after the health check.

Assert:
- The tool result is not an MCP error.
- The returned JSON object has `status == "completed"`.
- `returncode == 0`.
- `timed_out` is false.
- `stdout` contains a non-empty Git status response.

Use the actual MCP stdio client and actual server process as the regression test. Do not mock the subprocess or bypass the protocol.

Parse the tool's JSON text using `json.loads`; fail clearly if the result shape is unexpected.

## Verification

From `mcp-server`, using the dedicated interpreter:
1. Focused protocol smoke test: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_protocol_smoke.py -v`
2. Full suite: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
3. Compile: `.venv\Scripts\python.exe -m compileall -q src tests`
4. `git diff --check`

Also verify the `git_status` tool through an MCP stdio client and report the returned status and elapsed time.

## Boundaries

- Do not modify README, pyproject, `.gitignore`, Copilot configuration, or any S7/application files.
- Do not change the seven-tool Copilot allowlist.
- Do not use Git credential helpers or initiate authentication.
- Do not stage, commit, or push.
- Preserve all existing work and report exact files changed and test outcomes.