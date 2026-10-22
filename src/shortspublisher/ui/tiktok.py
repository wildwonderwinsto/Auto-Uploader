"""TikTok via Buffer: Accounts card, hosting settings, video publishing, automation and queue.

All Buffer, Cloudinary, Keychain and SQLite work runs on a worker thread. Only values that
Buffer's public API can actually control are offered.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, QObject, Qt, QThread, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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
from ..tiktok import (
    AUTOMATION_CONSENT,
    HOSTING_CONSENT,
    TikTokError,
    TikTokService,
    caption_errors,
    caption_from,
)
from .components import (
    Checklist,
    Section,
    card,
    day_row,
    flow,
    icon_button,
    natural,
    pill,
    platform_top,
    primary,
    restyle,
    row,
)
from .formatting import system_zone, time_pattern, timestamp
from .icons import image, logo
from .recurring import rule_label
from .videos import text
from .watching import FolderConsent

if TYPE_CHECKING:
    from .window import MainWindow

logger = logging.getLogger(__name__)

BUFFER_CHANNELS = "https://account.buffer.com/channels"
SETUP = (
    "1. In Buffer, connect your TikTok account (Buffer's help center says TikTok Business accounts "
    "are needed for automatic publishing).\n"
    "2. In Buffer → Settings → API → App Clients, create a PUBLIC client with the permissions "
    "account:read, posts:read and posts:write.\n"
    "3. Host the relay page from docs/buffer-oauth-relay.html at an HTTPS address you control "
    "(for example GitHub Pages) and register that exact address as the redirect URI. Buffer requires "
    "HTTPS even for localhost; the relay forwards the sign-in back to this app on "
    f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}.\n"
    "4. Enter the Client ID and the redirect URI here. There is no client secret."
)
UNSUPPORTED = (
    "Buffer posts TikTok videos publicly by default. Buffer's API cannot set TikTok privacy, "
    "comments, Duet, Stitch or commercial-content disclosure. Videos with TikTok music, trending "
    "sounds or polls cannot be posted automatically."
)
ACTIVE_STATES = (
    "Scheduled in Buffer",
    "Hosted and ready",
    "Waiting for Buffer limit",
    "Waiting for TikTok reconnection",
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


class TikTokWorker(QThread):
    result = Signal(object)

    def __init__(self, job: Callable[[Event], Any], parent: QObject) -> None:
        super().__init__(parent)
        self.job, self.cancel = job, Event()

    def run(self) -> None:
        try:
            value = self.job(self.cancel)
            self.result.emit({"ok": value})
        except (TikTokError, BufferError, HostingError) as exc:
            self.result.emit({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surfaced, never silently dropped
            self.result.emit({"error": f"TikTok operation failed safely: {exc}"})


def label_for(post: dict[str, Any]) -> str:
    when = (
        timestamp(post["due_at"], weekday=True)
        if post["due_at"]
        else timestamp(post["created_at"], weekday=True)
    )
    # Same three lines as a scheduled YouTube upload: title, time, account · state.
    return f"{post['caption'][:60] or 'Untitled'}\n{when}\n{post['channel_name']} · {post['state']}"


class TikTokController(QObject):
    idle = Signal()
    changed = Signal()

    def __init__(self, service: TikTokService, window: MainWindow) -> None:
        super().__init__(window)
        self.service, self.window = service, window
        self.worker: TikTokWorker | None = None
        self.closing = False
        self.loaded = False  # True once the first check of the saved connection has finished
        self.pending: list[tuple[Callable[[Event], Any], Callable[[Any], None] | None, bool]] = []
        self.error_box: QMessageBox | None = None
        self.data: dict[str, Any] = {
            "insights": False,
            "connection": None,
            "client": None,
            "hosting": None,
            "posts": [],
            "profiles": [],
        }
        self.video_id: int | None = None
        self.summary = text("TikTok · checking…", "Muted")
        self.build_card()
        self.build_hosting()
        self.build_video()
        self.build_automation()
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
        self.worker = TikTokWorker(job, self)
        self.worker.result.connect(lambda result: self.finished(result, done, quiet))
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
        self.set_busy(False)
        if self.closing:
            self.idle.emit()
            return
        if "error" in result:
            logger.warning("TikTok operation needs attention: %s", result["error"])
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
            QMessageBox.Icon.Warning, "TikTok", message, QMessageBox.StandardButton.Ok, self.window
        )
        self.error_box.open()

    def activate(self) -> None:
        if not self.closing:
            self.timer.start()
            self.run(self.snapshot, self.render, quiet=True)

    def poll(self) -> None:
        if not self.worker:
            self.run(self.refreshed, self.render, quiet=True)

    def refreshed(self, cancel: Event) -> dict[str, Any]:
        self.service.refresh(cancel)
        self.service.cleanup()
        return self.snapshot(cancel)

    def snapshot(self, cancel: Event) -> dict[str, Any]:
        try:
            client = self.service.client()
        except BufferError:
            client = None
        return {
            "client": client,
            "connection": self.service.connection(),
            "hosting": self.service.hosting(),
            "posts": self.service.posts(),
            "profiles": self.service.profiles(),
            "decisions": {
                p["id"]: self.service.decisions(p["id"]) for p in self.service.profiles()
            },
            "folders": self.window.watching.service.folders(),
            "insights": bool(self.service.insights_key()),
        }

    def reload(self, message: str = "") -> Callable[[Any], None]:
        def done(_: Any) -> None:
            if message:
                self.feedback(message)
            self.run(self.snapshot, self.render, quiet=True)

        return done

    def feedback(self, message: str) -> None:
        for label in (
            self.card_feedback,
            self.hosting_feedback,
            self.video_feedback,
            self.auto_feedback,
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
            self.save_insights_button,
            self.remove_insights_button,
            self.save_hosting_button,
            self.test_hosting_button,
            self.remove_hosting_button,
            self.save_draft_button,
            self.publish_button,
            self.schedule_button,
            self.refresh_button,
            self.cancel_post_button,
            self.release_button,
            self.new_automation,
            self.auto_pause,
            self.auto_resume,
            self.auto_revoke,
            self.auto_remove,
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
        name = (connection or {}).get("displayName") or (connection or {}).get("name") or ""
        self.card_status.setText(
            "Connected" if connected else "Sign in again" if connection else "Not connected"
        )
        restyle(self.card_status, "Good" if connected else "Bad" if connection else "Neutral")
        self.card_account.setVisible(bool(connection))
        self.card_account.setText(f"@{(connection or {}).get('name') or name} · through Buffer")
        if connection:
            self.card_detail.setText(
                "Ready to post once TikTok video hosting is set up in Settings."
                if connected and not hosting
                else "Ready to post."
                if connected
                else "Buffer needs you to sign in again before anything else is posted."
            )
        else:
            self.card_detail.setText(
                "Connect your TikTok account through Buffer to post and schedule TikToks."
                if client
                else "Needs a one-time Buffer app client setup in Settings first."
            )
        self.connect_button.setText(
            "Reconnect TikTok via Buffer…" if connection else "Connect TikTok via Buffer…"
        )
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
                f"Buffer channel: {connection['id']}\nOrganization: {connection['organization_id']}\nConnected: {timestamp(connection['verified_at'])}\n"
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
            else "Not set up yet. Needed before posting to TikTok."
        )
        self.remove_hosting_button.setVisible(bool(hosting))
        if hosting and not self.cloud_name.text():
            self.cloud_name.setText(hosting["cloud_name"])
            self.cloud_key.setText(hosting["api_key"])
        self.render_video()
        self.render_automation()
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
        if not connection:
            self.summary.setText("TikTok · not connected (Accounts → Connect TikTok via Buffer)")
        elif connection.get("status") != "Connected":
            self.summary.setText("TikTok · reconnect required in Accounts")
        else:
            self.summary.setText(
                f"TikTok · @{connection.get('name')} · {scheduled} scheduled or publishing · "
                f"{attention} need attention · {enabled} enabled automation"
                + ("s" if enabled != 1 else "")
                + ("" if self.data["hosting"] else " · media hosting not set up (Settings)")
            )

    # Accounts card --------------------------------------------------------------------------

    def build_card(self) -> None:
        self.card, body = card(22, 14)
        self.card_status = pill("Not connected")
        body.addLayout(
            platform_top("tiktok", "Posts through your Buffer account", self.card_status)
        )
        self.card_account = text("", "Subheading")
        self.card_detail = text("", "Muted")
        self.card_feedback = text("", "Muted")
        self.card_feedback.hide()
        for widget in (self.card_account, self.card_detail, self.card_feedback):
            body.addWidget(widget)
        self.checklist = self.setup_steps()
        body.addWidget(self.checklist)
        self.connect_button = icon_button("Connect TikTok via Buffer…", "link", role="Primary")
        self.connect_button.clicked.connect(self.connect_tiktok)
        self.disconnect_button = icon_button("Disconnect…", "logout", role="Danger")
        self.disconnect_button.clicked.connect(self.disconnect_tiktok)
        self.setup_button = icon_button("Open Setup", "settings", role="Ghost")
        self.setup_button.clicked.connect(lambda: self.window.navigate("settings"))
        body.addStretch()
        body.addWidget(row(self.connect_button, self.disconnect_button, self.setup_button))
        # Kept for tooltips and support; IDs are not shown on the card itself.
        self.technical = text("", "Muted")
        self.technical.hide()
        # One-time developer setup lives in Settings; the window places this section there.
        self.client_setup = Section(
            "TikTok connection (Buffer)",
            "TikTok posts go through your own Buffer account. Do this once.",
        )
        self.client_setup.body.addWidget(text(SETUP, "Muted"))
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
        # Optional: Buffer serves post view counts only to a personal API key.
        self.insights_setup = Section(
            "TikTok view counts (optional)",
            "Buffer only shares view counts with a personal API key, not with app sign-ins. "
            "Create one in Buffer → Settings → API, paste it here, and TikTok views appear on the "
            "Dashboard. It is stored in the macOS Keychain and only ever reads counts.",
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

    def setup_steps(self) -> Checklist:
        """TikTok needs three things; the same steps appear wherever TikTok is not ready."""
        settings = lambda: self.window.navigate("settings")  # noqa: E731
        return Checklist(
            [
                ("Add your Buffer app details", "Open Setup", settings),
                ("Connect your TikTok account", "Connect", self.connect_tiktok),
                ("Set up video hosting", "Open Setup", settings),
            ]
        )

    def save_insights(self) -> None:
        key = self.insights_key.text()
        self.insights_key.clear()
        self.run(
            lambda c: self.service.save_insights_key(key),
            self.reload("Buffer key saved. TikTok views load on the Dashboard."),
        )

    def remove_insights(self) -> None:
        self.run(lambda c: self.service.remove_insights_key(), self.reload("Buffer key removed."))

    def save_client(self) -> None:
        cid, uri = self.client_id.text(), self.redirect.text()
        self.run(
            lambda c: self.service.save_client(cid, uri), self.reload("Buffer app client saved.")
        )

    def connect_tiktok(self) -> None:
        client = self.data["client"]
        if not client:
            return
        dialog = ConnectDialog(self.window)
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
            self.run(lambda c: self.service.finish_connect(code, verifier), self.choose_channel)

        dialog.open_buffer.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BUFFER_CHANNELS)))
        dialog.sign_in.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        dialog.use_pasted.clicked.connect(lambda: returned(dialog.pasted.toPlainText()))
        if listener:
            self.run(wait, returned)
        if dialog.exec() != QDialog.DialogCode.Accepted and not used["done"]:
            cancel.set()
            self.feedback("Connect TikTok was cancelled. Nothing was connected.")

    def choose_channel(self, value: tuple[str, list[dict[str, Any]]]) -> None:
        key, channels = value
        usable = [c for c in channels if not c.get("isDisconnected") and not c.get("isLocked")]
        if not usable:
            self.run(lambda c: self.service.discard_key(key))
            self.feedback(
                "No usable TikTok channel was found in your Buffer account. Connect TikTok in Buffer first, then try again."
            )
            return
        names = [f"@{c.get('name')} · {c.get('displayName') or ''}".strip(" ·") for c in usable]
        index = 0
        if len(usable) > 1:
            choice, ok = QInputDialog.getItem(
                self.window, "Choose TikTok account", "TikTok account in Buffer:", names, 0, False
            )
            if not ok:
                self.run(lambda c: self.service.discard_key(key))
                self.feedback("No TikTok account was selected. Nothing was connected.")
                return
            index = names.index(choice)
        chosen = usable[index]
        self.run(
            lambda c: self.service.select_channel(key, chosen),
            self.reload(f"Connected {names[index]} through Buffer."),
        )

    def disconnect_tiktok(self) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Disconnect TikTok?",
                "Remove the Buffer sign-in from this Mac? Posts already scheduled in Buffer stay scheduled there; history is kept.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(lambda c: self.service.disconnect(), self.reload("TikTok disconnected."))

    # Settings: hosting ----------------------------------------------------------------------

    def build_hosting(self) -> None:
        self.hosting, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(logo("tiktok", 20)))
        heading.addWidget(text("TikTok video hosting", "Heading"), 1)
        body.addLayout(heading)
        body.addWidget(
            text(
                "Buffer can only post a video from a web link, so each TikTok video is first "
                "copied to your own Cloudinary account. Needed only for TikTok.",
                "Muted",
            )
        )
        self.hosting_status = text("Not set up yet. Needed before posting to TikTok.", "Notice")
        body.addWidget(self.hosting_status)
        body.addWidget(text(HOSTING_CONSENT, "Subtle"))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.cloud_name, self.cloud_key, self.cloud_secret = QLineEdit(), QLineEdit(), QLineEdit()
        for field in (self.cloud_name, self.cloud_key, self.cloud_secret):
            field.textChanged.connect(self.split_pasted)
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
                "Cloudinary's Free plan accepts videos up to 100 MB and includes 25 monthly credits; nothing is uploaded until you publish to TikTok.",
                "Muted",
            )
        )

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

    def remove_hosting(self) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Remove media hosting?",
                "Forget the Cloudinary settings and Keychain secret on this Mac? Copies already hosted are not deleted.",
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
        self.video_heading = text("Post to TikTok now", "Heading")
        body.addWidget(self.video_heading)
        self.video_account = text("", "Notice")
        body.addWidget(self.video_account)
        self.video_checklist = self.setup_steps()
        body.addWidget(self.video_checklist)
        self.details: tuple[str, str, tuple[str, ...]] = ("", "", ())
        self.caption = QPlainTextEdit()
        self.caption.setFixedHeight(96)
        self.caption.setReadOnly(True)
        self.caption.setAccessibleName("TikTok caption")
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
        self.ai_field = QWidget()
        field = QVBoxLayout(self.ai_field)
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(6)
        field.addWidget(text("AI-generated content label", "Muted"))
        field.addWidget(self.ai)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setDisplayFormat("ddd, MMM d, yyyy · " + time_pattern())
        self.when.setCalendarPopup(True)
        self.when.setAccessibleName("TikTok post time")
        self.save_draft_button = QPushButton("Save TikTok Details")
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
        body.addWidget(text(UNSUPPORTED, "Subtle"))
        self.video_history_title = text("TikTok posts for this video", "Subheading")
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
        self.video_heading.setText("Schedule a TikTok post" if later else "Post to TikTok now")
        for widget in (self.when_label, self.when, self.schedule_button):
            widget.setVisible(later)
        self.publish_button.setVisible(not later)
        self.schedule_button.setText("Schedule on TikTok…")
        restyle(self.schedule_button, "Primary")

    def follow_details(self, metadata: Any) -> None:
        """The caption always comes from the video's shared details."""
        self.details = (metadata.title, metadata.description, tuple(metadata.tags))
        caption = caption_from(*self.details)
        if caption != self.caption.toPlainText():
            self.caption.setPlainText(caption)

    def when_utc(self) -> datetime:
        return datetime.fromtimestamp(self.when.dateTime().toSecsSinceEpoch(), UTC)

    def caption_changed(self) -> None:
        value = self.caption.toPlainText()
        problems = caption_errors(value)
        self.caption_count.setText(
            " ".join(problems) if problems else f"{len(value)} / 2,200 characters"
        )

    def context(self, video_id: int | None) -> None:
        if video_id == self.video_id:
            return
        self.video_id = video_id
        if video_id is None:
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
            self.reload("" if quiet else "TikTok details saved."),
            quiet=quiet,
        )

    def publish(self, due: datetime | None) -> None:
        if self.video_id is None:
            return
        connection = self.data["connection"] or {}
        caption, ai = self.caption.toPlainText(), self.ai.currentData()
        message = (
            f"Post this video PUBLICLY to TikTok @{connection.get('name')} through Buffer"
            + (f" at {timestamp(due, weekday=True)}?" if due else " now?")
            + f"\n\nCaption: {caption or '(none)'}\nAI-generated content: {'Yes' if ai else 'No' if ai is False else 'not chosen'}\n\n"
            + HOSTING_CONSENT
            + "\n\n"
            + UNSUPPORTED
        )
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Publish to TikTok?",
            message,
            QMessageBox.StandardButton.Cancel,
            self.window,
        )
        go = box.addButton("Post Publicly via Buffer", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        video_id = self.video_id

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
                "Cancel this TikTok post?",
                "Delete it from Buffer's schedule and remove its hosted copy? Posts Buffer has already started cannot be cancelled.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(
                lambda c: self.service.cancel(post["id"]), self.reload("TikTok post cancelled.")
            )

    def release(self) -> None:
        post = self.selected_post(self.video_history)
        if (
            post
            and QMessageBox.question(
                self.window,
                "Allow another attempt?",
                "Only continue if you have checked Buffer and TikTok and this post did NOT go out. Another attempt could otherwise post a duplicate.",
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
            self.video_account.setText("TikTok isn't set up yet. Follow these steps:")
        elif connection.get("status") != "Connected":
            self.video_account.setText("Reconnect TikTok in Accounts to continue.")
        elif not hosting:
            self.video_account.setText("One step left before you can post:")
        else:
            self.video_account.setText(
                f"Posting as @{connection.get('name')} · public, through Buffer"
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

    # Automatic Posting ----------------------------------------------------------------------

    def build_automation(self) -> None:
        self.automation, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(logo("tiktok", 24)))
        heading.addWidget(text("TikTok", "Heading"), 1)
        self.auto_profiles = QComboBox()
        self.auto_profiles.setAccessibleName("TikTok automations")
        self.auto_profiles.currentIndexChanged.connect(lambda _: self.render_automation())
        heading.addWidget(self.auto_profiles)
        # Kept for its busy state; new auto uploads start from New Auto Upload above.
        self.new_automation = QPushButton("New TikTok Automation…")
        self.new_automation.clicked.connect(self.new_tiktok_automation)
        self.new_automation.hide()
        body.addLayout(heading)
        self.auto_headline = text("No TikTok automations yet", "Notice")
        self.auto_status = text("", "Muted")
        self.auto_activity = QListWidget()
        self.auto_activity.setFixedHeight(140)
        self.auto_activity.setWordWrap(True)
        for widget in (self.auto_headline, self.auto_status, self.auto_activity):
            body.addWidget(widget)
        self.auto_pause = icon_button("Pause", "pause")
        self.auto_resume = icon_button("Resume", "play", role="Primary")
        self.auto_revoke = icon_button("Turn Off…", "power", role="Danger")
        self.auto_revoke.setAccessibleName("Revoke Approval…")
        self.auto_remove = icon_button("Remove…", "trash", role="Danger")
        for button, action in (
            (self.auto_pause, "pause"),
            (self.auto_resume, "resume"),
            (self.auto_revoke, "revoke"),
            (self.auto_remove, "remove"),
        ):
            button.clicked.connect(lambda checked=False, a=action: self.control(a))
        body.addWidget(flow(self.auto_pause, self.auto_resume, self.auto_revoke, self.auto_remove))
        self.auto_feedback = text("", "Muted")
        body.addWidget(self.auto_feedback)

    def current_profile(self) -> dict[str, Any] | None:
        return next(
            (p for p in self.data["profiles"] if p["id"] == self.auto_profiles.currentData()), None
        )

    def render_automation(self) -> None:
        selected = self.auto_profiles.currentData()
        self.auto_profiles.blockSignals(True)
        self.auto_profiles.clear()
        for p in self.data["profiles"]:
            self.auto_profiles.addItem(p["name"], p["id"])
        self.auto_profiles.setCurrentIndex(max(0, self.auto_profiles.findData(selected)))
        self.auto_profiles.blockSignals(False)
        p = self.current_profile()
        self.auto_profiles.setVisible(len(self.data["profiles"]) > 1)
        self.automation.setVisible(bool(self.data["profiles"]))
        self.auto_activity.setVisible(p is not None)
        for button in (self.auto_pause, self.auto_resume, self.auto_revoke, self.auto_remove):
            button.setVisible(p is not None)
        if not p:
            self.auto_headline.setText("No TikTok automations yet")
            self.auto_status.setText(
                "Choose New Auto Upload and tick TikTok to post new folder videos to TikTok."
            )
            return
        paused = bool(self.window.automatic.data.get("paused", 1))
        connected = bool(
            self.data["connection"] and self.data["connection"].get("status") == "Connected"
        )
        self.auto_headline.setText(
            "Needs approval again"
            if p["revoked"]
            else "Paused · no new TikTok posts are scheduled"
            if not p["enabled"]
            else "Not posting · reconnect TikTok in Accounts"
            if not connected
            else "Not posting · automatic publishing is paused for the whole app (Turn On in the sidebar)"
            if paused
            else "Active · Buffer posts each video at its approved time"
        )
        rule = Rule.decode(p["settings"]["rule"])
        folder = next((f for f in self.data.get("folders", []) if f["id"] == p["folder_id"]), None)
        pending = [
            x
            for x in self.data["posts"]
            if x["profile_id"] == p["id"]
            and not x["released"]
            and x["state"] in ACTIVE_STATES + ("Publishing", "Sending to Buffer")
        ]
        self.auto_status.setText(
            f"Folder: {folder['name'] if folder else 'unavailable'} · TikTok: {p['channel_name']}\n"
            f"Schedule: {rule_label(rule)} · up to {p['settings']['daily_cap']} a day\n"
            + (
                f"Next post: {timestamp(pending[0]['due_at'], weekday=True)} ({pending[0]['state']})"
                if pending and pending[0]["due_at"]
                else "No post scheduled in Buffer right now"
            )
            + (f"\n{p['status']}" if p["status"] else "")
        )
        self.auto_pause.setVisible(bool(p["enabled"]))
        self.auto_resume.setVisible(not p["enabled"] and not p["revoked"])
        self.auto_revoke.setVisible(not p["revoked"])
        decisions = self.data.get("decisions", {}).get(p["id"], [])
        self.auto_activity.setVisible(bool(decisions))
        self.auto_activity.clear()
        for d in self.data.get("decisions", {}).get(p["id"], []):
            self.auto_activity.addItem(
                f"{d['name']} · {d['state']}"
                + (f" · {d['reason']}" if d["reason"] and d["state"] != "Excluded existing" else "")
            )

    def control(self, action: str) -> None:
        p = self.current_profile()
        if not p:
            return
        if (
            action in ("revoke", "remove")
            and QMessageBox.question(
                self.window,
                "Stop this TikTok automation?",
                "No new videos will be posted under it. A post already scheduled in Buffer stays scheduled — cancel it in Publishing Queue if needed.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.run(lambda c: self.service.control(p["id"], action), self.reload())

    def review_automation(
        self, values: dict[str, Any], done: Callable[[bool], None], parent: QWidget
    ) -> None:
        """Check an automation, show its own approval, and enable it only if approved."""

        def guarded(job: Callable[[Event], Any]) -> Callable[[Event], tuple[str, Any]]:
            def wrapped(cancel: Event) -> tuple[str, Any]:
                try:
                    return "ok", job(cancel)
                except (TikTokError, BufferError, HostingError) as exc:
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
            self.reload("TikTok auto upload turned on. Only videos added from now on post.")(None)
            done(True)

        def reviewed(result: tuple[str, Any]) -> None:
            if result[0] == "error":
                failed(result[1])
                return
            proposal = result[1]
            consent = TikTokConsent(proposal, parent)
            if consent.exec() != QDialog.DialogCode.Accepted:
                done(False)
                return
            scanner = self.window.watching.service
            self.run(guarded(lambda c: self.service.approve(proposal, scanner, c)), enabled)

        self.run(guarded(lambda c: self.service.preview(values)), reviewed)

    def new_tiktok_automation(self) -> None:
        editor = TikTokAutomationEditor(self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        values = editor.values()

        def reviewed(proposal: dict[str, Any]) -> None:
            consent = TikTokConsent(proposal, self.window)
            if consent.exec() == QDialog.DialogCode.Accepted:
                scanner = self.window.watching.service
                self.run(
                    lambda c: self.service.approve(proposal, scanner, c),
                    self.reload(
                        "TikTok automation enabled. Only videos added from now on are eligible."
                    ),
                )

        self.run(lambda c: self.service.preview(values), reviewed)

    # Queue ----------------------------------------------------------------------------------

    def build_queue(self) -> None:
        self.queue, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(logo("tiktok", 22)))
        heading.addWidget(text("TikTok uploads", "Heading"), 1)
        body.addLayout(heading)
        self.queue_list = QListWidget()
        self.queue_list.setWordWrap(True)
        self.queue_list.setMinimumHeight(150)
        self.queue_list.setAccessibleName("TikTok posts")
        self.queue_empty = text("", "Muted")
        self.queue_refresh = icon_button("Refresh Status", "refresh", role="Ghost")
        self.queue_refresh.setAccessibleName("Refresh TikTok Status")
        self.queue_refresh.clicked.connect(self.poll)
        self.queue_cancel = icon_button("Cancel Post…", "x", role="Danger")
        self.queue_cancel.setAccessibleName("Cancel Selected TikTok Post…")
        self.queue_cancel.clicked.connect(lambda: self.cancel_post(self.queue_list))
        self.queue_feedback = text("", "Muted")
        self.queue_feedback.hide()
        self.queue_actions = flow(self.queue_cancel, self.queue_refresh)
        for widget in (self.queue_empty, self.queue_list, self.queue_actions, self.queue_feedback):
            body.addWidget(widget)

    def render_queue(self) -> None:
        self.queue_list.clear()
        for post in self.data["posts"]:
            item = QListWidgetItem(label_for(post))
            item.setData(Qt.ItemDataRole.UserRole, post["id"])
            item.setToolTip(post["error"] or post["external_link"])
            self.queue_list.addItem(item)
        count = len(self.data["posts"])
        self.queue_empty.setText(
            f"{count} saved"
            if count
            else "No TikTok uploads yet. To post one, open a video, choose Upload, then TikTok."
        )
        self.queue_list.setVisible(bool(count))
        self.queue_actions.setVisible(bool(count))


class ConnectDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect TikTok via Buffer")
        layout = QVBoxLayout(self)
        layout.addWidget(text("Connect TikTok via Buffer", "Heading"))
        layout.addWidget(
            text(
                "1. If you have not yet, connect your TikTok account inside Buffer.\n"
                "2. Sign in to Buffer and allow ShortsScheduler to read your channels and create posts.\n"
                "3. Your browser returns here automatically. If it shows a code instead, paste it below.",
                "Muted",
            )
        )
        self.open_buffer = QPushButton("Open Buffer Channels…")
        self.sign_in = QPushButton("Sign In to Buffer…")
        primary(self.sign_in)
        layout.addWidget(self.open_buffer)
        layout.addWidget(self.sign_in)
        self.status = text("Waiting for Buffer to return here…", "Muted")
        layout.addWidget(self.status)
        self.pasted = QPlainTextEdit()
        self.pasted.setFixedHeight(60)
        self.pasted.setPlaceholderText("Paste the text shown by the relay page (only if needed)")
        layout.addWidget(self.pasted)
        self.use_pasted = QPushButton("Use Pasted Code")
        layout.addWidget(self.use_pasted)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TikTokAutomationEditor(QDialog):
    def __init__(self, controller: TikTokController) -> None:
        super().__init__(controller.window)
        self.setWindowTitle("New TikTok Automation")
        self.resize(560, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(text("TikTok automation · future files only", "Heading"))
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
        self.name = QLineEdit("My TikTok Automation")
        self.folder = QComboBox()
        for folder in controller.data.get("folders", []):
            if folder["enabled"]:
                self.folder.addItem(folder["name"], folder["id"])
        connection = controller.data["connection"] or {}
        account = text(f"TikTok account: @{connection.get('name') or 'not connected'} (via Buffer)")
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
        self.cap.setRange(1, 10)
        self.cap.setValue(3)
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
        second.addRow("AI-generated content", self.ai)
        third.addRow("Frequency", self.hours)
        third.addRow("First post of the day", self.start)
        third.addRow("Last possible post", self.end)
        third.addRow("Time zone (IANA)", self.zone)
        third.addRow("Most posts per day", self.cap)
        third.addRow("Eligible filenames", self.pattern)
        for body, form in zip(pages, (first, second, third), strict=True):
            body.addLayout(form)
        pages[1].addWidget(text(UNSUPPORTED, "Muted"))
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


class TikTokConsent(QDialog):
    def __init__(self, proposal: dict[str, Any], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Authorize this TikTok automation?")
        self.resize(560, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(text("Authorize this exact PUBLIC TikTok automation?", "Heading"))
        summary = QPlainTextEdit(proposal["summary"])
        summary.setReadOnly(True)
        layout.addWidget(summary)
        layout.addWidget(text(AUTOMATION_CONSENT, "Muted"))
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
