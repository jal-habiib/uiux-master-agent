import json
import logging
import time
from collections.abc import Callable

from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from config import MAX_ITER, PROMPTS_DIR, PROVIDERS
from tools import TOOLS

log = logging.getLogger("agent")

SYSTEM_PROMPT = (PROMPTS_DIR / "system.md").read_text()

# Seconds to wait for each provider before giving up on it.
PROVIDER_TIMEOUTS = {"NVIDIA": 90.0}
DEFAULT_TIMEOUT = 30.0

# Tool output is cut to this many characters before going back to the model.
MAX_TOOL_RESULT_CHARS = 8000

# provider name -> unix time until which we skip it (set after a 429).
# Module level, so it survives Streamlit reruns.
_blocked_until: dict[str, float] = {}


def _build_tool_decls():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": meta["desc"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        pname: {
                            "type": param["type"],
                            "description": param["desc"],
                        }
                        for pname, param in meta["params"].items()
                    },
                    "required": meta.get("required", []),
                },
            },
        }
        for name, meta in TOOLS.items()
    ]


def _describe(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}"
    return text if len(text) <= 300 else text[:300] + "..."


def _is_retryable(error: Exception) -> bool:
    """Server hiccups worth one quick retry. Timeouts are not retried."""
    if isinstance(error, APITimeoutError):
        return False
    if isinstance(error, APIConnectionError):
        return True
    return getattr(error, "status_code", None) in (500, 502, 503, 504)


def _call_provider(provider: dict, messages: list, tools: list):
    """Return (response, None) on success or (None, reason) on failure."""
    name = provider["name"]

    wait = _blocked_until.get(name, 0) - time.time()
    if wait > 0:
        return None, f"skipped, rate limited (retry in about {int(wait)}s)"

    client = OpenAI(
        api_key=provider["api_key"],
        base_url=provider["base_url"],
        max_retries=0,
        timeout=PROVIDER_TIMEOUTS.get(name, DEFAULT_TIMEOUT),
    )

    reason = "unknown error"
    for attempt in range(2):
        try:
            log.info("Attempting Provider: %s (try %s)", name, attempt + 1)
            response = client.chat.completions.create(
                model=provider["model"],
                messages=messages,
                tools=tools,
            )
            if not response.choices:
                raise ValueError("provider returned a response with no choices")
            return response, None
        except RateLimitError as error:
            text = str(error).lower()
            daily = "per-day" in text or "per day" in text
            _blocked_until[name] = time.time() + (1800 if daily else 60)
            reason = _describe(error)
            break
        except Exception as error:
            reason = _describe(error)
            if attempt == 0 and _is_retryable(error):
                log.warning("Provider %s hiccup (%s), retrying once...", name, reason)
                time.sleep(2)
                continue
            break

    log.warning("Provider %s failed: %s", name, reason)
    return None, reason


def _assistant_message(message, call_ids: list[str]) -> dict:
    """Build the assistant message by hand.

    model_dump(exclude_none=True) can leak provider-specific fields and drops
    "content" when it is None. Several OpenAI-compatible servers (NVIDIA NIM,
    Gemini) reject one or both when the message is sent back.
    """
    data = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        data["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                },
            }
            for call, call_id in zip(message.tool_calls, call_ids)
        ]
    return data


def _tool_content(result) -> str:
    text = json.dumps(result, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        extra = len(text) - MAX_TOOL_RESULT_CHARS
        text = text[:MAX_TOOL_RESULT_CHARS] + f"... [truncated, {extra} more characters]"
    return text


def run(
    user_input: str,
    image_paths: list[str] | None = None,
    status_callback: Callable[[str], None] | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    if not PROVIDERS:
        return (
            "⚠️ **Provider Error:** No AI providers are configured. "
            "Add at least one real API key to your .env file."
        )

    user_content = []

    if image_paths:
        from base64 import b64encode
        from pathlib import Path

        for image_path in image_paths:
            p = Path(image_path)
            suffix = p.suffix.lower()
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(suffix, "image/png")
            image_data = b64encode(p.read_bytes()).decode("ascii")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image_data}"},
                },
            )

    user_content.append({"type": "text", "text": user_input})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[
            {"role": message["role"], "content": message["content"]}
            for message in (history or [])
        ],
        {"role": "user", "content": user_content},
    ]
    tools = _build_tool_decls()

    for i in range(MAX_ITER):
        log.info("--- Iteration %s ---", i + 1)

        response = None
        failures = []
        used = None
        for provider in PROVIDERS:
            if status_callback:
                status_callback(f"Asking {provider['name']}...")
            response, reason = _call_provider(provider, messages, tools)
            if response is not None:
                used = provider["name"]
                break
            failures.append(f"- **{provider['name']}:** {reason}")

        if response is None:
            return (
                "⚠️ **Provider Error:** All configured AI providers failed.\n\n"
                + "\n".join(failures)
            )

        choice = response.choices[0]
        message = choice.message
        log.info(
            "Response from %s: finish_reason=%s, tool_calls=%s, text_chars=%s",
            used,
            choice.finish_reason,
            len(message.tool_calls or []),
            len(message.content or ""),
        )

        call_ids = [
            call.id or f"call_{i}_{n}"
            for n, call in enumerate(message.tool_calls or [])
        ]
        messages.append(_assistant_message(message, call_ids))

        if not message.tool_calls:
            return message.content or "(no text response)"

        for call, call_id in zip(message.tool_calls, call_ids):
            name = call.function.name
            args = call.function.arguments
            log.info("Agent decided to call tool: %s", name)
            if status_callback:
                status_callback(f"🛠️ Agent decided to call tool: {name}")

            if name not in TOOLS:
                available_tools = ", ".join(sorted(TOOLS))
                result = {
                    "error": (
                        f"Unknown tool '{name}'. "
                        f"Available tools: {available_tools}"
                    )
                }
            else:
                try:
                    result = TOOLS[name]["fn"](**json.loads(args or "{}"))
                except Exception as error:
                    result = {"error": f"{name} failed: {error}"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_content(result),
                }
            )

    return "[max iterations reached without final answer]"