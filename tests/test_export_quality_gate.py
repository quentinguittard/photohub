import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import select

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.exports import ExportService
from photohub.services.projects import ProjectService
from photohub.services.quality_checks import QualityChecklistError


class ExportQualityGateTests(unittest.TestCase):
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
        export_service = ExportService(session_factory=session_factory)
        return td, engine, session_factory, project_service, export_service

    def _seed_asset(
        self,
        *,
        sf,
        project_id: int,
        root_path: str,
        file_name: str,
        rating: int,
        author: str,
        copyright_text: str,
        rejected: bool = False,
    ) -> None:
        src = Path(root_path) / "raw" / file_name
        src.write_bytes(b"image-bytes")
        with sf() as session:
            session.add(
                Asset(
                    project_id=project_id,
                    src_path=str(src),
                    hash_sha256=(file_name[0] * 64)[:64],
                    rating=int(rating),
                    is_rejected=bool(rejected),
                    iptc_author=str(author),
                    iptc_copyright=str(copyright_text),
                    metadata_json="{}",
                )
            )
            session.commit()

    def test_export_blocked_when_checklist_not_validated(self):
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("Gate NotValidated", date(2026, 2, 14))
            self._seed_asset(
                sf=sf,
                project_id=project.id,
                root_path=project.root_path,
                file_name="a.jpg",
                rating=4,
                author="Studio",
                copyright_text="(c) Studio",
            )
            with self.assertRaises(QualityChecklistError):
                ex.run_export(
                    project_id=project.id,
                    destination_dir=Path(td.name) / "out",
                    profiles=["web"],
                    min_rating=1,
                    create_zip=False,
                    create_report=False,
                    create_contact_sheet=False,
                )
        finally:
            engine.dispose()
            td.cleanup()

    def test_export_blocked_when_min_rating_is_zero(self):
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("Gate MinRating", date(2026, 2, 14))
            self._seed_asset(
                sf=sf,
                project_id=project.id,
                root_path=project.root_path,
                file_name="b.jpg",
                rating=5,
                author="Studio",
                copyright_text="(c) Studio",
            )
            ps.validate_quality_check(project.id)
            with self.assertRaises(QualityChecklistError):
                ex.run_export(
                    project_id=project.id,
                    destination_dir=Path(td.name) / "out_min0",
                    profiles=["web"],
                    min_rating=0,
                    create_zip=False,
                    create_report=False,
                    create_contact_sheet=False,
                )
        finally:
            engine.dispose()
            td.cleanup()

    def test_export_blocked_when_metadata_changed_after_validation(self):
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("Gate Metadata", date(2026, 2, 14))
            self._seed_asset(
                sf=sf,
                project_id=project.id,
                root_path=project.root_path,
                file_name="c.jpg",
                rating=4,
                author="Studio",
                copyright_text="(c) Studio",
            )
            ps.validate_quality_check(project.id)

            with sf() as session:
                asset = session.scalar(select(Asset).where(Asset.project_id == project.id).limit(1))
                assert asset is not None
                asset.iptc_author = ""
                session.commit()

            with self.assertRaises(QualityChecklistError):
                ex.run_export(
                    project_id=project.id,
                    destination_dir=Path(td.name) / "out_metadata",
                    profiles=["web"],
                    min_rating=1,
                    create_zip=False,
                    create_report=False,
                    create_contact_sheet=False,
                )
        finally:
            engine.dispose()
            td.cleanup()

    def test_export_succeeds_after_manual_validation(self):
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("Gate Success", date(2026, 2, 14))
            self._seed_asset(
                sf=sf,
                project_id=project.id,
                root_path=project.root_path,
                file_name="d.jpg",
                rating=4,
                author="Studio",
                copyright_text="(c) Studio",
            )
            ps.validate_quality_check(project.id)

            result = ex.run_export(
                project_id=project.id,
                destination_dir=Path(td.name) / "out_ok",
                profiles=["web"],
                min_rating=1,
                create_zip=False,
                create_report=False,
                create_contact_sheet=False,
            )
            self.assertEqual(len(result.profiles), 1)
            self.assertEqual(result.profiles[0].status, "completed")
            self.assertEqual(result.profiles[0].failed, 0)
            self.assertEqual(result.profiles[0].exported, 1)
        finally:
            engine.dispose()
            td.cleanup()

    def test_watermark_rule_off_does_not_block_export(self):
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("Gate WatermarkOff", date(2026, 2, 14))
            self._seed_asset(
                sf=sf,
                project_id=project.id,
                root_path=project.root_path,
                file_name="e.jpg",
                rating=4,
                author="Studio",
                copyright_text="(c) Studio",
            )
            ps.update_quality_check(
                project.id,
                {
                    "enabled": True,
                    "rules": {
                        "min_rating_non_zero": {"enabled": True},
                        "metadata_author_copyright": {"enabled": True},
                        "watermark_enabled": {"enabled": False},
                    },
                },
            )
            ps.validate_quality_check(project.id)

            result = ex.run_export(
                project_id=project.id,
                destination_dir=Path(td.name) / "out_watermark_off",
                profiles=["web"],
                min_rating=1,
                create_zip=False,
                create_report=False,
                create_contact_sheet=False,
            )
            self.assertEqual(result.profiles[0].status, "completed")
        finally:
            engine.dispose()
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
