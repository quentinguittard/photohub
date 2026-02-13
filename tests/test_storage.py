import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import load_settings, resolve_app_paths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.projects import ProjectService
from photohub.services.storage import StorageService


class StorageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_appdata = os.environ.get('APPDATA')
        os.environ['APPDATA'] = self.tempdir.name

        base_paths = resolve_app_paths()
        self.engine = create_sqlite_engine(base_paths.db_path)
        init_db(self.engine)
        self.session_factory = create_session_factory(self.engine)

        project_service = ProjectService(session_factory=self.session_factory, paths=base_paths)
        project = project_service.create_project("Seed", date(2026, 2, 13))
        raw_file = Path(project.root_path) / "raw" / "a.jpg"
        raw_file.write_bytes(b"demo")

        with self.session_factory() as session:
            session.add(
                Asset(
                    project_id=project.id,
                    src_path=str(raw_file),
                    hash_sha256="e" * 64,
                    rating=3,
                    is_rejected=False,
                )
            )
            session.commit()

    def tearDown(self):
        self.engine.dispose()
        if self.old_appdata is None:
            os.environ.pop('APPDATA', None)
        else:
            os.environ['APPDATA'] = self.old_appdata
        self.tempdir.cleanup()

    def test_migration_success_updates_active_data_dir(self):
        service = StorageService()
        new_root = Path(self.tempdir.name) / 'new_storage_root'
        result = service.set_global_storage_root(new_root)
        self.assertEqual(result.status, 'completed')

        settings = load_settings()
        self.assertIn('new_storage_root', settings['active_data_dir'])
        new_data_dir = Path(settings['active_data_dir'])
        self.assertTrue((new_data_dir / 'photohub.db').exists())
        self.assertTrue((new_data_dir / 'projects').exists())

        with sqlite3.connect(new_data_dir / "photohub.db") as conn:
            project_root = conn.execute("SELECT root_path FROM projects LIMIT 1").fetchone()[0]
            asset_src = conn.execute("SELECT src_path FROM assets LIMIT 1").fetchone()[0]

        self.assertIn(str(new_data_dir / "projects"), project_root)
        self.assertIn(str(new_data_dir / "projects"), asset_src)
        self.assertTrue(Path(asset_src).exists())

    def test_migration_failure_keeps_previous_active_data_dir(self):
        service = StorageService()
        before = load_settings()['active_data_dir']

        invalid_file = Path(self.tempdir.name) / 'not_a_dir'
        invalid_file.write_text('x', encoding='utf-8')

        with self.assertRaises(Exception):
            service.set_global_storage_root(invalid_file)

        after = load_settings()['active_data_dir']
        self.assertEqual(before, after)
        self.assertEqual(load_settings()['last_migration_status'], 'failed')

    def test_apply_same_location_repairs_stale_paths(self):
        service = StorageService()
        settings = load_settings()
        active_data_dir = Path(settings["active_data_dir"])
        active_projects = active_data_dir / "projects"

        fake_projects = Path(self.tempdir.name) / "stale_root" / "projects"
        with sqlite3.connect(active_data_dir / "photohub.db") as conn:
            project_root = conn.execute("SELECT root_path FROM projects LIMIT 1").fetchone()[0]
            asset_src = conn.execute("SELECT src_path FROM assets LIMIT 1").fetchone()[0]

            stale_project_root = project_root.replace(str(active_projects), str(fake_projects))
            stale_asset_src = asset_src.replace(str(active_projects), str(fake_projects))
            Path(stale_project_root).mkdir(parents=True, exist_ok=True)
            Path(stale_asset_src).parent.mkdir(parents=True, exist_ok=True)
            Path(stale_asset_src).write_bytes(b"old-copy")

            conn.execute("UPDATE projects SET root_path = ?", (stale_project_root,))
            conn.execute("UPDATE assets SET src_path = ?", (stale_asset_src,))
            conn.commit()

        service.set_global_storage_root(active_data_dir)

        with sqlite3.connect(active_data_dir / "photohub.db") as conn:
            repaired_root = conn.execute("SELECT root_path FROM projects LIMIT 1").fetchone()[0]
            repaired_asset = conn.execute("SELECT src_path FROM assets LIMIT 1").fetchone()[0]

        self.assertIn(str(active_projects), repaired_root)
        self.assertIn(str(active_projects), repaired_asset)
        self.assertTrue(Path(repaired_asset).exists())

    def test_migration_to_non_empty_destination_preserves_unrelated_files(self):
        service = StorageService()
        new_root = Path(self.tempdir.name) / "existing_folder"
        new_data_dir = new_root / "PhotoHub"
        new_data_dir.mkdir(parents=True, exist_ok=True)
        keep_file = new_data_dir / "do_not_touch.txt"
        keep_file.write_text("keep", encoding="utf-8")

        result = service.set_global_storage_root(new_root)
        self.assertEqual(result.status, "completed")
        self.assertTrue(keep_file.exists())


if __name__ == '__main__':
    unittest.main()
