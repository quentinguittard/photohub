from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_sqlite_engine(db_path: Path):
    return create_engine(f"sqlite:///{db_path}", future=True)


def create_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(engine) -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _run_sqlite_migrations(engine)


def _run_sqlite_migrations(engine) -> None:
    with engine.begin() as conn:
        _ensure_column(conn, table="projects", column="client_id", ddl="INTEGER")
        _ensure_column(conn, table="projects", column="quality_check_config_json", ddl="TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, table="projects", column="quality_check_validation_json", ddl="TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(
            conn,
            table="assets",
            column="workflow_state",
            ddl="TEXT NOT NULL DEFAULT 'draft'",
        )
        _ensure_column(conn, table="assets", column="exif_iso", ddl="INTEGER")
        _ensure_column(conn, table="assets", column="exif_lens", ddl="TEXT")
        _ensure_column(conn, table="assets", column="exif_camera", ddl="TEXT")
        _ensure_column(conn, table="assets", column="exif_shot_date", ddl="TEXT")
        _ensure_column(conn, table="assets", column="iptc_keywords", ddl="TEXT")
        _ensure_column(conn, table="assets", column="iptc_author", ddl="TEXT")
        _ensure_column(conn, table="assets", column="iptc_copyright", ddl="TEXT")
        _backfill_project_quality_check(conn)
        _backfill_asset_workflow_state(conn)
        _backfill_asset_metadata_index(conn)


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    columns = {row[1] for row in rows}
    if column in columns:
        return
    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _backfill_asset_workflow_state(conn) -> None:
    conn.exec_driver_sql(
        """
        UPDATE assets
           SET workflow_state = 'draft'
         WHERE workflow_state IS NULL OR TRIM(workflow_state) = ''
        """
    )


def _backfill_asset_metadata_index(conn) -> None:
    rows = conn.exec_driver_sql(
        """
        SELECT id, metadata_json, exif_iso, exif_lens, exif_camera, exif_shot_date,
               iptc_keywords, iptc_author, iptc_copyright
          FROM assets
        """
    ).fetchall()
    if not rows:
        return

    for row in rows:
        row_id = int(row[0])
        metadata_text = row[1]
        if any(value is not None and str(value).strip() for value in row[2:]):
            continue
        try:
            payload = json.loads(metadata_text or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        exif = payload.get("exif", {})
        if not isinstance(exif, dict):
            exif = {}
        iptc = payload.get("iptc", {})
        if not isinstance(iptc, dict):
            iptc = {}

        iso = _to_int(exif.get("iso"))
        lens = _to_text(exif.get("lens_model"))
        camera = _to_text(exif.get("camera"))
        shot_date = _to_text(exif.get("shot_date"))[:10]
        keywords_norm = _keywords_norm_string(iptc.get("keywords", []))
        author = _to_text(iptc.get("author"))
        copyright_text = _to_text(iptc.get("copyright"))

        conn.exec_driver_sql(
            """
            UPDATE assets
               SET exif_iso = ?,
                   exif_lens = ?,
                   exif_camera = ?,
                   exif_shot_date = ?,
                   iptc_keywords = ?,
                   iptc_author = ?,
                   iptc_copyright = ?
             WHERE id = ?
            """,
            (
                iso,
                lens or None,
                camera or None,
                shot_date or None,
                keywords_norm or None,
                author or None,
                copyright_text or None,
                row_id,
            ),
        )


def _backfill_project_quality_check(conn) -> None:
    rows = conn.exec_driver_sql(
        """
        SELECT id, quality_check_config_json, quality_check_validation_json
          FROM projects
        """
    ).fetchall()
    if not rows:
        return

    default_config = _default_project_quality_config()

    for row in rows:
        row_id = int(row[0])
        config_payload = _parse_json_dict(row[1])
        validation_payload = _parse_json_dict(row[2])

        normalized_config = _normalize_quality_config_payload(config_payload, default_config)
        normalized_validation = _normalize_quality_validation_payload(validation_payload)

        encoded_config = json.dumps(normalized_config, ensure_ascii=True)
        encoded_validation = json.dumps(normalized_validation, ensure_ascii=True)
        current_config = str(row[1] or "").strip()
        current_validation = str(row[2] or "").strip()

        if encoded_config == current_config and encoded_validation == current_validation:
            continue

        conn.exec_driver_sql(
            """
            UPDATE projects
               SET quality_check_config_json = ?,
                   quality_check_validation_json = ?
             WHERE id = ?
            """,
            (encoded_config, encoded_validation, row_id),
        )


def _default_project_quality_config() -> dict:
    return {
        "enabled": True,
        "version": 1,
        "rules": {
            "min_rating_non_zero": {"enabled": True},
            "metadata_author_copyright": {"enabled": True},
            "watermark_enabled": {"enabled": False},
        },
    }


def _normalize_quality_config_payload(payload: dict, default_config: dict) -> dict:
    source = payload if isinstance(payload, dict) else {}
    rules = source.get("rules", {}) if isinstance(source.get("rules"), dict) else {}
    return {
        "enabled": _to_bool(source.get("enabled"), True),
        "version": 1,
        "rules": {
            "min_rating_non_zero": {"enabled": _to_bool(_read_rule_enabled(rules, "min_rating_non_zero"), True)},
            "metadata_author_copyright": {
                "enabled": _to_bool(_read_rule_enabled(rules, "metadata_author_copyright"), True)
            },
            "watermark_enabled": {"enabled": _to_bool(_read_rule_enabled(rules, "watermark_enabled"), False)},
        },
    } if source else json.loads(json.dumps(default_config, ensure_ascii=True))


def _normalize_quality_validation_payload(payload: dict) -> dict:
    source = payload if isinstance(payload, dict) else {}
    validated_at_utc = str(source.get("validated_at_utc", "") or "").strip()
    fingerprint = str(source.get("fingerprint", "") or "").strip()
    summary = source.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    if not validated_at_utc or not fingerprint:
        return {}
    return {
        "validated_at_utc": validated_at_utc,
        "fingerprint": fingerprint,
        "summary": dict(summary),
    }


def _read_rule_enabled(rules: dict, key: str):
    rule = rules.get(key, {})
    if isinstance(rule, dict):
        return rule.get("enabled")
    return None


def _to_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return bool(default)


def _parse_json_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _to_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except Exception:
        return None


def _keywords_norm_string(value) -> str:
    items: list[str] = []
    if isinstance(value, str):
        text = value.replace("\n", ",").replace(";", ",").replace("|", ",")
        items = [part.strip() for part in text.split(",")]
    elif isinstance(value, list):
        items = [str(part).strip() for part in value]
    lowered: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        lowered.append(token)
    if not lowered:
        return ""
    return "|" + "|".join(lowered) + "|"
