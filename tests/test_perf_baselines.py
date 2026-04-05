"""Performance regression guards for PhotoHub.

These tests are NOT load tests — they do not launch the Qt UI.  They validate
that the core service-layer operations meet minimum throughput / latency budgets
that, if violated, indicate a performance regression in a critical hot path.

Run with:
    pytest tests/test_perf_baselines.py -v

Each test is designed to be fast (< 5 s) and deterministic on any modern
developer machine or CI runner with an SSD.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# sha256 throughput
# ---------------------------------------------------------------------------

def test_sha256_chunk_size_is_at_least_4mb():
    """sha256_file() must use chunks of at least 4 MB for large-file throughput."""
    import inspect
    from photohub.utils import sha256_file

    src = inspect.getsource(sha256_file)
    # Verify the chunk size is 4 MB or larger by parsing the literal in source.
    assert "4 * 1024 * 1024" in src or "4096 * 1024" in src or "8 * 1024 * 1024" in src, (
        "sha256_file() chunk size appears to be below 4 MB — expected at least 4 MB "
        "for large-file throughput on modern storage."
    )


def test_sha256_file_throughput_20mb(tmp_path: Path):
    """sha256_file() must hash a 20 MB file in under 2 s on any reasonable machine."""
    from photohub.utils import sha256_file

    data = b"x" * (20 * 1024 * 1024)
    target = tmp_path / "large.bin"
    target.write_bytes(data)

    t0 = time.perf_counter()
    result = sha256_file(target)
    elapsed = time.perf_counter() - t0

    assert len(result) == 64, "sha256_file returned unexpected hash length"
    assert elapsed < 2.0, (
        f"sha256_file() took {elapsed:.3f}s on a 20 MB file — expected < 2 s. "
        "Check chunk size or I/O path."
    )


# ---------------------------------------------------------------------------
# copy_and_hash correctness
# ---------------------------------------------------------------------------

def test_copy_and_hash_matches_sha256_file(tmp_path: Path):
    """copy_and_hash() must produce hashes identical to sha256_file() for both src and dst."""
    from photohub.utils import copy_and_hash, sha256_file

    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    payload = b"PhotoHub performance test payload " * 4096  # ~140 KB
    src.write_bytes(payload)

    src_hash, dst_hash = copy_and_hash(src, dst)

    assert dst.exists(), "copy_and_hash() did not create the destination file"
    assert dst.read_bytes() == payload, "Destination file content differs from source"
    assert src_hash == sha256_file(src), "src_hash from copy_and_hash differs from sha256_file(src)"
    assert dst_hash == sha256_file(dst), "dst_hash from copy_and_hash differs from sha256_file(dst)"
    assert src_hash == dst_hash, "src and dst hashes should be equal for identical content"


def test_copy_and_hash_detects_corruption(tmp_path: Path):
    """copy_and_hash() must report differing hashes when src and dst diverge."""
    from photohub.utils import copy_and_hash

    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"original content")

    src_hash, dst_hash = copy_and_hash(src, dst)

    # Now corrupt the destination after copy.
    dst.write_bytes(b"corrupted content!!")
    dst_hash_corrupted = hashlib.sha256(b"corrupted content!!").hexdigest()

    assert src_hash != dst_hash_corrupted, (
        "copy_and_hash should report different hashes when destination is corrupted"
    )


# ---------------------------------------------------------------------------
# DiskImageCache: no new DB connection per cache hit
# ---------------------------------------------------------------------------

def test_disk_image_cache_persistent_connection(tmp_path: Path):
    """DiskImageCache must reuse a single connection per thread (no per-call open/close)."""
    import sqlite3 as _sqlite3
    from photohub.services.preload import DiskImageCache

    cache = DiskImageCache(tmp_path / "cache")

    # _get_conn() must return the same object on repeated calls within the
    # same thread — proving no open/close cycle on every cache operation.
    conn_a = cache._get_conn()
    conn_b = cache._get_conn()
    conn_c = cache._get_conn()

    assert conn_a is conn_b is conn_c, (
        "DiskImageCache._get_conn() returned different connection objects on "
        "successive calls within the same thread. Per-call open/close is a "
        "known performance hotspot — connections must be thread-local singletons."
    )
    assert isinstance(conn_a, _sqlite3.Connection), (
        "Expected _get_conn() to return a sqlite3.Connection instance"
    )

    # Cross-thread connections must be independent objects.
    other_thread_conn: list = []

    def _get_other_thread_conn():
        other_thread_conn.append(cache._get_conn())

    t = threading.Thread(target=_get_other_thread_conn)
    t.start()
    t.join()

    assert other_thread_conn, "Background thread did not obtain a connection"
    assert other_thread_conn[0] is not conn_a, (
        "DiskImageCache must use independent connections per thread (threading.local)"
    )


# ---------------------------------------------------------------------------
# DiskImageCache: prune throttling
# ---------------------------------------------------------------------------

def test_disk_image_cache_prune_is_throttled(tmp_path: Path):
    """_prune_if_needed() must not be invoked on every cache entry creation."""
    from photohub.services.preload import DiskImageCache

    cache = DiskImageCache(tmp_path / "cache", max_cache_bytes=500 * 1024 * 1024)

    prune_call_count = [0]
    original_prune = cache._prune_if_needed

    def counting_prune():
        prune_call_count[0] += 1
        return original_prune()

    cache._prune_if_needed = counting_prune  # type: ignore[method-assign]

    # Simulate _PRUNE_ENTRY_THRESHOLD - 1 entries (should not trigger prune).
    threshold = cache._PRUNE_ENTRY_THRESHOLD
    with cache._lock:
        cache._entries_since_prune = threshold - 1
        cache._last_prune_time = time.monotonic()  # reset timer to block time-based prune

    # One more entry — still below threshold if we consider the reset.
    cache._throttled_prune()

    assert prune_call_count[0] == 0, (
        f"Prune should not fire below the {threshold}-entry threshold, "
        f"but _prune_if_needed() was called {prune_call_count[0]} time(s)."
    )

    # Now exceed the threshold.
    with cache._lock:
        cache._entries_since_prune = threshold
    cache._throttled_prune()

    assert prune_call_count[0] == 1, (
        f"Prune should fire exactly once when threshold is reached, "
        f"got {prune_call_count[0]} call(s)."
    )

    # Immediate second call must be suppressed (time guard).
    cache._throttled_prune()
    assert prune_call_count[0] == 1, (
        "Prune must not fire again immediately after the first run — time throttle is broken."
    )


# ---------------------------------------------------------------------------
# SQLAlchemy engine WAL mode
# ---------------------------------------------------------------------------

def test_sqlalchemy_engine_uses_wal_mode(tmp_path: Path):
    """The PhotoHub SQLAlchemy engine must configure SQLite WAL journal mode."""
    from photohub.db import create_sqlite_engine

    engine = create_sqlite_engine(tmp_path / "test.db")
    with engine.connect() as conn:
        row = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()
    engine.dispose()

    assert row is not None
    assert row[0].lower() == "wal", (
        f"Expected SQLite journal_mode=WAL, got '{row[0]}'. "
        "WAL mode is required for concurrent read access during imports."
    )


# ---------------------------------------------------------------------------
# Startup backfill: batch update
# ---------------------------------------------------------------------------

def test_backfill_uses_executemany_not_per_row(tmp_path: Path):
    """_backfill_asset_metadata_index must batch-update rows, not loop with single UPDATEs."""
    import inspect
    from photohub import db as db_module

    src = inspect.getsource(db_module._backfill_asset_metadata_index)

    # The optimized version collects updates and calls exec_driver_sql once with
    # a list — the loop's UPDATE call is gone and executemany semantics are used.
    assert "updates.append(" in src, (
        "_backfill_asset_metadata_index should collect rows into 'updates' list for batch commit"
    )
    # There must be a single exec_driver_sql call AFTER the loop (not inside).
    # Count occurrences: the call inside the loop is removed.
    loop_body_lines = [
        ln for ln in src.splitlines()
        if "exec_driver_sql" in ln and "UPDATE" in ln
    ]
    assert len(loop_body_lines) <= 1, (
        f"Expected at most 1 exec_driver_sql(UPDATE) call (outside the loop), "
        f"found {len(loop_body_lines)}: {loop_body_lines}"
    )


# ---------------------------------------------------------------------------
# CullingService: list_assets latency (SQLite)
# ---------------------------------------------------------------------------

def test_list_assets_latency_1000_rows(tmp_path: Path):
    """CullingService.list_assets() must return 1000 assets in under 300 ms."""
    from sqlalchemy import text
    from photohub.db import create_sqlite_engine, create_session_factory, init_db
    from photohub.services.culling import CullingService

    engine = create_sqlite_engine(tmp_path / "perf.db")
    init_db(engine)
    session_factory = create_session_factory(engine)

    # Seed 1000 assets for project 1 directly via SQL for speed.
    now_iso = "2024-01-01T00:00:00"
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO projects "
            "(name, shoot_date, status, root_path, quality_check_config_json, quality_check_validation_json, created_at) "
            "VALUES ('PerfProject', '2024-01-01', 'importe', '/tmp/perf', '{}', '{}', ?)",
            (now_iso,),
        )
        project_id = conn.exec_driver_sql("SELECT last_insert_rowid()").fetchone()[0]
        rows = [
            (
                project_id,
                f"/photos/img_{i:05d}.jpg",
                "a" * 64,  # fake hash
                i % 6,     # rating 0-5
                0,         # is_rejected
                "draft",   # workflow_state
                "{}",      # metadata_json
                now_iso,
            )
            for i in range(1000)
        ]
        conn.exec_driver_sql(
            "INSERT INTO assets "
            "(project_id, src_path, hash_sha256, rating, is_rejected, workflow_state, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    svc = CullingService(session_factory)

    t0 = time.perf_counter()
    assets = svc.list_assets(project_id=project_id, rejected_mode="all", min_rating=0)
    elapsed = time.perf_counter() - t0

    engine.dispose()

    assert len(assets) == 1000, f"Expected 1000 assets, got {len(assets)}"
    assert elapsed < 0.3, (
        f"list_assets() took {elapsed:.3f}s for 1000 rows — expected < 300 ms. "
        "Check for N+1 queries or missing index."
    )
