from __future__ import annotations

import re
from datetime import datetime

ANCHOR_ORDER = (
    "top_left",
    "top_center",
    "top_right",
    "center_left",
    "center",
    "center_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
)

VARIABLE_CATALOG: list[tuple[str, str]] = [
    ("client_name", "Nom client"),
    ("project_name", "Nom projet"),
    ("shoot_date", "Date shooting"),
    ("export_date", "Date export"),
    ("photographer_name", "Nom photographe"),
    ("copyright_notice", "Copyright"),
    ("preset_name", "Nom preset"),
    ("rating_min", "Note min"),
]

DEFAULT_TEXT_LAYER = {
    "enabled": True,
    "template": "{{client_name}} - {{shoot_date}}",
    "font_family": "Sans",
    "bold": False,
    "italic": False,
    "color_hex": "#FFFFFF",
    "stroke_enabled": True,
    "stroke_color_hex": "#000000",
    "stroke_width_px": 2,
    "anchor": "bottom_right",
    "offset_x_pct": -2.0,
    "offset_y_pct": -2.0,
    "size_pct": 4.0,
    "angle_deg": 0.0,
    "opacity": 70,
}

DEFAULT_LOGO_LAYER = {
    "enabled": False,
    "asset_rel_path": "",
    "anchor": "bottom_left",
    "offset_x_pct": 2.0,
    "offset_y_pct": -2.0,
    "size_pct": 12.0,
    "angle_deg": 0.0,
    "opacity": 70,
}

DEFAULT_WATERMARK_CONFIG = {
    "enabled": False,
    "version": 2,
    "render_order": ["logo", "text"],
    "text": dict(DEFAULT_TEXT_LAYER),
    "logo": dict(DEFAULT_LOGO_LAYER),
}

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def normalize_opacity_percentage(value, *, default: int = 70) -> int:
    try:
        raw = int(float(value))
    except Exception:
        return max(0, min(100, int(default)))
    if raw < 0:
        return 0
    if raw > 100:
        raw = int(round((raw / 255.0) * 100))
    return max(0, min(100, raw))


def normalize_watermark_config(payload: dict | None) -> dict:
    source = payload if isinstance(payload, dict) else {}
    if isinstance(source.get("watermark"), dict):
        source = source.get("watermark", {})

    result = {
        "enabled": bool(source.get("enabled", DEFAULT_WATERMARK_CONFIG["enabled"])),
        "version": 2,
        "render_order": _normalize_render_order(source.get("render_order")),
        "text": _normalize_text_layer(source.get("text")),
        "logo": _normalize_logo_layer(source.get("logo")),
    }

    # Legacy keys support: watermark.text (str), watermark.opacity (0..100|255).
    legacy_text = source.get("text")
    if isinstance(legacy_text, str):
        clean = legacy_text.strip()
        if clean:
            result["text"]["template"] = clean
    if "opacity" in source:
        result["text"]["opacity"] = normalize_opacity_percentage(source.get("opacity"), default=70)

    return result


def build_watermark_context(
    *,
    project,
    preset_name: str,
    min_rating: int,
    studio_profile: dict | None,
    now_utc: datetime | None = None,
) -> dict[str, str]:
    now_value = now_utc or datetime.utcnow()
    profile = studio_profile if isinstance(studio_profile, dict) else {}

    client_name = ""
    try:
        client = getattr(project, "client", None)
        if client is not None:
            client_name = str(getattr(client, "name", "") or "").strip()
    except Exception:
        client_name = ""

    shoot_date = ""
    try:
        shoot_date_obj = getattr(project, "shoot_date", None)
        if shoot_date_obj is not None:
            shoot_date = str(shoot_date_obj)
    except Exception:
        shoot_date = ""

    return {
        "client_name": client_name,
        "project_name": str(getattr(project, "name", "") or "").strip(),
        "shoot_date": shoot_date,
        "export_date": now_value.strftime("%Y-%m-%d"),
        "photographer_name": str(profile.get("photographer_name", "") or "").strip(),
        "copyright_notice": str(profile.get("copyright_notice", "") or "").strip(),
        "preset_name": str(preset_name or "").strip(),
        "rating_min": str(max(0, min(int(min_rating), 5))),
    }


def render_template(text: str, context: dict[str, object] | None) -> str:
    payload = str(text or "")
    mapping = context if isinstance(context, dict) else {}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = mapping.get(key, "")
        return str(value or "")

    rendered = _TOKEN_RE.sub(_replace, payload)
    lines: list[str] = []
    for line in rendered.splitlines():
        normalized = re.sub(r"\s{2,}", " ", line).strip()
        lines.append(normalized)
    return "\n".join(line for line in lines if line).strip()


