import os
import math
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CAPABILITIES = frozenset({"text", "vision", "tool_calling", "json_output"})
DEFAULT_MAX_ITER = 20
MAX_ITER_HARD_CAP = 50
DEFAULT_TIMEOUT = 30.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 300.0


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _parse_capabilities(raw: str | None) -> tuple[frozenset[str], str | None]:
    if raw is None or not raw.strip():
        return frozenset(), "capabilities are not configured"
    values = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
    if not values:
        return frozenset(), "no valid capabilities declared"
    unknown = values - CAPABILITIES
    if unknown:
        return frozenset(), f"unknown capabilities: {', '.join(sorted(unknown))}"
    return values, None


def _parse_timeout(raw: str | None) -> tuple[float, str | None]:
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT, None
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT, "invalid timeout; using 30 seconds"
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT, "invalid timeout; using 30 seconds"
    if value < MIN_TIMEOUT:
        return MIN_TIMEOUT, "timeout clamped to 1 second"
    if value > MAX_TIMEOUT:
        return MAX_TIMEOUT, "timeout clamped to 300 seconds"
    return value, None


def _configured_provider(
    provider_id: str,
    api_key: str | None,
    base_url: str,
    model_id: str,
    capabilities_raw: str | None,
    timeout_raw: str | None,
    priority_raw: str | None,
    enabled_raw: str | None,
) -> dict | None:
    if not api_key or not api_key.strip():
        return None
    capabilities, diagnostic = _parse_capabilities(capabilities_raw)
    timeout, timeout_diagnostic = _parse_timeout(timeout_raw)
    diagnostic = diagnostic or timeout_diagnostic
    try:
        priority = int(priority_raw) if priority_raw else 100
    except ValueError:
        priority = 100
        diagnostic = diagnostic or "invalid priority; using 100"
    enabled = (enabled_raw or "true").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "provider_id": provider_id.lower(),
        "name": provider_id,
        "api_key": api_key,
        "base_url": base_url,
        "model_id": model_id,
        "model": model_id,
        "capabilities": capabilities,
        "capability_diagnostic": diagnostic,
        "timeout": timeout,
        "priority": priority,
        "enabled": enabled,
    }


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-405b-instruct")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
)

_provider_specs = (
    ("OpenRouter", OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL),
    ("NVIDIA", NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL),
    ("Groq", GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL),
    ("Gemini", GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL),
)
PROVIDER_DIAGNOSTICS: list[str] = []
PROVIDERS = []
for _provider_name, _key, _url, _model in _provider_specs:
    _prefix = _provider_name.upper()
    _provider = _configured_provider(
        _provider_name,
        _key,
        _url,
        _model,
        os.getenv(f"{_prefix}_CAPABILITIES"),
        os.getenv(f"{_prefix}_TIMEOUT"),
        os.getenv(f"{_prefix}_PRIORITY"),
        os.getenv(f"{_prefix}_ENABLED"),
    )
    if _provider is not None:
        PROVIDERS.append(_provider)
        if _provider["capability_diagnostic"]:
            PROVIDER_DIAGNOSTICS.append(
                f"{_provider_name}/{_model}: {_provider['capability_diagnostic']}"
            )

# Backwards-compatible aliases for tools that use the primary provider.
API_KEY = OPENROUTER_API_KEY
MODEL = OPENROUTER_MODEL
BASE_URL = OPENROUTER_BASE_URL
MAX_ITER = _env_int("MAX_ITER", DEFAULT_MAX_ITER, 1, MAX_ITER_HARD_CAP)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
PROMPTS_DIR = ROOT / "prompts"
OUTPUT_DIR.mkdir(exist_ok=True)
