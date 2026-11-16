from __future__ import annotations

import argparse
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from . import __version__

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # Keep the security core importable before installation.
    FastMCP = None  # type: ignore[assignment,misc]


MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_ENTRIES = 500
MAX_OUTPUT_CHARS = 20_000
DEFAULT_TIMEOUT = 30
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "data",
    "output",
    "temp_uploads",
}
EXCLUDED_DIRS_CASEFOLD = frozenset(name.casefold() for name in EXCLUDED_DIRS)
SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".ssh",
    ".aws",
    ".azure",
    ".gcloud",
    ".docker",
    "credentials",
    "credentials.json",
    "application_default_credentials.json",
    "service-account.json",
    "credential",
    "token",
    "token.txt",
    "api_token",
    "private_key",
    "private.key",
    "server.key",
    "certificate",
    "certificates",
}
SENSITIVE_PARTS = (
    "secret",
    "password",
    "passwd",
    "private_key",
    "apikey",
    "api_key",
    "access_token",
    "id_rsa",
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer")
SHELL_CHARS = set("&;|<>`$")
GIT_READ_COMMANDS = {"status", "diff", "log", "branch", "show", "check-ignore"}
ALLOWED_GIT_CREDENTIAL_HELPERS = {
    "cache",
    "libsecret",
    "manager",
    "manager-core",
    "osxkeychain",
    "wincred",
}


def _is_sensitive(name: str) -> bool:
    lower = name.casefold()
    return (
        lower in SENSITIVE_NAMES
        or lower.startswith(".env.")
        or any(part in lower for part in SENSITIVE_PARTS)
        or lower.endswith(SENSITIVE_SUFFIXES)
    )


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse)


