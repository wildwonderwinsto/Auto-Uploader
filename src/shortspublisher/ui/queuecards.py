"""Video queues as the person sees them: one card each, however many platforms they post to.

A queue (schema 17) keeps its name, times and platforms; its YouTube videos are the recurring plan
it links to, and its TikTok, Instagram and Facebook videos wait in `queue_items` until each is
sent to Buffer. Older queues (YouTube only, made before schema 17) are plans without a queue row
and show the same way while they still have videos waiting. Nothing here changes anything; the
cards only describe saved records and ask the controller to act.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from ..cadence import Rule
from .components import card, flow, icon_button, muted, pill
from .formatting import timestamp
from .icons import PLATFORM_NAMES, image, logo
from .recurring import outcome, rule_label

if TYPE_CHECKING:
    from .social import SocialController

ORDER = ("youtube", "tiktok", "instagram", "facebook")
UNSTARTED = ("Queued", "Needs Attention")
IN_BUFFER = (
    "Uploading to media host",
    "Hosted and ready",
    "Waiting for Buffer limit",
    "Sending to Buffer",
    "Scheduled in Buffer",
    "Publishing",
)


@dataclass(frozen=True)
class VideoQueue:
    key: str  # the queue's ID, or "plan:<ID>" for an older YouTube-only queue
    name: str
    rule: dict[str, Any]
    background: bool
    row: dict[str, Any] | None  # its `queues` record
    plan: dict[str, Any] | None  # its YouTube recurring plan

    @property
    def platforms(self) -> list[str]:
        found = set(self.row["platforms"] if self.row else ()) | (
            {"youtube"} if self.plan else set()
        )
        return [p for p in ORDER if p in found]

    @property
    def enabled(self) -> bool:
        return bool(self.row["enabled"] if self.row else self.plan and self.plan["enabled"])


def video_queues(data: dict[str, Any]) -> list[VideoQueue]:
    plans = {p["id"]: p for p in data.get("plans", [])}
    automatic = set(data.get("automatic", []))
    found = []
    linked = set()
    for row in data.get("queues", []):
        plan = plans.get(row["plan_id"]) if row["plan_id"] else None
        linked.add(row["plan_id"])
        found.append(
            VideoQueue(row["id"], row["name"], row["rule"], bool(row["background"]), row, plan)
        )
    waiting = {
        i["plan_id"] for i in data.get("items", []) if i["state"] in UNSTARTED and not i["job_id"]
    }
    for plan in data.get("plans", []):
        if plan["id"] in automatic or plan["id"] in linked or plan["id"] not in waiting:
            continue  # an auto upload's own plan, or an older queue that has finished
        found.append(
            VideoQueue(
                "plan:" + plan["id"],
                plan["name"],
                plan["rule"],
                bool(plan["background"]),
                None,
                plan,
            )
        )
    return found


def waiting_videos(queue: VideoQueue, data: dict[str, Any]) -> list[int]:
    """The queue's videos that haven't gone anywhere yet, in order (YouTube's first)."""
    order: list[int] = []
    if queue.plan:
        for item in data.get("items", []):
            if (
                item["plan_id"] == queue.plan["id"]
                and item["state"] in UNSTARTED
                and not item["job_id"]
                and item["video_id"] not in order
            ):
                order.append(item["video_id"])
    if queue.row:
        for item in data.get("queue_items", []):
            if (
                item["queue_id"] == queue.row["id"]
                and item["state"] in ("Waiting", "Needs attention")
                and item["video_id"] not in order
            ):
                order.append(item["video_id"])
    return order


@dataclass(frozen=True)
class QueueView:
    """Everything one card shows, so an unchanged card is never rebuilt (no flicker)."""

    key: str
    title: str
    platforms: tuple[str, ...]
    state: str
    tone: str
    summary: str
    lines: tuple[tuple[str, str], ...]
    videos: tuple[str, ...]
    enabled: bool


def describe(
    queue: VideoQueue,
    data: dict[str, Any],
    socials: dict[str, SocialController],
    titles: dict[int, str],
    paused: bool,
) -> QueueView:
    rows: dict[int, dict[str, str]] = {}
    lines: list[tuple[str, str]] = []
    left = 0
    if plan := queue.plan:
        items = [i for i in data.get("items", []) if i["plan_id"] == plan["id"]]
        waiting = [i for i in items if i["state"] in UNSTARTED and not i["job_id"]]
        left += len(waiting)
        words = (
            f"{len(waiting)} waiting · next posting time {timestamp(plan['next_slot'], weekday=True)}"
            if waiting and plan["enabled"]
            else f"{len(waiting)} waiting · paused"
            if waiting
            else "Nothing waiting"
        )
        lines.append(("youtube", words))
        for item in items:
            if item["state"] != "Removed":
                state = "Waiting" if item in waiting else outcome(item)
                rows.setdefault(item["video_id"], {})["youtube"] = state
    if row := queue.row:
        for platform in row["platforms"]:
            social = socials.get(platform)
            posts = {p["id"]: p for p in (social.data["posts"] if social else [])}
            items = [
                i
                for i in data.get("queue_items", [])
                if i["queue_id"] == row["id"] and i["platform"] == platform
            ]
            waiting = [i for i in items if i["state"] in ("Waiting", "Needs attention")]
            left += len(waiting)
            sending = [
                posts[i["post_id"]]
                for i in items
                if i["post_id"] in posts and posts[i["post_id"]]["state"] in IN_BUFFER
            ]
            words = (
                f"Next post {timestamp(sending[0]['due_at'], weekday=True)}"
                if sending and sending[0]["due_at"]
                else "Posting now"
                if sending
                else "Nothing in Buffer yet"
                if waiting
                else "Nothing waiting"
            )
            if waiting:
                words += f" · {len(waiting)} waiting their turn"
            lines.append((platform, words))
            for item in items:
                post = posts.get(item["post_id"] or "")
                state = (
                    "Posted"
                    if post and post["state"] == "Sent by Buffer"
                    else f"Scheduled {timestamp(post['due_at'], weekday=True)}"
                    if post and post["state"] in IN_BUFFER and post["due_at"]
                    else post["state"]
                    if post
                    else item["reason"] or item["state"]
                    if item["state"] == "Needs attention"
                    else "Waiting"
                )
                rows.setdefault(item["video_id"], {})[platform] = state
    state, tone = (
        ("Paused", "Neutral")
        if not queue.enabled
        else ("Done", "Neutral")
        if not left
        else ("Held back", "Warn")
        if paused
        else ("On", "Good")
    )
    videos = tuple(
        f"{number}. {titles.get(video, 'Video')} · "
        + " · ".join(
            f"{PLATFORM_NAMES[k]}: {v}"
            for k, v in sorted(by.items(), key=lambda p: ORDER.index(p[0]))
        )
        for number, (video, by) in enumerate(list(rows.items())[:12], 1)
    )
    return QueueView(
        queue.key,
        queue.name,
        tuple(queue.platforms),
        state,
        tone,
        rule_label(Rule.decode(queue.rule)),
        tuple(lines),
        videos,
        queue.enabled,
    )


def build_card(view: QueueView, act: Callable[[str, str], None]) -> QFrame:
    """One queue: its times, how each platform is doing, its videos, and Edit · Pause · Delete."""
    frame, body = card(20, 10)
    frame.setAccessibleName(f"Video queue {view.title}")
    top = QHBoxLayout()
    top.setSpacing(6)
    for platform in view.platforms:
        badge = image(logo(platform, 22))
        badge.setToolTip(PLATFORM_NAMES[platform])
        top.addWidget(badge)
    top.addSpacing(6)
    title = QLabel(view.title)
    title.setObjectName("Heading")
    top.addWidget(title, 1)
    top.addWidget(pill(view.state, view.tone))
    body.addLayout(top)
    body.addWidget(muted(view.summary))
    for platform, words in view.lines:
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(image(logo(platform, 16)))
        line.addWidget(muted(words, "Subtle"), 1)
        body.addLayout(line)
    if view.videos:
        body.addWidget(muted("VIDEOS, IN ORDER", "Eyebrow"))
        body.addWidget(muted("\n".join(view.videos), "Subtle"))
    edit = icon_button(
        "Edit…", "edit", tip="Change the videos, their order, the times or the platforms"
    )
    edit.setAccessibleName(f"Edit Queue {view.title}…")
    edit.clicked.connect(lambda: act("edit", view.key))
    pause = (
        icon_button("Pause Queue", "pause")
        if view.enabled
        else icon_button("Resume Queue", "play", role="Primary")
    )
    pause.clicked.connect(lambda: act("pause" if view.enabled else "resume", view.key))
    delete = icon_button("Delete Queue…", "trash", role="Danger")
    delete.setToolTip("Stops the queue. Videos already uploaded stay where they are.")
    delete.clicked.connect(lambda: act("delete", view.key))
    body.addWidget(flow(edit, pause, delete))
    return frame
