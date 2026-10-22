"""The uploader's home: what is coming up, what went out, what needs you, and how it is doing.

Read-only. Records come from the other pages' controllers; view counts come from one read-only
YouTube request on a worker thread.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import activity
from ..activity import Entry, Group
from . import theme
from .components import (
    CardGrid,
    Elided,
    StatTile,
    card,
    icon_button,
    muted,
    pill,
    platform_top,
    restyle,
)
from .formatting import compact, friendly
from .icons import PLATFORM_NAMES, image, logo_icon, pixmap

if TYPE_CHECKING:
    from .window import MainWindow

STATS_EVERY = 15 * 60  # seconds between automatic view-count refreshes
TONES = {
    activity.UPCOMING: "Info",
    activity.UPLOADING: "Info",
    activity.DONE: "Good",
    activity.ATTENTION: "Warn",
}


class StatsWorker(QThread):
    result = Signal(object)

    def __init__(self, window: MainWindow, ids: list[str], parent: QObject) -> None:
        super().__init__(parent)
        self.youtube = window.accounts.service if window.accounts.view.connection else None
        self.tiktok = window.tiktok.service if window.tiktok.data.get("insights") else None
        self.ids, self.cancel = ids, Event()

    def run(self) -> None:
        """Each platform's counts are optional: one failing never hides the others."""
        stats: dict[str, Any] = {"channel": {}, "videos": {}, "platforms": []}
        for platform, read in (
            (
                "youtube",
                lambda: self.youtube.statistics(self.ids, self.cancel) if self.youtube else None,
            ),
            (
                "tiktok",
                lambda: {"videos": self.tiktok.statistics(self.cancel)} if self.tiktok else None,
            ),
        ):
            try:
                found = read()
            except Exception:  # noqa: BLE001 - shown as "not available", never fatal
                found = None
            if found:
                stats["channel"] = found.get("channel") or stats["channel"]
                stats["videos"].update(found.get("videos") or {})
                stats["platforms"].append(platform)
        self.result.emit(stats)


def logo_button(entry: Entry) -> QPushButton:
    """A platform logo; clicking it opens the post when the platform returned a link."""
    name = PLATFORM_NAMES[entry.platform]
    button = QPushButton()
    button.setObjectName("Logo")
    button.setIcon(logo_icon(entry.platform))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(QSize(28, 28))
    when = friendly(entry.when, empty="waiting for a posting time")
    button.setToolTip(
        f"{name}: {entry.status} · {when}" + (f"\nOpen on {name}" if entry.link else "")
    )
    button.setAccessibleName(f"{name}: {entry.status}")
    if entry.link:
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(entry.link)))
    return button


def group_row(group: Group, extra: str = "") -> QWidget:
    """One video, however many platforms it is on: logos, title, time and one status."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 5, 0, 5)
    layout.setSpacing(10)
    logos = QWidget()
    logos.setMinimumWidth(60)  # titles line up whether a video is on one platform or two
    logo_row = QHBoxLayout(logos)
    logo_row.setContentsMargins(0, 0, 0, 0)
    logo_row.setSpacing(2)
    for entry in sorted(group.entries, key=lambda e: activity.PLATFORM_ORDER.index(e.platform)):
        logo_row.addWidget(logo_button(entry))
    logo_row.addStretch()
    layout.addWidget(logos)
    names = QVBoxLayout()
    names.setSpacing(2)
    title = Elided(group.title, "Subheading")
    title.setToolTip(group.title + "".join(f"\n{e.detail}" for e in group.entries if e.detail))
    names.addWidget(title)
    times = {friendly(e.when, empty="Waiting for a posting time") for e in group.entries}
    if len(times) <= 1 or group.kind == activity.DONE:
        when = friendly(group.when, empty="Waiting for a posting time")
    else:
        when = " · ".join(
            f"{PLATFORM_NAMES[e.platform]} {friendly(e.when, empty='waiting')}"
            for e in group.entries
        )
    names.addWidget(Elided(when + (f" · {extra}" if extra else ""), "Subtle"))
    layout.addLayout(names, 1)
    if len(group.platforms) > 1:
        count = pill(f"×{len(group.platforms)}", "Neutral")
        count.setToolTip("On " + ", ".join(PLATFORM_NAMES[p] for p in group.platforms))
        layout.addWidget(count, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(
        pill(group.status, TONES.get(group.kind, "Neutral")), 0, Qt.AlignmentFlag.AlignVCenter
    )
    return holder


class ListCard(QWidget):
    """A titled card holding up to `limit` rows, an empty message and a "See all" action."""

    def __init__(self, title: str, glyph: str, empty: str, action: str) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame, body = card(20, 6)
        outer.addWidget(self.frame)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(image(pixmap(glyph, 18, theme.ACCENT_TEXT)))
        top.addWidget(muted(title, "Heading"), 1)
        self.more = icon_button(action, "chevron-right", role="Ghost")
        self.more.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        top.addWidget(self.more)
        body.addLayout(top)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        body.addLayout(self.rows)
        self.empty = muted(empty)
        self.empty.setContentsMargins(0, 8, 0, 8)
        body.addWidget(self.empty)
        body.addStretch()
        self.shown: object = None

    def show_rows(self, rows: list[tuple[Group, str]]) -> None:
        """Rows of (video, extra text); rebuilt only when they change, so refreshes don't flash."""
        # The date is part of the key: rows say "Today" and "Tomorrow".
        key = (datetime.now().date(), rows)
        if key == self.shown:
            return
        self.shown = key
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.deleteLater()
        for group, extra in rows:
            self.rows.addWidget(group_row(group, extra))
        self.empty.setVisible(not rows)


