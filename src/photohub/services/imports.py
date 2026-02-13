from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..models import Asset, ImportRun, Project
from .projects import try_transition_project_status
from .presets import resolve_effective_config_for_project_model
from ..utils import iter_media_files, sha256_file, unique_path


@dataclass
class ImportResult:
    total: int
    copied: int
    failed: int
    destination: Path
    status: str
    message: str


class ImportService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def run_import(
        self,
        project_id: int,
        source_dir: str | Path,
        progress_cb=None,
        is_cancelled=None,
    ) -> ImportResult:
        source = Path(source_dir)
        if not source.exists() or not source.is_dir():
            raise ValueError("Le dossier source est invalide.")

        files = list(iter_media_files(source))
        if not files:
            raise ValueError("Aucun fichier image/RAW detecte dans le dossier source.")

        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError("Projet introuvable.")

            config = resolve_effective_config_for_project_model(session, project)

            verify_checksum = bool(config.get("import", {}).get("verify_checksum", True))
            dual_backup = bool(config.get("import", {}).get("dual_backup", False))
            backup_path = str(config.get("import", {}).get("backup_path", "")).strip()
            pattern = str(config.get("naming", {}).get("pattern", "{project}_{date}_{seq:04d}")).strip()

            dest_root = Path(project.root_path) / "raw"
            dest_root.mkdir(parents=True, exist_ok=True)

            backup_root = Path(backup_path) if backup_path else Path(project.root_path) / "backup"
            if dual_backup:
                backup_root.mkdir(parents=True, exist_ok=True)

            run = ImportRun(
                project_id=project.id,
                source_path=str(source),
                dest_path=str(dest_root),
                status="running",
                started_at=datetime.utcnow(),
            )
            session.add(run)
            session.flush()

            copied_count = 0
            failed_count = 0
            messages: list[str] = []

            for seq, source_file in enumerate(files, start=1):
                if is_cancelled is not None and is_cancelled():
                    break
                try:
                    target_name = self._format_name(
                        pattern=pattern,
                        project_name=project.name,
                        shoot_date=project.shoot_date.strftime("%Y%m%d"),
                        seq=seq,
                    )
                    destination = unique_path(dest_root / f"{target_name}{source_file.suffix.lower()}")

                    shutil.copy2(source_file, destination)

                    source_hash = sha256_file(source_file)
                    destination_hash = sha256_file(destination)
                    if verify_checksum and source_hash != destination_hash:
                        raise RuntimeError(f"Checksum mismatch: {source_file.name}")

                    if dual_backup:
                        backup_file = unique_path(backup_root / f"{target_name}{source_file.suffix.lower()}")
                        shutil.copy2(destination, backup_file)

                    session.add(
                        Asset(
                            project_id=project.id,
                            src_path=str(destination),
                            hash_sha256=destination_hash,
                            metadata_json="{}",
                        )
                    )
                    copied_count += 1
                except Exception as exc:
                    failed_count += 1
                    messages.append(f"{source_file.name}: {exc}")
                finally:
                    if progress_cb is not None:
                        progress_cb(copied_count + failed_count, len(files), source_file.name)

            run.file_count = len(files)
            run.copied_count = copied_count
            run.failed_count = failed_count
            run.ended_at = datetime.utcnow()
            cancelled = bool(is_cancelled is not None and is_cancelled())
            if cancelled:
                run.status = "cancelled"
            else:
                run.status = "completed" if failed_count == 0 else "completed_with_errors"
            run.message = "\n".join(messages[:100])

            if copied_count > 0:
                try_transition_project_status(project, "importe")
            session.commit()

            return ImportResult(
                total=len(files),
                copied=copied_count,
                failed=failed_count,
                destination=dest_root,
                status=run.status,
                message=run.message,
            )

    @staticmethod
    def _format_name(pattern: str, project_name: str, shoot_date: str, seq: int) -> str:
        safe_project = "".join(c if c.isalnum() else "_" for c in project_name).strip("_") or "project"
        try:
            name = pattern.format(project=safe_project, date=shoot_date, seq=seq)
        except Exception:
            name = f"{safe_project}_{shoot_date}_{seq:04d}"
        return name.strip() or f"{safe_project}_{shoot_date}_{seq:04d}"
