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
        # ignore_cleanup_errors=True avoids teardown failures on Windows when
        # SQLite WAL shared-memory files (.db-shm) are briefly locked.
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def test_migration_failure_does_not_corrupt_storage_root(self):
        """P0-3 regression: storage_root must NOT be updated before the copy
        completes. On migration failure, storage_root must still point to the
        original data directory so the app opens the correct data on restart."""
        service = StorageService()
        settings_before = load_settings()
        before_storage_root = settings_before['storage_root']
        before_active_data_dir = settings_before['active_data_dir']

        # Use a path whose parent is a file – this forces _copy_then_switch to fail.
        invalid_file = Path(self.tempdir.name) / 'blocker'
        invalid_file.write_text('block', encoding='utf-8')

        with self.assertRaises(Exception):
            service.set_global_storage_root(invalid_file)

        settings_after = load_settings()
        # storage_root must be preserved exactly as it was before the migration attempt.
        self.assertEqual(
            settings_after['storage_root'],
            before_storage_root,
            "storage_root must not be changed when migration fails.",
        )
        # active_data_dir must also be intact.
        self.assertEqual(
            settings_after['active_data_dir'],
            before_active_data_dir,
            "active_data_dir must not be changed when migration fails.",
        )
        # Status must reflect failure.
        self.assertEqual(settings_after['last_migration_status'], 'failed')

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


    def test_migration_copies_wal_data_to_new_db(self):
        """P0-4 regression: data written while WAL mode is active must be
        present in the migrated database. The migration must checkpoint the
        WAL before copying so no recently committed data is lost."""
        settings = load_settings()
        db_path = Path(settings['active_data_dir']) / 'photohub.db'

        # Enable WAL mode and disable auto-checkpoint so we can control when
        # WAL frames are merged back into the main database file.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")  # prevent auto-merge

        # Insert a sentinel row via a raw sqlite3 connection (without checkpoint).
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO clients (name, created_at) VALUES ('WAL_SENTINEL', datetime('now'))"
            )
            conn.commit()
            # At this point the row is in the WAL file, not the main DB file.
            # Verify WAL file was actually written.
            wal_file = db_path.parent / (db_path.name + "-wal")
            self.assertTrue(wal_file.exists(), "WAL file must exist after write with auto-checkpoint=0.")

        # Migrate to a new location.  The migration must checkpoint before copy.
        service = StorageService()
        new_root = Path(self.tempdir.name) / 'wal_migration_dest'
        result = service.set_global_storage_root(new_root)
        self.assertEqual(result.status, 'completed')

        # The sentinel row must exist in the new database.
        new_db_path = Path(load_settings()['active_data_dir']) / 'photohub.db'
        with sqlite3.connect(str(new_db_path)) as conn:
            row = conn.execute(
                "SELECT name FROM clients WHERE name = 'WAL_SENTINEL'"
            ).fetchone()
        self.assertIsNotNone(row, "WAL-written row must be present in the migrated database.")
        self.assertEqual(row[0], 'WAL_SENTINEL')


if __name__ == '__main__':
    unittest.main()
