from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from ..models import Asset, ExportRun, Project
from .projects import try_transition_project_status
from .presets import resolve_effective_config_for_project_model
from ..utils import unique_path

try:
    from PIL import Image, ImageDraw

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency fallback
    PIL_AVAILABLE = False
    Image = None
    ImageDraw = None


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
            watermark_cfg = config.get("watermark", {})
            results: list[ExportProfileResult] = []
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
                        )
                        exported_count += 1
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

                if watermark_cfg.get("enabled") and str(watermark_cfg.get("text", "")).strip():
                    opacity_pct = self._normalize_opacity_percentage(watermark_cfg.get("opacity", 70))
                    image = self._apply_watermark(
                        image,
                        text=str(watermark_cfg["text"]),
                        opacity_percent=opacity_pct,
                    )

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

    @staticmethod
    def _apply_watermark(image, text: str, opacity_percent: int):
        overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        x = 24
        y = max(24, image.height - 48)
        alpha = max(0, min(255, int(round((opacity_percent / 100.0) * 255))))
        draw.text((x, y), text, fill=(255, 255, 255, alpha))
        return Image.alpha_composite(image, overlay)

    @staticmethod
    def _normalize_opacity_percentage(value) -> int:
        try:
            raw = int(float(value))
        except Exception:
            return 70
        if raw < 0:
            return 0
        # Backward compatibility: older presets stored 0..255.
        if raw > 100:
            raw = int(round((raw / 255.0) * 100))
        return max(0, min(100, raw))

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
