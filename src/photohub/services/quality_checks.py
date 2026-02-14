from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from sqlalchemy import select

from ..models import Asset
from .watermarks import normalize_watermark_config


QUALITY_ERROR_PREFIX = "[QUALITY_CHECK_FAILED]"

DEFAULT_PROJECT_QUALITY_CONFIG = {
    "enabled": True,
    "version": 1,
    "rules": {
        "min_rating_non_zero": {"enabled": True},
        "metadata_author_copyright": {"enabled": True},
        "watermark_enabled": {"enabled": False},
    },
}


@dataclass(frozen=True)
class QualityIssue:
    rule: str
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class QualityEvaluation:
    enabled: bool
    status: str
    issues: list[QualityIssue]
    fingerprint: str
    summary: dict[str, object]
    validated_at_utc: str | None
    is_validated: bool

    @property
    def can_export(self) -> bool:
        if not self.enabled:
            return True
        if not self.is_validated:
            return False
        return len(self.issues) == 0

    def to_dict(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "status": str(self.status),
            "issues": [asdict(item) for item in self.issues],
            "fingerprint": str(self.fingerprint),
            "summary": dict(self.summary),
            "validated_at_utc": self.validated_at_utc,
            "is_validated": bool(self.is_validated),
            "can_export": bool(self.can_export),
        }


class QualityChecklistError(ValueError):
    def __init__(self, message: str, *, evaluation: QualityEvaluation | None = None):
        super().__init__(message)
        self.evaluation = evaluation


def normalize_quality_config(payload: dict | None) -> dict:
    source = payload if isinstance(payload, dict) else {}
    rules = source.get("rules", {}) if isinstance(source.get("rules"), dict) else {}
    return {
        "enabled": _as_bool(source.get("enabled", True), default=True),
        "version": 1,
        "rules": {
            "min_rating_non_zero": {
                "enabled": _as_bool(
                    _rule_payload(rules, "min_rating_non_zero").get("enabled", True),
                    default=True,
                )
            },
            "metadata_author_copyright": {
                "enabled": _as_bool(
                    _rule_payload(rules, "metadata_author_copyright").get("enabled", True),
                    default=True,
                )
            },
            "watermark_enabled": {
                "enabled": _as_bool(
                    _rule_payload(rules, "watermark_enabled").get("enabled", False),
                    default=False,
                )
            },
        },
    }


def normalize_quality_validation(payload: dict | None) -> dict:
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


def load_project_quality_payload(project) -> tuple[dict, dict]:
    config_payload = _parse_json_dict(getattr(project, "quality_check_config_json", "{}"))
    validation_payload = _parse_json_dict(getattr(project, "quality_check_validation_json", "{}"))
    config = normalize_quality_config(config_payload if config_payload else DEFAULT_PROJECT_QUALITY_CONFIG)
    validation = normalize_quality_validation(validation_payload)
    return config, validation


