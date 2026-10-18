"""Explicit recurring batch approval and read-only opportunity/outcome projections."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from threading import Event
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..cadence import Rule
from ..library_view import LibraryEntry, read_entries
from ..recurring import CONSENT, RecurringService
from .components import Disclosure, day_row, primary
from .formatting import file_size, system_zone, time_pattern, timestamp, upload_status
from .preview import VideoPreview
from .scheduling import text

if TYPE_CHECKING:
    from .window import MainWindow


def rule_label(rule: Rule) -> str:
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    cadence = "Once per selected day" if not rule.hours else f"Every {rule.hours} hour(s)"
    return f"{cadence} · {', '.join(names[d] for d in rule.days)} · {rule.start}–{rule.end} (end included) · {rule.zone}"


def outcome(item: dict[str, Any]) -> str:
    if item.get("attempt_state"):
        state = item["attempt_state"]
        if state == "Public Verified":
            return (
                "Published and verified"
                if item.get("processing") in ("processed", "succeeded")
                else "Public verified · processing not complete"
            )
        if state == "Needs Reconciliation":
            return "Needs reconciliation"
        if state == "Interrupted":
            return "Interrupted"
        if state in ("Preparing", "Uploading", "Awaiting Confirmation"):
            return f"Publishing · {file_size(item.get('acknowledged') or 0)} acknowledged of {file_size(item['snapshot']['size'])}"
        return upload_status(state, item.get("processing") or "")
    return {
        "Queued": "Approved and queued · waiting for next opportunity",
        "Removed": "Removed from queue",
        "Needs Attention": "Needs attention",
        "Reserved": "Reserved · " + (item.get("job_state") or "Waiting for execution"),
    }.get(item["state"], item["state"])


DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class BatchConfirmation(QDialog):
    def __init__(self, proposal: dict[str, Any], parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Approve recurring PUBLIC queue")
        self.resize(610, 650)
        body = QVBoxLayout(self)
        body.addWidget(text("Approve these exact PUBLIC YouTube uploads?", "Heading"))
        summary = QPlainTextEdit()
        summary.setReadOnly(True)
        rule = Rule.decode(proposal["rule"])
        day = (
            datetime.fromisoformat(proposal["opportunities"][0])
            .astimezone(ZoneInfo(rule.zone))
            .date()
        )
        lines = [
            CONSENT,
            f"Channel: {proposal['channel_title']} ({proposal['channel_id']})",
            "Visibility: PUBLIC",
            rule_label(rule),
            f"{len(rule.on_day(day))} opportunities on {day.strftime('%b %d, %Y')} · up to {proposal['approved_limit']} automatic claims per UTC day across all plans, subject to the current global limit.",
            "DST: nonexistent local times are skipped; repeated local times use only the first occurrence.",
            "Invalid items need attention; the next valid approved item may use that opportunity. No more than one item per opportunity.",
            "Background execution: "
            + (
                "Authorized for these exact items if the existing service is running."
                if proposal["background"]
                else "Not authorized; desktop execution only."
            ),
            "\nSpecific videos in initial order:",
        ]
        for number, item in enumerate(proposal["items"], 1):
            snap = item["snapshot"]
            m = snap["metadata"]
            lines.append(
                f"\n{number}. {m['title']}\nFile: {snap['filename']}\nDescription: {m['description']}\nTags: {', '.join(m['tags'])}\nMade for kids: {m['made_for_kids']} · Synthetic content: {m['synthetic']}\nReview revision: {snap['revision']}"
            )
        lines += ["\nNext opportunities (not guaranteed publications):"] + [
            timestamp(t, rule.zone, weekday=True) for t in proposal["opportunities"]
        ]
        summary.setPlainText("\n".join(lines))
        body.addWidget(summary)
        diagnostic = Disclosure("Exact source fingerprints and approval diagnostics")
        raw = QPlainTextEdit()
        raw.setReadOnly(True)
        raw.setPlainText(json.dumps(proposal, indent=2, ensure_ascii=False))
        raw.setFixedHeight(160)
        diagnostic.body.addWidget(raw)
        body.addWidget(diagnostic)
        self.restrictions = QCheckBox(
            "These videos need no unresolved manual restriction or additional YouTube setting"
        )
        self.restrictions.setToolTip(
            "Age restriction requires YouTube Studio. Not made for kids is not an 18+ setting. Do not authorize videos needing an unsupported restriction."
        )
        body.addWidget(self.restrictions)
        body.addWidget(
            text(
                "Age restriction must be applied in YouTube Studio. “Not made for kids” is not an 18+ setting.",
                "Muted",
            )
        )
        self.consent = QCheckBox(
            "I authorize automatic PUBLIC uploads of this exact batch and rule"
        )
        body.addWidget(self.consent)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.approve = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.approve.setText("Approve Recurring Queue")
        self.approve.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.consent.toggled.connect(self.ready)
        self.restrictions.toggled.connect(self.ready)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

    def ready(self) -> None:
        self.approve.setEnabled(self.consent.isChecked() and self.restrictions.isChecked())


class RecurringEditor(QDialog):
    def __init__(self, controller: RecurringController, plan: dict[str, Any] | None = None):
        super().__init__(controller.window)
        self.controller = controller
        self.plan = plan
        self.dirty = False
        self.setWindowTitle("Add Reviewed Videos" if plan else "Recurring Posting")
        self.resize(590, 680)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(text("Select reviewed videos for automatic Public posting", "Heading"))
        account = controller.account
        self.channel = QComboBox()
        self.channel.addItem(account.get("title", "No connected channel"), account.get("id", ""))
        self.channel.setToolTip(
            account.get("id", "Connect the intended YouTube channel in Accounts.")
        )
        self.name = QLineEdit(plan["name"] if plan else "My posting queue")
        self.name.setMaxLength(100)
        self.frequency = QComboBox()
        self.frequency.addItem("Once per day", 0)
        for hours in (2, 3, 4, 1):
            self.frequency.addItem(f"Every {hours} hour(s)", hours)
        self.frequency.addItem("Custom whole-hour interval", -1)
        self.hours = QSpinBox()
        self.hours.setRange(1, 24)
        self.hours.setValue(6)
        self.days_preset = QComboBox()
        self.days_preset.addItems(["Every day", "Weekdays", "Weekends", "Custom days"])
        self.days = [QCheckBox(name[:3]) for name in DAY_NAMES]
        for day, name in zip(self.days, DAY_NAMES, strict=True):
            day.setChecked(True)
            day.setAccessibleName(name)
        self.days_preset.currentIndexChanged.connect(self.preset_days)
        self.start_time = QTimeEdit(QTime(9, 0))
        self.end_time = QTimeEdit(QTime(21, 0))
        for field in (self.start_time, self.end_time):
            field.setDisplayFormat(time_pattern())
        self.zone = QLineEdit(plan["rule"]["zone"] if plan else system_zone())
        self.limit = QSpinBox()
        self.limit.setRange(1, controller.data.get("control", {}).get("daily_limit", 3))
        self.limit.setValue(self.limit.maximum())
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        for label, widget in (
            ("Plan name", self.name),
            ("YouTube channel", self.channel),
            ("Frequency", self.frequency),
            ("Custom interval (hours)", self.hours),
            ("Posting days", self.days_preset),
            ("Start time / daily time", self.start_time),
            ("Window end (included)", self.end_time),
            ("IANA time zone", self.zone),
            ("Approved maximum automatic claims per UTC day", self.limit),
        ):
            form.addRow(label, widget)
            widget.setAccessibleName(label)
        body.addLayout(form)
        days = Disclosure("Selected days")
        days.set_expanded(True)
        days.body.addWidget(day_row(list(self.days)))
        body.addWidget(days)
        body.addWidget(
            text(
                "Intervals restart at the selected local start each day. Same-day windows only; the end is included. DST gaps are skipped and repeated times use the first occurrence.",
                "Muted",
            )
        )
        self.background = QCheckBox(
            "Also allow these videos while the authorized background service runs"
        )
        body.addWidget(self.background)
        if plan:
            rule = Rule.decode(plan["rule"])
            index = self.frequency.findData(rule.hours)
            self.frequency.setCurrentIndex(index if index >= 0 else self.frequency.findData(-1))
            self.hours.setValue(rule.hours or 1)
            self.start_time.setTime(QTime.fromString(rule.start, "HH:mm"))
            self.end_time.setTime(QTime.fromString(rule.end, "HH:mm"))
            self.limit.setMaximum(max(self.limit.maximum(), plan["approved_limit"]))
            self.limit.setValue(plan["approved_limit"])
            self.background.setChecked(bool(plan["background"]))
            for i, day in enumerate(self.days):
                day.setChecked(i in rule.days)
            for disabled_widget in (
                self.name,
                self.channel,
                self.frequency,
                self.hours,
                self.days_preset,
                self.start_time,
                self.end_time,
                self.zone,
                self.limit,
                self.background,
                *self.days,
            ):
                disabled_widget.setEnabled(False)
            body.addWidget(
                text(
                    "This plan’s approved rule is immutable. Changing channel or rule requires a new explicitly approved plan."
                )
            )
        self.videos = QListWidget()
        self.videos.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.videos.setFixedHeight(180)
        self.videos.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.videos.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.videos.setAccessibleName("Select reviewed videos for recurring queue")
        reserved = {
            i["video_id"]
            for i in controller.data.get("items", [])
            if i["state"] != "Removed" and i["channel_id"] == account.get("id")
        }
        for identity, entry in controller.entries.items():
            ready = entry.reviewed and entry.preparation.metadata.privacy == "public"
            item = QListWidgetItem(
                f"{entry.preparation.metadata.title or entry.video.path.rsplit('/', 1)[-1]} · {'Ready to approve' if ready else 'Needs review / Public preference'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(entry.video.path)
            if not ready or identity in reserved:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.videos.addItem(item)
        self.videos.itemSelectionChanged.connect(self.edited)
        body.addWidget(self.videos)
        self.preview = VideoPreview()
        preview = Disclosure("Preview selected video")
        preview.body.addWidget(self.preview)
        body.addWidget(preview)
        self.videos.currentItemChanged.connect(self.preview_video)
        self.feedback = text(
            "Select a finite batch. New watched-folder arrivals never join automatically."
        )
        body.addWidget(self.feedback)
        self.review = QPushButton("Review and Approve Queue…")
        primary(self.review)
        self.review.clicked.connect(self.review_batch)
        body.addWidget(self.review)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        body.addWidget(cancel)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        for line_field in (self.name, self.zone):
            line_field.textChanged.connect(self.edited)
        for combo_field in (self.frequency, self.days_preset):
            combo_field.currentIndexChanged.connect(self.edited)
        for field in (self.start_time, self.end_time):
            field.timeChanged.connect(self.edited)
        for number_field in (self.limit, self.hours):
            number_field.valueChanged.connect(self.edited)
        for check_field in (*self.days, self.background):
            check_field.toggled.connect(self.edited)

    def edited(self) -> None:
        self.dirty = True

    def preset_days(self, index: int) -> None:
        if index < 3:
            for i, day in enumerate(self.days):
                day.setChecked(index == 0 or (index == 1 and i < 5) or (index == 2 and i >= 5))

    def preview_video(self, item: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.preview.set_source(
            self.controller.entries[item.data(Qt.ItemDataRole.UserRole)].video.path if item else ""
        )

    def values(self) -> dict[str, Any]:
        hours = self.frequency.currentData()
        return {
            "videos": [
                self.videos.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.videos.count())
                if self.videos.item(i).isSelected()
            ],
            "channel": self.channel.currentData(),
            "rule": Rule(
                self.zone.text().strip(),
                tuple(i for i, d in enumerate(self.days) if d.isChecked()),
                self.start_time.time().toString("HH:mm"),
                self.end_time.time().toString("HH:mm"),
                self.hours.value() if hours == -1 else hours,
            ),
            "name": self.name.text(),
            "background": self.background.isChecked(),
            "limit": self.limit.value(),
            "plan_id": self.plan["id"] if self.plan else "",
        }

    def review_batch(self) -> None:
        self.controller.start("preview", self.values())

    def reject(self) -> None:
        if (
            self.dirty
            and QMessageBox.question(
                self,
                "Discard queue draft?",
                "Discard these unapproved queue choices?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Discard
        ):
            return
        if self.controller.worker:
            self.controller.worker.cancel.set()
        self.preview.release()
        super().reject()


class RecurringWorker(QThread):
    result = Signal(object)

    def __init__(self, controller: RecurringController, action: str, argument: Any):
        super().__init__(controller)
        self.service = controller.service
        self.action = action
        self.argument = argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            self.service.library.initialize()
            result: dict[str, Any] = {}
            if self.action == "preview":
                result["proposal"] = self.service.preview(**self.argument, cancel=self.cancel)
            elif self.action == "approve":
                result["approved"] = self.service.approve(
                    self.argument, self.cancel, consent=True, restrictions_resolved=True
                )
            elif self.action == "control":
                self.service.control(*self.argument)
            result["data"] = self.service.state()
            result["entries"] = read_entries(self.service.library, self.cancel)
            with closing(self.service.library.connect()) as db:
                row = db.execute(
                    "SELECT channel FROM account_connection WHERE platform='youtube'"
                ).fetchone()
                result["account"] = json.loads(row[0]) if row else {}
            if not self.cancel.is_set():
                self.result.emit(result)
        except Exception as exc:
            if not self.cancel.is_set():
                self.result.emit({"error": str(exc)})


class RecurringController(QObject):
    idle = Signal()

    def __init__(self, service: RecurringService, window: MainWindow):
        super().__init__(window)
        self.service = service
        self.window = window
        self.worker: RecurringWorker | None = None
        self.editor: RecurringEditor | None = None
        self.closing = False
        self.data: dict[str, Any] = {}
        self.entries: dict[int, LibraryEntry] = {}
        self.account: dict[str, Any] = {}
        self.pending: dict[str, Any] = {}
        self.summary = text("Recurring posting: no plan loaded.", "Muted")
        self.queue = Disclosure("Recurring Posting", expanded=True)
        body = self.queue.body
        self.new = QPushButton("New Recurring Posting Queue…")
        primary(self.new)
        self.new.clicked.connect(lambda: self.start("open"))
        body.addWidget(self.new)
        self.plans = QComboBox()
        self.plans.setMinimumWidth(0)
        self.plans.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.plans.currentIndexChanged.connect(self.render_plan)
        body.addWidget(self.plans)
        self.status = text("No recurring plans. Approve specific reviewed videos to begin.")
        body.addWidget(self.status)
        self.items = QListWidget()
        self.items.setFixedHeight(180)
        self.items.setWordWrap(True)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setAccessibleName("Recurring posting queue")
        body.addWidget(self.items)
        self.pause = QPushButton("Activate / Resume Plan")
        self.pause.clicked.connect(self.toggle)
        body.addWidget(self.pause)
        self.append = QPushButton("Approve Additional Reviewed Videos…")
        self.append.clicked.connect(lambda: self.start("append"))
        body.addWidget(self.append)
        self.up = QPushButton("Move Selected Up")
        self.up.clicked.connect(lambda: self.control("up"))
        body.addWidget(self.up)
        self.down = QPushButton("Move Selected Down")
        self.down.clicked.connect(lambda: self.control("down"))
        body.addWidget(self.down)
        self.remove = QPushButton("Remove Unstarted Item…")
        self.remove.clicked.connect(self.remove_item)
        body.addWidget(self.remove)
        self.preview_button = QPushButton("Open Selected Video / Preview")
        self.preview_button.clicked.connect(self.open_video)
        body.addWidget(self.preview_button)
        self.history = QPushButton("Open Upload History")
        self.history.clicked.connect(window.show_upload_history)
        body.addWidget(self.history)
        self.feedback = text("")
        body.addWidget(self.feedback)
        details = Disclosure("Queue approvals and opportunity history")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFixedHeight(180)
        details.body.addWidget(self.details)
        body.addWidget(details)
        self.calendar = Disclosure(
            "Recurring opportunities · not guaranteed publications", expanded=True
        )
        self.opportunities = QListWidget()
        self.opportunities.setFixedHeight(190)
        self.opportunities.setWordWrap(True)
        self.opportunities.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.opportunities.itemActivated.connect(self.open_slot)
        self.calendar.body.addWidget(self.opportunities)
        self.calendar.body.addWidget(
            text(
                "Select an opportunity and press Enter to open its plan. Individual schedules appear separately below.",
                "Muted",
            )
        )
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.refresh)
        QTimer.singleShot(0, self.activate)

    def activate(self) -> None:
        if not self.closing:
            self.timer.start()
            self.refresh()

    def refresh(self) -> None:
        if not self.editor:
            self.start("load")

    def current(self) -> dict[str, Any] | None:
        return next(
            (p for p in self.data.get("plans", []) if p["id"] == self.plans.currentData()), None
        )

    def start(self, action: str, argument: Any = None) -> None:
        if self.worker or self.closing:
            return
        if action == "append" and not self.current():
            return
        self.pending = {}
        self.worker = RecurringWorker(self, action, argument)
        self.worker.result.connect(self.receive)
        self.worker.finished.connect(self.finished)
        for button in (self.new, self.pause, self.append, self.up, self.down, self.remove):
            button.setEnabled(False)
        if self.editor:
            self.editor.review.setEnabled(False)
            self.editor.feedback.setText(
                "Checking current saved review, fingerprints and conflicts…"
            )
        self.worker.start()

    def receive(self, result: dict[str, Any]) -> None:
        self.pending = result

    def finished(self) -> None:
        assert self.worker
        action = self.worker.action
        self.worker.deleteLater()
        self.worker = None
        for button in (self.new, self.pause, self.append, self.up, self.down, self.remove):
            button.setEnabled(True)
        if self.editor:
            self.editor.review.setEnabled(True)
        result = self.pending
        self.pending = {}
        if self.closing:
            self.idle.emit()
            return
        if "error" in result:
            self.feedback.setText(result["error"])
            if self.editor:
                self.editor.feedback.setText(result["error"])
        if "data" in result:
            self.entries = result["entries"]
            self.account = result["account"]
            self.render(result["data"])
        if "approved" in result:
            self.feedback.setText(
                "Batch approved. New plans start paused; activate the plan and enable global publishing separately."
            )
            if self.editor:
                self.editor.dirty = False
                self.editor.preview.release()
                self.editor.accept()
        if "proposal" in result and self.editor:
            dialog = BatchConfirmation(result["proposal"], self.editor)
            if (
                dialog.exec() == QDialog.DialogCode.Accepted
                and dialog.consent.isChecked()
                and dialog.restrictions.isChecked()
            ):
                self.start("approve", result["proposal"])
            dialog.deleteLater()
        if action in ("open", "append") and "data" in result and not self.editor:
            self.editor = RecurringEditor(self, self.current() if action == "append" else None)
            self.editor.exec()
            self.editor.preview.release()
            self.editor.deleteLater()
            self.editor = None
        self.idle.emit()

    def render(self, data: dict[str, Any]) -> None:
        self.data = data
        selected = self.plans.currentData()
        self.plans.blockSignals(True)
        self.plans.clear()
        for plan in data.get("plans", []):
            self.plans.addItem(plan["name"], plan["id"])
        self.plans.setCurrentIndex(max(0, self.plans.findData(selected)))
        self.plans.blockSignals(False)
        self.render_plan()
        self.opportunities.clear()
        active = []
        for plan in data.get("plans", []):
            rule = Rule.decode(plan["rule"])
            after = max(self.service.clock(), datetime.fromisoformat(plan["next_slot"]))
            # Include the persisted upcoming opportunity when it is still in the future.
            slots = rule.next(after, 7)
            if datetime.fromisoformat(plan["next_slot"]) >= self.service.clock():
                slots.insert(0, datetime.fromisoformat(plan["next_slot"]))
            for slot in slots:
                row = QListWidgetItem(
                    f"{timestamp(slot, rule.zone, weekday=True)} · {plan['name']} · {'Opportunity' if plan['enabled'] else 'Paused opportunity'}"
                )
                row.setData(Qt.ItemDataRole.UserRole, plan["id"])
                self.opportunities.addItem(row)
            if plan["enabled"]:
                active.append(plan)
        queued = sum(i["state"] == "Queued" for i in data.get("items", []))
        attention = sum(
            i["state"] == "Needs Attention"
            or i.get("attempt_state") in ("Interrupted", "Needs Reconciliation", "Failed")
            for i in data.get("items", [])
        )
        next_plan = min(active, key=lambda p: p["next_slot"]) if active else None
        self.summary.setText(
            f"Recurring posting · {'Paused globally' if data.get('control', {}).get('paused', True) else 'Automatic publishing on'}\n{len(active)} active plan(s) · {queued} videos waiting · {attention} need attention"
            + (
                f"\n{next_plan['name']} · next opportunity {timestamp(next_plan['next_slot'], next_plan['rule']['zone'])}"
                if next_plan
                else ""
            )
        )

    def render_plan(self) -> None:
        plan = self.current()
        selected = (
            self.items.currentItem().data(Qt.ItemDataRole.UserRole)
            if self.items.currentItem()
            else None
        )
        self.items.clear()
        # Without a plan, an empty picker, empty list and seven disabled actions are only noise.
        for widget in (
            self.plans,
            self.items,
            self.pause,
            self.append,
            self.up,
            self.down,
            self.remove,
            self.preview_button,
            self.history,
        ):
            widget.setVisible(plan is not None)
        if not plan:
            self.status.setText(
                "No recurring queues yet. A recurring queue posts approved, reviewed videos one at a time on a repeating schedule — choose New Recurring Posting Queue… to start."
            )
            return
        items = [i for i in self.data["items"] if i["plan_id"] == plan["id"]]
        queued = [i for i in items if i["state"] == "Queued"]
        self.status.setText(
            f"{plan['name']} · {'Active' if plan['enabled'] else 'Paused'} · {plan['channel_title']}\n{rule_label(Rule.decode(plan['rule']))}\n{len(queued)} videos waiting · next opportunity {timestamp(plan['next_slot'], plan['rule']['zone'])}"
            + (
                f"\nExpected next: {queued[0]['snapshot']['metadata']['title']}"
                if queued
                else "\nNo eligible video waiting."
            )
        )
        self.pause.setText("Pause Plan" if plan["enabled"] else "Activate / Resume Plan")
        for item in items:
            entry = self.entries.get(item["video_id"])
            review = (
                "Automation-approved · see profile"
                if item["snapshot"].get("automation")
                else "Review complete (saved)"
                if entry and entry.reviewed
                else "Needs review / unavailable"
            )
            row = QListWidgetItem(
                f"{item['snapshot']['metadata']['title']}\n{outcome(item)} · {review}"
            )
            row.setData(Qt.ItemDataRole.UserRole, item["id"])
            row.setToolTip(item.get("upload_error") or item.get("job_reason") or item["reason"])
            self.items.addItem(row)
            if item["id"] == selected:
                self.items.setCurrentItem(row)
        self.details.setPlainText(
            json.dumps(
                {
                    "plan": plan,
                    "items": items,
                    "opportunities": [s for s in self.data["slots"] if s["plan_id"] == plan["id"]],
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    def control(self, action: str) -> None:
        plan = self.current()
        item = self.items.currentItem()
        if plan:
            self.start(
                "control",
                (
                    plan["id"],
                    plan["revision"],
                    action,
                    item.data(Qt.ItemDataRole.UserRole) if item else "",
                ),
            )

    def toggle(self) -> None:
        plan = self.current()
        if plan:
            self.control("pause" if plan["enabled"] else "resume")

    def remove_item(self) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Remove unstarted item?",
                "Remove this item from the queue? Source and upload history remain.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.control("remove")

    def open_video(self) -> None:
        row = self.items.currentItem()
        if row:
            item = next(
                i for i in self.data["items"] if i["id"] == row.data(Qt.ItemDataRole.UserRole)
            )
            self.window.navigate("videos")
            self.window.videos.start("select", item["video_id"])

    def open_slot(self, item: QListWidgetItem) -> None:
        self.window.navigate("queue")
        self.plans.setCurrentIndex(self.plans.findData(item.data(Qt.ItemDataRole.UserRole)))

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
