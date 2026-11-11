import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

import json
import re
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from agent import run

HISTORY_PATH = Path(__file__).parent / "data" / "chat_history.json"


def load_history():
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", encoding="utf-8") as history_file:
            st.session_state.messages = json.load(history_file)
    else:
        st.session_state.messages = []


def save_history(messages):
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8") as history_file:
        json.dump(messages, history_file, indent=2)


def find_saved_html_file(response: str) -> Path | None:
    match = re.search(
        r"(?<!\w)Saved file:\s*([^\s`]+\.html)(?=$|[\s.,;:)\]}])",
        response,
    )
    if not match:
        return None

    filename = Path(match.group(1).rstrip(".,;:)]}")).name
    html_path = Path(__file__).parent / "output" / filename
    return html_path if html_path.is_file() else None


def extract_html_code(response: str) -> str | None:
    match = re.search(r"```html\s*(.*?)```", response, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


st.set_page_config(page_title="UI/UX Master Agent")

if "messages" not in st.session_state:
    load_history()
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

st.title("UI/UX Master Agent")

TEMP_UPLOADS_DIR = Path(__file__).parent / "temp_uploads"
TEMP_UPLOADS_DIR.mkdir(exist_ok=True)

uploaded_images = st.sidebar.file_uploader(
    "Upload an image for analysis",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key=st.session_state.uploader_key,
)

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("image_paths"):
            st.image(message["image_paths"])

if prompt := st.chat_input("Ask the UI/UX Master Agent..."):
    status = None
    try:
        image_paths = []
        for uploaded_image in uploaded_images:
            image_path = TEMP_UPLOADS_DIR / Path(uploaded_image.name).name
            image_path.write_bytes(uploaded_image.getvalue())
            image_paths.append(str(image_path))

        st.session_state.messages.append(
            {"role": "user", "content": prompt, "image_paths": image_paths}
        )
        save_history(st.session_state.messages)
        with st.chat_message("user"):
            st.markdown(prompt)
            if image_paths:
                st.image(image_paths)

        with st.chat_message("assistant"):
            with st.status("Thinking...", expanded=True) as status:
                response = run(
                    prompt,
                    image_paths=image_paths,
                    history=[
                        {
                            "role": message["role"],
                            "content": message["content"],
                        }
                        for message in st.session_state.messages[:-1]
                    ],
                    status_callback=lambda message: status.update(
                        label=message,
                        state="running",
                    ),
                )
                status.update(label="Done", state="complete")
                st.session_state.uploader_key += 1
            is_api_error = response.startswith(
                (
                    "Provider Error:",
                    "API Error:",
                    "⚠️ **Provider Error:**",
                    "⚠️ **API Error:**",
                )
            )
            if is_api_error:
                st.error(response)
            else:
                st.markdown(response)
                html_path = find_saved_html_file(response)
                if html_path:
                    html_content = html_path.read_text(encoding="utf-8")
                    components.html(html_content, height=600, scrolling=True)
                html_code = extract_html_code(response)
                if html_code:
                    st.download_button(
                        "Download HTML",
                        data=html_code,
                        file_name="generated.html",
                        mime="text/html",
                    )

        if not is_api_error:
            st.session_state.messages.append(
                {"role": "assistant", "content": response}
            )
            save_history(st.session_state.messages)
    except Exception as exc:
        response = f"Unable to get a response: {exc}"
        if status is not None:
            status.update(label="Agent failed", state="error")
        with st.chat_message("assistant"):
            st.error(response)
        st.session_state.messages.append({"role": "assistant", "content": response})
        save_history(st.session_state.messages)
