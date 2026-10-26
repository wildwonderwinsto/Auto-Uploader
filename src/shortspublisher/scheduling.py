"""Local publishing plans. No credential store, network client, or upload executor.

Stage 8 plans are never execution authorization. Due lasts 60 seconds; then Missed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .library import Library, LibraryError, source_status
from .preparation import PreparationStore, media_token, review_checks
from .source import fingerprint, signature
from .upload_store import SETTLED


class ScheduleError(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ScheduleError("An aware clock is required.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def local_choices(local: str, zone: str) -> list[datetime]:
    try:
        wall = datetime.fromisoformat(local)
        if wall.tzinfo is not None:
            raise ValueError()
        tz = ZoneInfo(zone)
        choices = {
            wall.replace(tzinfo=tz, fold=f).astimezone(UTC)
            for f in (0, 1)
            if wall.replace(tzinfo=tz, fold=f).astimezone(UTC).astimezone(tz).replace(tzinfo=None)
            == wall
        }
        if not choices:
            raise ScheduleError(
                "This local time does not exist because of a clock transition. Choose another time."
            )
        return sorted(choices)
    except (ValueError, ZoneInfoNotFoundError, OverflowError) as exc:
        raise ScheduleError(
            "Enter a valid local date/time and an installed IANA time zone."
        ) from exc


def resolve_time(local: str, zone: str, occurrence: int | None, now: datetime) -> datetime:
    choices = local_choices(local, zone)
    if len(choices) == 2 and occurrence not in (0, 1):
        raise ScheduleError(
            "This time occurs twice. Choose the first or second occurrence explicitly."
        )
    chosen = choices[occurrence or 0] if len(choices) == 2 else choices[0]
    if chosen <= now:
        raise ScheduleError("Choose a scheduled instant strictly in the future.")
    return chosen


CONFLICT = (
    "Upload history contains a completed or unresolved conflict. Inspect and deliberately "
    "reconcile it in Upload History first."
)


def due_state(instant: datetime, now: datetime) -> str:
    if now < instant:
        return "Scheduled"
    return "Due" if now < instant + timedelta(seconds=60) else "Missed"


@dataclass(frozen=True)
class Job:
    id: str
    video_id: int
    platform: str
    channel_id: str
    channel_title: str
    source_hash: str
    snapshot: dict[str, Any]
    scheduled_utc: str
    timezone: str
    local_time: str
    fold: int
    state: str
    reason: str
    revision: int
    created_at: str
    updated_at: str
    approved_at: str
    cancelled_at: str
    cancellation_reason: str
    execution_enabled: int


@dataclass(frozen=True)
class Proposal:
    video_id: int
    channel_id: str
    snapshot: dict[str, Any]
    local_time: str
    timezone: str
    fold: int
    scheduled_utc: str
    job_id: str = ""
    expected_revision: int = 0


class ScheduleService:
    def __init__(self, library: Library, clock: Callable[[], datetime] = utc_now):
        self.library, self.clock = library, clock

    @staticmethod
    def decode(row: sqlite3.Row) -> Job:
        values = dict(row)
        values["snapshot"] = json.loads(values["snapshot"])
        return Job(**values)

    def all(self) -> list[Job]:
        with closing(self.library.connect()) as db:
            return [
                self.decode(row)
                for row in db.execute("SELECT * FROM scheduled_jobs ORDER BY scheduled_utc,id")
            ]

    def get(self, identity: str) -> Job:
        with closing(self.library.connect()) as db:
            return self._get(db, identity)

    @staticmethod
    def _unclaimed(db: sqlite3.Connection, identity: str) -> None:
        if db.execute("SELECT 1 FROM scheduled_executions WHERE job_id=?", (identity,)).fetchone():
            raise ScheduleError(
                "Execution already claimed this job. Use Stop, Resume, Verify or Resolve in Upload History; it cannot be rescheduled or cancelled as an unstarted plan."
            )

    def _get(self, db: sqlite3.Connection, identity: str) -> Job:
        row = db.execute("SELECT * FROM scheduled_jobs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ScheduleError("Schedule no longer exists. Reload the queue.")
        return self.decode(row)

    def _snapshot(
        self, db: sqlite3.Connection, video_id: int, channel_id: str, *, automation: str = ""
    ) -> dict[str, Any]:
        row = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if row is None:
            raise ScheduleError(
                "The library video was removed. The plan is preserved; cancel it or restore and review the source."
            )
        video = self.library.decode(row)
        prep = PreparationStore.read(db, video_id)
        availability = source_status(video)
        if availability:
            raise ScheduleError("Source requires attention: " + availability)
        automation_token = ""
        if automation:
            from .automation import approved_preparation

            video, prep, automation_token = approved_preparation(
                db, self.library, automation, video_id, channel_id
            )
        if not automation and (
            review_checks(video, prep, source_status(video)).state != "Review Complete"
            or media_token(video) != prep.reviewed_media
        ):
            raise ScheduleError(
                "Save current metadata and complete review of the unchanged source first."
            )
        if prep.metadata.privacy != "public":
            raise ScheduleError("Scheduling requires a saved and reviewed Public preference.")
        account = db.execute(
            "SELECT channel FROM account_connection WHERE platform='youtube'"
        ).fetchone()
        if account is None:
            raise ScheduleError(
                "Connect the intended channel in Accounts. The plan will not be redirected."
            )
        channel = json.loads(account[0])
        if channel["id"] != channel_id:
            raise ScheduleError(
                "Connected channel differs from the intended channel. Reconnect the original channel."
            )
        return {
            "path": video.path,
            "filename": Path(video.path).name,
            "size": video.media.size,
            "duration": video.media.duration,
            "width": video.media.width,
            "height": video.media.height,
            "stat": list(signature(Path(video.path).stat())),
            **({"automation": automation} if automation else {}),
            "reviewed_media": automation_token if automation else prep.reviewed_media,
            "revision": prep.revision,
            "metadata": asdict(prep.metadata),
            "privacy": "public",
            "channel_title": channel["title"],
            "channel_id": channel_id,
        }

    def _conflicts(
        self,
        db: sqlite3.Connection,
        video_id: int,
        channel: str,
        digest: str,
        excluding: str = "",
        queue: str = "",
        *,
        starting: bool = False,
    ) -> None:
        """The same video or identical content already uploaded or planned on this channel is
        always a conflict. Another video's unfinished upload only matters when this one is
        about to start (`starting`): a plan for later must not be blocked by it."""
        from .recurring import reservation_conflict

        reservation_conflict(db, channel, video_id, digest, excluding, queue)
        if db.execute(
            """SELECT 1 FROM scheduled_jobs WHERE id!=? AND platform='youtube'
                AND channel_id=? AND state NOT IN ('Draft','Cancelled') AND (video_id=? OR source_hash=?)""",
            (excluding, channel, video_id, digest),
        ).fetchone():
            raise ScheduleError(
                "An active plan already covers this video or identical content on this channel. Reschedule or cancel that plan."
            )
        for row in db.execute(
            "SELECT * FROM upload_attempts WHERE released=0 AND state!='Cancelled'"
        ):
            same = row["channel_id"] == channel and (
                row["video_id"] == video_id or json.loads(row["snapshot"])["sha256"] == digest
            )
            if same or (starting and row["state"] not in SETTLED):
                raise ScheduleError(CONFLICT)

    def preview(
        self,
        video_id: int,
        channel_id: str,
        local: str,
        zone: str,
        occurrence: int | None = None,
        job_id: str = "",
        expected_revision: int = 0,
        cancel: Event | None = None,
        queue: str = "",
    ) -> Proposal:
        """`queue`: a recurring plan being edited; its own unstarted entries don't conflict."""
        cancel = cancel or Event()
        instant = resolve_time(local, zone, occurrence, self.clock())
        with closing(self.library.connect()) as db:
            snap = self._snapshot(db, video_id, channel_id)
        with Path(snap["path"]).open("rb") as file:
            digest = fingerprint(file, cancel)
            if list(signature(os.fstat(file.fileno()))) != snap["stat"]:
                raise ScheduleError(
                    "Source changed during preparation. Refresh inspection and review again."
                )
        with closing(self.library.connect()) as db:
            if self._snapshot(db, video_id, channel_id) != snap:
                raise ScheduleError(
                    "Video, metadata or channel changed during preparation. Review again."
                )
            self._conflicts(db, video_id, channel_id, digest, job_id, queue)
            if job_id:
                self._unclaimed(db, job_id)
                old = self._get(db, job_id)
                if (
                    old.revision != expected_revision
                    or old.state == "Cancelled"
                    or old.video_id != video_id
                    or old.channel_id != channel_id
                ):
                    raise ScheduleError("Schedule changed or was cancelled. Reload before editing.")
        snap["sha256"] = digest
        # JSON normalizes immutable payload representations before later equality checks.
        snap = json.loads(json.dumps(snap))
        fold = instant.astimezone(ZoneInfo(zone)).fold
        return Proposal(
            video_id, channel_id, snap, local, zone, fold, stamp(instant), job_id, expected_revision
        )

    def commit(self, proposal: Proposal, *, approve: bool, cancel: Event | None = None) -> Job:
        # Rehash outside SQLite's write transaction; recheck metadata/stat under the write lock.
        current = self.preview(
            proposal.video_id,
            proposal.channel_id,
            proposal.local_time,
            proposal.timezone,
            proposal.fold,
            proposal.job_id,
            proposal.expected_revision,
            cancel,
        )
        if current != proposal:
            raise ScheduleError(
                "The confirmation is stale. Review the current content and schedule again."
            )
        instant = datetime.fromisoformat(proposal.scheduled_utc)
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if instant <= self.clock():
                raise ScheduleError(
                    "The scheduled time passed while confirming. Choose a future time."
                )
            snap = self._snapshot(db, proposal.video_id, proposal.channel_id)
            snap["sha256"] = proposal.snapshot["sha256"]
            if json.loads(json.dumps(snap)) != proposal.snapshot:
                raise ScheduleError("The confirmation is stale. Reopen the editor.")
            self._conflicts(
                db, proposal.video_id, proposal.channel_id, snap["sha256"], proposal.job_id
            )
            if proposal.job_id:
                self._unclaimed(db, proposal.job_id)
                db.execute("DELETE FROM execution_consent WHERE job_id=?", (proposal.job_id,))
            old = self._get(db, proposal.job_id) if proposal.job_id else None
            if old and (old.revision != proposal.expected_revision or old.state == "Cancelled"):
                raise ScheduleError("Schedule changed in another window. Reload before editing.")
            if old and not approve and old.state != "Draft":
                raise ScheduleError("An approved plan can only be changed with renewed approval.")
            now = stamp(self.clock())
            job = Job(
                proposal.job_id or uuid.uuid4().hex,
                proposal.video_id,
                "youtube",
                proposal.channel_id,
                snap["channel_title"],
                snap["sha256"],
                proposal.snapshot,
                proposal.scheduled_utc,
                proposal.timezone,
                proposal.local_time,
                proposal.fold,
                "Scheduled" if approve else "Draft",
                "Saved plan only. Explicit automatic activation is required.",
                old.revision + 1 if old else 1,
                old.created_at if old else now,
                now,
                now if approve else "",
                "",
                "",
                0,
            )
            self._write(db, job, insert=old is None)
            return job

    @staticmethod
    def _write(db: sqlite3.Connection, job: Job, *, insert: bool = False) -> None:
        values = asdict(job)
        values["snapshot"] = json.dumps(job.snapshot)
        if insert:
            db.execute(
                f"INSERT INTO scheduled_jobs ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
        else:
            fields = [key for key in values if key != "id"]
            db.execute(
                f"UPDATE scheduled_jobs SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                tuple(values[k] for k in fields) + (job.id,),
            )
        db.execute(
            "INSERT INTO schedule_events(job_id,revision,record) VALUES (?,?,?)",
            (job.id, job.revision, json.dumps(asdict(job))),
        )

    def cancel(self, identity: str, revision: int, reason: str = "Cancelled by user") -> Job:
        from dataclasses import replace

        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            execution = db.execute(
                "SELECT a.released FROM scheduled_executions e JOIN upload_attempts a ON a.id=e.attempt_id WHERE e.job_id=?",
                (identity,),
            ).fetchone()
            if execution is not None and not execution["released"]:
                self._unclaimed(db, identity)
            old = self._get(db, identity)
            # A released attempt was explicitly reconciled in Upload History. Closing its local
            # schedule retains the immutable execution link and never permits reuse of this job.
            if execution is not None:
                reason = "Closed after explicit upload-history reconciliation. Original execution and outcome retained."
            else:
                db.execute("DELETE FROM execution_consent WHERE job_id=?", (identity,))
            if old.revision != revision or old.state == "Cancelled":
                raise ScheduleError("Schedule changed or was already cancelled. Reload the queue.")
            now = stamp(self.clock())
            job = replace(
                old,
                state="Cancelled",
                execution_enabled=0,
                reason=reason,
                cancellation_reason=reason,
                cancelled_at=now,
                updated_at=now,
                revision=old.revision + 1,
            )
            self._write(db, job)
            return job

    def evaluate(self, cancel: Event | None = None) -> list[Job]:
        from dataclasses import replace

        cancel = cancel or Event()
        # Stat/review checks are local and run only in a worker. No HTTP or Keychain access.
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            for row in db.execute("SELECT * FROM scheduled_jobs").fetchall():
                if cancel.is_set():
                    raise ScheduleError("Evaluation cancelled; no partial changes saved.")
                old = self.decode(row)
                if db.execute(
                    "SELECT 1 FROM scheduled_executions WHERE job_id=?", (old.id,)
                ).fetchone():
                    continue
                if old.state in ("Draft", "Cancelled") or (
                    # Checked again only when it was blocked by an upload conflict, which
                    # can clear (another video's upload finishing); a real duplicate stays.
                    old.state == "Needs Attention" and old.reason != CONFLICT
                ):
                    continue
                state, reason = old.state, old.reason
                try:
                    snap = self._snapshot(
                        db,
                        old.video_id,
                        old.channel_id,
                        automation=old.snapshot.get("automation", ""),
                    )
                    snap["sha256"] = old.source_hash
                    if json.loads(json.dumps(snap)) != old.snapshot:
                        raise ScheduleError(
                            "Approved content or review changed. Reopen and approve the current content; the original snapshot is preserved in history."
                        )
                    self._conflicts(db, old.video_id, old.channel_id, old.source_hash, old.id)
                    computed = due_state(datetime.fromisoformat(old.scheduled_utc), now)
                    # Clock rollback never makes an observed Due/Missed job Scheduled again.
                    state = (
                        "Missed"
                        if old.state == "Missed"
                        else "Due"
                        if old.state == "Due" and computed == "Scheduled"
                        else computed
                    )
                    reason = {
                        "Scheduled": "Saved plan only. Explicit automatic activation is required.",
                        "Due": "Due / Awaiting Execution. Automatic publishing requires activation and the global switch enabled.",
                        "Missed": "Missed / unexecuted. The 60-second start window expired. Reschedule and reapprove deliberately.",
                    }[state]
                    if state == "Scheduled" and old.execution_enabled:
                        reason = "Automatic Public execution authorized; subject to global pause and fresh preflight."
                except (ScheduleError, LibraryError, OSError) as exc:
                    state, reason = "Needs Attention", str(exc)
                if (state, reason) != (old.state, old.reason):
                    self._write(
                        db,
                        replace(
                            old,
                            state=state,
                            reason=reason,
                            revision=old.revision + 1,
                            updated_at=stamp(now),
                        ),
                    )
        return self.all()

    def execute(
        self, action: str, argument: Any = None, cancel: Event | None = None
    ) -> dict[str, Any]:
        try:
            self.library.initialize()
            if action == "load":
                return {"jobs": self.evaluate(cancel)}
            if action == "preview":
                return {"proposal": self.preview(**argument, cancel=cancel)}
            if action in ("approve", "draft"):
                self.commit(argument, approve=action == "approve", cancel=cancel)
            elif action == "cancel":
                self.cancel(*argument)
            else:
                raise ScheduleError("Unknown local schedule operation.")
            return {"jobs": self.all()}
        except (ScheduleError, LibraryError, OSError, ValueError, sqlite3.Error) as exc:
            return {"error": str(exc)}
        except Exception:
            return {
                "error": "Schedule operation failed safely. Reload; existing plans were preserved."
            }
