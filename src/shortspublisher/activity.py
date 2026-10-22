"""One display timeline of uploads across platforms. Pure functions over loaded records; no I/O.

Nothing here decides whether anything may upload: it only describes saved records in plain words.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .scheduling import Job
from .upload_store import Attempt

UPCOMING, UPLOADING, DONE, ATTENTION = "upcoming", "uploading", "done", "attention"

TIKTOK_UPCOMING = (
    "Uploading to media host",
    "Hosted and ready",
    "Waiting for Buffer limit",
    "Sending to Buffer",
    "Scheduled in Buffer",
)
TIKTOK_ATTENTION = (
    "Failed",
    "Needs reconciliation",
    "Needs attention",
    "Hosting failed",
    "Removed in Buffer",
    "Manual completion required",
    "Missed — not sent",
    "Waiting for TikTok reconnection",
)


@dataclass(frozen=True)
class Entry:
    platform: str
    title: str
    when: str  # ISO 8601 with offset, or "" when there is no fixed time yet
    kind: str
    status: str
    remote_id: str = ""
    link: str = ""
    video_id: int | None = None
    detail: str = ""


def title_of(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("metadata", {}).get("title") or snapshot.get("filename") or "Video")


def youtube_uploads(attempts: Iterable[Attempt]) -> list[Entry]:
    entries = []
    for a in attempts:
        if a.state == "Cancelled":
            continue
        processed = a.processing in ("processed", "succeeded")
        link = f"https://youtu.be/{a.remote_id}" if a.remote_id else ""
        when = a.completed_at or a.updated_at or a.created_at
        if a.state in ("Public Verified", "Uploaded Private"):
            privacy = "Public" if a.state == "Public Verified" else "Private"
            kind, status = DONE, privacy if processed else f"{privacy} · YouTube is processing"
        elif a.state in ("Preparing", "Uploading", "Awaiting Confirmation"):
            size = a.snapshot.get("size") or 0
            share = f" · {a.acknowledged * 100 // size}%" if size else ""
            kind, status = UPLOADING, "Uploading" + share
        else:
            kind = ATTENTION
            status = {
                "Needs Reconciliation": "Check the upload result",
                "Interrupted": "Upload interrupted",
                "Paused": "Upload paused",
                "Failed": "Upload failed",
            }.get(a.state, a.state)
        entries.append(
            Entry(
                "youtube",
                title_of(a.snapshot),
                when,
                kind,
                status,
                a.remote_id,
                link,
                a.video_id,
                a.error,
            )
        )
    return entries


def youtube_plans(jobs: Iterable[Job], started: set[str]) -> list[Entry]:
    entries = []
    for job in jobs:
        if job.id in started:
            continue  # its upload record describes it from here on
        title = title_of(job.snapshot)
        if job.state == "Scheduled":
            approved = bool(job.execution_enabled)
            entries.append(
                Entry(
                    "youtube",
                    title,
                    job.scheduled_utc,
                    UPCOMING if approved else ATTENTION,
                    "Scheduled" if approved else "Waiting for your approval",
                    video_id=job.video_id,
                )
            )
        elif job.state in ("Needs Attention", "Missed"):
            entries.append(
                Entry(
                    "youtube",
                    title,
                    job.scheduled_utc,
                    ATTENTION,
                    "Missed its time" if job.state == "Missed" else "Needs attention",
                    video_id=job.video_id,
                    detail=job.reason,
                )
            )
    return entries


def youtube_queue(recurring: dict[str, Any]) -> list[Entry]:
    plans = {p["id"]: p for p in recurring.get("plans", [])}
    seen: set[str] = set()
    entries = []
    for item in recurring.get("items", []):
        plan = plans.get(item["plan_id"])
        title = title_of(item["snapshot"])
        if item["state"] == "Queued" and plan:
            first = plan["id"] not in seen and bool(plan["enabled"])
            seen.add(plan["id"])
            entries.append(
                Entry(
                    "youtube",
                    title,
                    plan["next_slot"] if first else "",
                    UPCOMING,
                    "Next in queue" if first else "Waiting in queue",
                    video_id=item["video_id"],
                )
            )
        elif item["state"] == "Needs Attention":
            entries.append(
                Entry(
                    "youtube",
                    title,
                    "",
                    ATTENTION,
                    "Needs attention",
                    video_id=item["video_id"],
                    detail=item.get("reason", ""),
                )
            )
    return entries


def tiktok_posts(posts: Iterable[dict[str, Any]]) -> list[Entry]:
    entries = []
    for post in posts:
        state = post["state"]
        if state == "Cancelled" or (post.get("released") and state != "Sent by Buffer"):
            continue
        title = (
            (post.get("caption") or "").strip().splitlines()[0][:90]
            if post.get("caption")
            else "TikTok video"
        )
        link = post.get("external_link") or ""
        if state == "Sent by Buffer":
            kind, status, when = DONE, "Posted", post.get("sent_at") or post.get("due_at") or ""
        elif state == "Publishing":
            kind, status, when = UPLOADING, "Posting now", post.get("due_at") or ""
        elif state in TIKTOK_UPCOMING:
            kind, status = UPCOMING, "Scheduled" if post.get("due_at") else "Posting soon"
            when = post.get("due_at") or ""
        else:
            kind, when = ATTENTION, post.get("due_at") or post.get("updated_at") or ""
            status = {
                "Needs reconciliation": "Check the post result",
                "Waiting for TikTok reconnection": "Reconnect TikTok",
                "Manual completion required": "Finish in the TikTok app",
                "Missed — not sent": "Missed its time",
            }.get(state, state)
        entries.append(
            Entry(
                "tiktok",
                title,
                when,
                kind,
                status,
                post.get("buffer_post_id", ""),
                link,
                post.get("video_id"),
                post.get("error", ""),
            )
        )
    return entries


def timeline(
    jobs: Iterable[Job],
    attempts: Iterable[Attempt],
    started: set[str],
    recurring: dict[str, Any],
    posts: Iterable[dict[str, Any]],
) -> list[Entry]:
    return [
        *youtube_uploads(attempts),
        *youtube_plans(jobs, started),
        *youtube_queue(recurring),
        *tiktok_posts(posts),
    ]


def instant(entry: Entry) -> datetime | None:
    try:
        value = datetime.fromisoformat(entry.when) if entry.when else None
    except ValueError:
        return None
    return value if value and value.tzinfo else None


def upcoming(entries: Iterable[Entry]) -> list[Entry]:
    """Soonest first; queue items without a fixed time follow, in queue order."""
    pending = [e for e in entries if e.kind in (UPCOMING, UPLOADING)]
    timed = sorted((e for e in pending if instant(e)), key=lambda e: instant(e))  # type: ignore[arg-type, return-value]
    return (
        [e for e in pending if e.kind == UPLOADING and not instant(e)]
        + timed
        + [e for e in pending if not instant(e) and e.kind != UPLOADING]
    )


def recent(entries: Iterable[Entry]) -> list[Entry]:
    done = [e for e in entries if e.kind == DONE]
    return sorted(done, key=lambda e: instant(e) or datetime.min.astimezone(), reverse=True)


def since(entries: Iterable[Entry], start: datetime) -> int:
    return sum(1 for e in entries if (moment := instant(e)) and moment >= start)


def week_start(now: datetime) -> datetime:
    return now - timedelta(days=7)


PLATFORM_ORDER = ("youtube", "tiktok", "instagram", "facebook", "snapchat")


@dataclass(frozen=True)
class Group:
    """One video on one or more platforms, shown as a single row."""

    entries: tuple[Entry, ...]

    @property
    def kind(self) -> str:
        return self.entries[0].kind

    @property
    def platforms(self) -> tuple[str, ...]:
        found = {e.platform for e in self.entries}
        return tuple(p for p in PLATFORM_ORDER if p in found)

    @property
    def title(self) -> str:
        # The shared title; a TikTok caption carries hashtags, so prefer another platform's.
        named = [e for e in self.entries if e.platform != "tiktok"] or list(self.entries)
        return named[0].title

    @property
    def when(self) -> str:
        times = [e for e in self.entries if instant(e)]
        if not times:
            return ""
        pick = max if self.kind == DONE else min
        return pick(times, key=lambda e: instant(e)).when  # type: ignore[arg-type, return-value]

    @property
    def status(self) -> str:
        statuses = {e.status for e in self.entries}
        if len(statuses) == 1:
            return self.entries[0].status
        return {DONE: "Uploaded", UPLOADING: "Uploading"}.get(self.kind, "Scheduled")


def grouped(entries: Iterable[Entry]) -> list[Group]:
    """Combine the same video's entries of one kind; keep the incoming order otherwise.

    Problems stay one row per platform: each needs its own fix."""
    groups: dict[object, list[Entry]] = {}
    order: list[object] = []
    for entry in entries:
        key: object = (
            (entry.kind, entry.video_id)
            if entry.video_id is not None and entry.kind != ATTENTION
            else id(entry)
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(entry)
    return [Group(tuple(groups[key])) for key in order]