class PlatformCard(QWidget):
    def __init__(self, platform: str, subtitle: str) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame, body = card(20, 12)
        outer.addWidget(frame)
        self.status = pill("Not connected")
        body.addLayout(platform_top(platform, subtitle, self.status))
        self.account = muted("", "Subheading")
        body.addWidget(self.account)
        numbers = QHBoxLayout()
        numbers.setSpacing(24)
        self.values: list[tuple[QLabel, QLabel]] = []
        for _ in range(3):
            column = QVBoxLayout()
            column.setSpacing(0)
            value, label = QLabel("—"), QLabel("")
            value.setObjectName("Heading")
            label.setObjectName("StatLabel")
            column.addWidget(value)
            column.addWidget(label)
            numbers.addLayout(column)
            self.values.append((value, label))
        numbers.addStretch()
        body.addLayout(numbers)
        self.note = muted("", "Subtle")
        body.addWidget(self.note)
        self.connect_button = icon_button("Connect", "link", role="Primary")
        body.addWidget(self.connect_button, 0, Qt.AlignmentFlag.AlignLeft)

    def set_numbers(self, *pairs: tuple[str, str]) -> None:
        for (value, label), (number, caption) in zip(self.values, pairs, strict=False):
            value.setText(number)
            label.setText(caption)

    def set_connected(
        self, connected: bool, label: str = "Connected", tone: str = "Good", *, known: bool = True
    ) -> None:
        # Until the first check finishes, say so: never flash "Not connected" and Connect.
        self.status.setText("Checking…" if not known else label if connected else "Not connected")
        restyle(self.status, tone if known and connected else "Neutral")
        self.connect_button.setVisible(known and not connected)


