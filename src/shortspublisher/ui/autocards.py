"""Watched-folder auto uploads as the person sees them: one card each, however many platforms.

Each platform keeps its own saved profile and its own approval (YouTube's through
AutomationController; TikTok's, Instagram's and Facebook's through their SocialControllers).
Profiles made together share a folder and a name, so a card shows them as one auto upload and its
Pause, Edit and Delete act on all of them at once. Nothing here posts or decides anything; the
cards only describe saved records and start each platform's own control.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
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


@dataclass
class AutoUpload:
    """One auto upload: its folder and name, and each platform's saved profile."""

    folder_id: str
    name: str
    youtube: dict[str, Any] | None = None
    buffer: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return self.folder_id, self.name

    @property
    def platforms(self) -> list[str]:
        found = {"youtube"} if self.youtube else set()
        return [p for p in ORDER if p in found or p in self.buffer]

    @property
    def profiles(self) -> list[dict[str, Any]]:
        return [p for p in (self.youtube, *self.buffer.values()) if p]

    @property
    def settings(self) -> dict[str, Any]:
        return self.profiles[0]["settings"]

    @property
    def paused(self) -> bool:
        return not any(p["enabled"] for p in self.profiles)

    @property
    def revoked(self) -> bool:
        return any(p["revoked"] for p in self.profiles)


def auto_uploads(
    youtube: Iterable[dict[str, Any]], socials: Iterable[SocialController]
) -> list[AutoUpload]:
    found: dict[tuple[str, str], AutoUpload] = {}
    for p in youtube:
        if not p["removed"]:
            group = found.setdefault((p["folder_id"], p["name"]), AutoUpload(*_key(p)))
            group.youtube = p
    for social in socials:
        for p in social.data["profiles"]:  # removed ones are never loaded
            group = found.setdefault((p["folder_id"], p["name"]), AutoUpload(*_key(p)))
            group.buffer[social.key] = p
    return list(found.values())


def _key(p: dict[str, Any]) -> tuple[str, str]:
    return p["folder_id"], p["name"]


@dataclass(frozen=True)
class CardView:
    """Everything one card shows, so an unchanged card is never rebuilt (no flicker)."""

    key: tuple[str, str]
    title: str
    platforms: tuple[str, ...]
    state: str
    tone: str
    summary: str
    lines: tuple[tuple[str, str], ...]  # (platform, its status)
    recent: tuple[str, ...]
    paused: bool
    revoked: bool


def describe(
    group: AutoUpload,
    *,
    folders: list[dict[str, Any]],
    recurring: dict[str, Any],
    decisions: list[dict[str, Any]],
    socials: dict[str, SocialController],
    youtube_headline: Callable[[dict[str, Any], bool], str],
) -> CardView:
    folder = next((f for f in folders if f["id"] == group.folder_id), None)
    watching = bool(folder and folder["enabled"] and not folder["removed"])
    rule = Rule.decode(group.settings["rule"])
    name = folder["name"] if folder else Path(_path(group)).name or "Folder unavailable"
    summary = f"Folder: {name} · {rule_label(rule)}" + (
        "" if watching else " · the folder isn't being watched"
    )
    lines: list[tuple[str, str]] = []
    recent: dict[str, dict[str, str]] = {}
    blocked = False
    if p := group.youtube:
        plan = next((x for x in recurring.get("plans", []) if x["id"] == p["plan_id"]), None)
        items = {i["id"]: i for i in recurring.get("items", [])}
        mine = [d for d in decisions if d["profile_id"] == p["id"]]
        waiting = sum(
            d["item_id"] in items and items[d["item_id"]]["state"] == "Queued" for d in mine
        )
        words = youtube_headline(p, watching)
        blocked |= not words.startswith("Active")
        if p["enabled"] and not p["revoked"] and plan:
            words += f" · next posting time {timestamp(plan['next_slot'], weekday=True)}"
        if waiting:
            words += f" · {waiting} waiting their turn"
        lines.append(("youtube", words))
        for d in mine:
            if d["state"] != "Excluded existing" and d["profile_revision"] == p["revision"]:
                item = items.get(d["item_id"])
                recent.setdefault(d["name"], {})["youtube"] = outcome(item) if item else d["state"]
    for key, p in group.buffer.items():
        social = socials[key]
        words = social.automation_headline(p)
        blocked |= not words.startswith("Active")
        if p["enabled"] and not p["revoked"]:
            words += " · " + social.automation_next(p)
        lines.append((key, words))
        for d in social.data.get("decisions", {}).get(p["id"], []):
            if d["state"] != "Excluded existing" and d["profile_revision"] == p["revision"]:
                recent.setdefault(d["name"], {})[key] = d["state"]
    state, tone = (
        ("Needs approval", "Warn")
        if group.revoked
        else ("Paused", "Neutral")
        if group.paused
        else ("Held back", "Warn")
        if blocked or not watching
        else ("On", "Good")
    )
    rows = tuple(
        f"{file} · "
        + " · ".join(f"{PLATFORM_NAMES[k]}: {v}" for k, v in sorted(by.items(), key=_order))
        for file, by in list(recent.items())[:8]
    )
    return CardView(
        group.key,
        group.name,
        tuple(group.platforms),
        state,
        tone,
        summary,
        tuple(lines),
        rows,
        group.paused,
        group.revoked,
    )


def _order(pair: tuple[str, str]) -> int:
    return ORDER.index(pair[0])


def _path(group: AutoUpload) -> str:
    binding = group.youtube["folder_binding"] if group.youtube else {}
    return str(binding.get("path", ""))


def build_card(view: CardView, act: Callable[[str, tuple[str, str]], None]) -> QFrame:
    """One auto upload: what it does, how each platform is doing, and Edit · Pause · Delete."""
    frame, body = card(20, 10)
    frame.setAccessibleName(f"Auto upload {view.title}")
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
    if view.recent:
        body.addWidget(muted("RECENT VIDEOS", "Eyebrow"))
        body.addWidget(muted("\n".join(view.recent), "Subtle"))
    # Clear names: these act on the auto upload, never on videos already on a platform.
    edit = icon_button("Edit…", "edit", tip="Change anything; videos not uploaded yet follow it")
    edit.setAccessibleName(f"Edit Auto Upload {view.title}…")
    edit.clicked.connect(lambda: act("edit", view.key))
    buttons = [edit]
    if not view.revoked:
        pause = (
            icon_button("Resume Auto Upload", "play", role="Primary")
            if view.paused
            else icon_button("Pause Auto Upload", "pause")
        )
        pause.clicked.connect(lambda: act("resume" if view.paused else "pause", view.key))
        buttons.append(pause)
    delete = icon_button("Delete Auto Upload…", "trash", role="Danger")
    delete.setToolTip("Stops it on every platform. Videos already uploaded stay where they are.")
    delete.clicked.connect(lambda: act("delete", view.key))
    buttons.append(delete)
    body.addWidget(flow(*buttons))
    return frame
