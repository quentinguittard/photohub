from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QStyle,
)

from ..services import ProjectService, StorageService, JobQueueService
from .components import BentoCard


class DashboardTab(QWidget):
    """
    Refined Dashboard using a strict Bento Box layout.
    Focuses on KPIs, Quick Actions, and Recent Activity.
    """

    def __init__(
        self,
        project_service: ProjectService,
        get_active_jobs: Callable[[], int],
        storage_service: StorageService | None = None,
        job_queue_service: JobQueueService | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.get_active_jobs = get_active_jobs
        self.storage_service = storage_service
        self.job_queue_service = job_queue_service
        self.on_navigate = on_navigate

        # Main Layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(20)

        # Header
        self._setup_header()

        # Scrollable Content Area (for the bento grid)
        # We hide the scrollbar as per user preference (minimal/hidden)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        content_widget = QWidget()
        self.grid = QGridLayout(content_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        # Define grid columns: 4 equal columns
        for i in range(4):
            self.grid.setColumnStretch(i, 1)

        scroll.setWidget(content_widget)
        self.main_layout.addWidget(scroll, 1)

        # Initialize Grid Items
        self._init_kpi_cards()
        self._init_quick_actions()
        self._init_recent_projects()
        
        # Timer for auto-refresh active jobs if any
        self.timer = self.startTimer(2000) # Refresh status every 2s (only cheap calls)

    def timerEvent(self, event):
        # We only really need to refresh jobs often
        self._refresh_jobs_only()

    def _setup_header(self) -> None:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Greetings
        now = datetime.now()
        hour = now.hour
        greeting = "Bonjour" if 6 <= hour < 18 else "Bonsoir"
        
        title = QLabel(f"{greeting}, Studio.")
        title.setObjectName("PageTitle")
        title.setStyleSheet("font-size: 28px; font-weight: 800; color: #E8E8E8;")
        
        date_label = QLabel(now.strftime("%A %d %B %Y").capitalize())
        date_label.setStyleSheet("color: #7A7A7A; font-size: 14px; font-weight: 500;")
        
        text_layout = QVBoxLayout()
        text_layout.addWidget(title)
        text_layout.addWidget(date_label)
        layout.addLayout(text_layout)
        layout.addStretch()
        
        self.main_layout.addWidget(header)

    def _init_kpi_cards(self) -> None:
        # Row 0
        self.kpi_total = self._create_kpi_card("Total Projets", "0", 0, 0)
        self.kpi_import = self._create_kpi_card("A Importer", "0", 0, 1) # active state?
        self.kpi_jobs = self._create_kpi_card("Taches en cours", "0", 0, 2)
        # Replaced 'A Livrer' with 'Stockage' to provide more utility
        self.kpi_storage = self._create_kpi_card("Stockage", "0%", 0, 3)

    def _create_kpi_card(self, title: str, value: str, row: int, col: int) -> QLabel:
        card = BentoCard()
        # Custom layout for KPI to make it pop
        layout = QVBoxLayout(card.content_area)
        layout.setSpacing(4)
        
        t_label = QLabel(title)
        t_label.setObjectName("BentoCardTitle") # Reuse style
        
        v_label = QLabel(value)
        v_label.setStyleSheet("font-size: 32px; font-weight: 700; color: #E8E8E8;")
        v_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        layout.addWidget(t_label)
        layout.addWidget(v_label)
        layout.addStretch()
        
        self.grid.addWidget(card, row, col)
        return v_label

    def _init_quick_actions(self) -> None:
        # Row 1: Large actionable buttons
        container = BentoCard("Actions Rapides")
        layout = QHBoxLayout(container.content_area)
        layout.setSpacing(16)

        # New Project
        btn_new = self._create_action_btn("Nouveau Projet", "Create new session", QStyle.StandardPixmap.SP_FileIcon)
        btn_new.clicked.connect(lambda: self._trigger_nav("projects")) # Ideally opens 'New Project' dialog
        
        # Ingest
        btn_ingest = self._create_action_btn("Ingest", "Import from card", QStyle.StandardPixmap.SP_ArrowDown)
        btn_ingest.clicked.connect(lambda: self._trigger_nav("ingest"))
        
        # Export
        btn_export = self._create_action_btn("Export", "Deliver to client", QStyle.StandardPixmap.SP_ArrowForward)
        btn_export.clicked.connect(lambda: self._trigger_nav("export"))
        
        layout.addWidget(btn_new)
        layout.addWidget(btn_ingest)
        layout.addWidget(btn_export)
        
        self.grid.addWidget(container, 1, 0, 1, 4) # Full width

    def _create_action_btn(self, title: str, subtitle: str, icon_pix: QStyle.StandardPixmap) -> QPushButton:
        btn = QPushButton()
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(80)
        
        # We use a custom layout inside the button or just basic text if styling permits
        # For simplicity and styling robustness, we'll set text and use stylesheet
        # But we want title + subtitle.
        
        # Let's use a layout inside the button? No, QPushButton doesn't easily support layouts.
        # We'll use a QFrame that acts like a button? 
        # Actually, let's keep it simple: "Title\nSubtitle" and allow properties.
        btn.setText(f"{title}\n{subtitle}")
        icon = self.style().standardIcon(icon_pix)
        btn.setIcon(icon)
        btn.setIconSize(QSize(24, 24))
        
        # Style
        btn.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 16px;
                border: 1px solid #3A3A3A;
                border-radius: 12px;
                background-color: #242424;
                color: #E8E8E8;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2D2D2D;
                border-color: #545454;
            }
            QPushButton:pressed {
                background-color: #1A1A1A;
            }
        """)
        return btn

    def _init_recent_projects(self) -> None:
        # Row 2: Recent Projects List
        # This will separate the list into a card
        self.recent_container = BentoCard("Projets Recents")
        self.recent_layout = QVBoxLayout(self.recent_container.content_area)
        self.recent_layout.setSpacing(8)
        self.recent_layout.setContentsMargins(0, 10, 0, 0)
        
        self.grid.addWidget(self.recent_container, 2, 0, 1, 2) # Half width left
        
        
        # Row 2 Right: Active Job Monitor (Replacing Notifications for utility)
        self.jobs_container = BentoCard("Activite en cours")
        self.jobs_layout = QVBoxLayout(self.jobs_container.content_area)
        
        # Placeholder
        lbl = QLabel("Aucune tache en cours.")
        lbl.setStyleSheet("color: #7A7A7A; font-style: italic;")
        self.jobs_layout.addWidget(lbl)
        self.jobs_layout.addStretch()
        
        self.grid.addWidget(self.jobs_container, 2, 2, 1, 2) # Half width right

    def refresh_data(self) -> None:
        projects = self.project_service.list_projects() or []
        total = len(projects)
        to_import = sum(1 for p in projects if p.status == "a_importer")
        ready = sum(1 for p in projects if p.status == "pret_a_livrer")
        jobs = self.get_active_jobs()
        
        self.kpi_total.setText(str(total))
        self.kpi_import.setText(str(to_import))
        self.kpi_jobs.setText(str(jobs))
        
        if self.storage_service:
            usage = self.storage_service.get_storage_usage()
            percent = usage.get("percent_used", 0)
            free_gb = usage.get("free_gb", 0)
            self.kpi_storage.setText(f"{percent}%")
            self.kpi_storage.setToolTip(f"{free_gb} GB Libres")
            # Dynamic color for storage warning
            if percent > 90:
                self.kpi_storage.setStyleSheet("font-size: 32px; font-weight: 700; color: #EF4444;") # Red
            elif percent > 75:
                self.kpi_storage.setStyleSheet("font-size: 32px; font-weight: 700; color: #F59E0B;") # Orange
            else:
                 self.kpi_storage.setStyleSheet("font-size: 32px; font-weight: 700; color: #E8E8E8;")
        else:
             self.kpi_storage.setText("-")
        
        # Update Recent
        self._update_recent_list(projects[:5])
        
        # Update Jobs
        self._refresh_jobs_only()

    def _refresh_jobs_only(self) -> None:
        if not self.job_queue_service:
             return
             
        active_jobs = self.job_queue_service.list_jobs(statuses=("running",), limit=5)
        
        # Clear existing layout
        while self.jobs_layout.count():
            item = self.jobs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        if not active_jobs:
            lbl = QLabel("Aucune tache en cours.")
            lbl.setStyleSheet("color: #7A7A7A; font-style: italic;")
            self.jobs_layout.addWidget(lbl)
            self.jobs_layout.addStretch()
            return
            
        for job in active_jobs:
            row = self._create_job_row(job)
            self.jobs_layout.addWidget(row)
        self.jobs_layout.addStretch()

    def _create_job_row(self, job) -> QWidget:
        row = QFrame()
        row.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        
        # Header: Type + Status
        header = QHBoxLayout()
        type_lbl = QLabel(job.job_type.upper())
        type_lbl.setStyleSheet("font-weight: 700; color: #E8E8E8; font-size: 11px;")
        
        status_lbl = QLabel("Waiting..." if job.status == "retry_waiting" else "Working")
        status_lbl.setStyleSheet("color: #B2B2B2; font-size: 11px;")
        
        header.addWidget(type_lbl)
        header.addStretch()
        header.addWidget(status_lbl)
        
        # Progress Bar
        from PySide6.QtWidgets import QProgressBar
        pbar = QProgressBar()
        pbar.setFixedHeight(6)
        pbar.setTextVisible(False)
        pbar.setStyleSheet("""
            QProgressBar {
                border: none;
                background: #3A3A3A;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: #3B82F6;
                border-radius: 3px;
            }
        """)
        if job.progress_total > 0:
            pbar.setMaximum(job.progress_total)
            pbar.setValue(job.progress_done)
        else:
            pbar.setRange(0, 0) # Indeterminate
            
        layout.addLayout(header)
        layout.addWidget(pbar)
        
        return row

    def _update_recent_list(self, projects: list) -> None:
        # Clear existing
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not projects:
            lbl = QLabel("Aucun projet recent.")
            lbl.setStyleSheet("color: #7A7A7A;")
            self.recent_layout.addWidget(lbl)
            return

        for p in projects:
            row = self._create_project_row(p)
            self.recent_layout.addWidget(row)
        
        self.recent_layout.addStretch()

    def _create_project_row(self, project) -> QWidget:
        # A sleek row: [Status Color Info] [Name] [Client] [Date] [Action >]
        row = QFrame()
        row.setStyleSheet("""
            QFrame {
                background-color: transparent;
                border-radius: 8px;
            }
            QFrame:hover {
                background-color: #2D2D2D;
            }
        """)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        # Make the whole row clickable? Ideally yes, but QFrame doesn't have clicked signal easily without event filter.
        # We'll rely on a button.
        
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        
        # Status Dot
        dot = QLabel("●")
        status_color = "#7A7A7A"
        if project.status == "pret_a_livrer": status_color = "#10B981" # Green
        elif project.status == "en_cours": status_color = "#3B82F6" # Blue
        elif project.status == "a_importer": status_color = "#F59E0B" # Orange
        
        dot.setStyleSheet(f"color: {status_color}; font-size: 14px;")
        
        name = QLabel(project.name)
        name.setStyleSheet("font-weight: 600; color: #E8E8E8;")
        
        client = QLabel(project.client.name if project.client else "-")
        client.setStyleSheet("color: #B2B2B2;")
        
        layout.addWidget(dot)
        layout.addWidget(name, 2)
        layout.addWidget(client, 1)
        layout.addStretch()
        
        # Open Button
        # We can implement a "Go to project" logic here
        
        return row

    def _trigger_nav(self, route: str) -> None:
        if self.on_navigate:
            self.on_navigate(route)
