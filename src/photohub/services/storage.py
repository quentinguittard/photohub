from __future__ import annotations

import sqlite3
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import (
    compute_app_data_dir_from_root,
    load_settings,
    normalize_accent_color,
    normalize_studio_profile,
    resolve_app_paths,
    save_settings,
)
from ..db import create_sqlite_engine


@dataclass
class MigrationResult:
    status: str
    old_data_dir: Path
    new_data_dir: Path
    message: str


class StorageService:
    def __init__(self):
        self._migration_running = False

    def is_migration_running(self) -> bool:
        return self._migration_running

    def get_settings(self) -> dict:
        return load_settings()

    def set_accent_color(self, accent_color: str) -> str:
        normalized = normalize_accent_color(accent_color)
        settings = load_settings()
        settings["accent_color"] = normalized
        save_settings(settings)
        return normalized

    def get_studio_profile(self) -> dict:
        settings = load_settings()
        return normalize_studio_profile(settings.get("studio_profile"))

    def set_studio_profile(
        self,
        *,
        studio_name: str,
        photographer_name: str,
        copyright_notice: str,
    ) -> dict:
        normalized = normalize_studio_profile(
            {
                "studio_name": studio_name,
                "photographer_name": photographer_name,
                "copyright_notice": copyright_notice,
            }
        )
        settings = load_settings()
        settings["studio_profile"] = normalized
        save_settings(settings)
        return normalized

    def set_global_storage_root(self, new_root: str | Path) -> MigrationResult:
        if self._migration_running:
            raise ValueError("Une migration est deja en cours.")

        self._migration_running = True
        try:
            current_paths = resolve_app_paths()
            old_data_dir = current_paths.data_dir
            new_data_dir = compute_app_data_dir_from_root(new_root)

            settings = load_settings()
            settings["storage_root"] = str(new_data_dir)
            settings["last_migration_status"] = "running"
            settings["last_migration_error"] = None
            save_settings(settings)

            if old_data_dir.resolve() == new_data_dir.resolve():
                self._repair_paths_to_active_projects(
                    db_path=old_data_dir / "photohub.db",
                    active_projects_dir=old_data_dir / "projects",
                )
                settings["active_data_dir"] = str(new_data_dir)
                settings["last_migration_status"] = "completed"
                settings["last_migration_error"] = None
                save_settings(settings)
                return MigrationResult(
                    status="completed",
                    old_data_dir=old_data_dir,
                    new_data_dir=new_data_dir,
                    message="Le stockage est deja sur cet emplacement.",
                )

            self._copy_then_switch(old_data_dir=old_data_dir, new_data_dir=new_data_dir)
            self._repair_paths_to_active_projects(
                db_path=new_data_dir / "photohub.db",
                active_projects_dir=new_data_dir / "projects",
            )

            settings = load_settings()
            settings["storage_root"] = str(new_data_dir)
            settings["active_data_dir"] = str(new_data_dir)
            settings["last_migration_status"] = "completed"
            settings["last_migration_error"] = None
            save_settings(settings)
            return MigrationResult(
                status="completed",
                old_data_dir=old_data_dir,
                new_data_dir=new_data_dir,
                message="Migration terminee. L'ancien dossier est conserve.",
            )
        except Exception as exc:
            settings = load_settings()
            settings["last_migration_status"] = "failed"
            settings["last_migration_error"] = str(exc)
            save_settings(settings)
            raise
        finally:
            self._migration_running = False

    def _copy_then_switch(self, old_data_dir: Path, new_data_dir: Path) -> None:
        old_data_dir = old_data_dir.expanduser().resolve()
        new_data_dir = new_data_dir.expanduser().resolve()
        temp_dir = new_data_dir.parent / f"{new_data_dir.name}__migrating"

        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        source_db = old_data_dir / "photohub.db"
        source_projects = old_data_dir / "projects"

        target_db = temp_dir / "photohub.db"
        target_projects = temp_dir / "projects"

        if source_db.exists():
            shutil.copy2(source_db, target_db)
        if source_projects.exists():
            shutil.copytree(source_projects, target_projects, dirs_exist_ok=True)
        else:
            target_projects.mkdir(parents=True, exist_ok=True)

        if target_db.exists():
            self._rewrite_paths_in_db(
                db_path=target_db,
                old_projects_dir=source_projects,
                new_projects_dir=new_data_dir / "projects",
            )

        self._verify_migrated_data(temp_dir)

        # Never delete an existing user directory (it may contain unrelated files,
        # e.g. source code or a virtualenv). Merge only managed payload.
        new_data_dir.mkdir(parents=True, exist_ok=True)
        merged_db = new_data_dir / "photohub.db"
        merged_projects = new_data_dir / "projects"

        if target_db.exists():
            shutil.copy2(target_db, merged_db)
        if target_projects.exists():
            shutil.copytree(target_projects, merged_projects, dirs_exist_ok=True)
        else:
            merged_projects.mkdir(parents=True, exist_ok=True)

        shutil.rmtree(temp_dir)

    @staticmethod
    def _verify_migrated_data(data_dir: Path) -> None:
        db_path = data_dir / "photohub.db"
        projects_dir = data_dir / "projects"
        if not projects_dir.exists():
            raise RuntimeError("Migration invalide: dossier projects absent.")
        if db_path.exists():
            engine = create_sqlite_engine(db_path)
            try:
                with engine.connect() as conn:
                    conn.exec_driver_sql("SELECT 1")
            finally:
                engine.dispose()

    @staticmethod
    def _rewrite_paths_in_db(db_path: Path, old_projects_dir: Path, new_projects_dir: Path) -> None:
        old_projects = old_projects_dir.resolve()
        new_projects = new_projects_dir.resolve()

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            StorageService._rewrite_column(
                cursor=cursor,
                table="projects",
                id_column="id",
                path_column="root_path",
                old_prefix=old_projects,
                new_prefix=new_projects,
            )
            StorageService._rewrite_column(
                cursor=cursor,
                table="assets",
                id_column="id",
                path_column="src_path",
                old_prefix=old_projects,
                new_prefix=new_projects,
            )
            StorageService._rewrite_column(
                cursor=cursor,
                table="imports",
                id_column="id",
                path_column="dest_path",
                old_prefix=old_projects,
                new_prefix=new_projects,
            )
            StorageService._rewrite_column(
                cursor=cursor,
                table="exports",
                id_column="id",
                path_column="output_path",
                old_prefix=old_projects,
                new_prefix=new_projects,
            )
            conn.commit()

    @staticmethod
    def _repair_paths_to_active_projects(db_path: Path, active_projects_dir: Path) -> None:
        if not db_path.exists():
            return
        active_projects = active_projects_dir.resolve()

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            StorageService._repair_column_paths(cursor, "projects", "id", "root_path", active_projects)
            StorageService._repair_column_paths(cursor, "assets", "id", "src_path", active_projects)
            StorageService._repair_column_paths(cursor, "imports", "id", "dest_path", active_projects)
            StorageService._repair_column_paths(cursor, "exports", "id", "output_path", active_projects)
            conn.commit()

    @staticmethod
    def _repair_column_paths(cursor, table: str, id_column: str, path_column: str, active_projects: Path) -> None:
        query = f"SELECT {id_column}, {path_column} FROM {table}"
        try:
            rows = cursor.execute(query).fetchall()
        except sqlite3.OperationalError:
            return

        for row_id, path_value in rows:
            if not path_value:
                continue
            raw_path = str(path_value)
            if StorageService._path_starts_with_prefix(raw_path, active_projects):
                continue

            remapped = StorageService._remap_to_active_projects(raw_path, active_projects)
            if remapped is None or remapped == raw_path:
                continue

            update_query = f"UPDATE {table} SET {path_column} = ? WHERE {id_column} = ?"
            cursor.execute(update_query, (remapped, row_id))

    @staticmethod
    def _remap_to_active_projects(path_value: str, active_projects: Path) -> str | None:
        normalized = path_value.replace("\\", "/")
        lowered = normalized.lower()
        marker = "/projects/"
        idx = lowered.find(marker)
        if idx < 0:
            return None

        tail = normalized[idx + len(marker) :].lstrip("/").lstrip("\\")
        if not tail:
            return str(active_projects)

        parts = [part for part in tail.replace("\\", "/").split("/") if part]
        candidate = active_projects
        for part in parts:
            candidate = candidate / part

        if not candidate.exists() and not candidate.parent.exists():
            return None

        use_backslash = "\\" in path_value and "/" not in path_value
        result = str(candidate)
        if use_backslash:
            return result.replace("/", "\\")
        return result.replace("\\", "/") if "/" in path_value else result

    @staticmethod
    def _path_starts_with_prefix(path_value: str, prefix: Path) -> bool:
        prefix_text = str(prefix)
        variants = {
            prefix_text,
            prefix_text.replace("\\", "/"),
            prefix_text.replace("/", "\\"),
        }
        for base in variants:
            if not base:
                continue
            if path_value.lower() == base.lower():
                return True
            if path_value.lower().startswith((base + "/").lower()):
                return True
            if path_value.lower().startswith((base + "\\").lower()):
                return True
        return False

    @staticmethod
    def _rewrite_column(
        cursor,
        table: str,
        id_column: str,
        path_column: str,
        old_prefix: Path,
        new_prefix: Path,
    ) -> None:
        query = f"SELECT {id_column}, {path_column} FROM {table}"
        try:
            rows = cursor.execute(query).fetchall()
        except sqlite3.OperationalError:
            return

        for row_id, path_value in rows:
            if not path_value:
                continue
            remapped = StorageService._remap_stored_path(
                path_value=str(path_value),
                old_prefix=old_prefix,
                new_prefix=new_prefix,
            )
            if remapped != path_value:
                update_query = f"UPDATE {table} SET {path_column} = ? WHERE {id_column} = ?"
                cursor.execute(update_query, (remapped, row_id))

    @staticmethod
    def _remap_stored_path(path_value: str, old_prefix: Path, new_prefix: Path) -> str:
        old_text = str(old_prefix)
        new_text = str(new_prefix)
        old_variants = {
            old_text,
            old_text.replace("\\", "/"),
            old_text.replace("/", "\\"),
        }

        for variant in old_variants:
            if not variant:
                continue
            if path_value.lower() == variant.lower():
                return new_text

            variant_slash = variant + "/"
            variant_backslash = variant + "\\"

            if path_value.lower().startswith(variant_slash.lower()):
                tail = path_value[len(variant_slash) :]
                return new_text.replace("\\", "/").rstrip("/") + "/" + tail
            if path_value.lower().startswith(variant_backslash.lower()):
                tail = path_value[len(variant_backslash) :]
                return new_text.replace("/", "\\").rstrip("\\") + "\\" + tail

        return path_value
