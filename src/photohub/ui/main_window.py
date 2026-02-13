from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
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

from ..config import compute_app_data_dir_from_root, normalize_accent_color
from ..preset_defaults import default_preset_config
from ..services import (
    CullingService,
    EditService,
    ExportService,
    ImportService,
    PresetService,
    ProjectService,
    StorageService,
)
from ..services.edits import DEFAULT_EDIT_SETTINGS

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
        color = QColor("#39FF14")
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {max(0, min(255, int(alpha)))})"


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
    if primary and QFLUENT_AVAILABLE and FluentPrimaryPushButton is not None:
        button = FluentPrimaryPushButton(text)
    elif QFLUENT_AVAILABLE and FluentPushButton is not None:
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


class DashboardTab(QWidget):
    def __init__(self, project_service: ProjectService, get_active_jobs: Callable[[], int]) -> None:
        super().__init__()
        self.project_service = project_service
        self.get_active_jobs = get_active_jobs

        layout = QVBoxLayout(self)
        title = QLabel("Dashboard Studio")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        cards_row = QHBoxLayout()
        self.total_projects_label = self._build_card(cards_row, "Projets", "0")
        self.to_import_label = self._build_card(cards_row, "A importer", "0")
        self.ready_label = self._build_card(cards_row, "Prets a livrer", "0")
        self.jobs_label = self._build_card(cards_row, "Jobs actifs", "0")
        layout.addLayout(cards_row)

        recent_box = QGroupBox("Projets recents")
        recent_layout = QVBoxLayout(recent_box)
        self.recent_cards_area = QScrollArea()
        self.recent_cards_area.setWidgetResizable(True)
        self.recent_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.recent_cards_content = QWidget()
        self.recent_cards_layout = QVBoxLayout(self.recent_cards_content)
        self.recent_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.recent_cards_layout.setSpacing(10)
        self.recent_cards_area.setWidget(self.recent_cards_content)
        recent_layout.addWidget(self.recent_cards_area)
        layout.addWidget(recent_box, 1)

    def refresh_data(self) -> None:
        projects = self.project_service.list_projects()
        total = len(projects)
        to_import = len([p for p in projects if p.status == "a_importer"])
        ready = len([p for p in projects if p.status == "pret_a_livrer"])
        active_jobs = int(self.get_active_jobs())

        self.total_projects_label.setText(str(total))
        self.to_import_label.setText(str(to_import))
        self.ready_label.setText(str(ready))
        self.jobs_label.setText(str(active_jobs))

        recent = projects[:10]
        self._clear_recent_cards()
        if not recent:
            empty = QLabel("Aucun projet recent.")
            empty.setObjectName("CardMuted")
            self.recent_cards_layout.addWidget(empty)
        else:
            for project in recent:
                self.recent_cards_layout.addWidget(self._build_recent_project_card(project))
        self.recent_cards_layout.addStretch(1)

    @staticmethod
    def _build_card(parent_layout: QHBoxLayout, title: str, value: str) -> QLabel:
        box = QGroupBox(title)
        box.setObjectName("StatCard")
        card_layout = QVBoxLayout(box)
        value_label = QLabel(value)
        value_label.setObjectName("StatValue")
        card_layout.addWidget(value_label)
        parent_layout.addWidget(box, 1)
        return value_label

    def _clear_recent_cards(self) -> None:
        while self.recent_cards_layout.count():
            item = self.recent_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_recent_project_card(self, project) -> QWidget:
        card = QFrame()
        card.setObjectName("DataCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title = QLabel(project.name)
        title.setObjectName("CardTitle")
        status_label = QLabel(self.project_service.get_status_label(project.status))
        status_label.setObjectName("CardBadge")
        status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        toggle = QToolButton()
        toggle.setProperty("cardToggle", "true")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        toggle.setFixedSize(24, 24)
        header.addWidget(title, 1)
        header.addWidget(status_label)
        header.addWidget(toggle)
        card_layout.addLayout(header)

        details = QWidget()
        details.setObjectName("CardDetails")
        details_layout = QFormLayout(details)
        details_layout.setContentsMargins(0, 10, 0, 0)
        details_layout.setVerticalSpacing(6)
        details_layout.setHorizontalSpacing(10)
        details_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        details_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        details_layout.addRow("Client", self._card_value(project.client.name if project.client else "-"))
        details_layout.addRow("Date", self._card_value(project.shoot_date.strftime("%Y-%m-%d")))
        details_layout.addRow("Dossier", self._card_value(project.root_path))
        details.setVisible(False)
        card_layout.addWidget(details)

        def _on_toggle(expanded: bool, panel=details, btn=toggle):
            panel.setVisible(expanded)
            btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

        toggle.toggled.connect(_on_toggle)
        return card

    @staticmethod
    def _card_value(value: str) -> QLabel:
        label = QLabel(str(value))
        label.setWordWrap(True)
        label.setObjectName("CardValue")
        return label


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
    SIDEBAR_EXPANDED_WIDTH = 220
    SIDEBAR_COLLAPSED_WIDTH = 72

    NAV_ITEMS = [
        ("dashboard", "Dashboard", "HOME"),
        ("projects", "Projets", "ALBUM"),
        ("ingest", "Ingest", "DOWNLOAD"),
        ("culling", "Tri", "CUT"),
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
        "culling": ("Tri rapide", "Raccourcis: <-/-> suivant | P garder | X rejeter | 1..5 noter | F focus"),
        "edit": ("Edit rapide", "Raccourcis: Ctrl+C copier | Ctrl+V coller | Shift+S sync | Y avant/apres"),
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
        import_service: ImportService,
        export_service: ExportService,
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
        self.storage_service = storage_service
        self.on_reload_runtime = on_reload_runtime
        self.active_ops_count = 0
        self.accent_color = normalize_accent_color(self.storage_service.get_settings().get("accent_color"))

        # Load optional Fluent widgets only after QApplication exists.
        _ensure_fluent_loaded()

        self.setWindowTitle("PhotoHub - Studio Workflow")
        self.resize(1400, 860)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        self.current_nav_key = ""
        self.sidebar_expanded = True
        self.nav_item_labels: dict[str, str] = {}
        self.nav_buttons: dict[str, QPushButton] = {}

        self.nav_panel = QWidget()
        self.nav_panel.setObjectName("SideBar")
        self.nav_panel.setFixedWidth(self.SIDEBAR_EXPANDED_WIDTH)
        nav_layout = QVBoxLayout(self.nav_panel)
        nav_layout.setContentsMargins(10, 10, 10, 10)
        nav_layout.setSpacing(6)
        self.sidebar_toggle_btn = self._build_sidebar_toggle_button()
        nav_layout.addWidget(self.sidebar_toggle_btn)

        top_keys = ["dashboard", "projects", "ingest", "culling", "edit", "export", "presets"]
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

        root_layout.addWidget(self.nav_panel)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        topbar = QGroupBox()
        topbar.setObjectName("TopBar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(12, 8, 12, 8)

        app_title = QLabel("PhotoHub")
        app_title.setObjectName("AppTitle")
        self.search_edit = self._build_search_line_edit()
        self.search_edit.setPlaceholderText("Recherche projet (nom, client, statut)")
        self.search_edit.textChanged.connect(self._on_search_text_changed)

        self.project_context_combo = QComboBox()
        self.project_context_combo.currentIndexChanged.connect(self._on_project_context_changed)
        self.activity_badge = QLabel("Aucun job")
        self.activity_badge.setObjectName("ActivityBadge")
        self.context_mode_label = QLabel("Mode Dashboard")
        self.context_mode_label.setObjectName("ContextModeBadge")
        self.context_hint_label = QLabel("")
        self.context_hint_label.setObjectName("ContextHintLabel")
        self.context_hint_label.setWordWrap(False)

        topbar_layout.addWidget(app_title)
        topbar_layout.addSpacing(8)
        topbar_layout.addWidget(self.search_edit, 1)
        topbar_layout.addWidget(QLabel("Projet actif"))
        topbar_layout.addWidget(self.project_context_combo)
        topbar_layout.addSpacing(6)
        topbar_layout.addWidget(self.context_mode_label)
        topbar_layout.addWidget(self.context_hint_label, 1)
        topbar_layout.addWidget(self.activity_badge)
        content_layout.addWidget(topbar)

        self.stack = QStackedWidget()
        self.dashboard_tab = DashboardTab(self.project_service, get_active_jobs=self._get_active_jobs_count)
        self.hub_tab = HubTab(self.project_service, self.preset_service, on_data_changed=self.refresh_all)
        self.import_export_tab = ImportExportTab(
            project_service=self.project_service,
            preset_service=self.preset_service,
            culling_service=self.culling_service,
            edit_service=self.edit_service,
            import_service=self.import_service,
            export_service=self.export_service,
            on_data_changed=self.refresh_all,
            on_operation_started=self._on_operation_started,
            on_operation_ended=self._on_operation_ended,
            on_job_event=self._append_job_event,
        )
        self.import_export_tab.sections.currentChanged.connect(self._on_import_export_section_changed)
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
        self.stack.addWidget(self.import_export_tab)
        self.stack.addWidget(self.presets_tab)
        self.stack.addWidget(self.settings_tab)
        self.stack.addWidget(self.jobs_tab)
        content_layout.addWidget(self.stack, 1)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

        self._apply_theme()
        self._apply_sidebar_state()
        self._switch_page("dashboard")

        self.refresh_all()

    def _get_active_jobs_count(self) -> int:
        return int(self.active_ops_count)

    def is_busy(self) -> bool:
        return self.active_ops_count > 0

    def _on_operation_started(self) -> None:
        self.active_ops_count += 1
        self._update_activity_badge()
        self.jobs_tab.refresh_data()

    def _on_operation_ended(self) -> None:
        self.active_ops_count = max(0, self.active_ops_count - 1)
        self._update_activity_badge()
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
                fluent_set_theme_color(self.accent_color)
            except Exception:
                pass
        self._apply_sprint1_style()

    def _on_theme_settings_changed(self) -> None:
        settings = self.storage_service.get_settings()
        self.accent_color = normalize_accent_color(settings.get("accent_color"))
        self._apply_theme()
        self._apply_sidebar_state()
        self._append_job_event(f"Theme mis a jour (accent {self.accent_color}).")

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
        self.sidebar_expanded = not self.sidebar_expanded
        self._apply_sidebar_state()

    def _apply_sidebar_state(self) -> None:
        width = self.SIDEBAR_EXPANDED_WIDTH if self.sidebar_expanded else self.SIDEBAR_COLLAPSED_WIDTH
        self.nav_panel.setFixedWidth(width)

        if self.sidebar_expanded:
            self.sidebar_toggle_btn.setText("")
            self.sidebar_toggle_btn.setIcon(self._sidebar_toggle_icon(expanded=True))
            self.sidebar_toggle_btn.setToolTip("Replier la sidebar")
            self.sidebar_toggle_btn.setProperty("collapsed", "false")
        else:
            self.sidebar_toggle_btn.setText("")
            self.sidebar_toggle_btn.setIcon(self._sidebar_toggle_icon(expanded=False))
            self.sidebar_toggle_btn.setToolTip("Ouvrir la sidebar")
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

        if normalized == "dashboard":
            self.stack.setCurrentWidget(self.dashboard_tab)
        elif normalized == "projects":
            self.stack.setCurrentWidget(self.hub_tab)
        elif normalized == "ingest":
            self.stack.setCurrentWidget(self.import_export_tab)
            self.import_export_tab.set_current_section("import")
        elif normalized == "culling":
            self.stack.setCurrentWidget(self.import_export_tab)
            self.import_export_tab.set_current_section("culling")
        elif normalized == "edit":
            self.stack.setCurrentWidget(self.import_export_tab)
            self.import_export_tab.set_current_section("edit")
        elif normalized == "export":
            self.stack.setCurrentWidget(self.import_export_tab)
            self.import_export_tab.set_current_section("export")
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
        if self.stack.currentWidget() is not self.import_export_tab:
            return
        section_map = {0: "ingest", 1: "culling", 2: "edit", 3: "export"}
        nav_key = section_map.get(int(index), "ingest")
        self._set_active_nav(nav_key)
        self._update_context_bar(nav_key)

    def _update_context_bar(self, key: str) -> None:
        mode, hint = self.CONTEXT_HINTS.get((key or "").strip().lower(), ("Mode", ""))
        self.context_mode_label.setText(mode)
        self.context_hint_label.setText(hint)

    def _update_activity_badge(self) -> None:
        active = self._get_active_jobs_count()
        if active <= 0:
            self.activity_badge.setText("Aucun job")
            return
        self.activity_badge.setText(f"{active} job(s)")

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
        self.import_export_tab.set_selected_project(int(project_id))
        self.hub_tab.select_project_by_id(int(project_id))

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

        self.dashboard_tab.project_service = self.project_service
        self.hub_tab.project_service = self.project_service
        self.hub_tab.preset_service = self.preset_service
        self.import_export_tab.import_tab.project_service = self.project_service
        self.import_export_tab.import_tab.import_service = self.import_service
        self.import_export_tab.culling_tab.project_service = self.project_service
        self.import_export_tab.culling_tab.culling_service = self.culling_service
        self.import_export_tab.edit_tab.project_service = self.project_service
        self.import_export_tab.edit_tab.edit_service = self.edit_service
        self.import_export_tab.export_tab.project_service = self.project_service
        self.import_export_tab.export_tab.preset_service = self.preset_service
        self.import_export_tab.export_tab.export_service = self.export_service
        self.presets_tab.preset_service = self.preset_service
        self._append_job_event("Migration stockage terminee et runtime recharge.")
        self.refresh_all()

    def refresh_all(self) -> None:
        self.dashboard_tab.refresh_data()
        self.hub_tab.refresh_data()
        self.import_export_tab.refresh_data()
        self.presets_tab.refresh_data()
        self.settings_tab.refresh_data()
        self._refresh_project_context_combo()
        self.jobs_tab.refresh_data()
        self._update_activity_badge()

    def _apply_sprint1_style(self) -> None:
        accent = normalize_accent_color(self.accent_color)
        accent_hover = _lighter(accent, 15)
        accent_pressed = _darker(accent, 20)
        accent_muted = _darker(accent, 45)
        accent_soft = _rgba(accent, 32)
        accent_soft_hover = _rgba(accent, 56)

        self.setStyleSheet(
            """
            QWidget {
                background: #121417;
                color: #E8EDF2;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QLabel {
                background: transparent;
                color: #E8EDF2;
            }
            QWidget#SideBar {
                background: #171B20;
                border: 1px solid #2A3038;
                border-radius: 14px;
                color: #E8EDF2;
                padding: 8px;
            }
            QPushButton#SideBarToggle {
                text-align: center;
                border-radius: 8px;
                border: 1px solid #303843;
                background: #1E242C;
                color: #DCE3EA;
                padding: 6px 0;
                margin: 0 0 6px 0;
            }
            QPushButton#SideBarToggle:hover {
                background: #262D36;
                border-color: %(accent_hover)s;
            }
            QPushButton[navButton="true"] {
                text-align: left;
                border-radius: 8px;
                padding: 10px 12px;
                margin: 2px 0;
                border: 1px solid transparent;
                background: transparent;
                color: #DCE3EA;
            }
            QPushButton[navButton="true"][collapsed="true"] {
                text-align: center;
                padding: 10px 0;
            }
            QPushButton[navButton="true"]:hover {
                background: #222932;
                border-color: #38414E;
            }
            QPushButton[navButton="true"][active="true"] {
                background: %(accent_soft)s;
                border-color: %(accent_hover)s;
                color: #ffffff;
            }
            #TopBar {
                border: 1px solid #2A3038;
                border-radius: 12px;
                background: #171B20;
            }
            #AppTitle {
                font-size: 18px;
                font-weight: 700;
                color: #F5F8FC;
            }
            #ActivityBadge {
                border: 1px solid #39414D;
                border-radius: 10px;
                padding: 4px 10px;
                background: #1B2027;
                color: #D4DAE1;
                font-weight: 600;
            }
            #ContextModeBadge {
                border: 1px solid #39414D;
                border-radius: 10px;
                padding: 3px 10px;
                background: #1B2027;
                color: #EEF3F8;
                font-weight: 700;
            }
            #ContextHintLabel {
                color: #A9B2BC;
                padding-left: 6px;
            }
            #PageTitle {
                font-size: 20px;
                font-weight: 700;
                color: #F5F8FC;
            }
            #StatCard {
                border: 1px solid #2A3038;
                border-radius: 12px;
                background: #1A1F26;
            }
            #StatValue {
                font-size: 26px;
                font-weight: 700;
                color: %(accent_hover)s;
            }
            QFrame#DataCard {
                border: 1px solid #2F3640;
                border-radius: 10px;
                background: #1D2229;
            }
            QFrame#DataCard[selected="true"] {
                border-color: %(accent_hover)s;
                background: %(accent_soft)s;
            }
            QLabel#CardTitle {
                color: #F0F4F8;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#CardValue {
                color: #D6DCE4;
                background: transparent;
            }
            QLabel#CardMuted {
                color: #98A3AF;
                background: transparent;
                padding: 8px 4px;
            }
            QLabel#CardBadge {
                border: 1px solid #3A424D;
                border-radius: 9px;
                background: #222831;
                color: #CDD4DC;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QWidget#CardDetails {
                border-top: 1px solid #313A46;
                background: transparent;
                padding-top: 12px;
            }
            QFrame#PreviewFrame {
                border: 1px solid #313843;
                border-radius: 10px;
                background: #101419;
            }
            QLabel#PreviewLabel {
                background: #101419;
                border-radius: 8px;
                color: #98A3AF;
            }
            QLabel#CullingMeta {
                color: #AEB8C2;
                padding-left: 2px;
            }
            QLabel#CullingHud {
                border-radius: 10px;
                padding: 6px 10px;
                margin: 8px;
                font-size: 11px;
                font-weight: 700;
                color: #09100B;
                background: %(accent_hover)s;
                border: 1px solid %(accent_hover)s;
            }
            QLabel#CullingHud[hudState="warn"] {
                color: #FFF1F1;
                background: #8B2C2C;
                border-color: #A83A3A;
            }
            QLabel#CullingHud[hudState="info"] {
                color: #E8EDF2;
                background: #2A3340;
                border-color: #3F4958;
            }
            QPushButton[cardSelect="true"] {
                text-align: left;
                border: 1px solid transparent;
                border-radius: 6px;
                background: transparent;
                color: #EEF3F8;
                font-weight: 600;
                padding: 6px 8px;
            }
            QPushButton[cardSelect="true"]:hover {
                background: #262D36;
                border-color: #3C4551;
            }
            QToolButton[cardToggle="true"] {
                border: 1px solid #38414D;
                border-radius: 6px;
                background: #222A33;
                color: #DCE3EA;
                padding: 2px;
            }
            QToolButton[cardToggle="true"]:hover {
                border-color: %(accent_hover)s;
                background: #2A3340;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QGroupBox {
                border: 1px solid #34383D;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 8px;
                background: #1D1F22;
            }
            QGroupBox::title {
                left: 10px;
                padding: 0 4px;
                color: #D1D5DA;
                background: transparent;
            }
            QTabWidget::pane {
                border: 1px solid #2A3038;
                border-radius: 10px;
                background: #171B20;
                top: -1px;
            }
            QTabBar::tab {
                background: #1B2129;
                border: 1px solid #2A3038;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                color: #BAC3CC;
                min-width: 90px;
                padding: 6px 12px;
                margin-right: 4px;
            }
            QTabBar::tab:hover {
                color: #F2F6FA;
                border-color: #3A424E;
            }
            QTabBar::tab:selected {
                color: #F5F8FC;
                border-color: %(accent_hover)s;
                background: #1E252E;
            }
            QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget {
                border: 1px solid #313843;
                border-radius: 8px;
                background: #141A21;
                color: #E8EDF2;
                padding: 4px 6px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTableWidget:focus {
                border: 1px solid %(accent_hover)s;
            }
            QTableWidget::item:selected {
                background: %(accent_soft_hover)s;
                color: #ffffff;
            }
            QHeaderView::section {
                background: #1B2129;
                color: #DCE3EA;
                border: none;
                border-bottom: 1px solid #2D3440;
                padding: 6px;
            }
            QProgressBar {
                border: 1px solid #313843;
                border-radius: 7px;
                background: #141A21;
                color: #DCE3EA;
                text-align: center;
            }
            QProgressBar::chunk {
                background: %(accent)s;
                border-radius: 6px;
            }
            QPushButton {
                border: 1px solid #3A424E;
                border-radius: 8px;
                background: #222A33;
                color: #ffffff;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #2A3340;
                border-color: #48515F;
            }
            QPushButton:pressed {
                background: #1B222B;
            }
            QPushButton[isPrimaryButton="true"] {
                background: %(accent)s;
                border-color: %(accent_hover)s;
                color: #0B0F14;
            }
            QPushButton[isPrimaryButton="true"]:hover {
                background: %(accent_hover)s;
                border-color: %(accent_hover)s;
            }
            QPushButton[isPrimaryButton="true"]:pressed {
                background: %(accent_pressed)s;
                border-color: %(accent_pressed)s;
                color: #09100B;
            }
            QPushButton:disabled {
                background: #1C222A;
                border-color: #2A313C;
                color: #7C8795;
            }
            QPushButton[isPrimaryButton="true"]:disabled {
                background: %(accent_muted)s;
                border-color: %(accent_muted)s;
                color: #132116;
            }
            """
            % {
                "accent": accent,
                "accent_hover": accent_hover,
                "accent_pressed": accent_pressed,
                "accent_muted": accent_muted,
                "accent_soft": accent_soft,
                "accent_soft_hover": accent_soft_hover,
            }
        )


class HubTab(QWidget):
    def __init__(self, project_service: ProjectService, preset_service: PresetService, on_data_changed) -> None:
        super().__init__()
        self.project_service = project_service
        self.preset_service = preset_service
        self.on_data_changed = on_data_changed
        self._name_filter = ""

        layout = QVBoxLayout(self)

        creator_box = QGroupBox("Nouveau Projet")
        creator_layout = QGridLayout(creator_box)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Nom du projet")
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Nom client (optionnel)")

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Aucun preset", userData=None)

        self.custom_location_check = QCheckBox("Emplacement personnalise pour ce projet")
        self.custom_location_check.toggled.connect(self._toggle_custom_location)

        custom_row = QHBoxLayout()
        self.custom_location_edit = QLineEdit()
        self.custom_location_edit.setPlaceholderText("Dossier parent du projet")
        self.custom_location_edit.setEnabled(False)
        custom_browse_btn = _new_button("Parcourir")
        custom_browse_btn.setEnabled(False)
        custom_browse_btn.clicked.connect(self._pick_custom_location)
        self.custom_location_browse_btn = custom_browse_btn
        custom_row.addWidget(self.custom_location_edit)
        custom_row.addWidget(custom_browse_btn)

        self.create_btn = _new_button("Creer Projet", primary=True)
        self.create_btn.clicked.connect(self._create_project)

        creator_layout.addWidget(QLabel("Nom"), 0, 0)
        creator_layout.addWidget(self.name_edit, 0, 1)
        creator_layout.addWidget(QLabel("Client"), 0, 2)
        creator_layout.addWidget(self.client_edit, 0, 3)
        creator_layout.addWidget(QLabel("Date shooting"), 1, 0)
        creator_layout.addWidget(self.date_edit, 1, 1)
        creator_layout.addWidget(QLabel("Preset"), 1, 2)
        creator_layout.addWidget(self.preset_combo, 1, 3)
        creator_layout.addWidget(self.create_btn, 2, 3)
        creator_layout.addWidget(self.custom_location_check, 3, 0, 1, 4)
        creator_layout.addLayout(custom_row, 4, 0, 1, 4)

        layout.addWidget(creator_box)

        assign_box = QGroupBox("Affectation Preset")
        assign_layout = QHBoxLayout(assign_box)
        self.assign_combo = QComboBox()
        self.assign_combo.addItem("Aucun preset", userData=None)
        self.assign_btn = _new_button("Affecter au projet selectionne")
        self.assign_btn.clicked.connect(self._assign_selected_project)
        assign_layout.addWidget(QLabel("Preset"))
        assign_layout.addWidget(self.assign_combo)
        assign_layout.addWidget(self.assign_btn)
        layout.addWidget(assign_box)

        status_box = QGroupBox("Statut Projet")
        status_layout = QHBoxLayout(status_box)
        self.status_combo = QComboBox()
        for code, label in self.project_service.list_status_choices():
            self.status_combo.addItem(label, userData=code)
        self.status_btn = _new_button("Mettre a jour le statut")
        self.status_btn.clicked.connect(self._update_selected_project_status)
        status_layout.addWidget(QLabel("Statut"))
        status_layout.addWidget(self.status_combo)
        status_layout.addWidget(self.status_btn)
        layout.addWidget(status_box)

        self.current_project_id: int | None = None
        self.expanded_project_ids: set[int] = set()
        projects_box = QGroupBox("Projets")
        projects_box_layout = QVBoxLayout(projects_box)
        self.project_cards_area = QScrollArea()
        self.project_cards_area.setWidgetResizable(True)
        self.project_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.project_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.project_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.project_cards_content = QWidget()
        self.project_cards_layout = QVBoxLayout(self.project_cards_content)
        self.project_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.project_cards_layout.setSpacing(10)
        self.project_cards_area.setWidget(self.project_cards_content)
        projects_box_layout.addWidget(self.project_cards_area)
        layout.addWidget(projects_box, 1)

    def refresh_data(self) -> None:
        selected_project_id = self._selected_project_id()
        presets = self.preset_service.list_presets()
        self.preset_combo.blockSignals(True)
        self.assign_combo.blockSignals(True)
        self.preset_combo.clear()
        self.assign_combo.clear()
        self.preset_combo.addItem("Aucun preset", userData=None)
        self.assign_combo.addItem("Aucun preset", userData=None)
        for preset in presets:
            self.preset_combo.addItem(preset.name, userData=preset.id)
            self.assign_combo.addItem(preset.name, userData=preset.id)
        self.preset_combo.blockSignals(False)
        self.assign_combo.blockSignals(False)

        projects = self.project_service.list_projects()
        filtered_projects = projects
        if self._name_filter:
            term = self._name_filter.lower()
            filtered_projects = [
                project
                for project in projects
                if term
                in " ".join(
                    [
                        project.name,
                        project.client.name if project.client else "",
                        self.project_service.get_status_label(project.status),
                    ]
                ).lower()
            ]
        visible_ids = {project.id for project in filtered_projects}
        if selected_project_id not in visible_ids:
            selected_project_id = filtered_projects[0].id if filtered_projects else None
            self.current_project_id = selected_project_id

        self._render_project_cards(filtered_projects)
        self._sync_controls_with_selected_project()

    def set_name_filter(self, value: str) -> None:
        self._name_filter = value.strip()
        self.refresh_data()

    def select_project_by_id(self, project_id: int) -> None:
        self.current_project_id = int(project_id)
        self.refresh_data()

    def _selected_project_id(self) -> int | None:
        return self.current_project_id

    def _clear_project_cards(self) -> None:
        while self.project_cards_layout.count():
            item = self.project_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_project_cards(self, projects: list) -> None:
        self._clear_project_cards()
        if not projects:
            empty = QLabel("Aucun projet pour ce filtre.")
            empty.setObjectName("CardMuted")
            self.project_cards_layout.addWidget(empty)
            self.project_cards_layout.addStretch(1)
            return

        for project in projects:
            is_selected = self.current_project_id is not None and int(project.id) == int(self.current_project_id)
            card = self._build_project_card(project, is_selected=is_selected)
            self.project_cards_layout.addWidget(card)
        self.project_cards_layout.addStretch(1)

    def _build_project_card(self, project, is_selected: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("DataCard")
        card.setProperty("selected", "true" if is_selected else "false")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        select_btn = NativePushButton(f"{project.id} - {project.name}")
        select_btn.setProperty("cardSelect", "true")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.setMinimumHeight(32)
        select_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        select_btn.clicked.connect(lambda _checked=False, pid=project.id: self._on_project_card_selected(pid))
        badge = QLabel(self.project_service.get_status_label(project.status))
        badge.setObjectName("CardBadge")
        badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        toggle = QToolButton()
        toggle.setProperty("cardToggle", "true")
        toggle.setCheckable(True)
        expanded = bool(is_selected or (project.id in self.expanded_project_ids))
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
        details_layout.addRow("Client", self._card_value(project.client.name if project.client else "-"))
        details_layout.addRow("Date", self._card_value(project.shoot_date.strftime("%Y-%m-%d")))
        details_layout.addRow("Preset", self._card_value(project.preset.name if project.preset else "-"))
        details_layout.addRow("Dossier", self._card_value(project.root_path))
        details.setVisible(expanded)
        card_layout.addWidget(details)

        def _on_toggle(opened: bool, pid=project.id, panel=details, btn=toggle):
            panel.setVisible(opened)
            btn.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            if opened:
                self.expanded_project_ids.add(pid)
            else:
                self.expanded_project_ids.discard(pid)

        toggle.toggled.connect(_on_toggle)
        return card

    @staticmethod
    def _card_value(value: str) -> QLabel:
        label = QLabel(str(value))
        label.setWordWrap(True)
        label.setObjectName("CardValue")
        return label

    def _on_project_card_selected(self, project_id: int) -> None:
        self.current_project_id = int(project_id)
        self.refresh_data()

    def _toggle_custom_location(self, enabled: bool) -> None:
        self.custom_location_edit.setEnabled(enabled)
        self.custom_location_browse_btn.setEnabled(enabled)

    def _pick_custom_location(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier parent")
        if directory:
            self.custom_location_edit.setText(directory)

    def _create_project(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Le nom du projet est obligatoire.")
            return

        preset_id = self.preset_combo.currentData()
        shoot_date = self.date_edit.date().toPython()
        client_name = self.client_edit.text().strip() or None
        custom_root = None
        if self.custom_location_check.isChecked():
            custom_root = self.custom_location_edit.text().strip()
            if not custom_root:
                QMessageBox.warning(self, "Validation", "Selectionne un dossier personnalise.")
                return

        try:
            self.project_service.create_project(
                name=name,
                shoot_date=shoot_date,
                preset_id=preset_id,
                custom_root_path=custom_root,
                client_name=client_name,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur creation projet", str(exc))
            return

        self.name_edit.clear()
        self.client_edit.clear()
        if self.custom_location_check.isChecked():
            self.custom_location_edit.clear()
        self.on_data_changed()

    def _assign_selected_project(self) -> None:
        project_id = self._selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "Selection", "Selectionne un projet dans la liste.")
            return
        preset_id = self.assign_combo.currentData()
        try:
            self.project_service.assign_preset(project_id, preset_id)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur preset", str(exc))
            return
        self.on_data_changed()

    def _update_selected_project_status(self) -> None:
        project_id = self._selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "Selection", "Selectionne un projet dans la liste.")
            return
        status = self.status_combo.currentData()
        if status is None:
            QMessageBox.warning(self, "Validation", "Selectionne un statut valide.")
            return

        try:
            self.project_service.update_project_status(project_id=project_id, status=str(status))
        except Exception as exc:
            QMessageBox.critical(self, "Erreur statut", str(exc))
            return
        self.on_data_changed()

    def _sync_controls_with_selected_project(self) -> None:
        project_id = self._selected_project_id()
        if project_id is None:
            return
        project = self.project_service.get_project(project_id)
        if project is None:
            return

        status_idx = self.status_combo.findData(project.status)
        if status_idx >= 0:
            self.status_combo.setCurrentIndex(status_idx)

        target_preset_id = project.preset_id
        assign_idx = self.assign_combo.findData(target_preset_id)
        if assign_idx >= 0:
            self.assign_combo.setCurrentIndex(assign_idx)

class ImportExportTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        preset_service: PresetService,
        culling_service: CullingService,
        edit_service: EditService,
        import_service: ImportService,
        export_service: ExportService,
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
            on_operation_started=on_operation_started,
            on_operation_ended=on_operation_ended,
            on_job_event=on_job_event,
        )
        self.export_tab = ExportTab(
            project_service,
            preset_service,
            export_service,
            on_operation_started=on_operation_started,
            on_operation_ended=on_operation_ended,
            on_job_event=on_job_event,
        )

        layout = QVBoxLayout(self)
        self.sections = QTabWidget()
        self.sections.addTab(self.import_tab, "Import")
        self.sections.addTab(self.culling_tab, "Tri")
        self.sections.addTab(self.edit_tab, "Edit")
        self.sections.addTab(self.export_tab, "Export")
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

        controls = QGroupBox("Import securise")
        controls_layout = QFormLayout(controls)

        self.project_combo = QComboBox()

        source_row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Dossier source (carte SD / disque)")
        browse_btn = _new_button("Parcourir")
        browse_btn.clicked.connect(self._pick_source)
        source_row.addWidget(self.source_edit)
        source_row.addWidget(browse_btn)

        self.run_btn = _new_button("Lancer Import", primary=True)
        self.run_btn.clicked.connect(self._run_import)
        self.cancel_btn = _new_button("Annuler")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_import)

        run_row = QHBoxLayout()
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)

        controls_layout.addRow("Projet", self.project_combo)
        controls_layout.addRow("Source", source_row)
        controls_layout.addRow("", run_row)
        controls_layout.addRow("Progression", self.progress_bar)

        layout.addWidget(controls)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def refresh_data(self) -> None:
        current = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self.project_service.list_projects():
            self.project_combo.addItem(f"{project.id} - {project.name}", userData=project.id)
        if current is not None:
            idx = self.project_combo.findData(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)

    def set_selected_project(self, project_id: int) -> None:
        idx = self.project_combo.findData(project_id)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def _pick_source(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier source")
        if directory:
            self.source_edit.setText(directory)

    def _run_import(self) -> None:
        if self._job_thread is not None:
            QMessageBox.warning(self, "Operation en cours", "Un import est deja en cours.")
            return
        project_id = self.project_combo.currentData()
        source = self.source_edit.text().strip()
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return
        if not source:
            QMessageBox.warning(self, "Validation", "Selectionne un dossier source.")
            return

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
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
            self.cancel_btn.setEnabled(False)
            self.on_job_event("[Import] Annulation demandee par l'utilisateur.")

    def _on_import_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(max(0, min(int(done), safe_total)))

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
        self.on_data_changed()

    def _on_import_error(self, message: str) -> None:
        self.on_job_event(f"[Import] Erreur: {message}")
        QMessageBox.critical(self, "Erreur import", message)

    def _on_import_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
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
        self._hud_timer = QTimer(self)
        self._hud_timer.setSingleShot(True)
        self._hud_timer.timeout.connect(self._hide_hud)

        layout = QVBoxLayout(self)

        controls = QGroupBox("Tri / Culling")
        self.controls_box = controls
        controls_layout = QFormLayout(controls)

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._load_assets)

        self.rejected_mode_combo = QComboBox()
        self.rejected_mode_combo.addItem("Tout", userData="all")
        self.rejected_mode_combo.addItem("A garder", userData="kept")
        self.rejected_mode_combo.addItem("Rejetees", userData="rejected")
        self.rejected_mode_combo.currentIndexChanged.connect(self._load_assets)

        self.min_rating_filter_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_filter_combo.addItem(str(rating), userData=rating)
        self.min_rating_filter_combo.currentIndexChanged.connect(self._load_assets)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtre"))
        filter_row.addWidget(self.rejected_mode_combo)
        filter_row.addWidget(QLabel("Note min"))
        filter_row.addWidget(self.min_rating_filter_combo)
        refresh_btn = _new_button("Rafraichir")
        refresh_btn.clicked.connect(self._load_assets)
        filter_row.addWidget(refresh_btn)

        quick_row = QHBoxLayout()
        self.prev_btn = _new_button("Precedent")
        self.prev_btn.clicked.connect(self._select_previous_asset)
        self.next_btn = _new_button("Suivant")
        self.next_btn.clicked.connect(self._select_next_asset)
        self.keep_btn = _new_button("Garder (P)")
        self.keep_btn.clicked.connect(self._mark_selected_keep)
        self.reject_btn = _new_button("Rejeter (X)")
        self.reject_btn.clicked.connect(self._mark_selected_reject)
        self.auto_advance_check = QCheckBox("Auto suivant")
        self.auto_advance_check.setChecked(True)
        self.focus_mode_btn = _new_button("Mode focus (F)")
        self.focus_mode_btn.setCheckable(True)
        self.focus_mode_btn.toggled.connect(self._set_focus_mode)
        quick_row.addWidget(self.prev_btn)
        quick_row.addWidget(self.next_btn)
        quick_row.addWidget(self.keep_btn)
        quick_row.addWidget(self.reject_btn)
        quick_row.addWidget(self.auto_advance_check)
        quick_row.addWidget(self.focus_mode_btn)
        quick_row.addStretch(1)

        controls_layout.addRow("Projet", self.project_combo)
        controls_layout.addRow("", filter_row)
        controls_layout.addRow("", quick_row)
        layout.addWidget(controls)

        body = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter = body

        table_panel = QWidget()
        self.asset_panel = table_panel
        table_layout = QVBoxLayout(table_panel)
        self.selected_asset_id: int | None = None
        self.expanded_asset_ids: set[int] = set()
        self.assets_by_id: dict[int, object] = {}
        self.asset_order: list[int] = []
        self.asset_cards_area = QScrollArea()
        self.asset_cards_area.setWidgetResizable(True)
        self.asset_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.asset_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.asset_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.asset_cards_content = QWidget()
        self.asset_cards_layout = QVBoxLayout(self.asset_cards_content)
        self.asset_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.asset_cards_layout.setSpacing(10)
        self.asset_cards_area.setWidget(self.asset_cards_content)
        table_layout.addWidget(self.asset_cards_area)

        side_panel = QWidget()
        self.side_panel = side_panel
        side_layout = QVBoxLayout(side_panel)
        preview_frame = QFrame()
        preview_frame.setObjectName("PreviewFrame")
        preview_grid = QGridLayout(preview_frame)
        preview_grid.setContentsMargins(8, 8, 8, 8)
        self.preview_label = QLabel("Apercu")
        self.preview_label.setObjectName("PreviewLabel")
        self.preview_label.setMinimumHeight(280)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setScaledContents(False)
        self.hud_label = QLabel("")
        self.hud_label.setObjectName("CullingHud")
        self.hud_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hud_label.setVisible(False)
        preview_grid.addWidget(self.preview_label, 0, 0)
        preview_grid.addWidget(
            self.hud_label,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        side_layout.addWidget(preview_frame)

        self.asset_info_label = QLabel("Selection: -")
        self.asset_sequence_label = QLabel("0 / 0")
        self.asset_sequence_label.setObjectName("CullingMeta")
        self.asset_info_label.setObjectName("CullingMeta")
        side_layout.addWidget(self.asset_info_label)
        side_layout.addWidget(self.asset_sequence_label)

        actions_box = QGroupBox("Actions")
        self.actions_box = actions_box
        actions_layout = QVBoxLayout(actions_box)
        instant_row = QHBoxLayout()
        self.rating_combo = QComboBox()
        for rating in range(0, 6):
            self.rating_combo.addItem(str(rating), userData=rating)
        set_rating_btn = _new_button("Appliquer note")
        set_rating_btn.clicked.connect(self._apply_selected_rating)
        toggle_reject_btn = _new_button("Basculer rejet")
        toggle_reject_btn.clicked.connect(self._toggle_selected_reject)
        instant_row.addWidget(QLabel("Note"))
        instant_row.addWidget(self.rating_combo)
        instant_row.addWidget(set_rating_btn)
        instant_row.addWidget(toggle_reject_btn)
        actions_layout.addLayout(instant_row)

        batch_row = QHBoxLayout()
        self.batch_rating_combo = QComboBox()
        for rating in range(0, 6):
            self.batch_rating_combo.addItem(str(rating), userData=rating)
        batch_rate_btn = _new_button("Batch note filtres")
        batch_rate_btn.clicked.connect(self._start_batch_rating)
        batch_reject_btn = _new_button("Batch rejeter filtres")
        batch_reject_btn.clicked.connect(self._start_batch_reject)
        batch_restore_btn = _new_button("Batch restaurer filtres")
        batch_restore_btn.clicked.connect(self._start_batch_restore)
        batch_row.addWidget(QLabel("Batch note"))
        batch_row.addWidget(self.batch_rating_combo)
        batch_row.addWidget(batch_rate_btn)
        batch_row.addWidget(batch_reject_btn)
        batch_row.addWidget(batch_restore_btn)
        actions_layout.addLayout(batch_row)

        progress_row = QHBoxLayout()
        self.batch_progress = QProgressBar()
        self.batch_progress.setMinimum(0)
        self.batch_progress.setMaximum(100)
        self.batch_progress.setValue(0)
        self.batch_cancel_btn = _new_button("Annuler batch")
        self.batch_cancel_btn.setEnabled(False)
        self.batch_cancel_btn.clicked.connect(self._cancel_batch)
        progress_row.addWidget(self.batch_progress)
        progress_row.addWidget(self.batch_cancel_btn)
        actions_layout.addLayout(progress_row)

        side_layout.addWidget(actions_box)
        side_layout.addStretch(1)

        body.addWidget(table_panel)
        body.addWidget(side_panel)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        layout.addWidget(body)

        self._build_shortcuts()

    def _build_shortcuts(self) -> None:
        for rating in range(0, 6):
            shortcut = QShortcut(QKeySequence(str(rating)), self)
            shortcut.activated.connect(lambda r=rating: self._set_selected_rating(r))
            self._shortcut_refs.append(shortcut)

        reject_shortcut = QShortcut(QKeySequence("R"), self)
        reject_shortcut.activated.connect(self._toggle_selected_reject)
        self._shortcut_refs.append(reject_shortcut)

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

    def refresh_data(self) -> None:
        current = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self.project_service.list_projects():
            self.project_combo.addItem(f"{project.id} - {project.name}", userData=project.id)
        if current is not None:
            idx = self.project_combo.findData(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self._load_assets()

    def set_selected_project(self, project_id: int) -> None:
        idx = self.project_combo.findData(project_id)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def _load_assets(self) -> None:
        project_id = self.project_combo.currentData()
        if project_id is None:
            self._clear_asset_cards()
            self._set_selected_asset(None)
            self.assets_by_id = {}
            self.asset_order = []
            self.preview_label.setText("Apercu")
            self.asset_info_label.setText("Selection: -")
            self.asset_sequence_label.setText("0 / 0")
            return

        rejected_mode = self.rejected_mode_combo.currentData()
        min_rating = int(self.min_rating_filter_combo.currentData() or 0)
        assets = self.culling_service.list_assets(
            project_id=project_id,
            rejected_mode=rejected_mode,
            min_rating=min_rating,
        )

        current_asset_id = self._selected_asset_id()
        self.assets_by_id = {int(asset.id): asset for asset in assets}
        self.asset_order = [int(asset.id) for asset in assets]
        if current_asset_id not in self.assets_by_id:
            current_asset_id = int(assets[0].id) if assets else None
        self.selected_asset_id = int(current_asset_id) if current_asset_id is not None else None

        self._render_asset_cards(assets)
        self._set_selected_asset(self.selected_asset_id)
        if self._selected_asset_id() is None:
            self.preview_label.setText("Aucun asset")
            self.asset_info_label.setText("Selection: -")
            self.asset_sequence_label.setText(f"0 / {len(self.asset_order)}")
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
        badge = QLabel(f"Note {int(asset.rating)}")
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
        asset_id = self._selected_asset_id()
        if asset_id is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Apercu")
            self.asset_info_label.setText("Selection: -")
            self.asset_sequence_label.setText(f"0 / {len(self.asset_order)}")
            return

        asset = self.assets_by_id.get(int(asset_id))
        if asset is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Apercu")
            self.asset_info_label.setText("Selection: -")
            self.asset_sequence_label.setText(f"0 / {len(self.asset_order)}")
            return

        file_path = Path(str(asset.src_path)) if asset.src_path else None

        if file_path is None or not file_path.exists():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Fichier introuvable")
        else:
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText("Apercu indisponible")
            else:
                self.preview_label.setText("")
                scaled = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.preview_label.setPixmap(scaled)

        name = file_path.name if file_path else "-"
        rating = str(getattr(asset, "rating", "-"))
        rejected = "oui" if bool(getattr(asset, "is_rejected", False)) else "non"
        self.asset_info_label.setText(f"Selection: {name} | note={rating} | rejet={rejected}")
        index = self._selected_asset_index()
        display_index = index + 1 if index >= 0 else 0
        self.asset_sequence_label.setText(f"{display_index} / {len(self.asset_order)}")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep preview readable when panel size changes.
        self._on_select_asset()

    def _toggle_focus_mode_shortcut(self) -> None:
        self.focus_mode_btn.setChecked(not self.focus_mode_btn.isChecked())

    def _set_focus_mode(self, enabled: bool) -> None:
        self.focus_mode_enabled = bool(enabled)
        self.asset_panel.setVisible(not self.focus_mode_enabled)
        self.actions_box.setVisible(not self.focus_mode_enabled)
        if self.focus_mode_enabled:
            self.on_job_event("[Tri] Mode focus active.")
        else:
            self.on_job_event("[Tri] Mode focus desactive.")

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
        self._hud_timer.start(850)

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
            self._show_hud(f"RATING {safe_rating}", "ok")
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
        project_id = self.project_combo.currentData()
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return

        rejected_mode = self.rejected_mode_combo.currentData()
        min_rating = int(self.min_rating_filter_combo.currentData() or 0)
        self.batch_progress.setValue(0)
        self.batch_cancel_btn.setEnabled(True)
        self.on_operation_started()
        self.on_job_event(f"[Tri] Lancement batch sur projet ID {project_id}.")

        worker = JobWorker(
            self.culling_service.bulk_update_filtered,
            project_id=project_id,
            rejected_mode=rejected_mode,
            min_rating=min_rating,
            rating=rating,
            is_rejected=is_rejected,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_batch_progress)
        worker.result.connect(self._on_batch_result)
        worker.error.connect(self._on_batch_error)
        worker.finished.connect(self._on_batch_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._job_worker = worker
        self._job_thread = thread
        thread.start()

    def _cancel_batch(self) -> None:
        if self._job_worker is not None:
            self._job_worker.cancel()
            self.batch_cancel_btn.setEnabled(False)
            self.on_job_event("[Tri] Annulation batch demandee par l'utilisateur.")

    def _on_batch_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.batch_progress.setMaximum(safe_total)
        self.batch_progress.setValue(max(0, min(int(done), safe_total)))

    def _on_batch_result(self, result) -> None:
        self._load_assets()
        self.on_data_changed()
        self.on_job_event(f"[Tri] {result.status} | maj={result.updated}/{result.total}")
        QMessageBox.information(
            self,
            "Batch tri",
            f"Statut: {result.status}\nMAJ: {result.updated}/{result.total}",
        )

    def _on_batch_error(self, message: str) -> None:
        self.on_job_event(f"[Tri] Erreur batch: {message}")
        QMessageBox.critical(self, "Erreur batch tri", message)

    def _on_batch_finished(self) -> None:
        self.batch_cancel_btn.setEnabled(False)
        self.on_operation_ended()
        self._job_worker = None
        self._job_thread = None
        self.on_job_event("[Tri] Job batch termine.")


class EditTab(QWidget):
    def __init__(
        self,
        project_service: ProjectService,
        edit_service: EditService,
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.edit_service = edit_service
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)

        self._shortcut_refs: list[QShortcut] = []
        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._save_current_asset_settings)
        self._form_loading = False
        self._before_mode = False
        self._copied_settings: dict[str, object] | None = None

        self.selected_asset_id: int | None = None
        self.assets_by_id: dict[int, object] = {}
        self.asset_order: list[int] = []
        self.asset_card_widgets: dict[int, QFrame] = {}

        layout = QVBoxLayout(self)

        controls = QGroupBox("Edit / Retouche rapide")
        controls_layout = QFormLayout(controls)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._load_assets)

        self.rejected_mode_combo = QComboBox()
        self.rejected_mode_combo.addItem("A garder", userData="kept")
        self.rejected_mode_combo.addItem("Tout", userData="all")
        self.rejected_mode_combo.addItem("Rejetees", userData="rejected")
        self.rejected_mode_combo.currentIndexChanged.connect(self._load_assets)

        self.min_rating_filter_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_filter_combo.addItem(str(rating), userData=rating)
        self.min_rating_filter_combo.currentIndexChanged.connect(self._load_assets)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtre"))
        filter_row.addWidget(self.rejected_mode_combo)
        filter_row.addWidget(QLabel("Note min"))
        filter_row.addWidget(self.min_rating_filter_combo)
        refresh_btn = _new_button("Rafraichir")
        refresh_btn.clicked.connect(self._load_assets)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch(1)
        controls_layout.addRow("Projet", self.project_combo)
        controls_layout.addRow("", filter_row)
        layout.addWidget(controls)

        body = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        self.asset_cards_area = QScrollArea()
        self.asset_cards_area.setWidgetResizable(True)
        self.asset_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.asset_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.asset_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.asset_cards_content = QWidget()
        self.asset_cards_layout = QVBoxLayout(self.asset_cards_content)
        self.asset_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.asset_cards_layout.setSpacing(10)
        self.asset_cards_area.setWidget(self.asset_cards_content)
        left_layout.addWidget(self.asset_cards_area)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        preview_frame = QFrame()
        preview_frame.setObjectName("PreviewFrame")
        preview_grid = QGridLayout(preview_frame)
        preview_grid.setContentsMargins(8, 8, 8, 8)
        self.preview_label = QLabel("Apercu")
        self.preview_label.setObjectName("PreviewLabel")
        self.preview_label.setMinimumHeight(280)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setScaledContents(False)
        self.before_after_badge = QLabel("AVANT")
        self.before_after_badge.setObjectName("CullingHud")
        self.before_after_badge.setProperty("hudState", "info")
        preview_grid.addWidget(self.preview_label, 0, 0)
        preview_grid.addWidget(
            self.before_after_badge,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        right_layout.addWidget(preview_frame)

        self.asset_info_label = QLabel("Selection: -")
        self.asset_info_label.setObjectName("CullingMeta")
        right_layout.addWidget(self.asset_info_label)

        quick_box = QGroupBox("Quick")
        quick_form = QFormLayout(quick_box)
        quick_form.setHorizontalSpacing(10)

        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(-5.0, 5.0)
        self.exposure_spin.setSingleStep(0.1)
        self.exposure_spin.setDecimals(2)

        self.wb_temp_spin = QSpinBox()
        self.wb_temp_spin.setRange(2000, 12000)
        self.wb_temp_spin.setSingleStep(100)

        self.wb_tint_spin = QSpinBox()
        self.wb_tint_spin.setRange(-100, 100)
        self.wb_tint_spin.setSingleStep(1)

        self.crop_ratio_combo = QComboBox()
        self.crop_ratio_combo.addItems(["original", "1:1", "4:5", "3:2", "16:9"])

        self.straighten_spin = QDoubleSpinBox()
        self.straighten_spin.setRange(-45.0, 45.0)
        self.straighten_spin.setSingleStep(0.1)
        self.straighten_spin.setDecimals(2)

        quick_form.addRow("Exposure", self.exposure_spin)
        quick_form.addRow("WB Temp", self.wb_temp_spin)
        quick_form.addRow("WB Tint", self.wb_tint_spin)
        quick_form.addRow("Crop", self.crop_ratio_combo)
        quick_form.addRow("Straighten", self.straighten_spin)
        right_layout.addWidget(quick_box)

        quick_actions = QHBoxLayout()
        self.apply_btn = _new_button("Appliquer", primary=True)
        self.apply_btn.clicked.connect(self._save_current_asset_settings)
        self.copy_btn = _new_button("Copier reglages")
        self.copy_btn.clicked.connect(self._copy_current_settings)
        self.paste_btn = _new_button("Coller reglages")
        self.paste_btn.clicked.connect(self._paste_to_selected)
        self.sync_btn = _new_button("Sync filtres")
        self.sync_btn.clicked.connect(self._start_sync_filtered)
        self.reset_btn = _new_button("Reset")
        self.reset_btn.clicked.connect(self._reset_selected_settings)
        self.before_after_btn = _new_button("Avant/Apres (Y)")
        self.before_after_btn.setCheckable(True)
        self.before_after_btn.toggled.connect(self._toggle_before_after)
        quick_actions.addWidget(self.apply_btn)
        quick_actions.addWidget(self.copy_btn)
        quick_actions.addWidget(self.paste_btn)
        quick_actions.addWidget(self.sync_btn)
        quick_actions.addWidget(self.reset_btn)
        quick_actions.addWidget(self.before_after_btn)
        quick_actions.addStretch(1)
        right_layout.addLayout(quick_actions)

        advanced_box = QGroupBox("Advanced")
        advanced_layout = QVBoxLayout(advanced_box)
        advanced_layout.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setProperty("cardToggle", "true")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setFixedSize(24, 24)
        header_label = QLabel("Ajustements avances")
        header.addWidget(header_label)
        header.addStretch(1)
        header.addWidget(self.advanced_toggle)
        advanced_layout.addLayout(header)

        self.advanced_panel = QWidget()
        advanced_form = QFormLayout(self.advanced_panel)
        self.contrast_spin = QSpinBox()
        self.contrast_spin.setRange(-100, 100)
        self.highlights_spin = QSpinBox()
        self.highlights_spin.setRange(-100, 100)
        self.shadows_spin = QSpinBox()
        self.shadows_spin.setRange(-100, 100)
        self.vibrance_spin = QSpinBox()
        self.vibrance_spin.setRange(-100, 100)
        self.saturation_spin = QSpinBox()
        self.saturation_spin.setRange(-100, 100)
        self.clarity_spin = QSpinBox()
        self.clarity_spin.setRange(-100, 100)

        advanced_form.addRow("Contrast", self.contrast_spin)
        advanced_form.addRow("Highlights", self.highlights_spin)
        advanced_form.addRow("Shadows", self.shadows_spin)
        advanced_form.addRow("Vibrance", self.vibrance_spin)
        advanced_form.addRow("Saturation", self.saturation_spin)
        advanced_form.addRow("Clarity", self.clarity_spin)
        self.advanced_panel.setVisible(False)
        advanced_layout.addWidget(self.advanced_panel)
        self.advanced_toggle.toggled.connect(self._toggle_advanced_panel)
        right_layout.addWidget(advanced_box)

        sync_row = QHBoxLayout()
        self.sync_progress = QProgressBar()
        self.sync_progress.setMinimum(0)
        self.sync_progress.setMaximum(100)
        self.sync_progress.setValue(0)
        self.sync_cancel_btn = _new_button("Annuler sync")
        self.sync_cancel_btn.setEnabled(False)
        self.sync_cancel_btn.clicked.connect(self._cancel_sync)
        sync_row.addWidget(self.sync_progress)
        sync_row.addWidget(self.sync_cancel_btn)
        right_layout.addLayout(sync_row)
        right_layout.addStretch(1)

        body.addWidget(left_panel)
        body.addWidget(right_panel)
        body.setStretchFactor(0, 2)
        body.setStretchFactor(1, 3)
        layout.addWidget(body)

        self._connect_form_signals()
        self._build_shortcuts()
        self._apply_settings_to_form(dict(DEFAULT_EDIT_SETTINGS))
        self._apply_before_after_state()

    def _connect_form_signals(self) -> None:
        self.exposure_spin.valueChanged.connect(self._schedule_autosave)
        self.wb_temp_spin.valueChanged.connect(self._schedule_autosave)
        self.wb_tint_spin.valueChanged.connect(self._schedule_autosave)
        self.crop_ratio_combo.currentIndexChanged.connect(self._schedule_autosave)
        self.straighten_spin.valueChanged.connect(self._schedule_autosave)
        self.contrast_spin.valueChanged.connect(self._schedule_autosave)
        self.highlights_spin.valueChanged.connect(self._schedule_autosave)
        self.shadows_spin.valueChanged.connect(self._schedule_autosave)
        self.vibrance_spin.valueChanged.connect(self._schedule_autosave)
        self.saturation_spin.valueChanged.connect(self._schedule_autosave)
        self.clarity_spin.valueChanged.connect(self._schedule_autosave)

    def _build_shortcuts(self) -> None:
        copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        copy_shortcut.activated.connect(self._copy_current_settings)
        self._shortcut_refs.append(copy_shortcut)

        paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        paste_shortcut.activated.connect(self._paste_to_selected)
        self._shortcut_refs.append(paste_shortcut)

        apply_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        apply_shortcut.activated.connect(self._save_current_asset_settings)
        self._shortcut_refs.append(apply_shortcut)

        sync_shortcut = QShortcut(QKeySequence("Shift+S"), self)
        sync_shortcut.activated.connect(self._start_sync_filtered)
        self._shortcut_refs.append(sync_shortcut)

        before_after_shortcut = QShortcut(QKeySequence("Y"), self)
        before_after_shortcut.activated.connect(
            lambda: self.before_after_btn.setChecked(not self.before_after_btn.isChecked())
        )
        self._shortcut_refs.append(before_after_shortcut)

    def refresh_data(self) -> None:
        current = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self.project_service.list_projects():
            self.project_combo.addItem(f"{project.id} - {project.name}", userData=project.id)
        if current is not None:
            idx = self.project_combo.findData(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self._load_assets()

    def set_selected_project(self, project_id: int) -> None:
        idx = self.project_combo.findData(project_id)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def _toggle_advanced_panel(self, opened: bool) -> None:
        self.advanced_panel.setVisible(opened)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)

    def _toggle_before_after(self, enabled: bool) -> None:
        self._before_mode = bool(enabled)
        self._apply_before_after_state()

    def _apply_before_after_state(self) -> None:
        if self._before_mode:
            self.before_after_badge.setText("APRES")
            self.before_after_badge.setProperty("hudState", "ok")
        else:
            self.before_after_badge.setText("AVANT")
            self.before_after_badge.setProperty("hudState", "info")
        self.before_after_badge.style().unpolish(self.before_after_badge)
        self.before_after_badge.style().polish(self.before_after_badge)
        self.before_after_badge.update()

    def _schedule_autosave(self, *_args) -> None:
        if self._form_loading:
            return
        self._autosave_timer.start(220)

    def _load_assets(self) -> None:
        project_id = self.project_combo.currentData()
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
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        row = QHBoxLayout()
        select_btn = NativePushButton(f"{asset.id} - {asset.file_name}")
        select_btn.setProperty("cardSelect", "true")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.clicked.connect(lambda _checked=False, asset_id=asset.id: self._on_asset_card_selected(asset_id))
        badge = QLabel(f"R{int(asset.rating)}")
        badge.setObjectName("CardBadge")
        row.addWidget(select_btn, 1)
        row.addWidget(badge)
        card_layout.addLayout(row)

        details = QLabel(str(asset.src_path))
        details.setObjectName("CardValue")
        details.setWordWrap(True)
        card_layout.addWidget(details)
        return card

    def _on_asset_card_selected(self, asset_id: int) -> None:
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
            return

        asset = self.assets_by_id.get(int(asset_id))
        if asset is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Aucun asset")
            self.asset_info_label.setText("Selection: -")
            self._apply_settings_to_form(dict(DEFAULT_EDIT_SETTINGS))
            return

        file_path = Path(str(asset.src_path)) if asset.src_path else None
        if file_path is None or not file_path.exists():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Fichier introuvable")
        else:
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText("Apercu indisponible")
            else:
                self.preview_label.setText("")
                scaled = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.preview_label.setPixmap(scaled)

        rejected = "oui" if bool(asset.is_rejected) else "non"
        self.asset_info_label.setText(
            f"Selection: {asset.file_name} | note={int(asset.rating)} | rejet={rejected}"
        )
        self._apply_settings_to_form(asset.edit_settings)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._on_select_asset()

    def _apply_settings_to_form(self, settings: dict[str, object]) -> None:
        payload = dict(DEFAULT_EDIT_SETTINGS)
        payload.update(settings or {})
        self._form_loading = True
        try:
            self.exposure_spin.setValue(float(payload.get("exposure", 0.0)))
            self.wb_temp_spin.setValue(int(payload.get("wb_temp", 5500)))
            self.wb_tint_spin.setValue(int(payload.get("wb_tint", 0)))
            crop_ratio = str(payload.get("crop_ratio", "original"))
            crop_idx = self.crop_ratio_combo.findText(crop_ratio)
            self.crop_ratio_combo.setCurrentIndex(max(0, crop_idx))
            self.straighten_spin.setValue(float(payload.get("straighten", 0.0)))
            self.contrast_spin.setValue(int(payload.get("contrast", 0)))
            self.highlights_spin.setValue(int(payload.get("highlights", 0)))
            self.shadows_spin.setValue(int(payload.get("shadows", 0)))
            self.vibrance_spin.setValue(int(payload.get("vibrance", 0)))
            self.saturation_spin.setValue(int(payload.get("saturation", 0)))
            self.clarity_spin.setValue(int(payload.get("clarity", 0)))
        finally:
            self._form_loading = False

    def _collect_form_settings(self) -> dict[str, object]:
        return {
            "exposure": float(self.exposure_spin.value()),
            "wb_temp": int(self.wb_temp_spin.value()),
            "wb_tint": int(self.wb_tint_spin.value()),
            "crop_ratio": str(self.crop_ratio_combo.currentText()),
            "straighten": float(self.straighten_spin.value()),
            "contrast": int(self.contrast_spin.value()),
            "highlights": int(self.highlights_spin.value()),
            "shadows": int(self.shadows_spin.value()),
            "vibrance": int(self.vibrance_spin.value()),
            "saturation": int(self.saturation_spin.value()),
            "clarity": int(self.clarity_spin.value()),
        }

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
        project_id = self.project_combo.currentData()
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
        on_operation_started,
        on_operation_ended,
        on_job_event=None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.preset_service = preset_service
        self.export_service = export_service
        self.on_operation_started = on_operation_started
        self.on_operation_ended = on_operation_ended
        self.on_job_event = on_job_event or (lambda _message: None)
        self._last_auto_destination = ""
        self._job_thread: QThread | None = None
        self._job_worker: JobWorker | None = None
        self._queue_seq = 0
        self._queue_paused = False
        self._active_started_at: datetime | None = None
        self._active_queue_id: int | None = None
        self._queue_items: list[ExportQueueItem] = []

        layout = QVBoxLayout(self)

        controls = QGroupBox("Export multi-profils")
        controls_layout = QFormLayout(controls)

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._sync_export_context)

        destination_row = QHBoxLayout()
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Dossier de sortie")
        browse_btn = _new_button("Parcourir")
        browse_btn.clicked.connect(self._pick_destination)
        destination_row.addWidget(self.destination_edit)
        destination_row.addWidget(browse_btn)

        profiles_row = QHBoxLayout()
        self.web_check = QCheckBox("web")
        self.web_check.setChecked(True)
        self.print_check = QCheckBox("print")
        self.print_check.setChecked(True)
        self.social_check = QCheckBox("social")
        self.social_check.setChecked(True)
        profiles_row.addWidget(self.web_check)
        profiles_row.addWidget(self.print_check)
        profiles_row.addWidget(self.social_check)

        self.min_rating_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_combo.addItem(str(rating), userData=rating)

        delivery_row = QHBoxLayout()
        self.zip_check = QCheckBox("ZIP livraison")
        self.zip_check.setChecked(True)
        self.report_check = QCheckBox("Rapport .txt")
        self.report_check.setChecked(True)
        self.contact_sheet_check = QCheckBox("Planche contact PDF")
        self.contact_sheet_check.setChecked(True)
        delivery_row.addWidget(self.zip_check)
        delivery_row.addWidget(self.report_check)
        delivery_row.addWidget(self.contact_sheet_check)

        self.run_btn = _new_button("Ajouter + Lancer", primary=True)
        self.run_btn.clicked.connect(self._run_export)
        self.queue_add_btn = _new_button("Ajouter file")
        self.queue_add_btn.clicked.connect(self._enqueue_current_export)
        self.start_queue_btn = _new_button("Lancer file")
        self.start_queue_btn.clicked.connect(self._start_next_queue_item)
        self.cancel_btn = _new_button("Annuler")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_export)

        run_row = QHBoxLayout()
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.queue_add_btn)
        run_row.addWidget(self.start_queue_btn)
        run_row.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.eta_label = QLabel("ETA: -")
        self.eta_label.setObjectName("CullingMeta")

        controls_layout.addRow("Projet", self.project_combo)
        controls_layout.addRow("Destination", destination_row)
        controls_layout.addRow("Profils", profiles_row)
        controls_layout.addRow("Note min", self.min_rating_combo)
        controls_layout.addRow("Livraison", delivery_row)
        controls_layout.addRow("", run_row)
        controls_layout.addRow("Progression", self.progress_bar)
        controls_layout.addRow("", self.eta_label)

        layout.addWidget(controls)

        queue_box = QGroupBox("File d'attente exports")
        queue_layout = QVBoxLayout(queue_box)

        queue_header = QHBoxLayout()
        self.queue_state_label = QLabel("Queue: idle")
        self.queue_counts_label = QLabel("pending=0 | running=0 | done=0 | failed=0")
        self.queue_counts_label.setObjectName("CullingMeta")
        queue_header.addWidget(self.queue_state_label)
        queue_header.addStretch(1)
        queue_header.addWidget(self.queue_counts_label)
        queue_layout.addLayout(queue_header)

        queue_actions = QHBoxLayout()
        self.pause_queue_btn = _new_button("Pause file")
        self.pause_queue_btn.setCheckable(True)
        self.pause_queue_btn.toggled.connect(self._toggle_queue_pause)
        self.retry_failed_btn = _new_button("Retry fails")
        self.retry_failed_btn.clicked.connect(self._retry_failed_queue_items)
        self.clear_completed_btn = _new_button("Vider termines")
        self.clear_completed_btn.clicked.connect(self._clear_completed_queue_items)
        queue_actions.addWidget(self.pause_queue_btn)
        queue_actions.addWidget(self.retry_failed_btn)
        queue_actions.addWidget(self.clear_completed_btn)
        queue_actions.addStretch(1)
        queue_layout.addLayout(queue_actions)

        self.queue_cards_area = QScrollArea()
        self.queue_cards_area.setWidgetResizable(True)
        self.queue_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.queue_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_cards_content = QWidget()
        self.queue_cards_layout = QVBoxLayout(self.queue_cards_content)
        self.queue_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.queue_cards_layout.setSpacing(8)
        self.queue_cards_area.setWidget(self.queue_cards_content)
        queue_layout.addWidget(self.queue_cards_area)

        layout.addWidget(queue_box)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def refresh_data(self) -> None:
        current = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self.project_service.list_projects():
            self.project_combo.addItem(f"{project.id} - {project.name}", userData=project.id)
        if current is not None:
            idx = self.project_combo.findData(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self._sync_export_context()
        self._refresh_queue_view()

    def set_selected_project(self, project_id: int) -> None:
        idx = self.project_combo.findData(project_id)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def _sync_export_context(self) -> None:
        project_id = self.project_combo.currentData()
        if project_id is None:
            return
        project = self.project_service.get_project(project_id)
        if project is None:
            return
        self._sync_default_destination(project)
        self._sync_delivery_options_from_preset(project)

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

    def _build_export_payload(self) -> dict | None:
        project_id = self.project_combo.currentData()
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

        return {
            "project_id": int(project_id),
            "project_label": str(self.project_combo.currentText()),
            "destination_dir": destination,
            "profiles": profiles,
            "min_rating": int(self.min_rating_combo.currentData() or 0),
            "create_zip": self.zip_check.isChecked(),
            "create_report": self.report_check.isChecked(),
            "create_contact_sheet": self.contact_sheet_check.isChecked(),
        }

    def _enqueue_payload(self, payload: dict, attempts: int = 1) -> ExportQueueItem:
        self._queue_seq += 1
        item = ExportQueueItem(
            queue_id=self._queue_seq,
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
            if item.status == "queued":
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
        failed_items = [item for item in self._queue_items if item.status in {"failed", "cancelled"}]
        if not failed_items:
            QMessageBox.information(self, "Retry queue", "Aucun job failed/cancelled a relancer.")
            return
        for item in failed_items:
            payload = {
                "project_id": item.project_id,
                "project_label": item.project_label,
                "destination_dir": item.destination_dir,
                "profiles": list(item.profiles),
                "min_rating": item.min_rating,
                "create_zip": item.create_zip,
                "create_report": item.create_report,
                "create_contact_sheet": item.create_contact_sheet,
            }
            self._enqueue_payload(payload, attempts=int(item.attempts) + 1)
        self.on_job_event(f"[Export] Retry queue: {len(failed_items)} job(s) re-ajoutes.")
        self._start_next_queue_item()

    def _clear_completed_queue_items(self) -> None:
        self._queue_items = [item for item in self._queue_items if item.status not in {"completed"}]
        self._refresh_queue_view()

    def _on_export_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(max(0, min(int(done), safe_total)))
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
            if cancelled:
                active_item.status = "cancelled"
            elif total_failed > 0:
                active_item.status = "failed"
            else:
                active_item.status = "completed"
            active_item.message = (
                f"exported={total_exported}, failed={total_failed}, profils={len(batch.profiles)}"
            )

        self.on_job_event(
            f"[Export] termine | exported={total_exported}, failed={total_failed}, profils={len(batch.profiles)}"
        )
        self._refresh_queue_view()

    def _on_export_error(self, message: str) -> None:
        active_item = self._queue_item_by_id(self._active_queue_id)
        if active_item is not None:
            active_item.status = "failed"
            active_item.ended_at = datetime.utcnow()
            active_item.message = str(message)
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

            details = QLabel(
                f"Dest: {item.destination_dir}\n"
                f"Profils: {', '.join(item.profiles)} | note>={item.min_rating}\n"
                f"Options: zip={int(item.create_zip)} report={int(item.create_report)} "
                f"planche={int(item.create_contact_sheet)}"
            )
            details.setObjectName("CardValue")
            details.setWordWrap(True)
            card_layout.addWidget(details)

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
        failed = len([item for item in self._queue_items if item.status in {"failed", "cancelled"}])

        if self._queue_paused:
            state = "Queue: paused"
        elif running > 0:
            state = "Queue: running"
        elif pending > 0:
            state = "Queue: ready"
        else:
            state = "Queue: idle"
        self.queue_state_label.setText(state)
        self.queue_counts_label.setText(f"pending={pending} | running={running} | done={done} | failed={failed}")
        self._render_queue_cards()


class PresetTab(QWidget):
    def __init__(self, preset_service: PresetService, on_data_changed) -> None:
        super().__init__()
        self.preset_service = preset_service
        self.on_data_changed = on_data_changed
        self.current_preset_id: int | None = None
        self.expanded_preset_ids: set[int] = set()
        self.profile_widgets: dict[str, dict[str, object]] = {}

        layout = QHBoxLayout(self)

        self.preset_cards_area = QScrollArea()
        self.preset_cards_area.setWidgetResizable(True)
        self.preset_cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.preset_cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preset_cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preset_cards_content = QWidget()
        self.preset_cards_layout = QVBoxLayout(self.preset_cards_content)
        self.preset_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.preset_cards_layout.setSpacing(10)
        self.preset_cards_area.setWidget(self.preset_cards_content)
        layout.addWidget(self.preset_cards_area, 1)

        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)

        editor_form = QFormLayout()
        self.name_edit = QLineEdit()
        editor_form.addRow("Nom", self.name_edit)

        usage_hint = QLabel(
            "Un preset est un modele reutilisable. Assigne-le ensuite a un projet depuis Hub Projets."
        )
        usage_hint.setWordWrap(True)
        editor_form.addRow("Usage", usage_hint)
        self.associated_projects_label = QLabel("Projets associes: -")
        self.associated_projects_label.setWordWrap(True)
        editor_form.addRow("Associe a", self.associated_projects_label)

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

        sync_row = QHBoxLayout()
        to_json_btn = _new_button("Formulaire -> JSON")
        to_json_btn.clicked.connect(self._sync_json_from_form)
        to_form_btn = _new_button("JSON -> Formulaire")
        to_form_btn.clicked.connect(self._sync_form_from_json)
        sync_row.addWidget(to_json_btn)
        sync_row.addWidget(to_form_btn)

        btn_row = QHBoxLayout()
        new_btn = _new_button("Nouveau")
        new_btn.clicked.connect(self._reset_form)
        save_btn = _new_button("Enregistrer", primary=True)
        save_btn.clicked.connect(self._save)
        delete_btn = _new_button("Supprimer")
        delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(delete_btn)

        versions_box = QGroupBox("Versions")
        versions_layout = QHBoxLayout(versions_box)
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(240)
        rollback_btn = _new_button("Rollback")
        rollback_btn.clicked.connect(self._rollback)
        versions_layout.addWidget(self.version_combo)
        versions_layout.addWidget(rollback_btn)

        form_layout.addLayout(editor_form)
        form_layout.addWidget(self.config_tabs)
        form_layout.addLayout(sync_row)
        form_layout.addWidget(versions_box)
        form_layout.addLayout(btn_row)

        layout.addWidget(form_widget, 1)
        self._reset_form()

    def refresh_data(self) -> None:
        presets = self.preset_service.list_presets()
        ids = {preset.id for preset in presets}
        if self.current_preset_id not in ids:
            self.current_preset_id = None

        auto_select_first = False
        if self.current_preset_id is None and presets:
            self.current_preset_id = presets[0].id
            auto_select_first = True

        self._render_preset_cards(presets)
        if auto_select_first and self.current_preset_id is not None:
            self._load_preset_into_form(self.current_preset_id)

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
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        select_btn = NativePushButton(preset.name)
        select_btn.setProperty("cardSelect", "true")
        select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_btn.setMinimumHeight(32)
        select_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        select_btn.clicked.connect(
            lambda _checked=False, preset_id=preset.id: self._on_preset_card_selected(preset_id)
        )
        usage_label = QLabel(self._linked_projects_summary(preset))
        usage_label.setObjectName("CardBadge")
        usage_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        toggle = QToolButton()
        toggle.setProperty("cardToggle", "true")
        toggle.setCheckable(True)
        expanded = bool(is_selected or (preset.id in self.expanded_preset_ids))
        toggle.setChecked(expanded)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        toggle.setFixedSize(24, 24)

        header.addWidget(select_btn, 1)
        header.addWidget(usage_label)
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
        details_layout.addRow("Associe a", self._card_value(self._linked_projects_tooltip(preset)))
        details_layout.addRow("Maj", self._card_value(preset.updated_at.strftime("%Y-%m-%d %H:%M:%S")))
        details.setVisible(expanded)
        card_layout.addWidget(details)

        def _on_toggle(opened: bool, preset_id=preset.id, panel=details, btn=toggle):
            panel.setVisible(opened)
            btn.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            if opened:
                self.expanded_preset_ids.add(preset_id)
            else:
                self.expanded_preset_ids.discard(preset_id)

        toggle.toggled.connect(_on_toggle)
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

    def _reset_form(self) -> None:
        self.current_preset_id = None
        self.name_edit.clear()
        self.associated_projects_label.setText("Projets associes: -")
        self._apply_config_to_form(default_preset_config())
        self._sync_json_from_form()
        self.config_tabs.setCurrentIndex(0)
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
        outer_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        container = QWidget()
        layout = QGridLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

        naming_box = QGroupBox("Nommage")
        naming_form = QFormLayout(naming_box)
        naming_form.setHorizontalSpacing(10)
        self.naming_pattern_edit = QLineEdit()
        self.naming_pattern_edit.setPlaceholderText("{project}_{date}_{seq:04d}")
        self.naming_pattern_edit.setToolTip(
            "Pattern de nom de fichier.\nVariables: {project}, {date}, {seq}."
        )
        naming_form.addRow("Pattern", self.naming_pattern_edit)
        layout.addWidget(naming_box, 0, 0, 1, 2)

        import_box = QGroupBox("Import")
        import_form = QFormLayout(import_box)
        import_form.setHorizontalSpacing(10)
        self.import_verify_checksum_check = QCheckBox("Verifier checksum")
        self.import_verify_checksum_check.setToolTip(
            "Compare le hash source/destination pour garantir l'integrite des copies."
        )
        self.import_dual_backup_check = QCheckBox("Double sauvegarde")
        self.import_dual_backup_check.setToolTip(
            "Si active, une deuxieme copie est ecrite dans le dossier backup."
        )
        self.import_dual_backup_check.toggled.connect(self._toggle_backup_path)

        backup_row = QHBoxLayout()
        self.import_backup_path_edit = QLineEdit()
        self.import_backup_path_edit.setPlaceholderText("Chemin backup (optionnel)")
        self.import_backup_path_edit.setToolTip(
            "Dossier de destination pour la sauvegarde secondaire."
        )
        backup_btn = _new_button("Parcourir")
        backup_btn.clicked.connect(self._pick_backup_path)
        backup_row.addWidget(self.import_backup_path_edit)
        backup_row.addWidget(backup_btn)
        self.import_backup_browse_btn = backup_btn

        import_form.addRow("", self.import_verify_checksum_check)
        import_form.addRow("", self.import_dual_backup_check)
        import_form.addRow("Backup path", backup_row)
        layout.addWidget(import_box, 1, 0)

        export_box = QGroupBox("Export profils")
        export_grid = QGridLayout(export_box)
        export_grid.setHorizontalSpacing(10)
        export_grid.setVerticalSpacing(10)
        export_grid.setContentsMargins(8, 6, 8, 8)
        export_grid.setColumnStretch(0, 1)
        export_grid.setColumnStretch(1, 1)
        for idx, profile in enumerate(("web", "print", "social")):
            card = self._build_profile_card(profile)
            row = idx // 2
            col = idx % 2
            export_grid.addWidget(card, row, col)
        layout.addWidget(export_box, 2, 0, 1, 2)

        watermark_box = QGroupBox("Watermark")
        watermark_form = QFormLayout(watermark_box)
        watermark_form.setHorizontalSpacing(10)
        self.watermark_enabled_check = QCheckBox("Activer watermark")
        self.watermark_enabled_check.setToolTip("Ajoute un texte en filigrane sur les exports.")
        self.watermark_text_edit = QLineEdit()
        self.watermark_text_edit.setToolTip("Texte du watermark (ex: Nom Studio).")
        self.watermark_opacity_spin = QSpinBox()
        self.watermark_opacity_spin.setRange(0, 100)
        self.watermark_opacity_spin.setSingleStep(5)
        self.watermark_opacity_spin.setSuffix("%")
        self.watermark_opacity_spin.setToolTip("0% = transparent, 100% = opaque.")
        watermark_form.addRow("", self.watermark_enabled_check)
        watermark_form.addRow("Texte", self.watermark_text_edit)
        watermark_form.addRow("Opacite", self.watermark_opacity_spin)
        layout.addWidget(watermark_box, 1, 1)

        delivery_box = QGroupBox("Livraison")
        delivery_form = QFormLayout(delivery_box)
        delivery_form.setHorizontalSpacing(10)
        self.delivery_zip_check = QCheckBox("ZIP livraison")
        self.delivery_zip_check.setToolTip("Cree une archive ZIP prete a envoyer au client.")
        self.delivery_report_check = QCheckBox("Rapport .txt")
        self.delivery_report_check.setToolTip("Genere un rapport texte des exports.")
        self.delivery_contact_sheet_check = QCheckBox("Planche contact PDF")
        self.delivery_contact_sheet_check.setToolTip("Genere une planche contact PDF des images exportees.")
        delivery_form.addRow("", self.delivery_zip_check)
        delivery_form.addRow("", self.delivery_report_check)
        delivery_form.addRow("", self.delivery_contact_sheet_check)

        help_box = QGroupBox("Aide rapide")
        help_layout = QVBoxLayout(help_box)
        help_layout.setContentsMargins(8, 6, 8, 8)
        help_label = QLabel("Astuce: utilise Formulaire puis verifie le rendu dans l'onglet JSON.")
        help_label.setWordWrap(True)
        help_layout.addWidget(help_label)

        layout.addWidget(delivery_box, 3, 0)
        layout.addWidget(help_box, 3, 1)
        layout.setRowStretch(4, 1)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)
        return tab

    def _build_profile_card(self, profile: str) -> QGroupBox:
        box = QGroupBox(profile)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        format_combo = QComboBox()
        format_combo.addItems(["JPEG", "PNG", "TIFF"])
        format_combo.setToolTip("Format de sortie du profil.")

        width_spin = QSpinBox()
        width_spin.setRange(320, 12000)
        width_spin.setSingleStep(160)
        width_spin.setToolTip("Largeur maximale en pixels.")

        quality_spin = QSpinBox()
        quality_spin.setRange(1, 100)
        quality_spin.setSingleStep(1)
        quality_spin.setToolTip("Qualite de compression (surtout JPEG).")

        subdir_edit = QLineEdit()
        subdir_edit.setPlaceholderText(profile)
        subdir_edit.setToolTip("Sous-dossier de sortie pour ce profil.")

        grid.addWidget(QLabel("Format"), 0, 0)
        grid.addWidget(format_combo, 0, 1)
        grid.addWidget(QLabel("Max px"), 0, 2)
        grid.addWidget(width_spin, 0, 3)
        grid.addWidget(QLabel("Qualite"), 1, 0)
        grid.addWidget(quality_spin, 1, 1)
        grid.addWidget(QLabel("Subdir"), 1, 2)
        grid.addWidget(subdir_edit, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.profile_widgets[profile] = {
            "format": format_combo,
            "max_width": width_spin,
            "quality": quality_spin,
            "subdir": subdir_edit,
        }
        return box

    def _toggle_backup_path(self, enabled: bool) -> None:
        self.import_backup_path_edit.setEnabled(enabled)
        self.import_backup_browse_btn.setEnabled(enabled)

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

        watermark = merged.get("watermark", {})
        self.watermark_enabled_check.setChecked(bool(watermark.get("enabled", False)))
        self.watermark_text_edit.setText(str(watermark.get("text", "")))
        self.watermark_opacity_spin.setValue(
            self._normalize_opacity_percent_for_ui(watermark.get("opacity", 70))
        )

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
            "watermark": {
                "enabled": self.watermark_enabled_check.isChecked(),
                "text": self.watermark_text_edit.text().strip(),
                "opacity": int(self.watermark_opacity_spin.value()),
            },
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

    @staticmethod
    def _normalize_opacity_percent_for_ui(value) -> int:
        try:
            raw = int(float(value))
        except Exception:
            return 70
        if raw < 0:
            return 0
        if raw > 100:
            raw = int(round((raw / 255.0) * 100))
        return max(0, min(100, raw))


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

        layout = QVBoxLayout(self)

        box = QGroupBox("Stockage global")
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.storage_root_edit = QLineEdit()
        browse_btn = _new_button("Parcourir")
        browse_btn.clicked.connect(self._pick_storage_root)
        row.addWidget(self.storage_root_edit)
        row.addWidget(browse_btn)

        self.apply_btn = _new_button("Appliquer", primary=True)
        self.apply_btn.clicked.connect(self._apply_storage_root)

        self.status_label = QLabel("Statut migration: idle")
        self.error_label = QLabel("Erreur: -")
        self.active_data_dir_label = QLabel("Data active: -")

        form.addRow("Dossier global de stockage", row)
        form.addRow("", self.apply_btn)
        form.addRow("", self.status_label)
        form.addRow("", self.error_label)
        form.addRow("", self.active_data_dir_label)

        theme_box = QGroupBox("Theme")
        theme_form = QFormLayout(theme_box)

        accent_row = QHBoxLayout()
        self.accent_color_edit = QLineEdit()
        self.accent_color_edit.setPlaceholderText("#39FF14")
        accent_pick_btn = _new_button("Choisir")
        accent_pick_btn.clicked.connect(self._pick_accent_color)
        accent_row.addWidget(self.accent_color_edit)
        accent_row.addWidget(accent_pick_btn)

        self.apply_theme_btn = _new_button("Appliquer accent", primary=True)
        self.apply_theme_btn.clicked.connect(self._apply_accent_color)
        self.theme_hint_label = QLabel("Couleur d'accent UI (hex): #RRGGBB")
        self.theme_hint_label.setWordWrap(True)

        theme_form.addRow("Accent", accent_row)
        theme_form.addRow("", self.apply_theme_btn)
        theme_form.addRow("", self.theme_hint_label)

        layout.addWidget(box)
        layout.addWidget(theme_box)
        layout.addStretch(1)

    def refresh_data(self) -> None:
        settings = self.storage_service.get_settings()
        self.storage_root_edit.setText(settings.get("storage_root", ""))
        self.accent_color_edit.setText(settings.get("accent_color", "#39FF14"))
        self.status_label.setText(f"Statut migration: {settings.get('last_migration_status', 'idle')}")
        self.error_label.setText(f"Erreur: {settings.get('last_migration_error') or '-'}")
        self.active_data_dir_label.setText(f"Data active: {settings.get('active_data_dir', '-')}")

    def _pick_storage_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier global de stockage")
        if directory:
            self.storage_root_edit.setText(directory)

    def _pick_accent_color(self) -> None:
        current = QColor(normalize_accent_color(self.accent_color_edit.text().strip()))
        chosen = QColorDialog.getColor(current, self, "Choisir couleur d'accent")
        if chosen.isValid():
            self.accent_color_edit.setText(chosen.name().upper())

    def _apply_accent_color(self) -> None:
        requested = self.accent_color_edit.text().strip()
        normalized = normalize_accent_color(requested)
        try:
            self.storage_service.set_accent_color(normalized)
            self.accent_color_edit.setText(normalized)
            self.on_theme_changed()
            QMessageBox.information(self, "Theme", f"Accent applique: {normalized}")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur theme", str(exc))

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
