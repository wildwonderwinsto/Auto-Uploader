"""The video editor's Upload step: the details written once go to every ticked platform.

Each platform keeps its own page (Options) and its own safety rules: YouTube still shows its
exact-details confirmation (or schedule approval), and Buffer posts are confirmed here together.
Nothing here uploads by itself; it only starts each platform's own, existing action.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..social import HOSTING_CONSENT
from . import theme
from .components import icon_button, platform_choice, row, set_choice
from .formatting import handle, time_pattern, timestamp, upload_status
from .icons import PLATFORM_NAMES, icon
from .videos import text

if TYPE_CHECKING:
    from .window import MainWindow

PLATFORM_ORDER = ("youtube", "tiktok", "instagram", "facebook")
UNSENT = ("Uploading to media host", "Hosted and ready", "Waiting for Buffer limit")
UPCOMING = (*UNSENT, "Sending to Buffer", "Scheduled in Buffer")
DONE = ("Uploaded", "Posted", "Scheduled")  # row words that mean the video is already there


class UploadAll(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.video_id: int | None = None
        self.touched: set[str] = set()  # platforms the person ticked or unticked themselves
        self.available: list[str] = []  # platforms this video can go to right now
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)
        layout.addWidget(text("Upload to your platforms", "Heading"))
        layout.addWidget(
            text(
                "The title, description and tags you wrote are used on every platform. Tick "
                "where this video should go, choose when, and upload once.",
                "Muted",
            )
        )
        # While the details aren't marked ready, one notice says so instead of four rows.
        self.not_ready = text(
            "Finish the details and mark the video as ready in Review first. Then tick where it "
            "should go.",
            "Notice",
        )
        self.to_review = icon_button("Go to Review", "chevron-right", role="Primary")
        self.to_review.clicked.connect(lambda: window.videos.go_step(2))
        layout.addWidget(self.not_ready)
        layout.addWidget(row(self.to_review))
        self.rows: dict[str, tuple[QCheckBox, QLabel, QPushButton]] = {}
        self.badges: dict[str, QLabel] = {}
        for platform in PLATFORM_ORDER:
            frame, check, status, badge, line = platform_choice(platform)
            self.badges[platform] = badge
            check.clicked.connect(lambda _=False, p=platform: self.ticked(p))
            more = icon_button("Options", "chevron-right", role="Ghost")
            more.setAccessibleName(f"{PLATFORM_NAMES[platform]} options and history")
            more.clicked.connect(lambda _=False, p=platform: self.open_platform(p))
            line.addWidget(more)
            layout.addWidget(frame)
            self.rows[platform] = (check, status, more)
        self.when_row = QWidget()
        when = QHBoxLayout(self.when_row)
        when.setContentsMargins(0, 0, 0, 0)
        when.setSpacing(8)
        label = QLabel("When")
        label.setObjectName("Subheading")
        when.addWidget(label)
        self.now = self.segment("Now", "send")
        self.later = self.segment("Schedule", "calendar")
        self.now.setChecked(True)
        when.addWidget(self.now)
        when.addWidget(self.later)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setDisplayFormat("ddd, MMM d, yyyy · " + time_pattern())
        self.when.setCalendarPopup(True)
        self.when.setAccessibleName("Upload time on every platform")
        when.addWidget(self.when)
        when.addStretch(1)
        layout.addWidget(self.when_row)
        self.go = icon_button("Upload…", "upload", role="Primary")
        self.go.clicked.connect(self.upload)
        self.go_row = row(self.go)
        layout.addWidget(self.go_row)
        # Even controls: Now and Schedule share one size, and the Upload button sits right
        # under them at their combined width.
        self.go.setText("Schedule on 4 Platforms…")  # the longest text it can show
        height = self.go.sizeHint().height()
        width = max(
            self.now.sizeHint().width(),
            self.later.sizeHint().width(),
            (self.go.sizeHint().width() - when.spacing() + 1) // 2,
        )
        for segment in (self.now, self.later):
            segment.setFixedSize(width, height)
        self.go.setFixedSize(2 * width + when.spacing(), height)
        label.setFixedWidth(label.sizeHint().width())
        go_layout = self.go_row.layout()
        if go_layout:
            go_layout.setContentsMargins(label.width() + when.spacing(), 0, 0, 0)
        self.summary = text("", "Subtle")
        layout.addWidget(self.summary)
        layout.addStretch()
        self.render()

    def segment(self, value: str, glyph: str) -> QPushButton:
        button = QPushButton(value)
        button.setObjectName("Segment")
        button.setCheckable(True)
        button.setIcon(icon(glyph, theme.MUTED))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, b=button: self.choose_when(b))
        return button

    def choose_when(self, button: QPushButton) -> None:
        for other in (self.now, self.later):
            other.setChecked(other is button)
        self.render()

    def ticked(self, platform: str) -> None:
        self.touched.add(platform)
        self.render()

    def open_platform(self, platform: str) -> None:
        videos = self.window_.videos
        videos.choose_upload("when", "later" if self.later.isChecked() else "now")
        videos.choose_upload("platform", platform)

    # State ----------------------------------------------------------------------------------

    def state(self, platform: str) -> tuple[bool, str]:
        """Whether this video can go to a platform right now, and why not in plain words."""
        w = self.window_
        video = w.videos.selected
        later = self.later.isChecked()
        if video is None:
            return False, "Choose a video first"
        if not w.videos.review_state.text().startswith("Review Complete") or (
            w.videos.editor.dirty
        ):
            # One rule for every platform: the saved details are checked once, then go out.
            return False, "Waiting for Review"
        if platform == "youtube":
            done = [
                a
                for a in w.uploads.attempts
                if a.video_id == video.id and not a.released and a.state != "Cancelled"
            ]
            if done:
                words = upload_status(done[0].state, done[0].processing)
                if words.startswith("Video processed"):
                    words = "Uploaded · " + words.rsplit(" · ", 1)[-1]
                elif not words.startswith("Uploaded"):
                    words = "Already started · " + words
                return False, words
            # A saved YouTube plan for this video counts as its upload: no second one.
            planned = next(
                (
                    j
                    for j in w.schedules.jobs
                    if j.video_id == video.id and j.state not in ("Draft", "Cancelled")
                ),
                None,
            )
            if planned and planned.state in ("Scheduled", "Due"):
                return False, "Scheduled · " + timestamp(
                    planned.scheduled_utc, planned.timezone, weekday=True
                )
            if planned:
                return False, f"{planned.state} · see YouTube uploads in Schedule"
            if later:
                ready = w.schedule_button.isEnabled()
                return ready, (
                    "Public, at the time you pick below" if ready else w.schedule_reason.text()
                )
            ready = w.uploads.upload_button.isEnabled()
            if not ready:
                return False, w.uploads.reason.text()
            public = w.uploads.planned_privacy == "public"
            return True, (
                "Public upload"
                if public
                else "Private upload (set Visibility to Public in Details to publish)"
            )
        social = next(s for s in w.socials if s.key == platform)
        connection = social.data["connection"]
        if not connection:
            return False, f"Connect {social.name} in Accounts first"
        if connection.get("status") != "Connected":
            return False, f"Reconnect {social.name} in Accounts"
        if not social.data["hosting"]:
            return False, "Set up video hosting in Settings first"
        fit = w.videos.fits[platform]
        if fit.text() == "Won't post":
            return False, "Won't post: " + fit.toolTip().split("\n")[0]
        posted = [
            p
            for p in social.data["posts"]
            if p["video_id"] == video.id and not p["released"] and p["state"] != "Cancelled"
        ]
        if posted:
            post = posted[0]
            if post["state"] == "Sent by Buffer":
                return False, "Posted"
            if post["due_at"] and post["state"] in UPCOMING:
                return False, "Scheduled · " + timestamp(post["due_at"], weekday=True)
            return False, f"Already started · {post['state']}"
        if social.spec.ai_label and social.ai.currentData() is None:
            return False, "Choose the AI-generated content label in Details"
        if not social.publish_button.isEnabled():
            return False, "Busy · try again in a moment"
        return True, f"{handle(platform, connection)} · posts publicly"

    def render(self, *_: object) -> None:
        w = self.window_
        video = getattr(w.videos, "selected", None)
        identity = video.id if video else None
        if identity != self.video_id:
            self.video_id, self.touched = identity, set()  # a new video starts fresh
        self.when.setVisible(self.later.isChecked())
        if not hasattr(w, "socials"):
            return
        waiting = video is not None and (
            not w.videos.review_state.text().startswith("Review Complete") or w.videos.editor.dirty
        )
        self.not_ready.setVisible(waiting)
        self.to_review.setVisible(waiting)
        self.available = []
        for platform, (check, status, _more) in self.rows.items():
            ready, words = self.state(platform)
            done = next((d for d in DONE if words.startswith(d)), "")
            set_choice(check, status, self.badges[platform], ready, words, done)
            if ready:
                self.available.append(platform)
            if ready and platform not in self.touched:
                check.setChecked(True)  # every ready platform, unless the person said no
        self.render_action()

    def chosen(self) -> list[str]:
        return [p for p, (check, _s, _m) in self.rows.items() if check.isChecked()]

    def render_action(self) -> None:
        count = len(self.chosen())
        later = self.later.isChecked()
        # Nothing it can go to yet (or anymore): no When choice and no button that can't work.
        waiting = bool(self.available) and self.not_ready.isHidden()
        self.when_row.setVisible(waiting)
        self.go_row.setVisible(waiting)
        self.go.setText(
            ("Schedule on " if later else "Upload to ")
            + (f"{count} Platform{'s' if count != 1 else ''}…" if count else "Platforms…")
        )
        self.go.setEnabled(bool(count))
        self.go.setIcon(icon("calendar" if later else "upload", "#ffffff"))

    # Action ---------------------------------------------------------------------------------

    def due(self) -> datetime | None:
        if not self.later.isChecked():
            return None
        return datetime.fromtimestamp(self.when.dateTime().toSecsSinceEpoch(), UTC)

    def upload(self) -> None:
        w = self.window_
        chosen, due = self.chosen(), self.due()
        if not chosen or w.videos.selected is None:
            return
        if due is not None and due <= datetime.now(UTC) + timedelta(minutes=2):
            QMessageBox.warning(
                self, "Pick a later time", "Choose a time at least two minutes from now."
            )
            return
        when = f"at {timestamp(due, weekday=True)}" if due else "now"
        lines = []
        for platform in chosen:
            name = PLATFORM_NAMES[platform]
            if platform == "youtube":
                public = w.uploads.planned_privacy == "public" or due is not None
                lines.append(
                    f"• YouTube: {'PUBLIC' if public else 'Private'} upload {when}. YouTube's own "
                    "confirmation with the exact details opens next"
                    + (" — approving it is all that's needed." if due else ".")
                )
            else:
                social = next(s for s in w.socials if s.key == platform)
                lines.append(
                    f"• {name}: PUBLIC post {when} to "
                    f"{handle(platform, social.data['connection'] or {})} through Buffer."
                )
        buffer = [p for p in chosen if p != "youtube"]
        message = "\n".join(lines) + ("\n\n" + HOSTING_CONSENT if buffer else "")
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Upload this video?",
            message,
            QMessageBox.StandardButton.Cancel,
            self,
        )
        go = box.addButton(self.go.text().rstrip("…"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        for social in w.socials:
            if social.key in buffer:
                social.publish(due, confirmed=True)
        if "youtube" in chosen:
            video = w.videos.selected
            connection = w.accounts.view.connection if w.accounts.view else None
            if due is not None and video and connection:
                w.schedules.schedule(video.id, connection.channel.id, due)
            else:
                w.uploads.prepare("public" if w.uploads.planned_privacy == "public" else "private")
        names = ", ".join(PLATFORM_NAMES[p] for p in chosen)
        self.summary.setText(
            f"Started for {names}. Progress shows on each platform's row and under Options."
        )
        self.render()
