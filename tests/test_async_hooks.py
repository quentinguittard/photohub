import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.culling import CullingService
from photohub.services.exports import ExportService
from photohub.services.imports import ImportService
from photohub.services.projects import ProjectService


class AsyncHooksTests(unittest.TestCase):
    def test_import_supports_progress_and_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / "db.sqlite"
            projects_dir = base / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            engine = create_sqlite_engine(db_path)
            init_db(engine)
            sf = create_session_factory(engine)
            ps = ProjectService(sf, AppPaths(data_dir=base, db_path=db_path, projects_dir=projects_dir))
            service = ImportService(sf)

            project = ps.create_project("ImportCancel", date(2026, 2, 13))
            source = base / "source"
            source.mkdir()
            (source / "a.jpg").write_bytes(b"a")
            (source / "b.jpg").write_bytes(b"b")
            (source / "c.jpg").write_bytes(b"c")

            calls = []
            stop = {"value": False}

            def on_progress(done, total, detail):
                calls.append((done, total, detail))
                if done >= 1:
                    stop["value"] = True

            result = service.run_import(
                project_id=project.id,
                source_dir=source,
                progress_cb=on_progress,
                is_cancelled=lambda: stop["value"],
            )
            self.assertTrue(calls)
            self.assertEqual(result.status, "cancelled")
            engine.dispose()

    def test_export_supports_progress_and_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / "db.sqlite"
            projects_dir = base / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            engine = create_sqlite_engine(db_path)
            init_db(engine)
            sf = create_session_factory(engine)
            ps = ProjectService(sf, AppPaths(data_dir=base, db_path=db_path, projects_dir=projects_dir))
            ex = ExportService(sf)

            project = ps.create_project("ExportCancel", date(2026, 2, 13))
            ps.update_quality_check(project.id, {"enabled": False})
            raw = Path(project.root_path) / "raw"
            f1 = raw / "a.jpg"
            f2 = raw / "b.jpg"
            f1.write_bytes(b"a")
            f2.write_bytes(b"b")
            with sf() as session:
                session.add(
                    Asset(project_id=project.id, src_path=str(f1), hash_sha256="a" * 64, rating=5, is_rejected=False)
                )
                session.add(
                    Asset(project_id=project.id, src_path=str(f2), hash_sha256="b" * 64, rating=5, is_rejected=False)
                )
                session.commit()

            stop = {"value": False}
            progress = []

            def on_progress(done, total, detail):
                progress.append((done, total))
                if done >= 1:
                    stop["value"] = True

            batch = ex.run_export(
                project_id=project.id,
                destination_dir=base / "out",
                profiles=["web", "print"],
                progress_cb=on_progress,
                is_cancelled=lambda: stop["value"],
            )
            self.assertTrue(progress)
            self.assertTrue(any(item.status == "cancelled" for item in batch.profiles))
            engine.dispose()

    def test_culling_bulk_supports_progress_and_cancel(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / "db.sqlite"
            projects_dir = base / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            engine = create_sqlite_engine(db_path)
            init_db(engine)
            sf = create_session_factory(engine)
            ps = ProjectService(sf, AppPaths(data_dir=base, db_path=db_path, projects_dir=projects_dir))
            cs = CullingService(sf)
            project = ps.create_project("CullCancel", date(2026, 2, 13))
            raw = Path(project.root_path) / "raw"
            with sf() as session:
                for i in range(5):
                    p = raw / f"{i}.jpg"
                    p.write_bytes(b"x")
                    session.add(
                        Asset(
                            project_id=project.id,
                            src_path=str(p),
                            hash_sha256=str(i) * 64,
                            rating=0,
                            is_rejected=False,
                        )
                    )
                session.commit()

            stop = {"value": False}
            calls = []

            def on_progress(done, total, detail):
                calls.append(done)
                if done >= 2:
                    stop["value"] = True

            result = cs.bulk_update_filtered(
                project_id=project.id,
                rating=4,
                progress_cb=on_progress,
                is_cancelled=lambda: stop["value"],
            )
            self.assertEqual(result.status, "cancelled")
            self.assertLess(result.updated, result.total)
            self.assertTrue(calls)
            engine.dispose()

    def test_import_does_not_downgrade_project_status(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / "db.sqlite"
            projects_dir = base / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            engine = create_sqlite_engine(db_path)
            init_db(engine)
            sf = create_session_factory(engine)
            ps = ProjectService(sf, AppPaths(data_dir=base, db_path=db_path, projects_dir=projects_dir))
            service = ImportService(sf)

            project = ps.create_project("ImportNoDowngrade", date(2026, 2, 13))
            ps.update_project_status(project.id, "importe")
            ps.update_project_status(project.id, "en_tri")

            source = base / "source_downgrade"
            source.mkdir()
            (source / "x.jpg").write_bytes(b"x")

            result = service.run_import(project_id=project.id, source_dir=source)
            self.assertIn(result.status, {"completed", "completed_with_errors"})
            refreshed = ps.get_project(project.id)
            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed.status, "en_tri")
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
