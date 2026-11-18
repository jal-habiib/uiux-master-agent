import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app_jobs
import build
import design
import job_store
import orchestrator
import planning
import verify


def _brief():
    return {
        "schema_version": "1.0",
        "product_name": "Garden Tracker",
        "problem_statement": "Gardeners need to coordinate planting work.",
        "target_users": ["community gardeners"],
        "user_goals": ["review garden activity"],
        "business_goals": ["increase participation"],
        "core_features": ["overview"],
        "primary_user_flows": ["review the schedule"],
        "constraints": ["web"],
        "assumptions": ["users have browser access"],
        "open_questions": ["which roles can edit schedules"],
        "success_metrics": ["schedule visits"],
        "acceptance_criteria": ["readable at mobile widths"],
        "target_platform": "web",
        "output_target": "HTML + Tailwind",
    }


def _plan():
    return {
        "schema_version": "1.0",
        "summary": "A garden overview with a readable schedule.",
        "information_architecture": [
            {"page": "Garden overview", "purpose": "Review activity", "sections": ["Schedule"]}
        ],
        "primary_journeys": [
            {"name": "Review schedule", "steps": ["Open overview"], "success_outcome": "Schedule reviewed"}
        ],
        "interaction_inventory": [
            {"element": "Navigation link", "behavior": "Open overview", "states": ["default"]}
        ],
        "content_requirements": ["Garden names"],
        "accessibility_requirements": ["Keyboard-operable navigation"],
        "responsive_requirements": ["Reflow on narrow screens"],
        "risks": ["Edit permissions are unspecified"],
        "decisions": ["Use HTML + Tailwind"],
    }


def _design_envelope():
    return {
        "schema_version": "1.0",
        "status": "completed",
        "design_spec": {
            "schema_version": "1.0",
            "design_direction": "A calm garden workspace.",
            "brand_attributes": ["[supplied] Community gardening tool"],
            "page_specs": [
                {
                    "page": "Garden overview",
                    "purpose": "Review garden activity.",
                    "layout": "A responsive schedule-first layout.",
                    "sections": ["Schedule"],
                    "primary_action": "Review the schedule.",
                    "responsive_behavior": "Stack content on narrow screens.",
                }
            ],
            "component_specs": [
                {
                    "component": "Navigation",
                    "purpose": "Move between pages.",
                    "variants": ["primary"],
                    "states": ["default", "focus"],
                    "accessibility": "Use native links with visible focus.",
                }
            ],
            "interaction_specs": [
                {
                    "interaction": "Open overview",
                    "trigger": "Activate the navigation link.",
                    "feedback": "Show the overview page.",
                    "error_behavior": "Keep the current page.",
                    "keyboard_behavior": "Use native link keyboard behavior.",
                }
            ],
            "content_guidance": ["Use concise dates."],
            "accessibility_checks": ["Use a single main landmark."],
            "responsive_rules": ["Keep content readable at 375px."],
            "implementation_constraints": ["Use semantic HTML."],
            "assumptions": [],
            "open_questions": [],
        },
        "design_tokens": {
            "schema_version": "1.0",
            "color": {
                "primary": "#176B4D", "secondary": "#D8E9D8", "accent": "#D58A35",
                "background": "#F8FAF6", "surface": "#FFFFFF", "text": "#17231D",
                "muted": "#53645A", "border": "#C9D4CB", "success": "#176B4D",
                "warning": "#805000", "danger": "#9B2C2C",
            },
            "typography": {
                "font_family": "Arial, sans-serif",
                "heading_scale": [1.25, 1.5, 2.0],
                "body_size": 1.0,
                "line_height": 1.5,
                "font_weights": [400, 500, 700],
            },
            "spacing": {"unit": 4, "scale": [4, 8, 12, 16, 24, 32]},
            "radius": ["0.25rem", "0.5rem"],
            "shadows": ["none", "0 2px 8px #00000020"],
            "breakpoints": {"mobile": 375, "tablet": 768, "desktop": 1440},
            "container": {"max_width": 1200, "page_gutter": 24},
        },
        "clarification_questions": [],
    }


