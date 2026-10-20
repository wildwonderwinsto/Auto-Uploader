"""Local library presentation and one cancellable worker; no SQL or subprocesses here."""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFrame,
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
from .components import Disclosure, FlowLayout, primary
from .formatting import duration, timestamp
from .metadata import MetadataEditor, PreparationTabs
from .preview import VideoPreview

logger = logging.getLogger(__name__)


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
                        "Add publishing details, then save and review."
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
        self.save_succeeded = False
        self.availability = ""
        self.selected: Video | None = None
        self.records: dict[int, Video] = {}
        self.entries: dict[int, LibraryEntry] = {}
        self.setObjectName("videos")
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        self.content_layout = layout
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        layout.addWidget(text("Videos", "Title"))
        self.introduction = text("Import or select a video to begin.", "Muted")
        layout.addWidget(self.introduction)
        action_row = QWidget()
        action_row.setObjectName("ActionRow")
        actions = FlowLayout(action_row)
        self.import_button = QPushButton("Import Videos…")
        self.import_button.setAccessibleName("Import Video")
        primary(self.import_button)
        self.import_button.clicked.connect(self.choose_file)
        self.reload_button = QPushButton("Reload Library")
        self.reload_button.clicked.connect(
            lambda: self.start("load", self.selected.id if self.selected else None)
        )
        actions.addWidget(self.import_button)
        actions.addWidget(self.reload_button)
        self.action_layout = actions
        layout.addWidget(action_row)
        self.continue_button = QPushButton("Continue Editing")
        self.continue_button.clicked.connect(self.continue_editing)
        layout.addWidget(self.continue_button)
        self.continue_button.hide()
        self.back_button = QPushButton("← Back to Library")
        self.back_button.clicked.connect(lambda: self.guard(self.show_library))
        # One header row: while editing it reads "← Back to Library · Import Videos…".
        actions.insertWidget(0, self.back_button)
        self.back_button.hide()
        self.feedback = text("Open your library to get started.", "Muted")
        self.feedback.setAccessibleName("Library operation status")
        layout.addWidget(self.feedback)
        self.import_issues = Disclosure("Import errors")
        self.import_error_text = text("")
        self.import_issues.body.addWidget(self.import_error_text)
        self.import_issues.hide()
        layout.addWidget(self.import_issues)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setAccessibleName("Library operation in progress")
        self.progress.hide()
        layout.addWidget(self.progress)
        self.empty = QFrame()
        self.empty.setObjectName("Card")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(12)
        empty_layout.addWidget(text("Your library is empty", "Heading"))
        empty_layout.addWidget(
            text(
                "Choose Import Video to inspect your first MP4, MOV, WebM, or another supported video.",
                "Muted",
            )
        )
        layout.addWidget(self.empty)
        self.list = QListWidget()
        self.list.setAccessibleName("Video library")
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(0)
        self.list.setFixedHeight(90)
        self.list.currentItemChanged.connect(self.selection_changed)
        self.list.itemClicked.connect(self.open_current_item)
        self.library_section = Disclosure("Library · choose a video", expanded=True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename, saved title or location")
        self.search.setAccessibleName("Search video library")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render_library)
        self.library_section.body.addWidget(self.search)
        options = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.setAccessibleName("Filter videos by saved status")
        for label, key in (
            ("All videos", "all"),
            ("Needs details or review", "needs_review"),
            ("Review complete (saved)", "reviewed"),
            ("Has a future plan", "scheduled"),
            ("Has a YouTube result", "uploaded"),
            ("Inspection failed", "inspection"),
        ):
            self.filter.addItem(label, key)
        self.sort = QComboBox()
        self.sort.setAccessibleName("Sort video library")
        for label, key in (
            ("Recently imported", "newest"),
            ("Oldest imported", "oldest"),
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
        self.library_count = text("", "Muted")
        self.library_section.body.addWidget(self.library_count)
        self.library_section.body.addWidget(self.list)
        self.library_count.setToolTip(
            "Saved status. Selecting a video checks its source. Plans are not uploads; returned YouTube IDs may need verification."
        )
        layout.addWidget(self.library_section)
        self.library_section.changed.connect(self.refresh_browse)
        self.card = QFrame()
        self.card.setObjectName("Card")
        outer = QVBoxLayout(self.card)
        self.selected_name = text("", "Heading")
        outer.addWidget(self.selected_name)
        self.step_status = text("", "Muted")
        outer.addWidget(self.step_status)
        self.workspace = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        outer.addLayout(self.workspace)
        self.preview = VideoPreview()
        self.preview.setMaximumWidth(300)
        self.tabs = PreparationTabs()
        self.tabs.setAccessibleName("Video preparation tabs")
        self.workspace.addWidget(self.tabs, 1, Qt.AlignmentFlag.AlignTop)
        self.workspace.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
        self.preview.setFixedWidth(260)
        information = QWidget()
        self.tabs.addTab(information, "File")
        body = QVBoxLayout(information)
        body.setContentsMargins(20, 20, 20, 20)
        body.setSpacing(12)
        next_details = QPushButton("Edit details →")
        primary(next_details)
        next_details.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        body.addWidget(next_details)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected video metadata")
        self.details.setMinimumWidth(0)
        self.details.setFixedHeight(225)
        technical = Disclosure("File information")
        technical.body.addWidget(self.details)
        technical.changed.connect(self.tabs.resize_to_current)
        body.addWidget(technical)
        body.addWidget(text("YouTube Shorts Readiness", "Heading"))
        self.readiness = text("", "Badge")
        self.readiness.setAccessibleName("YouTube Shorts readiness result")
        body.addWidget(self.readiness)
        self.warnings = text("", "Muted")
        body.addWidget(self.warnings)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh Inspection")
        self.refresh_button.clicked.connect(self.refresh)
        self.remove_button = QPushButton("Remove from Library")
        self.remove_button.clicked.connect(self.confirm_remove)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        body.addLayout(buttons)
        self.editor = MetadataEditor()
        self.editor.changed.connect(self.update_review)
        self.editor.save_requested.connect(self.save_metadata)
        self.editor.review_requested.connect(self.save_and_review)
        self.editor.layout_changed.connect(self.tabs.resize_to_current)
        self.tabs.addTab(self.editor, "Details")
        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_layout.setContentsMargins(20, 20, 20, 20)
        review_layout.setSpacing(14)
        review_layout.addWidget(text("Review before publishing", "Heading"))
        self.review_state = text("Draft", "Badge")
        review_layout.addWidget(self.review_state)
        self.review_summary = QPlainTextEdit()
        self.review_summary.setReadOnly(True)
        self.review_summary.setAccessibleName("Video and publishing metadata review")
        self.review_summary.setFixedHeight(220)
        review_layout.addWidget(self.review_summary)
        self.review_errors = text("")
        self.review_warnings = text("", "Muted")
        review_layout.addWidget(self.review_errors)
        review_layout.addWidget(self.review_warnings)
        review_layout.addWidget(text(LIMITATIONS, "Muted"))
        self.review_button = QPushButton("Mark Review Complete")
        self.review_button.clicked.connect(self.complete_review)
        primary(self.review_button)
        self.check_button = QPushButton("Recheck Source and Saved Details")
        self.check_button.clicked.connect(
            lambda: self.start("check", self.selected.id) if self.selected else None
        )
        edit_button = QPushButton("Edit Publishing Details")
        edit_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        review_layout.addWidget(self.review_button)
        self.publish_next = QPushButton("Publish now →")
        self.publish_next.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        review_layout.addWidget(self.publish_next)
        self.schedule_next = QPushButton("Schedule →")
        self.schedule_next.clicked.connect(lambda: self.tabs.setCurrentIndex(4))
        review_layout.addWidget(self.schedule_next)
        review_layout.addWidget(self.check_button)
        review_layout.addWidget(edit_button)
        review_layout.addStretch()
        self.tabs.addTab(review_page, "Review")
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.resize_to_current()
        layout.addWidget(self.card)
        layout.addStretch()
        self.card.hide()
        self.list.hide()
        self.set_busy(False)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        wide = self.viewport().width() >= 820
        self.workspace.setDirection(
            QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        )
        self.preview.setMinimumWidth(260 if wide else 0)
        self.preview.setMaximumWidth(260 if wide else 16777215)
        self.tabs.resize_to_current()

    def show_library(self) -> None:
        if self.mode == "editor":
            self.editing_step = self.tabs.currentIndex()
        self.context_revision += 1
        self.mode = "library"
        self.feedback.setText("Import videos or select one to edit.")
        self.preview.release()
        self.sync_navigation()
        self.library_section.set_expanded(True)
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self.verticalScrollBar().setValue(0)

    def sync_navigation(self) -> None:
        editing = self.mode == "editor" and self.selected is not None
        self.card.setVisible(editing or self.mode == "history")
        self.introduction.setVisible(self.mode == "library")
        self.reload_button.setVisible(self.mode == "library")
        for button in (self.recurring_button, self.history_button):
            if button:
                button.setVisible(self.mode == "library")
        if self.watch_panel:
            self.watch_panel.setVisible(self.mode == "library")
        self.library_section.setVisible(self.mode == "library" and bool(self.records))
        self.back_button.setVisible(self.mode != "library")
        self.continue_button.setVisible(self.mode == "library" and self.selected is not None)
        self.continue_button.setText(
            "Continue Editing · " + Path(self.selected.path).name
            if self.selected
            else "Continue Editing"
        )
        self.empty.setVisible(self.mode == "library" and not self.records and self.worker is None)
        self.preview.setVisible(editing)
        self.selected_name.setText(
            Path(self.selected.path).name if editing and self.selected else "Upload History"
        )
        self.step_status.setVisible(editing)
        for index in range(self.tabs.count()):
            self.tabs.setTabVisible(index, self.mode != "history" or index == 3)

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
        if (
            self.loaded
            and self.isVisible()
            and self.library_section.toggle.isChecked()
            and self.worker is None
            and not self.closing
        ):
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
                "save_metadata": "Saving publishing details…",
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
                self.library_section.set_expanded(False)
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
                "Preview your video and add publishing details."
                if self.mode == "editor"
                else "Choose a video from Library to edit or preview it."
                if self.records
                else "No videos imported yet. Original files always stay in place."
            )
        )

    def render_library(self) -> None:
        if not hasattr(self, "sort"):
            return
        scroll = self.list.verticalScrollBar().value()
        self.list.blockSignals(True)
        self.list.clear()
        visible = browse(
            self.entries, self.search.text(), self.filter.currentData(), self.sort.currentData()
        )
        for entry in visible:
            video = entry.video
            status = entry.label
            if self.selected and video.id == self.selected.id and self.availability:
                status = "Source needs attention · " + status
            title = entry.preparation.metadata.title
            item = QListWidgetItem(
                f"{Path(video.path).name}"
                + (f" — {title}" if title else "")
                + f"\n{duration(video.media.duration)} · {status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, video.id)
            item.setToolTip(f"{video.path}\nTitle: {title or 'Not saved'}\n{status}")
            self.list.addItem(item)
            if self.mode == "editor" and self.selected and self.selected.id == video.id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.list.verticalScrollBar().setValue(scroll)
        self.list.setFixedHeight(min(220, max(76, len(visible) * 58)))
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
        if availability or video.error:
            self.readiness.setText("Not currently verified")
            self.warnings.setText(
                "\n".join(
                    filter(
                        None,
                        (
                            availability,
                            video.error,
                            "Metadata above is from the last successful inspection.",
                        ),
                    )
                )
            )
        else:
            result = evaluate(media)
            self.readiness.setText(READINESS_LABELS.get(result.status, result.status))
            self.readiness.setToolTip(LIMITATIONS)
            self.warnings.setText("\n".join(result.messages))

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
            "Unsaved publishing details",
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
        self.update_step_status()
        if index == 2 and self.selected and self.worker is None:
            self.start("check", self.selected.id)

    def update_step_status(self) -> None:
        if not self.selected or not self.editor.preparation:
            self.step_status.setText(
                "Select a video to prepare it. Previous uploads remain in Publish."
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
        step = self.tabs.tabText(self.tabs.currentIndex())
        self.step_status.setText(f"{step} · {saved} · {review}")

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
            "Blocking issues\n" + "\n".join(result.errors)
            if result.errors
            else "No blocking issues found by the local checks."
        )
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
