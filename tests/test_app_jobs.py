import tempfile
import unittest
from pathlib import Path

import app_jobs
import job_store
import orchestrator


class AppJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.sqlite3"

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


if __name__ == "__main__":
    unittest.main()
