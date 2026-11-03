"""Tells the person, once, about each upload that didn't go out: a macOS notification.

Problems are read from the library alone, so the open app and the background service (whichever
dispatches) announce them the same way; `alerts.json` beside the library remembers what was
already announced (no schema change). No credentials or network access. Notifications are sent
only when an entry point turns them on (`ENABLED`), never in tests.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from contextlib import closing
from typing import TYPE_CHECKING

from filelock import FileLock, Timeout

from .activity import BUFFER_ATTENTION, BUFFER_NAMES, plain_reason, title_of

if TYPE_CHECKING:
    from .library import Library

FILE = "alerts.json"
ENABLED = False  # set by the app's and the background service's entry points
TITLE = "ShortsScheduler"
log = logging.getLogger(__name__)
YOUTUBE_FAILED = ("Interrupted", "Failed", "Needs Reconciliation", "Paused")


def problems(library: Library) -> dict[str, str]:
    """Every current problem: a stable key (what and in which state) → one plain line."""
    found: dict[str, str] = {}
    with closing(library.connect()) as db:
        for row in db.execute(
            "SELECT id,state,reason,snapshot FROM scheduled_jobs "
            "WHERE state IN ('Needs Attention','Missed')"
        ):
            title = title_of(json.loads(row["snapshot"]))
            why = "it missed its time" if row["state"] == "Missed" else plain_reason(row["reason"])
            found[f"job:{row['id']}:{row['state']}"] = f"YouTube: “{title}” didn't upload — {why}."
        for row in db.execute(
            "SELECT id,state,error,snapshot FROM upload_attempts WHERE released=0 AND state IN "
            f"({','.join('?' * len(YOUTUBE_FAILED))})",
            YOUTUBE_FAILED,
        ):
            title = title_of(json.loads(row["snapshot"]))
            found[f"upload:{row['id']}:{row['state']}"] = (
                f"YouTube: “{title}” upload {row['state'].lower()} — {plain_reason(row['error'])}."
            )
        for row in db.execute(
            "SELECT id,reason,snapshot FROM recurring_items WHERE state='Needs Attention'"
        ):
            title = title_of(json.loads(row["snapshot"]))
            found[f"queue:{row['id']}"] = (
                f"YouTube queue: “{title}” is waiting for you — {plain_reason(row['reason'])}."
            )
        for key, name in BUFFER_NAMES.items():
            for row in db.execute(
                f"SELECT id,state,error,caption FROM {key}_posts WHERE released=0 AND state IN "
                f"({','.join('?' * len(BUFFER_ATTENTION))})",
                BUFFER_ATTENTION,
            ):
                title = next(iter((row["caption"] or "").strip().splitlines()), "")[:60]
                found[f"{key}:{row['id']}:{row['state']}"] = (
                    f"{name}: “{title or 'a video'}” didn't post — {row['state'].lower()}."
                )
        for row in db.execute(
            "SELECT id,platform,reason FROM queue_items WHERE state='Needs attention'"
        ):
            name = BUFFER_NAMES.get(row["platform"], row["platform"])
            found[f"queueitem:{row['id']}"] = f"{name} queue: a video is waiting for you."
        for row in db.execute("SELECT platform,channel FROM account_connection"):
            platform = BUFFER_NAMES.get(row["platform"])
            try:
                status = json.loads(row["channel"]).get("status")
            except (ValueError, AttributeError):
                continue
            if platform and status == "Reconnect required":
                found[f"reconnect:{row['platform']}"] = (
                    f"{platform} needs you to reconnect Buffer in Accounts; nothing posts until then."
                )
    return found


def announce(library: Library) -> list[str]:
    """Notifies about problems not announced before, and returns their lines. A problem that
    clears and comes back is announced again."""
    path = library.path.parent / FILE
    try:
        with FileLock(str(path) + ".lock", timeout=0):
            current = problems(library)
            try:
                seen = set(json.loads(path.read_text()))
            except (OSError, ValueError):
                seen = set()
            new = [line for key, line in current.items() if key not in seen]
            if set(current) != seen:
                path.write_text(json.dumps(sorted(current)))
    except Timeout:
        return []  # the other process is announcing right now
    if new:
        send(
            new[0] if len(new) == 1 else f"{len(new)} uploads need you",
            "Open ShortsScheduler to fix it."
            if len(new) == 1
            else " · ".join(new[:2]) + (" …" if len(new) > 2 else ""),
        )
    return new


def quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send(headline: str, body: str) -> None:
    """A macOS notification. Never raises: an upload must not fail because of a notification."""
    if not ENABLED or sys.platform != "darwin":
        return
    script = (
        f"display notification {quoted(body[:240])} with title {quoted(TITLE)} "
        f"subtitle {quoted(headline[:120])}"
    )
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        log.warning("Could not show a notification")
