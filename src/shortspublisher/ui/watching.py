"""Local-only folder management; filesystem work stays on a cancellable worker."""

from __future__ import annotations

from threading import Event
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..media import Cancelled
from ..watching import POLL_SECONDS, QUIET_SECONDS, WatchService
from .components import Disclosure, primary
from .formatting import timestamp
from .videos import text


class FolderConsent(QDialog):
    def __init__(self, proposal: dict[str, Any], parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Automatically import videos")
        self.resize(540, 460)
        body = QVBoxLayout(self)
        body.addWidget(text("Watch this folder?", "Heading"))
        body.addWidget(text(proposal["path"]))
        body.addWidget(
            text(
                f"{len(proposal['candidates'])} existing candidate videos found. MP4, MOV, WebM, MKV, M4V and AVI; this folder only, no subfolders or symbolic links."
            )
        )
        self.existing = QComboBox()
        self.existing.addItem("Choose how to handle existing files", None)
        self.existing.addItem("Import existing eligible videos", True)
        self.existing.addItem("Watch newly added videos only", False)
        self.existing.setAccessibleName("Initial existing-file choice")
        body.addWidget(self.existing)
        self.quiet = QSpinBox()
        self.quiet.setRange(1, 3600)
        self.quiet.setValue(QUIET_SECONDS)
        self.quiet.setSuffix(" seconds without changes")
        self.quiet.setAccessibleName("File quiet period")
        body.addWidget(self.quiet)
        body.addWidget(
            text(
                "Completion is checked with repeated observations and video inspection. For reliable handoff, finish writing a temporary file, then rename it into this folder. Originals are never changed. This does not authorize publishing.",
                "Muted",
            )
        )
        self.background = QCheckBox(
            "Also import locally while the existing authorized background service runs"
        )
        self.background.setToolTip(
            "Does not install or start a service and does not authorize uploading."
        )
        body.addWidget(self.background)
        self.consent = QCheckBox("Enable automatic local importing from this folder")
        body.addWidget(self.consent)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.approve = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.approve.setText("Enable importing")
        self.approve.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.consent.toggled.connect(self.update_approval)
        self.existing.currentIndexChanged.connect(self.update_approval)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

    def update_approval(self) -> None:
        self.approve.setEnabled(
            self.consent.isChecked() and self.existing.currentData() is not None
        )


class WatchWorker(QThread):
    progress = Signal(str)
    result = Signal(object)

    def __init__(self, service: WatchService, action: str, argument: Any, parent: QWidget):
        super().__init__(parent)
        self.service, self.action, self.argument = service, action, argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            self.service.library.initialize()
            result: dict[str, Any] = {"action": self.action}
            if self.action == "preview":
                result["proposal"] = self.service.preview(self.argument, self.cancel)
            elif self.action == "add":
                proposal, options = self.argument
                result["folder_id"] = self.service.add(proposal, **options)
            elif self.action == "control":
                self.service.control(*self.argument)
            elif self.action == "scan":
                result.update(self.service.scan(self.cancel, progress=self.progress.emit))
            result["folders"] = self.service.folders()
            result["files"] = {f["id"]: self.service.files(f["id"]) for f in result["folders"]}
            hashes = {r["sha256"] for rows in result["files"].values() for r in rows if r["sha256"]}
            result["evidence"] = {digest: self.service.evidence(digest) for digest in hashes}
            if not self.cancel.is_set():
                self.result.emit(result)
        except Cancelled:
            pass
        except Exception as exc:
            if not self.cancel.is_set():
                self.result.emit({"error": str(exc)})


class WatchPanel(Disclosure):
    idle = Signal()
    library_changed = Signal()

    def __init__(self, service: WatchService):
        super().__init__("Automatically Import Videos")
        self.service = service
        self.worker: WatchWorker | None = None
        self.closing = False
        self.started = False
        self.configuring = False
        self.folders: list[dict[str, Any]] = []
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.evidence: dict[str, Any] = {}
        self.pending: dict[str, Any] | None = None
        self.counts: tuple[Any, ...] = ()
        self.body.addWidget(
            text(
                "Import Videos… adds files you pick once. A watched folder adds new finished videos automatically. Watching only imports — it never publishes.",
                "Muted",
            )
        )
        self.choose = QPushButton("Watch a Folder…")
        primary(self.choose)
        self.choose.clicked.connect(self.choose_folder)
        self.body.addWidget(self.choose)
        self.folder = QComboBox()
        self.folder.setAccessibleName("Watched folders")
        self.folder.setMinimumWidth(0)
        self.folder.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.folder.currentIndexChanged.connect(self.present)
        self.body.addWidget(self.folder)
        self.status = text(
            "Choose a folder and explicitly enable local importing. Nothing is published."
        )
        self.body.addWidget(self.status)
        self.scan_button = QPushButton("Scan Now")
        self.scan_button.clicked.connect(lambda: self.start("scan"))
        self.pause = QPushButton("Pause Watching")
        self.pause.clicked.connect(self.toggle_pause)
        self.body.addWidget(self.scan_button)
        self.body.addWidget(self.pause)
        self.results = Disclosure("View Import Results")
        self.activity = QListWidget()
        self.activity.setMinimumWidth(0)
        self.activity.setWordWrap(True)
        self.activity.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.activity.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.activity.setFixedHeight(170)
        self.activity.setAccessibleName("Folder import results")
        self.results.body.addWidget(self.activity)
        self.body.addWidget(self.results)
        self.settings = Disclosure("Folder Settings and Diagnostics")
        self.path = text("")
        self.settings.body.addWidget(self.path)
        self.change = QPushButton("Change Folder…")
        self.change.clicked.connect(self.change_folder)
        self.remove = QPushButton("Remove Folder From Monitoring…")
        self.remove.clicked.connect(self.remove_folder)
        self.settings.body.addWidget(self.change)
        self.settings.body.addWidget(self.remove)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setFixedHeight(160)
        self.settings.body.addWidget(self.diagnostics)
        self.body.addWidget(self.settings)
        self.feedback = text("", "Muted")
        self.body.addWidget(self.feedback)
        self.notifications = QFileSystemWatcher(self)
        self.notifications.directoryChanged.connect(self.changed_directory)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(1000)
        self.debounce.timeout.connect(lambda: self.start("scan"))
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_SECONDS * 1000)
        self.timer.timeout.connect(lambda: self.start("scan"))
        self.present()

    def activate(self) -> None:
        if not self.started and not self.closing:
            self.started = True
            self.timer.start()
            self.start("scan")

    @Slot(str)
    def changed_directory(self, path: str) -> None:
        if not self.debounce.isActive():
            self.debounce.start()

    def current(self) -> dict[str, Any] | None:
        return next((f for f in self.folders if f["id"] == self.folder.currentData()), None)

    @Slot()
    def choose_folder(self) -> None:
        self.configuring = True
        try:
            path = QFileDialog.getExistingDirectory(
                self, "Choose a folder for local video importing"
            )
        finally:
            self.configuring = False
        if path:
            self.start("preview", path)

    def toggle_pause(self) -> None:
        folder = self.current()
        if folder:
            self.start(
                "control",
                (folder["id"], folder["revision"], "pause" if folder["enabled"] else "resume"),
            )

    def remove_folder(self) -> None:
        folder = self.current()
        if (
            folder
            and QMessageBox.question(
                self,
                "Remove monitoring?",
                "Stop monitoring this folder? Original files, imported videos and duplicate history remain.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.start("control", (folder["id"], folder["revision"], "remove"))

    def change_folder(self) -> None:
        # Changing location is fresh consent; the old location must be paused explicitly first.
        folder = self.current()
        if folder and folder["enabled"]:
            self.feedback.setText(
                "Pause this folder first, then choose Change Folder. The old folder and its history will remain paused until removed."
            )
            return
        self.choose_folder()

    def start(self, action: str, argument: Any = None) -> None:
        if self.worker or self.closing or (action == "scan" and self.configuring):
            return
        self.pending = None
        self.worker = WatchWorker(self.service, action, argument, self)
        self.worker.result.connect(self.receive)
        self.worker.progress.connect(self.feedback.setText)
        self.worker.finished.connect(self.finished)
        for button in (self.choose, self.scan_button, self.pause, self.change, self.remove):
            button.setEnabled(False)
        self.worker.start()

    @Slot(object)
    def receive(self, result: dict[str, Any]) -> None:
        if self.closing:
            return
        if "error" in result:
            self.feedback.setText("Local importing needs attention: " + result["error"])
            return
        self.pending = result
        selected = self.folder.currentData()
        self.folders, self.records, self.evidence = (
            result["folders"],
            result["files"],
            result["evidence"],
        )
        self.folder.blockSignals(True)
        self.folder.clear()
        for folder in self.folders:
            self.folder.addItem(folder["name"], folder["id"])
        index = self.folder.findData(result.get("folder_id", selected))
        self.folder.setCurrentIndex(max(0, index))
        self.folder.blockSignals(False)
        wanted = {f["path"] for f in self.folders if f["enabled"]}
        for path in set(self.notifications.directories()) - wanted:
            self.notifications.removePath(path)
        for path in wanted - set(self.notifications.directories()):
            self.notifications.addPath(path)  # Failed/missing roots are retried on reconciliation.
        counts = tuple(
            (r["folder_id"], r["name"], r["state"], r["video_id"])
            for rows in self.records.values()
            for r in rows
        )
        if counts != self.counts:
            self.counts = counts
            self.library_changed.emit()
        self.present()
        if result.get("action") == "scan":
            self.feedback.setText("Scan complete. " + result.get("message", ""))
        if result.get("imported"):
            count = result["imported"]
            self.feedback.setText(
                f"Last import: {count} video(s) added to the library. Your editing session is unchanged."
            )

    @Slot()
    def present(self) -> None:
        folder = self.current()
        self.activity.clear()
        if folder:
            rows = self.records.get(folder["id"], [])
            imported = sum(r["state"] == "Imported successfully" for r in rows)
            duplicates = sum(
                r["state"]
                in ("Duplicate content", "Already in your library", "Known publishing history")
                for r in rows
            )
            waiting = sum(r["state"] == "Waiting for file to finish" for r in rows)
            self.status.setText(
                f"{folder['name']} · {'Needs attention' if folder['error'] else 'Watching' if folder['enabled'] else 'Paused'}\nLast checked: {timestamp(folder['scanned_at'], empty='Not checked yet')}\nCurrent results · Imported: {imported} · Duplicates: {duplicates} · Waiting: {waiting}"
                + ("\n" + folder["error"] if folder["error"] else "")
            )
            self.status.setToolTip(folder["path"])
            self.path.setText(
                folder["path"]
                + f"\nQuiet period: {folder['quiet_seconds']:g} seconds\nBackground local import: {'Allowed' if folder['background'] else 'Not allowed'}"
            )
            for row in rows:
                message = row["message"]
                if row["state"] == "Unsupported or invalid video":
                    message = (
                        "Cannot inspect this video yet. It will retry; details are in diagnostics."
                    )
                elif row["state"] == "Needs attention":
                    message = "Check file access and diagnostics. Retrying with a delay."
                self.activity.addItem(
                    f"{row['name']}\n{row['state']}" + (" · " + message if message else "")
                )
            import json

            self.diagnostics.setPlainText(
                json.dumps(
                    {
                        "folder": folder,
                        "files": rows,
                        "channel_evidence": self.evidence,
                        "publication_eligibility": "Not granted by monitoring",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            self.pause.setText("Pause Watching" if folder["enabled"] else "Resume Watching")
        else:
            self.status.setText(
                "Choose a folder and explicitly enable local importing. Nothing is published."
            )
            self.path.clear()
            self.diagnostics.clear()
        for control in (self.folder, self.scan_button, self.pause, self.results, self.settings):
            control.setVisible(folder is not None)

    @Slot()
    def finished(self) -> None:
        assert self.worker
        self.worker.deleteLater()
        self.worker = None
        for button in (self.choose, self.scan_button, self.pause, self.change, self.remove):
            button.setEnabled(True)
        self.idle.emit()
        if self.closing:
            return
        proposal = self.pending.get("proposal") if self.pending else None
        self.pending = None
        if proposal:
            dialog = FolderConsent(proposal, self)
            self.configuring = True
            try:
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
            finally:
                self.configuring = False
            if (
                accepted
                and dialog.consent.isChecked()
                and dialog.existing.currentData() is not None
            ):
                self.start(
                    "add",
                    (
                        proposal,
                        {
                            "consent": True,
                            "existing": dialog.existing.currentData(),
                            "background": dialog.background.isChecked(),
                            "quiet": dialog.quiet.value(),
                        },
                    ),
                )
            dialog.deleteLater()

    def request_close(self) -> bool:
        self.timer.stop()
        self.debounce.stop()
        self.closing = True
        if self.worker:
            self.worker.cancel.set()
            return False
        return True

    def stop_for_quit(self) -> None:
        self.request_close()
        if self.worker:
            self.worker.wait()
