from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QObject, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QSpinBox,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import compute_app_data_dir_from_root, normalize_accent_color, resolve_app_paths
from ..preset_defaults import default_preset_config
from ..services import (
    CullingService,
    EditService,
    ExportService,
    ImportService,
    JobQueueService,
    MetadataService,
    PreviewPrefetchManager,
    PresetService,
    ProjectService,
    QualityChecklistError,
    RenameService,
    StorageService,
)

try:
    from PIL import Image, ImageEnhance, ImageOps
    from PIL.ImageQt import ImageQt
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None
    ImageEnhance = None
    ImageOps = None
    ImageQt = None

from ..services.edits import DEFAULT_EDIT_SETTINGS
from ..services.watermarks import normalize_watermark_config, summarize_watermark_config
from .watermark_editor import WatermarkEditorDialog
from .components import BentoCard, WorkflowStatusBar
from .dashboard import DashboardTab
from .projects import ProjectsTab

FIF = None
FluentPushButton = None
FluentPrimaryPushButton = None
FluentSearchLineEdit = None
FluentLineEdit = None
FluentComboBox = None
FluentSpinBox = None
FluentCheckBox = None
FluentPlainTextEdit = None
FluentTableWidget = None
FluentThemeEnum = None
fluent_set_theme = None
fluent_set_theme_color = None
QFLUENT_AVAILABLE = False
QFLUENT_DISABLE_REASON = ""
_QFLUENT_IMPORT_ATTEMPTED = False
NativePushButton = QPushButton


def _lighter(color_hex: str, amount: int) -> str:
    color = QColor(color_hex)
    if not color.isValid():
        return color_hex
    return color.lighter(max(100, 100 + int(amount))).name().upper()


def _darker(color_hex: str, amount: int) -> str:
    color = QColor(color_hex)
    if not color.isValid():
        return color_hex
    return color.darker(max(100, 100 + int(amount))).name().upper()


def _rgba(color_hex: str, alpha: int) -> str:
    color = QColor(color_hex)
    if not color.isValid():
        color = QColor("#10B981")
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {max(0, min(255, int(alpha)))})"


def _blend(color_a: str, color_b: str, ratio_b: float) -> str:
    a = QColor(color_a)
    b = QColor(color_b)
    if not a.isValid():
        a = QColor("#10B981")
    if not b.isValid():
        b = QColor("#2D5A27")
    t = max(0.0, min(1.0, float(ratio_b)))
    r = int(round((a.red() * (1.0 - t)) + (b.red() * t)))
    g = int(round((a.green() * (1.0 - t)) + (b.green() * t)))
    bl = int(round((a.blue() * (1.0 - t)) + (b.blue() * t)))
    return QColor(r, g, bl).name().upper()


def _reset_fluent_state() -> None:
    global FIF, FluentPushButton, FluentPrimaryPushButton, FluentSearchLineEdit
    global FluentLineEdit, FluentComboBox, FluentSpinBox, FluentCheckBox
    global FluentPlainTextEdit, FluentTableWidget
    global FluentThemeEnum, fluent_set_theme, fluent_set_theme_color, QFLUENT_AVAILABLE
    FIF = None
    FluentPushButton = None
    FluentPrimaryPushButton = None
    FluentSearchLineEdit = None
    FluentLineEdit = None
    FluentComboBox = None
    FluentSpinBox = None
    FluentCheckBox = None
    FluentPlainTextEdit = None
    FluentTableWidget = None
    FluentThemeEnum = None
    fluent_set_theme = None
    fluent_set_theme_color = None
    QFLUENT_AVAILABLE = False


def _disable_fluent(reason: str = "") -> None:
    global QFLUENT_DISABLE_REASON
    _reset_fluent_state()
    QFLUENT_DISABLE_REASON = reason.strip()
    if QFLUENT_DISABLE_REASON:
        print(f"[PhotoHub] Fluent widgets disabled: {QFLUENT_DISABLE_REASON}")


def _detect_qt_binding(*widget_classes) -> str:
    bindings = set()
    for widget_cls in widget_classes:
        for base_cls in getattr(widget_cls, "__mro__", ()):
            module_name = str(getattr(base_cls, "__module__", ""))
            root = module_name.split(".", 1)[0]
            if root in {"PyQt5", "PyQt6", "PySide2", "PySide6"}:
                bindings.add(root)
    if not bindings:
        return "unknown"
    if bindings == {"PySide6"}:
        return "PySide6"
    return ",".join(sorted(bindings))


def _apply_fluent_widget_aliases() -> None:
    global QPushButton, QLineEdit, QComboBox, QSpinBox, QCheckBox, QPlainTextEdit, QTableWidget
    if not QFLUENT_AVAILABLE:
        return
    if FluentPushButton is not None:
        QPushButton = FluentPushButton
    if FluentLineEdit is not None:
        QLineEdit = FluentLineEdit
    if FluentComboBox is not None:
        QComboBox = FluentComboBox
    if FluentSpinBox is not None:
        QSpinBox = FluentSpinBox
    if FluentCheckBox is not None:
        QCheckBox = FluentCheckBox
    if FluentPlainTextEdit is not None:
        QPlainTextEdit = FluentPlainTextEdit
    if FluentTableWidget is not None:
        QTableWidget = FluentTableWidget


def _ensure_fluent_loaded() -> None:
    global FIF, FluentPushButton, FluentPrimaryPushButton, FluentSearchLineEdit
    global FluentLineEdit, FluentComboBox, FluentSpinBox, FluentCheckBox
    global FluentPlainTextEdit, FluentTableWidget
    global FluentThemeEnum, fluent_set_theme, fluent_set_theme_color
    global QFLUENT_AVAILABLE, _QFLUENT_IMPORT_ATTEMPTED
    if _QFLUENT_IMPORT_ATTEMPTED:
        return
    if str(os.getenv("PHOTOHUB_DISABLE_FLUENT", "")).strip().lower() in {"1", "true", "yes", "on"}:
        _QFLUENT_IMPORT_ATTEMPTED = True
        _disable_fluent("PHOTOHUB_DISABLE_FLUENT is enabled.")
        return
    _QFLUENT_IMPORT_ATTEMPTED = True
    os.environ.setdefault("QT_API", "pyside6")
    try:
        from qfluentwidgets import (
            FluentIcon as _FIF,
            PushButton as _FluentPushButton,
            PrimaryPushButton as _FluentPrimaryPushButton,
            SearchLineEdit as _FluentSearchLineEdit,
            LineEdit as _FluentLineEdit,
            ComboBox as _FluentComboBox,
            SpinBox as _FluentSpinBox,
            CheckBox as _FluentCheckBox,
            PlainTextEdit as _FluentPlainTextEdit,
            TableWidget as _FluentTableWidget,
            Theme as _FluentThemeEnum,
            setTheme as _fluent_set_theme,
            setThemeColor as _fluent_set_theme_color,
        )
    except Exception:  # pragma: no cover - optional UI dependency fallback
        _disable_fluent("qfluentwidgets import failed.")
        return

    binding = _detect_qt_binding(
        _FluentPushButton,
        _FluentSearchLineEdit,
        _FluentLineEdit,
        _FluentComboBox,
        _FluentSpinBox,
        _FluentCheckBox,
        _FluentPlainTextEdit,
        _FluentTableWidget,
    )
    if binding != "PySide6":
        _disable_fluent(
            f"Incompatible qfluentwidgets binding detected ({binding}). "
            "Install PySide6-Fluent-Widgets to enable Fluent mode."
        )
        return

    FIF = _FIF
    FluentPushButton = _FluentPushButton
    FluentPrimaryPushButton = _FluentPrimaryPushButton
    FluentSearchLineEdit = _FluentSearchLineEdit
    FluentLineEdit = _FluentLineEdit
    FluentComboBox = _FluentComboBox
    FluentSpinBox = _FluentSpinBox
    FluentCheckBox = _FluentCheckBox
    FluentPlainTextEdit = _FluentPlainTextEdit
    FluentTableWidget = _FluentTableWidget
    FluentThemeEnum = _FluentThemeEnum
    fluent_set_theme = _fluent_set_theme
    fluent_set_theme_color = _fluent_set_theme_color
    QFLUENT_AVAILABLE = True
    _apply_fluent_widget_aliases()
    try:
        fluent_set_theme(FluentThemeEnum.DARK)
    except Exception:
        pass


def _new_button(text: str, *, primary: bool = False) -> QPushButton:
    # Keep one button class across the app and style primary intent via QSS.
    # This avoids qfluent primary widgets forcing a too-saturated accent fill.
    if QFLUENT_AVAILABLE and FluentPushButton is not None:
        button = FluentPushButton(text)
    else:
        button = QPushButton(text)
    button.setProperty("isPrimaryButton", "true" if primary else "false")
    return button


def _new_table_widget(rows: int = 0, columns: int = 0) -> QTableWidget:
    # Qt QTableWidget supports (rows, columns) ctor, while Fluent TableWidget
    # expects a parent-only ctor. Normalize creation for both implementations.
    try:
        return QTableWidget(rows, columns)
    except TypeError:
        table = QTableWidget()
        table.setRowCount(max(0, int(rows)))
        table.setColumnCount(max(0, int(columns)))
        return table


def _new_tag_label(text: str, color: str = "#10B981") -> QLabel:
    label = QLabel(text)
    label.setObjectName("TagLabel")
    label.setStyleSheet(f"""
        background: {_rgba(color, 40)};
        color: {color};
        border: 1px solid {_rgba(color, 100)};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 10px;
        font-weight: bold;
    """)
    return label


class JobWorker(QObject):
    progress = Signal(int, int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            call_kwargs = dict(self.kwargs)
            call_kwargs["progress_cb"] = self._emit_progress
            call_kwargs["is_cancelled"] = self.is_cancelled
            value = self.fn(*self.args, **call_kwargs)
            self.result.emit(value)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _emit_progress(self, done: int, total: int, detail: str = "") -> None:
        self.progress.emit(int(done), int(total), str(detail))


@dataclass
class ExportQueueItem:
    queue_id: int
    db_job_id: int | None
    project_id: int
    project_label: str
    destination_dir: str
    profiles: list[str]
    min_rating: int
    create_zip: bool
    create_report: bool
    create_contact_sheet: bool
    status: str
    attempts: int
    queued_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    message: str = ""


# DashboardTab moved to .dashboard.py


class JobsTab(QWidget):
    def __init__(self, get_active_jobs: Callable[[], int]) -> None:
        super().__init__()
        self.get_active_jobs = get_active_jobs

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Centre de Jobs")
        title.setObjectName("PageTitle")
        self.jobs_state_label = QLabel("0 en cours")
        clear_btn = _new_button("Vider journal")
        clear_btn.clicked.connect(self._clear_logs)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.jobs_state_label)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    def refresh_data(self) -> None:
        active = int(self.get_active_jobs())
        if active <= 0:
            self.jobs_state_label.setText("Aucun job en cours")
        else:
            self.jobs_state_label.setText(f"{active} job(s) en cours")

    def add_event(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{stamp}] {message}")

    def _clear_logs(self) -> None:
        self.log_text.clear()


