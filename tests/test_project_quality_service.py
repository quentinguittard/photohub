import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset, Project
from photohub.services.projects import ProjectService
from photohub.services.quality_checks import QualityChecklistError
from sqlalchemy import select


class ProjectQualityServiceTests(unittest.TestCase):
    def _build_env(self):
        td = tempfile.TemporaryDirectory()
        base = Path(td.name)
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
        return td, engine, session_factory, project_service

    def test_manual_validation_and_stale_after_metadata_change(self):
        td, engine, sf, project_service = self._build_env()
        try:
            project = project_service.create_project("QC Project", date(2026, 2, 14))
            raw = Path(project.root_path) / "raw"
            src = raw / "a.jpg"
            src.write_bytes(b"a")

            with sf() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(src),
                        hash_sha256="a" * 64,
                        rating=4,
                        is_rejected=False,
                        iptc_author="Studio",
                        iptc_copyright="(c) Studio",
                        metadata_json="{}",
                    )
                )
                session.commit()

            before = project_service.get_quality_check(project.id, export_min_rating=1)
            self.assertEqual(before["status"], "not_validated")

            validated = project_service.validate_quality_check(project.id)
            self.assertEqual(validated["status"], "validated")
            self.assertTrue(validated["validated_at_utc"])

            with sf() as session:
                asset = session.scalar(select(Asset).where(Asset.project_id == project.id).limit(1))
                assert asset is not None
                asset.iptc_author = ""
                session.commit()

            stale = project_service.get_quality_check(project.id, export_min_rating=1)
            self.assertEqual(stale["status"], "stale")
            self.assertFalse(stale["can_export"])
        finally:
            engine.dispose()
            td.cleanup()

    def test_update_quality_check_resets_manual_validation(self):
        td, engine, sf, project_service = self._build_env()
        try:
            project = project_service.create_project("QC Reset", date(2026, 2, 14))
            raw = Path(project.root_path) / "raw"
            src = raw / "b.jpg"
            src.write_bytes(b"b")
            with sf() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(src),
                        hash_sha256="b" * 64,
                        rating=5,
                        is_rejected=False,
                        iptc_author="Studio",
                        iptc_copyright="(c) Studio",
                        metadata_json="{}",
                    )
                )
                session.commit()

            project_service.validate_quality_check(project.id)
            updated = project_service.update_quality_check(
                project.id,
                {
                    "enabled": True,
                    "rules": {
                        "min_rating_non_zero": {"enabled": True},
                        "metadata_author_copyright": {"enabled": False},
                        "watermark_enabled": {"enabled": False},
                    },
                },
            )
            self.assertEqual(updated["status"], "not_validated")
            self.assertEqual(updated["validation"], {})
        finally:
            engine.dispose()
            td.cleanup()

    def test_invalid_project_json_falls_back_to_safe_defaults(self):
        td, engine, sf, project_service = self._build_env()
        try:
            project = project_service.create_project("QC Legacy", date(2026, 2, 14))
            with sf() as session:
                model = session.get(Project, project.id)
                assert model is not None
                model.quality_check_config_json = "{invalid"
                model.quality_check_validation_json = "{invalid"
                session.commit()

            snapshot = project_service.get_quality_check(project.id, export_min_rating=1)
            self.assertEqual(snapshot["config"]["enabled"], True)
            self.assertEqual(snapshot["validation"], {})
            self.assertIn(snapshot["status"], {"not_validated", "validated", "stale", "disabled"})
        finally:
            engine.dispose()
            td.cleanup()

    def test_validate_raises_if_required_metadata_missing(self):
        td, engine, sf, project_service = self._build_env()
        try:
            project = project_service.create_project("QC Missing", date(2026, 2, 14))
            raw = Path(project.root_path) / "raw"
            src = raw / "c.jpg"
            src.write_bytes(b"c")
            with sf() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(src),
                        hash_sha256="c" * 64,
                        rating=4,
                        is_rejected=False,
                        iptc_author="",
                        iptc_copyright="",
                        metadata_json="{}",
                    )
                )
                session.commit()

            with self.assertRaises(QualityChecklistError):
                project_service.validate_quality_check(project.id)
        finally:
            engine.dispose()
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
