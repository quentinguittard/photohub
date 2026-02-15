from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QDateEdit,
    QFormLayout,
    QMessageBox,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QToolButton,
)
# Use native PySide6 widgets as default
from PySide6.QtWidgets import (
    QPushButton as _NativePushButton,
    QLineEdit as _NativeLineEdit,
    QComboBox as _NativeComboBox,
    QCheckBox as _NativeCheckBox,
)

from ..services import ProjectService, PresetService, QualityChecklistError
from .components import BentoCard

# Try to use QFluentWidgets for a consistent look with the rest of the app.
try:
    from qfluentwidgets import (
        PushButton as QPushButton,
        LineEdit as QLineEdit,
        ComboBox as QComboBox,
        CheckBox as QCheckBox,
    )
except ImportError:
    QPushButton = _NativePushButton
    QLineEdit = _NativeLineEdit
    QComboBox = _NativeComboBox
    QCheckBox = _NativeCheckBox


def _create_btn(text: str, primary: bool = False) -> QPushButton:
    btn = QPushButton(text)
    if primary:
        btn.setProperty("isPrimaryButton", "true")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class ProjectItemWidget(QWidget):
    """
    Custom widget for project list items with tags and premium styling.
    """
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(6)
        
        # Project Name
        name_label = QLabel(project.name)
        name_label.setStyleSheet("font-weight: 700; font-size: 14px; color: #FFFFFF;")
        self.layout.addWidget(name_label)
        
        # Client Name
        client_name = project.client.name if project.client else "-"
        client_label = QLabel(client_name)
        client_label.setStyleSheet("font-size: 12px; color: #A1A1AA;")
        self.layout.addWidget(client_label)
        
        # Tags row
        tags_layout = QHBoxLayout()
        tags_layout.setSpacing(6)
        
        # Status Tag
        status_text = project.status.upper() if project.status else "UNKNOWN"
        # Determine color based on status
        colors = {
            "draft": ("#3B220B", "#F59E0B"),    # Amber
            "active": ("#064E3B", "#10B981"),   # Emerald
            "completed": ("#1E3A8A", "#60A5FA"), # Blue
            "archived": ("#2D2D2D", "#71717A"),  # Gray
        }
        bg, fg = colors.get(project.status.lower() if project.status else "", ("#1F2937", "#9CA3AF"))
        
        status_tag = self._create_tag(status_text, bg, fg)
        tags_layout.addWidget(status_tag)
        
        # Date Tag
        date_str = project.shoot_date.strftime("%d %b %Y") if project.shoot_date else "Pas de date"
        date_tag = self._create_tag(date_str, "#27272A", "#D4D4D8")
        tags_layout.addWidget(date_tag)
        
        tags_layout.addStretch()
        self.layout.addLayout(tags_layout)

    def _create_tag(self, text, bg, color):
        lbl = QLabel(text)
        lbl.setContentsMargins(8, 2, 8, 2)
        lbl.setStyleSheet(f"""
            background-color: {bg};
            color: {color};
            border-radius: 4px;
            font-size: 10px;
            font-weight: 800;
        """)
        return lbl