class MainWindow(QMainWindow):
    SIDEBAR_EXPANDED_WIDTH = 200
    SIDEBAR_COLLAPSED_WIDTH = 60

    NAV_ITEMS = [
        ("dashboard", "Dashboard", "HOME"),
        ("projects", "Projets", "ALBUM"),
        ("ingest", "Ingest", "DOWNLOAD"),
        ("culling", "Tri", "CUT"),
        ("rename", "Rename", "RENAME"),
        ("edit", "Edit", "PHOTO"),
        ("export", "Export", "SEND"),
        ("presets", "Presets", "EDIT"),
        ("settings", "Settings", "SETTING"),
        ("jobs", "Jobs", "SYNC"),
    ]

    CONTEXT_HINTS = {
        "dashboard": ("Dashboard", "Vue globale du studio"),
        "projects": ("Projets", "Creation, statut, et affectation preset"),
        "ingest": ("Ingest", "Importer les RAW vers le projet actif"),
        "culling": ("Tri rapide", "Raccourcis: <-/-> | molette | P garder | X rejeter | 1..5 noter"),
        "rename": ("Batch rename", "Renommage par lot de la selection active"),
        "edit": ("Edit rapide", "Raccourcis: Ctrl+C copier | Ctrl+V coller | Shift+S sync | F solo mode"),
        "export": ("Export", "Batch multi-profils et livraison"),
        "presets": ("Presets", "Templates d'import/export/watermark"),
        "settings": ("Settings", "Stockage global et theme"),
        "jobs": ("Jobs", "Suivi des operations en cours"),
    }

    def __init__(
        self,
        project_service: ProjectService,
        preset_service: PresetService,
        culling_service: CullingService,
        edit_service: EditService,
        metadata_service: MetadataService,
        import_service: ImportService,
        export_service: ExportService,
        job_queue_service: JobQueueService,
        rename_service: RenameService,
        storage_service: StorageService,
        on_reload_runtime: Callable,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.preset_service = preset_service
        self.culling_service = culling_service
        self.edit_service = edit_service
        self.import_service = import_service
        self.export_service = export_service
        self.job_queue_service = job_queue_service
        self.metadata_service = metadata_service
        self.rename_service = rename_service
        self.storage_service = storage_service
        self.on_reload_runtime = on_reload_runtime
        self.active_ops_count = 0
        self.accent_color = normalize_accent_color(self.storage_service.get_settings().get("accent_color"))

        # Load optional Fluent widgets only after QApplication exists.
        _ensure_fluent_loaded()

        self.setWindowTitle("PhotoHub - Studio Workflow")
        self.resize(1400, 860)

        root = QWidget()
        root_layout = QGridLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)
        root_layout.setColumnStretch(0, 0)
        root_layout.setColumnStretch(1, 1)
        root_layout.setRowStretch(0, 1)

        self.current_nav_key = ""
        self.sidebar_pinned = False
        self.sidebar_expanded = False
        self.nav_item_labels: dict[str, str] = {}
        self.nav_buttons: dict[str, QPushButton] = {}

        self.nav_panel = QWidget()
        self.nav_panel.setObjectName("SideBar")
        self.nav_panel.setFixedWidth(self.SIDEBAR_COLLAPSED_WIDTH)
        self.nav_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.nav_panel.installEventFilter(self)
        nav_layout = QVBoxLayout(self.nav_panel)
        nav_layout.setContentsMargins(10, 10, 10, 10)
        nav_layout.setSpacing(6)
        self.sidebar_toggle_btn = self._build_sidebar_toggle_button()
        nav_layout.addWidget(self.sidebar_toggle_btn)

        top_keys = ["dashboard", "projects", "ingest", "culling", "rename", "edit", "export", "presets"]
        bottom_keys = ["jobs", "settings"]
        for key in top_keys:
            button = self._build_nav_button(key)
            self.nav_buttons[key] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        for key in bottom_keys:
            button = self._build_nav_button(key)
            self.nav_buttons[key] = button
            nav_layout.addWidget(button)

        root_layout.addWidget(self.nav_panel, 0, 0)

        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        topbar = QGroupBox()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(74)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(14, 10, 14, 10)

        app_title = QLabel("PhotoHub")
        app_title.setObjectName("AppTitle")
        self.project_context_combo = QComboBox()
        self.project_context_combo.setMinimumHeight(34)
        self.project_context_combo.currentIndexChanged.connect(self._on_project_context_changed)
        self.context_mode_label = QLabel("Dashboard")
        self.context_mode_label.setObjectName("ContextModeBadge")
        self.context_mode_label.setMinimumHeight(32)

        # Workflow Status Bar (Fil Rouge)
        self.workflow_status = WorkflowStatusBar()
        self.workflow_status.step_clicked.connect(self._switch_page)

        topbar_layout.addWidget(app_title)
        topbar_layout.addSpacing(16)
        topbar_layout.addWidget(self.workflow_status)
        topbar_layout.addStretch(1)
        topbar_layout.addWidget(self.context_mode_label)
        topbar_layout.addSpacing(16)
        topbar_layout.addWidget(QLabel("Projet"))
        topbar_layout.addWidget(self.project_context_combo)
        content_layout.addWidget(topbar)

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.dashboard_tab = DashboardTab(
            self.project_service, 
            get_active_jobs=self._get_active_jobs_count,
            storage_service=self.storage_service,
            job_queue_service=self.job_queue_service,
            on_navigate=self._switch_page,
            get_icon=self._nav_icon
        )
        self.hub_tab = ProjectsTab(self.project_service, self.preset_service, on_data_changed=self.refresh_all)
        self.import_tab = ImportTab(
            project_service=self.project_service,
            import_service=self.import_service,
            on_data_changed=self.refresh_all,
            on_operation_started=self._on_operation_started,
            on_operation_ended=self._on_operation_ended,
            on_job_event=self._append_job_event,
        )
        self.culling_tab = CullingTab(
            project_service=self.project_service,
            culling_service=self.culling_service,
            on_data_changed=self.refresh_all,
            on_operation_started=self._on_operation_started,
            on_operation_ended=self._on_operation_ended,
            on_job_event=self._append_job_event,
        )
        self.edit_tab = EditTab(
            project_service=self.project_service,
            edit_service=self.edit_service,
            metadata_service=self.metadata_service,
            on_operation_started=self._on_operation_started,
            on_operation_ended=self._on_operation_ended,
            on_job_event=self._append_job_event,
        )
        # Prevent Edit minimum size hint from forcing window growth when sidebar expands.
        self.edit_tab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.export_tab = ExportTab(
            project_service=self.project_service,
            preset_service=self.preset_service,
            export_service=self.export_service,
            job_queue_service=self.job_queue_service,
            on_operation_started=self._on_operation_started,
            on_operation_ended=self._on_operation_ended,
            on_job_event=self._append_job_event,
        )
        self.rename_tab = BatchRenameTab(
            project_service=self.project_service,
            culling_service=self.culling_service,
            rename_service=self.rename_service,
            on_data_changed=self.refresh_all,
            on_operation_started=self._on_operation_started,
            on_operation_ended=self._on_operation_ended,
            on_job_event=self._append_job_event,
        )
        self.presets_tab = PresetTab(self.preset_service, on_data_changed=self.refresh_all)
        self.settings_tab = SettingsTab(
            storage_service=self.storage_service,
            is_busy=self.is_busy,
            on_migration_completed=self._reload_runtime_after_migration,
            on_theme_changed=self._on_theme_settings_changed,
        )
        self.jobs_tab = JobsTab(get_active_jobs=self._get_active_jobs_count)

        self.stack.addWidget(self.dashboard_tab)
        self.stack.addWidget(self.hub_tab)
        self.stack.addWidget(self.import_tab)
        self.stack.addWidget(self.culling_tab)
        self.stack.addWidget(self.edit_tab)
        self.stack.addWidget(self.export_tab)
        self.stack.addWidget(self.rename_tab)
        self.stack.addWidget(self.presets_tab)
        self.stack.addWidget(self.settings_tab)
        self.stack.addWidget(self.jobs_tab)
        content_layout.addWidget(self.stack, 1)
        root_layout.addWidget(content, 0, 1)
        self.setCentralWidget(root)

        self._apply_theme()
        self._apply_sidebar_state()
        self._switch_page("dashboard")

        self.refresh_all()

    def _focus_global_search(self) -> None:
        return

    def _get_active_jobs_count(self) -> int:
        return int(self.active_ops_count)

    def is_busy(self) -> bool:
        return self.active_ops_count > 0

    def _on_operation_started(self) -> None:
        self.active_ops_count += 1
        self.jobs_tab.refresh_data()

    def _on_operation_ended(self) -> None:
        self.active_ops_count = max(0, self.active_ops_count - 1)
        self.jobs_tab.refresh_data()

    def _append_job_event(self, message: str) -> None:
        self.jobs_tab.add_event(message)

    def _build_search_line_edit(self):
        if QFLUENT_AVAILABLE and FluentSearchLineEdit is not None:
            return FluentSearchLineEdit()
        return QLineEdit()

    def _apply_theme(self) -> None:
        self.accent_color = normalize_accent_color(self.accent_color)
        if QFLUENT_AVAILABLE and fluent_set_theme is not None and FluentThemeEnum is not None:
            try:
                fluent_set_theme(FluentThemeEnum.DARK)
            except Exception:
                pass
        if QFLUENT_AVAILABLE and fluent_set_theme_color is not None:
            try:
                # Tone down accent to keep a studio-safe dark UI even when users pick vivid colors.
                fluent_accent = _blend(self.accent_color, "#1A1D21", 0.48)
                fluent_set_theme_color(fluent_accent)
            except Exception:
                pass
        self._apply_sprint1_style()

    def _on_theme_settings_changed(self) -> None:
        settings = self.storage_service.get_settings()
        self.accent_color = normalize_accent_color(settings.get("accent_color"))
        self._apply_theme()
        self._apply_sidebar_state()
        self.refresh_all()
        self._append_job_event(f"Identité visuelle mise à jour (accent {self.accent_color}).")

    def _build_sidebar_toggle_button(self) -> QPushButton:
        button = NativePushButton("<")
        button.setObjectName("SideBarToggle")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(34)
        button.setText("")
        button.setIconSize(QSize(16, 16))
        button.clicked.connect(self._toggle_sidebar)
        button.setToolTip("Replier la sidebar")
        return button

    def _toggle_sidebar(self) -> None:
        self.sidebar_pinned = not self.sidebar_pinned
        self.sidebar_expanded = self.sidebar_pinned
        self._apply_sidebar_state()

    def _apply_sidebar_state(self) -> None:
        width = self.SIDEBAR_EXPANDED_WIDTH if self.sidebar_expanded else self.SIDEBAR_COLLAPSED_WIDTH
        self.nav_panel.setFixedWidth(width)

        if self.sidebar_expanded:
            self.sidebar_toggle_btn.setText("")
            self.sidebar_toggle_btn.setIcon(self._sidebar_toggle_icon(expanded=True))
            self.sidebar_toggle_btn.setToolTip("Desepingler la sidebar")
            self.sidebar_toggle_btn.setProperty("collapsed", "false")
        else:
            self.sidebar_toggle_btn.setText("")
            self.sidebar_toggle_btn.setIcon(self._sidebar_toggle_icon(expanded=False))
            self.sidebar_toggle_btn.setToolTip("Epingler la sidebar")
            self.sidebar_toggle_btn.setProperty("collapsed", "true")
        self._refresh_widget_style(self.sidebar_toggle_btn)

        for nav_key, button in self.nav_buttons.items():
            label = self.nav_item_labels.get(nav_key, nav_key)
            if self.sidebar_expanded:
                button.setText(label)
                button.setProperty("collapsed", "false")
            else:
                button.setText("")
                button.setProperty("collapsed", "true")
            button.setToolTip(label)
            self._refresh_widget_style(button)

        # Re-apply page split ratios after shell width changes to avoid persistent layout drift.
        QTimer.singleShot(0, self._restore_layout_after_sidebar_toggle)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.nav_panel and not self.sidebar_pinned:
            # Keep sidebar stable in collapsed mode (no hover-resize) to prevent content reflow jitter.
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    def _restore_layout_after_sidebar_toggle(self) -> None:
        try:
            self.culling_tab.reset_layout_after_shell_resize()
        except Exception:
            pass
        try:
            self.edit_tab.reset_layout_after_shell_resize()
        except Exception:
            pass
        try:
            self.rename_tab.reset_layout_after_shell_resize()
        except Exception:
            pass
        try:
            self.presets_tab.reset_layout_after_shell_resize()
        except Exception:
            pass

    def _build_nav_button(self, key: str) -> QPushButton:
        label = key
        icon_name = ""
        for nav_key, nav_label, nav_icon_name in self.NAV_ITEMS:
            if nav_key == key:
                label = nav_label
                icon_name = nav_icon_name
                break

        self.nav_item_labels[key] = label
        button = NativePushButton(label)

        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("navButton", True)
        button.setProperty("navKey", key)
        button.setProperty("active", "false")
        button.setProperty("collapsed", "false")
        button.setAccessibleName(label)
        button.setToolTip(label)
        button.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        button.setIconSize(QSize(16, 16))
        button.setMinimumHeight(38)

        icon = self._nav_icon(icon_name)
        if not icon.isNull():
            button.setIcon(icon)
        button.clicked.connect(lambda _checked=False, k=key: self._switch_page(k))
        return button

    def _nav_icon(self, icon_name: str) -> QIcon:
        icon = self._fluent_icon(icon_name)
        if not icon.isNull():
            return icon
        return self._fallback_nav_icon(icon_name)

    def _sidebar_toggle_icon(self, expanded: bool) -> QIcon:
        icon_name = "LEFT_ARROW" if expanded else "RIGHT_ARROW"
        icon = self._fluent_icon(icon_name)
        if not icon.isNull():
            return icon
        style = self.style()
        pixmap = QStyle.StandardPixmap.SP_ArrowLeft if expanded else QStyle.StandardPixmap.SP_ArrowRight
        return style.standardIcon(pixmap)

    def _fluent_icon(self, icon_name: str) -> QIcon:
        if not QFLUENT_AVAILABLE or FIF is None or not icon_name:
            return QIcon()
        icon_ref = getattr(FIF, icon_name, None)
        if icon_ref is None:
            return QIcon()
        try:
            icon = icon_ref.icon()
            if isinstance(icon, QIcon):
                return icon
        except Exception:
            pass
        try:
            if isinstance(icon_ref, QIcon):
                return icon_ref
        except Exception:
            pass
        return QIcon()

    def _fallback_nav_icon(self, icon_name: str) -> QIcon:
        style = self.style()
        icon_map = {
            "HOME": QStyle.StandardPixmap.SP_DesktopIcon,
            "ALBUM": QStyle.StandardPixmap.SP_DirOpenIcon,
            "DOWNLOAD": QStyle.StandardPixmap.SP_ArrowDown,
            "CUT": QStyle.StandardPixmap.SP_TrashIcon,
            "RENAME": QStyle.StandardPixmap.SP_FileDialogListView,
            "PHOTO": QStyle.StandardPixmap.SP_FileIcon,
            "SEND": QStyle.StandardPixmap.SP_ArrowForward,
            "EDIT": QStyle.StandardPixmap.SP_FileDialogDetailedView,
            "SETTING": QStyle.StandardPixmap.SP_FileDialogContentsView,
            "SYNC": QStyle.StandardPixmap.SP_BrowserReload,
        }
        standard_icon = icon_map.get(icon_name)
        if standard_icon is None:
            return QIcon()
        return style.standardIcon(standard_icon)

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _switch_page(self, key: str) -> None:
        normalized = (key or "").strip().lower()
        if not normalized:
            return
        self._set_active_nav(normalized)
        
        # Sync WorkflowStatusBar
        if hasattr(self, "workflow_status"):
             self.workflow_status.set_active_step(normalized)

        if normalized == "dashboard":
            self.stack.setCurrentWidget(self.dashboard_tab)
        elif normalized == "projects":
            self.stack.setCurrentWidget(self.hub_tab)
        elif normalized == "ingest":
            self.stack.setCurrentWidget(self.import_tab)
        elif normalized == "culling":
            self.stack.setCurrentWidget(self.culling_tab)
        elif normalized == "rename":
            self.stack.setCurrentWidget(self.rename_tab)
        elif normalized == "edit":
            self.stack.setCurrentWidget(self.edit_tab)
        elif normalized == "export":
            self.stack.setCurrentWidget(self.export_tab)
        elif normalized == "presets":
            self.stack.setCurrentWidget(self.presets_tab)
        elif normalized == "settings":
            self.stack.setCurrentWidget(self.settings_tab)
        elif normalized == "jobs":
            self.stack.setCurrentWidget(self.jobs_tab)
        self._update_context_bar(normalized)

    def _set_active_nav(self, key: str) -> None:
        normalized = (key or "").strip().lower()
        if not normalized:
            return
        self.current_nav_key = normalized
        for nav_key, button in self.nav_buttons.items():
            button.setProperty("active", "true" if nav_key == normalized else "false")
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _on_import_export_section_changed(self, index: int) -> None:
        # Kept for backward compatibility after moving to page-level sidebar navigation.
        _ = index

    def _update_context_bar(self, key: str) -> None:
        mode, hint = self.CONTEXT_HINTS.get((key or "").strip().lower(), ("Mode", ""))
        self.context_mode_label.setText(mode)
        self.context_mode_label.setToolTip(hint)

    def _update_activity_badge(self) -> None:
        return

    def _refresh_project_context_combo(self) -> None:
        current = self.project_context_combo.currentData()
        projects = self.project_service.list_projects()

        self.project_context_combo.blockSignals(True)
        self.project_context_combo.clear()
        self.project_context_combo.addItem("Aucun contexte", userData=None)
        for project in projects:
            self.project_context_combo.addItem(project.name, userData=project.id)

        target_idx = 0
        if current is not None:
            idx = self.project_context_combo.findData(current)
            if idx >= 0:
                target_idx = idx
        self.project_context_combo.setCurrentIndex(target_idx)
        self.project_context_combo.blockSignals(False)

        self._on_project_context_changed()

    def _on_project_context_changed(self) -> None:
        project_id = self.project_context_combo.currentData()
        if project_id is None:
            return
        self.import_tab.set_selected_project(int(project_id))
        self.culling_tab.set_selected_project(int(project_id))
        self.edit_tab.set_selected_project(int(project_id))
        self.export_tab.set_selected_project(int(project_id))
        self.hub_tab.select_project_by_id(int(project_id))
        self.rename_tab.set_selected_project(int(project_id))

    def _on_search_text_changed(self, value: str) -> None:
        self.hub_tab.set_name_filter(value.strip())

    def _reload_runtime_after_migration(self) -> None:
        runtime = self.on_reload_runtime()
        self.project_service = runtime.project_service
        self.preset_service = runtime.preset_service
        self.culling_service = runtime.culling_service
        self.edit_service = runtime.edit_service
        self.import_service = runtime.import_service
        self.export_service = runtime.export_service
        self.job_queue_service = runtime.job_queue_service
        self.metadata_service = runtime.metadata_service
        self.rename_service = runtime.rename_service

        self.dashboard_tab.project_service = self.project_service
        self.hub_tab.project_service = self.project_service
        self.hub_tab.preset_service = self.preset_service
        self.rename_tab.project_service = self.project_service
        self.rename_tab.culling_service = self.culling_service
        self.rename_tab.rename_service = self.rename_service
        self.import_tab.project_service = self.project_service
        self.import_tab.import_service = self.import_service
        self.culling_tab.project_service = self.project_service
        self.culling_tab.culling_service = self.culling_service
        self.edit_tab.project_service = self.project_service
        self.edit_tab.edit_service = self.edit_service
        self.edit_tab.metadata_service = self.metadata_service
        self.export_tab.project_service = self.project_service
        self.export_tab.preset_service = self.preset_service
        self.export_tab.export_service = self.export_service
        self.export_tab.job_queue_service = self.job_queue_service
        self.presets_tab.preset_service = self.preset_service
        self._append_job_event("Migration stockage terminee et runtime recharge.")
        self.refresh_all()

    def refresh_all(self) -> None:
        self.dashboard_tab.refresh_data()
        self.hub_tab.refresh_data()
        self.import_tab.refresh_data()
        self.culling_tab.refresh_data()
        self.edit_tab.refresh_data()
        self.export_tab.refresh_data()
        self.rename_tab.refresh_data()
        self.presets_tab.refresh_data()
        self.settings_tab.refresh_data()
        self._refresh_project_context_combo()
        self.jobs_tab.refresh_data()
        self._update_activity_badge()

    def _apply_sprint1_style(self) -> None:
        accent = normalize_accent_color(self.accent_color)
        accent_hover = _lighter(accent, 15)
        accent_pressed = _darker(accent, 20)
        accent_muted = _blend(accent, "#1A1D21", 0.78)
        accent_soft = _rgba(accent, 32)
        accent_soft_hover = _rgba(accent, 56)
        accent_subtle = _blend(accent, "#1A1D21", 0.68)
        accent_subtle_hover = _lighter(accent_subtle, 8)
        accent_subtle_pressed = _darker(accent_subtle, 10)
        accent_subtle_soft = _rgba(accent_subtle, 34)
        accent_subtle_soft_hover = _rgba(accent_subtle, 62)
        # Photoshop-like neutral grayscale palette (no blue tint).
        bg_app = "#121212"
        bg_panel = "#1A1A1A"
        bg_card = "#242424"
        bg_hover = "#2D2D2D"
        border_subtle = "#3A3A3A"
        border_focus = "#545454"
        text_primary = "#E8E8E8"
        text_secondary = "#B2B2B2"
        text_muted = "#7A7A7A"
        scrollbar_track = "#1A1A1A"
        scrollbar_handle = "#4A4A4A"
        scrollbar_handle_hover = "#5D5D5D"
        scrollbar_handle_pressed = "#707070"

        self.setStyleSheet(
            """
            QMainWindow {
                background: %(bg_app)s;
            }
            QWidget {
                background: transparent;
                color: %(text_primary)s;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QToolTip {
                background: #1E1E1E;
                color: #C8C8C8;
                border: 1px solid #3A3A3A;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                font-family: "Segoe UI";
            }
            QLabel {
                background: transparent;
                color: %(text_primary)s;
            }
            QWidget#SideBar {
                background: %(bg_panel)s;
                border: 1px solid %(border_subtle)s;
                border-radius: 14px;
                color: %(text_primary)s;
                padding: 8px;
            }
            QPushButton#SideBarToggle {
                text-align: center;
                border-radius: 8px;
                border: 1px solid %(border_subtle)s;
                background: %(bg_card)s;
                color: %(text_primary)s;
                padding: 6px 0;
                margin: 0 0 6px 0;
            }
            QPushButton#SideBarToggle:hover {
                background: %(bg_hover)s;
                border-color: %(accent_subtle_hover)s;
            }
            QPushButton[navButton="true"] {
                text-align: left;
                border-radius: 8px;
                padding: 10px 12px;
                margin: 2px 0;
                border: 1px solid transparent;
                background: transparent;
                color: %(text_primary)s;
            }
            QPushButton[navButton="true"][collapsed="true"] {
                text-align: center;
                padding: 10px 0;
            }
            QPushButton[navButton="true"]:hover {
                background: %(bg_hover)s;
                border-color: %(border_focus)s;
            }
            QPushButton[navButton="true"][active="true"] {
                background: %(accent_subtle_soft)s;
                border-color: %(accent_subtle_hover)s;
                color: %(text_primary)s;
            }
            #TopBar {
                border: 1px solid %(border_subtle)s;
                border-radius: 12px;
                background: %(bg_panel)s;
            }
            #AppTitle {
                font-size: 18px;
                font-weight: 700;
                color: %(text_primary)s;
            }
            #ActivityBadge {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                padding: 4px 10px;
                background: %(bg_card)s;
                color: %(text_secondary)s;
                font-weight: 600;
            }
            #ContextModeBadge {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                padding: 3px 10px;
                background: %(bg_card)s;
                color: %(text_primary)s;
                font-weight: 700;
            }
            #ContextHintLabel {
                color: %(text_secondary)s;
                padding-left: 6px;
            }
            #PageTitle {
                font-size: 20px;
                font-weight: 700;
                color: %(text_primary)s;
            }
            #StatCard {
                border: 1px solid %(border_subtle)s;
                border-radius: 12px;
                background: %(bg_panel)s;
            }
            #StatValue {
                font-size: 26px;
                font-weight: 700;
                color: %(accent_subtle_hover)s;
            }
            QFrame#DataCard {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_card)s;
            }
            QFrame#DataCard[selected="true"] {
                border-color: %(accent_subtle_hover)s;
                background: %(accent_subtle_soft)s;
            }
            QLabel#CardTitle {
                color: %(text_primary)s;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#CardValue {
                color: %(text_secondary)s;
                background: transparent;
            }
            QLabel#CardMuted {
                color: %(text_muted)s;
                background: transparent;
                padding: 8px 4px;
            }
            QLabel#CardBadge {
                border: 1px solid %(border_subtle)s;
                border-radius: 9px;
                background: %(bg_panel)s;
                color: %(text_secondary)s;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QWidget#CardDetails {
                border-top: 1px solid %(border_subtle)s;
                background: transparent;
                padding-top: 12px;
            }
            QFrame#PreviewFrame {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_panel)s;
            }
            QLabel#PreviewLabel {
                background: %(bg_panel)s;
                border-radius: 8px;
                color: %(text_muted)s;
            }
            QLabel#CullingMeta {
                color: %(text_secondary)s;
                padding-left: 2px;
            }
            QLabel#CullingHud {
                border-radius: 10px;
                padding: 6px 10px;
                margin: 8px;
                font-size: 11px;
                font-weight: 700;
                color: %(text_primary)s;
                background: %(accent_subtle_hover)s;
                border: 1px solid %(accent_subtle_hover)s;
            }
            QLabel#CullingHud[hudState="warn"] {
                color: #FFF1F1;
                background: #8B2C2C;
                border-color: #A83A3A;
            }
            QLabel#CullingHud[hudState="info"] {
                color: %(text_primary)s;
                background: #2C2C2C;
                border-color: #4A4A4A;
            }
            QLabel#PreviewInfoOverlay {
                background: rgba(10, 12, 16, 128);
                color: #E9EEF2;
                border-radius: 6px;
                padding: 6px 10px;
                margin: 8px;
            }
            QLabel#PreviewPathOverlay {
                background: rgba(10, 12, 16, 128);
                color: #B8C1CA;
                border-radius: 6px;
                padding: 6px 10px;
                margin: 8px;
            }
            QFrame#FilmstripFrame {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_panel)s;
            }
            QToolButton#FilmThumb {
                border: 1px solid %(border_subtle)s;
                border-radius: 7px;
                background: %(bg_card)s;
                padding: 2px;
            }
            QToolButton#FilmThumb:hover {
                border-color: %(border_focus)s;
                background: %(bg_hover)s;
            }
            QToolButton#FilmThumb[selected="true"] {
                border-color: %(accent_subtle_hover)s;
                background: %(accent_subtle_soft)s;
            }
            QGroupBox#BatchPanel {
                border: 1px solid %(border_subtle)s;
                background: %(bg_panel)s;
            }
            QFrame#EditDock {
                border: 1px solid %(border_subtle)s;
                border-radius: 12px;
                background: %(bg_panel)s;
            }
            QFrame#EditHeaderBar {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_panel)s;
            }
            QLabel#EditFilterLabel {
                color: rgba(233, 238, 244, 150);
                font-size: 11px;
                font-weight: 500;
                font-family: "Roboto", "Segoe UI";
                background: transparent;
            }
            QLabel#PresetFieldLabel {
                color: %(text_muted)s;
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                padding-bottom: 2px;
            }
            QLabel#SectionTitle {
                font-weight: 700;
                font-size: 14px;
                color: %(text_primary)s;
                padding-top: 10px;
                padding-bottom: 5px;
            }
            QFrame#PresetFormPanel {
                background: transparent;
            }
            QWidget#PresetAccordionHeader {
                background: %(bg_card)s;
                border: 1px solid %(border_subtle)s;
                border-radius: 8px;
            }
            QWidget#PresetAccordionHeader:hover {
                background: %(bg_hover)s;
                border-color: %(border_focus)s;
            }
            QLabel#PresetAccordionTitle {
                font-weight: 600;
                font-size: 13px;
                color: %(text_primary)s;
            }
            QLabel#PresetAccordionSubtitle {
                font-size: 11px;
                color: %(text_muted)s;
            }
            QFrame#PresetAccordionBody {
                background: rgba(43, 44, 48, 80);
                border: 1px solid %(border_subtle)s;
                border-top: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                padding: 10px;
            }
            QFrame#PresetSidebar {
                border-left: 1px solid %(border_subtle)s;
                background: %(bg_panel)s;
                border-radius: 10px;
            }
            QLineEdit#PresetSearch {
                min-height: 30px;
            }
            QFrame#PresetActionBar {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_card)s;
            }
            QGroupBox#PresetSectionBox {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                margin-top: 10px;
                background: %(bg_card)s;
                padding-top: 6px;
            }
            QGroupBox#PresetSectionBox::title {
                left: 12px;
                padding: 0 2px;
                color: #AAB4BE;
                font-size: 10px;
                font-weight: 700;
                background: transparent;
            }
            QGroupBox#PresetProfileCard {
                border: 1px solid #444444;
                border-radius: 12px;
                margin-top: 0px;
                background: #2A2A2A;
                padding-top: 0px;
            }
            QGroupBox#PresetProfileCard:hover {
                border-color: %(accent_subtle_hover)s;
            }
            QFrame#PresetProfileHeader {
                border-bottom: 1px solid %(border_subtle)s;
                border-top-left-radius: 11px;
                border-top-right-radius: 11px;
                border-left: 3px solid %(accent_subtle_hover)s;
                background: #303030;
            }
            QLabel#PresetProfileTitle {
                color: %(text_primary)s;
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.4px;
                background: transparent;
            }
            QLabel#PresetProfileHint {
                color: %(text_secondary)s;
                font-size: 11px;
                background: transparent;
            }
            QGroupBox#PresetProfileCard QLabel#PresetProfileFieldLabel {
                color: #BAC3CF;
                font-size: 11px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#EditAssetListTitle {
                color: #C6C6C6;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 2px 4px 0 4px;
            }
            QLabel#EditThumb {
                border: 1px solid %(border_subtle)s;
                border-radius: 6px;
                background: %(bg_panel)s;
            }
            QGroupBox#EditParamGroup, QGroupBox#EditActionGroup {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                margin-top: 10px;
                background: %(bg_card)s;
                padding-top: 6px;
            }
            
            /* Bento Card & Layout Components */
            QFrame#BentoCard {
                background: %(bg_card)s;
                border: 1px solid %(border_subtle)s;
                border-radius: 16px; 
            }
            QLabel#BentoCardTitle {
                font-size: 14px;
                font-weight: 700;
                color: %(text_secondary)s;
                padding-left: 2px;
            }
            QWidget#BentoCardContent {
                background: transparent;
            }

            /* Workflow Status Bar */
            QFrame#WorkflowStatusBar {
                border-radius: 18px;
                background: %(bg_panel)s;
                border: 1px solid %(border_subtle)s;
            }
            QPushButton#WorkflowStepButton {
                border: none;
                background: transparent;
                color: %(text_secondary)s;
                font-weight: 600;
                border-radius: 14px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton#WorkflowStepButton:hover {
                color: %(text_primary)s;
                background: %(bg_hover)s;
            }
            QPushButton#WorkflowStepButton[active="true"] {
                color: %(text_primary)s;
                background: %(accent_subtle_soft)s;
                border: 1px solid %(accent_subtle_hover)s;
            }

            QGroupBox#EditParamGroup::title, QGroupBox#EditActionGroup::title {
                left: 12px;
                padding: 0 2px;
                color: #AAB4BE;
                font-size: 10px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#EditFieldLabel {
                color: #C4C4C4;
                font-size: 12px;
                font-weight: 500;
                font-family: "Segoe UI";
                background: transparent;
            }
            QLabel#EditFieldValue {
                color: %(text_primary)s;
                background: #2B2B2B;
                border: 1px solid #4A4A4A;
                border-radius: 6px;
                padding: 2px 6px;
                font-weight: 600;
            }
            QSlider::groove:horizontal {
                border: none;
                height: 8px;
                border-radius: 4px;
                background: #3B3B3B;
            }
            QSlider::sub-page:horizontal {
                border: none;
                border-radius: 4px;
                background: %(accent_subtle)s;
            }
            QSlider::add-page:horizontal {
                border: none;
                border-radius: 4px;
                background: #2E2E2E;
            }
            QSlider::handle:horizontal {
                width: 16px;
                margin: -5px 0;
                border: 1px solid #D0D0D0;
                border-radius: 8px;
                background: #F0F0F0;
            }
            QSlider::handle:horizontal:hover {
                border-color: %(accent_subtle_hover)s;
            }
            QPushButton[cardSelect="true"] {
                text-align: left;
                border: none;
                border-radius: 4px;
                background: transparent;
                color: %(text_primary)s;
                font-family: "Segoe UI", "Roboto", sans-serif;
                font-size: 14px;
                font-weight: 700;
                padding: 2px 4px;
            }
            QPushButton[cardSelect="true"]:hover {
                background: %(bg_hover)s;
                border-color: %(border_focus)s;
            }
            
            /* Preset/Settings Accordions & Specialized Cards */
            QFrame#PresetAccordionHeader {
                background: %(bg_card)s;
                border: 1px solid %(border_subtle)s;
                border-radius: 12px;
            }
            QFrame#PresetAccordionHeader:hover {
                border-color: %(accent_subtle_hover)s;
                background: %(bg_hover)s;
            }
            QFrame#PresetAccordionHeader[opened="true"] {
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                border-color: %(accent_subtle_hover)s;
                background: %(bg_panel)s;
            }
            QFrame#PresetAccordionBody {
                background: %(bg_panel)s;
                border: 1px solid %(accent_subtle_hover)s;
                border-top: none;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }
            QLabel#PresetAccordionTitle {
                font-size: 13px;
                font-weight: 700;
                color: %(text_primary)s;
            }
            QLabel#PresetAccordionSubtitle {
                font-size: 11px;
                color: %(text_muted)s;
            }
            
            QLabel#PresetFieldLabel {
                color: %(text_muted)s;
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.2px;
                text-transform: uppercase;
                margin-bottom: 2px;
            }
            QLabel#EditFieldLabel {
                color: %(text_muted)s;
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.2px;
                text-transform: none;
                margin-bottom: 2px;
            }
            QLabel#EditFieldValue {
                color: %(accent_subtle_hover)s;
                font-family: "Consolas", monospace;
                font-size: 11px;
                font-weight: 700;
            }
            
            /* Generic Data Badge (e.g. Migration Status) */
            QLabel#StatusBadge {
                background: #1E1E1E;
                border: 1px solid #333;
                border-radius: 6px;
                padding: 4px 10px;
                color: %(text_secondary)s;
                font-size: 11px;
                font-family: "Consolas", monospace;
            }
            QToolButton#IngestBentoButton {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_card)s;
                color: %(text_primary)s;
                font-weight: 600;
                padding: 8px 6px;
            }
            QToolButton#IngestBentoButton:hover {
                border-color: %(accent_subtle_hover)s;
                background: %(bg_hover)s;
            }
            QToolButton#IngestBentoButton:pressed {
                border-color: %(accent_subtle_pressed)s;
                background: %(bg_panel)s;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            /* Global Scrollbar Policy: Hidden by default or very minimal */
            QScrollBar:vertical {
                background: transparent;
                width: 0px; /* Hidden by default per req, specific widgets override this later if needed */
                margin: 0px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: %(scrollbar_handle)s;
                min-height: 44px;
                border-radius: 4px;
                margin: 0 2px 0 2px;
            }
            /* Only show minimal width when explicit classes request it */
            QPlainTextEdit > QScrollBar:vertical, QTableWidget > QScrollBar:vertical, QScrollArea[minimalScroll="true"] > QScrollBar:vertical {
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: %(scrollbar_handle)s;
                min-height: 44px;
                border-radius: 6px;
                margin: 0 2px 0 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: %(scrollbar_handle_hover)s;
            }
            QScrollBar::handle:vertical:pressed {
                background: %(scrollbar_handle_pressed)s;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
                border: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: %(scrollbar_track)s;
                border-radius: 6px;
                margin: 0 2px 0 2px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 12px;
                margin: 2px 4px 2px 4px;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: %(scrollbar_handle)s;
                min-width: 44px;
                border-radius: 6px;
                margin: 2px 0 2px 0;
            }
            QScrollBar::handle:horizontal:hover {
                background: %(scrollbar_handle_hover)s;
            }
            QScrollBar::handle:horizontal:pressed {
                background: %(scrollbar_handle_pressed)s;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
                background: transparent;
                border: none;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: %(scrollbar_track)s;
                border-radius: 6px;
                margin: 2px 0 2px 0;
            }
            QAbstractScrollArea::corner {
                background: transparent;
            }
            QGroupBox {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 8px;
                background: %(bg_panel)s;
            }
            QGroupBox::title {
                left: 10px;
                padding: 0 4px;
                color: %(text_secondary)s;
                background: transparent;
            }
            QTabWidget::pane {
                border: 1px solid %(border_subtle)s;
                border-radius: 10px;
                background: %(bg_panel)s;
                top: -1px;
            }
            QTabBar::tab {
                background: %(bg_card)s;
                border: 1px solid %(border_subtle)s;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                color: %(text_secondary)s;
                min-width: 90px;
                padding: 6px 12px;
                margin-right: 4px;
            }
            QTabBar::tab:hover {
                color: %(text_primary)s;
                border-color: %(border_focus)s;
            }
            QTabBar::tab:selected {
                color: %(text_primary)s;
                border-color: %(accent_subtle_hover)s;
                background: %(bg_hover)s;
            }
            QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget {
                border: 1px solid transparent;
                border-radius: 8px;
                background: %(bg_card)s;
                color: %(text_primary)s;
                padding: 4px 6px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTableWidget:focus {
                border: 1px solid %(accent)s;
            }
            QTableWidget::item:selected {
                background: %(accent_soft_hover)s;
                color: %(text_primary)s;
            }
            QHeaderView::section {
                background: %(bg_card)s;
                color: %(text_secondary)s;
                border: none;
                border-bottom: 1px solid %(border_subtle)s;
                padding: 6px;
            }
            QProgressBar {
                border: 1px solid %(border_subtle)s;
                border-radius: 7px;
                background: %(bg_card)s;
                color: %(text_secondary)s;
                text-align: center;
            }
            QProgressBar::chunk {
                background: %(accent_subtle_hover)s;
                border-radius: 6px;
            }
            QPushButton {
                border: 1px solid %(border_subtle)s;
                border-radius: 8px;
                background: %(bg_card)s;
                color: %(text_primary)s;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: %(bg_hover)s;
                border-color: %(border_focus)s;
            }
            QPushButton:pressed {
                background: %(bg_panel)s;
            }
            QPushButton[isPrimaryButton="true"] {
                background: %(accent_subtle_soft)s;
                border-color: %(accent_subtle)s;
                color: %(text_primary)s;
                font-weight: 600;
            }
            QPushButton[isPrimaryButton="true"]:hover {
                background: %(accent_subtle_soft_hover)s;
                border-color: %(accent_subtle_hover)s;
            }
            QPushButton[isPrimaryButton="true"]:pressed {
                background: %(accent_subtle_soft)s;
                border-color: %(accent_subtle_pressed)s;
                color: %(text_primary)s;
            }
            QPushButton:disabled {
                background: %(bg_panel)s;
                border-color: %(border_subtle)s;
                color: %(text_muted)s;
            }
                border-color: %(border_subtle)s;
                color: %(text_muted)s;
            }
            QPushButton#TinderKeepBtn {
                background: rgba(34, 197, 94, 0.15);
                border: 1px solid rgba(34, 197, 94, 0.3);
                border-radius: 12px;
                color: #4ade80;
                font-weight: bold;
                font-size: 14px;
                padding: 10px;
            }
            QPushButton#TinderKeepBtn:hover {
                background: rgba(34, 197, 94, 0.25);
                border-color: #22c55e;
                color: white;
            }
            QPushButton#TinderKeepBtn:pressed {
                background: #166534;
            }
            
            QPushButton#TinderRejectBtn {
                background: rgba(239, 68, 68, 0.15);
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 12px;
                color: #f87171;
                font-weight: bold;
                font-size: 14px;
                padding: 10px;
            }
            QPushButton#TinderRejectBtn:hover {
                background: rgba(239, 68, 68, 0.25);
                border-color: #ef4444;
                color: white;
            }
            QPushButton#TinderRejectBtn:pressed {
                background: #991b1b;
            }

            QToolButton#TinderStarBtn {
                background: transparent;
                border: none;
                color: #71717A;
                font-size: 20px;
            }
            QToolButton#TinderStarBtn:hover {
                color: #FACC15;
            }
            QToolButton#TinderStarBtn:checked {
                color: #FDE047;
            }
            """
            % {
                "accent": accent,
                "accent_hover": accent_hover,
                "accent_pressed": accent_pressed,
                "accent_muted": accent_muted,
                "accent_soft": accent_soft,
                "accent_soft_hover": accent_soft_hover,
                "accent_subtle": accent_subtle,
                "accent_subtle_hover": accent_subtle_hover,
                "accent_subtle_pressed": accent_subtle_pressed,
                "accent_subtle_soft": accent_subtle_soft,
                "accent_subtle_soft_hover": accent_subtle_soft_hover,
                "bg_app": bg_app,
                "bg_panel": bg_panel,
                "bg_card": bg_card,
                "bg_hover": bg_hover,
                "border_subtle": border_subtle,
                "border_focus": border_focus,
                "text_primary": text_primary,
                "text_secondary": text_secondary,
                "text_muted": text_muted,
                "scrollbar_track": scrollbar_track,
                "scrollbar_handle": scrollbar_handle,
                "scrollbar_handle_hover": scrollbar_handle_hover,
                "scrollbar_handle_pressed": scrollbar_handle_pressed,
            }
        )


