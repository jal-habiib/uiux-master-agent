import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "nvidia/nemotron-3.5-super-vl:free",
)
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL",
    "https://openrouter.ai/api/v1",
)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-405b-instruct")
NVIDIA_BASE_URL = os.getenv(
    "NVIDIA_BASE_URL",
    "https://integrate.api.nvidia.com/v1",
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
)


def _configured_provider(name, api_key, base_url, model):
    if not api_key or not api_key.strip():
        return None
    return {
        "name": name,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


PROVIDERS = [
    provider
    for provider in (
        _configured_provider(
            "OpenRouter",
            OPENROUTER_API_KEY,
            OPENROUTER_BASE_URL,
            OPENROUTER_MODEL,
        ),
        _configured_provider(
            "NVIDIA",
            NVIDIA_API_KEY,
            NVIDIA_BASE_URL,
            NVIDIA_MODEL,
        ),
        _configured_provider(
            "Groq",
            GROQ_API_KEY,
            GROQ_BASE_URL,
            GROQ_MODEL,
        ),
        _configured_provider(
            "Gemini",
            GEMINI_API_KEY,
            GEMINI_BASE_URL,
            GEMINI_MODEL,
        ),
    )
    if provider is not None
]

# Backwards-compatible aliases for tools that use the primary provider.
API_KEY = OPENROUTER_API_KEY
MODEL = OPENROUTER_MODEL
BASE_URL = OPENROUTER_BASE_URL
MAX_ITER = 15

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
PROMPTS_DIR = ROOT / "prompts"

OUTPUT_DIR.mkdir(exist_ok=True)