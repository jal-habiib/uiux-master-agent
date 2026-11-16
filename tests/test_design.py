import copy
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app_jobs
import design
import job_store
import orchestrator
import planning


def _one(value):
    return [value]


def _brief():
    return {
        "schema_version": "1.0",
        "product_name": "Community Garden Tracker",
        "problem_statement": "Gardeners need to coordinate planting and harvests.",
        "target_users": _one("community gardeners"),
        "user_goals": _one("coordinate seasonal work"),
        "business_goals": _one("increase participation"),
        "core_features": _one("garden overview"),
        "primary_user_flows": _one("review the garden schedule"),
        "constraints": _one("web"),
        "assumptions": _one("users can access a browser"),
        "open_questions": _one("which garden roles can edit schedules"),
        "success_metrics": _one("successful schedule updates"),
        "acceptance_criteria": _one("schedule is readable on mobile"),
        "target_platform": "web",
        "output_target": "HTML + Tailwind",
    }


def _plan():
    return {
        "schema_version": "1.0",
        "summary": "A garden overview and schedule.",
        "information_architecture": [
            {"page": "Garden overview", "purpose": "Review progress", "sections": _one("Schedule")}
        ],
        "primary_journeys": [
            {"name": "Review schedule", "steps": _one("Open the overview"), "success_outcome": "Schedule reviewed"}
        ],
        "interaction_inventory": [
            {"element": "Schedule filter", "behavior": "Filters entries", "states": _one("default")}
        ],
        "content_requirements": _one("Garden names"),
        "accessibility_requirements": _one("Keyboard-operable filters"),
        "responsive_requirements": _one("Reflow at mobile widths"),
        "risks": _one("Role permissions are unspecified"),
        "decisions": _one("Use HTML + Tailwind"),
    }


def _spec():
    return {
        "schema_version": "1.0",
        "design_direction": "A calm, practical garden workspace with clear seasonal priorities.",
        "brand_attributes": ["[supplied] Community-focused gardening tool"],
        "page_specs": [
            {
                "page": "Garden overview",
                "purpose": "Help gardeners review current progress.",
                "layout": "A responsive single-column layout with a schedule-first hierarchy.",
                "sections": ["Schedule", "Garden progress"],
                "primary_action": "Review the upcoming schedule.",
                "responsive_behavior": "Stack cards on narrow screens and use a wider two-column layout on desktop.",
            }
        ],
        "component_specs": [
            {
                "component": "Schedule filter",
                "purpose": "Narrow schedule entries by time period.",
                "variants": ["compact", "full-width"],
                "states": ["default", "focused", "disabled"],
                "accessibility": "Use a labeled native select with visible keyboard focus.",
            }
        ],
        "interaction_specs": [
            {
                "interaction": "Filter the schedule",
                "trigger": "The user changes the time-period select.",
                "feedback": "Show only matching schedule entries.",
                "error_behavior": "Keep the current list and announce that filtering failed.",
                "keyboard_behavior": "Support native arrow-key selection and Enter.",
            }
        ],
        "content_guidance": ["Use concise dates and garden names."],
        "accessibility_checks": ["Associate a visible label with the schedule filter."],
        "responsive_rules": ["Keep all schedule content readable at 375px."],
        "implementation_constraints": ["Use semantic HTML and Tailwind utility classes."],
        "assumptions": [],
        "open_questions": [],
    }


def _tokens():
    return {
        "schema_version": "1.0",
        "color": {
            "primary": "#176B4D",
            "secondary": "#D8E9D8",
            "accent": "#D58A35",
            "background": "#F8FAF6",
            "surface": "#FFFFFF",
            "text": "#17231D",
            "muted": "#53645A",
            "border": "#C9D4CB",
            "success": "#176B4D",
            "warning": "#805000",
            "danger": "#9B2C2C",
        },
        "typography": {
            "font_family": "Inter, sans-serif",
            "heading_scale": [1.25, 1.5, 2.0],
            "body_size": 1.0,
            "line_height": 1.5,
            "font_weights": [400, 500, 700],
        },
        "spacing": {"unit": 4, "scale": [4, 8, 12, 16, 24, 32]},
        "radius": ["0.25rem", "0.5rem", "9999px"],
        "shadows": ["none", "0 2px 8px #00000020"],
        "breakpoints": {"mobile": 375, "tablet": 768, "desktop": 1440},
        "container": {"max_width": 1200, "page_gutter": 24},
    }


