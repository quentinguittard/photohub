import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.culling import CullingService
from photohub.services.projects import ProjectService


class CullingServiceTests(unittest.TestCase):
    def test_filters_and_updates(self):
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
            project = project_service.create_project("Shoot Culling", date(2026, 2, 13))

            file_a = Path(project.root_path) / "raw" / "a.jpg"
            file_b = Path(project.root_path) / "raw" / "b.jpg"
            file_a.write_bytes(b"a")
            file_b.write_bytes(b"b")

            with session_factory() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_a),
                        hash_sha256="a" * 64,
                        rating=1,
                        is_rejected=False,
                    )
                )
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_b),
                        hash_sha256="b" * 64,
                        rating=4,
                        is_rejected=True,
                    )
                )
                session.commit()

            service = CullingService(session_factory=session_factory)
            all_items = service.list_assets(project.id, rejected_mode="all", min_rating=0)
            self.assertEqual(len(all_items), 2)

            kept = service.list_assets(project.id, rejected_mode="kept", min_rating=0)
            self.assertEqual(len(kept), 1)
            self.assertFalse(kept[0].is_rejected)

            rejected = service.list_assets(project.id, rejected_mode="rejected", min_rating=0)
            self.assertEqual(len(rejected), 1)
            self.assertTrue(rejected[0].is_rejected)

            rated = service.list_assets(project.id, rejected_mode="all", min_rating=3)
            self.assertEqual(len(rated), 1)
            self.assertEqual(rated[0].rating, 4)

            service.update_asset(all_items[0].id, rating=5)
            updated = service.list_assets(project.id, rejected_mode="all", min_rating=5)
            self.assertEqual(len(updated), 1)
            self.assertEqual(updated[0].rating, 5)

            state = service.toggle_rejected(all_items[0].id)
            self.assertTrue(state)
            rejected_after = service.list_assets(project.id, rejected_mode="rejected", min_rating=0)
            self.assertEqual(len(rejected_after), 2)

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