# HubTab removed, replaced by ProjectsTab in .projects.py


class BatchRenameTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        culling_service: CullingService,
        rename_service: RenameService,
        on_data_changed,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.culling_service = culling_service
        self.rename_service = rename_service
        self.on_data_changed = on_data_changed
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)

        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None
        self._last_auto_pattern = "{project}_{date}_{seq:04d}"
        self._asset_order: list[int] = []
        self._asset_checks: dict[int, QCheckBox] = {}
        self._loading_ui = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._selected_project_id: Optional[int] = None

        # ── 1. Top Control Bar ──
        controls_card = BentoCard("Configuration du renommage")
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(12, 8, 12, 8)
        controls_layout.setSpacing(16)

        # Filters (Consolidated in top bar)
        filter_group = QHBoxLayout()
        filter_group.setSpacing(8)
        filter_group.addWidget(QLabel("Filtre:"))
        self.rejected_mode_combo = QComboBox()
        self.rejected_mode_combo.addItem("Tout", userData="all")
        self.rejected_mode_combo.addItem("A garder", userData="kept")
        self.rejected_mode_combo.addItem("Rejetees", userData="rejected")
        self.rejected_mode_combo.setFixedWidth(100)
        self.rejected_mode_combo.currentIndexChanged.connect(self._load_assets)
        filter_group.addWidget(self.rejected_mode_combo)

        self.min_rating_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_combo.addItem(str(rating), userData=rating)
        self.min_rating_combo.setFixedWidth(50)
        self.min_rating_combo.currentIndexChanged.connect(self._load_assets)
        filter_group.addWidget(QLabel("★"))
        filter_group.addWidget(self.min_rating_combo)
        
        controls_layout.addLayout(filter_group)
        controls_layout.addSpacing(10)

        # Pattern & Sequence
        controls_layout.addWidget(QLabel("Modèle:"))
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText("{project}_{date}_{seq:04d}")
        self.pattern_edit.setToolTip(
            "Tags disponibles :\n"
            "  {project}  — Nom du projet\n"
            "  {date}       — Date de shooting (YYYYMMDD)\n"
            "  {seq:04d}  — Numéro séquentiel (ex: 0001)\n"
            "  {orig}       — Nom original du fichier"
        )
        self.pattern_edit.textChanged.connect(self._refresh_preview)
        pattern_container = QVBoxLayout()
        pattern_container.setSpacing(2)
        pattern_container.addWidget(self.pattern_edit)
        pattern_hint = QLabel("Tags : {project}  {date}  {seq:04d}  {orig}")
        pattern_hint.setStyleSheet("color: #777; font-size: 9px; font-family: Consolas, monospace; background: transparent;")
        pattern_container.addWidget(pattern_hint)
        controls_layout.addLayout(pattern_container, 1)

        controls_layout.addWidget(QLabel("Séquence:"))
        self.start_seq_spin = QSpinBox()
        self.start_seq_spin.setRange(1, 999999)
        self.start_seq_spin.setValue(1)
        self.start_seq_spin.setFixedWidth(80)
        self.start_seq_spin.valueChanged.connect(self._refresh_preview)
        controls_layout.addWidget(self.start_seq_spin)

        # Main Action
        self.run_btn = _new_button("Renommer", primary=True)
        self.run_btn.setFixedWidth(110)
        self.run_btn.clicked.connect(self._run_rename)
        controls_layout.addWidget(self.run_btn)

        controls_card.content_layout.addLayout(controls_layout)
        layout.addWidget(controls_card)

        # ── 2. Middle Row (Selection & Preview) ──
        main_content = QHBoxLayout()
        main_content.setSpacing(10)

        # 2a. Selection Column
        select_card = BentoCard("Sélection des fichiers")
        select_content = QVBoxLayout()
        
        select_actions = QHBoxLayout()
        self.select_all_btn = _new_button("Tout")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn = _new_button("Aucun")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        self.summary_label = QLabel("0 sélectionné")
        self.summary_label.setObjectName("CullingMeta")
        
        select_actions.addWidget(self.select_all_btn)
        select_actions.addWidget(self.select_none_btn)
        select_actions.addStretch(1)
        select_actions.addWidget(self.summary_label)
        select_content.addLayout(select_actions)

        self.assets_area = QScrollArea()
        self.assets_area.setWidgetResizable(True)
        self.assets_area.setFrameShape(QFrame.Shape.NoFrame)
        self.assets_content = QWidget()
        self.assets_layout = QVBoxLayout(self.assets_content)
        self.assets_layout.setContentsMargins(0, 4, 0, 4)
        self.assets_layout.setSpacing(2)
        self.assets_area.setWidget(self.assets_content)
        select_content.addWidget(self.assets_area)
        
        select_card.content_layout.addLayout(select_content)
        main_content.addWidget(select_card, 1)

        # 2b. Preview Column
        preview_card = BentoCard("Aperçu du renommage")
        preview_content = QVBoxLayout()
        
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        # Styled like a terminal preview
        self.preview_text.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0F172A;
                color: #94A3B8;
                font-family: 'Consolas', 'Monaco', monospace;
                border: none;
                border-radius: 4px;
            }
        """)
        preview_content.addWidget(self.preview_text)
        
        preview_card.content_layout.addLayout(preview_content)
        main_content.addWidget(preview_card, 1)

        layout.addLayout(main_content, 1)

        # ── 3. Bottom Progress Area ──
        progress_container = QWidget()
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(10, 0, 10, 10)
        progress_layout.setSpacing(4)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        
        self.job_status_label = QLabel("Prêt.")
        self.job_status_label.setObjectName("CullingMeta")
        self.job_status_label.setStyleSheet("color: #94A3B8; font-size: 11px;")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.job_status_label)
        layout.addWidget(progress_container)

        # Dummy/Legacy references
        self.cancel_btn = QWidget()
        self.cancel_btn.setEnabled = lambda x: None # Mock for compatibility
        self.main_splitter = None

    def refresh_data(self) -> None:
        pass # Combo removed.

    def set_selected_project(self, project_id: int) -> None:
        self._selected_project_id = project_id
        self._on_project_changed()

    def _on_project_changed(self) -> None:
        self._sync_pattern_from_project()
        self._load_assets()

    def _sync_pattern_from_project(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            return
        pattern = "{project}_{date}_{seq:04d}"
        project = self.project_service.get_project(int(project_id))
        if project is not None and project.preset is not None:
            try:
                payload = json.loads(project.preset.config_json or "{}")
                naming = payload.get("naming", {})
                candidate = str(naming.get("pattern", "")).strip()
                if candidate:
                    pattern = candidate
            except Exception:
                pass
        current = self.pattern_edit.text().strip()
        if not current or current == self._last_auto_pattern:
            self.pattern_edit.setText(pattern)
        self._last_auto_pattern = pattern

    def _load_assets(self) -> None:
        project_id = self._selected_project_id
        current_checked = set(self._selected_asset_ids())
        self._clear_asset_cards()
        self._asset_order = []
        self._asset_checks = {}

        if project_id is None:
            self.preview_text.setPlainText("Selectionne un projet.")
            self.summary_label.setText("0 sélectionné")
            self.run_btn.setEnabled(False)
            return

        self._loading_ui = True
        try:
            assets = self.culling_service.list_assets(
                int(project_id),
                rejected_mode=str(self.rejected_mode_combo.currentData() or "all"),
                min_rating=int(self.min_rating_combo.currentData() or 0),
            )
            if not assets:
                empty = QLabel("Aucune photo avec ce filtre.")
                empty.setObjectName("CardMuted")
                self.assets_layout.addWidget(empty)
                self.assets_layout.addStretch(1)
                self._refresh_preview()
                return

            for asset in assets:
                card = QFrame()
                card.setObjectName("DataCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(12, 10, 12, 10)
                card_layout.setSpacing(4)

                top_row = QHBoxLayout()
                check = QCheckBox(Path(str(asset.src_path)).name)
                check.setChecked(asset.id in current_checked if current_checked else True)
                check.toggled.connect(self._refresh_preview)
                check.setToolTip(str(asset.src_path))
                top_row.addWidget(check, 1)
                badge = QLabel(f"note {int(asset.rating)}")
                badge.setObjectName("CardBadge")
                top_row.addWidget(badge)
                card_layout.addLayout(top_row)

                self._asset_order.append(int(asset.id))
                self._asset_checks[int(asset.id)] = check
                self.assets_layout.addWidget(card)
            self.assets_layout.addStretch(1)
        finally:
            self._loading_ui = False
        self._refresh_preview()

    def _clear_asset_cards(self) -> None:
        while self.assets_layout.count():
            item = self.assets_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_all_checked(self, checked: bool) -> None:
        for checkbox in self._asset_checks.values():
            checkbox.setChecked(bool(checked))
        self._refresh_preview()

    def _selected_asset_ids(self) -> list[int]:
        selected: list[int] = []
        for asset_id in self._asset_order:
            check = self._asset_checks.get(int(asset_id))
            if check is not None and check.isChecked():
                selected.append(int(asset_id))
        return selected

    def _refresh_preview(self) -> None:
        if self._loading_ui:
            return
        project_id = self._selected_project_id
        if project_id is None:
            self.preview_text.setPlainText("Selectionne un projet.")
            self.summary_label.setText("0 sélectionné")
            self.run_btn.setEnabled(False)
            return

        selected_ids = self._selected_asset_ids()
        if not selected_ids:
            self.preview_text.setPlainText("Selectionne au moins une photo.")
            self.summary_label.setText("0 sélectionné")
            self.run_btn.setEnabled(False)
            return

        pattern = self.pattern_edit.text().strip() or "{project}_{date}_{seq:04d}"
        start_seq = int(self.start_seq_spin.value())
        try:
            preview = self.rename_service.preview_batch_rename(
                project_id=int(project_id),
                asset_ids=selected_ids,
                pattern=pattern,
                start_seq=start_seq,
            )
        except Exception as exc:
            self.preview_text.setPlainText(f"Erreur preview: {exc}")
            self.summary_label.setText(f"{len(selected_ids)} sélectionnés (0 à renommer)")
            self.run_btn.setEnabled(False)
            return

        changed = 0
        lines: list[str] = []
        limit = 250
        for idx, item in enumerate(preview, start=1):
            src_name = Path(item.source_path).name
            dst_name = Path(item.target_path).name
            if src_name != dst_name:
                changed += 1
            arrow = "->" if src_name != dst_name else "="
            if idx <= limit:
                lines.append(f"{idx:04d}. {src_name} {arrow} {dst_name}")
        if len(preview) > limit:
            lines.append(f"... ({len(preview) - limit} ligne(s) masquees)")

        self.preview_text.setPlainText("\n".join(lines) if lines else "Aucun item.")
        self.summary_label.setText(f"{len(selected_ids)} sélectionnés ({changed} à renommer)")
        self.run_btn.setEnabled(changed > 0 and self._job_thread is None)

    def _run_rename(self) -> None:
        if self._job_thread is not None:
            QMessageBox.warning(self, "Operation en cours", "Un renommage est deja en cours.")
            return
        project_id = self._selected_project_id
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return
        selected_ids = self._selected_asset_ids()
        if not selected_ids:
            QMessageBox.warning(self, "Validation", "Selectionne au moins une photo.")
            return

        confirmation = QMessageBox.question(
            self,
            "Confirmer renommage",
            f"Lancer le renommage de {len(selected_ids)} photo(s) ?",
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        pattern = self.pattern_edit.text().strip() or "{project}_{date}_{seq:04d}"
        start_seq = int(self.start_seq_spin.value())
        self.progress_bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.select_all_btn.setEnabled(False)
        self.select_none_btn.setEnabled(False)

        worker = JobWorker(
            self.rename_service.run_batch_rename,
            project_id=int(project_id),
            asset_ids=list(selected_ids),
            pattern=pattern,
            start_seq=start_seq,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._job_worker = worker
        self._job_thread = thread
        self.on_operation_started()
        self.on_job_event(f"[Rename] start | project={project_id} | count={len(selected_ids)}")
        thread.start()

    def _cancel_rename(self) -> None:
        if self._job_worker is not None:
            self._job_worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.on_job_event("[Rename] Annulation demandee.")

    def _on_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(max(0, min(int(done), safe_total)))
        if detail:
            self.job_status_label.setText(f"Progression: {done}/{safe_total} | {detail}")

    def _on_result(self, result) -> None:
        status = str(getattr(result, "status", "completed"))
        renamed = int(getattr(result, "renamed", 0))
        skipped = int(getattr(result, "skipped", 0))
        failed = int(getattr(result, "failed", 0))
        message = str(getattr(result, "message", "")).strip()

        self.on_job_event(
            f"[Rename] {status} | renamed={renamed}, skipped={skipped}, failed={failed}"
        )
        if status == "completed":
            QMessageBox.information(self, "Batch rename", message or "Renommage termine.")
            self.on_data_changed()
        elif status == "cancelled":
            QMessageBox.information(self, "Batch rename", message or "Renommage annule.")
            self.refresh_data()
        else:
            QMessageBox.warning(self, "Batch rename", message or "Le renommage a echoue.")
            self.refresh_data()

    def _on_error(self, message: str) -> None:
        self.on_job_event(f"[Rename] Erreur: {message}")
        QMessageBox.critical(self, "Batch rename", str(message))

    def _on_finished(self) -> None:
        self.on_operation_ended()
        self._job_worker = None
        self._job_thread = None
        self.cancel_btn.setEnabled(False)
        self.select_all_btn.setEnabled(True)
        self.select_none_btn.setEnabled(True)
        self.refresh_data()

    def reset_layout_after_shell_resize(self) -> None:
        splitter = getattr(self, "main_splitter", None)
        if splitter is None:
            return
        total = max(1, int(splitter.width()))
        left = max(420, int(total * 0.62))
        right = max(300, total - left)
        splitter.setSizes([left, right])

class ImportExportTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        preset_service: PresetService,
        culling_service: CullingService,
        edit_service: EditService,
        metadata_service: MetadataService,
        import_service: ImportService,
        export_service: ExportService,
        job_queue_service: JobQueueService,
        on_data_changed,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.import_tab = ImportTab(
            project_service,
            import_service,
            on_data_changed=on_data_changed,
            on_operation_started=on_operation_started,
            on_operation_ended=on_operation_ended,
            on_job_event=on_job_event,
        )
        self.culling_tab = CullingTab(
            project_service=project_service,
            culling_service=culling_service,
            on_data_changed=on_data_changed,
            on_operation_started=on_operation_started,
            on_operation_ended=on_operation_ended,
            on_job_event=on_job_event,
        )
        self.edit_tab = EditTab(
            project_service=project_service,
            edit_service=edit_service,
            metadata_service=metadata_service,
            on_operation_started=on_operation_started,
            on_operation_ended=on_operation_ended,
            on_job_event=on_job_event,
        )
        self.export_tab = ExportTab(
            project_service,
            preset_service,
            export_service,
            job_queue_service,
            on_operation_started=on_operation_started,
            on_operation_ended=on_operation_ended,
            on_job_event=on_job_event,
        )

        layout = QVBoxLayout(self)
        # Sprint 1: remove internal tabs and keep only page-level navigation from sidebar.
        self.sections = QStackedWidget()
        self.sections.addWidget(self.import_tab)
        self.sections.addWidget(self.culling_tab)
        self.sections.addWidget(self.edit_tab)
        self.sections.addWidget(self.export_tab)
        layout.addWidget(self.sections)

    def refresh_data(self) -> None:
        self.import_tab.refresh_data()
        self.culling_tab.refresh_data()
        self.edit_tab.refresh_data()
        self.export_tab.refresh_data()

    def set_current_section(self, section: str) -> None:
        normalized = (section or "").strip().lower()
        index_map = {
            "import": 0,
            "ingest": 0,
            "culling": 1,
            "tri": 1,
            "edit": 2,
            "export": 3,
        }
        idx = index_map.get(normalized)
        if idx is None:
            return
        self.sections.setCurrentIndex(idx)

    def set_selected_project(self, project_id: int) -> None:
        self.import_tab.set_selected_project(project_id)
        self.culling_tab.set_selected_project(project_id)
        self.edit_tab.set_selected_project(project_id)
        self.export_tab.set_selected_project(project_id)


class ImportTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        import_service: ImportService,
        on_data_changed,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.import_service = import_service
        self.on_data_changed = on_data_changed
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)
        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._selected_project_id: Optional[int] = None
        # Redundant project selector removed, now follows global context from header.

        # ── 1. Top Control Bar ──
        controls_card = BentoCard("Importation des fichiers")
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(12, 8, 12, 8)
        controls_layout.setSpacing(12)
        
        # Source Selection
        controls_layout.addWidget(QLabel("Dossier Source:"))
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Parcourir un dossier ou une carte SD...")
        
        browse_btn = _new_button("Parcourir")
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(self._pick_source)
        
        controls_layout.addWidget(self.source_edit, 1) # Stretch to fill space
        controls_layout.addWidget(browse_btn)
        
        # Action Button
        self.run_btn = _new_button("Importer", primary=True)
        self.run_btn.setFixedWidth(120)
        self.run_btn.clicked.connect(self._run_import)
        
        controls_layout.addWidget(self.run_btn)
        
        controls_card.content_layout.addLayout(controls_layout)
        layout.addWidget(controls_card)

        # ── 2. Journal area ──
        self.log_card = BentoCard("Journal d'importation")
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        # Style the log for a terminal look (dark, monospaced)
        self.log_text.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0F172A;
                color: #E2E8F0;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 13px;
                border: none;
                border-radius: 4px;
            }
        """)
        self.log_card.content_layout.addWidget(self.log_text)
        layout.addWidget(self.log_card, 1)

        # ── 3. Bottom Progress Area (Full Width) ──
        progress_container = QWidget()
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(10, 0, 10, 10)
        progress_layout.setSpacing(4)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False) # Cleaner look
        
        self.import_meta_label = QLabel("Prêt pour l'importation.")
        self.import_meta_label.setObjectName("CullingMeta")
        self.import_meta_label.setStyleSheet("color: #94A3B8; font-size: 11px;")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.import_meta_label)
        
        layout.addWidget(progress_container)

    def refresh_data(self) -> None:
        pass # Combo removed, nothing to refresh here for now.

    def set_selected_project(self, project_id: int) -> None:
        self._selected_project_id = project_id

    def _pick_source(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier source")
        if directory:
            self.source_edit.setText(directory)

    def _run_import(self) -> None:
        if self._job_thread is not None:
            QMessageBox.warning(self, "Operation en cours", "Un import est deja en cours.")
            return
        project_id = self._selected_project_id
        source = self.source_edit.text().strip()
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return
        if not source:
            QMessageBox.warning(self, "Validation", "Selectionne un dossier source.")
            return

        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.on_operation_started()
        self.on_job_event(f"[Import] Lancement du job pour projet ID {project_id}.")

        worker = JobWorker(self.import_service.run_import, project_id=project_id, source_dir=source)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.result.connect(self._on_import_result)
        worker.error.connect(self._on_import_error)
        worker.finished.connect(self._on_import_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._job_worker = worker
        self._job_thread = thread
        thread.start()

    def _cancel_import(self) -> None:
        if self._job_worker is not None:
            self._job_worker.cancel()
            self.import_meta_label.setText("Annulation demandee...")
            self.on_job_event("[Import] Annulation demandee par l'utilisateur.")

    def _on_import_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(max(0, min(int(done), safe_total)))
        self.import_meta_label.setText(f"{int(done)}/{int(total)} - {detail or 'copie...'}")

    def _on_import_result(self, result) -> None:
        self.log_text.appendPlainText(
            f"Import {result.status} | total={result.total}, copied={result.copied}, "
            f"failed={result.failed} | dest={result.destination}"
        )
        if result.message:
            self.log_text.appendPlainText(result.message)
        self.on_job_event(
            f"[Import] {result.status} | total={result.total}, copied={result.copied}, failed={result.failed}"
        )
        self.import_meta_label.setText(
            f"{result.status} | copies={result.copied} | fails={result.failed}"
        )
        self.on_data_changed()

    def _on_import_error(self, message: str) -> None:
        self.on_job_event(f"[Import] Erreur: {message}")
        QMessageBox.critical(self, "Erreur import", message)

    def _on_import_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.on_operation_ended()
        self._job_worker = None
        self._job_thread = None
        self.on_job_event("[Import] Job termine.")


class CullingTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        culling_service: CullingService,
        on_data_changed,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.culling_service = culling_service
        self.on_data_changed = on_data_changed
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)
        self._shortcut_refs: list[QShortcut] = []
        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None
        self.focus_mode_enabled = False
        self.asset_card_widgets: dict[int, QFrame] = {}
        self.show_path_overlay = False
        self._preview_hovered = False
        self.filmstrip_buttons: dict[int, QToolButton] = {}
        self._filmstrip_window: tuple[int, int] = (0, -1)
        self._preview_cache: dict[str, QPixmap] = {}
        self._preview_cache_order: list[str] = []
        self._thumb_cache: dict[str, QPixmap] = {}
        self._thumb_cache_order: list[str] = []
        self._prefetch_manager: PreviewPrefetchManager | None = None
        try:
            cache_root = Path(self.project_service.paths.data_dir) / "cache" / "images"
            self._prefetch_manager = PreviewPrefetchManager(cache_root=cache_root, depth=3)
        except Exception:
            self._prefetch_manager = None
        self._hud_timer = QTimer(self)
        self._hud_timer.setSingleShot(True)
        self._hud_timer.timeout.connect(self._hide_hud)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._selected_project_id: Optional[int] = None

        # ── 1. Top Control Bar ──
        controls_card = BentoCard("Configuration du tri")
        self.controls_box = controls_card
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(12, 8, 12, 8)
        controls_layout.setSpacing(16)

        # Filters
        filter_group = QHBoxLayout()
        filter_group.setSpacing(8)
        filter_group.addWidget(QLabel("Filtre:"))
        self.rejected_mode_combo = QComboBox()
        self.rejected_mode_combo.addItem("Tout", userData="all")
        self.rejected_mode_combo.addItem("A garder", userData="kept")
        self.rejected_mode_combo.addItem("Rejetees", userData="rejected")
        self.rejected_mode_combo.setFixedWidth(100)
        self.rejected_mode_combo.currentIndexChanged.connect(self._load_assets)
        filter_group.addWidget(self.rejected_mode_combo)

        self.min_rating_filter_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_filter_combo.addItem(str(rating), userData=rating)
        self.min_rating_filter_combo.setFixedWidth(50)
        self.min_rating_filter_combo.currentIndexChanged.connect(self._load_assets)
        filter_group.addWidget(QLabel("★"))
        filter_group.addWidget(self.min_rating_filter_combo)
        
        controls_layout.addLayout(filter_group)
        
        # View Actions
        self.focus_mode_btn = _new_button("Focus Mode (F)")
        self.focus_mode_btn.setCheckable(True)
        self.focus_mode_btn.toggled.connect(self._set_focus_mode)

        self.overlay_toggle_btn = QCheckBox("Infos Chemin (I)")
        self.overlay_toggle_btn.toggled.connect(self._toggle_overlay_details)

        controls_layout.addWidget(self.focus_mode_btn)
        controls_layout.addWidget(self.overlay_toggle_btn)
        
        controls_layout.addStretch(1)
        
        self.auto_advance_check = QCheckBox("Auto suivant")
        self.auto_advance_check.setChecked(True)
        controls_layout.addWidget(self.auto_advance_check)

        controls_card.content_layout.addLayout(controls_layout)
        layout.addWidget(controls_card)

        # Legacy stubs for compatibility
        self.keyword_filter_edit = QLineEdit()
        self.date_to_check = QCheckBox()
        self.date_to_edit = QDateEdit()
        self.prev_btn = QWidget()
        self.next_btn = QWidget()
        self.batch_solo_btn = QWidget()
        self.batch_solo_btn.isChecked = lambda: False
        self.batch_solo_btn.setChecked = lambda x: None

        body = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter = body

        # Left Panel (Navigator)
        navigator_card = BentoCard("Explorateur")
        navigator_layout = QVBoxLayout()
        navigator_layout.setContentsMargins(0, 0, 0, 0)
        
        self.selected_asset_id: int | None = None
        self.expanded_asset_ids: set[int] = set()
        self.assets_by_id: dict[int, object] = {}
        self.asset_order: list[int] = []
        
        self.asset_cards_area = QScrollArea()
        self.asset_cards_area.setWidgetResizable(True)
        self.asset_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.asset_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.asset_cards_content = QWidget()
        self.asset_cards_layout = QVBoxLayout(self.asset_cards_content)
        self.asset_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.asset_cards_layout.setSpacing(8)
        self.asset_cards_area.setWidget(self.asset_cards_content)
        
        navigator_layout.addWidget(self.asset_cards_area)
        navigator_card.content_layout.addLayout(navigator_layout)
        body.addWidget(navigator_card)
        body.setStretchFactor(0, 1)

        # Right Panel (Preview)
        side_panel = QWidget()
        self.side_panel = side_panel
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)
        
        preview_card = BentoCard() # No title for clean look
        preview_grid = QGridLayout()
        preview_grid.setContentsMargins(0, 0, 0, 0)
        
        self.preview_label = QLabel("Aperçu")
        self.preview_label.setObjectName("PreviewLabel")
        self.preview_label.setMinimumHeight(280)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.hud_label = QLabel("")
        self.hud_label.setObjectName("CullingHud")
        self.hud_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hud_label.setVisible(False)

        self.info_overlay_label = QLabel("Sélection: -")
        self.info_overlay_label.setObjectName("CullingInfoOverlay")
        self.info_overlay_label.setStyleSheet("""
            background: rgba(0, 0, 0, 0.4);
            color: #AAA;
            padding: 4px 8px;
            border-top-right-radius: 6px;
            font-size: 10px;
            font-family: 'Consolas', monospace;
        """)

        self.path_overlay_label = QLabel("")
        self.path_overlay_label.setObjectName("CullingPathOverlay")
        self.path_overlay_label.setWordWrap(True)
        self.path_overlay_label.setStyleSheet("""
            background: rgba(0, 0, 0, 0.6);
            color: #00FF9D;
            padding: 6px 10px;
            font-size: 10px;
            font-family: 'Consolas', monospace;
        """)
        self.path_overlay_label.setVisible(False)
        
        preview_grid.addWidget(self.preview_label, 0, 0)
        preview_grid.addWidget(self.hud_label, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        preview_grid.addWidget(self.info_overlay_label, 0, 0, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        preview_grid.addWidget(self.path_overlay_label, 0, 0, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        
        preview_card.content_layout.addLayout(preview_grid)
        self.preview_frame = preview_card
        side_layout.addWidget(preview_card, 1)
        
        self.preview_label.installEventFilter(self)
        self.preview_frame.installEventFilter(self)

        # ── 3. Tinder-style Decision Bar ──
        decision_card = BentoCard() # Clean container
        decision_card.setMinimumHeight(80)
        decision_layout = QHBoxLayout()
        decision_layout.setContentsMargins(20, 10, 20, 10)
        decision_layout.setSpacing(15)

        # REJECT Button (Tinder Red)
        self.tinder_reject_btn = QPushButton("✘ REJETER")
        self.tinder_reject_btn.setObjectName("TinderRejectBtn")
        self.tinder_reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tinder_reject_btn.setMinimumHeight(44)
        self.tinder_reject_btn.clicked.connect(self._mark_selected_reject)
        
        # Rating Stars (Tinder Style Between Buttons)
        star_layout = QHBoxLayout()
        star_layout.setSpacing(4)
        self.star_btns = []
        for i in range(1, 6):
            btn = QToolButton()
            btn.setText("★")
            btn.setObjectName("TinderStarBtn")
            btn.setCheckable(True)
            btn.setFixedSize(32, 32)
            btn.clicked.connect(lambda _, r=i: self._set_selected_rating(r))
            star_layout.addWidget(btn)
            self.star_btns.append(btn)
        
        # KEEP Button (Tinder Green)
        self.tinder_keep_btn = QPushButton("✔ GARDER")
        self.tinder_keep_btn.setObjectName("TinderKeepBtn")
        self.tinder_keep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tinder_keep_btn.setMinimumHeight(44)
        self.tinder_keep_btn.clicked.connect(self._mark_selected_keep)

        # Build Bar
        decision_layout.addWidget(self.tinder_reject_btn, 1)
        decision_layout.addStretch(1)
        decision_layout.addLayout(star_layout)
        decision_layout.addStretch(1)
        decision_layout.addWidget(self.tinder_keep_btn, 1)
        
        decision_card.content_layout.addLayout(decision_layout)
        side_layout.addWidget(decision_card)

        # Filmstrip
        self.filmstrip_frame = QFrame()
        self.filmstrip_frame.setObjectName("FilmstripFrame")
        self.filmstrip_frame.setFixedHeight(140)
        film_layout = QVBoxLayout(self.filmstrip_frame)
        film_layout.setContentsMargins(0, 0, 0, 0)
        
        self.filmstrip_area = QScrollArea()
        self.filmstrip_area.setWidgetResizable(True)
        self.filmstrip_area.setFrameShape(QFrame.Shape.NoFrame)
        self.filmstrip_content = QWidget()
        self.filmstrip_layout = QHBoxLayout(self.filmstrip_content)
        self.filmstrip_layout.setContentsMargins(0, 0, 0, 0)
        self.filmstrip_layout.setSpacing(6)
        self.filmstrip_area.setWidget(self.filmstrip_content)
        film_layout.addWidget(self.filmstrip_area)
        
        side_layout.addWidget(self.filmstrip_frame)
        body.addWidget(side_panel)
        body.setStretchFactor(1, 3)
        layout.addWidget(body, 1)

        # Legacy stubs
        self.asset_info_label = QLabel()
        self.asset_sequence_label = QLabel()
        self.actions_box_anim = None
        self.actions_box = QWidget() # Stub
        self.batch_progress = QProgressBar() # Stub
        self.job_status_label = QLabel() # Stub
        self.batch_cancel_btn = QWidget() # Stub

        self._build_shortcuts()

    def eventFilter(self, obj, event) -> bool:
        if obj in {self.preview_frame, self.preview_label}:
            if event.type() == QEvent.Type.Enter:
                self._preview_hovered = True
                self._update_overlay_visibility()
            elif event.type() == QEvent.Type.Leave:
                self._preview_hovered = False
                self._update_overlay_visibility()
            elif event.type() == QEvent.Type.Wheel:
                delta = int(event.angleDelta().y())
                if delta < 0:
                    self._select_next_asset()
                elif delta > 0:
                    self._select_previous_asset()
                return True
        return super().eventFilter(obj, event)

    def _build_shortcuts(self) -> None:
        for rating in range(0, 6):
            shortcut = QShortcut(QKeySequence(str(rating)), self)
            shortcut.activated.connect(lambda r=rating: self._set_selected_rating(r))
            self._shortcut_refs.append(shortcut)

        keep_shortcut = QShortcut(QKeySequence("P"), self)
        keep_shortcut.activated.connect(self._mark_selected_keep)
        self._shortcut_refs.append(keep_shortcut)

        hard_reject_shortcut = QShortcut(QKeySequence("X"), self)
        hard_reject_shortcut.activated.connect(self._mark_selected_reject)
        self._shortcut_refs.append(hard_reject_shortcut)

        next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        next_shortcut.activated.connect(self._select_next_asset)
        self._shortcut_refs.append(next_shortcut)

        prev_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        prev_shortcut.activated.connect(self._select_previous_asset)
        self._shortcut_refs.append(prev_shortcut)

        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space_shortcut.activated.connect(self._select_next_asset)
        self._shortcut_refs.append(space_shortcut)

        focus_shortcut = QShortcut(QKeySequence("F"), self)
        focus_shortcut.activated.connect(self._toggle_focus_mode_shortcut)
        self._shortcut_refs.append(focus_shortcut)

        info_shortcut = QShortcut(QKeySequence("I"), self)
        info_shortcut.activated.connect(self._toggle_overlay_shortcut)
        self._shortcut_refs.append(info_shortcut)

    def refresh_data(self) -> None:
        self._load_assets()

    def set_selected_project(self, project_id: int) -> None:
        self._selected_project_id = project_id
        self._load_assets()

    def _load_assets(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            self._clear_asset_cards()
            self._clear_filmstrip()
            self._set_selected_asset(None)
            self.assets_by_id = {}
            self.asset_order = []
            if self._prefetch_manager is not None:
                self._prefetch_manager.update_sequence([])
            self.preview_label.setText("Apercu")
            self.info_overlay_label.setText("Selection: -")
            self.path_overlay_label.setVisible(False)
            return

        rejected_mode = self.rejected_mode_combo.currentData()
        min_rating = int(self.min_rating_filter_combo.currentData() or 0)
        
        assets = self.culling_service.list_assets(
            project_id=project_id,
            rejected_mode=rejected_mode,
            min_rating=min_rating
        )

        current_asset_id = self._selected_asset_id()
        self.assets_by_id = {int(asset.id): asset for asset in assets}
        self.asset_order = [int(asset.id) for asset in assets]
        if self._prefetch_manager is not None:
            sequence_paths = [
                str(asset.src_path)
                for asset in assets
                if getattr(asset, "src_path", None)
            ]
            self._prefetch_manager.update_sequence(sequence_paths)
        if current_asset_id not in self.assets_by_id:
            current_asset_id = int(assets[0].id) if assets else None
        self.selected_asset_id = int(current_asset_id) if current_asset_id is not None else None

        self._render_filmstrip(force=True)
        self._set_selected_asset(self.selected_asset_id)
        if self._selected_asset_id() is None:
            self.preview_label.setText("Aucun asset")
            self.info_overlay_label.setText("Aucun asset")
            self.path_overlay_label.setVisible(False)
        else:
            self._on_select_asset()

    def _selected_asset_id(self) -> int | None:
        return self.selected_asset_id

    def _clear_asset_cards(self) -> None:
        self.asset_card_widgets = {}
        while self.asset_cards_layout.count():
            item = self.asset_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _clear_filmstrip(self) -> None:
        self.filmstrip_buttons = {}
        self._filmstrip_window = (0, -1)
        while self.filmstrip_layout.count():
            item = self.filmstrip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _compute_filmstrip_window(self) -> tuple[int, int]:
        total = len(self.asset_order)
        if total <= 0:
            return (0, -1)
        index = self._selected_asset_index()
        if index < 0:
            index = 0
        window_size = 180
        half = window_size // 2
        start = max(0, index - half)
        end = min(total - 1, start + window_size - 1)
        if (end - start + 1) < window_size:
            start = max(0, end - window_size + 1)
        return (start, end)

    def _render_filmstrip(self, force: bool = False) -> None:
        if not self.asset_order:
            self._clear_filmstrip()
            empty = QLabel("Aucun asset pour ces filtres.")
            empty.setObjectName("CardMuted")
            self.filmstrip_layout.addWidget(empty)
            self.filmstrip_layout.addStretch(1)
            return

        start, end = self._compute_filmstrip_window()
        if not force and self._filmstrip_window == (start, end):
            self._refresh_filmstrip_selection()
            self._ensure_selected_thumb_visible()
            return

        self._clear_filmstrip()
        self._filmstrip_window = (start, end)
        thumb_w = 136
        thumb_h = 86
        self.filmstrip_content.setMinimumHeight(thumb_h + 20)
        for idx in range(start, end + 1):
            asset_id = int(self.asset_order[idx])
            asset = self.assets_by_id.get(asset_id)
            if asset is None:
                continue
            btn = QToolButton()
            btn.setObjectName("FilmThumb")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setIconSize(QSize(thumb_w, thumb_h))
            btn.setFixedSize(thumb_w + 18, thumb_h + 18)
            btn.setToolTip(asset.file_name)
            btn.setProperty("selected", "false")
            btn.clicked.connect(lambda _checked=False, aid=asset_id: self._on_filmstrip_clicked(aid))
            thumb = self._load_thumb_pixmap(Path(str(asset.src_path)), thumb_w, thumb_h)
            if thumb.isNull():
                fallback = QPixmap(thumb_w, thumb_h)
                fallback.fill(QColor("#2B2B2B"))
                btn.setIcon(QIcon(fallback))
            else:
                btn.setIcon(QIcon(thumb))
            self.filmstrip_buttons[asset_id] = btn
            self.filmstrip_layout.addWidget(btn)
        self.filmstrip_layout.addStretch(1)
        self._refresh_filmstrip_selection()
        self._ensure_selected_thumb_visible()

    def _on_filmstrip_clicked(self, asset_id: int) -> None:
        self._set_selected_asset(int(asset_id))
        self._on_select_asset()

    def _refresh_filmstrip_selection(self) -> None:
        selected_id = self._selected_asset_id()
        for asset_id, btn in self.filmstrip_buttons.items():
            is_selected = selected_id is not None and int(asset_id) == int(selected_id)
            btn.setProperty("selected", "true" if is_selected else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

    def _ensure_selected_thumb_visible(self) -> None:
        selected_id = self._selected_asset_id()
        if selected_id is None:
            return
        btn = self.filmstrip_buttons.get(int(selected_id))
        if btn is None:
            return
        self.filmstrip_area.ensureWidgetVisible(btn, 40, 2)

    def _load_preview_pixmap(self, file_path: Path | None) -> QPixmap:
        if file_path is None:
            return QPixmap()
        resolved = Path(file_path).expanduser().resolve()
        key = str(resolved)
        cached = self._preview_cache.get(key)
        if cached is not None:
            return cached

        pixmap = QPixmap()
        if self._prefetch_manager is not None:
            warm_bytes = self._prefetch_manager.get_warmed_preview_bytes(resolved)
            if warm_bytes:
                pixmap.loadFromData(warm_bytes)
            if pixmap.isNull():
                cached_path = self._prefetch_manager.get_cached_preview_path(resolved)
                if cached_path is not None and cached_path.exists():
                    pixmap = QPixmap(str(cached_path))
        if pixmap.isNull():
            pixmap = QPixmap(str(resolved)) if resolved.exists() else QPixmap()
        self._cache_put(self._preview_cache, self._preview_cache_order, key, pixmap, 24)
        return pixmap

    def _load_thumb_pixmap(self, file_path: Path | None, width: int, height: int) -> QPixmap:
        if file_path is None:
            return QPixmap()
        resolved = Path(file_path).expanduser().resolve()
        key = f"{resolved}|{width}x{height}"
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached

        if self._prefetch_manager is not None:
            cached_thumb_path = self._prefetch_manager.get_cached_thumb_path(resolved, width=width, height=height)
            if cached_thumb_path is not None and cached_thumb_path.exists():
                thumb = QPixmap(str(cached_thumb_path))
                if not thumb.isNull():
                    self._cache_put(self._thumb_cache, self._thumb_cache_order, key, thumb, 420)
                    return thumb

        source = self._load_preview_pixmap(resolved)
        if source.isNull():
            thumb = QPixmap()
        else:
            thumb = source.scaled(
                QSize(width, height),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._cache_put(self._thumb_cache, self._thumb_cache_order, key, thumb, 420)
        return thumb

    @staticmethod
    def _cache_put(cache: dict[str, QPixmap], order: list[str], key: str, value: QPixmap, max_size: int) -> None:
        if key in cache:
            try:
                order.remove(key)
            except ValueError:
                pass
        cache[key] = value
        order.append(key)
        while len(order) > max(1, int(max_size)):
            stale = order.pop(0)
            cache.pop(stale, None)

    def _prefetch_neighbors(self) -> None:
        index = self._selected_asset_index()
        if index < 0:
            return
        if self._prefetch_manager is not None:
            self._prefetch_manager.on_selected_index(index)
            start = max(0, index - 1)
            end = min(len(self.asset_order) - 1, index + 3)
            for pos in range(start, end + 1):
                asset = self.assets_by_id.get(int(self.asset_order[pos]))
                if asset is None:
                    continue
                path = Path(str(asset.src_path)) if asset.src_path else None
                if path is not None:
                    self._prefetch_manager.prefetch_thumb(path, width=136, height=86)
            self._prune_local_preview_cache(index)
            return

        start = max(0, index - 1)
        end = min(len(self.asset_order) - 1, index + 3)
        for pos in range(start, end + 1):
            asset = self.assets_by_id.get(int(self.asset_order[pos]))
            if asset is None:
                continue
            path = Path(str(asset.src_path)) if asset.src_path else None
            self._load_preview_pixmap(path)
            self._load_thumb_pixmap(path, 136, 86)

    def _prune_local_preview_cache(self, center_index: int) -> None:
        keep_paths: set[str] = set()
        start = max(0, int(center_index) - 1)
        end = min(len(self.asset_order) - 1, int(center_index) + 3)
        for pos in range(start, end + 1):
            asset = self.assets_by_id.get(int(self.asset_order[pos]))
            if asset is None or not getattr(asset, "src_path", None):
                continue
            keep_paths.add(str(Path(str(asset.src_path)).expanduser().resolve()))

        for key in list(self._preview_cache.keys()):
            if key not in keep_paths:
                self._preview_cache.pop(key, None)
                try:
                    self._preview_cache_order.remove(key)
                except ValueError:
                    pass

        for key in list(self._thumb_cache.keys()):
            src_key = str(key).split("|", 1)[0]
            if src_key not in keep_paths:
                self._thumb_cache.pop(key, None)
                try:
                    self._thumb_cache_order.remove(key)
                except ValueError:
                    pass

    def _render_asset_cards(self, assets: list) -> None:
        self._clear_asset_cards()
        if not assets:
            empty = QLabel("Aucun asset pour ces filtres.")
            empty.setObjectName("CardMuted")
            self.asset_cards_layout.addWidget(empty)
            self.asset_cards_layout.addStretch(1)
            return

        for asset in assets:
            is_selected = self.selected_asset_id is not None and int(asset.id) == int(self.selected_asset_id)
            card = self._build_asset_card(asset, is_selected=is_selected)
            self.asset_card_widgets[int(asset.id)] = card
            self.asset_cards_layout.addWidget(card)
        self.asset_cards_layout.addStretch(1)

    def _build_asset_card(self, asset, is_selected: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("DataCard")
        card.setProperty("selected", "true" if is_selected else "false")
        card.setMinimumHeight(86)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        select_btn = NativePushButton(f"{asset.id} - {asset.file_name}")
        select_btn.setProperty("cardSelect", "true")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.setMinimumHeight(32)
        select_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        select_btn.clicked.connect(lambda _checked=False, asset_id=asset.id: self._on_asset_card_selected(asset_id))
        badge = QLabel(f"{int(asset.rating)} ★")
        badge.setObjectName("CardBadge")
        badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        toggle = QToolButton()
        toggle.setProperty("cardToggle", "true")
        toggle.setCheckable(True)
        expanded = bool(is_selected or (int(asset.id) in self.expanded_asset_ids))
        toggle.setChecked(expanded)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        toggle.setFixedSize(24, 24)

        header.addWidget(select_btn, 1)
        header.addWidget(badge)
        header.addWidget(toggle)
        card_layout.addLayout(header)

        details = QWidget()
        details.setObjectName("CardDetails")
        details_layout = QFormLayout(details)
        details_layout.setContentsMargins(0, 10, 0, 0)
        details_layout.setHorizontalSpacing(10)
        details_layout.setVerticalSpacing(6)
        details_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        details_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        details_layout.addRow("Rejet", self._card_value("oui" if asset.is_rejected else "non"))
        details_layout.addRow("Chemin", self._card_value(asset.src_path))
        details.setVisible(expanded)
        card_layout.addWidget(details)

        def _on_toggle(opened: bool, asset_id=asset.id, panel=details, btn=toggle):
            panel.setVisible(opened)
            btn.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            if opened:
                self.expanded_asset_ids.add(int(asset_id))
            else:
                self.expanded_asset_ids.discard(int(asset_id))

        toggle.toggled.connect(_on_toggle)
        return card

    @staticmethod
    def _card_value(value: str) -> QLabel:
        label = QLabel(str(value))
        label.setWordWrap(True)
        label.setObjectName("CardValue")
        return label

    def _on_asset_card_selected(self, asset_id: int) -> None:
        self._set_selected_asset(asset_id)
        self._on_select_asset()

    def _set_selected_asset(self, asset_id: int | None) -> None:
        previous_id = self.selected_asset_id
        self.selected_asset_id = int(asset_id) if asset_id is not None else None

        # Sync Tinder Stars
        if self.selected_asset_id is not None:
            asset = self.assets_by_id.get(self.selected_asset_id)
            if asset:
                rating = int(getattr(asset, "rating", 0))
                for i, btn in enumerate(self.star_btns):
                    btn.blockSignals(True)
                    btn.setChecked(i < rating)
                    btn.blockSignals(False)

        if previous_id is not None and previous_id != self.selected_asset_id:
            previous_card = self.asset_card_widgets.get(int(previous_id))
            if previous_card is not None:
                previous_card.setProperty("selected", "false")
                self._refresh_widget_style(previous_card)

        if self.selected_asset_id is not None:
            current_card = self.asset_card_widgets.get(int(self.selected_asset_id))
            if current_card is not None:
                current_card.setProperty("selected", "true")
                self._refresh_widget_style(current_card)
        
        self._refresh_filmstrip_selection()

    def _on_select_asset(self) -> None:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Apercu")
            self.info_overlay_label.setText("Selection: -")
            self.path_overlay_label.setVisible(False)
            return

        asset = self.assets_by_id.get(int(asset_id))
        if asset is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Apercu")
            self.info_overlay_label.setText("Selection: -")
            self.path_overlay_label.setVisible(False)
            return

        file_path = Path(str(asset.src_path)) if asset.src_path else None

        preview_pixmap = self._load_preview_pixmap(file_path)
        if preview_pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Apercu indisponible")
            resolution = "-"
        else:
            self.preview_label.setText("")
            scaled = preview_pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            resolution = f"{preview_pixmap.width()}x{preview_pixmap.height()}"

        name = file_path.name if file_path else "-"
        rating = int(getattr(asset, "rating", 0))
        rejected = bool(getattr(asset, "is_rejected", False))
        index = self._selected_asset_index()
        display_index = index + 1 if index >= 0 else 0
        reject_flag = " | REJECT" if rejected else ""
        self.info_overlay_label.setText(
            f"{display_index}/{len(self.asset_order)} | {name} | {rating} ★ | {resolution}{reject_flag}"
        )
        self._update_overlay_visibility()
        self._prefetch_neighbors()
        self._render_filmstrip(force=False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep preview readable when panel size changes.
        self._on_select_asset()

    def reset_layout_after_shell_resize(self) -> None:
        splitter = getattr(self, "body_splitter", None)
        if splitter is None:
            return
        splitter.setSizes([0, max(1, int(splitter.width()))])
        self._render_filmstrip(force=True)

    def _toggle_focus_mode_shortcut(self) -> None:
        self.focus_mode_btn.setChecked(not self.focus_mode_btn.isChecked())

    def _set_focus_mode(self, enabled: bool) -> None:
        self.focus_mode_enabled = bool(enabled)
        self._apply_culling_layout_mode()
        if self.focus_mode_enabled:
            self.on_job_event("[Tri] Mode focus active.")
        else:
            self.on_job_event("[Tri] Mode focus desactive.")

    def _apply_culling_layout_mode(self) -> None:
        hide_chrome = bool(self.focus_mode_enabled)
        self.controls_box.setVisible(not hide_chrome)
        self.filmstrip_frame.setVisible(not hide_chrome)

    def _toggle_overlay_shortcut(self) -> None:
        self.overlay_toggle_btn.setChecked(not self.overlay_toggle_btn.isChecked())

    def _toggle_overlay_details(self, enabled: bool) -> None:
        self.show_path_overlay = bool(enabled)
        self._update_overlay_visibility()

    def _update_overlay_visibility(self) -> None:
        asset_id = self._selected_asset_id()
        asset = self.assets_by_id.get(int(asset_id)) if asset_id is not None else None
        if asset is None:
            self.path_overlay_label.setVisible(False)
            return
        file_path = Path(str(asset.src_path)) if asset.src_path else None
        show_path = self.show_path_overlay or self._preview_hovered
        self.path_overlay_label.setVisible(show_path and file_path is not None)
        if show_path and file_path is not None:
            self.path_overlay_label.setText(str(file_path))
        else:
            self.path_overlay_label.setText("")

    def _selected_asset_index(self) -> int:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            return -1
        try:
            return self.asset_order.index(int(asset_id))
        except ValueError:
            return -1

    def _neighbor_asset_id(self, step: int) -> int | None:
        if not self.asset_order:
            return None
        index = self._selected_asset_index()
        if index < 0:
            return int(self.asset_order[0] if step >= 0 else self.asset_order[-1])
        next_index = max(0, min(len(self.asset_order) - 1, index + int(step)))
        if next_index == index:
            return None
        return int(self.asset_order[next_index])

    def _select_previous_asset(self) -> None:
        target_id = self._neighbor_asset_id(-1)
        if target_id is None:
            return
        self._set_selected_asset(target_id)
        self._on_select_asset()

    def _select_next_asset(self) -> None:
        target_id = self._neighbor_asset_id(1)
        if target_id is None:
            return
        self._set_selected_asset(target_id)
        self._on_select_asset()

    def _mark_selected_keep(self) -> None:
        self._set_selected_rejected_state(False, hud_text="KEEP", hud_state="ok")

    def _mark_selected_reject(self) -> None:
        self._set_selected_rejected_state(True, hud_text="REJECT", hud_state="warn")

    def _set_selected_rejected_state(self, rejected: bool, hud_text: str, hud_state: str) -> None:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            return
        next_id = self._neighbor_asset_id(1) if self.auto_advance_check.isChecked() else None
        try:
            self.culling_service.update_asset(asset_id=asset_id, is_rejected=bool(rejected))
            if next_id is not None:
                self.selected_asset_id = int(next_id)
            self._load_assets()
            self._show_hud(hud_text, hud_state)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur tri", str(exc))

    def _show_hud(self, text: str, state: str = "info") -> None:
        self.hud_label.setText(str(text))
        self.hud_label.setProperty("hudState", str(state))
        self.hud_label.style().unpolish(self.hud_label)
        self.hud_label.style().polish(self.hud_label)
        self.hud_label.setVisible(True)
        self._hud_timer.start(420)

    def _hide_hud(self) -> None:
        self.hud_label.setVisible(False)

    def _apply_selected_rating(self) -> None:
        rating = int(self.rating_combo.currentData() or 0)
        self._set_selected_rating(rating)

    def _set_selected_rating(self, rating: int) -> None:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            return
        safe_rating = max(0, min(int(rating), 5))
        next_id = self._neighbor_asset_id(1) if self.auto_advance_check.isChecked() else None
        try:
            self.culling_service.update_asset(asset_id=asset_id, rating=safe_rating)
            if next_id is not None:
                self.selected_asset_id = int(next_id)
            self._load_assets()
            self._show_hud(f"NOTE {safe_rating}", "ok")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur tri", str(exc))

    def _toggle_selected_reject(self) -> None:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            return
        current = self.assets_by_id.get(int(asset_id))
        target_rejected = not bool(getattr(current, "is_rejected", False))
        try:
            self.culling_service.update_asset(asset_id=asset_id, is_rejected=target_rejected)
            self._load_assets()
            self._show_hud("REJECT" if target_rejected else "KEEP", "warn" if target_rejected else "ok")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur tri", str(exc))

    def _start_batch_rating(self) -> None:
        rating = int(self.batch_rating_combo.currentData() or 0)
        self._start_batch_job(rating=rating, is_rejected=None)

    def _start_batch_reject(self) -> None:
        self._start_batch_job(rating=None, is_rejected=True)

    def _start_batch_restore(self) -> None:
        self._start_batch_job(rating=None, is_rejected=False)

    def _start_batch_job(self, rating: int | None, is_rejected: bool | None) -> None:
        if self._job_thread is not None:
            QMessageBox.warning(self, "Operation en cours", "Un batch tri est deja en cours.")
            return
        project_id = self._selected_project_id
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return

        rejected_mode = self.rejected_mode_combo.currentData()
        min_rating = int(self.min_rating_filter_combo.currentData() or 0)
    def _on_asset_card_selected(self, asset_id: int) -> None:
        self._set_selected_asset(asset_id)
        self._on_select_asset()

    def _set_selected_asset(self, asset_id: int | None) -> None:
        self.selected_asset_id = int(asset_id) if asset_id is not None else None
        
        # Sync tinder stars
        if self.selected_asset_id:
            asset = self.assets_by_id.get(self.selected_asset_id)
            if asset:
                rating = int(getattr(asset, "rating", 0))
                for i, btn in enumerate(self.star_btns):
                    btn.blockSignals(True)
                    btn.setChecked(i < rating)
                    btn.blockSignals(False)

    def closeEvent(self, event) -> None:
        if self._prefetch_manager is not None:
            try:
                self._prefetch_manager.shutdown()
            except Exception:
                pass
        super().closeEvent(event)


class EditTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        edit_service: EditService,
        metadata_service: MetadataService,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.edit_service = edit_service
        self.metadata_service = metadata_service
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)

        self._shortcut_refs: list[QShortcut] = []
        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._save_current_asset_settings)
        self._autosave_delay = 150 # Faster feel
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._apply_edit_preview)
        
        self._base_image: Image.Image | None = None
        self._form_loading = False
        self._metadata_form_loading = False
        self._before_mode = False
        self._solo_mode_enabled = False
        self._copied_settings: dict[str, object] | None = None

        self.selected_asset_id: int | None = None
        self.assets_by_id: dict[int, object] = {}
        self.asset_order: list[int] = []
        self.asset_card_widgets: dict[int, QFrame] = {}
        self._thumb_cache: dict[str, QPixmap] = {}
        self._thumb_cache_order: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._selected_project_id: Optional[int] = None

        # ── 1. Top Control Bar (Toolbar) ──
        toolbar_card = BentoCard()
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(12, 4, 12, 4)
        toolbar_layout.setSpacing(12)

        # Filters Section
        filter_box = QHBoxLayout(); filter_box.setSpacing(8)
        vue_lbl = QLabel("Vue:")
        filter_box.addWidget(vue_lbl)
        self.rejected_mode_combo = QComboBox()
        self.rejected_mode_combo.addItem("A garder", userData="kept")
        self.rejected_mode_combo.addItem("Tout", userData="all")
        self.rejected_mode_combo.addItem("Rejetées", userData="rejected")
        self.rejected_mode_combo.setFixedWidth(110)
        self.rejected_mode_combo.currentIndexChanged.connect(self._load_assets)
        filter_box.addWidget(self.rejected_mode_combo)

        self.min_rating_filter_combo = QComboBox()
        for r in range(0, 6): self.min_rating_filter_combo.addItem(f"{r} ★", userData=r)
        self.min_rating_filter_combo.setFixedWidth(65)
        self.min_rating_filter_combo.currentIndexChanged.connect(self._load_assets)
        filter_box.addWidget(self.min_rating_filter_combo)
        toolbar_layout.addLayout(filter_box)

        toolbar_layout.addSpacing(24)
        v_sep = QFrame(); v_sep.setFrameShape(QFrame.Shape.VLine); v_sep.setStyleSheet("background: #333; max-width: 1px;"); toolbar_layout.addWidget(v_sep)
        toolbar_layout.addSpacing(24)

        # Tools Section
        tools_box = QHBoxLayout(); tools_box.setSpacing(12)
        self.solo_mode_btn = _new_button("Masquer Collection (F)")
        self.solo_mode_btn.setCheckable(True)
        self.solo_mode_btn.toggled.connect(self._toggle_solo_mode)
        tools_box.addWidget(self.solo_mode_btn)
        toolbar_layout.addLayout(tools_box)

        toolbar_layout.addStretch(1)
        
        # Shortcuts hint
        self.hud_hint = QLabel("F = Collection | B = Avant/Après")
        toolbar_layout.addWidget(self.hud_hint)

        self.panel_focus_label = QLabel() # Stub for compatibility
        self._solo_hidden_widgets = [
            vue_lbl, self.rejected_mode_combo, self.min_rating_filter_combo, v_sep
        ]

        toolbar_card.content_layout.addLayout(toolbar_layout)
        layout.addWidget(toolbar_card)

        body = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter = body

        # ── 2. Asset Collection (Left) ──
        collection_card = BentoCard("Collection")
        self.list_panel = collection_card
        collection_layout = QVBoxLayout()
        collection_layout.setContentsMargins(0, 0, 0, 0)
        
        self.asset_cards_area = QScrollArea()
        self.asset_cards_area.setWidgetResizable(True)
        self.asset_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.asset_cards_content = QWidget()
        self.asset_cards_layout = QVBoxLayout(self.asset_cards_content)
        self.asset_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.asset_cards_layout.setSpacing(8)
        self.asset_cards_layout.addStretch(1)
        self.asset_cards_area.setWidget(self.asset_cards_content)
        collection_layout.addWidget(self.asset_cards_area)
        collection_card.content_layout.addLayout(collection_layout)
        
        body.addWidget(collection_card)
        body.setStretchFactor(0, 1)

        # ── 3. Central Working Area (Preview) ──
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)

        preview_card = BentoCard() # Clean preview
        preview_card.main_layout.setContentsMargins(8, 8, 8, 8)
        preview_grid = QGridLayout()
        preview_grid.setContentsMargins(0, 0, 0, 0)
        
        self.preview_label = QLabel("Chargement...")
        self.preview_label.setObjectName("PreviewLabel")
        self.preview_label.setMinimumHeight(420)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background: #0D0D0D; border-radius: 8px;")
        
        # HUD Badge for Before/After comparison
        self.before_after_badge = QLabel("APRES (Edition)")
        self.before_after_badge.setStyleSheet("""
            background: rgba(0, 0, 0, 0.6);
            color: white;
            padding: 4px 10px;
            border-bottom-right-radius: 8px;
            font-size: 10px;
            font-weight: bold;
        """)
        
        preview_grid.addWidget(self.preview_label, 0, 0)
        preview_grid.addWidget(self.before_after_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        
        info_bar = QHBoxLayout(); info_bar.setContentsMargins(12, 0, 12, 0)
        self.asset_info_label = QLabel("Sélection: -")
        self.asset_info_label.setObjectName("CardMuted")
        self.asset_info_label.setStyleSheet("font-family: 'Consolas'; font-size: 11px; background: transparent;")
        info_bar.addWidget(self.asset_info_label)
        info_bar.addStretch(1)
        
        preview_card.content_layout.addLayout(preview_grid)
        center_layout.addWidget(preview_card, 1)
        center_layout.addLayout(info_bar)
        
        body.addWidget(center_panel)
        body.setStretchFactor(1, 4)

        # ── 4. Adjustment Column (Right) ──
        scroll_dock = QScrollArea()
        self.edit_dock = scroll_dock
        scroll_dock.setWidgetResizable(True)
        scroll_dock.setFrameShape(QFrame.Shape.NoFrame)
        scroll_dock.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_dock.setMinimumWidth(320)
        scroll_dock.setMaximumWidth(450)
        
        dock_container = QWidget()
        dock_v = QVBoxLayout(dock_container)
        dock_v.setContentsMargins(0, 0, 8, 0) # Small right margin for beauty
        dock_v.setSpacing(12)

        # Exposition
        self.light_box = BentoCard("Exposition")
        l_layout = QVBoxLayout()
        self.exposure_slider = QSlider(Qt.Orientation.Horizontal); self.exposure_slider.setRange(-500, 500)
        self.exposure_value_label = QLabel("+0.00")
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal); self.contrast_slider.setRange(-100, 100)
        self.contrast_value_label = QLabel("0")
        l_layout.addLayout(self._build_slider_row("Expo", self.exposure_slider, self.exposure_value_label))
        l_layout.addLayout(self._build_slider_row("Contrast", self.contrast_slider, self.contrast_value_label))
        self.light_box.content_layout.addLayout(l_layout)
        dock_v.addWidget(self.light_box)

        # Balance
        self.color_box = BentoCard("Couleur")
        c_layout = QVBoxLayout()
        self.wb_temp_slider = QSlider(Qt.Orientation.Horizontal); self.wb_temp_slider.setRange(2000, 12000)
        self.wb_temp_value_label = QLabel("5500K")
        self.wb_tint_slider = QSlider(Qt.Orientation.Horizontal); self.wb_tint_slider.setRange(-100, 100)
        self.wb_tint_value_label = QLabel("+0")
        c_layout.addLayout(self._build_slider_row("Temp", self.wb_temp_slider, self.wb_temp_value_label))
        c_layout.addLayout(self._build_slider_row("Tint", self.wb_tint_slider, self.wb_tint_value_label))
        self.color_box.content_layout.addLayout(c_layout)
        dock_v.addWidget(self.color_box)

        # Advanced Tools (Collapsible area inside dock)
        self.pro_tools_panel = BentoCard("Outils Avancés")
        pro_layout = QVBoxLayout(); pro_layout.setSpacing(10)
        self.highlights_slider = QSlider(Qt.Orientation.Horizontal); self.highlights_slider.setRange(-100, 100); self.highlights_value_label = QLabel("0")
        self.shadows_slider = QSlider(Qt.Orientation.Horizontal); self.shadows_slider.setRange(-100, 100); self.shadows_value_label = QLabel("0")
        self.vibrance_slider = QSlider(Qt.Orientation.Horizontal); self.vibrance_slider.setRange(-100, 100); self.vibrance_value_label = QLabel("0")
        self.saturation_slider = QSlider(Qt.Orientation.Horizontal); self.saturation_slider.setRange(-100, 100); self.saturation_value_label = QLabel("0")
        self.clarity_slider = QSlider(Qt.Orientation.Horizontal); self.clarity_slider.setRange(-100, 100); self.clarity_value_label = QLabel("0")
        pro_layout.addLayout(self._build_slider_row("H-Lts", self.highlights_slider, self.highlights_value_label))
        pro_layout.addLayout(self._build_slider_row("Shds", self.shadows_slider, self.shadows_value_label))
        pro_layout.addLayout(self._build_slider_row("Vibr", self.vibrance_slider, self.vibrance_value_label))
        pro_layout.addLayout(self._build_slider_row("Sat", self.saturation_slider, self.saturation_value_label))
        pro_layout.addLayout(self._build_slider_row("Clar", self.clarity_slider, self.clarity_value_label))
        self.pro_tools_panel.content_layout.addLayout(pro_layout)
        self.pro_tools_panel.setVisible(False)
        dock_v.addWidget(self.pro_tools_panel)

        # Geometry
        self.geometry_box = BentoCard("Géométrie")
        g_layout = QVBoxLayout()
        self.crop_ratio_combo = QComboBox(); self.crop_ratio_combo.addItems(["original", "1:1", "4:5", "3:2", "16:9"])
        self.straighten_slider = QSlider(Qt.Orientation.Horizontal); self.straighten_slider.setRange(-450, 450); self.straighten_value_label = QLabel("+0.0 deg")
        g_layout.addLayout(self._build_combo_row("Format", self.crop_ratio_combo))
        g_layout.addLayout(self._build_slider_row("Angle", self.straighten_slider, self.straighten_value_label))
        self.geometry_box.content_layout.addLayout(g_layout)
        dock_v.addWidget(self.geometry_box)

        # Actions (Simplified)
        self.action_box = BentoCard("Actions")
        a_layout = QVBoxLayout()
        self.reset_btn = _new_button("Réinitialiser d'origine"); self.reset_btn.clicked.connect(self._reset_selected_settings)
        self.pro_tools_toggle = _new_button("Afficher Options Avancées"); self.pro_tools_toggle.setCheckable(True); self.pro_tools_toggle.toggled.connect(self._toggle_pro_tools_panel)
        a_layout.addWidget(self.reset_btn)
        a_layout.addWidget(self.pro_tools_toggle)
        self.action_box.content_layout.addLayout(a_layout)
        dock_v.addWidget(self.action_box)
        dock_v.addStretch(1)

        scroll_dock.setWidget(dock_container)
        body.addWidget(scroll_dock)
        body.setStretchFactor(2, 1)
        
        layout.addWidget(body, 1)

        # ── 5. Metadata Panel (Optional bottom bar) ──
        metadata_card = BentoCard("Métadonnées IPTC")
        metadata_card.setVisible(False)
        self.pro_metadata_panel = metadata_card
        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(12)
        self.meta_keywords_edit = QLineEdit(); self.meta_keywords_edit.setPlaceholderText("Mots-clés...")
        self.meta_author_edit = QLineEdit(); self.meta_author_edit.setPlaceholderText("Artiste...")
        self.meta_copyright_edit = QLineEdit(); self.meta_copyright_edit.setPlaceholderText("Copyright...")
        self.meta_save_btn = _new_button("Mettre à jour IPTC")
        self.meta_save_btn.clicked.connect(self._save_selected_metadata)
        
        meta_layout.addWidget(self.meta_keywords_edit, 2)
        meta_layout.addWidget(self.meta_author_edit, 1)
        meta_layout.addWidget(self.meta_copyright_edit, 1)
        meta_layout.addWidget(self.meta_save_btn)
        metadata_card.content_layout.addLayout(meta_layout)
        layout.addWidget(metadata_card)

        # Status Bar Bottom
        self.status_bar_area = QWidget()
        s_layout = QHBoxLayout(self.status_bar_area)
        self.sync_progress = QProgressBar(); self.sync_progress.setFixedHeight(4); self.sync_progress.setTextVisible(False)
        self.sync_cancel_btn = _new_button("Quitter", primary=False)
        self.sync_cancel_btn.setFixedSize(60, 24)
        self.sync_cancel_btn.clicked.connect(self._cancel_sync)
        self.sync_progress.setVisible(False)
        self.sync_cancel_btn.setVisible(False)
        s_layout.addWidget(self.sync_progress, 1)
        s_layout.addWidget(self.sync_cancel_btn)
        layout.addWidget(self.status_bar_area)

        # Legacy stubs / References
        self._edit_panel_widgets = { "light": self.light_box, "color": self.color_box, "geometry": self.geometry_box, "actions": self.action_box }
        self.exif_info_label = QLabel() # Stub for compatibility
        self.advanced_panel = QWidget(); self.advanced_toggle = QToolButton(); self.meta_sync_btn = QWidget()
        self.action_scroll = QWidget(); self.before_after_btn = QWidget()

        self._connect_form_signals()
        self._build_shortcuts()
        self._apply_settings_to_form(dict(DEFAULT_EDIT_SETTINGS))
        self._apply_before_after_state()

    def _connect_form_signals(self) -> None:
        for slider in [
            self.exposure_slider, self.wb_temp_slider, self.wb_tint_slider,
            self.straighten_slider, self.contrast_slider, self.highlights_slider,
            self.shadows_slider, self.vibrance_slider, self.saturation_slider,
            self.clarity_slider
        ]:
            slider.valueChanged.connect(self._on_param_changed)
        
        self.crop_ratio_combo.currentIndexChanged.connect(self._on_param_changed)
        self._update_edit_value_labels()

    def _on_param_changed(self, *_args) -> None:
        if self._form_loading:
            return
        self._update_edit_value_labels()
        self._schedule_autosave()
        self._render_timer.start(30) # High-speed preview render

    def _build_slider_row(self, label_text: str, slider: QSlider, value_label: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        field_label = QLabel(label_text)
        field_label.setObjectName("EditFieldLabel")
        field_label.setFixedWidth(70)
        value_label.setObjectName("EditFieldValue")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setFixedWidth(65)
        slider.setMinimumHeight(24)
        row.addWidget(field_label)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        return row

    def _build_combo_row(self, label_text: str, combo: QComboBox) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        field_label = QLabel(label_text)
        field_label.setObjectName("EditFieldLabel")
        field_label.setFixedWidth(82)
        row.addWidget(field_label)
        row.addWidget(combo, 1)
        return row

    def _update_edit_value_labels(self, *_args) -> None:
        self.exposure_value_label.setText(f"{self.exposure_slider.value() / 100.0:+.2f}")
        self.contrast_value_label.setText(f"{int(self.contrast_slider.value()):+d}")
        self.wb_temp_value_label.setText(f"{int(self.wb_temp_slider.value())}K")
        self.wb_tint_value_label.setText(f"{int(self.wb_tint_slider.value()):+d}")
        self.straighten_value_label.setText(f"{self.straighten_slider.value() / 10.0:+.1f} deg")
        self.highlights_value_label.setText(f"{int(self.highlights_slider.value()):+d}")
        self.shadows_value_label.setText(f"{int(self.shadows_slider.value()):+d}")
        self.vibrance_value_label.setText(f"{int(self.vibrance_slider.value()):+d}")
        self.saturation_value_label.setText(f"{int(self.saturation_slider.value()):+d}")
        self.clarity_value_label.setText(f"{int(self.clarity_slider.value()):+d}")

    def _build_shortcuts(self) -> None:
        # Manual save removed (autosave is active)

        sync_shortcut = QShortcut(QKeySequence("Shift+S"), self)
        sync_shortcut.activated.connect(self._start_sync_filtered)
        self._shortcut_refs.append(sync_shortcut)

        solo_shortcut = QShortcut(QKeySequence("F"), self)
        solo_shortcut.activated.connect(lambda: self.solo_mode_btn.setChecked(not self.solo_mode_btn.isChecked()))
        self._shortcut_refs.append(solo_shortcut)
        
        ba_shortcut = QShortcut(QKeySequence("B"), self)
        ba_shortcut.activated.connect(lambda: self._toggle_before_after(not self._before_mode))
        self._shortcut_refs.append(ba_shortcut)

    def refresh_data(self) -> None:
        self._load_assets()

    def set_selected_project(self, project_id: int) -> None:
        self._selected_project_id = project_id
        self._load_assets()

    def _toggle_advanced_panel(self, opened: bool) -> None:
        self.pro_metadata_panel.setVisible(opened)

    def _toggle_pro_tools_panel(self, opened: bool) -> None:
        self.pro_tools_panel.setVisible(bool(opened))

    def _toggle_solo_mode(self, enabled: bool) -> None:
        self._solo_mode_enabled = bool(enabled)
        for widget in self._solo_hidden_widgets:
            widget.setVisible(not self._solo_mode_enabled)
        self.list_panel.setVisible(not self._solo_mode_enabled)
        
        if self._solo_mode_enabled:
            self.preview_label.setMinimumHeight(440)
        else:
            self.preview_label.setMinimumHeight(380)
            
        self.reset_layout_after_shell_resize()

    def _toggle_before_after(self, enabled: bool) -> None:
        self._before_mode = bool(enabled)
        self._apply_before_after_state()

    def _apply_before_after_state(self) -> None:
        if self._before_mode:
            self.before_after_badge.setText("AVANT (Original)")
            self.before_after_badge.setProperty("hudState", "info")
        else:
            self.before_after_badge.setText("APRES (Edition)")
            self.before_after_badge.setProperty("hudState", "ok")
        self.before_after_badge.style().unpolish(self.before_after_badge)
        self.before_after_badge.style().polish(self.before_after_badge)
        self.before_after_badge.update()
        self._render_timer.start(0)

    def _schedule_autosave(self, *_args) -> None:
        if self._form_loading:
            return
        self._autosave_timer.start(self._autosave_delay)

    def _load_assets(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            self._clear_asset_cards()
            self._set_selected_asset(None)
            self.assets_by_id = {}
            self.asset_order = []
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Apercu")
            self.asset_info_label.setText("Selection: -")
            return

        rejected_mode = self.rejected_mode_combo.currentData()
        min_rating = int(self.min_rating_filter_combo.currentData() or 0)
        assets = self.edit_service.list_assets(
            project_id=int(project_id),
            rejected_mode=str(rejected_mode),
            min_rating=min_rating,
        )

        current_asset_id = self.selected_asset_id
        self.assets_by_id = {int(asset.id): asset for asset in assets}
        self.asset_order = [int(asset.id) for asset in assets]
        if current_asset_id not in self.assets_by_id:
            current_asset_id = int(assets[0].id) if assets else None
        self.selected_asset_id = current_asset_id
        self._render_asset_cards(assets)
        self._set_selected_asset(self.selected_asset_id)
        self._on_select_asset()

    def _clear_asset_cards(self) -> None:
        self.asset_card_widgets = {}
        while self.asset_cards_layout.count():
            item = self.asset_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_asset_cards(self, assets: list) -> None:
        self._clear_asset_cards()
        if not assets:
            empty = QLabel("Aucun asset pour ces filtres.")
            empty.setObjectName("CardMuted")
            self.asset_cards_layout.addWidget(empty)
            self.asset_cards_layout.addStretch(1)
            return

        for asset in assets:
            is_selected = self.selected_asset_id is not None and int(asset.id) == int(self.selected_asset_id)
            card = self._build_asset_card(asset, is_selected=is_selected)
            self.asset_card_widgets[int(asset.id)] = card
            self.asset_cards_layout.addWidget(card)
        self.asset_cards_layout.addStretch(1)

    def _build_asset_card(self, asset, is_selected: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("DataCard")
        card.setProperty("selected", "true" if is_selected else "false")
        card.setMinimumHeight(88)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        thumb_label = QLabel()
        thumb_label.setObjectName("EditThumb")
        thumb_label.setFixedSize(82, 54)
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        src_path = Path(str(asset.src_path)) if asset.src_path else None
        thumb = self._load_asset_thumb(src_path, 82, 54)
        if thumb.isNull():
            fallback = QPixmap(82, 54)
            fallback.fill(QColor("#2B2B2B"))
            thumb_label.setPixmap(fallback)
        else:
            thumb_label.setPixmap(thumb)
        row.addWidget(thumb_label, 0)

        meta_col = QVBoxLayout()
        meta_col.setContentsMargins(0, 0, 0, 0)
        meta_col.setSpacing(6)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        short_name = self._short_asset_name(str(asset.file_name))
        select_btn = NativePushButton(short_name)
        select_btn.setProperty("cardSelect", "true")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.setMinimumHeight(30)
        select_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        select_btn.clicked.connect(lambda _checked=False, asset_id=asset.id: self._on_asset_card_selected(asset_id))
        badge = QLabel(f"{int(asset.rating)} ★")
        badge.setObjectName("CardBadge")
        header_row.addWidget(select_btn, 1)
        header_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        meta_col.addLayout(header_row)
        row.addLayout(meta_col, 1)
        card_layout.addLayout(row)
        return card

    @staticmethod
    def _short_asset_name(file_name: str, max_len: int = 28) -> str:
        text = Path(str(file_name)).name
        if len(text) <= max_len:
            return text
        keep = max(5, max_len - 3)
        return f"{text[:keep]}..."

    def _load_asset_thumb(self, file_path: Path | None, width: int, height: int) -> QPixmap:
        if file_path is None:
            return QPixmap()
        key = f"{file_path}|{width}x{height}"
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        source = QPixmap(str(file_path)) if file_path.exists() else QPixmap()
        if source.isNull():
            thumb = QPixmap()
        else:
            thumb = source.scaled(
                QSize(width, height),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        self._thumb_cache[key] = thumb
        self._thumb_cache_order.append(key)
        while len(self._thumb_cache_order) > 600:
            stale = self._thumb_cache_order.pop(0)
            self._thumb_cache.pop(stale, None)
        return thumb
    def _on_asset_card_selected(self, asset_id: int) -> None:
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
            self._save_current_asset_settings()
        self._set_selected_asset(asset_id)
        self._on_select_asset()

    def _set_selected_asset(self, asset_id: int | None) -> None:
        previous_id = self.selected_asset_id
        self.selected_asset_id = int(asset_id) if asset_id is not None else None

        if previous_id is not None and previous_id != self.selected_asset_id:
            previous_card = self.asset_card_widgets.get(int(previous_id))
            if previous_card is not None:
                previous_card.setProperty("selected", "false")
                previous_card.style().unpolish(previous_card)
                previous_card.style().polish(previous_card)
                previous_card.update()

        if self.selected_asset_id is not None:
            current_card = self.asset_card_widgets.get(int(self.selected_asset_id))
            if current_card is not None:
                current_card.setProperty("selected", "true")
                current_card.style().unpolish(current_card)
                current_card.style().polish(current_card)
                current_card.update()

    def _on_select_asset(self) -> None:
        asset_id = self.selected_asset_id
        if asset_id is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Aucun asset")
            self.asset_info_label.setText("Selection: -")
            self._apply_settings_to_form(dict(DEFAULT_EDIT_SETTINGS))
            self._clear_metadata_form()
            return

        asset = self.assets_by_id.get(int(asset_id))
        if asset is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Aucun asset")
            self.asset_info_label.setText("Selection: -")
            self._apply_settings_to_form(dict(DEFAULT_EDIT_SETTINGS))
            self._clear_metadata_form()
            return

        file_path = Path(str(asset.src_path)) if asset.src_path else None
        self._base_image = None
        if file_path is None or not file_path.exists():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Fichier introuvable")
        elif PIL_AVAILABLE:
            try:
                with Image.open(file_path) as img:
                    # Work on a reasonably sized preview for real-time performance
                    # but large enough for clarity.
                    max_dim = 1600
                    if img.width > max_dim or img.height > max_dim:
                        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                    self._base_image = img.convert("RGBA")
                self.preview_label.setText("")
            except Exception:
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText("Erreur lecture PIL")
        else:
            self.preview_label.setText("PIL non disponible")

        rejected = "oui" if bool(asset.is_rejected) else "non"
        self.asset_info_label.setText(
            f"Selection: {asset.file_name} | {int(asset.rating)} ★ | rejet={rejected}"
        )
        self._apply_settings_to_form(asset.edit_settings)
        self._load_selected_metadata()
        self._render_timer.start(0) # Initial render

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._base_image:
            self._render_timer.start(0)
        else:
            self._on_select_asset()

    def reset_layout_after_shell_resize(self) -> None:
        splitter = getattr(self, "body_splitter", None)
        if splitter is None:
            return
            
        total = max(1, int(splitter.width()))
        right_w = 350
        
        if self._solo_mode_enabled:
            # Hide left (collection), keep center and right (tools)
            center_w = max(400, total - right_w)
            splitter.setSizes([0, center_w, right_w])
        else:
            # Show all three modules
            left_w = 280
            center_w = max(400, total - (left_w + right_w))
            splitter.setSizes([left_w, center_w, right_w])

    def _apply_edit_preview(self) -> None:
        if not PIL_AVAILABLE or self._base_image is None:
            return
            
        settings = self._collect_form_settings()
        
        # If Before Mode is active, we bypass processing
        if self._before_mode:
            self._display_pil_image(self._base_image)
            return

        # Start from base
        img = self._base_image.copy()

        # 1. Geometry (Rotate) - Apply first before heavy pixel work
        angle = float(settings.get("straighten", 0.0))
        if abs(angle) > 0.01:
            img = img.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)

        # 2. Exposure (Approximate using Brightness)
        expo = float(settings.get("exposure", 0.0))
        if abs(expo) > 0.001:
            factor = pow(1.8, expo) # Adjust base to feel natural
            img = ImageEnhance.Brightness(img).enhance(factor)

        # 3. Contrast
        contrast = int(settings.get("contrast", 0))
        if contrast != 0:
            factor = 1.0 + (contrast / 100.0)
            img = ImageEnhance.Contrast(img).enhance(factor)

        # 4. White Balance (Simplified RGB multipliers)
        temp = int(settings.get("wb_temp", 5500))
        tint = int(settings.get("wb_tint", 0))
        if temp != 5500 or tint != 0:
             # Temp approx: Higher = Warmer (More Red/Yellow), Lower = Cooler (More Blue)
             r_m = 1.0 + (temp - 5500) / 12000.0
             b_m = 1.0 - (temp - 5500) / 12000.0
             g_m = 1.0 - (tint / 300.0)
             # Basic color transform matrix
             matrix = (r_m, 0, 0, 0,
                       0, g_m, 0, 0,
                       0, 0, b_m, 0)
             img = img.convert("RGB").convert("RGB", matrix).convert("RGBA")

        # 5. Advanced (Highlights / Shadows) - Simplified via Gamma/Brightness logic for now
        hl = int(settings.get("highlights", 0))
        sh = int(settings.get("shadows", 0))
        if hl != 0 or sh != 0:
            # Very simple fake HL/SH for preview speed
            total_corr = (sh * 0.3) + (hl * 0.1)
            if abs(total_corr) > 1:
                img = ImageEnhance.Brightness(img).enhance(1.0 + total_corr/100.0)

        # 6. Saturation / Vibrance
        sat = int(settings.get("saturation", 0))
        vib = int(settings.get("vibrance", 0))
        factor = 1.0 + ((sat + vib * 0.6) / 100.0)
        if abs(factor - 1.0) > 0.01:
            img = ImageEnhance.Color(img).enhance(factor)

        # 7. Clarity (Sharpness)
        clarity = int(settings.get("clarity", 0))
        if clarity != 0:
            factor = 1.0 + (clarity / 50.0)
            img = ImageEnhance.Sharpness(img).enhance(factor)

        self._display_pil_image(img)

    def _display_pil_image(self, img: Image.Image) -> None:
        if img is None: return
        
        # Convert PIL to QPixmap
        qimg = ImageQt(img)
        pixmap = QPixmap.fromImage(qimg)
        
        # Scale to label size
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _apply_settings_to_form(self, settings: dict[str, object]) -> None:
        payload = dict(DEFAULT_EDIT_SETTINGS)
        payload.update(settings or {})
        self._form_loading = True
        try:
            self.exposure_slider.setValue(int(round(float(payload.get("exposure", 0.0)) * 100.0)))
            self.wb_temp_slider.setValue(int(payload.get("wb_temp", 5500)))
            self.wb_tint_slider.setValue(int(payload.get("wb_tint", 0)))
            crop_ratio = str(payload.get("crop_ratio", "original"))
            crop_idx = self.crop_ratio_combo.findText(crop_ratio)
            self.crop_ratio_combo.setCurrentIndex(max(0, crop_idx))
            self.straighten_slider.setValue(int(round(float(payload.get("straighten", 0.0)) * 10.0)))
            self.contrast_slider.setValue(int(payload.get("contrast", 0)))
            self.highlights_slider.setValue(int(payload.get("highlights", 0)))
            self.shadows_slider.setValue(int(payload.get("shadows", 0)))
            self.vibrance_slider.setValue(int(payload.get("vibrance", 0)))
            self.saturation_slider.setValue(int(payload.get("saturation", 0)))
            self.clarity_slider.setValue(int(payload.get("clarity", 0)))
        finally:
            self._form_loading = False
        self._update_edit_value_labels()

    def _collect_form_settings(self) -> dict[str, object]:
        return {
            "exposure": float(self.exposure_slider.value()) / 100.0,
            "wb_temp": int(self.wb_temp_slider.value()),
            "wb_tint": int(self.wb_tint_slider.value()),
            "crop_ratio": str(self.crop_ratio_combo.currentText()),
            "straighten": float(self.straighten_slider.value()) / 10.0,
            "contrast": int(self.contrast_slider.value()),
            "highlights": int(self.highlights_slider.value()),
            "shadows": int(self.shadows_slider.value()),
            "vibrance": int(self.vibrance_slider.value()),
            "saturation": int(self.saturation_slider.value()),
            "clarity": int(self.clarity_slider.value()),
        }

    def _clear_metadata_form(self) -> None:
        self._metadata_form_loading = True
        try:
            self.meta_keywords_edit.setText("")
            self.meta_author_edit.setText("")
            self.meta_copyright_edit.setText("")
            self.exif_info_label.setText("EXIF: -")
        finally:
            self._metadata_form_loading = False

    def _load_selected_metadata(self) -> None:
        asset_id = self.selected_asset_id
        if asset_id is None:
            self._clear_metadata_form()
            return
        try:
            payload = self.metadata_service.get_asset_metadata(int(asset_id))
        except Exception:
            self._clear_metadata_form()
            return
        exif = payload.get("exif", {}) if isinstance(payload, dict) else {}
        iptc = payload.get("iptc", {}) if isinstance(payload, dict) else {}
        keywords = iptc.get("keywords", []) if isinstance(iptc, dict) else []
        keywords_text = ", ".join(str(item) for item in keywords if str(item).strip())
        author = str(iptc.get("author", "")).strip() if isinstance(iptc, dict) else ""
        copyright_text = str(iptc.get("copyright", "")).strip() if isinstance(iptc, dict) else ""

        camera = str(exif.get("camera", "")).strip() if isinstance(exif, dict) else ""
        lens = str(exif.get("lens_model", "")).strip() if isinstance(exif, dict) else ""
        iso = exif.get("iso", None) if isinstance(exif, dict) else None
        aperture = str(exif.get("aperture", "")).strip() if isinstance(exif, dict) else ""
        shutter = str(exif.get("shutter", "")).strip() if isinstance(exif, dict) else ""
        shot_date = str(exif.get("shot_date", "")).strip() if isinstance(exif, dict) else ""
        focal = exif.get("focal_length_mm", None) if isinstance(exif, dict) else None
        exif_parts = []
        if camera:
            exif_parts.append(camera)
        if lens:
            exif_parts.append(f"lens {lens}")
        if iso:
            exif_parts.append(f"ISO {iso}")
        if aperture:
            exif_parts.append(aperture)
        if shutter:
            exif_parts.append(shutter)
        if focal:
            exif_parts.append(f"{focal}mm")
        if shot_date:
            exif_parts.append(shot_date)
        exif_text = " | ".join(exif_parts) if exif_parts else "-"

        self._metadata_form_loading = True
        try:
            self.meta_keywords_edit.setText(keywords_text)
            self.meta_author_edit.setText(author)
            self.meta_copyright_edit.setText(copyright_text)
            self.exif_info_label.setText(f"EXIF: {exif_text}")
        finally:
            self._metadata_form_loading = False

    def _save_selected_metadata(self) -> None:
        if self._metadata_form_loading:
            return
        asset_id = self.selected_asset_id
        if asset_id is None:
            return
        try:
            self.metadata_service.update_asset_iptc(
                int(asset_id),
                keywords=self.meta_keywords_edit.text(),
                author=self.meta_author_edit.text(),
                copyright_text=self.meta_copyright_edit.text(),
            )
            self.on_job_event("[Metadata] IPTC sauvegarde.")
            self._load_selected_metadata()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur metadata", str(exc))

    def _sync_selected_metadata_to_filtered(self) -> None:
        project_id = self._selected_project_id
        asset_id = self.selected_asset_id
        if project_id is None or asset_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet et un asset source.")
            return
        try:
            result = self.metadata_service.sync_iptc_to_filtered(
                project_id=int(project_id),
                source_asset_id=int(asset_id),
                rejected_mode=str(self.rejected_mode_combo.currentData() or "kept"),
                min_rating=int(self.min_rating_filter_combo.currentData() or 0),
            )
            self.on_job_event(f"[Metadata] Sync {result.status} | maj={result.updated}/{result.total}")
            QMessageBox.information(
                self,
                "Sync IPTC",
                f"Statut: {result.status}\nMAJ: {result.updated}/{result.total}",
            )
            self._load_selected_metadata()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur metadata", str(exc))

    def _save_current_asset_settings(self) -> None:
        if self._form_loading:
            return
        asset_id = self.selected_asset_id
        if asset_id is None:
            return
        try:
            updated = self.edit_service.update_asset_edit_settings(asset_id=int(asset_id), updates=self._collect_form_settings())
            asset = self.assets_by_id.get(int(asset_id))
            if asset is not None:
                asset.edit_settings = updated
        except Exception as exc:
            QMessageBox.critical(self, "Erreur edition", str(exc))

    def _copy_current_settings(self) -> None:
        if self.selected_asset_id is None:
            return
        self._copied_settings = self._collect_form_settings()
        self.on_job_event("[Edit] Reglages copies.")

    def _paste_to_selected(self) -> None:
        if self.selected_asset_id is None or self._copied_settings is None:
            return
        try:
            updated = self.edit_service.update_asset_edit_settings(
                asset_id=int(self.selected_asset_id),
                updates=dict(self._copied_settings),
                replace=True,
            )
            asset = self.assets_by_id.get(int(self.selected_asset_id))
            if asset is not None:
                asset.edit_settings = updated
            self._apply_settings_to_form(updated)
            self.on_job_event("[Edit] Reglages colles.")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur edition", str(exc))

    def _reset_selected_settings(self) -> None:
        asset_id = self.selected_asset_id
        if asset_id is None:
            return
        try:
            updated = self.edit_service.reset_asset_edit_settings(asset_id=int(asset_id))
            asset = self.assets_by_id.get(int(asset_id))
            if asset is not None:
                asset.edit_settings = updated
            self._apply_settings_to_form(updated)
            self.on_job_event("[Edit] Reglages reinitialises.")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur edition", str(exc))

    def _start_sync_filtered(self) -> None:
        if self._job_thread is not None:
            QMessageBox.warning(self, "Operation en cours", "Un sync edit est deja en cours.")
            return
        project_id = self._selected_project_id
        asset_id = self.selected_asset_id
        if project_id is None or asset_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet et un asset source.")
            return

        self.sync_progress.setValue(0)
        self.sync_cancel_btn.setEnabled(True)
        self.on_operation_started()
        self.on_job_event(f"[Edit] Sync filtres lance depuis asset {asset_id}.")

        worker = JobWorker(
            self.edit_service.sync_edit_settings_to_filtered,
            project_id=int(project_id),
            source_asset_id=int(asset_id),
            rejected_mode=str(self.rejected_mode_combo.currentData()),
            min_rating=int(self.min_rating_filter_combo.currentData() or 0),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_sync_progress)
        worker.result.connect(self._on_sync_result)
        worker.error.connect(self._on_sync_error)
        worker.finished.connect(self._on_sync_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._job_worker = worker
        self._job_thread = thread
        thread.start()

    def _cancel_sync(self) -> None:
        if self._job_worker is not None:
            self._job_worker.cancel()
            self.sync_cancel_btn.setEnabled(False)
            self.on_job_event("[Edit] Annulation sync demandee.")

    def _on_sync_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.sync_progress.setMaximum(safe_total)
        self.sync_progress.setValue(max(0, min(int(done), safe_total)))

    def _on_sync_result(self, result) -> None:
        self._load_assets()
        self.on_job_event(f"[Edit] Sync {result.status} | maj={result.updated}/{result.total}")
        QMessageBox.information(
            self,
            "Sync edit",
            f"Statut: {result.status}\nMAJ: {result.updated}/{result.total}",
        )

    def _on_sync_error(self, message: str) -> None:
        self.on_job_event(f"[Edit] Erreur sync: {message}")
        QMessageBox.critical(self, "Erreur sync edit", message)

    def _on_sync_finished(self) -> None:
        self.sync_cancel_btn.setEnabled(False)
        self.on_operation_ended()
        self._job_worker = None
        self._job_thread = None
        self.on_job_event("[Edit] Sync termine.")


class ExportTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        preset_service: PresetService,
        export_service: ExportService,
        job_queue_service: JobQueueService | None,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.preset_service = preset_service
        self.export_service = export_service
        self.job_queue_service = job_queue_service
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)
        self._worker_id = f"export-ui-{id(self)}"
        self._last_auto_destination = ""
        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None
        self._queue_seq = 0
        self._queue_paused = False
        self._active_started_at: datetime | None = None
        self._active_queue_id: int | None = None
        self._queue_items: list[ExportQueueItem] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._selected_project_id: Optional[int] = None

        # ── 1. Top Control Bar ──
        top_card = BentoCard("Configuration de l'export")
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(12, 4, 12, 4)
        top_layout.setSpacing(12)

        top_layout.addWidget(QLabel("Dossier Destination:"))
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Dossier de sortie pour les exports...")
        browse_btn = _new_button("Parcourir")
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(self._pick_destination)
        
        top_layout.addWidget(self.destination_edit, 1)
        top_layout.addWidget(browse_btn)

        self.run_btn = _new_button("Exporter", primary=True)
        self.run_btn.setFixedWidth(120)
        self.run_btn.clicked.connect(self._run_export)
        top_layout.addWidget(self.run_btn)

        top_card.content_layout.addLayout(top_layout)
        layout.addWidget(top_card)

        # ── 2. Settings & Health Grid ──
        grid_row = QHBoxLayout()
        grid_row.setSpacing(10)

        # 2a. Export Settings
        settings_card = BentoCard("Paramètres d'export")
        settings_form = QFormLayout()
        settings_form.setSpacing(10)

        profiles_row = QHBoxLayout()
        self.web_check = QCheckBox("Web")
        self.print_check = QCheckBox("Impression")
        self.social_check = QCheckBox("Social")
        for cb in (self.web_check, self.print_check, self.social_check):
            cb.setChecked(True)
            profiles_row.addWidget(cb)
        settings_form.addRow("Profils:", profiles_row)

        self.min_rating_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_combo.addItem(str(rating), userData=rating)
        self.min_rating_combo.currentIndexChanged.connect(self._on_min_rating_changed)
        settings_form.addRow("Note Minimum:", self.min_rating_combo)

        delivery_layout = QVBoxLayout()
        self.zip_check = QCheckBox("Créer une archive ZIP")
        self.report_check = QCheckBox("Générer rapport .txt")
        self.contact_sheet_check = QCheckBox("Planche contact PDF")
        for cb in (self.zip_check, self.report_check, self.contact_sheet_check):
            cb.setChecked(True)
            delivery_layout.addWidget(cb)
        settings_form.addRow("Options:", delivery_layout)

        settings_card.content_layout.addLayout(settings_form)
        grid_row.addWidget(settings_card, 1)

        # 2b. Project Health (Checklist)
        health_card = BentoCard("Santé du Projet (Checklist)")
        health_layout = QVBoxLayout()
        
        self.quality_state_label = QLabel("Checklist: -")
        self.quality_state_label.setObjectName("CardValue")
        self.quality_state_label.setStyleSheet("font-size: 16px; font-weight: bold; background: transparent;")
        
        self.quality_summary_container = QWidget()
        self.quality_summary_layout = QHBoxLayout(self.quality_summary_container)
        self.quality_summary_layout.setContentsMargins(0, 0, 0, 0)
        self.quality_summary_layout.setSpacing(6)
        
        health_layout.addWidget(self.quality_state_label)
        health_layout.addWidget(self.quality_summary_container)
        health_layout.addStretch(1)

        health_btns = QHBoxLayout()
        self.quality_verify_btn = _new_button("Vérifier")
        self.quality_verify_btn.clicked.connect(self._verify_quality_gate)
        self.quality_validate_btn = _new_button("Valider")
        self.quality_validate_btn.clicked.connect(self._validate_quality_gate)
        health_btns.addWidget(self.quality_verify_btn)
        health_btns.addWidget(self.quality_validate_btn)
        health_layout.addLayout(health_btns)

        health_card.content_layout.addLayout(health_layout)
        grid_row.addWidget(health_card, 1)

        layout.addLayout(grid_row)

        # ── 3. Queue ──
        self.queue_box = BentoCard("File d'attente des exports")
        queue_layout = QVBoxLayout()
        
        queue_header = QHBoxLayout()
        self.queue_state_label = QLabel("Prêt")
        self.queue_counts_label = QLabel("Total: 0")
        self.queue_counts_label.setObjectName("CullingMeta")
        queue_header.addWidget(self.queue_state_label)
        queue_header.addStretch(1)
        queue_header.addWidget(self.queue_counts_label)
        queue_layout.addLayout(queue_header)

        queue_actions = QHBoxLayout()
        self.queue_add_btn = _new_button("Ajouter à la file")
        self.queue_add_btn.clicked.connect(self._enqueue_current_export)
        self.start_queue_btn = _new_button("Lancer la file")
        self.start_queue_btn.clicked.connect(self._start_next_queue_item)
        self.pause_queue_btn = _new_button("Pause")
        self.pause_queue_btn.setCheckable(True)
        self.pause_queue_btn.toggled.connect(self._toggle_queue_pause)
        self.retry_failed_btn = _new_button("Réessayer erreurs")
        self.retry_failed_btn.clicked.connect(self._retry_failed_queue_items)
        self.clear_completed_btn = _new_button("Vider l'historique")
        self.clear_completed_btn.clicked.connect(self._clear_completed_queue_items)
        
        queue_actions.addWidget(self.queue_add_btn)
        queue_actions.addWidget(self.start_queue_btn)
        queue_actions.addSpacing(20)
        queue_actions.addWidget(self.pause_queue_btn)
        queue_actions.addWidget(self.retry_failed_btn)
        queue_actions.addWidget(self.clear_completed_btn)
        queue_actions.addStretch(1)
        queue_layout.addLayout(queue_actions)

        self.queue_cards_area = QScrollArea()
        self.queue_cards_area.setWidgetResizable(True)
        self.queue_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.queue_cards_area.setMinimumHeight(150)
        self.queue_cards_content = QWidget()
        self.queue_cards_layout = QVBoxLayout(self.queue_cards_content)
        self.queue_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_cards_layout.setSpacing(4)
        self.queue_cards_area.setWidget(self.queue_cards_content)
        queue_layout.addWidget(self.queue_cards_area)

        self.queue_box.content_layout.addLayout(queue_layout)
        layout.addWidget(self.queue_box, 1)

        # ── 4. Progress Area ──
        progress_container = QWidget()
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(10, 0, 10, 10)
        progress_layout.setSpacing(4)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)

        self.eta_label = QLabel("En attente...")
        self.eta_label.setObjectName("CullingMeta")
        self.eta_label.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.eta_label)
        layout.addWidget(progress_container)

        # Removed redundant cancel_btn and log_text visibility handling
        self.cancel_btn = QWidget() # Dummy for internal compatibility
        self.log_text = QPlainTextEdit() # Keeping it for log output even if hidden
        self.log_text.setVisible(False)

        # Toggles and secondary panels removed and integrated or hidden
        pass

        if self.job_queue_service is not None:
            recovered = self.job_queue_service.recover_stale_running_jobs(stale_after_seconds=90)
            if recovered > 0:
                self.on_job_event(f"[Export] {recovered} job(s) stale recupere(s).")

    def refresh_data(self) -> None:
        self._sync_export_context()
        self._load_queue_from_backend()
        self._refresh_queue_view()

    def set_selected_project(self, project_id: int) -> None:
        self._selected_project_id = project_id
        self._sync_export_context()

    def _sync_export_context(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            self._set_quality_banner(None)
            return
        project = self.project_service.get_project(project_id)
        if project is None:
            self._set_quality_banner(None)
            return
        self._sync_default_destination(project)
        self._sync_delivery_options_from_preset(project)
        self._refresh_quality_banner()

    def _sync_default_destination(self, project) -> None:
        auto_destination = str(Path(project.root_path) / "exports")
        current = self.destination_edit.text().strip()
        if not current or current == self._last_auto_destination:
            self.destination_edit.setText(auto_destination)
        self._last_auto_destination = auto_destination

    def _sync_delivery_options_from_preset(self, project) -> None:
        config = default_preset_config()
        try:
            config = self.preset_service.resolve_effective_config_for_project(project.id)
        except Exception:
            pass

        delivery = config.get("delivery", {})
        self.zip_check.setChecked(bool(delivery.get("create_zip", True)))
        self.report_check.setChecked(bool(delivery.get("create_report", True)))
        self.contact_sheet_check.setChecked(bool(delivery.get("create_contact_sheet_pdf", True)))

    def _pick_destination(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier destination")
        if directory:
            self.destination_edit.setText(directory)

    @staticmethod
    def _quality_state_text(state: str) -> str:
        mapping = {
            "disabled": "desactivee",
            "not_validated": "non validee",
            "stale": "a revalider",
            "validated": "validee",
        }
        return mapping.get(str(state), str(state))

    def _set_quality_banner(self, snapshot: dict | None) -> None:
        # Clear tags
        while self.quality_summary_layout.count():
            item = self.quality_summary_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not snapshot:
            self.quality_state_label.setText("Checklist: -")
            self.quality_summary_layout.addWidget(_new_tag_label("Résumé: -", "#6B7280"))
            self.quality_summary_layout.addStretch()
            return

        state = self._quality_state_text(str(snapshot.get("status", "not_validated")))
        summary = snapshot.get("summary", {}) if isinstance(snapshot.get("summary"), dict) else {}
        exportable = int(summary.get("exportable_count", 0) or 0)
        missing_author = int(summary.get("missing_author_count", 0) or 0)
        missing_copyright = int(summary.get("missing_copyright_count", 0) or 0)
        
        self.quality_state_label.setText(f"Checklist: {state}")
        
        # Add visual tags
        self.quality_summary_layout.addWidget(_new_tag_label(f"{exportable} Photos", "#10B981"))
        if missing_author > 0:
            self.quality_summary_layout.addWidget(_new_tag_label(f"{missing_author} Auteur ?", "#EF4444"))
        if missing_copyright > 0:
            self.quality_summary_layout.addWidget(_new_tag_label(f"{missing_copyright} Copyright ?", "#EF4444"))
        
        val_date = snapshot.get('validated_at_utc')
        if val_date:
            self.quality_summary_layout.addWidget(_new_tag_label(f"Validé: {val_date[:10]}", "#3B82F6"))
            
        self.quality_summary_layout.addStretch()

    def _refresh_quality_banner(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            self._set_quality_banner(None)
            return
        min_rating = int(self.min_rating_combo.currentData() or 0)
        try:
            snapshot = self.project_service.get_quality_check(int(project_id), export_min_rating=min_rating)
        except Exception as exc:
            self.quality_state_label.setText("Checklist: erreur")
            self.quality_summary_label.setText(f"Resume checklist: {exc}")
            return
        self._set_quality_banner(snapshot)

    def _on_min_rating_changed(self, _index: int = -1) -> None:
        self._refresh_quality_banner()

    def _verify_quality_gate(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            QMessageBox.warning(self, "Checklist", "Selectionne un projet.")
            return
        min_rating = int(self.min_rating_combo.currentData() or 0)
        try:
            snapshot = self.project_service.assert_export_quality(int(project_id), export_min_rating=min_rating)
            self._set_quality_banner(snapshot)
            QMessageBox.information(self, "Checklist", "Checklist qualite valide pour export.")
        except QualityChecklistError as exc:
            self._refresh_quality_banner()
            QMessageBox.critical(self, "Checklist", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Checklist", str(exc))

    def _validate_quality_gate(self) -> None:
        project_id = self._selected_project_id
        if project_id is None:
            QMessageBox.warning(self, "Checklist", "Selectionne un projet.")
            return
        try:
            self.project_service.validate_quality_check(int(project_id))
        except QualityChecklistError as exc:
            self._refresh_quality_banner()
            QMessageBox.critical(self, "Checklist", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Checklist", str(exc))
            return
        self._refresh_quality_banner()
        QMessageBox.information(self, "Checklist", "Checklist projet validee.")

    def _build_export_payload(self) -> dict | None:
        project_id = self._selected_project_id
        destination = self.destination_edit.text().strip()
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return None
        if not destination:
            QMessageBox.warning(self, "Validation", "Selectionne un dossier destination.")
            return None

        profiles: list[str] = []
        if self.web_check.isChecked():
            profiles.append("web")
        if self.print_check.isChecked():
            profiles.append("print")
        if self.social_check.isChecked():
            profiles.append("social")
        if not profiles:
            QMessageBox.warning(self, "Validation", "Selectionne au moins un profil.")
            return None

        safe_min_rating = int(self.min_rating_combo.currentData() or 0)
        try:
            snapshot = self.project_service.assert_export_quality(int(project_id), export_min_rating=safe_min_rating)
            self._set_quality_banner(snapshot)
        except QualityChecklistError as exc:
            self._refresh_quality_banner()
            QMessageBox.critical(self, "Checklist qualite", str(exc))
            return None
        except Exception as exc:
            QMessageBox.critical(self, "Checklist qualite", str(exc))
            return None

        return {
            "project_id": int(project_id),
            "project_label": str(self._selected_project_id or "N/A"),
            "destination_dir": destination,
            "profiles": profiles,
            "min_rating": safe_min_rating,
            "create_zip": self.zip_check.isChecked(),
            "create_report": self.report_check.isChecked(),
            "create_contact_sheet": self.contact_sheet_check.isChecked(),
        }

    @staticmethod
    def _payload_from_queue_item(item: ExportQueueItem) -> dict:
        return {
            "project_id": int(item.project_id),
            "project_label": str(item.project_label),
            "destination_dir": str(item.destination_dir),
            "profiles": list(item.profiles),
            "min_rating": int(item.min_rating),
            "create_zip": bool(item.create_zip),
            "create_report": bool(item.create_report),
            "create_contact_sheet": bool(item.create_contact_sheet),
        }

    def _load_queue_from_backend(self) -> None:
        if self.job_queue_service is None:
            return
        snapshots = self.job_queue_service.list_jobs(
            statuses=(
                "queued",
                "retry_waiting",
                "running",
                "completed",
                "failed",
                "canceled",
            ),
            limit=500,
        )
        export_jobs = [item for item in snapshots if str(item.job_type) == "export"]
        export_jobs.sort(key=lambda item: int(item.id))

        items: list[ExportQueueItem] = []
        for snap in export_jobs:
            payload = dict(snap.payload or {})
            profiles = [str(p) for p in payload.get("profiles", []) if str(p).strip()]
            if not profiles:
                profiles = ["web"]
            item = ExportQueueItem(
                queue_id=int(snap.id),
                db_job_id=int(snap.id),
                project_id=int(payload.get("project_id") or (snap.project_id or 0)),
                project_label=str(payload.get("project_label") or f"{payload.get('project_id', snap.project_id)}"),
                destination_dir=str(payload.get("destination_dir") or ""),
                profiles=profiles,
                min_rating=max(0, min(5, int(payload.get("min_rating", 0) or 0))),
                create_zip=bool(payload.get("create_zip", False)),
                create_report=bool(payload.get("create_report", True)),
                create_contact_sheet=bool(payload.get("create_contact_sheet", False)),
                status=str(snap.status),
                attempts=max(1, int(snap.attempts or 1)),
                queued_at=snap.created_at,
                started_at=snap.locked_at,
                ended_at=snap.updated_at
                if str(snap.status) in {"completed", "failed", "canceled"}
                else None,
                message=str(snap.error_message or ""),
            )
            items.append(item)

        self._queue_items = items
        if self._queue_items:
            self._queue_seq = max(int(item.queue_id) for item in self._queue_items)
        else:
            self._queue_seq = 0

    def _enqueue_payload(self, payload: dict, attempts: int = 1) -> ExportQueueItem:
        db_job_id: int | None = None
        if self.job_queue_service is not None:
            snap = self.job_queue_service.enqueue(
                job_type="export",
                payload=dict(payload),
                project_id=int(payload["project_id"]),
                priority=100,
                max_attempts=3,
            )
            db_job_id = int(snap.id)
            self._queue_seq = max(self._queue_seq, int(db_job_id))
        else:
            self._queue_seq += 1
        item = ExportQueueItem(
            queue_id=int(db_job_id if db_job_id is not None else self._queue_seq),
            db_job_id=db_job_id,
            project_id=int(payload["project_id"]),
            project_label=str(payload["project_label"]),
            destination_dir=str(payload["destination_dir"]),
            profiles=list(payload["profiles"]),
            min_rating=int(payload["min_rating"]),
            create_zip=bool(payload["create_zip"]),
            create_report=bool(payload["create_report"]),
            create_contact_sheet=bool(payload["create_contact_sheet"]),
            status="queued",
            attempts=max(1, int(attempts)),
            queued_at=datetime.utcnow(),
        )
        self._queue_items.append(item)
        self._refresh_queue_view()
        return item

    def _enqueue_current_export(self) -> None:
        payload = self._build_export_payload()
        if payload is None:
            return
        item = self._enqueue_payload(payload)
        self.on_job_event(f"[Export] Ajoute a la queue: #{item.queue_id} ({item.project_label}).")

    def _run_export(self) -> None:
        payload = self._build_export_payload()
        if payload is None:
            return
        item = self._enqueue_payload(payload)
        self.on_job_event(f"[Export] Queue + start: #{item.queue_id} ({item.project_label}).")
        self._start_next_queue_item()

    def _next_queued_item(self) -> ExportQueueItem | None:
        for item in self._queue_items:
            if item.status in {"queued", "retry_waiting"}:
                return item
        return None

    def _queue_item_by_id(self, queue_id: int | None) -> ExportQueueItem | None:
        if queue_id is None:
            return None
        for item in self._queue_items:
            if int(item.queue_id) == int(queue_id):
                return item
        return None

    def _start_next_queue_item(self) -> None:
        if self._job_thread is not None:
            return
        if self._queue_paused:
            self._refresh_queue_view()
            return

        item: ExportQueueItem | None = None
        if self.job_queue_service is not None:
            claimed = self.job_queue_service.claim_next(worker_id=self._worker_id, allowed_job_types=("export",))
            if claimed is None:
                self._load_queue_from_backend()
                self._refresh_queue_view()
                return
            item = self._queue_item_by_id(int(claimed.id))
            if item is None:
                self._load_queue_from_backend()
                item = self._queue_item_by_id(int(claimed.id))
            if item is None:
                payload = dict(claimed.payload or {})
                item = ExportQueueItem(
                    queue_id=int(claimed.id),
                    db_job_id=int(claimed.id),
                    project_id=int(payload.get("project_id") or (claimed.project_id or 0)),
                    project_label=str(payload.get("project_label") or f"{claimed.project_id}"),
                    destination_dir=str(payload.get("destination_dir") or ""),
                    profiles=[str(p) for p in payload.get("profiles", ["web"])],
                    min_rating=max(0, min(5, int(payload.get("min_rating", 0) or 0))),
                    create_zip=bool(payload.get("create_zip", False)),
                    create_report=bool(payload.get("create_report", True)),
                    create_contact_sheet=bool(payload.get("create_contact_sheet", False)),
                    status="running",
                    attempts=max(1, int(claimed.attempts)),
                    queued_at=claimed.created_at,
                    started_at=claimed.locked_at,
                    ended_at=None,
                    message="",
                )
                self._queue_items.append(item)
            item.status = "running"
            item.attempts = max(1, int(claimed.attempts))
            item.started_at = claimed.locked_at or datetime.utcnow()
        else:
            item = self._next_queued_item()
            if item is None:
                self._refresh_queue_view()
                return

        self._active_queue_id = int(item.queue_id)
        self._active_started_at = datetime.utcnow()
        self.progress_bar.setValue(0)
        self.eta_label.setText("ETA: calcul...")
        self.cancel_btn.setEnabled(True)
        self.on_operation_started()
        item.status = "running"
        item.started_at = datetime.utcnow()
        item.ended_at = None
        item.message = ""
        self._refresh_queue_view()

        worker = JobWorker(
            self.export_service.run_export,
            project_id=item.project_id,
            destination_dir=item.destination_dir,
            profiles=list(item.profiles),
            min_rating=int(item.min_rating),
            create_zip=bool(item.create_zip),
            create_report=bool(item.create_report),
            create_contact_sheet=bool(item.create_contact_sheet),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_export_progress)
        worker.result.connect(self._on_export_result)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(self._on_export_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._job_worker = worker
        self._job_thread = thread
        thread.start()

    def _cancel_export(self) -> None:
        if self._job_worker is not None:
            self._job_worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.on_job_event("[Export] Annulation demandee pour le job actif.")
            active = self._queue_item_by_id(self._active_queue_id)
            if active is not None:
                active.message = "Annulation demandee"
                self._refresh_queue_view()

    def _toggle_queue_pause(self, paused: bool) -> None:
        self._queue_paused = bool(paused)
        if self._queue_paused:
            self.pause_queue_btn.setText("Reprendre file")
            self.on_job_event("[Export] Queue en pause (le job actif continue).")
        else:
            self.pause_queue_btn.setText("Pause file")
            self.on_job_event("[Export] Queue reprise.")
            self._start_next_queue_item()
        self._refresh_queue_view()

    def _retry_failed_queue_items(self) -> None:
        failed_items = [item for item in self._queue_items if item.status in {"failed", "cancelled", "canceled"}]
        if not failed_items:
            QMessageBox.information(self, "Retry queue", "Aucun job failed/cancelled a relancer.")
            return
        for item in failed_items:
            payload = self._payload_from_queue_item(item)
            self._enqueue_payload(payload, attempts=int(item.attempts) + 1)
        self.on_job_event(f"[Export] Retry queue: {len(failed_items)} job(s) re-ajoutes.")
        self._start_next_queue_item()

    def _clear_completed_queue_items(self) -> None:
        if self.job_queue_service is not None:
            try:
                self.job_queue_service.purge_jobs(statuses=("completed",), older_than_seconds=0)
            except Exception:
                pass
            self._load_queue_from_backend()
        self._queue_items = [item for item in self._queue_items if item.status not in {"completed"}]
        self._refresh_queue_view()

    def _on_export_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(max(0, min(int(done), safe_total)))
        active_item = self._queue_item_by_id(self._active_queue_id)
        if self.job_queue_service is not None and active_item is not None and active_item.db_job_id is not None:
            try:
                self.job_queue_service.heartbeat(
                    job_id=int(active_item.db_job_id),
                    worker_id=self._worker_id,
                    progress_done=int(done),
                    progress_total=int(total),
                    message=str(detail),
                )
            except Exception:
                pass
        if self._active_started_at is not None and done > 0:
            elapsed = max(0.001, (datetime.utcnow() - self._active_started_at).total_seconds())
            remaining = int(round((elapsed / float(done)) * max(0, safe_total - int(done))))
            self.eta_label.setText(f"ETA: ~{remaining}s ({detail})")
        else:
            self.eta_label.setText(f"ETA: calcul... ({detail})")

    def _on_export_result(self, batch) -> None:
        total_exported = 0
        total_failed = 0
        cancelled = False
        for item in batch.profiles:
            self.log_text.appendPlainText(
                f"Export {item.profile}: {item.status} | exported={item.exported}, "
                f"failed={item.failed} | out={item.output_dir}"
            )
            total_exported += int(item.exported)
            total_failed += int(item.failed)
            if str(item.status).lower().startswith("cancel"):
                cancelled = True
            if item.message:
                self.log_text.appendPlainText(item.message)
        if batch.report_path is not None:
            self.log_text.appendPlainText(f"Rapport export: {batch.report_path}")
        if batch.zip_path is not None:
            self.log_text.appendPlainText(f"ZIP livraison: {batch.zip_path}")
        if batch.contact_sheet_path is not None:
            self.log_text.appendPlainText(f"Planche contact PDF: {batch.contact_sheet_path}")

        active_item = self._queue_item_by_id(self._active_queue_id)
        if active_item is not None:
            active_item.ended_at = datetime.utcnow()
            summary = f"exported={total_exported}, failed={total_failed}, profils={len(batch.profiles)}"
            active_item.message = summary
            if self.job_queue_service is not None and active_item.db_job_id is not None:
                try:
                    if cancelled:
                        snap = self.job_queue_service.cancel(job_id=int(active_item.db_job_id), reason="cancelled")
                    elif total_failed > 0:
                        snap = self.job_queue_service.fail(
                            job_id=int(active_item.db_job_id),
                            worker_id=self._worker_id,
                            error_message=summary,
                            error_code="export_partial_failure",
                        )
                    else:
                        snap = self.job_queue_service.complete(
                            job_id=int(active_item.db_job_id),
                            worker_id=self._worker_id,
                            message=summary,
                        )
                    active_item.status = str(snap.status)
                    active_item.attempts = int(snap.attempts)
                    active_item.message = str(snap.error_message or summary)
                except Exception:
                    active_item.status = "failed" if total_failed > 0 else "completed"
            else:
                if cancelled:
                    active_item.status = "cancelled"
                elif total_failed > 0:
                    active_item.status = "failed"
                else:
                    active_item.status = "completed"

        self.on_job_event(
            f"[Export] termine | exported={total_exported}, failed={total_failed}, profils={len(batch.profiles)}"
        )
        self._refresh_queue_view()

    def _on_export_error(self, message: str) -> None:
        lower_message = str(message or "").lower()
        error_code = "quality_check_failed" if "quality_check_failed" in lower_message else "export_runtime_error"
        active_item = self._queue_item_by_id(self._active_queue_id)
        if active_item is not None:
            active_item.ended_at = datetime.utcnow()
            active_item.message = str(message)
            if self.job_queue_service is not None and active_item.db_job_id is not None:
                try:
                    snap = self.job_queue_service.fail(
                        job_id=int(active_item.db_job_id),
                        worker_id=self._worker_id,
                        error_message=str(message),
                        error_code=error_code,
                    )
                    active_item.status = str(snap.status)
                    active_item.attempts = int(snap.attempts)
                    active_item.message = str(snap.error_message or message)
                except Exception:
                    active_item.status = "failed"
            else:
                active_item.status = "failed"
            self._refresh_queue_view()
        self.log_text.appendPlainText(f"Erreur export: {message}")
        self.on_job_event(f"[Export] Erreur: {message}")

    def _on_export_finished(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.eta_label.setText("ETA: -")
        self.on_operation_ended()
        self._job_worker = None
        self._job_thread = None
        self._active_queue_id = None
        self._active_started_at = None
        self._load_queue_from_backend()
        self.on_job_event("[Export] Job actif termine.")
        self._refresh_queue_view()
        self._start_next_queue_item()

    def _clear_queue_cards(self) -> None:
        while self.queue_cards_layout.count():
            item = self.queue_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _queue_badge_text(item: ExportQueueItem) -> str:
        return f"{item.status.upper()} | try {item.attempts}"

    def _render_queue_cards(self) -> None:
        self._clear_queue_cards()
        if not self._queue_items:
            empty = QLabel("Queue vide.")
            empty.setObjectName("CardMuted")
            self.queue_cards_layout.addWidget(empty)
            self.queue_cards_layout.addStretch(1)
            return

        for item in self._queue_items:
            card = QFrame()
            card.setObjectName("DataCard")
            is_running = item.status == "running"
            card.setProperty("selected", "true" if is_running else "false")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)

            row = QHBoxLayout()
            title = QLabel(f"#{item.queue_id} {item.project_label}")
            title.setObjectName("CardTitle")
            badge = QLabel(self._queue_badge_text(item))
            badge.setObjectName("CardBadge")
            row.addWidget(title, 1)
            row.addWidget(badge)
            card_layout.addLayout(row)

            dest_label = QLabel(f"Dossier: {item.destination_dir}")
            dest_label.setObjectName("CardMuted")
            dest_label.setWordWrap(True)
            card_layout.addWidget(dest_label)

            tags_row = QHBoxLayout()
            tags_row.setSpacing(6)
            
            # Profiles Tags
            profile_colors = {"web": "#10B981", "print": "#3B82F6", "social": "#F59E0B"}
            for p in item.profiles:
                tags_row.addWidget(_new_tag_label(p.upper(), profile_colors.get(p.lower(), "#6B7280")))
            
            # Rating Tag
            tags_row.addWidget(_new_tag_label(f"Note >= {item.min_rating} ★", "#6366F1")) # Indigo
            
            # Options Tags
            if item.create_zip: tags_row.addWidget(_new_tag_label("ZIP", "#EC4899")) # Pink
            if item.create_report: tags_row.addWidget(_new_tag_label("RAPPORT", "#8B5CF6")) # Violet
            if item.create_contact_sheet: tags_row.addWidget(_new_tag_label("CONTACT", "#F43F5E")) # Rose
            
            tags_row.addStretch(1)
            card_layout.addLayout(tags_row)

            if item.message:
                msg = QLabel(item.message)
                msg.setObjectName("CardMuted")
                msg.setWordWrap(True)
                card_layout.addWidget(msg)
            self.queue_cards_layout.addWidget(card)
        self.queue_cards_layout.addStretch(1)

    def _refresh_queue_view(self) -> None:
        pending = len([item for item in self._queue_items if item.status == "queued"])
        running = len([item for item in self._queue_items if item.status == "running"])
        done = len([item for item in self._queue_items if item.status == "completed"])
        retry_waiting = len([item for item in self._queue_items if item.status == "retry_waiting"])
        failed = len([item for item in self._queue_items if item.status in {"failed", "cancelled", "canceled"}])

        if self._queue_paused:
            state = "Queue: paused"
        elif running > 0:
            state = "Queue: running"
        elif retry_waiting > 0:
            state = "Queue: retry_waiting"
        elif pending > 0:
            state = "Queue: ready"
        else:
            state = "Queue: idle"
        self.queue_state_label.setText(state)
        self.queue_counts_label.setText(
            f"pending={pending} | retry={retry_waiting} | running={running} | done={done} | failed={failed}"
        )
        self._render_queue_cards()


class PresetTab(QWidget):
    def __init__(self, preset_service: PresetService, on_data_changed) -> None:
        super().__init__()
        self.preset_service = preset_service
        self.on_data_changed = on_data_changed
        self.current_preset_id: int | None = None
        self.expanded_preset_ids: set[int] = set()
        self.profile_widgets: dict[str, dict[str, object]] = {}
        self.watermark_cfg = normalize_watermark_config(default_preset_config().get("watermark", {}))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter = split
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        layout.addWidget(split, 1)

        form_widget = QFrame()
        form_widget.setObjectName("PresetFormPanel")
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(12, 10, 12, 12)
        form_layout.setSpacing(10)

        header_box = BentoCard()
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(16)
        
        name_section = QVBoxLayout()
        name_section.setSpacing(2)
        name_section.addWidget(QLabel("Nom du Preset"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("ex: Shooting Mariage Standard")
        name_section.addWidget(self.name_edit)
        
        proj_section = QVBoxLayout()
        proj_section.setSpacing(2)
        proj_section.addWidget(QLabel("Projets associés"))
        self.associated_projects_label = QLabel("-")
        self.associated_projects_label.setObjectName("CardValue")
        proj_section.addWidget(self.associated_projects_label)
        
        header_layout.addLayout(name_section, 2)
        header_layout.addLayout(proj_section, 3)
        header_box.content_layout.addLayout(header_layout)
        form_layout.addWidget(header_box)

        action_bar = QFrame()
        action_bar.setObjectName("PresetActionBar")
        action_bar.setStyleSheet("background: #1A1D21; border: 1px solid #333; border-radius: 10px; margin: 4px 0;")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(12, 6, 12, 6)
        action_layout.setSpacing(12)
        
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(220)
        self.version_combo.setPlaceholderText("Versions précédentes...")
        rollback_btn = _new_button("Restaurer")
        rollback_btn.clicked.connect(self._rollback)
        
        hist_label = QLabel("HISTORIQUE")
        hist_label.setObjectName("PresetFieldLabel")
        hist_label.setStyleSheet("margin-top: 4px; background: transparent;")
        
        action_layout.addWidget(hist_label)
        action_layout.addWidget(self.version_combo)
        action_layout.addWidget(rollback_btn)
        
        self.expert_mode_check = QCheckBox("Mode JSON")
        self.expert_mode_check.toggled.connect(self._set_expert_mode)
        action_layout.addWidget(self.expert_mode_check)
        
        action_layout.addStretch(1)
        new_btn = _new_button("Nouveau")
        new_btn.clicked.connect(self._reset_form)
        save_btn = _new_button("Enregistrer", primary=True)
        save_btn.clicked.connect(self._save)
        delete_btn = _new_button("Supprimer")
        delete_btn.clicked.connect(self._delete)
        action_layout.addWidget(new_btn)
        action_layout.addWidget(save_btn)
        action_layout.addWidget(delete_btn)
        form_layout.addWidget(action_bar)

        self.config_tabs = QTabWidget()
        self.form_config_tab = self._build_form_config_tab()
        self.json_config_tab = QWidget()
        json_layout = QVBoxLayout(self.json_config_tab)
        self.config_edit = QPlainTextEdit()
        self.config_edit.setPlaceholderText("Configuration JSON du preset")
        self.config_edit.setMinimumWidth(430)
        json_layout.addWidget(self.config_edit)
        self.config_tabs.addTab(self.form_config_tab, "Formulaire")
        self.config_tabs.addTab(self.json_config_tab, "JSON")
        self.config_tabs.tabBar().setVisible(False)
        self.config_tabs.setCurrentIndex(0)
        form_layout.addWidget(self.config_tabs, 1)

        self.sync_controls = QWidget()
        sync_row = QHBoxLayout(self.sync_controls)
        sync_row.setContentsMargins(0, 0, 0, 0)
        sync_row.setSpacing(8)
        to_json_btn = _new_button("Formulaire -> JSON")
        to_json_btn.clicked.connect(self._sync_json_from_form)
        to_form_btn = _new_button("JSON -> Formulaire")
        to_form_btn.clicked.connect(self._sync_form_from_json)
        sync_row.addStretch(1)
        sync_row.addWidget(to_json_btn)
        sync_row.addWidget(to_form_btn)
        self.sync_controls.setVisible(False)
        form_layout.addWidget(self.sync_controls)

        split.addWidget(form_widget)

        sidebar_widget = QFrame()
        sidebar_widget.setObjectName("PresetSidebar")
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(8)
        self.preset_search_edit = QLineEdit()
        self.preset_search_edit.setObjectName("PresetSearch")
        self.preset_search_edit.setPlaceholderText("Rechercher un preset...")
        self.preset_search_edit.setMinimumHeight(36)
        self.preset_search_edit.setStyleSheet("padding-left: 32px; background: #121212; border-radius: 12px;")
        self.preset_search_edit.textChanged.connect(self._on_preset_search_changed)
        sidebar_layout.addWidget(self.preset_search_edit)

        self.preset_cards_area = QScrollArea()
        self.preset_cards_area.setWidgetResizable(True)
        self.preset_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.preset_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preset_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preset_cards_content = QWidget()
        self.preset_cards_layout = QVBoxLayout(self.preset_cards_content)
        self.preset_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.preset_cards_layout.setSpacing(8)
        self.preset_cards_area.setWidget(self.preset_cards_content)
        sidebar_layout.addWidget(self.preset_cards_area, 1)

        split.addWidget(sidebar_widget)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 320])
        self._reset_form()

    def reset_layout_after_shell_resize(self) -> None:
        splitter = getattr(self, "main_splitter", None)
        if splitter is None:
            return
        total = max(1, int(splitter.width()))
        right = min(420, max(300, int(total * 0.24)))
        left = max(520, total - right)
        splitter.setSizes([left, right])

    def refresh_data(self) -> None:
        presets = self.preset_service.list_presets()
        ids = {preset.id for preset in presets}
        if self.current_preset_id not in ids:
            self.current_preset_id = None

        search = self.preset_search_edit.text().strip().lower() if hasattr(self, "preset_search_edit") else ""
        if search:
            filtered = []
            for preset in presets:
                name_match = search in str(preset.name).lower()
                date_match = search in preset.updated_at.strftime("%Y-%m-%d").lower()
                project_match = search in self._linked_projects_summary(preset).lower()
                if name_match or date_match or project_match:
                    filtered.append(preset)
        else:
            filtered = presets

        auto_select_first = False
        filtered_ids = {preset.id for preset in filtered}
        if self.current_preset_id not in filtered_ids:
            self.current_preset_id = None
        if self.current_preset_id is None and filtered:
            self.current_preset_id = filtered[0].id
            auto_select_first = True

        self._render_preset_cards(filtered)
        if auto_select_first and self.current_preset_id is not None:
            self._load_preset_into_form(self.current_preset_id)

    def _on_preset_search_changed(self, _text: str) -> None:
        self.refresh_data()

    def _clear_preset_cards(self) -> None:
        while self.preset_cards_layout.count():
            item = self.preset_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_preset_cards(self, presets: list) -> None:
        self._clear_preset_cards()
        if not presets:
            empty = QLabel("Aucun preset.")
            empty.setObjectName("CardMuted")
            self.preset_cards_layout.addWidget(empty)
            self.preset_cards_layout.addStretch(1)
            return

        for preset in presets:
            is_selected = self.current_preset_id is not None and int(preset.id) == int(self.current_preset_id)
            card = self._build_preset_card(preset, is_selected=is_selected)
            self.preset_cards_layout.addWidget(card)
        self.preset_cards_layout.addStretch(1)

    def _build_preset_card(self, preset, is_selected: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("DataCard")
        card.setProperty("selected", "true" if is_selected else "false")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        select_btn = NativePushButton(preset.name)
        select_btn.setProperty("cardSelect", "true")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.setMinimumHeight(24)
        select_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        select_btn.clicked.connect(
            lambda _checked=False, preset_id=preset.id: self._on_preset_card_selected(preset_id)
        )
        date_label = QLabel(preset.updated_at.strftime("%Y-%m-%d"))
        date_label.setObjectName("CardBadge")
        date_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header.addWidget(select_btn, 1)
        header.addWidget(date_label)
        card_layout.addLayout(header)

        # Config Summary Tags
        try:
            config = json.loads(preset.config_json)
        except Exception:
            config = {}
        
        tags_row = QHBoxLayout()
        tags_row.setSpacing(4)
        
        profiles = config.get("export_profiles", {})
        for p in ("web", "print", "social"):
            if p in profiles:
                color = {"web": "#10B981", "print": "#3B82F6", "social": "#F59E0B"}.get(p, "#6B7280")
                tags_row.addWidget(_new_tag_label(p.upper(), color))
        
        if config.get("delivery", {}).get("create_zip"):
            tags_row.addWidget(_new_tag_label("ZIP", "#EC4899"))
        
        if config.get("watermark", {}).get("enabled"):
            tags_row.addWidget(_new_tag_label("WM", "#8B5CF6")) # Violet
            
        tags_row.addStretch()
        card_layout.addLayout(tags_row)

        project_label = QLabel(self._linked_projects_summary(preset))
        project_label.setObjectName("CardMuted")
        project_label.setWordWrap(True)
        project_label.setToolTip(self._linked_projects_tooltip(preset))
        card_layout.addWidget(project_label)
        return card

    def _on_preset_card_selected(self, preset_id: int) -> None:
        self.current_preset_id = int(preset_id)
        self._load_preset_into_form(self.current_preset_id)
        self.refresh_data()

    def _load_preset_into_form(self, preset_id: int) -> None:
        preset = self.preset_service.get_preset(int(preset_id))
        if preset is None:
            return
        self.current_preset_id = preset.id
        self.name_edit.setText(preset.name)
        self.associated_projects_label.setText(self._linked_projects_tooltip(preset))
        self._set_config_from_json_text(preset.config_json)
        self._refresh_versions()

    @staticmethod
    def _card_value(value: str) -> QLabel:
        text = str(value)
        if text.lower().startswith("projets associes: "):
            text = text[len("projets associes: ") :]
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("CardValue")
        return label

    def _set_expert_mode(self, enabled: bool) -> None:
        if bool(enabled):
            self._sync_json_from_form()
            self.config_tabs.setCurrentIndex(1)
            self.sync_controls.setVisible(True)
            self.config_edit.setFocus()
        else:
            self.config_tabs.setCurrentIndex(0)
            self.sync_controls.setVisible(False)

    def _reset_form(self) -> None:
        self.current_preset_id = None
        self.name_edit.clear()
        self.associated_projects_label.setText("-")
        self._apply_config_to_form(default_preset_config())
        self._sync_json_from_form()
        self.expert_mode_check.setChecked(False)
        self._set_expert_mode(False)
        self.version_combo.clear()

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if self.config_tabs.currentIndex() == 0:
            config = self._build_config_from_form()
            self._sync_json_from_form()
        else:
            config_text = self.config_edit.toPlainText().strip()
            if not config_text:
                QMessageBox.warning(self, "Validation", "La config JSON est obligatoire.")
                return
            try:
                config = self.preset_service.parse_config(config_text)
            except Exception as exc:
                QMessageBox.critical(self, "Erreur preset", str(exc))
                return
            self._apply_config_to_form(config)

        if not name:
            QMessageBox.warning(self, "Validation", "Le nom du preset est obligatoire.")
            return
        try:
            if self.current_preset_id is None:
                preset = self.preset_service.create_preset(
                    name=name,
                    config=config,
                )
                self.current_preset_id = preset.id
            else:
                self.preset_service.update_preset(
                    preset_id=self.current_preset_id,
                    name=name,
                    config=config,
                )
            self.on_data_changed()
            self._refresh_versions()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur preset", str(exc))

    def _delete(self) -> None:
        if self.current_preset_id is None:
            QMessageBox.warning(self, "Selection", "Selectionne un preset a supprimer.")
            return
        choice = QMessageBox.question(
            self,
            "Suppression preset",
            "Confirmer la suppression du preset selectionne ?",
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self.preset_service.delete_preset(self.current_preset_id)
            self.on_data_changed()
            self._reset_form()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur suppression", str(exc))

    def _refresh_versions(self) -> None:
        self.version_combo.clear()
        if self.current_preset_id is None:
            return
        versions = self.preset_service.list_versions(self.current_preset_id)
        for version in versions:
            label = f"v{version.version} - {version.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
            self.version_combo.addItem(label, userData=version.id)

    def _rollback(self) -> None:
        if self.current_preset_id is None:
            QMessageBox.warning(self, "Selection", "Selectionne un preset avant rollback.")
            return
        version_id = self.version_combo.currentData()
        if version_id is None:
            QMessageBox.warning(self, "Selection", "Aucune version selectionnee.")
            return
        choice = QMessageBox.question(
            self,
            "Rollback preset",
            "Confirmer le rollback vers la version selectionnee ?",
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            preset = self.preset_service.rollback_to_version(self.current_preset_id, int(version_id))
            self._set_config_from_json_text(preset.config_json)
            self.on_data_changed()
            self._refresh_versions()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur rollback", str(exc))

    def _build_form_config_tab(self) -> QWidget:
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("BentoScroll")
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(20)

        # ── 1. Top Settings Area (2 Columns) ──
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(20)

        # Left Column: Processing & Storage
        processing_box = BentoCard("Structure & Importation")
        proc_layout = QVBoxLayout()
        proc_layout.setSpacing(16)

        # Naming Group
        naming_group = QVBoxLayout()
        naming_lbl = QLabel("PATTERN DE NOMMAGE")
        naming_lbl.setObjectName("PresetFieldLabel")
        self.naming_pattern_edit = QLineEdit()
        self.naming_pattern_edit.setPlaceholderText("{project}_{date}_{seq:04d}")
        self.naming_pattern_edit.setToolTip(
            "Tags disponibles :\n"
            "  {project}  — Nom du projet\n"
            "  {date}       — Date de shooting (YYYYMMDD)\n"
            "  {seq:04d}  — Numéro séquentiel (ex: 0001)\n"
            "  {orig}       — Nom original du fichier"
        )
        naming_group.addWidget(naming_lbl)
        naming_group.addWidget(self.naming_pattern_edit)
        naming_hint = QLabel("Tags : {project}  {date}  {seq:04d}  {orig}")
        naming_hint.setStyleSheet("color: #777; font-size: 9px; font-family: Consolas, monospace; background: transparent;")
        naming_group.addWidget(naming_hint)
        proc_layout.addLayout(naming_group)

        # Import/Backup Group
        import_group = QVBoxLayout()
        import_group.setSpacing(10)
        import_lbl = QLabel("RÈGLES D'IMPORTATION")
        import_lbl.setObjectName("PresetFieldLabel")
        import_group.addWidget(import_lbl)
        
        self.import_verify_checksum_check = QCheckBox("Vérifier l'intégrité (Checksum)")
        self.import_dual_backup_check = QCheckBox("Activer la double sauvegarde")
        self.import_dual_backup_check.toggled.connect(self._toggle_backup_path)
        
        backup_container = QWidget()
        backup_row = QHBoxLayout(backup_container)
        backup_row.setContentsMargins(0, 0, 0, 0)
        self.import_backup_path_edit = QLineEdit()
        self.import_backup_path_edit.setPlaceholderText("Dossier de backup...")
        backup_btn = _new_button("Parcourir")
        backup_btn.clicked.connect(self._pick_backup_path)
        backup_row.addWidget(self.import_backup_path_edit)
        backup_row.addWidget(backup_btn)
        self.import_backup_browse_btn = backup_btn
        
        import_group.addWidget(self.import_verify_checksum_check)
        import_group.addWidget(self.import_dual_backup_check)
        import_group.addWidget(backup_container)
        proc_layout.addLayout(import_group)

        processing_box.content_layout.addLayout(proc_layout)
        settings_layout.addWidget(processing_box, 1)

        # Right Column: Output Assets
        output_box = BentoCard("Watermark & Livraison")
        out_layout = QVBoxLayout()
        out_layout.setSpacing(16)

        # Watermark Section
        wm_group = QVBoxLayout()
        wm_lbl = QLabel("MARQUAGE VISUEL")
        wm_lbl.setObjectName("PresetFieldLabel")
        wm_group.addWidget(wm_lbl)
        
        wm_ctrl = QHBoxLayout()
        self.watermark_enabled_check = QCheckBox("Appliquer le watermark")
        self.watermark_enabled_check.toggled.connect(self._on_watermark_enabled_toggled)
        wm_btn = _new_button("Ouvrir l'Éditeur")
        wm_btn.clicked.connect(self._open_watermark_editor)
        wm_ctrl.addWidget(self.watermark_enabled_check)
        wm_ctrl.addStretch(1)
        wm_ctrl.addWidget(wm_btn)
        
        self.watermark_tags_container = QWidget()
        self.watermark_tags_layout = QHBoxLayout(self.watermark_tags_container)
        self.watermark_tags_layout.setContentsMargins(0, 0, 0, 0)
        self.watermark_tags_layout.setSpacing(6)
        
        wm_group.addLayout(wm_ctrl)
        wm_group.addWidget(self.watermark_tags_container)
        out_layout.addLayout(wm_group)

        # Delivery Section
        deliv_group = QVBoxLayout()
        deliv_lbl = QLabel("FORMATS DE LIVRAISON")
        deliv_lbl.setObjectName("PresetFieldLabel")
        deliv_group.addWidget(deliv_lbl)
        
        deliv_checks = QHBoxLayout()
        deliv_checks.setSpacing(20)
        self.delivery_zip_check = QCheckBox("Archive ZIP")
        self.delivery_report_check = QCheckBox("Rapport TXT")
        self.delivery_contact_sheet_check = QCheckBox("Planche PDF")
        deliv_checks.addWidget(self.delivery_zip_check)
        deliv_checks.addWidget(self.delivery_report_check)
        deliv_checks.addWidget(self.delivery_contact_sheet_check)
        deliv_checks.addStretch(1)
        
        deliv_group.addLayout(deliv_checks)
        out_layout.addLayout(deliv_group)
        out_layout.addStretch(1)

        output_box.content_layout.addLayout(out_layout)
        settings_layout.addWidget(output_box, 1)

        layout.addLayout(settings_layout)

        # ── 2. Export Profiles Accordion ──
        export_section = QWidget()
        export_v = QVBoxLayout(export_section)
        export_v.setContentsMargins(0, 10, 0, 0)
        
        export_section_label = QLabel("PROFILS D'EXPORT AUTOMATISÉS")
        export_section_label.setObjectName("SectionTitle")
        export_v.addWidget(export_section_label)

        self.accordion_layout = QVBoxLayout()
        self.accordion_layout.setSpacing(8)
        
        for profile in ("web", "print", "social"):
            item = self._build_profile_accordion(profile)
            self.accordion_layout.addWidget(item)
        
        export_v.addLayout(self.accordion_layout)
        layout.addWidget(export_section)
        layout.addStretch(1)

        scroll.setWidget(container)
        outer_layout.addWidget(scroll)
        self._refresh_watermark_summary()
        return tab

    def _build_profile_accordion(self, profile: str) -> QWidget:
        profile_specs = {
            "web": ("WEB", "Diffusion web optimale", "#10B981"), # Emerald
            "print": ("PRINT", "Haute résolution / Archivage", "#3B82F6"), # Blue
            "social": ("SOCIAL", "Formats réseaux sociaux", "#F59E0B"), # Amber
        }
        title_text, subtitle_text, color = profile_specs.get(profile, (profile.upper(), "", "#6B7280"))

        container = QWidget()
        v_layout = QVBoxLayout(container)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setObjectName("PresetAccordionHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(12)

        color_dot = QFrame()
        color_dot.setFixedSize(10, 10)
        color_dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        
        info = QVBoxLayout()
        title_lbl = QLabel(title_text)
        title_lbl.setObjectName("PresetAccordionTitle")
        subtitle_lbl = QLabel(subtitle_text)
        subtitle_lbl.setObjectName("PresetAccordionSubtitle")
        info.addWidget(title_lbl)
        info.addWidget(subtitle_lbl)
        
        toggle_btn = QToolButton()
        toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        toggle_btn.setStyleSheet("border: none; background: transparent;")
        
        h_layout.addWidget(color_dot)
        h_layout.addLayout(info, 1)
        h_layout.addWidget(toggle_btn)

        v_layout.addWidget(header)

        # Body
        body = QFrame()
        body.setObjectName("PresetAccordionBody")
        body.setVisible(False)
        b_layout = QGridLayout(body)
        b_layout.setContentsMargins(16, 12, 16, 12)
        b_layout.setHorizontalSpacing(24)
        b_layout.setVerticalSpacing(12)

        format_combo = QComboBox()
        format_combo.addItems(["JPEG", "PNG", "TIFF"])
        
        width_spin = QSpinBox()
        width_spin.setRange(320, 12000)
        width_spin.setSingleStep(160)
        
        quality_slider = QSlider(Qt.Orientation.Horizontal)
        quality_slider.setRange(1, 100)
        quality_value = QLabel("85")
        quality_value.setFixedWidth(30)
        quality_slider.valueChanged.connect(lambda v, l=quality_value: l.setText(str(v)))
        q_row = QHBoxLayout()
        q_row.addWidget(quality_slider, 1)
        q_row.addWidget(quality_value)
        
        subdir_edit = QLineEdit()
        subdir_edit.setPlaceholderText(profile)

        # Build grid
        b_layout.addWidget(QLabel("Format de sortie"), 0, 0)
        b_layout.addWidget(format_combo, 0, 1)
        b_layout.addWidget(QLabel("Largeur max (px)"), 1, 0)
        b_layout.addWidget(width_spin, 1, 1)
        b_layout.addWidget(QLabel("Qualité compression"), 0, 2)
        b_layout.addLayout(q_row, 0, 3)
        b_layout.addWidget(QLabel("Sous-dossier"), 1, 2)
        b_layout.addWidget(subdir_edit, 1, 3)

        v_layout.addWidget(body)

        def _on_toggle(event=None):
            opened = not body.isVisible()
            body.setVisible(opened)
            toggle_btn.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            header.setProperty("opened", "true" if opened else "false")
            header.style().unpolish(header)
            header.style().polish(header)

        header.mousePressEvent = _on_toggle
        
        self.profile_widgets[profile] = {
            "format": format_combo,
            "max_width": width_spin,
            "quality": quality_slider,
            "subdir": subdir_edit,
        }
        quality_slider.setValue(85)
        return container

    def _toggle_backup_path(self, enabled: bool) -> None:
        self.import_backup_path_edit.setEnabled(enabled)
        self.import_backup_browse_btn.setEnabled(enabled)

    def _on_watermark_enabled_toggled(self, checked: bool) -> None:
        self.watermark_cfg["enabled"] = bool(checked)
        self._refresh_watermark_summary()

    def _open_watermark_editor(self) -> None:
        current = normalize_watermark_config(self.watermark_cfg)
        current["enabled"] = bool(self.watermark_enabled_check.isChecked())
        dialog = WatermarkEditorDialog(
            config=current,
            app_data_dir=resolve_app_paths().data_dir,
            accent_color=getattr(self.window(), "accent_color", "#10B981"),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.watermark_cfg = normalize_watermark_config(dialog.get_config())
        self.watermark_enabled_check.setChecked(bool(self.watermark_cfg.get("enabled", False)))
        self._refresh_watermark_summary()

    def _refresh_watermark_summary(self) -> None:
        self.watermark_cfg = normalize_watermark_config(self.watermark_cfg)
        self.watermark_cfg["enabled"] = bool(self.watermark_enabled_check.isChecked())
        
        # Clear tags
        while self.watermark_tags_layout.count():
            item = self.watermark_tags_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        if not self.watermark_cfg["enabled"]:
            self.watermark_tags_layout.addWidget(_new_tag_label("Désactivé", "#6B7280"))
        else:
            text_on = bool(self.watermark_cfg.get("text", {}).get("enabled"))
            logo_on = bool(self.watermark_cfg.get("logo", {}).get("enabled"))
            
            if text_on: self.watermark_tags_layout.addWidget(_new_tag_label("TEXTE", "#3B82F6"))
            if logo_on: self.watermark_tags_layout.addWidget(_new_tag_label("LOGO", "#10B981"))
            if not text_on and not logo_on:
                self.watermark_tags_layout.addWidget(_new_tag_label("Activé (vide)", "#F59E0B"))
        
        self.watermark_tags_layout.addStretch()

    def _pick_backup_path(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier backup")
        if directory:
            self.import_backup_path_edit.setText(directory)

    def _set_config_from_json_text(self, config_text: str) -> None:
        self.config_edit.setPlainText(config_text)
        try:
            payload = self.preset_service.parse_config(config_text)
        except Exception:
            payload = default_preset_config()
        self._apply_config_to_form(payload)

    def _sync_json_from_form(self) -> None:
        config = self._build_config_from_form()
        self.config_edit.setPlainText(json.dumps(config, ensure_ascii=True, indent=2))

    def _sync_form_from_json(self) -> None:
        try:
            config = self.preset_service.parse_config(self.config_edit.toPlainText().strip())
        except Exception as exc:
            QMessageBox.critical(self, "Erreur JSON", str(exc))
            return
        self._apply_config_to_form(config)

    def _apply_config_to_form(self, config: dict) -> None:
        merged = self._deep_merge(default_preset_config(), config)

        naming = merged.get("naming", {})
        self.naming_pattern_edit.setText(str(naming.get("pattern", "")))

        import_cfg = merged.get("import", {})
        verify_checksum = bool(import_cfg.get("verify_checksum", True))
        dual_backup = bool(import_cfg.get("dual_backup", False))
        backup_path = str(import_cfg.get("backup_path", ""))
        self.import_verify_checksum_check.setChecked(verify_checksum)
        self.import_dual_backup_check.setChecked(dual_backup)
        self.import_backup_path_edit.setText(backup_path)
        self._toggle_backup_path(dual_backup)

        export_profiles = merged.get("export_profiles", {})
        for profile, widgets in self.profile_widgets.items():
            cfg = export_profiles.get(profile, {})
            format_combo = widgets["format"]
            width_spin = widgets["max_width"]
            quality_spin = widgets["quality"]
            subdir_edit = widgets["subdir"]

            fmt = str(cfg.get("format", "JPEG")).upper()
            idx = format_combo.findText(fmt)
            format_combo.setCurrentIndex(max(0, idx))
            width_spin.setValue(int(cfg.get("max_width", width_spin.value())))
            quality_spin.setValue(int(cfg.get("quality", quality_spin.value())))
            subdir_edit.setText(str(cfg.get("subdir", profile)))

        self.watermark_cfg = normalize_watermark_config(merged.get("watermark", {}))
        self.watermark_enabled_check.setChecked(bool(self.watermark_cfg.get("enabled", False)))
        self._refresh_watermark_summary()

        delivery = merged.get("delivery", {})
        self.delivery_zip_check.setChecked(bool(delivery.get("create_zip", True)))
        self.delivery_report_check.setChecked(bool(delivery.get("create_report", True)))
        self.delivery_contact_sheet_check.setChecked(
            bool(delivery.get("create_contact_sheet_pdf", True))
        )

    def _build_config_from_form(self) -> dict:
        export_profiles = {}
        for profile, widgets in self.profile_widgets.items():
            export_profiles[profile] = {
                "format": str(widgets["format"].currentText()),
                "max_width": int(widgets["max_width"].value()),
                "quality": int(widgets["quality"].value()),
                "subdir": str(widgets["subdir"].text().strip() or profile),
            }

        return {
            "naming": {
                "pattern": self.naming_pattern_edit.text().strip() or "{project}_{date}_{seq:04d}",
            },
            "import": {
                "verify_checksum": self.import_verify_checksum_check.isChecked(),
                "dual_backup": self.import_dual_backup_check.isChecked(),
                "backup_path": self.import_backup_path_edit.text().strip(),
            },
            "export_profiles": export_profiles,
            "watermark": normalize_watermark_config(
                {
                    **self.watermark_cfg,
                    "enabled": self.watermark_enabled_check.isChecked(),
                }
            ),
            "delivery": {
                "create_zip": self.delivery_zip_check.isChecked(),
                "create_report": self.delivery_report_check.isChecked(),
                "create_contact_sheet_pdf": self.delivery_contact_sheet_check.isChecked(),
            },
        }

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        merged = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = PresetTab._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _linked_projects_summary(preset) -> str:
        names = sorted([item.name for item in (preset.projects or [])])
        if not names:
            return "Aucun projet"
        if len(names) <= 2:
            return ", ".join(names)
        return f"{len(names)} projets: {', '.join(names[:2])}..."

    @staticmethod
    def _linked_projects_tooltip(preset) -> str:
        names = sorted([item.name for item in (preset.projects or [])])
        if not names:
            return "Projets associes: aucun"
        return "Projets associes: " + ", ".join(names)

class SettingsTab(QWidget):
    def __init__(
        self,
        storage_service: StorageService,
        is_busy: Callable[[], bool],
        on_migration_completed,
        on_theme_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self.storage_service = storage_service
        self.is_busy = is_busy
        self.on_migration_completed = on_migration_completed
        self.on_theme_changed = on_theme_changed

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("BentoScroll")
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(24)

        # ── 1. Storage Section ──
        storage_card = BentoCard("Stockage Bibiliothèque")
        st_layout = QVBoxLayout()
        st_layout.setSpacing(16)
        
        st_info = QLabel("DÉFINITION DU RÉPERTOIRE GLOBAL")
        st_info.setObjectName("PresetFieldLabel")
        st_layout.addWidget(st_info)
        
        row = QHBoxLayout()
        self.storage_root_edit = QLineEdit()
        self.storage_root_edit.setPlaceholderText("ex: D:/Photos/PhotoHub_Library")
        browse_btn = _new_button("Parcourir")
        browse_btn.clicked.connect(self._pick_storage_root)
        row.addWidget(self.storage_root_edit)
        row.addWidget(browse_btn)
        st_layout.addLayout(row)

        self.apply_btn = _new_button("Appliquer la migration", primary=True)
        self.apply_btn.clicked.connect(self._apply_storage_root)
        
        status_box = QHBoxLayout()
        status_box.setSpacing(12)
        self.status_label = QLabel("IDLE")
        self.status_label.setObjectName("StatusBadge")
        self.active_data_dir_label = QLabel("-")
        self.active_data_dir_label.setObjectName("CardMuted")
        status_box.addWidget(QLabel("Statut:"))
        status_box.addWidget(self.status_label)
        status_box.addStretch(1)
        status_box.addWidget(self.active_data_dir_label)
        
        self.error_label = QLabel("-")
        self.error_label.setObjectName("CardMuted")
        self.error_label.setStyleSheet("color: #EF4444; background: transparent;") # Red error hint
        self.error_label.setVisible(False)

        st_layout.addWidget(self.apply_btn)
        st_layout.addLayout(status_box)
        st_layout.addWidget(self.error_label)
        
        storage_card.content_layout.addLayout(st_layout)
        layout.addWidget(storage_card)

        # ── 2. Middle Row: Theme & Profile ──
        mid_row = QHBoxLayout()
        mid_row.setSpacing(24)

        # Theme
        theme_card = BentoCard("Identité Visuelle")
        theme_v = QVBoxLayout()
        theme_v.setSpacing(16)
        
        th_lbl = QLabel("COULEUR D'ACCENTUATION")
        th_lbl.setObjectName("PresetFieldLabel")
        theme_v.addWidget(th_lbl)
        
        accent_row = QHBoxLayout()
        self.accent_color_edit = QLineEdit()
        self.accent_color_edit.setPlaceholderText("#10B981")
        accent_pick_btn = _new_button("Choisir")
        accent_pick_btn.clicked.connect(self._pick_accent_color)
        accent_row.addWidget(self.accent_color_edit, 1)
        accent_row.addWidget(accent_pick_btn)
        theme_v.addLayout(accent_row)
        
        banner_lbl = QLabel("BANNIÈRE TABLEAU DE BORD")
        banner_lbl.setObjectName("PresetFieldLabel")
        theme_v.addWidget(banner_lbl)
        
        banner_row = QHBoxLayout()
        self.banner_path_edit = QLineEdit()
        self.banner_path_edit.setPlaceholderText("Image panoramique (1200x200 conseillé)")
        banner_pick_btn = _new_button("Parcourir")
        banner_pick_btn.clicked.connect(self._pick_banner)
        banner_row.addWidget(self.banner_path_edit, 1)
        banner_row.addWidget(banner_pick_btn)
        theme_v.addLayout(banner_row)

        self.apply_theme_btn = _new_button("Mettre à jour le thème", primary=True)
        self.apply_theme_btn.clicked.connect(self._apply_accent_color)
        theme_v.addWidget(self.apply_theme_btn)
        
        theme_card.content_layout.addLayout(theme_v)
        mid_row.addWidget(theme_card, 1)

        # Studio Profile
        studio_card = BentoCard("Profil Studio")
        studio_v = QVBoxLayout()
        studio_v.setSpacing(12)
        
        self.studio_name_edit = QLineEdit(); self.studio_name_edit.setPlaceholderText("Nom du studio")
        self.photographer_name_edit = QLineEdit(); self.photographer_name_edit.setPlaceholderText("Nom du photographe")
        self.copyright_notice_edit = QLineEdit(); self.copyright_notice_edit.setPlaceholderText("Notice de copyright")
        
        studio_v.addWidget(self.studio_name_edit)
        studio_v.addWidget(self.photographer_name_edit)
        studio_v.addWidget(self.copyright_notice_edit)
        
        self.apply_studio_btn = _new_button("Sauvegarder le profil", primary=True)
        self.apply_studio_btn.clicked.connect(self._apply_studio_profile)
        studio_v.addSpacing(4)
        studio_v.addWidget(self.apply_studio_btn)
        
        studio_card.content_layout.addLayout(studio_v)
        mid_row.addWidget(studio_card, 1)
        
        layout.addLayout(mid_row)
        layout.addStretch(1)
        
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def refresh_data(self) -> None:
        settings = self.storage_service.get_settings()
        self.storage_root_edit.setText(settings.get("storage_root", ""))
        self.accent_color_edit.setText(settings.get("accent_color", "#10B981"))
        self.status_label.setText(str(settings.get('last_migration_status', 'IDLE')).upper())
        err = settings.get('last_migration_error')
        self.error_label.setText(f"Erreur: {err}")
        self.error_label.setVisible(bool(err))
        self.active_data_dir_label.setText(f"Bibliothèque active: {settings.get('active_data_dir', '-')}")
        self.banner_path_edit.setText(settings.get("dashboard_banner", ""))
        profile = settings.get("studio_profile", {}) if isinstance(settings, dict) else {}
        self.studio_name_edit.setText(str(profile.get("studio_name", "") or ""))
        self.photographer_name_edit.setText(str(profile.get("photographer_name", "") or ""))
        self.copyright_notice_edit.setText(str(profile.get("copyright_notice", "") or ""))

    def _pick_storage_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier global de stockage")
        if directory:
            self.storage_root_edit.setText(directory)

    def _pick_accent_color(self) -> None:
        current = QColor(normalize_accent_color(self.accent_color_edit.text().strip()))
        chosen = QColorDialog.getColor(current, self, "Choisir couleur d'accent")
        if chosen.isValid():
            self.accent_color_edit.setText(chosen.name().upper())


    def _pick_banner(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choisir une bannière", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if file_path:
            self.banner_path_edit.setText(file_path)

    def _apply_accent_color(self) -> None:
        # Accent Color
        requested = self.accent_color_edit.text().strip()
        normalized = normalize_accent_color(requested)
        
        # Banner Path
        banner_path = self.banner_path_edit.text().strip() or None
        
        try:
            self.storage_service.set_accent_color(normalized)
            self.storage_service.set_dashboard_banner(banner_path)
            
            self.accent_color_edit.setText(normalized)
            self.on_theme_changed() # This usually refreshes UI
            QMessageBox.information(self, "Identité", "Identité visuelle mise à jour.")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))

    def _apply_studio_profile(self) -> None:
        try:
            self.storage_service.set_studio_profile(
                studio_name=self.studio_name_edit.text().strip(),
                photographer_name=self.photographer_name_edit.text().strip(),
                copyright_notice=self.copyright_notice_edit.text().strip(),
            )
            QMessageBox.information(self, "Profil studio", "Profil studio mis a jour.")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur profil studio", str(exc))

    def _apply_storage_root(self) -> None:
        new_root = self.storage_root_edit.text().strip()
        if not new_root:
            QMessageBox.warning(self, "Validation", "Le dossier global est obligatoire.")
            return
        if self.is_busy():
            QMessageBox.warning(self, "Operation en cours", "Attends la fin des imports/exports avant migration.")
            return

        target_data_dir = compute_app_data_dir_from_root(new_root)
        text = (
            "La migration va copier la base et les projets vers:\n"
            f"{target_data_dir}\n\n"
            "L'ancien dossier sera conserve. Continuer ?"
        )
        choice = QMessageBox.question(self, "Confirmer migration", text)
        if choice != QMessageBox.StandardButton.Yes:
            return

        self.apply_btn.setEnabled(False)
        try:
            result = self.storage_service.set_global_storage_root(new_root)
            QMessageBox.information(self, "Migration terminee", result.message)
            self.on_migration_completed()
            self.refresh_data()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur migration", str(exc))
            self.refresh_data()
        finally:
            self.apply_btn.setEnabled(True)
