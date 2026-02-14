import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import select

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.projects import ProjectService
from photohub.services.renames import RenameService


class RenameServiceTests(unittest.TestCase):
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
        rename_service = RenameService(session_factory=session_factory)
        return td, engine, session_factory, project_service, rename_service

    @staticmethod
    def _seed_assets(session_factory, project_id: int, raw_dir: Path, names: list[str]) -> list[int]:
        asset_ids: list[int] = []
        with session_factory() as session:
            for idx, name in enumerate(names, start=1):
                path = raw_dir / name
                path.write_bytes(f"asset-{idx}".encode("utf-8"))
                model = Asset(
                    project_id=int(project_id),
                    src_path=str(path),
                    hash_sha256=f"{idx:064d}"[-64:],
                    metadata_json="{}",
                )
                session.add(model)
                session.flush()
                asset_ids.append(int(model.id))
            session.commit()
        return asset_ids

    def test_batch_rename_updates_files_and_db(self):
        td, engine, session_factory, project_service, rename_service = self._build_env()
        try:
            project = project_service.create_project(name="Test Mariage", shoot_date=date(2026, 2, 13))
            raw_dir = Path(project.root_path) / "raw"
            asset_ids = self._seed_assets(
                session_factory=session_factory,
                project_id=project.id,
                raw_dir=raw_dir,
                names=["IMG_0001.JPG", "IMG_0002.JPG"],
            )

            preview = rename_service.preview_batch_rename(
                project_id=project.id,
                asset_ids=asset_ids,
                pattern="{project}_{date}_{seq:03d}",
                start_seq=1,
            )
            self.assertEqual(len(preview), 2)

            result = rename_service.run_batch_rename(
                project_id=project.id,
                asset_ids=asset_ids,
                pattern="{project}_{date}_{seq:03d}",
                start_seq=1,
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.renamed, 2)
            self.assertEqual(result.failed, 0)

            with session_factory() as session:
                db_assets = list(
                    session.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.id.asc())).all()
                )
                self.assertEqual(len(db_assets), 2)
                basenames = [Path(item.src_path).name for item in db_assets]
                self.assertEqual(basenames[0], "Test_Mariage_20260213_001.jpg")
                self.assertEqual(basenames[1], "Test_Mariage_20260213_002.jpg")
                for item in db_assets:
                    self.assertTrue(Path(item.src_path).exists())
        finally:
            engine.dispose()
            td.cleanup()

    def test_batch_rename_handles_name_collisions(self):
        td, engine, session_factory, project_service, rename_service = self._build_env()
        try:
            project = project_service.create_project(name="Studio", shoot_date=date(2026, 2, 13))
            raw_dir = Path(project.root_path) / "raw"
            asset_ids = self._seed_assets(
                session_factory=session_factory,
                project_id=project.id,
                raw_dir=raw_dir,
                names=["A.JPG", "B.JPG"],
            )
            (raw_dir / "same.jpg").write_bytes(b"external")

            result = rename_service.run_batch_rename(
                project_id=project.id,
                asset_ids=asset_ids,
                pattern="same",
                start_seq=1,
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.renamed, 2)

            with session_factory() as session:
                db_assets = list(
                    session.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.id.asc())).all()
                )
                basenames = [Path(item.src_path).name for item in db_assets]
                self.assertEqual(len(set(basenames)), 2)
                self.assertTrue(all(name.startswith("same") for name in basenames))
                self.assertTrue(all(name != "same.jpg" for name in basenames))
            self.assertTrue((raw_dir / "same.jpg").exists())
        finally:
            engine.dispose()
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
