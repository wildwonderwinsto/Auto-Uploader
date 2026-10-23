"""Explicit recurring batch approval and read-only opportunity/outcome projections."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from threading import Event
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..cadence import Rule
from ..execution import DAILY_MAX
from ..library_view import LibraryEntry, read_entries
from ..recurring import CONSENT, RecurringService
from ..scheduling import ScheduleError
from ..social import HOSTING_CONSENT, caption_from
from . import theme
from .components import (
    Section,
    card,
    day_row,
    flow,
    icon_button,
    pill,
    platform_choice,
    restyle,
    row,
    set_choice,
    step_page,
    step_pills,
)
from .formatting import (
    file_size,
    friendly,
    handle,
    system_zone,
    time_pattern,
    timestamp,
    upload_status,
)
from .icons import PLATFORM_NAMES, image, pixmap
from .preview import VideoPreview
from .scheduling import text

if TYPE_CHECKING:
    from .window import MainWindow


VIDEO_FILES = "Videos (*.mp4 *.mov *.webm *.mkv *.m4v *.avi);;All files (*)"


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
            f"{len(rule.on_day(day))} opportunities on {day.strftime('%b %d, %Y')} · at most {proposal['approved_limit']} uploads per UTC day (YouTube's own limit).",
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


class PickVideo(QDialog):
    """Choose one library video to upload by itself; it opens on its Upload step."""

    def __init__(self, controller: RecurringController) -> None:
        super().__init__(controller.window)
        self.controller = controller
        self.choice: int | None = None
        self.setWindowTitle("Upload one video")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(14)
        layout.addWidget(text("Upload one video", "Title"))
        layout.addWidget(
            text(
                "Pick a video. It opens on its Upload step, where you choose the platforms and "
                "whether it goes out now or at a time you pick.",
                "Muted",
            )
        )
        self.videos = QListWidget()
        self.videos.setWordWrap(True)
        self.videos.setAccessibleName("Library videos")
        self.videos.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.videos.currentItemChanged.connect(self.preview_video)
        self.videos.itemDoubleClicked.connect(lambda _: self.open())
        picker = QHBoxLayout()
        picker.setSpacing(16)
        picker.addWidget(self.videos, 1)
        self.preview = VideoPreview()
        self.preview.setFixedWidth(210)
        picker.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(picker, 1)
        footer = QHBoxLayout()
        add = icon_button("Add Videos From Your Mac…", "plus")
        add.clicked.connect(self.add)
        cancel = icon_button("Cancel", "x")
        cancel.clicked.connect(self.reject)
        self.go = icon_button("Open Upload Step", "upload", role="Primary")
        self.go.clicked.connect(self.open)
        footer.addWidget(add)
        footer.addStretch()
        footer.addWidget(cancel)
        footer.addWidget(self.go)
        layout.addLayout(footer)
        self.fill()

    def fill(self) -> None:
        self.videos.clear()
        for identity, entry in self.controller.entries.items():
            title = entry.preparation.metadata.title or entry.video.path.rsplit("/", 1)[-1]
            note = "Ready to upload" if entry.reviewed else "Details not finished yet"
            item = QListWidgetItem(f"{title}\n{note}")
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(entry.video.path)
            self.videos.addItem(item)
        if self.videos.count():
            self.videos.setCurrentRow(0)
        else:
            self.videos.addItem(
                QListWidgetItem("Your library is empty. Choose Add Videos From Your Mac.")
            )
        self.go.setEnabled(bool(self.controller.entries))

    def preview_video(
        self, item: QListWidgetItem | None, previous: QListWidgetItem | None = None
    ) -> None:
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        entry = self.controller.entries.get(identity) if identity is not None else None
        self.preview.set_source(entry.video.path if entry else "")

    def add(self) -> None:
        """Import files and open the first one: a new video starts with its details."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Add videos from your Mac", "", VIDEO_FILES)
        if paths:
            self.reject()
            self.controller.window.navigate("videos")
            self.controller.window.videos.start("import_many", paths)

    def open(self) -> None:
        item = self.videos.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        if identity is not None:
            self.choice = identity
            self.accept()


