"""Finite, explicitly approved queues feeding the existing scheduled Public uploader.

No HTTP or credential access. Opportunities are durable; old opportunities are never replayed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any
from zoneinfo import ZoneInfo

from .cadence import Rule
from .library import LibraryError
from .media import checkpoint
from .preparation import media_token
from .scheduling import Job, ScheduleError, ScheduleService, stamp
from .source import fingerprint, signature

CONSENT = (
    "Authorize automatic PUBLIC YouTube uploads of only the specifically listed reviewed videos, "
    "to this exact channel, under this recurring rule and queue order. No second dialog appears at "
    "each opportunity. Opportunities are upload start times, not guaranteed publications. Empty, "
    "blocked and missed opportunities are skipped without catch-up. Future files are not authorized. "
    "No required manual restriction or unresolved YouTube setting may remain for these videos."
)


def normalized(value: Any) -> Any:
    return json.loads(json.dumps(value))


def item_binding(plan: dict[str, Any], video_id: int, snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                plan["id"],
                plan["channel_id"],
                plan["rule"],
                plan["background"],
                plan["approved_revision"],
                plan["approved_limit"],
                video_id,
                snapshot,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def reservation_conflict(
    db: sqlite3.Connection, channel: str, video: int, digest: str, job: str = "", queue: str = ""
) -> None:
    """`queue`: a plan being edited, whose own unstarted videos it is about to replace."""
    if db.execute(
        """SELECT 1 FROM recurring_items WHERE channel_id=? AND state!='Removed'
        AND (video_id=? OR source_hash=?) AND (job_id IS NULL OR job_id!=?)
        AND NOT (plan_id=? AND state IN ('Queued','Needs Attention') AND job_id IS NULL)""",
        (channel, video, digest, job, queue),
    ).fetchone():
        raise ScheduleError(
            "This video or identical content already belongs to a recurring queue on this channel. Remove its unstarted entry before approving another use."
        )


def validate_job(db: sqlite3.Connection, job: Job, *, start: bool) -> None:
    row = db.execute("SELECT * FROM recurring_items WHERE job_id=?", (job.id,)).fetchone()
    if row is None:
        return
    item = dict(row)
    plan = dict(
        db.execute("SELECT * FROM recurring_plans WHERE id=?", (item["plan_id"],)).fetchone()
    )
    plan["rule"] = json.loads(plan["rule"])
    snapshot = json.loads(item["snapshot"])
    slot = db.execute("SELECT * FROM recurring_slots WHERE job_id=?", (job.id,)).fetchone()
    if (
        item["state"] != "Reserved"
        or not slot
        or slot["item_id"] != item["id"]
        or slot["instant"] != job.scheduled_utc
        or item["binding"] != item_binding(plan, item["video_id"], snapshot)
        or snapshot != job.snapshot
        or plan["channel_id"] != job.channel_id
        or not plan["consent_at"]
        or (start and not plan["enabled"])
    ):
        raise ScheduleError(
            "The recurring queue approval or enabled state no longer authorizes this exact upload."
        )


class RecurringService:
    def __init__(self, schedules: ScheduleService):
        self.schedules = schedules
        self.library = schedules.library
        self.clock = schedules.clock

    @staticmethod
    def plan(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM recurring_plans WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ScheduleError("Recurring plan no longer exists. Reload the queue.")
        result = dict(row)
        result["rule"] = json.loads(result["rule"])
        return result

    @staticmethod
    def event(db: sqlite3.Connection, plan: str, now: str, action: str, record: Any) -> None:
        db.execute(
            "INSERT INTO recurring_events(plan_id,created_at,action,record) VALUES(?,?,?,?)",
            (plan, now, action, json.dumps(record)),
        )

    def conflict(
        self,
        db: sqlite3.Connection,
        video: int,
        channel: str,
        digest: str,
        job: str = "",
        queue: str = "",
    ) -> None:
        self.schedules._conflicts(db, video, channel, digest, job, queue)
        # Recurring automation never interprets an acknowledged/released old upload as permission
        # to repost. Even failed attempts require a separate supported deliberate workflow.
        for row in db.execute(
            "SELECT * FROM upload_attempts WHERE channel_id=? AND state!='Cancelled'", (channel,)
        ):
            if row["video_id"] == video or json.loads(row["snapshot"]).get("sha256") == digest:
                raise ScheduleError(
                    "Publishing history already covers these bytes on this channel. Inspect its result; recurring approval cannot bypass it."
                )

    def retry(self, identity: str, cancel: Event) -> str:
        """Requeue an unstarted item after verifying the exact approved bytes and details.

        A filesystem attribute change can alter ctime without changing the video. Explicit
        retry may refresh that file binding, never the approved content, metadata or scope.
        No upload starts here and no missed opportunity is replayed.
        """
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM recurring_items WHERE id=?", (identity,)).fetchone()
            if not row or row["job_id"] or row["state"] not in ("Queued", "Needs Attention"):
                raise ScheduleError("Only an unstarted queued video can retry at the next time.")
            original = dict(row)
            snap = json.loads(row["snapshot"])
        # Hash outside the write transaction; recheck the row and file identity under its lock.
        with Path(snap["path"]).open("rb") as source:
            digest = fingerprint(source, cancel)
            observed = list(signature(os.fstat(source.fileno())))
        if digest != original["source_hash"]:
            raise ScheduleError("Video contents changed. Review and approve this video again.")
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM recurring_items WHERE id=?", (identity,)).fetchone()
            if not row or dict(row) != original:
                raise ScheduleError("The queued video changed during retry. Refresh and try again.")
            plan = self.plan(db, row["plan_id"])
            if item_binding(plan, row["video_id"], snap) != row["binding"]:
                raise ScheduleError("Queue approval changed. Review the queue again.")
            if automation := snap.get("automation"):
                video_row = db.execute(
                    "SELECT * FROM videos WHERE id=?", (row["video_id"],)
                ).fetchone()
                if not video_row:
                    raise ScheduleError("The video is no longer in the library.")
                video = self.library.decode(video_row)
                # _snapshot still validates the profile, folder, metadata revision and review
                # mode. This provisional update is rolled back if any check fails.
                updated = db.execute(
                    "UPDATE automation_decisions SET media_token=? "
                    "WHERE id=? AND item_id=? AND source_hash=? AND state='Queued'",
                    (media_token(video), automation, identity, digest),
                )
                if updated.rowcount != 1:
                    raise ScheduleError("This auto upload no longer approves the queued video.")
            current = self.schedules._snapshot(
                db, row["video_id"], row["channel_id"], automation=automation or ""
            )
            current["sha256"] = digest
            current = normalized(current)
            if current["stat"] != observed:
                raise ScheduleError("Source changed during retry. Try again once it is stable.")
            if any(
                current.get(k) != v for k, v in snap.items() if k not in ("stat", "reviewed_media")
            ):
                raise ScheduleError("Approved details changed. Review and approve the queue again.")
            db.execute("UPDATE recurring_items SET state='Removed' WHERE id=?", (identity,))
            self.conflict(db, row["video_id"], row["channel_id"], digest)
            db.execute(
                "UPDATE recurring_items SET state='Queued',reason='',snapshot=?,binding=? WHERE id=?",
                (json.dumps(current), item_binding(plan, row["video_id"], current), identity),
            )
            if automation:
                db.execute(
                    "UPDATE automation_decisions SET snapshot=?,updated_at=? WHERE id=?",
                    (json.dumps(current), stamp(self.clock()), automation),
                )
            self.skip_past(db, plan, "Retry waits for the next future posting time.")
            self.event(db, plan["id"], stamp(self.clock()), "Retry queued", {"item_id": identity})
            checkpoint(cancel)
            return str(self.plan(db, plan["id"])["next_slot"])

    def preview(
        self,
        videos: list[int],
        channel: str,
        rule: Rule,
        name: str,
        cancel: Event,
        *,
        background: bool = False,
        plan_id: str = "",
        limit: int | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """`replace`: an edit of the plan; these videos and this rule replace its unstarted
        videos and its times. Otherwise videos are added under its approved rule."""
        rule.validate()
        if not 1 <= len(videos) <= 100 or len(set(videos)) != len(videos):
            raise ScheduleError(
                "Select 1–100 distinct reviewed videos in the intended queue order."
            )
        name = name.strip()
        if not name or len(name) > 100:
            raise ScheduleError("Give the recurring plan a name of 1–100 characters.")
        with closing(self.library.connect()) as db:
            current_limit = db.execute(
                "SELECT daily_limit FROM publishing_control WHERE id=1"
            ).fetchone()[0]
            old = self.plan(db, plan_id) if plan_id else None
        if old:
            if channel != old["channel_id"] or (
                not replace
                and (
                    normalized(rule.encode()) != old["rule"]
                    or background != bool(old["background"])
                )
            ):
                raise ScheduleError(
                    "Rule and channel are immutable. Create a new plan with renewed approval to change them."
                )
            limit = old["approved_limit"]
        limit = current_limit if limit is None else limit
        if not 1 <= limit <= current_limit:
            raise ScheduleError(f"YouTube allows at most {current_limit} uploads a day.")
        opportunities = rule.next(self.clock())
        instant = opportunities[0]
        local = instant.astimezone(ZoneInfo(rule.zone))
        snapshots: list[dict[str, Any]] = []
        hashes = set()
        for video in videos:
            own = plan_id if replace else ""  # its own unstarted entry is being replaced
            proposal = self.schedules.preview(
                video,
                channel,
                local.replace(tzinfo=None).isoformat(),
                rule.zone,
                local.fold,
                cancel=cancel,
                queue=own,
            )
            snap = proposal.snapshot
            with closing(self.library.connect()) as db:
                self.conflict(db, video, channel, snap["sha256"], queue=own)
            if snap["sha256"] in hashes:
                raise ScheduleError(
                    "The selected batch contains identical bytes under different library entries."
                )
            hashes.add(snap["sha256"])
            snapshots.append({"video_id": video, "snapshot": snap})
        return {
            "plan_id": plan_id,
            "replace": bool(old and replace),
            "expected_revision": old["revision"] if old else 0,
            "name": old["name"] if old and not replace else name,
            "channel_id": channel,
            "channel_title": snapshots[0]["snapshot"]["channel_title"],
            "rule": normalized(rule.encode()),
            "background": bool(background),
            "approved_limit": limit,
            "items": snapshots,
            "opportunities": [stamp(t) for t in opportunities],
        }

    def approve(
        self, proposal: dict[str, Any], cancel: Event, *, consent: bool, restrictions_resolved: bool
    ) -> str:
        if not consent or not restrictions_resolved:
            raise ScheduleError(
                "Explicit batch PUBLIC approval and confirmation of no unresolved restrictions are required."
            )
        fresh = self.preview(
            [i["video_id"] for i in proposal["items"]],
            proposal["channel_id"],
            Rule.decode(proposal["rule"]),
            proposal["name"],
            cancel,
            background=proposal["background"],
            plan_id=proposal["plan_id"],
            limit=proposal["approved_limit"],
            replace=proposal.get("replace", False),
        )
        if fresh != proposal:
            raise ScheduleError(
                "The batch confirmation is stale. Review the videos and opportunities again."
            )
        now = stamp(self.clock())
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = self.plan(db, proposal["plan_id"]) if proposal["plan_id"] else None
            if old and old["revision"] != proposal["expected_revision"]:
                raise ScheduleError("Queue changed in another window. Reload and approve again.")
            if datetime.fromisoformat(proposal["opportunities"][0]) <= self.clock():
                raise ScheduleError(
                    "The previewed first opportunity passed. Review a fresh preview."
                )
            current_limit = db.execute(
                "SELECT daily_limit FROM publishing_control WHERE id=1"
            ).fetchone()[0]
            if proposal["approved_limit"] > current_limit:
                raise ScheduleError("The global daily limit changed during approval.")
            identity = old["id"] if old else uuid.uuid4().hex
            if old and proposal["replace"]:
                # Edited: its unstarted videos make way for the new list, and its times change.
                db.execute(
                    """UPDATE recurring_items SET state='Removed',removed_at=?,
                    reason='Queue edited; approved again with its new times and details'
                    WHERE plan_id=? AND state IN ('Queued','Needs Attention') AND job_id IS NULL""",
                    (now, identity),
                )
                db.execute(
                    """UPDATE recurring_plans SET name=?,rule=?,background=?,
                    approved_revision=approved_revision+1,consent_at=?,consent=?,updated_at=?,
                    next_slot=?,watermark=? WHERE id=?""",
                    (
                        proposal["name"],
                        json.dumps(proposal["rule"]),
                        int(proposal["background"]),
                        now,
                        CONSENT,
                        now,
                        proposal["opportunities"][0],
                        now,
                        identity,
                    ),
                )
                old = self.plan(db, identity)
            plan = old or dict(
                id=identity,
                name=proposal["name"],
                channel_id=proposal["channel_id"],
                channel_title=proposal["channel_title"],
                rule=proposal["rule"],
                background=int(proposal["background"]),
                approved_revision=1,
                approved_limit=proposal["approved_limit"],
            )
            if not old:
                db.execute(
                    """INSERT INTO recurring_plans VALUES(?,?,'youtube',?,?,?,0,?,1,1,?,?,?,?,?,?,?)""",
                    (
                        identity,
                        plan["name"],
                        plan["channel_id"],
                        plan["channel_title"],
                        json.dumps(plan["rule"]),
                        plan["background"],
                        now,
                        CONSENT,
                        now,
                        now,
                        proposal["opportunities"][0],
                        now,
                        plan["approved_limit"],
                    ),
                )
            position = db.execute(
                "SELECT coalesce(max(position),0) FROM recurring_items WHERE plan_id=?", (identity,)
            ).fetchone()[0]
            for entry in proposal["items"]:
                snap = entry["snapshot"]
                video = entry["video_id"]
                current = self.schedules._snapshot(db, video, proposal["channel_id"])
                current["sha256"] = snap["sha256"]
                if normalized(current) != snap:
                    raise ScheduleError(
                        "Reviewed source, declarations or metadata changed during approval."
                    )
                self.conflict(db, video, proposal["channel_id"], snap["sha256"])
                position += 1
                db.execute(
                    """INSERT INTO recurring_items(id,plan_id,video_id,channel_id,source_hash,snapshot,position,state,approved_at,binding)
                    VALUES(?,?,?,?,?,?,?,'Queued',?,?)""",
                    (
                        uuid.uuid4().hex,
                        identity,
                        video,
                        proposal["channel_id"],
                        snap["sha256"],
                        json.dumps(snap),
                        position,
                        now,
                        item_binding(plan, video, snap),
                    ),
                )
                db.execute(
                    "INSERT OR IGNORE INTO content_registry VALUES(?,?,?)",
                    (snap["sha256"], snap["size"], now),
                )
                db.execute(
                    "INSERT OR IGNORE INTO content_videos VALUES(?,?,?,?)",
                    (snap["sha256"], video, snap["path"], json.dumps(snap["stat"])),
                )
            if old:
                db.execute(
                    "UPDATE recurring_plans SET revision=revision+1,updated_at=? WHERE id=?",
                    (now, identity),
                )
            self.event(db, identity, now, "Batch approved", proposal)
            checkpoint(cancel)
        return identity

    def control(self, identity: str, revision: int, action: str, item_id: str = "") -> None:
        now = stamp(self.clock())
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            plan = self.plan(db, identity)
            if plan["revision"] != revision:
                raise ScheduleError("Queue changed elsewhere. Reload before changing it.")
            if action in ("pause", "resume"):
                self.skip_past(
                    db, plan, "Plan paused/resumed; prior opportunities are not replayed."
                )
                db.execute(
                    "UPDATE recurring_plans SET enabled=? WHERE id=?",
                    (int(action == "resume"), identity),
                )
            elif action == "clear":
                # The queue no longer uploads to YouTube, or was deleted: nothing waits here.
                db.execute(
                    """UPDATE recurring_items SET state='Removed',removed_at=?,
                    reason='Removed from the queue before it uploaded'
                    WHERE plan_id=? AND state IN ('Queued','Needs Attention') AND job_id IS NULL""",
                    (now, identity),
                )
                db.execute("UPDATE recurring_plans SET enabled=0 WHERE id=?", (identity,))
            elif action in ("remove", "up", "down"):
                row = db.execute(
                    "SELECT * FROM recurring_items WHERE id=? AND plan_id=?", (item_id, identity)
                ).fetchone()
                if not row or row["job_id"] or row["state"] not in ("Queued", "Needs Attention"):
                    raise ScheduleError("Only unstarted queue items may be reordered or removed.")
                if action == "remove":
                    db.execute(
                        "UPDATE recurring_items SET state='Removed',removed_at=?,reason='Removed explicitly before execution' WHERE id=?",
                        (now, item_id),
                    )
                else:
                    rows = db.execute(
                        "SELECT id,position FROM recurring_items WHERE plan_id=? AND state IN ('Queued','Needs Attention') AND job_id IS NULL ORDER BY position,id",
                        (identity,),
                    ).fetchall()
                    index = next(i for i, r in enumerate(rows) if r["id"] == item_id)
                    other = index + (-1 if action == "up" else 1)
                    if 0 <= other < len(rows):
                        db.execute(
                            "UPDATE recurring_items SET position=? WHERE id=?",
                            (rows[other]["position"], item_id),
                        )
                        db.execute(
                            "UPDATE recurring_items SET position=? WHERE id=?",
                            (row["position"], rows[other]["id"]),
                        )
            else:
                raise ScheduleError("Unknown queue action.")
            db.execute(
                "UPDATE recurring_plans SET revision=revision+1,updated_at=? WHERE id=?",
                (now, identity),
            )
            self.event(db, identity, now, action, {"item_id": item_id})

    def record_slot(
        self,
        db: sqlite3.Connection,
        plan: dict[str, Any],
        state: str,
        reason: str,
        item: str | None = None,
        job: str | None = None,
    ) -> None:
        now = stamp(self.clock())
        db.execute(
            "INSERT OR IGNORE INTO recurring_slots VALUES(?,?,?,?,?,?,?)",
            (plan["id"], plan["next_slot"], state, reason, now, item, job),
        )
        following = Rule.decode(plan["rule"]).next(
            max(self.clock(), datetime.fromisoformat(plan["next_slot"])), 1
        )[0]
        db.execute(
            "UPDATE recurring_plans SET next_slot=?,watermark=? WHERE id=?",
            (stamp(following), max(now, plan["watermark"]), plan["id"]),
        )

    def skip_past(self, db: sqlite3.Connection, plan: dict[str, Any], reason: str) -> None:
        if datetime.fromisoformat(plan["next_slot"]) <= self.clock():
            self.record_slot(db, plan, "Skipped", reason)

    def restart(self, db: sqlite3.Connection, reason: str) -> None:
        for row in db.execute("SELECT id FROM recurring_plans").fetchall():
            self.skip_past(db, self.plan(db, row[0]), reason)

    def reserve(self, db: sqlite3.Connection, kind: str) -> None:
        """Called only under publisher ownership + transfer/account locks + claim write transaction.

        One bounded missed-range record replaces unbounded historical-slot enumeration.
        At most one valid item is materialized; claim() creates its attempt in the SAME transaction.
        """
        from .background_state import BACKGROUND_CONSENT
        from .execution import binding

        now = self.clock()
        control = db.execute("SELECT * FROM publishing_control WHERE id=1").fetchone()
        for row in db.execute("SELECT id FROM recurring_plans ORDER BY next_slot,id").fetchall():
            plan = self.plan(db, row[0])
            instant = datetime.fromisoformat(plan["next_slot"])
            if now < instant or stamp(now) < plan["watermark"]:
                continue
            reason = ""
            if now >= instant + timedelta(seconds=60):
                reason = "Missed opportunities through this check; no catch-up."
            elif not plan["enabled"]:
                reason = "Plan paused."
            elif control["paused"]:
                reason = "Global automatic publishing paused."
            elif kind == "background" and not plan["background"]:
                reason = "This plan has foreground-only consent."
            day = stamp(now)[:10]
            claims = db.execute(
                "SELECT count(*) FROM scheduled_executions WHERE substr(claimed_at,1,10)=?", (day,)
            ).fetchone()[0]
            if not reason and claims >= min(control["daily_limit"], plan["approved_limit"]):
                reason = "Daily automatic attempt budget reached; queue retained."
            if (
                not reason
                and db.execute(
                    "SELECT 1 FROM upload_attempts WHERE substr(updated_at,1,10)=? AND (error LIKE '%API quota is exhausted%' OR (channel_id=? AND error LIKE '%upload limit was reached%'))",
                    (day, plan["channel_id"]),
                ).fetchone()
            ):
                reason = "Known quota/channel restriction; queue retained."
            if reason:
                self.record_slot(db, plan, "Skipped", reason)
                continue
            # An individually approved due job has priority. Never manufacture a competing job.
            if db.execute(
                "SELECT 1 FROM scheduled_jobs j WHERE execution_enabled=1 AND state IN ('Scheduled','Due') AND scheduled_utc<=? AND scheduled_utc>? AND NOT EXISTS(SELECT 1 FROM scheduled_executions e WHERE e.job_id=j.id)",
                (stamp(now), stamp(now - timedelta(seconds=60))),
            ).fetchone():
                self.record_slot(
                    db,
                    plan,
                    "Skipped",
                    "An individual or earlier recurring job owns this opportunity.",
                )
                continue
            if db.execute(
                "SELECT 1 FROM upload_attempts WHERE released=0 AND state NOT IN ('Cancelled','Uploaded Private','Public Verified','Processing','Failed')"
            ).fetchone():
                self.record_slot(
                    db,
                    plan,
                    "Skipped",
                    "Another transfer or unresolved result requires attention; queue retained.",
                )
                continue
            items = db.execute(
                "SELECT * FROM recurring_items WHERE plan_id=? AND state='Queued' ORDER BY position,id",
                (plan["id"],),
            ).fetchall()
            for row_item in items:
                item = dict(row_item)
                snap = json.loads(item["snapshot"])
                try:
                    if item_binding(plan, item["video_id"], snap) != item["binding"]:
                        raise ScheduleError("Immutable batch approval binding changed.")
                    current = self.schedules._snapshot(
                        db,
                        item["video_id"],
                        plan["channel_id"],
                        automation=snap.get("automation", ""),
                    )
                    current["sha256"] = item["source_hash"]
                    if normalized(current) != snap:
                        raise ScheduleError(
                            "Reviewed source, metadata or channel changed since approval."
                        )
                    # Ignore only this queue reservation when applying shared conflicts.
                    db.execute(
                        "UPDATE recurring_items SET state='Removed' WHERE id=?", (item["id"],)
                    )
                    self.conflict(db, item["video_id"], plan["channel_id"], item["source_hash"])
                    db.execute(
                        "UPDATE recurring_items SET state='Queued' WHERE id=?", (item["id"],)
                    )
                except (ScheduleError, LibraryError, OSError) as exc:
                    db.execute(
                        "UPDATE recurring_items SET state='Needs Attention',reason=? WHERE id=?",
                        (str(exc), item["id"]),
                    )
                    continue
                zone = Rule.decode(plan["rule"]).zone
                local = instant.astimezone(ZoneInfo(zone))
                stamp_now = stamp(now)
                job = Job(
                    uuid.uuid4().hex,
                    item["video_id"],
                    "youtube",
                    plan["channel_id"],
                    plan["channel_title"],
                    item["source_hash"],
                    snap,
                    plan["next_slot"],
                    zone,
                    local.replace(tzinfo=None).isoformat(),
                    local.fold,
                    "Scheduled",
                    "Reserved by explicitly approved recurring queue.",
                    1,
                    stamp_now,
                    stamp_now,
                    item["approved_at"],
                    "",
                    "",
                    1,
                )
                self.schedules._write(db, job, insert=True)
                db.execute(
                    "INSERT INTO execution_consent VALUES(?,?,?,?,?)",
                    (job.id, 1, binding(job), item["approved_at"], CONSENT),
                )
                if plan["background"]:
                    db.execute(
                        "INSERT INTO background_consent VALUES(?,?,?,?,?)",
                        (job.id, binding(job), 1, item["approved_at"], BACKGROUND_CONSENT),
                    )
                db.execute(
                    "UPDATE recurring_items SET state='Reserved',job_id=?,reason='Reserved once; see linked execution history' WHERE id=?",
                    (job.id, item["id"]),
                )
                self.record_slot(
                    db, plan, "Reserved", "One approved item reserved.", item["id"], job.id
                )
                self.event(
                    db,
                    plan["id"],
                    stamp_now,
                    "Slot reserved",
                    {"item_id": item["id"], "job_id": job.id, "instant": plan["next_slot"]},
                )
                return
            self.record_slot(
                db,
                plan,
                "Skipped",
                "No eligible approved item; invalid items need attention, empty slots are skipped.",
            )

    def state(self) -> dict[str, Any]:
        with closing(self.library.connect()) as db:
            db.execute("BEGIN")
            plans = [
                self.plan(db, r[0])
                for r in db.execute("SELECT id FROM recurring_plans ORDER BY created_at,id")
            ]
            items = []
            for row in db.execute("""SELECT i.*,a.state AS attempt_state,a.processing,a.remote_id,a.acknowledged,a.error AS upload_error,
                a.id AS attempt_id,j.state AS job_state,j.reason AS job_reason
                FROM recurring_items i LEFT JOIN scheduled_jobs j ON j.id=i.job_id
                LEFT JOIN scheduled_executions e ON e.job_id=j.id LEFT JOIN upload_attempts a ON a.id=e.attempt_id
                ORDER BY i.position,i.id"""):
                item = dict(row)
                item["snapshot"] = json.loads(item["snapshot"])
                items.append(item)
            slots = [
                dict(r)
                for r in db.execute("SELECT * FROM recurring_slots ORDER BY instant DESC LIMIT 200")
            ]
            control = dict(db.execute("SELECT * FROM publishing_control WHERE id=1").fetchone())
            # Plans that carry an auto upload's YouTube part, not a queue someone made.
            automatic = [r[0] for r in db.execute("SELECT plan_id FROM automation_profiles")]
        return {
            "plans": plans,
            "items": items,
            "slots": slots,
            "control": control,
            "automatic": automatic,
        }
