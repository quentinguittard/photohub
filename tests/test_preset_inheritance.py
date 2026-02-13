import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.preset_defaults import default_preset_config
from photohub.services.presets import PresetService
from photohub.services.projects import ProjectService


class PresetInheritanceTests(unittest.TestCase):
    def test_effective_config_uses_assigned_project_preset_only(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / "db.sqlite"
            projects_dir = base / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)

            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)

            project_service = ProjectService(
                session_factory=session_factory,
                paths=AppPaths(data_dir=base, db_path=db_path, projects_dir=projects_dir),
            )
            preset_service = PresetService(session_factory=session_factory)

            global_preset = preset_service.create_preset(
                name="GLOBAL_BASE",
                scope="global",
                config={
                    "delivery": {"create_zip": False, "create_report": True, "create_contact_sheet_pdf": True},
                    "watermark": {"enabled": False, "text": "", "opacity": 30},
                },
            )
            self.assertIsNotNone(global_preset)

            project = project_service.create_project(
                name="Shoot Inheritance",
                shoot_date=date(2026, 2, 13),
                client_name="ClientX",
            )
            loaded = project_service.get_project(project.id)
            self.assertIsNotNone(loaded)
            self.assertIsNotNone(loaded.client_id)

            preset_service.create_preset(
                name="CLIENT_X",
                scope="client",
                scope_ref_id=loaded.client_id,
                config={
                    "delivery": {"create_zip": False, "create_report": False},
                    "watermark": {"text": "ClientX"},
                },
            )
            preset_service.create_preset(
                name="PROJECT_OVERRIDE",
                scope="project",
                scope_ref_id=project.id,
                config={
                    "watermark": {"enabled": True, "opacity": 80},
                },
            )

            effective = preset_service.resolve_effective_config_for_project(project.id)
            defaults = default_preset_config()
            self.assertEqual(effective["delivery"]["create_zip"], defaults["delivery"]["create_zip"])
            self.assertEqual(effective["delivery"]["create_report"], defaults["delivery"]["create_report"])
            self.assertEqual(effective["watermark"]["enabled"], defaults["watermark"]["enabled"])
            self.assertEqual(effective["watermark"]["text"], defaults["watermark"]["text"])
            self.assertEqual(effective["watermark"]["opacity"], defaults["watermark"]["opacity"])

            assigned = preset_service.create_preset(
                name="ASSIGNED_LAST",
                scope="global",
                config={
                    "delivery": {"create_zip": False, "create_report": False},
                    "watermark": {"enabled": True, "text": "ASSIGNED", "opacity": 55},
                },
            )
            project_service.assign_preset(project.id, assigned.id)
            effective_assigned = preset_service.resolve_effective_config_for_project(project.id)
            self.assertFalse(effective_assigned["delivery"]["create_zip"])
            self.assertFalse(effective_assigned["delivery"]["create_report"])
            self.assertTrue(effective_assigned["watermark"]["enabled"])
            self.assertEqual(effective_assigned["watermark"]["text"], "ASSIGNED")
            self.assertEqual(effective_assigned["watermark"]["opacity"], 55)

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
