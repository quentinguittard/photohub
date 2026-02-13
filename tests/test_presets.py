import json
import tempfile
import unittest
from pathlib import Path

from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.services.presets import PresetService


class PresetVersioningTests(unittest.TestCase):
    def test_create_update_and_rollback_versions(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "db.sqlite"
            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)
            service = PresetService(session_factory=session_factory)

            preset = service.create_preset(
                name="Preset A",
                scope="project",
                scope_ref_id=12,
                config={"naming": {"pattern": "v1"}},
            )
            versions = service.list_versions(preset.id)
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0].version, 1)

            updated = service.update_preset(
                preset_id=preset.id,
                name="Preset A",
                scope="project",
                scope_ref_id=12,
                config={"naming": {"pattern": "v2"}},
            )
            self.assertIn("v2", updated.config_json)
            versions = service.list_versions(preset.id)
            self.assertEqual(len(versions), 2)
            self.assertEqual(versions[0].version, 2)

            # rollback to previous version content and create a new head version
            previous_version = versions[1]
            rolled = service.rollback_to_version(preset.id, previous_version.id)
            payload = json.loads(rolled.config_json)
            self.assertEqual(payload["naming"]["pattern"], "v1")

            versions = service.list_versions(preset.id)
            self.assertEqual(len(versions), 3)
            self.assertEqual(versions[0].version, 3)

            fetched = service.get_preset(preset.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.scope_ref_id, 12)

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
