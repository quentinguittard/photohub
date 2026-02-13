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
        "text": "",
        "opacity": 70,
    },
    "delivery": {
        "create_zip": True,
        "create_report": True,
        "create_contact_sheet_pdf": True,
    },
}


def default_preset_config() -> dict:
    return copy.deepcopy(DEFAULT_PRESET_CONFIG)
