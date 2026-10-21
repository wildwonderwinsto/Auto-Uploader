"""Small, shared dark theme adapted from ShortsStudio's visual conventions."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BACKGROUND = "#16181d"
SURFACE = "#1e2128"
BORDER = "#343945"
TEXT = "#e8eaf0"
MUTED = "#9aa1b1"
ACCENT = "#4c8dff"
SPACE = 24

STYLESHEET = f"""
QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-size: 10pt; }}
QLabel {{ background: transparent; }}
QWidget#ActionRow {{ background: transparent; }}
QFrame#Card QCheckBox {{ background: {SURFACE}; }}
QLabel#Title {{ font-size: 22pt; font-weight: 600; }}
QLabel#Heading {{ font-size: 14pt; font-weight: 600; }}
QLabel#Brand {{ font-size: 14pt; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Eyebrow {{ color: {MUTED}; font-size: 9pt; font-weight: 600; }}
QLabel#Badge {{ color: #a9c8ff; background: #22304a; border-radius: 5px; padding: 6px 10px; }}
QLabel#Mark {{ color: #a9c8ff; font-size: 28pt; font-weight: 600; }}
QFrame#Rail {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QPushButton {{ background: #272b34; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 10px 14px; text-align: left; }}
QPushButton#Primary {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; font-weight: 600; }}
QPushButton#Primary:focus, QPushButton#Disclosure:focus {{ border: 1px solid #8ab4ff; }}
QPushButton#Primary:hover {{ background: #659dff; }}
QPushButton#Primary:disabled {{ background: #272b34; color: {MUTED}; border-color: {BORDER}; }}
QPushButton#Disclosure {{ background: transparent; border: none; color: #a9c8ff; padding: 6px 0px; }}
QPushButton:hover {{ background: #30353f; }}
QPushButton:pressed {{ background: #22304a; }}
QPushButton:focus {{ border-color: #8ab4ff; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#Nav {{ background: transparent; color: {MUTED}; border: 1px solid transparent;
    padding: 12px 14px; }}
QPushButton#Nav:hover {{ background: #272b34; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: #22304a; color: #a9c8ff; border-left: 3px solid {ACCENT}; }}
QPushButton#Nav:focus {{ border: 1px solid #8ab4ff; }}
QPushButton#Nav:pressed {{ background: #303e58; }}
QLineEdit, QComboBox, QListWidget, QPlainTextEdit {{ background: {SURFACE}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 8px; selection-background-color: #22304a; }}
QListWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}
QListWidget::item:selected {{ background: #22304a; color: #a9c8ff; }}
QListWidget::item:hover {{ background: #272b34; }}
QProgressBar {{ border: none; background: {SURFACE}; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: #8ab4ff; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {SURFACE}; color: {MUTED}; padding: 10px 9px; }}
QTabBar::tab:selected {{ background: #22304a; color: #a9c8ff; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ background: #272b34; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: {BACKGROUND}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BACKGROUND),
        (QPalette.ColorRole.Base, BACKGROUND),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, SURFACE),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
