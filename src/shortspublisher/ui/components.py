"""Small presentation helpers shared across workflow pages."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from .icons import PLATFORM_NAMES, icon, image, logo, pixmap


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
    return isinstance(widget, QPushButton) and widget.objectName() not in ("Nav", "Ghost")


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


class Section(QFrame):
    """A titled group that is always open: nothing on a page hides behind a toggle."""

    changed = Signal()

    def __init__(self, title: str, detail: str = "") -> None:
        super().__init__()
        self.title = title
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        self.outer = layout
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.heading = QLabel(title)
        self.heading.setObjectName("Subheading")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setVisible(bool(title))
        layout.addWidget(self.heading)
        self.detail = muted(detail)
        self.detail.setVisible(bool(detail))
        layout.addWidget(self.detail)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        layout.addWidget(self.content)


def muted(value: str, style: str = "Muted") -> QLabel:
    label = QLabel(value)
    label.setObjectName(style)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def restyle(widget: QWidget, name: str) -> None:
    """Change a widget's style role after it has been shown."""
    if widget.objectName() != name:
        widget.setObjectName(name)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def pill(value: str = "", tone: str = "Neutral") -> QLabel:
    """A compact status chip; tone is Good, Warn, Bad, Info or Neutral."""
    label = QLabel(value)
    label.setObjectName(tone)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


