import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.services.presets import PresetService
from photohub.services.projects import ProjectService


class ProjectCustomRootTests(unittest.TestCase):
    def test_create_project_uses_custom_root_path(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / 'db.sqlite'
            default_projects = base / 'default_projects'
            default_projects.mkdir(parents=True, exist_ok=True)

            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)

            service = ProjectService(
                session_factory=session_factory,
                paths=AppPaths(data_dir=base, db_path=db_path, projects_dir=default_projects),
            )

            custom_parent = base / 'custom_parent'
            custom_parent.mkdir(parents=True, exist_ok=True)

            project = service.create_project(
                name='Client A',
                shoot_date=date(2026, 2, 13),
                custom_root_path=str(custom_parent),
            )

            project_root = Path(project.root_path)
            self.assertEqual(project_root.parent, custom_parent)
            self.assertTrue((project_root / 'raw').exists())
            self.assertTrue((project_root / 'exports').exists())
            self.assertTrue((project_root / 'backup').exists())

            engine.dispose()

    def test_get_project_returns_loaded_preset(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / 'db.sqlite'
            default_projects = base / 'default_projects'
            default_projects.mkdir(parents=True, exist_ok=True)

            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)

            project_service = ProjectService(
                session_factory=session_factory,
                paths=AppPaths(data_dir=base, db_path=db_path, projects_dir=default_projects),
            )
            preset_service = PresetService(session_factory=session_factory)
            preset = preset_service.create_preset(name='Preset Test')

            project = project_service.create_project(
                name='Client B',
                shoot_date=date(2026, 2, 13),
                preset_id=preset.id,
            )
            loaded = project_service.get_project(project.id)
            self.assertIsNotNone(loaded)
            self.assertIsNotNone(loaded.preset)
            self.assertEqual(loaded.preset.name, 'Preset Test')

            engine.dispose()

    def test_update_project_status_with_valid_transitions(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / 'db.sqlite'
            default_projects = base / 'default_projects'
            default_projects.mkdir(parents=True, exist_ok=True)

            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)

            project_service = ProjectService(
                session_factory=session_factory,
                paths=AppPaths(data_dir=base, db_path=db_path, projects_dir=default_projects),
            )
            project = project_service.create_project(
                name='Client C',
                shoot_date=date(2026, 2, 13),
            )

            project_service.update_project_status(project.id, 'importe')
            project_service.update_project_status(project.id, 'en_tri')
            project_service.update_project_status(project.id, 'pret_a_livrer')
            project_service.update_project_status(project.id, 'archive')
            loaded = project_service.get_project(project.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, 'archive')

            engine.dispose()

    def test_update_project_status_rejects_invalid_value(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / 'db.sqlite'
            default_projects = base / 'default_projects'
            default_projects.mkdir(parents=True, exist_ok=True)

            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)

            project_service = ProjectService(
                session_factory=session_factory,
                paths=AppPaths(data_dir=base, db_path=db_path, projects_dir=default_projects),
            )
            project = project_service.create_project(
                name='Client D',
                shoot_date=date(2026, 2, 13),
            )

            with self.assertRaises(ValueError):
                project_service.update_project_status(project.id, 'inconnu')

            engine.dispose()

    def test_update_project_status_rejects_invalid_transition(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = base / 'db.sqlite'
            default_projects = base / 'default_projects'
            default_projects.mkdir(parents=True, exist_ok=True)

            engine = create_sqlite_engine(db_path)
            init_db(engine)
            session_factory = create_session_factory(engine)

            project_service = ProjectService(
                session_factory=session_factory,
                paths=AppPaths(data_dir=base, db_path=db_path, projects_dir=default_projects),
            )
            project = project_service.create_project(
                name='Client E',
                shoot_date=date(2026, 2, 13),
            )

            with self.assertRaises(ValueError):
                project_service.update_project_status(project.id, 'pret_a_livrer')

            loaded = project_service.get_project(project.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, 'a_importer')

            engine.dispose()


if __name__ == '__main__':
    unittest.main()
