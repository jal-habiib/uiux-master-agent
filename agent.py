import json
import logging
from collections.abc import Callable

from config import MAX_ITER, PROMPTS_DIR, PROVIDERS
from provider_gateway import call as gateway_call
from tools import TOOLS

log = logging.getLogger("agent")

SYSTEM_PROMPT = (PROMPTS_DIR / "system.md").read_text()

# Tool output is cut to this many characters before going back to the model.
MAX_TOOL_RESULT_CHARS = 8000

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

        required_capabilities = {"text", "tool_calling"}
        if image_paths:
            required_capabilities.add("vision")
        result = gateway_call(
            messages,
            tools,
            required_capabilities,
            providers=PROVIDERS,
            status_callback=status_callback,
        )
        if result.response is None:
            return (
                "⚠️ **Provider Error:** All configured AI providers failed.\n\n"
                + "\n".join(result.failures)
            )
        response = result.response
        used = (
            f"{result.provider.get('name', result.provider.get('provider_id', 'provider'))}/"
            f"{result.provider.get('model_id', result.provider.get('model'))}"
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