from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..services.watermark_assets import import_logo, resolve_logo_asset_path
from ..services.watermarks import ANCHOR_ORDER, VARIABLE_CATALOG, normalize_watermark_config, render_template

ANCHOR_LABELS = {
    "top_left": "Haut gauche",
    "top_center": "Haut centre",
    "top_right": "Haut droite",
    "center_left": "Centre gauche",
    "center": "Centre",
    "center_right": "Centre droite",
    "bottom_left": "Bas gauche",
    "bottom_center": "Bas centre",
    "bottom_right": "Bas droite",
}


def _normalize_hex(value: str, fallback: str) -> str:
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


def _anchored(canvas_w: int, canvas_h: int, layer_w: float, layer_h: float, anchor: str, ox: float, oy: float) -> tuple[float, float]:
    mapping = {
        "top_left": (0.0, 0.0),
        "top_center": ((canvas_w - layer_w) / 2.0, 0.0),
        "top_right": (canvas_w - layer_w, 0.0),
        "center_left": (0.0, (canvas_h - layer_h) / 2.0),
        "center": ((canvas_w - layer_w) / 2.0, (canvas_h - layer_h) / 2.0),
        "center_right": (canvas_w - layer_w, (canvas_h - layer_h) / 2.0),
        "bottom_left": (0.0, canvas_h - layer_h),
        "bottom_center": ((canvas_w - layer_w) / 2.0, canvas_h - layer_h),
        "bottom_right": (canvas_w - layer_w, canvas_h - layer_h),
    }
    bx, by = mapping.get(anchor, mapping["bottom_right"])
    return bx + (canvas_w * (ox / 100.0)), by + (canvas_h * (oy / 100.0))


