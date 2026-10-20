"""Accounts presentation; all OAuth, network and secret storage runs off the GUI thread."""

from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..accounts import AccountService, AccountView
from .components import Disclosure, primary
from .formatting import timestamp
from .videos import text

SETUP = (
    "1. In Google Cloud Console, create or select a project and enable YouTube Data API v3.\n"
    "2. Configure Google Auth Platform branding, audience and data access. For External / Testing, add your Google account as a test user.\n"
    "3. Create an OAuth client of type Desktop app, download its JSON, and choose it here. No source-code edits are needed.\n"
    "4. Connect YouTube, choose the intended YouTube or Brand Account in your system browser, approve read-only access, then confirm the returned channel here.\n\n"
    "Testing authorizations can expire after seven days. Production may require Google's verification; it does not guarantee indefinite authorization.\n"
    "The desktop client is public configuration. Access and refresh tokens are stored only in macOS Keychain."
)


class AccountWorker(QThread):
    result = Signal(object)
    progress = Signal(str)

    def __init__(
        self, service: AccountService, action: str, argument: Any, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.service, self.action, self.argument = service, action, argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            result = self.service.execute(
                self.action, self.argument, self.cancel, self.progress.emit
            )
        except Exception:
            result = AccountView(
                "Verification pending",
                "Account operation failed safely. Retry after checking storage and network access.",
                False,
            )
        self.result.emit(result)


class AccountsPage(QScrollArea):
    idle = Signal()
    state_changed = Signal(object)

    def __init__(self, service: AccountService) -> None:
        super().__init__()
        self.service = service
        self.worker: AccountWorker | None = None
        self.closing = False
        self.loaded = False
        self.view = AccountView(
            "Disconnected", "Choose a Desktop client configuration, then connect YouTube.", False
        )
        self.setObjectName("accounts")
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.addWidget(text("Accounts", "Title"))
        layout.addWidget(
            text(
                "Connect the accounts you publish to. YouTube connects directly with Google; TikTok connects through your Buffer account. Publishing always needs separate consent and confirmation.",
                "Muted",
            )
        )
        card = QFrame()
        card.setObjectName("Card")
        self.card, self.content_layout = card, layout
        body = QVBoxLayout(card)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(16)
        body.addWidget(text("YouTube", "Heading"))
        self.status = text("Disconnected", "Badge")
        self.feedback = text(self.view.message, "Muted")
        self.avatar = text("")
        body.addWidget(self.avatar)
        self.details = text("")
        self.details.setTextInteractionFlags(self.details.textInteractionFlags())
        self.config_status = text("Desktop client not configured", "Muted")
        for item in (self.status, self.feedback, self.details, self.config_status):
            body.addWidget(item)
        self.diagnostics = Disclosure("Channel details")
        self.technical = text("")
        self.diagnostics.body.addWidget(self.technical)
        body.addWidget(self.diagnostics)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        body.addWidget(self.progress)
        self.configure_button = QPushButton("Choose Desktop Client JSON…")
        self.connect_button = QPushButton("Connect YouTube")
        self.refresh_button = QPushButton("Refresh Channel")
        self.disconnect_button = QPushButton("Disconnect…")
        self.upload_permission = QPushButton("Authorize Uploads…")
        self.upload_permission.clicked.connect(self.confirm_upload_permission)
        self.cancel_button = QPushButton("Cancel")
        self.channels = QComboBox()
        self.channels.setMinimumWidth(0)
        self.channels.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.channels.setMinimumContentsLength(10)
        self.channels.setAccessibleName("Authorized channel to confirm")
        self.confirm_button = QPushButton("Confirm Selected Channel")
        self.choice_details = text("")
        self.channels.currentIndexChanged.connect(self.show_choice)
        body.addWidget(self.choice_details)
        for widget in (
            self.configure_button,
            self.connect_button,
            self.refresh_button,
            self.disconnect_button,
            self.upload_permission,
            self.channels,
            self.confirm_button,
            self.cancel_button,
        ):
            body.addWidget(widget)
        self.configure_button.clicked.connect(self.choose_config)
        self.connect_button.clicked.connect(lambda: self.start("connect"))
        self.refresh_button.clicked.connect(lambda: self.start("refresh"))
        self.disconnect_button.clicked.connect(self.confirm_disconnect)
        self.cancel_button.clicked.connect(self.cancel)
        self.confirm_button.clicked.connect(
            lambda: self.start("select", self.channels.currentData())
        )
        layout.addWidget(card)
        self.setup_guide = Disclosure("Google Cloud setup and connection options")
        guide = self.setup_guide
        guide_layout = guide.body
        guide_layout.addWidget(self.configure_button)
        guide_layout.addWidget(text(SETUP, "Muted"))
        layout.addWidget(guide)
        layout.addStretch()
        self.freshness = QTimer(self)
        self.freshness.setSingleShot(True)
        self.freshness.setInterval(5 * 60 * 1000)
        self.freshness.timeout.connect(self.expire_verification)
        primary(self.connect_button)
        primary(self.confirm_button)
        self.present(self.view)

    def activate(self) -> None:
        if not self.loaded:
            self.start("restore")

    @Slot()
    def choose_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Google Desktop OAuth client", "", "JSON configuration (*.json)"
        )
        if path:
            self.start("configure", Path(path))

    def start(self, action: str, argument: Any = None) -> None:
        if self.worker is not None or self.closing:
            return
        self.freshness.stop()
        self.worker = AccountWorker(self.service, action, argument, self)
        self.worker.result.connect(self.present)
        self.worker.progress.connect(self.feedback.setText)
        self.worker.finished.connect(self.finished)
        self.status.setText("Connecting" if action == "connect" else "Verification pending")
        self.feedback.setText(
            "Starting browser authorization…" if action == "connect" else "Working…"
        )
        self.set_busy(True)
        self.state_changed.emit(
            AccountView(
                "Verification pending",
                "Account operation in progress",
                self.view.configured,
                self.view.connection,
            )
        )
        self.worker.start()

    def set_busy(self, busy: bool) -> None:
        for widget in (
            self.configure_button,
            self.connect_button,
            self.refresh_button,
            self.disconnect_button,
            self.upload_permission,
            self.channels,
            self.confirm_button,
        ):
            widget.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy or bool(self.view.choices))
        # Committed storage mutations finish atomically; cancel is for authorization/verification.
        self.cancel_button.setEnabled(
            not busy
            or bool(
                self.worker
                and self.worker.action in ("connect", "authorize_upload", "restore", "refresh")
            )
        )

    @Slot(object)
    def present(self, view: AccountView) -> None:
        self.view = view
        self.avatar.clear()
        self.avatar.hide()
        if view.thumbnail:
            buffer = QBuffer()
            buffer.setData(QByteArray(view.thumbnail))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            size = reader.size()
            if 0 < size.width() <= 2048 and 0 < size.height() <= 2048:
                reader.setScaledSize(QSize(72, 72))
                image = reader.read()
                if not image.isNull():
                    self.avatar.setPixmap(QPixmap.fromImage(image))
                    self.avatar.show()
        self.status.setText(view.state)
        self.feedback.setText(view.message)
        self.config_status.setText(
            "Desktop client configured"
            if view.configured
            else "Desktop client not configured or unavailable"
        )
        self.setup_guide.set_expanded(not view.configured)
        connection = view.connection
        self.details.setVisible(connection is not None)
        if connection:
            channel = connection.channel
            self.details.setText(channel.title + (f" · {channel.handle}" if channel.handle else ""))
            self.technical.setText(
                f"Channel ID: {channel.id}\n{channel.description}\n"
                f"Last verified: {timestamp(connection.verified_at)}\n"
                f"Verified (UTC): {connection.verified_at}"
            )
        self.diagnostics.setVisible(connection is not None)
        self.configure_button.setVisible(connection is None and not view.choices)
        self.connect_button.setVisible(connection is None and not view.choices)
        self.refresh_button.setVisible(connection is not None)
        self.disconnect_button.setVisible(connection is not None)
        self.upload_permission.setVisible(connection is not None)
        self.upload_permission.setText(
            "Reauthorize Upload Permission…" if view.upload_allowed else "Authorize Uploads…"
        )
        self.channels.clear()
        for channel in view.choices:
            self.channels.addItem(f"{channel.title} · {channel.id}", channel.id)
        self.show_choice()
        self.channels.setVisible(bool(view.choices))
        self.confirm_button.setVisible(bool(view.choices))
        if view.state.startswith("Connected") and connection:
            age = (
                datetime.now(UTC) - datetime.fromisoformat(connection.verified_at)
            ).total_seconds()
            self.freshness.start(max(1, int((300 - age) * 1000)))
        elif view.choices:
            self.freshness.start(300_000)
        self.set_busy(self.worker is not None)
        self.state_changed.emit(view)

    @Slot()
    def show_choice(self) -> None:
        channel = next(
            (item for item in self.view.choices if item.id == self.channels.currentData()), None
        )
        self.choice_details.setVisible(channel is not None)
        self.choice_details.setText(
            f"Confirm channel: {channel.title}\nChannel ID: {channel.id}\nHandle / identifier: {channel.handle or 'Not provided'}"
            if channel
            else ""
        )

    @Slot()
    def finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.loaded = True
        if self.closing:
            self.service.pending = None
        self.set_busy(False)
        self.idle.emit()

    @Slot()
    def cancel(self) -> None:
        if self.worker:
            self.worker.cancel.set()
            self.feedback.setText("Cancelling… waiting for the current bounded request to finish.")
        else:
            self.start("discard")

    @Slot()
    def confirm_upload_permission(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Authorize upload permission",
                "Google will request read-only YouTube access plus permission to upload videos. This preserves your current connection if consent fails. Nothing uploads until you separately confirm a reviewed video. Continue?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.start("authorize_upload")

    @Slot()
    def confirm_disconnect(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Disconnect YouTube")
        box.setText(
            "Remove this app's local authorization? Videos, publishing drafts and reviews will be kept."
        )
        revoke = QCheckBox("Also revoke Google access for this Cloud project")
        box.setCheckBox(revoke)
        box.setInformativeText(
            "Revocation can affect other permissions granted to the same Google Cloud project. If offline, only local removal can be confirmed."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.start("disconnect", revoke.isChecked())

    @Slot()
    def expire_verification(self) -> None:
        if self.view.choices and self.worker is None:
            self.start("discard")
            return
        if self.view.connection and self.worker is None:
            self.present(
                AccountView(
                    "Verification pending",
                    "Last verification is over five minutes old. Refresh Channel to verify again; no background polling occurs.",
                    self.view.configured,
                    self.view.connection,
                )
            )

    def request_close(self) -> bool:
        self.closing = True
        self.service.pending = None
        if self.worker:
            self.worker.cancel.set()
            self.feedback.setText("Finishing account operation before closing…")
            return False
        return True

    @Slot()
    def stop_for_quit(self) -> None:
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait()
        self.service.pending = None
