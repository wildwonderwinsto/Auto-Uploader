"""Navigation and honest empty states, with no platform dependencies."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..accounts import AccountService
from ..automation import AutomationService
from ..background import LaunchAgent
from ..credentials import KeychainSecrets
from ..execution import AutomaticPublisher
from ..library import LibraryService
from ..recurring import RecurringService
from ..scheduling import ScheduleService
from ..tiktok import TikTokService
from ..uploads import UploadService
from ..watching import WatchService
from . import icons, theme
from .accounts import AccountsPage, coming_soon
from .automation import AutomationController
from .autoupload import AutoUploadEditor
from .background import BackgroundPage
from .components import card, divider, icon_button, row, tidy_buttons
from .dashboard import DashboardPage
from .execution import ExecutionController
from .recurring import RecurringController
from .scheduling import ScheduleController
from .tiktok import TikTokController
from .uploads import UploadPanel
from .videos import VideosPage
from .watching import WatchPanel

logger = logging.getLogger(__name__)

APP_NAME = "ShortsScheduler"


@dataclass(frozen=True)
class Page:
    key: str
    title: str
    glyph: str
    purpose: str
    nav: str = ""  # the sidebar entry that shows this page, when it has none of its own


PAGES = (
    Page("dashboard", "Dashboard", "dashboard", "Upcoming and recent uploads on every platform"),
    Page("videos", "Videos", "videos", "Add videos, write their details and upload them"),
    Page("queue", "Schedule", "calendar", "Everything waiting to upload, and when"),
    Page("calendar", "Calendar", "calendar", "Your uploads by day", nav="queue"),
    Page("automation", "Auto Upload", "auto", "Upload new videos from a folder automatically"),
    Page("accounts", "Accounts", "accounts", "Connect the platforms you upload to"),
    Page("settings", "Settings", "settings", "Background uploading, limits and connections"),
)


def label(text: str, style: str = "", *, wrap: bool = True) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(style)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: LibraryService,
        accounts: AccountService,
        uploads: UploadService,
        scheduling: ScheduleService | None = None,
        background: LaunchAgent | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(760, 560)
        self.resize(1180, 780)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedWidth(220)
        nav = QVBoxLayout(rail)
        nav.setContentsMargins(14, 22, 14, 16)
        nav.setSpacing(4)
        nav.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        brand.addWidget(icons.image(icons.mark(30)))
        brand.addWidget(label(APP_NAME, "Brand", wrap=False), 1)
        nav.addLayout(brand)
        nav.addSpacing(22)
        self.buttons: dict[str, QPushButton] = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Pages")
        self.indices: dict[str, int] = {}
        self.accounts = AccountsPage(accounts)
        self.accounts.idle.connect(self._finish_close)
        self.videos = VideosPage(service)
        self.videos.idle.connect(self._finish_close)
        self.watching = WatchPanel(WatchService(service))
        self.watching.idle.connect(self._finish_close)
        self.watching.library_changed.connect(self.videos.discovery_changed)
        QTimer.singleShot(0, self.watching.activate)
        self.schedules = ScheduleController(
            scheduling or ScheduleService(uploads.store.library), self
        )
        self.schedules.idle.connect(self._finish_close)
        self.schedule_panel = QWidget()
        schedule_layout = QVBoxLayout(self.schedule_panel)
        schedule_layout.setContentsMargins(0, 4, 0, 4)
        schedule_layout.setSpacing(12)
        schedule_layout.addWidget(label("Schedule a YouTube upload", "Heading"))
        self.schedule_reason = label(
            "Save Public metadata and complete review, then connect the intended channel.", "Muted"
        )
        schedule_layout.addWidget(self.schedule_reason)
        self.schedule_button = icon_button("Pick a Time…", "calendar", role="Primary")
        self.schedule_button.clicked.connect(self.schedule_video)
        schedule_layout.addWidget(self.schedule_button, 0, Qt.AlignmentFlag.AlignLeft)
        schedule_layout.addStretch()
        self.uploads = UploadPanel(uploads)
        self.history_button = icon_button("Upload History", "history")
        self.history_button.clicked.connect(self.show_upload_history)
        self.videos.action_layout.insertWidget(0, self.history_button)
        self.videos.history_button = self.history_button
        self.automatic = ExecutionController(
            AutomaticPublisher(self.schedules.service, uploads), self
        )
        self.automatic.idle.connect(self._finish_close)
        self.recurring = RecurringController(RecurringService(self.schedules.service), self)
        self.recurring.idle.connect(self._finish_close)
        self.automation = AutomationController(AutomationService(self.schedules.service), self)
        self.automation.idle.connect(self._finish_close)
        self.automatic.recurring_updated.connect(self.recurring.render)
        self.schedules.queue.recurring_slot.addWidget(self.recurring.queue)
        self.background = BackgroundPage(background or LaunchAgent(uploads.store.library), self)
        self.background.idle.connect(self._finish_close)
        self.background.status_changed.connect(self.automation.render)
        self.videos.tabs.addTab(self.uploads, "Publish")
        self.videos.tabs.addTab(self.schedule_panel, "Schedule")
        # TikTok via Buffer: its own credentials namespace, consent and records.
        self.tiktok = TikTokController(
            TikTokService(uploads.store.library, KeychainSecrets("ShortsPublisher.TikTok")), self
        )
        self.tiktok.idle.connect(self._finish_close)
        self.videos.tabs.addTab(self.tiktok.video_panel, "TikTok")
        # One set of details: TikTok's only own setting sits with them, and its caption follows them.
        self.videos.editor.tiktok_slot.insertWidget(0, self.tiktok.ai_field)
        self.videos.editor.changed.connect(
            lambda: self.tiktok.follow_details(self.videos.editor.value())
        )
        self.tiktok.changed.connect(self.mark_tiktok_posts)
        self.videos.when_changed = self.tiktok.set_when
        self.tiktok.set_when("now")
        self.accounts.grid.add(self.tiktok.card)
        for platform in ("instagram", "facebook", "snapchat"):
            self.accounts.soon.add(coming_soon(platform))
        self.accounts.setup_requested.connect(lambda: self.navigate("settings"))
        self.accounts.hold_refresh = self.upload_due_soon
        self.uploads.fix_button.clicked.connect(lambda: self.navigate("accounts"))
        # Settings: one card per topic, each with a plain explanation.
        connections, connections_body = card(20, 14)
        connections_body.addWidget(label("Connections · one-time setup", "Heading"))
        connections_body.addWidget(
            label(
                "Developer details each platform needs before you can connect it in Accounts. "
                "You only do this once.",
                "Muted",
            )
        )
        connections_body.addWidget(self.accounts.setup_guide)
        connections_body.addWidget(divider())
        connections_body.addWidget(self.tiktok.client_setup)
        connections_body.addWidget(divider())
        connections_body.addWidget(self.tiktok.insights_setup)
        self.background.connections_slot.addWidget(connections)
        self.background.hosting_slot.addWidget(self.tiktok.hosting)
        self.automation.tiktok_slot.addWidget(self.tiktok.automation)
        self.automation.folders_slot.addWidget(self.watching)
        self.automation.editor_factory = lambda controller, profile: AutoUploadEditor(
            controller, self.tiktok, profile
        )
        self.schedules.queue.tiktok_slot.addWidget(self.tiktok.queue)
        self.uploads.changed.connect(self.videos.tabs.resize_to_current)
        self.uploads.idle.connect(self._finish_close)
        self.uploads.idle.connect(self.update_upload_context)
        self.accounts.state_changed.connect(self.uploads.account_changed)
        self.accounts.state_changed.connect(self.update_upload_context)
        self.videos.idle.connect(self.update_upload_context)
        self.videos.editor.changed.connect(self.update_upload_context)
        self.videos.tabs.currentChanged.connect(self.update_upload_context)
        self.dashboard = DashboardPage(self)
        for schedule_page in (self.schedules.queue, self.schedules.calendar):
            self.dashboard.entries_changed.connect(schedule_page.render_agenda)
        self.accounts.state_changed.connect(lambda _: self.dashboard.refresh())
        self.accounts.idle.connect(self.dashboard.schedule)  # the first check has finished
        for signal in (
            self.uploads.changed,
            self.automatic.changed,
            self.automatic.recurring_updated,
            self.tiktok.changed,
        ):
            signal.connect(self.dashboard.schedule)
        limits, limits_body = card(20, 10)
        limits_body.addWidget(label("Daily upload limit", "Heading"))
        limits_body.addWidget(
            label(
                "The most scheduled and automatic uploads ShortsScheduler starts in one day, "
                "across all your schedules. Uploads you start by hand don't count.",
                "Muted",
            )
        )
        self.automatic.limit.setPrefix("")
        self.automatic.limit.setSuffix(" per day")
        self.automatic.save_limit.setText("Save")
        limits_body.addWidget(row(self.automatic.limit, self.automatic.save_limit))
        self.background.limits_slot.addWidget(limits)
        for page in PAGES:
            self.indices[page.key] = self.stack.addWidget(
                self.videos
                if page.key == "videos"
                else self.automation.page
                if page.key == "automation"
                else self.accounts
                if page.key == "accounts"
                else self.schedules.queue
                if page.key == "queue"
                else self.schedules.calendar
                if page.key == "calendar"
                else self.background
                if page.key == "settings"
                else self.dashboard
            )
        for index, page in enumerate(p for p in PAGES if not p.nav):
            if page.key == "accounts":
                nav.addSpacing(18)
                manage = label("MANAGE", "Eyebrow")
                manage.setContentsMargins(12, 0, 0, 4)
                nav.addWidget(manage)
            button = QPushButton(page.title)
            button.setObjectName("Nav")
            button.setIcon(icons.icon(page.glyph, theme.MUTED, theme.ACCENT_TEXT))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(page.title)
            button.setToolTip(f"{page.purpose} (Ctrl+{index + 1})")
            button.clicked.connect(lambda checked=False, key=page.key: self.navigate(key))
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda key=page.key: self.navigate(key))
            self.group.addButton(button)
            self.buttons[page.key] = button
            nav.addWidget(button)
        nav.addStretch()
        status, status_layout = card(14, 8, "RailStatus")
        status_layout.addWidget(self.automatic.state)
        status_layout.addWidget(self.automatic.button)
        nav.addWidget(status)
        navigation = QScrollArea()
        navigation.setFixedWidth(220)
        navigation.setWidgetResizable(True)
        navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        navigation.setWidget(rail)
        layout.addWidget(navigation)
        layout.addWidget(self.stack, 1)
        tidy_buttons(self.stack)
        self.adopt_unplaced()
        self.navigate("dashboard")

    def upload_due_soon(self) -> bool:
        """An upload needs the account lock now or within two minutes."""
        if self.uploads.worker is not None:
            return True
        soon = datetime.now(UTC) + timedelta(minutes=2)
        jobs = any(
            job.execution_enabled
            and job.state == "Scheduled"
            and datetime.fromisoformat(job.scheduled_utc) <= soon
            for job in self.automatic.data.get("jobs", [])
        )
        # Posting times and auto uploads start their uploads at each plan's next slot.
        plans = any(
            plan["enabled"] and datetime.fromisoformat(plan["next_slot"]) <= soon
            for plan in (self.recurring.data or {}).get("plans", [])
        )
        return jobs or plans

    def mark_tiktok_posts(self) -> None:
        posted = {
            p["video_id"]
            for p in self.tiktok.data.get("posts", [])
            if p.get("state") == "Sent by Buffer" and p.get("video_id") is not None
        }
        hosting = self.tiktok.data.get("hosting") or {}
        self.videos.tiktok_limit = hosting.get("max_bytes") or self.videos.tiktok_limit
        if posted != self.videos.tiktok_posted:
            self.videos.tiktok_posted = posted
            self.videos.render_library()

    def adopt_unplaced(self) -> None:
        """Widgets kept for their state but never shown still need a Qt parent.

        Without one, Python owns them, and its cycle collector can free them on a worker thread;
        Qt objects destroyed off the GUI thread crash the app."""
        self.unplaced = QWidget(self)
        self.unplaced.hide()
        owners = (
            self,
            self.videos,
            self.videos.editor,
            self.accounts,
            self.uploads,
            self.schedules,
            self.schedules.queue,
            self.schedules.calendar,
            self.automatic,
            self.recurring,
            self.automation,
            self.background,
            self.watching,
            self.tiktok,
            self.dashboard,
        )
        for owner in owners:
            for value in list(vars(owner).values()):
                if (
                    isinstance(value, QWidget)
                    and value.parentWidget() is None
                    and not isinstance(value, (QDialog, QMainWindow))
                ):
                    value.setParent(self.unplaced)

    @property
    def channel_summary(self) -> QLabel:
        return self.dashboard.channel_summary

    def navigate(self, key: str) -> None:
        if key not in self.indices:
            raise ValueError(f"Unknown page: {key}")
        current = self.stack.currentWidget()
        if current is self.videos and key != "videos" and self.videos.editor.dirty:
            self.buttons["videos"].setChecked(True)
            self.videos.guard(lambda: self._navigate(key))
            return
        self._navigate(key)

    def _navigate(self, key: str) -> None:
        if key != "videos":
            self.videos.preview.release()
        self.stack.setCurrentIndex(self.indices[key])
        button = self.buttons[next(p.nav or p.key for p in PAGES if p.key == key)]
        button.setChecked(True)
        button.setFocus(Qt.FocusReason.OtherFocusReason)
        if key == "videos":
            self.videos.activate()
            self.watching.activate()
            if not self.uploads.loaded and not self.uploads.worker:
                self.uploads.start("history")
        elif key in ("queue", "calendar"):
            self.schedules.activate()
            self.recurring.activate()
            self.dashboard.refresh()
        elif key == "automation":
            self.automation.activate()
            self.watching.activate()
        elif key == "settings":
            self.background.activate()
        elif key == "accounts":
            self.accounts.activate()
        elif key == "dashboard":
            self.dashboard.activate()
        logger.info("Navigated to %s", key)

    def show_upload_history(self) -> None:
        def show() -> None:
            self._navigate("videos")
            self.videos.mode = "history"
            self.videos.sync_navigation()
            self.videos.tabs.setCurrentWidget(self.uploads)
            self.update_upload_context()

        self.videos.guard(show)

    def update_upload_context(self) -> None:
        video, prep = self.videos.selected, self.videos.editor.preparation
        if self.videos.mode != "editor":
            video, prep = None, None
        for index in range(3):
            self.videos.tabs.setTabEnabled(index, self.videos.selected is not None)
        self.videos.tabs.setTabEnabled(5, self.videos.selected is not None)
        self.uploads.set_history_mode(self.videos.mode == "history")
        self.tiktok.context(video.id if video else None)
        if prep:
            self.tiktok.follow_details(self.videos.editor.value())
        self.videos.sync_navigation()
        self.uploads.context(
            video.id if video else None,
            prep.revision if prep else 0,
            self.videos.review_state.text().startswith("Review Complete")
            and not self.videos.editor.dirty
            and self.videos.worker is None,
            prep.metadata.privacy if prep else "",
        )

        eligible = (
            video is not None
            and prep is not None
            and prep.metadata.privacy == "public"
            and self.videos.review_state.text().startswith("Review Complete")
            and not self.videos.editor.dirty
            and not self.videos.worker
            and self.accounts.view is not None
            and self.accounts.view.connection is not None
        )
        self.schedule_button.setEnabled(eligible and self.schedules.worker is None)
        self.schedule_reason.setText(
            "Pick the day and time the public upload starts. You'll see the exact plan and approve it before anything is scheduled."
            if eligible
            else "To schedule: set Visibility to Public in Details, mark the video as ready in Review, and connect YouTube in Accounts."
        )

    def schedule_video(self) -> None:
        video = self.videos.selected
        connection = self.accounts.view.connection if self.accounts.view else None
        if video and connection and self.schedule_button.isEnabled():
            self.schedules.open_editor(video.id, connection.channel.id)

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            self.videos.request_close()
            and self.accounts.request_close()
            and self.uploads.request_close()
            and self.schedules.request_close()
            and self.automatic.request_close()
            and self.recurring.request_close()
            and self.automation.request_close()
            and self.background.request_close()
            and self.watching.request_close()
            and self.tiktok.request_close()
        ):
            event.accept()
        else:
            event.ignore()

    def _finish_close(self) -> None:
        if (
            self.videos.closing
            or self.accounts.closing
            or self.uploads.closing
            or self.schedules.closing
            or self.automatic.closing
            or self.recurring.closing
            or self.automation.closing
            or self.background.closing
            or self.watching.closing
            or self.tiktok.closing
        ):
            self.close()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QDialog)
            and not watched.property("tidied")
        ):
            watched.setProperty("tidied", True)
            tidy_buttons(watched)
            frame = watched.layout()
            if frame is not None and 0 < frame.contentsMargins().left() < 16:
                # Every dialog gets the same breathing room as the pages.
                frame.setContentsMargins(24, 22, 24, 18)
                frame.setSpacing(max(frame.spacing(), 12))
        # Application-level Quit must honor the same unsaved-data guard as window closure.
        if event.type() == QEvent.Type.Quit and self.isVisible():
            if (
                not self.videos.request_close()
                or not self.accounts.request_close()
                or not self.uploads.request_close()
                or not self.schedules.request_close()
                or not self.automatic.request_close()
                or not self.recurring.request_close()
                or not self.automation.request_close()
                or not self.background.request_close()
                or not self.watching.request_close()
                or not self.tiktok.request_close()
            ):
                event.ignore()
                return True
        return super().eventFilter(watched, event)
