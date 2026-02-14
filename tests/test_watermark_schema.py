import unittest

from photohub.services.watermarks import normalize_watermark_config


class WatermarkSchemaTests(unittest.TestCase):
    def test_legacy_text_and_opacity_are_migrated_to_v2(self):
        cfg = normalize_watermark_config(
            {
                "enabled": True,
                "text": "CLIENT_X",
                "opacity": 180,  # legacy 0..255
            }
        )
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["version"], 2)
        self.assertEqual(cfg["text"]["template"], "CLIENT_X")
        self.assertEqual(cfg["text"]["opacity"], 71)
        self.assertIn("logo", cfg["render_order"])
        self.assertIn("text", cfg["render_order"])

    def test_clamps_out_of_range_values(self):
        cfg = normalize_watermark_config(
            {
                "enabled": True,
                "text": {
                    "size_pct": 1000,
                    "angle_deg": 999,
                    "opacity": -5,
                },
                "logo": {
                    "size_pct": -10,
                    "angle_deg": -999,
                    "opacity": 999,
                },
            }
        )
        self.assertEqual(cfg["text"]["size_pct"], 80.0)
        self.assertEqual(cfg["text"]["angle_deg"], 180.0)
        self.assertEqual(cfg["text"]["opacity"], 0)
        self.assertEqual(cfg["logo"]["size_pct"], 0.5)
        self.assertEqual(cfg["logo"]["angle_deg"], -180.0)
        self.assertEqual(cfg["logo"]["opacity"], 100)


if __name__ == "__main__":
    unittest.main()
