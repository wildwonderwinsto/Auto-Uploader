"""Settings → Limits & usage: one meter per limit, how much is used and when it frees up.

Counts are read locally (`usage.snapshot`). Cloudinary's monthly credits are asked of
Cloudinary automatically (a read-only request; nothing is uploaded): when Settings opens and
every 30 minutes while it stays open. Rows are built once and updated in place, so the regular
refresh never flickers."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar, QVBoxLayout

from .. import usage
from .components import card, in_background, restyle
from .formatting import timestamp
from .icons import PLATFORM_NAMES, image, logo, pixmap
from .videos import text

if TYPE_CHECKING:
    from .window import MainWindow

CLOUDINARY_EVERY = timedelta(minutes=30)  # Cloudinary's usage changes slowly


class Meter:
    """Who and "used of limit" on one line, the bar under them, then a note. Three grid rows,
    so it fits the narrowest window."""

    ROWS = 3

    def __init__(self, grid: QGridLayout, line: int, mark: QLabel, name: str) -> None:
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setAccessibleName(f"{name} usage")
        self.count = QLabel("—")
        self.count.setObjectName("Subheading")
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.note = text("", "Subtle")
        self.note.setContentsMargins(0, 0, 0, 10)  # room before the next meter
        label = QLabel(name)
        label.setObjectName("Subheading")
        grid.addWidget(mark, line, 0, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(label, line, 1)
        grid.addWidget(self.count, line, 2)
        grid.addWidget(self.bar, line + 1, 1, 1, 2)
        grid.addWidget(self.note, line + 2, 1, 1, 2)
        self.widgets = (mark, label, self.bar, self.count, self.note)

    def show(self, used: float, limit: float, count: str, note: str) -> None:
        share = 0 if limit <= 0 else min(1.0, used / limit)
        value = round(share * 1000)
        if self.bar.maximum() != 1000:
            self.bar.setRange(0, 1000)
        if self.bar.value() != value:
            self.bar.setValue(value)
        restyle(self.bar, "MeterBad" if share >= 1 else "MeterWarn" if share >= 0.8 else "")
        for label, value_text in ((self.count, count), (self.note, note)):
            if label.text() != value_text:
                label.setText(value_text)

    def remove(self) -> None:
        for widget in self.widgets:
            widget.hide()
            widget.deleteLater()


class UsagePanel(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.reader: threading.Thread | None = None
        self.data: dict[str, Any] | None = None
        self.card, body = card(20, 12)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        top = QGridLayout()
        top.setHorizontalSpacing(10)
        top.addWidget(image(pixmap("chart", 20, "#a8c6ff")), 0, 0)
        top.addWidget(text("Limits & usage", "Heading"), 0, 1)
        top.setColumnStretch(1, 1)
        heading.addLayout(top)
        heading.addWidget(
            text(
                "The platforms' own limits, how much of each is used, and when it frees up. "
                "ShortsScheduler adds no limits of its own.",
                "Muted",
            )
        )
        body.addLayout(heading)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(2)
        self.grid.setColumnStretch(1, 1)
        body.addLayout(self.grid)
        self.lines = 0
        self.meters: dict[str, Meter] = {}
        self.add("youtube", "youtube", "YouTube uploads")
        for platform in ("tiktok", "instagram", "facebook"):
            self.add(platform, platform, f"{PLATFORM_NAMES[platform]} posts")
        for platform in ("tiktok", "instagram", "facebook"):
            self.add(
                f"{platform}-scheduled", platform, f"{PLATFORM_NAMES[platform]} scheduled in Buffer"
            )
        # Cloudinary accounts come and go; their rows sit after these.
        self.hosts: dict[str, Meter] = {}
        self.buffer_note = text("", "Subtle")
        body.addWidget(self.buffer_note)
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh)
        window.tiktok.changed.connect(self.render)

    def add(self, key: str, platform: str, name: str) -> Meter:
        meter = Meter(self.grid, self.lines, image(logo(platform, 20)), name)
        self.lines += Meter.ROWS
        self.meters[key] = meter
        return meter

    def activate(self) -> None:
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self.window.stack.currentWidget() is not self.window.background:
            self.timer.stop()  # only while Settings is shown
            return
        social = self.window.tiktok
        last = social.hosts_checked
        if social.data.get("hosts") and (
            last is None or datetime.now(UTC) - last >= CLOUDINARY_EVERY
        ):
            social.hosts_checked = datetime.now(UTC)  # one check at a time, even if it fails
            social.check_hosts(quiet=True)
        if self.reader:
            return
        library = self.window.videos.service.library

        def read(result: dict[str, Any]) -> None:
            self.reader = None
            self.loaded(result.get("ok"))  # a failed read keeps the last values

        self.reader = in_background(self, lambda: usage.snapshot(library, datetime.now(UTC)), read)

    def loaded(self, data: dict[str, Any] | None) -> None:
        if data is not None:
            self.data = data
        self.render()

    def request_close(self) -> bool:
        self.timer.stop()
        if self.reader:
            self.reader.join(timeout=2)  # one quick local read
        return True

    def render(self) -> None:
        data = self.data
        if data is None:
            return
        youtube = data["youtube"]
        self.meters["youtube"].show(
            youtube["used"],
            youtube["limit"],
            f"{youtube['used']} of {youtube['limit']} today",
            (
                "YouTube reported its limit today; uploads resume after "
                if youtube["blocked"]
                else "YouTube's API allows 100 a day. Starts over "
            )
            + timestamp(youtube["resets"], weekday=True)
            + ".",
        )
        for platform, counts in data["buffer"].items():
            frees = counts["frees"]
            self.meters[platform].show(
                counts["used"],
                counts["limit"],
                f"{counts['used']} of {counts['limit']} in 24 h",
                f"Buffer accepts {counts['limit']} a day. "
                + (
                    f"The oldest counted post stops counting {timestamp(frees, weekday=True)}."
                    if frees
                    else "None counted right now."
                ),
            )
            self.meters[f"{platform}-scheduled"].show(
                counts["scheduled"],
                counts["scheduled_limit"],
                f"{counts['scheduled']} of {counts['scheduled_limit']} at once",
                "Buffer's free plan holds 10 scheduled posts per channel. A slot frees up as "
                "each one posts.",
            )
        self.render_hosts()
        waits = data["waits"]
        note = (
            "Buffer asked ShortsScheduler to wait (its API limit): "
            + "; ".join(f"{name} until {timestamp(until, weekday=True)}" for name, until in waits)
            + ". Posts continue on their own after that."
            if waits
            else "Buffer's free plan allows about 3,000 requests every 30 days. If Buffer asks "
            "ShortsScheduler to slow down, it shows here with the time it resumes."
        )
        if self.buffer_note.text() != note:
            self.buffer_note.setText(note)
        restyle(self.buffer_note, "Notice" if waits else "Subtle")

    def render_hosts(self) -> None:
        social = self.window.tiktok
        hosts = {h["credential_key"]: h for h in social.data.get("hosts", [])}
        for key in [k for k in self.hosts if k not in hosts]:
            self.hosts.pop(key).remove()
        for key, host in hosts.items():
            meter = self.hosts.get(key)
            if meter is None:
                meter = Meter(
                    self.grid,
                    self.lines,
                    image(pixmap("image", 20, "#a8c6ff")),
                    f"Cloudinary · {host['cloud_name']}",
                )
                self.lines += Meter.ROWS
                self.hosts[key] = meter
            found = social.host_usage.get(key)
            checked = (
                f" Checked {timestamp(social.hosts_checked, weekday=True)}."
                if social.hosts_checked and found
                else ""
            )
            if found and "error" not in found and found.get("credits_limit"):
                used, limit = float(found["credits_used"] or 0), float(found["credits_limit"])
                meter.show(
                    used,
                    limit,
                    f"{used:g} of {limit:g} credits",
                    f"Monthly credits on plan {found.get('plan') or 'unknown'}; they start over "
                    "each month. At 100 % the next Cloudinary account takes new videos." + checked,
                )
            else:
                meter.show(
                    0,
                    1,
                    "Checking…" if not found else "Unavailable",
                    str(found["error"]) + checked
                    if found and found.get("error")
                    else "Asking Cloudinary for this month's credits…",
                )
