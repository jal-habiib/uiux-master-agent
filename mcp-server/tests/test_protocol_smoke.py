from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProtocolSmokeTest(unittest.TestCase):
    def test_stdio_lists_tools_and_health_check(self) -> None:
        async def exercise() -> None:
            root = Path(__file__).resolve().parents[2]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(root / "src")
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "uiux_agent_mcp.server", "--project-root", str(root)],
                env=env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    expected = {
                        "health_check", "list_project_files", "read_project_file",
                        "write_project_file", "run_project_command", "git_status",
                        "git_diff", "git_commit_preview", "git_commit",
                        "git_push_preview", "git_push",
                    }
                    self.assertEqual(names, expected)
                    health = await session.call_tool("health_check", {})
                    self.assertFalse(health.isError)
                    self.assertIn('"status": "ok"', str(health.content))
                    git_status = await session.call_tool("git_status", {})
                    self.assertFalse(git_status.isError)
                    self.assertEqual(len(git_status.content), 1, "git_status returned unexpected content")
                    status_content = git_status.content[0]
                    self.assertTrue(hasattr(status_content, "text"), "git_status returned no JSON text")
                    try:
                        status_result = json.loads(status_content.text)
                    except json.JSONDecodeError as exc:
                        self.fail(f"git_status returned invalid JSON: {exc}")
                    self.assertIsInstance(status_result, dict, "git_status JSON result was not an object")
                    self.assertEqual(status_result.get("status"), "completed")
                    self.assertEqual(status_result.get("returncode"), 0)
                    self.assertFalse(status_result.get("timed_out"))
                    self.assertTrue(status_result.get("stdout"), "git_status returned empty stdout")

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
