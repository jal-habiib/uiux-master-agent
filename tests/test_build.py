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
import build
import design
import job_store
import orchestrator
import planning


def _brief():
    return {
        "schema_version": "1.0",
        "product_name": "Community Garden Tracker",
        "problem_statement": "Gardeners need to coordinate planting and harvests.",
        "target_users": ["community gardeners"],
        "user_goals": ["coordinate seasonal work"],
        "business_goals": ["increase participation"],
        "core_features": ["garden overview", "garden settings"],
        "primary_user_flows": ["review the garden schedule"],
        "constraints": ["web"],
        "assumptions": ["users can access a browser"],
        "open_questions": ["which garden roles can edit schedules"],
        "success_metrics": ["successful schedule updates"],
        "acceptance_criteria": ["schedule is readable on mobile"],
        "target_platform": "web",
        "output_target": "HTML + Tailwind",
    }


def _plan():
    return {
        "schema_version": "1.0",
        "summary": "A garden overview and settings page.",
        "information_architecture": [
            {"page": "Garden overview", "purpose": "Review progress", "sections": ["Schedule"]},
            {"page": "Garden settings", "purpose": "Manage preferences", "sections": ["Preferences"]},
        ],
        "primary_journeys": [
            {"name": "Review schedule", "steps": ["Open the overview"], "success_outcome": "Schedule reviewed"}
        ],
        "interaction_inventory": [
            {"element": "Schedule filter", "behavior": "Filters entries", "states": ["default"]}
        ],
        "content_requirements": ["Garden names"],
        "accessibility_requirements": ["Keyboard-operable filters"],
        "responsive_requirements": ["Reflow at mobile widths"],
        "risks": ["Role permissions are unspecified"],
        "decisions": ["Use HTML + Tailwind"],
    }


def _spec():
    page_specs = [
        {
            "page": "Garden overview",
            "purpose": "Help gardeners review current progress.",
            "layout": "A responsive schedule-first layout.",
            "sections": ["Schedule", "Garden progress"],
            "primary_action": "Review the upcoming schedule.",
            "responsive_behavior": "Stack cards on narrow screens.",
        },
        {
            "page": "Garden settings",
            "purpose": "Let gardeners manage preferences.",
            "layout": "A responsive settings form.",
            "sections": ["Preferences"],
            "primary_action": "Save preferences.",
            "responsive_behavior": "Use a single column on narrow screens.",
        },
    ]
    return {
        "schema_version": "1.0",
        "design_direction": "A calm, practical garden workspace.",
        "brand_attributes": ["[supplied] Community gardening tool"],
        "page_specs": page_specs,
        "component_specs": [
            {
                "component": "Schedule filter",
                "purpose": "Filter schedule entries.",
                "variants": ["compact", "full-width"],
                "states": ["default", "focused"],
                "accessibility": "Use a labeled native select with visible focus.",
            }
        ],
        "interaction_specs": [
            {
                "interaction": "Filter the schedule",
                "trigger": "The user changes the time-period select.",
                "feedback": "Show matching schedule entries.",
                "error_behavior": "Keep the current list and report the error.",
                "keyboard_behavior": "Support native keyboard selection.",
            }
        ],
        "content_guidance": ["Use concise dates and garden names."],
        "accessibility_checks": ["Associate a visible label with each control."],
        "responsive_rules": ["Keep content readable at 375px."],
        "implementation_constraints": ["Use semantic HTML."],
        "assumptions": [],
        "open_questions": [],
    }


def _tokens():
    return {
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
        "radius": ["0.25rem", "0.5rem", "9999px"],
        "shadows": ["none", "0 2px 8px #00000020"],
        "breakpoints": {"mobile": 375, "tablet": 768, "desktop": 1440},
        "container": {"max_width": 1200, "page_gutter": 24},
    }


def _design_envelope():
    return {
        "schema_version": "1.0",
        "status": "completed",
        "design_spec": _spec(),
        "design_tokens": _tokens(),
        "clarification_questions": [],
    }


def _html(title, extra=""):
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title><style>body{{margin:0;color:#17231d}}{extra}</style>"
        "</head><body><header><nav aria-label=\"Main\"><a href=\"index.html\">Overview</a>"
        "</nav></header><main><h1>" + title + "</h1><p>Garden schedule.</p></main></body></html>"
    )


def _envelope(status="completed"):
    if status == "needs_input":
        return {
            "schema_version": "1.0",
            "status": "needs_input",
            "pages": [],
            "clarification_questions": ["Which roles can update garden preferences?"],
        }
    return {
        "schema_version": "1.0",
        "status": "completed",
        "pages": [
            {"page": "Garden overview", "filename": "index.html", "html": _html("Garden overview")},
            {"page": "Garden settings", "filename": "settings.html", "html": _html("Garden settings")},
        ],
        "clarification_questions": [],
    }


def _response(value):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))],
    )


