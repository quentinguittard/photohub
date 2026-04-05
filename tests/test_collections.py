"""Tests for CollectionService."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.collections import CollectionService
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
    collection_service = CollectionService(session_factory=session_factory)
    return engine, session_factory, project_service, collection_service


def _seed_assets(session_factory, project_id: int, raw_dir: Path, count: int) -> list[int]:
    asset_ids: list[int] = []
    with session_factory() as session:
        for i in range(1, count + 1):
            path = raw_dir / f"img_{i:04d}.jpg"
            path.write_bytes(f"data-{i}".encode())
            asset = Asset(
                project_id=int(project_id),
                src_path=str(path),
                hash_sha256=f"{i:064d}"[-64:],
                metadata_json="{}",
            )
            session.add(asset)
            session.flush()
            asset_ids.append(int(asset.id))
        session.commit()
    return asset_ids


class CollectionCRUDTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._engine, self._sf, self._ps, self._cs = _build_env(self._td.name)

    def tearDown(self):
        self._engine.dispose()
        self._td.cleanup()

    def _make_project(self, name="Test"):
        return self._ps.create_project(name=name, shoot_date=date(2026, 1, 1))

    def test_create_and_list(self):
        proj = self._make_project()
        col = self._cs.create_collection(project_id=proj.id, name="Livraison")
        self.assertEqual(col.name, "Livraison")
        cols = self._cs.list_collections(project_id=proj.id)
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0][0].name, "Livraison")

    def test_rename(self):
        proj = self._make_project()
        col = self._cs.create_collection(project_id=proj.id, name="Ancienne")
        renamed = self._cs.rename_collection(collection_id=col.id, name="Nouvelle")
        self.assertEqual(renamed.name, "Nouvelle")
        cols = self._cs.list_collections(project_id=proj.id)
        self.assertEqual(cols[0][0].name, "Nouvelle")

    def test_delete_removes_collection_not_assets(self):
        proj = self._make_project()
        raw_dir = Path(proj.root_path) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        asset_ids = _seed_assets(self._sf, proj.id, raw_dir, 2)
        col = self._cs.create_collection(project_id=proj.id, name="Sel")
        self._cs.add_assets(collection_id=col.id, asset_ids=asset_ids)
        self._cs.delete_collection(collection_id=col.id)
        # Collection gone
        self.assertEqual(self._cs.list_collections(project_id=proj.id), [])
        # Assets still in DB
        from sqlalchemy import select
        from photohub.models import Asset as A
        with self._sf() as session:
            count = session.query(A).filter_by(project_id=proj.id).count()
        self.assertEqual(count, 2)

    def test_duplicate_name_same_project_raises(self):
        proj = self._make_project()
        self._cs.create_collection(project_id=proj.id, name="Dup")
        with self.assertRaises(ValueError):
            self._cs.create_collection(project_id=proj.id, name="Dup")

    def test_same_name_different_projects_allowed(self):
        p1 = self._make_project("P1")
        p2 = self._make_project("P2")
        self._cs.create_collection(project_id=p1.id, name="Shared")
        col2 = self._cs.create_collection(project_id=p2.id, name="Shared")
        self.assertIsNotNone(col2)


class CollectionAssetTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._engine, self._sf, self._ps, self._cs = _build_env(self._td.name)
        proj = self._ps.create_project(name="Proj", shoot_date=date(2026, 1, 1))
        self._proj = proj
        raw_dir = Path(proj.root_path) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        self._asset_ids = _seed_assets(self._sf, proj.id, raw_dir, 3)
        self._col = self._cs.create_collection(project_id=proj.id, name="Test")

    def tearDown(self):
        self._engine.dispose()
        self._td.cleanup()

    def test_add_assets(self):
        added = self._cs.add_assets(collection_id=self._col.id, asset_ids=self._asset_ids[:2])
        self.assertEqual(added, 2)
        ids = self._cs.get_asset_ids(collection_id=self._col.id)
        self.assertEqual(sorted(ids), sorted(self._asset_ids[:2]))

    def test_remove_assets(self):
        self._cs.add_assets(collection_id=self._col.id, asset_ids=self._asset_ids)
        removed = self._cs.remove_assets(collection_id=self._col.id, asset_ids=[self._asset_ids[0]])
        self.assertEqual(removed, 1)
        ids = self._cs.get_asset_ids(collection_id=self._col.id)
        self.assertNotIn(self._asset_ids[0], ids)
        self.assertEqual(len(ids), 2)

    def test_add_duplicate_is_idempotent(self):
        self._cs.add_assets(collection_id=self._col.id, asset_ids=self._asset_ids[:1])
        added = self._cs.add_assets(collection_id=self._col.id, asset_ids=self._asset_ids[:1])
        self.assertEqual(added, 0)
        self.assertEqual(len(self._cs.get_asset_ids(collection_id=self._col.id)), 1)

    def test_asset_from_other_project_rejected(self):
        other_proj = self._ps.create_project(name="Other", shoot_date=date(2026, 1, 1))
        raw_dir = Path(other_proj.root_path) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        other_ids = _seed_assets(self._sf, other_proj.id, raw_dir, 1)
        added = self._cs.add_assets(collection_id=self._col.id, asset_ids=other_ids)
        self.assertEqual(added, 0)
        self.assertEqual(self._cs.get_asset_ids(collection_id=self._col.id), [])


if __name__ == "__main__":
    unittest.main()
