from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from ..models import Asset, Project
from .projects import try_transition_project_status


@dataclass
class AssetItem:
    id: int
    project_id: int
    file_name: str
    src_path: str
    rating: int
    is_rejected: bool
    color_label: str | None
    exif_iso: int | None
    exif_lens: str | None
    exif_shot_date: str | None
    iptc_keywords: str | None


@dataclass
class BulkCullingResult:
    total: int
    updated: int
    status: str


class CullingService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def list_assets(
        self,
        project_id: int,
        rejected_mode: str = "all",
        min_rating: int = 0,
        iso_min: int | None = None,
        iso_max: int | None = None,
        lens_contains: str = "",
        keyword: str = "",
        shot_date_from: str | None = None,
        shot_date_to: str | None = None,
    ) -> list[AssetItem]:
        safe_rating = max(0, min(int(min_rating), 5))
        safe_iso_min = _safe_int_or_none(iso_min)
        safe_iso_max = _safe_int_or_none(iso_max)
        if safe_iso_min is not None and safe_iso_min <= 0:
            safe_iso_min = None
        if safe_iso_max is not None and safe_iso_max <= 0:
            safe_iso_max = None
        lens_query = str(lens_contains or "").strip().lower()
        keyword_token = _normalize_keyword_token(keyword)
        safe_date_from = _normalize_date_token(shot_date_from)
        safe_date_to = _normalize_date_token(shot_date_to)

        with self.session_factory() as session:
            query = select(Asset).where(Asset.project_id == project_id, Asset.rating >= safe_rating)
            if rejected_mode == "kept":
                query = query.where(Asset.is_rejected.is_(False))
            elif rejected_mode == "rejected":
                query = query.where(Asset.is_rejected.is_(True))
            if safe_iso_min is not None:
                query = query.where(Asset.exif_iso.is_not(None), Asset.exif_iso >= int(safe_iso_min))
            if safe_iso_max is not None:
                query = query.where(Asset.exif_iso.is_not(None), Asset.exif_iso <= int(safe_iso_max))
            if lens_query:
                query = query.where(func.lower(func.coalesce(Asset.exif_lens, "")).like(f"%{lens_query}%"))
            if keyword_token:
                query = query.where(func.coalesce(Asset.iptc_keywords, "").like(f"%|{keyword_token}|%"))
            if safe_date_from:
                query = query.where(Asset.exif_shot_date.is_not(None), Asset.exif_shot_date >= safe_date_from)
            if safe_date_to:
                query = query.where(Asset.exif_shot_date.is_not(None), Asset.exif_shot_date <= safe_date_to)

            assets = list(session.scalars(query.order_by(Asset.id.asc())).all())
            return [
                AssetItem(
                    id=item.id,
                    project_id=item.project_id,
                    file_name=Path(item.src_path).name,
                    src_path=item.src_path,
                    rating=item.rating,
                    is_rejected=item.is_rejected,
                    color_label=item.color_label,
                    exif_iso=item.exif_iso,
                    exif_lens=item.exif_lens,
                    exif_shot_date=item.exif_shot_date,
                    iptc_keywords=item.iptc_keywords,
                )
                for item in assets
            ]

    def update_asset(
        self,
        asset_id: int,
        rating: int | None = None,
        is_rejected: bool | None = None,
        color_label: str | None = None,
    ) -> None:
        with self.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise ValueError("Asset introuvable.")

            if rating is not None:
                safe_rating = max(0, min(int(rating), 5))
                asset.rating = safe_rating
            if is_rejected is not None:
                asset.is_rejected = bool(is_rejected)
            if color_label is not None:
                asset.color_label = color_label.strip() or None
            asset.workflow_state = "culled"

            project = session.get(Project, asset.project_id)
            if project is not None:
                try_transition_project_status(project, "en_tri")

            session.commit()

    def toggle_rejected(self, asset_id: int) -> bool:
        with self.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise ValueError("Asset introuvable.")
            asset.is_rejected = not asset.is_rejected
            asset.workflow_state = "culled"

            project = session.get(Project, asset.project_id)
            if project is not None:
                try_transition_project_status(project, "en_tri")

            session.commit()
            return asset.is_rejected

    def bulk_update_filtered(
        self,
        project_id: int,
        rejected_mode: str = "all",
        min_rating: int = 0,
        rating: int | None = None,
        is_rejected: bool | None = None,
        progress_cb=None,
        is_cancelled=None,
    ) -> BulkCullingResult:
        safe_rating = max(0, min(int(min_rating), 5))
        with self.session_factory() as session:
            query = select(Asset).where(Asset.project_id == project_id, Asset.rating >= safe_rating)
            if rejected_mode == "kept":
                query = query.where(Asset.is_rejected.is_(False))
            elif rejected_mode == "rejected":
                query = query.where(Asset.is_rejected.is_(True))
            assets = list(session.scalars(query.order_by(Asset.id.asc())).all())

            total = len(assets)
            if total == 0:
                return BulkCullingResult(total=0, updated=0, status="completed")

            updated = 0
            cancelled = False
            for index, asset in enumerate(assets, start=1):
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    break
                if rating is not None:
                    asset.rating = max(0, min(int(rating), 5))
                if is_rejected is not None:
                    asset.is_rejected = bool(is_rejected)
                asset.workflow_state = "culled"
                updated += 1
                if progress_cb is not None:
                    progress_cb(index, total, Path(asset.src_path).name)

            project = session.get(Project, project_id)
            if project is not None and updated > 0:
                try_transition_project_status(project, "en_tri")

            session.commit()
            return BulkCullingResult(
                total=total,
                updated=updated,
                status="cancelled" if cancelled else "completed",
            )


def _safe_int_or_none(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _normalize_keyword_token(value: str | None) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    token = token.replace("|", " ").replace(",", " ").replace(";", " ").strip()
    token = " ".join(part for part in token.split() if part)
    return token


def _normalize_date_token(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y:%m:%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    if len(raw) >= 10:
        maybe = raw[:10].replace("/", "-").replace(":", "-")
        try:
            dt = datetime.strptime(maybe, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    return None
