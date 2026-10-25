"""Reuse Details and Upload Defaults on the Details step, and their card in Settings.

Both fill everything but the title: the description, tags, YouTube's settings and the AI label
on TikTok and Instagram. Reused details are shown unsaved, like typing them; defaults fill
each new video you add. Workers own the reads and writes."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..defaults import AI_PLATFORMS, DefaultsStore, Details
from .components import icon_button, in_background, row
from .icons import PLATFORM_NAMES
from .videos import text

if TYPE_CHECKING:
    from .window import MainWindow

PRIVACY_NAMES = {"private": "Private", "unlisted": "Unlisted", "public": "Public"}


def describe(details: Details) -> str:
    """The saved defaults in a few plain lines."""

    def answer(value: bool | None) -> str:
        return "not chosen" if value is None else "Yes" if value else "No"

    lines = [
        "Description: "
        + (details.description.splitlines()[0][:80] if details.description else "none"),
        "Tags: " + (", ".join(details.tags) if details.tags else "none"),
        f"YouTube: {PRIVACY_NAMES.get(details.privacy, details.privacy)} · made for kids "
        f"{answer(details.made_for_kids)} · altered or AI content {answer(details.synthetic)}",
    ]
    lines += [
        f"{PLATFORM_NAMES[p]}: AI-generated label {answer(details.ai.get(p))}" for p in AI_PLATFORMS
    ]
    return "\n".join(lines)


class ReuseDetails(QDialog):
    """Pick another video whose details to copy (all but the title)."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.choice: int | None = None
        self.setWindowTitle("Reuse details")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(text("Reuse details", "Title"))
        layout.addWidget(
            text(
                "Copy the description, tags, YouTube settings and AI labels from another video. "
                "Your title stays. Nothing is saved until you save the details.",
                "Muted",
            )
        )
        self.videos = QListWidget()
        self.videos.setWordWrap(True)
        self.videos.setAccessibleName("Videos to copy details from")
        self.videos.itemDoubleClicked.connect(lambda _: self.pick())
        current = window.videos.selected
        for identity, entry in window.videos.entries.items():
            if current and identity == current.id:
                continue
            metadata = entry.preparation.metadata
            title = metadata.title or entry.video.path.rsplit("/", 1)[-1]
            summary = metadata.description.splitlines()[0][:70] if metadata.description else ""
            tags = f"{len(metadata.tags)} tag{'s' if len(metadata.tags) != 1 else ''}"
            item = QListWidgetItem(f"{title}\n{summary or 'No description'} · {tags}")
            item.setData(Qt.ItemDataRole.UserRole, identity)
            self.videos.addItem(item)
        if self.videos.count():
            self.videos.setCurrentRow(0)
        else:
            self.videos.addItem(QListWidgetItem("No other videos in your library yet."))
        layout.addWidget(self.videos, 1)
        footer = QHBoxLayout()
        cancel = icon_button("Cancel", "x")
        cancel.clicked.connect(self.reject)
        self.go = icon_button("Reuse These Details", "copy", role="Primary")
        self.go.setEnabled(
            bool(
                self.videos.currentItem()
                and self.videos.currentItem().data(Qt.ItemDataRole.UserRole) is not None
            )
        )
        self.go.clicked.connect(self.pick)
        footer.addWidget(cancel)
        footer.addStretch()
        footer.addWidget(self.go)
        layout.addLayout(footer)

    def pick(self) -> None:
        item = self.videos.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        if identity is not None:
            self.choice = identity
            self.accept()


class DefaultsController(QObject):
    changed = Signal()

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.store = DefaultsStore(window.videos.service.library)
        self.saved: Details | None = None
        self.tasks: list[threading.Thread] = []
        editor = window.videos.editor
        editor.reuse.clicked.connect(self.reuse)
        editor.save_defaults.clicked.connect(self.save_current)
        # Settings: what new videos start with, and a way to stop using them.
        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.clear = icon_button("Clear Upload Defaults…", "x", role="Danger")
        self.clear.clicked.connect(self.clear_saved)
        self.settings = QWidget()
        body = QVBoxLayout(self.settings)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.addWidget(self.summary)
        body.addWidget(row(self.clear))
        self.render()

    def activate(self) -> None:
        self.run(self.store.read, self.loaded)

    def run(self, job: Callable[[], Any], done: Callable[[Any], None]) -> None:
        def finished(result: dict[str, Any]) -> None:
            if thread in self.tasks:
                self.tasks.remove(thread)
            if "error" in result:
                QMessageBox.warning(self.window, "Upload defaults", result["error"])
            else:
                done(result["ok"])

        thread = in_background(self, job, finished)
        self.tasks.append(thread)

    def request_close(self) -> bool:
        for thread in list(self.tasks):
            thread.join(timeout=2)  # a small file read or write; never cut off at quit
        return True

    def loaded(self, details: Details | None) -> None:
        self.saved = details
        self.render()

    def render(self) -> None:
        saved = self.saved
        self.summary.setText(
            "New videos you add start with these details (each one still gets its own title):\n"
            + describe(saved)
            if saved and not saved.empty
            else "No upload defaults saved. On a video's Details step, choose Save as Upload "
            "Defaults to have new videos start with its description, tags and settings."
        )
        self.clear.setVisible(bool(saved and not saved.empty))
        note = self.window.videos.editor.defaults_note
        note.setText(
            "New videos you add start with your upload defaults (Settings)."
            if saved and not saved.empty
            else ""
        )
        note.setVisible(bool(note.text()))
        self.changed.emit()

    # Details step ---------------------------------------------------------------------------

    def ai_choices(self) -> dict[str, bool | None]:
        return {s.key: s.ai.currentData() for s in self.window.socials if s.key in AI_PLATFORMS}

    def save_current(self) -> None:
        editor = self.window.videos.editor
        details = Details.of(editor.value(), self.ai_choices())
        if details.empty:
            QMessageBox.information(
                self.window,
                "Nothing to save yet",
                "Write a description, add tags or choose settings first. The title is never "
                "saved as a default.",
            )
            return
        if (
            QMessageBox.question(
                self.window,
                "Save as upload defaults?",
                "New videos you add will start with these details (not the title):\n\n"
                + describe(details),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            != QMessageBox.StandardButton.Save
        ):
            return

        def saved(_: object) -> None:
            self.loaded(details)
            editor.defaults_note.setText(
                "Saved as your upload defaults. New videos you add start with them."
            )

        self.run(lambda: self.store.save(details), saved)

    def clear_saved(self) -> None:
        if (
            QMessageBox.question(
                self.window,
                "Clear upload defaults?",
                "New videos will start empty again. Videos you already have keep their details.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.run(self.store.clear, lambda _: self.loaded(None))

    def reuse(self) -> None:
        dialog = ReuseDetails(self.window)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.choice is not None:
            source = dialog.choice
            self.run(lambda: self.store.of_video(source), self.fill)
        dialog.deleteLater()

    def fill(self, details: Details) -> None:
        """Into the editor, unsaved, like typing them; the AI labels save as they always do."""
        self.window.videos.editor.apply_details(details)
        for social in self.window.socials:
            value = details.ai.get(social.key)
            if social.key in AI_PLATFORMS and value is not None:
                social.ai.setCurrentIndex(social.ai.findData(value))
                social.save_draft(quiet=True)
