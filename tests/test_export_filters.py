import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.exports import ExportService
from photohub.services.projects import ProjectService
from conftest import _PIL_AVAILABLE, write_test_image as _write_image


class ExportFilterTests(unittest.TestCase):
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
            project_service.update_quality_check(project.id, {"enabled": False})

            file_low = Path(project.root_path) / "raw" / "low.jpg"
            file_high = Path(project.root_path) / "raw" / "high.jpg"
            _write_image(file_low)
            _write_image(file_high)

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


@unittest.skipUnless(_PIL_AVAILABLE, "Pillow requis pour tester la gestion d'erreur PIL.")
class ExportPilFailureTests(unittest.TestCase):
    """Regression tests for P0-2: PIL failures must not silently deliver
    unwatermarked originals – they must be counted as failed exports."""

    def _build_env(self):
        td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def test_corrupt_source_file_counts_as_failed_not_exported(self):
        """A corrupt/unreadable image must be counted as failed, NOT silently
        copied as an unwatermarked original to the export directory."""
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("PIL Failure", date(2026, 1, 15))
            ps.update_quality_check(project.id, {"enabled": False})

            # Write a file that PIL cannot open (corrupt bytes).
            bad_src = Path(project.root_path) / "raw" / "corrupt.jpg"
            bad_src.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)  # invalid JPEG body

            with sf() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(bad_src),
                        hash_sha256="e" * 64,
                        rating=4,
                        is_rejected=False,
                    )
                )
                session.commit()

            out_dir = Path(td.name) / "delivery_corrupt"
            result = ex.run_export(
                project_id=project.id,
                destination_dir=out_dir,
                profiles=["web"],
                min_rating=0,
                create_zip=False,
                create_report=False,
                create_contact_sheet=False,
            )

            profile_result = result.profiles[0]
            # Must be counted as a failure, NOT as exported.
            self.assertEqual(profile_result.failed, 1, "Corrupt image must count as failed.")
            self.assertEqual(profile_result.exported, 0, "Corrupt image must NOT count as exported.")

            # No files must be written to the output directory (no silent raw copy).
            web_dir = out_dir / "web"
            written_files = list(web_dir.rglob("*")) if web_dir.exists() else []
            written_files = [f for f in written_files if f.is_file()]
            self.assertEqual(
                len(written_files),
                0,
                f"No output file should exist for a failed export, but found: {written_files}",
            )
        finally:
            engine.dispose()
            td.cleanup()

    def test_valid_image_with_watermark_counts_as_exported(self):
        """Sanity check: a valid image must still be counted as exported."""
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("PIL OK", date(2026, 1, 15))
            ps.update_quality_check(project.id, {"enabled": False})

            good_src = Path(project.root_path) / "raw" / "good.jpg"
            _write_image(good_src)

            with sf() as session:
                session.add(
                    Asset(
                        project_id=project.id,
                        src_path=str(good_src),
                        hash_sha256="f" * 64,
                        rating=4,
                        is_rejected=False,
                    )
                )
                session.commit()

            out_dir = Path(td.name) / "delivery_ok"
            result = ex.run_export(
                project_id=project.id,
                destination_dir=out_dir,
                profiles=["web"],
                min_rating=0,
                create_zip=False,
                create_report=False,
                create_contact_sheet=False,
            )

            profile_result = result.profiles[0]
            self.assertEqual(profile_result.exported, 1, "Valid image must be counted as exported.")
            self.assertEqual(profile_result.failed, 0, "Valid image must not be counted as failed.")
        finally:
            engine.dispose()
            td.cleanup()


@unittest.skipUnless(_PIL_AVAILABLE, "Pillow requis pour tester la preservation EXIF.")
class ExportExifPreservationTests(unittest.TestCase):
    """Regression tests for P1-4: EXIF metadata must survive the export pipeline."""

    _COPYRIGHT_TAG = 33432   # EXIF tag for Copyright
    _ARTIST_TAG = 315        # EXIF tag for Artist/Author

    def _build_env(self):
        td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def _write_jpeg_with_exif(self, path: Path, copyright_text: str) -> None:
        """Write a small JPEG embedding a copyright EXIF tag."""
        from PIL import Image as _Image
        img = _Image.new("RGB", (20, 20), color=(64, 64, 64))
        exif = img.getexif()
        exif[self._COPYRIGHT_TAG] = copyright_text
        exif[self._ARTIST_TAG] = "Test Author"
        img.save(path, format="JPEG", exif=exif.tobytes())

    def test_exif_copyright_preserved_through_jpeg_export(self):
        """Copyright EXIF tag must be present in the exported JPEG."""
        from PIL import Image as _Image
        td, engine, sf, ps, ex = self._build_env()
        try:
            project = ps.create_project("EXIF Test", date(2026, 1, 15))
            ps.update_quality_check(project.id, {"enabled": False})

            src = Path(project.root_path) / "raw" / "photo.jpg"
            self._write_jpeg_with_exif(src, "© 2026 Photographer")

            with sf() as session:
                from photohub.models import Asset
                session.add(Asset(
                    project_id=project.id,
                    src_path=str(src),
                    hash_sha256="a" * 64,
                    rating=4,
                    is_rejected=False,
                ))
                session.commit()

            out_dir = Path(td.name) / "delivery"
            result = ex.run_export(
                project_id=project.id,
                destination_dir=out_dir,
                profiles=["web"],
                min_rating=0,
                create_zip=False,
                create_report=False,
                create_contact_sheet=False,
            )
            self.assertEqual(result.profiles[0].exported, 1)

            exported_files = [f for f in (out_dir / "web").rglob("*") if f.is_file()]
            self.assertEqual(len(exported_files), 1)

            with _Image.open(exported_files[0]) as exported:
                out_exif = exported.getexif()

            self.assertIn(self._COPYRIGHT_TAG, out_exif,
                          "Copyright EXIF tag must survive export.")
            self.assertIn("2026", str(out_exif[self._COPYRIGHT_TAG]),
                          "Copyright text content must be preserved.")
        finally:
            engine.dispose()
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
