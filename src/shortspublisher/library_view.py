"""Read-only library browsing projection. No source scans, authorization or execution."""

import math
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from .library import Library, Video
from .media import checkpoint
from .preparation import Preparation, PreparationStore, review_checks


def search_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True)
class LibraryEntry:
    video: Video
    preparation: Preparation
    plans: int = 0
    results: int = 0
    attempts: int = 0

    @property
    def reviewed(self) -> bool:
        return review_checks(self.video, self.preparation, "").state == "Review Complete"

    @property
    def label(self) -> str:
        state = "Review complete (saved)" if self.reviewed else "Needs details or review"
        if self.video.error:
            state = "File inspection needs attention"
        parts = [state]
        if self.plans:
            parts.append(f"{self.plans} future plan(s)")
        if self.results:
            parts.append(f"{self.results} YouTube result(s)")
        elif self.attempts:
            parts.append(f"{self.attempts} upload attempt(s)")
        return " · ".join(parts)


def read_entries(library: Library, cancel: Event) -> dict[int, LibraryEntry]:
    """Read one consistent SQLite snapshot on the library worker; never probe files."""
    with closing(library.connect()) as db:
        db.execute("BEGIN")
        plans: dict[int, int] = {}
        current = datetime.now(UTC)
        for row in db.execute(
            "SELECT video_id,scheduled_utc FROM scheduled_jobs j "
            "WHERE state='Scheduled' AND NOT EXISTS "
            "(SELECT 1 FROM scheduled_executions e WHERE e.job_id=j.id)"
        ):
            if datetime.fromisoformat(row["scheduled_utc"]) > current:
                plans[row["video_id"]] = plans.get(row["video_id"], 0) + 1
        history = {
            row["video_id"]: (row["attempts"], row["results"])
            for row in db.execute(
                "SELECT video_id,COUNT(*) attempts,"
                "SUM(CASE WHEN remote_id<>'' THEN 1 ELSE 0 END) results "
                "FROM upload_attempts GROUP BY video_id"
            )
        }
        entries = {}
        for row in db.execute("SELECT * FROM videos"):
            checkpoint(cancel)
            video = library.decode(row)
            attempts, results = history.get(video.id, (0, 0))
            entries[video.id] = LibraryEntry(
                video,
                PreparationStore.read(db, video.id),
                plans.get(video.id, 0),
                results,
                attempts,
            )
        return entries


def browse(
    entries: dict[int, LibraryEntry], query: str, category: str, order: str
) -> list[LibraryEntry]:
    terms = search_key(query).split()
    result = []
    for entry in entries.values():
        haystack = search_key(
            f"{entry.video.path} {entry.preparation.metadata.title} {entry.video.id}"
        )
        if not all(term in haystack for term in terms):
            continue
        if category == "reviewed" and not entry.reviewed:
            continue
        if category == "needs_review" and entry.reviewed:
            continue
        if category == "scheduled" and not entry.plans:
            continue
        if category == "uploaded" and not entry.results:
            continue
        if category == "inspection" and not entry.video.error:
            continue
        result.append(entry)
    if order in ("newest", "oldest"):
        return sorted(
            result, key=lambda e: (e.video.imported_at, e.video.id), reverse=order == "newest"
        )
    if order in ("name_az", "name_za"):
        return sorted(
            result,
            key=lambda e: (search_key(Path(e.video.path).name), e.video.id),
            reverse=order == "name_za",
        )
    known = [
        e for e in result if math.isfinite(e.video.media.duration) and e.video.media.duration > 0
    ]
    known_ids = {e.video.id for e in known}
    unknown = [e for e in result if e.video.id not in known_ids]
    return sorted(
        known, key=lambda e: (e.video.media.duration, e.video.id), reverse=order == "longest"
    ) + sorted(unknown, key=lambda e: e.video.id)
