"""Native service controls; launchctl and database work stay off the GUI thread."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..background import LaunchAgent, ServiceError
from ..background_state import SERVICE_CONSENT
from .components import Disclosure, primary
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

    def __init__(self, manager: LaunchAgent, window: MainWindow):
        super().__init__()
        self.manager, self.host = manager, window
        self.worker: ServiceWorker | None = None
        self.closing = False
        self.quit_after_stop = False
        self.setObjectName("settings")
        self.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(text("Settings", "Title"))
        layout.addWidget(text("Background Publishing", "Heading"))
        layout.addWidget(
            text(
                "Publish approved schedules while this window is closed. Your Mac must stay awake, logged in and online.",
                "Muted",
            )
        )
        self.status = text("Not checked. Opening Settings never installs or starts a service.")
        self.dashboard = text("Background service: not checked")
        self.feedback = text("", "Muted")
        layout.addWidget(self.feedback)
        self.feedback.hide()
        self.advanced = Disclosure("Service options and diagnostics")
        self.diagnostics = text("Status has not been checked.", "Muted")
        self.advanced.body.addWidget(self.diagnostics)
        layout.addWidget(self.status)
        self.login = QCheckBox("Also start at login (requires Start once after enabling)")
        layout.addWidget(self.login)
        self.buttons: dict[str, QPushButton] = {}
        for action, title in (
            ("enable", "Enable Background Publishing…"),
            ("start", "Start Background Service"),
            ("stop", "Stop Background Service…"),
            ("restart", "Restart Background Service…"),
            ("disable", "Disable Background Publishing…"),
            ("remove", "Remove Background Service…"),
            ("status", "Refresh Service Status"),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, a=action: self.act(a))
            self.buttons[action] = button
            (
                self.advanced.body
                if action in {"restart", "disable", "remove", "status"}
                else layout
            ).addWidget(button)
            if action in {"enable", "start"}:
                primary(button)
        self.quit_button = QPushButton("Stop Background Publishing and Quit…")
        self.quit_button.clicked.connect(self.stop_and_quit)
        layout.addWidget(self.quit_button)
        layout.addWidget(self.advanced)
        self.advanced.body.addWidget(
            text(
                "Closing this window or quitting the interface leaves an explicitly started background service running. Pause All Scheduled Publishing prevents new starts. Stop waits for an active request; it does not delete remote content. A stopped service permits desktop scheduling while this interface remains open. Disable or Remove also turns off login startup.\n\nThis checkout and its Python environment must stay at their current paths. The service cannot publish while logged out or powered off and does not wake the Mac."
            )
        )
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
        if self.worker or self.closing:
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
            self.quit_after_stop = False
            return
        owner = result["owner"]
        value = (
            f"Background service: {result['status']}\n"
            f"Permission: {'Enabled' if result['enabled'] else 'Disabled'} · Installed: {'Yes' if result['installed'] else 'No'}\n"
            f"Login startup: {'On' if result['login'] else 'Off'} · OS process: {result['pid'] or 'Not running'}\n"
            f"Last check-in: {owner['heartbeat'] or 'None'}\n"
            f"Last dispatcher: {owner['kind'] or 'None'} · {owner['state']}\n{owner['error']}\n"
            "Credentials and channel are checked before each upload. Global publishing and per-job approval are separate."
        )
        self.diagnostics.setText(value)
        self.status.setText(
            f"Background service: {result['status']}\n"
            + (
                "Configured. Select Start to run it."
                if result["enabled"] and not result["requested"]
                else "Enable background publishing to set it up."
                if not result["enabled"]
                else "Global publishing and each job’s background approval must also be enabled."
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