def _validate_project_root(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        if component in ("", "."):
            continue
        if component == "..":
            current = current.parent
            continue
        current /= component
        if _is_reparse_or_link(current):
            raise PermissionError("project root cannot traverse symlink or reparse-point paths")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("project root must be an existing regular directory") from exc
    if not resolved.is_dir():
        raise ValueError("project root must be an existing regular directory")
    return resolved


def _is_excluded_directory(name: str) -> bool:
    return name.casefold() in EXCLUDED_DIRS_CASEFOLD


class ProjectSecurity:
    """Filesystem and subprocess operations constrained to one pinned root."""

    def __init__(
        self,
        project_root: Path | str | None = None,
        credential_helper: str | None = None,
    ) -> None:
        configured = (
            project_root
            if project_root is not None
            else os.environ.get("UIUX_AGENT_PROJECT_ROOT")
        )
        default = Path(__file__).resolve().parents[3]
        if configured is None:
            self.root = default
        else:
            if not isinstance(configured, (str, Path)) or not str(configured):
                raise ValueError("project root must be an existing regular directory")
            self.root = _validate_project_root(configured)
        configured_helper = (
            os.environ.get("UIUX_AGENT_GIT_CREDENTIAL_HELPER")
            if credential_helper is None
            else credential_helper
        )
        if configured_helper == "":
            configured_helper = None
        if configured_helper is not None and configured_helper not in ALLOWED_GIT_CREDENTIAL_HELPERS:
            raise ValueError(
                "credential helper must be one of: "
                + ", ".join(sorted(ALLOWED_GIT_CREDENTIAL_HELPERS))
            )
        self.credential_helper = configured_helper

    def _parts(self, value: str, *, allow_dot: bool = True) -> tuple[str, ...]:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("path must be a non-empty string")
        if Path(value).is_absolute() or value.startswith(("\\", "/")):
            raise PermissionError("absolute paths are not allowed")
        normalized = value.replace("\\", "/")
        parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
        if not parts and not allow_dot:
            raise ValueError("path must name a file")
        if any(part == ".." for part in parts):
            raise PermissionError("path escapes project root")
        if any(_is_excluded_directory(part) or _is_sensitive(part) for part in parts):
            raise PermissionError("path is excluded")
        return parts

    def _check_components(self, path: Path) -> None:
        relative = path.relative_to(self.root)
        current = self.root
        for component in relative.parts:
            current /= component
            if _is_reparse_or_link(current):
                raise PermissionError("symlink or reparse-point paths are not allowed")

    def resolve_read_path(self, value: str) -> Path:
        parts = self._parts(value)
        path = self.root.joinpath(*parts)
        self._check_components(path)
        if path.resolve(strict=False) != path:
            raise PermissionError("path escapes project root")
        return path

    def _validate_workspace_ancestors(self, path: Path, workspace: Path) -> None:
        current = path.parent
        missing: list[Path] = []
        while not current.exists():
            missing.append(current)
            if current == self.root:
                break
            current = current.parent
        self._check_components(current)
        for item in reversed(missing):
            item.mkdir()
            if not item.is_dir() or _is_reparse_or_link(item):
                raise PermissionError("write ancestor is unsafe")
        self._check_components(path.parent)

    def resolve_write_path(self, value: str) -> Path:
        parts = self._parts(value, allow_dot=False)
        workspace = self.root / "mcp-workspace"
        path = workspace.joinpath(*parts)
        self._validate_workspace_ancestors(path, workspace)
        if path.exists() and _is_reparse_or_link(path):
            raise PermissionError("write target is a symlink or reparse point")
        if path.resolve(strict=False) != path:
            raise PermissionError("path escapes project root")
        return path

    def list_files(self, value: str = ".") -> dict[str, Any]:
        directory = self.resolve_read_path(value)
        if not directory.is_dir() or _is_reparse_or_link(directory):
            raise ValueError("path is not a regular directory")
        entries: list[str] = []
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if (
                _is_reparse_or_link(entry)
                or _is_excluded_directory(entry.name)
                or _is_sensitive(entry.name)
            ):
                continue
            entries.append(str(entry.relative_to(self.root)).replace("\\", "/"))
            if len(entries) >= MAX_LIST_ENTRIES:
                break
        return {
            "path": str(directory.relative_to(self.root)).replace("\\", "/") or ".",
            "entries": entries,
            "truncated": len(entries) == MAX_LIST_ENTRIES,
        }

    def read_file(self, value: str) -> dict[str, Any]:
        path = self.resolve_read_path(value)
        if not path.is_file() or _is_reparse_or_link(path):
            raise ValueError("path is not a regular file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file exceeds 1 MiB limit")
        try:
            with path.open("r", encoding="utf-8") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
        except UnicodeDecodeError as exc:
            raise ValueError("file is not valid UTF-8") from exc
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("file exceeds 1 MiB limit")
        return {"path": str(path.relative_to(self.root)).replace("\\", "/"), "content": content}

    def write_file(self, value: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError("content exceeds 1 MiB limit")
        path = self.resolve_write_path(value)
        # Revalidate immediately before replacement; portable Python cannot make
        # this sequence fully race-proof without OS handle-relative APIs.
        self._check_components(path.parent)
        if path.exists() and _is_reparse_or_link(path):
            raise PermissionError("write target changed to a symlink or reparse point")
        fd, temporary = tempfile.mkstemp(prefix=".mcp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_components(path.parent)
            if path.exists() and _is_reparse_or_link(path):
                raise PermissionError("write target changed to a symlink or reparse point")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": str(path.relative_to(self.root)).replace("\\", "/"), "bytes": len(encoded)}

    @staticmethod
    def _validate_args(args: Sequence[str]) -> list[str]:
        if not isinstance(args, (list, tuple)) or not all(isinstance(item, str) for item in args):
            raise ValueError("args must be a list of strings")
        for item in args:
            if "\x00" in item or any(char in item for char in SHELL_CHARS):
                raise PermissionError("shell syntax is not allowed")
            if _is_sensitive(item) or any(part in item.casefold() for part in SENSITIVE_PARTS):
                raise PermissionError("sensitive command arguments are not allowed")
        return list(args)

    @staticmethod
    def _validate_git(args: list[str]) -> None:
        if not args or args[0] not in GIT_READ_COMMANDS:
            raise PermissionError("git command is not allowed")
        command, rest = args[0], args[1:]
        forbidden = {"-c", "--config-env", "--git-dir", "--work-tree", "--exec-path",
                     "--upload-pack", "--receive-pack", "--no-pager", "--paginate",
                     "--no-replace-objects", "--literal-pathspecs", "--glob-pathspecs"}
        if any(item in forbidden or item.startswith(("-c", "--config-env=", "--git-dir=", "--work-tree=",
                                                       "--exec-path=", "--upload-pack=", "--receive-pack="))
               for item in rest):
            raise PermissionError("git global options are not allowed")
        if command == "status":
            if rest not in ([], ["--short"], ["--branch"], ["--short", "--branch"]):
                raise PermissionError("git status form is not allowed")
        elif command == "diff":
            if rest not in ([], ["--cached"], ["--"], ["--cached", "--"]):
                raise PermissionError("git diff form is not allowed")
        elif command == "log":
            if rest not in ([], ["--oneline"], ["-1"], ["--oneline", "-1"]):
                raise PermissionError("git log form is not allowed")
        elif command == "branch":
            if rest not in ([], ["--list"]):
                raise PermissionError("git branch form is not allowed")
        elif command == "show":
            if rest not in ([], ["--stat"]):
                raise PermissionError("git show form is not allowed")
        elif command == "check-ignore":
            if not rest or rest[0] != "--" or len(rest) > 51 or any(
                not item or item.startswith("-") for item in rest[1:]
            ):
                raise PermissionError("git check-ignore form is not allowed")

    @staticmethod
    def _validate_python(args: list[str]) -> None:
        if args in (["--version"], ["-V"]):
            return
        allowed = (
            ["-m", "unittest", "discover", "-s", "tests", "-v"],
            ["-m", "compileall", "-q", "src", "tests"],
        )
        if args not in allowed:
            raise PermissionError("python command is not allowed")

    def _validate_command(self, command: str, args: Sequence[str]) -> list[str]:
        if command not in {"python", "git"}:
            raise PermissionError("executable is not allowed; Copilot is operator-only")
        checked = self._validate_args(args)
        if command == "git":
            self._validate_git(checked)
            if checked[0] == "check-ignore":
                for path in checked[2:]:
                    self._parts(path, allow_dot=False)
        else:
            self._validate_python(checked)
        return checked

    def run_command(self, command: str, args: Sequence[str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
        if not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds")
        checked = self._validate_command(command, args or [])
        return self._run_argv([command, *checked], timeout)

    @staticmethod
    def _child_environment() -> dict[str, str]:
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith("GIT_") and not name.upper().startswith("PYTHON")
        }
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return environment

    @staticmethod
    def _resolve_executable(executable: str) -> str:
        if executable == "python":
            return sys.executable
        if executable == "git":
            resolved = shutil.which("git")
            if not resolved:
                raise FileNotFoundError("git executable is unavailable")
            return resolved
        return executable

    def _prepare_argv(self, argv: Sequence[str]) -> list[str]:
        if not argv:
            raise ValueError("command must not be empty")
        executable = self._resolve_executable(argv[0])
        prepared = [executable, *argv[1:]]
        executable_name = Path(executable).name.casefold()
        if executable_name in {"git", "git.exe"} and len(prepared) > 1:
            command = prepared[1]
            if command == "push":
                if self.credential_helper:
                    prepared[1:1] = [
                        "-c",
                        f"credential.helper={self.credential_helper}",
                    ]
            elif command != "commit":
                prepared = [prepared[0], "--no-pager", *prepared[1:]]
                if command in {"diff", "show"}:
                    prepared[3:3] = ["--no-ext-diff", "--no-textconv"]
        return prepared

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                env=ProjectSecurity._child_environment(),
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()

    def _run_argv(self, argv: Sequence[str], timeout: int) -> dict[str, Any]:
        prepared_argv = self._prepare_argv(argv)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        kwargs: dict[str, Any] = {
            "cwd": self.root,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "start_new_session": os.name != "nt",
            "creationflags": creationflags,
            "env": self._child_environment(),
        }
        try:
            process = subprocess.Popen(prepared_argv, **kwargs)
        except (OSError, ValueError) as exc:
            return {
                "command": prepared_argv, "returncode": None, "stdout": "", "stderr": "",
                "status": "launch_failed", "error": str(exc),
                "timed_out": False, "limit_exceeded": False, "truncated": False,
            }
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        exceeded = threading.Event()

        def drain(name: str, stream: Any) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    remaining = MAX_OUTPUT_CHARS - len(output[name])
                    if remaining > 0:
                        output[name].extend(chunk[:remaining])
                    if len(chunk) > max(remaining, 0):
                        exceeded.set()
                        return
            finally:
                stream.close()

        readers = [
            threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
            for name in ("stdout", "stderr")
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while process.poll() is None:
            if exceeded.is_set():
                self._terminate(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._terminate(process)
                break
            time.sleep(0.01)
        if process.poll() is None:
            process.wait()
        for reader in readers:
            reader.join(timeout=2)
        status = "timed_out" if timed_out else ("limit_exceeded" if exceeded.is_set() else "completed")
        return {
            "command": prepared_argv,
            "returncode": process.returncode,
            "stdout": output["stdout"].decode("utf-8", errors="replace"),
            "stderr": output["stderr"].decode("utf-8", errors="replace"),
            "status": status,
            "timed_out": timed_out,
            "limit_exceeded": exceeded.is_set(),
            "truncated": exceeded.is_set(),
        }

    def git_status(self) -> dict[str, Any]:
        return self.run_command("git", ["status", "--short", "--branch"])

    def git_diff(self, staged: bool = False) -> dict[str, Any]:
        return self.run_command("git", ["diff", "--cached" if staged else "--"])

    def _git_checked(self, args: list[str], *, allowed_codes: set[int] | None = None) -> dict[str, Any]:
        result = self._run_argv(args, DEFAULT_TIMEOUT)
        if result["status"] != "completed" or result["returncode"] not in (allowed_codes or {0}):
            raise RuntimeError("git metadata command failed")
        return result

    def _git_output(self, args: list[str], *, allowed_codes: set[int] | None = None) -> str:
        return str(self._git_checked(args, allowed_codes=allowed_codes)["stdout"]).strip()

    def _branch_name(self) -> str:
        try:
            branch = self._git_output(["git", "symbolic-ref", "--quiet", "--short", "HEAD"])
        except RuntimeError as exc:
            raise PermissionError("operation requires a non-detached branch") from exc
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            raise PermissionError("operation requires a non-detached branch")
        return branch

    def _head_sha(self) -> str:
        sha = self._git_output(["git", "rev-parse", "HEAD"])
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
            raise RuntimeError("could not determine HEAD")
        return sha

    def _optional_head_sha(self) -> str | None:
        result = self._git_checked(["git", "rev-parse", "HEAD"], allowed_codes={0, 128})
        if result["returncode"] == 128:
            return None
        sha = str(result["stdout"]).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", sha):
            raise RuntimeError("could not determine HEAD")
        return sha

    def _staged_paths(self) -> list[str]:
        output = str(self._git_checked(["git", "diff", "--cached", "--name-only", "-z"])["stdout"])
        output = output.rstrip("\r\n")
        paths = [name for name in output.split("\x00") if name]
        if len(paths) > MAX_LIST_ENTRIES:
            raise ValueError("staged path list exceeds 500 entries")
        for name in paths:
            if any(
                _is_sensitive(part) or _is_excluded_directory(part)
                for part in Path(name).parts
            ):
                raise PermissionError("staged path is excluded or sensitive")
        return paths

    def _commit_state(self) -> dict[str, Any]:
        branch = self._branch_name()
        unstaged = self._git_checked(["git", "diff", "--quiet"], allowed_codes={0, 1})
        untracked = self._git_output(["git", "ls-files", "--others", "--exclude-standard"])
        staged = self._git_checked(["git", "diff", "--cached", "--quiet"], allowed_codes={0, 1})
        if unstaged["returncode"] == 1 or untracked:
            raise PermissionError("working tree must be clean except for staged changes")
        if staged["returncode"] != 1:
            raise PermissionError("at least one staged change is required")
        paths = self._staged_paths()
        tree = self._git_output(["git", "write-tree"])
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", tree):
            raise RuntimeError("could not determine staged tree")
        head = self._optional_head_sha()
        return {"branch": branch, "head": head, "staged_tree": tree, "staged_paths": paths}

    def git_commit_preview(self) -> dict[str, Any]:
        state = self._commit_state()
        state["confirmation"] = f"COMMIT {state['branch']} {state['staged_tree']}"
        return state

    def git_commit(self, message: str, confirmation: str) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip() or "\x00" in message:
            raise ValueError("commit message is required")
        state = self._commit_state()
        expected = f"COMMIT {state['branch']} {state['staged_tree']}"
        if confirmation != expected:
            raise PermissionError(f"exact confirmation required: {expected}")
        # Hooks remain enabled: repository hooks can execute local code at commit time.
        result = self._run_argv(["git", "commit", "-m", message], DEFAULT_TIMEOUT)
        result.update(state)
        result["confirmation"] = expected
        return result

    def _push_state(self) -> dict[str, Any]:
        status = self._git_output(["git", "status", "--porcelain"])
        if status:
            raise PermissionError("working tree must be clean")
        branch_name = self._branch_name()
        try:
            upstream_name = self._git_output(
                ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
            )
        except RuntimeError as exc:
            raise PermissionError("push requires a configured upstream") from exc
        if not re.fullmatch(r"[^/\s]+/[A-Za-z0-9._/-]+", upstream_name):
            raise PermissionError("push requires a configured upstream")
        remote, upstream_branch = upstream_name.split("/", 1)
        return {
            "branch": branch_name,
            "head": self._head_sha(),
            "upstream": upstream_name,
            "remote": remote,
            "upstream_branch": upstream_branch,
        }

    def git_push_preview(self) -> dict[str, Any]:
        state = self._push_state()
        state["confirmation"] = f"PUSH {state['branch']} {state['head']} {state['upstream']}"
        return state

    def git_push(self, confirmation: str) -> dict[str, Any]:
        state = self._push_state()
        expected = f"PUSH {state['branch']} {state['head']} {state['upstream']}"
        if confirmation != expected:
            raise PermissionError(f"exact confirmation required: {expected}")
        result = self._run_argv(
            ["git", "push", state["remote"], f"HEAD:{state['upstream_branch']}"], DEFAULT_TIMEOUT
        )
        result.update(state)
        result["confirmation"] = expected
        return result


def create_server(security: ProjectSecurity, *, host: str = "127.0.0.1", port: int = 8000) -> Any:
    if FastMCP is None:
        raise RuntimeError("MCP SDK is not installed; run python -m pip install -e mcp-server")
    app = FastMCP("uiux-agent-mcp", host=host, port=port)
    names = ["health_check", "list_project_files", "read_project_file", "write_project_file",
             "run_project_command", "git_status", "git_diff", "git_commit_preview", "git_commit",
             "git_push_preview", "git_push"]

    @app.tool()
    def health_check() -> dict[str, Any]:
        return {"version": __version__, "status": "ok", "project_root": str(security.root), "tools": names}

    @app.tool()
    def list_project_files(path: str = ".") -> dict[str, Any]:
        return security.list_files(path)

    @app.tool()
    def read_project_file(path: str) -> dict[str, Any]:
        return security.read_file(path)

    @app.tool()
    def write_project_file(path: str, content: str) -> dict[str, Any]:
        return security.write_file(path, content)

    @app.tool()
    def run_project_command(command: str, args: list[str] | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
        return security.run_command(command, args or [], timeout)

    @app.tool()
    def git_status() -> dict[str, Any]:
        return security.git_status()

    @app.tool()
    def git_diff(staged: bool = False) -> dict[str, Any]:
        return security.git_diff(staged)

    @app.tool()
    def git_commit_preview() -> dict[str, Any]:
        return security.git_commit_preview()

    @app.tool()
    def git_commit(message: str, confirmation: str) -> dict[str, Any]:
        return security.git_commit(message, confirmation)

    @app.tool()
    def git_push_preview() -> dict[str, Any]:
        return security.git_push_preview()

    @app.tool()
    def git_push(confirmation: str) -> dict[str, Any]:
        return security.git_push(confirmation)

    return app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local UI/UX Agent MCP server")
    parser.add_argument("--project-root", type=Path, help="Pinned project root")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host; loopback only")
    parser.add_argument("--port", type=int, default=8000)
    options = parser.parse_args(argv)
    if options.transport == "streamable-http" and options.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("HTTP host must be loopback")
    if not 1 <= options.port <= 65535:
        parser.error("port must be between 1 and 65535")
    security = ProjectSecurity(options.project_root)
    app = create_server(security, host=options.host, port=options.port)
    app.run(transport=options.transport)


if __name__ == "__main__":
    main()
