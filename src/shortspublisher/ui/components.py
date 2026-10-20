"""Small presentation helpers shared across workflow pages."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)


class FlowLayout(QLayout):
    """Left-to-right row that wraps onto further lines when the window is narrow."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 10) -> None:
        super().__init__(parent)
        self.items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt override
        self.items.append(item)

    def insertWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802 - box-layout API
        self.addChildWidget(widget)
        self.items.insert(index, QWidgetItem(widget))
        self.invalidate()

    def count(self) -> int:
        return len(self.items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self.arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self.arrange(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self.items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def arrange(self, rect: QRect, *, move: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self.items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, line = rect.x(), y + line + self.spacing(), 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y()


def day_row(days: list[QWidget]) -> QWidget:
    """Weekday checkboxes on one wrapping line instead of seven stacked rows."""
    row = QWidget()
    row.setObjectName("ActionRow")  # transparent over cards and dialogs
    flow = FlowLayout(row, spacing=14)
    for day in days:
        flow.addWidget(day)
    return row


def _plain(widget: QWidget | None) -> bool:
    return isinstance(widget, QPushButton) and widget.objectName() not in ("Nav", "Disclosure")


def tidy_buttons(root: QWidget) -> None:
    """Show actions at their natural size instead of full-width bars.

    Runs of two or more adjacent buttons in a vertical layout become one wrapping row; a single
    button is left-aligned. Order, visibility and enabled state are unchanged."""
    for layout in [root.layout(), *root.findChildren(QLayout)]:
        if not isinstance(layout, QBoxLayout) or layout.direction() not in (
            QBoxLayout.Direction.TopToBottom,
            QBoxLayout.Direction.BottomToTop,
        ):
            continue
        index = 0
        while index < layout.count():
            run: list[QWidget] = []
            while index + len(run) < layout.count():
                item = layout.itemAt(index + len(run))
                widget = item.widget() if item else None
                if widget is None or not _plain(widget):
                    break
                run.append(widget)
            if len(run) >= 2:
                row = QWidget()
                row.setObjectName("ActionRow")
                flow = FlowLayout(row)
                for button in run:
                    layout.removeWidget(button)
                    flow.addWidget(button)
                layout.insertWidget(index, row)
            elif run:
                layout.setAlignment(run[0], Qt.AlignmentFlag.AlignLeft)
            index += 1


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
