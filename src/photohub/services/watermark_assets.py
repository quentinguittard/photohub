from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def import_logo(source_path: str | Path, app_data_dir: str | Path) -> str:
    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError("Fichier logo introuvable.")

    data_dir = Path(app_data_dir).expanduser().resolve()
    target_dir = data_dir / "assets" / "watermarks"
    target_dir.mkdir(parents=True, exist_ok=True)

    ext = source.suffix.lower() or ".png"
    target_name = f"{uuid.uuid4().hex}{ext}"
    target_path = target_dir / target_name
    shutil.copy2(source, target_path)
    return f"assets/watermarks/{target_name}"


def resolve_logo_asset_path(asset_rel_path: str | None, app_data_dir: str | Path) -> Path | None:
    token = str(asset_rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not token:
        return None
    data_dir = Path(app_data_dir).expanduser().resolve()
    candidate = (data_dir / token).resolve()
    data_root = str(data_dir).lower()
    if not str(candidate).lower().startswith(data_root):
        return None
    return candidate
