from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "PhotoHub"


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    db_path: Path
    projects_dir: Path


def get_default_system_data_root() -> Path:
    if sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def settings_file_path() -> Path:
    return get_default_system_data_root() / APP_NAME / "settings.json"


def compute_app_data_dir_from_root(storage_root: str | Path) -> Path:
    root = Path(storage_root).expanduser().resolve()
    if root.name.lower() == APP_NAME.lower():
        return root
    return root / APP_NAME


def _default_settings() -> dict:
    default_app_dir = get_default_system_data_root() / APP_NAME
    return {
        "storage_root": str(default_app_dir),
        "active_data_dir": str(default_app_dir),
        "last_migration_status": "idle",
        "last_migration_error": None,
    }


def load_settings() -> dict:
    path = settings_file_path()
    defaults = _default_settings()
    if not path.exists():
        return defaults

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    if not isinstance(payload, dict):
        return defaults

    merged = {**defaults, **payload}
    merged["storage_root"] = str(Path(str(merged["storage_root"])).expanduser())
    merged["active_data_dir"] = str(Path(str(merged["active_data_dir"])).expanduser())
    if merged.get("last_migration_status") not in {"idle", "running", "failed", "completed"}:
        merged["last_migration_status"] = "idle"
    if merged.get("last_migration_error") is not None:
        merged["last_migration_error"] = str(merged["last_migration_error"])
    return merged


def save_settings(settings: dict) -> None:
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=True, indent=2), encoding="utf-8")


def resolve_app_paths() -> AppPaths:
    settings = load_settings()
    data_dir = compute_app_data_dir_from_root(settings["active_data_dir"])
    storage_root = compute_app_data_dir_from_root(settings["storage_root"])
    normalized = {
        "storage_root": str(storage_root),
        "active_data_dir": str(data_dir),
        "last_migration_status": settings.get("last_migration_status", "idle"),
        "last_migration_error": settings.get("last_migration_error"),
    }
    save_settings(normalized)

    projects_dir = data_dir / "projects"
    db_path = data_dir / "photohub.db"

    data_dir.mkdir(parents=True, exist_ok=True)
    projects_dir.mkdir(parents=True, exist_ok=True)
    return AppPaths(
        data_dir=data_dir,
        db_path=db_path,
        projects_dir=projects_dir,
    )
