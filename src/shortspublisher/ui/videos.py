"""Local library presentation and one cancellable worker; no SQL or subprocesses here."""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..library import LibraryService, Video
from ..library_view import LibraryEntry, browse, read_entries
from ..media import Cancelled
from ..preparation import review_checks
from ..readiness import LIMITATIONS, evaluate
from ..social import PLATFORMS, media_errors, readable
from . import theme
from .components import (
    Elided,
    FlowLayout,
    Section,
    card,
    flow,
    icon_button,
    pill,
    restyle,
    row,
)
from .formatting import duration, file_size, friendly, timestamp
from .icons import PLATFORM_NAMES, icon, image, logo, logo_icon, pixmap
from .metadata import MetadataEditor, PreparationTabs
from .preview import VideoPreview

logger = logging.getLogger(__name__)

# Editor pages for each Buffer platform; 3 and 4 are YouTube's (now / scheduled).
BUFFER_PAGES = {"tiktok": 5, "instagram": 6, "facebook": 7}
ALL_PAGE = 8  # the Upload step itself: every platform at once (added by the window)
UPLOAD_PAGES = (3, 4, *BUFFER_PAGES.values(), ALL_PAGE)


def declaration(value: bool | None) -> str:
    return "Not specified" if value is None else "Yes" if value else "No"


class LibraryWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(
        self, service: LibraryService, action: str, argument: Any, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.service, self.action, self.argument = service, action, argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            if self.action == "browse":
                entries = read_entries(self.service.library, self.cancel)
                if not self.cancel.is_set():
                    self.result.emit({"action": "browse", "entries": entries})
                return
            if self.action == "import_many":
                imported = []
                errors = []
                for path in self.argument:
                    try:
                        item = self.service.execute("import", path, self.cancel)
                        imported.append(item["selected"])
                    except Cancelled:
                        raise
                    except Exception as exc:
                        errors.append(f"{Path(path).name}: {exc}")
                result = self.service.execute(
                    "select" if imported else "load",
                    imported[0].id if imported else None,
                    self.cancel,
                )
                result["action"] = "import_many"
                result["message"] = (
                    f"{len(imported)} videos imported and saved to your library. "
                    + (
                        "Give each one a title, then save and review."
                        if imported
                        else "No videos imported."
                    )
                )
                result["import_errors"] = errors
            elif self.action == "select" and not any(
                v.id == self.argument for v in self.service.library.all()
            ):
                result = self.service.execute("load", None, self.cancel)
                result["message"] = (
                    "This video was removed. Choose another video or import it again."
                )
            else:
                result = self.service.execute(self.action, self.argument, self.cancel)
            if self.action == "load" and self.argument is not None:
                if any(v.id == self.argument for v in result["videos"]):
                    result = self.service.execute("select", self.argument, self.cancel)
                    result["action"] = "load"
            result["entries"] = read_entries(self.service.library, self.cancel)
            if not self.cancel.is_set():
                self.result.emit(result)
        except Cancelled:
            pass
        except Exception as exc:
            # Worker boundary: unexpected failures must also reach the UI, never kill the thread.
            logger.exception("Library operation failed")
            if not self.cancel.is_set():
                self.failed.emit(str(exc))


# Display wording only; readiness statuses stay unchanged for storage and logic.
READINESS_LABELS = {"Passes implemented checks": "Ready for YouTube Shorts · local checks passed"}
REVIEW_TONES = {"Review Complete": "Good", "Needs Attention": "Bad", "Draft": "Neutral"}


def text(value: str, style: str = "") -> QLabel:
    result = QLabel(value)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setObjectName(style)
    result.setMinimumWidth(0)
    result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return result


class VideosPage(QScrollArea):
    idle = Signal()

    def __init__(self, service: LibraryService) -> None:
        super().__init__()
        self.service = service
        self.worker: LibraryWorker | None = None
        self.closing = False
        self.loaded = False
        self.pending_discovery = False
        self.watch_panel: QWidget | None = None
        self.recurring_button: QPushButton | None = None
        self.history_button: QPushButton | None = None
        self.mode = "library"
        self.editing_step = 1
        self.context_revision = 0
        self.operation_context = 0
        self.after_save: Callable[[], object] | None = None
        # Open this video on its Upload step once selected; False until the select is asked for.
        self.upload_next: tuple[int, bool] | None = None
        self.save_succeeded = False
        self.availability = ""
        self.selected: Video | None = None
        self.records: dict[int, Video] = {}
        self.entries: dict[int, LibraryEntry] = {}
        # Video IDs posted on each Buffer platform, and the video host's size limit; both are
        # set by the window.
        self.buffer_posted: dict[str, set[int]] = {p.key: set() for p in PLATFORMS}
        self.hosting_limit = max(p.max_bytes for p in PLATFORMS)  # until hosting is known
        self.setObjectName("videos")
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        self.content_layout = layout
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.back_button = icon_button("All Videos", "chevron-left", role="Ghost")
        self.back_button.clicked.connect(lambda: self.guard(self.show_library))
        top.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(4)
        self.page_title = text("Videos", "Title")
        names.addWidget(self.page_title)
        self.introduction = text(
            "Add videos, give them a title, then upload or schedule them.", "Muted"
        )
        names.addWidget(self.introduction)
        top.addLayout(names, 1)
        action_row = QWidget()
        action_row.setObjectName("ActionRow")
        actions = QHBoxLayout(action_row)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)
        self.import_button = icon_button("Add Videos", "plus", role="Primary")
        self.import_button.setAccessibleName("Import Video")
        self.import_button.clicked.connect(self.choose_file)
        self.reload_button = icon_button("", "refresh", tip="Reload the library")
        self.reload_button.setAccessibleName("Reload Library")
        self.reload_button.clicked.connect(
            lambda: self.start("load", self.selected.id if self.selected else None)
        )
        actions.addWidget(self.reload_button)
        actions.addWidget(self.import_button)
        self.action_layout = actions
        top.addWidget(action_row, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(top)
        self.continue_button = icon_button("Continue Editing", "edit", role="Ghost")
        self.continue_button.clicked.connect(self.continue_editing)
        layout.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.continue_button.hide()
        self.back_button.hide()
        self.feedback = text("Open your library to get started.", "Subtle")
        self.feedback.setAccessibleName("Library operation status")
        layout.addWidget(self.feedback)
        self.import_issues = Section("Some files could not be added")
        self.import_error_text = text("", "Error")
        self.import_issues.body.addWidget(self.import_error_text)
        self.import_issues.hide()
        layout.addWidget(self.import_issues)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setAccessibleName("Library operation in progress")
        self.progress.hide()
        layout.addWidget(self.progress)
        self.empty, empty_layout = card(32, 12)
        empty_layout.addWidget(
            image(pixmap("film", 40, theme.SUBTLE)), 0, Qt.AlignmentFlag.AlignHCenter
        )
        heading = text("No videos yet", "Heading")
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(heading)
        hint = text(
            "Choose Add Videos to pick MP4, MOV or WebM files. Your original files always stay where they are.",
            "Muted",
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(hint)
        layout.addWidget(self.empty)
        self.list = QListWidget()
        self.list.setObjectName("Library")
        self.list.setAccessibleName("Video library")
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(0)
        self.list.setFixedHeight(90)
        self.list.currentItemChanged.connect(self.selection_changed)
        self.list.itemClicked.connect(self.open_current_item)
        self.library_section = Section("")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles and file names")
        self.search.setAccessibleName("Search video library")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            icon("search", theme.SUBTLE), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self.render_library)
        options = QHBoxLayout()
        options.setSpacing(10)
        options.addWidget(self.search, 2)
        self.filter = QComboBox()
        self.filter.setAccessibleName("Filter videos by saved status")
        for label, key in (
            ("All videos", "all"),
            ("Needs details", "needs_review"),
            ("Ready to upload", "reviewed"),
            ("Scheduled", "scheduled"),
            ("Uploaded", "uploaded"),
            ("File problems", "inspection"),
        ):
            self.filter.addItem(label, key)
        self.sort = QComboBox()
        self.sort.setAccessibleName("Sort video library")
        for label, key in (
            ("Newest first", "newest"),
            ("Oldest first", "oldest"),
            ("Name A–Z", "name_az"),
            ("Name Z–A", "name_za"),
            ("Shortest first", "shortest"),
            ("Longest first", "longest"),
        ):
            self.sort.addItem(label, key)
        for control in (self.filter, self.sort):
            control.setMinimumWidth(0)
            control.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            control.currentIndexChanged.connect(self.render_library)
            options.addWidget(control, 1)
        self.library_section.body.addLayout(options)
        self.library_count = text("", "Subtle")
        self.library_section.body.addWidget(self.library_count)
        self.library_section.body.addWidget(self.list)
        self.library_count.setToolTip(
            "Saved status. Selecting a video checks its file. Plans are not uploads; returned YouTube IDs may need verification."
        )
        layout.addWidget(self.library_section)

        self.card, outer = card(22, 14)
        heading_row = QHBoxLayout()
        heading_names = QVBoxLayout()
        heading_names.setSpacing(2)
        self.selected_name = Elided("", "Heading")
        heading_names.addWidget(self.selected_name)
        self.step_status = text("", "Subtle")
        heading_names.addWidget(self.step_status)
        heading_row.addLayout(heading_names, 1)
        outer.addLayout(heading_row)
        # Three steps over the six editor pages; the tab bar itself stays hidden.
        self.stepper = QWidget()
        self.stepper.setObjectName("ActionRow")
        steps = FlowLayout(self.stepper, 6)
        self.step_buttons: dict[int, QPushButton] = {}
        for number, (label, index) in enumerate(
            (("Details", 1), ("Review", 2), ("Upload", 3)), start=1
        ):
            step = QPushButton(f"{number}  {label}")
            step.setObjectName("Step")
            step.setCheckable(True)
            step.setCursor(Qt.CursorShape.PointingHandCursor)
            step.clicked.connect(lambda checked=False, i=index: self.go_step(i))
            steps.addWidget(step)
            self.step_buttons[index] = step
        outer.addWidget(self.stepper)
        # Upload step: the same two choices for every platform.
        self.upload_platform, self.upload_when = "youtube", "now"
        self.when_changed: Callable[[str], None] | None = None  # set by the window (Buffer)
        self.destinations = QWidget()
        self.destinations.setObjectName("ActionRow")
        targets = FlowLayout(self.destinations, 6)
        self.platform_buttons: dict[str, QPushButton] = {}
        self.when_buttons: dict[str, QPushButton] = {}
        for group, key, label, glyph in (
            ("platform", "youtube", "YouTube", ""),
            ("platform", "tiktok", "TikTok", ""),
            ("platform", "instagram", "Instagram", ""),
            ("platform", "facebook", "Facebook", ""),
            ("when", "now", "Now", "send"),
            ("when", "later", "Schedule", "calendar"),
        ):
            if group == "when" and key == "now":
                spacer = QLabel("When")
                spacer.setObjectName("Subtle")
                spacer.setContentsMargins(14, 0, 2, 0)
                spacer.setMinimumHeight(36)  # same height as the buttons, text centred
                spacer.setAlignment(Qt.AlignmentFlag.AlignVCenter)
                targets.addWidget(spacer)
            elif group == "platform" and key == "youtube":
                lead = QLabel("Upload to")
                lead.setObjectName("Subtle")
                lead.setContentsMargins(0, 0, 2, 0)
                lead.setMinimumHeight(36)  # same height as the buttons, text centred
                lead.setAlignment(Qt.AlignmentFlag.AlignVCenter)
                targets.addWidget(lead)
            target = QPushButton(label)
            target.setObjectName("Segment")
            target.setCheckable(True)
            target.setIcon(logo_icon(key) if group == "platform" else icon(glyph, theme.MUTED))
            target.setCursor(Qt.CursorShape.PointingHandCursor)
            target.clicked.connect(lambda checked=False, g=group, k=key: self.choose_upload(g, k))
            targets.addWidget(target)
            (self.platform_buttons if group == "platform" else self.when_buttons)[key] = target
        outer.addWidget(self.destinations)
        self.workspace = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.workspace.setSpacing(24)
        outer.addLayout(self.workspace)
        self.preview = VideoPreview()
        self.tabs = PreparationTabs()
        self.tabs.tabBar().hide()
        self.tabs.setAccessibleName("Video preparation steps")
        self.workspace.addWidget(self.tabs, 1, Qt.AlignmentFlag.AlignTop)
        side = QWidget()
        self.side = side
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)
        side_layout.addWidget(self.preview)
        self.facts = text("", "Subtle")
        side_layout.addWidget(self.facts)
        # Whether the file suits each platform, side by side.
        side_layout.addWidget(text("FITS ON", "Eyebrow"))
        self.readiness = pill("", "Good")
        self.readiness.setAccessibleName("YouTube readiness result")
        self.fits = {"youtube": self.readiness}
        for spec in PLATFORMS:
            self.fits[spec.key] = pill("", "Good")
            self.fits[spec.key].setAccessibleName(f"{spec.name} readiness result")
        for platform, badge in self.fits.items():
            line = QHBoxLayout()
            line.setSpacing(8)
            line.addWidget(image(logo(platform, 18)))
            name = QLabel(PLATFORM_NAMES[platform])
            line.addWidget(name, 1)
            line.addWidget(badge)
            side_layout.addLayout(line)
        self.warnings = text("", "Subtle")
        side_layout.addWidget(self.warnings)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected video metadata")
        self.details.hide()  # full file facts stay available as a tooltip
        self.refresh_button = icon_button("Check File Again", "refresh", role="Ghost")
        self.refresh_button.clicked.connect(self.refresh)
        self.remove_button = icon_button("Remove", "trash", role="Ghost")
        self.remove_button.setToolTip("Remove from the library. The original file is not touched.")
        self.remove_button.clicked.connect(self.confirm_remove)
        side_layout.addWidget(row(self.refresh_button, self.remove_button, spacing=2))
        side_layout.addStretch()
        self.workspace.addWidget(side, 0, Qt.AlignmentFlag.AlignTop)
        # Page 0 (file facts) now lives beside the preview; the page keeps step numbers stable.
        self.tabs.addTab(QWidget(), "File")
        self.editor = MetadataEditor()
        self.editor.changed.connect(self.update_review)
        self.editor.save_requested.connect(self.save_metadata)
        self.editor.review_requested.connect(self.save_and_review)
        self.editor.layout_changed.connect(self.tabs.resize_to_current)
        self.editor.use_filename.clicked.connect(self.use_filename)
        self.tabs.addTab(self.editor, "Details")
        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_layout.setContentsMargins(0, 4, 0, 4)
        review_layout.setSpacing(14)
        review_heading = QHBoxLayout()
        review_heading.addWidget(text("Check everything before uploading", "Heading"), 1)
        self.review_state = pill("Draft", "Neutral")
        review_heading.addWidget(self.review_state)
        review_layout.addLayout(review_heading)
        self.review_summary = QPlainTextEdit()
        self.review_summary.setReadOnly(True)
        self.review_summary.setAccessibleName("Video and publishing metadata review")
        self.review_summary.setFixedHeight(220)
        review_layout.addWidget(self.review_summary)
        self.review_errors = text("")
        self.review_warnings = text("", "Muted")
        review_layout.addWidget(self.review_errors)
        review_layout.addWidget(self.review_warnings)
        review_layout.addWidget(text(LIMITATIONS, "Subtle"))
        self.review_button = icon_button("Mark as Ready", "check", role="Primary")
        self.review_button.setToolTip(
            "Confirms these exact saved details. Nothing is uploaded yet."
        )
        self.review_button.clicked.connect(self.complete_review)
        self.check_button = icon_button("Check Again", "refresh")
        self.check_button.clicked.connect(
            lambda: self.start("check", self.selected.id) if self.selected else None
        )
        edit_button = icon_button("Edit Details", "edit")
        edit_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.publish_next = icon_button("Continue to Upload", "upload", role="Primary")
        self.publish_next.clicked.connect(lambda: self.go_step(3))
        self.schedule_next = icon_button("Schedule", "calendar")
        self.schedule_next.clicked.connect(lambda: self.tabs.setCurrentIndex(4))
        self.schedule_next.hide()  # the Upload step offers scheduling for every platform
        review_layout.addWidget(
            flow(self.review_button, self.publish_next, edit_button, self.check_button)
        )
        review_layout.addStretch()
        self.tabs.addTab(review_page, "Review")
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.resize_to_current()
        layout.addWidget(self.card)
        layout.addStretch()
        self.card.hide()
        self.list.hide()
        self.set_busy(False)

    def upload_page(self) -> int:
        """Pages 3 and 4 are YouTube now / scheduled; pages 5–7 are TikTok, Instagram and
        Facebook (now and scheduled in one page each)."""
        if self.upload_platform in BUFFER_PAGES:
            return BUFFER_PAGES[self.upload_platform]
        return 4 if self.upload_when == "later" else 3

    def choose_upload(self, group: str, key: str) -> None:
        if group == "platform":
            self.upload_platform = key
        else:
            self.upload_when = key
        if self.when_changed:
            self.when_changed(self.upload_when)
        self.tabs.setCurrentIndex(self.upload_page())
        self.sync_steps()

    def import_files(self, paths: list[str], done: Callable[[], None] | None = None) -> None:
        """Import now, or as soon as the current work ends: the request is never dropped.
        The first new video opens on its Details step; `done` runs once the import ends."""
        if self.worker is None:
            self.start("import_many", paths)
            if done and self.worker is not None:

                def finished() -> None:
                    self.idle.disconnect(finished)
                    done()

                self.idle.connect(finished)
            return

        def later() -> None:
            self.idle.disconnect(later)
            self.import_files(paths, done)

        self.idle.connect(later)

    def open_upload(self, video_id: int) -> None:
        """Select one video and show its Upload step (its Details while it isn't ready)."""
        self.upload_next = (video_id, self.worker is None)
        if self.worker is None:
            self.start("select", video_id)  # otherwise asked for when the current work ends

    def go_step(self, index: int) -> None:
        if index == 3:
            # The Upload step shows every platform; one platform's page opens from its row.
            index = ALL_PAGE if self.tabs.count() > ALL_PAGE else self.upload_page()
        self.tabs.setCurrentIndex(index)
        self.sync_steps()

    def sync_steps(self) -> None:
        current = self.tabs.currentIndex()
        # Direct jumps to an upload page (tests, links) update the two choices to match.
        if current in (3, 4):
            self.upload_platform, self.upload_when = "youtube", "later" if current == 4 else "now"
        elif current in BUFFER_PAGES.values():
            self.upload_platform = next(k for k, v in BUFFER_PAGES.items() if v == current)
        for index, step in self.step_buttons.items():
            step.setChecked(current == index or (index == 3 and current in UPLOAD_PAGES))
            step.setEnabled(self.tabs.isTabEnabled(index))
        for key, target in self.platform_buttons.items():
            target.setChecked(key == self.upload_platform)
            page = BUFFER_PAGES.get(key, 3)
            target.setEnabled(page < self.tabs.count() and self.tabs.isTabEnabled(page))
        for key, target in self.when_buttons.items():
            target.setChecked(key == self.upload_when)
        if self.when_changed:
            self.when_changed(self.upload_when)
        # One platform at a time was confusing: platforms are ticked together on the Upload step.
        self.destinations.setVisible(False)
        self.stepper.setVisible(self.mode == "editor")

    def use_filename(self) -> None:
        if self.selected:
            self.editor.title.setText(readable(Path(self.selected.path).name))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        wide = self.viewport().width() >= 860
        self.workspace.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )
        if wide:
            self.side.setFixedWidth(270)
        else:
            self.side.setMinimumWidth(0)
            self.side.setMaximumWidth(300)
        self.tabs.resize_to_current()

    def show_library(self) -> None:
        if self.mode == "editor":
            self.editing_step = self.tabs.currentIndex()
        self.context_revision += 1
        self.mode = "library"
        self.feedback.setText("Import videos or select one to edit.")
        self.preview.release()
        self.sync_navigation()
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self.verticalScrollBar().setValue(0)
        # Returning to the library refreshes each video's saved status.
        self.refresh_browse()

    def title_of(self, video: Video) -> str:
        entry = self.entries.get(video.id)
        saved = entry.preparation.metadata.title if entry else ""
        return saved or readable(Path(video.path).name)

    def sync_navigation(self) -> None:
        editing = self.mode == "editor" and self.selected is not None
        library = self.mode == "library"
        self.card.setVisible(editing or self.mode == "history")
        self.introduction.setVisible(library)
        self.reload_button.setVisible(library)
        for button in (self.recurring_button, self.history_button):
            if button:
                button.setVisible(library)
        if self.watch_panel:
            self.watch_panel.setVisible(library)
        self.library_section.setVisible(library and bool(self.records))
        self.back_button.setVisible(not library)
        self.continue_button.setVisible(library and self.selected is not None)
        self.continue_button.setText(
            "Continue editing · " + self.title_of(self.selected)
            if self.selected
            else "Continue Editing"
        )
        self.empty.setVisible(library and not self.records and self.worker is None)
        self.side.setVisible(editing)
        self.preview.setVisible(editing)
        self.selected_name.setText(
            self.title_of(self.selected) if editing and self.selected else "Upload History"
        )
        self.selected_name.setToolTip(self.selected.path if editing and self.selected else "")
        self.step_status.setVisible(editing)
        for index in range(self.tabs.count()):
            self.tabs.setTabVisible(index, self.mode != "history" or index == 3)
        self.sync_steps()

    def continue_editing(self) -> None:
        if self.selected and self.worker is None:
            # Revalidate saved work, but never replace an unsaved editing buffer.
            if self.editor.dirty:
                self.mode = "editor"
                self.sync_navigation()
                self.tabs.setCurrentIndex(self.editing_step)
                self.preview.set_source(self.selected.path)
            else:
                self.start("select", self.selected.id)

    def open_current_item(self, item: QListWidgetItem) -> None:
        if self.mode == "library" and self.worker is None:
            self.start("select", item.data(Qt.ItemDataRole.UserRole))

    def activate(self) -> None:
        self.show_library()
        if self.worker is None and not self.loaded:
            self.start("load")

    def discovery_changed(self) -> None:
        if self.worker:
            self.pending_discovery = True
        elif not self.closing:
            self.start("browse")

    def refresh_browse(self) -> None:
        if self.loaded and self.isVisible() and self.worker is None and not self.closing:
            self.start("browse")

    @Slot()
    def choose_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import local videos",
            "",
            "Videos (*.mp4 *.mov *.webm *.mkv *.m4v *.avi);;All files (*)",
        )
        if paths:
            self.start("import_many", paths)

    @Slot()
    def refresh(self) -> None:
        if self.selected:
            self.start("refresh", self.selected.id)

    @Slot()
    def confirm_remove(self) -> None:
        if not self.selected:
            return
        answer = QMessageBox.question(
            self,
            "Remove from library?",
            "Remove the selected entry? The original video file will remain untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.start("remove", self.selected.id)

    @Slot(QListWidgetItem, QListWidgetItem)
    def selection_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if current and self.worker is None:
            target = current.data(Qt.ItemDataRole.UserRole)
            # Keep selection and editor identity aligned while a guard/save is pending.
            if self.selected:
                self.list.blockSignals(True)
                for index in range(self.list.count()):
                    item = self.list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole) == self.selected.id:
                        self.list.setCurrentItem(item)
                self.list.blockSignals(False)
            self.start("select", target)

    def start(self, action: str, argument: Any = None) -> None:
        if self.worker is not None or self.closing:
            return
        if (
            action in ("select", "import", "import_many", "load", "remove", "refresh")
            and self.editor.dirty
        ):
            self.guard(lambda: self._start(action, argument))
            return
        self._start(action, argument)

    def _start(self, action: str, argument: Any = None) -> None:
        if self.worker is not None or self.closing:
            return
        self.operation_context = self.context_revision
        self.feedback.setText(
            {
                "import": "Importing video…",
                "import_many": "Importing videos…",
                "refresh": "Refreshing inspection…",
                "load": "Loading library…",
                "select": "Checking source availability…",
                "remove": "Removing library entry…",
                "check": "Rechecking source and saved details…",
                "save_metadata": "Saving details…",
                "complete_review": "Checking and recording local review…",
                "browse": "Updating library status…",
            }[action]
        )
        self.save_succeeded = False
        self.set_busy(True)
        self.worker = LibraryWorker(self.service, action, argument, self)
        self.worker.result.connect(self.receive)
        self.worker.failed.connect(self.failure)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def set_busy(self, busy: bool) -> None:
        self.editor.setEnabled(not busy)
        self.review_button.setEnabled(not busy)
        self.check_button.setEnabled(not busy)
        self.update_review(busy=busy)
        self.progress.setVisible(busy)
        self.empty.setVisible(not busy and not self.records and self.mode == "library")
        self.continue_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)
        self.list.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy and self.selected is not None)
        self.remove_button.setEnabled(not busy and self.selected is not None)

    @Slot(object)
    def receive(self, result: dict[str, Any]) -> None:
        if self.closing:
            return
        if result["action"] == "browse":
            self.entries = result["entries"]
            self.records = {key: entry.video for key, entry in self.entries.items()}
            self.sync_navigation()
            self.render_library()
            self.feedback.setText("Library updated.")
            return
        self.loaded = True
        self.records = {video.id: video for video in result["videos"]}
        self.entries = result.get("entries", {})
        requested_step = self.tabs.currentIndex() if self.mode == "editor" else self.editing_step
        previous_id = self.selected.id if self.selected else None
        self.selected = result["selected"]
        self.availability = result["source_status"]
        if self.selected:
            preserve = (
                result["action"] == "check"
                and self.editor.dirty
                and previous_id == self.selected.id
            )
            if not preserve:
                self.editor.load(result["preparation"])
            if previous_id != self.selected.id:
                self.editing_step = 1
                requested_step = 1
                self.tabs.setCurrentIndex(1)
        if result["action"] == "save_metadata":
            self.save_succeeded = True
        self.update_review()

        if self.selected is None:
            self.editor.preparation = None
        if self.selected is None and self.mode != "history":
            self.mode = "library"
        elif (
            self.selected is not None
            and result["action"] in ("select", "import", "import_many")
            and self.operation_context == self.context_revision
        ):
            self.mode = "editor"
            if result["action"] in ("import", "import_many"):
                requested_step = 1
        self.sync_navigation()
        if self.mode == "editor":
            self.tabs.setCurrentIndex(requested_step)
        self.render_library()
        self.preview.set_source(
            self.selected.path if self.selected and self.mode == "editor" else ""
        )

        if self.selected:
            self.show_details(self.selected, result["source_status"])
        self.import_error_text.setText("\n".join(result.get("import_errors", [])))
        self.import_issues.setVisible(bool(result.get("import_errors")))
        self.feedback.setToolTip(self.import_error_text.text())
        self.feedback.setText(
            (
                "Some files could not be imported. Open Import errors for details. "
                if result.get("import_errors")
                else ""
            )
            + result["message"]
            or (
                "Add a title and details, then review and upload."
                if self.mode == "editor"
                else "Choose a video from Library to edit or preview it."
                if self.records
                else "No videos imported yet. Original files always stay in place."
            )
        )

    def library_row(self, entry: LibraryEntry, attention: bool) -> QWidget:
        video = entry.video
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(12)
        thumb = image(pixmap("film", 20, theme.MUTED), 40)
        thumb.setObjectName("Thumb")
        layout.addWidget(thumb)
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(Elided(self.title_of(video), "Subheading"))
        media = video.media
        names.addWidget(
            Elided(
                f"{Path(video.path).name} · {duration(media.duration)} · "
                f"{media.width}×{media.height} · added {friendly(video.imported_at)}",
                "Subtle",
            )
        )
        layout.addLayout(names, 1)
        if entry.results:
            layout.addWidget(image(logo("youtube", 18)))
        for platform, posted in self.buffer_posted.items():
            if video.id in posted:
                layout.addWidget(image(logo(platform, 18)))
        if attention or video.error:
            status, tone = "Check file", "Bad"
        elif entry.plans:
            status, tone = "Scheduled", "Info"
        elif entry.results or self.posted_through_buffer(video.id):
            status, tone = "Uploaded", "Good"
        elif entry.auto_queued:
            status, tone = "Queued for auto upload", "Info"
        elif entry.reviewed:
            status, tone = "Ready", "Good"
        else:
            status, tone = "Needs details", "Warn"
        layout.addWidget(pill(status, tone))
        layout.addWidget(image(pixmap("chevron-right", 16, theme.SUBTLE)))
        return holder

    def posted_through_buffer(self, video_id: int) -> bool:
        return any(video_id in posted for posted in self.buffer_posted.values())

    def render_library(self) -> None:
        if not hasattr(self, "sort"):
            return
        scroll = self.list.verticalScrollBar().value()
        self.list.blockSignals(True)
        self.list.clear()
        category = self.filter.currentData()
        visible = browse(
            self.entries,
            self.search.text(),
            "all" if category == "uploaded" else category,
            self.sort.currentData(),
        )
        if category == "uploaded":  # on any platform
            visible = [e for e in visible if e.results or self.posted_through_buffer(e.video.id)]
        for entry in visible:
            video = entry.video
            status = entry.label
            attention = bool(self.selected and video.id == self.selected.id and self.availability)
            if attention:
                status = "Source needs attention · " + status
            title = entry.preparation.metadata.title
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, video.id)
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"{Path(video.path).name}" + (f" — {title}" if title else "") + f", {status}",
            )
            item.setToolTip(f"{video.path}\nTitle: {title or 'Not saved'}\n{status}")
            item.setSizeHint(QSize(0, 62))
            self.list.addItem(item)
            self.list.setItemWidget(item, self.library_row(entry, attention))
            if self.mode == "editor" and self.selected and self.selected.id == video.id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.list.verticalScrollBar().setValue(scroll)
        self.list.setFixedHeight(min(520, max(76, len(visible) * 64 + 10)))
        self.list.setVisible(bool(visible))
        message = f"{len(visible)} of {len(self.entries)} videos"
        if not visible:
            message += " · No matches. Clear search or choose All videos."
        if self.selected and all(e.video.id != self.selected.id for e in visible):
            message += (
                "\nThe selected video is hidden by these filters; its editor stays open below."
            )
        self.library_count.setText(message)

    def show_details(self, video: Video, availability: str) -> None:
        media = video.media
        fps = f"{media.fps:.3f} fps" if media.fps else "Unknown"
        self.details.setPlainText(
            f"Filename: {Path(video.path).name}\nLocation: {video.path}\n"
            f"Duration: {media.duration:.3f} seconds\nDimensions: {media.width} × {media.height} (encoded)\n"
            f"Display aspect ratio: {media.aspect:.4f}:1 · Rotation: {media.rotation:g}°\n"
            f"Frame rate: {fps}\nVideo codec: {media.video_codec}\n"
            f"Audio: {', '.join(media.audio_codecs) or 'No audio stream'}\n"
            f"Size: {media.size:,} bytes ({media.size / 1024 / 1024:.2f} MiB)\n"
            f"Imported (UTC): {video.imported_at}\nLast successful inspection (UTC): {video.inspected_at}"
        )
        self.facts.setText(
            f"{Path(video.path).name}\n{duration(media.duration)} · {media.width}×{media.height}"
            f" · {file_size(media.size)}"
        )
        self.facts.setToolTip(self.details.toPlainText())
        if availability or video.error:
            for badge in self.fits.values():
                badge.setText("Not currently verified")
                restyle(badge, "Bad")
            self.warnings.setText(
                "\n".join(
                    filter(
                        None,
                        (
                            availability,
                            video.error,
                            "Details above are from the last successful check of the file.",
                        ),
                    )
                )
            )
            return
        result = evaluate(media)
        # The app decides by YouTube's own Shorts rules (length and shape). Other notes, such
        # as an unknown pixel shape or no sound, stay in the tooltip; they don't stop an upload.
        blocking = [m for m in result.messages if m.startswith(("Longer than", "Landscape"))]
        self.readiness.setText("Won't post" if blocking else "Fits")
        restyle(self.readiness, "Bad" if blocking else "Good")
        notes = [m for m in result.messages if m not in blocking]
        if result.status in READINESS_LABELS:
            notes = []  # the passed checks themselves, not notes
        self.readiness.setToolTip(
            "\n".join(blocking or ["Length and shape suit YouTube Shorts."])
            + "".join(f"\n{m}" for m in notes)
            + "\n\n"
            + LIMITATIONS
        )
        lines = [f"YouTube: {m}" for m in blocking]
        for spec in PLATFORMS:
            problems = media_errors(spec, video, self.hosting_limit)
            badge = self.fits[spec.key]
            badge.setText("Fits" if not problems else "Won't post")
            restyle(badge, "Good" if not problems else "Bad")
            badge.setToolTip(
                "\n".join(problems) or f"Length, size, shape and file type suit {spec.name}."
            )
            lines += [f"{spec.name}: {m}" for m in problems]
        self.warnings.setText("\n".join(lines))

    @Slot(str)
    def failure(self, message: str) -> None:
        if not self.closing:
            self.feedback.setText("Operation failed: " + message)
            # A failed new import never replaces the selected video's prior inspection.

    @Slot()
    def finished(self) -> None:
        worker = self.worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self.worker = None
        self.set_busy(False)
        continuation = self.after_save if self.save_succeeded else None
        self.after_save = None
        target, self.upload_next = self.upload_next, None
        if target and self.selected and self.selected.id == target[0]:
            self.go_step(3 if self.tabs.isTabEnabled(3) else 1)
        elif target and not target[1] and not self.closing and not continuation:
            self.open_upload(target[0])  # the page was busy when it was asked; select it now
            return  # idle once that select has finished
        self.idle.emit()
        if continuation:
            continuation()
        elif self.pending_discovery and not self.closing:
            self.pending_discovery = False
            self.start("browse")

    def request_close(self) -> bool:
        self.preview.release()
        if self.worker is None:
            if self.editor.dirty:
                return self.guard(self.window().close)
            return True
        if self.editor.dirty and self.worker.action != "save_metadata":
            self.feedback.setText(
                "Please wait for the current check, then close to save or discard your edits."
            )
            return False
        if self.worker.action == "save_metadata":
            self.after_save = self.window().close
            return False
        self.closing = True
        self.worker.cancel.set()
        self.feedback.setText("Closing safely… Waiting for the current operation to stop.")
        return False

    @Slot()
    def stop_for_quit(self) -> None:
        """Also guard application-level Quit, which may bypass the window close event."""
        self.closing = True
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait()

    def guard(self, continuation: Callable[[], object]) -> bool:
        """Continue only after a successful save or explicit discard; Cancel does nothing."""
        if self.worker is not None:
            if self.worker.action == "save_metadata":
                self.after_save = continuation
            else:
                self.feedback.setText(
                    "Please wait for the current operation before leaving your edits."
                )
            return False
        if not self.editor.dirty:
            continuation()
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved details",
            "Save your changes before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            self.after_save = continuation
            self.save_metadata()
            return False
        if answer == QMessageBox.StandardButton.Discard:
            if self.editor.preparation:
                self.editor.load(self.editor.preparation)
            continuation()
            return True
        return False

    def save_and_review(self) -> None:
        if self.editor.dirty:
            self.after_save = lambda: self.tabs.setCurrentIndex(2)
            self.save_metadata()
        else:
            self.tabs.setCurrentIndex(2)

    @Slot()
    def save_metadata(self) -> None:
        if self.selected and self.editor.preparation:
            self.start(
                "save_metadata",
                (self.selected.id, self.editor.value(), self.editor.preparation.revision),
            )

    @Slot()
    def complete_review(self) -> None:
        if self.selected and self.editor.preparation and not self.editor.dirty:
            self.start(
                "complete_review",
                (
                    self.selected.id,
                    self.editor.preparation.revision,
                    self.selected.inspection_revision,
                ),
            )

    @Slot(int)
    def tab_changed(self, index: int) -> None:
        self.sync_steps()
        self.update_step_status()
        if index == 2 and self.selected and self.worker is None:
            self.start("check", self.selected.id)

    def update_step_status(self) -> None:
        if not self.selected or not self.editor.preparation:
            self.step_status.setText(
                "Select a video to edit it. Past uploads stay in Upload History."
            )
            return
        saved = (
            "Unsaved changes"
            if self.editor.dirty
            else "Details saved"
            if self.editor.preparation.revision
            else "Details not saved yet"
        )
        reviewed = self.review_state.text().startswith("Review Complete") and not self.editor.dirty
        review = "Review complete" if reviewed else "Review required before uploading"
        self.step_status.setText(f"{saved} · {review}")

    def update_review(self, *, busy: bool | None = None) -> None:
        if not self.selected or not self.editor.preparation:
            self.update_step_status()
            self.publish_next.setEnabled(False)
            self.review_button.setEnabled(False)
            return
        preparation = replace(self.editor.preparation, metadata=self.editor.value())
        result = review_checks(
            self.selected, preparation, self.availability, unsaved=self.editor.dirty
        )
        self.review_state.setText(result.state + " · Local preparation only")
        restyle(self.review_state, REVIEW_TONES.get(result.state, "Warn"))
        self.update_step_status()
        self.publish_next.setEnabled(result.state == "Review Complete" and not self.editor.dirty)
        self.schedule_next.setEnabled(self.publish_next.isEnabled())
        data = preparation.metadata
        self.review_summary.setPlainText(
            f"Video: {Path(self.selected.path).name}\n"
            f"{duration(self.selected.media.duration)} · {self.selected.media.width} × {self.selected.media.height}\n"
            f"Title: {data.title}\nDescription:\n{data.description}\n"
            f"Tags: {', '.join(data.tags) or 'None'}\nMade for Kids: {declaration(data.made_for_kids)}\n"
            f"Synthetic disclosure: {declaration(data.synthetic)}\nSaved visibility preference: {data.privacy.title()}\n"
            f"Last completed local review: {timestamp(preparation.reviewed_at, empty='Not reviewed')}"
        )
        self.review_errors.setText(
            "Fix these first:\n" + "\n".join(result.errors)
            if result.errors
            else "Everything required is filled in."
        )
        self.review_errors.setObjectName("Error" if result.errors else "Muted")
        self.review_errors.style().polish(self.review_errors)
        self.review_warnings.setText(
            "Warnings\n" + "\n".join(result.warnings)
            if result.warnings
            else "No technical warnings."
        )
        self.tabs.resize_to_current()
        working = self.worker is not None if busy is None else busy
        self.review_button.setEnabled(
            not working and not result.errors and result.state != "Review Complete"
        )
        self.review_button.setVisible(result.state != "Review Complete")
