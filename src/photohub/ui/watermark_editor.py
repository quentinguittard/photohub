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
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QToolButton,
    QMenu,
    QButtonGroup,
)
from PySide6.QtGui import QAction, QFontDatabase
from .components import BentoCard

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


def _adjust_color(hex_color: str, amount: int) -> str:
    """amount > 100 is lighter, < 100 is darker"""
    color = QColor(hex_color)
    if not color.isValid():
        return hex_color
    if amount > 100:
        return color.lighter(amount).name().upper()
    else:
        return color.darker(200 - amount).name().upper()


def _rgba(hex_color: str, alpha: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _get_contrast_color(hex_color: str) -> str:
    """Returns #FFFFFF or #000000 based on brightness of hex_color"""
    c = QColor(hex_color)
    luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#000000" if luma > 150 else "#FFFFFF"


def _map_font_family(family: str) -> str:
    f = family.lower()
    if "serif" in f: return "Times New Roman"
    if "mono" in f: return "Consolas"
    return "Segoe UI"


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
        font_family = _map_font_family(str(cfg.get("font_family", "Sans")))
        font = QFont(font_family)
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
    def __init__(self, *, config: dict, app_data_dir: Path, accent_color: str = "#10B981", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuration de la Signature Visuelle")
        self.resize(1150, 750)
        self._app_data_dir = Path(app_data_dir)
        self._cfg = normalize_watermark_config(config)
        self._accent = _normalize_hex(accent_color, "#10B981")
        self._active = "text"
        self._loading = False
        self._ctx = {k: f"{label}" for k, label in VARIABLE_CATALOG}
        self._ctx.update({"shoot_date": "2026-02-14", "export_date": "2026-02-14", "rating_min": "0"})

        # Derive colors
        accent_hover = _adjust_color(self._accent, 115)
        accent_text = _get_contrast_color(self._accent)
        
        self.setStyleSheet(f"""
            QDialog {{ background-color: #121212; }}
            QLabel {{ color: #E8E8E8; font-size: 11px; font-weight: 500; }}
            QLabel#Muted {{ color: #7A7A7A; font-size: 10px; }}
            
            /* Sidebar and Cards */
            QFrame#SettingsSidebar {{ 
                background-color: #1A1A1A; 
                border-left: 1px solid #333; 
            }}
            
            /* Inputs */
            QLineEdit, QSpinBox, QComboBox {{
                background: #121212;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 6px;
                color: #E8E8E8;
            }}
            QLineEdit:focus {{ border-color: #555; }}
            QLineEdit#PathDisplay {{
                background: #0F0F0F;
                border: 1px dashed #333;
                color: #B2B2B2;
                font-size: 10px;
                padding: 4px 8px;
            }}
            
            /* Buttons */
            QPushButton {{
                background: #2D2D2D;
                border: 1px solid #3A3A3A;
                border-radius: 6px;
                padding: 8px 12px;
                color: #E8E8E8;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #353535; border-color: #545454; }}
            QPushButton#Primary {{ background: {self._accent}; border: none; color: {accent_text}; }}
            QPushButton#Primary:hover {{ background: {accent_hover}; }}
            
            /* Common Variable Chips */
            QPushButton#VarChip {{
                background: #242424;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 4px 8px;
                color: #B2B2B2;
                font-size: 9px;
                font-weight: 700;
                text-transform: uppercase;
            }}
            QPushButton#VarChip:hover {{ background: #2D2D2D; border-color: #555; coloe: #E8E8E8; }}
            
            /* Style Toggle Buttons */
            QToolButton#ModeBtn {{
                background: #242424;
                border: 1px solid #333;
                padding: 10px;
                border-radius: 8px;
                color: #B2B2B2;
                font-weight: 700;
                font-size: 11px;
            }}
            QToolButton#ModeBtn:checked {{
                background: {self._accent};
                border-color: {self._accent};
                color: {accent_text};
            }}
            
            /* Anchor Buttons Grid */
            QToolButton#AnchorBtn {{
                background: #121212;
                border: 1px solid #333;
                border-radius: 4px;
                width: 28px;
                height: 28px;
            }}
            QToolButton#AnchorBtn:hover {{ background: #222; }}
            QToolButton#AnchorBtn:checked {{ background: {self._accent}; border-color: {self._accent}; }}

            /* Sliders */
            QSlider::groove:horizontal {{
                height: 4px;
                background: #121212;
                border: 1px solid #333;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #E8E8E8;
                border: 1px solid #333;
                width: 14px;
                height: 14px;
                margin: -6px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {self._accent}; border-radius: 2px; }}
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 1. PREVIEW PANEL (Left) ──
        preview_panel = QWidget()
        preview_v = QVBoxLayout(preview_panel)
        preview_v.setContentsMargins(40, 40, 40, 40)
        preview_v.setSpacing(30)

        nav = QHBoxLayout()
        self.enabled_check = QCheckBox("Activer le marquage visuel")
        self.enabled_check.setStyleSheet("font-size: 14px; font-weight: 600; color: #10B981;")
        self.enabled_check.setChecked(bool(self._cfg.get("enabled", False)))
        self.enabled_check.toggled.connect(self._set_global_enabled)
        nav.addWidget(self.enabled_check)
        nav.addStretch(1)
        
        pick_img = QPushButton("Changer l'image de test")
        pick_img.clicked.connect(self._pick_preview)
        nav.addWidget(pick_img)
        preview_v.addLayout(nav)

        self.preview = WatermarkPreview(config=self._cfg, app_data_dir=self._app_data_dir, context=self._ctx)
        self.preview.offsetsDragged.connect(self._on_drag_offsets)
        preview_v.addWidget(self.preview, 1)
        
        hint = QLabel("💡 Astuce : Glissez le texte ou le logo à la souris pour ajuster la position librement.")
        hint.setObjectName("Muted")
        preview_v.addWidget(hint)
        
        main_layout.addWidget(preview_panel, 1)

        # ── 2. SETTINGS SIDEBAR (Right) ──
        sidebar = QFrame()
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setFixedWidth(380)
        side_v = QVBoxLayout(sidebar)
        side_v.setContentsMargins(24, 24, 24, 24)
        side_v.setSpacing(20)

        # Mode Selector
        mode_box = QHBoxLayout()
        self.btn_text = QToolButton(); self.btn_text.setText("TEXTE"); self.btn_text.setCheckable(True); self.btn_text.setChecked(True)
        self.btn_logo = QToolButton(); self.btn_logo.setText("LOGO"); self.btn_logo.setCheckable(True)
        self.btn_text.setObjectName("ModeBtn"); self.btn_logo.setObjectName("ModeBtn")
        self.btn_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); self.btn_logo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.btn_text); self.mode_group.addButton(self.btn_logo)
        self.mode_group.buttonClicked.connect(self._on_mode_switched)
        mode_box.addWidget(self.btn_text); mode_box.addWidget(self.btn_logo)
        side_v.addLayout(mode_box)

        # Content Card
        self.content_card = BentoCard("Personnalisation")
        self.content_stack = QStackedWidget()
        
        # - Text Layout
        self.text_widget = QWidget()
        text_v = QVBoxLayout(self.text_widget); text_v.setContentsMargins(0, 0, 0, 0); text_v.setSpacing(12)
        text_v.addWidget(QLabel("TEXTE & VARIABLES"))
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("ex: © {photographer}")
        self.template_edit.textChanged.connect(lambda v: self._set_text("template", str(v)))
        text_v.addWidget(self.template_edit)
        
        # Quick Chips
        chips_layout = QHBoxLayout(); chips_layout.setSpacing(4)
        for key in ["project_name", "photographer", "shoot_date", "seq"]:
            chip = QPushButton(key.replace("_", " ")); chip.setObjectName("VarChip")
            chip.clicked.connect(lambda _=False, k=key: self._insert_variable_key(k))
            chips_layout.addWidget(chip)
        chips_layout.addStretch(1)
        text_v.addLayout(chips_layout)
        
        style_row = QHBoxLayout()
        self.font_combo = QComboBox(); self.font_combo.addItems(["Sans", "Serif", "Monospace"]); self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        self.color_btn = QPushButton("Couleur..."); self.color_btn.clicked.connect(self._pick_text_color)
        style_row.addWidget(self.font_combo, 2); style_row.addWidget(self.color_btn, 1)
        text_v.addLayout(style_row)
        self.content_stack.addWidget(self.text_widget)
        
        # - Logo Layout
        self.logo_widget = QWidget()
        logo_v = QVBoxLayout(self.logo_widget); logo_v.setContentsMargins(0, 0, 0, 0); logo_v.setSpacing(12)
        logo_v.addWidget(QLabel("SOURCE DE L'IMAGE"))
        
        path_box = QVBoxLayout(); path_box.setSpacing(4)
        self.logo_path_edit = QLineEdit()
        self.logo_path_edit.setObjectName("PathDisplay")
        self.logo_path_edit.setReadOnly(True)
        self.logo_path_edit.setPlaceholderText("Aucun fichier sélectionné")
        path_box.addWidget(self.logo_path_edit)
        
        btns = QHBoxLayout(); btns.setSpacing(8)
        pick_logo = QPushButton("Parcourir..."); pick_logo.setObjectName("Primary"); pick_logo.clicked.connect(self._pick_logo)
        remove_logo = QPushButton("Supprimer"); remove_logo.clicked.connect(self._remove_logo)
        btns.addWidget(pick_logo, 2); btns.addWidget(remove_logo, 1)
        
        logo_v.addLayout(path_box); logo_v.addLayout(btns)
        self.content_stack.addWidget(self.logo_widget)
        
        self.content_card.content_layout.addWidget(self.content_stack)
        side_v.addWidget(self.content_card)

        # Appearance Card
        self.app_card = BentoCard("Apparence")
        app_v = QVBoxLayout()
        
        # Scale
        s_row = QHBoxLayout(); s_row.addWidget(QLabel("Taille:"))
        self.scale_slider = QSlider(Qt.Orientation.Horizontal); self.scale_slider.setRange(2, 100)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        self.scale_val = QLabel("10%"); self.scale_val.setFixedWidth(30)
        s_row.addWidget(self.scale_slider); s_row.addWidget(self.scale_val)
        app_v.addLayout(s_row)
        
        # Opacity
        o_row = QHBoxLayout(); o_row.addWidget(QLabel("Opacité:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal); self.opacity_slider.setRange(0, 100)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.opacity_val = QLabel("70%"); self.opacity_val.setFixedWidth(30)
        o_row.addWidget(self.opacity_slider); o_row.addWidget(self.opacity_val)
        app_v.addLayout(o_row)
        
        self.app_card.content_layout.addLayout(app_v)
        side_v.addWidget(self.app_card)

        # Position Card
        self.pos_card = BentoCard("Positionnement")
        pos_v = QVBoxLayout()
        
        # Anchor Grid
        grid_container = QHBoxLayout()
        grid = QGridLayout(); grid.setSpacing(4)
        self.anchor_btns: dict[str, QToolButton] = {}
        positions = [
            ("top_left", 0, 0), ("top_center", 0, 1), ("top_right", 0, 2),
            ("center_left", 1, 0), ("center", 1, 1), ("center_right", 1, 2),
            ("bottom_left", 2, 0), ("bottom_center", 2, 1), ("bottom_right", 2, 2)
        ]
        self.anchor_group = QButtonGroup(self)
        for key, r, c in positions:
            btn = QToolButton(); btn.setObjectName("AnchorBtn"); btn.setCheckable(True)
            self.anchor_btns[key] = btn; self.anchor_group.addButton(btn)
            grid.addWidget(btn, r, c)
        self.anchor_group.buttonClicked.connect(self._on_anchor_btn_clicked)
        grid_container.addLayout(grid); grid_container.addStretch(1)
        
        # Precision offsets
        off_box = QVBoxLayout(); off_box.setSpacing(4)
        self.spin_x = QSpinBox(); self.spin_x.setRange(-100, 100); self.spin_x.setSuffix("%")
        self.spin_y = QSpinBox(); self.spin_y.setRange(-100, 100); self.spin_y.setSuffix("%")
        self.spin_x.valueChanged.connect(self._on_offsets_changed)
        self.spin_y.valueChanged.connect(self._on_offsets_changed)
        off_box.addWidget(QLabel("OFFSET X")); off_box.addWidget(self.spin_x)
        off_box.addWidget(QLabel("OFFSET Y")); off_box.addWidget(self.spin_y)
        grid_container.addLayout(off_box)
        
        pos_v.addLayout(grid_container)
        self.pos_card.content_layout.addLayout(pos_v)
        side_v.addWidget(self.pos_card)

        side_v.addStretch(1)

        # Final Actions
        foot = QHBoxLayout()
        can = QPushButton("Annuler"); can.clicked.connect(self.reject)
        ok = QPushButton("Enregistrer la Signature"); ok.setObjectName("Primary"); ok.clicked.connect(self.accept)
        foot.addWidget(can); foot.addWidget(ok)
        side_v.addLayout(foot)

        main_layout.addWidget(sidebar)
        self._load_controls()

    def _create_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionHeader")
        return lbl

    def get_config(self) -> dict:
        return normalize_watermark_config(self._cfg)

    def _set_global_enabled(self, val: bool) -> None:
        self._cfg["enabled"] = bool(val)
        self.preview.set_config(self._cfg)

    def _on_mode_switched(self, btn: QToolButton) -> None:
        self._active = "logo" if btn == self.btn_logo else "text"
        self._load_controls()

    def _on_scale_changed(self, val: int) -> None:
        if self._loading: return
        self.scale_val.setText(f"{val}%")
        self._cfg[self._active]["size_pct"] = float(val)
        self.preview.set_config(self._cfg)

    def _on_opacity_changed(self, val: int) -> None:
        if self._loading: return
        self.opacity_val.setText(f"{val}%")
        self._cfg[self._active]["opacity"] = int(val)
        self.preview.set_config(self._cfg)

    def _on_anchor_btn_clicked(self, btn: QToolButton) -> None:
        if self._loading: return
        for k, b in self.anchor_btns.items():
            if b == btn:
                self._cfg[self._active]["anchor"] = k
                break
        self.preview.set_config(self._cfg)

    def _on_offsets_changed(self) -> None:
        if self._loading: return
        self._cfg[self._active]["offset_x_pct"] = float(self.spin_x.value())
        self._cfg[self._active]["offset_y_pct"] = float(self.spin_y.value())
        self.preview.set_config(self._cfg)

    def _on_drag_offsets(self, x: float, y: float) -> None:
        self._loading = True
        try:
            self.spin_x.setValue(int(round(x))); self.spin_y.setValue(int(round(y)))
        finally:
            self._loading = False
        self._on_offsets_changed()

    def _set_text(self, k: str, v: any) -> None:
        if self._loading: return
        self._cfg["text"][k] = v
        self.preview.set_config(self._cfg)

    def _on_font_changed(self) -> None:
        if self._loading: return
        self._set_text("font_family", self.font_combo.currentText())

    def _pick_text_color(self) -> None:
        curr = QColor(_normalize_hex(str(self._cfg["text"]["color_hex"]), "#FFFFFF"))
        color = QColorDialog.getColor(curr, self, "Couleur du texte")
        if color.isValid():
            self._set_text("color_hex", color.name().upper())

    def _insert_variable_key(self, key: str) -> None:
        tk = f"{{{{{key}}}}}"
        cur = self.template_edit.cursorPosition(); txt = self.template_edit.text()
        self.template_edit.setText(txt[:cur] + tk + txt[cur:])
        self.template_edit.setCursorPosition(cur + len(tk))

    def _pick_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Image Logo", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not path: return
        try:
            rel = import_logo(path, self._app_data_dir)
            self._cfg["logo"]["asset_rel_path"] = rel
            self._cfg["logo"]["enabled"] = True
            self.logo_path_edit.setText(rel)
            self.logo_path_edit.setToolTip(rel)
            self.preview.set_config(self._cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))

    def _remove_logo(self) -> None:
        self._cfg["logo"]["asset_rel_path"] = ""
        self._cfg["logo"]["enabled"] = False
        self.logo_path_edit.setText("Aucun logo chargé")
        self.logo_path_edit.setToolTip("")
        self.preview.set_config(self._cfg)

    def _pick_preview(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Image Aperçu", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path: self.preview.set_preview_image(path)

    def _load_controls(self) -> None:
        self._loading = True
        try:
            cfg = self._cfg.get(self._active, {})
            self.btn_text.setChecked(self._active == "text")
            self.btn_logo.setChecked(self._active == "logo")
            self.content_stack.setCurrentIndex(1 if self._active == "logo" else 0)

            self.scale_slider.setValue(int(float(cfg.get("size_pct", 4.0))))
            self.scale_val.setText(f"{self.scale_slider.value()}%")
            self.opacity_slider.setValue(int(float(cfg.get("opacity", 70))))
            self.opacity_val.setText(f"{self.opacity_slider.value()}%")
            
            anchor = str(cfg.get("anchor", "center"))
            if anchor in self.anchor_btns: self.anchor_btns[anchor].setChecked(True)
            self.spin_x.setValue(int(float(cfg.get("offset_x_pct", 0.0))))
            self.spin_y.setValue(int(float(cfg.get("offset_y_pct", 0.0))))
            
            if self._active == "text":
                self.template_edit.setText(str(cfg.get("template", "")))
                font_idx = self.font_combo.findText(str(cfg.get("font_family", "Sans")))
                self.font_combo.setCurrentIndex(max(0, font_idx))
            else:
                p = str(cfg.get("asset_rel_path", ""))
                self.logo_path_edit.setText(p or "Aucun logo chargé")
                self.logo_path_edit.setToolTip(p)
            
            self.preview.set_active_layer(self._active)
            self.preview.set_config(self._cfg)
        finally:
            self._loading = False
