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
