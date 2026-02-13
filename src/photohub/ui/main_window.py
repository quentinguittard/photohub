from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QThread, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
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
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import compute_app_data_dir_from_root
from ..preset_defaults import default_preset_config
from ..services import (
    CullingService,
    ExportService,
    ImportService,
    PresetService,
    ProjectService,
    StorageService,
)

try:
    from qfluentwidgets import (
        FluentIcon as FIF,
        PushButton as FluentPushButton,
        SearchLineEdit as FluentSearchLineEdit,
    )

    QFLUENT_AVAILABLE = True
except Exception:  # pragma: no cover - optional UI dependency
    FIF = None
    FluentPushButton = None
    FluentSearchLineEdit = None
    QFLUENT_AVAILABLE = False


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
        self.recent_table = QTableWidget(0, 4)
        self.recent_table.setHorizontalHeaderLabels(["Nom", "Client", "Statut", "Date"])
        self.recent_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent_table.horizontalHeader().setStretchLastSection(True)
        recent_layout.addWidget(self.recent_table)
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
        self.recent_table.setRowCount(len(recent))
        for row, project in enumerate(recent):
            self.recent_table.setItem(row, 0, QTableWidgetItem(project.name))
            self.recent_table.setItem(row, 1, QTableWidgetItem(project.client.name if project.client else "-"))
            self.recent_table.setItem(row, 2, QTableWidgetItem(self.project_service.get_status_label(project.status)))
            self.recent_table.setItem(row, 3, QTableWidgetItem(project.shoot_date.strftime("%Y-%m-%d")))
        self.recent_table.resizeColumnsToContents()

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


