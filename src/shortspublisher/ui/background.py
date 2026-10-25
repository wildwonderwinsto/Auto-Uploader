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
from .components import card, flow, icon_button, pill, restyle
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
        self.then: str | None = None  # Turn On… then starts it, in one click
        # Plain words first: what it does, what it is doing now, and one button for the next
        # step. Only the buttons that apply right now are shown.
        service, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(pixmap("monitor", 20, theme.ACCENT_TEXT)))
        heading.addWidget(text("Background uploading", "Heading"), 1)
        self.state_pill = pill("Checking…")
        heading.addWidget(self.state_pill)
        body.addLayout(heading)
        body.addWidget(
            text(
                "Keeps your scheduled uploads, queues and watched folders going while "
                "ShortsScheduler is closed.",
                "Muted",
            )
        )
        self.status = text("Checking whether it is set up…", "Subheading")
        self.hint = text("", "Muted")
        self.dashboard = text("Background service: not checked")
        self.service_status: str | None = None
        self.feedback = text("", "Muted")
        self.feedback.hide()
        body.addWidget(self.status)
        body.addWidget(self.hint)
        self.buttons: dict[str, QPushButton] = {}
        for action, title, glyph, role in (
            ("enable", "Turn On…", "power", "Primary"),
            ("start", "Start", "play", "Primary"),
            ("restart", "Restart", "refresh", "Primary"),
            ("stop", "Stop…", "pause", ""),
            ("disable", "Turn Off…", "power", "Ghost"),
            ("remove", "Remove…", "trash", "Ghost"),
            ("status", "Check Now", "refresh", "Ghost"),
        ):
            button = icon_button(title, glyph, role=role)
            button.clicked.connect(lambda checked=False, a=action: self.act(a))
            self.buttons[action] = button
        self.buttons["enable"].setAccessibleName("Enable Background Publishing…")
        self.buttons["enable"].setToolTip(SERVICE_CONSENT)
        self.buttons["restart"].setToolTip("Stop it and start it again, running this version")
        self.buttons["remove"].setToolTip(
            "Turn it off and delete its macOS service file. Your videos, library and sign-ins "
            "are kept."
        )
        self.buttons["status"].hide()  # it checks itself every few seconds
        self.quit_button = icon_button("Stop and Quit…", "power", role="Ghost")
        self.quit_button.setAccessibleName("Stop Background Publishing and Quit…")
        self.quit_button.clicked.connect(self.stop_and_quit)
        # Until the first status check, nothing is offered that might not apply.
        for hidden in (*self.buttons.values(), self.quit_button):
            hidden.hide()
        body.addWidget(
            flow(
                self.buttons["enable"],
                self.buttons["start"],
                self.buttons["restart"],
                self.buttons["stop"],
                self.quit_button,
                self.buttons["disable"],
                self.buttons["remove"],
                self.buttons["status"],
            )
        )
        self.login = QCheckBox("Also start it when I log in to my Mac")
        self.login.setToolTip("Takes effect after the service has been started once")
        body.addWidget(self.login)
        body.addWidget(self.feedback)
        # Technical details (process, last check-in) are the status line's tooltip.
        self.diagnostics = text("Status has not been checked.", "Subtle")
        self.diagnostics.hide()
        body.addWidget(
            text(
                "• Closing ShortsScheduler doesn't stop it; Turn Off does (after an upload in "
                "progress) and never deletes anything online.\n"
                "• Each scheduled YouTube upload also needs “Also upload it while "
                "ShortsScheduler is closed” ticked when you approve it.\n"
                "• After you update ShortsScheduler, choose Restart so it runs the new version. "
                "Your Mac must stay logged in and online.",
                "Subtle",
            )
        )
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
        messages = {
            "enable": SERVICE_CONSENT,
            "stop": "Stop background uploading? An upload in progress finishes first and "
            "nothing online is deleted. Afterwards, uploads only go out while ShortsScheduler "
            "is open.",
            "restart": "Restart background uploading? It stops (after any upload in progress) "
            "and starts again with this version of ShortsScheduler.",
            "disable": "Turn off background uploading? It stops (an upload in progress "
            "finishes first) and won't start again until you turn it on. Nothing online is "
            "deleted. Uploads then only go out while ShortsScheduler is open.",
            "remove": "Turn off background uploading and delete its macOS service file? Your "
            "videos, library and sign-ins are kept.",
        }
        if action in messages and (
            QMessageBox.question(
                self.host,
                "Background uploading",
                messages[action],
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
            self.then = None
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
        enabled, requested = bool(result["enabled"]), bool(result["requested"])
        # A switched-on service that isn't running stopped unexpectedly (often an update).
        stopped = requested and not running
        pill_text, tone, line, hint = (
            (
                "Running",
                "Good",
                "Running. Uploads keep going when you close ShortsScheduler.",
                f"Last check-in: {timestamp(owner['heartbeat'], empty='not yet')}.",
            )
            if running
            else (
                "Stopped",
                "Warn",
                "Stopped unexpectedly, often after an update.",
                "While ShortsScheduler is open it still uploads on time. Choose Restart so "
                "uploads also go out while it's closed.",
            )
            if stopped
            else (
                "Not running",
                "Info",
                "Set up, but not running.",
                "Choose Start to keep uploading while ShortsScheduler is closed.",
            )
            if enabled
            else (
                "Off",
                "Neutral",
                "Off. Uploads only go out while ShortsScheduler is open.",
                "Turn it on to keep uploading while the app is closed. Nothing is installed "
                "until you do.",
            )
        )
        self.state_pill.setText(pill_text)
        restyle(self.state_pill, tone)
        for label, value_text in ((self.status, line), (self.hint, hint)):
            if label.text() != value_text:
                label.setText(value_text)
        self.dashboard.setText(f"Background service: {result['status']}")
        if self.worker and self.worker.action == "enable" and enabled and not running:
            self.then = "start"
        if self.worker and self.worker.action != "status":
            self.feedback.setText(
                {
                    "enable": "Turned on. Starting it…",
                    "start": "Started.",
                    "stop": "Stopped. Uploads now only go out while ShortsScheduler is open.",
                    "restart": "Restarted with this version.",
                    "disable": "Turned off.",
                    "remove": "Removed. Your videos, library and sign-ins were kept.",
                }[self.worker.action]
            )
        # One main button for the next step, and only the other actions that apply now.
        # Simply on or off: Turn Off also stops it, so there is no separate Stop.
        self.buttons["enable"].setVisible(not enabled)
        self.buttons["start"].setVisible(enabled and not requested and not running)
        self.buttons["restart"].setVisible(enabled and (running or stopped))
        restyle(self.buttons["restart"], "Primary" if stopped else "Ghost")
        self.buttons["stop"].hide()
        self.quit_button.hide()
        self.buttons["disable"].setVisible(enabled)
        restyle(self.buttons["disable"], "" if running else "Ghost")
        # Only a leftover service file after turning off needs removing.
        self.buttons["remove"].setVisible(not enabled and result["status"] != "Not Installed")
        if (not self.worker or self.worker.action == "status") and self.feedback.text().startswith(
            "Action needs attention"
        ):
            self.feedback.hide()  # an earlier problem that has since cleared
        if self.worker and self.worker.action != "status":
            self.login.setChecked(bool(result["login"]))

    def finished(self) -> None:
        assert self.worker
        self.worker.deleteLater()
        self.worker = None
        for button in (*self.buttons.values(), self.quit_button):
            button.setEnabled(True)
        if self.then and not self.closing:
            action, self.then = self.then, None
            self.start(action)
            return
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
