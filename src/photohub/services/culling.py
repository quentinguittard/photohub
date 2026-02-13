from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

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
    ) -> list[AssetItem]:
        safe_rating = max(0, min(int(min_rating), 5))

        with self.session_factory() as session:
            query = select(Asset).where(Asset.project_id == project_id, Asset.rating >= safe_rating)
            if rejected_mode == "kept":
                query = query.where(Asset.is_rejected.is_(False))
            elif rejected_mode == "rejected":
                query = query.where(Asset.is_rejected.is_(True))

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