class ProjectsTab(QWidget):
    """
    Split view for Project Management:
    Left: Create / Edit form.
    Right: List of projects with search.
    """

    def __init__(
        self,
        project_service: ProjectService,
        preset_service: PresetService,
        on_data_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self.project_service = project_service
        self.preset_service = preset_service
        self.on_data_changed_callback = on_data_changed

        self.current_project_id: Optional[int] = None
        self._name_filter = ""

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(16)

        # --- LEFT PANEL (Details / Form) ---
        self.left_panel = QScrollArea()
        self.left_panel.setWidgetResizable(True)
        self.left_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.left_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.details_container = QWidget()
        self.details_layout = QVBoxLayout(self.details_container)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(20)

        self.left_panel.setWidget(self.details_container)

        # --- RIGHT PANEL (List) ---
        self.right_panel = QWidget()
        self.right_panel.setFixedWidth(400) # Widened from 320
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(10)

        # Search
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rechercher un projet...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.right_layout.addWidget(self.search_edit)

        # Project List
        self.project_list = QListWidget()
        self.project_list.setFrameShape(QFrame.Shape.NoFrame)
        self.project_list.setSpacing(4)
        self.project_list.setStyleSheet("""
            QListWidget { 
                background: transparent; 
                outline: none;
            }
            QListWidget::item {
                background: #1F1F1F;
                border: 1px solid #2D2D2D;
                border-radius: 8px;
                margin-bottom: 4px;
            }
            QListWidget::item:selected {
                background: #2D2D2D;
                border: 1px solid #3F3F3F;
            }
            QListWidget::item:hover {
                background: #252525;
            }
        """)
        self.project_list.itemClicked.connect(self._on_list_item_clicked)
        self.right_layout.addWidget(self.project_list)

        # Add panels to main layout
        self.main_layout.addWidget(self.left_panel)
        self.main_layout.addWidget(self.right_panel)

        # Build form UI
        self._init_form_ui()

        # Initial state: creation mode
        self._set_mode_create()

    # ------------------------------------------------------------------ #
    #  UI INIT                                                            #
    # ------------------------------------------------------------------ #

    def _init_form_ui(self) -> None:
        # ── Header Row (Title + Action Buttons) ──
        header_row = QHBoxLayout()
        self.header_label = QLabel("Nouveau Projet")
        self.header_label.setStyleSheet("font-size: 20px; font-weight: 700; color: #E8E8E8;")

        self.btn_create = _create_btn("Creer le Projet", primary=True)
        self.btn_create.setMinimumHeight(32)
        self.btn_create.clicked.connect(self._create_project)

        self.btn_save = _create_btn("Enregistrer")
        self.btn_save.setMinimumHeight(32)
        self.btn_save.clicked.connect(self._save_project_changes)

        self.btn_new_mode = _create_btn("Nouveau Projet")
        self.btn_new_mode.clicked.connect(self._on_click_new_project)

        self.btn_delete = _create_btn("Supprimer")
        self.btn_delete.setStyleSheet("color: #EF4444; border-color: #EF4444;")
        self.btn_delete.clicked.connect(self._delete_project)

        header_row.addWidget(self.header_label)
        header_row.addStretch()
        header_row.addWidget(self.btn_delete)
        header_row.addWidget(self.btn_new_mode)
        header_row.addWidget(self.btn_save)
        header_row.addWidget(self.btn_create)

        self.details_layout.addLayout(header_row)

        # ── 1. General Info Card ──
        self.info_card = BentoCard("Informations Generales")
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.info_card.content_layout.addLayout(form)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Nom du projet")

        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Client (optionnel)")

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())

        self.preset_combo = QComboBox()

        # Path selection
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.browse_btn = QToolButton()
        self.browse_btn.setText("...")
        self.browse_btn.clicked.connect(self._browse_path)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)

        form.addRow("Nom", self.name_edit)
        form.addRow("Client", self.client_edit)
        form.addRow("Date", self.date_edit)
        form.addRow("Preset", self.preset_combo)
        form.addRow("Dossier", path_row)

        self.details_layout.addWidget(self.info_card)

        # ── 2. Status Card (Edit mode only) ──
        self.actions_card = BentoCard("Statut")
        stat_row = QHBoxLayout()
        self.status_combo = QComboBox()
        # self.btn_update_status removed, handled by global save
        stat_row.addWidget(QLabel("Statut:"))
        stat_row.addWidget(self.status_combo, 1)
        self.actions_card.content_layout.addLayout(stat_row)

        self.details_layout.addWidget(self.actions_card)

        # ── 3. Quality Checklist Card (Edit mode only) ──
        self.quality_card = BentoCard("Qualite & Validation")

        self.q_enabled = QCheckBox("Activer checklist")
        self.q_min_rating = QCheckBox("Exiger note > 0")
        self.q_metadata = QCheckBox("Exiger Auteur/Copyright")
        self.q_watermark = QCheckBox("Exiger Watermark")

        self.quality_card.content_layout.addWidget(self.q_enabled)
        self.quality_card.content_layout.addWidget(self.q_min_rating)
        self.quality_card.content_layout.addWidget(self.q_metadata)
        self.quality_card.content_layout.addWidget(self.q_watermark)

        q_btns = QHBoxLayout()
        # self.btn_save_quality removed, handled by global save
        self.btn_validate_quality = _create_btn("Lancer Validation", primary=True)
        self.btn_validate_quality.clicked.connect(self._validate_quality)
        q_btns.addWidget(self.btn_validate_quality)
        self.quality_card.content_layout.addLayout(q_btns)

        self.lbl_quality_status = QLabel("Etat: -")
        self.lbl_quality_status.setStyleSheet("color: #7A7A7A;")
        self.quality_card.content_layout.addWidget(self.lbl_quality_status)

        self.details_layout.addWidget(self.quality_card)

        # Stretch at bottom
        self.details_layout.addStretch()

    # ------------------------------------------------------------------ #
    #  PUBLIC API                                                         #
    # ------------------------------------------------------------------ #

    def on_data_changed(self) -> None:
        """Called when parent wants us to refresh."""
        self.refresh_list()
        if self.current_project_id:
            self._load_project_details(self.current_project_id)

    def refresh_data(self) -> None:
        """Alias for refresh_list to match MainWindow expectation."""
        self.refresh_list()

    def select_project_by_id(self, project_id: int) -> None:
        """Select a project programmatically."""
        self.current_project_id = int(project_id)
        self._load_project_details(self.current_project_id)
        for i in range(self.project_list.count()):
            it = self.project_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == self.current_project_id:
                self.project_list.setCurrentItem(it)
                break

    # ------------------------------------------------------------------ #
    #  LIST                                                               #
    # ------------------------------------------------------------------ #

    def refresh_list(self) -> None:
        projects = self.project_service.list_projects()

        term = self._name_filter.lower().strip()
        filtered = []
        for p in projects:
            text = f"{p.name} {p.client.name if p.client else ''} {p.status}".lower()
            if not term or term in text:
                filtered.append(p)

        self.project_list.clear()

        for p in filtered:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            
            # Create custom widget
            widget = ProjectItemWidget(p)
            item.setSizeHint(widget.sizeHint())
            
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, widget)

        # Restore selection
        if self.current_project_id:
            for i in range(self.project_list.count()):
                it = self.project_list.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == self.current_project_id:
                    self.project_list.setCurrentItem(it)
                    break

    def _on_search_changed(self, text: str) -> None:
        self._name_filter = text
        self.refresh_list()

    def set_name_filter(self, text: str) -> None:
        """Called externally (e.g. from MainWindow search bar)."""
        self._name_filter = text
        self.search_edit.setText(text)
        self.refresh_list()

    def _on_list_item_clicked(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        self._load_project_details(pid)

    # ------------------------------------------------------------------ #
    #  MODES                                                              #
    # ------------------------------------------------------------------ #

    def _on_click_new_project(self) -> None:
        self.project_list.clearSelection()
        self.current_project_id = None
        self._set_mode_create()

    def _set_mode_create(self) -> None:
        self.header_label.setText("Nouveau Projet")
        self.details_container.setVisible(True)

        # Clear fields
        self.name_edit.clear()
        self.client_edit.clear()
        self.date_edit.setDate(QDate.currentDate())

        # Default path from settings
        self.path_edit.setText(str(self.project_service.paths.projects_dir))
        self.path_edit.setEnabled(True)
        self.browse_btn.setEnabled(True)

        # Populate Preset Combo
        self._populate_presets(self.preset_combo)

        # Hide Edit-specific cards
        self.actions_card.setVisible(False)
        self.quality_card.setVisible(False)

        # Buttons
        self.btn_create.setVisible(True)
        self.btn_save.setVisible(False)
        self.btn_new_mode.setVisible(False)
        self.btn_delete.setVisible(False)

        # Enable fields
        self.name_edit.setEnabled(True)
        self.client_edit.setEnabled(True)
        self.date_edit.setEnabled(True)
        self.preset_combo.setEnabled(True)

    def _load_project_details(self, pid: int) -> None:
        self.current_project_id = pid
        project = self.project_service.get_project(pid)
        if not project:
            self._set_mode_create()
            return

        self.header_label.setText(f"Projet: {project.name}")

        # Set fields
        self.name_edit.setText(project.name)
        self.client_edit.setText(project.client.name if project.client else "")
        self.date_edit.setDate(project.shoot_date)

        # Path: read-only in edit mode
        self.path_edit.setText(project.root_path)
        self.path_edit.setEnabled(False)
        self.browse_btn.setEnabled(False)

        # Preset: populate and select current
        self._populate_presets(self.preset_combo)
        self.preset_combo.setEnabled(True)
        pidx = self.preset_combo.findData(project.preset_id)
        if pidx >= 0:
            self.preset_combo.setCurrentIndex(pidx)
        else:
            self.preset_combo.setCurrentIndex(0)

        # Show Edit Cards
        self.actions_card.setVisible(True)
        self.quality_card.setVisible(True)

        # Buttons
        self.btn_create.setVisible(False)
        self.btn_save.setVisible(True)
        self.btn_new_mode.setVisible(True)
        self.btn_delete.setVisible(True)

        # Populate Status
        self.status_combo.clear()
        for code, label in self.project_service.list_status_choices():
            self.status_combo.addItem(label, userData=code)
        idx = self.status_combo.findData(project.status)
        if idx >= 0:
            self.status_combo.setCurrentIndex(idx)

        # Load Quality
        self._refresh_quality(pid)

    # ------------------------------------------------------------------ #
    #  HELPERS                                                            #
    # ------------------------------------------------------------------ #

    def _populate_presets(self, combo: QComboBox) -> None:
        combo.clear()
        combo.addItem("Aucun preset", userData=None)
        presets = self.preset_service.list_presets()
        for p in presets:
            combo.addItem(p.name, userData=p.id)

    def _browse_path(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choisir dossier")
        if d:
            self.path_edit.setText(d)

    # ------------------------------------------------------------------ #
    #  ACTIONS                                                            #
    # ------------------------------------------------------------------ #

    def _create_project(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Erreur", "Le nom est obligatoire.")
            return

        try:
            custom_path = self.path_edit.text().strip() or None
            self.project_service.create_project(
                name=name,
                shoot_date=self.date_edit.date().toPython(),
                preset_id=self.preset_combo.currentData(),
                custom_root_path=custom_path,
                client_name=self.client_edit.text().strip() or None,
            )
            self.on_data_changed_callback()
            self._set_mode_create()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _save_project_changes(self) -> None:
        if not self.current_project_id:
            return

        name = self.name_edit.text().strip()
        client = self.client_edit.text().strip() or None
        date_ = self.date_edit.date().toPython()
        preset_id = self.preset_combo.currentData()
        status = self.status_combo.currentData()

        # Quality config
        quality_config = {
            "enabled": self.q_enabled.isChecked(),
            "rules": {
                "min_rating_non_zero": {"enabled": self.q_min_rating.isChecked()},
                "metadata_author_copyright": {"enabled": self.q_metadata.isChecked()},
                "watermark_enabled": {"enabled": self.q_watermark.isChecked()},
            },
        }

        try:
            # 1. Update core project (including status)
            self.project_service.update_project(
                project_id=self.current_project_id,
                name=name,
                client_name=client,
                shoot_date=date_,
                preset_id=preset_id,
                status=str(status) if status else None
            )
            
            # 2. Update quality config
            snap = self.project_service.update_quality_check(self.current_project_id, quality_config)
            self._display_quality_snap(snap)
            
            self.on_data_changed_callback()
            QMessageBox.information(self, "Succes", "Modifications enregistrees.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    # _update_status and _save_quality removed, moved to _save_project_changes

    def _validate_quality(self) -> None:
        if not self.current_project_id:
            return
        try:
            snap = self.project_service.validate_quality_check(self.current_project_id)
            self._display_quality_snap(snap)
            QMessageBox.information(self, "Succes", "Qualite validee.")
        except QualityChecklistError as e:
            self._refresh_quality(self.current_project_id)
            QMessageBox.warning(self, "Echec Validation", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _refresh_quality(self, pid: int) -> None:
        try:
            snap = self.project_service.get_quality_check(pid, export_min_rating=1)
            self._display_quality_snap(snap)
        except Exception:
            pass

    def _display_quality_snap(self, snap: dict) -> None:
        config = snap.get("config", {})
        rules = config.get("rules", {})
        self.q_enabled.setChecked(bool(config.get("enabled", True)))
        self.q_min_rating.setChecked(bool(rules.get("min_rating_non_zero", {}).get("enabled", True)))
        self.q_metadata.setChecked(bool(rules.get("metadata_author_copyright", {}).get("enabled", True)))
        self.q_watermark.setChecked(bool(rules.get("watermark_enabled", {}).get("enabled", False)))

        status = snap.get("status", "unknown")
        issues = len(snap.get("issues", []) or [])
        self.lbl_quality_status.setText(f"Etat: {status} | {issues} Erreurs")
        if status == "validated":
            self.lbl_quality_status.setStyleSheet("color: #10B981; font-weight: bold;")
        else:
            self.lbl_quality_status.setStyleSheet("color: #F59E0B;")

    def _delete_project(self) -> None:
        if not self.current_project_id:
            return
        r = QMessageBox.question(
            self,
            "Confirmer suppression",
            "Voulez-vous vraiment supprimer ce projet (fichiers inclus)?",
        )
        if r == QMessageBox.StandardButton.Yes:
            try:
                self.project_service.delete_project(self.current_project_id, delete_files=True)
                self.on_data_changed_callback()
                self._set_mode_create()
                QMessageBox.information(self, "Supprime", "Le projet a ete supprime.")
            except Exception as e:
                QMessageBox.critical(self, "Erreur", str(e))
