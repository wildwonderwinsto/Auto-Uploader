"""One dark theme for every page and dialog, refined from ShortsStudio's palette."""

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QIcon, QPalette, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCalendarWidget,
    QComboBox,
    QLineEdit,
    QStyledItemDelegate,
    QToolButton,
)

BACKGROUND = "#101217"
RAIL = "#14161c"
SURFACE = "#181b22"
RAISED = "#21252e"
HOVER = "#2a2f3a"
BORDER = "#262a34"
STRONG = "#353b48"
TEXT = "#eceef3"
MUTED = "#8d94a5"
SUBTLE = "#6b7284"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9dff"
ACCENT_SOFT = "#1b2a47"
ACCENT_TEXT = "#a8c6ff"
GOOD, GOOD_SOFT = "#3ecf8e", "#122b20"
WARN, WARN_SOFT = "#f2b24c", "#2f2614"
BAD, BAD_SOFT = "#f07070", "#331b1e"
SPACE = 24

ASSETS = (Path(__file__).parent / "assets").as_posix()

STYLESHEET = f"""
QWidget {{ background: transparent; color: {TEXT}; font-size: 10pt; }}
QMainWindow, QDialog, QMessageBox, QStackedWidget#Pages {{ background: {BACKGROUND}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {STRONG}; padding: 6px 8px;
    border-radius: 6px; }}

QLabel#Title {{ font-size: 21pt; font-weight: 700; }}
QLabel#Heading {{ font-size: 13pt; font-weight: 600; }}
QLabel#Subheading {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#Brand {{ font-size: 13pt; font-weight: 700; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Subtle {{ color: {SUBTLE}; font-size: 9pt; }}
QLabel#Eyebrow {{ color: {SUBTLE}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}
QLabel#StatValue {{ font-size: 22pt; font-weight: 700; }}
QLabel#StatLabel {{ color: {MUTED}; font-size: 9pt; font-weight: 600; }}
QLabel#Badge {{ color: {ACCENT_TEXT}; background: {ACCENT_SOFT}; border-radius: 8px;
    padding: 7px 11px; }}
QLabel#Info, QLabel#Good, QLabel#Warn, QLabel#Bad, QLabel#Neutral {{ border-radius: 10px;
    padding: 3px 10px; font-size: 9pt; font-weight: 600; }}
QLabel#Info {{ color: {ACCENT_TEXT}; background: {ACCENT_SOFT}; }}
QLabel#Good {{ color: {GOOD}; background: {GOOD_SOFT}; }}
QLabel#Warn {{ color: {WARN}; background: {WARN_SOFT}; }}
QLabel#Bad {{ color: {BAD}; background: {BAD_SOFT}; }}
QLabel#Neutral {{ color: {MUTED}; background: {RAISED}; }}
QLabel#Notice {{ color: {TEXT}; background: {RAISED}; border: 1px solid {STRONG};
    border-radius: 10px; padding: 10px 12px; }}
QLabel#Error {{ color: {BAD}; }}
QLabel#StepMark, QLabel#StepNext, QLabel#StepDone {{ border-radius: 11px; font-size: 9pt;
    font-weight: 700; }}
QLabel#StepMark {{ color: {SUBTLE}; border: 1px solid {STRONG}; }}
QLabel#StepNext {{ color: #ffffff; background: {ACCENT}; }}
QLabel#StepDone {{ background: {GOOD_SOFT}; }}

QFrame#Rail {{ background: {RAIL}; border-right: 1px solid {BORDER}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Row {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#Divider {{ background: {BORDER}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#RailStatus {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#RailStatusWarn {{ background: {WARN_SOFT}; border: 1px solid {WARN}; border-radius: 10px; }}
QFrame#Warning {{ background: {WARN_SOFT}; border: 1px solid {WARN}; border-radius: 12px; }}

QPushButton {{ background: {RAISED}; border: 1px solid {STRONG}; border-radius: 8px;
    padding: 8px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {SUBTLE}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton#Primary {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT};
    font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:focus {{ border-color: {ACCENT_TEXT}; }}
QPushButton#Primary:disabled {{ background: {RAISED}; color: {SUBTLE}; border-color: {BORDER}; }}
QPushButton#Danger {{ color: {BAD}; }}
QPushButton#Danger:hover {{ background: {BAD_SOFT}; border-color: {BAD}; }}
QPushButton#Ghost {{ background: transparent; border-color: transparent; color: {ACCENT_TEXT}; }}
QPushButton#Ghost:hover {{ background: {RAISED}; }}
QPushButton#Logo {{ background: transparent; border: none; padding: 0; border-radius: 6px; }}
QPushButton#Logo:hover {{ background: {RAISED}; }}
QPushButton#Nav {{ background: transparent; color: {MUTED}; border: 1px solid transparent;
    padding: 9px 12px; text-align: left; font-weight: 500; }}
QPushButton#Nav:hover {{ background: {RAISED}; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: {ACCENT_SOFT}; color: {TEXT}; font-weight: 600; }}
QPushButton#Nav:focus {{ border-color: {STRONG}; }}

QPushButton#Step {{ background: transparent; border: 1px solid {BORDER}; border-radius: 16px;
    padding: 7px 16px; color: {MUTED}; font-weight: 600; }}
QPushButton#Step:hover {{ color: {TEXT}; border-color: {STRONG}; }}
QPushButton#Step:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton#Step:disabled {{ color: {SUBTLE}; }}
QPushButton#Segment {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 12px; color: {MUTED}; }}
QPushButton#Segment:hover {{ color: {TEXT}; background: {RAISED}; }}
QPushButton#Segment:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {TEXT};
    font-weight: 600; }}
QLabel#Thumb {{ background: {RAISED}; border-radius: 8px; }}
QListWidget#Library::item {{ padding: 0; margin: 2px 0; }}
QListWidget#Library::item:selected {{ background: {ACCENT_SOFT}; }}
QListWidget#Chips {{ background: transparent; border: none; padding: 0; }}
QListWidget#Chips::item {{ background: {RAISED}; border: 1px solid {STRONG}; border-radius: 12px;
    padding: 3px 10px; margin: 2px; }}
QListWidget#Chips::item:selected {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QTimeEdit, QDateEdit, QDateTimeEdit {{
    background: {RAISED}; border: 1px solid {STRONG}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus,
QDateEdit:focus, QDateTimeEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QTimeEdit:disabled, QDateEdit:disabled {{ color: {SUBTLE}; background: {SURFACE}; }}
QLineEdit:read-only {{ background: {SURFACE}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({ASSETS}/chevron-down.svg); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {STRONG};
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: none;
    padding: 4px; }}
QComboBox QAbstractItemView::item {{ padding: 7px 10px; border-radius: 6px; min-height: 22px; }}
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
    background: {ACCENT_SOFT}; color: {TEXT}; }}
QDateEdit::up-button, QDateEdit::down-button, QDateTimeEdit::up-button,
QDateTimeEdit::down-button {{ width: 0; border: none; }}
QDateEdit::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 26px; }}
QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{ image: url({ASSETS}/chevron-down.svg);
    width: 14px; height: 14px; }}
/* After the date rules: a time field is a date field to Qt, and needs its arrows. */
QSpinBox, QTimeEdit {{ padding-right: 30px; }}
QSpinBox::up-button, QTimeEdit::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 26px; border: none; border-left: 1px solid {STRONG};
    border-top-right-radius: 8px; }}
QSpinBox::down-button, QTimeEdit::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 26px; border: none; border-left: 1px solid {STRONG};
    border-bottom-right-radius: 8px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QTimeEdit::up-button:hover,
QTimeEdit::down-button:hover {{ background: {HOVER}; }}
QSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url({ASSETS}/chevron-up.svg); width: 11px;
    height: 11px; }}
QSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url({ASSETS}/chevron-down.svg);
    width: 11px; height: 11px; }}

QCheckBox, QRadioButton {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; border-radius: 5px; border: 1px solid {STRONG};
    background: {RAISED}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url({ASSETS}/check.svg); }}
QCheckBox::indicator:disabled {{ background: {SURFACE}; border-color: {BORDER}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px;
    border: 1px solid {STRONG}; background: {RAISED}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url({ASSETS}/dot.svg); }}

QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 4px; outline: none; }}
QListWidget::item {{ padding: 9px 10px; border-radius: 7px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {RAISED}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 14px; margin-right: 4px;
    border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QTabBar::tab:disabled {{ color: {SUBTLE}; }}

QProgressBar {{ border: none; background: {RAISED}; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QProgressBar#MeterWarn::chunk {{ background: {WARN}; }}
QProgressBar#MeterBad::chunk {{ background: {BAD}; }}
QSlider::groove:horizontal {{ height: 4px; background: {STRONG}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; }}
QSlider::handle:horizontal:disabled {{ background: {STRONG}; }}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {STRONG}; border-radius: 3px; min-height: 28px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {STRONG}; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {SURFACE}; }}
QCalendarWidget QAbstractItemView {{ background: {SURFACE}; selection-background-color:
    {ACCENT}; selection-color: #ffffff; outline: none; }}
QCalendarWidget QToolButton {{ color: {TEXT}; padding: 6px; border-radius: 6px; }}
QCalendarWidget QToolButton:hover {{ background: {RAISED}; }}
QMenu {{ background: {RAISED}; border: 1px solid {STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
"""


