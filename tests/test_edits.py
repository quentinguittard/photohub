import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.edits import EditService
from photohub.services.projects import ProjectService


class EditServiceTests(unittest.TestCase):
    def test_edit_settings_update_copy_and_sync(self):
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
            project = project_service.create_project("Shoot Edit", date(2026, 2, 13))

            file_a = Path(project.root_path) / "raw" / "a.jpg"
            file_b = Path(project.root_path) / "raw" / "b.jpg"
            file_c = Path(project.root_path) / "raw" / "c.jpg"
            file_a.write_bytes(b"a")
            file_b.write_bytes(b"b")
            file_c.write_bytes(b"c")

            with session_factory() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_a),
                        hash_sha256="a" * 64,
                        rating=5,
                        is_rejected=False,
                        metadata_json="{}",
                    )
                )
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_b),
                        hash_sha256="b" * 64,
                        rating=4,
                        is_rejected=False,
                        metadata_json="{}",
                    )
                )
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_c),
                        hash_sha256="c" * 64,
                        rating=1,
                        is_rejected=True,
                        metadata_json="{}",
                    )
                )
                session.commit()

            service = EditService(session_factory=session_factory)
            assets = service.list_assets(project.id, rejected_mode="all", min_rating=0)
            self.assertEqual(len(assets), 3)
            source_id = assets[0].id
            target_id = assets[1].id

            default_settings = service.get_asset_edit_settings(source_id)
            self.assertEqual(default_settings["crop_ratio"], "original")
            self.assertEqual(default_settings["wb_temp"], 5500)

            updated = service.update_asset_edit_settings(
                source_id,
                {
                    "exposure": 9.5,
                    "wb_temp": 1800,
                    "wb_tint": 130,
                    "crop_ratio": "invalid",
                    "straighten": -99,
                },
            )
            self.assertEqual(updated["exposure"], 5.0)
            self.assertEqual(updated["wb_temp"], 2000)
            self.assertEqual(updated["wb_tint"], 100)
            self.assertEqual(updated["crop_ratio"], "original")
            self.assertEqual(updated["straighten"], -45.0)

            copied = service.copy_edit_settings(source_id, target_id)
            self.assertEqual(copied["exposure"], 5.0)
            self.assertEqual(copied["wb_temp"], 2000)

            sync = service.sync_edit_settings_to_filtered(
                project_id=project.id,
                source_asset_id=source_id,
                rejected_mode="kept",
                min_rating=2,
            )
            self.assertEqual(sync.status, "completed")
            self.assertEqual(sync.total, 1)
            self.assertEqual(sync.updated, 1)

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
