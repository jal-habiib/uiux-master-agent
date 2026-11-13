import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "verify_html.py"


def load_module():
    config = types.ModuleType("config")
    config.OUTPUT_DIR = Path("output")
    config.ROOT = Path.cwd()
    critic = types.ModuleType("tools.critic")
    critic.critique_code = lambda path: {
        "score": 85,
        "score_gate_passed": True,
        "weighted_score_breakdown": {},
    }
    with patch.dict(sys.modules, {"config": config, "tools.critic": critic}):
        spec = importlib.util.spec_from_file_location("verify_html_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


verify = load_module()


class _FakeMessage:
    type = "error"
    text = "console failure"


class _FakeRequest:
    url = "https://example.test/asset.js"
    failure = "connection failed"


class _FakeResponse:
    url = "https://example.test/missing.js"
    status = 404


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key == "Tab":
            self.page.tab_index += 1


class _FakePage:
    def __init__(self, dom, axe, events=None, navigation_error=None):
        self.dom = dom
        self.axe = axe
        self.events = events or set()
        self.navigation_error = navigation_error
        self.handlers = {}
        self.tab_index = -1
        self.keyboard = _FakeKeyboard(self)

    def on(self, name, callback):
        self.handlers[name] = callback

    def goto(self, *args, **kwargs):
        if "console" in self.events:
            self.handlers["console"](_FakeMessage())
        if "pageerror" in self.events:
            self.handlers["pageerror"](RuntimeError("uncaught page error"))
        if "requestfailed" in self.events:
            self.handlers["requestfailed"](_FakeRequest())
        if "response" in self.events:
            self.handlers["response"](_FakeResponse())
        if self.navigation_error:
            raise self.navigation_error

    def wait_for_timeout(self, _):
        pass

    def evaluate(self, script):
        if "axe.run" in script:
            return self.axe
        if "activeElement" in script:
            index = min(self.tab_index, len(self.dom["focusableElements"]) - 1)
            expected = self.dom["focusableElements"][index] if index >= 0 else None
            return {"index": index, "selector": expected["selector"] if expected else "body"}
        return self.dom

    def add_script_tag(self, **kwargs):
        pass

    def close(self):
        pass


class _FakeBrowser:
    def __init__(self, page_factory):
        self.page_factory = page_factory

    def new_page(self, **kwargs):
        return self.page_factory()

    def new_context(self, **kwargs):
        return self

    def close(self):
        pass


class _FakeLocator:
    def __init__(self, *, visible=True, text="", error=None):
        self.visible = visible
        self.text = text
        self.error = error

    def _run(self):
        if self.error:
            raise self.error

    def click(self):
        self._run()

    def fill(self, _value):
        self._run()

    def check(self):
        self._run()

    def uncheck(self):
        self._run()

    def select_option(self, _value):
        self._run()

    def press(self, _value):
        self._run()

    def is_visible(self):
        self._run()
        return self.visible

    def inner_text(self):
        self._run()
        return self.text


class _FlowPage:
    def __init__(self, locator):
        self.locator_instance = locator
        self.timeout = None

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    def locator(self, _selector):
        return self.locator_instance


class _FakePlaywrightContext:
    def __init__(self, page_factory):
        self.page_factory = page_factory
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def launch(self):
        return _FakeBrowser(self.page_factory)


class VerifyHtmlValidationTests(unittest.TestCase):
    def test_rejects_unsafe_and_missing_paths_without_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            with patch.object(verify, "OUTPUT_DIR", output), patch.dict(sys.modules, {}):
                result = verify.verify_html("../page.html")
            self.assertFalse(result["overall_passed"])
            self.assertEqual(result["hard_gates"]["build_render"]["status"], "FAIL")

    def test_missing_html_is_structured_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)):
                result = verify.verify_html("page.html")
        self.assertIn("hard_gates", result)
        self.assertFalse(result["overall_passed"])

    def test_malformed_flows_do_not_execute(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)):
                result = verify.verify_html("page.html", json.dumps({"flows": [{"name": "bad", "steps": [{"action": "eval", "selector": "#x"}]}]}))
        self.assertFalse(result["overall_passed"])
        self.assertEqual(result["hard_gates"]["required_user_flows"]["status"], "NOT_VERIFIED")
        self.assertIn("supported browser action", result["hard_gates"]["required_user_flows"]["evidence"])

    def test_rejects_empty_and_action_mismatched_flows(self):
        invalid_specs = (
            {"flows": []},
            {"flows": [{"name": "empty", "steps": []}]},
            {"flows": [{"name": "extra", "steps": [{"action": "click", "selector": "#x", "value": "x"}]}]},
            {"flows": [{"name": "missing", "steps": [{"action": "fill", "selector": "#x"}]}]},
        )
        for payload in invalid_specs:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    verify._parse_flows(json.dumps(payload))
        with self.assertRaises(ValueError):
            verify._parse_flows(123)
        with self.assertRaises(ValueError):
            verify._parse_flows(json.dumps({
                "flows": [{"name": "blank selector", "steps": [
                    {"action": "click", "selector": "   "}
                ]}],
            }))

    def test_valid_flow_schema_is_complete(self):
        flows = verify._parse_flows(
            json.dumps(
                {
                    "flows": [
                        {
                            "name": "Submit",
                            "steps": [
                                {"action": "fill", "selector": "#email", "value": "a@b.test"},
                                {"action": "click", "selector": "#submit"},
                                {"action": "expect_visible", "selector": ".success"},
                            ],
                        }
                    ]
                }
            )
        )
        self.assertEqual(len(flows[0]["steps"]), 3)

    def test_in_output_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            target = output / "target.html"
            target.write_text("<html></html>", encoding="utf-8")
            alias = output / "alias.html"
            try:
                alias.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")
            with self.assertRaises(ValueError):
                with patch.object(verify, "OUTPUT_DIR", output):
                    verify._validated_path("alias.html")

    def test_symlink_escaping_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            output = Path(temp_dir)
            outside = Path(outside_dir) / "outside.html"
            outside.write_text("<html></html>", encoding="utf-8")
            alias = output / "alias.html"
            try:
                alias.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable in this environment.")
            with self.assertRaises(ValueError):
                with patch.object(verify, "OUTPUT_DIR", output):
                    verify._validated_path("alias.html")

    def test_all_flow_actions_have_strict_schemas(self):
        value_actions = {"fill", "select_option", "press", "expect_text"}
        for action in verify.FLOW_ACTIONS:
            fields = {"action": action, "selector": "#target"}
            if action in value_actions:
                fields["value"] = "value"
            valid = {"flows": [{"name": action, "steps": [fields]}]}
            self.assertEqual(len(verify._parse_flows(json.dumps(valid))), 1)
            invalid_fields = dict(fields)
            if action in value_actions:
                invalid_fields.pop("value")
                with self.assertRaises(ValueError):
                    verify._parse_flows(json.dumps({
                        "flows": [{"name": action, "steps": [invalid_fields]}]
                    }))
            else:
                invalid_fields["value"] = "unexpected"
                with self.assertRaises(ValueError):
                    verify._parse_flows(json.dumps({
                        "flows": [{"name": action, "steps": [invalid_fields]}]
                    }))
            invalid_fields = dict(fields)
            invalid_fields["extra"] = "unexpected"
            with self.assertRaises(ValueError):
                verify._parse_flows(json.dumps({
                    "flows": [{"name": action, "steps": [invalid_fields]}]
                }))

    def test_malformed_flow_schema_is_rejected_before_execution(self):
        with patch.object(verify, "_run_flow", side_effect=AssertionError("must not execute")):
            with self.assertRaises(ValueError):
                verify._parse_flows(json.dumps({
                    "flows": [{"name": "bad", "steps": [{"action": "click"}]}]
                }))


