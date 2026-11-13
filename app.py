import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

import json
import re
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from agent import run
import app_jobs
import job_store

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
if "active_job_id" not in st.session_state:
    st.session_state.active_job_id = None

st.title("UI/UX Master Agent")

TEMP_UPLOADS_DIR = Path(__file__).parent / "temp_uploads"
TEMP_UPLOADS_DIR.mkdir(exist_ok=True)

selected_mode = st.sidebar.radio(
    "Job mode",
    options=("Create", "Review"),
    help="Create builds a new interface; Review evaluates an existing one.",
)
mode = selected_mode.lower()

active_job = None
if st.session_state.active_job_id:
    try:
        active_job = app_jobs.resume_intake_job(st.session_state.active_job_id)
    except job_store.JobNotFoundError:
        st.session_state.active_job_id = None
        st.sidebar.warning("The selected job no longer exists.")
    except job_store.ValidationError:
        st.session_state.active_job_id = None
        st.sidebar.warning("The selected job is no longer available for intake.")

if st.sidebar.button("Start new job", type="primary"):
    active_job = app_jobs.start_intake_job(mode)
    st.session_state.active_job_id = active_job["id"]

recoverable_jobs = app_jobs.list_recoverable_intake_jobs()
if recoverable_jobs:
    jobs_by_id = {job["id"]: job for job in recoverable_jobs}
    options = [""] + list(jobs_by_id)
    active_id = st.session_state.active_job_id
    selected_job_id = st.sidebar.selectbox(
        "Resume an intake job",
        options,
        index=options.index(active_id) if active_id in options else 0,
        format_func=lambda job_id: (
            "Select a saved job..."
            if not job_id
            else f"{jobs_by_id[job_id]['mode'].title()} · {job_id[:8]}"
        ),
    )
    if selected_job_id and selected_job_id != st.session_state.active_job_id:
        try:
            active_job = app_jobs.resume_intake_job(selected_job_id)
            st.session_state.active_job_id = active_job["id"]
        except job_store.JobNotFoundError:
            st.sidebar.warning("That saved job no longer exists. Refresh and try again.")
        except job_store.ValidationError:
            st.sidebar.warning("That saved job is no longer in intake.")

if active_job:
    st.sidebar.success(
        f"Active job: {active_job['mode'].title()} · {active_job['state']} · "
        f"{active_job['id'][:8]}"
    )
else:
    st.sidebar.info("No active job. One will be created when you submit a prompt.")
st.sidebar.caption(
    "S6 status: intake persistence is connected. Downstream stages and workers "
    "are not connected yet."
)

uploaded_images = st.sidebar.file_uploader(
    "Upload an image for analysis",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key=st.session_state.uploader_key,
)

for message in app_jobs.messages_for_job(
    st.session_state.messages, st.session_state.active_job_id
):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("image_paths"):
            st.image(message["image_paths"])

if prompt := st.chat_input("Ask the UI/UX Master Agent..."):
    status = None
    try:
        if active_job is None:
            active_job = app_jobs.start_intake_job(mode)
            st.session_state.active_job_id = active_job["id"]

        image_paths = []
        for uploaded_image in uploaded_images:
            image_path = TEMP_UPLOADS_DIR / Path(uploaded_image.name).name
            image_path.write_bytes(uploaded_image.getvalue())
            image_paths.append(str(image_path))

        st.session_state.messages.append(
            {
                "role": "user",
                "content": prompt,
                "image_paths": image_paths,
                "job_id": active_job["id"],
            }
        )
        save_history(st.session_state.messages)
        with st.chat_message("user"):
            st.markdown(prompt)
            if image_paths:
                st.image(image_paths)

        with st.chat_message("assistant"):
            with st.status("Thinking...", expanded=True) as status:
                job_messages = app_jobs.messages_for_job(
                    st.session_state.messages, active_job["id"]
                )
                response = run(
                    prompt,
                    image_paths=image_paths,
                    history=[
                        {
                            "role": message["role"],
                            "content": message["content"],
                        }
                        for message in job_messages[:-1]
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
                {
                    "role": "assistant",
                    "content": response,
                    "job_id": active_job["id"],
                }
            )
            save_history(st.session_state.messages)
    except Exception as exc:
        response = f"Unable to get a response: {exc}"
        if status is not None:
            status.update(label="Agent failed", state="error")
        with st.chat_message("assistant"):
            st.error(response)
        error_message = {"role": "assistant", "content": response}
        if active_job is not None:
            error_message["job_id"] = active_job["id"]
        st.session_state.messages.append(error_message)
        save_history(st.session_state.messages)