def compute_quality_fingerprint(payload: dict) -> str:
    serialized = _canonical_json(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def evaluate_quality(
    *,
    session,
    project,
    export_min_rating: int | None,
    config: dict | None = None,
    validation: dict | None = None,
) -> QualityEvaluation:
    loaded_config, loaded_validation = load_project_quality_payload(project)
    normalized_config = normalize_quality_config(config if isinstance(config, dict) else loaded_config)
    normalized_validation = normalize_quality_validation(validation if isinstance(validation, dict) else loaded_validation)
    enabled = bool(normalized_config.get("enabled", True))
    rules = normalized_config.get("rules", {})
    safe_min_rating = _safe_min_rating(export_min_rating, default=1)

    rows = list(
        session.execute(
            select(
                Asset.id,
                Asset.rating,
                Asset.is_rejected,
                Asset.iptc_author,
                Asset.iptc_copyright,
            ).where(Asset.project_id == int(project.id))
        ).all()
    )

    issues: list[QualityIssue] = []
    exportable = [row for row in rows if not bool(row[2]) and int(row[1] or 0) >= safe_min_rating]

    missing_author = 0
    missing_copyright = 0

    min_rule_enabled = bool(_rule_payload(rules, "min_rating_non_zero").get("enabled", True))
    metadata_rule_enabled = bool(_rule_payload(rules, "metadata_author_copyright").get("enabled", True))
    watermark_rule_enabled = bool(_rule_payload(rules, "watermark_enabled").get("enabled", False))

    if enabled and min_rule_enabled and safe_min_rating <= 0:
        issues.append(
            QualityIssue(
                rule="min_rating_non_zero",
                code="min_rating_invalid",
                message="Checklist: la note min export doit etre strictement superieure a 0.",
                details={"min_rating": int(safe_min_rating)},
            )
        )

    if enabled and metadata_rule_enabled:
        for row in exportable:
            author = str(row[3] or "").strip()
            copyright_text = str(row[4] or "").strip()
            if not author:
                missing_author += 1
            if not copyright_text:
                missing_copyright += 1
        if missing_author > 0 or missing_copyright > 0:
            issues.append(
                QualityIssue(
                    rule="metadata_author_copyright",
                    code="metadata_missing",
                    message=(
                        "Checklist: metadata incomplete sur assets exportables "
                        f"(author manquant: {missing_author}, copyright manquant: {missing_copyright})."
                    ),
                    details={
                        "missing_author_count": int(missing_author),
                        "missing_copyright_count": int(missing_copyright),
                        "exportable_count": int(len(exportable)),
                    },
                )
            )

    watermark_config = None
    watermark_enabled = None
    if enabled and watermark_rule_enabled:
        from .presets import resolve_effective_config_for_project_model  # local import to avoid cycles

        effective = resolve_effective_config_for_project_model(session, project)
        watermark_config = normalize_watermark_config(effective.get("watermark", {}))
        watermark_enabled = bool(watermark_config.get("enabled", False))
        if not watermark_enabled:
            issues.append(
                QualityIssue(
                    rule="watermark_enabled",
                    code="watermark_disabled",
                    message="Checklist: watermark requis mais desactive dans le preset effectif.",
                    details={},
                )
            )

    fingerprint_payload: dict[str, object] = {
        "version": 1,
        "project_id": int(project.id),
        "project_preset_id": int(project.preset_id) if getattr(project, "preset_id", None) is not None else None,
        "config": normalized_config,
    }
    if min_rule_enabled:
        fingerprint_payload["ratings"] = [
            [int(row[0]), int(row[1] or 0), bool(row[2])]
            for row in sorted(rows, key=lambda item: int(item[0]))
        ]
    if metadata_rule_enabled:
        fingerprint_payload["metadata"] = [
            [
                int(row[0]),
                str(row[3] or "").strip(),
                str(row[4] or "").strip(),
            ]
            for row in sorted(rows, key=lambda item: int(item[0]))
        ]
    if watermark_rule_enabled:
        fingerprint_payload["watermark"] = watermark_config if watermark_config is not None else {}

    fingerprint = compute_quality_fingerprint(fingerprint_payload)
    saved_fingerprint = str(normalized_validation.get("fingerprint", "") or "").strip()
    validated_at_utc = normalized_validation.get("validated_at_utc")
    has_validation = bool(saved_fingerprint and validated_at_utc)

    if not enabled:
        status = "disabled"
        is_validated = True
    elif not has_validation:
        status = "not_validated"
        is_validated = False
    elif saved_fingerprint != fingerprint:
        status = "stale"
        is_validated = False
    else:
        status = "validated"
        is_validated = True

    if enabled and len(issues) > 0 and status == "validated":
        status = "stale"
        is_validated = False

    summary = {
        "total_assets": int(len(rows)),
        "exportable_count": int(len(exportable)),
        "export_min_rating": int(safe_min_rating),
        "missing_author_count": int(missing_author),
        "missing_copyright_count": int(missing_copyright),
        "watermark_enabled": bool(watermark_enabled) if watermark_enabled is not None else None,
    }

    return QualityEvaluation(
        enabled=enabled,
        status=status,
        issues=issues,
        fingerprint=fingerprint,
        summary=summary,
        validated_at_utc=validated_at_utc if isinstance(validated_at_utc, str) else None,
        is_validated=is_validated,
    )


def validate_quality_manually(*, session, project, config: dict | None = None) -> dict:
    normalized_config = normalize_quality_config(config if isinstance(config, dict) else load_project_quality_payload(project)[0])
    if not bool(normalized_config.get("enabled", True)):
        return {}

    evaluation = evaluate_quality(
        session=session,
        project=project,
        export_min_rating=1,
        config=normalized_config,
        validation={},
    )
    if evaluation.issues:
        raise QualityChecklistError(format_quality_blocking_message(evaluation), evaluation=evaluation)

    now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    return normalize_quality_validation(
        {
            "validated_at_utc": now_utc,
            "fingerprint": evaluation.fingerprint,
            "summary": evaluation.summary,
        }
    )


def assert_quality_for_export(*, session, project, export_min_rating: int, config: dict | None = None, validation: dict | None = None) -> QualityEvaluation:
    evaluation = evaluate_quality(
        session=session,
        project=project,
        export_min_rating=export_min_rating,
        config=config,
        validation=validation,
    )
    if evaluation.can_export:
        return evaluation
    raise QualityChecklistError(format_quality_blocking_message(evaluation), evaluation=evaluation)


def format_quality_blocking_message(evaluation: QualityEvaluation) -> str:
    lines = [f"{QUALITY_ERROR_PREFIX} Checklist qualite export invalide."]
    if evaluation.status == "not_validated":
        lines.append("- Validation manuelle requise avant export.")
    elif evaluation.status == "stale":
        lines.append("- La validation est perimee (donnees projet modifiees).")
    for issue in evaluation.issues:
        lines.append(f"- {issue.message}")
    return "\n".join(lines)


def default_project_quality_config() -> dict:
    return copy.deepcopy(DEFAULT_PROJECT_QUALITY_CONFIG)


def _safe_min_rating(value, *, default: int) -> int:
    try:
        raw = int(value)
    except Exception:
        raw = int(default)
    return max(0, min(5, raw))


def _rule_payload(rules: dict, key: str) -> dict:
    payload = rules.get(key, {})
    if not isinstance(payload, dict):
        return {}
    return payload


def _as_bool(value, *, default: bool) -> bool:
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


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