class WatermarkPreview(QWidget):
    offsetsDragged = Signal(float, float)

    def __init__(self, *, config: dict, app_data_dir: Path, context: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self._app_data_dir = Path(app_data_dir)
        self._context = dict(context)
        self._config = normalize_watermark_config(config)
        self._active_layer = "text"
        self._image = self._placeholder()
        self._layer_rects: dict[str, QRectF] = {}
        self._display_rect = QRectF()
        self._scale = 1.0
        self._dragging = False
        self._drag_origin = QPoint()
        self._drag_start = (0.0, 0.0)

    def set_config(self, config: dict) -> None:
        self._config = normalize_watermark_config(config)
        self.update()

    def set_active_layer(self, layer: str) -> None:
        self._active_layer = "logo" if str(layer).lower() == "logo" else "text"
        self.update()

    def set_preview_image(self, path: str | Path | None) -> None:
        if path:
            image = QImage(str(Path(path)))
            if not image.isNull():
                self._image = image
                self.update()
                return
        self._image = self._placeholder()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0F1115"))
        if self._image.isNull():
            return
        canvas = self._image.copy()
        self._layer_rects = {}
        cp = QPainter(canvas)
        cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._config.get("enabled"):
            order = list(self._config.get("render_order", [])) or ["logo", "text"]
            for name in order:
                if name == "text":
                    rect = self._draw_text(cp, canvas.width(), canvas.height())
                    if rect is not None:
                        self._layer_rects["text"] = rect
                elif name == "logo":
                    rect = self._draw_logo(cp, canvas.width(), canvas.height())
                    if rect is not None:
                        self._layer_rects["logo"] = rect
        cp.end()
        self._display_rect = self._fit(canvas.width(), canvas.height())
        self._scale = self._display_rect.width() / max(1.0, float(canvas.width()))
        painter.drawImage(self._display_rect, canvas)
        selected = self._layer_rects.get(self._active_layer)
        if selected is not None:
            box = self._map(selected)
            pen = QPen(QColor("#10B981"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(box)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        rect = self._layer_rects.get(self._active_layer)
        if rect is None:
            return
        if self._map(rect).contains(event.position()):
            self._dragging = True
            self._drag_origin = event.position().toPoint()
            cfg = self._config.get(self._active_layer, {})
            self._drag_start = (float(cfg.get("offset_x_pct", 0.0)), float(cfg.get("offset_y_pct", 0.0)))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return super().mouseMoveEvent(event)
        delta = event.position().toPoint() - self._drag_origin
        scale = max(0.0001, self._scale)
        dx = float(delta.x()) / scale
        dy = float(delta.y()) / scale
        x0, y0 = self._drag_start
        x = max(-100.0, min(100.0, x0 + (dx / max(1.0, float(self._image.width())) * 100.0)))
        y = max(-100.0, min(100.0, y0 + (dy / max(1.0, float(self._image.height())) * 100.0)))
        self.offsetsDragged.emit(float(x), float(y))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
        return super().mouseReleaseEvent(event)

    def _draw_text(self, painter: QPainter, w: int, h: int) -> QRectF | None:
        cfg = self._config.get("text", {})
        if not cfg.get("enabled"):
            return None
        text = render_template(str(cfg.get("template", "")), self._context)
        if not text:
            return None
        font = QFont(str(cfg.get("font_family", "Sans")))
        font.setBold(bool(cfg.get("bold", False)))
        font.setItalic(bool(cfg.get("italic", False)))
        font.setPixelSize(max(8, int(round(w * (float(cfg.get("size_pct", 4.0)) / 100.0)))))
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = max(1, fm.horizontalAdvance(text))
        th = max(1, fm.height())
        angle = float(cfg.get("angle_deg", 0.0))
        rad = math.radians(angle)
        bw = abs(tw * math.cos(rad)) + abs(th * math.sin(rad))
        bh = abs(tw * math.sin(rad)) + abs(th * math.cos(rad))
        x, y = _anchored(w, h, bw, bh, str(cfg.get("anchor", "bottom_right")), float(cfg.get("offset_x_pct", -2.0)), float(cfg.get("offset_y_pct", -2.0)))
        color = QColor(_normalize_hex(str(cfg.get("color_hex", "#FFFFFF")), "#FFFFFF"))
        color.setAlpha(int(round(max(0, min(100, int(float(cfg.get("opacity", 70))))) / 100.0 * 255)))
        painter.save()
        painter.translate(x + bw / 2.0, y + bh / 2.0)
        painter.rotate(angle)
        painter.translate(-tw / 2.0, th / 2.0)
        painter.setPen(color)
        painter.drawText(0, 0, text)
        painter.restore()
        return QRectF(x, y, bw, bh)

    def _draw_logo(self, painter: QPainter, w: int, h: int) -> QRectF | None:
        cfg = self._config.get("logo", {})
        if not cfg.get("enabled"):
            return None
        logo_path = resolve_logo_asset_path(str(cfg.get("asset_rel_path", "")), self._app_data_dir)
        if logo_path is None or (not logo_path.exists()):
            return None
        logo = QImage(str(logo_path))
        if logo.isNull():
            return None
        target_w = max(8, int(round(w * (float(cfg.get("size_pct", 12.0)) / 100.0))))
        ratio = target_w / max(1.0, float(logo.width()))
        target_h = max(1, int(round(logo.height() * ratio)))
        logo = logo.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        angle = float(cfg.get("angle_deg", 0.0))
        rad = math.radians(angle)
        bw = abs(logo.width() * math.cos(rad)) + abs(logo.height() * math.sin(rad))
        bh = abs(logo.width() * math.sin(rad)) + abs(logo.height() * math.cos(rad))
        x, y = _anchored(w, h, bw, bh, str(cfg.get("anchor", "bottom_left")), float(cfg.get("offset_x_pct", 2.0)), float(cfg.get("offset_y_pct", -2.0)))
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, float(cfg.get("opacity", 70)) / 100.0)))
        painter.translate(x + bw / 2.0, y + bh / 2.0)
        painter.rotate(angle)
        painter.translate(-logo.width() / 2.0, -logo.height() / 2.0)
        painter.drawImage(0, 0, logo)
        painter.restore()
        return QRectF(x, y, bw, bh)

    def _fit(self, sw: int, sh: int) -> QRectF:
        area = self.rect().adjusted(8, 8, -8, -8)
        scale = min(area.width() / max(1.0, float(sw)), area.height() / max(1.0, float(sh)))
        dw = sw * scale
        dh = sh * scale
        return QRectF(area.left() + (area.width() - dw) / 2.0, area.top() + (area.height() - dh) / 2.0, dw, dh)

    def _map(self, rect: QRectF) -> QRectF:
        return QRectF(
            self._display_rect.left() + rect.left() * self._scale,
            self._display_rect.top() + rect.top() * self._scale,
            max(1.0, rect.width() * self._scale),
            max(1.0, rect.height() * self._scale),
        )

    @staticmethod
    def _placeholder() -> QImage:
        image = QImage(1600, 1000, QImage.Format.Format_ARGB32)
        image.fill(QColor("#353942"))
        painter = QPainter(image)
        painter.setPen(QColor("#C7CBD6"))
        font = QFont("Sans")
        font.setPixelSize(34)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, 1600, 1000), Qt.AlignmentFlag.AlignCenter, "Preview Photo Placeholder")
        painter.end()
        return image


