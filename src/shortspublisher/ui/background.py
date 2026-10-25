"""Native service controls; launchctl and database work stay off the GUI thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..background import LaunchAgent, ServiceError
from ..background_state import SERVICE_CONSENT
from . import theme
from .components import Section, card, flow, icon_button, pill, restyle
from .formatting import timestamp
from .icons import image, pixmap
from .scheduling import text

if TYPE_CHECKING:
    from .window import MainWindow


class ServiceWorker(QThread):
    result = Signal(object)

    def __init__(
        self, manager: LaunchAgent, action: str, consent: bool, login: bool, parent: QWidget
    ):
        super().__init__(parent)
        self.manager, self.action, self.consent, self.login = manager, action, consent, login

    def run(self) -> None:
        try:
            self.result.emit(
                self.manager.perform(self.action, consent=self.consent, login=self.login)
            )
        except Exception as exc:
            self.result.emit(
                {
                    "error": str(exc)
                    if isinstance(exc, ServiceError)
                    else "Service control failed safely. Refresh status and inspect saved history."
                }
            )


class BackgroundPage(QScrollArea):
    idle = Signal()
    status_changed = Signal()

    def __init__(self, manager: LaunchAgent, window: MainWindow):
        super().__init__()
        self.manager, self.host = manager, window
        self.worker: ServiceWorker | None = None
        # A click made during the regular status check runs right after it, never dropped.
        self.queued: tuple[str, bool] | None = None
        self.closing = False
        self.quit_after_stop = False
        self.setObjectName("settings")
        self.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.content_layout = layout
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(18)
        layout.addWidget(text("Settings", "Title"))
        layout.addWidget(
            text("Everything here is optional. Each section says what it does.", "Muted")
        )
        service, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(pixmap("monitor", 20, theme.ACCENT_TEXT)))
        heading.addWidget(text("Background uploading", "Heading"), 1)
        self.state_pill = pill("Not checked")
        heading.addWidget(self.state_pill)
        body.addLayout(heading)
        body.addWidget(
            text(
                "Keeps scheduled and auto uploads going while ShortsScheduler is closed. Your Mac "
                "must stay logged in and online; while an upload is planned, it is kept awake "
                "(closing the lid still puts it to sleep).",
                "Muted",
            )
        )
        self.status = text("Not checked. Opening Settings never installs or starts a service.")
        self.dashboard = text("Background service: not checked")
        self.service_status: str | None = None
        self.feedback = text("", "Muted")
        self.feedback.hide()
        body.addWidget(self.status)
        self.login = QCheckBox("Also start when I log in to my Mac")
        self.login.setToolTip("Takes effect after the service has been started once")
        body.addWidget(self.login)
        self.buttons: dict[str, QPushButton] = {}
        for action, title, glyph, role in (
            ("enable", "Turn On Background Uploading…", "power", "Primary"),
            ("start", "Start", "play", "Primary"),
            ("stop", "Stop…", "pause", ""),
            ("restart", "Restart…", "refresh", ""),
            ("disable", "Turn Off…", "power", ""),
            ("remove", "Remove Service…", "trash", "Danger"),
            ("status", "Check Status", "refresh", "Ghost"),
        ):
            button = icon_button(title, glyph, role=role)
            button.clicked.connect(lambda checked=False, a=action: self.act(a))
            self.buttons[action] = button
        self.buttons["enable"].setAccessibleName("Enable Background Publishing…")
        self.quit_button = icon_button("Stop and Quit…", "power")
        self.quit_button.setAccessibleName("Stop Background Publishing and Quit…")
        self.quit_button.clicked.connect(self.stop_and_quit)
        # Until the first status check, only the actions that always apply are offered.
        for hidden in (
            self.buttons["enable"],
            self.buttons["start"],
            self.buttons["stop"],
            self.quit_button,
        ):
            hidden.hide()
        body.addWidget(
            flow(
                self.buttons["enable"],
                self.buttons["start"],
                self.buttons["stop"],
                self.quit_button,
            )
        )
        body.addWidget(self.feedback)
        # Less common service actions; their details are in the tooltip of the status line.
        self.advanced = Section("")
        self.diagnostics = text("Status has not been checked.", "Subtle")
        self.diagnostics.hide()
        self.advanced.body.addWidget(self.diagnostics)
        self.advanced.body.addWidget(
            flow(
                self.buttons["restart"],
                self.buttons["disable"],
                self.buttons["remove"],
                self.buttons["status"],
            )
        )
        self.advanced.body.addWidget(
            text(
                "Quitting leaves a started service running. Stop waits for an upload in progress "
                "and never deletes anything online. This folder and its Python environment must "
                "stay where they are.",
                "Subtle",
            )
        )
        body.addWidget(self.advanced)
        layout.addWidget(service)
        # Sections from other pages are placed in these slots by the window.
        self.defaults_slot = QVBoxLayout()
        layout.addLayout(self.defaults_slot)
        self.limits_slot = QVBoxLayout()
        layout.addLayout(self.limits_slot)
        self.connections_slot = QVBoxLayout()
        layout.addLayout(self.connections_slot)
        self.hosting_slot = QVBoxLayout()
        layout.addLayout(self.hosting_slot)
        about, about_body = card(20, 8)
        about_body.addWidget(text("About & privacy", "Heading"))
        about_body.addWidget(
            text(
                "ShortsScheduler runs on this Mac. Your original video files are never changed, "
                "moved or deleted. Sign-ins are kept in the macOS Keychain. Nothing uploads "
                "without your approval.",
                "Muted",
            )
        )
        layout.addWidget(about)
        layout.addStretch()
        self.setWidget(content)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(lambda: self.start("status"))

    def activate(self) -> None:
        self.timer.start()
        self.start("status")

    def act(self, action: str) -> None:
        if action in {"enable", "stop", "restart", "disable", "remove"}:
            message = (
                SERVICE_CONSENT
                if action == "enable"
                else "Stop the background worker after its active request returns? Remote content is preserved. Desktop scheduling can resume after ownership is released; use Pause All to prevent all new starts."
            )
            if action == "remove":
                message += " Only this managed service configuration will be removed. Your library and credentials remain."
            if (
                QMessageBox.question(
                    self.host,
                    "Background publishing",
                    message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        self.start(action, consent=action == "enable")

    def stop_and_quit(self) -> None:
        if (
            QMessageBox.question(
                self.host,
                "Stop background publishing and quit?",
                "Stop the service, wait for its active request, then close the interface? Remote content is not deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.host.automatic.running = False
            self.host.automatic.timer.stop()
            if self.host.automatic.worker:
                self.host.automatic.worker.cancel.set()
            self.quit_after_stop = True
            self.start("stop")

    def start(self, action: str, *, consent: bool = False) -> None:
        if self.closing:
            return
        if self.worker:
            if action != "status" and self.worker.action == "status":
                self.queued = (action, consent)
            return
        if action != "status":
            self.feedback.show()
            self.feedback.setText(
                {
                    "enable": "Enabling background publishing…",
                    "start": "Starting service…",
                    "stop": "Stopping service; waiting for active work…",
                    "restart": "Restarting service…",
                    "disable": "Disabling background publishing…",
                    "remove": "Removing service configuration…",
                }[action]
            )
        worker = ServiceWorker(self.manager, action, consent, self.login.isChecked(), self)
        self.worker = worker
        if action != "status":
            # The regular check (every five seconds) leaves the buttons alone: no flashing.
            for button in (*self.buttons.values(), self.quit_button):
                button.setEnabled(False)
        worker.result.connect(self.receive)
        worker.finished.connect(self.finished)
        worker.start()

    def receive(self, result: dict[str, Any]) -> None:
        if result.get("error"):
            self.status.setText(result["error"])
            self.feedback.show()
            self.feedback.setText("Action needs attention. " + result["error"])
            self.dashboard.setText(
                "Background service: unable to verify status. " + result["error"]
            )
            self.service_status = None
            self.quit_after_stop = False
            return
        owner = result["owner"]
        if self.service_status != result["status"]:
            self.service_status = result["status"]
            self.status_changed.emit()
        value = (
            f"Background service: {result['status']}\n"
            f"Permission: {'Enabled' if result['enabled'] else 'Disabled'} · Installed: {'Yes' if result['installed'] else 'No'}\n"
            f"Login startup: {'On' if result['login'] else 'Off'} · OS process: {result['pid'] or 'Not running'}\n"
            f"Last check-in: {timestamp(owner['heartbeat'], empty='None')}\n"
            f"Last dispatcher: {owner['kind'] or 'None'} · {owner['state']}\n{owner['error']}\n"
            "Credentials and channel are checked before each upload. Global publishing and per-job approval are separate."
        )
        self.diagnostics.setText(value)
        self.status.setToolTip(value)
        running = bool(result["pid"])
        self.state_pill.setText(
            "Running" if running else "Set up · stopped" if result["enabled"] else "Off"
        )
        restyle(self.state_pill, "Good" if running else "Info" if result["enabled"] else "Neutral")
        self.buttons["enable"].setVisible(not result["enabled"])
        self.status.setText(
            f"Background service: {result['status']}\n"
            + (
                "Set up. Choose Start to run it."
                if result["enabled"] and not result["requested"]
                else "Turn it on to set it up. Nothing is installed until you do."
                if not result["enabled"]
                else "Auto uploading must also be on (sidebar), and each upload must allow background uploading."
            )
        )
        self.dashboard.setText(f"Background service: {result['status']}")
        if self.worker and self.worker.action != "status":
            self.feedback.setText(
                {
                    "enable": "Background publishing configured. Select Start when ready.",
                    "start": "Start requested. Check the service status below.",
                    "stop": "Background service stopped.",
                    "restart": "Restart requested. Check the service status below.",
                    "disable": "Background publishing disabled.",
                    "remove": "Service removed. Your library and connection were kept.",
                }[self.worker.action]
            )
        # Only the actions that apply now: nothing to restart or turn off before it's set up.
        self.buttons["restart"].setVisible(bool(result["enabled"]))
        self.buttons["disable"].setVisible(bool(result["enabled"]))
        self.buttons["remove"].setVisible(
            bool(result["enabled"]) or result["status"] != "Not Installed"
        )
        self.buttons["start"].setVisible(bool(result["enabled"]) and not bool(result["pid"]))
        self.buttons["stop"].setVisible(bool(result["pid"] or result["requested"]))
        self.quit_button.setVisible(bool(result["pid"] or result["requested"]))
        if self.worker and self.worker.action != "status":
            self.login.setChecked(bool(result["login"]))

    def finished(self) -> None:
        assert self.worker
        self.worker.deleteLater()
        self.worker = None
        for button in (*self.buttons.values(), self.quit_button):
            button.setEnabled(True)
        if self.queued and not self.closing:
            action, consent = self.queued
            self.queued = None
            self.start(action, consent=consent)
            return
        self.idle.emit()
        if self.quit_after_stop:
            self.host.close()

    def request_close(self) -> bool:
        self.timer.stop()
        if self.worker:
            self.closing = True
            return False
        return True

    def stop_for_quit(self) -> None:
        self.request_close()
        if self.worker:
            self.worker.wait()