class BuildTests(unittest.TestCase):
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
        design.design_job(
            self.job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            gateway_call_fn=self._gateway(_response(_design_envelope())),
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

    def _build(self, envelope=None, gateway=None, **kwargs):
        return build.build_job(
            self.job["id"],
            db_path=self.db_path,
            artifact_root=self.data_root,
            gateway_call_fn=gateway or self._gateway(_response(envelope or _envelope())),
            **kwargs,
        )

    def test_completed_multi_page_build_is_saved_and_checkpointed_atomically(self):
        calls = []
        result = self._build(
            gateway=self._gateway(
                _response(_envelope()),
                calls,
                {"provider_id": "provider-a", "model_id": "model-a", "api_key": "do-not-store"},
            )
        )
        self.assertEqual(result["job"]["state"], "BUILD")
        self.assertEqual(len(result["manifest"]["pages"]), 2)
        self.assertEqual(
            {Path(reference).name for reference in result["artifact_refs"]},
            {"index.html", "settings.html", "build_manifest.json"},
        )
        transition = orchestrator.list_transitions(self.job["id"], self.db_path)[-1]
        checkpoint = orchestrator.list_checkpoints(self.job["id"], self.db_path)[-1]
        self.assertEqual(transition["to_state"], "BUILD")
        self.assertEqual(checkpoint["stage"], "DESIGN")
        self.assertEqual(checkpoint["artifact_refs"], result["artifact_refs"])
        self.assertEqual(transition["metadata"]["source_artifact_refs"], result["manifest"]["source_artifact_refs"])
        self.assertNotIn("do-not-store", str(transition))
        messages, tools, capabilities = calls[0][0][:3]
        self.assertEqual(tools, [])
        self.assertEqual(capabilities, {"text", "json_output"})
        self.assertEqual(messages[0], {"role": "system", "content": build._PROMPT})
        self.assertNotIn("Community Garden Tracker", messages[0]["content"])
        self.assertIn("Community Garden Tracker", messages[1]["content"])
        self.assertEqual(calls[0][1]["response_format"], {"type": "json_object"})

        resumed = app_jobs.resume_ui_job(self.job["id"], self.db_path)
        saved = build.load_completed_build(resumed, self.data_root)
        self.assertEqual([page["page"] for page in saved["pages"]], ["Garden overview", "Garden settings"])
        self.assertEqual(saved["pages"][0]["html"], _html("Garden overview"))

    def test_needs_input_persists_questions_without_state_transition(self):
        before = orchestrator.get_job(self.job["id"], self.db_path)
        result = self._build(envelope=_envelope("needs_input"))
        after = orchestrator.get_job(self.job["id"], self.db_path)
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(after["state"], "DESIGN")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(result["artifact_refs"], [])
        self.assertEqual(
            app_jobs.saved_build_clarification_questions(
                app_jobs.resume_ui_job(self.job["id"], self.db_path)
            ),
            result["clarification_questions"],
        )
        with self.assertRaisesRegex(build.BuildError, "response to the saved"):
            self._build()
        retry = self._build(
            clarification="Only coordinators may edit preferences.",
            envelope=_envelope(),
        )
        self.assertEqual(retry["job"]["state"], "BUILD")

    def test_exact_envelope_rejects_invalid_fields_types_mapping_and_names(self):
        valid = build.validate_envelope(_envelope(), _spec()["page_specs"])
        self.assertEqual([page["page"] for page in valid["pages"]], ["Garden overview", "Garden settings"])
        variants = []
        missing = copy.deepcopy(_envelope())
        del missing["pages"]
        variants.append(missing)
        extra = copy.deepcopy(_envelope())
        extra["extra"] = True
        variants.append(extra)
        wrong_status = copy.deepcopy(_envelope())
        wrong_status["status"] = "ready"
        variants.append(wrong_status)
        duplicate_page = copy.deepcopy(_envelope())
        duplicate_page["pages"][1]["page"] = "Garden overview"
        variants.append(duplicate_page)
        duplicate_filename = copy.deepcopy(_envelope())
        duplicate_filename["pages"][1]["filename"] = "index.html"
        variants.append(duplicate_filename)
        traversal = copy.deepcopy(_envelope())
        traversal["pages"][0]["filename"] = "../index.html"
        variants.append(traversal)
        extra_page_field = copy.deepcopy(_envelope())
        extra_page_field["pages"][0]["extra"] = "field"
        variants.append(extra_page_field)
        wrong_type = copy.deepcopy(_envelope())
        wrong_type["pages"] = "page"
        variants.append(wrong_type)
        needs_with_page = copy.deepcopy(_envelope("needs_input"))
        needs_with_page["pages"] = [{"page": "Garden overview"}]
        variants.append(needs_with_page)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(build.BuildError):
                build.validate_envelope(value, _spec()["page_specs"])

    def test_page_and_aggregate_size_limits_and_malformed_json(self):
        oversized_page = copy.deepcopy(_envelope())
        oversized_page["pages"][0]["html"] = "x" * (build.MAX_PAGE_BYTES + 1)
        with self.assertRaisesRegex(build.BuildError, "256 KB"):
            build.validate_envelope(oversized_page, _spec()["page_specs"])
        aggregate_specs = [{"page": f"Page {index}"} for index in range(5)]
        aggregate = {
            "schema_version": "1.0",
            "status": "completed",
            "pages": [
                {
                    "page": spec["page"],
                    "filename": f"page-{index}.html",
                    "html": _html(spec["page"], " " * 215_000).replace(
                        "index.html", f"page-{index}.html"
                    ),
                }
                for index, spec in enumerate(aggregate_specs)
            ],
            "clarification_questions": [],
        }
        with self.assertRaisesRegex(build.BuildError, "aggregate"):
            build.validate_envelope(aggregate, aggregate_specs)
        oversized_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=" " * (build.MAX_RESPONSE_BYTES + 1))
                )
            ]
        )
        with self.assertRaisesRegex(build.BuildError, "1 MB"):
            build._response_json(oversized_response, _spec()["page_specs"])
        malformed = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{"))]
        )
        with self.assertRaisesRegex(build.BuildError, "malformed JSON"):
            build._response_json(malformed, _spec()["page_specs"])

    def test_html_validator_rejects_active_remote_and_malformed_content(self):
        safe = _html("Safe page")
        build.validate_html_document(safe, {"index.html", "settings.html"})
        unsafe_values = [
            _html("x").replace("</body>", '<img alt="x" src="javascript:alert(1)"></body>'),
            _html("x").replace("</body>", '<img alt="x" src="data:image/png;base64,abc"></body>'),
            _html("x").replace("</body>", '<img alt="x" src="https://evil.test/image"></body>'),
            _html("x").replace("</body>", '<div onclick="alert(1)">x</div></body>'),
            _html("x").replace("</body>", "<script>alert(1)</script></body>"),
            _html("x").replace("</body>", "<iframe src=\"index.html\"></iframe></body>"),
            _html("x").replace("</body>", "<object data=\"index.html\"></object></body>"),
            _html("x").replace("</body>", "<embed src=\"index.html\"></body>"),
            _html("x").replace("</body>", '<form action="https://evil.test/"></form></body>'),
            _html("x").replace("</body>", '<a href="missing.html">missing</a></body>'),
            _html("x").replace(
                "</body>", '<a href="#section" ping="https://evil.test/ping">section</a></body>'
            ),
            _html("x").replace(
                "</body>", '<a href="#section" attributionsrc="https://evil.test/">section</a></body>'
            ),
            _html("x").replace("</body>", '<div style="background:u\\72l(https://evil.test)"></div></body>'),
            _html("x").replace(
                "</body>", "<style>/*allowed?*/body{background-image:image-set(\"https://evil.test/a\")}</style></body>"
            ),
            _html("x").replace("</body>", "<div></body></html>"),
            _html("x").replace("</html>", "text outside root</html>"),
        ]
        for html in unsafe_values:
            with self.subTest(html=html[:100]), self.assertRaises(build.BuildError):
                build.validate_html_document(html, {"index.html", "settings.html"})

    def test_html_validator_rejects_svg_animation_and_allows_static_svg(self):
        unsafe_values = [
            _html("x").replace(
                "</body>",
                '<svg><a href="#section"><animate attributeName="href" '
                'values="javascript:alert(1)" dur="1s" repeatCount="indefinite">'
                '</animate></a></svg></body>',
            ),
            _html("x").replace(
                "</body>",
                '<svg><a href="#section"><set attributeName="href" '
                'to="javascript:alert(1)" begin="0s"></set></a></svg></body>',
            ),
            _html("x").replace(
                "</body>",
                '<svg><path d="M0 0" attributeName="href" '
                'values="javascript:alert(1)"></path></svg></body>',
            ),
        ]
        for html in unsafe_values:
            with self.subTest(html=html[:100]), self.assertRaises(build.BuildError):
                build.validate_html_document(html, {"index.html", "settings.html"})

        static_svg = _html("Safe icon").replace(
            "</body>",
            '<svg aria-hidden="true" viewBox="0 0 24 24">'
            '<path d="M2 12h20" fill="none" stroke="currentColor"></path>'
            '<text x="2" y="12" rotate="90">N</text>'
            '</svg></body>',
        )
        build.validate_html_document(static_svg, {"index.html", "settings.html"})

    def test_wrong_job_mode_state_and_invalid_id_reject_before_provider(self):
        calls = Mock()
        with self.assertRaises(build.BuildError):
            build.build_job("not-a-uuid", gateway_call_fn=calls)
        self.assertEqual(calls.call_count, 0)
        intake = app_jobs.start_intake_job("create", self.db_path)
        with self.assertRaisesRegex(build.BuildError, "DESIGN"):
            build.build_job(intake["id"], db_path=self.db_path, gateway_call_fn=calls)
        review = app_jobs.start_intake_job("review", self.db_path)
        with self.assertRaisesRegex(build.BuildError, "Create"):
            build.build_job(review["id"], db_path=self.db_path, gateway_call_fn=calls)
        calls.assert_not_called()

    def test_invalid_s7_s8_and_cross_job_references_reject_before_provider(self):
        checkpoints = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        s8_checkpoint = next(item for item in reversed(checkpoints) if item["metadata"].get("integration") == "s8-design")
        wrong_job = str(uuid.uuid4())
        with self.assertRaises(build.BuildError):
            build._source_artifacts(wrong_job, checkpoints, self.data_root)

        with self.assertRaisesRegex(build.BuildError, "latest completed S8"):
            build._source_artifacts(
                self.job["id"],
                [item for item in checkpoints if item["metadata"].get("integration") != "s8-design"],
                self.data_root,
            )

        refs = list(s8_checkpoint["artifact_refs"])
        s8_checkpoint["artifact_refs"] = [f"job_artifacts/{wrong_job}/{refs[0].split('/')[2]}/design_spec.json", refs[1]]
        s8_checkpoint["metadata"]["artifact_refs"] = s8_checkpoint["artifact_refs"]
        with self.assertRaises(build.BuildError):
            build._source_artifacts(self.job["id"], checkpoints, self.data_root)

        checkpoints = orchestrator.list_checkpoints(self.job["id"], self.db_path)
        s8_checkpoint = next(item for item in reversed(checkpoints) if item["metadata"].get("integration") == "s8-design")
        spec_ref = next(ref for ref in s8_checkpoint["artifact_refs"] if ref.endswith("design_spec.json"))
        self.data_root.joinpath(*spec_ref.split("/")).write_text("{", encoding="utf-8")
        with self.assertRaises(build.BuildError):
            build._source_artifacts(self.job["id"], checkpoints, self.data_root)

        # Corrupt the job's S7 UX Plan in place; schema validation must stop the provider call.
        original = orchestrator.list_checkpoints(self.job["id"], self.db_path)[0]
        plan_ref = next(ref for ref in original["artifact_refs"] if ref.endswith("ux_plan.json"))
        plan_file = self.data_root.joinpath(*plan_ref.split("/"))
        plan_file.write_text("{}", encoding="utf-8")
        calls = Mock()
        with self.assertRaises(build.BuildError):
            self._build(gateway=calls)
        calls.assert_not_called()

    def test_provider_failure_does_not_transition_or_save_build_files(self):
        with self.assertRaisesRegex(build.BuildError, "providers failed"):
            self._build(gateway=lambda *args, **kwargs: SimpleNamespace(response=None))
        self.assertEqual(orchestrator.get_job(self.job["id"], self.db_path)["state"], "DESIGN")
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/build_manifest.json"))
        )

    def test_transition_failure_cleans_only_current_run_artifacts(self):
        response = _response(_envelope())

        def make_stale(*args, **kwargs):
            current = orchestrator.get_job(self.job["id"], self.db_path)
            orchestrator.transition_job(
                self.job["id"], "BUILD", "test", "advance state", current["revision"],
                db_path=self.db_path,
            )
            return SimpleNamespace(response=response, provider={"provider_id": "test"}, attempts=1)

        with self.assertRaisesRegex(job_store.ValidationError, "stale revision"):
            self._build(gateway=make_stale)
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/build_manifest.json"))
        )
        self.assertFalse(
            any(
                item.get("metadata", {}).get("integration") == "s9-build"
                for item in orchestrator.list_checkpoints(self.job["id"], self.db_path)
            )
        )

    def test_artifact_write_failure_cleans_current_run_only(self):
        original_write = build._write_atomic

        def fail_manifest(path, content, max_bytes, name):
            if path.name == build.BUILD_MANIFEST_NAME:
                raise OSError("forced manifest write failure")
            return original_write(path, content, max_bytes, name)

        with patch("build._write_atomic", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "forced manifest"):
                build._save_artifacts(
                    self.job["id"], _envelope()["pages"], ["source-ref"], self.data_root
                )
        self.assertFalse(
            list((self.data_root / "job_artifacts" / self.job["id"]).glob("*/build"))
        )

    def test_writer_rejects_symlinked_artifact_root(self):
        target = self.root / "real-artifacts"
        target.mkdir()
        linked_data = self.root / "linked-data"
        linked_data.mkdir()
        try:
            (linked_data / "job_artifacts").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"platform cannot create required symlink: {exc}")
        with self.assertRaises(build.BuildError):
            build._save_artifacts(
                self.job["id"], _envelope()["pages"], [], linked_data
            )

    def test_run_collision_and_loader_reject_traversal_and_job_substitution(self):
        run_id = str(uuid.uuid4())
        existing = self.data_root / "job_artifacts" / self.job["id"] / run_id
        existing.mkdir(parents=True)
        (existing / "marker").write_text("keep", encoding="utf-8")
        with patch.object(build.uuid, "uuid4", return_value=uuid.UUID(run_id)):
            with self.assertRaisesRegex(build.BuildError, "collision"):
                build._save_artifacts(self.job["id"], _envelope()["pages"], [], self.data_root)
        self.assertEqual((existing / "marker").read_text(encoding="utf-8"), "keep")

        result = self._build()
        resumed = app_jobs.resume_ui_job(self.job["id"], self.db_path)
        other = dict(resumed)
        other["id"] = str(uuid.uuid4())
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_build_artifacts(other, self.data_root)

        invalid = dict(resumed)
        invalid["checkpoints"] = [
            {
                "stage": "DESIGN",
                "artifact_refs": [
                    f"job_artifacts/{self.job['id']}/{result['run_id']}/build/../index.html"
                ],
                "metadata": {
                    "integration": "s9-build",
                    "status": "completed",
                    "artifact_refs": [
                        f"job_artifacts/{self.job['id']}/{result['run_id']}/build/../index.html"
                    ],
                },
            }
        ]
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_build_artifacts(invalid, self.data_root)

    def test_loader_rejects_symlinked_build_page(self):
        result = self._build()
        run_dir = self.data_root / "job_artifacts" / self.job["id"] / result["run_id"]
        page_file = run_dir / "build" / "index.html"
        outside = self.root / "outside.html"
        outside.write_text(_html("Outside"), encoding="utf-8")
        page_file.unlink()
        try:
            page_file.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"platform cannot create required symlink: {exc}")
        resumed = app_jobs.resume_ui_job(self.job["id"], self.db_path)
        with self.assertRaises(job_store.ValidationError):
            app_jobs.saved_build_artifacts(resumed, self.data_root)

    def test_streamlit_build_action_questions_and_reload_do_not_regenerate(self):
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
            design.design_job(
                ui_job["id"],
                artifact_root=self.data_root,
                gateway_call_fn=self._gateway(_response(_design_envelope())),
            )
            app = AppTest.from_file(str(script))
            app.run(timeout=20)
            app.session_state["active_job_id"] = ui_job["id"]
            app.run(timeout=20)
            build_button = next(button for button in app.button if button.label == "Generate HTML build")
            with patch("build.gateway_call", self._gateway(_response(_envelope("needs_input")))):
                build_button.click()
                app.run(timeout=20)
            questions = app_jobs.saved_build_clarification_questions(
                app_jobs.resume_ui_job(ui_job["id"])
            )
            self.assertTrue(any(questions[0] in item.value for item in app.markdown))
            clarification = next(item for item in app.text_area if item.label == "Your Build clarification")
            clarification.set_value("Only coordinators may edit preferences.")
            build_button = next(button for button in app.button if button.label == "Generate HTML build")
            with patch("build.gateway_call", self._gateway(_response(_envelope()))):
                build_button.click()
                app.run(timeout=20)
            self.assertEqual(orchestrator.get_job(ui_job["id"])["state"], "BUILD")
            self.assertTrue(any(item.label.startswith("Garden overview") for item in app.expander))
            self.assertTrue(any("Saved HTML build" in item.value for item in app.subheader))
            with patch("build.build_job") as generate:
                app.run(timeout=20)
            generate.assert_not_called()
            self.assertFalse(any(button.label == "Generate HTML build" for button in app.button))


if __name__ == "__main__":
    unittest.main()
