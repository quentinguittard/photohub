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


def sanitize_filename_part(value: str) -> str:
    """Replace non-alphanumeric/control/forbidden chars with underscores.

    Collapses consecutive underscores and strips leading/trailing ones.
    Returns an empty string for blank input — caller chooses the fallback.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    forbidden = '<>:"/\\|?*'
    chars: list[str] = []
    for char in raw:
        if char in forbidden or ord(char) < 32:
            chars.append("_")
        elif char.isalnum():
            chars.append(char)
        else:
            chars.append("_")
    cleaned = "".join(chars).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


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
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_and_hash(src: Path, dst: Path) -> tuple[str, str]:
    """Copy *src* to *dst* and return (src_sha256, dst_sha256) in one pass.

    The source is read exactly once; SHA-256 is computed as bytes flow through
    to the destination.  This avoids a redundant full re-read of the source when
    the caller needs to verify that the copy succeeded.
    """
    src_digest = hashlib.sha256()
    dst_digest = hashlib.sha256()
    chunk_size = 4 * 1024 * 1024
    with src.open("rb") as src_fh, dst.open("wb") as dst_fh:
        while True:
            chunk = src_fh.read(chunk_size)
            if not chunk:
                break
            src_digest.update(chunk)
            dst_fh.write(chunk)
            dst_digest.update(chunk)
    # Preserve original timestamps so metadata tools see the source mtime.
    import shutil as _shutil
    _shutil.copystat(src, dst)
    return src_digest.hexdigest(), dst_digest.hexdigest()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    idx = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
        idx += 1