def summarize_watermark_config(config: dict | None) -> str:
    wm = normalize_watermark_config(config if isinstance(config, dict) else {})
    if not wm.get("enabled", False):
        return "Desactive"
    text_on = bool(wm.get("text", {}).get("enabled", False))
    logo_on = bool(wm.get("logo", {}).get("enabled", False))
    text_part = "texte on" if text_on else "texte off"
    logo_part = "logo on" if logo_on else "logo off"
    return f"{text_part} | {logo_part} | ordre: {'>'.join(wm.get('render_order', []))}"


def _normalize_render_order(value) -> list[str]:
    if not isinstance(value, list):
        return list(DEFAULT_WATERMARK_CONFIG["render_order"])
    out: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name not in {"text", "logo"}:
            continue
        if name in out:
            continue
        out.append(name)
    for fallback in ("logo", "text"):
        if fallback not in out:
            out.append(fallback)
    return out


def _normalize_text_layer(value) -> dict:
    source = value if isinstance(value, dict) else {}
    out = dict(DEFAULT_TEXT_LAYER)
    out["enabled"] = bool(source.get("enabled", out["enabled"]))
    out["template"] = str(source.get("template", out["template"]) or "").strip()
    out["font_family"] = str(source.get("font_family", out["font_family"]) or "").strip() or "Sans"
    out["bold"] = bool(source.get("bold", out["bold"]))
    out["italic"] = bool(source.get("italic", out["italic"]))
    out["color_hex"] = _normalize_hex(source.get("color_hex"), fallback=out["color_hex"])
    out["stroke_enabled"] = bool(source.get("stroke_enabled", out["stroke_enabled"]))
    out["stroke_color_hex"] = _normalize_hex(source.get("stroke_color_hex"), fallback=out["stroke_color_hex"])
    out["stroke_width_px"] = _to_int(source.get("stroke_width_px"), default=out["stroke_width_px"], low=0, high=24)
    out["anchor"] = _normalize_anchor(source.get("anchor"), out["anchor"])
    out["offset_x_pct"] = _to_float(source.get("offset_x_pct"), out["offset_x_pct"], low=-100.0, high=100.0)
    out["offset_y_pct"] = _to_float(source.get("offset_y_pct"), out["offset_y_pct"], low=-100.0, high=100.0)
    out["size_pct"] = _to_float(source.get("size_pct"), out["size_pct"], low=0.5, high=80.0)
    out["angle_deg"] = _to_float(source.get("angle_deg"), out["angle_deg"], low=-180.0, high=180.0)
    out["opacity"] = normalize_opacity_percentage(source.get("opacity"), default=out["opacity"])
    return out


def _normalize_logo_layer(value) -> dict:
    source = value if isinstance(value, dict) else {}
    out = dict(DEFAULT_LOGO_LAYER)
    out["enabled"] = bool(source.get("enabled", out["enabled"]))
    out["asset_rel_path"] = str(source.get("asset_rel_path", out["asset_rel_path"]) or "").strip()
    out["anchor"] = _normalize_anchor(source.get("anchor"), out["anchor"])
    out["offset_x_pct"] = _to_float(source.get("offset_x_pct"), out["offset_x_pct"], low=-100.0, high=100.0)
    out["offset_y_pct"] = _to_float(source.get("offset_y_pct"), out["offset_y_pct"], low=-100.0, high=100.0)
    out["size_pct"] = _to_float(source.get("size_pct"), out["size_pct"], low=0.5, high=100.0)
    out["angle_deg"] = _to_float(source.get("angle_deg"), out["angle_deg"], low=-180.0, high=180.0)
    out["opacity"] = normalize_opacity_percentage(source.get("opacity"), default=out["opacity"])
    return out


def _normalize_anchor(value, fallback: str) -> str:
    token = str(value or "").strip().lower()
    if token in ANCHOR_ORDER:
        return token
    return fallback


def _normalize_hex(value, *, fallback: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return fallback
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if len(raw) != 7:
        return fallback
    if not all(ch in "0123456789ABCDEF" for ch in raw[1:]):
        return fallback
    return raw


def _to_int(value, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = int(default)
    return max(low, min(high, parsed))


def _to_float(value, default: float, *, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if parsed < low:
        return float(low)
    if parsed > high:
        return float(high)
    return float(parsed)
