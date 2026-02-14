from __future__ import annotations

import copy


DEFAULT_PRESET_CONFIG = {
    "naming": {
        "pattern": "{project}_{date}_{seq:04d}",
    },
    "import": {
        "verify_checksum": True,
        "dual_backup": False,
        "backup_path": "",
    },
    "export_profiles": {
        "web": {
            "format": "JPEG",
            "max_width": 2048,
            "quality": 82,
            "subdir": "web",
        },
        "print": {
            "format": "JPEG",
            "max_width": 6000,
            "quality": 95,
            "subdir": "print",
        },
        "social": {
            "format": "JPEG",
            "max_width": 1080,
            "quality": 80,
            "subdir": "social",
        },
    },
    "watermark": {
        "enabled": False,
        "version": 2,
        "render_order": ["logo", "text"],
        "text": {
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
        },
        "logo": {
            "enabled": False,
            "asset_rel_path": "",
            "anchor": "bottom_left",
            "offset_x_pct": 2.0,
            "offset_y_pct": -2.0,
            "size_pct": 12.0,
            "angle_deg": 0.0,
            "opacity": 70,
        },
    },
    "delivery": {
        "create_zip": True,
        "create_report": True,
        "create_contact_sheet_pdf": True,
    },
}


def default_preset_config() -> dict:
    return copy.deepcopy(DEFAULT_PRESET_CONFIG)
