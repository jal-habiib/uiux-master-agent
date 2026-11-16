# UI/UX Agent MCP Server

This is an isolated local MCP server for inspecting and making reviewed changes to the UI/UX Master Agent repository. It uses the official Python MCP SDK and supports stdio for local clients plus Streamable HTTP for a future authenticated bridge.

## Install and run

From `mcp-server`:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m uiux_agent_mcp.server --transport stdio
.venv\Scripts\python.exe -m uiux_agent_mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
```

The project root is pinned at startup. Set `UIUX_AGENT_PROJECT_ROOT` or pass `--project-root`; the default is the repository root containing `mcp-server`. HTTP accepts loopback hosts only and is not an authenticated public server.

Git global and system configuration remain disabled for subprocess safety. If
authenticated pushes need a credential helper, opt in at launch with the
strict identifier setting below:

```powershell
$env:UIUX_AGENT_GIT_CREDENTIAL_HELPER = "manager"
.venv\Scripts\python.exe -m uiux_agent_mcp.server --transport stdio
```

Accepted identifiers are `cache`, `libsecret`, `manager`, `manager-core`,
`osxkeychain`, and `wincred`. The setting is unset by default, is applied only
to push commands, and never enables arbitrary helper commands or repository
configuration.

## Copilot CLI connection

For a local stdio connection, configure the client with the executable and arguments (adjust the absolute paths for the machine):

```json
{
  "mcpServers": {
    "uiux-agent": {
      "command": "C:\\Users\\DELL\\Desktop\\uiux-agent\\mcp-server\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "uiux_agent_mcp.server",
        "--project-root",
        "C:\\Users\\DELL\\Desktop\\uiux-agent",
        "--transport",
        "stdio"
      ],
      "cwd": "C:\\Users\\DELL\\Desktop\\uiux-agent\\mcp-server"
    }
  }
}
```

If the client supports an HTTP MCP URL, use `http://127.0.0.1:8000/mcp` after starting Streamable HTTP. ChatGPT direct connectivity is not claimed here; it requires a confirmed host/transport bridge.

The MCP server does not expose Copilot CLI execution. An operator must launch Copilot
separately, with their own approvals and policy controls.

## Security model

The server rejects symlink and Windows reparse-point components when they are observed,
reads excluded or sensitive paths, and permits filesystem writes only under
`mcp-workspace/`. Reads and writes are capped at 1 MiB. Commands use an argv list with
`shell=False`, exact grammars for read-only Git and two Python maintenance forms, a
1–120 second timeout, process-tree termination, and in-memory 20,000-character output
caps. Git metadata preflights use the same bounded runner and reject truncated output.
`git_commit_preview` validates staged-only state and returns a confirmation bound to
the branch and staged tree digest; `git_commit` recomputes those values immediately
before committing and never stages files automatically. `git_push_preview` returns a
confirmation bound to the branch, full HEAD SHA, and configured upstream;
`git_push` recomputes all of them and pushes only `HEAD:<upstream-branch>` without
force. Commit hooks remain enabled and can execute repository-local code, so a
confirmation can run untrusted hook behavior from the selected repository.
Portable Python path checks cannot provide an OS-level race-proof guarantee between
validation and replacement; the write scope is therefore limited to the dedicated
workspace. HTTP binds to loopback by default and must be placed behind authentication
before any bridge exposes it remotely.

## Tests

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src tests
git diff --check
```

The unittest discovery includes the official MCP SDK stdio protocol smoke test.
