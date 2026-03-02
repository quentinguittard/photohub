"""Shared test fixtures for PhotoHub test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from photohub.config import AppPaths
from photohub.db import Base, create_session_factory, create_sqlite_engine, init_db


@pytest.fixture()
def db_engine(tmp_path: Path):
    """Create an in-memory SQLite engine with the full PhotoHub schema."""
    db_file = tmp_path / "test.db"
    engine = create_sqlite_engine(db_file)
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Return a session factory bound to a fresh test database."""
    return create_session_factory(db_engine)


@pytest.fixture()
def app_paths(tmp_path: Path) -> AppPaths:
    """Build AppPaths pointing at a temporary directory."""
    data_dir = tmp_path / "photohub_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    projects_dir = data_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "photohub.db"
    return AppPaths(
        data_dir=data_dir,
        projects_dir=projects_dir,
        db_path=db_path,
    )
