"""Video queues that span platforms (schema 17): a name, posting times and platforms.

A queue's YouTube videos stay in the recurring plan it links to; the dispatcher uploads them as
before. Its TikTok, Instagram and Facebook videos wait here, in order: each platform's
`BufferPostService.advance_queues` sends the next one to Buffer only after the one before it has
posted. Until a video is sent, editing the queue, or the video's own details, still applies to it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from typing import Any

from .cadence import Rule
from .library import Library
from .scheduling import ScheduleError, stamp

BUFFER_PLATFORMS = ("tiktok", "instagram", "facebook")
WAITING, SENT, REMOVED, ATTENTION = "Waiting", "Sent", "Removed", "Needs attention"
CONSENT = (
    "Post these exact videos PUBLICLY, one at a time and in this order, at the queue's posting "
    "times, to the platforms ticked. Each is sent to Buffer only after the one before it has "
    "posted, so changes to the queue or to a video's details apply to every video not yet sent. "
    "Automatic publishing must also be turned on in the sidebar."
)


class QueueService:
    def __init__(self, library: Library, clock: Callable[[], datetime]):
        self.library = library
        self.clock = clock

    @staticmethod
    def read(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM queues WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ScheduleError("That queue no longer exists. Reload and try again.")
        return decode(row)

    def save(
        self,
        *,
        name: str,
        rule: Rule,
        videos: dict[str, list[int]],
        plan_id: str | None = None,
        background: bool = False,
        identity: str = "",
        revision: int = 0,
    ) -> str:
        """Create a queue, or replace an existing one's settings and waiting videos.

        `videos` holds each Buffer platform's videos in order. A video already sent to Buffer
        keeps its record; every other waiting video is replaced by the new list."""
        rule.validate()
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ScheduleError("Name the queue using 1–100 characters.")
        if set(videos) - set(BUFFER_PLATFORMS):
            raise ScheduleError("Queues post to TikTok, Instagram and Facebook through Buffer.")
        for chosen in videos.values():
            if len(set(chosen)) != len(chosen):
                raise ScheduleError("Each video can be in a queue only once.")
        when = stamp(self.clock())
        platforms = [p for p in BUFFER_PLATFORMS if videos.get(p)]
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if identity:
                old = self.read(db, identity)
                if old["revision"] != revision or old["removed"]:
                    raise ScheduleError("This queue changed elsewhere. Reload and edit it again.")
                db.execute(
                    """UPDATE queues SET name=?,rule=?,platforms=?,plan_id=?,background=?,enabled=1,
                    revision=revision+1,consent=?,consent_at=?,updated_at=? WHERE id=?""",
                    (
                        name,
                        json.dumps(rule.encode()),
                        json.dumps(platforms),
                        plan_id,
                        int(background),
                        CONSENT,
                        when,
                        when,
                        identity,
                    ),
                )
            else:
                identity = uuid.uuid4().hex
                db.execute(
                    """INSERT INTO queues(id,name,rule,platforms,plan_id,background,enabled,removed,
                    revision,consent,consent_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,1,0,1,?,?,?,?)""",
                    (
                        identity,
                        name,
                        json.dumps(rule.encode()),
                        json.dumps(platforms),
                        plan_id,
                        int(background),
                        CONSENT,
                        when,
                        when,
                        when,
                    ),
                )
            db.execute(
                """UPDATE queue_items SET state=?,updated_at=?,
                reason='Replaced when the queue was changed' WHERE queue_id=? AND state IN (?,?)""",
                (REMOVED, when, identity, WAITING, ATTENTION),
            )
            for platform in platforms:
                sent = {
                    r[0]
                    for r in db.execute(
                        "SELECT video_id FROM queue_items WHERE queue_id=? AND platform=? AND state=?",
                        (identity, platform, SENT),
                    )
                }
                position = db.execute(
                    "SELECT coalesce(max(position),0) FROM queue_items WHERE queue_id=?",
                    (identity,),
                ).fetchone()[0]
                for video in videos[platform]:
                    if video in sent:
                        continue  # already on its way, or posted: never sent twice
                    position += 1
                    db.execute(
                        """INSERT INTO queue_items(id,queue_id,platform,video_id,position,state,
                        created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            uuid.uuid4().hex,
                            identity,
                            platform,
                            video,
                            position,
                            WAITING,
                            when,
                            when,
                        ),
                    )
        return identity

    def link(self, identity: str, plan_id: str | None) -> None:
        """The YouTube plan carrying this queue's YouTube videos (None: it has none)."""
        with closing(self.library.connect()) as db, db:
            db.execute(
                "UPDATE queues SET plan_id=?,updated_at=? WHERE id=?",
                (plan_id, stamp(self.clock()), identity),
            )

    def control(self, identity: str, action: str) -> None:
        """pause, resume or remove. Removing leaves nothing waiting; posts already in Buffer
        are cancelled under Schedule, never here."""
        if action not in ("pause", "resume", "remove"):
            raise ScheduleError("Unknown queue action.")
        when = stamp(self.clock())
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = self.read(db, identity)
            if old["removed"]:
                raise ScheduleError("That queue was already deleted.")
            db.execute(
                "UPDATE queues SET enabled=?,removed=?,revision=revision+1,updated_at=? WHERE id=?",
                (int(action == "resume"), int(action == "remove"), when, identity),
            )
            if action == "remove":
                db.execute(
                    """UPDATE queue_items SET state=?,updated_at=?,
                    reason='Queue deleted before this video was sent' WHERE queue_id=?
                    AND state IN (?,?)""",
                    (REMOVED, when, identity, WAITING, ATTENTION),
                )

    def state(self) -> dict[str, Any]:
        with closing(self.library.connect()) as db:
            db.execute("BEGIN")
            queues = [
                decode(r)
                for r in db.execute("SELECT * FROM queues WHERE removed=0 ORDER BY created_at,id")
            ]
            items = [
                dict(r)
                for r in db.execute(
                    """SELECT i.* FROM queue_items i JOIN queues q ON q.id=i.queue_id
                    WHERE q.removed=0 AND i.state!=? ORDER BY i.position,i.id""",
                    (REMOVED,),
                )
            ]
        return {"queues": queues, "items": items}


def decode(row: sqlite3.Row) -> dict[str, Any]:
    queue = dict(row)
    queue["rule"] = json.loads(queue["rule"])
    queue["platforms"] = json.loads(queue["platforms"])
    return queue