class Polisher(QObject):
    """Gives every dropdown and calendar the theme, wherever it is created."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Polish:
            if isinstance(watched, QComboBox) and not watched.property("themed"):
                watched.setProperty("themed", True)
                # Qt's default combo delegate ignores style sheets for the open list.
                watched.setItemDelegate(QStyledItemDelegate(watched))
            elif isinstance(watched, QAbstractSpinBox) and not watched.property("themed"):
                watched.setProperty("themed", True)
                editor = watched.findChild(QLineEdit)
                if editor:
                    editor.setTextMargins(6, 0, 0, 0)
            elif isinstance(watched, QCalendarWidget) and not watched.property("themed"):
                watched.setProperty("themed", True)
                plain = QTextCharFormat()
                plain.setForeground(QColor(TEXT))
                for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
                    watched.setWeekdayTextFormat(day, plain)
                header = QTextCharFormat()
                header.setForeground(QColor(MUTED))
                watched.setHeaderTextFormat(header)
                watched.setVerticalHeaderFormat(
                    QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
                )
                for name, glyph in (
                    ("qt_calendar_prevmonth", "chevron-left"),
                    ("qt_calendar_nextmonth", "chevron-right"),
                ):
                    arrow = watched.findChild(QToolButton, name)
                    if arrow:
                        arrow.setIcon(QIcon(f"{ASSETS}/{glyph}.svg"))
        return False


POLISHER = Polisher()


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BACKGROUND),
        (QPalette.ColorRole.Base, RAISED),
        (QPalette.ColorRole.AlternateBase, SURFACE),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, RAISED),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.PlaceholderText, SUBTLE),
        (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, RAISED),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.Link, ACCENT_TEXT),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    app.installEventFilter(POLISHER)
