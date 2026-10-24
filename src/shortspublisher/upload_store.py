"""Durable nonsecret attempt snapshots. History deliberately survives library removal."""

import json
import uuid
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from .auth import AccountError
from .library import Library, now

STATES = frozenset(
    (
        "Awaiting Confirmation",
        "Preparing",
        "Uploading",
        "Paused",
        "Interrupted",
        "Needs Reconciliation",
        "Transfer Complete",
        "Processing",
        "Uploaded Private",
        "Public Verified",
        "Failed",
        "Cancelled",
    )
)
# Finished attempts that hold nothing up: on YouTube with verified privacy and channel (still
# processing, or done), or failed. The same video or bytes is still checked separately.
SETTLED = ("Uploaded Private", "Public Verified", "Processing", "Failed")
# YouTube's own processing results; anything else is checked again later.
PROCESSED = ("processed", "succeeded", "failed", "rejected", "terminated")


@dataclass(frozen=True)
class Attempt:
    id: str
    video_id: int
    channel_id: str
    snapshot: dict[str, Any]
    state: str
    acknowledged: int
    remote_id: str
    privacy: str
    processing: str
    error: str
    created_at: str
    updated_at: str
    completed_at: str
    released: bool
    verification_status: str = "historical"
    verified_at: str = ""

    @property
    def requested_privacy(self) -> str:
        payload_privacy = self.snapshot.get("payload", {}).get("status", {}).get("privacyStatus")
        intent = self.snapshot.get("requested_privacy")
        # Legacy Stage 5/6 snapshots explicitly stored Private in their payload.
        if (
            intent is None
            and "requested_privacy" not in self.snapshot
            and payload_privacy == "private"
        ):
            return "private"
        if intent not in ("private", "public") or intent != payload_privacy:
            raise AccountError("Missing or inconsistent approved upload privacy.")
        return str(intent)


class UploadStore:
    def __init__(self, library: Library) -> None:
        self.library = library

    @staticmethod
    def decode(row: Any) -> Attempt:
        data = dict(row)
        data["snapshot"] = json.loads(data["snapshot"])
        return Attempt(**data)

    def all(self) -> list[Attempt]:
        with closing(self.library.connect()) as db:
            return [
                self.decode(row)
                for row in db.execute(
                    "SELECT * FROM upload_attempts ORDER BY created_at DESC, rowid DESC"
                )
            ]

    def get(self, identity: str) -> Attempt:
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM upload_attempts WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise AccountError("Upload attempt no longer exists.")
            return self.decode(row)

    def create(self, video_id: int, channel_id: str, snapshot: dict[str, Any]) -> Attempt:
        identity, stamp = uuid.uuid4().hex, now()
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            from .recurring import reservation_conflict
            from .scheduling import ScheduleError

            try:
                reservation_conflict(db, channel_id, video_id, snapshot["sha256"])
            except ScheduleError as exc:
                raise AccountError(str(exc)) from exc
            if db.execute(
                "SELECT 1 FROM scheduled_jobs WHERE channel_id=? AND state NOT IN ('Draft','Cancelled') AND (video_id=? OR source_hash=?)",
                (channel_id, video_id, snapshot["sha256"]),
            ).fetchone():
                raise AccountError(
                    "An active schedule covers this video. Cancel that local plan before starting a manual upload."
                )
            for row in db.execute(
                "SELECT * FROM upload_attempts WHERE released=0 AND state!='Cancelled'"
            ):
                previous = self.decode(row)
                same_source = (
                    previous.video_id == video_id
                    or previous.snapshot["sha256"] == snapshot["sha256"]
                )
                if (previous.channel_id == channel_id and same_source) or previous.state not in (
                    "Uploaded Private",
                    "Public Verified",
                    "Failed",
                ):
                    raise AccountError(
                        "An existing attempt blocks a new upload. Resume/reconcile it or explicitly acknowledge the duplicate risk in Upload History."
                    )
            db.execute(
                "INSERT INTO upload_attempts(id,video_id,channel_id,snapshot,state,created_at,updated_at,verification_status) VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    video_id,
                    channel_id,
                    json.dumps(snapshot),
                    "Awaiting Confirmation",
                    stamp,
                    stamp,
                    "unverified",
                ),
            )
        return self.get(identity)

    def update(self, identity: str, state: str, **values: Any) -> Attempt:
        allowed = {
            "acknowledged",
            "remote_id",
            "privacy",
            "processing",
            "error",
            "completed_at",
            "released",
            "verification_status",
            "verified_at",
        }
        if state not in STATES or set(values) - allowed:
            raise ValueError("Invalid upload update")
        with closing(self.library.connect()) as db, db:
            old = self.decode(
                db.execute("SELECT * FROM upload_attempts WHERE id=?", (identity,)).fetchone()
            )
            if old.state == "Cancelled" and state != "Cancelled":
                raise AccountError("A cancelled confirmation cannot become active.")
            if old.state in ("Uploaded Private", "Public Verified") and state not in (
                "Uploaded Private",
                "Public Verified",
                "Processing",
                "Needs Reconciliation",
                "Failed",
            ):
                raise AccountError("Completed uploads cannot restart transfer.")
            acknowledged = values.get("acknowledged", old.acknowledged)
            if not isinstance(acknowledged, int) or not 0 <= acknowledged <= old.snapshot["size"]:
                raise AccountError("Invalid acknowledged upload progress.")
            values.update(state=state, updated_at=now())
            columns = ",".join(f"{name}=?" for name in values)
            db.execute(
                f"UPDATE upload_attempts SET {columns} WHERE id=?", (*values.values(), identity)
            )
        return self.get(identity)

    def recover(self) -> None:
        """Called only under the upload process lock; never starts network activity."""
        with closing(self.library.connect()) as db, db:
            db.execute(
                """UPDATE upload_attempts SET state='Interrupted', updated_at=?,
                error='Application stopped during an operation. Check the existing session before resuming; no upload restarted.'
                WHERE state IN ('Preparing','Uploading')""",
                (now(),),
            )
