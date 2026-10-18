"""Explicit upload confirmation, acknowledged-byte progress, and durable history."""

from threading import Event
from typing import Any

from PySide6.QtCore import QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..accounts import AccountView
from ..upload_store import Attempt
from ..uploads import UploadService
from .components import Disclosure, primary
from .formatting import duration, file_size, timestamp, upload_status
from .videos import text


class UploadWorker(QThread):
    result = Signal(object)
    progress = Signal(object)

    def __init__(self, service: UploadService, action: str, argument: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.service, self.action, self.argument = service, action, argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            result = self.service.execute(
                self.action, self.argument, self.cancel, self.progress.emit
            )
        except Exception:
            result = {
                "error": "Upload operation failed safely. Inspect saved history before retrying."
            }
        self.result.emit(result)


class UploadConfirmation(QDialog):
    def __init__(self, attempt: Attempt, parent: QWidget) -> None:
        super().__init__(parent)
        privacy = attempt.requested_privacy
        public = privacy == "public"
        self.setWindowTitle(
            "Confirm PUBLIC YouTube Publication" if public else "Confirm Private YouTube Upload"
        )
        self.resize(580, 500)
        self.setMinimumSize(400, 360)
        body = QVBoxLayout(self)
        body.addWidget(
            text(
                "Publish a real PUBLIC video on YouTube?"
                if public
                else "Create a real PRIVATE video on YouTube?",
                "Heading",
            )
        )
        snapshot = attempt.snapshot
        payload = snapshot["payload"]
        details = QPlainTextEdit()
        details.setReadOnly(True)
        status = payload["status"]
        details.setPlainText(
            f"File: {snapshot['filename']}\n{snapshot['duration']:.3f} seconds · {snapshot['width']} × {snapshot['height']}\n"
            f"Source: {snapshot['path']}\nSize: {snapshot['size']:,} bytes\n"
            f"Destination: {snapshot['channel_title']}\nChannel ID: {attempt.channel_id}\n"
            f"Privacy: {privacy.upper()}"
            + (" — publicly accessible" if public else " (planned privacy is ignored)")
            + "\nCategory: People & Blogs (22)\n"
            + f"Made for Kids: {'Yes' if status['selfDeclaredMadeForKids'] else 'No'}\n"
            f"Synthetic disclosure: {'Yes' if status['containsSyntheticMedia'] else 'No'}\n"
            f"Title: {payload['snippet']['title']}\nDescription:\n{payload['snippet']['description']}\n"
            f"Tags: {', '.join(payload['snippet'].get('tags', [])) or 'None'}\n"
            f"Source SHA-256: {snapshot['sha256']}"
        )
        summary = QPlainTextEdit()
        summary.setObjectName("confirmation_summary")
        summary.setReadOnly(True)
        summary.setPlainText(
            f"{snapshot['filename']} · {duration(snapshot['duration'])} · {file_size(snapshot['size'])}\n"
            f"Destination: {snapshot['channel_title']}\nChannel ID: {attempt.channel_id}\n"
            f"Privacy: {privacy.upper()}\n"
            f"Made for Kids: {'Yes' if status['selfDeclaredMadeForKids'] else 'No'}\n"
            f"Synthetic disclosure: {'Yes' if status['containsSyntheticMedia'] else 'No'}\n"
            f"Title: {payload['snippet']['title']}\nDescription:\n{payload['snippet']['description']}\n"
            f"Tags: {', '.join(payload['snippet'].get('tags', [])) or 'None'}"
        )
        body.addWidget(summary, 1)
        diagnostics = Disclosure("Source diagnostics")
        diagnostics.body.addWidget(details)
        body.addWidget(diagnostics)
        body.addWidget(
            text(
                (
                    "This creates a real publicly accessible YouTube video. Confirm the exact channel and content. Stopping does not undo publication or delete remote content."
                    if public
                    else "Confirm only if this is the intended channel and content. This sends the original video to YouTube. Stopping does not delete remote content."
                ),
                "Muted",
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Publish Publicly Now" if public else "Upload Privately Now"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)


class UploadPanel(QWidget):
    idle = Signal()
    changed = Signal()

    def __init__(self, service: UploadService) -> None:
        super().__init__()
        self.service = service
        self.worker: UploadWorker | None = None
        self.closing = False
        self.loaded = False
        self.pending_confirmation: Attempt | None = None
        self.account: AccountView | None = None
        self.video_id: int | None = None
        self.revision = 0
        self.reviewed = False
        self.planned_privacy = ""
        self.attempts: list[Attempt] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(text("Publish now", "Heading"))
        layout.addWidget(
            text(
                "Choose visibility, then review the final confirmation. To upload later, use Schedule.",
                "Muted",
            )
        )
        self.reason = text(
            "Select a reviewed video and verify upload permission in Accounts.", "Muted"
        )
        layout.addWidget(self.reason)
        self.upload_button = QPushButton("Upload Privately…")
        self.upload_button.clicked.connect(lambda: self.prepare("private"))
        layout.addWidget(self.upload_button)
        self.public_reason = text(
            "Public publishing requires a saved, reviewed Public preference.", "Muted"
        )
        layout.addWidget(self.public_reason)
        self.public_button = QPushButton("Publish Publicly Now…")
        primary(self.public_button)
        self.public_button.clicked.connect(lambda: self.prepare("public"))
        layout.addWidget(self.public_button)
        self.feedback = text("No transfer is active.", "Muted")
        layout.addWidget(self.feedback)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.stop_button = QPushButton("Stop Upload")
        self.stop_button.clicked.connect(self.stop)
        layout.addWidget(self.stop_button)
        layout.addWidget(text("Upload History", "Heading"))
        self.history = QListWidget()
        self.history.setMinimumWidth(0)
        self.history.setFixedHeight(140)
        self.history.currentRowChanged.connect(self.select_history)
        layout.addWidget(self.history)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(210)
        history_details = Disclosure("Upload details and recovery")
        history_details.changed.connect(self.changed.emit)
        layout.addWidget(history_details)
        history_details.body.addWidget(self.details)
        self.reload_button = QPushButton("Reload Upload History")
        self.reload_button.clicked.connect(lambda: self.start("history"))
        self.resume_button = QPushButton("Check Session and Resume…")
        self.resume_button.clicked.connect(self.resume)
        self.verify_button = QPushButton("Verify Remote Result")
        self.verify_button.clicked.connect(lambda: self.start_selected("verify"))
        self.release_button = QPushButton("Acknowledge Outcome / Allow Another Attempt…")
        self.release_button.clicked.connect(self.release)
        self.link_button = QPushButton("Open Returned Video on YouTube")
        self.link_button.clicked.connect(self.open_video)
        self.cancel_confirmation = QPushButton("Cancel Unstarted Confirmation")
        self.cancel_confirmation.clicked.connect(lambda: self.start_selected("cancel_confirmation"))
        for button in (
            self.reload_button,
            self.resume_button,
            self.verify_button,
            self.link_button,
            self.cancel_confirmation,
            self.release_button,
        ):
            (
                history_details.body
                if button in (self.resume_button, self.cancel_confirmation, self.release_button)
                else layout
            ).addWidget(button)
        self.refresh_controls()

    def selected(self) -> Attempt | None:
        index = self.history.currentRow()
        return self.attempts[index] if 0 <= index < len(self.attempts) else None

    def context(
        self, video_id: int | None, revision: int, reviewed: bool, planned_privacy: str = ""
    ) -> None:
        self.video_id, self.revision, self.reviewed = video_id, revision, reviewed
        self.planned_privacy = planned_privacy
        self.refresh_controls()

    @Slot(object)
    def account_changed(self, view: AccountView) -> None:
        self.account = view
        self.refresh_controls()

    def refresh_controls(self) -> None:
        busy = self.worker is not None
        reason = "Ready to review the final upload confirmation."
        if busy:
            reason = "An upload operation is active. Stop or wait before starting another."
        elif not self.video_id or not self.reviewed:
            reason = "Save your details and complete review first."
        elif (
            not self.account
            or not self.account.state.startswith("Connected")
            or not self.account.connection
        ):
            reason = "Use Accounts → Refresh Channel to verify the intended channel."
        elif not self.account.upload_allowed:
            reason = "Authorize Uploads in Accounts first."
        elif blocking := [
            a
            for a in self.attempts
            if not a.released
            and a.state != "Cancelled"
            and (
                a.video_id == self.video_id
                or a.state not in ("Uploaded Private", "Public Verified", "Failed")
            )
        ]:
            # Same gate as before; the wording says which situation the user is in.
            done = [
                a
                for a in blocking
                if a.video_id == self.video_id
                and a.state in ("Uploaded Private", "Public Verified")
            ]
            reason = (
                "This video has already been uploaded — see Upload History below. "
                "Duplicate uploads are blocked."
                if done and len(done) == len(blocking)
                else "An earlier upload needs attention before another can start. Select it in "
                "Upload History below to resume or check its result."
            )
        self.reason.setText(reason)
        self.upload_button.setEnabled(reason.startswith("Ready") and not self.closing)
        public_ready = self.upload_button.isEnabled() and self.planned_privacy == "public"
        self.public_button.setEnabled(public_ready)
        self.public_reason.setText(
            "PUBLIC: final confirmation will authorize a publicly accessible video."
            if public_ready
            else "Save Planned privacy as Public and complete review to publish publicly."
            if self.planned_privacy != "public"
            else reason
        )
        # Don't repeat the same blocking sentence under both buttons.
        self.public_reason.setVisible(self.public_reason.text() != reason or public_ready)
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.reload_button.setEnabled(not busy)
        old = self.selected()
        self.resume_button.setEnabled(
            not busy
            and bool(
                old
                and not old.released
                and not old.remote_id
                and old.state in ("Paused", "Interrupted", "Needs Reconciliation")
            )
        )
        self.verify_button.setEnabled(not busy and bool(old and old.remote_id))
        self.link_button.setEnabled(bool(old and old.remote_id))
        self.link_button.setVisible(bool(old and old.remote_id))
        self.verify_button.setVisible(bool(old and old.remote_id))
        self.release_button.setEnabled(
            not busy
            and bool(old and not old.released and old.state not in ("Preparing", "Uploading"))
        )
        self.cancel_confirmation.setEnabled(
            not busy and bool(old and old.state == "Awaiting Confirmation")
        )
        self.changed.emit()

    @Slot()
    def prepare(self, privacy: str = "private") -> None:
        button = self.public_button if privacy == "public" else self.upload_button
        if self.account and self.account.connection and button.isEnabled():
            self.start(
                "prepare_public" if privacy == "public" else "prepare",
                (self.video_id, self.revision, self.account.connection.channel.id),
            )

    def start_selected(self, action: str) -> None:
        if old := self.selected():
            self.start(action, old.id)

    def start(self, action: str, argument: Any = None) -> None:
        if self.worker or self.closing:
            return
        self.worker = UploadWorker(self.service, action, argument, self)
        self.worker.result.connect(self.receive)
        self.worker.progress.connect(self.show_progress)
        self.worker.finished.connect(self.finished)
        self.feedback.setText(
            "Checking saved upload history…"
            if action == "history"
            else "Preparing / checking source and destination…"
        )
        self.progress.setRange(0, 0)
        self.refresh_controls()
        self.worker.start()

    @Slot(object)
    def receive(self, result: dict[str, Any]) -> None:
        if "attempts" in result:
            self.attempts = result["attempts"]
            self.history.clear()
            for attempt in self.attempts:
                item = QListWidgetItem(
                    f"{attempt.snapshot['payload']['snippet']['title']} · {attempt.requested_privacy.title()} · {upload_status(attempt.state, attempt.processing)}"
                )
                self.history.addItem(item)
            if self.attempts:
                self.history.setCurrentRow(0)
        if "error" in result:
            self.feedback.setText(result["error"])
        elif "confirmation" in result:
            self.pending_confirmation = result["confirmation"]
        elif self.attempts:
            self.show_progress(self.attempts[0])
        else:
            self.feedback.setText("No upload attempts yet.")

    @Slot(object)
    def show_progress(self, attempt: Attempt) -> None:
        for index, old in enumerate(self.attempts):
            if old.id == attempt.id:
                self.attempts[index] = attempt
                self.history.item(index).setText(
                    f"{attempt.snapshot['payload']['snippet']['title']} · {attempt.requested_privacy.title()} · {upload_status(attempt.state, attempt.processing)}"
                )
                if self.history.currentRow() == index:
                    self.select_history()
                break
        total = attempt.snapshot["size"]
        self.progress.setRange(0, 100)
        self.progress.setValue(attempt.acknowledged * 100 // total)
        self.feedback.setText(
            f"{attempt.requested_privacy.title()} · {upload_status(attempt.state, attempt.processing)} · {file_size(attempt.acknowledged)} / {file_size(total)} received by YouTube\n{attempt.error}"
        )
        self.changed.emit()

    @Slot()
    def finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.loaded = True
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self.refresh_controls()
        pending, self.pending_confirmation = self.pending_confirmation, None
        if pending and not self.closing:
            dialog = UploadConfirmation(pending, self)
            action = (
                ("confirm_public" if pending.requested_privacy == "public" else "confirm")
                if dialog.exec() == QDialog.DialogCode.Accepted
                else "cancel_confirmation"
            )
            dialog.deleteLater()
            self.start(action, pending.id)
        self.idle.emit()

    @Slot()
    def select_history(self) -> None:
        old = self.selected()
        if old:
            self.details.setPlainText(
                f"{old.snapshot['payload']['snippet']['title']}\nState: {old.state}\n"
                f"Destination: {old.snapshot['channel_title']} ({old.channel_id})\n"
                f"Requested privacy: {old.requested_privacy.upper()}\nVerification: {old.verification_status}\nLast verified observation: {timestamp(old.verified_at, empty='Historical / not recorded')}\n"
                f"YouTube video ID: {old.remote_id or 'Not returned'}\nReturned privacy: {old.privacy or 'Unverified'}\n"
                f"Processing / upload status: {old.processing or 'Unknown'}\nCreated: {old.created_at}\n"
                f"Last local update: {old.updated_at}\n"
                f"Transfer completed: {old.completed_at or 'Not confirmed'}\n"
                f"Acknowledged: {old.acknowledged:,} / {old.snapshot['size']:,} bytes\n{old.error}\n"
                "Local history cannot guarantee detection of every remote duplicate. Stopping never deletes a remote video."
            )
        else:
            self.details.clear()
        self.refresh_controls()

    @Slot()
    def resume(self) -> None:
        old = self.selected()
        if not old:
            return
        if (
            QMessageBox.question(
                self,
                "Resume existing upload",
                f"Check the existing session and continue sending the original approved content if valid? This may complete a real {old.requested_privacy.upper()} video on {old.snapshot['channel_title']} ({old.channel_id}). No new session will be created.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.start_selected("resume")

    @Slot()
    def release(self) -> None:
        if (
            QMessageBox.warning(
                self,
                "Possible duplicate upload",
                "Check YouTube Studio and this attempt's returned ID first. Its remote outcome may be unknown. Allowing another attempt may create a duplicate. Nothing will be deleted or uploaded by this acknowledgement. Allow another attempt?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.start_selected("release")

    @Slot()
    def open_video(self) -> None:
        if old := self.selected():
            if old.remote_id:
                QDesktopServices.openUrl(QUrl("https://www.youtube.com/watch?v=" + old.remote_id))

    @Slot()
    def stop(self) -> None:
        if self.worker:
            self.worker.cancel.set()
            self.feedback.setText(
                "Stopping after the current request. No further chunks will start; remote content is not deleted."
            )

    def request_close(self) -> bool:
        self.closing = True
        self.stop()
        return self.worker is None

    @Slot()
    def stop_for_quit(self) -> None:
        self.stop()
        if self.worker:
            self.worker.wait()
