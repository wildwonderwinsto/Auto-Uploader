"""Local YouTube draft editor. No database or worker ownership."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
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
from .components import Disclosure, primary


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
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(10)
        self.save_state = caption("No video selected", "Badge")
        layout.addWidget(self.save_state)
        self.title = QLineEdit()
        self.title.setAccessibleName("YouTube title")
        self.title.setPlaceholderText("Enter a title")
        self.title.setMaxLength(1_000_000)  # Drafts may exceed the platform limit.
        label = caption("Title", "Heading")
        label.setBuddy(self.title)
        layout.addWidget(label)
        layout.addWidget(self.title)
        self.title_counter = caption("")
        layout.addWidget(self.title_counter)
        self.description = QPlainTextEdit()
        self.description.setAccessibleName("YouTube description")
        self.description.setFixedHeight(120)
        label = caption("Description (optional)")
        label.setBuddy(self.description)
        layout.addWidget(label)
        layout.addWidget(self.description)
        self.description_counter = caption("")
        layout.addWidget(self.description_counter)
        tags_section = Disclosure("Tags (optional)")
        tags_section.changed.connect(self.layout_changed.emit)
        layout.addWidget(tags_section)
        tag_layout = tags_section.body
        tag_row = QHBoxLayout()
        self.tag_input = QLineEdit()
        self.tag_input.setAccessibleName("New keyword tag")
        self.tag_input.setPlaceholderText("One keyword or phrase")
        self.tag_input.setMaxLength(1_000_000)
        self.add_tag_button = QPushButton("Add Tag")
        self.add_tag_button.clicked.connect(self.add_tag)
        self.tag_input.returnPressed.connect(self.add_tag)
        tag_row.addWidget(self.tag_input, 1)
        tag_row.addWidget(self.add_tag_button)
        tag_layout.addLayout(tag_row)
        self.tag_list = QListWidget()
        self.tag_list.setAccessibleName("Keyword tags")
        self.tag_list.setFixedHeight(78)
        self.tag_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_layout.addWidget(self.tag_list)
        self.remove_tag_button = QPushButton("Remove Selected Tag")
        self.remove_tag_button.clicked.connect(self.remove_tag)
        tag_layout.addWidget(self.remove_tag_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.tag_counter = caption("")
        tag_layout.addWidget(self.tag_counter)
        self.kids = QComboBox()
        self.synthetic = QComboBox()
        for combo in (self.kids, self.synthetic):
            combo.addItem("Not specified", None)
            combo.addItem("Yes", True)
            combo.addItem("No", False)
        self.kids.setAccessibleName("Made for Kids")
        self.synthetic.setAccessibleName("Altered or synthetic content disclosure")
        for title, combo in (
            ("Is this video made for kids?", self.kids),
            ("Does it include realistic altered or synthetic content?", self.synthetic),
        ):
            label = caption(title)
            label.setBuddy(combo)
            layout.addWidget(label)
            layout.addWidget(combo)
        disclosure_help = Disclosure("Age restriction (18+) and disclosure guidance")
        disclosure_help.changed.connect(self.layout_changed.emit)
        layout.addWidget(disclosure_help)
        disclosure_help.body.addWidget(
            caption(
                "Age restriction (18+): this app cannot apply it through YouTube’s upload API. If your video needs an age restriction, upload privately and set the restriction in YouTube Studio before publishing."
            )
        )
        disclosure_help.body.addWidget(
            caption(
                "YouTube requires disclosure for realistic AI-generated or meaningfully altered content, "
                "including certain generated music. Animation, Blender rendering, or AI assistance alone "
                "does not automatically require Yes. Review the official guidance for your content."
            )
        )
        guidance = QLabel(
            '<a href="https://support.google.com/youtube/answer/14328491">Read YouTube disclosure guidance</a>'
        )
        guidance.setOpenExternalLinks(True)
        guidance.setAccessibleName("Open official YouTube disclosure guidance in browser")
        disclosure_help.body.addWidget(guidance)
        self.privacy = QComboBox()
        for value in ("private", "unlisted", "public"):
            self.privacy.addItem(value.title(), value)
        self.privacy.setAccessibleName("Planned privacy for a future upload")
        label = caption("Visibility preference")
        label.setBuddy(self.privacy)
        layout.addWidget(label)
        layout.addWidget(self.privacy)
        self.errors = caption("")
        layout.addWidget(self.errors)
        self.save_button = QPushButton("Save Draft")
        self.save_button.clicked.connect(self.save_requested.emit)

        action_row = QHBoxLayout()
        action_row.addWidget(self.save_button)
        self.review_link = QPushButton("Save and review →")
        self.review_link.clicked.connect(self.review_requested.emit)
        primary(self.review_link)
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
        self.title_counter.setText(f"{len(data.title)} / 100 characters")
        self.description_counter.setText(
            f"{len(data.description.encode('utf-8')):,} / 5,000 UTF-8 bytes"
        )
        self.tag_counter.setText(f"{tag_length(data.tags)} / 500 combined tag characters")
        self.errors.setText("\n".join(metadata_errors(data)))
        if self.dirty:
            self.save_state.setText("Unsaved changes · Draft")
        elif self.preparation and self.preparation.revision:
            self.save_state.setText("Saved on this Mac")
        else:
            self.save_state.setText("Draft · Not saved yet")
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
