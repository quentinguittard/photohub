import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.exports import ExportService
from photohub.services.projects import ProjectService


class ExportFilterTests(unittest.TestCase):
    def test_watermark_opacity_normalization_supports_legacy_scale(self):
        self.assertEqual(ExportService._normalize_opacity_percentage(0), 0)
        self.assertEqual(ExportService._normalize_opacity_percentage(70), 70)
        self.assertEqual(ExportService._normalize_opacity_percentage(180), 71)
        self.assertEqual(ExportService._normalize_opacity_percentage(255), 100)

    def test_export_respects_min_rating(self):
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
            project = project_service.create_project("Shoot Export", date(2026, 2, 13))

            file_low = Path(project.root_path) / "raw" / "low.jpg"
            file_high = Path(project.root_path) / "raw" / "high.jpg"
            file_low.write_bytes(b"low")
            file_high.write_bytes(b"high")

            with session_factory() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_low),
                        hash_sha256="c" * 64,
                        rating=1,
                        is_rejected=False,
                    )
                )
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(file_high),
                        hash_sha256="d" * 64,
                        rating=4,
                        is_rejected=False,
                    )
                )
                session.commit()

            service = ExportService(session_factory=session_factory)
            out_dir = base / "delivery"
            batch = service.run_export(
                project_id=project.id,
                destination_dir=out_dir,
                profiles=["web"],
                min_rating=3,
                create_zip=True,
                create_report=True,
                create_contact_sheet=True,
            )
            self.assertEqual(len(batch.profiles), 1)
            self.assertEqual(batch.profiles[0].exported, 1)
            self.assertEqual(batch.profiles[0].failed, 0)
            self.assertIsNotNone(batch.report_path)
            self.assertIsNotNone(batch.zip_path)
            self.assertIsNotNone(batch.contact_sheet_path)
            self.assertTrue(batch.report_path.exists())
            self.assertTrue(batch.zip_path.exists())
            self.assertTrue(batch.contact_sheet_path.exists())

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
