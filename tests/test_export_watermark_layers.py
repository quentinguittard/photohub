import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from photohub.config import AppPaths, resolve_app_paths
from photohub.db import create_session_factory, create_sqlite_engine, init_db
from photohub.models import Asset
from photohub.services.exports import ExportService
from photohub.services.presets import PresetService
from photohub.services.projects import ProjectService
from photohub.services.watermark_assets import import_logo

try:
    from PIL import Image, ImageChops

    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False
    Image = None
    ImageChops = None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow requis pour les tests watermark export.")
class ExportWatermarkLayerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tempdir.name

        self.paths = resolve_app_paths()
        self.engine = create_sqlite_engine(self.paths.db_path)
        init_db(self.engine)
        self.session_factory = create_session_factory(self.engine)
        self.project_service = ProjectService(
            session_factory=self.session_factory,
            paths=AppPaths(
                data_dir=self.paths.data_dir,
                db_path=self.paths.db_path,
                projects_dir=self.paths.projects_dir,
            ),
        )
        self.preset_service = PresetService(session_factory=self.session_factory)
        self.export_service = ExportService(session_factory=self.session_factory)

    def tearDown(self):
        self.engine.dispose()
        if self.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old_appdata
        self.tempdir.cleanup()

    def _seed_project_with_single_asset(
        self,
        name: str,
        *,
        width: int = 1200,
        height: int = 800,
        source_format: str = "JPEG",
    ) -> tuple[int, Path]:
        project = self.project_service.create_project(name, date(2026, 2, 14))
        self.project_service.update_quality_check(project.id, {"enabled": False})
        ext = ".png" if str(source_format).upper() == "PNG" else ".jpg"
        src = Path(project.root_path) / "raw" / f"frame{ext}"
        Image.new("RGB", (int(width), int(height)), color=(245, 245, 245)).save(src, format=source_format)
        with self.session_factory() as session:
            session.add(
                Asset(
                    project_id=project.id,
                    src_path=str(src),
                    hash_sha256="a" * 64,
                    rating=4,
                    is_rejected=False,
                )
            )
            session.commit()
        return project.id, src

    def _export_once(self, project_id: int) -> Path:
        destination = Path(self.tempdir.name) / "delivery"
        result = self.export_service.run_export(
            project_id=project_id,
            destination_dir=destination,
            profiles=["web"],
            min_rating=0,
            create_zip=False,
            create_report=False,
            create_contact_sheet=False,
        )
        out_dir = result.profiles[0].output_dir
        files = sorted([p for p in out_dir.iterdir() if p.is_file()])
        self.assertTrue(files)
        return max(files, key=lambda p: p.stat().st_mtime)

    def _assert_export_changed_image(self, src: Path, out: Path):
        with Image.open(src) as src_img:
            with Image.open(out) as out_img:
                diff = ImageChops.difference(src_img.convert("RGB"), out_img.convert("RGB"))
                self.assertIsNotNone(diff.getbbox(), "Le watermark doit modifier l'image exportee.")

    @staticmethod
    def _diff_bbox_normalized(src: Path, out: Path) -> tuple[float, float, float, float]:
        with Image.open(src) as src_img:
            with Image.open(out) as out_img:
                lhs = src_img.convert("RGB")
                rhs = out_img.convert("RGB")
                diff = ImageChops.difference(lhs, rhs)
                bbox = diff.getbbox()
                if bbox is None:
                    raise AssertionError("Aucune difference detectee alors qu'un watermark etait attendu.")
                left, top, right, bottom = bbox
                return (
                    left / max(1.0, float(lhs.width)),
                    top / max(1.0, float(lhs.height)),
                    right / max(1.0, float(lhs.width)),
                    bottom / max(1.0, float(lhs.height)),
                )

    def test_export_text_logo_and_legacy_watermark_paths(self):
        project_id, src = self._seed_project_with_single_asset("WM Layers")

        logo_source = Path(self.tempdir.name) / "logo.png"
        Image.new("RGBA", (240, 120), color=(16, 185, 129, 255)).save(logo_source, format="PNG")
        logo_rel = import_logo(logo_source, self.paths.data_dir)

        # Text only.
        text_preset = self.preset_service.create_preset(
            name="WM_TEXT_ONLY",
            config={
                "watermark": {
                    "enabled": True,
                    "text": {
                        "enabled": True,
                        "template": "HELLO {{project_name}}",
                        "color_hex": "#111111",
                        "anchor": "top_left",
                        "offset_x_pct": 2.0,
                        "offset_y_pct": 2.0,
                        "size_pct": 5.0,
                        "opacity": 90,
                    },
                    "logo": {"enabled": False},
                }
            },
        )
        self.project_service.assign_preset(project_id, text_preset.id)
        out_text = self._export_once(project_id)
        self._assert_export_changed_image(src, out_text)

        # Logo only.
        logo_preset = self.preset_service.create_preset(
            name="WM_LOGO_ONLY",
            config={
                "watermark": {
                    "enabled": True,
                    "text": {"enabled": False},
                    "logo": {
                        "enabled": True,
                        "asset_rel_path": logo_rel,
                        "anchor": "bottom_left",
                        "offset_x_pct": 2.0,
                        "offset_y_pct": -2.0,
                        "size_pct": 16.0,
                        "opacity": 80,
                    },
                }
            },
        )
        self.project_service.assign_preset(project_id, logo_preset.id)
        out_logo = self._export_once(project_id)
        self._assert_export_changed_image(src, out_logo)

        # Text + logo.
        both_preset = self.preset_service.create_preset(
            name="WM_BOTH",
            config={
                "watermark": {
                    "enabled": True,
                    "render_order": ["logo", "text"],
                    "text": {
                        "enabled": True,
                        "template": "{{client_name}} {{export_date}}",
                        "anchor": "bottom_right",
                        "offset_x_pct": -2.0,
                        "offset_y_pct": -2.0,
                        "size_pct": 4.0,
                        "opacity": 75,
                    },
                    "logo": {
                        "enabled": True,
                        "asset_rel_path": logo_rel,
                        "anchor": "top_right",
                        "offset_x_pct": -2.0,
                        "offset_y_pct": 2.0,
                        "size_pct": 14.0,
                        "opacity": 70,
                    },
                }
            },
        )
        self.project_service.assign_preset(project_id, both_preset.id)
        out_both = self._export_once(project_id)
        self._assert_export_changed_image(src, out_both)

        # Legacy compatibility (text/opacite old schema).
        legacy_preset = self.preset_service.create_preset(
            name="WM_LEGACY",
            config={"watermark": {"enabled": True, "text": "LEGACY", "opacity": 85}},
        )
        self.project_service.assign_preset(project_id, legacy_preset.id)
        out_legacy = self._export_once(project_id)
        self._assert_export_changed_image(src, out_legacy)

    def test_relative_position_is_stable_across_formats(self):
        project_landscape, src_landscape = self._seed_project_with_single_asset(
            "WM Landscape",
            width=1400,
            height=900,
            source_format="PNG",
        )
        project_portrait, src_portrait = self._seed_project_with_single_asset(
            "WM Portrait",
            width=900,
            height=1400,
            source_format="PNG",
        )

        preset = self.preset_service.create_preset(
            name="WM_RELATIVE_POSITION",
            config={
                "export_profiles": {
                    "web": {"format": "PNG", "max_width": 4000, "quality": 90, "subdir": "web"}
                },
                "watermark": {
                    "enabled": True,
                    "render_order": ["text"],
                    "text": {
                        "enabled": True,
                        "template": "RELATIVE",
                        "anchor": "bottom_right",
                        "offset_x_pct": -3.0,
                        "offset_y_pct": -4.0,
                        "size_pct": 5.0,
                        "angle_deg": 18.0,
                        "opacity": 92,
                        "color_hex": "#111111",
                    },
                    "logo": {"enabled": False},
                },
            },
        )
        self.project_service.assign_preset(project_landscape, preset.id)
        self.project_service.assign_preset(project_portrait, preset.id)

        out_landscape = self._export_once(project_landscape)
        out_portrait = self._export_once(project_portrait)
        self._assert_export_changed_image(src_landscape, out_landscape)
        self._assert_export_changed_image(src_portrait, out_portrait)

        _, _, right_l, bottom_l = self._diff_bbox_normalized(src_landscape, out_landscape)
        _, _, right_p, bottom_p = self._diff_bbox_normalized(src_portrait, out_portrait)
        self.assertLess(
            abs(right_l - right_p),
            0.04,
            "L'ancrage droit relatif du watermark doit rester stable entre formats.",
        )
        self.assertLess(
            abs(bottom_l - bottom_p),
            0.04,
            "L'ancrage bas relatif du watermark doit rester stable entre formats.",
        )


if __name__ == "__main__":
    unittest.main()
