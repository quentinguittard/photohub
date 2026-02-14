from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import shutil

try:
    from PIL import Image

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency fallback
    PIL_AVAILABLE = False
    Image = None


class DiskImageCache:
    def __init__(
        self,
        root: Path,
        *,
        max_cache_bytes: int = 2 * 1024 * 1024 * 1024,
        min_free_bytes: int = 0,
    ):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "preview").mkdir(parents=True, exist_ok=True)
        (self.root / "thumb").mkdir(parents=True, exist_ok=True)
        self.max_cache_bytes = max(8 * 1024 * 1024, int(max_cache_bytes))
        self.min_free_bytes = max(0, int(min_free_bytes))
        self._db_path = self.root / "index.sqlite3"
        self._lock = threading.Lock()
        self._ensure_schema()

    def get_existing_cached_path(self, src_path: Path, *, kind: str, width: int, height: int) -> Path | None:
        src = Path(src_path).expanduser().resolve()
        if not src.exists():
            return None
        key = self._build_key(src, kind=kind, width=width, height=height)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT rel_path FROM image_cache_entries WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    return None
                target = self.root / str(row[0])
                if not target.exists():
                    conn.execute("DELETE FROM image_cache_entries WHERE cache_key = ?", (key,))
                    conn.commit()
                    return None
                conn.execute(
                    "UPDATE image_cache_entries SET last_access_utc = ? WHERE cache_key = ?",
                    (self._utc_now_text(), key),
                )
                conn.commit()
                return target
            finally:
                conn.close()

    def get_or_create_cached_path(self, src_path: Path, *, kind: str, width: int, height: int) -> Path | None:
        existing = self.get_existing_cached_path(src_path, kind=kind, width=width, height=height)
        if existing is not None:
            return existing
        created = self._create_entry(Path(src_path), kind=kind, width=width, height=height)
        if created is not None:
            self._prune_if_needed()
        return created

    def _create_entry(self, src_path: Path, *, kind: str, width: int, height: int) -> Path | None:
        src = Path(src_path).expanduser().resolve()
        if not src.exists():
            return None
        if not PIL_AVAILABLE:
            return None
        key = self._build_key(src, kind=kind, width=width, height=height)
        target_rel = Path(kind) / f"{key}.jpg"
        target = self.root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            with Image.open(src) as img:
                image = img.convert("RGB")
                image.thumbnail((max(16, int(width)), max(16, int(height))), Image.Resampling.LANCZOS)
                image.save(target, format="JPEG", quality=88, optimize=True)
        except Exception:
            return None
        if not target.exists():
            return None

        src_stat = src.stat()
        size_bytes = int(target.stat().st_size)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO image_cache_entries (
                        cache_key, kind, src_path, src_mtime_ns, src_size_bytes, rel_path, bytes_size,
                        created_utc, last_access_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        rel_path=excluded.rel_path,
                        bytes_size=excluded.bytes_size,
                        last_access_utc=excluded.last_access_utc
                    """,
                    (
                        key,
                        kind,
                        str(src),
                        int(src_stat.st_mtime_ns),
                        int(src_stat.st_size),
                        str(target_rel).replace("\\", "/"),
                        size_bytes,
                        self._utc_now_text(),
                        self._utc_now_text(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return target

    def _prune_if_needed(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                total_row = conn.execute("SELECT COALESCE(SUM(bytes_size), 0) FROM image_cache_entries").fetchone()
                total_size = int(total_row[0] if total_row else 0)
                disk = shutil.disk_usage(self.root)
                # Keep cache below 15%% of disk and under configured max.
                hard_cap = max(1, min(self.max_cache_bytes, int(disk.total * 0.15)))
                needs_size_prune = total_size > hard_cap
                needs_free_prune = self.min_free_bytes > 0 and disk.free < self.min_free_bytes
                if not needs_size_prune and not needs_free_prune:
                    return
                target_size = int(hard_cap * 0.7) if needs_size_prune else total_size
                rows = conn.execute(
                    """
                    SELECT cache_key, rel_path, bytes_size
                      FROM image_cache_entries
                     ORDER BY last_access_utc ASC, created_utc ASC
                    """
                ).fetchall()
                removed = 0
                for cache_key, rel_path, bytes_size in rows:
                    current_free = shutil.disk_usage(self.root).free
                    if total_size <= target_size and (
                        self.min_free_bytes <= 0 or current_free >= self.min_free_bytes
                    ):
                        break
                    file_path = self.root / str(rel_path)
                    try:
                        if file_path.exists():
                            file_path.unlink()
                    except Exception:
                        pass
                    conn.execute("DELETE FROM image_cache_entries WHERE cache_key = ?", (str(cache_key),))
                    removed += int(bytes_size or 0)
                    total_size = max(0, total_size - int(bytes_size or 0))
                if removed > 0:
                    conn.commit()
            finally:
                conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS image_cache_entries (
                        cache_key TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        src_path TEXT NOT NULL,
                        src_mtime_ns INTEGER NOT NULL,
                        src_size_bytes INTEGER NOT NULL,
                        rel_path TEXT NOT NULL,
                        bytes_size INTEGER NOT NULL,
                        created_utc TEXT NOT NULL,
                        last_access_utc TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_image_cache_last_access ON image_cache_entries(last_access_utc)"
                )
                conn.commit()
            finally:
                conn.close()

    def _build_key(self, src: Path, *, kind: str, width: int, height: int) -> str:
        stat = src.stat()
        raw = f"{src}|{int(stat.st_mtime_ns)}|{int(stat.st_size)}|{kind}|{int(width)}x{int(height)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _connect(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @staticmethod
    def _utc_now_text() -> str:
        return datetime.utcnow().isoformat(timespec="seconds")


class PreviewPrefetchManager:
    def __init__(
        self,
        cache_root: Path,
        *,
        depth: int = 3,
        max_prev_keep: int = 1,
        max_warm_entries: int = 8,
        worker_count: int = 2,
        preview_width: int = 2200,
        preview_height: int = 2200,
    ):
        self.depth = max(1, int(depth))
        self.max_prev_keep = max(0, int(max_prev_keep))
        self.max_warm_entries = max(2, int(max_warm_entries))
        self.preview_width = max(320, int(preview_width))
        self.preview_height = max(320, int(preview_height))
        self.cache = DiskImageCache(cache_root)
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(worker_count)), thread_name_prefix="ph-prefetch")
        self._lock = threading.Lock()
        self._sequence: list[Path] = []
        self._last_index: int | None = None
        self._futures: dict[str, Future] = {}
        self._warm_preview_bytes: dict[str, bytes] = {}
        self._warm_order: list[str] = []

    def update_sequence(self, paths: list[str]) -> None:
        with self._lock:
            self._sequence = [Path(str(path)).expanduser().resolve() for path in paths]
            if not self._sequence:
                self._last_index = None
                self._warm_preview_bytes.clear()
                self._warm_order.clear()

    def on_selected_index(self, index: int) -> None:
        with self._lock:
            sequence = list(self._sequence)
            prev_index = self._last_index
            self._last_index = int(index)
        if not sequence or index < 0 or index >= len(sequence):
            return

        direction = 1
        if prev_index is not None and int(index) < int(prev_index):
            direction = -1

        keep_indices: set[int] = set()
        for offset in range(-self.max_prev_keep, self.depth + 1):
            idx = int(index) + offset
            if 0 <= idx < len(sequence):
                keep_indices.add(idx)
        self._prune_warm_memory({str(sequence[idx]) for idx in keep_indices})

        prefetch_indices = []
        if direction >= 0:
            prefetch_indices = [index + step for step in range(1, self.depth + 1)]
        else:
            prefetch_indices = [index - step for step in range(1, self.depth + 1)]
        for idx in prefetch_indices:
            if idx < 0 or idx >= len(sequence):
                continue
            self._schedule_prefetch(sequence[idx])

    def get_warmed_preview_bytes(self, src_path: Path) -> bytes | None:
        key = str(Path(src_path).expanduser().resolve())
        with self._lock:
            data = self._warm_preview_bytes.get(key)
            if data is not None:
                self._touch_warm_key(key)
                return data
        return None

    def get_cached_preview_path(self, src_path: Path) -> Path | None:
        return self.cache.get_existing_cached_path(
            Path(src_path),
            kind="preview",
            width=self.preview_width,
            height=self.preview_height,
        )

    def get_cached_thumb_path(self, src_path: Path, *, width: int, height: int) -> Path | None:
        return self.cache.get_existing_cached_path(
            Path(src_path),
            kind="thumb",
            width=max(16, int(width)),
            height=max(16, int(height)),
        )

    def prefetch_thumb(self, src_path: Path, *, width: int, height: int) -> None:
        resolved = Path(src_path).expanduser().resolve()
        key = f"thumb::{resolved}|{int(width)}x{int(height)}"
        with self._lock:
            if key in self._futures:
                return
            fut = self._executor.submit(
                self.cache.get_or_create_cached_path,
                resolved,
                kind="thumb",
                width=max(16, int(width)),
                height=max(16, int(height)),
            )
            self._futures[key] = fut
        fut.add_done_callback(lambda _f, k=key: self._on_background_task_done(k))

    def shutdown(self) -> None:
        with self._lock:
            futures = list(self._futures.values())
            self._futures.clear()
        for future in futures:
            future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _schedule_prefetch(self, src_path: Path) -> None:
        key = str(Path(src_path).expanduser().resolve())
        with self._lock:
            if key in self._warm_preview_bytes:
                self._touch_warm_key(key)
                return
            if key in self._futures:
                return
            future = self._executor.submit(self._build_preview_bytes, Path(key))
            self._futures[key] = future
        future.add_done_callback(lambda fut, k=key: self._on_prefetch_done(k, fut))

    def _on_background_task_done(self, key: str) -> None:
        with self._lock:
            self._futures.pop(key, None)

    def _on_prefetch_done(self, key: str, future: Future) -> None:
        try:
            value = future.result()
        except Exception:
            value = None
        with self._lock:
            self._futures.pop(key, None)
            if value:
                self._warm_preview_bytes[key] = value
                self._touch_warm_key(key)
                while len(self._warm_order) > self.max_warm_entries:
                    stale = self._warm_order.pop(0)
                    self._warm_preview_bytes.pop(stale, None)

    def _build_preview_bytes(self, src_path: Path) -> bytes | None:
        cached = self.cache.get_or_create_cached_path(
            src_path,
            kind="preview",
            width=self.preview_width,
            height=self.preview_height,
        )
        if cached is None or not cached.exists():
            return None
        try:
            data = cached.read_bytes()
        except Exception:
            return None
        if not data:
            return None
        # Keep warm-memory bounded.
        if len(data) > 8 * 1024 * 1024:
            return None
        return data

    def _prune_warm_memory(self, keep_keys: set[str]) -> None:
        with self._lock:
            if not self._warm_order:
                return
            new_order = []
            for key in self._warm_order:
                if key in keep_keys:
                    new_order.append(key)
                else:
                    self._warm_preview_bytes.pop(key, None)
            self._warm_order = new_order

    def _touch_warm_key(self, key: str) -> None:
        try:
            self._warm_order.remove(key)
        except ValueError:
            pass
        self._warm_order.append(key)
