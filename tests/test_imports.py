"""Tests for ImportService."""
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.imports import ImportService
from photohub.services.projects import ProjectService


def _build_env(td: str):
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
    import_service = ImportService(session_factory=session_factory)
    return engine, session_factory, project_service, import_service


class ImportOrphanCleanupTests(unittest.TestCase):
    """P0-1 regression: if a file is copied but the import subsequently fails
    (e.g. checksum mismatch), the destination file must be deleted so no
    untracked orphan is left in the raw/ directory."""

    def test_checksum_failure_removes_destination_file(self):
        """When copy_and_hash raises (simulating a corrupted copy or I/O error),
        the destination file that was partially written must be removed from disk
        so no untracked orphan is left in the raw/ directory."""
        with tempfile.TemporaryDirectory() as td:
            engine, sf, ps, importer = _build_env(td)
            try:
                project = ps.create_project("Orphan Test", date(2026, 3, 1))

                # Source directory with one valid file.
                src_dir = Path(td) / "source"
                src_dir.mkdir()
                src_file = src_dir / "photo.jpg"
                src_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

                dest_root = Path(project.root_path) / "raw"
                dest_root.mkdir(parents=True, exist_ok=True)

                # Patch copy_and_hash to simulate a copy that writes the
                # destination file and then fails (e.g. disk I/O error or a
                # hash mismatch detected during the stream).
                def fake_copy_and_hash(src, dst):
                    # Write partial content so the orphan actually exists on disk.
                    dst.write_bytes(b"partial orphan")
                    raise RuntimeError("Simulated copy corruption")

                with patch("photohub.services.imports.copy_and_hash", side_effect=fake_copy_and_hash):
                    result = importer.run_import(
                        project_id=project.id,
                        source_dir=src_dir,
                    )

                self.assertEqual(result.failed, 1)
                self.assertEqual(result.copied, 0)

                # The destination file must NOT exist (orphan was cleaned up).
                orphans = list(dest_root.rglob("*"))
                orphans = [f for f in orphans if f.is_file()]
                self.assertEqual(
                    len(orphans),
                    0,
                    f"Orphaned file(s) found after failed import: {orphans}",
                )

                # No Asset record must have been created.
                with sf() as session:
                    count = session.query(Asset).filter_by(project_id=project.id).count()
                self.assertEqual(count, 0)
            finally:
                engine.dispose()

    def test_successful_import_file_is_tracked(self):
        """Sanity check: a successfully imported file must remain on disk and
        be tracked in the database."""
        with tempfile.TemporaryDirectory() as td:
            engine, sf, ps, importer = _build_env(td)
            try:
                project = ps.create_project("Good Import", date(2026, 3, 1))

                src_dir = Path(td) / "source_ok"
                src_dir.mkdir()
                src_file = src_dir / "frame.jpg"
                src_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

                # Use verify_checksum=False so we don't need a real hash match.
                # The import service reads verify_checksum from preset config.
                # To disable it easily, we patch the config resolver.
                with patch(
                    "photohub.services.imports.resolve_effective_config_for_project_model",
                    return_value={
                        "import": {"verify_checksum": False, "dual_backup": False, "backup_path": ""},
                        "naming": {"pattern": "{project}_{date}_{seq:04d}"},
                    },
                ):
                    result = importer.run_import(
                        project_id=project.id,
                        source_dir=src_dir,
                    )

                self.assertEqual(result.copied, 1)
                self.assertEqual(result.failed, 0)

                dest_root = Path(project.root_path) / "raw"
                files = [f for f in dest_root.rglob("*") if f.is_file()]
                self.assertEqual(len(files), 1)

                with sf() as session:
                    count = session.query(Asset).filter_by(project_id=project.id).count()
                self.assertEqual(count, 1)
            finally:
                engine.dispose()


