import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI

import config

log = logging.getLogger("provider_gateway")

DEFAULT_TIMEOUT = getattr(config, "DEFAULT_TIMEOUT", 30.0)
_blocked_until: dict[str, float] = {}


@dataclass
class GatewayResult:
    response: Any | None
    provider: dict | None
    failures: list[str]
    attempts: int


def eligible_providers(
    providers: list[dict], required_capabilities: set[str]
) -> list[dict]:
    indexed = list(enumerate(providers))
    return [
        provider
        for _, provider in sorted(indexed, key=lambda item: (item[1].get("priority", 100), item[0]))
        if provider.get("enabled", True)
        and required_capabilities.issubset(provider.get("capabilities", frozenset()))
    ]


def _describe(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}"
    return text if len(text) <= 300 else text[:300] + "..."


def _describe_construction_failure(error: Exception) -> str:
    return f"client construction failed ({type(error).__name__})"


def _is_retryable(error: Exception) -> bool:
    name = type(error).__name__
    if name in {"APITimeoutError", "AuthenticationError", "BadRequestError"}:
        return False
    if name == "APIConnectionError":
        return True
    return getattr(error, "status_code", None) in (500, 502, 503, 504)


def _safe_timeout(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if not math.isfinite(parsed) or parsed <= 0:
        return DEFAULT_TIMEOUT
    minimum = getattr(config, "MIN_TIMEOUT", 1.0)
    maximum = getattr(config, "MAX_TIMEOUT", 300.0)
    return max(minimum, min(parsed, maximum))


def call(
    messages: list[dict],
    tools: list[dict],
    required_capabilities: set[str],
    providers: list[dict] | None = None,
    status_callback: Callable[[str], None] | None = None,
    client_factory: Callable[..., Any] = OpenAI,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> GatewayResult:
    configured = config.PROVIDERS if providers is None else providers
    eligible = eligible_providers(configured, required_capabilities)
    if not eligible:
        required = ", ".join(sorted(required_capabilities))
        diagnostics = [
            f"- **{provider.get('name', provider.get('provider_id', 'provider'))}:** "
            f"{provider['capability_diagnostic']}"
            for provider in configured
            if provider.get("capability_diagnostic")
        ]
        failures = [f"No configured model supports required capabilities: {required}."]
        failures.extend(diagnostics)
        return GatewayResult(None, None, failures, 0)

    failures: list[str] = []
    attempts = 0
    for provider in eligible:
        model_id = provider.get("model_id", provider.get("model"))
        identity = f"{provider.get('name', provider.get('provider_id', 'provider'))}/{model_id}"
        wait = _blocked_until.get(identity, 0) - clock()
        if wait > 0:
            failures.append(
                f"- **{provider.get('name', provider.get('provider_id', 'provider'))}:** "
                f"skipped, rate limited (retry in about {int(wait)}s)"
            )
            continue
        if status_callback:
            status_callback(f"Asking {provider.get('name', provider.get('provider_id', 'provider'))} ({model_id})...")
        try:
            client = client_factory(
                api_key=provider["api_key"],
                base_url=provider["base_url"],
                max_retries=0,
                timeout=_safe_timeout(provider.get("timeout", DEFAULT_TIMEOUT)),
            )
        except Exception as error:
            reason = _describe_construction_failure(error)
            log.warning("Provider %s %s", identity, reason)
            failures.append(
                f"- **{provider.get('name', provider.get('provider_id', 'provider'))}:** "
                f"{reason}"
            )
            continue
        for attempt in range(2):
            attempts += 1
            try:
                log.info("Attempting provider %s (try %s)", identity, attempt + 1)
                response = client.chat.completions.create(
                    model=model_id, messages=messages, tools=tools
                )
                if not response.choices:
                    raise ValueError("provider returned a response with no choices")
                return GatewayResult(response, provider, failures, attempts)
            except Exception as error:
                text = str(error).lower()
                if type(error).__name__ == "RateLimitError":
                    duration = 1800 if "per-day" in text or "per day" in text else 60
                    _blocked_until[identity] = clock() + duration
                    failures.append(
                        f"- **{provider.get('name', provider.get('provider_id', 'provider'))}:** "
                        f"{_describe(error)}"
                    )
                    break
                reason = _describe(error)
                if attempt == 0 and _is_retryable(error):
                    log.warning("Provider %s hiccup (%s), retrying once", identity, reason)
                    sleep(2)
                    continue
                failures.append(
                    f"- **{provider.get('name', provider.get('provider_id', 'provider'))}:** "
                    f"{reason}"
                )
                break
    return GatewayResult(None, None, failures, attempts)
