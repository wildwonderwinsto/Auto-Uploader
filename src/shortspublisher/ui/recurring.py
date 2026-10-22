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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..cadence import Rule
from ..library_view import LibraryEntry, read_entries
from ..recurring import CONSENT, RecurringService
from ..scheduling import ScheduleError
from ..tiktok import HOSTING_CONSENT, caption_from
from . import theme
from .components import Section, card, day_row, flow, icon_button
from .formatting import file_size, friendly, system_zone, time_pattern, timestamp, upload_status
from .icons import PLATFORM_NAMES, image, logo, pixmap
from .preview import VideoPreview
from .scheduling import text

if TYPE_CHECKING:
    from .window import MainWindow


def posting_times(rule: Rule, now: datetime, count: int, daily_limit: int) -> list[datetime]:
    """The next `count` posting times, at most `daily_limit` on any local day."""
    found: list[datetime] = []
    per_day: dict[Any, int] = {}
    after = now
    zone = ZoneInfo(rule.zone)
    while len(found) < count:
        batch = rule.next(after, 32)
        if not batch:
            break
        for slot in batch:
            day = slot.astimezone(zone).date()
            if per_day.get(day, 0) < daily_limit:
                per_day[day] = per_day.get(day, 0) + 1
                found.append(slot)
                if len(found) == count:
                    break
        after = batch[-1]
        if (after - now).days > 366:
            break
    if len(found) < count:
        raise ScheduleError("Not enough posting times in the next year. Pick fewer videos.")
    return found