class WatermarkEditorDialog(QDialog):
    def __init__(self, *, config: dict, app_data_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editeur Watermark")
        self.resize(1120, 720)
        self._app_data_dir = Path(app_data_dir)
        self._cfg = normalize_watermark_config(config)
        self._active = "text"
        self._loading = False
        self._ctx = {k: f"{label}" for k, label in VARIABLE_CATALOG}
        self._ctx.update({"shoot_date": "2026-02-14", "export_date": "2026-02-14", "rating_min": "0"})

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        self.enabled_check = QCheckBox("Activer watermark")
        self.enabled_check.setChecked(bool(self._cfg.get("enabled", False)))
        self.enabled_check.toggled.connect(lambda v: self._set_global_enabled(v))
        pick_preview = QPushButton("Charger image preview")
        pick_preview.clicked.connect(self._pick_preview)
        reset_preview = QPushButton("Placeholder")
        reset_preview.clicked.connect(lambda: self.preview.set_preview_image(None))
        header.addWidget(self.enabled_check)
        header.addStretch(1)
        header.addWidget(pick_preview)
        header.addWidget(reset_preview)
        root.addLayout(header)

        content = QHBoxLayout()
        self.preview = WatermarkPreview(config=self._cfg, app_data_dir=self._app_data_dir, context=self._ctx)
        self.preview.offsetsDragged.connect(self._on_drag_offsets)
        content.addWidget(self.preview, 3)

        right = QVBoxLayout()
        layer_box = QGroupBox("Calque")
        form = QFormLayout(layer_box)
        self.layer_combo = QComboBox()
        self.layer_combo.addItem("Texte", userData="text")
        self.layer_combo.addItem("Logo", userData="logo")
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        self.layer_enabled = QCheckBox("Actif")
        self.layer_enabled.toggled.connect(lambda v: self._set_layer("enabled", bool(v)))
        self.anchor_combo = QComboBox()
        for key in ANCHOR_ORDER:
            self.anchor_combo.addItem(ANCHOR_LABELS.get(key, key), userData=key)
        self.anchor_combo.currentIndexChanged.connect(lambda: self._set_layer("anchor", str(self.anchor_combo.currentData() or "center")))
        self.offset_x = QDoubleSpinBox(); self.offset_x.setRange(-100, 100); self.offset_x.setSuffix("%"); self.offset_x.valueChanged.connect(lambda _v: self._set_offsets())
        self.offset_y = QDoubleSpinBox(); self.offset_y.setRange(-100, 100); self.offset_y.setSuffix("%"); self.offset_y.valueChanged.connect(lambda _v: self._set_offsets())
        self.size_pct = QDoubleSpinBox(); self.size_pct.setRange(0.5, 100); self.size_pct.setSuffix("%"); self.size_pct.valueChanged.connect(lambda v: self._set_layer("size_pct", float(v)))
        self.angle = QDoubleSpinBox(); self.angle.setRange(-180, 180); self.angle.setSuffix(" deg"); self.angle.valueChanged.connect(lambda v: self._set_layer("angle_deg", float(v)))
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(0, 100); self.opacity.valueChanged.connect(self._on_opacity)
        self.opacity_label = QLabel("70%")
        op_row = QHBoxLayout(); op_row.addWidget(self.opacity, 1); op_row.addWidget(self.opacity_label)
        form.addRow("Type", self.layer_combo); form.addRow("", self.layer_enabled); form.addRow("Ancre", self.anchor_combo)
        form.addRow("Offset X", self.offset_x); form.addRow("Offset Y", self.offset_y); form.addRow("Taille", self.size_pct); form.addRow("Angle", self.angle); form.addRow("Opacite", op_row)
        right.addWidget(layer_box)

        self.text_box = QGroupBox("Texte")
        t = QFormLayout(self.text_box)
        self.template_edit = QLineEdit(); self.template_edit.textChanged.connect(lambda v: self._set_text("template", str(v)))
        self.font_family = QComboBox(); self.font_family.addItems(["Sans", "Serif", "Monospace"]); self.font_family.currentIndexChanged.connect(lambda: self._set_text("font_family", self.font_family.currentText()))
        self.bold = QCheckBox("Gras"); self.bold.toggled.connect(lambda v: self._set_text("bold", bool(v)))
        self.italic = QCheckBox("Italique"); self.italic.toggled.connect(lambda v: self._set_text("italic", bool(v)))
        self.text_color = QLineEdit(); self.text_color.editingFinished.connect(lambda: self._set_text_color())
        pick_text_color = QPushButton("Couleur texte"); pick_text_color.clicked.connect(self._pick_text_color)
        self.stroke_on = QCheckBox("Contour"); self.stroke_on.toggled.connect(lambda v: self._set_text("stroke_enabled", bool(v)))
        self.stroke_color = QLineEdit(); self.stroke_color.editingFinished.connect(lambda: self._set_stroke_color())
        pick_stroke = QPushButton("Couleur contour"); pick_stroke.clicked.connect(self._pick_stroke_color)
        self.stroke_width = QSpinBox(); self.stroke_width.setRange(0, 24); self.stroke_width.valueChanged.connect(lambda v: self._set_text("stroke_width_px", int(v)))
        var_row = QHBoxLayout(); self.var_combo = QComboBox(); [self.var_combo.addItem(f"{label} ({key})", userData=key) for key, label in VARIABLE_CATALOG]
        ins = QPushButton("Inserer variable"); ins.clicked.connect(self._insert_variable); var_row.addWidget(self.var_combo, 1); var_row.addWidget(ins)
        t.addRow("Template", self.template_edit); t.addRow("Font", self.font_family); t.addRow("", self.bold); t.addRow("", self.italic)
        t.addRow("Couleur", self.text_color); t.addRow("", pick_text_color); t.addRow("", self.stroke_on); t.addRow("Contour", self.stroke_color); t.addRow("", pick_stroke); t.addRow("Epaisseur", self.stroke_width); t.addRow("Variables", var_row)
        right.addWidget(self.text_box)

        self.logo_box = QGroupBox("Logo")
        lf = QFormLayout(self.logo_box)
        self.logo_asset = QLineEdit(); self.logo_asset.setReadOnly(True)
        pick_logo = QPushButton("Importer logo"); pick_logo.clicked.connect(self._pick_logo)
        lf.addRow("Asset", self.logo_asset); lf.addRow("", pick_logo)
        right.addWidget(self.logo_box)
        right.addStretch(1)
        content.addLayout(right, 2)
        root.addLayout(content, 1)

        footer = QHBoxLayout(); footer.addStretch(1)
        cancel_btn = QPushButton("Annuler"); cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Valider"); ok_btn.clicked.connect(self.accept)
        footer.addWidget(cancel_btn); footer.addWidget(ok_btn)
        root.addLayout(footer)
        self._load_controls()

    def get_config(self) -> dict:
        return normalize_watermark_config(self._cfg)

    def _set_global_enabled(self, value: bool) -> None:
        self._cfg["enabled"] = bool(value); self.preview.set_config(self._cfg)

    def _on_layer_changed(self) -> None:
        self._active = str(self.layer_combo.currentData() or "text"); self._load_controls()

    def _set_layer(self, key: str, value) -> None:
        if self._loading:
            return
        self._cfg[self._active][key] = value
        self.preview.set_active_layer(self._active)
        self.preview.set_config(self._cfg)

    def _set_offsets(self) -> None:
        self._set_layer("offset_x_pct", float(self.offset_x.value())); self._set_layer("offset_y_pct", float(self.offset_y.value()))

    def _on_opacity(self, value: int) -> None:
        self.opacity_label.setText(f"{int(value)}%"); self._set_layer("opacity", int(value))

    def _set_text(self, key: str, value) -> None:
        if self._loading:
            return
        self._cfg["text"][key] = value; self.preview.set_config(self._cfg)

    def _set_text_color(self) -> None:
        self._cfg["text"]["color_hex"] = _normalize_hex(self.text_color.text(), "#FFFFFF"); self.text_color.setText(self._cfg["text"]["color_hex"]); self.preview.set_config(self._cfg)

    def _set_stroke_color(self) -> None:
        self._cfg["text"]["stroke_color_hex"] = _normalize_hex(self.stroke_color.text(), "#000000"); self.stroke_color.setText(self._cfg["text"]["stroke_color_hex"]); self.preview.set_config(self._cfg)

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(QColor(_normalize_hex(self.text_color.text(), "#FFFFFF")), self, "Couleur texte")
        if color.isValid():
            self.text_color.setText(color.name().upper()); self._set_text_color()

    def _pick_stroke_color(self) -> None:
        color = QColorDialog.getColor(QColor(_normalize_hex(self.stroke_color.text(), "#000000")), self, "Couleur contour")
        if color.isValid():
            self.stroke_color.setText(color.name().upper()); self._set_stroke_color()

    def _insert_variable(self) -> None:
        key = str(self.var_combo.currentData() or "").strip()
        token = f"{{{{{key}}}}}"
        cur = self.template_edit.cursorPosition(); txt = self.template_edit.text()
        self.template_edit.setText(txt[:cur] + token + txt[cur:]); self.template_edit.setCursorPosition(cur + len(token))

    def _pick_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Selectionner logo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if not path:
            return
        try:
            rel = import_logo(path, self._app_data_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur logo", str(exc))
            return
        self._cfg["logo"]["asset_rel_path"] = rel; self.logo_asset.setText(rel); self.preview.set_config(self._cfg)

    def _pick_preview(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Selectionner image preview", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if path:
            self.preview.set_preview_image(path)

    def _on_drag_offsets(self, x: float, y: float) -> None:
        self._loading = True
        try:
            self.offset_x.setValue(float(x)); self.offset_y.setValue(float(y))
        finally:
            self._loading = False
        self._set_offsets()

    def _load_controls(self) -> None:
        self._loading = True
        try:
            self.enabled_check.setChecked(bool(self._cfg.get("enabled", False)))
            self.preview.set_active_layer(self._active)
            self.preview.set_config(self._cfg)
            layer = self._cfg.get(self._active, {})
            self.layer_enabled.setChecked(bool(layer.get("enabled", False)))
            i = self.anchor_combo.findData(str(layer.get("anchor", "center"))); self.anchor_combo.setCurrentIndex(max(0, i))
            self.offset_x.setValue(float(layer.get("offset_x_pct", 0.0))); self.offset_y.setValue(float(layer.get("offset_y_pct", 0.0)))
            self.size_pct.setValue(float(layer.get("size_pct", 4.0))); self.angle.setValue(float(layer.get("angle_deg", 0.0))); self.opacity.setValue(int(float(layer.get("opacity", 70))))
            self.opacity_label.setText(f"{self.opacity.value()}%")
            text = self._cfg.get("text", {})
            self.template_edit.setText(str(text.get("template", "")))
            idx_font = self.font_family.findText(str(text.get("font_family", "Sans"))); self.font_family.setCurrentIndex(max(0, idx_font))
            self.bold.setChecked(bool(text.get("bold", False))); self.italic.setChecked(bool(text.get("italic", False)))
            self.text_color.setText(_normalize_hex(str(text.get("color_hex", "#FFFFFF")), "#FFFFFF"))
            self.stroke_on.setChecked(bool(text.get("stroke_enabled", True))); self.stroke_color.setText(_normalize_hex(str(text.get("stroke_color_hex", "#000000")), "#000000")); self.stroke_width.setValue(int(float(text.get("stroke_width_px", 2))))
            self.logo_asset.setText(str(self._cfg.get("logo", {}).get("asset_rel_path", "")))
            self.text_box.setVisible(self._active == "text"); self.logo_box.setVisible(self._active == "logo")
        finally:
            self._loading = False