class DashboardPage(QScrollArea):
    entries_changed = Signal(object)

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.stats: dict[str, Any] = {"channel": {}, "videos": {}}
        self.stats_at = 0.0
        self.stats_worker: StatsWorker | None = None
        self.entries: list[Entry] = []
        self.setObjectName("dashboard")
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(20)

        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(4)
        names.addWidget(muted("Dashboard", "Title"))
        names.addWidget(muted("Your uploads across every connected platform.", "Muted"))
        top.addLayout(names, 1)
        self.add_videos = icon_button("Add Videos", "plus", role="Primary")
        self.add_videos.clicked.connect(self.add)
        self.auto = icon_button("Auto Upload", "auto")
        self.auto.clicked.connect(lambda: window.navigate("automation"))
        top.addWidget(self.auto, 0, Qt.AlignmentFlag.AlignBottom)
        top.addWidget(self.add_videos, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(top)

        tiles = CardGrid(minimum=190, spacing=14, maximum=4)
        self.upcoming_tile = StatTile("COMING UP", "clock")
        self.uploaded_tile = StatTile("UPLOADED", "check-circle", theme.GOOD)
        self.attention_tile = StatTile("NEEDS YOU", "alert", theme.WARN)
        self.views_tile = StatTile("VIEWS", "eye")
        for tile in (self.upcoming_tile, self.uploaded_tile, self.attention_tile, self.views_tile):
            tiles.add(tile)
        layout.addWidget(tiles)

        self.attention = ListCard(
            "Needs your attention", "alert", "Nothing needs you right now.", "Open Schedule"
        )
        self.attention.more.clicked.connect(lambda: window.navigate("queue"))
        layout.addWidget(self.attention)

        lists = CardGrid(minimum=380)
        self.coming = ListCard(
            "Coming up",
            "calendar",
            "Nothing is scheduled. Add a video and pick a time, or let Auto Upload do it.",
            "Schedule",
        )
        self.coming.more.clicked.connect(lambda: window.navigate("queue"))
        self.recent = ListCard(
            "Recently uploaded", "check-circle", "Nothing has been uploaded yet.", "History"
        )
        self.recent.more.clicked.connect(window.show_upload_history)
        lists.add(self.coming)
        lists.add(self.recent)
        layout.addWidget(lists)
        # Shown only while an automatic upload is running.
        self.stop = window.automatic.stop
        self.stop.setText("Stop Current Upload")
        layout.addWidget(self.stop, 0, Qt.AlignmentFlag.AlignLeft)

        heading = QHBoxLayout()
        heading.addWidget(muted("Platforms", "Heading"), 1)
        self.refresh_stats = icon_button("Refresh Views", "refresh", role="Ghost")
        self.refresh_stats.clicked.connect(lambda: self.load_stats(force=True))
        heading.addWidget(self.refresh_stats)
        layout.addLayout(heading)
        platforms = CardGrid(minimum=340)
        self.youtube = PlatformCard("youtube", "Your channel")
        self.tiktok = PlatformCard("tiktok", "Through Buffer")
        for platform in (self.youtube, self.tiktok):
            platform.connect_button.clicked.connect(lambda: window.navigate("accounts"))
            platforms.add(platform)
        layout.addWidget(platforms)
        # The YouTube account line; also read by other pages' tests as the channel summary.
        self.channel_summary = self.youtube.account
        layout.addStretch()

        self.pending = QTimer(self)
        self.pending.setSingleShot(True)
        self.pending.setInterval(50)
        self.pending.timeout.connect(self.refresh)

    # Data ------------------------------------------------------------------------------------

    def schedule(self, *_: Any) -> None:
        """Coalesce bursts of updates from the other pages into one repaint."""
        self.pending.start()

    def add(self) -> None:
        self.window_.navigate("videos")
        if self.window_.stack.currentWidget() is self.window_.videos:
            self.window_.videos.choose_file()

    def activate(self) -> None:
        self.refresh()
        self.load_stats()

    def collect(self) -> list[Entry]:
        w = self.window_
        data = w.automatic.data
        started = {e["job_id"] for e in data.get("executions", [])}
        attempts = w.uploads.attempts or data.get("attempts", [])
        return activity.timeline(
            data.get("jobs", []),
            attempts,
            started,
            w.recurring.data or {},
            w.tiktok.data.get("posts", []),
        )

    def load_stats(self, *, force: bool = False) -> None:
        view = self.window_.accounts.view
        youtube = bool(view and view.connection)
        if (
            self.stats_worker
            or not (youtube or self.window_.tiktok.data.get("insights"))
            or self.window_.accounts.worker
            or (not force and time.monotonic() - self.stats_at < STATS_EVERY and self.stats_at)
        ):
            return
        ids = [e.remote_id for e in self.entries if e.platform == "youtube" and e.remote_id]
        # A YouTube refresh never waits: scheduled uploads need the account lock on time.
        self.refresh_stats.setEnabled(False)
        self.refresh_stats.setText("Refreshing…")
        self.stats_worker = StatsWorker(self.window_, ids, self)
        self.stats_worker.result.connect(self.stats_ready)
        self.stats_worker.finished.connect(self.stats_finished)
        self.stats_worker.start()

    def stats_ready(self, stats: dict[str, Any] | None) -> None:
        if stats and stats.get("platforms"):
            self.stats, self.stats_at = stats, time.monotonic()
        self.refresh()

    def stats_finished(self) -> None:
        if self.stats_worker:
            self.stats_worker.wait()
            self.stats_worker.deleteLater()
        self.stats_worker = None
        self.refresh_stats.setEnabled(True)
        self.refresh_stats.setText("Refresh Views")

    def stop_for_quit(self) -> None:
        if self.stats_worker:
            self.stats_worker.cancel.set()
            self.stats_worker.wait()

    # Rendering -------------------------------------------------------------------------------

    def platform_views(self, uploaded: list[Entry]) -> dict[str, int]:
        """Views per platform for uploaded videos, only where the platform reports them."""
        totals: dict[str, int] = {}
        for entry in uploaded:
            counts = self.stats["videos"].get(entry.remote_id, {}) if entry.remote_id else {}
            if "views" in counts:
                totals[entry.platform] = totals.get(entry.platform, 0) + counts["views"]
        return totals

    def views_label(self, group: Group) -> str:
        """Views added up over every platform that reports them."""
        known = [
            self.stats["videos"][e.remote_id]["views"]
            for e in group.entries
            if e.remote_id and "views" in self.stats["videos"].get(e.remote_id, {})
        ]
        return f"{compact(sum(known))} views" if known else ""

    def refresh(self) -> None:
        self.entries = entries = self.collect()
        upcoming = activity.upcoming(entries)
        recent = activity.recent(entries)
        attention = [e for e in entries if e.kind == activity.ATTENTION]
        week = activity.since(recent, datetime.now(UTC) - timedelta(days=7))

        next_time = next((e for e in upcoming if e.when), None)
        self.upcoming_tile.set(
            str(len(activity.grouped(upcoming))),
            "Next: " + friendly(next_time.when) if next_time else "Nothing scheduled",
        )
        self.uploaded_tile.set(
            str(len(activity.grouped(recent))),
            f"{week} uploads in the last 7 days" if week != 1 else "1 upload in the last 7 days",
        )
        self.attention_tile.set(
            str(len(attention)), "All clear" if not attention else "See the list below"
        )
        connected = bool(self.window_.accounts.view and self.window_.accounts.view.connection)
        insights = bool(self.window_.tiktok.data.get("insights"))
        self.refresh_stats.setVisible(connected or insights)
        totals = self.platform_views(recent)
        self.views_tile.set(
            compact(sum(totals.values())) if totals else "—",
            " · ".join(f"{PLATFORM_NAMES[p]} {compact(v)}" for p, v in totals.items())
            if totals
            else "Connect an account to see views"
            if not (connected or insights)
            else "Choose Refresh Views to load them",
        )
        self.views_tile.note.setToolTip(
            "Views of the videos you uploaded with ShortsScheduler, added up across platforms."
            + ("" if insights else "\nTikTok views need a Buffer personal API key (Settings).")
        )

        self.attention.setVisible(bool(attention))
        self.attention.show_rows([(Group((e,)), "") for e in attention[:5]])
        self.coming.show_rows([(g, "") for g in activity.grouped(upcoming)[:6]])
        self.recent.show_rows([(g, self.views_label(g)) for g in activity.grouped(recent)[:6]])
        self.render_platforms(entries)
        self.entries_changed.emit(entries)

    def render_platforms(self, entries: list[Entry]) -> None:
        w = self.window_
        view = w.accounts.view
        youtube = [e for e in entries if e.platform == "youtube"]
        connection = view.connection if view else None
        self.youtube.set_connected(
            connection is not None,
            "Connected" if view and view.state.startswith("Connected") else "Check account",
            "Good" if view and view.state.startswith("Connected") else "Warn",
            known=w.accounts.checked or connection is not None,
        )
        self.youtube.account.setText(
            connection.channel.title
            + (f" · {connection.channel.handle}" if connection.channel.handle else "")
            if connection
            else "Connect your channel in Accounts to start uploading."
            if w.accounts.checked
            else "Checking your YouTube connection…"
        )
        channel = self.stats.get("channel", {})
        self.youtube.set_numbers(
            (str(sum(e.kind == activity.DONE for e in youtube)), "UPLOADED"),
            (compact(channel["views"]) if "views" in channel else "—", "VIEWS"),
            (compact(channel["subscribers"]) if "subscribers" in channel else "—", "SUBSCRIBERS"),
        )
        self.youtube.note.setText(
            "Counts come from YouTube and can lag by a few minutes."
            if channel
            else "View counts appear after YouTube is connected."
            if not connection
            else "Choose Refresh Views to load counts from YouTube."
        )
        tiktok = [e for e in entries if e.platform == "tiktok"]
        tconnection = w.tiktok.data.get("connection")
        live = bool(tconnection and tconnection.get("status") == "Connected")
        self.tiktok.set_connected(
            bool(tconnection),
            "Connected" if live else "Sign in again",
            "Good" if live else "Bad",
            known=w.tiktok.loaded,
        )
        self.tiktok.account.setText(
            f"@{tconnection.get('name')}"
            if tconnection
            else "Connect TikTok through Buffer in Accounts."
            if w.tiktok.loaded
            else "Checking your TikTok connection…"
        )
        views = self.platform_views([e for e in tiktok if e.kind == activity.DONE])
        self.tiktok.set_numbers(
            (str(sum(e.kind == activity.DONE for e in tiktok)), "POSTED"),
            (str(sum(e.kind == activity.UPCOMING for e in tiktok)), "SCHEDULED"),
            (compact(views["tiktok"]) if "tiktok" in views else "—", "VIEWS"),
        )
        self.tiktok.note.setText(
            "Counts come from Buffer and refresh about once a day."
            if w.tiktok.data.get("insights")
            else "To see TikTok views, add a Buffer personal API key in Settings."
        )
