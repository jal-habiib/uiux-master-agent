import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

import json
import re
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from agent import run
import app_jobs
import build
import design
import gate
import job_store
import planning
import refine
import orchestrator
import verify

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
        active_job = app_jobs.resume_ui_job(st.session_state.active_job_id)
    except job_store.JobNotFoundError:
        st.session_state.active_job_id = None
        st.sidebar.warning("The selected job no longer exists.")
    except job_store.ValidationError:
        st.session_state.active_job_id = None
        st.sidebar.warning("The selected job is no longer available.")

if st.sidebar.button("Start new job", type="primary"):
    active_job = app_jobs.start_intake_job(mode)
    st.session_state.active_job_id = active_job["id"]

recoverable_jobs = app_jobs.list_recoverable_ui_jobs()
if recoverable_jobs:
    jobs_by_id = {job["id"]: job for job in recoverable_jobs}
    options = [""] + list(jobs_by_id)
    active_id = st.session_state.active_job_id
    selected_job_id = st.sidebar.selectbox(
        "Resume a saved job",
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
            active_job = app_jobs.resume_ui_job(selected_job_id)
            st.session_state.active_job_id = active_job["id"]
        except job_store.JobNotFoundError:
            st.sidebar.warning("That saved job no longer exists. Refresh and try again.")
        except job_store.ValidationError:
            st.sidebar.warning("That saved job is no longer available.")

if active_job:
    st.sidebar.success(
        f"Active job: {active_job['mode'].title()} · {active_job['state']} · "
        f"{active_job['id'][:8]}"
    )
else:
    st.sidebar.info("No active job. One will be created when you submit a prompt.")
st.sidebar.caption(
    "S11 status: Create intake, UX planning, Design, Build, Verify, and Gate & Deliver are connected. "
    "Refine remains a bounded manual handoff; production deployment is not automated."
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

if active_job and active_job["mode"] == "create":
    saved = app_jobs.saved_planning_artifacts(active_job)
    if saved:
        brief = saved.get("product_brief.json", {})
        plan = saved.get("ux_plan.json")
        st.subheader("Saved UX plan")
        st.write(plan.get("summary", "Validated UX plan saved.") if plan else
                 "Clarification is still required before a UX plan can be saved.")
        with st.expander("Product brief JSON"):
            st.json(brief)
        if plan:
            with st.expander("UX plan JSON"):
                st.json(plan)
    if active_job["state"] == "PLANNING" and saved and saved.get("ux_plan.json"):
        st.info("The validated S7 plan is ready for Design.")
        questions = app_jobs.saved_design_clarification_questions(active_job)
        if questions:
            st.warning("Design needs clarification before it can be completed.")
            for question in questions:
                st.markdown(f"- {question}")
            clarification = st.text_area(
                "Your clarification",
                key=f"design_clarification_{active_job['id']}",
                max_chars=design.MAX_STRING_CHARS,
            )
        else:
            clarification = None

        if st.button(
            "Generate design specification",
            key=f"generate_design_{active_job['id']}",
            type="primary",
        ):
            if questions and not (clarification or "").strip():
                st.error("Answer the saved Design questions before continuing.")
            else:
                design_saved = False
                try:
                    with st.status("Generating design specification...", expanded=True) as status:
                        design.design_job(
                            active_job["id"],
                            clarification=clarification,
                        )
                        status.update(label="Design stage saved", state="complete")
                    design_saved = True
                except Exception as exc:
                    st.error(f"Unable to generate the design specification: {exc}")
                if design_saved:
                    st.rerun()

    if active_job["state"] in {"DESIGN", "BUILD"}:
        saved_design = app_jobs.saved_design_artifacts(active_job)
        if not saved_design or set(saved_design) != app_jobs.DESIGN_ARTIFACT_NAMES:
            st.error("The completed Design artifacts are missing or invalid.")
        else:
            spec = saved_design["design_spec.json"]
            tokens = saved_design["design_tokens.json"]
            st.subheader("Design specification")
            st.write(spec["design_direction"])
            st.markdown("**Page specifications**")
            for page in spec["page_specs"]:
                with st.expander(page["page"]):
                    st.write(page["purpose"])
                    st.write(page["layout"])
                    st.markdown("**Sections**")
                    st.write(page["sections"])
                    st.markdown(f"**Primary action:** {page['primary_action']}")
                    st.markdown(f"**Responsive behavior:** {page['responsive_behavior']}")
            st.markdown("**Component specifications**")
            st.json(spec["component_specs"])
            st.markdown("**Interaction specifications**")
            st.json(spec["interaction_specs"])
            st.markdown("**Accessibility guidance**")
            st.write(spec["accessibility_checks"])
            st.markdown("**Responsive guidance**")
            st.write(spec["responsive_rules"])
            with st.expander("Content and implementation guidance"):
                st.write(spec["content_guidance"])
                st.write(spec["implementation_constraints"])
            with st.expander("Design tokens"):
                st.json(tokens)
            st.caption(
                "Saved design artifacts: "
                + ", ".join(
                    reference
                    for reference in active_job["latest_checkpoint"]["artifact_refs"]
                )
            )

    if active_job["state"] == "DESIGN":
        try:
            build_inputs = build.load_build_inputs(active_job["id"])
        except job_store.ValidationError as exc:
            build_inputs = None
            st.error(f"Build is unavailable because its source artifacts failed validation: {exc}")
        if build_inputs:
            st.info("The validated S7 brief, UX Plan, and completed S8 Design are ready to build.")
            questions = app_jobs.saved_build_clarification_questions(active_job)
            if questions:
                st.warning("Build needs clarification before it can continue.")
                for question in questions:
                    st.markdown(f"- {question}")
                build_clarification = st.text_area(
                    "Your Build clarification",
                    key=f"build_clarification_{active_job['id']}",
                    max_chars=build.MAX_STRING_CHARS,
                )
            else:
                build_clarification = None
            if st.button(
                "Generate HTML build",
                key=f"generate_build_{active_job['id']}",
                type="primary",
            ):
                if questions and not (build_clarification or "").strip():
                    st.error("Answer the saved Build questions before continuing.")
                else:
                    build_saved = False
                    try:
                        with st.status("Generating HTML pages...", expanded=True) as status:
                            build.build_job(
                                active_job["id"],
                                clarification=build_clarification,
                            )
                            status.update(label="Build stage saved", state="complete")
                        build_saved = True
                    except Exception as exc:
                        st.error(f"Unable to generate the HTML build: {exc}")
                    if build_saved:
                        st.rerun()

    if active_job["state"] in {"BUILD", "VERIFY", "REFINE"}:
        try:
            saved_build = build.load_completed_build(active_job)
        except job_store.ValidationError as exc:
            saved_build = None
            st.error(f"The saved Build artifacts are invalid: {exc}")
        if not saved_build:
            st.error("The completed Build manifest or page artifacts are missing.")
        else:
            manifest = saved_build["manifest"]
            st.subheader("Saved HTML build")
            st.caption(
                "Immutable Build artifacts: "
                + ", ".join(active_job["latest_checkpoint"]["artifact_refs"])
            )
            for page in saved_build["pages"]:
                with st.expander(f"{page['page']} · {page['filename']}", expanded=True):
                    st.code(page["html"], language="html")
                    components.html(page["html"], height=700, scrolling=True)
            with st.expander("Build manifest"):
                st.json(manifest)

    if active_job["state"] in {"BUILD", "VERIFY", "REFINE"}:
        try:
            saved_verification = verify.load_saved_verification(active_job)
        except job_store.ValidationError as exc:
            saved_verification = None
            st.error(f"The saved Verify report is invalid: {exc}")
        if saved_verification:
            st.subheader("Saved Verify report")
            st.markdown(f"**Status: {saved_verification['status']}**")
            if not saved_verification["verified"]:
                st.warning(
                    "This report does not verify the build. Failed gates or checks that "
                    "did not run remain visible and cannot be treated as passing."
                )
            st.json(saved_verification["hard_gates"])
            with st.expander("Quality score and supplemental provider review"):
                st.json(saved_verification["soft_quality"])
                st.json(saved_verification["provider_review"])
            st.caption(
                "Immutable Verify artifact: "
                + ", ".join(
                    reference
                    for item in active_job.get("checkpoints", [])
                    if item.get("metadata", {}).get("integration") == verify.VERIFY_INTEGRATION
                    for reference in item.get("artifact_refs", [])
                )
            )

    if active_job["state"] == "BUILD":
        st.info("Verify checks the saved S9 manifest and pages; it does not regenerate them.")
        flows_json = st.text_area(
            "Required browser user flows (JSON)",
            key=f"verify_flows_{active_job['id']}",
            max_chars=64 * 1024,
            help=(
                "Provide the safe flow schema used by browser QA. Without flow evidence, "
                "the result remains NEEDS_INPUT; no flow is inferred or fabricated."
            ),
        )
        if st.button(
            "Verify saved HTML build",
            key=f"verify_build_{active_job['id']}",
            type="primary",
        ):
            try:
                with st.status("Verifying saved HTML pages...", expanded=True) as status:
                    result = verify.verify_job(
                        active_job["id"],
                        flows_json=flows_json or None,
                    )
                    status.update(
                        label=f"Verify status: {result['report']['status']}",
                        state="complete",
                    )
                st.rerun()
            except job_store.ValidationError as exc:
                st.error(f"Unable to verify this Build: {exc}")

if active_job and active_job["mode"] == "create" and active_job["state"] == "REFINE":
    st.subheader("S12 ? Automated Refine")
    st.info("Refine uses the saved failed Verify report to generate a new immutable HTML run. It does not assume the repair passed; the new run must be verified again.")
    refine_feedback = st.text_area(
        "Optional refinement guidance",
        key=f"refine_feedback_{active_job['id']}",
        max_chars=build.MAX_STRING_CHARS,
        help="Describe a desired correction. Verify findings remain the primary repair evidence.",
    )
    if st.button("Run automated refinement", key=f"run_refine_{active_job['id']}", type="primary"):
        try:
            with st.status("Refining the saved HTML using Verify findings...", expanded=True) as status:
                refined = refine.refine_job(active_job["id"], user_feedback=refine_feedback or None)
                status.update(label="New immutable HTML run saved; ready for Verify", state="complete")
            st.success(f"Refinement run {refined['run_id']} saved. Verify it before delivery.")
            st.rerun()
        except job_store.ValidationError as exc:
            st.error(f"Automated refinement could not complete: {exc}")

if active_job and active_job["mode"] == "create" and active_job["state"] == "VERIFY":
    checkpoints = orchestrator.list_checkpoints(active_job["id"])
    latest_build_checkpoint = next((item for item in reversed(checkpoints)
        if item.get("metadata", {}).get("integration") in {app_jobs.BUILD_INTEGRATION, app_jobs.REFINE_INTEGRATION}
        and item.get("metadata", {}).get("status") == "completed"), None)
    latest_verify_checkpoint = next((item for item in reversed(checkpoints)
        if item.get("metadata", {}).get("integration") == verify.VERIFY_INTEGRATION), None)
    needs_reverify = bool(
        latest_build_checkpoint and latest_verify_checkpoint
        and latest_build_checkpoint.get("artifact_refs") != latest_verify_checkpoint.get("metadata", {}).get("source_artifact_refs")
    ) or bool(saved_verification and saved_verification.get("status") == "NEEDS_INPUT")
    if needs_reverify:
        st.warning("A new refined HTML run is saved. The previous Verify report is now stale; rerun Verify before Gate & Deliver.")
        refined_flows = st.text_area(
            "Required browser user flows (JSON) for refined build",
            key=f"reverify_flows_{active_job['id']}",
            max_chars=64 * 1024,
        )
        if st.button("Re-verify refined HTML", key=f"reverify_build_{active_job['id']}", type="primary"):
            try:
                with st.status("Re-verifying refined HTML...", expanded=True) as status:
                    result = verify.verify_job(active_job["id"], flows_json=refined_flows or None)
                    status.update(label=f"Verify status: {result['report']['status']}", state="complete")
                st.rerun()
            except job_store.ValidationError as exc:
                st.error(f"Unable to re-verify refined HTML: {exc}")
    elif saved_verification and saved_verification.get("status") == "FAIL":
        st.warning("The latest Verify run found repairable failures. Refine will use its saved evidence to create a new immutable run.")
        if st.button("Refine failed HTML build", key=f"refine_failed_{active_job['id']}", type="primary"):
            try:
                with st.status("Refining from saved Verify findings...", expanded=True) as status:
                    result = refine.refine_job(active_job["id"])
                    status.update(label="Refinement run saved; re-verification required", state="complete")
                st.success(f"Refinement run {result['run_id']} saved.")
                st.rerun()
            except job_store.ValidationError as exc:
                st.error(f"Unable to refine failed HTML: {exc}")

if active_job and active_job["mode"] == "create" and active_job["state"] in {"VERIFY", "GATE_AND_DELIVER", "DELIVERED"}:
    try:
        saved_gate = gate.load_saved_gate(active_job)
    except job_store.ValidationError as exc:
        saved_gate = None
        st.error(f"The saved Gate report or delivery package is invalid: {exc}")

    if saved_gate:
        gate_report = saved_gate["report"]
        st.subheader("Gate & Deliver")
        st.markdown(f"**Gate status: {gate_report['status']}**")
        st.write(gate_report["reason"])
        if gate_report["status"] == "BLOCKED":
            st.warning("Delivery is blocked. Unverified checks are not passes, and failed checks require refinement.")
            if gate_report["failed_gates"]:
                st.markdown("**Failed checks**")
                for item in gate_report["failed_gates"]:
                    st.markdown(f"- {item}")
            if gate_report["not_verified_gates"]:
                st.markdown("**Not verified**")
                for item in gate_report["not_verified_gates"]:
                    st.markdown(f"- {item}")
        elif gate_report["status"] == "NOT_PASSED":
            st.warning("This package is explicitly NOT PASSED after three fix cycles. It is not a production-readiness claim.")
        elif gate_report["status"] == "PASS":
            st.success("All hard gates and the soft quality threshold passed.")
        if saved_gate["package_bytes"]:
            st.download_button(
                "Download delivery package",
                data=saved_gate["package_bytes"],
                file_name=f"uiux-delivery-{active_job['id'][:8]}.zip",
                mime="application/zip",
                key=f"download_delivery_{active_job['id']}",
            )
    elif active_job["state"] == "VERIFY":
        st.info("Run the code-enforced gate against the saved Verify report. Passing builds are packaged locally; failed builds remain blocked.")

    if active_job["state"] == "VERIFY":
        if st.button("Evaluate Gate & create delivery package", key=f"gate_job_{active_job['id']}", type="primary"):
            try:
                with st.status("Evaluating hard gates and preparing delivery...", expanded=True) as status:
                    gate_result = gate.gate_job(active_job["id"])
                    active_job = gate_result["job"]
                    status.update(label=f"Gate status: {gate_result['status']}", state="complete")
                if gate_result["status"] == "BLOCKED":
                    st.warning(gate_result["report"]["reason"])
                elif gate_result["status"] == "NOT_PASSED":
                    st.warning("A not-passed package was created after three recorded fix cycles.")
                else:
                    st.success("Gate passed. Delivery package created.")
                st.rerun()
            except job_store.ValidationError as exc:
                st.error(f"Unable to complete Gate & Deliver: {exc}")

    if active_job["state"] == "GATE_AND_DELIVER":
        st.warning("The delivery package and Gate checkpoint are saved. Finalize the state transition to complete delivery.")
        if st.button("Finalize saved delivery", key=f"finalize_delivery_{active_job['id']}", type="primary"):
            try:
                result = gate.finalize_delivery(active_job["id"])
                active_job = result["job"]
                st.success("Delivery finalized.")
                st.rerun()
            except job_store.ValidationError as exc:
                st.error(f"Unable to finalize delivery: {exc}")

if prompt := st.chat_input("Ask the UI/UX Master Agent..."):
    status = None
    try:
        if active_job is None:
            active_job = app_jobs.start_intake_job(mode)
            st.session_state.active_job_id = active_job["id"]
        if active_job["mode"] == "create" and active_job["state"] != "INTAKE":
            st.error("This Create job already has a saved plan. Start a new job to submit another brief.")
            st.stop()

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
                if active_job["mode"] == "create":
                    result = planning.plan_job(
                        active_job["id"],
                        job_messages,
                    )
                    if result["status"] == "needs_input":
                        response = "I need a little more information before planning:\n\n" + "\n".join(
                            f"- {question}" for question in result["clarification_questions"]
                        )
                    else:
                        response = (
                            "Product brief and UX plan saved. The job is now **PLANNING**.\n\n"
                            f"**Summary:** {result['ux_plan']['summary']}"
                        )
                    active_job = result["job"]
                else:
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
    except planning.PlanningResponseError as exc:
        response = (
            "The Create intake response did not match the required S7 JSON contract. "
            f"Details: {exc}. No plan artifacts were saved and this job remains in INTAKE. "
            "Retry in the same job; if the issue continues, add clarification to your brief."
        )
        if status is not None:
            status.update(label="Intake response needs correction", state="error")
        with st.chat_message("assistant"):
            st.error(response)
        error_message = {"role": "assistant", "content": response}
        if active_job is not None:
            error_message["job_id"] = active_job["id"]
        st.session_state.messages.append(error_message)
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
