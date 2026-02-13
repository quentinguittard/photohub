from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_sqlite_engine(db_path: Path):
    return create_engine(f"sqlite:///{db_path}", future=True)


def create_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(engine) -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _run_sqlite_migrations(engine)


def _run_sqlite_migrations(engine) -> None:
    with engine.begin() as conn:
        _ensure_column(conn, table="projects", column="client_id", ddl="INTEGER")


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    columns = {row[1] for row in rows}
    if column in columns:
        return
    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
