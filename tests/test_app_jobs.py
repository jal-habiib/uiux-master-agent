import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import app_jobs
import config
import job_store
import orchestrator
import planning
from streamlit.testing.v1 import AppTest


class AppJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "jobs.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_start_intake_job_supports_create_and_review_with_audit_fields(self):
        for mode in ("create", "review"):
            job = app_jobs.start_intake_job(mode, self.db_path)

            self.assertEqual(job["mode"], mode)
            self.assertEqual(job["state"], "INTAKE")
            history = orchestrator.list_transitions(job["id"], self.db_path)
            self.assertEqual(len(history), 2)
            intake = history[1]
            self.assertEqual(intake["from_state"], "CREATED")
            self.assertEqual(intake["from_revision"], 0)
            self.assertEqual(intake["to_state"], "INTAKE")
            self.assertEqual(intake["actor"], app_jobs.APP_ACTOR)
            self.assertEqual(intake["reason"], "Initialize intake stage from Streamlit UI")
            self.assertEqual(intake["metadata"], app_jobs.INTAKE_METADATA)

    def test_lists_and_resumes_intake_jobs(self):
        job = app_jobs.start_intake_job("create", self.db_path)

        recoverable = app_jobs.list_recoverable_intake_jobs(self.db_path)

        self.assertEqual([item["id"] for item in recoverable], [job["id"]])
        self.assertEqual(app_jobs.resume_intake_job(job["id"], self.db_path), job)

    def test_resume_rejects_missing_and_non_intake_jobs(self):
        with self.assertRaises(job_store.JobNotFoundError):
            app_jobs.resume_intake_job("missing-job", self.db_path)

        job = orchestrator.create_job("create", "test", "setup", self.db_path)
        with self.assertRaises(job_store.ValidationError):
            app_jobs.resume_intake_job(job["id"], self.db_path)

        intake_job = app_jobs.start_intake_job("create", self.db_path)
        orchestrator.transition_job(
            intake_job["id"],
            "PLANNING",
            "test",
            "advance for test",
            intake_job["revision"],
            db_path=self.db_path,
        )
        with self.assertRaises(job_store.ValidationError):
            app_jobs.resume_intake_job(intake_job["id"], self.db_path)

    def test_start_does_not_persist_prompt_text(self):
        prompt = "private prompt that must never be persisted in job audit data"
        job = app_jobs.start_intake_job("create", self.db_path)
        transitions = orchestrator.list_transitions(job["id"], self.db_path)

        for transition in transitions:
            self.assertNotIn(prompt, transition["reason"])
            self.assertNotIn(prompt, str(transition["metadata"]))

    def test_messages_for_job_shows_legacy_messages_without_active_job(self):
        messages = [
            {"role": "user", "content": "legacy"},
            {"role": "assistant", "content": "job message", "job_id": "job-a"},
        ]
        self.assertEqual(
            app_jobs.messages_for_job(messages, None),
            [{"role": "user", "content": "legacy"}],
        )

    def test_messages_for_job_isolates_job_context(self):
        messages = [
            {"role": "user", "content": "legacy"},
            {"role": "user", "content": "job a", "job_id": "job-a"},
            {"role": "assistant", "content": "job b", "job_id": "job-b"},
        ]
        self.assertEqual(
            app_jobs.messages_for_job(messages, "job-a"),
            [{"role": "user", "content": "job a", "job_id": "job-a"}],
        )

    def test_messages_for_job_does_not_mutate_input(self):
        messages = [
            {"role": "user", "content": "job a", "job_id": "job-a"},
            {"role": "assistant", "content": "job b", "job_id": "job-b"},
        ]
        original = [message.copy() for message in messages]
        app_jobs.messages_for_job(messages, "job-a")
        self.assertEqual(messages, original)

    def _write_artifacts(self, active_job_id, refs=None):
        run_id = str(uuid.uuid4())
        artifact_dir = self.root / "job_artifacts" / active_job_id / run_id
        artifact_dir.mkdir(parents=True)
        brief = {"product_name": "Active job"}
        (artifact_dir / "product_brief.json").write_text(
            json.dumps(brief), encoding="utf-8"
        )
        generated_ref = (
            f"job_artifacts/{active_job_id}/{run_id}/product_brief.json"
        )
        return artifact_dir, brief, refs or [generated_ref]

    def test_planning_job_recovery_loads_its_saved_artifacts(self):
        job = app_jobs.start_intake_job("create", self.db_path)
        artifact_dir, brief, refs = self._write_artifacts(job["id"])
        orchestrator.record_checkpoint(
            job["id"],
            "INTAKE",
            "test",
            "Saved artifacts",
            refs,
            {"schema_version": "1.0", "status": "needs_input"},
            expected_revision=job["revision"],
            db_path=self.db_path,
        )
        orchestrator.transition_job(
            job["id"],
            "PLANNING",
            "test",
            "Advance for recovery",
            job["revision"],
            artifact_refs=refs,
            db_path=self.db_path,
        )

        recovered = app_jobs.resume_ui_job(job["id"], self.db_path)
        saved = app_jobs.saved_planning_artifacts(recovered, self.root)

        self.assertEqual(recovered["state"], "PLANNING")
        self.assertEqual(saved, {"product_brief.json": brief})
        self.assertTrue(artifact_dir.is_dir())
        self.assertEqual(
            [item["id"] for item in app_jobs.list_recoverable_ui_jobs(self.db_path)],
            [job["id"]],
        )

    def test_loader_rejects_cross_job_traversal_and_unexpected_filenames(self):
        active_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        _, _, valid_refs = self._write_artifacts(active_id)
        run_id = valid_refs[0].split("/")[2]
        invalid_refs = (
            [f"job_artifacts/{other_id}/{run_id}/product_brief.json"],
            [f"job_artifacts/{active_id}/{run_id}/../product_brief.json"],
            [f"job_artifacts/{active_id}/{run_id}/secrets.json"],
        )
        job = {
            "id": active_id,
            "latest_checkpoint": {"stage": "INTAKE", "artifact_refs": []},
        }
        for refs in invalid_refs:
            with self.subTest(reference=refs[0]):
                job["latest_checkpoint"]["artifact_refs"] = refs
                with self.assertRaises(job_store.ValidationError):
                    app_jobs.saved_planning_artifacts(job, self.root)

    def test_loader_rejects_oversized_artifacts(self):
        active_id = str(uuid.uuid4())
        artifact_dir, _, refs = self._write_artifacts(active_id)
        (artifact_dir / "product_brief.json").write_bytes(
            b" " * (app_jobs.MAX_ARTIFACT_BYTES + 1)
        )
        job = {
            "id": active_id,
            "latest_checkpoint": {"stage": "INTAKE", "artifact_refs": refs},
        }
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_planning_artifacts(job, self.root)

    def test_loader_rejects_symlink_artifact_reference(self):
        active_id = str(uuid.uuid4())
        artifact_dir, _, refs = self._write_artifacts(active_id)
        outside = self.root / "outside.json"
        outside.write_text('{"product_name":"outside"}', encoding="utf-8")
        linked_file = artifact_dir / "product_brief.json"
        linked_file.unlink()
        try:
            linked_file.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"platform cannot create required symlink: {exc}")
        job = {
            "id": active_id,
            "latest_checkpoint": {"stage": "INTAKE", "artifact_refs": refs},
        }
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_planning_artifacts(job, self.root)

    def _app_script(self, directory):
        script = Path(directory) / "app.py"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "app.py", script)
        return script

    @staticmethod
    def _run_app(app):
        collection = Mock()
        chroma_client = Mock()
        chroma_client.get_or_create_collection.return_value = collection
        with patch("chromadb.PersistentClient", return_value=chroma_client):
            return app.run(timeout=20)

    @staticmethod
    def _filesystem_snapshot(path):
        if not path.exists() and not path.is_symlink():
            return None

        def metadata(entry):
            details = entry.lstat()
            return (
                details.st_mode,
                details.st_size,
                details.st_mtime_ns,
            )

        if path.is_file() or path.is_symlink():
            return metadata(path)
        return (
            metadata(path),
            tuple(
                (str(entry.relative_to(path)), metadata(entry))
                for entry in sorted(path.rglob("*"), key=lambda item: str(item))
            ),
        )

    def test_streamlit_startup_uses_isolated_paths(self):
        script = self._app_script(self.root)
        isolated_data = self.root / "isolated-data"
        production_data = config.DATA_DIR
        production_db = production_data / "jobs.sqlite3"
        production_artifacts = production_data / "job_artifacts"
        before = (
            self._filesystem_snapshot(production_db),
            self._filesystem_snapshot(production_artifacts),
        )
        with patch.object(config, "DATA_DIR", isolated_data):
            app = self._run_app(AppTest.from_file(str(script)))
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(isolated_data.exists())
        self.assertEqual(
            before,
            (
                self._filesystem_snapshot(production_db),
                self._filesystem_snapshot(production_artifacts),
            ),
        )

    def test_create_planning_job_rejects_another_prompt_without_running_agents(self):
        script = self._app_script(self.root)
        isolated_data = self.root / "isolated-data"
        with patch.object(config, "DATA_DIR", isolated_data):
            job = app_jobs.start_intake_job("create")
            orchestrator.record_checkpoint(
                job["id"],
                "INTAKE",
                "test",
                "Saved plan",
                [],
                {"schema_version": "1.0", "status": "completed"},
                expected_revision=job["revision"],
            )
            orchestrator.transition_job(
                job["id"],
                "PLANNING",
                "test",
                "Advance for UI test",
                job["revision"],
            )
            app = self._run_app(AppTest.from_file(str(script)))
            app.session_state["active_job_id"] = job["id"]
            with patch("agent.run") as agent_run, patch(
                "planning.plan_job"
            ) as plan_job:
                app.chat_input[0].set_value("A different brief")
                self._run_app(app)
            self.assertTrue(any("already has a saved plan" in item.value for item in app.error))
            agent_run.assert_not_called()
            plan_job.assert_not_called()

    def test_create_planning_contract_error_explains_same_job_recovery(self):
        script = self._app_script(self.root)
        isolated_data = self.root / "isolated-data"
        db_path = isolated_data / "jobs.sqlite3"
        with patch.object(config, "DATA_DIR", isolated_data):
            job = app_jobs.start_intake_job("create")
            app = self._run_app(AppTest.from_file(str(script)))
            app.session_state["active_job_id"] = job["id"]
            app = self._run_app(app)

            with patch(
                "planning.plan_job",
                side_effect=planning.PlanningResponseError(
                    "model response has invalid fields "
                    "(missing clarification_questions, status; "
                    "unknown next_steps, readiness, stage)"
                ),
            ):
                app.chat_input[0].set_value("Build a shop")
                app = self._run_app(app)

        errors = "\n".join(item.value for item in app.error)
        self.assertIn("required S7 JSON contract", errors)
        self.assertIn("No plan artifacts were saved", errors)
        self.assertIn("remains in INTAKE", errors)
        self.assertIn("Retry in the same job", errors)
        self.assertEqual(orchestrator.get_job(job["id"], db_path)["state"], "INTAKE")
        self.assertEqual(orchestrator.list_checkpoints(job["id"], db_path), [])

    def test_review_mode_keeps_agent_run_path_without_create_planning(self):
        script = self._app_script(self.root)
        isolated_data = self.root / "isolated-data"
        with patch.object(config, "DATA_DIR", isolated_data):
            job = app_jobs.start_intake_job("review")
            app = self._run_app(AppTest.from_file(str(script)))
            app.session_state["active_job_id"] = job["id"]
            with patch("agent.run", return_value="Review response") as agent_run, patch(
                "planning.plan_job"
            ) as plan_job:
                app.sidebar.radio[0].set_value("Review")
                app.chat_input[0].set_value("Review this interface")
                self._run_app(app)
            agent_run.assert_called_once()
            plan_job.assert_not_called()

    def test_create_planning_renders_completed_plan_and_persists_planning_state(self):
        script = self._app_script(self.root)
        isolated_data = self.root / "isolated-data"
        db_path = isolated_data / "jobs.sqlite3"
        brief = "A dashboard for tracking community garden harvests."
        plan = {"summary": "A harvest overview with garden-level progress."}

        with patch.object(config, "DATA_DIR", isolated_data):
            job = app_jobs.start_intake_job("create")
            app = self._run_app(AppTest.from_file(str(script)))
            app.session_state["active_job_id"] = job["id"]
            app = self._run_app(app)

            def complete_plan(job_id, messages):
                current = orchestrator.get_job(job_id, db_path)
                refs = []
                metadata = {"schema_version": "1.0", "status": "completed"}
                orchestrator.record_checkpoint(
                    job_id,
                    "INTAKE",
                    "test-planner",
                    "Saved test planning checkpoint",
                    refs,
                    metadata,
                    expected_revision=current["revision"],
                    db_path=db_path,
                )
                orchestrator.transition_job(
                    job_id,
                    "PLANNING",
                    "test-planner",
                    "Validated test plan artifacts saved",
                    current["revision"],
                    artifact_refs=refs,
                    metadata=metadata,
                    db_path=db_path,
                )
                return {
                    "status": "completed",
                    "product_brief": {"product_name": "Community Garden Tracker"},
                    "ux_plan": plan,
                    "job": orchestrator.get_job(job_id, db_path),
                }

            with patch("agent.run") as agent_run, patch(
                "planning.plan_job", side_effect=complete_plan
            ) as plan_job:
                app.chat_input[0].set_value(brief)
                self._run_app(app)

            plan_job.assert_called_once()
            called_job_id, called_messages = plan_job.call_args.args[:2]
            self.assertEqual(called_job_id, job["id"])
            self.assertEqual(
                called_messages,
                [
                    {
                        "role": "user",
                        "content": brief,
                        "image_paths": [],
                        "job_id": job["id"],
                    }
                ],
            )
            agent_run.assert_not_called()
            rendered_text = "\n".join(item.value for item in app.markdown)
            self.assertIn("Product brief and UX plan saved.", rendered_text)
            self.assertIn(plan["summary"], rendered_text)
            persisted = orchestrator.get_job(job["id"], db_path)
            self.assertEqual(persisted["state"], "PLANNING")

    def test_create_needs_input_follow_up_completes_same_job(self):
        script = self._app_script(self.root)
        isolated_data = self.root / "isolated-data"
        db_path = isolated_data / "jobs.sqlite3"
        initial_brief = "A booking tool for neighborhood sports courts."
        follow_up = "Players should be able to cancel up to one hour before."
        question = "Which user groups can manage court availability?"
        plan = {"summary": "A court search, booking, and management experience."}

        with patch.object(config, "DATA_DIR", isolated_data):
            job = app_jobs.start_intake_job("create")
            app = self._run_app(AppTest.from_file(str(script)))
            app.session_state["active_job_id"] = job["id"]
            app = self._run_app(app)
            planner_calls = []

            def plan_with_clarification(job_id, messages):
                planner_calls.append((job_id, messages))
                if len(planner_calls) == 1:
                    return {
                        "status": "needs_input",
                        "product_brief": {"product_name": "Neighborhood Court Booking"},
                        "ux_plan": None,
                        "clarification_questions": [question],
                        "job": orchestrator.get_job(job_id, db_path),
                    }

                current = orchestrator.get_job(job_id, db_path)
                refs = []
                metadata = {"schema_version": "1.0", "status": "completed"}
                orchestrator.record_checkpoint(
                    job_id,
                    "INTAKE",
                    "test-planner",
                    "Saved test planning checkpoint",
                    refs,
                    metadata,
                    expected_revision=current["revision"],
                    db_path=db_path,
                )
                orchestrator.transition_job(
                    job_id,
                    "PLANNING",
                    "test-planner",
                    "Validated test plan artifacts saved",
                    current["revision"],
                    artifact_refs=refs,
                    metadata=metadata,
                    db_path=db_path,
                )
                return {
                    "status": "completed",
                    "product_brief": {"product_name": "Neighborhood Court Booking"},
                    "ux_plan": plan,
                    "job": orchestrator.get_job(job_id, db_path),
                }

            with patch("agent.run") as agent_run, patch(
                "planning.plan_job", side_effect=plan_with_clarification
            ) as plan_job:
                app.chat_input[0].set_value(initial_brief)
                self._run_app(app)

                self.assertEqual(plan_job.call_count, 1)
                self.assertTrue(any(question in item.value for item in app.markdown))
                self.assertEqual(
                    orchestrator.get_job(job["id"], db_path)["state"], "INTAKE"
                )
                agent_run.assert_not_called()

                app.chat_input[0].set_value(follow_up)
                self._run_app(app)

            self.assertEqual(plan_job.call_count, 2)
            self.assertEqual(len(planner_calls), 2)
            self.assertEqual(
                [call[0] for call in planner_calls], [job["id"], job["id"]]
            )
            second_messages = planner_calls[1][1]
            user_messages = [
                message
                for message in second_messages
                if message.get("role") == "user"
            ]
            self.assertEqual(
                [message["content"] for message in user_messages],
                [initial_brief, follow_up],
            )
            self.assertEqual(
                planning._user_context(second_messages, job["id"]),
                f"{initial_brief}\n\n--- FOLLOW-UP USER MESSAGE ---\n\n{follow_up}",
            )
            self.assertTrue(
                any(
                    "Product brief and UX plan saved." in item.value
                    for item in app.markdown
                )
            )
            self.assertTrue(any(plan["summary"] in item.value for item in app.markdown))
            agent_run.assert_not_called()
            persisted = orchestrator.get_job(job["id"], db_path)
            self.assertEqual(persisted["state"], "PLANNING")


if __name__ == "__main__":
    unittest.main()
