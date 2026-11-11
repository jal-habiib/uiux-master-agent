"""List the models each configured provider offers, and optionally test one.

Usage (run from the project folder, next to your .env):
    python list_models.py                      # list models for every provider with a key
    python list_models.py vision               # only show ids containing "vision"
    python list_models.py --test openrouter nvidia/nemotron-3.5-super-vl:free
        # checks the model answers and can make a tool call
"""
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROVIDERS = {
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    "nvidia": ("NVIDIA_API_KEY", "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "groq": ("GROQ_API_KEY", "GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    "gemini": (
        "GEMINI_API_KEY",
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
}


def client_for(name):
    key_var, url_var, default_url = PROVIDERS[name]
    key = os.getenv(key_var)
    if not key or "your" in key:
        return None
    return OpenAI(api_key=key, base_url=os.getenv(url_var, default_url), timeout=30)


def list_models(needle):
    for name in PROVIDERS:
        client = client_for(name)
        if client is None:
            print(f"[{name}] skipped: no real key set")
            continue
        try:
            ids = sorted(m.id for m in client.models.list())
        except Exception as error:
            print(f"[{name}] could not list models: {error}")
            continue
        if needle:
            ids = [i for i in ids if needle.lower() in i.lower()]
        print(f"\n[{name}] {len(ids)} models")
        for model_id in ids:
            print("  ", model_id)


def test_model(name, model):
    client = client_for(name)
    if client is None:
        sys.exit(f"No real key set for {name}")
    tools = [{
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Call this tool to confirm tool calling works.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    try:
        reply = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Call the ping tool."}],
            tools=tools,
        )
    except Exception as error:
        sys.exit(f"FAILED: {error}")
    message = reply.choices[0].message
    print("Model answered.")
    print("Tool calling:", "works" if message.tool_calls else "NOT used (model ignored the tool)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--test":
        if len(args) != 3:
            sys.exit("Usage: python list_models.py --test <provider> <model-id>")
        test_model(args[1], args[2])
    else:
        list_models(args[0] if args else "")