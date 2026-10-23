"""Local YouTube draft editor. No database or worker ownership."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QPlainTextEdit,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..preparation import (
    Preparation,
    YouTubeMetadata,
    metadata_errors,
    tag_key,
    tag_length,
    unique_tags,
)
from .components import Section, card, icon_button, pill, restyle
from .icons import PLATFORM_NAMES, image, logo


def labelled(label: QLabel, counter: QLabel) -> QHBoxLayout:
    """A field label with its character counter on the right."""
    line = QHBoxLayout()
    line.setContentsMargins(0, 6, 0, 0)
    line.addWidget(label, 1, Qt.AlignmentFlag.AlignBottom)
    line.addWidget(counter, 1, Qt.AlignmentFlag.AlignBottom)
    return line


def field_row(field: QWidget, action: QWidget) -> QHBoxLayout:
    line = QHBoxLayout()
    line.setSpacing(8)
    line.addWidget(field, 1)
    line.addWidget(action)
    return line


def platform_box(platform: str, note: str) -> tuple[QFrame, QVBoxLayout]:
    """A bordered group for settings that only one platform uses."""
    frame, body = card(16, 10, "Row")
    top = QHBoxLayout()
    top.setSpacing(10)
    top.addWidget(image(logo(platform, 20)))
    name = QLabel(PLATFORM_NAMES[platform])
    name.setObjectName("Subheading")
    top.addWidget(name)
    top.addWidget(caption(note, "Subtle"), 1)
    body.addLayout(top)
    return frame, body


def caption(value: str, style: str = "Muted") -> QLabel:
    label = QLabel(value)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setObjectName(style)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


class MetadataEditor(QWidget):
    changed = Signal()
    layout_changed = Signal()
    save_requested = Signal()
    review_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.preparation: Preparation | None = None
        self.loading = False
        self.tags: tuple[str, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)
        self.save_state = pill("No video selected", "Neutral")
        layout.addWidget(self.save_state)
        layout.addWidget(
            caption("These details are used on every platform you upload this video to.", "Subtle")
        )
        self.title = QLineEdit()
        self.title.setAccessibleName("YouTube title")
        self.title.setPlaceholderText("Give your video a title")
        self.title.setMaxLength(1_000_000)  # Drafts may exceed the platform limit.
        label = caption("Title", "Subheading")
        label.setBuddy(self.title)
        self.title_counter = caption("", "Subtle")
        self.title_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addLayout(labelled(label, self.title_counter))
        self.use_filename = icon_button("Use File Name", "film", role="Ghost")
        self.use_filename.setToolTip("Fill the title from the video's file name")
        layout.addLayout(field_row(self.title, self.use_filename))
        self.description = QPlainTextEdit()
        self.description.setAccessibleName("YouTube description")
        self.description.setPlaceholderText("What is this video about? (optional)")
        self.description.setFixedHeight(110)
        label = caption("Description", "Subheading")
        label.setBuddy(self.description)
        self.description_counter = caption("", "Subtle")
        self.description_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addLayout(labelled(label, self.description_counter))
        layout.addWidget(self.description)
        tags_section = Section("Tags")
        tags_section.heading.hide()
        self.tag_counter = caption("", "Subtle")
        self.tag_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addLayout(labelled(caption("Tags", "Subheading"), self.tag_counter))
        layout.addWidget(tags_section)
        tag_layout = tags_section.body
        self.tag_input = QLineEdit()
        self.tag_input.setAccessibleName("New keyword tag")
        self.tag_input.setPlaceholderText("Type a tag and press Return (optional)")
        self.tag_input.setMaxLength(1_000_000)
        self.add_tag_button = icon_button("Add", "plus")
        self.add_tag_button.clicked.connect(self.add_tag)
        self.tag_input.returnPressed.connect(self.add_tag)
        self.remove_tag_button = icon_button("Remove", "x")
        self.remove_tag_button.setAccessibleName("Remove Selected Tag")
        self.remove_tag_button.setToolTip("Remove the selected tag")
        self.remove_tag_button.clicked.connect(self.remove_tag)
        tags_row = field_row(self.tag_input, self.add_tag_button)
        tags_row.addWidget(self.remove_tag_button)
        tag_layout.addLayout(tags_row)
        self.tag_list = QListWidget()
        self.tag_list.setObjectName("Chips")
        self.tag_list.setAccessibleName("Keyword tags")
        self.tag_list.setFlow(QListView.Flow.LeftToRight)
        self.tag_list.setWrapping(True)
        self.tag_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.tag_list.setSpacing(3)
        self.tag_list.setFixedHeight(46)
        self.tag_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_layout.addWidget(self.tag_list)
        tag_layout.addWidget(
            caption(
                "On TikTok, Instagram and Facebook, your tags become hashtags in the caption "
                "(the first 5 on TikTok and Instagram).",
                "Subtle",
            )
        )

        layout.addSpacing(6)
        layout.addWidget(caption("Platform settings", "Heading"))
        self.kids = QComboBox()
        self.synthetic = QComboBox()
        for combo in (self.kids, self.synthetic):
            combo.addItem("Choose one", None)
            combo.addItem("Yes", True)
            combo.addItem("No", False)
        self.kids.setAccessibleName("Made for Kids")
        self.synthetic.setAccessibleName("Altered or synthetic content disclosure")
        self.privacy = QComboBox()
        for value, shown in (
            ("private", "Private · only you"),
            ("unlisted", "Unlisted · anyone with the link"),
            ("public", "Public · everyone"),
        ):
            self.privacy.addItem(shown, value)
        self.privacy.setAccessibleName("Planned privacy for a future upload")
        youtube, body = platform_box("youtube", "Only used on YouTube")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setVerticalSpacing(8)
        for title, combo in (
            ("Visibility", self.privacy),
            ("Made for kids?", self.kids),
            ("Realistic altered or AI-generated content?", self.synthetic),
        ):
            name = QLabel(title)
            name.setObjectName("Muted")
            form.addRow(name, combo)
        body.addLayout(form)
        body.addWidget(
            caption(
                "Age restriction (18+): this app cannot apply it through YouTube’s upload API. If your video needs an age restriction, upload privately and set the restriction in YouTube Studio before publishing.",
                "Subtle",
            )
        )
        guidance = QLabel(
            '<a href="https://support.google.com/youtube/answer/14328491">How YouTube defines altered or synthetic content</a>'
        )
        guidance.setObjectName("Subtle")
        guidance.setOpenExternalLinks(True)
        guidance.setAccessibleName("Open official YouTube disclosure guidance in browser")
        body.addWidget(guidance)
        layout.addWidget(youtube)
        tiktok, self.tiktok_slot = platform_box("tiktok", "Only used on TikTok")
        self.tiktok_slot.addWidget(
            caption(
                "TikTok posts are public. Privacy, comments, Duet, Stitch and the made-for-kids "
                "and 18+ settings can't be changed through Buffer.",
                "Subtle",
            )
        )
        layout.addWidget(tiktok)
        instagram, self.instagram_slot = platform_box("instagram", "Only used on Instagram")
        self.instagram_slot.addWidget(
            caption(
                "Posted as a public Reel that also appears on your profile grid. Needs an "
                "Instagram Business or Creator account.",
                "Subtle",
            )
        )
        layout.addWidget(instagram)
        facebook, self.facebook_slot = platform_box("facebook", "Only used on Facebook")
        self.facebook_slot.addWidget(
            caption(
                "Posted as a public Reel on your Facebook Page. Facebook Reels must be 9:16 and "
                "3 to 90 seconds long.",
                "Subtle",
            )
        )
        layout.addWidget(facebook)
        self.errors = caption("", "Error")
        layout.addWidget(self.errors)
        self.save_button = icon_button("Save Draft", "check")
        self.save_button.clicked.connect(self.save_requested.emit)
        self.review_link = icon_button("Save and Review", "chevron-right", role="Primary")
        self.review_link.clicked.connect(self.review_requested.emit)
        action_row = QHBoxLayout()
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.review_link)
        action_row.addStretch()
        layout.addLayout(action_row)
        for field in (self.title, self.tag_input):
            field.textChanged.connect(self.on_changed)
        self.description.textChanged.connect(self.on_changed)
        for combo in (self.kids, self.synthetic, self.privacy):
            combo.currentIndexChanged.connect(self.on_changed)

    def value(self) -> YouTubeMetadata:
        tags = self.tags + ((self.tag_input.text(),) if self.tag_input.text().strip() else ())
        return YouTubeMetadata(
            self.title.text(),
            self.description.toPlainText(),
            unique_tags(tags),
            self.kids.currentData(),
            self.synthetic.currentData(),
            self.privacy.currentData(),
        )

    @property
    def dirty(self) -> bool:
        return self.preparation is not None and self.value() != self.preparation.metadata

    def load(self, preparation: Preparation) -> None:
        self.loading = True
        self.preparation = preparation
        data = preparation.metadata
        self.title.setText(data.title)
        self.description.setPlainText(data.description)
        self.tags = data.tags
        self.tag_input.clear()
        self.tag_list.clear()
        self.tag_list.addItems(list(self.tags))
        self.kids.setCurrentIndex(
            0 if data.made_for_kids is None else 1 if data.made_for_kids else 2
        )
        self.synthetic.setCurrentIndex(0 if data.synthetic is None else 1 if data.synthetic else 2)
        self.privacy.setCurrentIndex(self.privacy.findData(data.privacy))
        self.loading = False
        self.on_changed()

    def add_tag(self) -> None:
        value = self.tag_input.text().strip()
        if not value:
            return
        duplicate = tag_key(value) in {tag_key(t) for t in self.tags}
        if not duplicate:
            self.tags += (value,)
            self.tag_list.addItem(value)
        self.tag_input.clear()
        self.on_changed()
        if duplicate:
            self.tag_counter.setText(
                self.tag_counter.text() + " · Tag already present; no duplicate added."
            )

    def remove_tag(self) -> None:
        row = self.tag_list.currentRow()
        if row >= 0:
            self.tags = self.tags[:row] + self.tags[row + 1 :]
            self.tag_list.takeItem(row)
            self.on_changed()

    def on_changed(self) -> None:
        if self.loading:
            return
        data = self.value()
        self.title_counter.setText(f"{len(data.title)} / 100")
        self.description_counter.setText(f"{len(data.description.encode('utf-8')):,} / 5,000")
        self.description_counter.setToolTip("YouTube counts the description in UTF-8 bytes")
        self.tag_counter.setText(f"{tag_length(data.tags)} / 500")
        self.tag_list.setVisible(bool(self.tags))
        self.remove_tag_button.setVisible(bool(self.tags))
        self.errors.setText("\n".join(metadata_errors(data)))
        if self.dirty:
            self.save_state.setText("Unsaved changes · Draft")
            restyle(self.save_state, "Warn")
        elif self.preparation and self.preparation.revision:
            self.save_state.setText("Saved on this Mac")
            restyle(self.save_state, "Good")
        else:
            self.save_state.setText("Draft · Not saved yet")
            restyle(self.save_state, "Neutral")
        self.use_filename.setVisible(not data.title.strip())
        self.save_state.setToolTip(
            f"Revision {self.preparation.revision} · {self.preparation.modified_at}"
            if self.preparation
            else ""
        )
        self.save_button.setEnabled(
            self.dirty or not self.preparation or not self.preparation.revision
        )
        self.save_button.setText(
            "Save Draft"
            if self.dirty or not self.preparation or not self.preparation.revision
            else "Saved"
        )
        self.changed.emit()


class PreparationTabs(QTabWidget):
    """Only the current panel determines page height, avoiding empty space under short tabs."""

    def __init__(self) -> None:
        super().__init__()
        self.currentChanged.connect(self.resize_to_current)

    def resize_to_current(self, index: int = 0) -> None:
        for position in range(self.count()):
            panel = self.widget(position)
            if panel is None:
                continue
            panel.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred
                if position == self.currentIndex()
                else QSizePolicy.Policy.Ignored,
            )
            panel.updateGeometry()
        self.setFixedHeight(self.sizeHint().height())
        self.updateGeometry()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        height = self.sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        if current is None:
            return super().sizeHint()
        layout = current.layout()
        height = current.sizeHint().height()
        if layout and layout.hasHeightForWidth():
            height = layout.totalHeightForWidth(max(240, self.width() - 12))
        return QSize(current.sizeHint().width(), height + self.tabBar().sizeHint().height() + 12)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.sizeHint().height())
