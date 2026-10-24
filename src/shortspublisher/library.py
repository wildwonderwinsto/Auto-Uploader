"""Transactional SQLite library and a small import service, independent of Qt."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from .media import InspectionError, Inspector, Media, checkpoint
from .preparation import PreparationStore

SCHEMA_VERSION = 17


class LibraryError(Exception):
    pass


@dataclass(frozen=True)
class Video:
    id: int
    path: str
    imported_at: str
    inspected_at: str
    media: Media
    error: str = ""
    inspection_revision: int = 0


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except (OSError, RuntimeError) as exc:
        raise LibraryError("Cannot resolve this file location.") from exc


class Library:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise LibraryError(
                    "This library was created by a newer application. Update ShortsScheduler; the database was not changed."
                )
            if version == 0:
                db.execute("""CREATE TABLE videos (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    inspected_at TEXT NOT NULL,
                    media TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                )""")
                db.execute("PRAGMA user_version = 1")
            if version < 2:
                # Keep existing IDs but never reuse deleted IDs: delayed editors from another
                # process must not accidentally save to a newly imported video with the same ID.
                db.execute("""CREATE TABLE videos_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    inspected_at TEXT NOT NULL,
                    media TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    inspection_revision INTEGER NOT NULL DEFAULT 0
                )""")
                db.execute("""INSERT INTO videos_v2(id,path,imported_at,inspected_at,media,error)
                    SELECT id,path,imported_at,inspected_at,media,error FROM videos""")
                db.execute("DROP TABLE videos")
                db.execute("ALTER TABLE videos_v2 RENAME TO videos")
                db.execute("""CREATE TABLE preparations (
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    reviewed_revision INTEGER,
                    reviewed_media TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(video_id, platform)
                )""")
                db.execute("PRAGMA user_version = 2")
            if version < 3:
                db.execute("""CREATE TABLE account_config (
                    platform TEXT PRIMARY KEY,
                    client_config TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE account_connection (
                    platform TEXT PRIMARY KEY,
                    credential_key TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    verified_at TEXT NOT NULL
                )""")
                db.execute("PRAGMA user_version = 3")
            if version < 4:
                db.execute("""CREATE TABLE upload_attempts (
                    id TEXT PRIMARY KEY,
                    video_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    state TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    remote_id TEXT NOT NULL DEFAULT '',
                    privacy TEXT NOT NULL DEFAULT '',
                    processing TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    released INTEGER NOT NULL DEFAULT 0
                )""")
                db.execute("""CREATE UNIQUE INDEX one_upload_per_channel
                    ON upload_attempts(channel_id) WHERE released=0
                    AND state NOT IN ('Uploaded Private', 'Failed', 'Cancelled')""")
                db.execute("PRAGMA user_version = 4")
            if version < 5:
                db.execute(
                    "ALTER TABLE upload_attempts ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'historical'"
                )
                db.execute(
                    "ALTER TABLE upload_attempts ADD COLUMN verified_at TEXT NOT NULL DEFAULT ''"
                )
                db.execute("DROP INDEX one_upload_per_channel")
                db.execute("""CREATE UNIQUE INDEX one_upload_per_channel
                    ON upload_attempts(channel_id) WHERE released=0
                    AND state NOT IN ('Uploaded Private', 'Public Verified', 'Failed', 'Cancelled')""")
                db.execute("PRAGMA user_version = 5")

            if version < 6:
                db.execute("""CREATE TABLE scheduled_jobs (
                    id TEXT PRIMARY KEY, video_id INTEGER NOT NULL,
                    platform TEXT NOT NULL CHECK(platform='youtube'),
                    channel_id TEXT NOT NULL, channel_title TEXT NOT NULL,
                    source_hash TEXT NOT NULL, snapshot TEXT NOT NULL,
                    scheduled_utc TEXT NOT NULL, timezone TEXT NOT NULL, local_time TEXT NOT NULL,
                    fold INTEGER NOT NULL CHECK(fold IN (0,1)),
                    state TEXT NOT NULL CHECK(state IN ('Draft','Scheduled','Due','Missed','Needs Attention','Cancelled')),
                    reason TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, approved_at TEXT NOT NULL DEFAULT '',
                    cancelled_at TEXT NOT NULL DEFAULT '', cancellation_reason TEXT NOT NULL DEFAULT '',
                    execution_enabled INTEGER NOT NULL DEFAULT 0 CHECK(execution_enabled=0)
                )""")
                db.execute("""CREATE UNIQUE INDEX active_schedule_video ON scheduled_jobs(platform,channel_id,video_id)
                    WHERE state NOT IN ('Draft','Cancelled')""")
                db.execute("""CREATE UNIQUE INDEX active_schedule_hash ON scheduled_jobs(platform,channel_id,source_hash)
                    WHERE state NOT IN ('Draft','Cancelled')""")
                db.execute("""CREATE TABLE schedule_events (
                    job_id TEXT NOT NULL REFERENCES scheduled_jobs(id), revision INTEGER NOT NULL,
                    record TEXT NOT NULL, PRIMARY KEY(job_id,revision)
                )""")
                db.execute("PRAGMA user_version = 6")

            if version < 7:
                from .execution_schema import migrate

                migrate(db)

            if version < 8:
                from .background_state import migrate as migrate_background

                migrate_background(db)

            if version < 9:
                from .watch_schema import migrate as migrate_watches

                migrate_watches(db)

            if version < 10:
                from .recurring_schema import migrate as migrate_recurring

                migrate_recurring(db)

            if version < 11:
                from .automation_schema import migrate as migrate_automation

                migrate_automation(db)

            if version < 12:
                from .tiktok_schema import migrate as migrate_tiktok

                migrate_tiktok(db)

            if version < 13:
                from .social_schema import migrate as migrate_social

                migrate_social(db)

            if version < 14:
                from .limits_schema import migrate as migrate_limits

                migrate_limits(db)

            if version < 15:
                from .backups_schema import migrate as migrate_backups

                migrate_backups(db)

            if version < 16:
                from .fallback_schema import migrate as migrate_fallback

                migrate_fallback(db)

            if version < 17:
                from .queues_schema import migrate as migrate_queues

                migrate_queues(db)

    @staticmethod
    def decode(row: sqlite3.Row) -> Video:
        try:
            values = json.loads(row["media"])
            values["audio_codecs"] = tuple(values["audio_codecs"])
            values["notes"] = tuple(values["notes"])
            media = Media(**values)
            return Video(
                row["id"],
                row["path"],
                row["imported_at"],
                row["inspected_at"],
                media,
                row["error"],
                row["inspection_revision"],
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise LibraryError(
                "A library record is unreadable. The database has been preserved; restore a backup or investigate the record."
            ) from exc

    def all(self) -> list[Video]:
        with closing(self.connect()) as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM videos ORDER BY id DESC")]

    def get(self, video_id: int) -> Video:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                raise LibraryError("This entry was removed. Reload the library.")
            return self.decode(row)

    def find(self, path: str) -> Video | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM videos WHERE path = ?", (path,)).fetchone()
            return self.decode(row) if row else None

    def save(self, path: str, media: Media) -> tuple[Video, bool]:
        with closing(self.connect()) as db, db:
            return self.save_in_transaction(db, path, media)

    def save_in_transaction(
        self, db: sqlite3.Connection, path: str, media: Media
    ) -> tuple[Video, bool]:
        """Shared insertion for manual import and atomic watched-content association."""
        stamp = now()
        cursor = db.execute(
            """INSERT INTO videos(path, imported_at, inspected_at, media)
            VALUES (?, ?, ?, ?) ON CONFLICT(path) DO NOTHING""",
            (path, stamp, stamp, json.dumps(asdict(media))),
        )
        row = db.execute("SELECT * FROM videos WHERE path = ?", (path,)).fetchone()
        return self.decode(row), cursor.rowcount == 1

    def update(self, video_id: int, media: Media) -> Video:
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                "UPDATE videos SET media = ?, inspected_at = ?, error = '', inspection_revision=inspection_revision+1 WHERE id = ?",
                (json.dumps(asdict(media)), now(), video_id),
            )
            if cursor.rowcount != 1:
                raise LibraryError("This entry was removed while inspection was running.")
            db.execute(
                "UPDATE preparations SET reviewed_revision=NULL, reviewed_media='', reviewed_at='' WHERE video_id=?",
                (video_id,),
            )
        return self.get(video_id)

    def mark_error(self, video_id: int, error: str) -> Video:
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE videos SET error = ?, inspection_revision=inspection_revision+1 WHERE id = ?",
                (error, video_id),
            )
            db.execute(
                "UPDATE preparations SET reviewed_revision=NULL, reviewed_media='', reviewed_at='' WHERE video_id=?",
                (video_id,),
            )
        return self.get(video_id)

    def remove(self, video_id: int) -> None:
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM videos WHERE id = ?", (video_id,))


def source_status(video: Video) -> str:
    """Cheap check only for the selected item; never scan media during startup."""
    try:
        info = Path(video.path).stat()
        if info.st_size != video.media.size or info.st_mtime_ns != video.media.mtime_ns:
            return "Source changed since inspection. Refresh Inspection before relying on these results."
    except FileNotFoundError:
        return "Source missing. Restore it to the saved location, then refresh inspection."
    except OSError:
        return "Source inaccessible. Check permissions and refresh inspection."
    return ""


class LibraryService:
    def __init__(self, library: Library, inspector: Inspector) -> None:
        self.library = library
        self.inspector = inspector
        self.preparations = PreparationStore(library)

    def execute(self, action: str, argument: Any, cancel: Event) -> dict[str, Any]:
        checkpoint(cancel)
        try:
            self.library.initialize()
            selected: Video | None = None
            message = ""
            if action == "import":
                path = canonical(Path(argument))
                selected = self.library.find(path)
                if selected is not None:
                    message = (
                        "Already in your library. Select Refresh Inspection to inspect it again."
                    )
                else:
                    media = self.inspector.inspect(Path(path), cancel)
                    checkpoint(cancel)
                    selected, created = self.library.save(path, media)
                    message = (
                        "Video inspected and saved to your library."
                        if created
                        else "Already in your library."
                    )
            elif action == "refresh":
                selected = self.library.get(argument)
                try:
                    media = self.inspector.inspect(Path(selected.path), cancel)
                    checkpoint(cancel)
                    selected = self.library.update(selected.id, media)
                    message = "Inspection refreshed."
                except InspectionError as exc:
                    selected = self.library.mark_error(selected.id, str(exc))
                    message = "Refresh failed. Previous metadata is retained and is not a current passing inspection."
            elif action == "remove":
                checkpoint(cancel)
                self.library.remove(argument)
                message = "Removed from library. The original file was not changed."
            elif action == "save_metadata":
                video_id, data, revision = argument
                self.preparations.save(video_id, data, revision)
                selected = self.library.get(video_id)
                message = "Publishing details saved locally."
            elif action == "complete_review":
                video_id, revision, inspection = argument
                self.preparations.complete(video_id, revision, inspection)
                selected = self.library.get(video_id)
                message = "Local review complete. Nothing has been uploaded or published."
            elif action in ("select", "check"):
                selected = self.library.get(argument)
            elif action != "load":
                raise LibraryError("Unknown library operation.")
            checkpoint(cancel)
            availability = source_status(selected) if selected else ""
            preparation = self.preparations.snapshot(selected, availability) if selected else None
            return {
                "action": action,
                "preparation": preparation,
                "videos": self.library.all(),
                "selected": selected,
                "source_status": availability,
                "message": message,
            }
        except (sqlite3.Error, OSError) as exc:
            raise LibraryError(
                "The library could not be accessed or saved. Check storage permissions, available space, or another running copy. Existing data was not reset."
            ) from exc
