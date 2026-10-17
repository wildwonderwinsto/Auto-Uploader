"""Explicit per-job consent, durable ownership and desktop-only automatic dispatch.

No network protocol is implemented here. All transfers use UploadService.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from threading import Event
from typing import TYPE_CHECKING, Any

from filelock import Timeout

from .auth import AccountError
from .background_state import BACKGROUND_CONSENT, Ownership, read, validate_owner
from .recurring import RecurringService, validate_job
from .scheduling import Job, ScheduleError, ScheduleService, due_state, stamp

if TYPE_CHECKING:
    from collections.abc import Callable

    from .upload_store import Attempt
    from .uploads import UploadService

CONSENT = (
    "This video will be uploaded publicly to the selected YouTube channel automatically at the "
    "scheduled time while ShortsPublisher is running. No second confirmation will appear. "
    "The scheduled time is the upload START time; public playback may become available later. "
    "Closed or sleeping applications cannot guarantee execution. Missed jobs require reapproval."
)


def binding(job: Job) -> str:
    value = [
        job.id,
        job.video_id,
        job.channel_id,
        job.snapshot,
        job.scheduled_utc,
        job.local_time,
        job.timezone,
        job.fold,
        job.approved_at,
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def upload_snapshot(job: Job, chunk_size: int) -> dict[str, Any]:
    source, metadata = job.snapshot, job.snapshot["metadata"]
    result = {
        key: source[key]
        for key in (
            "path",
            "filename",
            "duration",
            "width",
            "height",
            "revision",
            "sha256",
            "stat",
            "size",
            "channel_title",
        )
    }
    payload: dict[str, Any] = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": metadata["made_for_kids"],
            "containsSyntheticMedia": metadata["synthetic"],
        },
    }
    if metadata["tags"]:
        payload["snippet"]["tags"] = metadata["tags"]
    if source.get("automation"):
        result["automation"] = source["automation"]
    result.update(chunk_size=chunk_size, requested_privacy="public", payload=payload)
    return result


def validate_execution(
    db: sqlite3.Connection,
    service: ScheduleService,
    attempt: Attempt,
    *,
    start: bool = False,
    owner_token: str = "",
) -> None:
    execution = db.execute(
        "SELECT * FROM scheduled_executions WHERE attempt_id=?", (attempt.id,)
    ).fetchone()
    if execution is None:
        raise AccountError("No durable scheduled execution authorizes this upload.")
    validate_owner(db, execution["id"], owner_token)
    job = service._get(db, execution["job_id"])
    try:
        validate_job(db, job, start=start)
    except ScheduleError as exc:
        raise AccountError(str(exc), "source") from exc
    consent = db.execute("SELECT * FROM execution_consent WHERE job_id=?", (job.id,)).fetchone()
    if (
        not job.execution_enabled
        or not consent
        or binding(job) != consent["binding"]
        or consent["binding"] != execution["binding"]
        or consent["revision"] != execution["consent_revision"]
        or job.channel_id != attempt.channel_id
        or job.video_id != attempt.video_id
        or upload_snapshot(job, attempt.snapshot["chunk_size"]) != attempt.snapshot
    ):
        raise AccountError(
            "Scheduled execution approval no longer matches its exact upload.", "source"
        )
    snap = service._snapshot(
        db, job.video_id, job.channel_id, automation=job.snapshot.get("automation", "")
    )
    snap["sha256"] = job.source_hash
    if json.loads(json.dumps(snap)) != job.snapshot:
        raise AccountError("Scheduled content, channel or reviewed metadata changed.", "source")
    if start:
        control = db.execute("SELECT * FROM publishing_control WHERE id=1").fetchone()
        if control["paused"]:
            raise AccountError("Automatic publishing was paused before initiation.")
        if due_state(datetime.fromisoformat(job.scheduled_utc), service.clock()) != "Due":
            raise AccountError("The 60-second start window expired or the clock moved backwards.")
        if execution["phase"] != "Claimed" or execution["started_at"]:
            raise AccountError(
                "This execution has already attempted initiation. Reconcile its existing session."
            )
        db.execute(
            "UPDATE scheduled_executions SET phase='Started',started_at=? WHERE id=?",
            (stamp(service.clock()), execution["id"]),
        )


class AutomaticPublisher:
    def __init__(self, schedules: ScheduleService, uploads: UploadService):
        self.schedules, self.uploads = schedules, uploads
        self.library = schedules.library
        self.ownership = Ownership(self.library)
        # The upload boundary independently checks the persisted consent using the same clock.
        self.uploads.schedule_clock = lambda: schedules.clock()

    def status(self) -> dict[str, Any]:
        with closing(self.library.connect()) as db:
            control = dict(db.execute("SELECT * FROM publishing_control WHERE id=1").fetchone())
            control["executions"] = [
                dict(r) for r in db.execute("SELECT * FROM scheduled_executions")
            ]
        control["background"] = read(self.library)
        with closing(self.library.connect()) as db:
            control["background_jobs"] = [
                r[0] for r in db.execute("SELECT job_id FROM background_consent")
            ]
        control["recurring"] = RecurringService(self.schedules).state()
        control["jobs"] = self.schedules.all()
        control["attempts"] = self.uploads.store.all()
        return control

    def authorize(
        self, identity: str, revision: int, cancel: Event, *, background: bool = False
    ) -> None:
        job = self.schedules.get(identity)
        if job.revision != revision or job.state not in ("Scheduled",):
            raise ScheduleError("Only an unchanged future approved plan can be activated.")
        proposed = self.schedules.preview(
            job.video_id,
            job.channel_id,
            job.local_time,
            job.timezone,
            job.fold,
            job.id,
            revision,
            cancel,
        )
        if proposed.snapshot != job.snapshot or proposed.scheduled_utc != job.scheduled_utc:
            raise ScheduleError("The saved plan changed. Reapprove it first.")
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = self.schedules._get(db, identity)
            if (
                current != job
                or datetime.fromisoformat(job.scheduled_utc) <= self.schedules.clock()
            ):
                raise ScheduleError("Activation is stale. Reload and review the schedule.")
            if db.execute(
                "SELECT 1 FROM scheduled_executions WHERE job_id=?", (identity,)
            ).fetchone():
                raise ScheduleError(
                    "An execution already owns this job. Inspect its upload history."
                )
            snap = self.schedules._snapshot(
                db, job.video_id, job.channel_id, automation=job.snapshot.get("automation", "")
            )
            snap["sha256"] = job.source_hash
            if json.loads(json.dumps(snap)) != job.snapshot:
                raise ScheduleError("Approved content changed during activation.")
            self.schedules._conflicts(db, job.video_id, job.channel_id, job.source_hash, job.id)
            now = stamp(self.schedules.clock())
            db.execute(
                "INSERT OR REPLACE INTO execution_consent VALUES (?,?,?,?,?)",
                (identity, revision, binding(job), now, CONSENT),
            )
            db.execute("DELETE FROM background_consent WHERE job_id=?", (identity,))
            if background:
                db.execute(
                    "INSERT INTO background_consent VALUES(?,?,?,?,?)",
                    (identity, binding(job), revision, now, BACKGROUND_CONSENT),
                )
            self.schedules._write(
                db,
                replace(
                    job,
                    execution_enabled=1,
                    revision=revision + 1,
                    updated_at=now,
                    reason="Explicit automatic Public execution authorized.",
                ),
            )

    def control(self, paused: bool, daily_limit: int | None = None) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            limit = (
                daily_limit
                if daily_limit is not None
                else db.execute("SELECT daily_limit FROM publishing_control WHERE id=1").fetchone()[
                    0
                ]
            )
            if not 1 <= limit <= 10:
                raise ScheduleError("Choose a daily automatic publishing limit from 1 to 10.")
            if not paused:
                RecurringService(self.schedules).restart(
                    db, "Global publishing resumed; no catch-up."
                )
                self._miss_past(
                    db,
                    "The scheduled instant passed while publishing was paused. Reapprove a future time.",
                )
            db.execute(
                "UPDATE publishing_control SET paused=?,daily_limit=?,updated_at=? WHERE id=1",
                (int(paused), limit, stamp(self.schedules.clock())),
            )

    def _miss_past(self, db: sqlite3.Connection, reason: str) -> None:
        for row in db.execute(
            "SELECT * FROM scheduled_jobs WHERE execution_enabled=1 AND state IN ('Scheduled','Due')"
        ).fetchall():
            job = self.schedules.decode(row)
            if (
                datetime.fromisoformat(job.scheduled_utc) <= self.schedules.clock()
                and not db.execute(
                    "SELECT 1 FROM scheduled_executions WHERE job_id=?", (job.id,)
                ).fetchone()
            ):
                self.schedules._write(
                    db,
                    replace(
                        job,
                        state="Missed",
                        reason=reason,
                        revision=job.revision + 1,
                        updated_at=stamp(self.schedules.clock()),
                    ),
                )

    def startup(self) -> None:
        self.library.initialize()
        with self.ownership.acquire() as owned:
            if owned:
                self._startup()

    def _startup(self) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            RecurringService(self.schedules).restart(
                db, "Dispatcher restarted after opportunity; no catch-up."
            )
            self._miss_past(
                db, "Application opened after the scheduled instant. No late upload started."
            )
        try:
            with self.uploads.lock.acquire(timeout=0):
                self.uploads.store.recover()
                with closing(self.library.connect()) as db, db:
                    # Claims left before POST are not automatically reused, even within the window.
                    db.execute(
                        "UPDATE scheduled_executions SET phase='Blocked',error='Application restarted before initiation. Inspect the linked attempt.' WHERE phase='Claimed'"
                    )
                    db.execute(
                        "UPDATE upload_attempts SET state='Interrupted',error='Claim interrupted before initiation. Inspect and resolve; no automatic retry.' WHERE id IN (SELECT attempt_id FROM scheduled_executions) AND state='Awaiting Confirmation'"
                    )
        except Timeout:
            pass  # Another process owns the live transfer; never interrupt it.

    def claim(self) -> str | None:
        """One transaction binds an eligible job to exactly one normal upload attempt."""
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            RecurringService(self.schedules).reserve(db, self.ownership.kind)
            control = db.execute("SELECT * FROM publishing_control WHERE id=1").fetchone()
            if control["paused"]:
                return None
            for row in db.execute(
                "SELECT * FROM scheduled_jobs WHERE execution_enabled=1 AND state IN ('Scheduled','Due') ORDER BY scheduled_utc,id"
            ).fetchall():
                job = self.schedules.decode(row)
                if db.execute(
                    "SELECT 1 FROM scheduled_executions WHERE job_id=?", (job.id,)
                ).fetchone():
                    continue
                timing = due_state(
                    datetime.fromisoformat(job.scheduled_utc), self.schedules.clock()
                )
                if timing != "Due":
                    continue
                try:
                    consent = db.execute(
                        "SELECT * FROM execution_consent WHERE job_id=?", (job.id,)
                    ).fetchone()
                    if not consent or consent["binding"] != binding(job):
                        raise ScheduleError("Explicit automatic approval is missing or stale.")
                    if (
                        self.ownership.kind == "background"
                        and not db.execute(
                            "SELECT 1 FROM background_consent WHERE job_id=? AND binding=? AND revision=?",
                            (job.id, consent["binding"], consent["revision"]),
                        ).fetchone()
                    ):
                        raise ScheduleError(
                            "This job has desktop-only approval. Reschedule and explicitly approve background execution."
                        )
                    day = stamp(self.schedules.clock())[:10]
                    count = db.execute(
                        "SELECT count(*) FROM scheduled_executions WHERE substr(claimed_at,1,10)=?",
                        (day,),
                    ).fetchone()[0]
                    if count >= control["daily_limit"]:
                        raise ScheduleError(
                            "The configured daily automatic attempt limit was reached. Reapprove another day."
                        )
                    known_limit = db.execute(
                        "SELECT 1 FROM upload_attempts WHERE substr(updated_at,1,10)=? AND (error LIKE '%API quota is exhausted%' OR (channel_id=? AND error LIKE '%upload limit was reached%'))",
                        (day, job.channel_id),
                    ).fetchone()
                    if known_limit:
                        raise ScheduleError(
                            "A quota or channel limit was reported today. Automatic initiation is blocked; investigate and deliberately reschedule after the restriction clears."
                        )
                    self.schedules._conflicts(
                        db, job.video_id, job.channel_id, job.source_hash, job.id
                    )
                    snap = self.schedules._snapshot(
                        db,
                        job.video_id,
                        job.channel_id,
                        automation=job.snapshot.get("automation", ""),
                    )
                    snap["sha256"] = job.source_hash
                    if json.loads(json.dumps(snap)) != job.snapshot:
                        raise ScheduleError("Approved source, metadata or channel changed.")
                    execution_id, attempt_id = uuid.uuid4().hex, uuid.uuid4().hex
                    now = stamp(self.schedules.clock())
                    snapshot = upload_snapshot(job, self.uploads.chunk_size)
                    db.execute(
                        """INSERT INTO upload_attempts(id,video_id,channel_id,snapshot,state,created_at,updated_at,verification_status)
                        VALUES (?,?,?,?,?,?,?,'unverified')""",
                        (
                            attempt_id,
                            job.video_id,
                            job.channel_id,
                            json.dumps(snapshot),
                            "Awaiting Confirmation",
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        """INSERT INTO scheduled_executions(id,job_id,attempt_id,consent_revision,binding,claimed_at,phase)
                        VALUES (?,?,?,?,?,?,'Claimed')""",
                        (
                            execution_id,
                            job.id,
                            attempt_id,
                            consent["revision"],
                            consent["binding"],
                            now,
                        ),
                    )
                    if self.ownership.depth:
                        import os

                        db.execute(
                            "INSERT INTO execution_owner VALUES(?,?,?,?)",
                            (execution_id, self.ownership.token, self.ownership.kind, os.getpid()),
                        )
                    self.schedules._write(
                        db,
                        replace(
                            job,
                            reason="Execution claimed; see linked upload state.",
                            revision=job.revision + 1,
                            updated_at=now,
                        ),
                    )
                    return execution_id
                except (ScheduleError, AccountError) as exc:
                    self.schedules._write(
                        db,
                        replace(
                            job,
                            state="Needs Attention",
                            reason=str(exc),
                            revision=job.revision + 1,
                            updated_at=stamp(self.schedules.clock()),
                        ),
                    )
        return None

    def dispatch(
        self,
        cancel: Event,
        progress: Callable[[Attempt], None],
        changed: Callable[[dict[str, Any]], None] = lambda _: None,
    ) -> dict[str, Any]:
        self.library.initialize()
        with self.ownership.acquire() as owned:
            if not owned:
                return self.status()
            self.uploads.owner_token = self.ownership.token
            self.ownership.heartbeat()
            try:
                return self._dispatch(cancel, progress, changed)
            finally:
                self.uploads.owner_token = ""

    def _dispatch(
        self,
        cancel: Event,
        progress: Callable[[Attempt], None],
        changed: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        self.schedules.evaluate(cancel)
        try:
            # Same lock order as manual transfers. No claim while another upload/account operation runs.
            with (
                self.uploads.lock.acquire(timeout=0),
                self.uploads.accounts.lock.acquire(timeout=0),
            ):
                if cancel.is_set():
                    return self.status()
                identity = self.claim()
                if identity:
                    changed(self.status())

                    def report(attempt: Attempt) -> None:
                        changed(self.status())
                        progress(attempt)

                    result = self.uploads.execute("scheduled", identity, cancel, report)
                    if result.get("error"):
                        with closing(self.library.connect()) as db, db:
                            db.execute(
                                "UPDATE scheduled_executions SET phase=CASE WHEN started_at='' THEN 'Blocked' ELSE phase END,error=? WHERE id=?",
                                (result["error"], identity),
                            )
                            row = db.execute(
                                "SELECT job_id,attempt_id,phase FROM scheduled_executions WHERE id=?",
                                (identity,),
                            ).fetchone()
                            db.execute(
                                "UPDATE upload_attempts SET state='Interrupted',error=? WHERE id=? AND state='Awaiting Confirmation'",
                                (result["error"], row["attempt_id"]),
                            )
                            if row["phase"] == "Blocked":
                                job = self.schedules._get(db, row["job_id"])
                                self.schedules._write(
                                    db,
                                    replace(
                                        job,
                                        state="Needs Attention",
                                        reason=result["error"],
                                        revision=job.revision + 1,
                                        updated_at=stamp(self.schedules.clock()),
                                    ),
                                )
        except Timeout:
            result = {
                "error": "Another upload/account operation is active. Due jobs wait only within their 60-second window."
            }
            return {**self.status(), **result}
        return self.status()
