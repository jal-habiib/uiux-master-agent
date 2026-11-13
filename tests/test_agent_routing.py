import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent
from provider_gateway import GatewayResult


def model(name, capabilities):
    return {
        "name": name,
        "model_id": f"{name}-model",
        "capabilities": set(capabilities),
        "enabled": True,
        "priority": 1,
        "api_key": "key",
        "base_url": "https://example.invalid",
    }


class AgentRoutingTests(unittest.TestCase):
    def test_images_require_vision_and_text_only_model_is_not_attempted(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=[]),
                    finish_reason="stop",
                )
            ]
        )
        gateway = Mock(return_value=GatewayResult(response, model("vision", {"text", "vision", "tool_calling"}), [], 1))
        with patch.object(agent, "PROVIDERS", [model("text", {"text", "tool_calling"})]        ), patch.object(
            agent, "gateway_call", gateway
        ):
            result = agent.run("describe", image_paths=[])
        self.assertEqual(result, "ok")
        self.assertEqual(gateway.call_args.args[2], {"text", "tool_calling"})

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            agent, "PROVIDERS", [model("vision", {"text", "vision", "tool_calling"})]
        ), patch.object(agent, "gateway_call", gateway):
            image_path = Path(temp_dir) / "image.png"
            image_path.write_bytes(b"png")
            agent.run("describe", image_paths=[image_path])
        self.assertEqual(gateway.call_args.args[2], {"text", "tool_calling", "vision"})

    def test_run_stops_at_configured_iteration_budget(self):
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="not-a-tool", arguments="{}"),
        )
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="", tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                )
            ]
        )
        gateway = Mock(return_value=GatewayResult(response, model("tools", {"text", "tool_calling"}), [], 1))
        with patch.object(agent, "PROVIDERS", [model("tools", {"text", "tool_calling"})]        ), patch.object(
            agent, "gateway_call", gateway
        ), patch.object(agent, "MAX_ITER", 3):
            result = agent.run("loop")
        self.assertEqual(result, "[max iterations reached without final answer]")
        self.assertEqual(gateway.call_count, 3)


if __name__ == "__main__":
    unittest.main()
