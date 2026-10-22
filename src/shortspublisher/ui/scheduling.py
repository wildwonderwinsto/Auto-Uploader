"""Shared local schedule editor, persisted queue and month calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import Event
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, available_timezones

from PySide6.QtCore import (
    QDate,
    QLocale,
    QObject,
    Qt,
    QThread,
    QTime,
    QTimer,
    QTimeZone,
    Signal,
)
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import activity
from ..activity import Entry
from ..scheduling import Job, Proposal, ScheduleService, local_choices
from . import theme
from .dashboard import group_row
from .formatting import day_label, duration, file_size, system_zone, time_pattern, timestamp
from .icons import icon, image, logo, pixmap
from .videos import text

if TYPE_CHECKING:
    from .window import MainWindow

from .components import Section, card, flow, icon_button

NOTICE = "Saved plans require separate automatic Public activation and the global switch. Background execution also requires job permission and a running, explicitly enabled service. Older approvals remain desktop-only."


def details(
    snapshot: dict[str, Any],
    channel: str,
    local: str,
    zone: str,
    utc: str,
    *,
    diagnostics: bool = True,
) -> str:
    m = snapshot["metadata"]
    return (
        f"File: {snapshot['filename']}\n"
        + (f"Source: {snapshot['path']}\n" if diagnostics else "")
        + f"Size: {file_size(snapshot['size'])} · {duration(snapshot['duration'])} · {snapshot['width']} × {snapshot['height']}\n"
        f"YouTube: {snapshot['channel_title']}\nChannel ID: {channel}\nVisibility: PUBLIC\n"
        f"Title: {m['title']}\nDescription: {m['description']}\nTags: {', '.join(m['tags']) or 'None'}\n"
        f"Made for Kids: {'Yes' if m['made_for_kids'] else 'No'}\nSynthetic disclosure: {'Yes' if m['synthetic'] else 'No'}\n"
        f"Upload starts: {timestamp(utc, zone, weekday=True)}\nTime zone: {zone}\n"
        + (f"Source SHA-256: {snapshot['sha256']}\n" if diagnostics else "")
        + f"\n{NOTICE}"
    )


class ScheduleConfirmation(QDialog):
    def __init__(self, proposal: Proposal, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Approve Public Publishing Plan")
        self.resize(580, 510)
        layout = QVBoxLayout(self)
        layout.addWidget(text("Approve this schedule?", "Heading"))
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(
            details(
                proposal.snapshot,
                proposal.channel_id,
                proposal.local_time,
                proposal.timezone,
                proposal.scheduled_utc,
            )
        )
        summary = QPlainTextEdit()
        summary.setReadOnly(True)
        summary.setPlainText(
            details(
                proposal.snapshot,
                proposal.channel_id,
                proposal.local_time,
                proposal.timezone,
                proposal.scheduled_utc,
                diagnostics=False,
            )
        )
        layout.addWidget(summary)
        diagnostics = Section("Schedule diagnostics")
        box.appendPlainText(f"\nUTC: {proposal.scheduled_utc}\nLocal ISO: {proposal.local_time}")
        diagnostics.body.addWidget(box)
        layout.addWidget(diagnostics)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Approve Schedule")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ScheduleEditor(QDialog):
    def __init__(
        self, controller: ScheduleController, video_id: int, channel_id: str, job: Job | None = None
    ):
        super().__init__(controller.window)
        self.controller, self.video_id, self.channel_id, self.job = (
            controller,
            video_id,
            channel_id,
            job,
        )
        self.dirty = False
        self.setWindowTitle("Reschedule / Reapprove" if job else "Schedule Video")
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(text("Plan a Public YouTube upload", "Heading"))
        body.addWidget(text(NOTICE, "Muted"))
        connection = controller.window.accounts.view.connection
        channel_name = (
            job.channel_title
            if job
            else connection.channel.title
            if connection
            else "Channel not connected"
        )
        body.addWidget(text("YouTube · " + channel_name))
        selected = controller.window.videos.editor.preparation
        title = (
            job.snapshot["metadata"]["title"]
            if job
            else selected.metadata.title
            if selected
            else "Selected reviewed video"
        )
        body.addWidget(text("Video: " + title))
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("MMM d, yyyy")
        self.date.setDateRange(QDate(1900, 1, 1), QDate(9998, 12, 31))
        self.time = QTimeEdit()
        self.time.setDisplayFormat(time_pattern(seconds=True))
        self.zone = QComboBox()
        self.zone.setEditable(True)
        self.zone.addItems(sorted(available_timezones()))
        system = bytes(QTimeZone.systemTimeZoneId().data()).decode()
        zone = job.timezone if job else system if system in available_timezones() else "UTC"
        self.zone.setCurrentText(zone)
        initial = (
            datetime.fromisoformat(job.local_time)
            if job
            else (controller.service.clock() + timedelta(hours=1))
            .astimezone(ZoneInfo(zone))
            .replace(tzinfo=None)
        )
        self.date.setDate(QDate(initial.year, initial.month, initial.day))
        self.time.setTime(QTime(initial.hour, initial.minute, initial.second))
        self.occurrence = QComboBox()
        self.occurrence.addItem("Select occurrence if time repeats", None)
        self.occurrence.addItem("First occurrence (earlier UTC instant)", 0)
        self.occurrence.addItem("Second occurrence (later UTC instant)", 1)
        if job:
            self.occurrence.setCurrentIndex(job.fold + 1)
        for name, widget in (
            ("Local date", self.date),
            ("Local time", self.time),
            ("IANA time zone", self.zone),
            ("Repeated-clock occurrence", self.occurrence),
        ):
            label = QLabel(name)
            label.setBuddy(widget)
            body.addWidget(label)
            body.addWidget(widget)
        self.feedback = text("", "Muted")
        body.addWidget(self.feedback)
        self.activate_after = QCheckBox("After saving, review automatic Public activation")
        body.addWidget(self.activate_after)
        self.review = QPushButton("Review Schedule…")
        self.review.clicked.connect(lambda: controller.preview(self, False))
        body.addWidget(self.review)
        self.draft = QPushButton("Save Draft")
        self.draft.setEnabled(job is None or job.state == "Draft")
        self.draft.clicked.connect(lambda: controller.preview(self, True))
        body.addWidget(self.draft)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        body.addWidget(cancel)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        for signal in (
            self.date.dateChanged,
            self.time.timeChanged,
            self.zone.currentTextChanged,
            self.occurrence.currentIndexChanged,
        ):
            signal.connect(self.edited)
        self.show_time()

    def values(self) -> dict[str, Any]:
        return dict(
            video_id=self.video_id,
            channel_id=self.channel_id,
            local=self.date.date().toString("yyyy-MM-dd")
            + "T"
            + self.time.time().toString("HH:mm:ss"),
            zone=self.zone.currentText(),
            occurrence=self.occurrence.currentData(),
            job_id=self.job.id if self.job else "",
            expected_revision=self.job.revision if self.job else 0,
        )

    def edited(self, *_: Any) -> None:
        self.dirty = True
        self.show_time()

    def show_time(self) -> None:
        try:
            args = self.values()
            choices = local_choices(args["local"], args["zone"])
            self.feedback.setText(
                "Upload starts: "
                + " / ".join(timestamp(c, args["zone"], weekday=True) for c in choices)
            )
        except Exception as exc:
            self.feedback.setText(str(exc))

    def busy(self, value: bool) -> None:
        for widget in (self.date, self.time, self.zone, self.occurrence, self.review):
            widget.setEnabled(not value)
        self.draft.setEnabled(not value and (self.job is None or self.job.state == "Draft"))

    def reject(self) -> None:
        if self.controller.worker:
            self.controller.worker.cancel.set()
            self.feedback.setText("Stopping validation; wait before closing.")
            return
        if (
            self.dirty
            and QMessageBox.question(
                self,
                "Discard unsaved schedule?",
                "Discard these unsaved scheduling changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        super().reject()


class ScheduleWorker(QThread):
    result = Signal(object)

    def __init__(self, service: ScheduleService, action: str, argument: Any, parent: QObject):
        super().__init__(parent)
        self.service, self.action, self.argument = service, action, argument
        self.cancel = Event()

    def run(self) -> None:
        self.result.emit(self.service.execute(self.action, self.argument, self.cancel))


class SchedulePage(QScrollArea):
    def __init__(self, controller: ScheduleController, calendar: bool = False):
        super().__init__()
        self.controller = controller
        self.calendar_mode = calendar
        self.jobs: list[Job] = []
        self.setObjectName("calendar" if calendar else "queue")
        self.setWidgetResizable(True)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(32, 32, 32, 32)
        self.body.setSpacing(18)
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(4)
        names.addWidget(text("Calendar" if calendar else "Schedule", "Title"))
        names.addWidget(
            text("Everything waiting to upload on every platform, and when it goes out.", "Muted")
        )
        top.addLayout(names, 1)
        self.views: dict[str, QPushButton] = {}
        for key, label, glyph in (("queue", "List", "list"), ("calendar", "Calendar", "calendar")):
            view = QPushButton(label)
            view.setObjectName("Segment")
            view.setCheckable(True)
            view.setChecked(key == ("calendar" if calendar else "queue"))
            view.setIcon(icon(glyph, theme.MUTED))
            view.setCursor(Qt.CursorShape.PointingHandCursor)
            view.clicked.connect(lambda checked=False, k=key: self.switch(k))
            top.addWidget(view, 0, Qt.AlignmentFlag.AlignBottom)
            self.views[key] = view
        self.body.addLayout(top)
        self.calendar: QCalendarWidget | None = None
        self.marked: list[QDate] = []
        if calendar:
            frame, holder = card(16, 8)
            self.calendar = QCalendarWidget()
            self.calendar.setVerticalHeaderFormat(
                QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
            )
            self.calendar.setGridVisible(False)
            header = QTextCharFormat()
            header.setForeground(QColor(theme.MUTED))
            self.calendar.setHeaderTextFormat(header)
            for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
                self.calendar.setWeekdayTextFormat(day, header)
            for name, glyph in (
                ("qt_calendar_prevmonth", "chevron-left"),
                ("qt_calendar_nextmonth", "chevron-right"),
            ):
                button = self.calendar.findChild(QToolButton, name)
                if button:
                    button.setIcon(icon(glyph, theme.TEXT))
            self.calendar.setMinimumHeight(300)
            self.calendar.selectionChanged.connect(self.render)
            holder.addWidget(self.calendar)
            self.body.addWidget(frame)
        # All platforms in one agenda, grouped by day (filled from the shared timeline).
        self.agenda_frame, agenda = card(20, 4)
        heading = QHBoxLayout()
        heading.addWidget(image(pixmap("clock", 18, theme.ACCENT_TEXT)))
        self.agenda_title = text("On this day" if calendar else "Coming up", "Heading")
        heading.addWidget(self.agenda_title, 1)
        agenda.addLayout(heading)
        self.agenda_rows = QVBoxLayout()
        self.agenda_shown: object = None
        self.agenda_rows.setSpacing(0)
        agenda.addLayout(self.agenda_rows)
        self.agenda_empty = text(
            "Nothing on this day."
            if calendar
            else "Nothing is scheduled yet. Open a video and choose Upload, or set up Auto Upload.",
            "Muted",
        )
        self.agenda_empty.setContentsMargins(0, 8, 0, 8)
        agenda.addWidget(self.agenda_empty)
        self.body.addWidget(self.agenda_frame)
        # Posting times (recurring queues) and TikTok posts are placed here by the window.
        self.recurring_slot = QVBoxLayout()
        self.body.addLayout(self.recurring_slot)

        self.youtube, youtube = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(logo("youtube", 22)))
        heading.addWidget(text("Scheduled YouTube uploads", "Heading"), 1)
        self.filter = QComboBox()
        self.filter.setAccessibleName("Show scheduled uploads")
        self.filter.addItems(
            [
                "Active plans",
                "All plans",
                "Draft",
                "Scheduled",
                "Due",
                "Missed",
                "Needs Attention",
                "Cancelled",
                "Uploading",
                "Interrupted",
                "Paused",
                "Needs Reconciliation",
                "Processing",
                "Public Verified",
                "Completed",
                "Failed",
            ]
        )
        self.filter.setItemText(0, "Active")
        self.filter.setItemText(1, "All")
        self.filter.currentTextChanged.connect(self.render)
        heading.addWidget(self.filter)
        youtube.addLayout(heading)
        self.status = text("Open or reload to read saved plans.", "Muted")
        youtube.addWidget(self.status)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setMinimumHeight(150)
        self.list.currentRowChanged.connect(self.selected_changed)
        youtube.addWidget(self.list)
        self.refresh = icon_button("Reload", "refresh", role="Ghost")
        self.refresh.clicked.connect(lambda: controller.start("load"))
        self.edit = icon_button("Change Time…", "edit")
        self.edit.setAccessibleName("Reschedule / Review Again…")
        self.edit.clicked.connect(self.edit_selected)
        self.cancel = icon_button("Cancel Schedule…", "x", role="Danger")
        self.cancel.clicked.connect(self.cancel_selected)
        self.activate_job = icon_button("Approve Automatic Upload…", "check", role="Primary")
        self.activate_job.setAccessibleName("Activate automatic publishing…")
        self.activate_job.clicked.connect(self.activate_selected)
        self.history = icon_button("Upload History…", "history")
        self.history.clicked.connect(self.open_history)
        self.stop_upload = icon_button("Stop Current Upload", "x", role="Danger")
        self.stop_upload.clicked.connect(lambda: controller.window.automatic.stop_active())
        self.action_row = flow(
            self.activate_job, self.edit, self.cancel, self.history, self.stop_upload, self.refresh
        )
        youtube.addWidget(self.action_row)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFixedHeight(150)
        self.plan_details = Section("Selected upload")
        self.plan_details.body.addWidget(self.details)
        youtube.addWidget(self.plan_details)
        self.body.addWidget(self.youtube)
        self.tiktok_slot = QVBoxLayout()
        self.body.addLayout(self.tiktok_slot)
        self.body.addStretch()
        self.setWidget(content)
        self.selected_changed()

    def switch(self, key: str) -> None:
        for name, view in self.views.items():
            view.setChecked(name == ("calendar" if self.calendar_mode else "queue"))
        self.controller.window.navigate(key)

    def render_agenda(self, entries: list[Entry]) -> None:
        # Rebuilt only when something shown changes, so the regular refreshes don't flash.
        key = (
            datetime.now().date(),
            entries,
            self.calendar.selectedDate() if self.calendar else None,
        )
        if key == self.agenda_shown:
            return
        self.agenda_shown = key
        while self.agenda_rows.count():
            item = self.agenda_rows.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.deleteLater()
        if self.calendar:
            for date in self.marked:
                self.calendar.setDateTextFormat(date, QTextCharFormat())
            self.marked = []
            mark = QTextCharFormat()
            mark.setBackground(QColor(theme.ACCENT_SOFT))
            mark.setForeground(QColor(theme.ACCENT_TEXT))
            mark.setFontWeight(700)
            for entry in entries:
                moment = activity.instant(entry)
                if moment:
                    local = moment.astimezone(ZoneInfo(system_zone()))
                    date = QDate(local.year, local.month, local.day)
                    self.calendar.setDateTextFormat(date, mark)
                    self.marked.append(date)
            chosen = self.calendar.selectedDate()
            shown = [
                e
                for e in sorted(
                    entries, key=lambda e: activity.instant(e) or datetime.max.astimezone()
                )
                if (moment := activity.instant(e))
                and (local := moment.astimezone(ZoneInfo(system_zone())))
                and QDate(local.year, local.month, local.day) == chosen
            ]
            self.agenda_title.setText("On " + QLocale().toString(chosen, "dddd, MMMM d"))
            for group in activity.grouped(shown):
                self.agenda_rows.addWidget(group_row(group))
            self.agenda_empty.setVisible(not shown)
            return
        attention = [e for e in entries if e.kind == activity.ATTENTION]
        pending = activity.grouped(attention) + activity.grouped(activity.upcoming(entries))
        last = None
        for group in pending[:30]:
            label_text = (
                "Needs your attention"
                if group.kind == activity.ATTENTION
                else day_label(group.when)
            )
            if label_text != last:
                label = text(label_text, "Eyebrow")
                label.setContentsMargins(0, 12 if last else 4, 0, 2)
                self.agenda_rows.addWidget(label)
                last = label_text
            self.agenda_rows.addWidget(group_row(group))
        self.agenda_empty.setVisible(not pending)

    def selected(self) -> Job | None:
        item = self.list.currentItem()
        return next(
            (j for j in self.jobs if item and j.id == item.data(Qt.ItemDataRole.UserRole)), None
        )

    def render(self, *_: Any) -> None:
        selected = self.selected()
        identity = selected.id if selected else ""
        self.list.clear()
        shown = []
        for job in self.jobs:
            # The first two choices are shown as "Active" and "All".
            wanted = (
                ("Active plans", "All plans")[i]
                if (i := self.filter.currentIndex()) < 2
                else (self.filter.currentText())
            )
            if wanted == "Active plans" and job.state == "Cancelled":
                continue
            automatic = getattr(self.controller.window, "automatic", None)
            attempt = automatic.attempt(job) if automatic else None
            shown_state = (
                "Completed"
                if attempt
                and attempt.state == "Public Verified"
                and attempt.processing in ("processed", "succeeded")
                else attempt.state
                if attempt
                else job.state
            )
            if wanted not in ("All plans", "Active plans") and wanted != shown_state:
                continue
            date = QDate.fromString(job.local_time[:10], "yyyy-MM-dd")
            if self.calendar and date != self.calendar.selectedDate():
                continue
            item = QListWidgetItem(
                f"{job.snapshot['metadata']['title']}\n{timestamp(job.scheduled_utc, job.timezone, weekday=True)}\n{job.channel_title} · {self.execution_label(job)}"
            )
            item.setToolTip("Approved time zone: " + job.timezone)
            item.setData(Qt.ItemDataRole.UserRole, job.id)
            self.list.addItem(item)
            shown.append(job)
            if job.id == identity:
                self.list.setCurrentItem(item)
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self.status.setText(
            f"{len(shown)} shown · {len(self.jobs)} saved"
            if shown
            else (
                "No scheduled YouTube uploads on this day."
                if self.calendar_mode
                else "No scheduled YouTube uploads."
            )
            + " To schedule one, open a video, choose Upload, then Schedule."
        )
        self.list.setVisible(bool(shown))
        self.action_row.setVisible(bool(shown))
        self.plan_details.setVisible(bool(shown))
        window = self.controller.window
        if hasattr(window, "dashboard"):
            self.render_agenda(window.dashboard.entries)
        self.selected_changed()

    def selected_changed(self, *_: Any) -> None:
        job = self.selected()
        busy = self.controller.worker is not None
        self.edit.setEnabled(bool(job and job.state != "Cancelled") and not busy)
        self.cancel.setEnabled(bool(job and job.state != "Cancelled") and not busy)
        automatic = getattr(self.controller.window, "automatic", None)
        claimed = bool(job and automatic and automatic.execution(job))
        self.edit.setEnabled(self.edit.isEnabled() and not claimed)
        attempt = automatic.attempt(job) if automatic and job else None
        self.cancel.setEnabled(
            self.cancel.isEnabled() and (not claimed or bool(attempt and attempt.released))
        )
        self.cancel.setText(
            "Close Reconciled Schedule…" if attempt and attempt.released else "Cancel Schedule…"
        )
        self.activate_job.setEnabled(
            bool(
                job
                and job.state == "Scheduled"
                and not job.execution_enabled
                and not claimed
                and not busy
            )
        )
        self.history.setEnabled(claimed)
        self.history.setVisible(claimed)
        self.stop_upload.setEnabled(
            bool(
                automatic
                and attempt
                and attempt.state in ("Awaiting Confirmation", "Preparing", "Uploading")
            )
        )
        self.stop_upload.setVisible(self.stop_upload.isEnabled())
        self.refresh.setEnabled(not busy)
        self.details.setPlainText(
            (
                f"UTC: {job.scheduled_utc}\nLocal ISO: {job.local_time}\n{automatic.description(job) if automatic else 'Execution disabled'}\n\nJob: {job.id}\nPlan timing/state: {job.state} · Revision {job.revision}\n{job.reason}\nApproved: {timestamp(job.approved_at, job.timezone, empty='Not approved')}\nCancelled: {timestamp(job.cancelled_at, job.timezone, empty='No')}\n\n"
                + details(
                    job.snapshot, job.channel_id, job.local_time, job.timezone, job.scheduled_utc
                )
            )
            if job
            else "Select a saved plan to inspect its approved content and schedule."
        )

    def execution_label(self, job: Job) -> str:
        automatic = getattr(self.controller.window, "automatic", None)
        attempt = automatic.attempt(job) if automatic else None
        return (
            automatic.outcome(attempt)
            if automatic and attempt
            else f"{job.state} · "
            + (
                "Automatic posting paused"
                if job.execution_enabled and automatic and automatic.data.get("paused", True)
                else "Automatic posting on"
                if job.execution_enabled
                else "Awaiting activation"
            )
        )

    def activate_selected(self) -> None:
        job = self.selected()
        if job:
            self.controller.window.automatic.authorize(job)

    def open_history(self) -> None:
        window = self.controller.window
        job = self.selected()
        attempt = window.automatic.attempt(job) if job else None
        window.show_upload_history()
        window.uploads.receive({"attempts": window.automatic.data.get("attempts", [])})
        if attempt:
            for index, item in enumerate(window.uploads.attempts):
                if item.id == attempt.id:
                    window.uploads.history.setCurrentRow(index)

    def edit_selected(self) -> None:
        job = self.selected()
        if job:
            self.controller.open_editor(job.video_id, job.channel_id, job)

    def cancel_selected(self) -> None:
        job = self.selected()
        if (
            job
            and QMessageBox.question(
                self,
                "Cancel schedule?",
                f"Cancel this plan for {job.snapshot['metadata']['title']}? The source and remote videos are unchanged.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.controller.start("cancel", (job.id, job.revision))


class ScheduleController(QObject):
    idle = Signal()

    def __init__(self, service: ScheduleService, parent: MainWindow):
        super().__init__(parent)
        self.service = service
        self.window = parent
        self.worker: ScheduleWorker | None = None
        self.editor: ScheduleEditor | None = None
        self.closing = False
        self.jobs: list[Job] = []
        self.draft_mode = False
        self.queue = SchedulePage(self)
        self.calendar = SchedulePage(self, True)
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.refresh_if_idle)
        self.pending: dict[str, Any] = {}

    def activate(self) -> None:
        self.timer.start()
        self.refresh_if_idle()

    def refresh_if_idle(self) -> None:
        if not self.worker and not self.editor and not self.closing:
            self.start("load")

    def open_editor(self, video_id: int, channel_id: str, job: Job | None = None) -> None:
        if self.worker or self.editor:
            return
        self.editor = ScheduleEditor(self, video_id, channel_id, job)
        self.editor.exec()
        self.editor.deleteLater()
        self.editor = None

    def preview(self, editor: ScheduleEditor, draft: bool) -> None:
        self.draft_mode = draft
        self.start("preview", editor.values())

    def start(self, action: str, argument: Any = None) -> None:
        if self.worker or self.closing:
            return
        self.worker = ScheduleWorker(self.service, action, argument, self)
        self.pending = {}
        self.worker.result.connect(self.receive)
        self.worker.finished.connect(self.finished)
        if self.editor:
            self.editor.busy(True)
            self.editor.feedback.setText("Checking saved review, source and schedule…")
        for page in (self.queue, self.calendar):
            page.selected_changed()
            page.status.setText("Reading / validating local plans…")
        self.worker.start()

    def receive(self, result: dict[str, Any]) -> None:
        self.pending = result

    def finished(self) -> None:
        assert self.worker is not None
        self.worker.deleteLater()
        self.worker = None
        result = self.pending
        if self.editor:
            self.editor.busy(False)
        if "jobs" in result:
            self.jobs = result["jobs"]
            for page in (self.queue, self.calendar):
                page.jobs = self.jobs
                page.render()
            if self.editor and not self.closing:
                if self.editor.activate_after.isChecked() and not self.draft_mode:
                    job = next(
                        (
                            j
                            for j in self.jobs
                            if j.video_id == self.editor.video_id and j.state == "Scheduled"
                        ),
                        None,
                    )
                    if job:
                        self.window.automatic.authorize(job)
                self.editor.dirty = False
                self.editor.accept()
        if "error" in result:
            for page in (self.queue, self.calendar):
                page.status.setText(result["error"])
                page.selected_changed()
            if self.editor:
                self.editor.feedback.setText(result["error"])
        if "proposal" in result and self.editor and not self.closing:
            proposal = result["proposal"]
            if self.draft_mode:
                self.start("draft", proposal)
            else:
                dialog = ScheduleConfirmation(proposal, self.editor)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.start("approve", proposal)
                dialog.deleteLater()
        self.idle.emit()

    def request_close(self) -> bool:
        if self.worker:
            self.closing = True
            self.timer.stop()
            self.worker.cancel.set()
            return False
        if self.editor:
            self.editor.reject()
            if self.editor.isVisible():
                return False
        self.timer.stop()
        return True
