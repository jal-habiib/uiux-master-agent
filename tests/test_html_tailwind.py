import unittest

from renderers.html_tailwind import render


class HtmlTailwindSecurityTests(unittest.TestCase):
    def test_escapes_text_and_ignores_unknown_sections(self):
        output = render(
            {
                "name": '"><script>alert(1)</script>',
                "structure": ["header", "<img src=x onerror=alert(1)>", "body"],
                "content": {
                    "title": '<img src=x onerror="alert(1)">',
                    "body": "<script>alert(1)</script>",
                },
            }
        )

        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertNotIn("<img src=x", output)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", output)
        self.assertIn("onerror=&quot;alert(1)&quot;", output)

    def test_rejects_malicious_palette_values(self):
        with self.assertRaises(ValueError):
            render({"palette": {"accent": "red; background-image:url(javascript:alert(1))"}})

    def test_preserves_normal_rendering(self):
        output = render(
            {
                "name": "pricing-card",
                "structure": ["header", "price", "features", "cta"],
                "content": {"title": "Starter", "price": "UGX 10,000", "features": ["Email support"]},
                "palette": {"bg": "#fff", "text": "#111111", "accent": "#2563EB", "muted": "#6B7280"},
            }
        )

        self.assertIn("<title>pricing-card</title>", output)
        self.assertIn("UGX 10,000", output)
        self.assertIn("Email support", output)
        self.assertIn("--accent: #2563EB;", output)


if __name__ == "__main__":
    unittest.main()
