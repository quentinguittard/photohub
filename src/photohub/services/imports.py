from __future__ import annotations

import json
import shutil
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..models import Asset, ImportRun, Project
from .metadata import build_asset_metadata_index, extract_embedded_metadata
from .projects import try_transition_project_status
from .presets import resolve_effective_config_for_project_model
from ..utils import copy_and_hash, iter_media_files, sanitize_filename_part, sha256_file, unique_path

# Number of concurrent I/O workers (copy + hash + metadata).
# I/O-bound operations benefit from parallelism even on a single disk.
_MAX_IO_WORKERS = 4

# Minimum interval between progress signal emissions (seconds).
# Prevents flooding the Qt event queue with thousands of cross-thread signals.
_PROGRESS_INTERVAL = 0.15

# Number of DB inserts to buffer before flushing (avoids unbounded ORM memory growth).
_FLUSH_BATCH_SIZE = 100

# Maximum number of per-file error messages stored in ImportRun.message.
_MAX_ERROR_MESSAGES = 100


@dataclass
class ImportResult:
    total: int
    copied: int
    failed: int
    destination: Path
    status: str
    message: str


def _process_file_io(
    source_file: Path,
    destination: Path,
    verify_checksum: bool,
    dual_backup: bool,
    backup_file: Path | None,
) -> dict:
    """Pure I/O for one file — thread-safe, no DB access.

    Performs: copy → hash → optional verify → optional backup → metadata.
    Cleans up the destination on any error so no orphan file is left.
    """
    try:
        if verify_checksum:
            # Stream-hash source while writing — one read pass for both copy and hash.
            src_hash, dst_hash = copy_and_hash(source_file, destination)
            if src_hash != dst_hash:
                raise RuntimeError(f"Checksum mismatch: {source_file.name}")
        else:
            shutil.copy2(source_file, destination)
            dst_hash = sha256_file(destination)
        if dual_backup and backup_file is not None:
            shutil.copy2(destination, backup_file)
        metadata_payload = extract_embedded_metadata(destination)
        metadata_index = build_asset_metadata_index(metadata_payload)
        return {
            "source_file": source_file,
            "destination": destination,
            "dst_hash": dst_hash,
            "metadata_payload": metadata_payload,
            "metadata_index": metadata_index,
        }
    except Exception:
        for path in (destination, backup_file):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise


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

            # ── Phase 0: resolve unique destination paths (must be sequential) ──
            # unique_path() must not run concurrently — it checks filesystem state
            # to avoid collisions, so sequential execution is required here.
            file_jobs: list[tuple[Path, Path, Path | None]] = []
            for seq, source_file in enumerate(files, start=1):
                target_name = self._format_name(
                    pattern=pattern,
                    project_name=project.name,
                    shoot_date=project.shoot_date.strftime("%Y%m%d"),
                    seq=seq,
                    client=project.client.name if project.client else "",
                    preset=project.preset.name if project.preset else "",
                )
                dest = unique_path(dest_root / f"{target_name}{source_file.suffix.lower()}")
                bkp = (
                    unique_path(backup_root / f"{target_name}{source_file.suffix.lower()}")
                    if dual_backup
                    else None
                )
                file_jobs.append((source_file, dest, bkp))

            copied_count = 0
            failed_count = 0
            messages: deque[str] = deque(maxlen=_MAX_ERROR_MESSAGES)
            _last_emit: float = 0.0
            total = len(file_jobs)

            def _emit(done: int, detail: str) -> None:
                """Throttled progress emission — max once per _PROGRESS_INTERVAL."""
                nonlocal _last_emit
                if progress_cb is None:
                    return
                now = time.monotonic()
                if now - _last_emit >= _PROGRESS_INTERVAL or done >= total:
                    progress_cb(done, total, detail)
                    _last_emit = now

            # ── Phase 1 (parallel I/O) + Phase 2 (sequential DB inserts) ──
            # I/O workers run concurrently; DB writes happen in this thread only.
            with ThreadPoolExecutor(max_workers=_MAX_IO_WORKERS) as pool:
                future_to_src: dict = {
                    pool.submit(
                        _process_file_io,
                        src, dst, verify_checksum, dual_backup, bkp,
                    ): src
                    for src, dst, bkp in file_jobs
                }
                done = 0
                for future in as_completed(future_to_src):
                    # Check cancellation before processing each completed result.
                    if is_cancelled is not None and is_cancelled():
                        # Cancel futures that have not started yet.
                        for f in future_to_src:
                            f.cancel()
                        break

                    src = future_to_src[future]
                    try:
                        r = future.result()
                        idx = r["metadata_index"]
                        session.add(
                            Asset(
                                project_id=project.id,
                                src_path=str(r["destination"]),
                                hash_sha256=r["dst_hash"],
                                exif_iso=idx["exif_iso"],
                                exif_lens=idx["exif_lens"],
                                exif_camera=idx["exif_camera"],
                                exif_shot_date=idx["exif_shot_date"],
                                iptc_keywords=idx["iptc_keywords"],
                                iptc_author=idx["iptc_author"],
                                iptc_copyright=idx["iptc_copyright"],
                                metadata_json=json.dumps(
                                    r["metadata_payload"], ensure_ascii=True
                                ),
                            )
                        )
                        copied_count += 1
                        # Flush every 100 inserts to avoid unbounded memory growth.
                        if copied_count % _FLUSH_BATCH_SIZE == 0:
                            session.flush()
                    except Exception as exc:
                        failed_count += 1
                        messages.append(f"{src.name}: {exc}")

                    done += 1
                    _emit(done, src.name)

            run.file_count = total
            run.copied_count = copied_count
            run.failed_count = failed_count
            run.ended_at = datetime.utcnow()
            cancelled = bool(is_cancelled is not None and is_cancelled())
            if cancelled:
                run.status = "cancelled"
            else:
                run.status = "completed" if failed_count == 0 else "completed_with_errors"
            run.message = "\n".join(messages)

            if copied_count > 0:
                try_transition_project_status(project, "importe")
            session.commit()

            return ImportResult(
                total=total,
                copied=copied_count,
                failed=failed_count,
                destination=dest_root,
                status=run.status,
                message=run.message,
            )

    @staticmethod
    def _format_name(
        pattern: str,
        project_name: str,
        shoot_date: str,
        seq: int,
        client: str = "",
        preset: str = "",
    ) -> str:
        safe_project = sanitize_filename_part(project_name) or "project"
        safe_client  = sanitize_filename_part(client)
        safe_preset  = sanitize_filename_part(preset)
        try:
            name = pattern.format(
                project=safe_project, date=shoot_date, seq=seq,
                client=safe_client, preset=safe_preset,
            )
        except Exception:
            name = f"{safe_project}_{shoot_date}_{seq:04d}"
        while "__" in name:
            name = name.replace("__", "_")
        return name.strip("_") or f"{safe_project}_{shoot_date}_{seq:04d}"