def card(margins: int = 20, spacing: int = 12, name: str = "Card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return frame, layout


def platform_top(platform: str, subtitle: str, status: QLabel) -> QHBoxLayout:
    """Logo, platform name and a status pill: the first line of every platform card."""
    top = QHBoxLayout()
    top.setSpacing(14)
    top.addWidget(image(logo(platform, 40)))
    names = QVBoxLayout()
    names.setSpacing(2)
    names.addWidget(muted(PLATFORM_NAMES[platform], "Heading"))
    names.addWidget(muted(subtitle, "Subtle"))
    top.addLayout(names, 1)
    top.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
    return top


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def icon_button(value: str, glyph: str = "", *, role: str = "", tip: str = "") -> QPushButton:
    """A button with an optional line icon; role is Primary, Danger or Ghost."""
    result = QPushButton(value)
    if glyph:
        result.setIcon(
            icon(
                glyph,
                {"Primary": "#ffffff", "Danger": "#f07070", "Ghost": "#a8c6ff"}.get(
                    role, "#c9cdd6"
                ),
            )
        )
        result.setIconSize(QSize(16, 16))
    if role:
        result.setObjectName(role)
        result.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        result.setToolTip(tip)
    return result


def row(*widgets: QWidget, spacing: int = 10, stretch: bool = True) -> QWidget:
    """Widgets side by side on one transparent line, left-aligned."""
    holder = QWidget()
    holder.setObjectName("ActionRow")
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    return holder


def flow(*widgets: QWidget, spacing: int = 10) -> QWidget:
    """Widgets on one line that wraps onto more lines when there is not enough room."""
    holder = QWidget()
    holder.setObjectName("ActionRow")
    layout = FlowLayout(holder, spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return holder


def header(title: str, subtitle: str = "") -> tuple[QWidget, QHBoxLayout]:
    """Page title with an optional one-line subtitle; actions can be added on the right."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(12)
    text_column = QVBoxLayout()
    text_column.setSpacing(4)
    title_label = QLabel(title)
    title_label.setObjectName("Title")
    title_label.setTextFormat(Qt.TextFormat.PlainText)
    text_column.addWidget(title_label)
    if subtitle:
        text_column.addWidget(muted(subtitle))
    layout.addLayout(text_column, 1)
    return holder, layout


class Elided(QLabel):
    """One line that ends in "…" when there is not enough room."""

    def __init__(self, value: str = "", style: str = "") -> None:
        super().__init__(value)
        self.setObjectName(style)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), text)


class StatTile(QFrame):
    """A big number with a label and icon, for the dashboard."""

    def __init__(self, label: str, glyph: str, tone: str = "#a8c6ff") -> None:
        super().__init__()
        self.setObjectName("Tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(image(pixmap(glyph, 16, tone)))
        caption = QLabel(label)
        caption.setObjectName("StatLabel")
        top.addWidget(caption, 1)
        layout.addLayout(top)
        self.value = QLabel("—")
        self.value.setObjectName("StatValue")
        layout.addWidget(self.value)
        self.note = muted("", "Subtle")
        layout.addWidget(self.note)

    def set(self, value: str, note: str = "") -> None:
        self.value.setText(value)
        self.note.setText(note)


def natural(button: QPushButton) -> QPushButton:
    """A button inside a form row keeps its natural width instead of filling the field column."""
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return button


def primary(button: QPushButton) -> None:
    button.setObjectName("Primary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)


class CardGrid(QWidget):
    """Cards in equal columns that drop to fewer columns as the window narrows."""

    def __init__(self, minimum: int = 340, spacing: int = 16, maximum: int = 2) -> None:
        super().__init__()
        self.minimum, self.maximum = minimum, maximum
        self.cards: list[QWidget] = []
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        # The current columns must not set the minimum, or the grid could never narrow again.
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget: QWidget) -> None:
        self.cards.append(widget)
        widget.installEventFilter(self)
        self.columns = 0
        self.arrange()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        # A card's content changed (text, shown/hidden rows): even the row out again.
        if event.type() == QEvent.Type.LayoutRequest and watched in self.cards:
            QTimer.singleShot(0, self.fit_rows)
        return super().eventFilter(watched, event)

    def fit_rows(self) -> None:
        """Every card in a row is as tall as the tallest one at the real column width."""
        visible = [card for card in self.cards if not card.isHidden()]
        if not visible or self.width() <= 1:
            return
        columns = max(1, min(len(visible), self.maximum, self.width() // self.minimum))
        spacing = self.grid.spacing()
        column = max(1, (self.width() - spacing * (columns - 1)) // columns)
        for start in range(0, len(visible), columns):
            line = visible[start : start + columns]
            for card in line:
                card.setMinimumHeight(0)
                card.setMaximumHeight(16777215)
            height = max(
                card.heightForWidth(column)
                if card.hasHeightForWidth()
                else card.sizeHint().height()
                for card in line
            )
            for card in line:
                card.setFixedHeight(height)

    def arrange(self) -> None:
        width = max(self.width(), 1)
        columns = (
            max(1, min(len(self.cards), self.maximum, width // self.minimum)) if self.cards else 1
        )
        if columns == self.columns:
            return
        self.columns = columns
        for widget in self.cards:
            self.grid.removeWidget(widget)
        for column in range(max(self.maximum, 4)):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, widget in enumerate(self.cards):
            self.grid.addWidget(widget, index // columns, index % columns)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        widest = max((card.minimumSizeHint().width() for card in self.cards), default=0)
        return QSize(widest, self.heightForWidth(max(self.width(), widest)))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        """Rows as tall as their tallest card at the real column width, not the narrowest."""
        visible = [card for card in self.cards if not card.isHidden()]
        if not visible:
            return 0
        columns = max(1, min(len(visible), self.maximum, max(width, 1) // self.minimum))
        spacing = self.grid.spacing()
        column = max(1, (width - spacing * (columns - 1)) // columns)
        rows = [visible[i : i + columns] for i in range(0, len(visible), columns)]
        height = 0
        for line in rows:
            height += max(
                card.heightForWidth(column)
                if card.hasHeightForWidth()
                else card.sizeHint().height()
                for card in line
            )
        return height + spacing * (len(rows) - 1)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = self.width() if self.width() > 1 else 780
        return QSize(width, self.heightForWidth(width))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.arrange()
        self.fit_rows()


class Checklist(QWidget):
    """Setup steps in order: done steps get a tick, only the next step offers its button."""

    def __init__(self, steps: list[tuple[str, str, Callable[[], object]]]) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.marks: list[QLabel] = []
        self.labels: list[QLabel] = []
        self.buttons: list[QPushButton] = []
        for number, (label, action, callback) in enumerate(steps, start=1):
            line = QHBoxLayout()
            line.setSpacing(10)
            mark = QLabel(str(number))
            mark.setObjectName("StepMark")
            mark.setFixedSize(QSize(22, 22))
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line.addWidget(mark)
            text = muted(label, "Muted")
            line.addWidget(text, 1)
            button = icon_button(action, "chevron-right", role="Ghost")
            button.clicked.connect(callback)
            line.addWidget(button)
            layout.addLayout(line)
            self.marks.append(mark)
            self.labels.append(text)
            self.buttons.append(button)
        self.done = [False] * len(steps)

    @property
    def complete(self) -> bool:
        return all(self.done)

    def update_steps(self, done: list[bool]) -> None:
        self.done = list(done)
        upcoming = next((i for i, finished in enumerate(done) if not finished), None)
        for index, finished in enumerate(done):
            mark = self.marks[index]
            if finished:
                mark.setText("")
                mark.setPixmap(pixmap("check", 14, "#3ecf8e"))
            else:
                mark.setPixmap(QPixmap())
                mark.setText(str(index + 1))
            restyle(
                mark, "StepDone" if finished else "StepNext" if index == upcoming else "StepMark"
            )
            self.labels[index].setObjectName("Subtle" if finished else "Muted")
            self.labels[index].style().polish(self.labels[index])
            self.buttons[index].setVisible(index == upcoming)
            restyle(self.buttons[index], "Primary" if index == upcoming else "Ghost")
