import os
import unittest
from unittest.mock import Mock, patch

import config
from provider_gateway import (
    GatewayResult,
    _blocked_until,
    _safe_timeout,
    call,
    eligible_providers,
)


def provider(name, capabilities, priority=100, timeout=12):
    return {
        "name": name,
        "provider_id": name.lower(),
        "model_id": f"{name}-model",
        "api_key": "test-key",
        "base_url": "https://example.invalid",
        "capabilities": set(capabilities),
        "priority": priority,
        "timeout": timeout,
        "enabled": True,
    }


class ProviderGatewayTests(unittest.TestCase):
    def setUp(self):
        from provider_gateway import _blocked_until

        _blocked_until.clear()

    def test_filters_each_declared_capability_and_orders_by_priority(self):
        providers = [
            provider("vision", {"text", "vision"}, priority=20),
            provider("tools", {"text", "tool_calling"}, priority=10),
            provider("json", {"text", "json_output"}, priority=5),
        ]
        self.assertEqual(
            [p["name"] for p in eligible_providers(providers, {"text", "vision"})],
            ["vision"],
        )
        self.assertEqual(
            [p["name"] for p in eligible_providers(providers, {"text", "tool_calling"})],
            ["tools"],
        )
        self.assertEqual(
            [p["name"] for p in eligible_providers(providers, {"text", "json_output"})],
            ["json"],
        )

    def test_missing_or_invalid_metadata_fails_closed(self):
        self.assertEqual(
            eligible_providers(
                [provider("missing", set()), provider("invalid", {"text", "vision"})],
                {"text", "tool_calling"},
            ),
            [],
        )

    def test_only_separators_are_reported_as_malformed_capabilities(self):
        capabilities, diagnostic = config._parse_capabilities(",,")
        self.assertEqual(capabilities, frozenset())
        self.assertEqual(diagnostic, "no valid capabilities declared")

    def test_falls_back_to_another_vision_model_and_applies_timeout(self):
        first = provider("first", {"text", "vision"}, priority=1, timeout=7)
        second = provider("second", {"text", "vision"}, priority=2, timeout=9)
        clients = []

        def factory(**kwargs):
            client = Mock()
            if kwargs["timeout"] == 7:
                client.chat.completions.create.side_effect = RuntimeError("down")
            else:
                client.chat.completions.create.return_value = Mock(choices=[Mock()])
            clients.append((client, kwargs))
            return client

        result = call([], [], {"text", "vision"}, [first, second], client_factory=factory)
        self.assertIsInstance(result, GatewayResult)
        self.assertEqual(result.provider["name"], "second")
        self.assertEqual(clients[0][1]["timeout"], 7)
        self.assertEqual(len(result.failures), 1)

    def test_falls_back_when_first_client_construction_fails_without_retrying(self):
        first = provider("first", {"text"}, priority=1)
        second = provider("second", {"text"}, priority=2)
        second["base_url"] = "https://second.example.invalid"
        constructed = []

        def factory(**kwargs):
            constructed.append(kwargs)
            if kwargs["base_url"] == first["base_url"]:
                raise ValueError("invalid base_url includes secret-key")
            client = Mock()
            client.chat.completions.create.return_value = Mock(choices=[Mock()])
            return client

        result = call([], [], {"text"}, [first, second], client_factory=factory)

        self.assertEqual(result.provider["name"], "second")
        self.assertEqual(len(constructed), 2)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("client construction failed (ValueError)", result.failures[0])
        self.assertNotIn("secret-key", result.failures[0])

    def test_timeout_is_finite_and_bounded(self):
        for raw, expected in (
            (float("nan"), 30.0),
            (float("inf"), 30.0),
            ("not-a-number", 30.0),
            (0, 30.0),
            (-2, 30.0),
            (0.5, 1.0),
            (1, 1.0),
            (300, 300.0),
            (301, 300.0),
        ):
            self.assertEqual(_safe_timeout(raw), expected)

    def test_transient_error_retries_once_but_timeout_auth_and_bad_request_do_not(self):
        class APIConnectionError(Exception):
            pass

        class APITimeoutError(Exception):
            pass

        class AuthenticationError(Exception):
            pass

        class BadRequestError(Exception):
            pass

        for error, expected_calls in (
            (APIConnectionError("temporary"), 2),
            (APITimeoutError("slow"), 1),
            (AuthenticationError("no"), 1),
            (BadRequestError("bad"), 1),
        ):
            client = Mock()
            client.chat.completions.create.side_effect = error
            result = call(
                [], [], {"text"}, [provider("one", {"text"})],
                client_factory=lambda **_: client, sleep=lambda _: None,
            )
            self.assertIsNone(result.response)
            self.assertEqual(client.chat.completions.create.call_count, expected_calls)

    def test_rate_limit_cooldown_skips_provider(self):
        class RateLimitError(Exception):
            pass

        client = Mock()
        client.chat.completions.create.side_effect = RateLimitError("ordinary limit")
        clock = Mock(side_effect=[100.0, 100.0, 101.0])
        first = call(
            [], [], {"text"}, [provider("one", {"text"})],
            client_factory=lambda **_: client, clock=clock, sleep=lambda _: None,
        )
        second = call(
            [], [], {"text"}, [provider("one", {"text"})],
            client_factory=lambda **_: client, clock=clock, sleep=lambda _: None,
        )
        self.assertIsNone(first.response)
        self.assertIn("rate limited", second.failures[0])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_daily_rate_limit_uses_1800_second_cooldown(self):
        class RateLimitError(Exception):
            pass

        client = Mock()
        client.chat.completions.create.side_effect = RateLimitError("per-day limit reached")
        clock = Mock(return_value=100.0)
        sleep = Mock()
        configured = [provider("one", {"text"})]

        first = call(
            [], [], {"text"}, configured,
            client_factory=lambda **_: client, clock=clock, sleep=sleep,
        )
        second = call(
            [], [], {"text"}, configured,
            client_factory=lambda **_: client, clock=clock, sleep=sleep,
        )

        self.assertIsNone(first.response)
        self.assertIsNone(second.response)
        self.assertEqual(_blocked_until["one/one-model"], 1900.0)
        self.assertIn("retry in about 1800s", second.failures[0])
        self.assertEqual(client.chat.completions.create.call_count, 1)
        sleep.assert_not_called()

    def test_iteration_configuration_defaults_and_clamps(self):
        original = os.environ.get("MAX_ITER")
        try:
            os.environ.pop("MAX_ITER", None)
            with patch("config.load_dotenv"):
                import importlib
                importlib.reload(config)
            self.assertEqual(config.MAX_ITER, 20)
            os.environ["MAX_ITER"] = "999"
            import importlib
            importlib.reload(config)
            self.assertEqual(config.MAX_ITER, config.MAX_ITER_HARD_CAP)
            os.environ["MAX_ITER"] = "not-a-number"
            importlib.reload(config)
            self.assertEqual(config.MAX_ITER, 20)
        finally:
            if original is None:
                os.environ.pop("MAX_ITER", None)
            else:
                os.environ["MAX_ITER"] = original
            import importlib
            importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