def rule_label(rule: Rule) -> str:
    """ "Every day at 9:00 AM", "Weekdays, every 3 hours from 9:00 AM to 9:00 PM"."""
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    days = (
        "Every day"
        if len(rule.days) == 7
        else "Weekdays"
        if sorted(rule.days) == [0, 1, 2, 3, 4]
        else "Weekends"
        if sorted(rule.days) == [5, 6]
        else ", ".join(names[d] for d in sorted(rule.days))
    )

    def clock(value: str) -> str:
        return QTime.fromString(value, "HH:mm").toString(time_pattern())

    when = (
        f"at {clock(rule.start)}"
        if not rule.hours
        else f"every {rule.hours} hour{'s' if rule.hours != 1 else ''} from {clock(rule.start)} "
        f"to {clock(rule.end)}"
    )
    zone = "" if rule.zone == system_zone() else f" ({rule.zone})"
    return f"{days} {when}{zone}" if days != "Every day" else f"Every day {when}{zone}"


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
        diagnostic = Section("Exact source fingerprints and approval diagnostics")
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
        self.approved_youtube = False
        self.setWindowTitle("Add Videos to Schedule" if plan else "New Posting Schedule")
        self.resize(720, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 24, 24, 12)
        body.setSpacing(16)
        body.addWidget(
            text("Add ready videos to this schedule" if plan else "New posting schedule", "Title")
        )
        body.addWidget(
            text(
                "Choose when videos go out, which ones, and where. They go out publicly one at a "
                "time, in order. Nothing is scheduled until you approve it.",
                "Muted",
            )
        )
        account = controller.account
        self.channel = QComboBox()
        self.channel.addItem(account.get("title", "No connected channel"), account.get("id", ""))
        self.channel.setToolTip(
            account.get("id", "Connect the intended YouTube channel in Accounts.")
        )
        self.name = QLineEdit(plan["name"] if plan else "My posting schedule")
        self.name.setMaxLength(100)
        self.frequency = QComboBox()
        self.frequency.addItem("Once a day", 0)
        for hours in (1, 2, 3, 4):
            self.frequency.addItem(f"Every {hours} hour{'s' if hours > 1 else ''}", hours)
        self.frequency.addItem("Every … hours (choose)", -1)
        self.hours = QSpinBox()
        self.hours.setRange(1, 24)
        self.hours.setValue(6)
        self.hours.setSuffix(" hours")
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
        self.limit.setSuffix(" per day at most")
        accessible = (
            ("Plan name", self.name),
            ("YouTube channel", self.channel),
            ("Frequency", self.frequency),
            ("Custom interval (hours)", self.hours),
            ("Posting days", self.days_preset),
            ("Start time / daily time", self.start_time),
            ("Window end (included)", self.end_time),
            ("IANA time zone", self.zone),
            ("Approved maximum automatic claims per UTC day", self.limit),
        )
        for label, widget in accessible:
            widget.setAccessibleName(label)

        when, when_body = card(18, 10)
        when_body.addWidget(text("When should videos go out?", "Heading"))
        when_body.addWidget(self.days_preset)
        when_body.addWidget(day_row(list(self.days)))
        timing = QHBoxLayout()
        timing.setSpacing(8)
        timing.addWidget(self.frequency, 2)
        self.at_label = QLabel("at")
        timing.addWidget(self.at_label)
        timing.addWidget(self.start_time, 1)
        self.until_label = QLabel("until")
        timing.addWidget(self.until_label)
        timing.addWidget(self.end_time, 1)
        when_body.addLayout(timing)
        when_body.addWidget(self.hours)
        zone_row = QHBoxLayout()
        zone_label = QLabel("Time zone")
        zone_label.setObjectName("Muted")
        zone_row.addWidget(zone_label)
        zone_row.addWidget(self.zone, 1)
        when_body.addLayout(zone_row)
        self.summary = text("", "Notice")
        when_body.addWidget(self.summary)
        body.addWidget(when)

        # Where: every ready platform, each with its own way of holding the posts.
        tiktok = controller.window.tiktok.data
        tiktok_ready = (tiktok.get("connection") or {}).get("status") == "Connected" and bool(
            tiktok.get("hosting")
        )
        where, where_body = card(18, 10)
        where_body.addWidget(text("Where should they go?", "Heading"))
        self.targets: dict[str, QCheckBox] = {}
        for platform, ready, note in (
            (
                "youtube",
                bool(account.get("id")),
                account.get("title") or "Connect YouTube in Accounts first",
            ),
            (
                "tiktok",
                tiktok_ready,
                f"@{(tiktok.get('connection') or {}).get('name')} · each post is scheduled in Buffer"
                if tiktok_ready
                else "Connect TikTok and set up video hosting first",
            ),
        ):
            line = QHBoxLayout()
            line.setSpacing(10)
            check = QCheckBox()
            check.setEnabled(ready and not plan)
            check.setChecked(ready and (platform == "youtube" or not account.get("id")))
            check.setAccessibleName(f"Post to {PLATFORM_NAMES[platform]}")
            check.toggled.connect(self.fill_videos)
            line.addWidget(check)
            line.addWidget(image(logo(platform, 22)))
            platform_name = QLabel(PLATFORM_NAMES[platform])
            platform_name.setObjectName("Subheading")
            line.addWidget(platform_name)
            line.addWidget(text(note, "Subtle"), 1)
            where_body.addLayout(line)
            self.targets[platform] = check
        where.setVisible(not plan)  # adding to an approved schedule keeps its platform
        body.addWidget(where)

        which, which_body = card(18, 10)
        which_body.addWidget(text("Which videos?", "Heading"))
        which_body.addWidget(
            text(
                "Pick videos marked ready in Videos. For YouTube, Visibility must also be Public. "
                "New files in watched folders never join on their own.",
                "Subtle",
            )
        )
        self.videos = QListWidget()
        self.videos.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.videos.setFixedHeight(180)
        self.videos.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.videos.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.videos.setAccessibleName("Select reviewed videos for recurring queue")
        self.reserved = {
            i["video_id"]
            for i in controller.data.get("items", [])
            if i["state"] != "Removed" and i["channel_id"] == account.get("id")
        }
        self.videos.itemSelectionChanged.connect(self.edited)
        picker = QHBoxLayout()
        picker.setSpacing(14)
        picker.addWidget(self.videos, 1)
        self.preview = VideoPreview()
        self.preview.setFixedWidth(150)
        picker.addWidget(self.preview)
        which_body.addLayout(picker)
        self.videos.currentItemChanged.connect(self.preview_video)
        body.addWidget(which)
        self.fill_videos()

        options, options_body = card(18, 10)
        options_body.addWidget(text("Options", "Heading"))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Name", self.name)
        form.addRow("YouTube channel", self.channel)
        form.addRow("Daily limit", self.limit)
        options_body.addLayout(form)
        self.background = QCheckBox("Keep uploading while ShortsScheduler is closed")
        self.background.setToolTip("Needs Background Uploading turned on in Settings")
        options_body.addWidget(self.background)
        body.addWidget(options)
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
            when_body.addWidget(
                text(
                    "An approved schedule's times can't change. To use different times, create a new posting schedule.",
                    "Subtle",
                )
            )
        self.feedback = text("", "Muted")
        body.addWidget(self.feedback)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 10, 24, 18)
        cancel = icon_button("Cancel", "x")
        cancel.clicked.connect(self.reject)
        self.review = icon_button("Review and Approve…", "check", role="Primary")
        self.review.setAccessibleName("Review and Approve Queue…")
        self.review.clicked.connect(self.review_batch)
        footer.addWidget(cancel)
        footer.addStretch()
        footer.addWidget(self.review)
        layout.addLayout(footer)
        self.frequency.currentIndexChanged.connect(self.describe)
        self.hours.valueChanged.connect(self.describe)
        self.start_time.timeChanged.connect(self.describe)
        self.end_time.timeChanged.connect(self.describe)
        self.zone.textChanged.connect(self.describe)
        for day in self.days:
            day.toggled.connect(self.describe)
        self.describe()
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

    def fill_videos(self) -> None:
        """Ready means reviewed; YouTube also needs a Public preference."""
        youtube = self.targets["youtube"].isChecked() or bool(self.plan)
        chosen = {
            self.videos.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.videos.count())
            if self.videos.item(i).isSelected()
        }
        self.videos.clear()
        for identity, entry in self.controller.entries.items():
            public = entry.preparation.metadata.privacy == "public"
            ready = entry.reviewed and (public or not youtube)
            taken = youtube and identity in self.reserved
            note = (
                "Already in a YouTube schedule"
                if taken
                else "Ready"
                if ready
                else "Set Visibility to Public in Videos"
                if entry.reviewed
                else "Not ready · finish it in Videos"
            )
            item = QListWidgetItem(
                f"{entry.preparation.metadata.title or entry.video.path.rsplit('/', 1)[-1]} · {note}"
            )
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(entry.video.path)
            if not ready or taken:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.videos.addItem(item)
            item.setSelected(identity in chosen and ready and not taken)

    def describe(self) -> None:
        """Show only the fields this frequency uses, and say the schedule in words."""
        hours = self.frequency.currentData()
        daily = hours == 0
        self.at_label.setText("at" if daily else "from")
        self.until_label.setVisible(not daily)
        self.end_time.setVisible(not daily)
        self.hours.setVisible(hours == -1)
        rule = self.values()["rule"]
        try:
            rule.validate()
            upcoming = rule.next(self.controller.service.clock(), 3)
            self.summary.setText(
                rule_label(rule)
                + (
                    "\nNext: " + " · ".join(friendly(t.isoformat(), rule.zone) for t in upcoming)
                    if upcoming
                    else ""
                )
            )
        except Exception as exc:  # noqa: BLE001 - show the reason instead of a preview
            self.summary.setText(str(exc))

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
        youtube, tiktok = self.targets["youtube"].isChecked(), self.targets["tiktok"].isChecked()
        if not (youtube or tiktok or self.plan):
            self.feedback.setText("Pick at least one platform under Where should they go?")
            return
        if not self.values()["videos"]:
            self.feedback.setText("Pick at least one ready video.")
            return
        if (youtube or self.plan) and not self.approved_youtube:
            self.controller.start("preview", self.values())
        else:
            self.youtube_approved()

    def youtube_approved(self) -> None:
        """YouTube is approved (or not chosen); TikTok posts get their own confirmation."""
        self.approved_youtube = self.targets["youtube"].isChecked() or bool(self.plan)
        if not self.targets["tiktok"].isChecked() or self.plan:
            self.finish()
            return
        values = self.values()
        rule, videos, limit = values["rule"], values["videos"], values["limit"]
        try:
            rule.validate()
            times = posting_times(rule, self.controller.service.clock(), len(videos), limit)
        except Exception as exc:  # noqa: BLE001 - the reason is the useful part
            self.feedback.setText(str(exc))
            return
        entries = self.controller.entries
        plan = list(zip(videos, times, strict=True))
        lines = "\n".join(
            f"{friendly(when.isoformat(), rule.zone)} · {entries[v].preparation.metadata.title}"
            for v, when in plan
        )
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Schedule these TikTok posts?",
            f"Each video is posted PUBLICLY to TikTok through Buffer at its time:\n\n{lines}\n\n"
            + HOSTING_CONSENT,
            QMessageBox.StandardButton.Cancel,
            self,
        )
        go = box.addButton(f"Schedule {len(plan)} TikTok Posts", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            if self.approved_youtube:
                self.feedback.setText("YouTube is scheduled. TikTok posts were not scheduled.")
            return
        controller = self.controller.window.tiktok
        details = {
            v: (
                entries[v].preparation.metadata.title,
                entries[v].preparation.metadata.description,
                tuple(entries[v].preparation.metadata.tags),
            )
            for v in videos
        }

        def job(cancel: Event) -> list[str]:
            problems = []
            for video_id, when in plan:
                draft = controller.service.draft(video_id)
                title = details[video_id][0]
                if draft["ai_generated"] is None:
                    problems.append(f"{title}: choose the AI-generated content label in Details.")
                    continue
                try:
                    controller.service.publish(
                        video_id,
                        caption_from(*details[video_id]),
                        draft["ai_generated"],
                        when,
                        cancel,
                    )
                except Exception as exc:  # noqa: BLE001 - reported per video
                    problems.append(f"{title}: {exc}")
            return problems

        def done(problems: list[str]) -> None:
            controller.reload("TikTok posts scheduled in Buffer.")(None)
            if problems:
                self.feedback.setText(
                    "Some TikTok posts were not scheduled:\n" + "\n".join(problems)
                )
                self.review.setEnabled(True)
            else:
                self.finish()

        self.review.setEnabled(False)
        controller.run(job, done)

    def finish(self) -> None:
        self.dirty = False
        self.preview.release()
        self.accept()

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
        self.queue, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(pixmap("clock", 20, theme.ACCENT_TEXT)))
        heading.addWidget(text("Posting times", "Heading"), 1)
        self.new = icon_button("New Posting Schedule…", "plus", role="Primary")
        self.new.setAccessibleName("New Recurring Posting Queue…")
        self.new.clicked.connect(lambda: self.start("open"))
        heading.addWidget(self.new)
        body.addLayout(heading)
        body.addWidget(
            text(
                "Pick days and times once; ready videos then upload one at a time, in order, at "
                "each posting time.",
                "Muted",
            )
        )
        self.plans = QComboBox()
        self.plans.setMinimumWidth(0)
        self.plans.setAccessibleName("Posting schedule")
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
        self.pause = icon_button("Turn On", "power")
        self.pause.clicked.connect(self.toggle)
        self.append = icon_button("Add Ready Videos…", "plus")
        self.append.setAccessibleName("Approve Additional Reviewed Videos…")
        self.append.clicked.connect(lambda: self.start("append"))
        self.up = icon_button("Move Up", "chevron-left")
        self.up.setAccessibleName("Move Selected Up")
        self.up.clicked.connect(lambda: self.control("up"))
        self.down = icon_button("Move Down", "chevron-right")
        self.down.setAccessibleName("Move Selected Down")
        self.down.clicked.connect(lambda: self.control("down"))
        self.remove = icon_button("Remove…", "trash", role="Danger")
        self.remove.setAccessibleName("Remove Unstarted Item…")
        self.remove.clicked.connect(self.remove_item)
        self.preview_button = icon_button("Open Video", "film")
        self.preview_button.setAccessibleName("Open Selected Video / Preview")
        self.preview_button.clicked.connect(self.open_video)
        self.history = icon_button("Upload History", "history")
        self.history.clicked.connect(window.show_upload_history)
        body.addWidget(
            flow(
                self.pause,
                self.append,
                self.up,
                self.down,
                self.preview_button,
                self.remove,
                self.history,
            )
        )
        self.feedback = text("", "Muted")
        body.addWidget(self.feedback)
        # Approvals and opportunity history, for support; shown as a tooltip on the status.
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.hide()
        self.calendar = Section("Recurring opportunities · not guaranteed publications")
        self.opportunities = QListWidget()
        self.opportunities.setFixedHeight(190)
        self.opportunities.setWordWrap(True)
        self.opportunities.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.opportunities.itemActivated.connect(self.open_slot)
        self.calendar.body.addWidget(self.opportunities)
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
                self.editor.youtube_approved()
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
                "No posting schedules yet. Choose New Posting Schedule to pick your days and times."
            )
            return
        items = [i for i in self.data["items"] if i["plan_id"] == plan["id"]]
        queued = [i for i in items if i["state"] == "Queued"]
        self.status.setText(
            f"{rule_label(Rule.decode(plan['rule']))} · {'On' if plan['enabled'] else 'Paused'}\n"
            f"YouTube · {plan['channel_title']} · {len(queued)} video"
            f"{'s' if len(queued) != 1 else ''} waiting · next posting time "
            f"{friendly(plan['next_slot'], plan['rule']['zone'])}"
            + (
                f"\nUp next: {queued[0]['snapshot']['metadata']['title']}"
                if queued
                else "\nNo ready video is waiting. Choose Add Ready Videos."
            )
        )
        self.pause.setText("Pause" if plan["enabled"] else "Turn On")
        self.pause.setAccessibleName("Pause Plan" if plan["enabled"] else "Activate / Resume Plan")
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
        self.status.setToolTip(f"Approved {timestamp(plan['consent_at'])} · {plan['name']}")

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
