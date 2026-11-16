from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from uiux_agent_mcp.server import MAX_FILE_BYTES, ProjectSecurity


class ProjectSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UIUX_AGENT_GIT_CREDENTIAL_HELPER", None)
            self.security = ProjectSecurity(self.root)
        (self.root / "safe.txt").write_text("hello", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True)

    @staticmethod
    def _write_user_git_configs(root: Path, content: str) -> dict[str, str]:
        home = root / "home"
        userprofile = root / "userprofile"
        xdg = root / "xdg"
        home.mkdir()
        userprofile.mkdir()
        (xdg / "git").mkdir(parents=True)
        (home / ".gitconfig").write_text(content, encoding="utf-8")
        (userprofile / ".gitconfig").write_text(content, encoding="utf-8")
        (xdg / "git" / "config").write_text(content, encoding="utf-8")
        return {
            "HOME": str(home),
            "USERPROFILE": str(userprofile),
            "XDG_CONFIG_HOME": str(xdg),
        }

    def test_path_traversal_sensitive_names_and_absolute_paths_are_rejected(self) -> None:
        for path in ("../safe.txt", str(self.root / "safe.txt"), r"..\safe.txt", ".npmrc", ".aws/config"):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                self.security.read_file(path)

    def test_symlink_components_are_rejected(self) -> None:
        outside = self.root.parent / "mcp-outside-test"
        outside.mkdir(exist_ok=True)
        try:
            (outside / "file.txt").write_text("outside", encoding="utf-8")
            link = self.root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(PermissionError):
                self.security.read_file("link/file.txt")
            workspace = self.root / "mcp-workspace"
            workspace.mkdir()
            nested_link = workspace / "nested"
            try:
                nested_link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaises(PermissionError):
                self.security.write_file("nested/new.txt", "blocked")
        finally:
            if outside.exists():
                for child in outside.iterdir():
                    child.unlink()
                outside.rmdir()

    def test_project_root_rejects_final_and_parent_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            target = base / "target"
            target.mkdir()
            (target / "project").mkdir()
            final_link = base / "final-link"
            parent_link = base / "parent-link"
            try:
                final_link.symlink_to(target, target_is_directory=True)
                parent_link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("the operating system does not permit directory symlinks here")
            with self.assertRaises((PermissionError, ValueError)):
                ProjectSecurity(final_link)
            with self.assertRaises((PermissionError, ValueError)):
                ProjectSecurity(parent_link / "project")

    def test_excluded_directories_are_case_insensitive_across_file_operations(self) -> None:
        variants = (".GIT", ".VENV", "DATA", "OUTPUT", "TEMP_UPLOADS", "NODE_MODULES")
        for name in variants:
            directory = self.root / name
            directory.mkdir()
            (directory / "item.txt").write_text("private", encoding="utf-8")

        listing = self.security.list_files(".")
        listed_names = {Path(entry).name.casefold() for entry in listing["entries"]}
        self.assertFalse(any(name.casefold() in listed_names for name in variants))

        for name in variants:
            with self.subTest(name=name):
                with self.assertRaises(PermissionError):
                    self.security.read_file(f"{name}/item.txt")
                with self.assertRaises(PermissionError):
                    self.security.write_file(f"{name}/new.txt", "blocked")

        for name in variants:
            with self.subTest(staged_name=name), patch.object(
                self.security,
                "_git_checked",
                return_value={"stdout": f"{name}/item.txt\0"},
            ):
                with self.assertRaises(PermissionError):
                    self.security._staged_paths()

    def test_file_size_and_utf8_limits(self) -> None:
        (self.root / "large.bin").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        (self.root / "invalid.bin").write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            self.security.read_file("large.bin")
        with self.assertRaises(ValueError):
            self.security.read_file("invalid.bin")
        with self.assertRaises(ValueError):
            self.security.write_file("large.txt", "x" * (MAX_FILE_BYTES + 1))

    def test_writes_create_only_validated_workspace_ancestors(self) -> None:
        with self.assertRaises(PermissionError):
            self.security.write_file("../safe.txt", "no")
        result = self.security.write_file("nested/note.txt", "written")
        self.assertEqual(result["path"], "mcp-workspace/nested/note.txt")
        self.assertEqual((self.root / "mcp-workspace/nested/note.txt").read_text(encoding="utf-8"), "written")
        self.assertFalse(any(path.name.startswith(".mcp-") for path in (self.root / "mcp-workspace/nested").iterdir()))

    def test_exact_command_grammars_reject_injection_and_copilot(self) -> None:
        rejected = [
            ("copilot", ["--help"]),
            ("copilot", ["-p", "do something"]),
            ("git", ["-c", "core.pager=cat", "status"]),
            ("git", ["status", "--no-pager"]),
            ("git", ["diff", "--exec-path=C:\\bad"]),
            ("git", ["show", "HEAD"]),
            ("python", ["-m", "os"]),
            ("python", ["script.py"]),
            ("python", ["-c", "print(1)"]),
            ("python", ["-m", "unittest", "discover", "-s", "other", "-v"]),
        ]
        for command, args in rejected:
            with self.subTest(command=command, args=args), self.assertRaises(PermissionError):
                self.security.run_command(command, args)
        with self.assertRaises(ValueError):
            self.security.run_command("git", ["status"], timeout=0)

    def test_bounded_real_output_terminates_process(self) -> None:
        result = self.security._run_argv(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()"], 10
        )
        self.assertEqual(result["status"], "limit_exceeded")
        self.assertTrue(result["limit_exceeded"])
        self.assertLessEqual(len(result["stdout"]), 20_000)

    def test_timeout_terminates_process_tree(self) -> None:
        result = self.security._run_argv([sys.executable, "-c", "import time; time.sleep(20)"], 1)
        self.assertEqual(result["status"], "timed_out")
        self.assertTrue(result["timed_out"])

    def test_child_environment_removes_hostile_git_and_python_variables(self) -> None:
        inherited = {
            "GIT_CONFIG_GLOBAL": r"C:\attacker\config",
            "GIT_DIR": r"C:\attacker\repo.git",
            "GIT_WORK_TREE": r"C:\attacker\worktree",
            "GIT_INDEX_FILE": r"C:\attacker\index",
            "GIT_PAGER": "attacker-pager",
            "GIT_EXTERNAL_DIFF": "attacker-diff",
            "PYTHONPATH": r"C:\attacker\python",
            "PYTHONWARNINGS": "error",
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\Windows\System32",
            "TEMP": r"C:\Temp",
            "TMP": r"C:\Temp",
            "USERPROFILE": r"C:\Users\tester",
            "HOME": r"C:\Users\tester",
            "SSH_AUTH_SOCK": r"\\.\pipe\ssh-agent",
            "GCM_INTERACTIVE": "Always",
        }
        with patch.dict(os.environ, inherited, clear=False):
            child = self.security._child_environment()
        for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
            "GIT_PAGER", "GIT_EXTERNAL_DIFF", "PYTHONPATH", "PYTHONWARNINGS",
        ):
            self.assertNotIn(name, child)
        for name in (
            "SystemRoot", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME",
            "SSH_AUTH_SOCK", "GCM_INTERACTIVE",
        ):
            matching = next(key for key in child if key.casefold() == name.casefold())
            self.assertEqual(child[matching], inherited[name])
        self.assertEqual(child["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(child["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(child["GIT_TERMINAL_PROMPT"], "0")

    def test_global_config_isolated_while_authentication_environment_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as config_dir:
            config_root = Path(config_dir)
            inherited = {
                "HOME": str(config_root),
                "USERPROFILE": str(config_root),
                "XDG_CONFIG_HOME": str(config_root / "xdg"),
                "GIT_CONFIG_GLOBAL": str(config_root / "attacker.gitconfig"),
                "SSH_AUTH_SOCK": r"\\.\pipe\ssh-agent",
                "GCM_INTERACTIVE": "Always",
            }
            with patch.dict(os.environ, inherited, clear=False):
                child = self.security._child_environment()
            self.assertEqual(child["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(child["SSH_AUTH_SOCK"], inherited["SSH_AUTH_SOCK"])
            self.assertEqual(child["GCM_INTERACTIVE"], inherited["GCM_INTERACTIVE"])

    def test_normal_user_git_config_locations_are_ignored_by_status_diff_and_commit(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "safe.txt")
        self.git("commit", "-m", "initial")
        (self.root / "safe.txt").write_text("changed", encoding="utf-8")

        with tempfile.TemporaryDirectory() as config_dir:
            config_root = Path(config_dir)
            marker = config_root / "marker.txt"
            evil = config_root / ("evil.cmd" if os.name == "nt" else "evil.sh")
            if os.name == "nt":
                evil.write_text(f'@echo off\necho hostile>>"{marker}"\n', encoding="utf-8")
            else:
                evil.write_text(f'#!/bin/sh\necho hostile >> "{marker}"\n', encoding="utf-8")
                evil.chmod(0o700)
            global_hooks = config_root / "global-hooks"
            global_hooks.mkdir()
            global_hook = global_hooks / "pre-commit"
            global_hook.write_text(
                f'#!/bin/sh\necho global >> "{marker.as_posix()}"\n',
                encoding="utf-8",
            )
            if os.name != "nt":
                global_hook.chmod(0o700)
            global_content = (
                "[core]\n"
                f"    pager = {evil.as_posix()}\n"
                f"    hooksPath = {global_hooks.as_posix()}\n"
                "[diff]\n"
                f"    external = {evil.as_posix()}\n"
                '[diff "hostile"]\n'
                f"    textconv = {evil.as_posix()}\n"
            )
            environment = self._write_user_git_configs(config_root, global_content)
            with patch.dict(os.environ, environment, clear=False):
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
                status = self.security.git_status()
                self.assertEqual(status["returncode"], 0)
                self.assertIn("--no-pager", status["command"])
                result = self.security.git_diff()
                self.assertEqual(result["returncode"], 0)
                self.assertIn("--no-pager", result["command"])
                (self.root / "safe.txt").write_text("committed", encoding="utf-8")
                self.git("add", "safe.txt")
                preview = self.security.git_commit_preview()
                commit = self.security.git_commit("normal config test", preview["confirmation"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(commit["returncode"], 0)
            self.assertFalse(marker.exists())

    def test_global_hooks_path_is_ignored_and_default_local_hook_runs(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "safe.txt")
        self.git("commit", "-m", "initial")

        with tempfile.TemporaryDirectory() as config_dir:
            config_root = Path(config_dir)
            global_marker = config_root / "global-marker.txt"
            local_marker = config_root / "local-marker.txt"
            global_hooks = config_root / "global-hooks"
            global_hooks.mkdir()
            global_hook = global_hooks / "pre-commit"
            global_hook.write_text(
                f'#!/bin/sh\necho global >> "{global_marker.as_posix()}"\n',
                encoding="utf-8",
            )
            local_hook = self.root / ".git" / "hooks" / "pre-commit"
            local_hook.write_text(
                f'#!/bin/sh\necho local >> "{local_marker.as_posix()}"\n',
                encoding="utf-8",
            )
            if os.name != "nt":
                global_hook.chmod(0o700)
                local_hook.chmod(0o700)
            (self.root / "safe.txt").write_text("hooked", encoding="utf-8")
            self.git("add", "safe.txt")
            environment = self._write_user_git_configs(
                config_root,
                "[core]\n"
                f"    hooksPath = {global_hooks.as_posix()}\n",
            )
            with patch.dict(os.environ, environment, clear=False):
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
                preview = self.security.git_commit_preview()
                result = self.security.git_commit("hook test", preview["confirmation"])
            self.assertEqual(result["returncode"], 0)
            self.assertFalse(global_marker.exists())
            if os.name == "nt" and not local_marker.exists():
                self.skipTest("Git hook execution is platform-dependent on Windows")
            self.assertTrue(local_marker.exists())

    def test_global_textconv_and_external_diff_cannot_execute(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / ".gitattributes").write_text("safe.txt diff=hostile\n", encoding="utf-8")
        (self.root / "safe.txt").write_text("original", encoding="utf-8")
        self.git("add", ".gitattributes", "safe.txt")
        self.git("commit", "-m", "initial")
        (self.root / "safe.txt").write_text("changed", encoding="utf-8")

        with tempfile.TemporaryDirectory() as config_dir:
            config_root = Path(config_dir)
            marker = config_root / "marker.txt"
            evil = config_root / ("evil.cmd" if os.name == "nt" else "evil.sh")
            if os.name == "nt":
                evil.write_text(f'@echo off\necho hostile>>"{marker}"\n', encoding="utf-8")
            else:
                evil.write_text(f'#!/bin/sh\necho hostile >> "{marker}"\n', encoding="utf-8")
                evil.chmod(0o700)
            environment = self._write_user_git_configs(
                config_root,
                "[diff]\n"
                f"    external = {evil.as_posix()}\n"
                '[diff "hostile"]\n'
                f"    textconv = {evil.as_posix()}\n",
            )
            with patch.dict(os.environ, environment, clear=False):
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
                result = self.security.git_diff()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["returncode"], 0)
            self.assertIn("--no-ext-diff", result["command"])
            self.assertIn("--no-textconv", result["command"])
            self.assertFalse(marker.exists())

    def test_allowed_executables_resolve_to_safe_interpreters(self) -> None:
        self.assertEqual(self.security._resolve_executable("python"), sys.executable)
        with patch("uiux_agent_mcp.server.shutil.which", return_value=r"C:\Git\bin\git.exe"):
            self.assertEqual(
                self.security._resolve_executable("git"),
                r"C:\Git\bin\git.exe",
            )
        with patch("uiux_agent_mcp.server.shutil.which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "git executable is unavailable"):
                self.security._resolve_executable("git")

    def test_unset_credential_helper_does_not_change_any_command(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UIUX_AGENT_GIT_CREDENTIAL_HELPER", None)
            security = ProjectSecurity(self.root)
        for argv in (
            ["git", "status", "--short", "--branch"],
            ["git", "commit", "-m", "message"],
            ["git", "push", "origin", "HEAD:main"],
        ):
            with self.subTest(argv=argv):
                prepared = security._prepare_argv(argv)
                self.assertNotIn("credential.helper", " ".join(prepared))

    def test_credential_helper_rejects_untrusted_values(self) -> None:
        rejected = (
            "git credential-manager",
            "!echo unsafe",
            "unknown-helper",
            "manager --config bad",
            r"C:\attacker\helper.exe",
        )
        for helper in rejected:
            with self.subTest(helper=helper), patch.dict(
                os.environ,
                {"UIUX_AGENT_GIT_CREDENTIAL_HELPER": helper},
                clear=False,
            ):
                with self.assertRaises(ValueError):
                    ProjectSecurity(self.root)

    def test_approved_credential_helper_is_push_only(self) -> None:
        with tempfile.TemporaryDirectory() as helper_dir:
            helper_root = Path(helper_dir)
            helper = helper_root / (
                "git-credential-manager.cmd" if os.name == "nt" else "git-credential-manager"
            )
            helper.write_text("@echo off\nexit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n",
                              encoding="utf-8")
            if os.name != "nt":
                helper.chmod(0o700)
            path_key = next(key for key in os.environ if key.casefold() == "path")
            with patch.dict(
                os.environ,
                {
                    "UIUX_AGENT_GIT_CREDENTIAL_HELPER": "manager",
                    path_key: str(helper_root) + os.pathsep + os.environ[path_key],
                },
                clear=False,
            ):
                security = ProjectSecurity(self.root)
                child = security._child_environment()
            self.assertIn(str(helper_root), child[path_key])
        push = security._prepare_argv(["git", "push", "origin", "HEAD:main"])
        self.assertEqual(
            push[1:4],
            ["-c", "credential.helper=manager", "push"],
        )
        for argv in (
            ["git", "status", "--short", "--branch"],
            ["git", "diff", "--"],
            ["git", "commit", "-m", "message"],
        ):
            with self.subTest(argv=argv):
                prepared = security._prepare_argv(argv)
                self.assertNotIn("credential.helper", " ".join(prepared))

    def test_git_invokes_approved_fake_credential_helper_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = root / "repository"
            repository.mkdir()
            helper_dir = root / "helper-bin"
            helper_dir.mkdir()
            log_path = root / "helper-actions.log"
            if os.name == "nt":
                project = root / "git-credential-manager.csproj"
                source = root / "Program.cs"
                local_package_source = root / "local-nuget"
                local_package_source.mkdir()
                project.write_text(
                    """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
""",
                    encoding="utf-8",
                )
                source.write_text(
                    """using System;
using System.IO;

var action = args.Length > 0 ? args[0] : "";
_ = Console.In.ReadToEnd();
if (action is "get" or "store" or "erase")
{
    File.AppendAllText(
        Environment.GetEnvironmentVariable("GIT_CREDENTIAL_HELPER_LOG")!,
        action + Environment.NewLine);
}
if (action == "get")
{
    Console.Write("username=dummy-user\\npassword=dummy-pass\\n\\n");
}
""",
                    encoding="utf-8",
                )
                build = subprocess.run(
                    [
                        "dotnet",
                        "publish",
                        str(project),
                        "-c",
                        "Release",
                        "-o",
                        str(helper_dir),
                        "--nologo",
                        "--source",
                        str(local_package_source),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(build.returncode, 0, "temporary helper executable could not be built")
            else:
                helper_script = root / "credential_helper.py"
                helper_script.write_text(
                    """import os
import pathlib
import sys

action = sys.argv[1] if len(sys.argv) > 1 else ""
sys.stdin.read()
if action in {"get", "store", "erase"}:
    with pathlib.Path(os.environ["GIT_CREDENTIAL_HELPER_LOG"]).open("a", encoding="utf-8") as stream:
        stream.write(action + "\\n")
if action == "get":
    sys.stdout.write("username=dummy-user\\npassword=dummy-pass\\n\\n")
""",
                    encoding="utf-8",
                )
                launcher = helper_dir / "git-credential-manager"
                launcher.write_text(
                    f'#!/bin/sh\nexec "{sys.executable}" "{helper_script}" "$@"\n',
                    encoding="utf-8",
                )
                launcher.chmod(0o700)

            environment = self.security._child_environment()
            path_key = next(key for key in environment if key.casefold() == "path")
            git_executable = self.security._resolve_executable("git")
            environment[path_key] = os.pathsep.join(
                (str(helper_dir), str(Path(git_executable).parent))
            )
            environment["HOME"] = str(root / "home")
            environment["USERPROFILE"] = str(root / "userprofile")
            environment["XDG_CONFIG_HOME"] = str(root / "xdg")
            environment["GIT_CREDENTIAL_HELPER_LOG"] = str(log_path)
            init = subprocess.run(
                [git_executable, "init", "-q"],
                cwd=repository,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)

            # Windows Git may prioritize its bundled helper over PATH; this override
            # makes this protocol fixture deterministic, not a production PATH test.
            result = subprocess.run(
                [
                    git_executable,
                    f"--exec-path={helper_dir}",
                    "-c",
                    "credential.helper=manager",
                    "credential",
                    "fill",
                ],
                cwd=repository,
                env=environment,
                input="protocol=file\nhost=credential-helper.test\n\n",
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, "git credential fill failed")
            self.assertTrue(log_path.is_file(), "credential helper was not invoked")
            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(), ["get"])
            self.assertTrue("username=dummy-user" in result.stdout, "dummy username was not returned")
            self.assertTrue("password=dummy-pass" in result.stdout, "dummy password was not returned")
            helper_log = log_path.read_text(encoding="utf-8")
            self.assertNotIn("dummy-user", helper_log)
            self.assertNotIn("dummy-pass", helper_log)

    def test_read_only_git_commands_disable_pagers_diff_and_textconv(self) -> None:
        for command in ("status", "diff", "log", "branch", "show", "rev-parse", "ls-files"):
            with self.subTest(command=command):
                prepared = self.security._prepare_argv(["git", command])
                self.assertEqual(prepared[1:3], ["--no-pager", command])
                if command in {"diff", "show"}:
                    self.assertEqual(prepared[3:5], ["--no-ext-diff", "--no-textconv"])
        self.assertEqual(
            self.security._prepare_argv(["git", "commit", "-m", "message"])[1],
            "commit",
        )
        self.assertEqual(
            self.security._prepare_argv(["git", "push", "origin", "HEAD:main"])[1],
            "push",
        )

    def test_hostile_git_environment_cannot_redirect_repository_or_index(self) -> None:
        self.git("init")
        hostile = {
            "GIT_DIR": str(self.root.parent / "attacker.git"),
            "GIT_WORK_TREE": str(self.root.parent / "attacker-worktree"),
            "GIT_INDEX_FILE": str(self.root.parent / "attacker-index"),
            "GIT_CONFIG_GLOBAL": str(self.root.parent / "attacker-config"),
            "GIT_CONFIG_SYSTEM": str(self.root.parent / "attacker-system-config"),
            "GIT_PAGER": "attacker-pager",
            "GIT_EXTERNAL_DIFF": "attacker-diff",
        }
        with patch.dict(os.environ, hostile, clear=False):
            result = self.security.git_status()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("--no-pager", result["command"])
        self.assertNotIn("attacker", result["stdout"])

    def test_commit_requires_staged_only_changes_and_rejects_sensitive_paths(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        with self.assertRaises(PermissionError):
            self.security.git_commit_preview()
        (self.root / "safe.txt").write_text("changed", encoding="utf-8")
        self.git("add", "safe.txt")
        preview = self.security.git_commit_preview()
        self.assertIn("staged_tree", preview)
        self.assertEqual(preview["confirmation"], f"COMMIT {preview['branch']} {preview['staged_tree']}")
        result = self.security.git_commit("message", preview["confirmation"])
        self.assertEqual(result["returncode"], 0)

    def test_changed_staged_tree_invalidates_commit_confirmation(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "safe.txt")
        preview = self.security.git_commit_preview()
        (self.root / "safe.txt").write_text("changed", encoding="utf-8")
        self.git("add", "safe.txt")
        with self.assertRaises(PermissionError):
            self.security.git_commit("message", preview["confirmation"])

    def test_commit_rejects_sensitive_staged_path(self) -> None:
        self.git("init")
        (self.root / "credentials.json").write_text("secret", encoding="utf-8")
        self.git("add", "credentials.json")
        with self.assertRaises(PermissionError):
            self.security.git_commit_preview()

    def test_push_rejects_detached_or_missing_upstream_and_binds_confirmation(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "safe.txt")
        self.git("commit", "-m", "initial")
        with self.assertRaises(PermissionError):
            self.security.git_push("no")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaises(PermissionError):
            self.security.git_push(f"PUSH main {sha} origin/main")

    def test_push_preview_includes_upstream_and_targets_exact_remote(self) -> None:
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "safe.txt")
        self.git("commit", "-m", "initial")
        remote_temp = tempfile.TemporaryDirectory()
        self.addCleanup(remote_temp.cleanup)
        remote = Path(remote_temp.name)
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
        self.git("remote", "add", "local", str(remote))
        self.git("push", "-u", "local", "HEAD:main")
        (self.root / "safe.txt").write_text("pushed", encoding="utf-8")
        self.git("add", "safe.txt")
        commit_preview = self.security.git_commit_preview()
        self.security.git_commit("change", commit_preview["confirmation"])
        preview = self.security.git_push_preview()
        self.assertEqual(preview["upstream"], "local/main")
        self.assertEqual(preview["confirmation"], f"PUSH {preview['branch']} {preview['head']} local/main")
        with self.assertRaises(PermissionError):
            self.security.git_push(f"PUSH {preview['branch']} {preview['head']} other/main")
        result = self.security.git_push(preview["confirmation"])
        self.assertEqual(result["returncode"], 0)
        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(remote_head, preview["head"])

    def test_oversized_staged_path_metadata_is_rejected(self) -> None:
        self.git("init")
        for index in range(501):
            (self.root / f"f{index:03d}.txt").write_text("x", encoding="utf-8")
        self.git("add", ".")
        with self.assertRaises(ValueError):
            self.security.git_commit_preview()


if __name__ == "__main__":
    unittest.main()