class RecurringEditor(QDialog):
    def __init__(self, controller: RecurringController, plan: dict[str, Any] | None = None):
        super().__init__(controller.window)
        self.controller = controller
        self.plan = plan
        self.dirty = False
        self.approved_youtube = False
        self.setWindowTitle("Add Videos to Queue" if plan else "Choose Videos to Upload")
        self.resize(720, 720)
        # Same step-by-step window as a new auto upload: Videos · Where · When.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        top = QVBoxLayout()
        top.setContentsMargins(24, 22, 24, 10)
        top.setSpacing(10)
        top.addWidget(
            text("Add videos to this queue" if plan else "Choose videos to upload", "Title")
        )
        top.addWidget(
            text(
                "Pick videos once; they go to every platform you tick, one at a time, in order, "
                "at the times you choose. Nothing is scheduled until you approve it.",
                "Muted",
            )
        )
        names = ("Videos", "When") if plan else ("Videos", "Where", "When")
        steps, self.step_buttons = step_pills(names, lambda i: self.go(i))
        top.addLayout(steps)
        layout.addLayout(top)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        account = controller.account
        # The connected YouTube channel is the only choice; it is named under "Where".
        self.channel = QComboBox(self)
        self.channel.hide()
        self.channel.addItem(account.get("title", "No connected channel"), account.get("id", ""))
        self.channel.setToolTip(
            account.get("id", "Connect the intended YouTube channel in Accounts.")
        )
        self.name = QLineEdit(plan["name"] if plan else "My video queue")
        self.name.setMaxLength(100)
        self.name.setPlaceholderText("A name for this queue, only shown to you")
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_label = QLabel("Queue name")
        name_label.setObjectName("Subheading")
        name_row.addWidget(name_label)
        name_row.addWidget(self.name, 1)
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
        # No daily limit of our own: the schedule decides; YouTube's own ceiling still applies.
        self.limit = (
            plan["approved_limit"]
            if plan
            else controller.data.get("control", {}).get("daily_limit", DAILY_MAX)
        )
        accessible = (
            ("Plan name", self.name),
            ("YouTube channel", self.channel),
            ("Frequency", self.frequency),
            ("Custom interval (hours)", self.hours),
            ("Posting days", self.days_preset),
            ("Start time / daily time", self.start_time),
            ("Window end (included)", self.end_time),
            ("IANA time zone", self.zone),
        )
        for label, widget in accessible:
            widget.setAccessibleName(label)

        when, when_body = step_page(
            "When should they go out?",
            "One video goes out at each of these times, on every platform you ticked.",
        )
        when_body.addLayout(name_row)
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
        self.background = QCheckBox("Keep uploading while ShortsScheduler is closed")
        self.background.setToolTip("Needs Background Uploading turned on in Settings")
        when_body.addWidget(self.background)

        # Where: every ready platform, each with its own way of holding the posts.
        choices: list[tuple[str, bool, str]] = [
            (
                "youtube",
                bool(account.get("id")),
                account.get("title") or "Connect YouTube in Accounts first",
            )
        ]
        for social in controller.window.socials:
            connection = social.data.get("connection") or {}
            ready = connection.get("status") == "Connected" and bool(social.data.get("hosting"))
            choices.append(
                (
                    social.key,
                    ready,
                    f"{handle(social.key, connection)} · each post is scheduled in Buffer"
                    if ready
                    else f"Connect {social.name} and set up video hosting first",
                )
            )
        where, where_body = step_page(
            "Where should they go?",
            "Every ready platform is ticked. Each one is approved on its own screen next.",
        )
        self.targets: dict[str, QCheckBox] = {}
        for platform, ready, note in choices:
            frame, check, detail, badge, _line = platform_choice(platform)
            set_choice(check, detail, badge, ready, note)
            check.setEnabled(ready and not plan)
            check.setChecked(ready)  # enter it once, upload it everywhere
            check.setAccessibleName(f"Post to {PLATFORM_NAMES[platform]}")
            check.toggled.connect(self.fill_videos)
            where_body.addWidget(frame)
            self.targets[platform] = check
        where_body.addStretch()

        which, which_body = step_page(
            "Which videos?",
            "Tick every video to add; they go out in the order shown. Videos can be ticked once "
            "they are marked ready in Videos (for YouTube, Visibility must also be Public). Click "
            "a video to preview it.",
        )
        self.chosen = pill("None chosen", "Neutral")
        self.videos = QListWidget()
        self.videos.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.videos.setMinimumHeight(330)
        self.videos.setWordWrap(True)
        self.videos.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.videos.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.videos.setAccessibleName("Select reviewed videos for recurring queue")
        self.reserved = {
            i["video_id"]
            for i in controller.data.get("items", [])
            if i["state"] != "Removed" and i["channel_id"] == account.get("id")
        }
        self.videos.itemChanged.connect(self.ticked)
        self.videos.itemChanged.connect(self.edited)
        picker = QHBoxLayout()
        picker.setSpacing(16)
        picker.addWidget(self.videos, 1)
        self.preview = VideoPreview()
        self.preview.setFixedWidth(210)
        picker.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        which_body.addLayout(picker)
        self.add_files = icon_button("Add Videos From Your Mac…", "plus")
        self.add_files.clicked.connect(self.import_videos)
        self.tick_all = icon_button("Tick All Ready", "check", role="Ghost")
        self.tick_all.clicked.connect(lambda: self.tick(True))
        self.clear_ticks = icon_button("Clear", "x", role="Ghost")
        self.clear_ticks.clicked.connect(lambda: self.tick(False))
        which_body.addWidget(row(self.add_files, self.tick_all, self.clear_ticks, self.chosen))
        self.videos.currentItemChanged.connect(self.preview_video)
        self.fill_videos()
        if plan:
            rule = Rule.decode(plan["rule"])
            index = self.frequency.findData(rule.hours)
            self.frequency.setCurrentIndex(index if index >= 0 else self.frequency.findData(-1))
            self.hours.setValue(rule.hours or 1)
            self.start_time.setTime(QTime.fromString(rule.start, "HH:mm"))
            self.end_time.setTime(QTime.fromString(rule.end, "HH:mm"))
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
                self.background,
                *self.days,
            ):
                disabled_widget.setEnabled(False)
            when_body.addWidget(
                text(
                    "An approved schedule's times can't change. To use different times, create a new upload queue.",
                    "Subtle",
                )
            )
        when_body.addStretch()  # fields sit at the top, like every other step
        for page_widget in (which, *(() if plan else (where,)), when):
            self.pages.addWidget(page_widget)
        self.feedback = text("", "Muted")
        self.feedback.setContentsMargins(24, 6, 24, 0)
        layout.addWidget(self.feedback)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 10, 24, 18)
        cancel = icon_button("Cancel", "x")
        cancel.clicked.connect(self.reject)
        self.back = icon_button("Back", "chevron-left")
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next = icon_button("Next", "chevron-right", role="Primary")
        self.next.clicked.connect(lambda: self.go(self.pages.currentIndex() + 1))
        self.review = icon_button("Review and Approve…", "check", role="Primary")
        self.review.setAccessibleName("Review and Approve Queue…")
        self.review.clicked.connect(self.review_batch)
        footer.addWidget(cancel)
        footer.addStretch()
        for button in (self.back, self.next, self.review):
            footer.addWidget(button)
        layout.addLayout(footer)
        self.go(0)
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
        self.hours.valueChanged.connect(self.edited)
        for check_field in (*self.days, self.background):
            check_field.toggled.connect(self.edited)

    def edited(self, *_: Any) -> None:
        self.dirty = True

    def go(self, index: int) -> None:
        index = max(0, min(index, self.pages.count() - 1))
        self.pages.setCurrentIndex(index)
        for position, step in enumerate(self.step_buttons):
            step.setChecked(position == index)
        last = index == self.pages.count() - 1
        self.back.setEnabled(index > 0)
        self.next.setVisible(not last)
        self.review.setVisible(last)

    def checked(self) -> list[int]:
        return [
            self.videos.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.videos.count())
            if self.videos.item(i).checkState() == Qt.CheckState.Checked
        ]

    def ticked(self, *_: Any) -> None:
        count = len(self.checked())
        self.chosen.setText(f"{count} chosen" if count else "None chosen")
        restyle(self.chosen, "Good" if count else "Neutral")

    def tick(self, on: bool) -> None:
        self.videos.blockSignals(True)
        for i in range(self.videos.count()):
            item = self.videos.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.videos.blockSignals(False)
        self.ticked()
        self.edited()

    def import_videos(self) -> None:
        """Add files to the library from here; they can be ticked once they're ready."""
        paths, _ = QFileDialog.getOpenFileNames(self, "Add videos from your Mac", "", VIDEO_FILES)
        if not paths:
            return
        videos = self.controller.window.videos

        def imported() -> None:
            videos.idle.disconnect(imported)
            self.controller.start("load")  # refills this list

        videos.idle.connect(imported)
        videos.start("import_many", paths)
        self.feedback.setText(
            f"Adding {len(paths)} video{'s' if len(paths) != 1 else ''} to your library. Write "
            "their details and mark them ready in Videos, then tick them here."
        )

    def fill_videos(self) -> None:
        """Ready means reviewed; YouTube also needs a Public preference."""
        youtube = self.targets["youtube"].isChecked() or bool(self.plan)
        chosen = set(self.checked())
        current = self.videos.currentItem()
        shown = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.videos.blockSignals(True)
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
                f"{entry.preparation.metadata.title or entry.video.path.rsplit('/', 1)[-1]}\n{note}"
            )
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(entry.video.path)
            if ready and not taken:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if identity in chosen else Qt.CheckState.Unchecked
                )
            else:
                # Still clickable to preview; it just can't be ticked.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setForeground(QColor(theme.SUBTLE))
            self.videos.addItem(item)
            if identity == shown:
                self.videos.setCurrentItem(item)
        if self.videos.count() and not self.videos.currentItem():
            self.videos.setCurrentRow(0)  # something to preview straight away
        self.videos.blockSignals(False)
        if not self.videos.count():
            self.videos.addItem(
                QListWidgetItem("Your library is empty. Choose Add Videos From Your Mac below.")
            )
        if (
            self.videos.currentItem()
            and self.videos.currentItem().data(Qt.ItemDataRole.UserRole) != shown
        ):
            self.preview_video(self.videos.currentItem(), None)
        self.ticked()

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
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        entry = self.controller.entries.get(identity) if identity is not None else None
        self.preview.set_source(entry.video.path if entry else "")

    def values(self) -> dict[str, Any]:
        hours = self.frequency.currentData()
        return {
            "videos": self.checked(),
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
            "limit": self.limit,
            "plan_id": self.plan["id"] if self.plan else "",
        }

    def review_batch(self) -> None:
        youtube = self.targets["youtube"].isChecked()
        if not (any(check.isChecked() for check in self.targets.values()) or self.plan):
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
        """YouTube is approved (or not chosen); each Buffer platform's posts get their own
        confirmation, in turn."""
        self.approved_youtube = self.targets["youtube"].isChecked() or bool(self.plan)
        self.buffer_queue = (
            []
            if self.plan
            else [s for s in self.controller.window.socials if self.targets[s.key].isChecked()]
        )
        self.buffer_skipped: list[str] = []
        self.schedule_buffer()

    def schedule_buffer(self) -> None:
        if not self.buffer_queue:
            if self.buffer_skipped:
                scheduled = ["YouTube"] if self.approved_youtube else []
                self.feedback.setText(
                    (f"{' and '.join(scheduled)} is scheduled. " if scheduled else "")
                    + f"{' and '.join(self.buffer_skipped)} posts were not scheduled."
                )
                self.review.setEnabled(True)
                return
            self.finish()
            return
        social = self.buffer_queue.pop(0)
        values = self.values()
        rule, videos, limit = values["rule"], values["videos"], values["limit"]
        try:
            rule.validate()
            times = posting_times(
                rule,
                self.controller.service.clock(),
                len(videos),
                min(limit, social.spec.daily_max),
            )
        except Exception as exc:  # noqa: BLE001 - the reason is the useful part
            self.feedback.setText(str(exc))
            return
        entries = self.controller.entries
        plan = list(zip(videos, times, strict=True))
        lines = "\n".join(
            f"{friendly(when.isoformat(), rule.zone)} · {entries[v].preparation.metadata.title}"
            for v, when in plan
        )
        account = handle(social.key, social.data.get("connection"))
        box = QMessageBox(
            QMessageBox.Icon.Question,
            f"Schedule these {social.name} posts?",
            f"Each video is posted PUBLICLY to {social.name} {account} through Buffer at its "
            f"time:\n\n{lines}\n\n" + HOSTING_CONSENT,
            QMessageBox.StandardButton.Cancel,
            self,
        )
        go = box.addButton(
            f"Schedule {len(plan)} {social.name} Posts", QMessageBox.ButtonRole.AcceptRole
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            self.buffer_skipped.append(social.name)
            self.schedule_buffer()
            return
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
                title = details[video_id][0]
                ai = (
                    social.service.draft(video_id)["ai_generated"]
                    if social.spec.ai_label
                    else False
                )
                if ai is None:
                    problems.append(f"{title}: choose the AI-generated content label in Details.")
                    continue
                try:
                    social.service.publish(
                        video_id,
                        caption_from(*details[video_id], hashtags=social.spec.hashtags_max),
                        ai,
                        when,
                        cancel,
                    )
                except Exception as exc:  # noqa: BLE001 - reported per video
                    problems.append(f"{title}: {exc}")
            return problems

        def done(problems: list[str]) -> None:
            social.reload(f"{social.name} posts scheduled in Buffer.")(None)
            if problems:
                self.feedback.setText(
                    f"Some {social.name} posts were not scheduled:\n" + "\n".join(problems)
                )
                self.review.setEnabled(True)
            else:
                self.schedule_buffer()

        self.review.setEnabled(False)
        social.run(job, done)

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
        self.queued: tuple[str, Any] | None = None  # a click that arrived during a refresh
        self.summary = text("Recurring posting: no plan loaded.", "Muted")
        self.queue, body = card(20, 12)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(image(pixmap("clock", 20, theme.ACCENT_TEXT)))
        heading.addWidget(text("Video queues", "Heading"), 1)
        # Placed by the window on the Auto Upload page, in the "Individual videos" heading.
        self.new = icon_button("Choose Videos…", "plus", role="Primary")
        self.new.setAccessibleName("New Recurring Posting Queue…")
        self.new.clicked.connect(lambda: self.start("open"))
        # One video, uploaded now or at one time: opens it on its Upload step.
        self.single = icon_button("Upload One Video…", "upload")
        self.single.clicked.connect(self.pick_one)
        body.addLayout(heading)
        body.addWidget(
            text(
                "Pick days and times once; the videos you add then upload one at a time, in "
                "order, at each posting time.",
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
        if self.closing:
            return
        if self.worker:
            if action != "load":
                self.queued = (action, argument)  # runs as soon as the current work ends
            return
        if action == "append" and not self.current():
            return
        self.pending = {}
        self.worker = RecurringWorker(self, action, argument)
        self.worker.result.connect(self.receive)
        self.worker.finished.connect(self.finished)
        if action != "load":
            # Regular refreshes leave the buttons alone, so nothing flickers.
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
        if "data" in result and (
            result["data"] != self.data
            or result["entries"] != self.entries
            or result["account"] != self.account
        ):
            self.entries = result["entries"]
            self.account = result["account"]
            self.render(result["data"])
            if self.editor:
                self.editor.fill_videos()  # e.g. after adding videos from the editor
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
        if self.queued and not self.worker:
            action, argument = self.queued
            self.queued = None
            self.start(action, argument)

    def changed(self, data: dict[str, Any]) -> None:
        """The dispatcher's copy of the queues, every few seconds: redrawn only when it differs."""
        if data != self.data:
            self.render(data)

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
                "Nothing queued yet. Use Choose Videos above to pick the days and times, then the "
                "videos."
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

    def pick_one(self) -> None:
        dialog = PickVideo(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.choice is not None:
            self.window.navigate("videos")
            self.window.videos.open_upload(dialog.choice)
        dialog.preview.release()
        dialog.deleteLater()

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
