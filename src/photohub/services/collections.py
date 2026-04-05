from __future__ import annotations

from sqlalchemy import func, select

from ..models import Collection, CollectionAsset, Asset


class CollectionService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def create_collection(self, *, project_id: int, name: str, description: str = "") -> Collection:
        name = str(name or "").strip()
        if not name:
            raise ValueError("Le nom de la collection est requis.")
        with self.session_factory() as session:
            existing = session.scalars(
                select(Collection).where(
                    Collection.project_id == int(project_id),
                    Collection.name == name,
                )
            ).first()
            if existing is not None:
                raise ValueError(f"Une collection nommée '{name}' existe déjà.")
            col = Collection(
                project_id=int(project_id),
                name=name,
                description=str(description or ""),
            )
            session.add(col)
            session.commit()
            session.refresh(col)
            return col

    def list_collections(self, *, project_id: int) -> list[tuple[Collection, int]]:
        """Return [(collection, asset_count), ...] ordered by creation date."""
        with self.session_factory() as session:
            counts = dict(
                session.execute(
                    select(CollectionAsset.collection_id, func.count())
                    .join(Collection, Collection.id == CollectionAsset.collection_id)
                    .where(Collection.project_id == int(project_id))
                    .group_by(CollectionAsset.collection_id)
                ).all()
            )
            cols = list(
                session.scalars(
                    select(Collection)
                    .where(Collection.project_id == int(project_id))
                    .order_by(Collection.created_at.asc())
                ).all()
            )
            session.expunge_all()
            return [(col, counts.get(int(col.id), 0)) for col in cols]

    def rename_collection(self, *, collection_id: int, name: str) -> Collection:
        name = str(name or "").strip()
        if not name:
            raise ValueError("Le nom de la collection est requis.")
        with self.session_factory() as session:
            col = session.get(Collection, int(collection_id))
            if col is None:
                raise ValueError("Collection introuvable.")
            conflict = session.scalars(
                select(Collection).where(
                    Collection.project_id == col.project_id,
                    Collection.name == name,
                    Collection.id != col.id,
                )
            ).first()
            if conflict is not None:
                raise ValueError(f"Une collection nommée '{name}' existe déjà.")
            col.name = name
            session.commit()
            session.refresh(col)
            return col

    def delete_collection(self, *, collection_id: int) -> None:
        with self.session_factory() as session:
            col = session.get(Collection, int(collection_id))
            if col is None:
                return
            session.delete(col)
            session.commit()

    def add_assets(self, *, collection_id: int, asset_ids: list[int]) -> int:
        if not asset_ids:
            return 0
        with self.session_factory() as session:
            col = session.get(Collection, int(collection_id))
            if col is None:
                raise ValueError("Collection introuvable.")
            existing = {
                int(row.asset_id)
                for row in session.scalars(
                    select(CollectionAsset).where(
                        CollectionAsset.collection_id == int(collection_id)
                    )
                ).all()
            }
            added = 0
            for asset_id in asset_ids:
                if int(asset_id) in existing:
                    continue
                asset = session.get(Asset, int(asset_id))
                if asset is None or int(asset.project_id) != int(col.project_id):
                    continue
                session.add(CollectionAsset(collection_id=int(collection_id), asset_id=int(asset_id)))
                existing.add(int(asset_id))
                added += 1
            session.commit()
            return added

    def remove_assets(self, *, collection_id: int, asset_ids: list[int]) -> int:
        if not asset_ids:
            return 0
        ids = {int(a) for a in asset_ids}
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(CollectionAsset).where(
                        CollectionAsset.collection_id == int(collection_id),
                        CollectionAsset.asset_id.in_(ids),
                    )
                ).all()
            )
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)

    def get_asset_ids(self, *, collection_id: int) -> list[int]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(CollectionAsset.asset_id).where(
                    CollectionAsset.collection_id == int(collection_id)
                )
            ).all()
            return [int(r) for r in rows]