class VerifyHtmlHelperTests(unittest.TestCase):
    def test_flow_runtime_and_navigation_failures_are_distinguished(self):
        flow = {"name": "Submit", "steps": [{"action": "click", "selector": "#submit"}]}
        with self.assertRaises(RuntimeError):
            verify._run_flow(_FlowPage(_FakeLocator(error=RuntimeError("click failed"))), flow)
        self.assertFalse(verify._is_unverified_exception(RuntimeError("click failed")))
        self.assertTrue(verify._is_unverified_exception(TimeoutError("navigation timeout")))

    def test_keyboard_status_requires_dom_identity_probe(self):
        self.assertEqual(
            verify._keyboard_focus_status(
                {"mobile"},
                [{
                    "tab_order": [{"index": 0, "selector": "#first"}],
                    "probe_completed": True,
                    "matches_dom_order": True,
                }],
            ),
            "PASS",
        )
        self.assertEqual(
            verify._keyboard_focus_status(
                {"mobile"},
                [{
                    "tab_order": [{"index": 1, "selector": "#second"}],
                    "probe_completed": True,
                    "matches_dom_order": False,
                }],
            ),
            "FAIL",
        )
        self.assertEqual(
            verify._keyboard_focus_status(
                {"mobile"},
                [{"tab_order": [], "probe_completed": False}],
            ),
            "NOT_VERIFIED",
        )

    def test_semantic_dom_defects_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            dom = {
                "scrollWidth": 375, "bodyScrollWidth": 375, "innerWidth": 375,
                "semantic": [
                    "image missing alt",
                    "control missing accessible name",
                    "form control missing label",
                ],
                "targets": [], "positiveTabindex": [],
                "focusableElements": [],
            }
            sync_api.sync_playwright = lambda: _FakePlaywrightContext(
                lambda: _FakePage(dom, {"violations": [], "incomplete": []})
            )
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.dict(
                sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}
            ):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["semantic_accessibility"]["status"], "FAIL")

    def test_incomplete_contrast_without_violation_is_not_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            axe = Path(temp_dir) / "node_modules" / "axe-core"
            axe.mkdir(parents=True)
            (axe / "axe.min.js").write_text("fake", encoding="utf-8")
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            axe_result = {
                "violations": [],
                "incomplete": [{"id": "color-contrast", "impact": "serious"}],
            }
            sync_api.sync_playwright = lambda: _FakePlaywrightContext(
                lambda: _FakePage(
                    {"scrollWidth": 375, "bodyScrollWidth": 375, "innerWidth": 375,
                     "semantic": [], "targets": [], "positiveTabindex": [],
                     "focusableElements": []},
                    axe_result,
                )
            )
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.object(
                verify, "ROOT", Path(temp_dir)
            ), patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["wcag_aa_contrast"]["status"], "NOT_VERIFIED")

    def test_report_has_stable_gate_keys_and_fail_precedence(self):
        gates = _all_pass_gates()
        gates["console_errors"] = {"status": "NOT_VERIFIED", "evidence": "timeout"}
        gates["failed_requests"] = {"status": "FAIL", "evidence": "404"}
        report = verify._report("page.html", gates, {"score": 100, "score_gate_passed": True})
        self.assertEqual(set(report["hard_gates"]), set(verify._empty_gates("x")))
        self.assertEqual(report["hard_gates_status"], "FAIL")
        self.assertFalse(report["overall_passed"])


