"""Posting through Buffer — TikTok, Instagram, Facebook: one controller per platform with its
Accounts card, video publishing page, automation card and queue card.

The TikTok controller also owns the Buffer-wide Settings (app client, view-count key, video
hosting) shared by every Buffer platform. All Buffer, Cloudinary, Keychain and SQLite work runs on
a worker thread. Only values that Buffer's public API can actually control are offered.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..buffer import (
    CALLBACK_PATH,
    CALLBACK_PORT,
    BufferError,
    CallbackListener,
    authorization_url,
    parse_callback,
    pkce,
)
from ..cadence import Rule
from ..hosting import HostingError, cloudinary_fields
from ..social import (
    HOSTING_CONSENT,
    MAIN,
    TIKTOK,
    BufferPostService,
    Platform,
    SocialError,
    caption_errors,
    caption_from,
    usable_channels,
)
from .components import (
    Checklist,
    Section,
    card,
    day_row,
    flow,
    guide,
    icon_button,
    initial_avatar,
    natural,
    open_page,
    pill,
    platform_top,
    primary,
    restyle,
    row,
)
from .formatting import handle, in_an_hour, picked, system_zone, time_pattern, timestamp
from .icons import PLATFORM_NAMES, image, logo
from .videos import text
from .watching import FolderConsent

if TYPE_CHECKING:
    from .window import MainWindow

logger = logging.getLogger(__name__)

BUFFER_CHANNELS = "https://account.buffer.com/channels"
BUFFER_API = "https://publish.buffer.com/settings/api"
CLOUDINARY_SIGN_UP = "https://cloudinary.com/users/register_free"
CLOUDINARY_KEYS = "https://console.cloudinary.com/app/settings/api-keys"
SETUP = (
    (
        "In Buffer, connect the accounts you want to post to: TikTok, an Instagram Business or "
        "Creator account, and a Facebook Page (Buffer can't post to Facebook profiles or Groups "
        "automatically).",
        "Open Buffer Channels",
        BUFFER_CHANNELS,
    ),
    (
        "In Buffer's API settings, open App Clients and create a PUBLIC client with the "
        "permissions account:read, posts:read and posts:write.",
        "Open Buffer API",
        BUFFER_API,
    ),
    (
        "Host the relay page from docs/buffer-oauth-relay.html at an HTTPS address you control "
        "(for example GitHub Pages) and register that exact address as the client's redirect "
        "URI. Buffer requires HTTPS even for localhost; the relay forwards the sign-in back to "
        f"this app on http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}.",
        "",
        "",
    ),
    ("Enter the Client ID and the redirect address below. There is no client secret.", "", ""),
)
HOSTING_STEPS = (
    ("Create a free Cloudinary account.", "Sign Up", CLOUDINARY_SIGN_UP),
    (
        "On the API Keys page, copy the API environment variable (it starts with cloudinary://) "
        "and paste it into any field below; all three fill in.",
        "Open API Keys",
        CLOUDINARY_KEYS,
    ),
    ("Tick the box to confirm hosted copies are public by link, then Save.", "", ""),
)
UNSUPPORTED = {
    "tiktok": (
        "Buffer posts TikTok videos publicly by default. Buffer's API cannot set TikTok privacy, "
        "comments, Duet, Stitch or commercial-content disclosure. Videos with TikTok music, "
        "trending sounds or polls cannot be posted automatically."
    ),
    "instagram": (
        "Buffer posts Instagram Reels publicly, and they also appear on your profile grid. "
        "Automatic posting needs an Instagram Business or Creator account; with a personal account "
        "Buffer only sends a phone reminder. Tagged people, collaborators, topics, product tags "
        "and Instagram music can't be added through Buffer's API."
    ),
    "facebook": (
        "Buffer posts Facebook Reels publicly to your Page. Reels can't have a location or a "
        "first comment, and Facebook's API has no AI-generated content label."
    ),
}
BUFFER_KEYS = ("tiktok", "instagram", "facebook")
# What each platform's destination is called where the user connects it.
DESTINATION = {
    "tiktok": "TikTok account",
    "instagram": "Instagram Business or Creator account",
    "facebook": "Facebook Page",
}
ACTIVE_STATES = (
    "Scheduled in Buffer",
    "Hosted and ready",
    "Waiting for Buffer limit",
    "Waiting for TikTok reconnection",
    "Waiting for Instagram reconnection",
    "Waiting for Facebook reconnection",
    "Hosting failed",
)
# What `BufferPostService.cancel` accepts: nothing Buffer has started publishing.
CANCELLABLE = (
    "Scheduled in Buffer",
    "Hosted and ready",
    "Waiting for Buffer limit",
    "Waiting for TikTok reconnection",
    "Waiting for Instagram reconnection",
    "Waiting for Facebook reconnection",
    "Hosting failed",
)
RELEASABLE = (
    "Failed",
    "Needs reconciliation",
    "Needs attention",
    "Hosting failed",
    "Removed in Buffer",
    "Manual completion required",
)


class SocialWorker(QThread):
    result = Signal(object)

    def __init__(self, job: Callable[[Event], Any], name: str, parent: QObject) -> None:
        super().__init__(parent)
        self.job, self.name, self.cancel = job, name, Event()

    def run(self) -> None:
        try:
            value = self.job(self.cancel)
            self.result.emit({"ok": value})
        except (SocialError, BufferError, HostingError) as exc:
            self.result.emit({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surfaced, never silently dropped
            self.result.emit({"error": f"{self.name} operation failed safely: {exc}"})


def label_for(post: dict[str, Any]) -> str:
    when = (
        timestamp(post["due_at"], weekday=True)
        if post["due_at"]
        else timestamp(post["created_at"], weekday=True)
    )
    # Same three lines as a YouTube upload: title, time, account · state.
    state = "Posted" if post["state"] == "Sent by Buffer" else post["state"]
    return f"{post['caption'][:60] or 'Untitled'}\n{when}\n{post['channel_name']} · {state}"


class SocialController(QObject):
    idle = Signal()
    changed = Signal()
    # After a user action that may touch what Buffer platforms share (app client, hosting,
    # view-count key, a shared sign-in); the window reloads the other platforms.
    buffer_changed = Signal()

    def __init__(
        self, service: BufferPostService, window: MainWindow, *, owner: bool = False
    ) -> None:
        super().__init__(window)
        self.service, self.window = service, window
        self.spec: Platform = service.spec
        self.key, self.name = self.spec.key, self.spec.name
        # The owner's Buffer-wide Settings are the ones shown, and only it reads the shared
        # view-count key from Keychain.
        self.owner = owner
        self.worker: SocialWorker | None = None
        self.closing = False
        self.loaded = False  # True once the first check of the saved connection has finished
        self.pending: list[tuple[Callable[[Event], Any], Callable[[Any], None] | None, bool]] = []
        self.error_box: QMessageBox | None = None
        self.data: dict[str, Any] = {
            "insights": False,
            "connection": None,
            "client": None,
            "accounts": [],
            "account": MAIN,
            "hosting": None,
            "hosts": [],
            "posts": [],
            "profiles": [],
            "shared": None,
        }
        self.video_id: int | None = None
        self.usage: dict[str, str] = {}  # last checked usage per Cloudinary account
        self.host_usage: dict[str, dict[str, Any]] = {}  # the same, as numbers (Settings meters)
        self.summary = text(f"{self.name} · checking…", "Muted")
        self.build_card()
        self.build_hosting()
        self.build_video()
        self.build_queue()
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.poll)
        QTimer.singleShot(0, self.activate)

    # Plumbing -------------------------------------------------------------------------------

    def run(
        self,
        job: Callable[[Event], Any],
        done: Callable[[Any], None] | None = None,
        *,
        quiet: bool = False,
    ) -> None:
        """quiet: background refreshes report errors inline only; user actions also get a dialog."""
        if self.closing:
            return
        if self.worker:
            self.pending.append((job, done, quiet))
            return
        self.worker = SocialWorker(job, self.name, self)
        self.worker.result.connect(lambda result: self.finished(result, done, quiet))
        if not quiet:
            # Background refreshes leave the buttons alone, so nothing flickers; a click made
            # meanwhile waits in `pending`.
            self.set_busy(True)
        self.worker.start()

    def finished(
        self, result: dict[str, Any], done: Callable[[Any], None] | None, quiet: bool = False
    ) -> None:
        assert self.worker
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        self.loaded = True
        if not quiet:
            self.set_busy(False)
        if self.closing:
            self.idle.emit()
            return
        if "error" in result:
            logger.warning("%s operation needs attention: %s", self.name, result["error"])
            self.feedback(result["error"])
            if not quiet:
                self.alert(result["error"])
        elif done:
            done(result["ok"])
        if self.pending:
            job, after, silent = self.pending.pop(0)
            self.run(job, after, quiet=silent)
        else:
            self.idle.emit()

    def alert(self, message: str) -> None:
        # Window-modal and non-blocking, so background work and tests never stall on it.
        self.feedback(message)
        self.error_box = QMessageBox(
            QMessageBox.Icon.Warning, self.name, message, QMessageBox.StandardButton.Ok, self.window
        )
        self.error_box.open()

    def activate(self) -> None:
        if not self.closing:
            self.timer.start()
            self.run(self.snapshot, self.render, quiet=True)

    def poll(self) -> None:
        if not self.worker:
            self.run(self.refreshed, self.render_changed, quiet=True)

    def render_changed(self, data: dict[str, Any]) -> None:
        """A regular refresh is drawn only when something it shows has changed."""
        if data != self.data:
            self.render(data)

    def refreshed(self, cancel: Event) -> dict[str, Any]:
        self.service.refresh(cancel)
        self.service.cleanup()
        return self.snapshot(cancel)

    def snapshot(self, cancel: Event) -> dict[str, Any]:
        try:
            client = self.service.client()
        except BufferError:
            client = None
        connection = self.service.connection()
        return {
            "client": client,
            "accounts": self.service.buffer_accounts() if client else [],
            "account": self.service.account_of(connection["key"]) if connection else MAIN,
            "connection": connection,
            "hosting": self.service.hosting(),
            "hosts": self.service.hosts(),
            "posts": self.service.posts(),
            "profiles": self.service.profiles(),
            "decisions": {
                p["id"]: self.service.decisions(p["id"]) for p in self.service.profiles()
            },
            "folders": self.window.watching.service.folders(),
            # The view-count key is shared; only the owner reads it from Keychain.
            "insights": self.owner and bool(self.service.insights_key()),
            "shared": self.service.shared_sign_in(),
            "working": self.service.working_sign_in(),
        }

    def reload(self, message: str = "") -> Callable[[Any], None]:
        def done(_: Any) -> None:
            if message:
                self.feedback(message)
            self.run(self.snapshot, self.render, quiet=True)
            self.buffer_changed.emit()

        return done

    def sync(self) -> None:
        """Another Buffer platform changed shared settings or sign-ins; re-read them."""
        self.run(self.snapshot, self.render_changed, quiet=True)

    def feedback(self, message: str) -> None:
        for label in (
            self.card_feedback,
            self.hosting_feedback,
            self.video_feedback,
            self.queue_feedback,
        ):
            label.setText(message)
        self.card_feedback.setVisible(bool(message))
        self.queue_feedback.setVisible(bool(message))

    def set_busy(self, busy: bool) -> None:
        for button in self.buttons():
            button.setEnabled(not busy)
        if not busy:
            self.render(None)

    def buttons(self) -> list[QPushButton]:
        return [
            self.connect_button,
            self.disconnect_button,
            self.save_client_button,
            self.add_buffer_button,
            self.save_insights_button,
            self.remove_insights_button,
            self.save_hosting_button,
            self.test_hosting_button,
            self.remove_hosting_button,
            self.add_host_button,
            self.check_hosts_button,
            self.save_draft_button,
            self.publish_button,
            self.schedule_button,
            self.refresh_button,
            self.cancel_post_button,
            self.release_button,
            self.queue_refresh,
            self.queue_cancel,
        ]

    def request_close(self) -> bool:
        self.timer.stop()
        self.closing = True
        if self.worker:
            self.worker.cancel.set()
            return False
        return True

    def stop_for_quit(self) -> None:
        self.request_close()
        if self.worker:
            self.worker.wait()

    # Rendering ------------------------------------------------------------------------------

    def render(self, data: dict[str, Any] | None) -> None:
        if data is not None:
            self.data = data
        d = self.data
        connection, client, hosting = d["connection"], d["client"], d["hosting"]
        connected = bool(connection and connection.get("status") == "Connected")
        self.card_status.setText(
            "Connected" if connected else "Sign in again" if connection else "Not connected"
        )
        restyle(self.card_status, "Good" if connected else "Bad" if connection else "Neutral")
        self.card_account.setVisible(bool(connection))
        self.card_avatar.setVisible(bool(connection))
        self.card_account.setText(f"{handle(self.key, connection)} · through Buffer")
        if connection:
            self.card_avatar.setPixmap(initial_avatar(handle(self.key, connection)))
        if connection:
            self.card_detail.setText(
                "Ready to post once video hosting is set up in Settings."
                if connected and not hosting
                else "Ready to post."
                if connected
                else "Buffer needs you to sign in again before anything else is posted."
            )
        else:
            self.card_detail.setText(
                f"Connect your {DESTINATION[self.key]} through Buffer to post and schedule. "
                "One Buffer sign-in connects TikTok, Instagram and Facebook together."
                if client
                else "Needs a one-time Buffer app client setup in Settings first."
            )
        # Short once connected, so every account card's buttons fit on one even row.
        self.connect_button.setText(
            "Reconnect…" if connection else f"Connect {self.name} via Buffer…"
        )
        self.connect_button.setToolTip(f"Reconnect {self.name} via Buffer" if connection else "")
        restyle(self.connect_button, "" if connected else "Primary")
        self.connect_button.setEnabled(bool(client) and not self.worker)
        self.disconnect_button.setVisible(bool(connection))
        steps = [bool(client), connected, bool(hosting)]
        for checklist in (self.checklist, self.video_checklist):
            checklist.update_steps(steps)
            checklist.setVisible(not checklist.complete)
        guided = not self.checklist.complete
        self.connect_button.setVisible(not guided)
        self.card_detail.setVisible(not guided)
        self.setup_button.setVisible(False)  # the checklist offers each setup step
        insights = bool(d.get("insights"))
        self.insights_status.setText("On" if insights else "Off")
        restyle(self.insights_status, "Good" if insights else "Neutral")
        self.remove_insights_button.setVisible(insights)
        self.client_status.setText("Set up" if client else "Not set up")
        restyle(self.client_status, "Good" if client else "Warn")
        self.technical.setText(
            (
                f"Buffer channel: {connection['id']}"
                + (f" ({connection['type']})" if connection.get("type") else "")
                + f"\nBuffer account: {self.account_label(d['account'])}"
                + f"\nOrganization: {connection['organization_id']}\nConnected: {timestamp(connection['verified_at'])}\n"
                if connection
                else ""
            )
            + (
                f"Client ID: {client.client_id}\nRedirect URI: {client.redirect_uri}"
                if client
                else "No Buffer app client saved."
            )
        )
        self.card_account.setToolTip(self.technical.text())
        if client and not self.client_id.text():
            self.client_id.setText(client.client_id)
            self.redirect.setText(client.redirect_uri)
        self.hosting_status.setText(
            f"Cloudinary · {hosting['cloud_name']} · "
            + (
                "hosted copies are deleted a day after Buffer reports the post sent"
                if hosting["retention"] == "delete_after_sent"
                else "hosted copies are kept"
            )
            if hosting
            else "Not set up yet. Needed before posting to TikTok, Instagram or Facebook."
        )
        self.remove_hosting_button.setVisible(bool(hosting))
        if hosting and not self.cloud_name.text():
            self.cloud_name.setText(hosting["cloud_name"])
            self.cloud_key.setText(hosting["api_key"])
        self.render_backups()
        self.render_video()
        self.render_queue()
        self.render_summary()
        self.changed.emit()

    def render_summary(self) -> None:
        connection = self.data["connection"]
        posts = [p for p in self.data["posts"] if not p["released"]]
        live = ("Scheduled in Buffer", "Publishing", "Sending to Buffer")
        scheduled = sum(p["state"] in live for p in posts)
        attention = sum(p["state"] in RELEASABLE for p in posts)
        enabled = sum(bool(p["enabled"] and not p["revoked"]) for p in self.data["profiles"])
        name = self.name
        if not connection:
            self.summary.setText(f"{name} · not connected (Accounts → Connect {name} via Buffer)")
        elif connection.get("status") != "Connected":
            self.summary.setText(f"{name} · reconnect required in Accounts")
        else:
            self.summary.setText(
                f"{name} · {handle(self.key, connection)} · {scheduled} scheduled or publishing · "
                f"{attention} need attention · {enabled} enabled automation"
                + ("s" if enabled != 1 else "")
                + ("" if self.data["hosting"] else " · media hosting not set up (Settings)")
            )

    # Accounts card --------------------------------------------------------------------------

    def build_card(self) -> None:
        self.card, body = card(22, 14)
        self.card_status = pill("Not connected")
        body.addLayout(
            platform_top(self.key, "Posts through your Buffer account", self.card_status)
        )
        # Laid out like YouTube's card: a round avatar, the account, then one line under it.
        self.card_avatar = QLabel()
        self.card_avatar.setFixedSize(44, 44)
        self.card_account = text("", "Subheading")
        self.card_detail = text("", "Muted")
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self.card_account)
        names.addWidget(self.card_detail)
        account = QHBoxLayout()
        account.setSpacing(12)
        account.addWidget(self.card_avatar)
        account.addLayout(names, 1)
        body.addLayout(account)
        self.card_feedback = text("", "Muted")
        self.card_feedback.hide()
        body.addWidget(self.card_feedback)
        self.checklist = self.setup_steps()
        body.addWidget(self.checklist)
        self.connect_button = icon_button(
            f"Connect {self.name} via Buffer…", "link", role="Primary"
        )
        self.connect_button.clicked.connect(self.connect_account)
        self.disconnect_button = icon_button("Disconnect…", "logout", role="Danger")
        self.disconnect_button.clicked.connect(self.disconnect_account)
        self.setup_button = icon_button("Open Setup", "settings", role="Ghost")
        self.setup_button.clicked.connect(lambda: self.window.navigate("settings"))
        body.addStretch()
        body.addWidget(flow(self.connect_button, self.disconnect_button, self.setup_button))
        # Kept for tooltips and support; IDs are not shown on the card itself.
        self.technical = text("", "Muted")
        self.technical.hide()
        # One-time developer setup lives in Settings; the window places this section there.
        self.client_setup = Section(
            "Buffer connection",
            "TikTok, Instagram and Facebook posts go through your own Buffer account. Do this once.",
            BUFFER_KEYS,
        )
        self.client_setup.body.addWidget(guide(SETUP))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.client_id = QLineEdit()
        self.client_id.setAccessibleName("Buffer Client ID")
        self.redirect = QLineEdit()
        self.redirect.setPlaceholderText(
            "https://you.github.io/shortspublisher/buffer-callback.html"
        )
        self.redirect.setAccessibleName("Registered HTTPS redirect URI")
        form.addRow("Client ID (public client, no secret)", self.client_id)
        form.addRow("Redirect address (HTTPS)", self.redirect)
        self.client_setup.body.addLayout(form)
        self.save_client_button = icon_button("Save Buffer App", "check")
        self.save_client_button.clicked.connect(self.save_client)
        self.client_status = pill("Not set up", "Warn")
        self.client_setup.body.addWidget(row(self.save_client_button, self.client_status))
        self.build_buffer_backups()
        # Optional: Buffer serves post view counts only to a personal API key.
        self.insights_setup = Section(
            "Buffer view counts (optional)",
            "Buffer only shares view counts with a personal API key, not with app sign-ins. "
            "Create one in Buffer → Settings → API, paste it here, and views of your TikTok, "
            "Instagram and Facebook posts appear on the Dashboard. It is stored in the macOS "
            "Keychain and only ever reads counts.",
            BUFFER_KEYS,
        )
        self.insights_key = QLineEdit()
        self.insights_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.insights_key.setPlaceholderText("Buffer personal API key")
        self.insights_key.setAccessibleName("Buffer personal API key")
        self.save_insights_button = icon_button("Save Key", "check")
        self.save_insights_button.clicked.connect(self.save_insights)
        self.remove_insights_button = icon_button("Remove", "trash", role="Ghost")
        self.remove_insights_button.clicked.connect(self.remove_insights)
        self.insights_status = pill("Off", "Neutral")
        self.remove_insights_button.hide()
        self.insights_setup.body.addWidget(self.insights_key)
        self.insights_setup.body.addWidget(
            row(self.save_insights_button, self.remove_insights_button, self.insights_status)
        )

    def build_buffer_backups(self) -> None:
        body = self.client_setup.body
        body.addWidget(text("Buffer accounts", "Subheading"))
        body.addWidget(
            text(
                "Add more Buffer accounts (separate Buffer sign-ups, each with its own limits). "
                "When one reaches a limit, posts automatically go through the next one, to the "
                "same TikTok, Instagram or Facebook account connected there too.",
                "Muted",
            )
        )
        self.buffer_rows = QVBoxLayout()
        self.buffer_rows.setSpacing(8)
        body.addLayout(self.buffer_rows)
        self.add_buffer_button = icon_button("Add Buffer Account…", "plus")
        self.add_buffer_button.clicked.connect(self.add_buffer_account)
        body.addWidget(row(self.add_buffer_button))

    def account_label(self, account: str) -> str:
        found = next((a for a in self.data["accounts"] if a["id"] == account), None)
        return found["label"] if found else "Main"

    @staticmethod
    def account_row(
        title: str, badge: str, status: str, tone: str, *buttons: QPushButton
    ) -> QWidget:
        """One account in a Settings list: name, main/backup, status and its own actions."""
        frame, body = card(14, 6, "Tile")
        frame.setAccessibleName(title)
        body.addWidget(text(title, "Subheading"))
        body.addWidget(row(pill(badge, "Neutral"), pill(status, tone)))
        if buttons:
            body.addWidget(row(*buttons))
        return frame

    @staticmethod
    def clear(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

    def render_backups(self) -> None:
        now = datetime.now(UTC)
        idle = not self.worker
        self.clear(self.buffer_rows)
        for account in self.data["accounts"]:
            retry = account["retry_after"]
            main = account["id"] == MAIN
            buttons = []
            if retry and datetime.fromisoformat(retry) > now:
                status, tone = f"Limit reached · used again {timestamp(retry)}", "Warn"
            elif account["key"]:
                status, tone = ("Signed in" if main else "Signed in · ready as a backup"), "Good"
            else:
                status, tone = (
                    ("Not signed in · connect a platform in Accounts", "Warn")
                    if main
                    else ("Not signed in · sign in to use it as a backup", "Warn")
                )
            if not main:
                sign = icon_button(
                    "Sign In Again…" if account["key"] else "Sign In…", "link", role="Ghost"
                )
                sign.clicked.connect(lambda _=False, a=account["id"]: self.sign_in(a, backup=True))
                remove = icon_button("Remove…", "trash", role="Ghost")
                remove.setAccessibleName(f"Remove {account['label']}…")
                remove.clicked.connect(
                    lambda _=False, a=account["id"]: self.remove_buffer_account(a)
                )
                buttons = [sign, remove]
                for button in buttons:
                    button.setEnabled(idle)
            self.buffer_rows.addWidget(
                self.account_row(
                    account["label"], "Main" if main else "Backup", status, tone, *buttons
                )
            )
        self.add_buffer_button.setEnabled(bool(self.data["client"]) and idle)
        self.clear(self.host_rows)
        for host in self.data["hosts"]:
            full = host["full_until"]
            if full and datetime.fromisoformat(full) > now:
                status, tone = f"Over its limits · tried again {timestamp(full)}", "Warn"
            else:
                status, tone = self.usage.get(host["credential_key"], "Ready"), "Good"
            buttons = []
            if not host["main"]:
                remove = icon_button("Remove…", "trash", role="Ghost")
                remove.setAccessibleName(f"Remove {host['cloud_name']}…")
                remove.setEnabled(idle)
                remove.clicked.connect(
                    lambda _=False, k=host["credential_key"]: self.remove_host(k)
                )
                buttons = [remove]
            self.host_rows.addWidget(
                self.account_row(
                    host["cloud_name"], "Main" if host["main"] else "Backup", status, tone, *buttons
                )
            )
        self.add_host_button.setEnabled(bool(self.data["hosting"]) and idle)
        self.check_hosts_button.setVisible(bool(self.data["hosts"]))

    def add_buffer_account(self) -> None:
        dialog = AddBufferAccount(self.window, len(self.data["accounts"]) + 1)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        label, cid, uri = dialog.values()

        def add(_: Event) -> dict[str, Any]:
            account = self.service.add_buffer_account(label, cid, uri)
            return next(a for a in self.service.buffer_accounts() if a["id"] == account)

        def added(account: dict[str, Any]) -> None:
            self.data["accounts"] = [*self.data["accounts"], account]
            self.reload()(None)
            self.sign_in(account["id"], backup=True)

        self.run(add, added)

    def remove_buffer_account(self, account: str) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Remove Buffer account?",
                f"Forget {self.account_label(account)} and its sign-in on this Mac? Posts stop "
                "falling back to it. Nothing in Buffer is changed.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(
                lambda c: self.service.remove_buffer_account(account),
                self.reload("Buffer account removed."),
            )

    def backup_signed_in(self, label: str) -> Callable[[dict[str, list[str]]], None]:
        def done(coverage: dict[str, list[str]]) -> None:
            found, missing = coverage["found"], coverage["missing"]
            message = (
                f"{label} is signed in. Posts fall back to it for "
                + (", ".join(found) if found else "no connected account yet")
                + "."
            )
            if missing:
                message += (
                    f" It doesn't have {', '.join(missing)} yet: connect "
                    + ("it" if len(missing) == 1 else "them")
                    + " in that Buffer account, then choose Sign In Again."
                )
            self.reload(message)(None)
            box = QMessageBox(
                QMessageBox.Icon.Information,
                "Buffer account",
                message,
                QMessageBox.StandardButton.Ok,
                self.window,
            )
            box.open()
            self.error_box = box

        return done

    def setup_steps(self) -> Checklist:
        """Three things are needed; the same steps appear wherever the platform is not ready."""
        return Checklist(
            [
                ("Add your Buffer app details", "Open Setup", lambda: self.open_setup("client")),
                (f"Connect your {DESTINATION[self.key]}", "Connect", self.connect_account),
                ("Set up video hosting", "Open Setup", lambda: self.open_setup("hosting")),
            ]
        )

    def open_setup(self, part: str) -> None:
        """Settings, scrolled to the Buffer app or video hosting section (shown once, by the
        TikTok controller)."""
        owner = getattr(self.window, "tiktok", self)
        target = owner.client_setup if part == "client" else owner.hosting
        opener = getattr(self.window, "open_setup", None)
        if opener:
            opener(target)
        else:
            self.window.navigate("settings")

    def save_insights(self) -> None:
        key = self.insights_key.text()
        self.insights_key.clear()
        self.run(
            lambda c: self.service.save_insights_key(key),
            self.reload("Buffer key saved. Views load on the Dashboard."),
        )

    def remove_insights(self) -> None:
        self.run(lambda c: self.service.remove_insights_key(), self.reload("Buffer key removed."))

    def save_client(self) -> None:
        cid, uri = self.client_id.text(), self.redirect.text()
        self.run(
            lambda c: self.service.save_client(cid, uri), self.reload("Buffer app client saved.")
        )

    def connect_account(self) -> None:
        """One Buffer sign-in connects TikTok, Instagram and Facebook: reuse one that works,
        otherwise sign in, then choose every account to connect in one window."""
        if not self.data["client"]:
            return
        key = self.data.get("working")
        if key:
            self.run(lambda c: (key, self.service.account_channels(key)), self.choose_accounts)
        else:
            self.sign_in()

    def sign_in(self, account: str = MAIN, *, backup: bool = False) -> None:
        """Buffer's sign-in in the browser. backup: sign in a backup Buffer account (Settings)
        instead of connecting platforms through the main one."""
        found = next((a for a in self.data["accounts"] if a["id"] == account), None)
        client = found["client"] if found and found.get("client") else self.data["client"]
        if not client:
            return
        label = found["label"] if found else "Main"
        dialog = ConnectDialog(self.window, self.spec)
        if backup:
            dialog.status.setText(
                f"Your browser opened Buffer's sign-in. Sign in with the Buffer account for "
                f"“{label}” (not your main one) and allow ShortsScheduler. If Buffer signs you in "
                "automatically, sign out of Buffer in the browser and choose Open Sign-In Page "
                "Again."
            )
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(32)
        url = authorization_url(client, state, challenge)
        try:
            listener: CallbackListener | None = CallbackListener()
        except BufferError as exc:
            listener = None
            dialog.status.setText(str(exc))
        cancel = Event()
        used = {"done": False}

        def wait(_: Event) -> str | None:
            assert listener
            try:
                return listener.wait(cancel)
            except BufferError:
                if cancel.is_set():
                    return None  # closed by the dialog or by a pasted code; not an error
                raise

        def returned(query: str | None) -> None:
            if query is None or used["done"]:
                return
            used["done"] = True
            cancel.set()
            dialog.accept()
            try:
                code = parse_callback(query, state)
            except BufferError as exc:
                self.feedback(str(exc))
                return
            if backup:
                self.run(
                    lambda c: self.service.sign_in_backup(account, code, verifier),
                    self.backup_signed_in(label),
                )
                return
            self.run(
                lambda c: self.service.finish_connect_all(code, verifier, account),
                self.choose_accounts,
            )

        dialog.open_buffer.clicked.connect(lambda: open_page(BUFFER_CHANNELS))
        dialog.sign_in.clicked.connect(lambda: open_page(url))
        dialog.use_pasted.clicked.connect(lambda: returned(dialog.pasted.toPlainText()))
        if listener:
            self.run(wait, returned)
        open_page(url)  # straight to Buffer's sign-in; the dialog waits for it to return
        if dialog.exec() != QDialog.DialogCode.Accepted and not used["done"]:
            cancel.set()
            self.feedback(
                f"Sign-in for {label} was cancelled. Choose Sign In… beside it in Settings when "
                "ready."
                if backup
                else f"Connect {self.name} was cancelled. Nothing was connected."
            )

    def choose_accounts(self, value: tuple[str, list[dict[str, Any]]]) -> None:
        """Pick the account for each Buffer platform from one Buffer account, then connect them
        all at once."""
        key, channels = value
        socials = getattr(self.window, "socials", (self,))
        dialog = ChooseAccounts(self.window, socials, channels, self.key)
        result = dialog.exec()
        picks = dialog.picks() if result == QDialog.DialogCode.Accepted else []
        if not picks:
            # Removed only if no platform uses this sign-in.
            self.run(lambda c: self.service.forget_key(key))
            if dialog.fresh:
                self.sign_in()
            else:
                self.feedback("Nothing was connected.")
            return

        def job(cancel: Event) -> None:
            for social, channel in picks:
                social.service.select_channel(key, channel)

        names = ", ".join(f"{s.name} ({handle(s.key, c)})" for s, c in picks)
        self.run(job, self.reload(f"Connected {names} through Buffer."))

    def choose_channel(self, value: tuple[str, list[dict[str, Any]]]) -> None:
        key, channels = value
        usable = [c for c in channels if not c.get("isDisconnected") and not c.get("isLocked")]
        destination = DESTINATION[self.key]
        if not usable:
            # Removed only if no other platform uses this sign-in.
            self.run(lambda c: self.service.forget_key(key))
            self.feedback(
                f"No usable {self.name} channel was found in your Buffer account. Connect your "
                f"{destination} in Buffer first, then try again."
            )
            return
        names = [
            f"{handle(self.key, c)} · {c.get('displayName') or ''}".strip(" ·")
            if self.key != "facebook"
            else handle(self.key, c)
            for c in usable
        ]
        index = 0
        if len(usable) > 1:
            choice, ok = QInputDialog.getItem(
                self.window,
                f"Choose {destination}",
                f"{destination[0].upper() + destination[1:]} in Buffer:",
                names,
                0,
                False,
            )
            if not ok:
                self.run(lambda c: self.service.forget_key(key))
                self.feedback(f"No {destination} was selected. Nothing was connected.")
                return
            index = names.index(choice)
        chosen = usable[index]
        self.run(
            lambda c: self.service.select_channel(key, chosen),
            self.reload(f"Connected {names[index]} through Buffer."),
        )

    def disconnect_account(self) -> None:
        if (
            QMessageBox.question(
                self.window,
                f"Disconnect {self.name}?",
                f"Stop posting to {self.name} from this Mac? The Buffer sign-in is removed once no "
                "other platform uses it. Posts already scheduled in Buffer stay scheduled there; "
                "history is kept.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(lambda c: self.service.disconnect(), self.reload(f"{self.name} disconnected."))

    # Kept for the TikTok tests and harnesses that call them by their original names.
    connect_tiktok = connect_account
    disconnect_tiktok = disconnect_account

    # Settings: hosting ----------------------------------------------------------------------

    def build_hosting(self) -> None:
        self.hosting, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        for platform in BUFFER_KEYS:
            badge = image(logo(platform, 20))
            badge.setToolTip(f"Used by {PLATFORM_NAMES[platform]}")
            heading.addWidget(badge)
        heading.addWidget(text("Video hosting for Buffer", "Heading"), 1)
        body.addLayout(heading)
        body.addWidget(
            text(
                "Buffer can only post a video from a web link, so each video for TikTok, "
                "Instagram or Facebook is first copied to your own Cloudinary account. "
                "YouTube doesn't use it.",
                "Muted",
            )
        )
        self.hosting_status = text(
            "Not set up yet. Needed before posting to TikTok, Instagram or Facebook.", "Notice"
        )
        body.addWidget(self.hosting_status)
        body.addWidget(guide(HOSTING_STEPS))
        body.addWidget(text(HOSTING_CONSENT, "Subtle"))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.cloud_name, self.cloud_key, self.cloud_secret = QLineEdit(), QLineEdit(), QLineEdit()
        for field in (self.cloud_name, self.cloud_key, self.cloud_secret):
            field.textChanged.connect(self.split_pasted)
        self.cloud_name.setPlaceholderText("Paste cloudinary://… here, or type the cloud name")
        self.cloud_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_secret.setPlaceholderText(
            "Stored in macOS Keychain; leave empty to keep the saved one"
        )
        self.retention = QComboBox()
        self.retention.addItem("Delete a day after posting (recommended)", "delete_after_sent")
        self.retention.addItem("Keep hosted copies", "keep")
        self.retention.setToolTip(
            "Deleted one day after Buffer reports the post sent — never earlier"
        )
        for label, widget in (
            ("Cloudinary cloud name", self.cloud_name),
            ("API key", self.cloud_key),
            ("API secret", self.cloud_secret),
            ("Retention", self.retention),
        ):
            form.addRow(label, widget)
            widget.setAccessibleName(label)
        body.addLayout(form)
        self.hosting_consent = QCheckBox("Hosted copies are public by link")
        self.hosting_consent.setToolTip(
            "I understand anyone with a hosted link can watch that copy until it is deleted"
        )
        body.addWidget(self.hosting_consent)
        self.hosting_feedback = text("", "Muted")
        body.addWidget(self.hosting_feedback)
        self.save_hosting_button = icon_button("Save", "check", role="Primary")
        self.save_hosting_button.setAccessibleName("Save Hosting")
        self.save_hosting_button.clicked.connect(self.save_hosting)
        self.test_hosting_button = icon_button("Check Account", "refresh")
        self.test_hosting_button.setAccessibleName("Check Account and Usage")
        self.test_hosting_button.clicked.connect(self.test_hosting)
        self.remove_hosting_button = icon_button("Remove…", "trash", role="Danger")
        self.remove_hosting_button.setAccessibleName("Remove Hosting…")
        self.remove_hosting_button.clicked.connect(self.remove_hosting)
        body.addWidget(
            row(self.save_hosting_button, self.test_hosting_button, self.remove_hosting_button)
        )
        body.addWidget(
            text(
                "Cloudinary's Free plan accepts videos up to 100 MB and includes 25 monthly credits; nothing is uploaded until you post through Buffer.",
                "Muted",
            )
        )
        body.addWidget(text("Cloudinary accounts", "Subheading"))
        body.addWidget(
            text(
                "Add more Cloudinary accounts (separate sign-ups, each with its own limits). When "
                "one is over its plan's limits, the next one takes the video automatically. The "
                "settings above apply to all of them.",
                "Muted",
            )
        )
        self.host_rows = QVBoxLayout()
        self.host_rows.setSpacing(8)
        body.addLayout(self.host_rows)
        self.add_host_button = icon_button("Add Cloudinary Account…", "plus")
        self.add_host_button.clicked.connect(self.add_host)
        self.check_hosts_button = icon_button("Check Usage", "refresh", role="Ghost")
        self.check_hosts_button.setAccessibleName("Check Every Cloudinary Account")
        self.check_hosts_button.clicked.connect(self.check_hosts)
        body.addWidget(row(self.add_host_button, self.check_hosts_button))

    def save_hosting(self) -> None:
        cloud, key, secret = self.cloud_name.text(), self.cloud_key.text(), self.cloud_secret.text()
        consent, retention = self.hosting_consent.isChecked(), self.retention.currentData()
        if not consent:
            self.alert("Tick “Hosted copies are public by link” to confirm, then save again.")
            return

        def saved(usage: dict[str, Any]) -> None:
            self.cloud_secret.clear()
            self.reload("Media hosting saved and verified with Cloudinary. Nothing was uploaded.")(
                None
            )
            self.feedback(f"Saved · {self.usage_text(usage)}")

        self.run(
            lambda c: self.service.save_hosting(
                cloud, key, secret, consent=True, retention=retention
            ),
            saved,
        )

    @staticmethod
    def usage_text(usage: dict[str, Any]) -> str:
        return (
            f"Cloudinary account OK · plan {usage.get('plan') or 'unknown'} · "
            f"{usage.get('credits_used')} of {usage.get('credits_limit')} monthly credits used"
        )

    def test_hosting(self) -> None:
        cloud, key, secret = self.cloud_name.text(), self.cloud_key.text(), self.cloud_secret.text()
        self.run(
            lambda c: self.service.check_hosting(cloud, key, secret),
            lambda usage: self.feedback(self.usage_text(usage) + "."),
        )

    def split_pasted(self, value: str) -> None:
        """Accept Cloudinary's “API environment variable” pasted into any field."""
        try:
            cloud, key, secret = cloudinary_fields(value, "", "")
        except HostingError:
            return
        if "cloudinary://" in value:
            for field, text_value in (
                (self.cloud_name, cloud),
                (self.cloud_key, key),
                (self.cloud_secret, secret),
            ):
                field.blockSignals(True)
                field.setText(text_value)
                field.blockSignals(False)
            self.feedback("Filled in from the Cloudinary environment variable you pasted.")

    def add_host(self) -> None:
        dialog = AddCloudinaryAccount(self.window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cloud, key, secret = dialog.fields

        def added(usage: dict[str, Any]) -> None:
            self.reload(f"Cloudinary account {cloud} added. Nothing was uploaded.")(None)
            self.feedback(f"Added {cloud} · {self.usage_text(usage)}")

        self.run(lambda c: self.service.add_host(cloud, key, secret), added)

    def check_hosts(self) -> None:
        def checked(results: list[dict[str, Any]]) -> None:
            for result in results:
                usage = result.get("usage")
                self.host_usage[result["credential_key"]] = usage or {
                    "error": str(result.get("error", ""))
                }
                self.usage[result["credential_key"]] = (
                    f"{usage.get('credits_used')} of {usage.get('credits_limit')} credits used"
                    if usage
                    else str(result.get("error", ""))
                )
            self.reload("Checked every Cloudinary account. Nothing was uploaded.")(None)

        self.run(lambda c: self.service.check_hosts(), checked)

    def remove_host(self, key: str) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Remove Cloudinary account?",
                "Forget this Cloudinary account and its Keychain secret on this Mac? Copies "
                "already hosted there are not deleted.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(
                lambda c: self.service.remove_host(key),
                self.reload("Cloudinary account removed."),
            )

    def remove_hosting(self) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Remove media hosting?",
                "Forget the Cloudinary settings and Keychain secret on this Mac? Copies already "
                "hosted are not deleted. If you added more accounts, the first one becomes the "
                "main account.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(
                lambda c: self.service.remove_hosting(), self.reload("Hosting settings removed.")
            )

    # Videos tab -----------------------------------------------------------------------------

    def build_video(self) -> None:
        self.video_panel = QWidget()
        body = QVBoxLayout(self.video_panel)
        body.setContentsMargins(0, 4, 0, 4)
        body.setSpacing(12)
        self.video_heading = text(f"Post to {self.name} now", "Heading")
        body.addWidget(self.video_heading)
        self.video_account = text("", "Notice")
        body.addWidget(self.video_account)
        self.video_checklist = self.setup_steps()
        body.addWidget(self.video_checklist)
        self.details: tuple[str, str, tuple[str, ...]] = ("", "", ())
        self.caption = QPlainTextEdit()
        self.caption.setFixedHeight(96)
        self.caption.setReadOnly(True)
        self.caption.setAccessibleName(f"{self.name} caption")
        self.caption.textChanged.connect(self.caption_changed)
        self.caption_count = text("", "Subtle")
        body.addWidget(text("Caption · made from your title, description and tags", "Subheading"))
        body.addWidget(self.caption)
        body.addWidget(self.caption_count)
        # Shown in the video's Details step with the other platform settings.
        self.ai = QComboBox()
        self.ai.addItem("Choose one", None)
        self.ai.addItem("Yes, it contains AI-generated content", True)
        self.ai.addItem("No AI-generated content", False)
        self.ai.setAccessibleName("AI-generated content disclosure")
        self.ai.activated.connect(lambda _: self.save_draft(quiet=True))
        if not self.spec.ai_label:
            self.ai.setCurrentIndex(self.ai.findData(False))  # nothing is sent; never asked
        self.ai_field = QWidget()
        field = QVBoxLayout(self.ai_field)
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(6)
        field.addWidget(text("AI-generated content label", "Muted"))
        field.addWidget(self.ai)
        self.when = QDateTimeEdit(in_an_hour())
        self.when.setDisplayFormat("ddd, MMM d, yyyy · " + time_pattern())
        self.when.setCalendarPopup(True)
        self.when.setAccessibleName(f"{self.name} post time")
        self.save_draft_button = QPushButton(f"Save {self.name} Details")
        self.save_draft_button.clicked.connect(lambda: self.save_draft())
        self.save_draft_button.hide()  # details save with the video; kept for the busy state
        self.publish_button = icon_button("Post Now…", "send", role="Primary")
        self.publish_button.clicked.connect(lambda: self.publish(None))
        self.schedule_button = icon_button("Schedule…", "calendar")
        self.schedule_button.clicked.connect(lambda: self.publish(self.when_utc()))
        self.when_label = text("Post at", "Subheading")
        body.addWidget(self.when_label)
        body.addWidget(self.when)
        body.addWidget(row(self.publish_button, self.schedule_button, self.save_draft_button))
        self.video_feedback = text("", "Muted")
        body.addWidget(self.video_feedback)
        body.addWidget(text(UNSUPPORTED[self.key], "Subtle"))
        self.video_history_title = text(f"{self.name} posts for this video", "Subheading")
        body.addWidget(self.video_history_title)
        self.video_history = QListWidget()
        self.video_history.setFixedHeight(120)
        self.video_history.setWordWrap(True)
        self.video_history.currentRowChanged.connect(lambda _: self.render_video_buttons())
        body.addWidget(self.video_history)
        self.refresh_button = icon_button("Refresh Status", "refresh")
        self.refresh_button.clicked.connect(self.poll)
        self.cancel_post_button = icon_button("Cancel Scheduled Post…", "x", role="Danger")
        self.cancel_post_button.clicked.connect(lambda: self.cancel_post(self.video_history))
        self.release_button = icon_button("Allow Another Attempt…", "refresh")
        self.release_button.clicked.connect(self.release)
        body.addWidget(row(self.refresh_button, self.cancel_post_button, self.release_button))
        body.addStretch()

    def set_when(self, when: str) -> None:
        """Now or Schedule, chosen once in the Upload step for every platform."""
        later = when == "later"
        self.video_heading.setText(
            f"Schedule a {self.name} post" if later else f"Post to {self.name} now"
        )
        for widget in (self.when_label, self.when, self.schedule_button):
            widget.setVisible(later)
        self.publish_button.setVisible(not later)
        self.schedule_button.setText(f"Schedule on {self.name}…")
        restyle(self.schedule_button, "Primary")

    def follow_details(self, metadata: Any) -> None:
        """The caption always comes from the video's shared details."""
        self.details = (metadata.title, metadata.description, tuple(metadata.tags))
        caption = caption_from(*self.details, hashtags=self.spec.hashtags_max)
        if caption != self.caption.toPlainText():
            self.caption.setPlainText(caption)

    def when_utc(self) -> datetime:
        return picked(self.when.dateTime())

    def caption_changed(self) -> None:
        value = self.caption.toPlainText()
        problems = caption_errors(self.spec, value)
        self.caption_count.setText(
            " ".join(problems)
            if problems
            else f"{len(value)} / {self.spec.caption_max:,} characters"
        )

    def context(self, video_id: int | None) -> None:
        if video_id == self.video_id:
            return
        self.video_id = video_id
        if video_id is None:
            return
        if not self.spec.ai_label:
            self.render_video()  # no per-video setting to load
            return

        def done(draft: dict[str, Any]) -> None:
            self.ai.setCurrentIndex(
                self.ai.findData(draft["ai_generated"]) if draft["ai_generated"] is not None else 0
            )
            self.render_video()

        self.run(lambda c: self.service.draft(video_id), done, quiet=True)

    def save_draft(self, *, quiet: bool = False) -> None:
        if self.video_id is None:
            return
        video_id, caption, ai = self.video_id, self.caption.toPlainText(), self.ai.currentData()
        self.run(
            lambda c: self.service.save_draft(video_id, caption, ai),
            self.reload("" if quiet else f"{self.name} details saved."),
            quiet=quiet,
        )

    def publish(self, due: datetime | None, *, confirmed: bool = False) -> None:
        """confirmed: the person already approved this post in the all-platforms Upload step."""
        if self.video_id is None:
            return
        connection = self.data["connection"] or {}
        caption, ai = self.caption.toPlainText(), self.ai.currentData()
        if confirmed:
            self.post(due, caption, ai)
            return
        message = (
            f"Post this video PUBLICLY to {self.name} {handle(self.key, connection)} through Buffer"
            + (f" at {timestamp(due, weekday=True)}?" if due else " now?")
            + f"\n\nCaption: {caption or '(none)'}\n"
            + (
                f"AI-generated content: {'Yes' if ai else 'No' if ai is False else 'not chosen'}\n"
                if self.spec.ai_label
                else ""
            )
            + "\n"
            + HOSTING_CONSENT
            + "\n\n"
            + UNSUPPORTED[self.key]
        )
        box = QMessageBox(
            QMessageBox.Icon.Question,
            f"Publish to {self.name}?",
            message,
            QMessageBox.StandardButton.Cancel,
            self.window,
        )
        go = box.addButton("Post Publicly via Buffer", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        self.post(due, caption, ai)

    def post(self, due: datetime | None, caption: str, ai: bool | None) -> None:
        video_id = self.video_id
        if video_id is None:
            return

        def job(cancel: Event) -> str:
            self.service.save_draft(video_id, caption, ai)
            return self.service.publish(video_id, caption, ai, due, cancel)

        self.run(
            job,
            self.reload(
                "Sent to Buffer. Status updates appear below."
                if due is None
                else "Scheduled in Buffer for the approved time."
            ),
        )

    def selected_post(self, view: QListWidget) -> dict[str, Any] | None:
        item = view.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((p for p in self.data["posts"] if p["id"] == identity), None)

    def cancel_post(self, view: QListWidget) -> None:
        post = self.selected_post(view)
        if (
            post
            and QMessageBox.question(
                self.window,
                f"Cancel this {self.name} post?",
                "Delete it from Buffer's schedule and remove its hosted copy? Posts Buffer has already started cannot be cancelled.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(
                lambda c: self.service.cancel(post["id"]),
                self.reload(f"{self.name} post cancelled."),
            )

    def release(self) -> None:
        post = self.selected_post(self.video_history)
        if (
            post
            and QMessageBox.question(
                self.window,
                "Allow another attempt?",
                f"Only continue if you have checked Buffer and {self.name} and this post did NOT go out. Another attempt could otherwise post a duplicate.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(
                lambda c: self.service.release(post["id"]),
                self.reload("Another attempt is allowed for this video."),
            )

    def render_video(self) -> None:
        connection, hosting = self.data["connection"], self.data["hosting"]
        if not connection:
            self.video_account.setText(f"{self.name} isn't set up yet. Follow these steps:")
        elif connection.get("status") != "Connected":
            self.video_account.setText(f"Reconnect {self.name} in Accounts to continue.")
        elif not hosting:
            self.video_account.setText("One step left before you can post:")
        else:
            self.video_account.setText(
                f"Posting as {handle(self.key, connection)} · public, through Buffer"
            )
        self.video_history.clear()
        for post in self.data["posts"]:
            if post["video_id"] == self.video_id:
                item = QListWidgetItem(
                    label_for(post)
                    + (f"\n{post['error']}" if post["error"] else "")
                    + (f"\n{post['external_link']}" if post["external_link"] else "")
                )
                item.setData(Qt.ItemDataRole.UserRole, post["id"])
                self.video_history.addItem(item)
        posted = self.video_history.count() > 0
        self.video_history.setVisible(posted)
        self.video_history_title.setVisible(posted)
        self.render_video_buttons()

    def render_video_buttons(self) -> None:
        ready = bool(
            self.data["connection"]
            and self.data["connection"].get("status") == "Connected"
            and self.data["hosting"]
            and self.video_id is not None
        )
        for button in (self.publish_button, self.schedule_button):
            button.setEnabled(ready and not self.worker)
        post = self.selected_post(self.video_history)
        self.cancel_post_button.setVisible(
            bool(post and post["state"] in ACTIVE_STATES and not post["released"])
        )
        self.release_button.setVisible(
            bool(post and post["state"] in RELEASABLE and not post["released"])
        )

    # Auto Upload ----------------------------------------------------------------------------
    # The Auto Upload page shows each auto upload as one card for all of its platforms
    # (ui/autocards.py). These describe and change this platform's part of one.

    def automation_headline(self, p: dict[str, Any]) -> str:
        """Never "Active" while something stops it from posting."""
        paused = bool(self.window.automatic.data.get("paused", 1))
        connected = bool(
            self.data["connection"] and self.data["connection"].get("status") == "Connected"
        )
        return (
            "Needs approval again"
            if p["revoked"]
            else f"Paused · no new {self.name} posts are scheduled"
            if not p["enabled"]
            else f"Not posting · reconnect {self.name} in Accounts"
            if not connected
            else "Not posting · set up video hosting in Settings"
            if not self.data["hosting"]
            else "Not posting · automatic publishing is paused for the whole app (Turn On in the sidebar)"
            if paused
            else "Active · Buffer posts each video at its approved time"
        )

    def automation_next(self, p: dict[str, Any]) -> str:
        """The one post this auto upload has on its way to Buffer, or in it."""
        pending = [
            x
            for x in self.data["posts"]
            if x["profile_id"] == p["id"]
            and not x["released"]
            and x["state"] in ACTIVE_STATES + ("Publishing", "Sending to Buffer")
        ]
        waiting = sum(
            d["state"] == "Waiting for next opportunity"
            for d in self.data.get("decisions", {}).get(p["id"], [])
            if d["profile_revision"] == p["revision"]
        )
        line = (
            f"Next post {timestamp(pending[0]['due_at'], weekday=True)}"
            if pending and pending[0]["due_at"]
            else "Nothing in Buffer right now"
        )
        return line + (f" · {waiting} more waiting their turn" if waiting else "")

    def control_automation(self, identity: str, action: str) -> None:
        """pause, resume or remove this platform's part of an auto upload."""
        self.run(lambda c: self.service.control(identity, action), self.reload())

    def review_automation(
        self, values: dict[str, Any], done: Callable[[bool], None], parent: QWidget
    ) -> None:
        """Check an automation, show its own approval, and enable it only if approved."""

        def guarded(job: Callable[[Event], Any]) -> Callable[[Event], tuple[str, Any]]:
            def wrapped(cancel: Event) -> tuple[str, Any]:
                try:
                    return "ok", job(cancel)
                except (SocialError, BufferError, HostingError) as exc:
                    return "error", str(exc)

            return wrapped

        def failed(message: str) -> None:
            self.feedback(message)
            if hasattr(parent, "show_error"):
                parent.show_error(message)
            done(False)

        def enabled(result: tuple[str, Any]) -> None:
            if result[0] == "error":
                failed(result[1])
                return
            self.reload(f"{self.name} auto upload turned on. Only videos added from now on post.")(
                None
            )
            done(True)

        def reviewed(result: tuple[str, Any]) -> None:
            if result[0] == "error":
                failed(result[1])
                return
            proposal = result[1]
            consent = AutomationConsent(proposal, self.spec, parent)
            if consent.exec() != QDialog.DialogCode.Accepted:
                done(False)
                return
            scanner = self.window.watching.service
            self.run(guarded(lambda c: self.service.approve(proposal, scanner, c)), enabled)

        self.run(guarded(lambda c: self.service.preview(values)), reviewed)

    # Queue ----------------------------------------------------------------------------------

    def build_queue(self) -> None:
        self.queue, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(logo(self.key, 22)))
        heading.addWidget(text(f"{self.name} uploads", "Heading"), 1)
        body.addLayout(heading)
        self.queue_list = QListWidget()
        self.queue_list.setWordWrap(True)
        self.queue_list.setMinimumHeight(150)
        self.queue_list.setAccessibleName(f"{self.name} posts")
        self.queue_list.currentRowChanged.connect(lambda _: self.queue_selected())
        self.queue_empty = text("", "Muted")
        # Like the YouTube uploads card: open what was posted; cancel only what still can be.
        self.queue_open = icon_button(f"Open on {self.name}", "external")
        self.queue_open.clicked.connect(self.open_post)
        self.queue_refresh = icon_button("Refresh Status", "refresh", role="Ghost")
        self.queue_refresh.setAccessibleName(f"Refresh {self.name} Status")
        self.queue_refresh.clicked.connect(self.poll)
        self.queue_cancel = icon_button("Cancel Post…", "x", role="Danger")
        self.queue_cancel.setAccessibleName(f"Cancel Selected {self.name} Post…")
        self.queue_cancel.clicked.connect(lambda: self.cancel_post(self.queue_list))
        self.queue_feedback = text("", "Muted")
        self.queue_feedback.hide()
        self.queue_actions = flow(self.queue_open, self.queue_cancel, self.queue_refresh)
        for widget in (self.queue_empty, self.queue_list, self.queue_actions, self.queue_feedback):
            body.addWidget(widget)

    def render_queue(self) -> None:
        chosen = self.selected_post(self.queue_list)
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        for post in self.data["posts"]:
            item = QListWidgetItem(label_for(post))
            item.setData(Qt.ItemDataRole.UserRole, post["id"])
            item.setToolTip(post["error"] or post["external_link"])
            self.queue_list.addItem(item)
            if chosen and post["id"] == chosen["id"]:
                self.queue_list.setCurrentItem(item)  # a refresh keeps the selection
        if self.queue_list.count() and not self.queue_list.currentItem():
            self.queue_list.setCurrentRow(0)
        self.queue_list.blockSignals(False)
        self.queue_selected()
        count = len(self.data["posts"])
        self.queue_empty.setText(
            f"{count} saved"
            if count
            else f"No {self.name} uploads yet. To post one, open a video and choose Upload."
        )
        self.queue_list.setVisible(bool(count))
        self.queue_actions.setVisible(bool(count))
        self.queue.setVisible(bool(count))  # Coming up already lists every platform

    def queue_selected(self) -> None:
        post = self.selected_post(self.queue_list)
        self.queue_open.setEnabled(bool(post and post["external_link"]))
        self.queue_open.setToolTip(
            ""
            if post and post["external_link"]
            else f"Available once {self.name} has the post and Buffer reports its link"
        )
        self.queue_cancel.setVisible(
            bool(post and post["state"] in CANCELLABLE and not post["released"])
        )

    def open_post(self) -> None:
        post = self.selected_post(self.queue_list)
        if post and post["external_link"]:
            open_page(post["external_link"])


class AddBufferAccount(QDialog):
    """What a backup Buffer account needs, then its name (and its own app client, if any)."""

    def __init__(self, parent: QWidget, number: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add a Buffer account")
        self.resize(640, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(text("Add a Buffer account", "Heading"))
        layout.addWidget(
            guide(
                (
                    (
                        "Sign up for another Buffer account with a different email. Each Buffer "
                        "account has its own limits.",
                        "Open Buffer",
                        "https://buffer.com",
                    ),
                    (
                        "In that Buffer account, connect the same TikTok, Instagram and Facebook "
                        "accounts you post to here. Posts only fall back to the same accounts.",
                        "Open Buffer Channels",
                        BUFFER_CHANNELS,
                    ),
                    (
                        "Sign out of Buffer in your browser, so the sign-in that opens next uses "
                        "the new account.",
                        "",
                        "",
                    ),
                    ("Name it here, then choose Add and Sign In.", "", ""),
                )
            )
        )
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.name = QLineEdit(f"Buffer {number}")
        self.name.setAccessibleName("Buffer account name")
        form.addRow("Name", self.name)
        layout.addLayout(form)
        layout.addWidget(
            text(
                "Only if you created a separate app client in that Buffer account (otherwise "
                "leave these empty; your main app client is used):",
                "Subtle",
            )
        )
        own = QFormLayout()
        own.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.client, self.redirect = QLineEdit(), QLineEdit()
        self.client.setAccessibleName("Its own Client ID")
        self.redirect.setAccessibleName("Its own redirect address")
        own.addRow("Client ID", self.client)
        own.addRow("Redirect address (HTTPS)", self.redirect)
        layout.addLayout(own)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        add = buttons.addButton("Add and Sign In", QDialogButtonBox.ButtonRole.AcceptRole)
        add.setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return self.name.text(), self.client.text(), self.redirect.text()


class AddCloudinaryAccount(QDialog):
    """A separate Cloudinary account, from its pasted API environment variable."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add a Cloudinary account")
        self.resize(640, 290)
        self.fields: tuple[str, str, str] = ("", "", "")
        layout = QVBoxLayout(self)
        layout.addWidget(text("Add a Cloudinary account", "Heading"))
        layout.addWidget(
            guide(
                (
                    (
                        "Sign up for a new Cloudinary account with a different email. Another "
                        "API key of an account you already added shares its limits, so it "
                        "doesn't help.",
                        "Sign Up",
                        CLOUDINARY_SIGN_UP,
                    ),
                    (
                        "Signed in to the new account, open API Keys and copy the API "
                        "environment variable (it starts with cloudinary://).",
                        "Open API Keys",
                        CLOUDINARY_KEYS,
                    ),
                    ("Paste it below and choose Add.", "", ""),
                )
            )
        )
        self.value = QLineEdit()
        self.value.setEchoMode(QLineEdit.EchoMode.Password)
        self.value.setPlaceholderText("cloudinary://…")
        self.value.setAccessibleName("Cloudinary API environment variable")
        layout.addWidget(self.value)
        self.problem = text("", "Error")
        self.problem.hide()
        layout.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        add = buttons.addButton("Add", QDialogButtonBox.ButtonRole.AcceptRole)
        add.setObjectName("Primary")
        buttons.accepted.connect(self.check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def check(self) -> None:
        value = self.value.text()
        try:
            if "cloudinary://" not in value:
                raise HostingError("missing", "config")
            self.fields = cloudinary_fields(value, "", "")
        except HostingError:
            self.problem.setText(
                "Paste the whole API environment variable from the new account's API Keys page "
                "(cloudinary://key:secret@cloud-name)."
            )
            self.problem.show()
            return
        self.accept()


class ConnectDialog(QDialog):
    """Shown while Buffer's sign-in is open in the browser."""

    def __init__(self, parent: QWidget, spec: Platform = TIKTOK) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in to Buffer")
        self.resize(520, 0)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.setSpacing(8)
        for platform in BUFFER_KEYS:
            top.addWidget(image(logo(platform, 22)))
        top.addWidget(text("Sign in to Buffer", "Heading"), 1)
        layout.addLayout(top)
        self.status = text(
            "Your browser opened Buffer's sign-in. Allow ShortsScheduler there and you come back "
            "here automatically to choose your TikTok, Instagram and Facebook accounts.",
            "Muted",
        )
        layout.addWidget(self.status)
        self.sign_in = icon_button("Open Sign-In Page Again", "external", role="Primary")
        self.open_buffer = icon_button("Add Accounts in Buffer…", "external", role="Ghost")
        self.open_buffer.setToolTip(
            f"Connect your {DESTINATION[spec.key]} inside Buffer first if it isn't there yet"
        )
        layout.addWidget(row(self.sign_in, self.open_buffer))
        layout.addWidget(text("Only if the browser shows a code instead:", "Subtle"))
        self.pasted = QPlainTextEdit()
        self.pasted.setFixedHeight(56)
        self.pasted.setPlaceholderText("Paste the code from the browser here")
        self.use_pasted = icon_button("Use Pasted Code", "check")
        paste = QHBoxLayout()
        paste.setSpacing(10)
        paste.addWidget(self.pasted, 1)
        paste.addWidget(self.use_pasted, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(paste)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ChooseAccounts(QDialog):
    """Every Buffer platform's account from one Buffer account, connected together."""

    def __init__(
        self,
        parent: QWidget,
        socials: Iterable[SocialController],
        channels: list[dict[str, Any]],
        initiator: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose accounts to connect")
        self.resize(600, 0)
        self.fresh = False  # "Use a Different Buffer Account" was chosen
        layout = QVBoxLayout(self)
        layout.addWidget(text("Choose accounts to connect", "Heading"))
        layout.addWidget(
            text(
                "These are the accounts in your Buffer. Connecting doesn't post anything: every "
                "upload and auto upload still asks for your approval.",
                "Muted",
            )
        )
        self.rows: list[tuple[SocialController, QCheckBox, QComboBox]] = []
        for social in socials:
            found = usable_channels(social.spec, channels)
            frame, body = card(14, 8, "Row")
            line = QHBoxLayout()
            line.setSpacing(12)
            check = QCheckBox()
            check.setAccessibleName(f"Connect {social.name}")
            line.addWidget(check)
            line.addWidget(image(logo(social.key, 26)))
            names = QVBoxLayout()
            names.setSpacing(4)
            names.addWidget(text(social.name, "Subheading"))
            choice = QComboBox()
            choice.setAccessibleName(f"{social.name} account")
            for channel in found:
                label = handle(social.key, channel)
                if social.key != "facebook" and channel.get("displayName"):
                    label += f" · {channel['displayName']}"
                choice.addItem(label, channel)
            connection = social.data.get("connection") or {}
            current = choice.findText(handle(social.key, connection), Qt.MatchFlag.MatchStartsWith)
            choice.setCurrentIndex(max(0, current))
            if found:
                names.addWidget(choice)
                if connection:
                    state = (
                        "Connected now"
                        if connection.get("status") == "Connected"
                        else "Needs signing in again"
                    )
                    names.addWidget(text(f"{state}: {handle(social.key, connection)}", "Subtle"))
            else:
                choice.hide()
                names.addWidget(
                    text(f"No {DESTINATION[social.key]} in this Buffer account yet.", "Subtle")
                )
            line.addLayout(names, 1)
            if not found:
                add = icon_button("Add in Buffer…", "external", role="Ghost")
                add.clicked.connect(lambda: open_page(BUFFER_CHANNELS))
                line.addWidget(add)
            body.addLayout(line)
            layout.addWidget(frame)
            check.setEnabled(bool(found))
            # Pre-selected: the platform being connected, and any that aren't working yet.
            check.setChecked(
                bool(found) and (social.key == initiator or connection.get("status") != "Connected")
            )
            check.toggled.connect(lambda _: self.update_connect())
            self.rows.append((social, check, choice))
        different = icon_button("Use a Different Buffer Account…", "logout", role="Ghost")
        different.clicked.connect(self.different)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.connect_button = buttons.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole)
        primary(self.connect_button)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(different)
        footer.addStretch()
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self.update_connect()

    def update_connect(self) -> None:
        self.connect_button.setEnabled(any(check.isChecked() for _, check, _ in self.rows))

    def different(self) -> None:
        self.fresh = True
        self.reject()

    def picks(self) -> list[tuple[SocialController, dict[str, Any]]]:
        return [
            (social, choice.currentData())
            for social, check, choice in self.rows
            if check.isChecked() and choice.currentData()
        ]


class AutomationEditor(QDialog):
    def __init__(self, controller: SocialController) -> None:
        super().__init__(controller.window)
        name = controller.name
        self.setWindowTitle(f"New {name} Automation")
        self.resize(560, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(text(f"{name} automation · future files only", "Heading"))
        self.progress = text("", "Muted")
        layout.addWidget(self.progress)
        self.steps = QTabWidget()
        pages = []
        for name in ("1 · Folder and account", "2 · Caption", "3 · Schedule and limits"):
            page = QWidget()
            body = QVBoxLayout(page)
            body.setAlignment(Qt.AlignmentFlag.AlignTop)
            self.steps.addTab(page, name)
            pages.append(body)
        layout.addWidget(self.steps)
        self.name = QLineEdit(f"My {name} Automation")
        self.folder = QComboBox()
        for folder in controller.data.get("folders", []):
            if folder["enabled"]:
                self.folder.addItem(folder["name"], folder["id"])
        connection = controller.data["connection"] or {}
        account = text(
            f"{name} {controller.spec.account}: "
            f"{handle(controller.key, connection) if connection else 'not connected'} (via Buffer)"
        )
        self.background = QCheckBox("Also run while ShortsScheduler is closed")
        self.template = QLineEdit("{filename}")
        self.ai = QComboBox()
        self.ai.addItem("Choose explicitly", None)
        self.ai.addItem("Yes — videos contain AI-generated content", True)
        self.ai.addItem("No AI-generated content", False)
        self.hours = QComboBox()
        for label, value in (
            ("Once per day", 0),
            ("Every 2 hours", 2),
            ("Every 3 hours", 3),
            ("Every 4 hours", 4),
            ("Every 6 hours", 6),
        ):
            self.hours.addItem(label, value)
        self.hours.setCurrentIndex(1)
        self.start, self.end = QTimeEdit(QTime(9, 0)), QTimeEdit(QTime(21, 0))
        for clock in (self.start, self.end):
            clock.setDisplayFormat(time_pattern())
        self.zone = QLineEdit(system_zone())
        self.cap = QSpinBox()
        self.cap.setRange(1, controller.spec.daily_max)
        self.cap.setValue(controller.spec.daily_max)  # no limit of our own
        self.pattern = QLineEdit("*.mp4")
        self.days = [QCheckBox(n) for n in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
        for day in self.days:
            day.setChecked(True)
        first, second, third = (QFormLayout() for _ in range(3))
        for form in (first, second, third):
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.controller = controller
        self.add_folder = natural(QPushButton("Choose and Authorize a New Folder…"))
        self.add_folder.clicked.connect(self.choose_folder)
        first.addRow("Automation name", self.name)
        first.addRow("Watched folder", self.folder)
        first.addRow("", self.add_folder)
        first.addRow("", account)
        first.addRow("", self.background)
        second.addRow("Caption template · only {filename}", self.template)
        if controller.spec.ai_label:
            second.addRow("AI-generated content", self.ai)
        else:
            self.ai.setCurrentIndex(self.ai.findData(False))  # nothing is sent
        third.addRow("Frequency", self.hours)
        third.addRow("First post of the day", self.start)
        third.addRow("Last possible post", self.end)
        third.addRow("Time zone (IANA)", self.zone)
        third.addRow("Eligible filenames", self.pattern)
        for body, form in zip(pages, (first, second, third), strict=True):
            body.addLayout(form)
        pages[1].addWidget(text(UNSUPPORTED[controller.key], "Muted"))
        pages[2].addWidget(text("Posting days", "Muted"))
        pages[2].addWidget(day_row(list(self.days)))
        bar = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.back, self.next = QPushButton("Back"), QPushButton("Next")
        self.review = QPushButton("Review and Enable…")
        self.back.clicked.connect(lambda: self.steps.setCurrentIndex(self.steps.currentIndex() - 1))
        self.next.clicked.connect(lambda: self.steps.setCurrentIndex(self.steps.currentIndex() + 1))
        self.review.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addStretch()
        for button in (self.back, self.next, self.review):
            button.setAutoDefault(False)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.steps.currentChanged.connect(self.step_changed)
        self.step_changed()

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to watch for new videos")
        if not path:
            return
        scanner = self.controller.window.watching.service

        def proposed(proposal: dict[str, Any]) -> None:
            dialog = FolderConsent(proposal, self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.consent.isChecked():
                options = dict(
                    consent=True,
                    existing=dialog.existing.currentData(),
                    background=dialog.background.isChecked(),
                    quiet=dialog.quiet.value(),
                )
                self.controller.run(lambda c: scanner.add(proposal, **options), added)
            dialog.deleteLater()

        def added(folder_id: str) -> None:
            self.folder.clear()
            for folder in scanner.folders():
                if folder["enabled"]:
                    self.folder.addItem(folder["name"], folder["id"])
            self.folder.setCurrentIndex(max(0, self.folder.findData(folder_id)))
            self.controller.window.watching.activate()

        self.controller.run(lambda c: scanner.preview(path, c), proposed)

    def step_changed(self) -> None:
        index, last = self.steps.currentIndex(), self.steps.count() - 1
        self.back.setEnabled(index > 0)
        self.next.setEnabled(index < last)
        self.progress.setText(
            f"Step {index + 1} of {last + 1}"
            + (
                " · Next: " + self.steps.tabText(index + 1).split(" · ")[1]
                if index < last
                else " · Last step: review, then approve"
            )
        )
        for button, emphasized in ((self.next, index < last), (self.review, index == last)):
            button.setObjectName("Primary" if emphasized else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name.text(),
            "folder_id": self.folder.currentData(),
            "settings": {
                "caption_template": self.template.text(),
                "ai_generated": self.ai.currentData(),
                "rule": Rule(
                    self.zone.text().strip(),
                    tuple(i for i, d in enumerate(self.days) if d.isChecked()),
                    self.start.time().toString("HH:mm"),
                    self.end.time().toString("HH:mm"),
                    int(self.hours.currentData()),
                ).encode(),
                "daily_cap": self.cap.value(),
                "pattern": self.pattern.text(),
                "background": self.background.isChecked(),
            },
        }


class AutomationConsent(QDialog):
    def __init__(self, proposal: dict[str, Any], spec: Platform, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Authorize this {spec.name} automation?")
        self.resize(560, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(text(f"Authorize this exact PUBLIC {spec.name} automation?", "Heading"))
        summary = QPlainTextEdit(proposal["summary"])
        summary.setReadOnly(True)
        layout.addWidget(summary)
        layout.addWidget(text(spec.automation_consent, "Muted"))
        self.scope = QCheckBox("I authorize this exact scope, as summarized above")
        self.hosting = QCheckBox(
            "Each video is copied to my media host and reachable by public link until deleted"
        )
        for box in (self.scope, self.hosting):
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.approve = buttons.addButton(
            "Authorize and Enable", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.approve.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for box in (self.scope, self.hosting):
            box.toggled.connect(
                lambda _: self.approve.setEnabled(
                    self.scope.isChecked() and self.hosting.isChecked()
                )
            )
        layout.addWidget(buttons)


# The Stage 19 names, kept for the TikTok tests and native harnesses.
TikTokController = SocialController
TikTokAutomationEditor = AutomationEditor


class TikTokConsent(AutomationConsent):
    def __init__(self, proposal: dict[str, Any], parent: QWidget) -> None:
        super().__init__(proposal, TIKTOK, parent)
