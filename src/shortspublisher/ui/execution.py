"""Desktop dispatcher and explicit activation UI. Workers own all I/O."""

from __future__ import annotations

from datetime import datetime
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QSize, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..auth import AccountError
from ..awake import Keeper
from ..background_state import BACKGROUND_CONSENT
from ..execution import CONSENT, AutomaticPublisher
from ..scheduling import Job, ScheduleError
from ..upload_store import Attempt
from .components import Section, card, icon_button, pill, restyle
from .formatting import timestamp, upload_status
from .icons import icon
from .scheduling import NOTICE, details, text

if TYPE_CHECKING:
    from .window import MainWindow


class ActivationDialog(QDialog):
    def __init__(self, job: Job, parent: MainWindow):
        super().__init__(parent)
        self.setWindowTitle("Authorize Automatic Public Publishing")
        self.resize(580, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(text("Authorize this exact PUBLIC upload?", "Heading"))
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(
            CONSENT
            + "\n\n"
            + details(
                job.snapshot, job.channel_id, job.local_time, job.timezone, job.scheduled_utc
            ).replace(
                NOTICE,
                "Global publishing must also be enabled. Pause prevents new starts; it does not undo an active transfer.",
            )
            + f"\nApproved plan revision: {job.revision}\nCategory: 22 (People & Blogs)"
        )
        summary = QPlainTextEdit()
        summary.setReadOnly(True)
        summary.setPlainText(
            CONSENT
            + "\n\n"
            + details(
                job.snapshot,
                job.channel_id,
                job.local_time,
                job.timezone,
                job.scheduled_utc,
                diagnostics=False,
            ).replace(
                NOTICE,
                "Global publishing must also be enabled. Pause prevents new starts; it does not undo an active transfer.",
            )
        )
        layout.addWidget(summary)
        diagnostics = Section("Activation diagnostics")
        content.appendPlainText(f"\nUTC: {job.scheduled_utc}\nLocal ISO: {job.local_time}")
        diagnostics.body.addWidget(content)
        layout.addWidget(diagnostics)
        self.background = QCheckBox("Also allow this exact upload while the interface is closed")
        self.background.setToolTip(BACKGROUND_CONSENT)
        layout.addWidget(self.background)
        layout.addWidget(text(BACKGROUND_CONSENT))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Authorize Automatic PUBLIC Upload"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ExecutionWorker(QThread):
    result = Signal(object)
    progress = Signal(object)

    def __init__(self, service: AutomaticPublisher, action: str, argument: Any, parent: QObject):
        super().__init__(parent)
        self.service, self.action, self.argument = service, action, argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            if self.action == "startup":
                self.service.startup()
            elif self.action == "dispatch":
                self.result.emit(
                    self.service.dispatch(self.cancel, self.progress.emit, self.result.emit)
                )
                return
            elif self.action == "authorize":
                self.service.authorize(
                    self.argument[0],
                    self.argument[1],
                    self.cancel,
                    background=bool(self.argument[2]) if len(self.argument) > 2 else False,
                )
            elif self.action == "control":
                self.service.control(*self.argument)
            self.result.emit(self.service.status())
        except Exception as exc:
            self.result.emit(
                {
                    "error": str(exc)
                    if isinstance(exc, (ScheduleError, AccountError))
                    else "Automatic publishing stopped safely. Reload and inspect the saved records."
                }
            )


class ExecutionController(QObject):
    idle = Signal()
    recurring_updated = Signal(object)
    changed = Signal()

    def __init__(self, service: AutomaticPublisher, window: MainWindow):
        super().__init__(window)
        self.service, self.window = service, window
        self.worker: ExecutionWorker | None = None
        self.control_worker: ExecutionWorker | None = None
        # Requests made while another runs go next, in order, instead of being dropped.
        self.queued: list[tuple[str, Any]] = []
        self.closing = False
        self.running = False
        self.data: dict[str, Any] = {}
        # Keeps the Mac and its display awake while an automatic upload is planned.
        self.keeper = Keeper()
        # Sidebar: the global switch, with its state beside it and a warning when uploads are
        # waiting while it is off. The window places `rail` at the bottom of the sidebar.
        self.title = text("Auto uploading", "Subheading")
        self.light = pill("…", "Neutral")
        self.state = text("Checking…", "Muted")
        self.state.setAccessibleName("Automatic publishing state")
        self.button = icon_button("Checking…", "power")
        self.button.clicked.connect(self.toggle)
        self.button.setEnabled(False)
        self.rail, rail = card(14, 8, "RailStatus")
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.title, 1)
        head.addWidget(self.light)
        rail.addLayout(head)
        rail.addWidget(self.state)
        rail.addWidget(self.button)
        # The same warning on the pages where uploads are planned.
        self.banners: list[tuple[QFrame, QLabel]] = []
        self.summary = text(
            "Automatic publishing is not running. Open the application normally to start its dispatcher."
        )
        self.notification = text("No automatic uploads have run yet.")
        self.stop = QPushButton("Stop Active Scheduled Upload")
        self.stop.setEnabled(False)
        self.stop.setVisible(False)
        self.stop.clicked.connect(self.stop_active)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)

    def activate(self) -> None:
        if self.running:
            return
        self.running = True
        self.start("startup")

    def start(self, action: str) -> None:
        if self.worker or self.closing:
            return
        worker = ExecutionWorker(self.service, action, None, self)
        self.worker = worker
        worker.result.connect(self.receive)
        worker.progress.connect(self.progress)
        worker.finished.connect(self.finished)
        worker.start()

    def tick(self) -> None:
        if self.running:
            self.start("dispatch")

    def control(self, action: str, argument: Any) -> None:
        if self.closing:
            return
        if self.control_worker:
            self.queued.append((action, argument))
            return
        worker = ExecutionWorker(self.service, action, argument, self)
        self.control_worker = worker
        worker.result.connect(self.receive)
        worker.finished.connect(self.control_finished)
        self.button.setEnabled(False)
        worker.start()

    def toggle(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        paused = bool(self.data.get("paused", 1))
        if (
            paused
            and QMessageBox.question(
                self.window,
                "Turn on auto uploading?",
                "Your approved scheduled uploads, queued videos and watched folders will upload "
                "PUBLICLY at their times, from this app or the background service if you allowed "
                "it. Times that have already passed are not caught up.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.control("control", (not paused, None))

    def authorize(self, job: Job) -> None:
        dialog = ActivationDialog(job, self.window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.control("authorize", (job.id, job.revision, dialog.background.isChecked()))
        dialog.deleteLater()

    def execution(self, job: Job) -> dict[str, Any] | None:
        return next((e for e in self.data.get("executions", []) if e["job_id"] == job.id), None)

    def attempt(self, job: Job) -> Attempt | None:
        execution = self.execution(job)
        return next(
            (
                a
                for a in self.data.get("attempts", [])
                if execution and a.id == execution["attempt_id"]
            ),
            None,
        )

    def outcome(self, attempt: Attempt) -> str:
        if attempt.state == "Public Verified" and attempt.processing in ("processed", "succeeded"):
            return "Video processed · Public verified"
        return upload_status(attempt.state, attempt.processing)

    def description(self, job: Job) -> str:
        attempt = self.attempt(job)
        if attempt:
            execution = self.execution(job)
            assert execution is not None
            return (
                f"Execution: {execution['id']} · {execution['phase']}\nUpload: {attempt.id} · {self.outcome(attempt)}\n"
                f"Start intent: {timestamp(execution['started_at'], empty='Not initiated')}\n"
                f"Acknowledged: {attempt.acknowledged:,} / {attempt.snapshot['size']:,} bytes\n"
                f"Video ID: {attempt.remote_id or 'Not returned'} · Privacy: {attempt.privacy or 'Unverified'}\n"
                f"Verification: {attempt.verification_status} · Processing: {attempt.processing or 'Unknown'}\n"
                f"{attempt.error or execution['error']}"
            )
        return "Automatic execution: " + (
            (
                "Activated — background permitted"
                if job.id in self.data.get("background_jobs", [])
                else "Activated — desktop only"
            )
            if job.execution_enabled
            else "Disabled — explicit activation required"
        )

    def receive(self, result: dict[str, Any]) -> None:
        if "recurring" in result:
            self.recurring_updated.emit(result["recurring"])
        if "jobs" in result:
            self.data = result
            self.keeper.hold(bool(result.get("awake")))
            jobs = result["jobs"]
            upcoming = [
                j
                for j in jobs
                if j.execution_enabled and j.state == "Scheduled" and not self.execution(j)
            ]
            attention = [
                j
                for j in jobs
                if j.state in ("Needs Attention", "Missed")
                or (
                    (attempt := self.attempt(j))
                    and attempt.state in ("Interrupted", "Paused", "Needs Reconciliation", "Failed")
                )
            ]
            active = [a for a in result["attempts"] if a.state in ("Preparing", "Uploading")]
            self.stop.setEnabled(bool(active and result["executions"]))
            self.stop.setVisible(self.stop.isEnabled())
            self.summary.setText(
                (
                    "Automatic publishing is paused. Scheduled videos will not post until you turn it on."
                    if result["paused"]
                    else "Automatic publishing is on."
                )
                + f"\nScheduled videos: {len(upcoming)} upcoming · {len(attention)} need attention\n"
                + (
                    f"Next upload starts {timestamp(upcoming[0].scheduled_utc, upcoming[0].timezone, weekday=True)}"
                    if upcoming
                    else "No individually scheduled video is activated."
                )
                + (f"\nUploading now: {active[0].snapshot['filename']}" if active else "")
            )
            self.window.schedules.jobs = jobs
            for page in (self.window.schedules.queue, self.window.schedules.calendar):
                page.jobs = jobs
                page.render()
            if not self.window.uploads.worker:
                self.window.uploads.receive({"attempts": result["attempts"]})
            if result["executions"]:
                latest = result["executions"][-1]
                job = next(j for j in jobs if j.id == latest["job_id"])
                attempt = self.attempt(job)
                self.notification.setText(
                    f"{job.snapshot['metadata']['title']} · {self.outcome(attempt) if attempt else job.state}\n"
                    + (
                        f"Processing: {attempt.processing or 'Unknown'} · Verification: {attempt.verification_status}\n{attempt.error}"
                        if attempt
                        else job.reason
                    )
                )
            elif attention:
                self.notification.setText(attention[0].reason)
        if result.get("error"):
            self.notification.setText(result["error"])
            if not self.data:
                # Never leave the sidebar on "Checking…" when the first load failed.
                self.state.setText("Status unavailable · see Dashboard")
                self.button.setText("Unavailable")
        self.render_switch()
        self.button.setEnabled(self.control_worker is None and bool(self.data))
        self.changed.emit()

    def waiting(self) -> list[str]:
        """What won't upload while auto uploading is off, in plain words. Posts already handed
        to Buffer are not listed: Buffer sends those itself."""
        w = self.window
        jobs = [
            j
            for j in self.data.get("jobs", [])
            if j.execution_enabled and j.state == "Scheduled" and not self.execution(j)
        ]
        recurring = getattr(getattr(w, "recurring", None), "data", None) or {}
        plans = {p["id"] for p in recurring.get("plans", []) if p["enabled"]}
        queued = [
            i
            for i in recurring.get("items", [])
            if i["state"] == "Queued" and i["plan_id"] in plans
        ]
        automation = getattr(w, "automation", None)
        folders = sum(
            bool(p["enabled"] and not p["revoked"] and not p["removed"])
            for p in (automation.data["profiles"] if automation else [])
        ) + sum(
            bool(p["enabled"] and not p["revoked"])
            for s in getattr(w, "socials", ())
            for p in s.data["profiles"]
        )

        def count(n: int, word: str) -> str:
            return f"{n} {word}{'s' if n != 1 else ''}"

        return [
            count(n, word)
            for n, word in (
                (len(jobs), "scheduled upload"),
                (len(queued), "queued video"),
                (folders, "watched folder"),
            )
            if n
        ]

    def render_switch(self) -> None:
        """The sidebar switch and every page's warning, from the latest state."""
        if not self.data:
            return
        paused = bool(self.data.get("paused", 1))
        waiting = self.waiting() if paused else []
        self.light.setText("Off" if paused else "On")
        restyle(self.light, ("Warn" if waiting else "Neutral") if paused else "Good")
        restyle(self.rail, "RailStatusWarn" if waiting else "RailStatus")
        self.state.setText(
            f"{' · '.join(waiting)} won't upload until you turn this on."
            if waiting
            else "Nothing uploads automatically."
            if paused
            else "Scheduled and queued uploads go out on time."
        )
        self.button.setText("Turn On" if paused else "Pause All")
        restyle(self.button, "Primary" if paused else "")
        self.button.setIcon(
            icon("power" if paused else "pause", "#ffffff" if paused else "#c9cdd6")
        )
        self.button.setIconSize(QSize(16, 16))
        message = (
            "Auto uploading is off, so these won't go out: "
            + ", ".join(waiting)
            + ". Turn it on to upload them at their times."
        )
        for banner, label in self.banners:
            label.setText(message)
            banner.setVisible(bool(waiting))

    def banner(self) -> QFrame:
        """A warning bar for a page; shown only while planned uploads are held back."""
        frame, layout = card(16, 8, "Warning")
        line = QHBoxLayout()
        line.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(icon("alert", "#f2b24c").pixmap(20, 20))
        line.addWidget(mark)
        label = text("", "")
        line.addWidget(label, 1)
        turn_on = icon_button("Turn On Auto Uploading", "power", role="Primary")
        turn_on.clicked.connect(self.toggle)
        line.addWidget(turn_on)
        layout.addLayout(line)
        frame.hide()
        self.banners.append((frame, label))
        return frame

    def progress(self, attempt: Attempt) -> None:
        self.stop.setEnabled(True)
        self.stop.setVisible(True)
        self.notification.setText(
            f"{attempt.state}: {attempt.snapshot['filename']} · "
            f"{attempt.acknowledged * 100 // attempt.snapshot['size']}% acknowledged. "
            "Visibility and processing are verified separately."
        )
        self.window.uploads.show_progress(attempt)
        attempts = self.data.setdefault("attempts", [])
        self.data["attempts"] = [attempt] + [a for a in attempts if a.id != attempt.id]
        # Link becomes available before the first chunk; the final worker result refreshes all records.

    def finished(self) -> None:
        assert self.worker
        self.worker.deleteLater()
        self.worker = None
        self.stop.setEnabled(
            any(a.state in ("Preparing", "Uploading") for a in self.data.get("attempts", []))
        )
        self.stop.setVisible(self.stop.isEnabled())
        if self.running and not self.closing:
            delay = 5000
            now = self.service.schedules.clock()
            for job in self.data.get("jobs", []):
                if job.execution_enabled and job.state == "Scheduled" and not self.execution(job):
                    remaining = (datetime.fromisoformat(job.scheduled_utc) - now).total_seconds()
                    if remaining > 0:
                        delay = min(delay, max(1, int(remaining * 1000)))
            self.timer.start(delay)
        self.idle.emit()

    def control_finished(self) -> None:
        assert self.control_worker
        self.control_worker.deleteLater()
        self.control_worker = None
        if self.queued and not self.closing:
            self.control(*self.queued.pop(0))
            return
        self.button.setEnabled(bool(self.data))
        self.idle.emit()

    def stop_active(self) -> None:
        state = self.data.get("background", {})
        if state.get("requested"):
            self.window.background.act("stop")
            return
        if self.worker:
            self.worker.cancel.set()
            self.notification.setText(
                "Stopping after the active request returns. Remote content is not deleted."
            )

    def request_close(self) -> bool:
        self.timer.stop()
        self.running = False
        self.queued.clear()
        self.keeper.hold(False)
        if self.worker or self.control_worker:
            self.closing = True
            for worker in (self.worker, self.control_worker):
                if worker:
                    worker.cancel.set()
            return False
        return True

    def stop_for_quit(self) -> None:
        self.request_close()
        for worker in (self.worker, self.control_worker):
            if worker:
                worker.wait()
