"""Small presentation helpers shared across workflow pages."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget


class Disclosure(QWidget):
    """Keyboard-accessible advanced content with an explicit expanded state."""

    changed = Signal()

    def __init__(self, title: str, *, expanded: bool = False) -> None:
        super().__init__()
        self.title = title
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.toggle = QPushButton()
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setAccessibleName(title)
        self.toggle.setObjectName("Disclosure")
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(8, 0, 8, 8)
        self.body.setSpacing(10)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.content.setVisible(expanded)
        self.toggle.setText(("▾ " if expanded else "▸ ") + self.title)
        self.toggle.setChecked(expanded)
        self.updateGeometry()
        self.changed.emit()


def primary(button: QPushButton) -> None:
    button.setObjectName("Primary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
