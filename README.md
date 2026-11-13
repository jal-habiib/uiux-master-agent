# uiux-master-agent
Active Development: A growing autonomous UI/UX AI agent built with Python, Streamlit, and Playwright. Currently exploring multi-provider LLM fallbacks, self-reflection loops, and long-term memory to build production-ready HTML/Tailwind code.
# 🎨 UI/UX Master Agent

An advanced, autonomous AI agent designed to act as a Senior UI/UX Designer and Frontend Developer. It doesn't just generate code; it plans, generates, reviews its own screenshots, and iterates until the design is perfect.

## 🚀 Features

*   **Multi-Provider Fallback:** Never crashes. Automatically switches between OpenRouter (Nemotron), NVIDIA NIM (Llama 3.2 90B Vision), and Google Gemini if one hits a rate limit or goes offline.
*   **Self-Reflection Loop:** Uses Playwright to take a screenshot of the generated HTML, "looks" at its own work using vision models, and critiques/fixes its own mistakes.
*   **Long-Term Memory:** Uses ChromaDB to remember user preferences (e.g., "I like dark mode and cyan accents") across different sessions.
*   **Design Token System:** Generates a structured JSON design token system (colors, typography, spacing) before writing any code to ensure consistency.
*   **Surgical Edits:** Uses an `edit_file` tool to make precise modifications to existing files rather than rewriting the whole page.
*   **Live Preview:** See the generated HTML render in real-time inside the Streamlit UI.

## 🛠️ Tech Stack

*   **Frontend/UI:** Streamlit
*   **Agent Framework:** Custom Python Loop (OpenAI SDK format)
*   **AI Models:** OpenRouter (Nemotron), NVIDIA NIM, Google Gemini
*   **Vision & Rendering:** Playwright (Headless Chromium)
*   **Memory:** ChromaDB (Vector Database)
*   **Styling Engine:** Tailwind CSS (via CDN)

## ⚙️ How to Run Locally

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/uiux-master-agent.git
   cd uiux-master-agent

2. **Install dependencies:**
   ```powershell
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe -m playwright install chromium
   npm ci
   ```

   Verified browser QA requires both the Chromium browser binary and the local
   `node_modules/axe-core/axe.min.js` bundle. They are separate prerequisites.
   Run the `verify_html` tool on a saved HTML file after generation.

### Provider capabilities and limits

Providers are selected by explicit per-model metadata, not by provider or model
name. Copy `.env.example` and set `OPENROUTER_CAPABILITIES`,
`NVIDIA_CAPABILITIES`, `GROQ_CAPABILITIES`, and `GEMINI_CAPABILITIES` to a
comma-separated subset of `text`, `vision`, `tool_calling`, and `json_output`.
Missing capability declarations disable routing to that model; malformed
declarations fail closed with a diagnostic. For example:

```dotenv
OPENROUTER_CAPABILITIES=text,vision,tool_calling,json_output
NVIDIA_CAPABILITIES=text,tool_calling
GROQ_CAPABILITIES=text,tool_calling
GEMINI_CAPABILITIES=text,vision,tool_calling,json_output
```

Use only capabilities explicitly supported by each selected model. The complete
set of supported provider variables is shown in `.env.example`.
`*_TIMEOUT`, `*_PRIORITY`, and `*_ENABLED` control bounded timeout,
deterministic order, and availability.

The agent defaults to 20 tool-loop iterations. `MAX_ITER` may lower or raise
that value up to the hard maximum of 50; invalid values use the safe default.
Provider retries are separate from agent iterations: only one retry is allowed
for transient connection/selected 5xx failures, while timeout, authentication,
invalid-request, and rate-limit failures are not retried.