class JobsTab(QWidget):
    def __init__(self, get_active_jobs: Callable[[], int]) -> None:
        super().__init__()
        self.get_active_jobs = get_active_jobs

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Centre de Jobs")
        title.setObjectName("PageTitle")
        self.jobs_state_label = QLabel("0 en cours")
        clear_btn = QPushButton("Vider journal")
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
    NAV_ITEMS = [
        ("dashboard", "Dashboard", "HOME"),
        ("projects", "Projets", "ALBUM"),
        ("ingest", "Ingest", "DOWNLOAD"),
        ("culling", "Tri", "CUT"),
        ("export", "Export", "SEND"),
        ("presets", "Presets", "EDIT"),
        ("settings", "Settings", "SETTING"),
        ("jobs", "Jobs", "SYNC"),
    ]

    def __init__(
        self,
        project_service: ProjectService,
        preset_service: PresetService,
        culling_service: CullingService,
        import_service: ImportService,
        export_service: ExportService,
        storage_service: StorageService,
        on_reload_runtime: Callable,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.preset_service = preset_service
        self.culling_service = culling_service
        self.import_service = import_service
        self.export_service = export_service
        self.storage_service = storage_service
        self.on_reload_runtime = on_reload_runtime
        self.active_ops_count = 0

        self.setWindowTitle("PhotoHub - Studio Workflow")
        self.resize(1400, 860)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        self.current_nav_key = ""
        self.nav_buttons: dict[str, QPushButton] = {}

        nav_panel = QWidget()
        nav_panel.setObjectName("SideBar")
        nav_panel.setFixedWidth(220)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(10, 10, 10, 10)
        nav_layout.setSpacing(6)

        top_keys = ["dashboard", "projects", "ingest", "culling", "export", "presets"]
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

        root_layout.addWidget(nav_panel)

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

        topbar_layout.addWidget(app_title)
        topbar_layout.addSpacing(8)
        topbar_layout.addWidget(self.search_edit, 1)
        topbar_layout.addWidget(QLabel("Projet actif"))
        topbar_layout.addWidget(self.project_context_combo)
        topbar_layout.addWidget(self.activity_badge)
        content_layout.addWidget(topbar)

        self.stack = QStackedWidget()
        self.dashboard_tab = DashboardTab(self.project_service, get_active_jobs=self._get_active_jobs_count)
        self.hub_tab = HubTab(self.project_service, self.preset_service, on_data_changed=self.refresh_all)
        self.import_export_tab = ImportExportTab(
            project_service=self.project_service,
            preset_service=self.preset_service,
            culling_service=self.culling_service,
            import_service=self.import_service,
            export_service=self.export_service,
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

        self._apply_sprint1_style()
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

    def _build_nav_button(self, key: str) -> QPushButton:
        label = key
        icon_name = ""
        for nav_key, nav_label, nav_icon_name in self.NAV_ITEMS:
            if nav_key == key:
                label = nav_label
                icon_name = nav_icon_name
                break

        if QFLUENT_AVAILABLE and FluentPushButton is not None:
            button = FluentPushButton(label)
        else:
            button = QPushButton(label)

        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("navButton", True)
        button.setProperty("navKey", key)
        button.setProperty("active", "false")
        icon = self._fluent_icon(icon_name)
        if not icon.isNull():
            button.setIcon(icon)
        button.clicked.connect(lambda _checked=False, k=key: self._switch_page(k))
        return button

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

    def _switch_page(self, key: str) -> None:
        normalized = (key or "").strip().lower()
        if not normalized:
            return
        self.current_nav_key = normalized
        for nav_key, button in self.nav_buttons.items():
            button.setProperty("active", "true" if nav_key == normalized else "false")
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

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
        elif normalized == "export":
            self.stack.setCurrentWidget(self.import_export_tab)
            self.import_export_tab.set_current_section("export")
        elif normalized == "presets":
            self.stack.setCurrentWidget(self.presets_tab)
        elif normalized == "settings":
            self.stack.setCurrentWidget(self.settings_tab)
        elif normalized == "jobs":
            self.stack.setCurrentWidget(self.jobs_tab)

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
        self.project_context_combo.addItem("Aucun contexte", None)
        for project in projects:
            self.project_context_combo.addItem(project.name, project.id)

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
        self.import_service = runtime.import_service
        self.export_service = runtime.export_service

        self.dashboard_tab.project_service = self.project_service
        self.hub_tab.project_service = self.project_service
        self.hub_tab.preset_service = self.preset_service
        self.import_export_tab.import_tab.project_service = self.project_service
        self.import_export_tab.import_tab.import_service = self.import_service
        self.import_export_tab.culling_tab.project_service = self.project_service
        self.import_export_tab.culling_tab.culling_service = self.culling_service
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
        self.setStyleSheet(
            """
            QWidget {
                background: #f4f7fb;
                color: #10243e;
                font-size: 12px;
            }
            QWidget#SideBar {
                background: #0f2742;
                border: none;
                border-radius: 14px;
                color: #dbe8f7;
                padding: 8px;
            }
            QPushButton[navButton="true"] {
                text-align: left;
                border-radius: 8px;
                padding: 10px 10px;
                margin: 2px 0;
                border: 1px solid transparent;
                background: transparent;
                color: #dbe8f7;
            }
            QPushButton[navButton="true"]:hover {
                background: #173855;
                border-color: #2e587b;
            }
            QPushButton[navButton="true"][active="true"] {
                background: #1f4f78;
                border-color: #3d7bad;
                color: #ffffff;
            }
            #TopBar {
                border: 1px solid #d7e3f1;
                border-radius: 12px;
                background: #ffffff;
            }
            #AppTitle {
                font-size: 18px;
                font-weight: 700;
                color: #0f2742;
            }
            #ActivityBadge {
                border: 1px solid #b8cde3;
                border-radius: 10px;
                padding: 4px 10px;
                background: #eaf3fb;
                color: #20456b;
                font-weight: 600;
            }
            #PageTitle {
                font-size: 20px;
                font-weight: 700;
                color: #0f2742;
            }
            #StatCard {
                border: 1px solid #d7e3f1;
                border-radius: 12px;
                background: #ffffff;
            }
            #StatValue {
                font-size: 26px;
                font-weight: 700;
                color: #143656;
            }
            QGroupBox {
                border: 1px solid #d7e3f1;
                border-radius: 10px;
                margin-top: 8px;
                padding-top: 8px;
                background: #ffffff;
            }
            QGroupBox::title {
                left: 10px;
                padding: 0 4px;
            }
            QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit, QTableWidget {
                border: 1px solid #c5d6e8;
                border-radius: 8px;
                background: #ffffff;
                padding: 4px 6px;
            }
            QPushButton {
                border: 1px solid #2f6ea1;
                border-radius: 8px;
                background: #2f6ea1;
                color: #ffffff;
                padding: 6px 12px;
            }
            QPushButton:disabled {
                background: #94abc3;
                border-color: #94abc3;
                color: #f2f6fa;
            }
            """
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
        self.preset_combo.addItem("Aucun preset", None)

        self.custom_location_check = QCheckBox("Emplacement personnalise pour ce projet")
        self.custom_location_check.toggled.connect(self._toggle_custom_location)

        custom_row = QHBoxLayout()
        self.custom_location_edit = QLineEdit()
        self.custom_location_edit.setPlaceholderText("Dossier parent du projet")
        self.custom_location_edit.setEnabled(False)
        custom_browse_btn = QPushButton("Parcourir")
        custom_browse_btn.setEnabled(False)
        custom_browse_btn.clicked.connect(self._pick_custom_location)
        self.custom_location_browse_btn = custom_browse_btn
        custom_row.addWidget(self.custom_location_edit)
        custom_row.addWidget(custom_browse_btn)

        self.create_btn = QPushButton("Creer Projet")
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
        self.assign_combo.addItem("Aucun preset", None)
        self.assign_btn = QPushButton("Affecter au projet selectionne")
        self.assign_btn.clicked.connect(self._assign_selected_project)
        assign_layout.addWidget(QLabel("Preset"))
        assign_layout.addWidget(self.assign_combo)
        assign_layout.addWidget(self.assign_btn)
        layout.addWidget(assign_box)

        status_box = QGroupBox("Statut Projet")
        status_layout = QHBoxLayout(status_box)
        self.status_combo = QComboBox()
        for code, label in self.project_service.list_status_choices():
            self.status_combo.addItem(label, code)
        self.status_btn = QPushButton("Mettre a jour le statut")
        self.status_btn.clicked.connect(self._update_selected_project_status)
        status_layout.addWidget(QLabel("Statut"))
        status_layout.addWidget(self.status_combo)
        status_layout.addWidget(self.status_btn)
        layout.addWidget(status_box)

        self.project_table = QTableWidget(0, 7)
        self.project_table.setHorizontalHeaderLabels(
            ["ID", "Nom", "Client", "Date", "Statut", "Preset", "Dossier"]
        )
        self.project_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.project_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.project_table.horizontalHeader().setStretchLastSection(True)
        self.project_table.itemSelectionChanged.connect(self._sync_controls_with_selected_project)
        layout.addWidget(self.project_table)

    def refresh_data(self) -> None:
        selected_project_id = self._selected_project_id()
        presets = self.preset_service.list_presets()
        self.preset_combo.blockSignals(True)
        self.assign_combo.blockSignals(True)
        self.preset_combo.clear()
        self.assign_combo.clear()
        self.preset_combo.addItem("Aucun preset", None)
        self.assign_combo.addItem("Aucun preset", None)
        for preset in presets:
            self.preset_combo.addItem(preset.name, preset.id)
            self.assign_combo.addItem(preset.name, preset.id)
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

        self.project_table.setRowCount(len(filtered_projects))
        selected_row = -1
        for row, project in enumerate(filtered_projects):
            self.project_table.setItem(row, 0, QTableWidgetItem(str(project.id)))
            self.project_table.setItem(row, 1, QTableWidgetItem(project.name))
            self.project_table.setItem(row, 2, QTableWidgetItem(project.client.name if project.client else "-"))
            self.project_table.setItem(row, 3, QTableWidgetItem(project.shoot_date.strftime("%Y-%m-%d")))
            self.project_table.setItem(
                row,
                4,
                QTableWidgetItem(self.project_service.get_status_label(project.status)),
            )
            self.project_table.setItem(row, 5, QTableWidgetItem(project.preset.name if project.preset else "-"))
            self.project_table.setItem(row, 6, QTableWidgetItem(project.root_path))
            if selected_project_id is not None and project.id == selected_project_id:
                selected_row = row
        self.project_table.resizeColumnsToContents()
        if selected_row >= 0:
            self.project_table.selectRow(selected_row)
        elif self.project_table.rowCount() > 0:
            self.project_table.selectRow(0)
        self._sync_controls_with_selected_project()

    def set_name_filter(self, value: str) -> None:
        self._name_filter = value.strip()
        self.refresh_data()

    def select_project_by_id(self, project_id: int) -> None:
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 0)
            if item is None:
                continue
            if int(item.text()) == int(project_id):
                self.project_table.selectRow(row)
                self._sync_controls_with_selected_project()
                return

    def _selected_project_id(self) -> int | None:
        row = self.project_table.currentRow()
        if row < 0:
            return None
        item = self.project_table.item(row, 0)
        if item is None:
            return None
        return int(item.text())

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
        row = self.project_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Selection", "Selectionne un projet dans la table.")
            return
        project_id = int(self.project_table.item(row, 0).text())
        preset_id = self.assign_combo.currentData()
        try:
            self.project_service.assign_preset(project_id, preset_id)
        except Exception as exc:
            QMessageBox.critical(self, "Erreur preset", str(exc))
            return
        self.on_data_changed()

    def _update_selected_project_status(self) -> None:
        row = self.project_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Selection", "Selectionne un projet dans la table.")
            return

        project_id = int(self.project_table.item(row, 0).text())
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
        row = self.project_table.currentRow()
        if row < 0:
            return
        try:
            project_id = int(self.project_table.item(row, 0).text())
        except Exception:
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
        self.sections.addTab(self.export_tab, "Export")
        layout.addWidget(self.sections)

    def refresh_data(self) -> None:
        self.import_tab.refresh_data()
        self.culling_tab.refresh_data()
        self.export_tab.refresh_data()

    def set_current_section(self, section: str) -> None:
        normalized = (section or "").strip().lower()
        index_map = {
            "import": 0,
            "ingest": 0,
            "culling": 1,
            "tri": 1,
            "export": 2,
        }
        idx = index_map.get(normalized)
        if idx is None:
            return
        self.sections.setCurrentIndex(idx)

    def set_selected_project(self, project_id: int) -> None:
        self.import_tab.set_selected_project(project_id)
        self.culling_tab.set_selected_project(project_id)
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
        browse_btn = QPushButton("Parcourir")
        browse_btn.clicked.connect(self._pick_source)
        source_row.addWidget(self.source_edit)
        source_row.addWidget(browse_btn)

        self.run_btn = QPushButton("Lancer Import")
        self.run_btn.clicked.connect(self._run_import)
        self.cancel_btn = QPushButton("Annuler")
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
            self.project_combo.addItem(f"{project.id} - {project.name}", project.id)
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

        layout = QVBoxLayout(self)

        controls = QGroupBox("Tri / Culling")
        controls_layout = QFormLayout(controls)

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._load_assets)

        self.rejected_mode_combo = QComboBox()
        self.rejected_mode_combo.addItem("Tout", "all")
        self.rejected_mode_combo.addItem("A garder", "kept")
        self.rejected_mode_combo.addItem("Rejetees", "rejected")
        self.rejected_mode_combo.currentIndexChanged.connect(self._load_assets)

        self.min_rating_filter_combo = QComboBox()
        for rating in range(0, 6):
            self.min_rating_filter_combo.addItem(str(rating), rating)
        self.min_rating_filter_combo.currentIndexChanged.connect(self._load_assets)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtre"))
        filter_row.addWidget(self.rejected_mode_combo)
        filter_row.addWidget(QLabel("Note min"))
        filter_row.addWidget(self.min_rating_filter_combo)
        refresh_btn = QPushButton("Rafraichir")
        refresh_btn.clicked.connect(self._load_assets)
        filter_row.addWidget(refresh_btn)

        controls_layout.addRow("Projet", self.project_combo)
        controls_layout.addRow("", filter_row)
        layout.addWidget(controls)

        body = QSplitter(Qt.Orientation.Horizontal)

        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Fichier", "Note", "Rejet", "Chemin"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_select_asset)
        self.table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.table)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        self.preview_label = QLabel("Apercu")
        self.preview_label.setMinimumHeight(280)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid #999;")
        self.preview_label.setScaledContents(False)
        side_layout.addWidget(self.preview_label)

        self.asset_info_label = QLabel("Selection: -")
        side_layout.addWidget(self.asset_info_label)

        actions_box = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_box)
        instant_row = QHBoxLayout()
        self.rating_combo = QComboBox()
        for rating in range(0, 6):
            self.rating_combo.addItem(str(rating), rating)
        set_rating_btn = QPushButton("Appliquer note")
        set_rating_btn.clicked.connect(self._apply_selected_rating)
        toggle_reject_btn = QPushButton("Basculer rejet")
        toggle_reject_btn.clicked.connect(self._toggle_selected_reject)
        instant_row.addWidget(QLabel("Note"))
        instant_row.addWidget(self.rating_combo)
        instant_row.addWidget(set_rating_btn)
        instant_row.addWidget(toggle_reject_btn)
        actions_layout.addLayout(instant_row)

        batch_row = QHBoxLayout()
        self.batch_rating_combo = QComboBox()
        for rating in range(0, 6):
            self.batch_rating_combo.addItem(str(rating), rating)
        batch_rate_btn = QPushButton("Batch note filtres")
        batch_rate_btn.clicked.connect(self._start_batch_rating)
        batch_reject_btn = QPushButton("Batch rejeter filtres")
        batch_reject_btn.clicked.connect(self._start_batch_reject)
        batch_restore_btn = QPushButton("Batch restaurer filtres")
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
        self.batch_cancel_btn = QPushButton("Annuler batch")
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

    def refresh_data(self) -> None:
        current = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in self.project_service.list_projects():
            self.project_combo.addItem(f"{project.id} - {project.name}", project.id)
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
            self.table.setRowCount(0)
            self.preview_label.setText("Apercu")
            self.asset_info_label.setText("Selection: -")
            return

        rejected_mode = self.rejected_mode_combo.currentData()
        min_rating = int(self.min_rating_filter_combo.currentData() or 0)
        assets = self.culling_service.list_assets(
            project_id=project_id,
            rejected_mode=rejected_mode,
            min_rating=min_rating,
        )

        current_asset_id = self._selected_asset_id()
        self.table.setRowCount(len(assets))
        selected_row = -1
        for row, asset in enumerate(assets):
            self.table.setItem(row, 0, QTableWidgetItem(str(asset.id)))
            self.table.setItem(row, 1, QTableWidgetItem(asset.file_name))
            self.table.setItem(row, 2, QTableWidgetItem(str(asset.rating)))
            self.table.setItem(row, 3, QTableWidgetItem("oui" if asset.is_rejected else "non"))
            self.table.setItem(row, 4, QTableWidgetItem(asset.src_path))
            if current_asset_id is not None and asset.id == current_asset_id:
                selected_row = row

        self.table.resizeColumnsToContents()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            self.preview_label.setText("Aucun asset")
            self.asset_info_label.setText("Selection: -")

    def _selected_asset_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return int(item.text())

    def _on_select_asset(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.preview_label.setText("Apercu")
            self.asset_info_label.setText("Selection: -")
            return

        path_item = self.table.item(row, 4)
        rating_item = self.table.item(row, 2)
        reject_item = self.table.item(row, 3)
        file_path = Path(path_item.text()) if path_item is not None else None

        if file_path is None or not file_path.exists():
            self.preview_label.setText("Fichier introuvable")
        else:
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                self.preview_label.setText("Apercu indisponible")
            else:
                scaled = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.preview_label.setPixmap(scaled)

        name = file_path.name if file_path else "-"
        rating = rating_item.text() if rating_item is not None else "-"
        rejected = reject_item.text() if reject_item is not None else "-"
        self.asset_info_label.setText(f"Selection: {name} | note={rating} | rejet={rejected}")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep preview readable when panel size changes.
        self._on_select_asset()

    def _apply_selected_rating(self) -> None:
        rating = int(self.rating_combo.currentData() or 0)
        self._set_selected_rating(rating)

    def _set_selected_rating(self, rating: int) -> None:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            return
        try:
            self.culling_service.update_asset(asset_id=asset_id, rating=rating)
            self._load_assets()
            self.on_data_changed()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur tri", str(exc))

    def _toggle_selected_reject(self) -> None:
        asset_id = self._selected_asset_id()
        if asset_id is None:
            return
        try:
            self.culling_service.toggle_rejected(asset_id=asset_id)
            self._load_assets()
            self.on_data_changed()
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

        layout = QVBoxLayout(self)

        controls = QGroupBox("Export multi-profils")
        controls_layout = QFormLayout(controls)

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._sync_export_context)

        destination_row = QHBoxLayout()
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Dossier de sortie")
        browse_btn = QPushButton("Parcourir")
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
            self.min_rating_combo.addItem(str(rating), rating)

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

        self.run_btn = QPushButton("Lancer Export")
        self.run_btn.clicked.connect(self._run_export)
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_export)

        run_row = QHBoxLayout()
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)

        controls_layout.addRow("Projet", self.project_combo)
        controls_layout.addRow("Destination", destination_row)
        controls_layout.addRow("Profils", profiles_row)
        controls_layout.addRow("Note min", self.min_rating_combo)
        controls_layout.addRow("Livraison", delivery_row)
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
            self.project_combo.addItem(f"{project.id} - {project.name}", project.id)
        if current is not None:
            idx = self.project_combo.findData(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self._sync_export_context()

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

    def _run_export(self) -> None:
        if self._job_thread is not None:
            QMessageBox.warning(self, "Operation en cours", "Un export est deja en cours.")
            return
        project_id = self.project_combo.currentData()
        destination = self.destination_edit.text().strip()
        if project_id is None:
            QMessageBox.warning(self, "Validation", "Selectionne un projet.")
            return
        if not destination:
            QMessageBox.warning(self, "Validation", "Selectionne un dossier destination.")
            return

        profiles = []
        if self.web_check.isChecked():
            profiles.append("web")
        if self.print_check.isChecked():
            profiles.append("print")
        if self.social_check.isChecked():
            profiles.append("social")
        if not profiles:
            QMessageBox.warning(self, "Validation", "Selectionne au moins un profil.")
            return

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.on_operation_started()
        self.on_job_event(f"[Export] Lancement du job pour projet ID {project_id}.")

        min_rating = int(self.min_rating_combo.currentData() or 0)
        worker = JobWorker(
            self.export_service.run_export,
            project_id=project_id,
            destination_dir=destination,
            profiles=profiles,
            min_rating=min_rating,
            create_zip=self.zip_check.isChecked(),
            create_report=self.report_check.isChecked(),
            create_contact_sheet=self.contact_sheet_check.isChecked(),
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
            self.on_job_event("[Export] Annulation demandee par l'utilisateur.")

    def _on_export_progress(self, done: int, total: int, detail: str) -> None:
        safe_total = max(1, int(total))
        self.progress_bar.setMaximum(safe_total)
        self.progress_bar.setValue(max(0, min(int(done), safe_total)))

    def _on_export_result(self, batch) -> None:
        total_exported = 0
        total_failed = 0
        for item in batch.profiles:
            self.log_text.appendPlainText(
                f"Export {item.profile}: {item.status} | exported={item.exported}, "
                f"failed={item.failed} | out={item.output_dir}"
            )
            total_exported += int(item.exported)
            total_failed += int(item.failed)
            if item.message:
                self.log_text.appendPlainText(item.message)
        if batch.report_path is not None:
            self.log_text.appendPlainText(f"Rapport export: {batch.report_path}")
        if batch.zip_path is not None:
            self.log_text.appendPlainText(f"ZIP livraison: {batch.zip_path}")
        if batch.contact_sheet_path is not None:
            self.log_text.appendPlainText(f"Planche contact PDF: {batch.contact_sheet_path}")
        self.on_job_event(
            f"[Export] termine | exported={total_exported}, failed={total_failed}, profils={len(batch.profiles)}"
        )

    def _on_export_error(self, message: str) -> None:
        self.on_job_event(f"[Export] Erreur: {message}")
        QMessageBox.critical(self, "Erreur export", message)

    def _on_export_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.on_operation_ended()
        self._job_worker = None
        self._job_thread = None
        self.on_job_event("[Export] Job termine.")


class PresetTab(QWidget):
    def __init__(self, preset_service: PresetService, on_data_changed) -> None:
        super().__init__()
        self.preset_service = preset_service
        self.on_data_changed = on_data_changed
        self.current_preset_id: int | None = None
        self.profile_widgets: dict[str, dict[str, object]] = {}

        layout = QHBoxLayout(self)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Nom", "Utilisation"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table, 1)

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
        to_json_btn = QPushButton("Formulaire -> JSON")
        to_json_btn.clicked.connect(self._sync_json_from_form)
        to_form_btn = QPushButton("JSON -> Formulaire")
        to_form_btn.clicked.connect(self._sync_form_from_json)
        sync_row.addWidget(to_json_btn)
        sync_row.addWidget(to_form_btn)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("Nouveau")
        new_btn.clicked.connect(self._reset_form)
        save_btn = QPushButton("Enregistrer")
        save_btn.clicked.connect(self._save)
        delete_btn = QPushButton("Supprimer")
        delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(delete_btn)

        versions_box = QGroupBox("Versions")
        versions_layout = QHBoxLayout(versions_box)
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(240)
        rollback_btn = QPushButton("Rollback")
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
        current_id = self.current_preset_id
        self.table.setRowCount(len(presets))
        selected_row = -1
        for row, preset in enumerate(presets):
            name_item = QTableWidgetItem(preset.name)
            name_item.setData(Qt.ItemDataRole.UserRole, preset.id)
            self.table.setItem(row, 0, name_item)
            linked = self._linked_projects_summary(preset)
            linked_item = QTableWidgetItem(linked)
            linked_item.setToolTip(self._linked_projects_tooltip(preset))
            self.table.setItem(row, 1, linked_item)
            if current_id is not None and preset.id == current_id:
                selected_row = row
        self.table.resizeColumnsToContents()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self.table.rowCount() > 0 and self.current_preset_id is None:
            self.table.selectRow(0)

    def _on_select(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        preset_id = item.data(Qt.ItemDataRole.UserRole)
        if preset_id is None:
            return
        preset = self.preset_service.get_preset(int(preset_id))
        if preset is None:
            return
        self.current_preset_id = preset.id
        self.name_edit.setText(preset.name)
        self.associated_projects_label.setText(self._linked_projects_tooltip(preset))
        self._set_config_from_json_text(preset.config_json)
        self._refresh_versions()

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
            self.version_combo.addItem(label, version.id)

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
        layout = QVBoxLayout(tab)

        naming_box = QGroupBox("Nommage")
        naming_form = QFormLayout(naming_box)
        self.naming_pattern_edit = QLineEdit()
        self.naming_pattern_edit.setPlaceholderText("{project}_{date}_{seq:04d}")
        self.naming_pattern_edit.setToolTip(
            "Pattern de nom de fichier.\nVariables: {project}, {date}, {seq}."
        )
        naming_form.addRow("Pattern", self.naming_pattern_edit)
        layout.addWidget(naming_box)

        import_box = QGroupBox("Import")
        import_form = QFormLayout(import_box)
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
        backup_btn = QPushButton("Parcourir")
        backup_btn.clicked.connect(self._pick_backup_path)
        backup_row.addWidget(self.import_backup_path_edit)
        backup_row.addWidget(backup_btn)
        self.import_backup_browse_btn = backup_btn

        import_form.addRow("", self.import_verify_checksum_check)
        import_form.addRow("", self.import_dual_backup_check)
        import_form.addRow("Backup path", backup_row)
        layout.addWidget(import_box)

        export_box = QGroupBox("Export profils")
        export_grid = QGridLayout(export_box)
        for col, profile in enumerate(("web", "print", "social")):
            card = self._build_profile_card(profile)
            export_grid.addWidget(card, 0, col)
        layout.addWidget(export_box)

        watermark_box = QGroupBox("Watermark")
        watermark_form = QFormLayout(watermark_box)
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
        layout.addWidget(watermark_box)

        delivery_box = QGroupBox("Livraison")
        delivery_form = QFormLayout(delivery_box)
        self.delivery_zip_check = QCheckBox("ZIP livraison")
        self.delivery_zip_check.setToolTip("Cree une archive ZIP prete a envoyer au client.")
        self.delivery_report_check = QCheckBox("Rapport .txt")
        self.delivery_report_check.setToolTip("Genere un rapport texte des exports.")
        self.delivery_contact_sheet_check = QCheckBox("Planche contact PDF")
        self.delivery_contact_sheet_check.setToolTip("Genere une planche contact PDF des images exportees.")
        delivery_form.addRow("", self.delivery_zip_check)
        delivery_form.addRow("", self.delivery_report_check)
        delivery_form.addRow("", self.delivery_contact_sheet_check)

        help_label = QLabel(
            "Astuce: commence en mode Formulaire, puis verifie le rendu final dans l'onglet JSON."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        layout.addWidget(delivery_box)
        layout.addStretch(1)
        return tab

    def _build_profile_card(self, profile: str) -> QGroupBox:
        box = QGroupBox(profile)
        form = QFormLayout(box)
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

        form.addRow("Format", format_combo)
        form.addRow("Max width", width_spin)
        form.addRow("Quality", quality_spin)
        form.addRow("Subdir", subdir_edit)
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
            idx = format_combo.findText(fmt, Qt.MatchFlag.MatchFixedString)
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
    def __init__(self, storage_service: StorageService, is_busy: Callable[[], bool], on_migration_completed) -> None:
        super().__init__()
        self.storage_service = storage_service
        self.is_busy = is_busy
        self.on_migration_completed = on_migration_completed

        layout = QVBoxLayout(self)

        box = QGroupBox("Stockage global")
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.storage_root_edit = QLineEdit()
        browse_btn = QPushButton("Parcourir")
        browse_btn.clicked.connect(self._pick_storage_root)
        row.addWidget(self.storage_root_edit)
        row.addWidget(browse_btn)

        self.apply_btn = QPushButton("Appliquer")
        self.apply_btn.clicked.connect(self._apply_storage_root)

        self.status_label = QLabel("Statut migration: idle")
        self.error_label = QLabel("Erreur: -")
        self.active_data_dir_label = QLabel("Data active: -")

        form.addRow("Dossier global de stockage", row)
        form.addRow("", self.apply_btn)
        form.addRow("", self.status_label)
        form.addRow("", self.error_label)
        form.addRow("", self.active_data_dir_label)

        layout.addWidget(box)
        layout.addStretch(1)

    def refresh_data(self) -> None:
        settings = self.storage_service.get_settings()
        self.storage_root_edit.setText(settings.get("storage_root", ""))
        self.status_label.setText(f"Statut migration: {settings.get('last_migration_status', 'idle')}")
        self.error_label.setText(f"Erreur: {settings.get('last_migration_error') or '-'}")
        self.active_data_dir_label.setText(f"Data active: {settings.get('active_data_dir', '-')}")

    def _pick_storage_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choisir dossier global de stockage")
        if directory:
            self.storage_root_edit.setText(directory)

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