def _all_pass_gates():
    return {
        name: {"status": "PASS", "evidence": {}}
        for name in verify._empty_gates("pending")
    }


class VerifyHtmlFlowTests(unittest.TestCase):
    def test_valid_flow_executes_and_passes(self):
        flow = {
            "name": "Submit",
            "steps": [
                {"action": "fill", "selector": "#email", "value": "a@b.test"},
                {"action": "click", "selector": "#submit"},
                {"action": "expect_visible", "selector": ".success"},
                {"action": "expect_text", "selector": ".success", "value": "Saved"},
            ],
        }
        result = verify._run_flow(
            _FlowPage(_FakeLocator(visible=True, text="Saved")),
            flow,
        )
        self.assertTrue(all(step["status"] == "PASS" for step in result))


class VerifyHtmlBrowserFailureTests(unittest.TestCase):
    def test_playwright_import_failure_is_not_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.dict(sys.modules, {"playwright.sync_api": None}):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates_status"], "NOT_VERIFIED")
        self.assertFalse(result["overall_passed"])

    def test_report_requires_all_hard_gates_and_soft_score(self):
        gates = {
            name: {"status": "PASS", "evidence": {}}
            for name in verify._empty_gates("pending")
        }
        report = verify._report("page.html", gates, {"score": 85, "score_gate_passed": True})
        self.assertEqual(report["hard_gates_status"], "PASS")
        self.assertTrue(report["overall_passed"])

        gates["axe_accessibility"] = {"status": "NOT_VERIFIED", "evidence": "missing"}
        report = verify._report("page.html", gates, {"score": 100, "score_gate_passed": True})
        self.assertFalse(report["overall_passed"])
        self.assertEqual(report["hard_gates_status"], "NOT_VERIFIED")

    def test_build_timeout_is_not_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)):
                playwright = types.ModuleType("playwright")
                sync_api = types.ModuleType("playwright.sync_api")
                sync_api.sync_playwright = lambda: _FakePlaywrightContext(
                    lambda: _FakePage(
                        {"scrollWidth": 375, "bodyScrollWidth": 375, "innerWidth": 375,
                         "semantic": [], "targets": [], "positiveTabindex": [],
                         "focusableElements": []},
                        {"violations": [], "incomplete": []},
                        navigation_error=TimeoutError("timeout"),
                    )
                )
                with patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}):
                    result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["build_render"]["status"], "NOT_VERIFIED")
        self.assertFalse(result["overall_passed"])

    def test_console_and_request_defects_fail_separate_gates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            factory = lambda: _FakePage(
                {"scrollWidth": 375, "bodyScrollWidth": 375, "innerWidth": 375,
                 "semantic": [], "targets": [], "positiveTabindex": [],
                 "focusableElements": []},
                {"violations": [], "incomplete": []},
                {"console", "pageerror", "requestfailed", "response"},
            )
            sync_api.sync_playwright = lambda: _FakePlaywrightContext(factory)
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.object(
                verify, "ROOT", Path(temp_dir)
            ), patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["console_errors"]["status"], "FAIL")
        self.assertEqual(result["hard_gates"]["failed_requests"]["status"], "FAIL")

    def test_overflow_touch_target_and_positive_tabindex_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            dom = {
                "scrollWidth": 500, "bodyScrollWidth": 500, "innerWidth": 375,
                "semantic": [], "targets": [{"selector": "#small", "width": 20, "height": 20}],
                "positiveTabindex": [{"index": 0, "selector": "#bad"}],
                "focusableElements": [{"index": 0, "selector": "#small"}],
            }
            sync_api.sync_playwright = lambda: _FakePlaywrightContext(
                lambda: _FakePage(dom, {"violations": [], "incomplete": []})
            )
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.dict(
                sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}
            ):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["horizontal_overflow"]["status"], "FAIL")
        self.assertEqual(result["hard_gates"]["mobile_touch_targets"]["status"], "FAIL")
        self.assertEqual(result["hard_gates"]["keyboard_focus_order"]["status"], "FAIL")

    def test_missing_axe_is_not_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            sync_api.sync_playwright = lambda: _FakePlaywrightContext(
                lambda: _FakePage(
                    {"scrollWidth": 375, "bodyScrollWidth": 375, "innerWidth": 375,
                     "semantic": [], "targets": [], "positiveTabindex": [],
                     "focusableElements": []},
                    {"violations": [], "incomplete": []},
                )
            )
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.object(
                verify, "ROOT", Path(temp_dir)
            ), patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["axe_accessibility"]["status"], "NOT_VERIFIED")
        self.assertEqual(result["hard_gates"]["wcag_aa_contrast"]["status"], "NOT_VERIFIED")
        self.assertEqual(result["hard_gates"]["required_user_flows"]["status"], "NOT_VERIFIED")

    def test_axe_critical_and_contrast_violations_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            axe = Path(temp_dir) / "node_modules" / "axe-core"
            axe.mkdir(parents=True)
            (axe / "axe.min.js").write_text("fake", encoding="utf-8")
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            axe_result = {
                "violations": [
                    {"id": "button-name", "impact": "serious", "description": "name",
                     "help": "help", "helpUrl": "url", "nodes": [{"target": ["button"], "html": "<button>"}]},
                    {"id": "color-contrast", "impact": "serious", "description": "contrast",
                     "help": "help", "helpUrl": "url", "nodes": [{"target": ["p"], "html": "<p>"}]},
                ],
                "incomplete": [],
            }
            sync_api.sync_playwright = lambda: _FakePlaywrightContext(
                lambda: _FakePage(
                    {"scrollWidth": 375, "bodyScrollWidth": 375, "innerWidth": 375,
                     "semantic": [], "targets": [], "positiveTabindex": [],
                     "focusableElements": []},
                    axe_result,
                )
            )
            with patch.object(verify, "OUTPUT_DIR", Path(temp_dir)), patch.object(
                verify, "ROOT", Path(temp_dir)
            ), patch.dict(sys.modules, {"playwright": playwright, "playwright.sync_api": sync_api}):
                result = verify.verify_html("page.html")
        self.assertEqual(result["hard_gates"]["axe_accessibility"]["status"], "FAIL")
        self.assertEqual(result["hard_gates"]["wcag_aa_contrast"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
