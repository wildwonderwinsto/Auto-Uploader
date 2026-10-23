"""One auto upload for every platform: a folder, one set of details, one schedule.

Each platform still gets its own explicit approval: YouTube through AutomationController's
consent flow, then TikTok, Instagram and Facebook, in turn, through their SocialControllers. This
dialog only gathers the choices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..automation import title_for
from ..cadence import Rule
from ..social import caption_from
from .components import (
    card,
    day_row,
    icon_button,
    muted,
    platform_choice,
    set_choice,
    step_page,
    step_pills,
)
from .formatting import friendly, handle, system_zone, time_pattern
from .icons import PLATFORM_NAMES, image, logo
from .recurring import DAY_NAMES, rule_label

if TYPE_CHECKING:
    from .automation import AutomationController
    from .social import SocialController

STEPS = ("Folder", "Details", "When", "Where")


page = step_page  # the shared step-by-step page


def form() -> QFormLayout:
    layout = QFormLayout()
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    layout.setVerticalSpacing(8)
    return layout


def platform_box(platform: str, note: str) -> tuple[QWidget, QVBoxLayout]:
    frame, body = card(16, 10, "Row")
    top = QHBoxLayout()
    top.setSpacing(10)
    top.addWidget(image(logo(platform, 20)))
    name = QLabel(PLATFORM_NAMES[platform])
    name.setObjectName("Subheading")
    top.addWidget(name)
    top.addWidget(muted(note, "Subtle"), 1)
    body.addLayout(top)
    return frame, body


class AutoUploadEditor(QDialog):
    """Gathers one auto upload; `AutomationController` drives its YouTube approval."""

    def __init__(
        self,
        controller: AutomationController,
        tiktok: SocialController,
        profile: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(controller.window)
        self.controller, self.tiktok, self.profile = controller, tiktok, profile
        self.socials: tuple[SocialController, ...] = getattr(
            controller.window, "socials", (tiktok,)
        )
        self.dirty = False
        self.approved_youtube = False
        self.approved_buffer: set[str] = set()  # Buffer platforms turned on from this dialog
        self.waiting: list[SocialController] = []  # Buffer platforms still to approve
        self.setWindowTitle("Edit Auto Upload" if profile else "New Auto Upload")
        self.resize(720, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        top = QVBoxLayout()
        top.setContentsMargins(24, 22, 24, 10)
        top.setSpacing(10)
        top.addWidget(muted("Edit auto upload" if profile else "New auto upload", "Title"))
        top.addWidget(
            muted(
                "New videos you add to a folder upload on your schedule, with the same title, "
                "description and tags on every platform you pick.",
                "Muted",
            )
        )
        steps, self.step_buttons = step_pills(STEPS, lambda i: self.go(i))
        top.addLayout(steps)
        layout.addLayout(top)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)

        # 1 · Folder ----------------------------------------------------------------------------
        folder_page, body = page(
            "Which folder should be watched?",
            "Only videos you add after turning this on are uploaded. Videos already in the "
            "folder are left alone. Your original files are never changed or moved.",
        )
        self.folder = QComboBox()
        self.folder.setAccessibleName("Watched folder")
        self.add_folder = icon_button("Add a Folder…", "folder")
        self.add_folder.clicked.connect(self.choose_folder)
        line = QHBoxLayout()
        line.addWidget(self.folder, 1)
        line.addWidget(self.add_folder)
        body.addLayout(line)
        self.name = QLineEdit(profile["name"] if profile else "My auto upload")
        self.pattern = QLineEdit("*.mp4")
        self.seconds, self.mib = QSpinBox(), QSpinBox()
        self.seconds.setRange(1, 180)
        self.seconds.setValue(180)
        self.seconds.setSuffix(" seconds")
        self.mib.setRange(1, 20480)
        self.mib.setValue(2048)
        self.mib.setSuffix(" MB")
        files = form()
        files.addRow("Name", self.name)
        files.addRow("Which files (for example *.mp4)", self.pattern)
        files.addRow("Longest video", self.seconds)
        files.addRow("Largest file", self.mib)
        body.addLayout(files)
        body.addStretch()
        self.pages.addWidget(folder_page)

        # 2 · Details ---------------------------------------------------------------------------
        details_page, body = page(
            "Details for every video",
            "Used on every platform. {filename} is replaced with each video's file name.",
        )
        self.template = QLineEdit("{filename}")
        self.template.setAccessibleName("Title template")
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(84)
        self.description.setPlaceholderText("Optional, the same for every video")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("Optional, separated by commas")
        shared = form()
        shared.addRow("Title", self.template)
        self.example = muted("", "Subtle")
        shared.addRow("", self.example)
        shared.addRow("Description", self.description)
        shared.addRow("Tags", self.tags)
        body.addLayout(shared)
        self.mode = QComboBox()
        self.mode.addItem("Ask me to check each video first", "review")
        self.mode.addItem("Upload automatically", "automatic")
        self.kids, self.synthetic = QComboBox(), QComboBox()
        for choice in (self.kids, self.synthetic):
            choice.addItem("Choose one", None)
            choice.addItem("Yes", True)
            choice.addItem("No", False)
        youtube, youtube_body = platform_box("youtube", "Only used on YouTube")
        settings = form()
        settings.addRow("Before each upload", self.mode)
        settings.addRow("Made for kids?", self.kids)
        settings.addRow("Realistic altered or AI-generated content?", self.synthetic)
        youtube_body.addLayout(settings)
        youtube_body.addWidget(
            muted(
                "Uploads are Public. The 18+ age restriction can't be set by apps; videos that "
                "need it should be uploaded by hand.",
                "Subtle",
            )
        )
        body.addWidget(youtube)
        self.ai, self.instagram_ai = QComboBox(), QComboBox()
        for choice in (self.ai, self.instagram_ai):
            choice.addItem("Choose one", None)
            choice.addItem("Yes, videos contain AI-generated content", True)
            choice.addItem("No AI-generated content", False)
        self.ai_choices = {"tiktok": self.ai, "instagram": self.instagram_ai}
        notes = {
            "tiktok": "The caption is your title, description and first 5 tags as hashtags. "
            "TikTok posts are public; made-for-kids, 18+, comments, Duet and Stitch can't be set "
            "through Buffer.",
            "instagram": "Posted as public Reels that also appear on your profile grid, with the "
            "same caption as TikTok. Needs an Instagram Business or Creator account.",
            "facebook": "Posted as public Reels on your Facebook Page; every tag becomes a "
            "hashtag. Facebook Reels must be 9:16 and 3 to 90 seconds long.",
        }
        for key, note in notes.items():
            group, group_body = platform_box(key, f"Only used on {PLATFORM_NAMES[key]}")
            if key in self.ai_choices:
                ai_form = form()
                ai_form.addRow("AI-generated content label", self.ai_choices[key])
                group_body.addLayout(ai_form)
            group_body.addWidget(muted(note, "Subtle"))
            body.addWidget(group)
        body.addStretch()
        self.pages.addWidget(details_page)

        # 3 · When ------------------------------------------------------------------------------
        when_page, body = page(
            "When should videos go out?",
            "New videos wait their turn and upload at the next posting time, in the order they "
            "arrived.",
        )
        self.days_preset = QComboBox()
        self.days_preset.addItems(["Every day", "Weekdays", "Weekends", "Custom days"])
        self.days = [QCheckBox(name[:3]) for name in DAY_NAMES]
        for day, name in zip(self.days, DAY_NAMES, strict=True):
            day.setChecked(True)
            day.setAccessibleName(name)
        self.days_preset.currentIndexChanged.connect(self.preset_days)
        body.addWidget(self.days_preset)
        body.addWidget(day_row(list(self.days)))
        self.frequency = QComboBox()
        self.frequency.addItem("Once a day", 0)
        for hours in (1, 2, 3, 4, 6):
            self.frequency.addItem(f"Every {hours} hour{'s' if hours > 1 else ''}", hours)
        self.start, self.end = QTimeEdit(QTime(9, 0)), QTimeEdit(QTime(21, 0))
        for clock in (self.start, self.end):
            clock.setDisplayFormat(time_pattern())
        timing = QHBoxLayout()
        timing.setSpacing(8)
        timing.addWidget(self.frequency, 2)
        self.at = QLabel("at")
        timing.addWidget(self.at)
        timing.addWidget(self.start, 1)
        self.until = QLabel("until")
        timing.addWidget(self.until)
        timing.addWidget(self.end, 1)
        body.addLayout(timing)
        self.zone = QLineEdit(system_zone())
        options = form()
        options.addRow("Time zone", self.zone)
        body.addLayout(options)
        self.background = QCheckBox("Keep uploading while ShortsScheduler is closed")
        self.background.setToolTip("Needs Background Uploading turned on in Settings")
        body.addWidget(self.background)
        self.summary = muted("", "Notice")
        body.addWidget(self.summary)
        body.addStretch()
        self.pages.addWidget(when_page)

        # 4 · Where -----------------------------------------------------------------------------
        where_page, body = page(
            "Where should videos upload?",
            "Pick one or more. You'll confirm each platform on its own approval screen.",
        )
        account = controller.account
        self.channel = QComboBox(self)  # the approval flow reads the destination channel from here
        self.channel.addItem(account.get("title", "No connected channel"), account.get("id", ""))
        self.channel.hide()
        self.targets: dict[str, QCheckBox] = {}
        choices: list[tuple[str, bool, str, str]] = [
            (
                "youtube",
                bool(account.get("id")),
                account.get("title", ""),
                "Connect YouTube and allow uploads in Accounts first.",
            )
        ]
        for social in self.socials:
            connection = social.data.get("connection") or {}
            connected = connection.get("status") == "Connected"
            choices.append(
                (
                    social.key,
                    connected and bool(social.data.get("hosting")),
                    handle(social.key, connection) if connection else "",
                    f"Connect {social.name} in Accounts and set up video hosting in Settings first."
                    if not connected
                    else "Set up video hosting in Settings first.",
                )
            )
        choices.append(("snapchat", False, "", "Coming soon"))
        for platform, ready, who, missing in choices:
            frame, check, detail, badge, _line = platform_choice(platform)
            set_choice(check, detail, badge, ready, who if ready else missing)
            check.setChecked(ready and (platform == "youtube" or not profile))
            check.toggled.connect(self.edited)
            body.addWidget(frame)
            if platform != "snapchat":
                self.targets[platform] = check
        body.addStretch()
        self.pages.addWidget(where_page)

        self.feedback = muted("", "Error")
        self.feedback.setVisible(False)
        self.feedback.setContentsMargins(24, 0, 24, 0)
        layout.addWidget(self.feedback)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 10, 24, 18)
        cancel = icon_button("Cancel", "x")
        cancel.clicked.connect(self.reject)
        self.back = icon_button("Back", "chevron-left")
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next = icon_button("Next", "chevron-right", role="Primary")
        self.next.clicked.connect(lambda: self.go(self.pages.currentIndex() + 1))
        self.review = icon_button("Review and Turn On…", "check", role="Primary")
        self.review.clicked.connect(self.start_review)
        footer.addWidget(cancel)
        footer.addStretch()
        for button in (self.back, self.next, self.review):
            button.setAutoDefault(False)
            footer.addWidget(button)
        layout.addLayout(footer)

        self.fill_folders(profile["folder_id"] if profile else None)
        if profile:
            self.load(profile)
        for line_edit in (self.name, self.template, self.tags, self.zone, self.pattern):
            line_edit.textChanged.connect(self.edited)
        self.description.textChanged.connect(self.edited)
        for combo in (
            self.folder,
            self.mode,
            self.kids,
            self.synthetic,
            self.ai,
            self.instagram_ai,
            self.frequency,
        ):
            combo.currentIndexChanged.connect(self.edited)
        for spin in (self.seconds, self.mib):
            spin.valueChanged.connect(self.edited)
        for clock in (self.start, self.end):
            clock.timeChanged.connect(self.edited)
        for check in (*self.days, self.background):
            check.toggled.connect(self.edited)
        self.go(0)

    # Navigation ----------------------------------------------------------------------------------

    def go(self, index: int) -> None:
        index = max(0, min(index, self.pages.count() - 1))
        self.pages.setCurrentIndex(index)
        for position, step in enumerate(self.step_buttons):
            step.setChecked(position == index)
        last = index == self.pages.count() - 1
        self.back.setEnabled(index > 0)
        self.next.setVisible(not last)
        self.review.setVisible(last)
        self.describe()

    def edited(self) -> None:
        self.dirty = True
        self.describe()

    def preset_days(self, index: int) -> None:
        if index < 3:
            for i, day in enumerate(self.days):
                day.setChecked(index == 0 or (index == 1 and i < 5) or (index == 2 and i >= 5))

    def describe(self) -> None:
        daily = self.frequency.currentData() == 0
        self.at.setText("at" if daily else "from")
        self.until.setVisible(not daily)
        self.end.setVisible(not daily)
        try:
            self.example.setText("Example: " + title_for("my-new-video.mp4", self.template.text()))
        except Exception as exc:  # noqa: BLE001 - the reason is the useful part
            self.example.setText(str(exc))
        rule = self.rule()
        try:
            rule.validate()
            upcoming = rule.next(self.controller.service.clock(), 3)
            self.summary.setText(
                rule_label(rule)
                + "\nNext: "
                + " · ".join(friendly(t.isoformat(), rule.zone) for t in upcoming)
            )
        except Exception as exc:  # noqa: BLE001
            self.summary.setText(str(exc))

    def fill_folders(self, selected: str | None = None) -> None:
        current = selected or self.folder.currentData()
        self.folder.blockSignals(True)
        self.folder.clear()
        for folder in self.controller.folders:
            if not folder.get("removed"):
                self.folder.addItem(folder["name"], folder["id"])
                self.folder.setItemData(
                    self.folder.count() - 1, folder["path"], Qt.ItemDataRole.ToolTipRole
                )
        self.folder.setCurrentIndex(max(0, self.folder.findData(current)))
        self.folder.blockSignals(False)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to watch for new videos")
        if path:
            self.controller.start("folder_preview", path)

    # Values --------------------------------------------------------------------------------------

    def rule(self) -> Rule:
        return Rule(
            self.zone.text().strip(),
            tuple(i for i, d in enumerate(self.days) if d.isChecked()),
            self.start.time().toString("HH:mm"),
            self.end.time().toString("HH:mm"),
            int(self.frequency.currentData()),
        )

    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.text().split(",") if t.strip()]

    def values(self) -> dict[str, Any]:
        """The YouTube profile, in the shape AutomationService.preview expects."""
        return dict(
            folder_id=self.folder.currentData(),
            channel=self.channel.currentData(),
            name=self.name.text(),
            settings=dict(
                mode=self.mode.currentData(),
                title_template=self.template.text(),
                description=self.description.toPlainText(),
                tags=self.tag_list(),
                made_for_kids=self.kids.currentData(),
                synthetic=self.synthetic.currentData(),
                rule=self.rule().encode(),
                limit=self.controller.limit,  # no limit of our own; YouTube's is 100
                background=self.background.isChecked(),
                pattern=self.pattern.text(),
                max_seconds=self.seconds.value(),
                max_bytes=self.mib.value() * 1024**2,
            ),
            identity=self.profile["id"] if self.profile else "",
            revision=self.profile["revision"] if self.profile else 0,
        )

    def tiktok_values(self) -> dict[str, Any]:
        return self.platform_values(self.tiktok)

    def platform_values(self, social: SocialController) -> dict[str, Any]:
        """The same choices as a Buffer platform's profile; its caption comes from the shared
        details."""
        choice = self.ai_choices.get(social.key)
        return {
            "name": self.name.text(),
            "folder_id": self.folder.currentData(),
            "settings": {
                "caption_template": caption_from(
                    self.template.text(),
                    self.description.toPlainText(),
                    tuple(self.tag_list()),
                    hashtags=social.spec.hashtags_max,
                ),
                "ai_generated": choice.currentData() if choice else False,
                "daily_cap": social.spec.daily_max,  # the platform's own ceiling
                "rule": self.rule().encode(),
                "pattern": self.pattern.text(),
                "background": self.background.isChecked(),
            },
        }

    def load(self, profile: dict[str, Any]) -> None:
        s = profile["settings"]
        self.template.setText(s["title_template"])
        self.pattern.setText(s["pattern"])
        self.description.setPlainText(s["description"])
        self.tags.setText(", ".join(s["tags"]))
        self.mode.setCurrentIndex(self.mode.findData(s["mode"]))
        self.kids.setCurrentIndex(self.kids.findData(s["made_for_kids"]))
        self.synthetic.setCurrentIndex(self.synthetic.findData(s["synthetic"]))
        rule = s["rule"]
        self.zone.setText(rule["zone"])
        self.start.setTime(QTime.fromString(rule["start"], "HH:mm"))
        self.end.setTime(QTime.fromString(rule["end"], "HH:mm"))
        self.frequency.setCurrentIndex(max(0, self.frequency.findData(rule["hours"])))
        for i, day in enumerate(self.days):
            day.setChecked(i in rule["days"])
        self.seconds.setValue(s["max_seconds"])
        self.mib.setValue(s["max_bytes"] // 1024**2)
        self.background.setChecked(s["background"])

    # Approval ------------------------------------------------------------------------------------

    def show_error(self, message: str) -> None:
        self.feedback.setText(message)
        self.feedback.setVisible(bool(message))

    def start_review(self) -> None:
        youtube = self.targets["youtube"].isChecked()
        if not any(check.isChecked() for check in self.targets.values()):
            self.show_error("Pick at least one platform to upload to.")
            return
        if not self.folder.currentData():
            self.go(0)
            self.show_error("Choose or add a folder to watch first.")
            return
        self.show_error("")
        if youtube and not self.approved_youtube:
            self.controller.start("preview", self.values())
        else:
            self.approved()

    def approved(self) -> None:
        """YouTube is approved (or was not chosen); each Buffer platform gets its own approval
        next, in turn."""
        self.approved_youtube = self.targets["youtube"].isChecked()
        self.waiting = [
            s
            for s in self.socials
            if self.targets[s.key].isChecked() and s.key not in self.approved_buffer
        ]
        for social in self.waiting:
            choice = self.ai_choices.get(social.key)
            if choice is not None and choice.currentData() is None:
                self.go(1)
                self.show_error(
                    f"For {social.name}, choose whether these videos contain AI-generated content."
                )
                return
        self.next_platform()

    def next_platform(self) -> None:
        if not self.waiting:
            self.finish()
            return
        self.review.setEnabled(False)
        social = self.waiting[0]
        social.review_automation(self.platform_values(social), self.platform_done, parent=self)

    def platform_done(self, enabled: bool) -> None:
        self.review.setEnabled(True)
        social = self.waiting.pop(0)
        if enabled:
            self.approved_buffer.add(social.key)
            self.next_platform()
            return
        on = (["YouTube"] if self.approved_youtube else []) + [
            s.name for s in self.socials if s.key in self.approved_buffer
        ]
        off = [social.name] + [s.name for s in self.waiting]
        self.waiting = []
        if on:
            self.show_error(
                f"{' and '.join(on)} {'is' if len(on) == 1 else 'are'} on. "
                f"{' and '.join(off)} {'was' if len(off) == 1 else 'were'} not turned on; "
                "you can add " + ("it" if len(off) == 1 else "them") + " with a new auto upload."
            )

    tiktok_done = platform_done

    def finish(self) -> None:
        self.dirty = False
        self.accept()

    def reject(self) -> None:
        if (
            self.dirty
            and not self.approved_youtube
            and not self.approved_buffer
            and QMessageBox.question(
                self,
                "Discard this auto upload?",
                "Close without turning it on? Nothing has been approved.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Discard
        ):
            return
        if self.controller.worker:
            self.controller.worker.cancel.set()
        super().reject()
