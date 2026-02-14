import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.services.culling import CullingService
from photohub.services.imports import ImportService
from photohub.services.metadata import MetadataService
from photohub.services.projects import ProjectService

try:
    from PIL import Image

    PIL_AVAILABLE = True
except Exception:
    Image = None
    PIL_AVAILABLE = False


@unittest.skipUnless(PIL_AVAILABLE, "Pillow requis pour tests metadata.")
class MetadataFeatureTests(unittest.TestCase):
    @staticmethod
    def _create_image_with_exif(path: Path, *, iso: int, lens: str, dt: str) -> None:
        assert Image is not None
        img = Image.new("RGB", (2000, 1300), color=(40, 60, 80))
        exif = Image.Exif()
        exif[271] = "Canon"
        exif[272] = "EOS R6"
        exif[42036] = lens
        exif[34855] = int(iso)
        exif[33437] = (28, 10)  # f/2.8
        exif[33434] = (1, 250)  # 1/250
        exif[37386] = (50, 1)  # 50mm
        exif[36867] = dt
        img.save(path, format="JPEG", exif=exif)
        img.close()

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
        import_service = ImportService(session_factory=session_factory)
        culling_service = CullingService(session_factory=session_factory)
        metadata_service = MetadataService(session_factory=session_factory)
        return td, engine, project_service, import_service, culling_service, metadata_service

    def test_import_extracts_exif_and_advanced_filters_work(self):
        td, engine, project_service, import_service, culling_service, metadata_service = self._build_env()
        try:
            source_dir = Path(td.name) / "source"
            source_dir.mkdir(parents=True, exist_ok=True)
            self._create_image_with_exif(
                source_dir / "a.jpg",
                iso=640,
                lens="RF24-70mm F2.8 L IS USM",
                dt="2026:02:13 10:11:12",
            )
            project = project_service.create_project("Meta Test", date(2026, 2, 13))
            result = import_service.run_import(project_id=project.id, source_dir=source_dir)
            self.assertEqual(result.copied, 1)

            assets = culling_service.list_assets(
                project_id=project.id,
                rejected_mode="all",
                min_rating=0,
                iso_min=400,
                iso_max=800,
                lens_contains="24-70",
                shot_date_from="2026-02-13",
                shot_date_to="2026-02-13",
            )
            self.assertEqual(len(assets), 1)
            asset = assets[0]
            self.assertEqual(asset.exif_iso, 640)
            self.assertIn("24-70", str(asset.exif_lens))
            self.assertEqual(asset.exif_shot_date, "2026-02-13")

            metadata_service.update_asset_iptc(
                asset.id,
                keywords="wedding, ceremony",
                author="Studio Test",
                copyright_text="(c) 2026",
            )
            by_keyword = culling_service.list_assets(
                project_id=project.id,
                rejected_mode="all",
                min_rating=0,
                keyword="wedding",
            )
            self.assertEqual(len(by_keyword), 1)

            md = metadata_service.get_asset_metadata(asset.id)
            self.assertEqual(md["iptc"]["author"], "Studio Test")
            self.assertIn("wedding", [item.lower() for item in md["iptc"]["keywords"]])
        finally:
            engine.dispose()
            td.cleanup()

    def test_sync_iptc_to_filtered(self):
        td, engine, project_service, import_service, culling_service, metadata_service = self._build_env()
        try:
            source_dir = Path(td.name) / "source2"
            source_dir.mkdir(parents=True, exist_ok=True)
            self._create_image_with_exif(
                source_dir / "a.jpg",
                iso=200,
                lens="Lens A",
                dt="2026:02:14 09:00:00",
            )
            self._create_image_with_exif(
                source_dir / "b.jpg",
                iso=400,
                lens="Lens B",
                dt="2026:02:14 09:01:00",
            )
            project = project_service.create_project("Meta Sync", date(2026, 2, 14))
            result = import_service.run_import(project_id=project.id, source_dir=source_dir)
            self.assertEqual(result.copied, 2)

            assets = culling_service.list_assets(project_id=project.id, rejected_mode="all", min_rating=0)
            self.assertEqual(len(assets), 2)
            source_asset_id = int(assets[0].id)
            target_asset_id = int(assets[1].id)

            metadata_service.update_asset_iptc(
                source_asset_id,
                keywords="portrait, studio",
                author="Alice",
                copyright_text="(c) Alice",
            )
            sync = metadata_service.sync_iptc_to_filtered(
                project_id=project.id,
                source_asset_id=source_asset_id,
                rejected_mode="all",
                min_rating=0,
            )
            self.assertEqual(sync.status, "completed")
            self.assertEqual(sync.updated, 1)

            target_md = metadata_service.get_asset_metadata(target_asset_id)
            self.assertEqual(target_md["iptc"]["author"], "Alice")
            self.assertIn("studio", [item.lower() for item in target_md["iptc"]["keywords"]])
        finally:
            engine.dispose()
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