def _html():
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Garden overview</title><style>body{padding:1rem;font-family:Arial,sans-serif}"
        "a:focus-visible{outline:2px solid #176b4d}"
        "@media (max-width:700px){body{padding:.5rem}}</style></head><body>"
        "<header><nav aria-label=\"Main\"><a href=\"index.html\">Overview</a></nav></header>"
        "<main class=\"page\"><h1>Garden overview</h1><section><h2>Schedule</h2>"
        "<p>Garden schedule.</p></section><article><h2>Updates</h2>"
        "<p>Latest garden activity.</p></article></main></body></html>"
    )


def _response(value):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))],
    )


def _gateway(response):
    return lambda *args, **kwargs: SimpleNamespace(
        response=response,
        provider={"provider_id": "test-provider", "model_id": "test-model"},
        attempts=1,
        failures=[],
    )


def _planning_response():
    return {
        "schema_version": "1.0",
        "status": "completed",
        "product_brief": _brief(),
        "ux_plan": _plan(),
        "clarification_questions": [],
    }


def _build_response():
    return {
        "schema_version": "1.0",
        "status": "completed",
        "pages": [
            {"page": "Garden overview", "filename": "index.html", "html": _html()}
        ],
        "clarification_questions": [],
    }


def _all_browser_gates(status="PASS"):
    return {
        name: {"status": status, "evidence": {"checked": True}}
        for name in verify._BROWSER_GATES
    }


def _browser_result(status="PASS"):
    return {
        "hard_gates": _all_browser_gates(status),
        "score": 85,
        "score_gate_passed": True,
        "overall_passed": status == "PASS",
    }


