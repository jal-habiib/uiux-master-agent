---
name: llm-provider-routing
description: Implement or review model-provider integrations, capability-aware routing, vision analysis, structured outputs, fallback, rate limits, retries, and cost controls for the UI/UX Master Agent.
---

# LLM provider routing skill

## Capability-first routing
Maintain a provider/model registry with explicit metadata such as provider_id, model_id, enabled, capabilities, context_limit, timeout, rate-limit/cost metadata, health, priority, and secret reference.

Capabilities must be declared per model, including at least text, vision, tool calling, and structured/JSON output as supported. Route a task by required capabilities, not by hard-coded provider names or a fixed primary provider.

A vision request must only be sent to a model that declares vision capability. Image analysis must use the same eligible-provider selection and fallback policy as other model tasks. Never send image payloads to text-only models.

In this repository, configure the registry with `OPENROUTER_*`, `NVIDIA_*`,
`GROQ_*`, and `GEMINI_*` variables. Each configured model should set
`*_CAPABILITIES` to a comma-separated list from `text`, `vision`,
`tool_calling`, and `json_output`; missing or malformed values fail closed.
Missing capability declarations disable routing to that model.
`*_TIMEOUT`, `*_PRIORITY`, and `*_ENABLED` are optional metadata. Provider
order remains deterministic (priority, then existing configuration order).
The shared `provider_gateway.call` is used by both the main agent and
`analyze_image`, including cooldowns and bounded retries. The agent defaults
to 20 iterations and clamps `MAX_ITER` to the hard maximum of 50.

## Resilience
- Apply bounded timeouts and bounded retries.
- Retry only transient failures (for example, selected 5xx or temporary network errors); do not blindly retry invalid requests or authentication failures.
- Respect rate-limit and daily-limit cooldowns where known.
- The current gateway retries one transient failure once after a fixed 2-second delay.
- Apply the current 60-second ordinary and 1800-second daily rate-limit cooldowns.
- Track provider health/circuit state and recover cautiously.
- Normalize provider-specific responses into one internal result shape.
- Preserve compatibility with providers that differ in message/tool-call formats.
- Keep fallback selection deterministic and observable.

## Secrets and privacy
- Read credentials only from approved environment or secret stores.
- Never place credentials in prompts, source control, logs, error messages, or artifacts.
- Redact sensitive headers and payload fields from diagnostics.
- Do not transmit user images or project data to a provider unless that provider is selected and the operation is authorized.

## Evaluation
- Keep builder and judge roles distinct. Prefer a judge provider different from the builder when feasible.
- Use vision models only for subjective visual assessment; deterministic code checks own accessibility, contrast, overflow, console/network, and functional pass/fail.
- Record provider/model, capability decision, fallback reason, latency, retries, and cost metadata where available.
- Test capability filtering, fallback, cooldown, malformed responses, timeouts, and provider-specific normalization with mocks.
