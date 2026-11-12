import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "critic.py"
spec = importlib.util.spec_from_file_location("critic_under_test", MODULE_PATH)
critic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(critic)


class CriticRubricTests(unittest.TestCase):
    def test_authoritative_rubric_has_exact_weights(self):
        self.assertEqual(
            critic.RUBRIC,
            (
                ("Visual hierarchy", 20),
                ("Spacing and rhythm", 15),
                ("Responsive behavior", 15),
                ("Design-system consistency", 15),
                ("State coverage", 15),
                ("Typography", 10),
                ("Code quality and performance", 10),
            ),
        )
        self.assertEqual(sum(weight for _, weight in critic.RUBRIC), 100)

    def test_returns_stable_weighted_breakdown_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text(
                """<!doctype html><html><head><meta name="viewport" content="width=device-width">
                <style>@media (max-width: 600px) { .x { padding: 1rem; } }</style></head>
                <body><h1>Page</h1><h2>Section</h2><button class="x" style="font-size: 1rem">Go</button></body></html>""",
                encoding="utf-8",
            )
            result = critic.critique_code(str(path))

        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)
        self.assertEqual(set(result["weighted_score_breakdown"]), {name for name, _ in critic.RUBRIC})
        self.assertEqual(
            sum(item["weight"] for item in result["weighted_score_breakdown"].values()),
            100,
        )
        self.assertEqual(result["assessment_type"], "static_heuristic")
        self.assertEqual(result["hard_gates_status"], "NOT_VERIFIED")

    def test_score_threshold_is_soft_only(self):
        result = critic._assessment(80, critic._empty_breakdown(), [])

        self.assertEqual(result["score"], 80)
        self.assertTrue(result["score_gate_passed"])
        self.assertTrue(result["passed"])
        self.assertFalse(result["overall_passed"])

    def test_responsive_viewport_and_breakpoint_evidence_are_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            no_viewport = Path(temp_dir) / "no-viewport.html"
            no_viewport.write_text(
                "<html><head><style>@media (max-width: 600px) { .x {} }</style></head>"
                "<body><h1>Page</h1></body></html>",
                encoding="utf-8",
            )
            no_breakpoint = Path(temp_dir) / "no-breakpoint.html"
            no_breakpoint.write_text(
                "<html><head><meta name='viewport'></head><body><h1>Page</h1></body></html>",
                encoding="utf-8",
            )

            viewport_result = critic.critique_code(str(no_viewport))
            breakpoint_result = critic.critique_code(str(no_breakpoint))

        self.assertTrue(any("viewport meta" in issue["message"] for issue in viewport_result["issues"]))
        self.assertFalse(any("viewport meta" in issue["message"] for issue in breakpoint_result["issues"]))
        self.assertTrue(any("breakpoint" in issue["message"] for issue in breakpoint_result["issues"]))
        self.assertFalse(any("breakpoint" in issue["message"] for issue in viewport_result["issues"]))

    def test_page_without_interactive_elements_is_not_penalized_for_states(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<!doctype html><html><body><h1>Static page</h1></body></html>", encoding="utf-8")
            result = critic.critique_code(str(path))

        self.assertFalse(
            any(
                issue["category"] == "State coverage"
                for issue in result["issues"]
            )
        )

    def test_form_controls_are_checked_for_state_coverage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text(
                "<!doctype html><html><body><h1>Form</h1><input></body></html>",
                encoding="utf-8",
            )
            result = critic.critique_code(str(path))

        self.assertTrue(any(issue["category"] == "State coverage" for issue in result["issues"]))

    def test_missing_file_returns_structured_failure(self):
        result = critic.critique_code("does-not-exist.html")

        self.assertEqual(result["score"], 0)
        self.assertFalse(result["score_gate_passed"])
        self.assertFalse(result["overall_passed"])
        self.assertEqual(result["assessment_type"], "static_heuristic")
        self.assertEqual(result["hard_gates_status"], "NOT_VERIFIED")
        self.assertEqual(set(result["weighted_score_breakdown"]), {name for name, _ in critic.RUBRIC})
        self.assertIn("error", result)

    def test_unreadable_file_returns_structured_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html></html>", encoding="utf-8")
            original_read_text = critic.Path.read_text

            def unreadable(*args, **kwargs):
                raise OSError("permission denied")

            try:
                critic.Path.read_text = unreadable
                result = critic.critique_code(str(path))
            finally:
                critic.Path.read_text = original_read_text

        self.assertEqual(result["score"], 0)
        self.assertIn("error", result)
        self.assertFalse(result["overall_passed"])

    def test_image_free_layout_is_not_penalized_and_placeholders_are_not_special(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text(
                """<!doctype html><html><head><meta name="viewport"></head><body>
                <h1>Image-free page</h1><h2>Section</h2>
                <div class="p-4 text-base hover:bg-black">CSS-native visual</div>
                <img src="https://placehold.co/600x400" alt="Example">
                </body></html>""",
                encoding="utf-8",
            )
            result = critic.critique_code(str(path))

        categories = {issue["category"] for issue in result["issues"]}
        self.assertNotIn("Images", categories)
        self.assertNotIn("placehold.co", "\n".join(issue["message"] for issue in result["issues"]))

    def test_issue_is_categorized_and_actionable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "page.html"
            path.write_text("<html><body><p>Plain page</p></body></html>", encoding="utf-8")
            result = critic.critique_code(str(path))

        hierarchy_issues = [
            issue for issue in result["issues"] if issue["category"] == "Visual hierarchy"
        ]
        self.assertTrue(hierarchy_issues)
        self.assertTrue(any("h1" in issue["message"] for issue in hierarchy_issues))


if __name__ == "__main__":
    unittest.main()
