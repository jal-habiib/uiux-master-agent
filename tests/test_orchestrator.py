import tempfile
import unittest
from pathlib import Path
import sqlite3
from unittest import mock

import job_store
import orchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def create(self, mode="create"):
        return orchestrator.create_job(mode, "test", "start", self.db_path)

    def move(self, job, state, metadata=None, key=None):
        return orchestrator.transition_job(
            job["id"],
            state,
            "worker",
            f"move to {state}",
            orchestrator.get_job(job["id"], self.db_path)["revision"],
            metadata=metadata,
            idempotency_key=key,
            artifact_refs=["artifact://one"],
            db_path=self.db_path,
        )

    def test_create_and_review_start_with_auditable_created_history(self):
        for mode in ("create", "review"):
            job = self.create(mode)
            self.assertEqual(job["state"], "CREATED")
            history = orchestrator.list_transitions(job["id"], self.db_path)
            self.assertEqual(len(history), 1)
            self.assertIsNone(history[0]["from_state"])
            self.assertEqual(history[0]["from_revision"], 0)
            self.assertEqual(history[0]["to_state"], "CREATED")
            self.assertTrue(history[0]["timestamp"])
            self.assertEqual(history[0]["actor"], "test")
            self.assertEqual(history[0]["reason"], "start")

    def test_full_create_path_supports_refine_loop_and_gate(self):
        job = self.create()
        for state in ("INTAKE", "PLANNING", "DESIGN", "BUILD", "VERIFY", "REFINE", "VERIFY"):
            self.move(job, state)
        self.move(job, "GATE_AND_DELIVER", {"gate_status": "PASS"})
        self.move(job, "DELIVERED", {"gate_status": "PASS"})
        final = orchestrator.get_job(job["id"], self.db_path)
        self.assertEqual(final["state"], "DELIVERED")
        self.assertEqual(final["revision"], 9)

    def test_review_shortcut_rejects_create_only_stages(self):
        job = self.create("review")
        self.move(job, "INTAKE")
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "PLANNING")
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path)["state"], "INTAKE")
        self.move(job, "VERIFY")
        self.move(job, "GATE_AND_DELIVER", {"gate_status": "PASS"})

    def test_create_path_cannot_skip_intake_to_verify(self):
        job = self.create()
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "VERIFY")
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path)["state"], "CREATED")
        self.assertEqual(len(orchestrator.list_transitions(job["id"], self.db_path)), 1)

    def test_invalid_skips_terminal_and_gate_failures_do_not_change_history(self):
        job = self.create()
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "DESIGN")
        self.assertEqual(len(orchestrator.list_transitions(job["id"], self.db_path)), 1)
        for state in ("INTAKE", "PLANNING", "DESIGN", "BUILD", "VERIFY", "GATE_AND_DELIVER"):
            self.move(job, state)
        for status in ("FAIL", "NOT_VERIFIED", None):
            with self.assertRaises(job_store.ValidationError):
                self.move(job, "DELIVERED", {} if status is None else {"gate_status": status})
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path)["revision"], 6)

    def test_control_states_preserve_and_only_resume_to_saved_stage(self):
        job = self.create()
        self.move(job, "INTAKE")
        self.move(job, "PLANNING")
        self.move(job, "WAITING_FOR_USER")
        paused = orchestrator.get_job(job["id"], self.db_path)
        self.assertEqual(paused["resume_state"], "PLANNING")
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "DESIGN")
        self.move(job, "PLANNING")
        self.assertIsNone(orchestrator.get_job(job["id"], self.db_path)["resume_state"])

    def test_revision_stale_and_idempotent_retry(self):
        job = self.create()
        self.move(job, "INTAKE", key="k1")
        before = orchestrator.get_job(job["id"], self.db_path)
        with self.assertRaises(job_store.ValidationError):
            orchestrator.transition_job(
                job["id"], "PLANNING", "worker", "stale", 0, db_path=self.db_path
            )
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path), before)
        first = self.move(job, "PLANNING", key="k2")
        original_revision = first["from_revision"]
        retry = orchestrator.transition_job(
            job["id"],
            "PLANNING",
            "worker",
            "move to PLANNING",
            original_revision,
            artifact_refs=["artifact://one"],
            idempotency_key="k2",
            db_path=self.db_path,
        )
        self.assertEqual(retry, first)
        self.assertEqual(len(orchestrator.list_transitions(job["id"], self.db_path)), 3)
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "DESIGN", key="k2")
        with self.assertRaises(job_store.ValidationError):
            orchestrator.transition_job(
                job["id"],
                "PLANNING",
                "worker",
                "move to PLANNING",
                original_revision + 1,
                artifact_refs=["artifact://one"],
                idempotency_key="k2",
                db_path=self.db_path,
            )
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path)["revision"], 2)
        self.assertEqual(len(orchestrator.list_transitions(job["id"], self.db_path)), 3)

    def test_stale_snapshot_rechecks_idempotency_before_policy_validation(self):
        job = self.create()
        committed = self.move(job, "INTAKE", key="race-key")
        before = orchestrator.get_job(job["id"], self.db_path)
        history_before = orchestrator.list_transitions(job["id"], self.db_path)

        with mock.patch.object(
            job_store,
            "find_idempotent_transition",
            side_effect=[None, committed],
        ):
            retry = orchestrator.transition_job(
                job["id"],
                "INTAKE",
                "worker",
                "move to INTAKE",
                committed["from_revision"],
                artifact_refs=["artifact://one"],
                idempotency_key="race-key",
                db_path=self.db_path,
            )

        self.assertEqual(retry, committed)
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path), before)
        self.assertEqual(orchestrator.list_transitions(job["id"], self.db_path), history_before)

    def test_checkpoints_reopen_without_incrementing_revision_and_recovery(self):
        job = self.create()
        checkpoint = orchestrator.record_checkpoint(
            job["id"], "CREATED", "worker", "saved", ["artifact://brief"], {"version": 1}, 0, self.db_path
        )
        self.assertEqual(checkpoint["revision"], 0)
        orchestrator.initialize_store(self.db_path)
        self.assertEqual(len(orchestrator.list_checkpoints(job["id"], self.db_path)), 1)
        self.move(job, "INTAKE")
        recovered = orchestrator.list_recoverable_jobs(self.db_path)
        self.assertEqual(recovered[0]["id"], job["id"])
        self.assertEqual(recovered[0]["latest_checkpoint"]["stage"], "CREATED")
        self.assertNotEqual(recovered[0]["state"], "DELIVERED")

    def test_checkpoints_must_match_active_or_persisted_resume_stage(self):
        job = self.create()
        with self.assertRaises(job_store.ValidationError):
            orchestrator.record_checkpoint(
                job["id"], "INTAKE", "worker", "wrong", db_path=self.db_path
            )
        self.move(job, "INTAKE")
        self.move(job, "BLOCKED")
        with self.assertRaises(job_store.ValidationError):
            orchestrator.record_checkpoint(
                job["id"], "CREATED", "worker", "wrong", db_path=self.db_path
            )
        orchestrator.record_checkpoint(
            job["id"], "INTAKE", "worker", "saved", db_path=self.db_path
        )
        self.move(job, "INTAKE")
        self.move(job, "CANCELLED")
        with self.assertRaises(job_store.ValidationError):
            orchestrator.record_checkpoint(
                job["id"], "INTAKE", "worker", "terminal", db_path=self.db_path
            )

    def test_stale_checkpoint_revision_does_not_change_history(self):
        job = self.create()
        self.move(job, "INTAKE")
        before = orchestrator.list_checkpoints(job["id"], self.db_path)
        with self.assertRaises(job_store.ValidationError):
            orchestrator.record_checkpoint(
                job["id"],
                "INTAKE",
                "worker",
                "stale",
                expected_revision=0,
                db_path=self.db_path,
            )
        self.assertEqual(orchestrator.list_checkpoints(job["id"], self.db_path), before)

    def test_cancel_active_and_control_jobs_but_not_terminal_jobs(self):
        active = self.create()
        orchestrator.cancel_job(active["id"], "user", "stop", 0, db_path=self.db_path)
        self.assertEqual(orchestrator.get_job(active["id"], self.db_path)["state"], "CANCELLED")
        control = self.create()
        self.move(control, "INTAKE")
        self.move(control, "BLOCKED")
        revision = orchestrator.get_job(control["id"], self.db_path)["revision"]
        orchestrator.cancel_job(control["id"], "user", "stop", revision, db_path=self.db_path)
        self.assertEqual(orchestrator.get_job(control["id"], self.db_path)["state"], "CANCELLED")
        with self.assertRaises(job_store.ValidationError):
            orchestrator.cancel_job(active["id"], "user", "again", 1, db_path=self.db_path)

    def test_schema_version_rejects_newer_database(self):
        orchestrator.initialize_store(self.db_path)
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE schema_metadata SET value = '999' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(job_store.SchemaVersionError):
            orchestrator.initialize_store(self.db_path)

    def test_schema_version_rejects_older_database_without_migration(self):
        orchestrator.initialize_store(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE schema_metadata SET value = '0' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(job_store.SchemaVersionError):
            orchestrator.initialize_store(self.db_path)

    def test_validation_does_not_coerce_metadata(self):
        job = self.create()
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "INTAKE", metadata=["not", "an", "object"])
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "INTAKE", metadata={"bad": float("nan")})
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "INTAKE", metadata={"bad": (1, 2)})
        with self.assertRaises(job_store.ValidationError):
            self.move(job, "INTAKE", metadata={1: "bad"})
        with self.assertRaises(job_store.ValidationError):
            orchestrator.transition_job(
                job["id"],
                "INTAKE",
                "worker",
                "move",
                0,
                artifact_refs=["   "],
                db_path=self.db_path,
            )
        self.assertEqual(len(orchestrator.list_transitions(job["id"], self.db_path)), 1)

    def test_invalid_expected_revision_is_validated_before_idempotency_lookup(self):
        job = self.create()
        self.move(job, "INTAKE", key="revision-key")
        with self.assertRaises(job_store.ValidationError):
            orchestrator.transition_job(
                job["id"],
                "INTAKE",
                "worker",
                "move to INTAKE",
                True,
                artifact_refs=["artifact://one"],
                idempotency_key="revision-key",
                db_path=self.db_path,
            )

    def test_sql_failure_rolls_back_state_and_history(self):
        job = self.create()
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """CREATE TRIGGER fail_job_update BEFORE UPDATE ON jobs
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END"""
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            self.move(job, "INTAKE")
        self.assertEqual(orchestrator.get_job(job["id"], self.db_path)["revision"], 0)
        self.assertEqual(len(orchestrator.list_transitions(job["id"], self.db_path)), 1)


if __name__ == "__main__":
    unittest.main()
