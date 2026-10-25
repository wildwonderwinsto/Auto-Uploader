"""How much of each platform's limit is used, and when it frees up. Local reads only: no
request to YouTube, Buffer or Cloudinary is made here.

The counts follow the rules the app itself enforces: YouTube's daily ceiling per UTC day (as
`execution` counts it), each Buffer platform's posts per rolling 24 hours, and Buffer Free's
10 scheduled posts per channel at a time."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .execution import DAILY_MAX
from .social import PLATFORMS

if TYPE_CHECKING:
    from .library import Library

BUFFER_SCHEDULED_MAX = 10  # Buffer Free: scheduled posts per channel at a time
BUFFER_WAITING = ("Scheduled in Buffer",)  # posts Buffer holds until their time


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def snapshot(library: Library, now: datetime) -> dict[str, Any]:
    day = now.astimezone(UTC).date()
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
    since = now - timedelta(hours=24)
    with closing(library.connect()) as db:
        db.execute("BEGIN")
        started = db.execute(
            "SELECT count(*) FROM upload_attempts WHERE substr(created_at,1,10)=? "
            "AND state!='Cancelled'",
            (day.isoformat(),),
        ).fetchone()[0]
        blocked = bool(
            db.execute(
                "SELECT 1 FROM upload_attempts WHERE substr(updated_at,1,10)=? AND "
                "(error LIKE '%API quota is exhausted%' OR error LIKE '%upload limit was reached%')",
                (day.isoformat(),),
            ).fetchone()
        )
        result: dict[str, Any] = {
            "youtube": {
                "used": started,
                "limit": DAILY_MAX,
                "resets": midnight,
                "blocked": blocked,
            },
            "buffer": {},
            "waits": [],
        }
        for spec in PLATFORMS:
            sent = [
                moment
                for (value,) in db.execute(
                    f"SELECT submitted_at FROM {spec.key}_posts WHERE submitted_at IS NOT NULL"
                )
                if (moment := parse(value)) and moment > since
            ]
            scheduled = db.execute(
                f"SELECT count(*) FROM {spec.key}_posts WHERE released=0 AND state IN "
                f"({','.join('?' * len(BUFFER_WAITING))})",
                BUFFER_WAITING,
            ).fetchone()[0]
            result["buffer"][spec.key] = {
                "used": len(sent),
                "limit": spec.daily_max,
                # When the oldest post of the last 24 hours stops counting.
                "frees": min(sent) + timedelta(hours=24) if sent else None,
                "scheduled": scheduled,
                "scheduled_limit": BUFFER_SCHEDULED_MAX,
            }
        # Buffer asked the app to wait (its API limit): the main account and any backups.
        waits = [
            ("Buffer", parse(row[0]))
            for row in db.execute("SELECT retry_after FROM buffer_control WHERE id=1")
        ]
        waits += [
            (f"Buffer · {row[0]}", parse(row[1]))
            for row in db.execute("SELECT label, retry_after FROM buffer_accounts")
        ]
        result["waits"] = [(name, until) for name, until in waits if until and until > now]
        db.rollback()
    return result
