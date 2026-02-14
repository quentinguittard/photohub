from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from ..models import Asset

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional fallback
    Image = None


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-16-le", "utf-8", "latin-1"):
            try:
                return value.decode(encoding, errors="ignore").replace("\x00", "").strip()
            except Exception:
                continue
        return ""
    return str(value).strip()


def _to_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except Exception:
        return None


def _as_float(value) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, tuple) and len(value) == 2 and int(value[1]) != 0:
            return float(value[0]) / float(value[1])
        return float(value)
    except Exception:
        return None


def _format_exposure_time(value) -> str:
    numeric = _as_float(value)
    if numeric is None or numeric <= 0:
        return ""
    if numeric >= 1:
        return f"{numeric:.1f}s".replace(".0s", "s")
    denominator = round(1.0 / numeric)
    if denominator > 0:
        return f"1/{denominator}"
    return f"{numeric:.4f}s"


def _format_aperture(value) -> str:
    numeric = _as_float(value)
    if numeric is None or numeric <= 0:
        return ""
    return f"f/{numeric:.1f}".replace(".0", "")


def _format_focal(value) -> int | None:
    numeric = _as_float(value)
    if numeric is None or numeric <= 0:
        return None
    return int(round(numeric))


def _normalize_exif_datetime(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    # EXIF default format: YYYY:MM:DD HH:MM:SS
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            iso_dt = dt.strftime("%Y-%m-%dT%H:%M:%S")
            return iso_dt, iso_dt[:10]
        except Exception:
            continue
    # fallback if already ISO-like
    safe = raw.replace(" ", "T")
    if len(safe) >= 10:
        return safe, safe[:10]
    return safe, ""


def normalize_keywords(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    raw_items: list[str] = []
    if isinstance(value, str):
        text = value.replace("\n", ",").replace(";", ",").replace("|", ",")
        raw_items = [part.strip() for part in text.split(",")]
    else:
        for item in value:
            raw_items.extend(normalize_keywords(str(item)))
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        clean = str(item).strip()
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(clean)
    return out


def keywords_norm_string(keywords: list[str]) -> str:
    lowered = [item.strip().lower() for item in keywords if str(item).strip()]
    unique: list[str] = []
    seen: set[str] = set()
    for token in lowered:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    if not unique:
        return ""
    return "|" + "|".join(unique) + "|"


def extract_embedded_metadata(file_path: Path) -> dict:
    payload = {
        "exif": {
            "camera_make": "",
            "camera_model": "",
            "lens_model": "",
            "camera": "",
            "iso": None,
            "aperture": "",
            "shutter": "",
            "focal_length_mm": None,
            "datetime_original": "",
            "shot_date": "",
            "width": None,
            "height": None,
        },
        "iptc": {
            "keywords": [],
            "author": "",
            "copyright": "",
        },
    }

    if Image is None:
        return payload
    source = Path(file_path).expanduser().resolve()
    if not source.exists():
        return payload

    try:
        with Image.open(source) as img:
            try:
                payload["exif"]["width"] = int(img.width)
                payload["exif"]["height"] = int(img.height)
            except Exception:
                pass

            exif = {}
            try:
                exif = img.getexif() or {}
            except Exception:
                exif = {}

            make = _to_text(exif.get(271))
            model = _to_text(exif.get(272))
            lens = _to_text(exif.get(42036))
            artist = _to_text(exif.get(315))
            copyright_text = _to_text(exif.get(33432))
            xp_keywords = _to_text(exif.get(40094))
            iso = _to_int(exif.get(34855) or exif.get(41989))
            aperture = _format_aperture(exif.get(33437))
            shutter = _format_exposure_time(exif.get(33434))
            focal = _format_focal(exif.get(37386))
            dt_source = _to_text(exif.get(36867) or exif.get(36868) or exif.get(306))
            iso_dt, shot_date = _normalize_exif_datetime(dt_source)

            payload["exif"]["camera_make"] = make
            payload["exif"]["camera_model"] = model
            payload["exif"]["lens_model"] = lens
            payload["exif"]["camera"] = " ".join(part for part in [make, model] if part).strip()
            payload["exif"]["iso"] = iso
            payload["exif"]["aperture"] = aperture
            payload["exif"]["shutter"] = shutter
            payload["exif"]["focal_length_mm"] = focal
            payload["exif"]["datetime_original"] = iso_dt
            payload["exif"]["shot_date"] = shot_date

            keywords = normalize_keywords(xp_keywords)
            payload["iptc"]["keywords"] = keywords
            payload["iptc"]["author"] = artist
            payload["iptc"]["copyright"] = copyright_text
    except Exception:
        return payload

    return payload


def build_asset_metadata_index(metadata: dict) -> dict:
    exif = metadata.get("exif", {}) if isinstance(metadata, dict) else {}
    iptc = metadata.get("iptc", {}) if isinstance(metadata, dict) else {}
    keywords = normalize_keywords(iptc.get("keywords", []))
    author = _to_text(iptc.get("author"))
    copyright_text = _to_text(iptc.get("copyright"))

    return {
        "exif_iso": _to_int(exif.get("iso")),
        "exif_lens": _to_text(exif.get("lens_model")) or None,
        "exif_camera": _to_text(exif.get("camera")) or None,
        "exif_shot_date": _to_text(exif.get("shot_date")) or None,
        "iptc_keywords": keywords_norm_string(keywords) or None,
        "iptc_author": author or None,
        "iptc_copyright": copyright_text or None,
    }


@dataclass
class MetadataSyncResult:
    total: int
    updated: int
    status: str


class MetadataService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_asset_metadata(self, asset_id: int) -> dict:
        with self.session_factory() as session:
            asset = session.get(Asset, int(asset_id))
            if asset is None:
                raise ValueError("Asset introuvable.")
            return self._read_metadata(asset)

    def update_asset_iptc(
        self,
        asset_id: int,
        *,
        keywords: str | list[str] | None = None,
        author: str | None = None,
        copyright_text: str | None = None,
    ) -> dict:
        with self.session_factory() as session:
            asset = session.get(Asset, int(asset_id))
            if asset is None:
                raise ValueError("Asset introuvable.")
            metadata = self._read_metadata(asset)
            iptc = metadata.get("iptc", {})
            if not isinstance(iptc, dict):
                iptc = {}

            if keywords is not None:
                iptc["keywords"] = normalize_keywords(keywords)
            if author is not None:
                iptc["author"] = str(author).strip()
            if copyright_text is not None:
                iptc["copyright"] = str(copyright_text).strip()

            metadata["iptc"] = iptc
            self._write_metadata(asset, metadata)
            session.commit()
            return metadata

    def sync_iptc_to_filtered(
        self,
        *,
        project_id: int,
        source_asset_id: int,
        rejected_mode: str = "kept",
        min_rating: int = 0,
        progress_cb=None,
        is_cancelled=None,
    ) -> MetadataSyncResult:
        safe_rating = max(0, min(int(min_rating), 5))
        with self.session_factory() as session:
            source = session.get(Asset, int(source_asset_id))
            if source is None or int(source.project_id) != int(project_id):
                raise ValueError("Asset source introuvable.")
            source_md = self._read_metadata(source)
            source_iptc = source_md.get("iptc", {})
            if not isinstance(source_iptc, dict):
                source_iptc = {}
            payload = {
                "keywords": normalize_keywords(source_iptc.get("keywords", [])),
                "author": _to_text(source_iptc.get("author")),
                "copyright": _to_text(source_iptc.get("copyright")),
            }

            query = select(Asset).where(Asset.project_id == int(project_id), Asset.rating >= safe_rating)
            if rejected_mode == "kept":
                query = query.where(Asset.is_rejected.is_(False))
            elif rejected_mode == "rejected":
                query = query.where(Asset.is_rejected.is_(True))

            assets = list(session.scalars(query.order_by(Asset.id.asc())).all())
            targets = [item for item in assets if int(item.id) != int(source_asset_id)]
            total = len(targets)
            if total == 0:
                return MetadataSyncResult(total=0, updated=0, status="completed")

            updated = 0
            cancelled = False
            for idx, asset in enumerate(targets, start=1):
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    break
                md = self._read_metadata(asset)
                iptc = md.get("iptc", {})
                if not isinstance(iptc, dict):
                    iptc = {}
                iptc["keywords"] = list(payload["keywords"])
                iptc["author"] = str(payload["author"])
                iptc["copyright"] = str(payload["copyright"])
                md["iptc"] = iptc
                self._write_metadata(asset, md)
                updated += 1
                if progress_cb is not None:
                    progress_cb(idx, total, Path(asset.src_path).name)

            session.commit()
            return MetadataSyncResult(total=total, updated=updated, status="cancelled" if cancelled else "completed")

    @staticmethod
    def _read_metadata(asset: Asset) -> dict:
        try:
            payload = json.loads(asset.metadata_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if "exif" not in payload or not isinstance(payload.get("exif"), dict):
            payload["exif"] = {}
        if "iptc" not in payload or not isinstance(payload.get("iptc"), dict):
            payload["iptc"] = {"keywords": [], "author": "", "copyright": ""}
        return payload

    @staticmethod
    def _write_metadata(asset: Asset, metadata: dict) -> None:
        iptc = metadata.get("iptc", {})
        if not isinstance(iptc, dict):
            iptc = {}
            metadata["iptc"] = iptc
        iptc["keywords"] = normalize_keywords(iptc.get("keywords", []))
        iptc["author"] = _to_text(iptc.get("author"))
        iptc["copyright"] = _to_text(iptc.get("copyright"))

        index = build_asset_metadata_index(metadata)
        asset.metadata_json = json.dumps(metadata, ensure_ascii=True)
        asset.exif_iso = index["exif_iso"]
        asset.exif_lens = index["exif_lens"]
        asset.exif_camera = index["exif_camera"]
        asset.exif_shot_date = index["exif_shot_date"]
        asset.iptc_keywords = index["iptc_keywords"]
        asset.iptc_author = index["iptc_author"]
        asset.iptc_copyright = index["iptc_copyright"]
