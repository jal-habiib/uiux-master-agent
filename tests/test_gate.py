import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

import gate
import job_store
import orchestrator
from test_verify import VerifyTests, _flows


class GateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = VerifyTests("test_pass_saves_hashes_and_atomically_transitions_to_verify")
        self.fixture.setUp()
        self.db_path = self.fixture.db_path
        self.data_root = self.fixture.data_root
        self.job = self.fixture.job

    def tearDown(self):
        self.fixture.tearDown()

    def _verify(self, status="PASS", flows=True):
        kwargs = {"flows_json": _flows()} if flows else {}
        kwargs.setdefault("provider_call_fn", self.fixture._provider())
        kwargs.setdefault("providers", self.fixture._eligible_provider())
        return self.fixture._verify(status=status, **kwargs)

    def test_pass_creates_immutable_package_and_delivers(self):
        verified = self._verify()
        result = gate.gate_job(
            self.job["id"], db_path=self.db_path, artifact_root=self.data_root
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["job"]["state"], "DELIVERED")
        self.assertEqual(result["report"]["gate_status"], "PASS")
        self.assertIsNotNone(result["package_bytes"])
        self.assertLessEqual(len(result["package_bytes"]), gate.MAX_PACKAGE_BYTES)
        with zipfile.ZipFile(__import__("io").BytesIO(result["package_bytes"])) as archive:
            names = set(archive.namelist())
        self.assertIn("README_DELIVERY.md", names)
        self.assertIn("qa/verify_report.json", names)
        self.assertIn("build/index.html", names)
        self.assertIn("artifacts/product_brief.json", names)
        saved_job = orchestrator.get_job(self.job["id"], self.db_path)
        saved_job["checkpoints"] = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        saved = gate.load_saved_gate(saved_job, self.data_root)
        self.assertEqual(saved["report"]["status"], "PASS")
        self.assertEqual(
            hashlib.sha256(saved["package_bytes"]).hexdigest(),
            saved["report"]["package_sha256"],
        )
        self.assertEqual(len(verified["report"]["source_sha256"]), 2)

    def test_failed_gate_stays_in_verify_and_never_packages(self):
        self._verify(status="FAIL")
        result = gate.gate_job(
            self.job["id"], db_path=self.db_path, artifact_root=self.data_root
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["package_bytes"])
        self.assertEqual(result["job"]["state"], "VERIFY")
        self.assertIn("horizontal_overflow", result["report"]["failed_gates"])

    def test_gate_rejects_missing_flow_evidence_before_verify_completes(self):
        self._verify(flows=False)
        with self.assertRaises(gate.GateError):
            gate.gate_job(
                self.job["id"], db_path=self.db_path, artifact_root=self.data_root
            )
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "BUILD")

    def test_tampered_verified_page_is_rejected(self):
        self._verify()
        build_checkpoint = next(
            item for item in reversed(orchestrator.list_checkpoints(self.job["id"], self.db_path))
            if item["metadata"].get("integration") == "s9-build"
        )
        page_ref = next(ref for ref in build_checkpoint["artifact_refs"] if ref.endswith(".html"))
        page_path = self.data_root / page_ref
        page_path.write_text("<!doctype html><html><head><title>tampered</title></head><body></body></html>", encoding="utf-8")
        with self.assertRaises(gate.GateError):
            gate.gate_job(self.job["id"], db_path=self.db_path, artifact_root=self.data_root)
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "VERIFY")

    def test_wrong_state_is_rejected_without_side_effects(self):
        with self.assertRaises(gate.GateError):
            gate.gate_job(self.job["id"], db_path=self.db_path, artifact_root=self.data_root)
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "BUILD")

    def test_delivery_finalization_can_resume_from_gate_state(self):
        self._verify()
        original = gate.finalize_delivery

        def fail_once(*args, **kwargs):
            raise job_store.ValidationError("simulated interruption")

        with unittest.mock.patch.object(gate, "finalize_delivery", side_effect=fail_once):
            with self.assertRaises(job_store.ValidationError):
                gate.gate_job(self.job["id"], db_path=self.db_path, artifact_root=self.data_root)
        pending = orchestrator.get_job(self.job["id"], self.db_path)
        self.assertEqual(pending["state"], "GATE_AND_DELIVER")
        pending["checkpoints"] = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        saved = gate.load_saved_gate(pending, self.data_root)
        self.assertIsNotNone(saved["package_bytes"])
        result = original(self.job["id"], db_path=self.db_path, artifact_root=self.data_root)
        self.assertEqual(result["job"]["state"], "DELIVERED")


if __name__ == "__main__":
    unittest.main()
