"""Accounts presentation; all OAuth, network and secret storage runs off the GUI thread."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..accounts import AccountService, AccountView
from . import theme
from .components import (
    CardGrid,
    Checklist,
    FlowLayout,
    Section,
    card,
    guide,
    header,
    icon_button,
    pill,
    platform_top,
    restyle,
    row,
)
from .formatting import timestamp
from .icons import PLATFORM_NAMES, image, logo, pixmap
from .videos import text

SETUP = (
    (
        "In Google Cloud Console, create a project and turn on YouTube Data API v3.",
        "Open YouTube API",
        "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    ),
    (
        "Set up the Google Auth Platform and add your Google account as a test user (while it "
        "is in Testing, sign-ins expire after seven days).",
        "Open Test Users",
        "https://console.cloud.google.com/auth/audience",
    ),
    (
        "Create an OAuth client of type Desktop app and download its JSON file.",
        "Open Clients",
        "https://console.cloud.google.com/auth/clients",
    ),
    (
        "Choose the downloaded file below. Your sign-in is kept only in the macOS Keychain.",
        "",
        "",
    ),
)
# Messages that only repeat what the setup checklist already says.
ROUTINE = {
    "Checking your YouTube connection…",
    "First choose your Google Desktop client JSON in Settings (one-time setup), then connect.",
    "Connect YouTube to verify your channel. Only read-only access is requested.",
    "Desktop client configured. Connect YouTube to authorize read-only access.",
}

# Account states in plain words, with the pill colour for each.
STATES = {
    "Disconnected": ("Not connected", "Neutral"),
    "Confirm channel": ("Choose channel", "Info"),
    "Connecting": ("Connecting…", "Info"),
    "Verification pending": ("Checking…", "Info"),
    "Temporarily offline": ("Offline", "Warn"),
    "Reconnection required": ("Sign in again", "Bad"),
}


def state_label(state: str) -> tuple[str, str]:
    return (
        ("Connected", "Good")
        if state.startswith("Connected")
        else STATES.get(state, (state, "Info"))
    )


def coming_soon(platform: str) -> QFrame:
    """A compact card for a platform that is not supported yet."""
    frame, body = card(18, 10)
    top = QHBoxLayout()
    top.setSpacing(12)
    top.addWidget(image(logo(platform, 32)))
    top.addWidget(text(PLATFORM_NAMES[platform], "Subheading"), 1)
    connect = icon_button("Connect", "link")
    connect.setEnabled(False)
    connect.setToolTip(f"Uploading to {PLATFORM_NAMES[platform]} arrives in a future version")
    top.addWidget(connect)
    body.addLayout(top)
    return frame


def rounded(source: QPixmap, side: int) -> QPixmap:
    """A circular avatar."""
    ratio = 2.0
    result = QPixmap(int(side * ratio), int(side * ratio))
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, result.width(), result.height())
    painter.setClipPath(path)
    painter.drawPixmap(
        result.rect(),
        source.scaled(
            result.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ),
    )
    painter.end()
    result.setDevicePixelRatio(ratio)
    return result


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
    setup_requested = Signal()
    # Set by the window: True while an upload is running or one is due within two minutes.
    hold_refresh: Callable[[], bool] = staticmethod(lambda: False)

    def __init__(self, service: AccountService) -> None:
        super().__init__()
        self.service = service
        self.worker: AccountWorker | None = None
        # The regular re-check runs quietly; a click made meanwhile runs right after it.
        self.quiet = False
        self.queued: tuple[str, Any] | None = None
        self.closing = False
        self.loaded = False
        # The saved connection has been looked at (or there is no library to hold one yet).
        self.checked = False
        self.view = AccountView("Disconnected", "Checking your YouTube connection…", False)
        self.setObjectName("accounts")
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)
        self.content_layout = layout
        layout.addWidget(
            header(
                "Accounts", "Connect the platforms you upload to. Nothing uploads until you say so."
            )[0]
        )
        self.grid = CardGrid()
        layout.addWidget(self.grid)
        # Not yet supported: one even row of three, apart from the platforms that work.
        layout.addWidget(text("COMING SOON", "Eyebrow"))
        self.soon = CardGrid(minimum=200, maximum=3)
        layout.addWidget(self.soon)
        self.card, body = card(22, 14)
        self.status = pill("Not connected")
        body.addLayout(platform_top("youtube", "Uploads straight to your channel", self.status))
        account = QHBoxLayout()
        account.setSpacing(12)
        self.avatar = QLabel()
        self.avatar.setFixedSize(44, 44)
        account.addWidget(self.avatar)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.details = text("", "Subheading")
        self.permission = text("", "Muted")
        names.addWidget(self.details)
        names.addWidget(self.permission)
        account.addLayout(names, 1)
        self.account_row = QWidget()
        self.account_row.setLayout(account)
        body.addWidget(self.account_row)
        self.feedback = text(self.view.message, "Muted")
        body.addWidget(self.feedback)
        self.checklist = Checklist(
            [
                (
                    "Add your Google client file",
                    "Open Setup",
                    lambda: self.setup_requested.emit(),
                ),
                ("Connect your YouTube channel", "Connect", lambda: self.start("connect")),
                (
                    "Allow uploads",
                    "Allow Uploads",
                    self.confirm_upload_permission,
                ),
            ]
        )
        body.addWidget(self.checklist)
        # Kept for tooltips and support; channel IDs are not shown on the card itself.
        self.technical = text("")
        self.technical.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        body.addWidget(self.progress)
        self.channels = QComboBox()
        self.channels.setMinimumWidth(0)
        self.channels.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.channels.setMinimumContentsLength(10)
        self.channels.setAccessibleName("Authorized channel to confirm")
        self.choice_details = text("", "Muted")
        self.channels.currentIndexChanged.connect(self.show_choice)
        body.addWidget(self.choice_details)
        body.addWidget(self.channels)
        self.configure_button = icon_button("Choose Google Client File…", "folder")
        self.connect_button = icon_button("Connect YouTube", "link", role="Primary")
        self.confirm_button = icon_button("Use This Channel", "check", role="Primary")
        self.upload_permission = icon_button("Allow Uploads…", "upload")
        self.upload_permission.clicked.connect(self.confirm_upload_permission)
        self.refresh_button = icon_button("Check Again", "refresh")
        self.disconnect_button = icon_button("Disconnect…", "logout", role="Danger")
        self.cancel_button = icon_button("Cancel", "x")
        self.setup_button = icon_button("Open Setup", "settings", role="Ghost")
        self.setup_button.clicked.connect(self.setup_requested.emit)
        actions = QWidget()
        actions.setObjectName("ActionRow")
        flow = FlowLayout(actions)
        for widget in (
            self.connect_button,
            self.confirm_button,
            self.upload_permission,
            self.refresh_button,
            self.disconnect_button,
            self.cancel_button,
            self.setup_button,
        ):
            flow.addWidget(widget)
        body.addStretch()
        body.addWidget(actions)
        self.configure_button.clicked.connect(self.choose_config)
        self.connect_button.clicked.connect(lambda: self.start("connect"))
        self.refresh_button.clicked.connect(lambda: self.start("refresh"))
        self.disconnect_button.clicked.connect(self.confirm_disconnect)
        self.cancel_button.clicked.connect(self.cancel)
        self.confirm_button.clicked.connect(
            lambda: self.start("select", self.channels.currentData())
        )
        self.grid.add(self.card)
        layout.addStretch()
        # One-time developer setup lives in Settings; the window places this section there.
        self.setup_guide = Section(
            "YouTube connection",
            "ShortsScheduler signs in to YouTube with your own Google Cloud app. Do this once.",
            ("youtube",),
        )
        self.setup_guide.body.addWidget(guide(SETUP))
        self.setup_status = pill("Not set up", "Warn")
        self.setup_guide.body.addWidget(row(self.configure_button, self.setup_status))
        self.freshness = QTimer(self)
        self.freshness.setSingleShot(True)
        self.freshness.setInterval(5 * 60 * 1000)
        self.freshness.timeout.connect(self.expire_verification)
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

    def start(self, action: str, argument: Any = None, *, quiet: bool = False) -> None:
        if self.worker is not None and self.quiet and not quiet and not self.closing:
            self.queued = (action, argument)
            return
        if self.worker is not None or self.closing:
            return
        self.freshness.stop()
        self.quiet = quiet
        self.worker = AccountWorker(self.service, action, argument, self)
        self.worker.result.connect(self.present)
        self.worker.finished.connect(self.finished)
        if quiet:
            # Buttons, status and the rest of the app stay as they are unless the result
            # differs, so the five-minute check never flickers anything.
            self.worker.start()
            return
        self.worker.progress.connect(self.feedback.setText)
        self.status.setText(
            state_label("Connecting" if action == "connect" else "Verification pending")[0]
        )
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
        self.avatar.setPixmap(pixmap("users", 22, theme.MUTED))
        if view.thumbnail:
            buffer = QBuffer()
            buffer.setData(QByteArray(view.thumbnail))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            size = reader.size()
            if 0 < size.width() <= 2048 and 0 < size.height() <= 2048:
                reader.setScaledSize(QSize(88, 88))
                image = reader.read()
                if not image.isNull():
                    self.avatar.setPixmap(rounded(QPixmap.fromImage(image), 44))
        shown, tone = state_label(view.state)
        self.status.setText(shown)
        restyle(self.status, tone)
        self.feedback.setText(view.message)
        connection = view.connection
        self.setup_button.setVisible(False)  # the checklist offers setup when it is needed
        self.setup_status.setText("Set up" if view.configured else "Not set up")
        restyle(self.setup_status, "Good" if view.configured else "Warn")
        self.account_row.setVisible(connection is not None)
        self.details.setVisible(connection is not None)
        if connection:
            channel = connection.channel
            self.details.setText(channel.title + (f" · {channel.handle}" if channel.handle else ""))
            self.technical.setText(
                f"Channel ID: {channel.id}\n{channel.description}\n"
                f"Last verified: {timestamp(connection.verified_at)}\n"
                f"Verified (UTC): {connection.verified_at}"
            )
            self.details.setToolTip(self.technical.text())
            self.permission.setText(
                "Uploads allowed · checked " + timestamp(connection.verified_at)
                if view.upload_allowed
                else "Uploads not allowed yet · choose Allow Uploads"
            )
        self.checklist.update_steps(
            [view.configured or connection is not None, connection is not None, view.upload_allowed]
        )
        guided = not self.checklist.complete and not view.choices
        self.checklist.setVisible(guided)
        # The checklist says what to do next; the message only adds something when it matters.
        # A plain "verified" note on a working connection would only make this card taller.
        settled = bool(
            connection and view.upload_allowed and view.message.startswith("Channel verified")
        )
        self.feedback.setVisible((not guided or view.message not in ROUTINE) and not settled)
        self.connect_button.setVisible(connection is None and not view.choices and not guided)
        self.refresh_button.setVisible(connection is not None)
        self.disconnect_button.setVisible(connection is not None)
        self.upload_permission.setVisible(connection is not None and not guided)
        # Same size and style as the other buttons, so the row stays even.
        self.upload_permission.setText(
            "Sign In Again…" if view.upload_allowed else "Allow Uploads…"
        )
        self.upload_permission.setToolTip(
            "Sign in to Google again to renew upload permission" if view.upload_allowed else ""
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
            f"Choose the channel to upload to: {channel.title}"
            + (f" · {channel.handle}" if channel.handle else "")
            if channel
            else ""
        )

    @Slot()
    def finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.loaded = self.checked = True
        if self.closing:
            self.service.pending = None
        if not self.quiet:
            self.set_busy(False)
        self.quiet = False
        if self.queued and not self.closing:
            action, argument = self.queued
            self.queued = None
            self.start(action, argument)
            return
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
            if not self.closing and not self.hold_refresh():
                # Check again on its own, so uploads never wait for a manual "Check Again".
                self.start("refresh", quiet=True)
                return
            self.freshness.start(60_000)  # an upload is about to use the account; try later
            self.present(
                AccountView(
                    "Verification pending",
                    "Checking again shortly, once the upload that is due has started.",
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
