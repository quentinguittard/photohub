from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from ..models import Asset, Project


@dataclass(frozen=True)
class RenamePreviewItem:
    asset_id: int
    source_path: str
    target_path: str


@dataclass(frozen=True)
class BatchRenameResult:
    total: int
    renamed: int
    skipped: int
    failed: int
    status: str
    message: str


class _OperationCancelled(Exception):
    pass


class RenameService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def preview_batch_rename(
        self,
        *,
        project_id: int,
        asset_ids: list[int],
        pattern: str,
        start_seq: int = 1,
    ) -> list[RenamePreviewItem]:
        with self.session_factory() as session:
            project, assets = self._load_project_assets(
                session=session,
                project_id=int(project_id),
                asset_ids=list(asset_ids),
            )
            plan = self._build_plan(
                project=project,
                assets=assets,
                pattern=str(pattern),
                start_seq=max(1, int(start_seq)),
            )
            return [
                RenamePreviewItem(
                    asset_id=int(entry["asset"].id),
                    source_path=str(entry["source"]),
                    target_path=str(entry["target"]),
                )
                for entry in plan
            ]

    def run_batch_rename(
        self,
        *,
        project_id: int,
        asset_ids: list[int],
        pattern: str,
        start_seq: int = 1,
        progress_cb=None,
        is_cancelled=None,
    ) -> BatchRenameResult:
        with self.session_factory() as session:
            project, assets = self._load_project_assets(
                session=session,
                project_id=int(project_id),
                asset_ids=list(asset_ids),
            )
            plan = self._build_plan(
                project=project,
                assets=assets,
                pattern=str(pattern),
                start_seq=max(1, int(start_seq)),
            )

            total = len(plan)
            if total == 0:
                return BatchRenameResult(
                    total=0,
                    renamed=0,
                    skipped=0,
                    failed=0,
                    status="completed",
                    message="Aucun asset selectionne.",
                )

            unchanged = [entry for entry in plan if self._path_key(entry["source"]) == self._path_key(entry["target"])]
            changed = [entry for entry in plan if self._path_key(entry["source"]) != self._path_key(entry["target"])]
            skipped = len(unchanged)

            if not changed:
                return BatchRenameResult(
                    total=total,
                    renamed=0,
                    skipped=skipped,
                    failed=0,
                    status="completed",
                    message="Aucun renommage necessaire.",
                )

            for entry in changed:
                source = Path(entry["source"])
                if not source.exists():
                    raise ValueError(f"Fichier introuvable: {source}")

            tmp_map: dict[int, Path] = {}
            renamed_count = 0

            try:
                stage1_total = len(changed)
                progress_total = max(1, stage1_total * 2)
                for idx, entry in enumerate(changed, start=1):
                    if is_cancelled is not None and is_cancelled():
                        raise _OperationCancelled()
                    source = Path(entry["source"])
                    tmp_path = self._allocate_temp_path(source, idx=idx)
                    source.rename(tmp_path)
                    tmp_map[int(entry["asset"].id)] = tmp_path
                    if progress_cb is not None:
                        progress_cb(idx, progress_total, f"tmp {source.name}")

                for idx, entry in enumerate(changed, start=1):
                    if is_cancelled is not None and is_cancelled():
                        raise _OperationCancelled()
                    asset = entry["asset"]
                    target = Path(entry["target"])
                    tmp_path = tmp_map[int(asset.id)]
                    if target.exists():
                        raise RuntimeError(f"Conflit destination: {target.name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path.rename(target)
                    asset.src_path = str(target)
                    renamed_count += 1
                    if progress_cb is not None:
                        progress_cb(stage1_total + idx, progress_total, target.name)

                session.commit()
                return BatchRenameResult(
                    total=total,
                    renamed=renamed_count,
                    skipped=skipped,
                    failed=0,
                    status="completed",
                    message=f"Renommage termine: {renamed_count} fichier(s).",
                )

            except _OperationCancelled:
                session.rollback()
                self._restore_original_paths(changed=changed, tmp_map=tmp_map)
                return BatchRenameResult(
                    total=total,
                    renamed=0,
                    skipped=skipped,
                    failed=len(changed),
                    status="cancelled",
                    message="Renommage annule.",
                )
            except Exception as exc:
                session.rollback()
                self._restore_original_paths(changed=changed, tmp_map=tmp_map)
                return BatchRenameResult(
                    total=total,
                    renamed=0,
                    skipped=skipped,
                    failed=len(changed),
                    status="failed",
                    message=str(exc),
                )

    def _load_project_assets(self, *, session, project_id: int, asset_ids: list[int]) -> tuple[Project, list[Asset]]:
        ids: list[int] = []
        seen: set[int] = set()
        for raw_id in asset_ids:
            try:
                value = int(raw_id)
            except Exception:
                continue
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            ids.append(value)

        if not ids:
            raise ValueError("Aucun asset selectionne.")

        project = session.get(Project, int(project_id))
        if project is None:
            raise ValueError("Projet introuvable.")

        rows = list(
            session.scalars(
                select(Asset).where(
                    Asset.project_id == int(project_id),
                    Asset.id.in_(ids),
                )
            ).all()
        )
        by_id = {int(item.id): item for item in rows}
        ordered: list[Asset] = []
        missing: list[int] = []
        for asset_id in ids:
            item = by_id.get(int(asset_id))
            if item is None:
                missing.append(int(asset_id))
            else:
                ordered.append(item)
        if missing:
            raise ValueError(f"Assets introuvables: {', '.join(str(value) for value in missing[:10])}")
        return project, ordered

    def _build_plan(self, *, project: Project, assets: list[Asset], pattern: str, start_seq: int) -> list[dict]:
        safe_project = self._sanitize_stem(str(project.name))
        shoot_date = project.shoot_date.strftime("%Y%m%d")
        clean_pattern = str(pattern or "").strip() or "{project}_{date}_{seq:04d}"

        source_keys = {
            self._path_key(Path(item.src_path).expanduser().resolve())
            for item in assets
        }
        taken: set[str] = set()
        plan: list[dict] = []

        seq = int(start_seq)
        for asset in assets:
            source = Path(asset.src_path).expanduser().resolve()
            parent = source.parent
            safe_orig = self._sanitize_stem(source.stem)
            stem = self._format_target_stem(
                pattern=clean_pattern,
                project=safe_project,
                shoot_date=shoot_date,
                seq=seq,
                orig=safe_orig,
            )
            seq += 1
            ext = source.suffix.lower()
            candidate = parent / f"{stem}{ext}"
            target = self._resolve_unique_target(
                candidate=candidate,
                source_keys=source_keys,
                taken_keys=taken,
            )
            taken.add(self._path_key(target))
            plan.append(
                {
                    "asset": asset,
                    "source": source,
                    "target": target,
                }
            )
        return plan

    @staticmethod
    def _format_target_stem(*, pattern: str, project: str, shoot_date: str, seq: int, orig: str) -> str:
        try:
            raw = str(pattern).format(project=project, date=shoot_date, seq=int(seq), orig=orig)
        except Exception:
            raw = f"{project}_{shoot_date}_{int(seq):04d}"
        cleaned = RenameService._sanitize_stem(raw)
        return cleaned or f"{project}_{shoot_date}_{int(seq):04d}"

    @staticmethod
    def _sanitize_stem(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        forbidden = '<>:"/\\|?*'
        safe_chars: list[str] = []
        for char in raw:
            if char in forbidden or ord(char) < 32:
                safe_chars.append("_")
                continue
            if char.isalnum():
                safe_chars.append(char)
            else:
                safe_chars.append("_")
        cleaned = "".join(safe_chars).strip(" ._")
        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")
        return cleaned or "image"

    @staticmethod
    def _path_key(path: Path) -> str:
        raw = str(Path(path).expanduser().resolve())
        if os.name == "nt":
            return raw.lower()
        return raw

    def _resolve_unique_target(self, *, candidate: Path, source_keys: set[str], taken_keys: set[str]) -> Path:
        safe = Path(candidate)
        index = 1
        while True:
            key = self._path_key(safe)
            exists_conflict = safe.exists() and key not in source_keys
            if key not in taken_keys and not exists_conflict:
                return safe
            safe = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
            index += 1

    @staticmethod
    def _allocate_temp_path(source: Path, *, idx: int) -> Path:
        token = uuid4().hex[:8]
        parent = source.parent
        suffix = source.suffix
        candidate = parent / f".ph_tmp_{token}_{idx}{suffix}"
        while candidate.exists():
            token = uuid4().hex[:8]
            candidate = parent / f".ph_tmp_{token}_{idx}{suffix}"
        return candidate

    @staticmethod
    def _restore_original_paths(*, changed: list[dict], tmp_map: dict[int, Path]) -> None:
        for entry in reversed(changed):
            asset = entry["asset"]
            source = Path(entry["source"])
            target = Path(entry["target"])
            tmp_path = tmp_map.get(int(asset.id))
            try:
                if source.exists():
                    continue
                if target.exists():
                    target.rename(source)
                    continue
                if tmp_path is not None and tmp_path.exists():
                    tmp_path.rename(source)
            except Exception:
                # Best effort rollback only.
                pass
