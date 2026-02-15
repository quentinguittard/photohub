from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QPushButton,
)

class BentoCard(QFrame):
    """
    A unified container for the 'Bento' design system.
    Provides consistent styling (border radius, background, padding).
    """
    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BentoCard")
        
        # Main layout for the card
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(12)

        # Optional Title Header
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("BentoCardTitle")
            self.main_layout.addWidget(self.title_label)
            
        # Content placeholder (to be filled by consumer)
        self.content_area = QWidget()
        self.content_area.setObjectName("BentoCardContent")
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self.main_layout.addWidget(self.content_area, 1)

    def add_widget(self, widget: QWidget) -> None:
        """Helper to add a widget to the card's content area."""
        self.content_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        """Helper to add a layout to the card's content area."""
        self.content_layout.addLayout(layout)


class WorkflowStatusBar(QFrame):
    """
    'Fil Rouge' navigation bar.
    Shows the progression: Import -> Ingest -> Culling -> Edit -> Export.
    """
    step_clicked = Signal(str)  # Emits the key of the step clicked

    STEPS = [
        ("ingest", "1. Ingest"),
        ("culling", "2. Tri"),
        ("edit", "3. Edit"),
        ("export", "4. Export"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkflowStatusBar")
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(2)

        self._buttons: dict[str, QPushButton] = {}
        
        for key, label in self.STEPS:
            btn = QPushButton(label)
            btn.setObjectName("WorkflowStepButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self.step_clicked.emit(k))
            self.layout.addWidget(btn)
            self._buttons[key] = btn
            
        self.layout.addStretch(1)

    def set_active_step(self, step_key: str) -> None:
        """Highlights the active step."""
        # Normalize key if needed (e.g. 'dashboard' might not be in STEPS)
        if step_key not in self._buttons:
            # Uncheck all if not in workflow
            for btn in self._buttons.values():
                btn.setChecked(False)
                btn.setProperty("active", "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            return

        for key, btn in self._buttons.items():
            is_active = (key == step_key)
            btn.setChecked(is_active)
            btn.setProperty("active", "true" if is_active else "false")
            # Force style refresh
            btn.style().unpolish(btn)
            btn.style().polish(btn)
