"""Cohesive profile setup with explicit standing consent and worker-owned local operations."""

from __future__ import annotations

import json
from collections.abc import Callable
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..automation import CONSENT, AutomationService, title_for
from ..cadence import Rule
from . import theme
from .autocards import AutoUpload, CardView, auto_uploads, build_card, describe
from .components import Section, day_row, icon_button, natural
from .formatting import system_zone, time_pattern, timestamp
from .icons import PLATFORM_NAMES, image, pixmap
from .recurring import rule_label
from .scheduling import text
from .watching import FolderConsent

if TYPE_CHECKING:
    from .window import MainWindow


DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class ProfileConsent(QDialog):
    def __init__(self, proposal: dict[str, Any], parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Authorize Automatic Posting")
        self.resize(610, 680)
        body = QVBoxLayout(self)
        body.addWidget(text("Authorize this exact future-file scope?", "Heading"))
        s = proposal["settings"]
        summary = QPlainTextEdit()
        summary.setReadOnly(True)
        summary.setPlainText(
            "\n\n".join(
                [
                    CONSENT,
                    f"{proposal['name']}\nFolder: {proposal['folder_binding']['path']}\nChannel: {proposal['channel_title']} ({proposal['channel_id']})\nVisibility: PUBLIC",
                    "Mode: "
                    + (
                        "Review each video before queue enrollment"
                        if s["mode"] == "review"
                        else "Standing approval for matching future files; no individual human review"
                    ),
                    f"Title template: {s['title_template']}\nDescription: {s['description']}\nTags: {', '.join(s['tags'])}\nMade for Kids: {s['made_for_kids']}\nSynthetic content: {s['synthetic']}\nCategory: People & Blogs (22; existing uploader)",
                    f"Eligibility: {s['pattern']}; square/portrait inspected video, at most {s['max_seconds']} seconds and {s['max_bytes'] / 1024**2:g} MiB. No symlinks or subfolders. Invalid/ambiguous metadata needs review. No title truncation.",
                    rule_label(Rule.decode(s["rule"])),
                    f"At most {s['limit']} automatic uploads per UTC day (YouTube's own limit). Repeated DST time uses first occurrence; gaps and missed/empty opportunities skip without catch-up. At most 100 waiting entries; overflow waits locally.",
                    "Background: "
                    + (
                        "Approved if both folder permission and the separately authorized service are enabled."
                        if s["background"]
                        else "Desktop only."
                    ),
                    "Existing files present at confirmation are excluded. New files may import while the profile is paused, but cannot enroll or initiate through it. Folder Pause stops discovery; profile Pause stops new posting; global publishing and service controls are separate. Disable revokes approval. Started uploads retain history.",
                    "Next opportunities:\n"
                    + "\n".join(
                        timestamp(t, s["rule"]["zone"], weekday=True)
                        for t in proposal["opportunities"]
                    ),
                ]
            )
        )
        body.addWidget(summary)
        self.scope = QCheckBox(
            "I authorize this exact folder, channel, rules and PUBLIC posting scope"
        )
        self.declarations = QCheckBox(
            "These declarations and metadata rules are appropriate for content I place here"
        )
        self.restrictions = QCheckBox(
            "My content complies with YouTube terms and needs no unresolved manual restriction"
        )
        for box in (self.scope, self.declarations, self.restrictions):
            box.setToolTip(
                "Do not authorize age-restricted or otherwise unresolved content. Not made for kids is not an 18+ setting."
            )
            body.addWidget(box)
            box.toggled.connect(self.ready)
        body.addWidget(
            text(
                "Age restrictions require YouTube Studio. No global publishing or service activation is performed here.",
                "Muted",
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.approve = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.approve.setText("Authorize and Enable Automation")
        self.approve.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

    def ready(self) -> None:
        self.approve.setEnabled(
            all(b.isChecked() for b in (self.scope, self.declarations, self.restrictions))
        )


STEPS = ("1 · Folder and channel", "2 · Video details", "3 · Schedule", "4 · Limits")


class ProfileEditor(QDialog):
    def __init__(self, controller: AutomationController, profile: dict[str, Any] | None = None):
        super().__init__(controller.window)
        self.controller, self.profile = controller, profile
        self.dirty = False
        self.setWindowTitle(
            "Edit Automation · renewed approval required" if profile else "Create Automation"
        )
        self.resize(590, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(text("Automatic Posting · future files only", "Heading"))
        self.steps = QTabWidget()
        self.steps.setAccessibleName("Automation setup steps")
        pages = []
        for name in STEPS:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            body = QVBoxLayout(content)
            body.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(content)
            self.steps.addTab(scroll, name)
            pages.append(body)
        self.progress = text("", "Muted")
        layout.addWidget(self.progress)
        layout.addWidget(self.steps)
        self.name = QLineEdit(profile["name"] if profile else "My Shorts Automation")
        self.folder = QComboBox()
        for folder in controller.folders:
            self.folder.addItem(folder["name"], folder["id"])
            self.folder.setItemData(
                self.folder.count() - 1, folder["path"], Qt.ItemDataRole.ToolTipRole
            )
        if profile:
            self.folder.setCurrentIndex(self.folder.findData(profile["folder_id"]))
        self.add_folder = natural(QPushButton("Choose and Authorize a New Folder…"))
        self.add_folder.clicked.connect(self.choose_folder)
        self.channel = QComboBox()
        self.channel.addItem(
            controller.account.get("title", "No connected channel"),
            controller.account.get("id", ""),
        )
        self.channel.setToolTip(controller.account.get("id", "Connect the intended channel first"))
        self.mode = QComboBox()
        self.mode.addItem("Review Before Publishing (default)", "review")
        self.mode.addItem("Approved Automatic Publishing", "automatic")
        self.template = QLineEdit("{filename} #Shorts")
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(84)
        self.tags = QLineEdit()
        self.kids, self.synthetic = QComboBox(), QComboBox()
        for choice in (self.kids, self.synthetic):
            choice.addItem("Choose explicitly", None)
            choice.addItem("Yes", True)
            choice.addItem("No", False)
        self.frequency = QComboBox()
        self.frequency.addItem("Once per selected day", 0)
        for hour in (2, 3, 4, 1):
            self.frequency.addItem(f"Every {hour} hours", hour)
        self.frequency.addItem("Custom whole hours", -1)
        self.hours = QSpinBox()
        self.hours.setRange(1, 24)
        self.hours.setValue(6)
        self.hours.setEnabled(False)
        self.frequency.currentIndexChanged.connect(
            lambda: self.hours.setEnabled(self.frequency.currentData() == -1)
        )
        self.start, self.end = QTimeEdit(QTime(9, 0)), QTimeEdit(QTime(21, 0))
        for clock in (self.start, self.end):
            clock.setDisplayFormat(time_pattern())
        self.zone = QLineEdit(system_zone())
        self.limit = QSpinBox()
        self.limit.setRange(1, controller.limit)
        self.limit.setValue(controller.limit)
        self.pattern = QLineEdit("*.mp4")
        self.seconds, self.mib = QSpinBox(), QSpinBox()
        self.seconds.setRange(1, 180)
        self.seconds.setValue(180)
        self.mib.setRange(1, 20480)
        self.mib.setValue(2048)
        self.days = [QCheckBox(name[:3]) for name in DAY_NAMES]
        for day, name in zip(self.days, DAY_NAMES, strict=True):
            day.setChecked(True)
            day.setAccessibleName(name)
        self.background = QCheckBox("Also post while ShortsScheduler is closed")
        self.background.setToolTip(
            "Requires Background Publishing to be enabled and started in Settings"
        )
        forms = [QFormLayout() for _ in pages]
        for form in forms:
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        fields = (
            ("Automation name", self.name),
            ("Watched folder", self.folder),
            ("Destination YouTube channel", self.channel),
            ("Approval mode", self.mode),
            ("Title template · only {filename}", self.template),
            ("Description (literal)", self.description),
            ("Tags (comma separated)", self.tags),
            ("Made for Kids", self.kids),
            ("Altered or synthetic content", self.synthetic),
            ("Frequency", self.frequency),
            ("Custom interval (hours)", self.hours),
            ("Start / daily time", self.start),
            ("Inclusive same-day window end", self.end),
            ("IANA time zone", self.zone),
            ("Approved daily automatic claim cap", self.limit),
            ("Eligible filename pattern", self.pattern),
            ("Maximum seconds", self.seconds),
            ("Maximum size (MiB)", self.mib),
        )
        for index, (label, widget) in enumerate(fields):
            page = 0 if index < 4 else 1 if index < 9 else 2 if index < 15 else 3
            forms[page].addRow(label, widget)
            widget.setAccessibleName(label)
        # Directly under the folder picker it adds to.
        forms[0].insertRow(2, self.add_folder)
        for body, form in zip(pages, forms, strict=True):
            body.addLayout(form)
        body = pages[2]
        self.day_preset = QComboBox()
        self.day_preset.addItems(["Every day", "Weekdays", "Weekends", "Custom days"])
        self.day_preset.currentIndexChanged.connect(self.set_days)
        body.addWidget(self.day_preset)
        days_box = Section("Posting days")
        days_box.body.addWidget(day_row(list(self.days)))
        body.addWidget(days_box)
        pages[0].addWidget(self.background)
        body = pages[1]
        body.addWidget(
            text(
                "PUBLIC only for this automation. Manual Private/Unlisted preferences remain in Videos. Category remains People & Blogs. No AI metadata. No guessed declarations.",
                "Muted",
            )
        )
        self.sample = QLineEdit()
        self.sample.setReadOnly(True)
        self.sample.setVisible(False)  # Shown once a sample video is chosen.
        sample_button = QPushButton("Select Actual Video for Title Preview…")
        sample_button.clicked.connect(self.choose_sample)
        body.addWidget(sample_button)
        body.addWidget(self.sample)
        self.preview = text("Choose a real local video to preview its generated title.")
        body.addWidget(self.preview)
        self.template.textChanged.connect(self.show_title)
        pages[0].addWidget(
            text(
                "Videos already in the folder are never posted. Invalid videos are held for your review, never silently corrected or truncated.",
                "Muted",
            )
        )
        # Errors only; hidden while empty so every step keeps its full height.
        self.feedback = text("")
        self.feedback.setVisible(False)
        layout.addWidget(self.feedback)
        self.review = QPushButton("Review and Enable…")
        self.review.setToolTip("Check every setting, then approve this exact automation scope")
        self.review.clicked.connect(lambda: controller.start("preview", self.values()))
        buttons = QHBoxLayout()
        self.back = QPushButton("Back")
        self.next = QPushButton("Next")
        self.back.clicked.connect(lambda: self.steps.setCurrentIndex(self.steps.currentIndex() - 1))
        self.next.clicked.connect(lambda: self.steps.setCurrentIndex(self.steps.currentIndex() + 1))
        self.steps.currentChanged.connect(self.step_changed)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        for button in (cancel, self.back, self.next, self.review):
            button.setAutoDefault(False)
            if button is not cancel:
                buttons.addWidget(button)
        layout.addLayout(buttons)
        self.step_changed()
        if profile:
            s = profile["settings"]
            for field, key in ((self.template, "title_template"), (self.pattern, "pattern")):
                field.setText(s[key])
            self.description.setPlainText(s["description"])
            self.tags.setText(", ".join(s["tags"]))
            self.mode.setCurrentIndex(self.mode.findData(s["mode"]))
            self.kids.setCurrentIndex(self.kids.findData(s["made_for_kids"]))
            self.synthetic.setCurrentIndex(self.synthetic.findData(s["synthetic"]))
            self.zone.setText(s["rule"]["zone"])
            self.start.setTime(QTime.fromString(s["rule"]["start"], "HH:mm"))
            self.end.setTime(QTime.fromString(s["rule"]["end"], "HH:mm"))
            index = self.frequency.findData(s["rule"]["hours"])
            self.frequency.setCurrentIndex(index if index >= 0 else self.frequency.findData(-1))
            self.hours.setValue(s["rule"]["hours"] or 1)
            self.seconds.setValue(s["max_seconds"])
            self.mib.setValue(s["max_bytes"] // 1024**2)
            self.limit.setValue(s["limit"])
            self.background.setChecked(s["background"])
            for i, day in enumerate(self.days):
                day.setChecked(i in s["rule"]["days"])
        for line in (self.name, self.template, self.tags, self.zone, self.pattern):
            line.textChanged.connect(self.edited)
        self.description.textChanged.connect(self.edited)
        for combo in (
            self.folder,
            self.mode,
            self.channel,
            self.kids,
            self.synthetic,
            self.frequency,
        ):
            combo.currentIndexChanged.connect(self.edited)
        for spin in (self.hours, self.limit, self.seconds, self.mib):
            spin.valueChanged.connect(self.edited)
        for clock in (self.start, self.end):
            clock.timeChanged.connect(self.edited)
        for check in (*self.days, self.background):
            check.toggled.connect(self.edited)

    def step_changed(self) -> None:
        index, last = self.steps.currentIndex(), self.steps.count() - 1
        self.back.setEnabled(index > 0)
        self.next.setEnabled(index < last)
        self.progress.setText(
            f"Step {index + 1} of {last + 1}: {STEPS[index].split(' · ')[1]}"
            + (
                f" · Next: {STEPS[index + 1].split(' · ')[1]}"
                if index < last
                else " · Last step: review every setting, then approve"
            )
        )
        # The forward action is the visually primary one; Review stays available throughout.
        for button, emphasized in ((self.next, index < last), (self.review, index == last)):
            button.setObjectName("Primary" if emphasized else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def set_days(self, index: int) -> None:
        if index < 3:
            for i, day in enumerate(self.days):
                day.setChecked(index == 0 or (index == 1 and i < 5) or (index == 2 and i >= 5))

    def edited(self) -> None:
        self.dirty = True

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose automation source folder")
        if path:
            self.controller.start("folder_preview", path)

    def choose_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Preview a real video's filename",
            "",
            "Videos (*.mp4 *.mov *.mkv *.webm *.m4v *.avi)",
        )
        if path:
            self.sample.setText(path)
            self.sample.setVisible(True)
            self.show_title()

    def show_title(self) -> None:
        try:
            self.preview.setText(
                title_for(self.sample.text(), self.template.text())
                if self.sample.text()
                else "Select an actual video first."
            )
        except Exception as exc:
            self.preview.setText(str(exc))

    def values(self) -> dict[str, Any]:
        hours = self.frequency.currentData()
        return dict(
            folder_id=self.folder.currentData(),
            channel=self.channel.currentData(),
            name=self.name.text(),
            settings=dict(
                mode=self.mode.currentData(),
                title_template=self.template.text(),
                description=self.description.toPlainText(),
                tags=[t.strip() for t in self.tags.text().split(",") if t.strip()],
                made_for_kids=self.kids.currentData(),
                synthetic=self.synthetic.currentData(),
                rule=Rule(
                    self.zone.text().strip(),
                    tuple(i for i, d in enumerate(self.days) if d.isChecked()),
                    self.start.time().toString("HH:mm"),
                    self.end.time().toString("HH:mm"),
                    self.hours.value() if hours == -1 else hours,
                ).encode(),
                limit=self.limit.value(),
                background=self.background.isChecked(),
                pattern=self.pattern.text(),
                max_seconds=self.seconds.value(),
                max_bytes=self.mib.value() * 1024**2,
            ),
            identity=self.profile["id"] if self.profile else "",
            revision=self.profile["revision"] if self.profile else 0,
        )

    def reject(self) -> None:
        if (
            self.dirty
            and QMessageBox.question(
                self,
                "Discard unapproved changes?",
                "Discard this automation draft? Existing disabled approval stays disabled.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Discard
        ):
            return
        if self.controller.worker:
            self.controller.worker.cancel.set()
        super().reject()


class AutomationWorker(QThread):
    result = Signal(object)

    def __init__(self, controller: AutomationController, action: str, argument: Any):
        super().__init__(controller)
        self.service, self.scanner = controller.service, controller.window.watching.service
        self.action, self.argument = action, argument
        self.cancel = Event()

    def run(self) -> None:
        try:
            result: dict[str, Any] = {}
            if self.action == "load" and not self.service.library.path.exists():
                # No library yet: nothing to show, and reading must not create one.
                self.result.emit(
                    {
                        "state": {"profiles": [], "decisions": []},
                        "recurring": {"items": [], "plans": [], "control": {"daily_limit": 3}},
                        "folders": [],
                        "files": {},
                        "account": {},
                    }
                )
                return
            self.service.library.initialize()
            if self.action == "folder_preview":
                result["folder_proposal"] = self.scanner.preview(self.argument, self.cancel)
            elif self.action == "folder_add":
                result["folder_id"] = self.scanner.add(self.argument[0], **self.argument[1])
            elif self.action == "preview":
                result["proposal"] = self.service.preview(**self.argument)
            elif self.action == "approve":
                result["approved"] = self.service.approve(
                    self.argument, self.cancel, consent=True, declarations=True, restrictions=True
                )
            elif self.action == "control":
                self.service.control(*self.argument)
            result["state"] = self.service.state()
            result["recurring"] = self.service.recurring.state()
            result["folders"] = self.scanner.folders()
            result["files"] = {f["id"]: self.scanner.files(f["id"]) for f in result["folders"]}
            from contextlib import closing

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


def headline(p: dict[str, Any], watching: bool, global_paused: bool, service: str | None) -> str:
    """One honest answer to "will this automation post?" — never "active" while blocked."""
    if p["revoked"]:
        return "Needs approval again · choose Edit… to review and re-approve"
    if not p["enabled"]:
        return "Paused · new videos will not be queued or posted"
    if global_paused:
        return "Not posting · automatic publishing is paused for the whole app (Turn On in the sidebar)"
    if not watching:
        return "Not posting · the watched folder is paused or unavailable"
    if p["settings"]["mode"] == "review":
        return "Active · new videos wait for your review before they are queued"
    if p["settings"]["background"] and service == "Running":
        return "Active · posts on schedule, including while ShortsScheduler is closed"
    return "Active · posts on schedule while ShortsScheduler is open"


def runs_when(p: dict[str, Any], service: str | None) -> str:
    state = f"Background service: {service or 'not checked'}"
    if not p["settings"]["background"]:
        return f"Posts only while ShortsScheduler is open · {state}"
    if service == "Running":
        return f"Also posts while ShortsScheduler is closed · {state}"
    return f"Background posting allowed, but it only happens once the service runs (Settings) · {state}"


def section_head(glyph: str, title: str, detail: str, action: QWidget | QHBoxLayout) -> QVBoxLayout:
    """A page section's title, explanation and its one start button, above its cards."""
    head = QVBoxLayout()
    head.setSpacing(4)
    line = QHBoxLayout()
    line.setSpacing(10)
    line.addWidget(image(pixmap(glyph, 22, theme.ACCENT_TEXT)))
    line.addWidget(text(title, "Heading"), 1)
    if isinstance(action, QWidget):
        line.addWidget(action)
    else:
        line.addLayout(action)
    head.addLayout(line)
    head.addWidget(text(detail, "Muted"))
    return head


class AutomationController(QObject):
    idle = Signal()

    def __init__(self, service: AutomationService, window: MainWindow):
        super().__init__(window)
        self.service, self.window = service, window
        self.worker: AutomationWorker | None = None
        # The editor shown for New/Edit; the window swaps in the all-platform AutoUploadEditor.
        self.editor: Any = None
        self.editor_factory: Callable[[AutomationController, AutoUpload | None], Any] = (
            lambda controller, group: ProfileEditor(controller, group.youtube if group else None)
        )
        self.closing = False
        self.pending: dict[str, Any] = {}
        self.queued: tuple[str, Any] | None = None  # a click that arrived during a refresh
        self.loaded: dict[str, Any] = {}  # the last load, so an identical one isn't redrawn
        self.data: dict[str, Any] = {"profiles": [], "decisions": []}
        self.recurring: dict[str, Any] = {"items": [], "plans": [], "control": {}}
        self.folders: list[dict[str, Any]] = []
        self.files: dict[str, list[dict[str, Any]]] = {}
        self.account: dict[str, Any] = {}
        self.limit = 3
        self.views: list[CardView] = []  # what the cards show now; redrawn only when it changes
        self.page = QScrollArea()
        self.page.setObjectName("automation")
        self.page.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self.body = body
        body.setContentsMargins(32, 32, 32, 32)
        body.setSpacing(18)
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(4)
        names.addWidget(text("Auto Upload", "Title"))
        names.addWidget(text("Videos upload by themselves at the times you choose.", "Muted"))
        top.addLayout(names, 1)
        body.addLayout(top)
        # Two plain sections, each with one button: videos picked one by one, then folders
        # that upload new videos by themselves. The window places the queue cards and button.
        self.queue_new_slot = QHBoxLayout()
        body.addLayout(
            section_head(
                "videos",
                "Individual videos",
                "Upload one video now or at a time you pick, or choose several from your library "
                "to upload one at a time, in order, at the times you choose.",
                self.queue_new_slot,
            )
        )
        self.queue_slot = QVBoxLayout()
        body.addLayout(self.queue_slot)
        body.addSpacing(10)
        self.new = icon_button("Watch a Folder…", "folder", role="Primary")
        self.new.setAccessibleName("New Auto Upload…")
        self.new.clicked.connect(lambda: self.start("open"))
        body.addLayout(
            section_head(
                "folder",
                "Watched folders",
                "Put new videos in a watched folder and each one uploads by itself to the "
                "platforms you pick. Videos already in the folder are left alone.",
                self.new,
            )
        )
        self.folders_empty = text(
            "No watched folders yet. Choose Watch a Folder to pick a folder, write the details "
            "once, and choose the times and platforms.",
            "Notice",
        )
        body.addWidget(self.folders_empty)
        self.feedback = text("", "Muted")
        self.feedback.hide()
        body.addWidget(self.feedback)
        # One card per auto upload, for all of its platforms.
        self.cards = QVBoxLayout()
        self.cards.setSpacing(12)
        body.addLayout(self.cards)
        self.folders_slot = QVBoxLayout()
        body.addLayout(self.folders_slot)
        body.addStretch()
        self.page.setWidget(content)
        self.summary = text("Automatic Posting · no profile loaded.", "Muted")
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.activate)
        QTimer.singleShot(0, self.activate)

    def pause_all(self) -> None:
        self.window.automatic.control("control", (True, None))

    def groups(self) -> list[AutoUpload]:
        return auto_uploads(self.data["profiles"], getattr(self.window, "socials", ()))

    def group(self, key: Any) -> AutoUpload | None:
        return next((g for g in self.groups() if list(g.key) == list(key or ())), None)

    def say(self, message: str) -> None:
        self.feedback.setText(message)
        self.feedback.setVisible(bool(message))

    def activate(self) -> None:
        if not self.closing:
            self.timer.start()
            if not self.editor:
                self.start("load")
            if self.window.stack.currentWidget() is self.page:
                # Read-only status query, so "can it post while closed?" is not a guess.
                self.window.background.start("status")

    def start(self, action: str, argument: Any = None) -> None:
        if self.closing:
            return
        if self.worker:
            if action != "load":
                self.queued = (action, argument)  # runs as soon as the current work ends
            return
        self.pending = {}
        self.worker = AutomationWorker(self, action, argument)
        self.worker.result.connect(self.receive)
        self.worker.finished.connect(self.finished)
        if action != "load":
            # Regular refreshes leave the buttons alone, so nothing flickers every few seconds.
            self.new.setEnabled(False)
            if self.editor:
                self.editor.review.setEnabled(False)
                self.editor.add_folder.setEnabled(False)
        self.worker.start()

    def receive(self, result: dict[str, Any]) -> None:
        self.pending = result

    def finished(self) -> None:
        assert self.worker
        action, argument = self.worker.action, self.worker.argument
        self.worker.deleteLater()
        self.worker = None
        result = self.pending
        self.pending = {}
        if self.closing:
            self.idle.emit()
            return
        self.new.setEnabled(True)
        if self.editor:
            self.editor.review.setEnabled(True)
            self.editor.add_folder.setEnabled(True)
        if "error" in result:
            self.say(result["error"])
            if self.editor:
                self.editor.feedback.setText(result["error"])
                self.editor.feedback.setVisible(True)
        unchanged = action == "load" and "state" in result and result == self.loaded
        if "state" in result:
            self.loaded = result
        if "state" in result and not unchanged:
            self.data, self.recurring, self.folders, self.account = (
                result["state"],
                result["recurring"],
                result["folders"],
                result["account"],
            )
            self.files = result["files"]
            self.limit = self.recurring["control"]["daily_limit"]
            self.render()
        if "folder_proposal" in result and self.editor:
            dialog = FolderConsent(result["folder_proposal"], self.editor)
            # Automation starts with future files only; local import choices remain explicit.
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.consent.isChecked():
                self.start(
                    "folder_add",
                    (
                        result["folder_proposal"],
                        dict(
                            consent=True,
                            existing=dialog.existing.currentData(),
                            background=dialog.background.isChecked(),
                            quiet=dialog.quiet.value(),
                        ),
                    ),
                )
            dialog.deleteLater()
        if "folder_id" in result and self.editor:
            self.editor.folder.clear()
            for folder in self.folders:
                self.editor.folder.addItem(folder["name"], folder["id"])
            self.editor.folder.setCurrentIndex(self.editor.folder.findData(result["folder_id"]))
            self.window.watching.activate()
        if "proposal" in result and self.editor:
            confirmation = ProfileConsent(result["proposal"], self.editor)
            if confirmation.exec() == QDialog.DialogCode.Accepted and all(
                b.isChecked()
                for b in (confirmation.scope, confirmation.declarations, confirmation.restrictions)
            ):
                self.start("approve", result["proposal"])
            confirmation.deleteLater()
        if "approved" in result and self.editor:
            self.editor.dirty = False
            self.say(
                "Auto upload saved. New videos in the folder upload on schedule once Auto "
                "uploading is on in the sidebar."
            )
            if hasattr(self.editor, "approved"):
                self.editor.approved()  # other platforms ask for their own approval next
            else:
                self.editor.accept()
        if action == "open" and "state" in result and not self.editor:
            # A new auto upload, or (with its folder and name) an existing one to edit.
            self.editor = self.editor_factory(self, self.group(argument) if argument else None)
            self.editor.exec()
            self.editor.deleteLater()
            self.editor = None
        self.idle.emit()
        if self.queued and not self.worker:
            action, argument = self.queued
            self.queued = None
            self.start(action, argument)

    def render(self, *_: Any) -> None:
        enabled = sum(
            bool(p["enabled"] and not p["revoked"] and not p["removed"])
            for p in self.data["profiles"]
        )
        self.summary.setText(
            f"Automatic Posting · {enabled} enabled automation{'s' if enabled != 1 else ''}"
            + (
                " · global publishing paused"
                if self.recurring["control"].get("paused", True)
                else ""
            )
        )
        socials = {s.key: s for s in getattr(self.window, "socials", ())}
        paused = self.recurring["control"].get("paused", True)
        service = self.window.background.service_status
        views = [
            describe(
                group,
                folders=self.known_folders(),
                recurring=self.recurring,
                decisions=self.data["decisions"],
                socials=socials,
                youtube_headline=lambda p, watching: headline(p, watching, paused, service),
            )
            for group in self.groups()
        ]
        self.folders_empty.setVisible(not views)
        if views == self.views:
            return  # the same cards: nothing is rebuilt, so nothing flickers
        self.views = views
        while self.cards.count():
            item = self.cards.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.deleteLater()
        for view in views:
            self.cards.addWidget(build_card(view, self.act))

    def known_folders(self) -> list[dict[str, Any]]:
        """The folders this page loaded, or (until it does) those the Buffer platforms loaded."""
        socials = getattr(self.window, "socials", ())
        folders = {f["id"]: f for s in socials for f in s.data.get("folders", [])}
        folders.update({f["id"]: f for f in self.folders})
        return list(folders.values())

    def act(self, action: str, key: tuple[str, str]) -> None:
        """Edit, pause, resume or delete one auto upload on every platform it uses."""
        group = self.group(key)
        if not group:
            return
        if action == "edit":
            self.start("open", group.key)
            return
        names = " and ".join(PLATFORM_NAMES[p] for p in group.platforms)
        if action == "delete":
            others = [
                g for g in self.groups() if g.folder_id == group.folder_id and g.key != group.key
            ]
            answer = QMessageBox.question(
                self.window,
                "Delete this auto upload?",
                f"“{group.name}” stops uploading new videos to {names}"
                + ("" if others else ", and its folder is no longer watched")
                + ".\n\nVideos already uploaded stay on "
                + names
                + ". Nothing is deleted from your channels or accounts, and your video files "
                "stay where they are. A post already scheduled in Buffer still goes out unless "
                "you cancel it under Schedule.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        control = {"pause": "pause", "resume": "resume", "delete": "remove"}[action]
        if p := group.youtube:
            self.start("control", (p["id"], p["revision"], control))
        socials = {s.key: s for s in getattr(self.window, "socials", ())}
        for key_, p in group.buffer.items():
            socials[key_].control_automation(p["id"], control)
        if action == "delete":
            folder = next((f for f in self.known_folders() if f["id"] == group.folder_id), None)
            if folder and not others:
                self.window.watching.start("control", (folder["id"], folder["revision"], "remove"))
        self.say(
            {
                "pause": f"“{group.name}” is paused. Nothing new is sent to {names}.",
                "resume": f"“{group.name}” is on again.",
                "delete": f"“{group.name}” was deleted. Videos already uploaded stay on {names}.",
            }[action]
        )

    def request_close(self) -> bool:
        self.timer.stop()
        self.closing = True
        if self.worker:
            self.worker.cancel.set()
            return False
        return True

    def stop_for_quit(self) -> None:
        self.timer.stop()
        self.closing = True
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait()