class ImportChecksumFlagTests(unittest.TestCase):
    """Regression tests for P1-1: source hash must not be computed when verify_checksum=False."""

    def test_verify_checksum_false_skips_source_hash(self):
        """When verify_checksum=False the source file must be hashed exactly 0 times
        (only the destination hash is computed for Asset.hash_sha256)."""
        with tempfile.TemporaryDirectory() as td:
            engine, sf, ps, importer = _build_env(td)
            try:
                project = ps.create_project("Hash Guard", date(2026, 3, 1))

                src_dir = Path(td) / "source_nochk"
                src_dir.mkdir()
                src_file = src_dir / "img.jpg"
                src_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

                call_paths: list[Path] = []

                def recording_sha256(path):
                    call_paths.append(Path(path))
                    # Return a deterministic fake hash so the import succeeds.
                    return "a" * 64

                with patch(
                    "photohub.services.imports.resolve_effective_config_for_project_model",
                    return_value={
                        "import": {"verify_checksum": False, "dual_backup": False, "backup_path": ""},
                        "naming": {"pattern": "{project}_{date}_{seq:04d}"},
                    },
                ), patch("photohub.services.imports.sha256_file", side_effect=recording_sha256):
                    result = importer.run_import(
                        project_id=project.id,
                        source_dir=src_dir,
                    )

                self.assertEqual(result.copied, 1)
                # Only the destination file should have been hashed (once).
                self.assertEqual(len(call_paths), 1,
                    f"Expected 1 sha256 call (destination only), got {len(call_paths)}: {call_paths}")
                # The single call must be for the destination, not the source.
                self.assertNotEqual(call_paths[0], src_file,
                    "Source file must NOT be hashed when verify_checksum=False.")
            finally:
                engine.dispose()


class ImportNamingTagTests(unittest.TestCase):
    """Tests for {client} and {preset} tags in the import naming pattern."""

    _BASE_CONFIG = {
        "import": {"verify_checksum": False, "dual_backup": False, "backup_path": ""},
    }

    def _run_import_with_pattern(self, pattern: str, client_name: str | None = None) -> str:
        """Run a single-file import with the given pattern and return the imported filename stem."""
        with tempfile.TemporaryDirectory() as td:
            engine, sf, ps, importer = _build_env(td)
            try:
                project = ps.create_project("Test Project", date(2026, 3, 1))

                # Optionally attach a client to the project.
                if client_name is not None:
                    with sf() as session:
                        from photohub.models import Client, Project as Proj
                        client = Client(name=client_name)
                        session.add(client)
                        session.flush()
                        proj = session.get(Proj, project.id)
                        proj.client_id = client.id
                        session.commit()

                src_dir = Path(td) / "src"
                src_dir.mkdir()
                (src_dir / "img.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

                config = dict(self._BASE_CONFIG)
                config["naming"] = {"pattern": pattern}

                with patch(
                    "photohub.services.imports.resolve_effective_config_for_project_model",
                    return_value=config,
                ):
                    result = importer.run_import(project_id=project.id, source_dir=src_dir)

                self.assertEqual(result.copied, 1, f"Import failed: {result.message}")
                dest_root = Path(project.root_path) / "raw"
                files = [f for f in dest_root.rglob("*") if f.is_file()]
                self.assertEqual(len(files), 1)
                return files[0].stem
            finally:
                engine.dispose()

    def test_client_tag_included_in_filename(self):
        """Pattern {project}_{client}_{date}_{seq:04d} must embed the client name."""
        stem = self._run_import_with_pattern(
            "{project}_{client}_{date}_{seq:04d}", client_name="Dupont Mariage"
        )
        self.assertIn("Dupont_Mariage", stem, f"Client name not found in '{stem}'")

    def test_preset_tag_uses_format_name_directly(self):
        """`_format_name` with preset tag produces correct sanitized output."""
        from photohub.services.imports import ImportService
        result = ImportService._format_name(
            pattern="{project}_{preset}_{date}_{seq:04d}",
            project_name="MyProject",
            shoot_date="20260301",
            seq=1,
            preset="Wedding Deluxe",
        )
        self.assertIn("Wedding_Deluxe", result)
        self.assertNotIn("__", result)

    def test_missing_client_no_double_underscore(self):
        """When project has no client, {client} is empty and must not create __."""
        stem = self._run_import_with_pattern(
            "{project}_{client}_{date}_{seq:04d}", client_name=None
        )
        self.assertNotIn("__", stem, f"Double underscore found in '{stem}'")

    def test_legacy_pattern_unaffected(self):
        """Existing patterns without {client}/{preset} must produce unchanged results."""
        from photohub.services.imports import ImportService
        result = ImportService._format_name(
            pattern="{project}_{date}_{seq:04d}",
            project_name="Wedding",
            shoot_date="20260301",
            seq=5,
        )
        self.assertEqual(result, "Wedding_20260301_0005")


if __name__ == "__main__":
    unittest.main()