def _flows():
    return json.dumps(
        {
            "flows": [
                {
                    "name": "Open overview",
                    "steps": [{"action": "expect_visible", "selector": "main"}],
                }
            ]
        }
    )


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "jobs.sqlite3"
        self.data_root = self.root / "data"
        self.jobs = []
        self.job = self._make_build()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_build(self, mode="create"):
        job = app_jobs.start_intake_job(mode, self.db_path)
        self.jobs.append(job["id"])
        if mode != "create":
            return job
        planning.plan_job(
            job["id"],
            [{"role": "user", "content": "Track garden activity.", "job_id": job["id"]}],
            db_path=self.db_path,
            artifact_root=self.data_root / "job_artifacts",
            gateway_call_fn=_gateway(_response(_planning_response())),
        )
        design.design_job(
            job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            gateway_call_fn=_gateway(_response(_design_envelope())),
        )
        build.build_job(
            job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            gateway_call_fn=_gateway(_response(_build_response())),
        )
        return orchestrator.get_job(job["id"], self.db_path)

    def _verify(self, *, status="PASS", **kwargs):
        kwargs.setdefault("provider_call_fn", self._provider())
        kwargs.setdefault("providers", self._eligible_provider())
        return verify.verify_job(
            self.job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            browser_qa_fn=lambda *_args, **_kwargs: _browser_result(status),
            **kwargs,
        )

    def _provider(self, content=None):
        payload = content or {
            "schema_version": "1.0",
            "summary": "No additional source risks found.",
            "findings": [],
        }
        return lambda *_args, **_kwargs: SimpleNamespace(
            response=_response(payload),
            provider={"provider_id": "test-provider", "model_id": "review-model"},
            attempts=1,
            failures=[],
        )

    @staticmethod
    def _eligible_provider():
        return [
            {
                "provider_id": "test-provider",
                "model_id": "review-model",
                "enabled": True,
                "capabilities": {"text", "json_output"},
            }
        ]

    def _checkpoint(self, job_id=None, integration=None):
        checkpoints = orchestrator.list_checkpoints(job_id or self.job["id"], self.db_path)
        return next(
            item for item in reversed(checkpoints)
            if integration is None or item["metadata"].get("integration") == integration
        )

    def test_pass_saves_hashes_and_atomically_transitions_to_verify(self):
        calls = []

        def provider(*args, **kwargs):
            calls.append((args, kwargs))
            return self._provider()(*args, **kwargs)

        result = self._verify(
            flows_json=_flows(),
            providers=self._eligible_provider(),
            provider_call_fn=provider,
        )
        self.assertEqual(result["report"]["status"], "PASS")
        self.assertTrue(result["report"]["verified"])
        self.assertEqual(result["job"]["state"], "VERIFY")
        self.assertEqual(len(result["report"]["source_sha256"]), 2)
        self.assertEqual(
            set(result["report"]["source_sha256"]),
            set(result["report"]["source_artifact_refs"]),
        )
        build_transition = orchestrator.list_transitions(self.job["id"], self.db_path)[-1]
        checkpoint = self._checkpoint(integration=verify.VERIFY_INTEGRATION)
        self.assertEqual(build_transition["from_state"], "BUILD")
        self.assertEqual(build_transition["to_state"], "VERIFY")
        self.assertEqual(build_transition["from_revision"], self.job["revision"])
        self.assertEqual(checkpoint["stage"], "BUILD")
        self.assertEqual(checkpoint["artifact_refs"], result["artifact_refs"])
        self.assertEqual(
            checkpoint["metadata"]["report_sha256"],
            hashlib.sha256(
                (self.data_root / result["artifact_refs"][0]).read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(len(calls), 1)
        messages, tools, capabilities = calls[0][0][:3]
        self.assertEqual(tools, [])
        self.assertEqual(capabilities, {"text", "json_output"})
        self.assertEqual(calls[0][1]["response_format"], {"type": "json_object"})
        self.assertNotIn("Garden Tracker", messages[0]["content"])
        provider_data = json.loads(messages[1]["content"])
        self.assertIn("<main class=", provider_data[0]["html"])

    def test_failed_hard_gate_transitions_but_never_marks_report_verified(self):
        result = self._verify(flows_json=_flows(), status="FAIL")
        self.assertEqual(result["report"]["status"], "FAIL")
        self.assertFalse(result["report"]["verified"])
        self.assertEqual(result["report"]["hard_gates"]["horizontal_overflow"]["status"], "FAIL")
        self.assertEqual(result["job"]["state"], "VERIFY")
        with self.assertRaises(verify.VerifyError):
            verify._validate_report(
                {**result["report"], "verified": True},
                self.job["id"],
            )

    def test_missing_flow_evidence_is_needs_input_and_retry_does_not_rebuild(self):
        original_build_refs = self._checkpoint(integration=app_jobs.BUILD_INTEGRATION)["artifact_refs"]
        before = orchestrator.get_job(self.job["id"], self.db_path)
        result = self._verify()
        self.assertEqual(result["report"]["status"], "NEEDS_INPUT")
        self.assertFalse(result["report"]["verified"])
        self.assertEqual(
            result["report"]["hard_gates"]["required_user_flows"]["status"],
            "NOT_VERIFIED",
        )
        self.assertEqual(result["job"]["state"], "BUILD")
        self.assertEqual(result["job"]["revision"], before["revision"])
        self.assertEqual(self._checkpoint()["stage"], "BUILD")
        retry = self._verify(flows_json=_flows())
        self.assertEqual(retry["report"]["status"], "PASS")
        self.assertEqual(retry["job"]["state"], "VERIFY")
        self.assertEqual(
            self._checkpoint(integration=app_jobs.BUILD_INTEGRATION)["artifact_refs"],
            original_build_refs,
        )

    def test_browser_unavailability_is_never_passed_or_advanced(self):
        result = verify.verify_job(
            self.job["id"],
            flows_json=_flows(),
            db_path=self.db_path,
            artifact_root=self.data_root,
            browser_qa_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("browser missing")
            ),
        )
        self.assertEqual(result["report"]["status"], "NEEDS_INPUT")
        self.assertEqual(
            result["report"]["hard_gates"]["build_render"]["status"],
            "NOT_VERIFIED",
        )
        self.assertEqual(result["job"]["state"], "BUILD")
        self.assertNotIn("browser missing", str(result["report"]))

    def test_provider_failure_is_reported_as_unverified_supplemental_review(self):
        result = self._verify(
            flows_json=_flows(),
            providers=self._eligible_provider(),
            provider_call_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("provider payload must not be stored")
            ),
        )
        self.assertEqual(result["report"]["status"], "PASS")
        self.assertEqual(result["report"]["provider_review"]["status"], "NOT_VERIFIED")
        self.assertNotIn("provider payload must not be stored", str(result["report"]))

    def test_provider_routing_is_capability_based_and_untrusted_html_is_user_data(self):
        calls = []

        def provider(*args, **kwargs):
            calls.append((args, kwargs))
            return self._provider()(*args, **kwargs)

        result = verify._provider_review(
            [{"page": "Garden overview", "filename": "index.html", "html": _html()}],
            provider,
            self._eligible_provider(),
        )
        self.assertEqual(result["status"], "PASS")
        messages, tools, capabilities = calls[0][0][:3]
        self.assertEqual(tools, [])
        self.assertEqual(capabilities, {"text", "json_output"})
        self.assertNotIn("<main>", messages[0]["content"])
        provider_data = json.loads(messages[1]["content"])
        self.assertIn("<main class=", provider_data[0]["html"])

    def test_unavailable_provider_is_explicit_and_never_called(self):
        calls = []
        result = verify._provider_review(
            [{"page": "Garden overview", "filename": "index.html", "html": _html()}],
            lambda *args, **kwargs: calls.append((args, kwargs)),
            [{"enabled": True, "capabilities": {"text"}}],
        )
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertEqual(calls, [])

    def test_unsafe_html_fails_static_gate_and_is_not_reported_verified(self):
        checkpoint = self._checkpoint(integration=app_jobs.BUILD_INTEGRATION)
        page_ref = next(ref for ref in checkpoint["artifact_refs"] if ref.endswith("index.html"))
        path = self.data_root / page_ref
        path.write_text(_html().replace("</main>", "<script>alert(1)</script></main>"), encoding="utf-8")
        result = verify.verify_job(
            self.job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            browser_qa_fn=lambda *_args, **_kwargs: self.fail("unsafe HTML must not reach browser QA"),
        )
        self.assertEqual(result["report"]["status"], "FAIL")
        self.assertFalse(result["report"]["verified"])
        self.assertEqual(result["report"]["hard_gates"]["html_security"]["status"], "FAIL")
        self.assertEqual(result["job"]["state"], "VERIFY")

    def test_invalid_flow_schema_and_oversized_input_are_rejected(self):
        for flows_json in (
            '{"flows":[{"name":"bad","steps":[{"action":"eval","selector":"body"}]}]}',
            "x" * (64 * 1024 + 1),
        ):
            with self.subTest(length=len(flows_json)):
                with self.assertRaises(verify.VerifyError):
                    self._verify(flows_json=flows_json)
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "BUILD")

    def test_rejects_wrong_state_mode_and_noncanonical_job_ids(self):
        with self.assertRaisesRegex(verify.VerifyError, "canonical UUID"):
            verify.load_verify_inputs("../job", self.db_path, self.data_root)
        with self.assertRaisesRegex(verify.VerifyError, "canonical UUID"):
            verify.load_verify_inputs(self.job["id"].upper(), self.db_path, self.data_root)
        review_job = self._make_build("review")
        orchestrator.transition_job(
            review_job["id"], "VERIFY", "test", "review verify", review_job["revision"],
            db_path=self.db_path,
        )
        review_job = orchestrator.get_job(review_job["id"], self.db_path)
        with self.assertRaisesRegex(verify.VerifyError, "Create"):
            verify.load_verify_inputs(review_job["id"], self.db_path, self.data_root)
        final = orchestrator.get_job(self.job["id"], self.db_path)
        orchestrator.transition_job(
            final["id"], "VERIFY", "test", "wrong state", final["revision"],
            db_path=self.db_path,
        )
        with self.assertRaisesRegex(verify.VerifyError, "BUILD"):
            verify.load_verify_inputs(self.job["id"], self.db_path, self.data_root)

    def test_rejects_manifest_mismatch_and_cross_job_substitution(self):
        other = self._make_build()
        checkpoint = self._checkpoint(integration=app_jobs.BUILD_INTEGRATION)
        other_checkpoint = self._checkpoint(other["id"], app_jobs.BUILD_INTEGRATION)
        connection = sqlite3.connect(self.db_path)
        try:
            metadata = dict(checkpoint["metadata"])
            metadata["artifact_refs"] = other_checkpoint["artifact_refs"]
            metadata["run_id"] = other_checkpoint["metadata"]["run_id"]
            connection.execute(
                "UPDATE job_checkpoints SET artifact_refs = ?, metadata = ? WHERE id = ?",
                (
                    json.dumps(other_checkpoint["artifact_refs"]),
                    json.dumps(metadata),
                    checkpoint["id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(job_store.ValidationError):
            verify.load_verify_inputs(self.job["id"], self.db_path, self.data_root)
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "BUILD")

    def test_rejects_manifest_source_ref_mismatch(self):
        checkpoint = self._checkpoint(integration=app_jobs.BUILD_INTEGRATION)
        manifest_ref = next(ref for ref in checkpoint["artifact_refs"] if ref.endswith("build_manifest.json"))
        path = self.data_root / manifest_ref
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["source_artifact_refs"] = [f"job_artifacts/{uuid.uuid4()}/bad/ux_plan.json"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(job_store.ValidationError):
            verify.load_verify_inputs(self.job["id"], self.db_path, self.data_root)

    def test_rejects_build_page_symlink(self):
        checkpoint = self._checkpoint(integration=app_jobs.BUILD_INTEGRATION)
        page_ref = next(ref for ref in checkpoint["artifact_refs"] if ref.endswith("index.html"))
        page = self.data_root / page_ref
        outside = self.root / "outside.html"
        outside.write_text(_html(), encoding="utf-8")
        page.unlink()
        try:
            page.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"platform cannot create required symlink: {exc}")
        with self.assertRaises(job_store.ValidationError):
            verify.load_verify_inputs(self.job["id"], self.db_path, self.data_root)

    def test_saved_report_resume_is_integrity_checked_without_rerunning(self):
        result = self._verify(flows_json=_flows())
        resumed = app_jobs.resume_ui_job(self.job["id"], self.db_path)
        with patch.object(verify, "verify_job", side_effect=AssertionError("must not rerun")):
            report = verify.load_saved_verification(resumed, self.data_root)
        self.assertEqual(report["run_id"], result["run_id"])
        self.assertEqual(report["status"], "PASS")
        report_path = self.data_root / result["artifact_refs"][0]
        damaged = json.loads(report_path.read_text(encoding="utf-8"))
        damaged["status"] = "NEEDS_INPUT"
        report_path.write_text(json.dumps(damaged), encoding="utf-8")
        with self.assertRaisesRegex(verify.VerifyError, "status"):
            verify.load_saved_verification(resumed, self.data_root)

    def test_stale_transition_rolls_back_current_report_and_checkpoint(self):
        before_checkpoints = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        with patch.object(
            verify.orchestrator,
            "transition_job",
            side_effect=job_store.ValidationError("stale revision"),
        ):
            with self.assertRaisesRegex(job_store.ValidationError, "stale revision"):
                self._verify(flows_json=_flows())
        after = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        self.assertEqual(after, before_checkpoints)
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/verify_report.json"))
        )

    def test_sql_transition_failure_rolls_back_checkpoint_and_artifact(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """CREATE TRIGGER fail_verify_transition BEFORE UPDATE ON jobs
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END"""
            )
            connection.commit()
        finally:
            connection.close()
        before = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            self._verify(flows_json=_flows())
        self.assertEqual(orchestrator.list_checkpoints(self.job["id"], self.db_path), before)
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/verify_report.json"))
        )
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "BUILD")

    def test_report_artifact_collision_preserves_existing_run(self):
        inputs = verify.load_verify_inputs(self.job["id"], self.db_path, self.data_root)
        report = verify._evaluate(inputs, None, lambda *_a, **_k: {}, None, None)
        fixed_id = uuid.uuid4()
        with patch.object(verify.uuid, "uuid4", return_value=fixed_id):
            first = verify._save_report(self.job["id"], copy.deepcopy(report), self.data_root)
            original = (self.data_root / first[0]).read_bytes()
            with self.assertRaises(FileExistsError):
                verify._save_report(self.job["id"], copy.deepcopy(report), self.data_root)
        self.assertEqual((self.data_root / first[0]).read_bytes(), original)

    def test_report_writer_enforces_size_limit_and_atomic_cleanup(self):
        target = self.root / "oversized.json"
        with self.assertRaises(verify.VerifyError):
            verify._write_atomic(target, b"x" * (verify.MAX_REPORT_BYTES + 1))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
