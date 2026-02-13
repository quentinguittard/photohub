from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ..models import Asset, Project
from .projects import try_transition_project_status


DEFAULT_EDIT_SETTINGS: dict[str, object] = {
    "exposure": 0.0,
    "wb_temp": 5500,
    "wb_tint": 0,
    "crop_ratio": "original",
    "straighten": 0.0,
    "contrast": 0,
    "highlights": 0,
    "shadows": 0,
    "vibrance": 0,
    "saturation": 0,
    "clarity": 0,
}

_CROP_RATIOS = {"original", "1:1", "4:5", "3:2", "16:9"}


@dataclass
class EditAssetItem:
    id: int
    project_id: int
    file_name: str
    src_path: str
    rating: int
    is_rejected: bool
    edit_settings: dict[str, object]


@dataclass
class BulkSyncResult:
    total: int
    updated: int
    status: str


class EditService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def list_assets(
        self,
        project_id: int,
        rejected_mode: str = "kept",
        min_rating: int = 0,
    ) -> list[EditAssetItem]:
        safe_rating = max(0, min(int(min_rating), 5))
        with self.session_factory() as session:
            query = select(Asset).where(Asset.project_id == int(project_id), Asset.rating >= safe_rating)
            if rejected_mode == "kept":
                query = query.where(Asset.is_rejected.is_(False))
            elif rejected_mode == "rejected":
                query = query.where(Asset.is_rejected.is_(True))

            assets = list(session.scalars(query.order_by(Asset.id.asc())).all())
            return [
                EditAssetItem(
                    id=item.id,
                    project_id=item.project_id,
                    file_name=Path(item.src_path).name,
                    src_path=item.src_path,
                    rating=int(item.rating),
                    is_rejected=bool(item.is_rejected),
                    edit_settings=self._read_edit_settings(item),
                )
                for item in assets
            ]

    def get_asset_edit_settings(self, asset_id: int) -> dict[str, object]:
        with self.session_factory() as session:
            asset = session.get(Asset, int(asset_id))
            if asset is None:
                raise ValueError("Asset introuvable.")
            return self._read_edit_settings(asset)

    def update_asset_edit_settings(
        self,
        asset_id: int,
        updates: dict[str, object],
        *,
        replace: bool = False,
    ) -> dict[str, object]:
        with self.session_factory() as session:
            asset = session.get(Asset, int(asset_id))
            if asset is None:
                raise ValueError("Asset introuvable.")

            current = self._read_edit_settings(asset)
            payload = self._normalize_edit_settings(updates if replace else {**current, **updates})
            metadata = self._read_metadata(asset)
            metadata["edit"] = payload
            asset.metadata_json = json.dumps(metadata, ensure_ascii=True)

            project = session.get(Project, asset.project_id)
            if project is not None:
                try_transition_project_status(project, "en_tri")

            session.commit()
            return payload

    def copy_edit_settings(self, source_asset_id: int, target_asset_id: int) -> dict[str, object]:
        if int(source_asset_id) == int(target_asset_id):
            return self.get_asset_edit_settings(target_asset_id)
        source_payload = self.get_asset_edit_settings(source_asset_id)
        return self.update_asset_edit_settings(target_asset_id, source_payload, replace=True)

    def sync_edit_settings_to_filtered(
        self,
        project_id: int,
        source_asset_id: int,
        *,
        rejected_mode: str = "kept",
        min_rating: int = 0,
        progress_cb=None,
        is_cancelled=None,
    ) -> BulkSyncResult:
        source_settings = self.get_asset_edit_settings(source_asset_id)
        safe_rating = max(0, min(int(min_rating), 5))

        with self.session_factory() as session:
            query = select(Asset).where(Asset.project_id == int(project_id), Asset.rating >= safe_rating)
            if rejected_mode == "kept":
                query = query.where(Asset.is_rejected.is_(False))
            elif rejected_mode == "rejected":
                query = query.where(Asset.is_rejected.is_(True))

            assets = list(session.scalars(query.order_by(Asset.id.asc())).all())
            targets = [asset for asset in assets if int(asset.id) != int(source_asset_id)]
            total = len(targets)
            if total == 0:
                return BulkSyncResult(total=0, updated=0, status="completed")

            updated = 0
            cancelled = False
            for index, asset in enumerate(targets, start=1):
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    break

                metadata = self._read_metadata(asset)
                metadata["edit"] = dict(source_settings)
                asset.metadata_json = json.dumps(metadata, ensure_ascii=True)
                updated += 1

                if progress_cb is not None:
                    progress_cb(index, total, Path(asset.src_path).name)

            project = session.get(Project, int(project_id))
            if project is not None and updated > 0:
                try_transition_project_status(project, "en_tri")

            session.commit()
            return BulkSyncResult(
                total=total,
                updated=updated,
                status="cancelled" if cancelled else "completed",
            )

    def reset_asset_edit_settings(self, asset_id: int) -> dict[str, object]:
        return self.update_asset_edit_settings(asset_id, dict(DEFAULT_EDIT_SETTINGS), replace=True)

    @staticmethod
    def _read_metadata(asset: Asset) -> dict:
        try:
            payload = json.loads(asset.metadata_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return payload

    @staticmethod
    def _normalize_edit_settings(payload: dict[str, object]) -> dict[str, object]:
        merged = dict(DEFAULT_EDIT_SETTINGS)
        merged.update(payload or {})

        def as_float(key: str, min_value: float, max_value: float) -> float:
            try:
                value = float(merged.get(key, DEFAULT_EDIT_SETTINGS[key]))
            except Exception:
                value = float(DEFAULT_EDIT_SETTINGS[key])
            return max(min_value, min(max_value, value))

        def as_int(key: str, min_value: int, max_value: int) -> int:
            try:
                value = int(float(merged.get(key, DEFAULT_EDIT_SETTINGS[key])))
            except Exception:
                value = int(DEFAULT_EDIT_SETTINGS[key])
            return max(min_value, min(max_value, value))

        crop_ratio = str(merged.get("crop_ratio", "original")).strip().lower()
        if crop_ratio not in {item.lower() for item in _CROP_RATIOS}:
            crop_ratio = "original"
        if crop_ratio == "original":
            normalized_crop = "original"
        else:
            normalized_crop = next(
                (item for item in _CROP_RATIOS if item.lower() == crop_ratio),
                "original",
            )

        return {
            "exposure": round(as_float("exposure", -5.0, 5.0), 2),
            "wb_temp": as_int("wb_temp", 2000, 12000),
            "wb_tint": as_int("wb_tint", -100, 100),
            "crop_ratio": normalized_crop,
            "straighten": round(as_float("straighten", -45.0, 45.0), 2),
            "contrast": as_int("contrast", -100, 100),
            "highlights": as_int("highlights", -100, 100),
            "shadows": as_int("shadows", -100, 100),
            "vibrance": as_int("vibrance", -100, 100),
            "saturation": as_int("saturation", -100, 100),
            "clarity": as_int("clarity", -100, 100),
        }

    def _read_edit_settings(self, asset: Asset) -> dict[str, object]:
        metadata = self._read_metadata(asset)
        payload = metadata.get("edit", {})
        if not isinstance(payload, dict):
            payload = {}
        return self._normalize_edit_settings(payload)
