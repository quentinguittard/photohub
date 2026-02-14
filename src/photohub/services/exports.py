from __future__ import annotations

import math
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from ..config import load_settings
from ..models import Asset, ExportRun, Project
from .projects import try_transition_project_status
from .presets import resolve_effective_config_for_project_model
from .quality_checks import assert_quality_for_export
from .watermark_assets import resolve_logo_asset_path
from .watermarks import (
    build_watermark_context,
    normalize_opacity_percentage as normalize_watermark_opacity_percentage,
    normalize_watermark_config,
    render_template,
)
from ..utils import unique_path

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency fallback
    PIL_AVAILABLE = False
    Image = None
    ImageColor = None
    ImageDraw = None
    ImageFont = None


FORMAT_EXTENSION = {
    "JPEG": ".jpg",
    "JPG": ".jpg",
    "PNG": ".png",
    "TIFF": ".tif",
}


@dataclass
class ExportProfileResult:
    profile: str
    exported: int
    failed: int
    output_dir: Path
    status: str
    message: str


@dataclass
class ExportBatchResult:
    profiles: list[ExportProfileResult]
    zip_path: Path | None
    report_path: Path | None
    contact_sheet_path: Path | None


class ExportService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def run_export(
        self,
        project_id: int,
        destination_dir: str | Path,
        profiles: list[str],
        min_rating: int = 0,
        create_zip: bool = False,
        create_report: bool = True,
        create_contact_sheet: bool = False,
        progress_cb=None,
        is_cancelled=None,
    ) -> ExportBatchResult:
        if not profiles:
            raise ValueError("Selectionne au moins un profil d'export.")
        safe_min_rating = max(0, min(int(min_rating), 5))

        output_root = Path(destination_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError("Projet introuvable.")
            assert_quality_for_export(
                session=session,
                project=project,
                export_min_rating=safe_min_rating,
            )

            assets = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.project_id == project.id,
                        Asset.is_rejected.is_(False),
                        Asset.rating >= safe_min_rating,
                    )
                    .order_by(Asset.id.asc())
                ).all()
            )
            if not assets:
                raise ValueError("Aucun asset exportable pour ce projet.")

            config = resolve_effective_config_for_project_model(session, project)

            profile_configs = config.get("export_profiles", {})
            watermark_cfg = normalize_watermark_config(config.get("watermark", {}))
            settings = load_settings()
            studio_profile = settings.get("studio_profile", {})
            app_data_dir = Path(str(settings.get("active_data_dir", "") or "")).expanduser()
            if not str(app_data_dir).strip():
                app_data_dir = Path.cwd()
            preset_name = ""
            try:
                if getattr(project, "preset", None) is not None:
                    preset_name = str(getattr(project.preset, "name", "") or "").strip()
            except Exception:
                preset_name = ""
            watermark_context = build_watermark_context(
                project=project,
                preset_name=preset_name,
                min_rating=safe_min_rating,
                studio_profile=studio_profile,
                now_utc=datetime.utcnow(),
            )
            results: list[ExportProfileResult] = []
            exported_asset_ids: set[int] = set()
            total_steps = len(profiles) * len(assets)
            done_steps = 0
            cancelled = False

            for profile in profiles:
                if is_cancelled is not None and is_cancelled():
                    cancelled = True
                    break
                profile_cfg = profile_configs.get(profile, {})
                fmt = str(profile_cfg.get("format", "JPEG")).upper()
                max_width = int(profile_cfg.get("max_width", 0) or 0)
                quality = int(profile_cfg.get("quality", 90) or 90)
                subdir = str(profile_cfg.get("subdir", profile)).strip() or profile

                target_dir = output_root / subdir
                target_dir.mkdir(parents=True, exist_ok=True)

                run = ExportRun(
                    project_id=project.id,
                    profile=profile,
                    output_path=str(target_dir),
                    status="running",
                    started_at=datetime.utcnow(),
                )
                session.add(run)
                session.flush()

                exported_count = 0
                failed_count = 0
                messages: list[str] = []

                for asset in assets:
                    if is_cancelled is not None and is_cancelled():
                        cancelled = True
                        break
                    src = Path(asset.src_path)
                    try:
                        target_ext = FORMAT_EXTENSION.get(fmt, src.suffix.lower())
                        out_name = f"{src.stem}_{profile}{target_ext}"
                        dst = unique_path(target_dir / out_name)

                        self._export_one(
                            src=src,
                            dst=dst,
                            output_format=fmt,
                            max_width=max_width,
                            quality=quality,
                            watermark_cfg=watermark_cfg,
                            watermark_context=watermark_context,
                            app_data_dir=app_data_dir,
                            warning_cb=lambda warning: messages.append(f"[WARN] {src.name}: {warning}"),
                        )
                        exported_count += 1
                        exported_asset_ids.add(int(asset.id))
                    except Exception as exc:
                        failed_count += 1
                        messages.append(f"{src.name}: {exc}")
                    finally:
                        done_steps += 1
                        if progress_cb is not None:
                            progress_cb(done_steps, total_steps, f"{profile}:{src.name}")

                if cancelled:
                    run.exported_count = exported_count
                    run.failed_count = failed_count
                    run.ended_at = datetime.utcnow()
                    run.status = "cancelled"
                    run.message = "\n".join(messages[:100])
                    results.append(
                        ExportProfileResult(
                            profile=profile,
                            exported=exported_count,
                            failed=failed_count,
                            output_dir=target_dir,
                            status=run.status,
                            message=run.message,
                        )
                    )
                    break

                run.exported_count = exported_count
                run.failed_count = failed_count
                run.ended_at = datetime.utcnow()
                run.status = "completed" if failed_count == 0 else "completed_with_errors"
                run.message = "\n".join(messages[:100])
                results.append(
                    ExportProfileResult(
                        profile=profile,
                        exported=exported_count,
                        failed=failed_count,
                        output_dir=target_dir,
                        status=run.status,
                        message=run.message,
                    )
                )

            if not cancelled:
                try_transition_project_status(project, "pret_a_livrer")
                if exported_asset_ids:
                    by_id = {int(asset.id): asset for asset in assets}
                    for asset_id in exported_asset_ids:
                        asset = by_id.get(int(asset_id))
                        if asset is not None:
                            asset.workflow_state = "exported"
            session.commit()
            report_path = self._write_report(
                output_root=output_root,
                project=project,
                min_rating=safe_min_rating,
                results=results,
            ) if create_report and results else None
            contact_sheet_path = self._create_contact_sheet_pdf(
                output_root=output_root,
                project=project,
                results=results,
            ) if create_contact_sheet and results else None
            zip_path = self._create_delivery_zip(
                output_root=output_root,
                project=project,
                results=results,
                report_path=report_path,
                contact_sheet_path=contact_sheet_path,
            ) if create_zip and results else None
            return ExportBatchResult(
                profiles=results,
                zip_path=zip_path,
                report_path=report_path,
                contact_sheet_path=contact_sheet_path,
            )

    def _export_one(
        self,
        src: Path,
        dst: Path,
        output_format: str,
        max_width: int,
        quality: int,
        watermark_cfg: dict,
        watermark_context: dict[str, str],
        app_data_dir: Path,
        warning_cb=None,
    ) -> None:
        if not src.exists():
            raise FileNotFoundError(f"fichier manquant: {src}")

        if not PIL_AVAILABLE:
            shutil.copy2(src, dst.with_suffix(src.suffix.lower()))
            return

        try:
            with Image.open(src) as image:
                image = image.convert("RGBA")

                if max_width > 0 and image.width > max_width:
                    ratio = max_width / float(image.width)
                    new_height = max(1, int(image.height * ratio))
                    image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)

                if watermark_cfg.get("enabled"):
                    image, warnings = self._apply_watermark_layers(
                        image=image,
                        watermark_cfg=watermark_cfg,
                        watermark_context=watermark_context,
                        app_data_dir=app_data_dir,
                    )
                    if warning_cb is not None:
                        for warning in warnings:
                            warning_cb(str(warning))

                if output_format in {"JPEG", "JPG"}:
                    if image.mode in ("RGBA", "LA"):
                        # Flatten alpha channel for JPEG export.
                        base = Image.new("RGB", image.size, (255, 255, 255))
                        base.paste(image, mask=image.split()[-1])
                        image = base
                    else:
                        image = image.convert("RGB")
                    image.save(dst, format="JPEG", quality=quality, optimize=True)
                    return

                if output_format == "PNG":
                    image.save(dst, format="PNG", optimize=True)
                    return

                if output_format == "TIFF":
                    image.save(dst, format="TIFF")
                    return

                image.save(dst)
        except Exception:
            # Unsupported format/decoder path fallback.
            shutil.copy2(src, dst.with_suffix(src.suffix.lower()))

    def _apply_watermark_layers(
        self,
        *,
        image,
        watermark_cfg: dict,
        watermark_context: dict[str, str],
        app_data_dir: Path,
    ) -> tuple[object, list[str]]:
        warnings: list[str] = []
        base = image.convert("RGBA")
        order = list(watermark_cfg.get("render_order", []))
        if not order:
            order = ["logo", "text"]

        for layer_name in order:
            if layer_name == "logo":
                base, warn = self._apply_logo_layer(
                    image=base,
                    layer_cfg=watermark_cfg.get("logo", {}),
                    app_data_dir=app_data_dir,
                )
                if warn:
                    warnings.append(warn)
            elif layer_name == "text":
                base, warn = self._apply_text_layer(
                    image=base,
                    layer_cfg=watermark_cfg.get("text", {}),
                    context=watermark_context,
                )
                if warn:
                    warnings.append(warn)
        return base, warnings

    def _apply_text_layer(self, *, image, layer_cfg: dict, context: dict[str, str]) -> tuple[object, str | None]:
        if not bool(layer_cfg.get("enabled", False)):
            return image, None
        template = str(layer_cfg.get("template", "") or "")
        rendered = render_template(template, context)
        if not rendered:
            return image, None

        size_pct = self._to_float(layer_cfg.get("size_pct"), default=4.0, low=0.5, high=80.0)
        font_size = max(10, int(round(image.width * (size_pct / 100.0))))
        font = self._load_font(
            family=str(layer_cfg.get("font_family", "Sans")),
            size_px=font_size,
            bold=bool(layer_cfg.get("bold", False)),
            italic=bool(layer_cfg.get("italic", False)),
        )
        stroke_enabled = bool(layer_cfg.get("stroke_enabled", False))
        stroke_width = self._to_int(layer_cfg.get("stroke_width_px"), default=2, low=0, high=24) if stroke_enabled else 0
        color_rgb = self._parse_rgb(layer_cfg.get("color_hex"), fallback=(255, 255, 255))
        stroke_rgb = self._parse_rgb(layer_cfg.get("stroke_color_hex"), fallback=(0, 0, 0))

        probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        probe_draw = ImageDraw.Draw(probe)
        bbox = probe_draw.textbbox(
            (0, 0),
            rendered,
            font=font,
            stroke_width=stroke_width,
            align="left",
        )
        text_w = max(1, int(bbox[2] - bbox[0]))
        text_h = max(1, int(bbox[3] - bbox[1]))
        text_img = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(text_img)
        draw.text(
            (-bbox[0], -bbox[1]),
            rendered,
            font=font,
            fill=(color_rgb[0], color_rgb[1], color_rgb[2], 255),
            stroke_width=stroke_width,
            stroke_fill=(stroke_rgb[0], stroke_rgb[1], stroke_rgb[2], 255) if stroke_width > 0 else None,
            align="left",
        )
        angle = self._to_float(layer_cfg.get("angle_deg"), default=0.0, low=-180.0, high=180.0)
        if abs(angle) > 0.001:
            # Qt preview rotates clockwise for positive values while Pillow rotates
            # counter-clockwise, so invert here to keep export consistent with UI.
            text_img = text_img.rotate(-float(angle), expand=True, resample=Image.Resampling.BICUBIC)
        opacity = normalize_watermark_opacity_percentage(layer_cfg.get("opacity", 70), default=70)
        text_img = self._apply_layer_opacity(text_img, opacity)

        x, y = self._anchored_position(
            canvas_size=image.size,
            layer_size=text_img.size,
            anchor=str(layer_cfg.get("anchor", "bottom_right")),
            offset_x_pct=self._to_float(layer_cfg.get("offset_x_pct"), default=-2.0, low=-100.0, high=100.0),
            offset_y_pct=self._to_float(layer_cfg.get("offset_y_pct"), default=-2.0, low=-100.0, high=100.0),
        )
        image.alpha_composite(text_img, dest=(x, y))
        return image, None

    def _apply_logo_layer(self, *, image, layer_cfg: dict, app_data_dir: Path) -> tuple[object, str | None]:
        if not bool(layer_cfg.get("enabled", False)):
            return image, None
        rel_path = str(layer_cfg.get("asset_rel_path", "") or "").strip()
        logo_path = resolve_logo_asset_path(rel_path, app_data_dir=app_data_dir)
        if logo_path is None:
            return image, "Logo non valide (chemin hors stockage)."
        if not logo_path.exists():
            return image, f"Logo introuvable: {logo_path.name}"

        try:
            with Image.open(logo_path) as raw:
                logo = raw.convert("RGBA")
        except Exception:
            return image, f"Logo illisible: {logo_path.name}"

        size_pct = self._to_float(layer_cfg.get("size_pct"), default=12.0, low=0.5, high=100.0)
        target_width = max(8, int(round(image.width * (size_pct / 100.0))))
        if logo.width > 0 and logo.width != target_width:
            ratio = target_width / float(logo.width)
            target_height = max(1, int(round(logo.height * ratio)))
            logo = logo.resize((target_width, target_height), Image.Resampling.LANCZOS)

        angle = self._to_float(layer_cfg.get("angle_deg"), default=0.0, low=-180.0, high=180.0)
        if abs(angle) > 0.001:
            # Keep the same clockwise convention as the editor preview.
            logo = logo.rotate(-float(angle), expand=True, resample=Image.Resampling.BICUBIC)
        opacity = normalize_watermark_opacity_percentage(layer_cfg.get("opacity", 70), default=70)
        logo = self._apply_layer_opacity(logo, opacity)

        x, y = self._anchored_position(
            canvas_size=image.size,
            layer_size=logo.size,
            anchor=str(layer_cfg.get("anchor", "bottom_left")),
            offset_x_pct=self._to_float(layer_cfg.get("offset_x_pct"), default=2.0, low=-100.0, high=100.0),
            offset_y_pct=self._to_float(layer_cfg.get("offset_y_pct"), default=-2.0, low=-100.0, high=100.0),
        )
        image.alpha_composite(logo, dest=(x, y))
        return image, None

    @staticmethod
    def _normalize_opacity_percentage(value) -> int:
        return normalize_watermark_opacity_percentage(value, default=70)

    @staticmethod
    def _parse_rgb(value, *, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        try:
            parsed = ImageColor.getrgb(str(value or ""))
            return int(parsed[0]), int(parsed[1]), int(parsed[2])
        except Exception:
            return fallback

    @staticmethod
    def _to_float(value, *, default: float, low: float, high: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            parsed = float(default)
        if parsed < low:
            return low
        if parsed > high:
            return high
        return parsed

    @staticmethod
    def _to_int(value, *, default: int, low: int, high: int) -> int:
        try:
            parsed = int(float(value))
        except Exception:
            parsed = int(default)
        if parsed < low:
            return low
        if parsed > high:
            return high
        return parsed

    @staticmethod
    def _apply_layer_opacity(layer, opacity_percent: int):
        alpha = layer.getchannel("A")
        ratio = max(0.0, min(1.0, float(opacity_percent) / 100.0))
        alpha = alpha.point(lambda px: int(math.floor(px * ratio)))
        out = layer.copy()
        out.putalpha(alpha)
        return out

    @staticmethod
    def _anchored_position(
        *,
        canvas_size: tuple[int, int],
        layer_size: tuple[int, int],
        anchor: str,
        offset_x_pct: float,
        offset_y_pct: float,
    ) -> tuple[int, int]:
        canvas_w, canvas_h = canvas_size
        layer_w, layer_h = layer_size
        mapping = {
            "top_left": (0, 0),
            "top_center": ((canvas_w - layer_w) / 2, 0),
            "top_right": (canvas_w - layer_w, 0),
            "center_left": (0, (canvas_h - layer_h) / 2),
            "center": ((canvas_w - layer_w) / 2, (canvas_h - layer_h) / 2),
            "center_right": (canvas_w - layer_w, (canvas_h - layer_h) / 2),
            "bottom_left": (0, canvas_h - layer_h),
            "bottom_center": ((canvas_w - layer_w) / 2, canvas_h - layer_h),
            "bottom_right": (canvas_w - layer_w, canvas_h - layer_h),
        }
        base_x, base_y = mapping.get(anchor, mapping["bottom_right"])
        x = int(round(base_x + (canvas_w * (offset_x_pct / 100.0))))
        y = int(round(base_y + (canvas_h * (offset_y_pct / 100.0))))
        return x, y

    @staticmethod
    def _load_font(*, family: str, size_px: int, bold: bool, italic: bool):
        if ImageFont is None:
            return None
        clean_family = str(family or "Sans").strip().lower()
        if clean_family in {"sans", "dejavu", "arial"}:
            if bold and italic:
                candidates = ["DejaVuSans-BoldOblique.ttf", "arialbi.ttf", "Arial Bold Italic.ttf"]
            elif bold:
                candidates = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"]
            elif italic:
                candidates = ["DejaVuSans-Oblique.ttf", "ariali.ttf", "Arial Italic.ttf"]
            else:
                candidates = ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"]
        else:
            candidates = [f"{family}.ttf", "DejaVuSans.ttf"]
        for font_name in candidates:
            try:
                return ImageFont.truetype(font_name, size=max(8, int(size_px)))
            except Exception:
                continue
        try:
            return ImageFont.load_default()
        except Exception:
            return None

    @staticmethod
    def _write_report(
        output_root: Path,
        project: Project,
        min_rating: int,
        results: list[ExportProfileResult],
    ) -> Path:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        report_path = output_root / f"export_report_{stamp}.txt"

        lines = [
            "PhotoHub Export Report",
            f"project_id={project.id}",
            f"project_name={project.name}",
            f"generated_at_utc={datetime.utcnow().isoformat()}",
            f"min_rating={min_rating}",
            "",
        ]
        for item in results:
            lines.append(
                f"profile={item.profile}; status={item.status}; "
                f"exported={item.exported}; failed={item.failed}; out={item.output_dir}"
            )
            if item.message:
                lines.append(f"errors={item.message}")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    @staticmethod
    def _create_delivery_zip(
        output_root: Path,
        project: Project,
        results: list[ExportProfileResult],
        report_path: Path | None,
        contact_sheet_path: Path | None,
    ) -> Path:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() else "_" for c in project.name).strip("_") or "project"
        zip_path = output_root / f"{safe_name}_{stamp}_delivery.zip"

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in results:
                if not item.output_dir.exists():
                    continue
                for file_path in item.output_dir.rglob("*"):
                    if not file_path.is_file():
                        continue
                    archive.write(file_path, arcname=str(file_path.relative_to(output_root)))
            if report_path is not None and report_path.exists():
                archive.write(report_path, arcname=report_path.name)
            if contact_sheet_path is not None and contact_sheet_path.exists():
                archive.write(contact_sheet_path, arcname=contact_sheet_path.name)

        return zip_path

    @staticmethod
    def _create_contact_sheet_pdf(
        output_root: Path,
        project: Project,
        results: list[ExportProfileResult],
    ) -> Path | None:
        if not PIL_AVAILABLE:
            return None

        source_files: list[Path] = []
        for item in results:
            if not item.output_dir.exists():
                continue
            source_files = sorted([p for p in item.output_dir.rglob("*") if p.is_file()])
            if source_files:
                break
        if not source_files:
            return None

        page_width = 1240
        page_height = 1754
        cols = 3
        rows = 4
        margin = 40
        gutter = 20
        label_height = 28
        items_per_page = cols * rows

        cell_width = (page_width - (2 * margin) - ((cols - 1) * gutter)) // cols
        cell_height = (page_height - (2 * margin) - ((rows - 1) * gutter)) // rows
        thumb_height = max(80, cell_height - label_height - 8)

        pages = []
        for start in range(0, len(source_files), items_per_page):
            chunk = source_files[start : start + items_per_page]
            page = Image.new("RGB", (page_width, page_height), "white")
            draw = ImageDraw.Draw(page)

            for idx, file_path in enumerate(chunk):
                col = idx % cols
                row = idx // cols
                x = margin + col * (cell_width + gutter)
                y = margin + row * (cell_height + gutter)

                try:
                    with Image.open(file_path) as raw:
                        image = raw.convert("RGB")
                        image.thumbnail((cell_width, thumb_height), Image.Resampling.LANCZOS)
                except Exception:
                    continue

                x_offset = x + (cell_width - image.width) // 2
                y_offset = y + (thumb_height - image.height) // 2
                page.paste(image, (x_offset, y_offset))

                label = file_path.name
                if len(label) > 38:
                    label = label[:35] + "..."
                draw.text((x, y + thumb_height + 6), label, fill=(0, 0, 0))

            pages.append(page)

        if not pages:
            return None

        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() else "_" for c in project.name).strip("_") or "project"
        pdf_path = output_root / f"{safe_name}_{stamp}_contact_sheet.pdf"

        first, rest = pages[0], pages[1:]
        first.save(pdf_path, format="PDF", save_all=True, append_images=rest, resolution=150.0)
        for page in pages:
            page.close()
        return pdf_path
