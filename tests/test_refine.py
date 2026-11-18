import unittest
from types import SimpleNamespace

import build
import gate
import orchestrator
import refine
import verify
import test_verify as fixtures


class RefineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerifyTests("test_pass_saves_hashes_and_atomically_transitions_to_verify")
        self.fixture.setUp()
        self.job = self.fixture.job

    def tearDown(self):
        self.fixture.tearDown()

    def test_failed_verify_refines_into_new_immutable_run_and_returns_to_verify(self):
        failed = self.fixture._verify(
            status="FAIL", flows_json=fixtures._flows(),
            provider_call_fn=self.fixture._provider(),
            providers=self.fixture._eligible_provider(),
        )
        self.assertEqual(failed["job"]["state"], "VERIFY")
        envelope = {
            "schema_version": "1.0", "status": "completed",
            "pages": [{"page": "Garden overview", "filename": "index.html", "html": fixtures._html()}],
            "clarification_questions": [],
        }
        calls = []
        def fake_gateway(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(response=fixtures._response(envelope), provider={"provider_id": "test-provider", "model_id": "refine-model"}, attempts=1, failures=[])
        prior_checkpoints = orchestrator.list_checkpoints(self.job["id"], self.fixture.db_path)
        prior_build = next(item for item in reversed(prior_checkpoints) if item["metadata"].get("integration") == "s9-build")
        result = refine.refine_job(
            self.job["id"], db_path=self.fixture.db_path, artifact_root=self.fixture.data_root,
            gateway_call_fn=fake_gateway, providers=self.fixture._eligible_provider(),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["job"]["state"], "VERIFY")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["manifest"]["run_id"], result["run_id"])
        self.assertNotEqual(result["run_id"], prior_build["metadata"]["run_id"])
        checkpoints = orchestrator.list_checkpoints(self.job["id"], self.fixture.db_path)
        refined = next(item for item in reversed(checkpoints) if item["metadata"].get("integration") == "s12-refine")
        self.assertEqual(refined["stage"], "REFINE")
        self.assertEqual(refined["metadata"]["parent_build_run_id"], prior_build["metadata"]["run_id"])
        with self.assertRaises(gate.GateError):
            gate.gate_job(self.job["id"], db_path=self.fixture.db_path, artifact_root=self.fixture.data_root)
        verified_again = verify.verify_job(
            self.job["id"], flows_json=fixtures._flows(), db_path=self.fixture.db_path,
            artifact_root=self.fixture.data_root, browser_qa_fn=lambda *_a, **_k: fixtures._browser_result("PASS"),
            provider_call_fn=self.fixture._provider(), providers=self.fixture._eligible_provider(),
        )
        self.assertEqual(verified_again["job"]["state"], "VERIFY")
        self.assertEqual(verified_again["report"]["source_artifact_refs"], result["artifact_refs"])


    def test_provider_failure_leaves_refine_recoverable_and_retry_succeeds(self):
        self.fixture._verify(
            status="FAIL", flows_json=fixtures._flows(),
            provider_call_fn=self.fixture._provider(),
            providers=self.fixture._eligible_provider(),
        )
        def unavailable(*_args, **_kwargs):
            return SimpleNamespace(response=None, provider=None, attempts=1, failures=["unavailable"])
        with self.assertRaises(refine.RefineError):
            refine.refine_job(
                self.job["id"], db_path=self.fixture.db_path, artifact_root=self.fixture.data_root,
                gateway_call_fn=unavailable, providers=self.fixture._eligible_provider(),
            )
        self.assertEqual(orchestrator.get_job(self.job["id"], self.fixture.db_path)["state"], "REFINE")
        envelope = {
            "schema_version": "1.0", "status": "completed",
            "pages": [{"page": "Garden overview", "filename": "index.html", "html": fixtures._html()}],
            "clarification_questions": [],
        }
        retried = refine.refine_job(
            self.job["id"], db_path=self.fixture.db_path, artifact_root=self.fixture.data_root,
            gateway_call_fn=lambda *_a, **_k: SimpleNamespace(response=fixtures._response(envelope), provider={"provider_id": "test-provider", "model_id": "refine-model"}, attempts=1, failures=[]),
            providers=self.fixture._eligible_provider(),
        )
        self.assertEqual(retried["job"]["state"], "VERIFY")

    def test_refine_rejects_non_failed_verify_report(self):
        self.fixture._verify(flows_json=fixtures._flows(), provider_call_fn=self.fixture._provider(), providers=self.fixture._eligible_provider())
        with self.assertRaises(refine.RefineError):
            refine.refine_job(self.job["id"], db_path=self.fixture.db_path, artifact_root=self.fixture.data_root, gateway_call_fn=lambda *_a, **_k: None)


if __name__ == "__main__":
    unittest.main()
