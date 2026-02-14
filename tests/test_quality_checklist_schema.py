import unittest

from photohub.services.quality_checks import (
    DEFAULT_PROJECT_QUALITY_CONFIG,
    normalize_quality_config,
    normalize_quality_validation,
)


class QualityChecklistSchemaTests(unittest.TestCase):
    def test_normalize_quality_config_defaults(self):
        cfg = normalize_quality_config(None)
        self.assertEqual(cfg["version"], 1)
        self.assertEqual(cfg["enabled"], True)
        self.assertEqual(cfg["rules"]["min_rating_non_zero"]["enabled"], True)
        self.assertEqual(cfg["rules"]["metadata_author_copyright"]["enabled"], True)
        self.assertEqual(cfg["rules"]["watermark_enabled"]["enabled"], False)
        self.assertEqual(cfg, DEFAULT_PROJECT_QUALITY_CONFIG)

    def test_normalize_quality_config_coerces_legacy_values(self):
        cfg = normalize_quality_config(
            {
                "enabled": "0",
                "rules": {
                    "min_rating_non_zero": {"enabled": "1"},
                    "metadata_author_copyright": {"enabled": "yes"},
                    "watermark_enabled": {"enabled": "off"},
                },
            }
        )
        self.assertEqual(cfg["enabled"], False)
        self.assertEqual(cfg["rules"]["min_rating_non_zero"]["enabled"], True)
        self.assertEqual(cfg["rules"]["metadata_author_copyright"]["enabled"], True)
        self.assertEqual(cfg["rules"]["watermark_enabled"]["enabled"], False)

    def test_normalize_quality_validation_requires_minimum_payload(self):
        self.assertEqual(normalize_quality_validation({}), {})
        self.assertEqual(normalize_quality_validation({"validated_at_utc": "2026-02-14T20:00:00Z"}), {})
        self.assertEqual(normalize_quality_validation({"fingerprint": "abc"}), {})

        valid = normalize_quality_validation(
            {
                "validated_at_utc": "2026-02-14T20:00:00Z",
                "fingerprint": "abc",
                "summary": {"exportable_count": 10},
            }
        )
        self.assertEqual(valid["validated_at_utc"], "2026-02-14T20:00:00Z")
        self.assertEqual(valid["fingerprint"], "abc")
        self.assertEqual(valid["summary"]["exportable_count"], 10)


if __name__ == "__main__":
    unittest.main()
