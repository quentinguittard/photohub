import unittest

from photohub.services.watermarks import render_template


class WatermarkTemplateTests(unittest.TestCase):
    def test_render_replaces_known_tokens(self):
        rendered = render_template(
            "{{client_name}} - {{shoot_date}} - {{photographer_name}}",
            {
                "client_name": "Acme",
                "shoot_date": "2026-02-14",
                "photographer_name": "Alice",
            },
        )
        self.assertEqual(rendered, "Acme - 2026-02-14 - Alice")

    def test_render_uses_empty_for_missing_tokens_and_cleans_spaces(self):
        rendered = render_template(
            " {{project_name}}  {{unknown_var}}   {{export_date}} ",
            {
                "project_name": "Shoot A",
                "export_date": "2026-02-14",
            },
        )
        self.assertEqual(rendered, "Shoot A 2026-02-14")


if __name__ == "__main__":
    unittest.main()
