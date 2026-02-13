from __future__ import annotations

import hashlib
import re
from pathlib import Path


MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".heic",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
}


def slugify(value: str) -> str:
    raw = value.strip().lower()
    raw = re.sub(r"[^\w\-]+", "_", raw, flags=re.ASCII)
    raw = re.sub(r"_+", "_", raw)
    return raw.strip("_") or "project"


def iter_media_files(source_dir: Path):
    for path in sorted(source_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
            yield path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    idx = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
        idx += 1