def _envelope(status="completed"):
    return {
        "schema_version": "1.0",
        "status": status,
        "design_spec": _spec() if status == "completed" else None,
        "design_tokens": _tokens() if status == "completed" else None,
        "clarification_questions": (
            [] if status == "completed" else ["Which garden roles may change the schedule?"]
        ),
    }


def _response(value):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(value, allow_nan=False))
            )
        ]
    )


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "jobs.sqlite3"
        self.data_root = self.root / "data"
        self.job = app_jobs.start_intake_job("create", self.db_path)
        planning.plan_job(
            self.job["id"],
            [{"role": "user", "content": "Track garden harvests.", "job_id": self.job["id"]}],
            db_path=self.db_path,
            artifact_root=self.data_root / "job_artifacts",
            gateway_call_fn=self._gateway(_response(
                {
                    "schema_version": "1.0",
                    "status": "completed",
                    "product_brief": _brief(),
                    "ux_plan": _plan(),
                    "clarification_questions": [],
                }
            )),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _gateway(response, calls=None, provider=None):
        def call(*args, **kwargs):
            if calls is not None:
                calls.append((args, kwargs))
            return SimpleNamespace(
                response=response,
                provider=provider or {"provider_id": "test-provider", "model_id": "test-model"},
                attempts=1,
                failures=[],
            )

        return call

    def _design(self, envelope=None, gateway=None, **kwargs):
        return design.design_job(
            self.job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            gateway_call_fn=gateway or self._gateway(_response(envelope or _envelope())),
            **kwargs,
        )

    def test_exact_response_contract_and_page_cross_references(self):
        validated = design.validate_envelope(_envelope(), _plan())
        self.assertEqual(validated["status"], "completed")
        self.assertEqual(validated["design_spec"]["page_specs"][0]["page"], "Garden overview")

        invalid = copy.deepcopy(_envelope())
        invalid["design_spec"]["page_specs"][0]["page"] = "Unplanned page"
        with self.assertRaisesRegex(design.DesignError, "planned page"):
            design.validate_envelope(invalid, _plan())

    def test_schema_rejects_missing_extra_wrong_type_and_invalid_enums(self):
        cases = []
        missing = copy.deepcopy(_envelope())
        del missing["design_tokens"]["color"]["danger"]
        cases.append(("missing field", missing))
        extra = copy.deepcopy(_envelope())
        extra["unexpected"] = True
        cases.append(("extra field", extra))
        wrong_type = copy.deepcopy(_envelope())
        wrong_type["design_spec"]["component_specs"] = "not a list"
        cases.append(("wrong type", wrong_type))
        bad_status = copy.deepcopy(_envelope())
        bad_status["status"] = "ready"
        cases.append(("invalid status", bad_status))
        for label, item in cases:
            with self.subTest(case=label), self.assertRaises(design.DesignError):
                design.validate_envelope(item, _plan())

    def test_schema_rejects_colors_breakpoints_numbers_lengths_and_bounds(self):
        cases = []
        bad_color = copy.deepcopy(_envelope())
        bad_color["design_tokens"]["color"]["primary"] = "red"
        cases.append(("color", bad_color))
        bad_breakpoints = copy.deepcopy(_envelope())
        bad_breakpoints["design_tokens"]["breakpoints"] = {
            "mobile": 768, "tablet": 375, "desktop": 1440
        }
        cases.append(("breakpoint ordering", bad_breakpoints))
        bad_number = copy.deepcopy(_envelope())
        bad_number["design_tokens"]["typography"]["line_height"] = True
        cases.append(("numeric type", bad_number))
        bad_spacing = copy.deepcopy(_envelope())
        bad_spacing["design_tokens"]["spacing"]["scale"] = [4, 10]
        cases.append(("spacing multiple", bad_spacing))
        bad_radius = copy.deepcopy(_envelope())
        bad_radius["design_tokens"]["radius"] = ["url(https://example.test)"]
        cases.append(("CSS radius", bad_radius))
        bad_shadow = copy.deepcopy(_envelope())
        bad_shadow["design_tokens"]["shadows"] = ["url(secret)"]
        cases.append(("CSS shadow", bad_shadow))
        bad_container = copy.deepcopy(_envelope())
        bad_container["design_tokens"]["container"] = {
            "max_width": 48, "page_gutter": 24
        }
        cases.append(("container bounds", bad_container))
        for label, item in cases:
            with self.subTest(case=label), self.assertRaises(design.DesignError):
                design.validate_envelope(item, _plan())

    def test_schema_rejects_oversized_content_arrays_and_unmarked_brand_attributes(self):
        overlong = copy.deepcopy(_envelope())
        overlong["design_spec"]["design_direction"] = "x" * (design.MAX_STRING_CHARS + 1)
        too_many = copy.deepcopy(_envelope())
        too_many["design_spec"]["accessibility_checks"] = ["check"] * (
            design.MAX_ARRAY_ITEMS + 1
        )
        ungrounded = copy.deepcopy(_envelope())
        ungrounded["design_spec"]["brand_attributes"] = ["Earthy and rustic"]
        for item in (overlong, too_many, ungrounded):
            with self.assertRaises(design.DesignError):
                design.validate_envelope(item, _plan())

    def test_non_finite_json_is_rejected(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(_envelope()).replace('"line_height": 1.5', '"line_height": NaN')
                    )
                )
            ]
        )
        with self.assertRaisesRegex(design.DesignError, "malformed JSON"):
            design._response_json(response, _plan())

    def test_completed_design_saves_artifacts_and_atomic_checkpoint_transition(self):
        calls = []
        result = self._design(
            gateway=self._gateway(
                _response(_envelope()),
                calls,
                provider={
                    "provider_id": "provider-a",
                    "model_id": "model-a",
                    "api_key": "must-not-be-persisted",
                },
            )
        )
        self.assertEqual(result["job"]["state"], "DESIGN")
        self.assertEqual(
            {Path(reference).name for reference in result["artifact_refs"]},
            design.DESIGN_ARTIFACT_NAMES,
        )
        self.assertEqual(
            sorted(path.name for path in (self.data_root / "job_artifacts").rglob("design_*.json")),
            ["design_spec.json", "design_tokens.json"],
        )
        transition = orchestrator.list_transitions(self.job["id"], self.db_path)[-1]
        checkpoint = orchestrator.list_checkpoints(self.job["id"], self.db_path)[-1]
        self.assertEqual(transition["to_state"], "DESIGN")
        self.assertEqual(transition["artifact_refs"], result["artifact_refs"])
        self.assertEqual(checkpoint["stage"], "PLANNING")
        self.assertEqual(checkpoint["artifact_refs"], result["artifact_refs"])
        self.assertEqual(checkpoint["metadata"]["artifact_refs"], result["artifact_refs"])
        self.assertEqual(transition["metadata"]["artifact_refs"], result["artifact_refs"])
        self.assertEqual(checkpoint["metadata"]["source_artifact_refs"], transition["metadata"]["source_artifact_refs"])
        self.assertEqual(transition["metadata"]["run_id"], result["run_id"])
        self.assertNotIn("must-not-be-persisted", str(transition))
        self.assertNotIn("api_key", str(transition))
        messages, tools, capabilities = calls[0][0][:3]
        self.assertEqual(tools, [])
        self.assertEqual(capabilities, {"text", "json_output"})
        self.assertEqual(messages[0], {"role": "system", "content": design._PROMPT})
        self.assertNotIn("Community Garden Tracker", messages[0]["content"])
        self.assertIn("Community Garden Tracker", messages[1]["content"])
        self.assertEqual(calls[0][1]["response_format"], {"type": "json_object"})

    def test_needs_input_persists_questions_on_planning_without_transition(self):
        before = orchestrator.get_job(self.job["id"], self.db_path)
        result = self._design(envelope=_envelope("needs_input"))
        after = orchestrator.get_job(self.job["id"], self.db_path)

        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(after["state"], "PLANNING")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(result["artifact_refs"], [])
        checkpoint = orchestrator.list_checkpoints(self.job["id"], self.db_path)[-1]
        self.assertEqual(checkpoint["stage"], "PLANNING")
        self.assertEqual(checkpoint["artifact_refs"], [])
        self.assertEqual(
            app_jobs.saved_design_clarification_questions(
                app_jobs.resume_ui_job(self.job["id"], self.db_path)
            ),
            result["clarification_questions"],
        )
        with self.assertRaisesRegex(design.DesignError, "response to the saved"):
            self._design()

    def test_clarification_answer_allows_a_later_completed_run(self):
        self._design(envelope=_envelope("needs_input"))
        result = self._design(
            clarification="Garden coordinators can edit schedules.",
            gateway=self._gateway(_response(_envelope())),
        )
        self.assertEqual(result["job"]["state"], "DESIGN")

    def test_invalid_s7_artifact_and_cross_job_references_are_rejected(self):
        checkpoint = orchestrator.list_checkpoints(self.job["id"], self.db_path)[-1]
        wrong_job_id = str(uuid.uuid4())
        with self.assertRaises(job_store.ValidationError):
            design._latest_s7_plan(wrong_job_id, [checkpoint], self.data_root)

        s7_ref = checkpoint["artifact_refs"][0]
        artifact = self.data_root.joinpath(*s7_ref.split("/"))
        artifact.write_text("{}", encoding="utf-8")
        calls = Mock()
        with self.assertRaises(design.DesignError):
            self._design(gateway=calls)
        calls.assert_not_called()

    def test_missing_plan_and_wrong_job_state_are_rejected_before_provider(self):
        new_job = app_jobs.start_intake_job("create", self.db_path)
        calls = Mock()
        with self.assertRaises(design.DesignError):
            design.design_job(
                new_job["id"],
                db_path=self.db_path,
                artifact_root=self.data_root,
                gateway_call_fn=calls,
            )
        calls.assert_not_called()

        review_job = app_jobs.start_intake_job("review", self.db_path)
        with self.assertRaises(design.DesignError):
            design.design_job(
                review_job["id"],
                db_path=self.db_path,
                artifact_root=self.data_root,
                gateway_call_fn=calls,
            )
        calls.assert_not_called()

    def test_provider_failure_malformed_and_oversized_responses_do_not_transition(self):
        with self.assertRaisesRegex(design.DesignError, "providers failed"):
            self._design(gateway=lambda *args, **kwargs: SimpleNamespace(response=None))
        malformed = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{"))]
        )
        oversized = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=" " * (design.MAX_RESPONSE_BYTES + 1))
                )
            ]
        )
        for response in (malformed, oversized):
            with self.subTest(response=response):
                with self.assertRaises(design.DesignError):
                    self._design(gateway=self._gateway(response))
        self.assertEqual(
            orchestrator.get_job(self.job["id"], self.db_path)["state"], "PLANNING"
        )
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/design_spec.json"))
        )

    def test_stale_transition_cleans_only_current_failed_run(self):
        response = _response(_envelope())

        def make_stale(*args, **kwargs):
            current = orchestrator.get_job(self.job["id"], self.db_path)
            orchestrator.transition_job(
                self.job["id"],
                "WAITING_FOR_USER",
                "test",
                "Make design snapshot stale",
                current["revision"],
                db_path=self.db_path,
            )
            return SimpleNamespace(
                response=response, provider={"provider_id": "test"}, attempts=1
            )

        with self.assertRaisesRegex(job_store.ValidationError, "stale revision"):
            self._design(gateway=make_stale)
        self.assertEqual(
            orchestrator.get_job(self.job["id"], self.db_path)["state"],
            "WAITING_FOR_USER",
        )
        checkpoints = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        self.assertFalse(
            any(item.get("metadata", {}).get("integration") == "s8-design" for item in checkpoints)
        )
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/design_spec.json"))
        )

    def test_run_id_collision_preserves_existing_immutable_artifacts(self):
        run_id = str(uuid.uuid4())
        existing = (
            self.data_root / "job_artifacts" / self.job["id"] / run_id
        )
        existing.mkdir(parents=True)
        marker = existing / "design_spec.json"
        marker.write_text('{"existing":true}', encoding="utf-8")
        with patch.object(design.uuid, "uuid4", return_value=uuid.UUID(run_id)):
            with self.assertRaisesRegex(design.DesignError, "collision"):
                design._save_artifacts(
                    self.job["id"], _spec(), _tokens(), self.data_root
                )
        self.assertEqual(marker.read_text(encoding="utf-8"), '{"existing":true}')

    def test_saved_design_loader_is_job_scoped_and_rejects_traversal(self):
        result = self._design()
        recovered = app_jobs.resume_ui_job(self.job["id"], self.db_path)
        loaded = app_jobs.saved_design_artifacts(recovered, self.data_root)
        self.assertEqual(set(loaded), {"design_spec.json", "design_tokens.json"})
        self.assertEqual(loaded["design_spec.json"]["design_direction"], _spec()["design_direction"])

        other_job = dict(recovered)
        other_job["id"] = str(uuid.uuid4())
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_design_artifacts(other_job, self.data_root)

        malformed = dict(recovered)
        malformed["checkpoints"] = [
            {
                "stage": "PLANNING",
                "metadata": {"integration": "s8-design", "status": "completed"},
                "artifact_refs": [
                    f"job_artifacts/{self.job['id']}/{result['run_id']}/../design_spec.json"
                ],
            }
        ]
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_design_artifacts(malformed, self.data_root)

    def test_streamlit_design_action_and_completed_resume_do_not_regenerate(self):
        from streamlit.testing.v1 import AppTest

        script = self.root / "app.py"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "app.py", script)
        chroma_client = Mock()
        chroma_client.get_or_create_collection.return_value = Mock()
        with patch.object(__import__("config"), "DATA_DIR", self.data_root), patch(
            "chromadb.PersistentClient", return_value=chroma_client
        ):
            ui_job = app_jobs.start_intake_job("create")
            planning.plan_job(
                ui_job["id"],
                [{"role": "user", "content": "Track garden harvests.", "job_id": ui_job["id"]}],
                artifact_root=self.data_root / "job_artifacts",
                gateway_call_fn=self._gateway(
                    _response(
                        {
                            "schema_version": "1.0",
                            "status": "completed",
                            "product_brief": _brief(),
                            "ux_plan": _plan(),
                            "clarification_questions": [],
                        }
                    )
                ),
            )
            app = AppTest.from_file(str(script))
            app.run(timeout=20)
            app.session_state["active_job_id"] = ui_job["id"]
            app.run(timeout=20)
            self.assertTrue(
                any(button.label == "Generate design specification" for button in app.button),
                f"app={app!r}; exceptions={list(app.exception)}; "
                f"state={dict(app.session_state)}; markdown={[item.value for item in app.markdown]}",
            )

            with patch(
                "design.gateway_call",
                self._gateway(_response(_envelope("needs_input"))),
            ):
                app.button[0].click()
                app.run(timeout=20)
            questions = app_jobs.saved_design_clarification_questions(
                app_jobs.resume_ui_job(ui_job["id"])
            )
            self.assertEqual(questions, _envelope("needs_input")["clarification_questions"])
            self.assertEqual(
                orchestrator.get_job(ui_job["id"])["state"], "PLANNING"
            )
            self.assertTrue(any(questions[0] in item.value for item in app.markdown))

            app.text_area[0].set_value("Garden coordinators can edit schedules.")
            with patch("design.gateway_call", self._gateway(_response(_envelope()))):
                app.button[0].click()
                app.run(timeout=20)
            self.assertEqual(orchestrator.get_job(ui_job["id"])["state"], "DESIGN")
            with patch("design.design_job") as generate:
                app.run(timeout=20)
            generate.assert_not_called()
            self.assertTrue(any("Design specification" in item.value for item in app.subheader))
            self.assertTrue(any("Garden overview" in item.label for item in app.expander))

    def test_artifact_writer_rejects_symlinked_artifact_root(self):
        target = self.root / "real-artifacts"
        target.mkdir()
        data_root = self.root / "linked-data"
        data_root.mkdir()
        linked_root = data_root / "job_artifacts"
        try:
            linked_root.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"platform cannot create required symlink: {exc}")
        with self.assertRaises(design.DesignError):
            design._save_artifacts(self.job["id"], _spec(), _tokens(), data_root)


if __name__ == "__main__":
    unittest.main()
