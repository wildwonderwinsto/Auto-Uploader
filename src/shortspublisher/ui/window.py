"""Navigation and honest empty states, with no platform dependencies."""

import logging
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
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

from ..accounts import AccountService, AccountView
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
from .accounts import AccountsPage
from .automation import AutomationController
from .background import BackgroundPage
from .components import Disclosure, primary, tidy_buttons
from .execution import ExecutionController
from .formatting import upload_status
from .recurring import RecurringController
from .scheduling import ScheduleController
from .theme import SPACE
from .tiktok import TikTokController
from .uploads import UploadPanel
from .videos import VideosPage
from .watching import WatchPanel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Page:
    key: str
    title: str
    purpose: str
    heading: str
    detail: str


PAGES = (
    Page(
        "dashboard",
        "Dashboard",
        "What is scheduled, what needs attention, and what was published.",
        "A home for your next release",
        "Review a finished video, authorize its exact publishing plan, and follow its upload here.",
    ),
    Page(
        "videos",
        "Videos",
        "Prepare your short-form videos for publishing.",
        "Your videos will start here",
        "Import, inspect and review a video before explicitly confirming a Private or Public upload.",
    ),
    Page(
        "queue",
        "Publishing Queue",
        "Follow each video from preparation to publication.",
        "A clear path to publication",
        "Publishing queues and automatic posting are not implemented. Upload one reviewed video privately from Videos.",
    ),
    Page(
        "calendar",
        "Calendar",
        "Give every release its own moment.",
        "Plan your publishing rhythm",
        "Calendar planning, time zone controls, and scheduled publication are not implemented yet.",
    ),
    Page(
        "automation",
        "Automatic Posting",
        "Configure approved folder publishing.",
        "Automatic Posting",
        "Explicit standing approval for matching future files.",
    ),
    Page(
        "accounts",
        "Accounts",
        "Manage the channels you publish to.",
        "YouTube comes first",
        "Connect and verify your YouTube channel. Uploads require separate permission and confirmation.",
    ),
    Page(
        "settings",
        "Settings",
        "Make this workspace your own.",
        "A focused foundation",
        "Preferences are not implemented yet. This version uses the ShortsStudio-inspired "
        "dark theme. Account, publishing, and notification settings will follow.",
    ),
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
        self.setWindowTitle("ShortsPublisher")
        self.setMinimumSize(760, 560)
        self.resize(1180, 780)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedWidth(214)
        nav = QVBoxLayout(rail)
        nav.setContentsMargins(16, 28, 16, 20)
        nav.setSpacing(8)
        nav.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        nav.addWidget(label("ShortsPublisher", "Brand", wrap=False))
        nav.addWidget(label("From creation to release", "Muted"))
        nav.addSpacing(28)
        nav.addWidget(label("WORKSPACE", "Eyebrow"))
        self.buttons: dict[str, QPushButton] = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.stack = QStackedWidget()
        self.indices: dict[str, int] = {}
        self.accounts = AccountsPage(accounts)
        self.accounts.idle.connect(self._finish_close)
        self.videos = VideosPage(service)
        self.videos.idle.connect(self._finish_close)
        self.watching = WatchPanel(WatchService(service))
        self.watching.idle.connect(self._finish_close)
        self.watching.library_changed.connect(self.videos.discovery_changed)
        self.videos.content_layout.insertWidget(4, self.watching)
        self.videos.watch_panel = self.watching
        QTimer.singleShot(0, self.watching.activate)
        self.schedules = ScheduleController(
            scheduling or ScheduleService(uploads.store.library), self
        )
        self.schedules.idle.connect(self._finish_close)
        self.schedule_panel = QWidget()
        schedule_layout = QVBoxLayout(self.schedule_panel)
        self.schedule_reason = label(
            "Save Public metadata and complete review, then connect the intended channel."
        )
        schedule_layout.addWidget(self.schedule_reason)
        self.schedule_button = QPushButton("Schedule Video…")
        primary(self.schedule_button)
        self.schedule_button.clicked.connect(self.schedule_video)
        schedule_layout.addWidget(self.schedule_button)
        self.uploads = UploadPanel(uploads)
        self.history_button = QPushButton("Upload History")
        self.history_button.clicked.connect(self.show_upload_history)
        self.videos.action_layout.insertWidget(2, self.history_button)
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
        self.schedules.queue.body.insertWidget(2, self.recurring.queue)
        self.schedules.calendar.body.insertWidget(2, self.recurring.calendar)
        self.videos.recurring_button = QPushButton("Recurring Posting…")
        self.videos.recurring_button.clicked.connect(lambda: self.recurring.start("open"))
        self.videos.action_layout.insertWidget(3, self.videos.recurring_button)
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
        accounts_layout = self.accounts.content_layout
        accounts_layout.insertWidget(
            accounts_layout.indexOf(self.accounts.card) + 1, self.tiktok.card
        )
        settings_layout = self.background.content_layout
        settings_layout.insertWidget(
            settings_layout.indexOf(self.background.advanced) + 1, self.tiktok.hosting
        )
        self.automation.body.insertWidget(self.automation.body.count() - 1, self.tiktok.automation)
        self.schedules.queue.body.insertWidget(3, self.tiktok.queue)
        self.uploads.changed.connect(self.videos.tabs.resize_to_current)
        self.uploads.idle.connect(self._finish_close)
        self.uploads.idle.connect(self.update_upload_context)
        self.accounts.state_changed.connect(self.uploads.account_changed)
        self.accounts.state_changed.connect(self.update_upload_context)
        self.videos.idle.connect(self.update_upload_context)
        self.videos.editor.changed.connect(self.update_upload_context)
        self.videos.tabs.currentChanged.connect(self.update_upload_context)
        for index, page in enumerate(PAGES):
            if page.key == "accounts":
                nav.addSpacing(16)
                nav.addWidget(label("MANAGE", "Eyebrow"))
            button = QPushButton(page.title)
            button.setObjectName("Nav")
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
                else self._page(page)
            )
        nav.addSpacing(16)
        nav.addWidget(self.automatic.state)
        nav.addWidget(self.automatic.button)
        nav.addStretch()
        nav.addWidget(label("SHORTSPUBLISHER", "Eyebrow"))
        nav.addWidget(label("Local library · YouTube", "Muted"))
        navigation = QScrollArea()
        navigation.setFixedWidth(224)
        navigation.setWidgetResizable(True)
        navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        navigation.setWidget(rail)
        layout.addWidget(navigation)
        layout.addWidget(self.stack, 1)
        tidy_buttons(self.stack)
        self.navigate("dashboard")

    def _page(self, page: Page) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName(page.key)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE + 8, SPACE + 12, SPACE + 8, SPACE)
        layout.setSpacing(16)
        layout.addWidget(label(page.title, "Title"))
        layout.addWidget(label(page.purpose, "Muted"))
        if page.key == "dashboard":
            self.channel_summary = label("Channel: not checked. Open Accounts to connect.", "Badge")
            layout.addWidget(self.channel_summary)
            self.accounts.state_changed.connect(self.update_channel_summary)
            card = QFrame()
            card.setObjectName("Card")
            body = QVBoxLayout(card)
            body.setContentsMargins(20, 20, 20, 20)
            body.setSpacing(14)
            body.addWidget(label("Publishing status", "Heading"))
            body.addWidget(self.automatic.summary)
            body.addWidget(self.recurring.summary)
            body.addWidget(self.automation.summary)
            body.addWidget(self.background.dashboard)
            body.addWidget(self.automatic.stop)
            layout.addWidget(card)
            layout.addWidget(label("Latest activity", "Heading"))
            layout.addWidget(self.automatic.notification)
            self.recent_results = label("No upload history loaded.", "Muted")
            layout.addWidget(self.recent_results)
            self.uploads.changed.connect(self.update_recent_results)
            for index, (title, target) in enumerate(
                (
                    ("Prepare a video →", "videos"),
                    ("Automatic Posting", "automation"),
                    ("Publishing Queue", "queue"),
                )
            ):
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, key=target: self.navigate(key))
                if target == "videos":
                    primary(button)
                # Quick actions sit directly under the channel summary, as one row.
                layout.insertWidget(3 + index, button)
            limits = Disclosure("Publishing limits")
            limits.body.addWidget(self.automatic.limit)
            limits.body.addWidget(self.automatic.save_limit)
            layout.addWidget(limits)
        else:
            layout.addWidget(label("Not implemented yet", "Badge"))
            layout.addWidget(label(page.heading, "Heading"))
            layout.addWidget(label(page.detail, "Muted"))
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def update_channel_summary(self, view: AccountView) -> None:
        channel = view.connection.channel if view.connection else None
        self.channel_summary.setText(
            f"{channel.title} · {view.state}\nUpload permission: {'Authorized' if view.upload_allowed else 'Not authorized'}"
            if channel
            else f"YouTube · {view.state}"
        )
        self.channel_summary.setToolTip(channel.id if channel else "Open Accounts to connect")

    def update_recent_results(self) -> None:
        results = self.uploads.attempts[:3]
        self.recent_results.setText(
            "\n".join(
                f"{a.snapshot.get('metadata', {}).get('title', a.snapshot.get('filename', 'Video'))} · {upload_status(a.state, a.processing)}"
                for a in results
            )
            if results
            else "No uploads yet. Prepare a video to begin."
        )

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
        self.buttons[key].setChecked(True)
        self.buttons[key].setFocus(Qt.FocusReason.OtherFocusReason)
        if key == "videos":
            self.videos.activate()
            self.watching.activate()
            if not self.uploads.loaded and not self.uploads.worker:
                self.uploads.start("history")
        elif key in ("queue", "calendar"):
            self.schedules.activate()
            self.recurring.activate()
        elif key == "automation":
            self.automation.activate()
        elif key == "settings":
            self.background.activate()
        elif key == "accounts":
            self.accounts.activate()
        logger.info("Navigated to %s", key)

    def show_upload_history(self) -> None:
        def show() -> None:
            self._navigate("videos")
            self.videos.mode = "history"
            self.videos.sync_navigation()
            self.videos.tabs.setCurrentWidget(self.uploads)

        self.videos.guard(show)

    def update_upload_context(self) -> None:
        video, prep = self.videos.selected, self.videos.editor.preparation
        if self.videos.mode != "editor":
            video, prep = None, None
        for index in range(3):
            self.videos.tabs.setTabEnabled(index, self.videos.selected is not None)
        self.videos.tabs.setTabEnabled(5, self.videos.selected is not None)
        self.tiktok.context(video.id if video else None)
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
            "Choose when the Public upload starts. Review the plan, then activate automatic publishing. YouTube processing can delay availability."
            if eligible
            else "Save Public metadata, complete review and connect the intended channel before scheduling."
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
