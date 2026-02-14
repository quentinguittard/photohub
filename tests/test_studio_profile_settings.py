import os
import tempfile
import unittest

from photohub.config import load_settings
from photohub.services.storage import StorageService


class StudioProfileSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tempdir.name

    def tearDown(self):
        if self.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old_appdata
        self.tempdir.cleanup()

    def test_storage_service_roundtrip_studio_profile(self):
        service = StorageService()
        initial = service.get_studio_profile()
        self.assertEqual(initial["studio_name"], "")
        self.assertEqual(initial["photographer_name"], "")
        self.assertEqual(initial["copyright_notice"], "")

        saved = service.set_studio_profile(
            studio_name="Mon Studio",
            photographer_name="Quentin",
            copyright_notice="(c) 2026 Mon Studio",
        )
        self.assertEqual(saved["studio_name"], "Mon Studio")
        self.assertEqual(saved["photographer_name"], "Quentin")

        loaded = load_settings()["studio_profile"]
        self.assertEqual(loaded["studio_name"], "Mon Studio")
        self.assertEqual(loaded["photographer_name"], "Quentin")
        self.assertEqual(loaded["copyright_notice"], "(c) 2026 Mon Studio")


if __name__ == "__main__":
    unittest.main()
